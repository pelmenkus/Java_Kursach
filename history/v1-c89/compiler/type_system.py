"""Структурная система типов и централизованные правила типизации.

Все решения «допустима ли операция / присваивание / приведение» принимаются
здесь. Неявных преобразований нет (п. 5.4 ТЗ): кроме совпадения типов
допускается только присваивание null ссылочному типу.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from .symbol_table import ClassSymbol, InterfaceSymbol

if TYPE_CHECKING:
    from .inheritance import Hierarchy


class Type:
    is_primitive = False
    is_reference = False

    def __repr__(self) -> str:
        return str(self)


class PrimitiveType(Type):
    is_primitive = True

    def __init__(self, kind: str):
        self.kind = kind

    def __str__(self) -> str:
        return self.kind


class VoidType(Type):
    def __str__(self) -> str:
        return "void"


class NullType(Type):
    is_reference = True

    def __str__(self) -> str:
        return "null"


class ClassType(Type):
    is_reference = True

    def __init__(self, sym: ClassSymbol):
        self.sym = sym

    def __eq__(self, other) -> bool:
        return isinstance(other, ClassType) and other.sym is self.sym

    def __hash__(self) -> int:
        return hash(("class", id(self.sym)))

    def __str__(self) -> str:
        return self.sym.name


class InterfaceType(Type):
    is_reference = True

    def __init__(self, sym: InterfaceSymbol):
        self.sym = sym

    def __eq__(self, other) -> bool:
        return isinstance(other, InterfaceType) and other.sym is self.sym

    def __hash__(self) -> int:
        return hash(("iface", id(self.sym)))

    def __str__(self) -> str:
        return self.sym.name


class ArrayType(Type):
    is_reference = True

    def __init__(self, elem: Type):
        if isinstance(elem, ArrayType):
            raise AssertionError("вложенные массивы невыразимы")
        self.elem = elem

    def __eq__(self, other) -> bool:
        return isinstance(other, ArrayType) and other.elem == self.elem

    def __hash__(self) -> int:
        return hash(("array", self.elem))

    def __str__(self) -> str:
        return f"{self.elem}[]"


class QualifierType(Type):
    """Псевдотип квалификатора `obj.Parent` — не значение (I4)."""

    def __init__(self, sym: ClassSymbol):
        self.sym = sym

    def __str__(self) -> str:
        return f"<квалификатор {self.sym.name}>"


BYTE = PrimitiveType("byte")
SHORT = PrimitiveType("short")
INT = PrimitiveType("int")
LONG = PrimitiveType("long")
FLOAT = PrimitiveType("float")
DOUBLE = PrimitiveType("double")
CHAR = PrimitiveType("char")
BOOLEAN = PrimitiveType("boolean")
VOID = VoidType()
NULL = NullType()
PRIMITIVES = {t.kind: t for t in (BYTE, SHORT, INT, LONG, FLOAT, DOUBLE, CHAR, BOOLEAN)}

INTEGRAL = (BYTE, SHORT, INT, LONG, CHAR)
NUMERIC = (BYTE, SHORT, INT, LONG, FLOAT, DOUBLE, CHAR)
FLOATING = (FLOAT, DOUBLE)

# Встроенные классы (методы заполняет semantic.install_builtins)
OBJECT_SYM = ClassSymbol("Object", {"public"}, builtin=True)
STRING_SYM = ClassSymbol("String", {"public", "final"}, builtin=True)
CLASS_SYM = ClassSymbol("Class", {"public", "final"}, builtin=True)
OBJECT = ClassType(OBJECT_SYM)
STRING = ClassType(STRING_SYM)
CLASS = ClassType(CLASS_SYM)
BUILTIN_CLASSES = (OBJECT_SYM, STRING_SYM, CLASS_SYM)


def is_user_class(t: Type) -> bool:
    return isinstance(t, ClassType) and not t.sym.builtin


def is_string(t: Type) -> bool:
    return t == STRING


def is_object(t: Type) -> bool:
    return t == OBJECT


def is_integral(t: Type) -> bool:
    return any(t is p for p in INTEGRAL)


def is_numeric(t: Type) -> bool:
    return any(t is p for p in NUMERIC)


def same_type(a: Type, b: Type) -> bool:
    if a.is_primitive or b.is_primitive:
        return a is b
    return a == b


def assignable(target: Type, value: Type) -> bool:
    """Присваивание/передача аргумента/возврат: только точное совпадение
    либо null → ссылочный тип (п. 5.4.2)."""
    if same_type(target, value):
        return True
    return value is NULL and target.is_reference and not isinstance(target, NullType)


# ---------------------------------------------------------------- operators
class OpError(Exception):
    def __init__(self, message: str, notes: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.notes = notes or []


ARITH_OPS = ("+", "-", "*", "/", "%")
SHIFT_OPS = ("<<", ">>", ">>>")
BIT_OPS = ("&", "|", "^")
REL_OPS = ("<", ">", "<=", ">=")
EQ_OPS = ("==", "!=")


def binary_result(op: str, lt: Type, rt: Type) -> Type:
    """Тип результата бинарной операции или OpError."""
    if op == "+" and (is_string(lt) or is_string(rt)):
        if (is_string(lt) and (is_string(rt) or rt is NULL)) or (lt is NULL and is_string(rt)):
            return STRING
        raise OpError("конкатенация определена только для String + String; "
                      "преобразуйте операнд явно (String.valueOf / toString)",
                      [f"операнды: {lt} + {rt}"])
    if op in ARITH_OPS:
        _require_same(op, lt, rt, is_numeric, "числового типа")
        return lt
    if op in SHIFT_OPS:
        _require_same(op, lt, rt, is_integral, "целочисленного типа")
        return lt
    if op in BIT_OPS:
        _require_same(op, lt, rt, lambda t: is_integral(t) or t is BOOLEAN, "целочисленного или boolean типа")
        return lt
    if op in REL_OPS:
        _require_same(op, lt, rt, is_numeric, "числового типа")
        return BOOLEAN
    if op in EQ_OPS:
        if lt.is_primitive or rt.is_primitive:
            if lt is not rt:
                raise OpError(f"операнды '{op}' должны иметь одинаковый тип", [f"операнды: {lt} {op} {rt}"])
            return BOOLEAN
        if isinstance(lt, QualifierType) or isinstance(rt, QualifierType):
            raise OpError("квалификатор родителя не является выражением")
        if lt is NULL or rt is NULL or lt == rt:
            return BOOLEAN
        raise OpError(f"сравнение ссылок разных типов запрещено; нужно явное приведение",
                      [f"операнды: {lt} {op} {rt}"])
    raise OpError(f"неизвестная операция {op}")


def _require_same(op: str, lt: Type, rt: Type, pred, what: str) -> None:
    if not pred(lt) or not pred(rt):
        raise OpError(f"операция '{op}' требует операндов {what}", [f"операнды: {lt} {op} {rt}"])
    if lt is not rt:
        raise OpError(f"операнды '{op}' должны иметь одинаковый тип (неявных преобразований нет)",
                      [f"операнды: {lt} {op} {rt}"])


def unary_result(op: str, t: Type) -> Type:
    if op in ("+", "-"):
        if is_numeric(t):
            return t
        raise OpError(f"унарный '{op}' требует числового операнда", [f"операнд: {t}"])
    if op == "~":
        if is_integral(t):
            return t
        raise OpError("'~' требует целочисленного операнда", [f"операнд: {t}"])
    if op == "!":
        if t is BOOLEAN:
            return t
        raise OpError("'!' требует операнда boolean", [f"операнд: {t}"])
    raise OpError(f"неизвестная операция {op}")


# ---------------------------------------------------------------- casts
@dataclass(frozen=True)
class CastInfo:
    """Классификация явного приведения (README §6.6)."""
    kind: str
    path: tuple = ()             # путь подобъекта для 'up'
    target: object = None        # символ целевого класса/интерфейса или тип массива
    static_true: bool = False    # instanceof заведомо истинно для не-null

    # kind ∈ identity prim null up to_object down_enclosing obj_to_class obj_exact
    #        iface_to_class to_iface_static to_iface_dynamic obj_to_iface


def classify_cast(src: Type, dst: Type, h: "Hierarchy") -> CastInfo:
    if isinstance(src, QualifierType):
        raise OpError("квалификатор родителя не является выражением")
    if src.is_primitive or dst.is_primitive:
        if src is dst:
            return CastInfo("identity", static_true=True)
        if src.is_primitive and dst.is_primitive and is_numeric(src) and is_numeric(dst):
            return CastInfo("prim")
        raise OpError(f"недопустимое приведение {src} к {dst}")
    if src is NULL:
        return CastInfo("null")
    if src == dst:
        return CastInfo("identity", static_true=True)
    if is_object(dst):
        return CastInfo("to_object", static_true=True)
    if is_object(src):
        if is_user_class(dst):
            return CastInfo("obj_to_class", target=dst.sym)
        if isinstance(dst, InterfaceType):
            return CastInfo("obj_to_iface", target=dst.sym)
        return CastInfo("obj_exact", target=dst)
    if isinstance(src, ArrayType) or isinstance(dst, ArrayType):
        raise OpError(f"недопустимое приведение {src} к {dst} (массивы совместимы только с Object)")
    if isinstance(src, ClassType) and isinstance(dst, ClassType):
        if src.sym.builtin or dst.sym.builtin:
            raise OpError(f"недопустимое приведение {src} к {dst}")
        paths = h.paths_to(src.sym, dst.sym)
        if len(paths) == 1:
            return CastInfo("up", path=paths[0], target=dst.sym, static_true=True)
        if len(paths) > 1:
            raise OpError(f"неоднозначное приведение: {dst} входит в {src} несколько раз "
                          f"({len(paths)} подобъекта)",
                          ["пути: " + "; ".join(h.path_str(src.sym, p) for p in paths)])
        if h.is_ancestor(src.sym, dst.sym):
            return CastInfo("down_enclosing", target=dst.sym)
        raise OpError(f"приведение между неродственными классами {src} и {dst} запрещено; "
                      f"используйте ({dst})(Object)x")
    if isinstance(dst, InterfaceType):
        if isinstance(src, ClassType):
            if src.sym.builtin and src.sym is not OBJECT_SYM:
                raise OpError(f"{src} не реализует {dst}")
            if dst.sym in h.implemented(src.sym):
                return CastInfo("to_iface_static", target=dst.sym, static_true=True)
            if src.sym.is_final:
                raise OpError(f"final-класс {src} не реализует интерфейс {dst}")
            return CastInfo("to_iface_dynamic", target=dst.sym)
        if isinstance(src, InterfaceType):
            if dst.sym in h.iface_closure(src.sym):
                return CastInfo("to_iface_static", target=dst.sym, static_true=True)
            return CastInfo("to_iface_dynamic", target=dst.sym)
    if isinstance(src, InterfaceType) and isinstance(dst, ClassType):
        if dst.sym.builtin:
            raise OpError(f"{dst} не реализует {src}")
        if dst.sym.is_final and src.sym not in h.implemented(dst.sym):
            raise OpError(f"final-класс {dst} не реализует интерфейс {src}")
        return CastInfo("iface_to_class", target=dst.sym)
    raise OpError(f"недопустимое приведение {src} к {dst}")


def classify_instanceof(src: Type, dst: Type, h: "Hierarchy") -> CastInfo:
    if src.is_primitive or dst.is_primitive:
        raise OpError("instanceof применим только к ссылочным типам")
    if src is NULL:
        return CastInfo("null")
    return classify_cast(src, dst, h)
