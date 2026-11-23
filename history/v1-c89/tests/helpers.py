"""Общие средства тестов: компиляция Java-текста, сборка GCC в режиме C90, запуск."""
from __future__ import annotations

import glob
import os
import subprocess
import tempfile
import unittest

from compiler.build import C_FLAGS, LIBS, RUNTIME_DIR
from compiler.codegen import generate
from compiler.errors import CompilationFailed, CompileError
from compiler.ir_builder import build_ir
from compiler.lexer import tokenize
from compiler.parser import parse
from compiler.semantic import analyze
from compiler.source import SourceFile, decode_utf16, encode_utf16

_WORK = tempfile.mkdtemp(prefix="jc89_tests_")
_RUNTIME_OBJS: list[str] = []
_counter = [0]


def source(code: str, name: str = "Test.java") -> SourceFile:
    return decode_utf16(name, encode_utf16(code))


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


def runtime_objects() -> list[str]:
    if not _RUNTIME_OBJS:
        for c in sorted(glob.glob(os.path.join(RUNTIME_DIR, "*.c"))):
            obj = os.path.join(_WORK, os.path.basename(c) + ".o")
            r = subprocess.run(["gcc", *C_FLAGS, "-I", RUNTIME_DIR, "-c", c, "-o", obj],
                               capture_output=True, text=True)
            if r.returncode != 0 or r.stderr.strip():
                raise AssertionError(f"runtime {c} не собирается без предупреждений:\n{r.stderr}")
            _RUNTIME_OBJS.append(obj)
    return list(_RUNTIME_OBJS)


def build_c(c_files: list[str], exe: str, include: str, with_main: bool = True) -> subprocess.CompletedProcess:
    objs = runtime_objects()
    if not with_main:
        objs = [o for o in objs if not o.endswith("rt_main.c.o")]
    cmd = ["gcc", *C_FLAGS, "-I", RUNTIME_DIR, "-I", include, *c_files, *objs, "-o", exe, *LIBS]
    return subprocess.run(cmd, capture_output=True, text=True)


class Result:
    def __init__(self, stdout: str, stderr: str, code: int, c_source: str):
        self.stdout = stdout
        self.stderr = stderr
        self.code = code
        self.c_source = c_source

    @property
    def lines(self) -> list[str]:
        return self.stdout.split("\n")[:-1] if self.stdout.endswith("\n") else self.stdout.split("\n")


def run_java(code: str, args: list[str] | None = None, main_class: str | None = None) -> Result:
    """Полный цикл: Java → IR → C89 → gcc -std=c89 -pedantic-errors → запуск."""
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
    exe = os.path.join(d, "prog.exe")
    r = build_c([c_path], exe, d)
    if r.returncode != 0 or r.stderr.strip():
        raise AssertionError(f"сгенерированный C не собирается чисто в режиме C90:\n{r.stderr[:4000]}")
    p = subprocess.run([exe, *(args or [])], capture_output=True, timeout=120)
    out = p.stdout.decode("utf-8").replace("\r\n", "\n")
    err = p.stderr.decode("utf-8", "replace").replace("\r\n", "\n")
    return Result(out, err, p.returncode, c_text)


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
