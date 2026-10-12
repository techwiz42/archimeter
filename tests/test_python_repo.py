from pathlib import Path

from archimeter.config import load_config
from archimeter.gitlog import GitHistory
from archimeter.pipeline import analyze_context, build_context

from conftest import EVENT_CFG


def run(root: Path, tmp_path: Path, cfg_text: str = EVENT_CFG):
    cfgp = tmp_path / "cfg.toml"
    cfgp.write_text(cfg_text)
    cfg = load_config(cfgp)
    ctx = build_context(root, cfg, target={"name": "pyrepo"})
    ctx.git = GitHistory(root)
    return analyze_context(ctx)


def test_modules_imports_cycle_and_propagation(pyrepo, tmp_path):
    r = run(pyrepo, tmp_path)
    mod = r["metrics"]["levels"]["module"]
    assert mod["nodes"] == [f"module:{n}" for n in ("alpha", "beta", "delta", "epsilon", "gamma")]
    assert mod["cycles"]["import"] == [["module:alpha", "module:beta"]]
    assert mod["cycles"]["combined"] == [["module:alpha", "module:beta"]]
    # closure incl. diagonal over 5 modules: alpha{a,b} beta{a,b} others self -> 7/25
    assert mod["propagation_cost"]["import"] == 0.28
    # event edge gamma->alpha lifts gamma's reach to {gamma,alpha,beta}: 9/25
    assert mod["propagation_cost"]["combined"] == 0.36
    assert mod["propagation_cost"]["event"] == 0.24
    assert sorted(a for a, b, w in mod["dsm"]["import"]) == ["module:alpha", "module:beta"]


def test_event_inventory_flags_and_unresolved(pyrepo, tmp_path):
    ev = run(pyrepo, tmp_path)["events"]
    by = {e["name"]: e for e in ev["inventory"]}
    assert set(by) == {"order.created", "order.shipped", "audit.never"}
    assert by["order.created"]["flags"] == []
    assert by["order.created"]["publishers"][0]["module"] == "alpha"
    assert by["order.created"]["subscribers"][0]["module"] == "gamma"
    assert by["order.shipped"]["flags"] == ["no_subscriber"]
    assert by["audit.never"]["flags"] == ["never_published"]
    assert len(ev["unresolved"]) == 1 and "f-string" in ev["unresolved"][0]["reason"]


def test_no_event_import_link_between_gamma_and_alpha(pyrepo, tmp_path):
    r = run(pyrepo, tmp_path)
    imp = {(a, b) for a, b, w in r["metrics"]["levels"]["module"]["dsm"]["import"]}
    assert ("module:gamma", "module:alpha") not in imp
    ev = {(a, b) for a, b, w in r["metrics"]["levels"]["module"]["dsm"]["event"]}
    assert ev == {("module:gamma", "module:alpha")}


def test_hidden_coupling_ignores_bulk_commit(pyrepo, tmp_path):
    r = run(pyrepo, tmp_path)
    hidden = r["hidden_coupling"]
    assert [(h["a"], h["b"], h["support"]) for h in hidden] == [("delta", "epsilon", 5)]
    assert hidden[0]["confidence_a_to_b"] == 1.0
    # the bulk commit touches alpha and gamma but must not couple them
    pairs = {(c["a"], c["b"]): c["support"] for c in r["change_coupling"]}
    assert pairs.get(("alpha", "gamma"), 0) == 1  # only the initial commit
    assert r["churn"]["commits_skipped_as_bulk"] == 1


def test_function_complexity_known_values(pyrepo, tmp_path):
    r = run(pyrepo, tmp_path)
    assert r["metrics"]["totals"]["functions"] >= 5
    mod = r["metrics"]["levels"]["module"]["node_metrics"]
    assert mod["module:beta"]["max_complexity"] == 3  # for + if + 1
    assert mod["module:alpha"]["max_complexity"] == 2


def test_martin_metrics(pyrepo, tmp_path):
    nm = run(pyrepo, tmp_path)["metrics"]["levels"]["module"]["node_metrics"]
    assert nm["module:gamma"]["Ce"] == 1 and nm["module:gamma"]["Ca"] == 0
    assert nm["module:gamma"]["instability"] == 1.0
    assert nm["module:delta"]["instability"] is None  # isolated: Ca+Ce == 0
    assert nm["module:alpha"]["abstractness"] == 0.0  # Topics(Enum) is a concrete class


def test_ownership_hashes_authors(pyrepo, tmp_path):
    r = run(pyrepo, tmp_path)
    own = {o["module"]: o for o in r["ownership"]}
    assert "one@example.com" not in str(r) and "Dev One" not in str(r)
    assert own["delta"]["changes"] == 5


def test_mutual_dependencies_name_the_cycle_edges(pyrepo, tmp_path):
    mod = run(pyrepo, tmp_path)["metrics"]["levels"]["module"]
    assert [(m["a"], m["b"]) for m in mod["mutual_dependencies"]] == [("module:alpha", "module:beta")]


def test_fixed_event_pattern(tmp_path):
    from pathlib import Path
    root = tmp_path / "p"
    (root / "m").mkdir(parents=True)
    (root / "m" / "a.py").write_text("def f():\n    publish_entity_created(1)\n")
    (root / "m" / "b.py").write_text("def g(bus):\n    bus.subscribe('entity.created', None)\n")
    cfg = tmp_path / "c.toml"
    cfg.write_text('[[events.patterns]]\nrole="publish"\ncall="publish_entity_created"\nevent="entity.created"\n'
                   '[[events.patterns]]\nrole="subscribe"\ncall="subscribe"\narg=0\n')
    ctx = build_context(root, load_config(cfg), target={"name": "p"})
    r = analyze_context(ctx)
    inv = {e["name"]: e for e in r["events"]["inventory"]}
    assert inv["entity.created"]["flags"] == []
