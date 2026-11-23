"""Исходные файлы: строгое декодирование UTF-8, позиции.

Раздел 11 ТЗ: исходный файл — строгий UTF-8. BOM UTF-8 (EF BB BF) необязателен
и пропускается. Некорректные последовательности, overlong-формы, закодированные
суррогаты и значения больше U+10FFFF — лексическая ошибка. Файлы UTF-16 не
поддерживаются: BOM UTF-16 распознаётся только ради понятного сообщения.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass

from .errors import LexicalError

BOM_UTF8 = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class SourcePos:
    file: str
    offset: int   # индекс кодовой точки в декодированном тексте
    line: int     # с 1
    column: int   # с 1, в кодовых точках (I17)

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.column}"


class SourceFile:
    def __init__(self, name: str, text: str):
        self.name = name
        self.text = text
        starts = [0]
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch == "\r":
                if i + 1 < n and text[i + 1] == "\n":
                    i += 1
                starts.append(i + 1)
            elif ch == "\n":
                starts.append(i + 1)
            i += 1
        self._starts = starts

    @property
    def line_count(self) -> int:
        return len(self._starts)

    def pos(self, offset: int) -> SourcePos:
        line = bisect.bisect_right(self._starts, offset)
        return SourcePos(self.name, offset, line, offset - self._starts[line - 1] + 1)

    def line_text(self, line: int) -> str:
        start = self._starts[line - 1]
        end = start
        while end < len(self.text) and self.text[end] not in "\r\n":
            end += 1
        return self.text[start:end]


def decode_utf8(name: str, data: bytes) -> SourceFile:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        raise LexicalError("файл в UTF-16 не поддерживается: исходный текст должен быть в UTF-8",
                           SourcePos(name, 0, 1, 1))
    body = data[len(BOM_UTF8):] if data.startswith(BOM_UTF8) else data
    try:
        # кодек Python строгий: отвергает overlong, суррогаты и > U+10FFFF
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as e:
        partial = SourceFile(name, body[:e.start].decode("utf-8"))
        raise LexicalError(f"некорректная последовательность UTF-8 (байт 0x{body[e.start]:02X} "
                           f"по смещению {e.start + (len(data) - len(body))})",
                           partial.pos(len(partial.text))) from None
    return SourceFile(name, text)


def encode_utf8(text: str, bom: bool = False) -> bytes:
    """Обратная операция (для тестов и примеров)."""
    return (BOM_UTF8 if bom else b"") + text.encode("utf-8")
