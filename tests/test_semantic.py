"""Семантические ошибки и диагностика."""
import os
import subprocess
import sys
import tempfile
import unittest

from compiler.errors import format_error
from compiler.source import encode_utf8
from tests.helpers import CompilerTestCase, errors_of, frontend, main_wrap, source

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DeclarationTests(CompilerTestCase):
    def test_duplicates(self):
        self.assertCompileError("class A { } class A { }" + main_wrap(""), "уже занято")
        self.assertCompileError("class A { int x; int x; }" + main_wrap(""), "уже объявлено")
        self.assertCompileError("class A { void m() { } void m() { } }" + main_wrap(""), "уже объявлен")
        self.assertCompileError("class A { void m(int a, int a) { } }" + main_wrap(""), "повторно")
        self.assertCompileError("class String { }" + main_wrap(""), "зарезервировано")
        self.assertCompileError("class Throwable { }" + main_wrap(""), "встроенным типом")
        self.assertCompileError("class RuntimeException { }" + main_wrap(""), "встроенным типом")

    def test_unknown_names(self):
        self.assertCompileError("class A extends Zz { }" + main_wrap(""), "неизвестный класс")
        self.assertCompileError(main_wrap("Zz z = null;"), "неизвестный тип")
        self.assertCompileError(main_wrap("int x = y;"), "неизвестное имя")
        self.assertCompileError(main_wrap("int x = Math.foo(1.0);"), "неизвестный встроенный")

    def test_inheritance_graph(self):
        self.assertCompileError("class A extends B { } class B extends A { }" + main_wrap(""),
                                "цикл в цепочке наследования")
        self.assertCompileError("class A extends A { }" + main_wrap(""), "самого себя")
        self.assertCompileError("interface I extends J { } interface J extends I { }" + main_wrap(""), "цикл")
        self.assertCompileError("final class F { } class G extends F { }" + main_wrap(""), "final-класса")
        self.assertCompileError("interface I { } class G extends I { }" + main_wrap(""), "интерфейс")
        self.assertCompileError("class A { } class G implements A { }" + main_wrap(""), "не является интерфейсом")
        self.assertCompileError("interface I { } class G implements I, I { }" + main_wrap(""), "повторно")
        self.assertCompileError("class G extends String { }" + main_wrap(""), "встроенного")
        self.assertCompiles("class G extends Object { }" + main_wrap(""))

    def test_single_parent_and_object_root(self):
        prog = frontend("class A { } class B extends A { } interface I { } class C extends B implements I { }"
                        + main_wrap(""))
        c = {k.name: k for k in prog.classes}
        self.assertEqual([k.name for k in c["C"].chain()], ["C", "B", "A", "Object"])
        self.assertEqual(c["Main"].parent.name, "Object")
        self.assertEqual([k.name for k in c["RuntimeException"].chain()],
                         ["RuntimeException", "Exception", "Throwable", "Object"])

    def test_static_methods(self):
        self.assertCompileError("class A { static void f() { } }" + main_wrap(""), "статические методы")
        self.assertCompileError(main_wrap("") + "class B { public static void main(String[] a) { } }",
                                "несколько точек входа")
        self.assertCompileError("class A { }", "точка входа")
        self.assertCompileError("class A { public static int main(String[] a) { return 0; } }", "статические")
        self.assertCompileError("class A { int f; }" + main_wrap("A.f();"), "статические методы")

    def test_this_in_static_context(self):
        self.assertCompileError("class Main { int f; public static void main(String[] a) { int x = f; } }",
                                "статическом")
        self.assertCompileError(main_wrap("Object o = (Object) this;"), "статическом")
        self.assertCompileError("class Main { void g() { } public static void main(String[] a) { g(); } }",
                                "статическом")
        self.assertCompileError("class A { int f = 1; static int s = f; }" + main_wrap(""), "статическом")

    def test_abstract_rules(self):
        self.assertCompileError("abstract class A { abstract void m(); } class Main { "
                                "public static void main(String[] a) { Object o = (Object) new A(); } }",
                                "абстрактного класса")
        self.assertCompileError("class A { abstract void m(); }" + main_wrap(""), "неабстрактном")
        self.assertCompileError("abstract class A { abstract void m() { } }" + main_wrap(""), "не может иметь тело")
        self.assertCompileError("class A { void m(); }" + main_wrap(""), "должно быть тело")
        self.assertCompileError("abstract class A { abstract void m(); } class B extends A { }"
                                + main_wrap(""), "не реализует абстрактные методы")
        self.assertCompileError("interface I { void m(); } class B implements I { }" + main_wrap(""),
                                "не реализует")
        self.assertCompileError("interface I { void m(); } class B implements I { void m() { } }" + main_wrap(""),
                                "должен быть public")
        # интерфейс через родителя и через extends интерфейсов
        self.assertCompileError("interface I { void m(); } interface J extends I { } abstract class A implements J { }"
                                " class B extends A { }" + main_wrap(""), "не реализует")
        self.assertCompiles("interface I { void m(); } abstract class A implements I { } "
                            "class B extends A { public void m() { } }" + main_wrap(""))

    def test_final_rules(self):
        self.assertCompileError("class A { final void m() { } } class B extends A { void m() { } }"
                                + main_wrap(""), "final-метод")
        self.assertCompileError("class A { final void m() { } } class B extends A { } class C extends B { "
                                "void m() { } }" + main_wrap(""), "final-метод")
        self.assertCompileError("class A { final int x = 1; void m() { x = 2; } }" + main_wrap(""), "final-поле")
        self.assertCompiles("class A { final int x; A() { x = 2; } }" + main_wrap(""))
        self.assertCompileError("class A { final int x; } class B extends A { B() { x = 1; } }" + main_wrap(""),
                                "final-поле")
        self.assertCompileError("class A { public String getClass() { return null; } }" + main_wrap(""), "")

    def test_override_rules(self):
        self.assertCompileError("class A { int m() { return 1; } } class B extends A { "
                                "long m() { return 1L; } }" + main_wrap(""), "другим типом результата")
        self.assertCompileError("class A { public void m() { } } class B extends A { "
                                "void m() { } }" + main_wrap(""), "сужает доступ")
        self.assertCompileError("class A { protected void m() { } } class B extends A { "
                                "private void m() { } }" + main_wrap(""), "сужает доступ")
        self.assertCompileError("class A { public String toString() { return null; } } class B extends A { "
                                "String toString() { return null; } }" + main_wrap(""), "сужает доступ")
        self.assertCompileError("interface I { int m(); } interface J { long m(); } "
                                "interface K extends I, J { }" + main_wrap(""), "разными типами результата")

    def test_constructor_rules(self):
        self.assertCompiles("class A { } class B extends A { }" + main_wrap("B b = new B();"))
        self.assertCompileError("class A { A(int x) { } } class B extends A { }" + main_wrap(""),
                                "нет конструктора без аргументов")
        self.assertCompileError("class A { A(int x) { } } class B extends A { B() { } }" + main_wrap(""),
                                "нет конструктора без аргументов")
        self.assertCompiles("class A { A(int x) { } } class B extends A { B() { super(1); } }" + main_wrap(""))
        self.assertCompileError("class A { } class B extends A { B() { int x = 1; super(); } }" + main_wrap(""),
                                "первым оператором")
        self.assertCompileError("class A { A(int x) { } } class B extends A { B() { super(1L); } }" + main_wrap(""),
                                "конструктора")
        self.assertCompileError("class A { private A() { } } class B extends A { }" + main_wrap(""), "private")
        self.assertCompileError("class B { B() { super(1); } }" + main_wrap(""), "Object не принимает")
        self.assertCompileError("class A { A(int x) { } }" + main_wrap("A a = new A();"), "конструктора")
        self.assertCompileError("class A { private A() { } }" + main_wrap("A a = new A();"), "private")
        self.assertCompileError("class A { void m() { super(); } }" + main_wrap(""), "только первым")

    def test_this_before_super(self):
        base = "class A { A(int x) { } } "
        self.assertCompileError(base + "class B extends A { int f; B() { super(f); } }" + main_wrap(""),
                                "до вызова конструктора родителя")
        self.assertCompileError(base + "class B extends A { int g() { return 1; } B() { super(g()); } }"
                                + main_wrap(""), "до вызова конструктора родителя")
        self.assertCompileError(base + "class B extends A { B() { super(this.hashOf()); } int hashOf() "
                                "{ return 0; } }" + main_wrap(""), "до вызова конструктора родителя")
        self.assertCompiles(base + "class B extends A { B(int p) { super(p + 1); } }" + main_wrap(""))

    def test_access(self):
        self.assertCompileError("class A { private int x; } " + main_wrap("int y = new A().x;"), "private")
        self.assertCompileError("class A { private void m() { } } " + main_wrap("new A().m();"), "private")
        self.assertCompileError("class A { private int x; } class B extends A { "
                                "int g() { return x; } }" + main_wrap(""), "неизвестное имя")
        self.assertCompiles("class A { protected int x; int y; } class B extends A { "
                            "int g() { return x + y; } }" + main_wrap("int z = new A().x;"))

    def test_super_members(self):
        base = "class A { int f; private int p; int m() { return 1; } static int s; } "
        self.assertCompiles(base + "class B extends A { int f; int g() { return super.f + f + super.m(); } "
                                   "void h() { super.f = 3; } }" + main_wrap(""))
        self.assertCompileError(base + "class B extends A { int g() { return super.p; } }" + main_wrap(""),
                                "нет доступного поля")
        self.assertCompileError(base + "class B extends A { int g() { return super.s; } }" + main_wrap(""),
                                "static-поле")
        self.assertCompileError("class B { int g() { return super.x; } }" + main_wrap(""), "нет доступного поля")
        self.assertCompileError("abstract class A { abstract int m(); } class C extends A { "
                                "int m() { return super.m(); } }" + main_wrap(""), "абстрактный метод")
        self.assertCompileError("class A { } class B extends A { void m() { super.A(); } }" + main_wrap(""),
                                "нет метода")
        self.assertCompileError(main_wrap("int x = super.hashCode();"), "статическом")

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

    def test_constant_condition_through_constant_variable(self):
        self.assertCompileError("class K { static final boolean DEBUG = false; }"
                                + main_wrap("while (K.DEBUG) { }"), "всегда ложно")
        self.assertCompiles("class K { static final boolean ON = 1 < 2; int m() { while (ON) { } } }"
                            + main_wrap(""))

    def test_definite_assignment(self):
        self.assertCompileError(main_wrap("int x; int y = x;"), "не инициализирована")
        self.assertCompileError(main_wrap("int x; if (args.length > 0) x = 1; int y = x;"), "не инициализирована")
        self.assertCompiles(main_wrap("int x; if (args.length > 0) x = 1; else x = 2; int y = x;"))
        self.assertCompiles(main_wrap("int x; while (true) { x = 1; break; } int y = x;"))
        self.assertCompiles(main_wrap("int x; boolean b = args.length > 0 && (x = 1) > 0; if (b) { }"))
        self.assertCompileError(main_wrap("int x; x += 1;"), "не инициализирована")

    def test_void_and_misc(self):
        self.assertCompileError("class A { void m() { } }" + main_wrap("int x = new A().m();"), "void")
        self.assertCompileError(main_wrap("int[] a = new int[1]; a.length = 2;"), "length")
        self.assertCompileError(main_wrap("String s = \"a\"; int n = s.size();"), "нет")
        self.assertCompileError("interface I { } " + main_wrap("I i = new I();"), "интерфейса")
        self.assertCompileError(main_wrap("int x = 1; x.y = 2;"), "нет поля")
        self.assertCompileError("class A { }" + main_wrap("Object o = (Object) A;"), "имя типа")

    def test_multiple_errors_collected(self):
        errs = errors_of(main_wrap("long a = 1; byte b = 2; zz();"))
        self.assertGreaterEqual(len(errs), 3)


class StaticFieldTests(CompilerTestCase):
    """Решения N4/N5: static-поля, константы, ленивая инициализация классов."""

    def test_static_access_forms(self):
        base = "class A { static int n; static Object o; int i; } class B extends A { int g() { return n; } } "
        self.assertCompiles(base + main_wrap("A.n = 1; int x = A.n + B.n; A.o = null;"))
        self.assertCompileError(base + main_wrap("A a = new A(); int x = a.n;"), "через экземпляр запрещён")
        self.assertCompileError(base + "class C extends A { int g() { return this.n; } }" + main_wrap(""),
                                "через экземпляр запрещён")
        self.assertCompileError(base + main_wrap("int x = A.i;"), "через имя класса")
        self.assertCompileError(base + main_wrap("int x = n;"), "неизвестное имя")
        self.assertCompileError("interface I { }" + main_wrap("int x = I.n;"), "нет static-полей")
        self.assertCompileError("class A { private static int p; }" + main_wrap("int x = A.p;"), "private")

    def test_static_final_rules(self):
        self.assertCompileError("class A { static final int X; }" + main_wrap(""), "должно иметь инициализатор")
        self.assertCompileError("class A { static final int X = 1; }" + main_wrap("A.X = 2;"), "нельзя изменять")
        self.assertCompileError("class A { static final Object O = null; void m() { O = null; } }" + main_wrap(""),
                                "нельзя изменять")

    def test_forward_references(self):
        self.assertCompileError("class A { static int a = b; static int b = 1; }" + main_wrap(""),
                                "опережающая ссылка")
        self.assertCompileError("class A { int a = b + 1; int b = 1; }" + main_wrap(""), "опережающая ссылка")
        self.assertCompileError("class A { static int a = a + 1; }" + main_wrap(""), "опережающая ссылка")
        self.assertCompiles("class A { static int a = A.b; static int b = 1; int c = b; }" + main_wrap(""))
        self.assertCompiles("class A { int i = s; static int s = 5; }" + main_wrap(""))

    def test_constants_and_init_classes(self):
        prog = frontend("class K { static final int A = 6 * 7; static final String S = \"s\" + \"t\"; } "
                        "class L { static int n = 5; static final int M = 1; } "
                        "class P { static Object o = new Object(); } class Q extends P { } "
                        + main_wrap(""))
        names = sorted(c.name for c in prog.init_classes)
        # K, L — только константные значения; P — new Object(); Q наследует необходимость
        self.assertEqual(names, ["P", "Q"])
        self.assertEqual(prog.globals_count, 2)       # K.S, P.o

    def test_cyclic_constants_are_not_constant(self):
        self.assertCompiles("class A { static final int X = B.Y + 1; } class B { static final int Y = A.X + 1; }"
                            + main_wrap(""))


class ThrowTests(CompilerTestCase):
    def test_throw_types(self):
        self.assertCompiles(main_wrap("throw new RuntimeException(\"x\");"))
        self.assertCompiles(main_wrap("throw null;"))
        self.assertCompiles("class E extends Exception { }" + main_wrap("throw new E();"))
        self.assertCompileError(main_wrap("throw new Object();"), "Throwable", "type")
        self.assertCompileError(main_wrap("throw \"s\";"), "Throwable")
        self.assertCompileError("interface I { }" + main_wrap("I i = null; throw i;"), "Throwable")

    def test_throws_clause(self):
        self.assertCompiles("class A { void m() throws Exception, RuntimeException { } A() throws Throwable { } }"
                            + main_wrap(""))
        self.assertCompileError("class A { void m() throws String { } }" + main_wrap(""), "подклассы Throwable")
        self.assertCompileError("interface I { void m() throws Zz; }" + main_wrap(""), "неизвестный тип")

    def test_throw_terminates_path(self):
        self.assertCompiles("class A { int m(boolean b) { if (b) return 1; throw new RuntimeException(); } }"
                            + main_wrap(""))
        self.assertCompileError(main_wrap("throw null; int x = 1;"), "недостижимый код")

    def test_prelude_api(self):
        self.assertCompiles(main_wrap("Throwable t = new Throwable(); String m = t.getMessage(); "
                                      "String s = t.toString(); Exception e = new Exception(\"x\"); "
                                      "RuntimeException r = new RuntimeException();"))
        self.assertCompileError(main_wrap("Throwable t = new Throwable(); String m = t.message;"), "private")

    def test_try_catch_rejected(self):
        self.assertCompileError(main_wrap("try { } finally { }"), "try")


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
            f.write(encode_utf8(main_wrap('System.out.println("ok ✓");'), bom=True))
        with open(bad, "wb") as f:
            f.write(encode_utf8(main_wrap("int x = 1L;")))
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
