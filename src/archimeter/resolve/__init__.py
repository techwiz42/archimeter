"""Target resolution: path / GitHub / image / tarball / build-output -> a local file tree."""
from __future__ import annotations

import contextlib
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from ..errors import ArchimeterError


@dataclass
class ResolvedTarget:
    root: Path
    analysis_root: Path
    kind: str  # path | github | image | build-output
    mode: str  # source | generated
    identity: dict[str, Any]
    preparers: list[Callable] = field(default_factory=list)

    def prepare(self, ctx) -> None:
        for p in self.preparers:
            p(ctx)


def _git_head(path: Path) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def looks_like_build_output(p: Path) -> bool:
    names = {c.name for c in p.iterdir()} if p.is_dir() else set()
    return bool(names & {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}) or \
        any(n.endswith("Dockerfile") or n == "Dockerfile" for n in names)


@contextlib.contextmanager
def resolve_target(target: str, *, submodules: bool = False) -> Iterator[ResolvedTarget]:
    if target.startswith("docker://") or target.startswith("oci:"):
        with _image_target(target) as rt:
            yield rt
        return
    p = Path(target).expanduser()
    if not p.exists():
        from .github import clone_or_update, parse_github
        gh = parse_github(target)
        if gh:
            owner, repo, ref = gh
            path, sha, resolved = clone_or_update(owner, repo, ref, submodules=submodules)
            yield ResolvedTarget(path, path, "github", "source", {
                "kind": "github", "repo": f"{owner}/{repo}", "ref": resolved, "commit": sha, "name": repo})
            return
    if p.exists():
        if not p.is_dir():
            raise ArchimeterError(f"target is not a directory: {target}")
        p = p.resolve()
        if looks_like_build_output(p):
            with tempfile.TemporaryDirectory(prefix="archimeter-img-") as tmp:
                rt = ResolvedTarget(p, p, "build-output", "generated", {"kind": "build-output", "path": p.name, "name": p.name})
                rt.preparers.append(lambda ctx, tmp=Path(tmp): _attach_image_services(ctx, tmp))
                yield rt
        else:
            ident: dict[str, Any] = {"kind": "path", "path": p.name, "name": p.name}
            sha = _git_head(p)
            if sha:
                ident["commit"] = sha
            yield ResolvedTarget(p, p, "path", "source", ident)
        return
    raise ArchimeterError(
        f"cannot resolve target '{target}'. Accepted: a local directory, owner/repo[@ref], https://github.com/owner/repo[@ref], "
        "docker://image:tag, oci:/path/image.tar, or a build-output directory")


def _norm_ref(ref: str) -> str:
    r = ref.removeprefix("docker://").removeprefix("docker.io/").removeprefix("library/")
    last = r.rsplit("/", 1)[-1]
    if ":" not in last and "@" not in r:
        r += ":latest"
    return r


def _service_name(ref: str) -> str:
    base = ref.removeprefix("docker://").split("@")[0]
    base = base.rsplit("/", 1)[-1].split(":")[0]
    return re.sub(r"[^A-Za-z0-9_.-]", "-", base) or "image"


def _record_image(ctx, name: str, flat, prefix: str | None) -> None:
    svc = {"name": name, "kind": "app", "image": flat.summary["ref"], "ports": [p.split("/")[0] for p in flat.summary["exposed_ports"]],
           "env_names": flat.summary["env_names"], "entrypoint": (flat.summary["entrypoint"] or flat.summary["cmd"] or [None])[0],
           "workdir": flat.summary["workdir"], "user": flat.summary["user"], "source": "image:" + flat.summary["digest"]}
    if prefix:
        svc["source_prefix"] = prefix
    ctx.shared.setdefault("image_services", {})[name] = svc
    ctx.shared.setdefault("dependency_inventory", {})[name] = flat.inventory
    ctx.shared.setdefault("uncertainty", {})[name] = flat.separation
    ctx.shared.setdefault("images", {})[name] = flat.summary


@contextlib.contextmanager
def _image_target(target: str) -> Iterator[ResolvedTarget]:
    from ..images.flatten import flatten
    from ..images.registry import RegistryImage, parse_ref
    from ..images.sources import TarballImage
    with tempfile.TemporaryDirectory(prefix="archimeter-img-") as tmp:
        if target.startswith("oci:"):
            path = Path(target[4:]).expanduser()
            if not path.exists():
                raise ArchimeterError(f"image tarball/layout not found: {path}")
            src = TarballImage(path)
            if not src.infos:
                raise ArchimeterError(f"{path} contains no images")
            info = src.infos[0]
            multi = len(src.infos) > 1
        else:
            from ..config import Config
            ref = parse_ref(target)
            src = RegistryImage(ref)
            info = src.resolve()
            multi = False
        holder: dict = {}

        def prep(ctx, src=src, info=info):
            flat = flatten(src, info, Path(tmp) / "flat", ctx.config)
            holder["flat"] = flat
            name = _service_name(info.repo_tags[0] if info.repo_tags else info.ref)
            ctx.target["service"] = name
            ctx.target["name"] = name
            ctx.target["image"] = flat.summary
            _record_image(ctx, name, flat, None)
            if multi:
                ctx.notes.append("the tarball contains several images; only the first was analyzed")
            from ..safety import walk_sources
            w = walk_sources(flat.app_root, max_bytes=ctx.config.analysis.max_file_bytes, skip_vendored=True,
                             extra_deny=ctx.config.security.secrets_denylist, exclude=ctx.config.modules.exclude, skip_generated=False)
            ctx.files = w.files
            ctx.root = flat.app_root
            ctx.shared["skipped"] = w.skipped

        ident = {"kind": "image", "ref": info.ref, "digest": info.digest, "name": _service_name(info.repo_tags[0] if info.repo_tags else info.ref)}
        rt = ResolvedTarget(Path(tmp), Path(tmp), "image", "generated", ident, [prep])
        try:
            yield rt
        finally:
            if hasattr(src, "close"):
                src.close()


def _attach_image_services(ctx, tmp: Path) -> None:
    """Build-output directories may ship saved image tarballs next to a compose file; unpack those matching `image:`."""
    from ..extractors.service_deps import ServiceDeps, _load_yaml
    from ..images.flatten import flatten
    from ..images.sources import TarballImage
    from ..safety import SourceFile, walk_sources
    tars = sorted(set(ctx.root.glob("*.tar")) | set((ctx.root / "images").glob("*.tar")))
    if not tars:
        return
    wanted: dict[str, str] = {}
    for cf in ServiceDeps._compose_files(ctx):
        for doc in _load_yaml(cf):
            for name, spec in ((doc or {}).get("services") or {}).items():
                if isinstance(spec, dict) and spec.get("image") and not spec.get("build"):
                    wanted[_norm_ref(spec["image"])] = name
    if not wanted:
        return
    prefixes: set[str] = ctx.shared.setdefault("prefix_roots", set())
    for t in tars:
        try:
            img = TarballImage(t)
        except ArchimeterError:
            continue
        try:
            for info in img.infos:
                for tag in info.repo_tags or [info.ref]:
                    svc = wanted.get(_norm_ref(tag))
                    if not svc:
                        continue
                    flat = flatten(img, info, tmp / svc, ctx.config)
                    w = walk_sources(flat.app_root, max_bytes=ctx.config.analysis.max_file_bytes, skip_vendored=True,
                                     extra_deny=ctx.config.security.secrets_denylist, exclude=ctx.config.modules.exclude,
                                     skip_generated=False)
                    for f in w.files:
                        ctx.files.append(SourceFile(f"{svc}/{f.rel}", f.abs, f.lang, f.size))
                    prefixes.add(svc)
                    _record_image(ctx, svc, flat, svc)
                    break
        finally:
            img.close()
