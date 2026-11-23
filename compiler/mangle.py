"""Детерминированное отображение имён в идентификаторы C99 (README §10).

Java-идентификаторы — только ASCII (раздел 11). Экранирование компонента:
[A-Za-z0-9] без изменений, '_' -> '_1', '$' -> '_2'. Разделитель
компонентов '_0' и терминатор кода типа '_9' экранирование не порождает,
поэтому отображение инъективно. Каждый вид имени имеет свой префикс,
отличный от префикса runtime 'rt_' и стандартной библиотеки C.
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
            raise ValueError(f"не-ASCII символ в идентификаторе: {name!r}")
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


def ctor_fn(cls, ctor) -> str:
    return f"jc_{esc(cls.name)}_0{sig_code(ctor.param_types)}"


def alloc_fn(cls) -> str:
    return f"jn_{esc(cls.name)}"


def clinit_fn(cls) -> str:
    return f"jci_{esc(cls.name)}"


def clinit_state(cls) -> str:
    return f"jcs_{esc(cls.name)}"


def static_var(f) -> str:
    return f"jsv_{esc(f.owner.name)}_0{esc(f.name)}"


def slot(m) -> str:
    """Член VTable/itable; для методов Object совпадает с rt_vtbl_Object."""
    return f"m_{esc(m.name)}_0{sig_code(m.param_types)}"


def field(f) -> str:
    return f"f_{esc(f.name)}"


def obj_struct(c) -> str:
    return f"jo_{esc(c.name)}"


def vtable_type(c) -> str:
    return f"jv_{esc(c.name)}"


def vtable_inst(c) -> str:
    return f"jvt_{esc(c.name)}"


def itable_type(i) -> str:
    return f"ji_{esc(i.name)}"


def itable_inst(c, i) -> str:
    return f"jit_{esc(c.name)}_0{esc(i.name)}"


def itable_entries(c) -> str:
    return f"jie_{esc(c.name)}"


def iface_desc(i) -> str:
    return f"jdi_{esc(i.name)}"


def class_desc(c) -> str:
    return f"jd_{esc(c.name)}"


def array_desc(elem: T.Type) -> str:
    return f"jda_{type_code(elem)}"


def refoffs(c) -> str:
    return f"jro_{esc(c.name)}"


def frame_desc(n: int) -> str:
    return f"jfd_{n}"


def string_obj(i: int) -> str:
    return f"jstr_{i}"


def string_data(i: int) -> str:
    return f"jstrd_{i}"


def var(v) -> str:
    """Имя C-переменной примитивного значения (ссылки живут в слотах кадра)."""
    if v.hint == "#self":           # '#' невозможен в идентификаторе исходника
        return "self"
    if v.hint:
        return f"v{v.id}_0{esc(v.hint)}"
    return f"t{v.id}"
