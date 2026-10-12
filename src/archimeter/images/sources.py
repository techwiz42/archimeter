"""Image sources: saved tarballs (docker save / OCI layout) and registries. All read-only; nothing runs."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterator

from ..errors import ArchimeterError, UnsafeArchiveError


@dataclass
class ImageInfo:
    ref: str
    digest: str  # sha256 of the manifest / config
    config: dict
    layer_digests: list[str] = field(default_factory=list)
    repo_tags: list[str] = field(default_factory=list)

    @property
    def runtime(self) -> dict:
        return self.config.get("config") or self.config.get("Config") or {}

    def env_names(self) -> list[str]:
        return sorted({e.split("=", 1)[0] for e in (self.runtime.get("Env") or [])})  # values deliberately dropped

    def summary(self) -> dict:
        rt = self.runtime
        return {
            "ref": self.ref, "digest": self.digest, "repo_tags": self.repo_tags,
            "entrypoint": rt.get("Entrypoint") or [], "cmd": rt.get("Cmd") or [], "workdir": rt.get("WorkingDir") or "/",
            "user": rt.get("User") or "", "exposed_ports": sorted((rt.get("ExposedPorts") or {}).keys()),
            "env_names": self.env_names(), "architecture": self.config.get("architecture"), "os": self.config.get("os"),
            "layers": len(self.layer_digests),
        }


def _open_layer(raw: BinaryIO) -> BinaryIO:
    """Layers may be gzip, zstd or plain tar; tarfile stream mode auto-detects gzip/bz2/xz, we handle zstd."""
    head = raw.read(4)
    raw.seek(0)
    if head[:4] == b"\x28\xb5\x2f\xfd":
        try:
            import zstandard  # optional
        except ImportError:
            raise ArchimeterError("layer is zstd-compressed; install 'zstandard' to read it") from None
        return zstandard.ZstdDecompressor().stream_reader(raw)
    return raw


class TarballImage:
    """`docker save` tarball, OCI image-layout tarball, or OCI layout directory."""

    def __init__(self, path: Path):
        self.path = path
        if path.is_dir():
            self._dir = path
            self._tar = None
        else:
            self._dir = None
            try:
                self._tar = tarfile.open(path, "r:*")
            except tarfile.TarError as e:
                raise ArchimeterError(f"{path} is not a readable tar archive: {e}") from None
        self.infos = self._load()

    # -- raw access (members are read, never extracted) --
    def _read(self, name: str) -> bytes:
        name = name.lstrip("./")
        if self._dir is not None:
            p = (self._dir / name)
            if not str(p.resolve()).startswith(str(self._dir.resolve())):
                raise UnsafeArchiveError(f"layout reference escapes the image directory: {name!r}")
            return p.read_bytes()
        try:
            f = self._tar.extractfile(self._tar.getmember(name))
        except KeyError:
            try:
                f = self._tar.extractfile(self._tar.getmember("./" + name))
            except KeyError:
                raise ArchimeterError(f"{self.path}: missing member {name}") from None
        if f is None:
            raise ArchimeterError(f"{self.path}: member {name} is not a regular file")
        return f.read()

    def _open_stream(self, name: str) -> BinaryIO:
        name = name.lstrip("./")
        if self._dir is not None:
            p = (self._dir / name)
            if not str(p.resolve()).startswith(str(self._dir.resolve())):
                raise UnsafeArchiveError(f"layout reference escapes the image directory: {name!r}")
            return open(p, "rb")
        try:
            m = self._tar.getmember(name)
        except KeyError:
            m = self._tar.getmember("./" + name)
        f = self._tar.extractfile(m)
        if f is None:
            raise ArchimeterError(f"{self.path}: member {name} is not a regular file")
        return io.BufferedReader(f) if not f.seekable() else f

    def _names(self) -> set[str]:
        if self._dir is not None:
            return {p.relative_to(self._dir).as_posix() for p in self._dir.rglob("*") if p.is_file()}
        return {m.name.lstrip("./") for m in self._tar.getmembers()}

    def _load(self) -> list[ImageInfo]:
        names = self._names()
        infos: list[ImageInfo] = []
        if "manifest.json" in names:  # docker save
            for entry in json.loads(self._read("manifest.json")):
                cfg_bytes = self._read(entry["Config"])
                infos.append(ImageInfo(
                    ref=(entry.get("RepoTags") or [self.path.name])[0],
                    digest="sha256:" + hashlib.sha256(cfg_bytes).hexdigest(),
                    config=json.loads(cfg_bytes), layer_digests=list(entry["Layers"]),
                    repo_tags=list(entry.get("RepoTags") or [])))
            return infos
        if "index.json" in names:  # OCI layout
            index = json.loads(self._read("index.json"))
            for desc in index.get("manifests", []):
                infos += self._oci_manifest(desc)
            return infos
        raise ArchimeterError(f"{self.path}: neither manifest.json (docker save) nor index.json (OCI layout) found")

    def _blob(self, digest: str) -> bytes:
        algo, _, hexd = digest.partition(":")
        data = self._read(f"blobs/{algo}/{hexd}")
        if algo == "sha256" and hashlib.sha256(data).hexdigest() != hexd:
            raise UnsafeArchiveError(f"blob {digest} failed digest verification")
        return data

    def _oci_manifest(self, desc: dict) -> list[ImageInfo]:
        mt = desc.get("mediaType", "")
        data = json.loads(self._blob(desc["digest"]))
        if "index" in mt or "manifest.list" in mt or "manifests" in data:
            out = []
            for d in data.get("manifests", []):
                plat = d.get("platform") or {}
                if plat.get("os") in (None, "linux"):
                    out += self._oci_manifest(d)
            return out[:1] or []
        cfg = json.loads(self._blob(data["config"]["digest"]))
        ref = (desc.get("annotations") or {}).get("org.opencontainers.image.ref.name") or desc["digest"]
        return [ImageInfo(ref=ref, digest=desc["digest"], config=cfg,
                          layer_digests=[l["digest"] for l in data["layers"]], repo_tags=[ref])]

    def layers(self, info: ImageInfo) -> Iterator[BinaryIO]:
        for ld in info.layer_digests:
            if ld.startswith("sha256:") or ":" in ld:
                algo, _, hexd = ld.partition(":")
                name = f"blobs/{algo}/{hexd}"
                # verify before use (memory-bounded: hash in chunks)
                h = hashlib.sha256()
                with self._open_stream(name) as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                if algo == "sha256" and h.hexdigest() != hexd:
                    raise UnsafeArchiveError(f"layer {ld} failed digest verification")
                yield _wrap(self._open_stream(name))
            else:
                yield _wrap(self._open_stream(ld))

    def close(self) -> None:
        if self._tar is not None:
            self._tar.close()


def _wrap(f: BinaryIO) -> BinaryIO:
    return _open_layer(f) if f.seekable() else f
