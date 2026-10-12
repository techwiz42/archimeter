import json
from pathlib import Path

from archimeter.config import load_config, find_config
from archimeter.resolve import resolve_target
from archimeter.pipeline import analyze_context, build_context


def analyze(path: Path, framework: Path | None = None):
    with resolve_target(str(path)) as rt:
        cfg = load_config(find_config(rt.root))
        if framework:
            cfg.provenance.framework = str(framework)
        ctx = build_context(rt.analysis_root, cfg, target=rt.identity, kind=rt.mode)
        rt.prepare(ctx)
        return analyze_context(ctx)


def test_service_graph_and_cross_service_event(genapp):
    r = analyze(genapp)
    assert set(r["service_graph"]["services"]) == {"api", "worker", "db", "redis"}
    edges = {(e["from"], e["to"]) for e in r["service_graph"]["edges"]}
    assert {("api", "db"), ("api", "redis"), ("worker", "db"), ("worker", "redis")} <= edges
    ev = {e["name"]: e for e in r["events"]["inventory"]}
    assert ev["job.created"]["flags"] == []
    assert ev["job.created"]["publishers"][0]["module"].startswith("api/")
    assert ev["job.created"]["subscribers"][0]["module"].startswith("worker/")
    # the event becomes a service-level edge worker -> api
    sv = r["metrics"]["levels"]["service"]["dsm"]["event"]
    assert [(a, b) for a, b, w in sv] == [("service:worker", "service:api")]


def test_shared_database_flagged_and_dangling_events(genapp):
    r = analyze(genapp)
    shared = [f for f in r["findings"] if f["type"] == "shared-database"]
    assert len(shared) == 1 and shared[0]["infrastructure"] == "db" and shared[0]["services"] == ["api", "worker"]
    assert shared[0]["severity"] == "high"
    ev = {e["name"]: e["flags"] for e in r["events"]["inventory"]}
    assert ev["api.orphan"] == ["no_subscriber"] and ev["ghost.event"] == ["never_published"]


def test_env_values_and_secret_files_never_leak(genapp, tmp_path):
    r = analyze(genapp)
    blob = json.dumps(r)
    assert "super-secret-value-123" not in blob
    assert "DB_PASSWORD" in r["service_graph"]["services"]["api"]["env_names"]
    assert "DB_PASSWORD" in r["images"]["api"]["env_names"]
    assert not any(f.endswith(".env") for f in json.dumps(r["metrics"]).split('"'))


def test_application_code_separated_from_base_and_third_party(genapp):
    r = analyze(genapp)
    sep = r["uncertainty"]["api"]
    assert sep["strategy"] == "workdir" and sep["app_roots"] == ["/app"] and not sep["uncertain"]
    assert sep["not_analyzed"]["third_party_files"] >= 1 and sep["not_analyzed"]["os_files"] >= 1
    assert sep["not_analyzed"]["secret_files_never_read"] == 1
    inv = r["dependency_inventory"]["api"]
    assert {"ecosystem": "pypi", "name": "requests", "version": "2.31.0", "evidence": "requests-2.31.0.dist-info"} in inv
    # only application files were analyzed
    files = {n.removeprefix("file:") for n in json.dumps(r["metrics"]["levels"]["module"]["nodes"]).split('"') if n.startswith("file:")}
    mods = r["metrics"]["levels"]["module"]["nodes"]
    assert all(m.startswith(("module:api/", "module:worker/")) for m in mods)


def test_provenance_copied_modified_new(genapp, framework):
    r = analyze(genapp, framework)
    pf = r["provenance"]["files"]
    assert pf["api/modules/shared/util.py"]["class"] == "copied"
    assert pf["worker/modules/shared/util.py"]["class"] == "copied"
    assert pf["api/modules/shared/models.py"]["class"] == "modified"
    assert pf["api/modules/shared/models.py"]["similarity"] >= 0.6
    assert pf["api/api/main.py"]["class"] == "new" and pf["worker/worker/main.py"]["class"] == "new"
    s = r["provenance"]["summary"]
    assert s["copied"]["files"] >= 2 and s["modified"]["files"] == 1 and s["new"]["files"] >= 2
    assert "modules/shared" in r["provenance"]["by_framework_module"]
    assert r["provenance"]["source"] == "framework"
    d = {x["event"] for x in r["provenance"]["dangling_events"]}
    assert d == {"api.orphan", "ghost.event"}


def test_provenance_manifest_takes_precedence(genapp, tmp_path):
    (genapp / "archimeter-provenance.json").write_text(json.dumps({
        "format": "archimeter-provenance/1", "generator": {"name": "fixture", "version": "1"},
        "files": {"api/api/main.py": {"module": "glue", "template": "main.py.j2", "version": "9", "kind": "rendered"}},
    }))
    r = analyze(genapp)
    f = r["provenance"]["files"]["api/api/main.py"]
    assert f["class"] == "modified" and f["via"] == "manifest" and f["template"] == "main.py.j2"
    assert r["provenance"]["source"] == "manifest"
