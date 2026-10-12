"""Builders for synthetic image tarballs (docker-save format) and hostile layers."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path


def layer(members: list[tuple]) -> bytes:
    """members: ("file", name, bytes) | ("dir", name) | ("symlink", name, target) | ("hardlink", name, target)
    | ("fifo", name) | ("sparse-size", name, declared_size, real_bytes)"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for m in members:
            kind, name = m[0], m[1]
            ti = tarfile.TarInfo(name)
            if kind == "file":
                ti.size = len(m[2])
                tf.addfile(ti, io.BytesIO(m[2]))
            elif kind == "dir":
                ti.type = tarfile.DIRTYPE
                tf.addfile(ti)
            elif kind == "symlink":
                ti.type = tarfile.SYMTYPE
                ti.linkname = m[2]
                tf.addfile(ti)
            elif kind == "hardlink":
                ti.type = tarfile.LNKTYPE
                ti.linkname = m[2]
                tf.addfile(ti)
            elif kind == "fifo":
                ti.type = tarfile.FIFOTYPE
                tf.addfile(ti)
            elif kind == "big":  # file of m[2] zero bytes (compresses extremely well: a bomb when gzipped)
                ti.size = m[2]
                tf.addfile(ti, _Zeros(m[2]))
            else:
                raise ValueError(kind)
    return buf.getvalue()


class _Zeros(io.RawIOBase):
    def __init__(self, n):
        self.n = n

    def readable(self):
        return True

    def readinto(self, b):
        k = min(len(b), self.n)
        b[:k] = bytes(k)
        self.n -= k
        return k


def docker_save(path: Path, repo_tag: str, layers: list[bytes], *, entrypoint=None, cmd=None, workdir="/app",
                env=None, ports=None, gz=True) -> Path:
    cfg = {"architecture": "amd64", "os": "linux",
           "config": {"Entrypoint": entrypoint, "Cmd": cmd, "WorkingDir": workdir, "Env": env or [],
                      "ExposedPorts": {f"{p}/tcp": {} for p in (ports or [])}},
           "rootfs": {"type": "layers", "diff_ids": []}}
    cfg_bytes = json.dumps(cfg).encode()
    cfg_name = hashlib.sha256(cfg_bytes).hexdigest() + ".json"
    names, blobs = [], {}
    for i, l in enumerate(layers):
        data = gzip.compress(l) if gz else l
        n = f"layer{i}/layer.tar"
        names.append(n)
        blobs[n] = data
    with tarfile.open(path, "w") as tf:
        def add(name, data):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        add(cfg_name, cfg_bytes)
        for n, d in blobs.items():
            add(n, d)
        add("manifest.json", json.dumps([{"Config": cfg_name, "RepoTags": [repo_tag], "Layers": names}]).encode())
    return path
