from __future__ import annotations

import os
import tempfile
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("ARCHIMETER_CACHE_DIR", tempfile.mkdtemp(prefix="archimeter-test-cache-"))


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def git(root: Path, *args: str, date: str | None = None, author: str = "Dev One <one@example.com>") -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": author.split(" <")[0], "GIT_AUTHOR_EMAIL": author.split("<")[1].rstrip(">"),
           "GIT_COMMITTER_NAME": author.split(" <")[0], "GIT_COMMITTER_EMAIL": author.split("<")[1].rstrip(">")}
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", "-C", str(root), "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, env=env)


def commit(root: Path, msg: str, files: dict[str, str], date: str, author: str = "Dev One <one@example.com>") -> None:
    for rel, text in files.items():
        write(root, rel, text)
    git(root, "add", "-A")
    git(root, "commit", "-m", msg, date=date, author=author)


@pytest.fixture
def pyrepo(tmp_path: Path) -> Path:
    """Known facts:
    modules: alpha, beta, gamma, delta, epsilon
    imports: alpha->beta, beta->alpha (the cycle), gamma->(none)
    events: 'order.created' published in alpha, subscribed in gamma (event edge gamma->alpha, no import)
            'order.shipped' published only (no_subscriber); 'audit.never' subscribed only (never_published)
            one runtime-built name (unresolved)
    git: delta and epsilon co-change in 4 commits and share no import/event (hidden coupling)
    """
    root = tmp_path / "pyrepo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, "init", {
        "alpha/__init__.py": "",
        "alpha/core.py": (
            "from beta.util import helper\n"
            "from alpha.names import Topics, ORDER_SHIPPED\n\n"
            "def make(bus, kind):\n"
            "    bus.publish(Topics.CREATED, {})\n"
            "    bus.publish(ORDER_SHIPPED, {})\n"
            "    bus.publish(f'dyn.{kind}', {})\n"
            "    if kind:\n"
            "        return helper(kind)\n"
            "    return None\n"
        ),
        "alpha/names.py": (
            "from enum import Enum\n"
            "ORDER_SHIPPED = 'order.shipped'\n\n"
            "class Topics(Enum):\n"
            "    CREATED = 'order.created'\n"
        ),
        "beta/__init__.py": "",
        "beta/util.py": (
            "from alpha.core import make\n\n"
            "def helper(x):\n"
            "    for i in range(3):\n"
            "        if i == x:\n"
            "            return make(None, i)\n"
            "    return x\n"
        ),
        "gamma/__init__.py": "",
        "gamma/handlers.py": (
            "def on_created(bus):\n"
            "    bus.subscribe('order.created', lambda e: e)\n"
            "    bus.subscribe('audit.never', lambda e: e)\n"
        ),
        "delta/__init__.py": "",
        "delta/d.py": "def d():\n    return 1\n",
        "epsilon/__init__.py": "",
        "epsilon/e.py": "def e():\n    return 2\n",
    }, "2026-01-01T10:00:00+00:00")
    for i in range(4):
        commit(root, f"co-change {i}", {"delta/d.py": f"def d():\n    return {i + 10}\n", "epsilon/e.py": f"def e():\n    return {i + 20}\n"},
               f"2026-02-0{i + 1}T10:00:00+00:00", author="Dev Two <two@example.com>")
    # a bulk commit touching >30 files must be ignored for coupling
    bulk = {f"alpha/bulk_{i}.py": f"X_{i} = {i}\n" for i in range(35)} | {"gamma/bulk.py": "Y = 1\n"}
    commit(root, "bulk", bulk, "2026-03-01T10:00:00+00:00")
    return root


EVENT_CFG = """
[[events.patterns]]
role = "publish"
call = "bus.publish"
arg = 0

[[events.patterns]]
role = "subscribe"
call = "bus.subscribe"
arg = 0
"""


@pytest.fixture
def tsrepo(tmp_path: Path) -> Path:
    """Known facts (TypeScript, tsconfig alias @app/* -> src/*):
    modules: src/ui, src/data, src/core, src/cart, src/reporta, src/reportb
    imports: ui->data, data->ui (cycle), ui->core, cart->core
    events (EventEmitter preset): 'cart:add' emitted in cart, listened in ui; 'cart:cleared' (enum member) emitted, never listened;
             'ui:ready' listened, never emitted; emit(`dyn-${x}`) is unresolved
    git: src/reporta/a.ts and src/reportb/b.ts co-change 4 times with no import/event link
    """
    root = tmp_path / "tsrepo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, "init", {
        "package.json": '{"name": "tsrepo", "private": true}\n',
        "tsconfig.json": '{\n  // comments are allowed in tsconfig\n  "compilerOptions": {"baseUrl": ".", "paths": {"@app/*": ["src/*"]},},\n}\n',
        "src/core/bus.ts": "import { EventEmitter } from 'events';\nexport const bus = new EventEmitter();\n",
        "src/core/events.ts": "export enum Events { CLEARED = 'cart:cleared' }\n",
        "src/ui/App.tsx": (
            "import { api } from '@app/data/api';\n"
            "import { bus } from '@app/core/bus';\n\n"
            "bus.on('cart:add', () => api());\n"
            "bus.on('ui:ready', () => {});\n"
            "export function App(x: number) { if (x > 1) { return api(); } return null; }\n"
        ),
        "src/data/api.ts": "import { App } from '@app/ui/App';\nexport function api() { return App(1); }\n",
        "src/cart/cart.ts": (
            "import { bus } from '../core/bus';\n"
            "import { Events } from '../core/events';\n"
            "export function add(x: string) { bus.emit('cart:add', x); bus.emit(Events.CLEARED); bus.emit(`dyn-${x}`); }\n"
        ),
        "src/reporta/a.ts": "export const a = 1;\n",
        "src/reportb/b.ts": "export const b = 1;\n",
    }, "2026-01-01T10:00:00+00:00")
    for i in range(4):
        commit(root, f"co {i}", {"src/reporta/a.ts": f"export const a = {i + 2};\n", "src/reportb/b.ts": f"export const b = {i + 2};\n"},
               f"2026-02-0{i + 1}T10:00:00+00:00")
    return root


UTIL = "def helper(x):\n    if x is None:\n        return 0\n    return 1\n"
MODELS_FW = (
    "class Model:\n"
    "    def __init__(self, value):\n        self.value = value\n\n"
    "    def get(self):\n        return self.value\n\n"
    "    def set(self, v):\n        self.value = v\n        return self\n\n"
    "    def clear(self):\n        self.value = None\n        return self\n\n"
    "    def is_empty(self):\n        return self.value is None\n"
)
MODELS_MODIFIED = MODELS_FW + "\n    def double(self):\n        return self.value * 2\n"
API_MAIN = (
    "import redis\n"
    "from modules.shared.util import helper\n"
    "from modules.shared.models import Model\n\n"
    "r = redis.Redis()\n\n"
    "def create(job):\n"
    "    r.publish('job.created', job)\n"
    "    r.publish('api.orphan', job)\n"
    "    return helper(Model(job))\n"
)
WORKER_MAIN = (
    "import redis\n"
    "from modules.shared.util import helper\n\n"
    "r = redis.Redis()\n\n"
    "def run():\n"
    "    p = r.pubsub()\n"
    "    p.subscribe('job.created')\n"
    "    p.subscribe('ghost.event')\n"
    "    return helper(None)\n"
)
COMPOSE = """\
version: '3.8'
services:
  api:
    image: acme/api:1.0
    depends_on: [db, redis]
    environment:
      DB_HOST: db
      DB_PASSWORD: super-secret-value-123
      REDIS_HOST: redis
  worker:
    image: acme/worker:1.0
    depends_on: [db, redis]
    environment:
      DB_HOST: db
      REDIS_HOST: redis
  db:
    image: postgres:15
  redis:
    image: redis:7
"""


@pytest.fixture
def framework(tmp_path: Path) -> Path:
    root = tmp_path / "framework"
    write(root, "modules/shared/__init__.py", "")
    write(root, "modules/shared/util.py", UTIL)
    write(root, "modules/shared/models.py", MODELS_FW)
    write(root, "modules/billing/charge.py", "def charge(a):\n    return a\n")
    return root


@pytest.fixture
def genapp(tmp_path: Path) -> Path:
    """Two-container generated application shipped as image tarballs plus a compose file.
    Known facts: services api, worker, db, redis; event 'job.created' published in api and subscribed in worker (cross-service);
    'api.orphan' dangling (no subscriber), 'ghost.event' dangling (never published); db shared by api and worker;
    api: util.py copied, models.py modified, main.py new; worker: util.py copied, models.py (absent), main.py new."""
    from imagefix import docker_save, layer
    root = tmp_path / "genapp"
    root.mkdir()
    write(root, "docker-compose.yml", COMPOSE)
    write(root, "archimeter.toml", '[events]\npresets = ["redis"]\n')
    base = layer([
        ("file", "etc/passwd", b"root:x:0:0\n"),
        ("file", "usr/bin/python3", b"\x7fELF"),
        ("file", "usr/lib/python3/dist-packages/apt.py", b"x = 1\n"),
        ("file", "usr/local/lib/python3.11/site-packages/requests/api.py", b"def get(): pass\n"),
        ("file", "usr/local/lib/python3.11/site-packages/requests-2.31.0.dist-info/METADATA", b"Metadata-Version: 2.1\nName: requests\nVersion: 2.31.0\n"),
    ])
    api_app = layer([
        ("file", "app/api/__init__.py", b""),
        ("file", "app/api/main.py", API_MAIN.encode()),
        ("file", "app/modules/shared/__init__.py", b""),
        ("file", "app/modules/shared/util.py", UTIL.encode()),
        ("file", "app/modules/shared/models.py", MODELS_MODIFIED.encode()),
        ("file", "app/.env", b"DB_PASSWORD=super-secret-value-123\n"),
    ])
    worker_app = layer([
        ("file", "app/worker/__init__.py", b""),
        ("file", "app/worker/main.py", WORKER_MAIN.encode()),
        ("file", "app/modules/shared/__init__.py", b""),
        ("file", "app/modules/shared/util.py", UTIL.encode()),
    ])
    docker_save(root / "api.tar", "acme/api:1.0", [base, api_app], entrypoint=["python", "-m", "api.main"], workdir="/app",
                env=["DB_PASSWORD=super-secret-value-123", "PATH=/usr/bin"], ports=[8000])
    docker_save(root / "worker.tar", "acme/worker:1.0", [base, worker_app], cmd=["python", "worker/main.py"], workdir="/app")
    return root
