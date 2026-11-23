"""Детерминированное отображение имён в идентификаторы C89 (README §10).

Экранирование компонента: [A-Za-z0-9] без изменений, '_' -> '_1', '$' -> '_2',
кодовая точка вне ASCII -> '_uXXXXXX'. Разделитель компонентов '_0' и
терминатор кода типа '_9' экранирование не порождает, поэтому отображение
инъективно. Все имена начинаются с буквы-префикса вида.
"""
from __future__ import annotations

from . import type_system as T


def esc(name: str) -> str:
    out = []
    for ch in name:
        if ch.isascii() and ch.isalnum():
            out.append(ch)
        elif ch == "_":
            out.append("_1")
        elif ch == "$":
            out.append("_2")
        else:
            out.append(f"_u{ord(ch):06X}")
    return "".join(out)


_PRIM_CODES = {"byte": "B", "short": "S", "int": "I", "long": "J", "float": "F", "double": "D",
               "char": "C", "boolean": "Z"}


def type_code(t: T.Type) -> str:
    if t.is_primitive:
        return _PRIM_CODES[str(t)]
    if isinstance(t, T.ArrayType):
        return "A" + type_code(t.elem)
    if isinstance(t, (T.ClassType, T.InterfaceType)):
        return "L" + esc(t.sym.name) + "_9"
    raise ValueError(f"нет кода для типа {t}")


def sig_code(param_types) -> str:
    return "".join(type_code(t) for t in param_types)


def method_fn(cls, m) -> str:
    return f"jm_{esc(cls.name)}_0{esc(m.name)}_0{sig_code(m.param_types)}"


def init_fn(cls, ctor) -> str:
    return f"jc_{esc(cls.name)}_0{sig_code(ctor.param_types)}"


def alloc_fn(cls, ctor) -> str:
    return f"jn_{esc(cls.name)}_0{sig_code(ctor.param_types)}"


def slot(m) -> str:
    return f"m_{esc(m.name)}_0{sig_code(m.param_types)}"


def field(f) -> str:
    return f"f_{esc(f.name)}"


def parent(p) -> str:
    return f"p_{esc(p.name)}"


def sub_struct(c) -> str:
    return f"js_{esc(c.name)}"


def full_struct(c) -> str:
    return f"jf_{esc(c.name)}"


def vtable_type(c) -> str:
    return f"jv_{esc(c.name)}"


def itable_type(i) -> str:
    return f"ji_{esc(i.name)}"


def iface_desc(i) -> str:
    return f"jdi_{esc(i.name)}"


def class_desc(c) -> str:
    return f"jd_{esc(c.name)}"


def array_desc(elem: T.Type) -> str:
    return f"jda_{type_code(elem)}"


def vtable_inst(d, idx: int) -> str:
    return f"jvt_{esc(d.name)}_0{idx}"


def itable_entries(d, idx: int) -> str:
    return f"jie_{esc(d.name)}_0{idx}"


def itable_inst(d, idx: int, i) -> str:
    return f"jit_{esc(d.name)}_0{idx}_0{esc(i.name)}"


def thunk(d, idx: int, m) -> str:
    return f"jt_{esc(d.name)}_0{idx}_0{esc(m.name)}_0{sig_code(m.param_types)}"


def ithunk(d, idx: int, i, m) -> str:
    return f"jti_{esc(d.name)}_0{idx}_0{esc(i.name)}_0{esc(m.name)}_0{sig_code(m.param_types)}"


def obj_thunk(d, which: str) -> str:
    return f"jto_{esc(d.name)}_0{which}"


def subdescs(d) -> str:
    return f"jsd_{esc(d.name)}"


def refoffs(d) -> str:
    return f"jro_{esc(d.name)}"


def string_obj(i: int) -> str:
    return f"jstr_{i}"


def string_data(i: int) -> str:
    return f"jstrd_{i}"


def var(v) -> str:
    if v.hint == "#self":           # '#' невозможен в идентификаторе исходника
        return "self"
    if v.hint:
        return f"v{v.id}_0{esc(v.hint)}"
    return f"t{v.id}"
