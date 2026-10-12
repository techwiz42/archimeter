"""Built-in event-bus presets, expressed as data in the same schema as custom `[[events.patterns]]`."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import EventPattern

PY = ["python"]
JS = ["javascript", "typescript"]


@dataclass
class Preset:
    name: str
    patterns: list[EventPattern]
    file_contains: list[str] = field(default_factory=list)  # any-of, case-insensitive; file must mention one
    declare: list[dict] = field(default_factory=list)  # {"call": "Signal", "bind": "self"|"arg0", "langs": [...]}
    builtin_names: list[str] = field(default_factory=list)  # receivers always treated as declared
    receiver_must_be_declared: bool = False


def P(role, call, **kw) -> EventPattern:
    return EventPattern(role=role, call=call, **kw)


PRESETS: dict[str, Preset] = {
    "redis": Preset("redis", [
        P("publish", "publish", name="redis"), P("publish", "xadd", name="redis"),
        P("subscribe", "subscribe", arg=-1, name="redis"), P("subscribe", "psubscribe", arg=-1, name="redis"),
        P("subscribe", "xread", arg=0, name="redis"), P("subscribe", "xreadgroup", arg=2, name="redis"),
    ], file_contains=["redis"]),

    "celery": Preset("celery", [
        # a task definition is the thing that "subscribes" to its own name; callers publish it
        P("subscribe", "task", kind="decorator", name_from="function", langs=PY, name="celery"),
        P("subscribe", "shared_task", kind="decorator", name_from="function", langs=PY, name="celery"),
        P("publish", "delay", name_from="receiver", langs=PY, name="celery"),
        P("publish", "apply_async", name_from="receiver", langs=PY, name="celery"),
        P("publish", "send_task", arg=0, kwarg="name", langs=PY, name="celery"),
    ], file_contains=["celery", ".delay(", "apply_async", "send_task", "tasks"]),

    "django-signals": Preset("django-signals", [
        P("publish", "send", name_from="receiver", langs=PY, name="django-signals"),
        P("publish", "send_robust", name_from="receiver", langs=PY, name="django-signals"),
        P("subscribe", "connect", name_from="receiver", langs=PY, name="django-signals"),
        P("subscribe", "receiver", kind="decorator", arg=-1, langs=PY, name="django-signals"),
    ], declare=[{"call": "Signal", "bind": "self", "langs": PY}],
        builtin_names=["pre_init", "post_init", "pre_save", "post_save", "pre_delete", "post_delete", "m2m_changed",
                       "class_prepared", "pre_migrate", "post_migrate", "request_started", "request_finished",
                       "got_request_exception", "setting_changed"],
        receiver_must_be_declared=True, file_contains=["signal", "receiver"]),

    "blinker": Preset("blinker", [
        P("publish", "send", name_from="receiver", langs=PY, name="blinker"),
        P("subscribe", "connect", name_from="receiver", langs=PY, name="blinker"),
        P("subscribe", "connect_via", name_from="receiver", kind="decorator", langs=PY, name="blinker"),
        P("subscribe", "connect", name_from="receiver", kind="decorator", langs=PY, name="blinker"),
    ], declare=[{"call": "signal", "bind": "arg0", "langs": PY}],
        receiver_must_be_declared=True, file_contains=["blinker", "signal"]),

    "kafka": Preset("kafka", [
        P("publish", "send", arg=0, kwarg="topic", name="kafka"), P("publish", "produce", arg=0, kwarg="topic", name="kafka"),
        P("publish", "send_and_wait", arg=0, kwarg="topic", langs=PY, name="kafka"),
        P("subscribe", "subscribe", arg=0, kwarg="topics", name="kafka"),
        P("subscribe", "KafkaConsumer", arg=-1, langs=PY, name="kafka"), P("subscribe", "AIOKafkaConsumer", arg=-1, langs=PY, name="kafka"),
    ], file_contains=["kafka"]),

    "node-eventemitter": Preset("node-eventemitter", [
        P("publish", "emit", langs=JS, name="node-eventemitter"),
        P("subscribe", "on", langs=JS, name="node-eventemitter"), P("subscribe", "once", langs=JS, name="node-eventemitter"),
        P("subscribe", "addListener", langs=JS, name="node-eventemitter"),
        P("subscribe", "prependListener", langs=JS, name="node-eventemitter"),
    ], declare=[{"call": "EventEmitter", "bind": "self", "langs": JS}], receiver_must_be_declared=True),
}

PRESET_NAMES = sorted(PRESETS)
