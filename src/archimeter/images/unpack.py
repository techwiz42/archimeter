"""Hardened image-layer flattening. Layers are *read as files*; nothing is executed.

Guarantees:
  - absolute paths, `..` components and NUL bytes in member names are rejected;
  - symlinks are never materialised on disk (recorded only); a later member that would be written
    *through* a recorded symlink is rejected, so a layer cannot redirect writes outside the root;
  - device nodes, fifos and sockets are never created;
  - hardlinks are only honoured when the target is an already-extracted regular file inside the root;
  - total uncompressed bytes (including skipped members) and file counts are capped (decompression bombs);
  - files matching the secrets denylist are never written;
  - whiteouts and opaque whiteouts are applied so deleted files stay deleted.
"""
from __future__ import annotations

import os
import posixpath
import shutil
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable

from ..errors import UnsafeArchiveError
from ..safety import is_secret, LANG_BY_EXT

DEFAULT_MAX_TOTAL = 4 * 1024**3
DEFAULT_MAX_FILES = 2_000_000
DEFAULT_MAX_FILE = 50 * 1024 * 1024

OS_SKIP_PREFIXES = (
    "proc/", "sys/", "dev/", "run/", "tmp/", "boot/", "etc/", "var/lib/", "var/cache/", "var/log/", "var/run/",
    "usr/share/", "usr/lib/", "usr/lib64/", "usr/bin/", "usr/sbin/", "usr/include/", "usr/libexec/", "usr/local/lib/python",
    "usr/local/lib/node_modules/", "usr/local/include/", "bin/", "sbin/", "lib/", "lib64/", "root/.cache/", "root/.npm/",
)
THIRD_PARTY_MARKERS = ("site-packages", "dist-packages", "node_modules")
METADATA_NAMES = {"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock",
                  "requirements.txt", "requirements.lock", "pyproject.toml", "go.mod", "Cargo.lock"}
APP_EXTS = set(LANG_BY_EXT)


@dataclass
class Layout:
    """What the flattened tree contains, and what was deliberately left out."""
    symlinks: dict[str, str] = field(default_factory=dict)
    skipped_special: int = 0
    skipped_os_files: int = 0
    skipped_third_party_files: int = 0
    skipped_secret_files: int = 0
    skipped_large_files: int = 0
    total_bytes_seen: int = 0
    files_written: int = 0


def _clean_name(raw: str) -> str:
    if "\x00" in raw:
        raise UnsafeArchiveError(f"archive member name contains NUL: {raw!r}")
    if raw.startswith("/"):
        raise UnsafeArchiveError(f"absolute path in archive: {raw!r}")
    name = posixpath.normpath(raw.lstrip("./")) if raw not in (".", "./") else "."
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise UnsafeArchiveError(f"path traversal in archive member: {raw!r}")
    return "/".join(parts) if parts else "."


def classify(path: str) -> str:
    """'metadata' | 'app' | 'skip-os' | 'skip-third-party' | 'skip'"""
    parts = path.split("/")
    base = parts[-1]
    for i, seg in enumerate(parts[:-1]):
        if seg in THIRD_PARTY_MARKERS:
            rest = parts[i + 1:]
            if seg == "node_modules":
                depth_ok = (len(rest) == 2 and rest[1] == "package.json") or \
                           (len(rest) == 3 and rest[0].startswith("@") and rest[2] == "package.json")
                return "metadata" if depth_ok else "skip-third-party"
            ok = (len(rest) == 2 and rest[0].endswith((".dist-info", ".egg-info")) and rest[1] in ("METADATA", "PKG-INFO", "top_level.txt"))
            return "metadata" if ok else "skip-third-party"
    if any(path.startswith(p) for p in OS_SKIP_PREFIXES):
        return "skip-os"
    if base in METADATA_NAMES:
        return "metadata"
    if os.path.splitext(base)[1].lower() in APP_EXTS:
        return "app"
    return "skip"


def _within(root: Path, rel: str) -> Path:
    target = Path(os.path.normpath(root / rel))
    if not str(target).startswith(str(root) + os.sep) and target != root:
        raise UnsafeArchiveError(f"member escapes extraction root: {rel!r}")
    return target


def _through_symlink(layout: Layout, rel: str) -> str | None:
    parts = rel.split("/")
    for i in range(1, len(parts)):
        p = "/".join(parts[:i])
        if p in layout.symlinks:
            return p
    return None


def apply_layer(stream: BinaryIO, root: Path, layout: Layout, *, max_total: int = DEFAULT_MAX_TOTAL,
                max_files: int = DEFAULT_MAX_FILES, max_file: int = DEFAULT_MAX_FILE,
                extra_deny: list[str] | None = None, keep: Callable[[str], str] | None = None) -> None:
    """Apply one (already decompressed or auto-detected) tar layer onto `root`."""
    root = Path(os.path.realpath(root))
    classify_fn = keep or classify
    with tarfile.open(fileobj=stream, mode="r|*") as tf:
        for m in tf:
            name = _clean_name(m.name)
            if name == ".":
                continue
            base = posixpath.basename(name)
            parent = posixpath.dirname(name)

            if base.startswith(".wh."):
                if base == ".wh..wh..opq":
                    d = _within(root, parent) if parent else root
                    if d.is_dir():
                        for child in list(d.iterdir()):
                            _remove(child)
                    for k in [k for k in layout.symlinks if parent and k.startswith(parent + "/")]:
                        del layout.symlinks[k]
                else:
                    victim = posixpath.join(parent, base[4:])
                    _remove(_within(root, victim))
                    layout.symlinks.pop(victim, None)
                continue

            hit = _through_symlink(layout, name)
            if hit is not None:
                raise UnsafeArchiveError(
                    f"archive member {name!r} would be written through symlink {hit!r} -> {layout.symlinks[hit]!r}")

            layout.total_bytes_seen += max(m.size, 0) if m.isreg() else 0
            if layout.total_bytes_seen > max_total:
                raise UnsafeArchiveError(f"extracted size exceeds cap of {max_total} bytes")
            if layout.files_written > max_files:
                raise UnsafeArchiveError(f"file count exceeds cap of {max_files}")

            if m.issym():
                layout.symlinks[name] = m.linkname
                _remove(_within(root, name))
                continue
            if m.isdev() or m.isfifo() or not (m.isreg() or m.isdir() or m.islnk()):
                layout.skipped_special += 1
                continue
            if m.isdir():
                continue  # directories are created on demand when files are written
            if m.islnk():
                src = _clean_name(m.linkname)
                src_path = _within(root, src)
                if src_path.is_file() and not src_path.is_symlink() and classify_fn(name) in ("app", "metadata"):
                    dest = _within(root, name)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src_path, dest)
                    layout.files_written += 1
                continue

            if is_secret(name, extra_deny):
                layout.skipped_secret_files += 1
                _remove(_within(root, name))
                continue
            kind = classify_fn(name)
            if kind == "skip-os":
                layout.skipped_os_files += 1
            elif kind == "skip-third-party":
                layout.skipped_third_party_files += 1
            if kind not in ("app", "metadata"):
                _remove(_within(root, name))  # a later layer replaced an earlier file we kept
                continue
            if m.size > max_file:
                layout.skipped_large_files += 1
                continue
            dest = _within(root, name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.is_symlink() or dest.is_dir():
                _remove(dest)
            f = tf.extractfile(m)
            if f is None:
                continue
            with open(dest, "wb") as out:
                written = 0
                while chunk := f.read(1024 * 1024):
                    written += len(chunk)
                    if written > max_file:
                        break
                    out.write(chunk)
            os.chmod(dest, 0o644)
            layout.files_written += 1


def _remove(p: Path) -> None:
    try:
        if p.is_symlink() or p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass
