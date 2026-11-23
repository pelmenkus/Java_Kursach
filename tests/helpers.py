"""Общие средства тестов: компиляция Java-текста, сборка GCC в режиме C99, запуск.

Каждая программа собирается и запускается на всех уровнях оптимизации из
OPT_LEVELS (по умолчанию -O0 и -O2; переменная окружения JC_OPT_LEVELS,
например "0,2,3"). Вывод, stderr и код завершения обязаны совпасть:
корректность сгенерированного кода и GC не зависит от оптимизаций.
"""
from __future__ import annotations

import glob
import os
import subprocess
import tempfile
import unittest

from compiler.build import C_FLAGS, LIBS, RUNTIME_DIR, WIN_ADAPTER
from compiler.codegen import generate
from compiler.errors import CompilationFailed, CompileError
from compiler.ir_builder import build_ir
from compiler.lexer import tokenize
from compiler.parser import parse
from compiler.semantic import analyze
from compiler.source import SourceFile, decode_utf8, encode_utf8

OPT_LEVELS = [x.strip() for x in os.environ.get("JC_OPT_LEVELS", "0,2").split(",") if x.strip()]
_WORK = tempfile.mkdtemp(prefix="jc99_tests_")
_RUNTIME_OBJS: dict[str, list[str]] = {}
_counter = [0]


def source(code: str, name: str = "Test.java") -> SourceFile:
    return decode_utf8(name, encode_utf8(code))


def frontend(code: str, main_class: str | None = None):
    src = source(code)
    return analyze([parse(tokenize(src), src.name)], main_class)


def errors_of(code: str) -> list[CompileError]:
    """Ошибки компиляции (лексика, синтаксис, семантика). Пустой список — успех."""
    try:
        frontend(code)
    except CompilationFailed as f:
        return f.errors
    except CompileError as e:
        return [e]
    return []


def _runtime_sources(win_args: bool) -> list[str]:
    srcs = sorted(glob.glob(os.path.join(RUNTIME_DIR, "*.c")))
    if win_args:
        srcs = [s for s in srcs if os.path.basename(s) != "rt_args.c"] + [os.path.join(RUNTIME_DIR, WIN_ADAPTER)]
    return srcs


def runtime_objects(opt: str, win_args: bool = False) -> list[str]:
    key = f"{opt}{'w' if win_args else ''}"
    if key not in _RUNTIME_OBJS:
        objs = []
        for c in _runtime_sources(win_args):
            obj = os.path.join(_WORK, f"O{opt}_{os.path.basename(c)}.o")
            r = subprocess.run(["gcc", *C_FLAGS, f"-O{opt}", "-I", RUNTIME_DIR, "-c", c, "-o", obj],
                               capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
            if r.returncode != 0 or r.stderr.strip():
                raise AssertionError(f"runtime {c} не собирается без предупреждений (-O{opt}):\n{r.stderr}")
            objs.append(obj)
        _RUNTIME_OBJS[key] = objs
    return list(_RUNTIME_OBJS[key])


def build_c(c_files: list[str], exe: str, include: str, opt: str = "0",
            with_main: bool = True, win_args: bool = False) -> subprocess.CompletedProcess:
    objs = runtime_objects(opt, win_args)
    if not with_main:
        objs = [o for o in objs if not o.endswith("rt_main.c.o")]
    cmd = ["gcc", *C_FLAGS, f"-O{opt}", "-I", RUNTIME_DIR, "-I", include, *c_files, *objs, "-o", exe, *LIBS,
           *(["-lshell32"] if win_args else [])]
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


class Result:
    def __init__(self, stdout: str, stderr: str, code: int, c_source: str):
        self.stdout = stdout
        self.stderr = stderr
        self.code = code
        self.c_source = c_source

    @property
    def lines(self) -> list[str]:
        return self.stdout.split("\n")[:-1] if self.stdout.endswith("\n") else self.stdout.split("\n")


def compile_to_dir(code: str, main_class: str | None = None) -> tuple[str, str, str]:
    """Java → IR → C99; возвращает (каталог, путь .c, текст .c)."""
    _counter[0] += 1
    d = os.path.join(_WORK, f"p{_counter[0]}")
    os.makedirs(d)
    program = frontend(code, main_class)
    header, c_text = generate(build_ir(program), "prog.h")
    with open(os.path.join(d, "prog.h"), "w", encoding="ascii", newline="\n") as fh:
        fh.write(header)
    c_path = os.path.join(d, "prog.c")
    with open(c_path, "w", encoding="ascii", newline="\n") as fh:
        fh.write(c_text)
    return d, c_path, c_text


def run_exe(exe: str, args: list[str] | None = None) -> tuple[str, str, int]:
    p = subprocess.run([exe, *(args or [])], capture_output=True, timeout=120)
    out = p.stdout.decode("utf-8").replace("\r\n", "\n")
    err = p.stderr.decode("utf-8", "replace").replace("\r\n", "\n")
    return out, err, p.returncode


def run_java(code: str, args: list[str] | None = None, main_class: str | None = None,
             win_args: bool = False) -> Result:
    """Полный цикл: Java → IR → C99 → gcc -std=c99 -pedantic-errors -O{0,2} → запуск.

    win_args — собрать с необязательным адаптером аргументов Windows (UTF-16 → UTF-8)."""
    d, c_path, c_text = compile_to_dir(code, main_class)
    results = []
    for opt in OPT_LEVELS:
        exe = os.path.join(d, f"prog_O{opt}.exe")
        r = build_c([c_path], exe, d, opt, win_args=win_args)
        if r.returncode != 0 or r.stderr.strip():
            raise AssertionError(f"сгенерированный C не собирается чисто (C99, -O{opt}):\n{r.stderr[:4000]}")
        results.append((opt, run_exe(exe, args)))
    base_opt, base = results[0]
    for opt, other in results[1:]:
        if other != base:
            raise AssertionError(f"результаты -O{base_opt} и -O{opt} различаются:\n"
                                 f"-O{base_opt}: {base!r}\n-O{opt}: {other!r}")
    out, err, code_ = base
    return Result(out, err, code_, c_text)


class CompilerTestCase(unittest.TestCase):
    maxDiff = None

    def assertOutput(self, code: str, expected: str, args=None):
        r = run_java(code, args)
        self.assertEqual(r.code, 0, f"код завершения {r.code}, stderr:\n{r.stderr}")
        self.assertEqual(r.stdout, expected)
        return r

    def assertRuntimeError(self, code: str, kind: str, fragment: str = ""):
        r = run_java(code)
        self.assertEqual(r.code, 1, f"ожидалась runtime error, вывод:\n{r.stdout}\n{r.stderr}")
        self.assertIn(f"Runtime error [{kind}]", r.stderr)
        if fragment:
            self.assertIn(fragment, r.stderr)
        return r

    def assertThrows(self, code: str, first_line: str, expected_stdout: str | None = None):
        r = run_java(code)
        self.assertEqual(r.code, 1, f"ожидался неперехваченный throw, вывод:\n{r.stdout}\n{r.stderr}")
        self.assertEqual(r.stderr.split("\n")[0], first_line)
        self.assertIn("  at ", r.stderr)
        if expected_stdout is not None:
            self.assertEqual(r.stdout, expected_stdout)
        return r

    def assertCompileError(self, code: str, fragment: str, category: str | None = None):
        errs = errors_of(code)
        self.assertTrue(errs, f"ожидалась ошибка компиляции «{fragment}»")
        text = "\n".join(f"[{e.category}] {e.message} {' '.join(e.notes)}" for e in errs)
        self.assertIn(fragment, text)
        if category is not None:
            self.assertTrue(any(e.category == category for e in errs), text)
        return errs

    def assertCompiles(self, code: str):
        errs = errors_of(code)
        self.assertEqual([str(e) for e in errs], [])


def main_wrap(body: str, extra: str = "") -> str:
    return extra + "\nclass Main {\n  public static void main(String[] args) {\n" + body + "\n  }\n}\n"
