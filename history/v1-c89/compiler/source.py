"""Исходные файлы: BOM, строгое декодирование UTF-16, позиции.

Правило D15: файл обязан начинаться с BOM. FF FE означает UTF-16LE, FE FF —
UTF-16BE. Без BOM, при нечётной длине или непарном суррогате выдаётся
лексическая ошибка. Кодировка никогда не угадывается.
"""
from __future__ import annotations

import bisect
import struct
from dataclasses import dataclass

from .errors import LexicalError

BOM_LE = b"\xff\xfe"
BOM_BE = b"\xfe\xff"


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


def decode_utf16(name: str, data: bytes) -> SourceFile:
    head = data[:2]
    if head == BOM_LE:
        order = "<"
    elif head == BOM_BE:
        order = ">"
    else:
        raise LexicalError(
            "файл должен начинаться с BOM UTF-16 (FF FE — UTF-16LE, FE FF — UTF-16BE)",
            SourcePos(name, 0, 1, 1))
    body = data[2:]
    count = len(body) // 2
    units = struct.unpack(f"{order}{count}H", body[:count * 2])
    out: list[str] = []
    i = 0
    while i < count:
        u = units[i]
        if 0xD800 <= u <= 0xDBFF:
            if i + 1 < count and 0xDC00 <= units[i + 1] <= 0xDFFF:
                out.append(chr(0x10000 + ((u - 0xD800) << 10) + (units[i + 1] - 0xDC00)))
                i += 2
                continue
            _fail(name, out, f"непарный старший суррогат U+{u:04X} в исходном файле")
        elif 0xDC00 <= u <= 0xDFFF:
            _fail(name, out, f"непарный младший суррогат U+{u:04X} в исходном файле")
        out.append(chr(u))
        i += 1
    if len(body) % 2:
        _fail(name, out, "нечётная длина файла: неполная кодовая единица UTF-16 в конце")
    return SourceFile(name, "".join(out))


def _fail(name: str, decoded: list[str], message: str) -> None:
    partial = SourceFile(name, "".join(decoded))
    raise LexicalError(message, partial.pos(len(partial.text)))


def encode_utf16(text: str, big_endian: bool = False) -> bytes:
    """Обратная операция (для тестов и примеров): BOM + UTF-16."""
    if big_endian:
        return BOM_BE + text.encode("utf-16-be")
    return BOM_LE + text.encode("utf-16-le")
