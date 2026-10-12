"""Compare generated applications: headline metrics, marginal complexity per module, consistency flags, ranking."""
from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from . import SCHEMA_VERSION, __version__


def _app_name(r: dict, i: int, used: set[str]) -> str:
    base = r["target"].get("name") or r["target"].get("repo") or f"app{i + 1}"
    name = base
    k = 2
    while name in used:
        name = f"{base}#{k}"
        k += 1
    used.add(name)
    return name


def _headline(r: dict) -> dict[str, Any]:
    m = r["metrics"]
    mod, svc = m["levels"]["module"], m["levels"]["service"]
    prov = (r.get("provenance") or {}).get("summary") or {}
    events = (r.get("events") or {}).get("inventory", [])
    return {
        "files": m["totals"]["files"], "loc": m["totals"]["loc"], "functions": m["totals"]["functions"],
        "modules": len(mod["nodes"]), "services": len(svc["nodes"]),
        "mean_complexity": m["totals"]["mean_complexity"], "functions_over_threshold": m["totals"]["functions_over_threshold"],
        "propagation_cost_module": mod["propagation_cost"].get("combined"),
        "reachable_pairs_module": round((mod["propagation_cost"].get("combined") or 0) * len(mod["nodes"]) ** 2),
        "propagation_cost_service": svc["propagation_cost"].get("combined"),
        "cycles_module": len(mod["cycles"].get("combined", [])), "cycles_service": len(svc["cycles"].get("combined", [])),
        "events": len(events), "dangling_events": sum(1 for e in events if e["flags"]),
        "unresolved_event_sites": len((r.get("events") or {}).get("unresolved", [])),
        "shared_database_couplings": sum(1 for f in r.get("findings", []) if f["type"] == "shared-database"),
        "glue_share_of_complexity": (prov.get("new") or {}).get("share_of_complexity"),
        "copied_share_of_complexity": (prov.get("copied") or {}).get("share_of_complexity"),
        "modified_share_of_complexity": (prov.get("modified") or {}).get("share_of_complexity"),
    }


def _per_unit(r: dict) -> dict[str, dict[str, Any]]:
    """Per framework module (or plain module when there is no provenance) metrics for one application."""
    mod = r["metrics"]["levels"]["module"]
    # Units are matched across applications by module name. Image-backed services keep their files under "<service>/",
    # which is stripped so the same framework module in two services or applications lines up. (A majority-vote mapping
    # to framework modules is NOT used here: one application module can hold files from several framework modules.)
    image_prefixes = {n + "/" for n, sv in ((r.get("service_graph") or {}).get("services") or {}).items() if str(sv.get("source", "")).startswith("image:")}

    def unit_of(name: str) -> str:
        for p in image_prefixes:
            if name.startswith(p):
                return name[len(p):]
        return name
    impact = mod.get("removal_impact") or {}
    cyc_members = {n for c in mod["cycles"].get("combined", []) for n in c}
    dang_by_module: dict[str, int] = defaultdict(int)
    for e in (r.get("events") or {}).get("inventory", []):
        if e["flags"]:
            side = e["publishers"] if "no_subscriber" in e["flags"] else e["subscribers"]
            for s in side:
                dang_by_module[s["module"]] += 1
    units: dict[str, dict[str, Any]] = {}
    for node in mod["nodes"]:
        name = node.removeprefix("module:")
        unit = unit_of(name)
        u = units.setdefault(unit, {"loc": 0, "pc_delta": 0.0, "pairs": 0, "coupling": 0, "cycles_delta": 0, "in_cycle": False, "dangling": 0, "app_modules": []})
        nm = mod["node_metrics"][node]
        u["loc"] += nm.get("loc", 0)
        u["app_modules"].append(name)
        imp = impact.get(node)
        if imp:
            u["pc_delta"] += imp["propagation_cost_contribution"]
            u["pairs"] += imp["reach_pairs_delta"]
            u["coupling"] += imp["coupling_delta"]
            u["cycles_delta"] += imp["cycles_delta"]
        u["in_cycle"] = u["in_cycle"] or node in cyc_members
        u["dangling"] += dang_by_module.get(name, 0)
    return units


def compare_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    used: set[str] = set()
    names = [_app_name(r, i, used) for i, r in enumerate(results)]
    heads = {n: _headline(r) for n, r in zip(names, results)}
    units = {n: _per_unit(r) for n, r in zip(names, results)}
    all_units = sorted({u for us in units.values() for u in us})

    marginal = []
    for u in all_units:
        has = [n for n in names if u in units[n]]
        lacks = [n for n in names if u not in units[n]]
        per = {n: units[n][u] for n in has}
        row: dict[str, Any] = {
            "module": u, "applications": has, "included_in": len(has), "of": len(names),
            "loc": {"mean": round(mean(p["loc"] for p in per.values()), 1), "min": min(p["loc"] for p in per.values()), "max": max(p["loc"] for p in per.values())},
            "propagation_cost_contribution": {"mean": round(mean(p["pc_delta"] for p in per.values()), 5), "per_app": {n: round(p["pc_delta"], 5) for n, p in per.items()}},
            "coupling": {"mean": round(mean(p["coupling"] for p in per.values()), 1), "per_app": {n: p["coupling"] for n, p in per.items()}},
            "in_cycle_in": [n for n, p in per.items() if p["in_cycle"]],
            "dangling_events": {n: p["dangling"] for n, p in per.items()},
        }
        # secondary, comparison-based estimate: how do whole-application metrics differ with vs without the module?
        est = None
        if has and lacks:
            d = {}
            for metric in ("loc", "propagation_cost_module", "cycles_module", "dangling_events"):
                a = [heads[n][metric] for n in has if heads[n][metric] is not None]
                b = [heads[n][metric] for n in lacks if heads[n][metric] is not None]
                if a and b:
                    d[metric] = round(mean(a) - mean(b), 5)
            exact = len(names) == 2 or any(set(units[a]) ^ set(units[b]) == {u} for a in has for b in lacks)
            est = {"difference_of_means": d, "with": len(has), "without": len(lacks),
                   "confidence": "exact (applications differ only by this module)" if any(set(units[a]) ^ set(units[b]) == {u} for a in has for b in lacks)
                   else "low (applications differ in many modules; confounded)" if len(has) < 2 or len(lacks) < 2 else "medium (difference of means, confounded by other modules)"}
        else:
            est = {"note": "module is in every compared application (or none): its comparison-based effect is not identifiable"}
        row["comparison_estimate"] = est
        row["consistently_in_cycles"] = len(has) >= 2 and len(row["in_cycle_in"]) == len(has)
        row["consistently_dangling"] = len(has) >= 2 and all(p["dangling"] > 0 for p in per.values())
        marginal.append(row)
    marginal.sort(key=lambda r: -r["propagation_cost_contribution"]["mean"])

    # ranking: mean rank over transparent metrics (higher value = more complex)
    keys = ["reachable_pairs_module", "loc", "functions_over_threshold", "cycles_module", "dangling_events"]
    ranks: dict[str, list[float]] = {n: [] for n in names}
    for k in keys:
        vals = {n: (heads[n][k] or 0) for n in names}
        for n in names:
            ranks[n].append(1 + sum(1 for m in names if vals[m] < vals[n]) + 0.5 * (sum(1 for m in names if vals[m] == vals[n]) - 1))
    score = {n: round(mean(v), 3) for n, v in ranks.items()}
    ordered = sorted(names, key=lambda n: (score[n], heads[n]["propagation_cost_module"] or 0))
    return {
        "schema_version": SCHEMA_VERSION, "tool": {"name": "archimeter", "version": __version__},
        "applications": [{"name": n, "target": results[i]["target"], "headline": heads[n]} for i, n in enumerate(names)],
        "marginal": marginal,
        "consistently_in_cycles": [r["module"] for r in marginal if r["consistently_in_cycles"]],
        "consistently_dangling": [r["module"] for r in marginal if r["consistently_dangling"]],
        "ranking": {"metrics": keys, "mean_rank": score, "method": "mean rank across the listed metrics (higher = more complex); reachable_pairs is propagation cost times modules squared, so applications of different size compare fairly"},
        "most_complex": ordered[-1], "least_complex": ordered[0],
        "notes": ["propagation_cost_contribution is counterfactual: the share of the application's n^2 dependency-matrix cells that are reachable pairs only because the module is present (removal impact), averaged over the applications that include it",
                  "comparison_estimate is confounded unless the applications differ only by the module"],
    }
