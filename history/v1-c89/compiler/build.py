"""Сборка сгенерированного C вместе с runtime (отдельный этап после генерации).

    python -m compiler.build output.c -o program.exe [--cc gcc] [--runtime DIR]

Флаги GCC фиксированы: строгий C90 (-std=c89 -pedantic-errors) для всех
файлов и -O0 — требование консервативного GC (README §8).
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

RUNTIME_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runtime")
C_FLAGS = ["-std=c89", "-pedantic-errors", "-Wall", "-Wextra", "-O0"]
LIBS = ["-lshell32", "-lm"]


def gcc_command(c_file: str, exe: str, cc: str = "gcc", runtime: str = RUNTIME_DIR) -> list[str]:
    sources = sorted(glob.glob(os.path.join(runtime, "*.c")))
    include = os.path.dirname(os.path.abspath(c_file))
    return [cc, *C_FLAGS, "-I", runtime, "-I", include, c_file, *sources, "-o", exe, *LIBS]


def build(c_file: str, exe: str, cc: str = "gcc", runtime: str = RUNTIME_DIR) -> subprocess.CompletedProcess:
    return subprocess.run(gcc_command(c_file, exe, cc, runtime), capture_output=True, text=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m compiler.build", description="Сборка C89 + runtime")
    ap.add_argument("c_file")
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--cc", default="gcc")
    ap.add_argument("--runtime", default=RUNTIME_DIR)
    args = ap.parse_args(argv)
    exe = args.output or os.path.splitext(args.c_file)[0] + ".exe"
    try:
        result = build(args.c_file, exe, args.cc, args.runtime)
    except OSError as e:
        print(f"не удалось запустить {args.cc}: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
