"""Event/message dependencies: publishers and subscribers matched by event name.

Presets and custom patterns share one schema. Names resolve from string literals, module-level constants
(same file, or a project-unique definition) and enum/class-attribute members. Anything else is *unresolved*
and reported, never guessed.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from ..config import EventPattern
from .base import Context, register
from .event_presets import PRESETS, Preset
from .jsparse import parse, string_value, walk


@dataclass
class Site:
    role: str
    name: str | None
    expr: str
    file: str
    line: int
    mechanism: str
    note: str = ""


@dataclass
class Active:
    pattern: EventPattern
    preset: Preset | None


# ----------------------------------------------------------------------------- shared symbol tables
class Symbols:
    def __init__(self) -> None:
        self.consts: dict[str, dict[str, str]] = defaultdict(dict)  # file -> name -> value
        self.classes: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)  # name -> file -> member -> value
        self.global_consts: dict[str, set[str]] = defaultdict(set)  # name -> values
        self.declared: dict[str, dict[str, str]] = defaultdict(dict)  # preset -> ident -> event name
        self.emitter_ctors: set[str] = {"EventEmitter"}

    def const(self, file: str, name: str) -> tuple[str | None, str]:
        if name in self.consts[file]:
            return self.consts[file][name], ""
        vals = self.global_consts.get(name)
        if vals and len(vals) == 1:
            return next(iter(vals)), ""
        if vals:
            return None, f"constant '{name}' has conflicting definitions"
        return None, f"cannot resolve '{name}'"

    def member(self, file: str, cls: str, member: str) -> tuple[str | None, str]:
        defs = self.classes.get(cls)
        if not defs:
            return None, f"unknown class/enum '{cls}'"
        if file in defs and member in defs[file]:
            return defs[file][member], ""
        vals = {d[member] for d in defs.values() if member in d}
        if len(vals) == 1:
            return next(iter(vals)), ""
        if vals:
            return None, f"'{cls}.{member}' has conflicting definitions"
        return None, f"'{cls}' has no resolvable member '{member}'"


def suffix_match(callee: str, call: str) -> bool:
    return callee == call or callee.endswith("." + call)


# ----------------------------------------------------------------------------- python
def py_dotted(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    if isinstance(node, ast.Call):  # chained: get_bus().publish -> "get_bus().publish"
        inner = py_dotted(node.func)
        if inner:
            parts.append(inner + "()")
            return ".".join(reversed(parts))
    return None


def py_collect(sym: Symbols, rel: str, tree: ast.Module, active_presets: list[Preset]) -> None:
    def str_of(v: ast.AST) -> str | None:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            return v.value
        return None

    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            v = str_of(node.value) if node.value is not None else None
            for t in targets:
                if isinstance(t, ast.Name) and v is not None:
                    sym.consts[rel][t.id] = v
                    sym.global_consts[t.id].add(v)
        elif isinstance(node, ast.ClassDef):
            members: dict[str, str] = {}
            for b in node.body:
                if isinstance(b, (ast.Assign, ast.AnnAssign)):
                    targets = b.targets if isinstance(b, ast.Assign) else [b.target]
                    v = str_of(b.value) if b.value is not None else None
                    if v is None and isinstance(b.value, ast.Tuple) and b.value.elts:  # Enum member = ("value", ...)
                        v = str_of(b.value.elts[0])
                    for t in targets:
                        if isinstance(t, ast.Name) and v is not None:
                            members[t.id] = v
            if members:
                sym.classes[node.name][rel] = members
    # declarations anywhere in the file (module or function level)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and node.targets:
            callee = py_dotted(node.value.func)
            if not callee:
                continue
            tgt = node.targets[0]
            ident = tgt.id if isinstance(tgt, ast.Name) else tgt.attr if isinstance(tgt, ast.Attribute) else None
            if not ident:
                continue
            for p in active_presets:
                for d in p.declare:
                    if "python" in d.get("langs", ["python"]) and suffix_match(callee, d["call"]):
                        if d["bind"] == "self":
                            sym.declared[p.name][ident] = ident
                        else:
                            a0 = str_of(node.value.args[0]) if node.value.args else None
                            if a0:
                                sym.declared[p.name][ident] = a0


def py_resolve(sym: Symbols, rel: str, node: ast.AST) -> tuple[list[str], list[str]]:
    """-> (resolved names, reasons for unresolved parts)"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value], []
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        names, bad = [], []
        for e in node.elts:
            n, b = py_resolve(sym, rel, e)
            names += n; bad += b
        return names, bad
    if isinstance(node, ast.Dict):
        names, bad = [], []
        for k in node.keys:
            if k is not None:
                n, b = py_resolve(sym, rel, k)
                names += n; bad += b
        return names, bad
    if isinstance(node, ast.Name):
        v, why = sym.const(rel, node.id)
        return ([v], []) if v is not None else ([], [why])
    if isinstance(node, ast.Attribute):
        base = py_dotted(node.value)
        if base:
            cls = base.split(".")[-1]
            v, why = sym.member(rel, cls, node.attr)
            if v is not None:
                return [v], []
            v2, why2 = sym.const(rel, node.attr) if base.split(".")[0] not in sym.classes else (None, why)
            return ([v2], []) if v2 is not None else ([], [why])
    if isinstance(node, ast.Call) and py_dotted(node.func) and node.args:
        # EventType("payment.received") style enum-by-value construction
        callee = py_dotted(node.func)
        if callee.split(".")[-1] in sym.classes and len(node.args) == 1:
            return py_resolve(sym, rel, node.args[0])
    if isinstance(node, ast.JoinedStr):
        return [], ["f-string built at runtime"]
    return [], [f"dynamic expression ({type(node).__name__})"]


def _arg_nodes(call: ast.Call, pat: EventPattern) -> list[ast.AST]:
    if pat.kwarg:
        for kw in call.keywords:
            if kw.arg == pat.kwarg:
                return [kw.value]
    if pat.arg == -1:
        return list(call.args)
    return [call.args[pat.arg]] if len(call.args) > pat.arg else []


def py_sites(sym: Symbols, rel: str, tree: ast.Module, actives: list[Active]) -> list[Site]:
    out: list[Site] = []

    def receiver_event(act: Active, callee: str) -> tuple[str | None, str]:
        recv = callee.rsplit(".", 1)[0] if "." in callee else ""
        ident = recv.split(".")[-1] if recv else ""
        if not ident:
            return None, ""
        if act.preset and act.preset.receiver_must_be_declared:
            if ident in act.preset.builtin_names:
                return ident, ""
            if ident in sym.declared[act.preset.name]:
                return sym.declared[act.preset.name][ident], ""
            return None, "skip"
        return ident, ""

    def emit(act: Active, node: ast.AST, names: list[str], bad: list[str], callee: str) -> None:
        mech = act.pattern.name or (act.preset.name if act.preset else "custom")
        for n in names:
            out.append(Site(act.pattern.role, n, n, rel, node.lineno, mech))
        for b in bad:
            out.append(Site(act.pattern.role, None, ast.unparse(node)[:120], rel, node.lineno, mech, b))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee = py_dotted(node.func)
            if not callee:
                continue
            for act in actives:
                pat = act.pattern
                if pat.kind != "call" or "python" not in pat.langs or not suffix_match(callee, pat.call):
                    continue
                if pat.event:
                    emit(act, node, [pat.event], [], callee)
                elif pat.name_from == "receiver":
                    n, why = receiver_event(act, callee)
                    if why == "skip" or n is None:
                        continue
                    emit(act, node, [n], [], callee)
                else:
                    args = _arg_nodes(node, pat)
                    if not args:
                        continue
                    names, bad = [], []
                    for a in args:
                        n, b = py_resolve(sym, rel, a)
                        names += n; bad += b
                    emit(act, node, names, bad, callee)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                dcall = dec if isinstance(dec, ast.Call) else None
                callee = py_dotted(dcall.func if dcall else dec)
                if not callee:
                    continue
                for act in actives:
                    pat = act.pattern
                    if pat.kind != "decorator" or "python" not in pat.langs or not suffix_match(callee, pat.call):
                        continue
                    if pat.name_from == "function":
                        nm = node.name
                        if dcall:
                            for kw in dcall.keywords:
                                if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                                    nm = kw.value.value
                        emit(act, node, [nm], [], callee)
                    elif pat.name_from == "receiver":
                        n, why = receiver_event(act, callee)
                        if n and why != "skip":
                            emit(act, node, [n], [], callee)
                    elif dcall is not None:
                        args = _arg_nodes(dcall, pat)
                        names, bad = [], []
                        for a in args:
                            if isinstance(a, ast.Name) and act.preset and act.preset.name == "django-signals":
                                # @receiver(post_save) names the signal object itself
                                names.append(sym.declared["django-signals"].get(a.id, a.id)); continue
                            if isinstance(a, ast.Attribute) and act.preset and act.preset.name == "django-signals":
                                names.append(a.attr); continue
                            n, b = py_resolve(sym, rel, a)
                            names += n; bad += b
                        emit(act, node, names, bad, callee)
    return out


# ----------------------------------------------------------------------------- javascript / typescript
def js_text(n, data: bytes) -> str:
    return data[n.start_byte:n.end_byte].decode("utf-8", "replace")


def js_dotted(n, data: bytes) -> str | None:
    if n is None:
        return None
    if n.type in ("identifier", "property_identifier", "this", "shorthand_property_identifier"):
        return js_text(n, data)
    if n.type == "member_expression":
        obj, prop = n.child_by_field_name("object"), n.child_by_field_name("property")
        o = js_dotted(obj, data)
        return f"{o}.{js_text(prop, data)}" if o and prop is not None else None
    if n.type == "call_expression":
        inner = js_dotted(n.child_by_field_name("function"), data)
        return inner + "()" if inner else None
    return None


def js_collect(sym: Symbols, rel: str, tree, data: bytes, active_presets: list[Preset]) -> None:
    for n in walk(tree.root_node):
        if n.type == "variable_declarator":
            name = n.child_by_field_name("name")
            val = n.child_by_field_name("value")
            if name is None or val is None or name.type != "identifier":
                continue
            ident = js_text(name, data)
            sv = string_value(val, data) if val.type in ("string", "template_string") else None
            if sv is not None and n.parent is not None and n.parent.parent is not None and n.parent.parent.type == "program" \
                    or (sv is not None and n.parent is not None and n.parent.parent is not None and n.parent.parent.type == "export_statement"):
                sym.consts[rel][ident] = sv
                sym.global_consts[ident].add(sv)
            if val.type == "object":
                members = {}
                for pair in val.children:
                    if pair.type == "pair":
                        k, v = pair.child_by_field_name("key"), pair.child_by_field_name("value")
                        kv = string_value(v, data) if v is not None else None
                        if k is not None and kv is not None:
                            members[js_text(k, data).strip("'\"")] = kv
                if members:
                    sym.classes[ident][rel] = members
            if val.type == "new_expression":
                ctor = val.child_by_field_name("constructor")
                cname = js_dotted(ctor, data) if ctor is not None else None
                if cname:
                    last = cname.split(".")[-1]
                    for p in active_presets:
                        for d in p.declare:
                            if any(l in d.get("langs", []) for l in ("javascript", "typescript")) and \
                                    (last == d["call"] or (d["call"] == "EventEmitter" and last in sym.emitter_ctors)):
                                sym.declared[p.name][ident] = ident
        elif n.type == "enum_declaration":
            name = n.child_by_field_name("name")
            body = n.child_by_field_name("body")
            if name is None or body is None:
                continue
            members = {}
            for m in body.children:
                if m.type == "enum_assignment":
                    k, v = m.child_by_field_name("name"), m.child_by_field_name("value")
                    kv = string_value(v, data) if v is not None else None
                    if k is not None and kv is not None:
                        members[js_text(k, data)] = kv
            if members:
                sym.classes[js_text(name, data)][rel] = members
        elif n.type == "class_declaration":
            heritage = next((c for c in n.children if c.type == "class_heritage"), None)
            if heritage is not None and "EventEmitter" in js_text(heritage, data):
                nm = n.child_by_field_name("name")
                if nm is not None:
                    sym.emitter_ctors.add(js_text(nm, data))


def js_resolve(sym: Symbols, rel: str, n, data: bytes) -> tuple[list[str], list[str]]:
    if n is None:
        return [], ["missing argument"]
    if n.type in ("string", "template_string"):
        v = string_value(n, data)
        return ([v], []) if v is not None else ([], ["template string with substitutions"])
    if n.type == "array":
        names, bad = [], []
        for c in n.named_children:
            a, b = js_resolve(sym, rel, c, data); names += a; bad += b
        return names, bad
    if n.type == "object":
        names, bad = [], []
        for pair in n.children:
            if pair.type == "pair":
                k = pair.child_by_field_name("key")
                if k is not None and js_text(k, data).strip("'\"") in ("topic", "topics"):
                    a, b = js_resolve(sym, rel, pair.child_by_field_name("value"), data); names += a; bad += b
        return names, bad
    if n.type == "identifier":
        v, why = sym.const(rel, js_text(n, data))
        return ([v], []) if v is not None else ([], [why])
    if n.type == "member_expression":
        obj, prop = n.child_by_field_name("object"), n.child_by_field_name("property")
        if obj is not None and prop is not None and obj.type == "identifier":
            v, why = sym.member(rel, js_text(obj, data), js_text(prop, data))
            return ([v], []) if v is not None else ([], [why])
    return [], [f"dynamic expression ({n.type})"]


def js_sites(sym: Symbols, rel: str, tree, data: bytes, actives: list[Active], lang: str, has_emitter_class: bool) -> list[Site]:
    out: list[Site] = []
    for n in walk(tree.root_node):
        if n.type != "call_expression":
            continue
        fn = n.child_by_field_name("function")
        callee = js_dotted(fn, data) if fn is not None else None
        if not callee:
            continue
        args_node = n.child_by_field_name("arguments")
        args = args_node.named_children if args_node is not None else []
        for act in actives:
            pat = act.pattern
            if pat.kind != "call" or lang not in pat.langs or not suffix_match(callee, pat.call):
                continue
            mech = pat.name or (act.preset.name if act.preset else "custom")
            if pat.event:
                out.append(Site(pat.role, pat.event, pat.event, rel, n.start_point[0] + 1, mech))
                continue
            if act.preset and act.preset.receiver_must_be_declared:
                recv = callee.rsplit(".", 1)[0] if "." in callee else ""
                ident = recv.split(".")[-1]
                ok = ident in sym.declared[act.preset.name] or (ident == "this" and has_emitter_class)
                if not ok:
                    continue
            if pat.name_from == "receiver":
                recv = callee.rsplit(".", 1)[0] if "." in callee else ""
                if recv:
                    out.append(Site(pat.role, recv.split(".")[-1], recv, rel, n.start_point[0] + 1, mech))
                continue
            sel = [args[pat.arg]] if pat.arg >= 0 and len(args) > pat.arg else (args if pat.arg == -1 else [])
            if pat.kwarg and sel and sel[0].type == "object":
                pass  # object arg handled by js_resolve (topic / topics keys)
            if not sel:
                continue
            for a in sel:
                names, bad = js_resolve(sym, rel, a, data)
                for nm in names:
                    out.append(Site(pat.role, nm, nm, rel, n.start_point[0] + 1, mech))
                for b in bad:
                    out.append(Site(pat.role, None, js_text(a, data)[:120], rel, n.start_point[0] + 1, mech, b))
    return out


# ----------------------------------------------------------------------------- extractor
@register
class EventExtractor:
    name = "events"
    requires = ("modules", "python-imports", "js-imports")

    def applies(self, ctx: Context) -> bool:
        e = ctx.config.events
        return bool(e.presets or e.patterns)

    def extract(self, ctx: Context) -> None:
        from ..errors import ArchimeterError
        cfg = ctx.config.events
        actives: list[Active] = []
        presets: list[Preset] = []
        for name in cfg.presets:
            if name not in PRESETS:
                raise ArchimeterError(f"unknown event preset '{name}' (available: {', '.join(sorted(PRESETS))})")
            p = PRESETS[name]
            presets.append(p)
            actives += [Active(pat, p) for pat in p.patterns]
        actives += [Active(pat, None) for pat in cfg.patterns]

        sym = Symbols()
        parsed: list[tuple[Any, Any, Any]] = []
        for f in ctx.files:
            text = ctx.read(f)
            if f.lang == "python":
                try:
                    tree = ast.parse(text)
                except (SyntaxError, ValueError, RecursionError):
                    continue
                py_collect(sym, f.rel, tree, presets)
                parsed.append((f, tree, None))
            else:
                tree, data = parse(f.rel, text)
                js_collect(sym, f.rel, tree, data, presets)
                parsed.append((f, tree, data))

        sites: list[Site] = []
        for f, tree, data in parsed:
            low = ctx.read(f).lower()
            file_actives = [a for a in actives if f.lang in a.pattern.langs and
                            (a.preset is None or not a.preset.file_contains or any(s.lower() in low for s in a.preset.file_contains))]
            if not file_actives:
                continue
            if f.lang == "python":
                sites += py_sites(sym, f.rel, tree, file_actives)
            else:
                has_cls = "extends EventEmitter" in ctx.read(f) or any(c in ctx.read(f) for c in [f"extends {c}" for c in sym.emitter_ctors])
                sites += js_sites(sym, f.rel, tree, data, file_actives, f.lang, has_cls)

        self._finish(ctx, sites)

    @staticmethod
    def _finish(ctx: Context, sites: list[Site]) -> None:
        g = ctx.graph
        module_of = ctx.shared.get("module_of", {})
        by_name: dict[str, dict[str, list[Site]]] = defaultdict(lambda: {"publish": [], "subscribe": []})
        unresolved = []
        for s in sites:
            if s.name is None:
                unresolved.append({"role": s.role, "file": s.file, "line": s.line, "expression": s.expr,
                                   "mechanism": s.mechanism, "reason": s.note})
            else:
                by_name[s.name][s.role].append(s)

        def mod(rel: str) -> str:
            return module_of.get(rel, "").removeprefix("module:")

        inventory = []
        for name in sorted(by_name):
            pubs, subs = by_name[name]["publish"], by_name[name]["subscribe"]
            flags = []
            if pubs and not subs:
                flags.append("no_subscriber")
            if subs and not pubs:
                flags.append("never_published")
            mechs = sorted({s.mechanism for s in pubs + subs})
            inventory.append({
                "name": name, "mechanisms": mechs, "flags": flags,
                "publishers": [{"file": s.file, "line": s.line, "module": mod(s.file)} for s in pubs],
                "subscribers": [{"file": s.file, "line": s.line, "module": mod(s.file)} for s in subs],
            })
            # subscriber depends on publisher (it consumes what the publisher emits)
            for sub in {s.file for s in subs}:
                for pub in {s.file for s in pubs}:
                    if sub != pub and f"file:{sub}" in g.nodes and f"file:{pub}" in g.nodes:
                        g.add_edge(f"file:{sub}", f"file:{pub}", "event", 1.0, f"event:{name}")
        ctx.shared["events"] = {
            "inventory": inventory,
            "unresolved": unresolved,
            "summary": {
                "events": len(inventory),
                "no_subscriber": sum("no_subscriber" in e["flags"] for e in inventory),
                "never_published": sum("never_published" in e["flags"] for e in inventory),
                "unresolved_sites": len(unresolved),
            },
        }
