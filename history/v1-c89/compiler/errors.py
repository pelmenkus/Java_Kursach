"""Диагностика.

Ошибки исходной программы (CompileError и подклассы) отделены от внутренних
ошибок компилятора (InternalCompilerError): первые форматируются для
пользователя, вторые означают дефект самого компилятора.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .source import SourceFile, SourcePos


class CompileError(Exception):
    category = "semantic"

    def __init__(self, message: str, pos: "SourcePos | None" = None,
                 notes: Iterable[str] | None = None, length: int = 1):
        super().__init__(message)
        self.message = message
        self.pos = pos
        self.notes = list(notes) if notes else []
        self.length = max(1, length)

    def __str__(self) -> str:
        where = f"{self.pos}: " if self.pos is not None else ""
        return f"{where}error[{self.category}]: {self.message}"


class LexicalError(CompileError):
    category = "lexical"


class ParseError(CompileError):
    category = "syntax"


class TypeCheckError(CompileError):
    category = "type"


class SemanticError(CompileError):
    category = "semantic"


class CodegenError(CompileError):
    category = "codegen"


class CompilationFailed(Exception):
    """Набор ошибок исходной программы, после которых компиляция остановлена."""

    def __init__(self, errors: Iterable[CompileError]):
        self.errors = list(errors)
        super().__init__(f"{len(self.errors)} error(s)")


class InternalCompilerError(Exception):
    """Дефект компилятора, а не ошибка в программе пользователя."""


def type_notes(expected, found) -> list[str]:
    return [f"ожидается: {expected}", f"получено:  {found}"]


def format_error(err: CompileError, sources: "dict[str, SourceFile]") -> str:
    lines = [str(err)]
    src = sources.get(err.pos.file) if err.pos is not None else None
    if src is not None and 1 <= err.pos.line <= src.line_count:
        text = src.line_text(err.pos.line).replace("\t", " ")
        lines.append("    " + text)
        width = max(1, min(err.length, len(text) - err.pos.column + 1))
        lines.append("    " + " " * (err.pos.column - 1) + "^" + "~" * (width - 1))
    for note in err.notes:
        lines.append("  " + note)
    return "\n".join(lines)
