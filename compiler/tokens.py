"""Токены исходного языка."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from .source import SourcePos


class TokenKind(Enum):
    IDENT = auto()
    KEYWORD = auto()
    INT_LIT = auto()
    LONG_LIT = auto()
    FLOAT_LIT = auto()
    DOUBLE_LIT = auto()
    CHAR_LIT = auto()
    STRING_LIT = auto()
    BOOL_LIT = auto()
    NULL_LIT = auto()
    OP = auto()
    EOF = auto()


# Ключевые слова поддерживаемого подмножества.
KEYWORDS = frozenset("""
abstract boolean break byte char class continue double else extends final float
for if implements instanceof int interface long new private protected public
return short static super this throw throws void while
""".split())

# Зарезервированные слова Java, конструкции которых не поддерживаются.
# Лексер выдаёт их как ключевые слова, парсер сообщает «не поддерживается».
RESERVED = frozenset("""
do switch case default try catch finally package import native
synchronized transient volatile strictfp goto const enum assert
""".split())

PRIMITIVE_KEYWORDS = frozenset("byte short int long float double char boolean".split())

# Операторы и разделители, от длинных к коротким (жадное сопоставление).
OPERATORS = (
    ">>>=", "<<=", ">>=", ">>>", "...", "->", "::",
    "==", "!=", "<=", ">=", "&&", "||", "++", "--",
    "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<", ">>",
    "+", "-", "*", "/", "%", "=", "<", ">", "!", "~", "?", ":",
    "&", "|", "^", ".", ",", ";", "(", ")", "[", "]", "{", "}", "@",
)


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    text: str          # исходный фрагмент
    value: object      # значение литерала (см. lexer.py)
    pos: SourcePos

    @property
    def line(self) -> int:
        return self.pos.line

    @property
    def column(self) -> int:
        return self.pos.column

    def is_op(self, *ops: str) -> bool:
        return self.kind is TokenKind.OP and self.text in ops

    def is_kw(self, *words: str) -> bool:
        return self.kind is TokenKind.KEYWORD and self.text in words

    def describe(self) -> str:
        if self.kind is TokenKind.EOF:
            return "конец файла"
        return f"'{self.text}'"
