from pathlib import Path

from archimeter.config import load_config
from archimeter.gitlog import GitHistory
from archimeter.pipeline import analyze_context, build_context


def run(root: Path, tmp_path: Path):
    cfgp = tmp_path / "cfg.toml"
    cfgp.write_text('[events]\npresets = ["node-eventemitter"]\n')
    ctx = build_context(root, load_config(cfgp), target={"name": "tsrepo"})
    ctx.git = GitHistory(root)
    return analyze_context(ctx)


def test_alias_imports_and_cycle(tsrepo, tmp_path):
    r = run(tsrepo, tmp_path)
    mod = r["metrics"]["levels"]["module"]
    imp = {(a, b) for a, b, w in mod["dsm"]["import"]}
    assert imp == {("module:src/ui", "module:src/data"), ("module:src/data", "module:src/ui"),
                   ("module:src/ui", "module:src/core"), ("module:src/cart", "module:src/core")}
    assert mod["cycles"]["import"] == [["module:src/data", "module:src/ui"]]
    assert r["parse_errors"] == []


def test_eventemitter_inventory(tsrepo, tmp_path):
    ev = run(tsrepo, tmp_path)["events"]
    by = {e["name"]: e for e in ev["inventory"]}
    assert set(by) == {"cart:add", "cart:cleared", "ui:ready"}
    assert by["cart:add"]["flags"] == []
    assert by["cart:cleared"]["flags"] == ["no_subscriber"]  # enum member resolved to its value
    assert by["ui:ready"]["flags"] == ["never_published"]
    assert len(ev["unresolved"]) == 1


def test_event_edge_ui_to_cart_has_no_import(tsrepo, tmp_path):
    r = run(tsrepo, tmp_path)["metrics"]["levels"]["module"]["dsm"]
    assert {(a, b) for a, b, w in r["event"]} == {("module:src/ui", "module:src/cart")}


def test_hidden_coupling(tsrepo, tmp_path):
    r = run(tsrepo, tmp_path)
    assert [(h["a"], h["b"], h["support"]) for h in r["hidden_coupling"]] == [("src/reporta", "src/reportb", 5)]
    assert r["hidden_coupling"][0]["has_dependency"] is False
