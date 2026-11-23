"""Лексический анализ.

Значения литералов (Token.value):
  INT_LIT    — int; десятичный 2147483648 сохраняется как есть, парсер принимает
               его только как операнд унарного минуса (D11);
  LONG_LIT   — int (аналогично для 9223372036854775808L);
  FLOAT_LIT  — FloatBits(bits32), DOUBLE_LIT — FloatBits(bits64);
  CHAR_LIT   — int, одна Unicode scalar value (U+0000..U+10FFFF без суррогатов);
  STRING_LIT — tuple[int, ...] Unicode scalar values;
  BOOL_LIT   — bool; NULL_LIT — None.
"""
from __future__ import annotations

import struct
import unicodedata
from dataclasses import dataclass
from fractions import Fraction

from .errors import LexicalError
from .source import SourceFile
from .tokens import KEYWORDS, OPERATORS, RESERVED, Token, TokenKind


@dataclass(frozen=True)
class FloatBits:
    bits: int
    width: int          # 32 или 64

    @property
    def value(self) -> float:
        if self.width == 32:
            return struct.unpack("<f", struct.pack("<I", self.bits))[0]
        return struct.unpack("<d", struct.pack("<Q", self.bits))[0]


def round_to_ieee(x: Fraction, mant_bits: int, exp_bits: int) -> int | None:
    """Точное округление положительного рационального числа к IEEE 754
    (round-half-even). Возвращает биты или None при переполнении."""
    if x == 0:
        return 0
    bias = (1 << (exp_bits - 1)) - 1
    emin, emax = 1 - bias, bias
    n, d = x.numerator, x.denominator
    e = n.bit_length() - d.bit_length()
    if not ((n >= d << e) if e >= 0 else ((n << -e) >= d)):
        e -= 1
    # теперь 2^e <= x < 2^(e+1)
    subnormal = e < emin
    e_eff = emin if subnormal else e
    shift = e_eff - mant_bits
    num, den = (n, d << shift) if shift >= 0 else (n << -shift, d)
    q, r = divmod(num, den)
    if 2 * r > den or (2 * r == den and q & 1):
        q += 1
    if subnormal:
        if q >= 1 << mant_bits:
            return (1 << mant_bits) | (q - (1 << mant_bits))
        return q
    if q >= 1 << (mant_bits + 1):
        q >>= 1
        e_eff += 1
    if e_eff > emax:
        return None
    return ((e_eff + bias) << mant_bits) | (q - (1 << mant_bits))


# Раздел 11: идентификаторы только ASCII, [A-Za-z_$][A-Za-z0-9_$]*
_ID_START = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$")
_ID_PART = _ID_START | frozenset("0123456789")


def _is_ident_start(ch: str) -> bool:
    return ch in _ID_START and ch != ""


def _is_ident_part(ch: str) -> bool:
    return ch in _ID_PART and ch != ""


def _is_unicode_ident_char(ch: str) -> bool:
    """Не-ASCII символ, который в Java мог бы входить в идентификатор."""
    if not ch or ord(ch) < 0x80:
        return False
    return unicodedata.category(ch)[0] in "LMN" or unicodedata.category(ch) == "Pc"


_SIMPLE_ESCAPES = {"n": 0x0A, "t": 0x09, "\\": 0x5C, '"': 0x22, "'": 0x27}
_HEX = "0123456789abcdefABCDEF"
_DIGITS = tuple("0123456789")   # кортеж: '' in _DIGITS ложно


class Lexer:
    def __init__(self, src: SourceFile):
        self.src = src
        self.text = src.text
        self.i = 0

    # ------------------------------------------------------------ utilities
    def _error(self, message: str, offset: int, length: int = 1) -> LexicalError:
        return LexicalError(message, self.src.pos(offset), length=length)

    def _peek(self, k: int = 0) -> str:
        j = self.i + k
        return self.text[j] if j < len(self.text) else ""

    def _token(self, kind: TokenKind, start: int, value: object = None) -> Token:
        return Token(kind, self.text[start:self.i], value, self.src.pos(start))

    # ------------------------------------------------------------ main loop
    def tokenize(self) -> list[Token]:
        out: list[Token] = []
        while True:
            self._skip_trivia()
            if self.i >= len(self.text):
                out.append(Token(TokenKind.EOF, "", None, self.src.pos(self.i)))
                return out
            out.append(self._next_token())

    def _skip_trivia(self) -> None:
        text = self.text
        while self.i < len(text):
            ch = text[self.i]
            if ch in " \t\f\r\n":
                self.i += 1
            elif text.startswith("//", self.i):
                while self.i < len(text) and text[self.i] not in "\r\n":
                    self.i += 1
            elif text.startswith("/*", self.i):
                end = text.find("*/", self.i + 2)
                if end < 0:
                    raise self._error("незакрытый комментарий /* ... */", self.i, 2)
                self.i = end + 2
            else:
                return

    def _next_token(self) -> Token:
        ch = self._peek()
        start = self.i
        if _is_ident_start(ch):
            return self._identifier()
        if ch in _DIGITS or (ch == "." and self._peek(1) in _DIGITS):
            return self._number()
        if ch == "'":
            return self._char_literal()
        if ch == '"':
            return self._string_literal()
        for op in OPERATORS:
            if self.text.startswith(op, start):
                self.i += len(op)
                return self._token(TokenKind.OP, start)
        if _is_unicode_ident_char(ch):
            raise self._error(f"не-ASCII символ U+{ord(ch):04X} '{ch}' в идентификаторе: "
                              f"допустимы только [A-Za-z0-9_$]", start)
        raise self._error(f"недопустимый символ U+{ord(ch):04X} '{ch}'", start)

    # ------------------------------------------------------------ identifiers
    def _identifier(self) -> Token:
        start = self.i
        self.i += 1
        while self.i < len(self.text) and _is_ident_part(self.text[self.i]):
            self.i += 1
        if _is_unicode_ident_char(self._peek()):
            ch = self._peek()
            raise self._error(f"не-ASCII символ U+{ord(ch):04X} '{ch}' в идентификаторе: "
                              f"допустимы только [A-Za-z0-9_$]", self.i)
        word = self.text[start:self.i]
        if word == "_":
            raise self._error("'_' не может быть идентификатором", start)
        if word in ("true", "false"):
            return self._token(TokenKind.BOOL_LIT, start, word == "true")
        if word == "null":
            return self._token(TokenKind.NULL_LIT, start, None)
        if word in KEYWORDS or word in RESERVED:
            return self._token(TokenKind.KEYWORD, start)
        return self._token(TokenKind.IDENT, start)

    # ------------------------------------------------------------ numbers
    def _scan_digits(self, alphabet: str) -> str:
        start = self.i
        while self.i < len(self.text) and self.text[self.i] in alphabet:
            self.i += 1
        return self.text[start:self.i]

    def _check_number_end(self, start: int) -> None:
        ch = self._peek()
        if ch == "_":
            raise self._error("символ '_' в числовых литералах не поддерживается", start, self.i - start + 1)
        if ch and (_is_ident_part(ch) or ch == "."):
            raise self._error("некорректный числовой литерал", start, self.i - start + 1)

    def _number(self) -> Token:
        start = self.i
        if self._peek() == "0" and self._peek(1) in ("x", "X"):
            self.i += 2
            digits = self._scan_digits(_HEX)
            if not digits:
                raise self._error("в шестнадцатеричном литерале нет цифр", start, 2)
            if self._peek() in (".", "p", "P"):
                raise self._error("шестнадцатеричные вещественные литералы не поддерживаются", start)
            value = int(digits, 16)
            if self._peek() in ("l", "L"):
                self.i += 1
                self._check_number_end(start)
                if value > 0xFFFFFFFFFFFFFFFF:
                    raise self._error("литерал вне диапазона long", start, self.i - start)
                if value >= 1 << 63:
                    value -= 1 << 64
                return self._token(TokenKind.LONG_LIT, start, value)
            self._check_number_end(start)
            if value > 0xFFFFFFFF:
                raise self._error("литерал вне диапазона int", start, self.i - start)
            if value >= 1 << 31:
                value -= 1 << 32
            return self._token(TokenKind.INT_LIT, start, value)
        if self._peek() == "0" and self._peek(1) in ("b", "B"):
            raise self._error("двоичные литералы не поддерживаются", start, 2)

        int_part = self._scan_digits("0123456789")
        frac_part = None
        exp_part = None
        if self._peek() == "." and self._peek(1) != ".":
            self.i += 1
            frac_part = self._scan_digits("0123456789")
        if self._peek() in ("e", "E"):
            mark = self.i
            self.i += 1
            sign = ""
            if self._peek() in ("+", "-"):
                sign = self._peek()
                self.i += 1
            digits = self._scan_digits("0123456789")
            if not digits:
                raise self._error("в экспоненте нет цифр", mark, self.i - mark)
            exp_part = int(sign + digits)
        suffix = self._peek()
        is_float = frac_part is not None or exp_part is not None or suffix in ("f", "F", "d", "D")
        if not is_float:
            if suffix in ("l", "L"):
                self.i += 1
            self._check_number_end(start)
            if len(int_part) > 1 and int_part[0] == "0":
                raise self._error("восьмеричные литералы не поддерживаются", start, self.i - start)
            value = int(int_part)
            if suffix in ("l", "L"):
                if value > 1 << 63:
                    raise self._error("литерал вне диапазона long", start, self.i - start)
                return self._token(TokenKind.LONG_LIT, start, value)
            if value > 1 << 31:
                raise self._error("литерал вне диапазона int", start, self.i - start)
            return self._token(TokenKind.INT_LIT, start, value)

        width = 64
        if suffix in ("f", "F"):
            width = 32
            self.i += 1
        elif suffix in ("d", "D"):
            self.i += 1
        elif suffix in ("l", "L"):
            raise self._error("суффикс L недопустим для вещественного литерала", start, self.i - start + 1)
        self._check_number_end(start)
        mantissa = int_part + (frac_part or "")
        scale = len(frac_part or "") - (exp_part or 0)
        x = Fraction(int(mantissa or "0"))
        x = x / (10 ** scale) if scale >= 0 else x * (10 ** -scale)
        mant_bits, exp_bits = (23, 8) if width == 32 else (52, 11)
        bits = round_to_ieee(x, mant_bits, exp_bits)
        kind_name = "float" if width == 32 else "double"
        if bits is None:
            raise self._error(f"литерал слишком велик для {kind_name}", start, self.i - start)
        if bits == 0 and x != 0:
            raise self._error(f"литерал слишком мал для {kind_name}", start, self.i - start)
        kind = TokenKind.FLOAT_LIT if width == 32 else TokenKind.DOUBLE_LIT
        return self._token(kind, start, FloatBits(bits, width))

    # ------------------------------------------------------------ literals
    def _escape(self) -> int:
        """Текущая позиция — на обратной косой черте. Возвращает кодовую единицу."""
        start = self.i
        self.i += 1
        ch = self._peek()
        if ch in _SIMPLE_ESCAPES:
            self.i += 1
            return _SIMPLE_ESCAPES[ch]
        if ch == "u":
            self.i += 1
            digits = self.text[self.i:self.i + 4]
            if len(digits) != 4 or any(c not in _HEX for c in digits):
                raise self._error("после \\u ожидаются ровно 4 шестнадцатеричные цифры", start, 2)
            self.i += 4
            value = int(digits, 16)
            if 0xD800 <= value <= 0xDFFF:
                raise self._error(f"\\u{digits}: суррогатные значения недопустимы "
                                  f"(char — Unicode scalar value)", start, 6)
            return value
        raise self._error(f"недопустимая escape-последовательность '\\{ch}'", start, 2)

    def _char_literal(self) -> Token:
        start = self.i
        self.i += 1
        ch = self._peek()
        if ch == "" or ch in "\r\n":
            raise self._error("незакрытый символьный литерал", start)
        if ch == "'":
            raise self._error("пустой символьный литерал", start, 2)
        if ch == "\\":
            unit = self._escape()
        else:
            unit = ord(ch)
            self.i += 1
        if self._peek() != "'":
            raise self._error("символьный литерал должен содержать ровно один символ", start, self.i - start + 1)
        self.i += 1
        return self._token(TokenKind.CHAR_LIT, start, unit)

    def _string_literal(self) -> Token:
        start = self.i
        self.i += 1
        units: list[int] = []
        while True:
            ch = self._peek()
            if ch == "" or ch in "\r\n":
                raise self._error("незакрытый строковый литерал", start)
            if ch == '"':
                self.i += 1
                break
            if ch == "\\":
                units.append(self._escape())
                continue
            units.append(ord(ch))
            self.i += 1
        return self._token(TokenKind.STRING_LIT, start, tuple(units))


def tokenize(src: SourceFile) -> list[Token]:
    return Lexer(src).tokenize()
