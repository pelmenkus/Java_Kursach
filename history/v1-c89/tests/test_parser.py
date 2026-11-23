import unittest

from compiler import ast_nodes as A
from compiler.errors import ParseError
from compiler.lexer import tokenize
from compiler.parser import parse
from tests.helpers import source


def unit(code: str) -> A.CompilationUnit:
    return parse(tokenize(source(code)), "Test.java")


def expr(text: str) -> A.Expr:
    u = unit("class T { void m() { x = " + text + "; } }")
    return u.types[0].members[0].body.stmts[0].expr.value


class ParserTests(unittest.TestCase):
    def test_class_header_multiple_parents(self):
        c = unit("public abstract class C extends A, B implements I, J { }").types[0]
        self.assertIsInstance(c, A.ClassDecl)
        self.assertEqual([p.name for p in c.parents], ["A", "B"])
        self.assertEqual([i.name for i in c.interfaces], ["I", "J"])
        self.assertEqual([m.name for m in c.modifiers], ["public", "abstract"])

    def test_interface(self):
        i = unit("interface I extends J, K { int m(int a); public abstract void n(); }").types[0]
        self.assertIsInstance(i, A.InterfaceDecl)
        self.assertEqual([m.name for m in i.methods], ["m", "n"])
        self.assertEqual(len(i.supers), 2)

    def test_members(self):
        c = unit("class C { private int x = 1, y; C(int a) { super.A(a); } "
                 "final int m(int[] a, String s) { return 0; } abstract void n(); }").types[0]
        kinds = [type(m).__name__ for m in c.members]
        self.assertEqual(kinds, ["FieldDecl", "ConstructorDecl", "MethodDecl", "MethodDecl"])
        self.assertIsInstance(c.members[1].body.stmts[0], A.SuperCtorCall)
        self.assertTrue(c.members[2].params[0].type.is_array)
        self.assertIsNone(c.members[3].body)

    def test_precedence(self):
        e = expr("a + b * c << 1 < d == e & f ^ g | h && i || j")
        self.assertIsInstance(e, A.Logical)
        self.assertEqual(e.op, "||")
        e = expr("a + b * c")
        self.assertEqual((e.op, e.right.op), ("+", "*"))
        e = expr("a - b - c")
        self.assertEqual(e.left.op, "-")          # левоассоциативно
        e = expr("a ? b : c ? d : e")
        self.assertIsInstance(e.els, A.Conditional)

    def test_assignment_right_assoc(self):
        u = unit("class T { void m() { a = b += c; } }")
        e = u.types[0].members[0].body.stmts[0].expr
        self.assertEqual((e.op, e.value.op), ("=", "+="))

    def test_casts(self):
        self.assertIsInstance(expr("(int) y"), A.Cast)
        self.assertIsInstance(expr("(A) y"), A.Cast)
        self.assertIsInstance(expr("(A[]) y"), A.Cast)
        self.assertIsInstance(expr("(int) -y"), A.Cast)
        self.assertIsInstance(expr("(a) - y"), A.Binary)     # не приведение
        self.assertIsInstance(expr("(a) + y"), A.Binary)
        c = expr("(A) (B) y")
        self.assertIsInstance(c.operand, A.Cast)

    def test_postfix_and_selectors(self):
        e = expr("a.b.c(1)[2].d")
        self.assertIsInstance(e, A.FieldAccess)
        self.assertIsInstance(e.target, A.ArrayIndex)
        self.assertIsInstance(e.target.array, A.MethodCall)
        self.assertIsInstance(expr("new A(1).m()"), A.MethodCall)
        self.assertIsInstance(expr("new int[3].length"), A.FieldAccess)
        self.assertIsInstance(expr("super.P.m(1)"), A.SuperMethodCall)
        self.assertIsInstance(expr("x instanceof A"), A.InstanceOf)

    def test_min_literals(self):
        self.assertEqual(expr("-2147483648").value, -2147483648)
        self.assertEqual(expr("-9223372036854775808L").value, -9223372036854775808)
        with self.assertRaises(ParseError):
            expr("2147483648")

    def test_statements(self):
        body = unit("class T { void m() { int a = 1; A[] b; if (a) x(); else { } while (c) break; "
                    "for (int i = 0; i < 3; i++, j--) continue; for (String s : arr) ; return; } }") \
            .types[0].members[0].body.stmts
        self.assertEqual([type(s).__name__ for s in body],
                         ["LocalVarDecl", "LocalVarDecl", "If", "While", "For", "ForEach", "Return"])
        self.assertEqual(len(body[4].update), 2)

    def test_not_a_statement(self):
        for bad in ("a + 1;", "a;", "(a = 1);", "1 = a;", "a.b;"):
            with self.assertRaises(ParseError, msg=bad):
                unit("class T { void m() { " + bad + " } }")

    def test_unsupported_constructs(self):
        cases = {
            "class T { int[][] a; }": "многомерные",
            "class T { void m() { x = new int[2][3]; } }": "многомерные",
            "class T { void m() { x = new int[2][]; } }": "многомерные",
            "class T { int[] a = {1, 2}; }": "литералы массивов",
            "class T { void m() { do { } while (true); } }": "do",
            "class T { void m() { switch (x) { } } }": "switch",
            "class T { void m() { l: while (true) { } } }": "метки",
            "class T { void m() { x &= 1; } }": "&=",
            "class T<X> { }": "обобщённые",
            "import a; class T { }": "импорт",
            "class T { class U { } }": "вложенные",
            "interface I { int x = 1; }": "поля в интерфейсах",
            "interface I { default void m() { } }": "default",
            "class T { void m() { final int x = 1; } }": "final",
            "class T { void m() { this(1); } }": "this(...)",
            "class T { int a[]; }": "имя[]",
            "class T { void m(int... a) { } }": "переменное число",
            "class T { void m() { x = y.class; } }": ".class",
        }
        for code, msg in cases.items():
            with self.assertRaises(ParseError, msg=code) as cm:
                unit(code)
            self.assertIn(msg, cm.exception.message, code)

    def test_syntax_error_position(self):
        with self.assertRaises(ParseError) as cm:
            unit("class T {\n  void m() {\n    int x = ;\n  }\n}")
        self.assertEqual((cm.exception.pos.line, cm.exception.pos.column), (3, 13))

    def test_stops_at_first_error(self):
        with self.assertRaises(ParseError) as cm:
            unit("class T { void m() { x = ; y = ; } }")
        self.assertEqual(cm.exception.pos.column, 26)


if __name__ == "__main__":
    unittest.main()
