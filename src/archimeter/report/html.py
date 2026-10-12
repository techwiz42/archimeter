"""report.html: one self-contained file; data embedded as JSON; d3 from cdnjs only."""
from __future__ import annotations

import json
from importlib import resources
from typing import Any

from ..safety import Redactor


def report_data(r: dict[str, Any]) -> dict[str, Any]:
    m = r["metrics"]
    levels = {}
    for lv in ("module", "service"):
        L = m["levels"][lv]
        levels[lv] = {k: L[k] for k in ("nodes", "order", "dsm", "propagation_cost", "cycles", "node_metrics", "mutual_dependencies") if k in L}
        if "parents" in L:
            levels[lv]["parents"] = L["parents"]
    t = r["target"]
    prov = r.get("provenance")
    if prov:
        prov = {k: v for k, v in prov.items() if k != "files"}
    return {
        "meta": {"name": t.get("name") or t.get("repo"), "kind": t.get("kind"), "commit": t.get("commit"), "digest": t.get("digest"),
                 "tool_version": r["tool"]["version"], "mode": "generated application" if r.get("provenance") or r.get("service_graph") else "source project"},
        "totals": m["totals"], "levels": levels,
        "hidden_coupling": r.get("hidden_coupling") if r.get("hidden_coupling") is not None else
        (((r.get("provenance") or {}).get("framework_change_metrics") or {}).get("hidden_coupling")),
        "hidden_coupling_source": "framework history" if r.get("hidden_coupling") is None and ((r.get("provenance") or {}).get("framework_change_metrics")) else None, "hotspots": r.get("hotspots"),
        "events": r.get("events"), "provenance": prov, "service_graph": r.get("service_graph"),
        "findings": r.get("findings"), "uncertainty": r.get("uncertainty"), "dependency_inventory": r.get("dependency_inventory"),
        "notes": {"parse_errors": len(r.get("parse_errors", [])), "skipped": len(r.get("skipped", [])), "messages": r.get("notes", [])},
    }


def embed(data: Any) -> str:
    s = json.dumps(data, default=str, separators=(",", ":"))
    s = Redactor.redact(s)
    return s.replace("</", "<\\/").replace("<!--", "<\\!--")


def render_report(r: dict[str, Any]) -> str:
    tpl = resources.files("archimeter.report").joinpath("report.html").read_text(encoding="utf-8")
    return tpl.replace("__DATA__", embed(report_data(r)))
