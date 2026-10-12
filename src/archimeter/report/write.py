from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..safety import Redactor


def write_outputs(result: dict[str, Any], out: Path) -> None:
    text = Redactor.redact(json.dumps(result, indent=2, sort_keys=True, default=str))
    (out / "metrics.json").write_text(text, encoding="utf-8")
    try:
        from .html import render_report
    except ImportError:
        return
    (out / "report.html").write_text(render_report(result), encoding="utf-8")


def write_compare(comp: dict[str, Any], results: list[dict[str, Any]], out: Path) -> None:
    from importlib import resources
    from .html import embed
    (out / "compare.json").write_text(Redactor.redact(json.dumps(comp, indent=2, sort_keys=True, default=str)), encoding="utf-8")
    tpl = resources.files("archimeter.report").joinpath("compare.html").read_text(encoding="utf-8")
    (out / "compare.html").write_text(tpl.replace("__DATA__", embed(comp)), encoding="utf-8")
    for i, r in enumerate(results):
        name = comp["applications"][i]["name"].replace("/", "_").replace("#", "_")
        d = out / name
        d.mkdir(exist_ok=True)
        write_outputs(r, d)


def write_trend(doc: dict[str, Any], out: Path) -> None:
    from importlib import resources
    from .html import embed
    (out / "trend.json").write_text(Redactor.redact(json.dumps(doc, indent=2, sort_keys=True, default=str)), encoding="utf-8")
    tpl = resources.files("archimeter.report").joinpath("trend.html").read_text(encoding="utf-8")
    (out / "trend.html").write_text(tpl.replace("__DATA__", embed(doc)), encoding="utf-8")
