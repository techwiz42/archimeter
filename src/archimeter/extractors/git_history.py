"""Change history: churn, change coupling, hotspots, hidden coupling, ownership."""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations

from .base import Context, register


@register
class GitHistoryExtractor:
    name = "git-history"
    requires = ("modules", "code-metrics", "python-imports", "js-imports", "events")

    def applies(self, ctx: Context) -> bool:
        return ctx.git is not None

    def extract(self, ctx: Context) -> None:
        cfg = ctx.config.git
        g = ctx.graph
        module_of = ctx.shared.get("module_of", {})
        commits = ctx.git.commits(cfg.window_days)
        file_churn: Counter[str] = Counter()
        mod_commits: Counter[str] = Counter()
        pair_support: Counter[tuple[str, str]] = Counter()
        mod_authors: dict[str, Counter[str]] = defaultdict(Counter)
        used = skipped_large = 0
        for c in commits:
            files = [f for f in c.files if f in module_of]
            if not files:
                continue
            for f in files:
                file_churn[f] += 1
            mods = sorted({module_of[f] for f in files})
            for m in mods:
                mod_commits[m] += 1
                mod_authors[m][c.author] += 1
            if len(c.files) > cfg.max_commit_files:
                skipped_large += 1
                continue
            used += 1
            for a, b in combinations(mods, 2):
                pair_support[(a, b)] += 1

        # coupling counts only commits within the size cutoff, so confidence uses cutoff-consistent denominators
        small_mod_commits: Counter[str] = Counter()
        for c in commits:
            if len(c.files) > cfg.max_commit_files:
                continue
            for m in {module_of[f] for f in c.files if f in module_of}:
                small_mod_commits[m] += 1

        mod_edges = {(a, b) for (a, b, k) in g.rollup("module", ["import", "event", "service"])}
        coupling, hidden = [], []
        for (a, b), s in sorted(pair_support.items(), key=lambda kv: -kv[1]):
            ca, cb = small_mod_commits[a], small_mod_commits[b]
            conf_ab, conf_ba = s / ca, s / cb  # P(b changes | a changes), P(a changes | b changes)
            row = {"a": a.removeprefix("module:"), "b": b.removeprefix("module:"), "support": s,
                   "confidence_a_to_b": round(conf_ab, 3), "confidence_b_to_a": round(conf_ba, 3)}
            linked = (a, b) in mod_edges or (b, a) in mod_edges
            row["has_dependency"] = linked
            coupling.append(row)
            if s >= cfg.min_support and max(conf_ab, conf_ba) >= cfg.min_confidence:
                for x, y in ((a, b), (b, a)):
                    g.add_edge(x, y, "change", float(s), f"co-changed in {s} commits")
                if not linked:
                    hidden.append(row)

        # hotspots: churn x complexity (sum of cyclomatic complexity of the file's functions)
        file_cc: Counter[str] = Counter()
        for n in g.nodes.values():
            if n.level == "function" and n.parent:
                file_cc[n.parent.removeprefix("file:")] += n.attrs.get("cyclomatic") or 0
        hot = [{"file": f, "module": module_of[f].removeprefix("module:"), "churn": ch, "complexity": file_cc[f], "score": ch * file_cc[f]}
               for f, ch in file_churn.items() if file_cc[f]]
        hot.sort(key=lambda r: -r["score"])

        own = []
        for m, ctr in sorted(mod_authors.items()):
            total = sum(ctr.values())
            top = ctr.most_common(3)
            own.append({"module": m.removeprefix("module:"), "changes": total,
                        "primary_authors": [{"author": a, "share": round(n / total, 3)} for a, n in top],
                        "single_owner": total >= 5 and top[0][1] / total >= cfg.owner_share})

        ctx.shared["change_coupling"] = coupling
        ctx.shared["hidden_coupling"] = hidden
        ctx.shared["hotspots"] = hot[: max(ctx.config.analysis.hotspot_top, 50)]
        ctx.shared["ownership"] = own
        ctx.shared["churn"] = {
            "window_days": cfg.window_days, "commits_in_window": len(commits), "commits_used_for_coupling": used,
            "commits_skipped_as_bulk": skipped_large,
            "module_churn": {m.removeprefix("module:"): n for m, n in mod_commits.most_common()},
            "file_churn_top": file_churn.most_common(50),
        }
