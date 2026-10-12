import time
from pathlib import Path

from archimeter.config import load_config
from archimeter.pipeline import analyze_context, build_context


def make_project(root: Path, files: int = 400, funcs_per_file: int = 25) -> int:
    """~100k physical lines: 40 packages x 10 modules, cross-package imports, branches and loops in every function."""
    lines_total = 0
    for i in range(files):
        pkg, mod = f"pkg{i % 40}", f"m{i // 40}"
        d = root / pkg
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").touch()
        body = []
        for k in range(2):
            j = (i * 7 + k * 13 + 1) % files
            body.append(f"from pkg{j % 40}.m{j // 40} import f0 as dep{k}\n")
        for f in range(funcs_per_file):
            body.append(
                f"\n\ndef f{f}(a, b=0):\n"
                f"    total = 0\n"
                f"    for i in range(a):\n"
                f"        if i % 2 == 0 and b:\n"
                f"            total += i * {f}\n"
                f"        elif i % 3 == 0:\n"
                f"            total -= b\n"
                f"        else:\n"
                f"            total += 1\n"
                f"    try:\n"
                f"        return total // (b or 1)\n"
                f"    except ZeroDivisionError:\n"
                f"        return 0\n")
        text = "".join(body)
        (d / f"{mod}.py").write_text(text)
        lines_total += text.count("\n")
    return lines_total


def test_100k_lines_in_under_two_minutes(tmp_path):
    root = tmp_path / "big"
    n = make_project(root)
    assert n >= 100_000
    t0 = time.monotonic()
    ctx = build_context(root, load_config(None), target={"name": "big"})
    r = analyze_context(ctx)
    elapsed = time.monotonic() - t0
    assert r["metrics"]["totals"]["files"] >= 400 and r["metrics"]["totals"]["functions"] >= 10_000
    assert elapsed < 120, f"took {elapsed:.0f}s"
    print(f"\n100k-line project analysed in {elapsed:.1f}s")
