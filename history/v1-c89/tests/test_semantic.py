"""Семантические ошибки и диагностика."""
import os
import subprocess
import sys
import tempfile
import unittest

from compiler.errors import format_error
from compiler.source import encode_utf16
from tests.helpers import CompilerTestCase, errors_of, main_wrap, source

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DeclarationTests(CompilerTestCase):
    def test_duplicates(self):
        self.assertCompileError("class A { } class A { }" + main_wrap(""), "уже занято")
        self.assertCompileError("class A { int x; int x; }" + main_wrap(""), "уже объявлено")
        self.assertCompileError("class A { void m() { } void m() { } }" + main_wrap(""), "уже объявлен")
        self.assertCompileError("class A { void m(int a, int a) { } }" + main_wrap(""), "повторно")
        self.assertCompileError("class String { }" + main_wrap(""), "зарезервировано")

    def test_unknown_names(self):
        self.assertCompileError("class A extends Zz { }" + main_wrap(""), "неизвестный класс")
        self.assertCompileError(main_wrap("Zz z = null;"), "неизвестный тип")
        self.assertCompileError(main_wrap("int x = y;"), "неизвестное имя")
        self.assertCompileError(main_wrap("int x = Math.foo(1.0);"), "неизвестный встроенный")

    def test_inheritance_graph(self):
        self.assertCompileError("class A extends B { A() { super.B(); } } class B extends A { B() { super.A(); } }"
                                + main_wrap(""), "цикл в графе наследования")
        self.assertCompileError("interface I extends J { } interface J extends I { }" + main_wrap(""), "цикл")
        self.assertCompileError("final class F { } class G extends F { G() { super.F(); } }" + main_wrap(""),
                                "final-класса")
        self.assertCompileError("interface I { } class G extends I { }" + main_wrap(""), "интерфейс")
        self.assertCompileError("class A { } class G implements A { }" + main_wrap(""), "не является интерфейсом")
        self.assertCompileError("class A { } class G extends A, A { G() { super.A(); } }" + main_wrap(""),
                                "повторно")
        self.assertCompileError("class G extends String { }" + main_wrap(""), "встроенного")

    def test_static_members(self):
        self.assertCompileError("class A { static int x; }" + main_wrap(""), "статические поля")
        self.assertCompileError("class A { static void f() { } }" + main_wrap(""), "статические методы")
        self.assertCompileError(main_wrap("") + "class B { public static void main(String[] a) { } }",
                                "несколько точек входа")
        self.assertCompileError("class A { }", "точка входа")
        self.assertCompileError("class A { public static int main(String[] a) { return 0; } }", "статические")

    def test_this_in_static_main(self):
        self.assertCompileError("class Main { int f; public static void main(String[] a) { int x = f; } }",
                                "статическом")
        self.assertCompileError(main_wrap("Object o = (Object) this;"), "статическом")

    def test_abstract_rules(self):
        self.assertCompileError("abstract class A { abstract void m(); } class Main { "
                                "public static void main(String[] a) { Object o = (Object) new A(); } }",
                                "абстрактного класса")
        self.assertCompileError("class A { abstract void m(); }" + main_wrap(""), "неабстрактном")
        self.assertCompileError("abstract class A { abstract void m() { } }" + main_wrap(""), "не может иметь тело")
        self.assertCompileError("class A { void m(); }" + main_wrap(""), "должно быть тело")
        self.assertCompileError("abstract class A { abstract void m(); } class B extends A { B() { super.A(); } }"
                                + main_wrap(""), "не реализует абстрактные методы")
        self.assertCompileError("interface I { void m(); } class B implements I { }" + main_wrap(""),
                                "не реализует")
        self.assertCompileError("interface I { void m(); } class B implements I { void m() { } }" + main_wrap(""),
                                "должен быть public")

    def test_final_rules(self):
        self.assertCompileError("class A { final void m() { } } class B extends A { B() { super.A(); } void m() { } }"
                                + main_wrap(""), "final-метод")
        # final в любой ветви, в том числе во второй
        self.assertCompileError("class A { } class P { final void m() { } } "
                                "class B extends A, P { B() { super.A(); super.P(); } void m() { } }" + main_wrap(""),
                                "final-метод")
        self.assertCompileError("class A { final int x = 1; void m() { x = 2; } }" + main_wrap(""), "final-поле")
        self.assertCompiles("class A { final int x; A() { x = 2; } }" + main_wrap(""))
        self.assertCompileError("class A { public String getClass() { return null; } }" + main_wrap(""), "")

    def test_override_rules(self):
        self.assertCompileError("class A { int m() { return 1; } } class B extends A { B() { super.A(); } "
                                "long m() { return 1L; } }" + main_wrap(""), "другим типом результата")
        self.assertCompileError("class A { public void m() { } } class B extends A { B() { super.A(); } "
                                "void m() { } }" + main_wrap(""), "сужает доступ")

    def test_constructor_rules(self):
        self.assertCompileError("class A { } class B extends A { }" + main_wrap(""), "должен явно объявить")
        self.assertCompileError("class A { } class P { } class B extends A, P { B() { super.A(); } }"
                                + main_wrap(""), "super.P")
        self.assertCompileError("class A { } class P { } class B extends A, P { B() { super.P(); super.A(); } }"
                                + main_wrap(""), "порядок")
        self.assertCompileError("class A { } class B extends A { B() { int x = 1; super.A(); } }" + main_wrap(""),
                                "super.A")
        self.assertCompileError("class A { A(int x) { } } class B extends A { B() { super.A(); } }" + main_wrap(""),
                                "конструктора")
        self.assertCompileError("class A { } class B { B() { super.A(); } }" + main_wrap(""), "не является")
        self.assertCompileError("class A { A(int x) { } }" + main_wrap("A a = new A();"), "конструктора")
        self.assertCompileError("class A { private A() { } }" + main_wrap("A a = new A();"), "private")

    def test_access(self):
        self.assertCompileError("class A { private int x; } " + main_wrap("int y = new A().x;"), "private")
        self.assertCompileError("class A { private void m() { } } " + main_wrap("new A().m();"), "private")
        self.assertCompileError("class A { private int x; } class B extends A { B() { super.A(); } "
                                "int g() { return x; } }" + main_wrap(""), "неизвестное имя")
        self.assertCompiles("class A { protected int x; int y; } class B extends A { B() { super.A(); } "
                            "int g() { return x + y; } }" + main_wrap("int z = new A().x;"))

    def test_scopes(self):
        self.assertCompileError(main_wrap("int x = 1; { int x = 2; }"), "уже объявлена")
        self.assertCompileError(main_wrap("for (int i = 0; i < 1; i++) { int i = 2; }"), "уже объявлена")
        self.assertCompileError(main_wrap("{ int x = 1; } int y = x;"), "неизвестное имя")
        self.assertCompiles(main_wrap("{ int x = 1; } { int x = 2; }"))
        self.assertCompileError("class A { void m(int p) { int p = 1; } }" + main_wrap(""), "уже объявлена")

    def test_control_flow(self):
        self.assertCompileError("class A { int m() { } }" + main_wrap(""), "должен возвращать")
        self.assertCompileError("class A { int m(boolean b) { if (b) return 1; } }" + main_wrap(""),
                                "должен возвращать")
        self.assertCompiles("class A { int m(boolean b) { if (b) return 1; else return 2; } "
                            "int n() { while (true) { } } }" + main_wrap(""))
        self.assertCompileError(main_wrap("return; int x = 1;"), "недостижимый код")
        self.assertCompileError(main_wrap("while (true) { } int x = 1;"), "недостижимый код")
        self.assertCompileError(main_wrap("while (false) { int x = 1; }"), "недостижимый код")
        self.assertCompileError(main_wrap("for (;;) { break; int y = 0; }"), "недостижимый код")
        self.assertCompiles(main_wrap("while (true) { break; } int x = 1; if (false) { x = 2; }"))
        self.assertCompileError(main_wrap("break;"), "вне цикла")
        self.assertCompileError(main_wrap("continue;"), "вне цикла")
        self.assertCompileError("class A { void m() { return 1; } }" + main_wrap(""), "return со значением")

    def test_definite_assignment(self):
        self.assertCompileError(main_wrap("int x; int y = x;"), "не инициализирована")
        self.assertCompileError(main_wrap("int x; if (args.length > 0) x = 1; int y = x;"), "не инициализирована")
        self.assertCompiles(main_wrap("int x; if (args.length > 0) x = 1; else x = 2; int y = x;"))
        self.assertCompiles(main_wrap("int x; while (true) { x = 1; break; } int y = x;"))
        self.assertCompiles(main_wrap("int x; boolean b = args.length > 0 && (x = 1) > 0; if (b) { }"))
        self.assertCompileError(main_wrap("int x; x += 1;"), "не инициализирована")

    def test_qualifiers(self):
        base = "class A { int f; int m() { return 1; } } class B { int f; } "
        self.assertCompiles(base + "class C extends A, B { C() { super.A(); super.B(); } "
                                   "int g() { return this.A.f + this.B.f + f + A.f + super.A.m(); } }" + main_wrap(""))
        self.assertCompileError(base + "class C extends A, B { C() { super.A(); super.B(); } } "
                                + main_wrap("C c = new C(); Object o = (Object) c.A;"), "не является выражением")
        self.assertCompileError(base + "class C extends A { C() { super.A(); } int g() { return super.B.m(); } }"
                                + main_wrap(""), "не является непосредственным родителем")
        self.assertCompileError(base + "class C extends A { int A; C() { super.A(); } }" + main_wrap(""),
                                "неоднозначный квалификатор")
        self.assertCompileError("abstract class A { abstract int m(); } class C extends A { C() { super.A(); } "
                                "int m() { return super.A.m(); } }" + main_wrap(""), "абстрактный метод")

    def test_void_and_misc(self):
        self.assertCompileError("class A { void m() { } }" + main_wrap("int x = new A().m();"), "void")
        self.assertCompileError(main_wrap("int[] a = new int[1]; a.length = 2;"), "length")
        self.assertCompileError(main_wrap("String s = \"a\"; int n = s.size();"), "нет")
        self.assertCompileError("interface I { } " + main_wrap("I i = new I();"), "интерфейса")
        self.assertCompileError(main_wrap("int x = 1; x.y = 2;"), "нет поля")

    def test_multiple_errors_collected(self):
        errs = errors_of(main_wrap("long a = 1; byte b = 2; zz();"))
        self.assertGreaterEqual(len(errs), 3)


class DiagnosticFormatTests(unittest.TestCase):
    def test_format_contains_position_snippet_and_types(self):
        code = main_wrap("    long a = 5;")
        errs = errors_of(code)
        src = source(code)
        text = format_error(errs[0], {src.name: src})
        self.assertIn("Test.java:4:14: error[type]", text)
        self.assertIn("long a = 5;", text)
        self.assertIn("^", text)
        self.assertIn("ожидается: long", text)
        self.assertIn("получено:  int", text)

    def test_cli_exit_codes(self):
        d = tempfile.mkdtemp()
        good = os.path.join(d, "Good.java")
        bad = os.path.join(d, "Bad.java")
        with open(good, "wb") as f:
            f.write(encode_utf16(main_wrap('System.out.println("ok");')))
        with open(bad, "wb") as f:
            f.write(encode_utf16(main_wrap("int x = 1L;")))
        run = lambda *a: subprocess.run([sys.executable, "-m", "compiler.main", *a], cwd=ROOT,
                                        capture_output=True, text=True, encoding="utf-8")
        r = run(good, "-o", "g.c", "--out-dir", d)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(d, "g.c")))
        self.assertTrue(os.path.exists(os.path.join(d, "g.h")))
        r = run(bad, "-o", os.path.join(d, "b.c"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("error[type]", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        r = run(os.path.join(d, "missing.java"), "-o", os.path.join(d, "m.c"))
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
