"""Runtime на C89: GC (mark-and-sweep, консервативное сканирование стека
Windows x86-64, внутренние указатели, циклы, массивы, строки) и защитные
проверки. Тестовые драйверы на C собираются вместе с runtime в режиме C90."""
import os
import subprocess
import unittest

from tests.helpers import _WORK, build_c

HERE = os.path.dirname(os.path.abspath(__file__))


def build_and_run(name: str) -> subprocess.CompletedProcess:
    src = os.path.join(HERE, "c", name + ".c")
    exe = os.path.join(_WORK, name + ".exe")
    r = build_c([src], exe, os.path.join(HERE, "c"))
    if r.returncode != 0 or r.stderr.strip():
        raise AssertionError(r.stderr)
    return subprocess.run([exe], capture_output=True, text=True)


class GcTests(unittest.TestCase):
    def test_gc(self):
        p = build_and_run("test_gc")
        lines = p.stdout.splitlines()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertGreaterEqual(len(lines), 9)
        for line in lines:
            self.assertTrue(line.startswith("ok "), line)


class DefensiveChecksTests(unittest.TestCase):
    def test_invalid_array_store(self):
        p = build_and_run("test_store")
        self.assertEqual(p.returncode, 1)
        self.assertIn("Runtime error [ArrayStore]", p.stderr)


if __name__ == "__main__":
    unittest.main()
