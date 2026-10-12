"""Service graph from compose files, Kubernetes manifests and Dockerfiles. Environment values are never stored."""
from __future__ import annotations

import fnmatch
import posixpath
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from ..dockerfile import parse_dockerfile
from ..safety import is_secret
from .base import Context, register

COMPOSE_NAMES = ("docker-compose*.yml", "docker-compose*.yaml", "compose*.yml", "compose*.yaml")
INFRA_IMAGES = {
    "database": ("postgres", "mysql", "mariadb", "mongo", "cockroach", "mssql", "oracle", "timescale"),
    "redis": ("redis", "valkey", "keydb", "dragonfly"),
    "broker": ("rabbitmq", "kafka", "redpanda", "nats", "activemq", "pulsar"),
    "proxy": ("nginx", "traefik", "haproxy", "envoy", "caddy"),
}


def classify_image(image: str | None) -> str:
    if not image:
        return "app"
    base = image.split("/")[-1].split(":")[0].split("@")[0].lower()
    for kind, names in INFRA_IMAGES.items():
        if any(n in base for n in names):
            return kind
    return "app"


def _load_yaml(path: Path):
    if is_secret(path.name):
        return []
    try:
        if path.stat().st_size > 2_000_000:
            return []
        return list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError):
        return []


def _env_pairs(env) -> list[tuple[str, str]]:
    """(name, value) pairs. Values are used transiently for service-name matching and are never stored."""
    if isinstance(env, dict):
        return [(str(k), "" if v is None else str(v)) for k, v in env.items()]
    out = []
    for e in env or []:
        if isinstance(e, str):
            k, _, v = e.partition("=")
            out.append((k, v))
    return out


def _host_of(value: str) -> str:
    v = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", "", value.strip())
    v = v.split("@")[-1].split("/")[0].split(":")[0]
    return v


@register
class ServiceDeps:
    name = "service-deps"
    requires = ("modules",)

    def applies(self, ctx: Context) -> bool:
        return ctx.kind == "generated" or bool(self._compose_files(ctx))

    @staticmethod
    def _compose_files(ctx: Context) -> list[Path]:
        out = []
        for pat in COMPOSE_NAMES:
            out += [p for p in ctx.root.glob(pat) if p.is_file()]
        return sorted(set(out))

    @staticmethod
    def _k8s_files(ctx: Context) -> list[Path]:
        out = []
        for d in [ctx.root, ctx.root / "k8s", ctx.root / "kubernetes", ctx.root / "deploy", ctx.root / "manifests"]:
            if d.is_dir():
                out += [p for p in d.rglob("*.y*ml") if p.is_file() and "node_modules" not in p.parts]
        return sorted(set(out))

    def extract(self, ctx: Context) -> None:
        g = ctx.graph
        services: dict[str, dict[str, Any]] = {}
        for cf in self._compose_files(ctx):
            for doc in _load_yaml(cf):
                if isinstance(doc, dict) and isinstance(doc.get("services"), dict):
                    self._compose(ctx, cf, doc, services)
        for kf in self._k8s_files(ctx):
            docs = [d for d in _load_yaml(kf) if isinstance(d, dict) and d.get("kind") and d.get("apiVersion")]
            if docs:
                self._k8s(ctx, kf, docs, services)
        for name, s in ctx.shared.get("image_services", {}).items():
            if name in services:
                services[name].update({k: v for k, v in s.items() if v})
            else:
                services[name] = {"name": name, "kind": "app", "depends_on": [], "wired": {}, "volumes": [], "env_names": [], **s}
        if not services:
            ctx.notes.append("no compose file or Kubernetes manifests found; service graph not built")
            return
        self._nodes_and_edges(ctx, services)
        self._ownership(ctx, services)
        self._findings(ctx, services)
        ctx.shared["service_graph"] = {
            "services": {n: {k: v for k, v in s.items() if k in ("name", "kind", "image", "build_context", "dockerfile", "base_image",
                                                                 "ports", "env_names", "entrypoint", "depends_on", "networks",
                                                                 "source", "exposed", "workdir", "user")}
                         for n, s in services.items()},
            "edges": [{"from": a, "to": b, "evidence": ev} for (a, b), ev in sorted(ctx.shared.get("_svc_edges", {}).items())],
        }

    # ------------------------------------------------------------------ compose
    def _compose(self, ctx: Context, path: Path, doc: dict, out: dict[str, dict[str, Any]]) -> None:
        names = set(doc["services"])
        top_volumes = set((doc.get("volumes") or {}).keys()) if isinstance(doc.get("volumes"), dict) else set()
        for name, spec in doc["services"].items():
            if not isinstance(spec, dict):
                continue
            build = spec.get("build")
            context_rel = dockerfile_rel = None
            if isinstance(build, str):
                build = {"context": build}
            if isinstance(build, dict):
                base_dir = path.parent.relative_to(ctx.root).as_posix()
                context_rel = posixpath.normpath(posixpath.join(base_dir, str(build.get("context", "."))))
                dockerfile_rel = posixpath.normpath(posixpath.join(context_rel, str(build.get("dockerfile", "Dockerfile"))))
            deps = spec.get("depends_on") or []
            deps = list(deps.keys()) if isinstance(deps, dict) else list(deps)
            links = [str(l).split(":")[0] for l in (spec.get("links") or [])]
            pairs = _env_pairs(spec.get("environment"))
            wired: dict[str, list[str]] = defaultdict(list)  # target service -> env var names that name it (values dropped)
            for k, v in pairs:
                h = _host_of(v)
                if h in names and h != name:
                    wired[h].append(k)
            vols = []
            for v in spec.get("volumes") or []:
                src = (v.get("source") if isinstance(v, dict) else str(v).split(":")[0]) or ""
                if src in top_volumes or src.startswith(("/", "./", "../")):
                    vols.append(posixpath.normpath(src) if src.startswith(("/", ".")) else src)
            ports = []
            for p in spec.get("ports") or []:
                ps = str(p.get("target") if isinstance(p, dict) else p)
                ports.append(ps.split(":")[-1].split("/")[0])
            cmd = spec.get("entrypoint") or spec.get("command")
            program = None
            if isinstance(cmd, list) and cmd:
                program = str(cmd[0])
            elif isinstance(cmd, str) and cmd.strip():
                program = cmd.split()[0]
            nets = spec.get("networks") or []
            nets = list(nets.keys()) if isinstance(nets, dict) else list(nets)
            out[name] = {
                "name": name, "kind": classify_image(spec.get("image")) if not build else "app",
                "image": spec.get("image"), "build_context": context_rel, "dockerfile": dockerfile_rel,
                "ports": ports, "env_names": sorted({k for k, _ in pairs}), "entrypoint": program,
                "depends_on": sorted(set(deps) | set(links)), "wired": dict(wired), "volumes": vols,
                "networks": nets, "source": path.relative_to(ctx.root).as_posix(),
            }
            if dockerfile_rel:
                df = ctx.root / dockerfile_rel
                if df.is_file() and not is_secret(df.name):
                    try:
                        stages = parse_dockerfile(df.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        stages = []
                    if stages:
                        last = stages[-1]
                        out[name]["base_image"] = last.base
                        out[name]["workdir"] = last.workdir
                        out[name]["user"] = last.user
                        out[name]["exposed"] = last.expose
                        out[name]["_copies"] = [(s, d) for st in stages for s, d, frm in st.copies if frm is None]
                        out[name]["env_names"] = sorted(set(out[name]["env_names"]) | {n for st in stages for n in st.env_names})
                        if not program:
                            ep = last.entrypoint or last.cmd
                            out[name]["entrypoint"] = ep[0] if ep else None

    # ------------------------------------------------------------------ kubernetes
    def _k8s(self, ctx: Context, path: Path, docs: list[dict], out: dict[str, dict[str, Any]]) -> None:
        svc_names = {d["metadata"]["name"] for d in docs if d.get("kind") == "Service" and isinstance(d.get("metadata"), dict)}
        for d in docs:
            kind = d.get("kind")
            if kind not in ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "ReplicaSet"):
                continue
            meta = d.get("metadata") or {}
            name = meta.get("name")
            spec = d.get("spec") or {}
            if kind == "CronJob":
                spec = (spec.get("jobTemplate") or {}).get("spec") or {}
            pod = ((spec.get("template") or {}).get("spec")) or {}
            containers = pod.get("containers") or []
            if not name or not containers:
                continue
            envs = []
            for c in containers:
                envs += _env_pairs({e.get("name"): str(e.get("value", "")) for e in (c.get("env") or []) if isinstance(e, dict) and e.get("name")})
            wired: dict[str, list[str]] = defaultdict(list)
            for k, v in envs:
                h = _host_of(v).split(".")[0]
                if h in svc_names and h != name:
                    wired[h].append(k)
            vols = []
            for v in pod.get("volumes") or []:
                claim = (v.get("persistentVolumeClaim") or {}).get("claimName")
                if claim:
                    vols.append(claim)
            image = containers[0].get("image")
            ports = [str(p.get("containerPort")) for c in containers for p in (c.get("ports") or []) if p.get("containerPort")]
            out[name] = {"name": name, "kind": classify_image(image), "image": image, "ports": ports,
                         "env_names": sorted({k for k, _ in envs}), "depends_on": [], "wired": dict(wired),
                         "volumes": vols, "source": path.relative_to(ctx.root).as_posix(), "workload": kind}

    # ------------------------------------------------------------------ graph
    def _nodes_and_edges(self, ctx: Context, services: dict[str, dict[str, Any]]) -> None:
        g = ctx.graph
        edges: dict[tuple[str, str], list[str]] = defaultdict(list)

        def add(a: str, b: str, ev: str) -> None:
            if a != b and a in services and b in services and ev not in edges[(a, b)]:
                edges[(a, b)].append(ev)

        for n, s in services.items():
            g.add_node(f"service:{n}", "service", kind=s["kind"], image=s.get("image"))
        for n, s in services.items():
            for d in s["depends_on"]:
                add(n, d, "depends_on")
            for t, names in s["wired"].items():
                add(n, t, "env:" + ",".join(sorted(names)[:4]))
        vol_users: dict[str, list[str]] = defaultdict(list)
        for n, s in services.items():
            for v in s["volumes"]:
                vol_users[v].append(n)
        for v, users in vol_users.items():
            if len(users) > 1:
                for a in users:
                    for b in users:
                        add(a, b, f"shared-volume:{v}")
        for (a, b), ev in edges.items():
            g.add_edge(f"service:{a}", f"service:{b}", "service", float(len(ev)), "; ".join(ev))
        ctx.shared["_svc_edges"] = dict(edges)

    def _ownership(self, ctx: Context, services: dict[str, dict[str, Any]]) -> None:
        """Re-parent modules under the service whose image/Dockerfile contains them."""
        g = ctx.graph
        old_root = ctx.shared.get("service_id")
        module_service: dict[str, str] = {}
        for n, s in services.items():
            if s.get("source_prefix"):  # image-sourced service: files live under <service>/
                for mid in g.nodes_at("module"):
                    if g.nodes[mid].attrs.get("path", "").split("/")[0] == s["source_prefix"]:
                        module_service[mid] = n
                continue
            copies = s.get("_copies")
            ctxdir = s.get("build_context")
            if not copies or ctxdir is None:
                continue
            for mid in g.nodes_at("module"):
                path = g.nodes[mid].attrs.get("path", "")
                if module_service.get(mid):
                    continue
                for src, _dest in copies:
                    srel = posixpath.normpath(posixpath.join(ctxdir, src.strip("/"))) if src not in (".", "./") else posixpath.normpath(ctxdir)
                    srel = "" if srel == "." else srel
                    if srel == "" or path == srel or path.startswith(srel.rstrip("/") + "/") or srel.startswith(path + "/") \
                            or fnmatch.fnmatch(path, srel):
                        module_service[mid] = n
                        break
        # a module owned by several services: first match wins and the others are recorded
        for mid, svc in module_service.items():
            pkg = g.nodes[mid].parent
            new_pkg = f"package:{svc}/{pkg.removeprefix('package:')}" if pkg else f"package:{svc}"
            g.add_node(new_pkg, "package", f"service:{svc}")
            g.nodes[mid].parent = new_pkg
            g.nodes[mid].attrs["service"] = svc
        if old_root and old_root in g.nodes:
            # unowned modules stay under the project-level service node; drop it when everything is owned
            if not any(n.parent and n.parent.startswith("package:") and g.nodes[n.parent].parent == old_root
                       for n in g.nodes.values() if n.level == "module"):
                pkgs = [p for p in g.nodes_at("package") if g.nodes[p].parent == old_root]
                for p in pkgs:
                    del g.nodes[p]
                if not any(old_root in (e.src, e.dst) for e in g.edges.values()):
                    del g.nodes[old_root]
        ctx.shared["module_service"] = module_service

    def _findings(self, ctx: Context, services: dict[str, dict[str, Any]]) -> None:
        findings: list[dict[str, Any]] = []
        consumers: dict[str, set[str]] = defaultdict(set)
        for n, s in services.items():
            for t in set(s["depends_on"]) | set(s["wired"]):
                if t in services:
                    consumers[t].add(n)
        for infra, s in services.items():
            users = sorted(u for u in consumers[infra] if services[u]["kind"] == "app")
            if len(users) < 2:
                continue
            kind = s["kind"]
            if kind == "database":
                findings.append({"type": "shared-database", "severity": "high", "infrastructure": infra, "services": users,
                                 "evidence": {u: sorted(set(services[u]["wired"].get(infra, [])) or ["depends_on"]) for u in users},
                                 "message": f"{len(users)} services share database service '{infra}': they are coupled through its schema"})
            elif kind in ("redis", "broker"):
                findings.append({"type": f"shared-{kind}", "severity": "info", "infrastructure": infra, "services": users,
                                 "evidence": {u: sorted(set(services[u]["wired"].get(infra, [])) or ["depends_on"]) for u in users},
                                 "message": f"{len(users)} services share {kind} '{infra}'; channel-level coupling is in the event inventory"})
        vols: dict[str, list[str]] = defaultdict(list)
        for n, s in services.items():
            for v in s["volumes"]:
                vols[v].append(n)
        for v, users in vols.items():
            if len(users) > 1:
                findings.append({"type": "shared-volume", "severity": "medium", "volume": v, "services": sorted(users),
                                 "message": f"volume '{v}' is mounted by {len(users)} services"})
        ctx.shared["findings"] = findings
