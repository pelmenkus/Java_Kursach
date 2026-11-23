"""Runtime на C99: точный GC (кадры корней, globals, точные счётчики живых объектов),
защитные проверки и переносимый адаптер командной строки. Тестовые драйверы
на C собираются вместе с runtime на всех уровнях оптимизации из OPT_LEVELS."""
import os
import subprocess
import unittest

from tests.helpers import _WORK, OPT_LEVELS, build_c

HERE = os.path.dirname(os.path.abspath(__file__))


def build(name: str, opt: str, with_main: bool) -> str:
    src = os.path.join(HERE, "c", name + ".c")
    exe = os.path.join(_WORK, f"{name}_O{opt}.exe")
    r = build_c([src], exe, os.path.join(HERE, "c"), opt, with_main=with_main)
    if r.returncode != 0 or r.stderr.strip():
        raise AssertionError(f"-O{opt}: {r.stderr}")
    return exe


def run(exe: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([exe, *args], capture_output=True, text=True, timeout=300)


class GcTests(unittest.TestCase):
    def test_precise_gc_exact_counts(self):
        for opt in OPT_LEVELS:
            with self.subTest(opt=opt):
                p = run(build("test_gc", opt, True))
                lines = p.stdout.splitlines()
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertGreaterEqual(len(lines), 20)
                for line in lines:
                    self.assertTrue(line.startswith("ok "), line)


class DefensiveChecksTests(unittest.TestCase):
    def test_invalid_array_store_and_frame_checks(self):
        for opt in OPT_LEVELS:
            exe = build("test_store", opt, False)
            for case, kind in (("store", "ArrayStore"), ("corrupt", "CorruptedRootFrame"),
                               ("unbal", "CorruptedRootFrame")):
                with self.subTest(opt=opt, case=case):
                    p = run(exe, case)
                    self.assertEqual(p.returncode, 1, p.stdout)
                    self.assertIn(f"Runtime error [{kind}]", p.stderr)
                    self.assertNotIn("not detected", p.stdout)


class ArgsTests(unittest.TestCase):
    def test_portable_utf8_arguments(self):
        for opt in OPT_LEVELS:
            exe = build("test_args", opt, False)
            p = run(exe, "valid")
            self.assertEqual((p.returncode, p.stdout), (0, "4 3 3 1 0 128512\n"))
            for k in range(7):
                with self.subTest(opt=opt, case=k):
                    p = run(exe, str(k))
                    self.assertEqual(p.returncode, 1, p.stdout)
                    self.assertIn("Runtime error [InvalidArgument]", p.stderr)
                    self.assertIn("argument 1", p.stderr)


if __name__ == "__main__":
    unittest.main()
