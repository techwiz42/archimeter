"""archimeter.toml loading. Unknown keys are errors; every field has a default."""
from __future__ import annotations

import tomllib
from dataclasses import MISSING, asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .errors import ArchimeterError


@dataclass
class ModulesConfig:
    roots: list[str] = field(default_factory=list)  # directories whose children are modules
    group: dict[str, list[str]] = field(default_factory=dict)  # module name -> path globs
    exclude: list[str] = field(default_factory=lambda: [
        "tests/**", "test/**", "**/tests/**", "**/__tests__/**", "**/*.test.*", "**/*.spec.*",
        "**/migrations/**", "**/alembic/versions/**", "**/seed*/**", "**/demo_data/**", "**/fixtures/**",
    ])


@dataclass
class AnalysisConfig:
    complexity_threshold: int = 10
    skip_generated: bool = True
    max_file_bytes: int = 1_000_000
    max_total_extract_bytes: int = 4 * 1024**3
    skip_vendored: bool = True
    hotspot_top: int = 10


@dataclass
class EventPattern:
    role: str = "publish"  # publish | subscribe
    call: str = ""  # dotted suffix of the callee or decorator, e.g. "bus.emit" or "emit"
    arg: int = 0  # positional index holding the event name
    kwarg: str = ""  # keyword holding the event name (takes precedence if present)
    name: str = ""  # label for the mechanism
    event: str = ""  # fixed event name: the call itself denotes this event (e.g. a publish_entity_created() helper)
    kind: str = "call"  # call | decorator
    name_from: str = "arg"  # arg | function (decorated function name) | receiver (object the method is called on)
    langs: list[str] = field(default_factory=lambda: ["python", "javascript", "typescript"])


@dataclass
class EventsConfig:
    presets: list[str] = field(default_factory=list)
    patterns: list[EventPattern] = field(default_factory=list)


@dataclass
class GitConfig:
    window_days: int = 180
    max_commit_files: int = 30
    min_support: int = 3
    min_confidence: float = 0.5
    owner_share: float = 0.7
    include_author_names: bool = False


@dataclass
class ImageConfig:
    app_paths: list[str] = field(default_factory=list)
    platform: str = "linux/amd64"


@dataclass
class ProvenanceConfig:
    manifest: str = ""
    framework: str = ""
    similarity_threshold: float = 0.6
    selected_modules: list[str] = field(default_factory=list)  # modules the customer selected, when not in the manifest


@dataclass
class SecurityConfig:
    secrets_denylist: list[str] = field(default_factory=list)


@dataclass
class GithubConfig:
    oauth_client_id: str = ""


@dataclass
class Config:
    modules: ModulesConfig = field(default_factory=ModulesConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    events: EventsConfig = field(default_factory=EventsConfig)
    git: GitConfig = field(default_factory=GitConfig)
    image: ImageConfig = field(default_factory=ImageConfig)
    provenance: ProvenanceConfig = field(default_factory=ProvenanceConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    github: GithubConfig = field(default_factory=GithubConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _build(cls, data: dict[str, Any], path: str):
    known = {f.name: f for f in fields(cls)}
    for key in data:
        if key not in known:
            raise ArchimeterError(f"unknown config key '{path}{key}' (valid: {', '.join(sorted(known))})")
    kwargs = {}
    for key, value in data.items():
        fld = known[key]
        default = fld.default_factory() if fld.default_factory is not MISSING else fld.default
        if hasattr(default, "__dataclass_fields__"):
            if not isinstance(value, dict):
                raise ArchimeterError(f"config key '{path}{key}' must be a table")
            kwargs[key] = _build(type(default), value, f"{path}{key}.")
        elif key == "patterns" and cls is EventsConfig:
            if not isinstance(value, list):
                raise ArchimeterError("config key 'events.patterns' must be an array of tables")
            kwargs[key] = [_build(EventPattern, p, "events.patterns.") for p in value]
        else:
            kwargs[key] = value
    return cls(**kwargs)


def load_config(path: Path | None) -> Config:
    if path is None:
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ArchimeterError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ArchimeterError(f"invalid TOML in {path}: {e}") from None
    cfg = _build(Config, data, "")
    for p in cfg.events.patterns:
        if p.role not in ("publish", "subscribe") or not p.call:
            raise ArchimeterError("events.patterns entries need role = 'publish'|'subscribe' and a call")
        if p.kind not in ("call", "decorator") or p.name_from not in ("arg", "function", "receiver"):
            raise ArchimeterError("events.patterns: kind must be call|decorator and name_from arg|function|receiver")
    return cfg


def find_config(root: Path) -> Path | None:
    p = root / "archimeter.toml"
    return p if p.is_file() else None
