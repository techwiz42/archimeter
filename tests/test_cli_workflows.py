import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from archimeter.cli import app
from conftest import EVENT_CFG, commit, git
from imagefix import docker_save, layer

runner = CliRunner()


def test_analyze_local_path_with_no_config(pyrepo, tmp_path):
    out = tmp_path / "out"
    res = runner.invoke(app, ["analyze", str(pyrepo), "--out", str(out)])
    assert res.exit_code == 0, res.output
    m = json.loads((out / "metrics.json").read_text())
    assert m["schema_version"] and m["target"]["commit"] and m["tool"]["name"] == "archimeter"
    assert (out / "report.html").exists() and m["events"] if "events" in m else True
    html = (out / "report.html").read_text()
    assert html.count("cdnjs.cloudflare.com") == 1 and "src=\"http" in html and "<script id=\"data\"" in html
    assert "d3.min.js" in html and "unpkg" not in html


def test_analyze_saved_image_tarball(genapp, tmp_path):
    out = tmp_path / "out"
    res = runner.invoke(app, ["analyze", f"oci:{genapp / 'api.tar'}", "--out", str(out)])
    assert res.exit_code == 0, res.output
    m = json.loads((out / "metrics.json").read_text())
    assert m["target"]["kind"] == "image" and m["target"]["digest"].startswith("sha256:")
    assert m["images"]["api"]["env_names"] == ["DB_PASSWORD", "PATH"]
    assert "super-secret-value-123" not in (out / "metrics.json").read_text()
    assert "super-secret-value-123" not in (out / "report.html").read_text()


def test_hostile_image_tarball_is_rejected_and_nothing_escapes(tmp_path):
    outside = tmp_path / "pwned"
    tar = docker_save(tmp_path / "evil.tar", "evil:1", [layer([("file", "app/../../pwned/x.py", b"x=1")])])
    res = runner.invoke(app, ["analyze", f"oci:{tar}", "--out", str(tmp_path / "o")])
    assert res.exit_code == 2 and "traversal" in res.output
    assert not outside.exists()
    tar2 = docker_save(tmp_path / "evil2.tar", "evil:2", [layer([("symlink", "app/l", str(tmp_path / "pwned")), ("file", "app/l/x.py", b"x")])])
    res = runner.invoke(app, ["analyze", f"oci:{tar2}", "--out", str(tmp_path / "o2")])
    assert res.exit_code == 2 and "symlink" in res.output and not outside.exists()


def test_target_is_never_executed(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    marker = tmp_path / "MARKER"
    (root / "setup.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
    (root / "conftest.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
    (root / "package.json").write_text('{"scripts": {"postinstall": "touch ' + str(marker) + '"}}')
    res = runner.invoke(app, ["analyze", str(root), "--out", str(tmp_path / "o")])
    assert res.exit_code == 0 and not marker.exists()


def test_secrets_never_read(tmp_path):
    root = tmp_path / "proj"
    (root / "secrets").mkdir(parents=True)
    (root / ".env").write_text("API_KEY=sk-live-should-never-appear\n")
    (root / "secrets" / "k.py").write_text("KEY = 'sk-live-should-never-appear'\n")
    (root / "app.py").write_text("x = 1\n")
    res = runner.invoke(app, ["analyze", str(root), "--out", str(tmp_path / "o")])
    assert res.exit_code == 0
    blob = (tmp_path / "o" / "metrics.json").read_text() + (tmp_path / "o" / "report.html").read_text()
    assert "sk-live-should-never-appear" not in blob
    skipped = json.loads((tmp_path / "o" / "metrics.json").read_text())["skipped"]
    assert {"path": "secrets", "reason": "secrets-denylist"} in skipped


def test_unknown_config_key_is_an_error_naming_the_key(pyrepo, tmp_path):
    (tmp_path / "c.toml").write_text("[git]\ngit_windw = 3\n")
    res = runner.invoke(app, ["analyze", str(pyrepo), "--config", str(tmp_path / "c.toml"), "--out", str(tmp_path / "o")])
    assert res.exit_code == 2 and "git.git_windw" in res.output


def test_unrecognised_target_lists_accepted_forms(tmp_path):
    res = runner.invoke(app, ["analyze", "definitely not a target", "--out", str(tmp_path / "o")])
    assert res.exit_code == 2 and "docker://" in res.output


def test_init_writes_commented_config_that_loads(pyrepo, tmp_path):
    res = runner.invoke(app, ["init", str(pyrepo)])
    assert res.exit_code == 0, res.output
    cfgp = pyrepo / "archimeter.toml"
    text = cfgp.read_text()
    assert "# " in text and "[events]" in text and "bus.publish" in text  # hint for the custom bus detected in the code
    from archimeter.config import load_config
    load_config(cfgp)
    again = runner.invoke(app, ["init", str(pyrepo)])
    assert again.exit_code == 2 and "--force" in again.output
    assert runner.invoke(app, ["init", str(pyrepo), "--force"]).exit_code == 0


def test_trend_snapshots_cache_and_user_tree_untouched(pyrepo, tmp_path):
    (pyrepo / "alpha" / "wip.py").write_text("UNCOMMITTED = 1\n")
    before = subprocess.run(["git", "-C", str(pyrepo), "status", "--porcelain"], capture_output=True, text=True).stdout
    head = subprocess.run(["git", "-C", str(pyrepo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    out = tmp_path / "t"
    res = runner.invoke(app, ["trend", str(pyrepo), "--since", "2026-01-01", "--step", "monthly", "--out", str(out)])
    assert res.exit_code == 0, res.output
    t = json.loads((out / "trend.json").read_text())
    assert [s["date"] for s in t["snapshots"]] == ["2026-01-01", "2026-02-01", "2026-03-01"]
    hc = [s["metrics"]["hidden_coupling"] for s in t["snapshots"]]
    assert hc == [0, 0, 1]  # delta/epsilon only reach the support threshold by March
    assert [s["metrics"]["loc"] for s in t["snapshots"]][0] < [s["metrics"]["loc"] for s in t["snapshots"]][2]
    assert (out / "trend.html").exists()
    assert subprocess.run(["git", "-C", str(pyrepo), "status", "--porcelain"], capture_output=True, text=True).stdout == before
    assert subprocess.run(["git", "-C", str(pyrepo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout == head
    assert (pyrepo / "alpha" / "wip.py").read_text() == "UNCOMMITTED = 1\n"
    assert "worktrees" not in [p.name for p in (pyrepo / ".git").iterdir()] or not list((pyrepo / ".git" / "worktrees").iterdir())
    again = runner.invoke(app, ["trend", str(pyrepo), "--since", "2026-01-01", "--step", "monthly", "--out", str(out)])
    assert again.output.count("(cached)") == 3


def test_compare_two_generated_apps(genapp, framework, tmp_path):
    import shutil
    other = tmp_path / "genapp2"
    shutil.copytree(genapp, other)
    (other / "worker.tar").unlink()
    (other / "docker-compose.yml").write_text((genapp / "docker-compose.yml").read_text().replace("  worker:\n    image: acme/worker:1.0\n    depends_on: [db, redis]\n    environment:\n      DB_HOST: db\n      REDIS_HOST: redis\n", ""))
    out = tmp_path / "cmp"
    res = runner.invoke(app, ["compare", str(genapp), str(other), "--framework", str(framework), "--out", str(out)])
    assert res.exit_code == 0, res.output
    c = json.loads((out / "compare.json").read_text())
    assert len(c["applications"]) == 2
    names = [a["name"] for a in c["applications"]]
    assert names[0] != names[1]
    big = next(a for a in c["applications"] if a["headline"]["services"] == 4)
    small = next(a for a in c["applications"] if a["headline"]["services"] == 3)
    assert c["most_complex"] == big["name"] and c["least_complex"] == small["name"]
    assert big["headline"]["shared_database_couplings"] == 1 and small["headline"]["shared_database_couplings"] == 0
    assert big["headline"]["loc"] > small["headline"]["loc"]
    assert (out / "compare.html").exists()
    mods = {m["module"] for m in c["marginal"]}
    assert "modules/shared" in mods


def test_hostile_git_config_in_target_never_executes(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, "init", {"a.py": "x = 1\n"}, "2026-01-01T10:00:00+00:00")
    marker = tmp_path / "RAN"
    with open(root / ".git" / "config", "a") as f:
        f.write(f"[core]\n\tfsmonitor = touch {marker}\n\thooksPath = {root}/hooks\n")
    (root / ".git" / "hooks").mkdir(exist_ok=True)
    hook = root / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    res = runner.invoke(app, ["analyze", str(root), "--out", str(tmp_path / "o")])
    assert res.exit_code == 0 and not marker.exists()


def test_packaged_templates_present():
    from importlib import resources
    for name in ("report.html", "compare.html", "trend.html"):
        assert resources.files("archimeter.report").joinpath(name).read_text().count("__DATA__") == 1
