"""Типовая система: точное совпадение, запрет неявных преобразований, приведения."""
import unittest

from compiler import type_system as T
from tests.helpers import CompilerTestCase, main_wrap

HIER = """
interface I { int m(); }
interface J extends I { }
class X { }
class A extends X { A() { super.X(); } }
class B extends X { B() { super.X(); } }
class C extends A, B implements J { C() { super.A(); super.B(); } public int m() { return 1; } }
final class F { }
class U { }
"""


class TypeObjectTests(unittest.TestCase):
    def test_structural_equality(self):
        self.assertEqual(T.ArrayType(T.INT), T.ArrayType(T.INT))
        self.assertNotEqual(T.ArrayType(T.INT), T.ArrayType(T.LONG))
        self.assertEqual(T.ClassType(T.STRING_SYM), T.STRING)
        self.assertFalse(T.same_type(T.INT, T.LONG))

    def test_no_nested_arrays(self):
        with self.assertRaises(AssertionError):
            T.ArrayType(T.ArrayType(T.INT))

    def test_assignable_only_exact_or_null(self):
        self.assertTrue(T.assignable(T.INT, T.INT))
        self.assertFalse(T.assignable(T.LONG, T.INT))
        self.assertFalse(T.assignable(T.DOUBLE, T.FLOAT))
        self.assertTrue(T.assignable(T.STRING, T.NULL))
        self.assertTrue(T.assignable(T.ArrayType(T.INT), T.NULL))
        self.assertFalse(T.assignable(T.INT, T.NULL))
        self.assertFalse(T.assignable(T.OBJECT, T.STRING))

    def test_binary_rules(self):
        self.assertIs(T.binary_result("+", T.BYTE, T.BYTE), T.BYTE)    # нет продвижения (I1)
        self.assertIs(T.binary_result("<", T.CHAR, T.CHAR), T.BOOLEAN)
        self.assertIs(T.binary_result("&", T.BOOLEAN, T.BOOLEAN), T.BOOLEAN)
        self.assertEqual(T.binary_result("+", T.STRING, T.NULL), T.STRING)
        for op, l, r in (("+", T.INT, T.LONG), ("<<", T.LONG, T.INT), ("+", T.STRING, T.INT),
                         ("==", T.INT, T.CHAR), ("&", T.FLOAT, T.FLOAT), ("+", T.BOOLEAN, T.BOOLEAN)):
            with self.assertRaises(T.OpError, msg=f"{l} {op} {r}"):
                T.binary_result(op, l, r)


class TypeCheckTests(CompilerTestCase):
    def test_implicit_numeric_conversions_rejected(self):
        for stmt in ("long a = 5;", "byte b = 5;", "double d = 1;", "float f = 1.0;", "int i = 'a';",
                     "char c = 65;", "short s = (byte) 1;", "int x = 1L;", "boolean z = 0;"):
            self.assertCompileError(main_wrap(stmt), "несовместимые типы", "type")

    def test_explicit_casts_accepted(self):
        self.assertCompiles(main_wrap("long a = (long) 5; byte b = (byte) 5; double d = (double) 1; "
                                      "float f = (float) 1.0; char c = (char) 65; int i = (int) 'a';"))

    def test_operands_must_match(self):
        for e in ("1 + 2L", "1.0 + 1.0f", "(byte) 1 + 1", "'a' + 1", "1 < 2.0", "1 << 2L", "true & 1"):
            self.assertCompileError(main_wrap(f"Object o = (Object) String.valueOf({e});"), "", "type")

    def test_string_concat_needs_explicit_conversion(self):
        self.assertCompileError(main_wrap('String s = "a" + 1;'), "конкатенация")
        self.assertCompiles(main_wrap('String s = "a" + String.valueOf(1) + null;'))

    def test_conditions_must_be_boolean(self):
        self.assertCompileError(main_wrap("if (1) { }"), "boolean")
        self.assertCompileError(main_wrap("while (0) { }"), "boolean")
        self.assertCompileError(main_wrap("boolean b = 1 && true;"), "boolean")
        self.assertCompileError(main_wrap("boolean b = !1;"), "boolean")

    def test_ternary_requires_same_type(self):
        self.assertCompileError(main_wrap("int x = true ? 1 : 2L;"), "ветви ?:")
        self.assertCompiles(main_wrap("String s = true ? null : \"a\";"))

    def test_reference_assignment_requires_exact_type(self):
        code = HIER + main_wrap("C c = new C(); A a = c;")
        self.assertCompileError(code, "несовместимые типы")
        self.assertCompiles(HIER + main_wrap("C c = new C(); A a = (A) c; Object o = (Object) c; I i = (I) c;"))
        self.assertCompileError(HIER + main_wrap("C c = new C(); I i = c;"), "несовместимые типы")
        self.assertCompileError(HIER + main_wrap('Object o = "s";'), "несовместимые типы")

    def test_method_arguments_exact(self):
        code = "class K { void m(long x) { } }" + main_wrap("new K().m(1);")
        self.assertCompileError(code, "точно совпадающими типами")
        self.assertCompiles("class K { void m(long x) { } }" + main_wrap("new K().m(1L);"))

    def test_return_exact(self):
        self.assertCompileError("class K { long m() { return 1; } }" + main_wrap(""), "несовместимые типы")

    def test_overload_by_exact_type(self):
        code = ("class K { int m(int x) { return 1; } int m(long x) { return 2; } int m(String s) { return 3; } "
                "int m(Object o) { return 4; } }")
        self.assertCompiles(code + main_wrap("K k = new K(); int a = k.m(1) + k.m(1L) + k.m(\"s\") + k.m((Object) k);"))
        self.assertCompileError(code + main_wrap("int a = new K().m(null);"), "неоднозначный вызов")
        self.assertCompileError(code + main_wrap("int a = new K().m((short) 1);"), "точно совпадающими")

    def test_null_rules(self):
        self.assertCompiles(HIER + main_wrap("C c = null; I i = null; int[] a = null; boolean z = c == null;"))
        self.assertCompileError(main_wrap("int x = null;"), "несовместимые типы")

    def test_reference_equality_requires_same_type(self):
        self.assertCompileError(HIER + main_wrap("C c = new C(); A a = (A) c; boolean z = a == c;"), "разных типов")

    def test_casts_classification(self):
        self.assertCompiles(HIER + main_wrap(
            "C c = new C(); Object o = (Object) c; A a = (A) c; C c2 = (C) a; C c3 = (C) o; "
            "J j = (J) c; I i = (I) j; C c4 = (C) i; X x = (X) a; U u = (U) o;"))

    def test_cast_to_repeated_ancestor_is_error(self):
        errs = self.assertCompileError(HIER + main_wrap("C c = new C(); X x = (X) c;"), "неоднозначное приведение")
        self.assertIn("C.A.X", " ".join(errs[0].notes))
        self.assertCompileError(HIER + main_wrap("C c = new C(); boolean z = c instanceof X;"), "неоднозначное")
        self.assertCompiles(HIER + main_wrap("C c = new C(); Object o = (Object) c; boolean z = o instanceof X;"))

    def test_unrelated_class_cast_is_error(self):
        self.assertCompileError(HIER + main_wrap("A a = null; B b = (B) a;"), "неродственными")
        self.assertCompileError(HIER + main_wrap("U u = null; F f = (F) (I) null;"), "")
        self.assertCompileError(HIER + main_wrap("F f = new F(); I i = (I) f;"), "final-класс")

    def test_array_types_not_compatible(self):
        self.assertCompileError(HIER + main_wrap("C[] cs = new C[1]; A[] as = (A[]) cs;"), "массивы")
        self.assertCompileError(main_wrap("int[] a = new int[1]; long[] b = (long[]) a;"), "массивы")
        self.assertCompiles(main_wrap("int[] a = new int[1]; Object o = (Object) a; int[] b = (int[]) o;"))

    def test_prim_ref_casts_rejected(self):
        self.assertCompileError(main_wrap("Object o = (Object) 1;"), "недопустимое приведение")
        self.assertCompileError(main_wrap("boolean b = (boolean) 1;"), "недопустимое приведение")

    def test_array_index_and_size_int(self):
        self.assertCompileError(main_wrap("int[] a = new int[2L];"), "размер массива")
        self.assertCompileError(main_wrap("int[] a = new int[2]; int x = a[(short) 0];"), "индекс")


if __name__ == "__main__":
    unittest.main()
