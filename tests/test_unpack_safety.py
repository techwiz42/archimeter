import io
from pathlib import Path

import pytest

from archimeter.errors import UnsafeArchiveError
from archimeter.images.unpack import Layout, apply_layer
from imagefix import layer


def unpack(layers, tmp_path, **kw):
    root = tmp_path / "root"
    root.mkdir()
    lay = Layout()
    for l in layers:
        apply_layer(io.BytesIO(l), root, lay, **kw)
    return root, lay


def test_rejects_path_traversal(tmp_path):
    with pytest.raises(UnsafeArchiveError, match="traversal"):
        unpack([layer([("file", "app/../../escape.py", b"x = 1\n")])], tmp_path)
    assert not (tmp_path / "escape.py").exists()


def test_rejects_absolute_path(tmp_path):
    with pytest.raises(UnsafeArchiveError, match="absolute"):
        unpack([layer([("file", "/etc/cron.d/evil.py", b"x")])], tmp_path)


def test_symlink_is_never_materialised_and_write_through_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    layers = [layer([("symlink", "app/link", str(outside))]), layer([("file", "app/link/pwn.py", b"x = 1\n")])]
    with pytest.raises(UnsafeArchiveError, match="through symlink"):
        unpack(layers, tmp_path)
    assert list(outside.iterdir()) == []


def test_symlink_to_absolute_etc_cannot_be_used_for_writes(tmp_path):
    with pytest.raises(UnsafeArchiveError):
        unpack([layer([("symlink", "app/etc", "/etc"), ("file", "app/etc/passwd.py", b"x")])], tmp_path)


def test_symlinks_are_not_created_on_disk(tmp_path):
    root, lay = unpack([layer([("file", "app/real.py", b"x = 1\n"), ("symlink", "app/alias.py", "real.py")])], tmp_path)
    assert (root / "app/real.py").exists()
    assert not (root / "app/alias.py").exists() and not (root / "app/alias.py").is_symlink()
    assert lay.symlinks == {"app/alias.py": "real.py"}


def test_oversized_layer_aborts(tmp_path):
    with pytest.raises(UnsafeArchiveError, match="exceeds cap"):
        unpack([layer([("big", "app/huge.py", 5 * 1024 * 1024)])], tmp_path, max_total=1024 * 1024, max_file=10 * 1024 * 1024)


def test_oversized_counts_skipped_members_too(tmp_path):
    with pytest.raises(UnsafeArchiveError, match="exceeds cap"):
        unpack([layer([("big", "usr/share/blob.bin", 5 * 1024 * 1024)])], tmp_path, max_total=1024 * 1024)


def test_whiteout_removes_earlier_file(tmp_path):
    root, _ = unpack([layer([("file", "app/a.py", b"1"), ("file", "app/b.py", b"2")]), layer([("file", "app/.wh.a.py", b"")])], tmp_path)
    assert not (root / "app/a.py").exists() and (root / "app/b.py").exists()


def test_opaque_whiteout_clears_directory(tmp_path):
    root, _ = unpack([layer([("file", "app/old/a.py", b"1")]), layer([("file", "app/old/.wh..wh..opq", b""), ("file", "app/old/new.py", b"2")])], tmp_path)
    assert not (root / "app/old/a.py").exists() and (root / "app/old/new.py").exists()


def test_devices_and_fifos_skipped(tmp_path):
    root, lay = unpack([layer([("fifo", "app/pipe"), ("file", "app/ok.py", b"1")])], tmp_path)
    assert lay.skipped_special == 1 and (root / "app/ok.py").exists()


def test_secret_files_never_written(tmp_path):
    root, lay = unpack([layer([("file", "app/.env", b"PASSWORD=hunter2"), ("file", "app/secrets/key.py", b"K='x'"), ("file", "app/ok.py", b"1")])], tmp_path)
    assert not (root / "app/.env").exists() and not (root / "app/secrets").exists()
    assert lay.skipped_secret_files == 2


def test_third_party_and_os_files_not_extracted_but_metadata_kept(tmp_path):
    root, lay = unpack([layer([
        ("file", "usr/local/lib/python3.11/site-packages/requests/api.py", b"x"),
        ("file", "usr/local/lib/python3.11/site-packages/requests-2.31.0.dist-info/METADATA", b"Name: requests\nVersion: 2.31.0\n"),
        ("file", "usr/lib/python3/os_thing.py", b"x"),
        ("file", "app/node_modules/left-pad/index.js", b"x"),
        ("file", "app/node_modules/left-pad/package.json", b'{"name":"left-pad","version":"1.3.0"}'),
        ("file", "app/main.py", b"print(1)"),
    ])], tmp_path)
    assert (root / "app/main.py").exists()
    assert (root / "usr/local/lib/python3.11/site-packages/requests-2.31.0.dist-info/METADATA").exists()
    assert (root / "app/node_modules/left-pad/package.json").exists()
    assert not (root / "usr/local/lib/python3.11/site-packages/requests/api.py").exists()
    assert not (root / "app/node_modules/left-pad/index.js").exists()
    assert lay.skipped_third_party_files == 2 and lay.skipped_os_files >= 1


def test_hardlink_escaping_root_rejected(tmp_path):
    with pytest.raises(UnsafeArchiveError, match="traversal"):
        unpack([layer([("hardlink", "app/h.py", "../../etc/passwd")])], tmp_path)
