"""System, module and code metrics computed from the graph (no networkx imports here)."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..graph import Graph
from ..graph.algo import GraphAlgo, default_algo

DEP_KINDS = ["import", "event", "service"]  # directed dependency kinds that form "combined"


def _round(x: float | None, n: int = 4) -> float | None:
    return None if x is None else round(x, n)


def propagation_cost(algo: GraphAlgo, nodes: list[str], edges: list[tuple[str, str]]) -> float | None:
    """Density of the transitive closure (MacCormack, Rusnak & Baldwin), diagonal included."""
    n = len(nodes)
    if n < 2:
        return None
    m = algo.reachability(nodes, edges)
    return m.nnz / (n * n)


def level_metrics(g: Graph, level: str, algo: GraphAlgo, extra_edges: dict[tuple[str, str, str], float] | None = None) -> dict[str, Any]:
    nodes = g.nodes_at(level)
    roll = g.rollup(level)
    kinds_present = ["import", "event", "service", "change"]
    per_kind: dict[str, dict[tuple[str, str], float]] = {k: defaultdict(float) for k in kinds_present}
    for (a, b, k), w in roll.items():
        per_kind[k][(a, b)] += w
    combined: dict[tuple[str, str], float] = defaultdict(float)
    for k in DEP_KINDS:
        for e, w in per_kind[k].items():
            combined[e] += w

    result: dict[str, Any] = {"nodes": nodes, "propagation_cost": {}, "cycles": {}, "edge_counts": {}}
    for k, edges in {**per_kind, "combined": combined}.items():
        el = list(edges)
        result["edge_counts"][k] = len(el)
        if k == "change":
            result["propagation_cost"][k] = None  # change coupling is symmetric co-occurrence, not a dependency direction
            continue
        result["propagation_cost"][k] = _round(propagation_cost(algo, nodes, el))
        sccs = [c for c in algo.scc(nodes, el) if len(c) > 1]
        result["cycles"][k] = sorted(sccs, key=lambda c: (-len(c), c))

    # fan-in / fan-out (distinct neighbours) per kind and combined
    fan: dict[str, dict[str, dict[str, int]]] = {n: {} for n in nodes}
    for k, edges in {**per_kind, "combined": combined}.items():
        fin: dict[str, set[str]] = defaultdict(set)
        fout: dict[str, set[str]] = defaultdict(set)
        for a, b in edges:
            fout[a].add(b)
            fin[b].add(a)
        for n in nodes:
            fan[n][k] = {"fan_in": len(fin[n]), "fan_out": len(fout[n])}
    result["fan"] = fan
    # direct two-way dependencies: the concrete edges that make a cycle, easiest to untangle first
    mutual = []
    for (a, b), w in combined.items():
        if a < b and (b, a) in combined:
            mutual.append({"a": a, "b": b, "weight_a_to_b": w, "weight_b_to_a": combined[(b, a)]})
    result["mutual_dependencies"] = sorted(mutual, key=lambda m: -(m["weight_a_to_b"] + m["weight_b_to_a"]))
    result["_edges"] = {k: dict(v) for k, v in per_kind.items()} | {"combined": dict(combined)}
    return result


def _aggregate_code(g: Graph, level: str, threshold: int) -> dict[str, dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = defaultdict(lambda: {"files": 0, "loc": 0, "functions": 0, "cc_sum": 0, "cc_max": 0,
                                                          "over_threshold": 0, "classes": 0, "abstract": 0, "mi_sum": 0.0, "mi_n": 0})
    for n in g.nodes.values():
        if n.level == "file":
            a = g.ancestor(n.id, level)
            if a is None:
                continue
            d = agg[a]
            d["files"] += 1
            d["loc"] += n.attrs.get("lloc") or 0
            d["classes"] += n.attrs.get("classes") or 0
            d["abstract"] += n.attrs.get("abstract_classes") or 0
            if n.attrs.get("mi") is not None:
                d["mi_sum"] += n.attrs["mi"]; d["mi_n"] += 1
        elif n.level == "function":
            a = g.ancestor(n.id, level)
            if a is None:
                continue
            d = agg[a]
            cc = n.attrs.get("cyclomatic") or 0
            d["functions"] += 1
            d["cc_sum"] += cc
            d["cc_max"] = max(d["cc_max"], cc)
            d["over_threshold"] += cc > threshold
    out = {}
    for k, d in agg.items():
        out[k] = {
            "files": d["files"], "loc": d["loc"], "functions": d["functions"],
            "mean_complexity": _round(d["cc_sum"] / d["functions"], 2) if d["functions"] else None,
            "max_complexity": d["cc_max"], "functions_over_threshold": d["over_threshold"],
            "mean_maintainability_index": _round(d["mi_sum"] / d["mi_n"], 1) if d["mi_n"] else None,
            "classes": d["classes"], "abstract_classes": d["abstract"],
        }
    return out


def martin_metrics(code: dict[str, dict], edges_combined: dict[tuple[str, str], float], nodes: list[str]) -> dict[str, dict]:
    ca: dict[str, set[str]] = defaultdict(set)
    ce: dict[str, set[str]] = defaultdict(set)
    for (a, b) in edges_combined:
        ce[a].add(b)
        ca[b].add(a)
    out = {}
    for n in nodes:
        c = code.get(n, {})
        i_ce, i_ca = len(ce[n]), len(ca[n])
        inst = i_ce / (i_ce + i_ca) if (i_ce + i_ca) else None
        classes = c.get("classes", 0)
        a_ = c.get("abstract_classes", 0) / classes if classes else None
        d = abs(a_ + inst - 1) if (a_ is not None and inst is not None) else None
        out[n] = {"Ca": i_ca, "Ce": i_ce, "instability": _round(inst), "abstractness": _round(a_), "distance": _round(d)}
    return out


MAX_REMOVAL_NODES = 400


def removal_impact(algo: GraphAlgo, nodes: list[str], edges: list[tuple[str, str]]) -> dict[str, dict[str, Any]] | None:
    """Counterfactual marginal cost of each node: system metrics with the node (and its edges) minus without it.

    propagation_cost_delta is the density difference (it can be negative: removing an isolated node shrinks the denominator);
    propagation_cost_contribution is the share of all n^2 cells that are reachable pairs involving this node and is never negative; coupling_delta is the number of
    dependency edges the node contributes; cycles_delta is the number of cycles that disappear without it.
    """
    if len(nodes) < 3 or len(nodes) > MAX_REMOVAL_NODES:
        return None
    base_pc = propagation_cost(algo, nodes, edges)
    n_all = len(nodes)
    base_pairs = int(algo.reachability(nodes, edges).nnz) - n_all  # reachable ordered pairs (i != j)
    base_cycles = len([c for c in algo.scc(nodes, edges) if len(c) > 1])
    out: dict[str, dict[str, Any]] = {}
    for n in nodes:
        rest = [x for x in nodes if x != n]
        e2 = [(a, b) for a, b in edges if a != n and b != n]
        pc2 = propagation_cost(algo, rest, e2)
        pairs2 = int(algo.reachability(rest, e2).nnz) - len(rest)
        cyc2 = len([c for c in algo.scc(rest, e2) if len(c) > 1])
        out[n] = {"propagation_cost_delta": _round((base_pc or 0) - (pc2 or 0), 5),
                  # never negative: the reachable pairs that exist only because this node is present, as a share of n^2
                  "reach_pairs_delta": base_pairs - pairs2, "propagation_cost_contribution": _round((base_pairs - pairs2) / (n_all * n_all), 5),
                  "coupling_delta": len(edges) - len(e2), "cycles_delta": base_cycles - cyc2,
                  "in_cycle": any(n in c and len(c) > 1 for c in algo.scc(nodes, edges))}
    return out


def compute_metrics(g: Graph, *, threshold: int = 10, algo: GraphAlgo | None = None) -> dict[str, Any]:
    algo = algo or default_algo()
    result: dict[str, Any] = {"levels": {}}
    for level in ("module", "service"):
        lm = level_metrics(g, level, algo)
        edges = lm.pop("_edges")
        code = _aggregate_code(g, level, threshold)
        martin = martin_metrics(code, edges["combined"], lm["nodes"])
        order = algo.order_by_clusters(lm["nodes"], [(a, b, w) for (a, b), w in edges["combined"].items()])
        lm["order"] = order
        if level == "module":
            lm["parents"] = {n: {"package": g.nodes[n].parent, "service": g.ancestor(n, "service")} for n in lm["nodes"]}
            lm["removal_impact"] = removal_impact(algo, lm["nodes"], list(edges["combined"]))
        lm["dsm"] = {k: [[a, b, w] for (a, b), w in sorted(e.items())] for k, e in edges.items()}
        lm["node_metrics"] = {
            n: {**code.get(n, {"files": 0, "loc": 0, "functions": 0}), **martin[n], "fan": lm["fan"][n]}
            for n in lm["nodes"]
        }
        del lm["fan"]
        result["levels"][level] = lm
    result["totals"] = _totals(g, threshold)
    return result


def _totals(g: Graph, threshold: int) -> dict[str, Any]:
    funcs = [n for n in g.nodes.values() if n.level == "function"]
    ccs = [n.attrs.get("cyclomatic") or 0 for n in funcs]
    return {
        "files": sum(1 for n in g.nodes.values() if n.level == "file"),
        "functions": len(funcs),
        "loc": sum(n.attrs.get("lloc") or 0 for n in g.nodes.values() if n.level == "file"),
        "mean_complexity": _round(sum(ccs) / len(ccs), 2) if ccs else None,
        "max_complexity": max(ccs) if ccs else 0,
        "functions_over_threshold": sum(1 for c in ccs if c > threshold),
    }
