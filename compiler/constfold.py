"""Вычисление константных выражений (решение N4).

Константное выражение: литерал примитивного типа или String, простое имя /
ИмяКласса.поле константной переменной (final-поле примитивного типа или
String с константным инициализатором), а также унарные, бинарные,
логические, условные операции и приведения к примитивам над константами.

Представление значений: целые типы и char — int Python (нормализованный),
boolean — bool, float/double — float Python (значение float уже округлено
к binary32), String — tuple кодовых точек. Семантика совпадает с runtime:
модульная целочисленная арифметика, усечение при приведении, IEEE 754.

Целочисленное деление на ноль не сворачивается (это ошибка времени
выполнения). Недопустимый результат char-арифметики (решение N2) — ошибка
компиляции (FoldError).
"""
from __future__ import annotations

import math
import struct
from fractions import Fraction

from . import type_system as T
from .lexer import round_to_ieee

_BITS = {"byte": 8, "short": 16, "int": 32, "long": 64}


class FoldError(Exception):
    pass


def wrap(v: int, bits: int) -> int:
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >= 1 << (bits - 1) else v


def valid_char(v: int) -> bool:
    return 0 <= v <= 0x10FFFF and not 0xD800 <= v <= 0xDFFF


def check_char(v: int) -> int:
    if not valid_char(v):
        shown = f"{v}" if v < 0 else f"0x{v:X}"
        raise FoldError(f"результат char-выражения {shown} не является Unicode scalar value "
                        f"(допустимо U+0000..U+10FFFF без U+D800..U+DFFF)")
    return v


def norm_int(t: T.Type, v: int) -> int:
    if t is T.CHAR:
        return check_char(v)
    return wrap(v, _BITS[str(t)])


def f32(x: float) -> float:
    """Округление double к ближайшему binary32 (переполнение → ±inf)."""
    if math.isnan(x) or math.isinf(x):
        return x
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


def int_to_float(v: int, t: T.Type) -> float:
    """Точное округление целого к binary32/binary64 (как приведение в C99)."""
    if v == 0:
        return 0.0
    mant, exp = (23, 8) if t is T.FLOAT else (52, 11)
    bits = round_to_ieee(Fraction(abs(v)), mant, exp)
    if bits is None:
        mag = math.inf
    elif t is T.FLOAT:
        mag = struct.unpack("<f", struct.pack("<I", bits))[0]
    else:
        mag = struct.unpack("<d", struct.pack("<Q", bits))[0]
    return -mag if v < 0 else mag


def float_to_int(x: float, bits: int) -> int:
    """Java-подобное усечение: NaN → 0, насыщение на границах диапазона."""
    if math.isnan(x):
        return 0
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    if x <= lo:
        return lo
    if x >= hi:
        return hi
    return int(x)       # усечение к нулю


def round_to(t: T.Type, x: float) -> float:
    return f32(x) if t is T.FLOAT else x


# ---------------------------------------------------------------- operations
def unary(op: str, t: T.Type, v):
    if op == "!":
        return not v
    if op == "+":
        return v
    if t in T.FLOATING:
        if op == "-":
            return -v
        return None
    if op == "-":
        return norm_int(t, -v)
    if op == "~":
        return norm_int(t, ~v)
    return None


def _fdiv(a: float, b: float) -> float:
    if b == 0.0:
        if a == 0.0 or math.isnan(a):
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)
    return a / b


def _frem(a: float, b: float) -> float:
    if math.isnan(a) or math.isnan(b) or math.isinf(a) or b == 0.0:
        return math.nan
    if math.isinf(b):
        return a
    return math.fmod(a, b)


def binary(op: str, t: T.Type, a, b):
    """op над операндами типа t (типы операндов совпадают)."""
    if op in ("==", "!="):
        if t.is_reference:
            return None          # сравнение ссылок — не константное выражение
        eq = a == b
        return eq if op == "==" else not eq
    if op in ("<", ">", "<=", ">="):
        return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
    if T.is_string(t):
        return a + b if op == "+" else None
    if t is T.BOOLEAN:
        return {"&": a and b, "|": a or b, "^": a != b}.get(op)
    if t in T.FLOATING:
        if op == "+":
            return round_to(t, a + b)
        if op == "-":
            return round_to(t, a - b)
        if op == "*":
            return round_to(t, a * b)
        if op == "/":
            return round_to(t, _fdiv(a, b))
        if op == "%":
            return round_to(t, _frem(a, b))
        return None
    # целые и char: вычисление в int32/int64, затем нормализация к типу
    wide = 64 if t is T.LONG else 32
    if op in ("+", "-", "*"):
        r = {"+": a + b, "-": a - b, "*": a * b}[op]
        return norm_int(t, r) if t is T.CHAR else wrap(wrap(r, wide), _BITS.get(str(t), wide))
    if op in ("/", "%"):
        if b == 0:
            return None
        q = abs(a) // abs(b) * (1 if (a >= 0) == (b >= 0) else -1)
        r = q if op == "/" else a - q * b
        return norm_int(t, r) if t is T.CHAR else wrap(wrap(r, wide), _BITS.get(str(t), wide))
    if op in ("&", "|", "^"):
        r = {"&": a & b, "|": a | b, "^": a ^ b}[op]
        return norm_int(t, r)
    if op in ("<<", ">>", ">>>"):
        n = b & (wide - 1)
        if op == "<<":
            r = a << n
        elif op == ">>":
            r = a >> n
        else:
            r = (a & ((1 << wide) - 1)) >> n
        if t is T.CHAR:
            return check_char(r)
        return wrap(wrap(r, wide), _BITS.get(str(t), wide))
    return None


def cast(src: T.Type, dst: T.Type, v):
    """Явное приведение константы между примитивами."""
    if src is dst:
        return v
    if src is T.BOOLEAN or dst is T.BOOLEAN:
        return None
    if src in T.FLOATING:
        if dst in T.FLOATING:
            return round_to(dst, v)
        if dst is T.LONG:
            return float_to_int(v, 64)
        i = float_to_int(v, 32)
        return check_char(i) if dst is T.CHAR else wrap(i, _BITS[str(dst)])
    # src — целый тип или char
    if dst in T.FLOATING:
        return int_to_float(v, dst)
    if dst is T.CHAR:
        return check_char(v)
    return wrap(v, _BITS[str(dst)])
