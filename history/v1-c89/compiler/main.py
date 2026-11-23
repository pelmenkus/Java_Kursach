"""Командная строка компилятора.

    python -m compiler.main input.java [more.java ...] -o output.c [--out-dir DIR] [--main Class]

Создаёт output.c и output.h. Сборка исполняемого файла — отдельный этап
(python -m compiler.build). Коды завершения: 0 — успех, 1 — ошибки в исходной
программе, 2 — ошибка командной строки или ввода-вывода, 3 — внутренняя
ошибка компилятора.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback

from .codegen import generate
from .errors import CompilationFailed, CompileError, format_error
from .ir_builder import build_ir
from .lexer import tokenize
from .parser import parse
from .semantic import analyze
from .source import decode_utf16

EXIT_OK, EXIT_SOURCE, EXIT_USAGE, EXIT_INTERNAL = 0, 1, 2, 3


def compile_sources(paths: list[str], main_class: str | None = None):
    """Полный pipeline до текста C. Возвращает (header, source, sources) или
    бросает CompilationFailed с ошибками исходной программы."""
    sources = {}
    units = []
    for path in paths:
        with open(path, "rb") as fh:
            data = fh.read()
        name = os.path.basename(path)
        try:
            src = decode_utf16(name, data)
            sources[name] = src
            units.append(parse(tokenize(src), name))
        except CompileError as e:
            raise CompilationFailed([e]) from None
    program = analyze(units, main_class)
    ir_program = build_ir(program)
    return ir_program, sources


def emit_files(ir_program, out_c: str) -> tuple[str, str]:
    base, _ = os.path.splitext(out_c)
    out_h = base + ".h"
    header, source = generate(ir_program, os.path.basename(out_h))
    with open(out_h, "w", encoding="ascii", newline="\n") as fh:
        fh.write(header)
    with open(out_c, "w", encoding="ascii", newline="\n") as fh:
        fh.write(source)
    return out_c, out_h


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m compiler.main",
                                 description="Компилятор подмножества Java в ANSI C89")
    ap.add_argument("inputs", nargs="+", help="исходные файлы (UTF-16 с BOM)")
    ap.add_argument("-o", "--output", default="output.c", help="имя выходного .c (рядом создаётся .h)")
    ap.add_argument("--out-dir", default=None, help="каталог для выходных файлов")
    ap.add_argument("--main", dest="main_class", default=None, help="класс с точкой входа main")
    ap.add_argument("--debug", action="store_true", help="traceback для внутренних ошибок")
    args = ap.parse_args(argv)

    out_c = args.output
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        out_c = os.path.join(args.out_dir, os.path.basename(out_c))
    if not out_c.endswith(".c"):
        print("ошибка: имя выходного файла должно оканчиваться на .c", file=sys.stderr)
        return EXIT_USAGE
    sources = {}
    try:
        try:
            ir_program, sources = compile_sources(args.inputs, args.main_class)
        except OSError as e:
            print(f"ошибка ввода-вывода: {e}", file=sys.stderr)
            return EXIT_USAGE
        emit_files(ir_program, out_c)
    except CompilationFailed as failed:
        srcs = sources or _read_for_messages(args.inputs)
        for err in failed.errors:
            print(format_error(err, srcs), file=sys.stderr)
        print(f"компиляция прервана, ошибок: {len(failed.errors)}", file=sys.stderr)
        return EXIT_SOURCE
    except OSError as e:
        print(f"ошибка ввода-вывода: {e}", file=sys.stderr)
        return EXIT_USAGE
    except Exception:  # noqa: BLE001 — граница между ошибками программы и дефектами компилятора
        print("internal compiler error: дефект компилятора, а не ошибка в программе", file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        else:
            exc = sys.exc_info()[1]
            print(f"  {type(exc).__name__}: {exc} (подробности: --debug)", file=sys.stderr)
        return EXIT_INTERNAL
    return EXIT_OK


def _read_for_messages(paths: list[str]) -> dict:
    out = {}
    for path in paths:
        try:
            with open(path, "rb") as fh:
                src = decode_utf16(os.path.basename(path), fh.read())
            out[src.name] = src
        except (OSError, CompileError):
            pass
    return out


if __name__ == "__main__":
    sys.exit(main())
