"""Which framework module did each generated file come from? Manifest first, then framework matching."""
from __future__ import annotations

import json
import posixpath
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..graph.algo import default_algo
from ..safety import LANG_BY_EXT, walk_sources
from ..similarity import content_hash, features, jaccard
from .base import Context, register

MANIFEST_FORMAT = "archimeter-provenance/1"
MANIFEST_DEFAULTS = ("archimeter-provenance.json", "provenance.json", ".archimeter/provenance.json")
KIND_TO_CLASS = {"copied": "copied", "rendered": "modified", "modified": "modified", "generated": "new", "new": "new"}


class FrameworkIndex:
    def __init__(self, root: Path, ctx: Context):
        from ..pipeline import build_context
        from .modules import ModuleDetector
        cfg = ctx.config
        fctx = build_context(root, cfg, target={"name": root.name}, kind="generated")  # keep generated files: no skip
        ModuleDetector().extract(fctx)
        self.root = root
        self.files = {f.rel: f for f in fctx.files}
        self.module_of = {rel: m.removeprefix("module:") for rel, m in fctx.shared["module_of"].items()}
        self.ctx = fctx
        self.by_hash: dict[str, list[str]] = defaultdict(list)
        self.by_base: dict[str, list[str]] = defaultdict(list)
        for rel, f in self.files.items():
            self.by_hash[content_hash(fctx.read(f))].append(rel)
            self.by_base[posixpath.basename(rel)].append(rel)
        self._feat: dict[str, set[int] | None] = {}

    def feat(self, rel: str) -> set[int] | None:
        if rel not in self._feat:
            f = self.files[rel]
            self._feat[rel] = features(rel, f.lang, self.ctx.read(f))
        return self._feat[rel]

    def head(self) -> str | None:
        from ..resolve import _git_head
        return _git_head(self.root)


@register
class Provenance:
    name = "provenance"
    requires = ("modules", "code-metrics", "python-imports", "js-imports", "events", "service-deps", "git-history")

    def applies(self, ctx: Context) -> bool:
        return ctx.kind == "generated"

    def extract(self, ctx: Context) -> None:
        cfg = ctx.config.provenance
        manifest = self._load_manifest(ctx)
        fw_dir = Path(cfg.framework).expanduser() if cfg.framework else None
        index = FrameworkIndex(fw_dir.resolve(), ctx) if fw_dir and fw_dir.is_dir() else None
        if cfg.framework and index is None:
            from ..errors import ArchimeterError
            raise ArchimeterError(f"provenance.framework / --framework is not a directory: {cfg.framework}")
        notes: list[str] = []
        if manifest is None and index is None:
            ctx.shared["provenance"] = {"source": "none", "notes": [
                "no provenance manifest found and no --framework given: files are not attributed"]}
            return

        results: dict[str, dict[str, Any]] = {}
        for f in ctx.files:
            r = None
            if manifest is not None:
                ent = manifest["files"].get(f.rel)
                if ent is not None:
                    cls = KIND_TO_CLASS.get(ent.get("kind", "generated"), "new")
                    r = {"class": cls, "via": "manifest", "module": ent.get("module"), "template": ent.get("template"),
                         "version": ent.get("version"), "source": ent.get("source")}
            if r is None and index is not None:
                r = self._match(ctx, index, f)
            if r is None:
                r = {"class": "unattributed", "via": "none", "reason": "not in manifest and no --framework given"}
            results[f.rel] = r
            ctx.graph.nodes[f"file:{f.rel}"].provenance = r

        ctx.shared["provenance"] = self._summarize(ctx, results, manifest, index)

    # ------------------------------------------------------------------ manifest
    def _load_manifest(self, ctx: Context) -> dict | None:
        cfg = ctx.config.provenance
        cands = [cfg.manifest] if cfg.manifest else list(MANIFEST_DEFAULTS)
        for c in cands:
            p = Path(c)
            p = p if p.is_absolute() else ctx.root / c
            if p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    from ..errors import ArchimeterError
                    raise ArchimeterError(f"provenance manifest {p} is unreadable: {e}") from None
                if data.get("format") != MANIFEST_FORMAT or not isinstance(data.get("files"), dict):
                    from ..errors import ArchimeterError
                    raise ArchimeterError(f"provenance manifest {p}: expected format '{MANIFEST_FORMAT}' with a 'files' object")
                return data
        if cfg.manifest:
            from ..errors import ArchimeterError
            raise ArchimeterError(f"provenance manifest not found: {cfg.manifest}")
        return None

    # ------------------------------------------------------------------ matching
    def _match(self, ctx: Context, index: FrameworkIndex, f) -> dict[str, Any]:
        text = ctx.read(f)
        h = content_hash(text)
        exact = index.by_hash.get(h)
        if exact:
            same_path = [e for e in exact if e == f.rel or e.endswith("/" + f.rel) or f.rel.endswith("/" + e)]
            pick = same_path or exact
            mods = {index.module_of.get(e) for e in pick}
            if len(mods) > 1 and not same_path and posixpath.basename(f.rel) != "__init__.py":
                return {"class": "unattributed", "via": "hash", "reason": "identical content in several framework modules",
                        "candidates": sorted(pick)[:5]}
            src = sorted(pick)[0]
            return {"class": "copied", "via": "hash", "source": src, "module": index.module_of.get(src), "similarity": 1.0}
        base = posixpath.basename(f.rel)
        same_path = [r for r in index.by_base.get(base, []) if r == f.rel or r.endswith("/" + f.rel) or f.rel.endswith("/" + r)]
        cands = same_path or index.by_base.get(base, [])
        cands = [c for c in cands if index.files[c].lang == f.lang]
        mine = features(f.rel, f.lang, text)
        if mine is not None and cands:
            scored = sorted(((jaccard(mine, index.feat(c) or set()), c) for c in cands), reverse=True)
            best, src = scored[0]
            if best >= ctx.config.provenance.similarity_threshold:
                tied = [c for s, c in scored if best - s < 0.01 and index.module_of.get(c) != index.module_of.get(src)]
                if tied:
                    return {"class": "unattributed", "via": "ast", "reason": "equally similar to files in several framework modules",
                            "candidates": [src] + tied[:4]}
                return {"class": "modified", "via": "ast", "source": src, "module": index.module_of.get(src),
                        "similarity": round(best, 3)}
        return {"class": "new", "via": "ast" if cands or mine is None else "none",
                "reason": "no framework file matches" if not cands else "below similarity threshold",
                "best_similarity": round(scored[0][0], 3) if mine is not None and cands else None}

    # ------------------------------------------------------------------ summary
    def _summarize(self, ctx: Context, results: dict[str, dict], manifest, index) -> dict[str, Any]:
        g = ctx.graph
        loc_of = {rel: g.nodes[f"file:{rel}"].attrs.get("lloc") or 0 for rel in results}
        cc_of: Counter[str] = Counter()
        for n in g.nodes.values():
            if n.level == "function" and n.parent:
                cc_of[n.parent.removeprefix("file:")] += n.attrs.get("cyclomatic") or 0
        classes = ("copied", "modified", "new", "unattributed")
        summary = {c: {"files": 0, "loc": 0, "complexity": 0} for c in classes}
        by_mod: dict[str, dict[str, Any]] = {}
        for rel, r in results.items():
            c = r["class"]
            summary[c]["files"] += 1
            summary[c]["loc"] += loc_of[rel]
            summary[c]["complexity"] += cc_of[rel]
            if c in ("copied", "modified") and r.get("module"):
                m = by_mod.setdefault(r["module"], {"copied_files": 0, "modified_files": 0, "loc": 0, "complexity": 0})
                m[f"{c}_files"] += 1
                m["loc"] += loc_of[rel]
                m["complexity"] += cc_of[rel]
        total_cc = sum(v["complexity"] for v in summary.values()) or 1
        for v in summary.values():
            v["share_of_complexity"] = round(v["complexity"] / total_cc, 4)
        for m in by_mod.values():
            m["share_of_complexity"] = round(m["complexity"] / total_cc, 4)

        # propagation attribution: how much of the file-level transitive closure originates in each provenance group
        files = sorted(f for f in g.nodes if g.nodes[f].level == "file")
        idx = {f: i for i, f in enumerate(files)}
        edges = [(s, d) for (s, d, k) in g.edges if k in ("import", "event") and s in idx and d in idx]
        algo = default_algo()
        reach = algo.reachability(files, edges).tocsr()
        row_nnz = reach.getnnz(axis=1)
        grp: dict[str, dict[str, Any]] = defaultdict(lambda: {"files": 0, "reach_pairs": 0})
        for f, i in idx.items():
            rel = f.removeprefix("file:")
            r = results.get(rel, {"class": "unattributed"})
            key = r.get("module") if r["class"] in ("copied", "modified") and r.get("module") else {"new": "generated glue"}.get(r["class"], r["class"])
            grp[key]["files"] += 1
            grp[key]["reach_pairs"] += int(row_nnz[i]) - 1  # exclude self
        total_pairs = sum(v["reach_pairs"] for v in grp.values()) or 1
        for v in grp.values():
            v["share_of_reachability"] = round(v["reach_pairs"] / total_pairs, 4)

        # dangling events, tagged with where the dangling side lives
        ev = ctx.shared.get("events", {}).get("inventory", [])
        dangling = []
        for e in ev:
            if e["flags"]:
                sides = e["publishers"] if "no_subscriber" in e["flags"] else e["subscribers"]
                dangling.append({"event": e["name"], "flags": e["flags"],
                                 "sites": [{"file": s["file"], "line": s["line"], "module": s["module"],
                                            "provenance": results.get(s["file"], {}).get("class")} for s in sides]})

        # dependency closure over framework modules actually included
        mod_edges = g.rollup("module", ["import", "event"])
        mods = set(by_mod)
        mod_of_node = ctx.shared.get("module_of", {})
        fw_module_of_module: dict[str, str] = {}
        votes: dict[str, Counter] = defaultdict(Counter)
        for rel, r in results.items():
            if r.get("module") and r["class"] in ("copied", "modified"):
                votes[mod_of_node.get(rel, "").removeprefix("module:")][r["module"]] += 1
        for m, c in votes.items():
            fw_module_of_module[m] = c.most_common(1)[0][0]
        required_by: dict[str, set[str]] = defaultdict(set)
        for (a, b, k) in mod_edges:
            fa, fb = fw_module_of_module.get(a.removeprefix("module:")), fw_module_of_module.get(b.removeprefix("module:"))
            if fa and fb and fa != fb:
                required_by[fb].add(fa)
        selected = (manifest or {}).get("selected_modules") or ctx.config.provenance.selected_modules or None
        roots = sorted(m for m in mods if not required_by.get(m))
        closure = {
            "included_modules": sorted(mods),
            "selected": sorted(selected) if selected else None,
            "selected_source": ("manifest" if manifest and manifest.get("selected_modules") else "config") if selected else "inferred",
            "pulled_in_transitively": sorted(mods - set(selected)) if selected else sorted(mods - set(roots)),
            "roots_not_required_by_other_modules": roots,
            "required_by": {m: sorted(v) for m, v in sorted(required_by.items())},
        }
        module_map = {m: fw for m, fw in sorted(fw_module_of_module.items())}
        ctx.shared["provenance_module_map"] = module_map
        top_complexity = sorted(({"file": r, "complexity": cc_of[r], "class": results[r]["class"],
                                  "module": results[r].get("module")} for r in results), key=lambda x: -x["complexity"])[:25]
        out: dict[str, Any] = {
            "source": "manifest" if manifest is not None else "framework",
            "generator": (manifest or {}).get("generator"),
            "framework": {"name": index.root.name, "commit": index.head()} if index else None,
            "summary": summary,
            "by_framework_module": dict(sorted(by_mod.items(), key=lambda kv: -kv[1]["complexity"])),
            "reachability_attribution": dict(sorted(grp.items(), key=lambda kv: -kv[1]["reach_pairs"])),
            "dangling_events": dangling,
            "dependency_closure": closure,
            "module_map": module_map,
            "top_complexity_files": top_complexity,
            "unattributed": sorted(r for r, v in results.items() if v["class"] == "unattributed"),
            "files": {rel: {k: v for k, v in r.items() if k in ("class", "via", "source", "module", "similarity", "template", "version")}
                      for rel, r in sorted(results.items())},
            "threshold": ctx.config.provenance.similarity_threshold,
        }
        if index is not None:
            # change metrics are only meaningful on the framework's own history
            out["framework_change_metrics"] = self._framework_changes(ctx, index, by_mod)
        return out

    def _framework_changes(self, ctx: Context, index: FrameworkIndex, included: dict) -> dict[str, Any] | None:
        """Change history of the framework modules this application includes (the application has none of its own)."""
        from itertools import combinations
        from ..gitlog import GitHistory
        if not GitHistory.available(index.root):
            return None
        cfg = ctx.config.git
        gh = GitHistory(index.root, include_author_names=cfg.include_author_names)
        commits = gh.commits(cfg.window_days)
        churn: Counter[str] = Counter()
        small: Counter[str] = Counter()
        pairs: Counter[tuple[str, str]] = Counter()
        authors: dict[str, Counter] = defaultdict(Counter)
        for c in commits:
            mods = sorted({index.module_of[f] for f in c.files if f in index.module_of and index.module_of[f] in included})
            for m in mods:
                churn[m] += 1
                authors[m][c.author] += 1
            if len(c.files) > cfg.max_commit_files:
                continue
            for m in mods:
                small[m] += 1
            for a, b in combinations(mods, 2):
                pairs[(a, b)] += 1
        # does the application link two framework modules by import or event? (app module names -> framework module names)
        mmap = {m: fw for m, fw in (ctx.shared.get("provenance_module_map") or {}).items()}
        linked: set[tuple[str, str]] = set()
        for (a, b, k) in ctx.graph.rollup("module", ["import", "event"]):
            fa, fb = mmap.get(a.removeprefix("module:")), mmap.get(b.removeprefix("module:"))
            if fa and fb and fa != fb:
                linked.add((fa, fb)); linked.add((fb, fa))
        coupling, hidden = [], []
        for (a, b), s_ in sorted(pairs.items(), key=lambda kv: -kv[1]):
            row = {"a": a, "b": b, "support": s_, "confidence_a_to_b": round(s_ / small[a], 3), "confidence_b_to_a": round(s_ / small[b], 3),
                   "has_dependency": (a, b) in linked}
            coupling.append(row)
            if s_ >= cfg.min_support and max(row["confidence_a_to_b"], row["confidence_b_to_a"]) >= cfg.min_confidence and not row["has_dependency"]:
                hidden.append(row)
        return {"window_days": cfg.window_days, "source": "framework history, restricted to the modules this application includes",
                "modules": {m: {"commits": churn[m], "top_author_share": round(authors[m].most_common(1)[0][1] / churn[m], 3) if churn[m] else None}
                            for m in sorted(included)},
                "change_coupling": coupling[:100], "hidden_coupling": hidden}
