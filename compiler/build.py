"""Сборка сгенерированного C вместе с runtime (отдельный этап после генерации).

    python -m compiler.build output.c -o program.exe [--cc gcc] [--opt 0|2] [--win-args]

Генерируемый код и runtime — ISO C99 без расширений, поэтому подходит любой
компилятор C99; по умолчанию используется GCC/Clang-совместимая командная
строка (-std=c99 -pedantic-errors -Wall -Wextra). Корректность GC не зависит
от уровня оптимизации: корни точные (кадры rets и globals), стек не
сканируется. --win-args подключает необязательный адаптер командной строки
Windows (runtime/adapters/win) вместо переносимого rt_args.c.
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

RUNTIME_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runtime")
C_FLAGS = ["-std=c99", "-pedantic-errors", "-Wall", "-Wextra"]
LIBS = ["-lm"]
WIN_ADAPTER = os.path.join("adapters", "win", "rt_args_win.c")


def runtime_sources(runtime: str = RUNTIME_DIR, win_args: bool = False) -> list[str]:
    sources = sorted(glob.glob(os.path.join(runtime, "*.c")))
    if win_args:
        sources = [s for s in sources if os.path.basename(s) != "rt_args.c"]
        sources.append(os.path.join(runtime, WIN_ADAPTER))
    return sources


def gcc_command(c_file: str, exe: str, cc: str = "gcc", runtime: str = RUNTIME_DIR, opt: str = "0",
                win_args: bool = False) -> list[str]:
    include = os.path.dirname(os.path.abspath(c_file))
    libs = LIBS + (["-lshell32"] if win_args else [])
    return [cc, *C_FLAGS, f"-O{opt}", "-I", runtime, "-I", include, c_file,
            *runtime_sources(runtime, win_args), "-o", exe, *libs]


def build(c_file: str, exe: str, cc: str = "gcc", runtime: str = RUNTIME_DIR, opt: str = "0",
          win_args: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(gcc_command(c_file, exe, cc, runtime, opt, win_args), capture_output=True, text=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m compiler.build", description="Сборка C99 + runtime")
    ap.add_argument("c_file")
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--cc", default="gcc", help="компилятор C99 с GCC-совместимыми опциями")
    ap.add_argument("--opt", default="0", choices=["0", "1", "2", "3", "s"], help="уровень оптимизации -O")
    ap.add_argument("--runtime", default=RUNTIME_DIR)
    ap.add_argument("--win-args", action="store_true",
                    help="необязательный адаптер аргументов Windows (UTF-16 -> UTF-8, кодовая страница UTF-8)")
    args = ap.parse_args(argv)
    exe = args.output or os.path.splitext(args.c_file)[0] + ".exe"
    try:
        result = build(args.c_file, exe, args.cc, args.runtime, args.opt, args.win_args)
    except OSError as e:
        print(f"не удалось запустить {args.cc}: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
