"""Множественное наследование, единый Object, VTable ветвей, интерфейсы.

Модель проверяется дважды: на уровне inheritance.Hierarchy и сквозным
запуском (Java → C89 → GCC → exe).
"""
import unittest

from tests.helpers import CompilerTestCase, frontend, main_wrap

DIAMOND = """
class X { int f; X(int v) { f = v; } int m() { return f; } int who() { return 10 + f; } }
class A extends X { A() { super.X(1); } }
class B extends X { B() { super.X(2); } }
class C extends A, B { C() { super.A(); super.B(); } int m() { return 100; } }
"""


class HierarchyModelTests(unittest.TestCase):
    def setUp(self):
        prog = frontend(DIAMOND + main_wrap(""))
        self.h = prog.hierarchy
        self.cls = {c.name: c for c in prog.classes}

    def test_subobjects_duplicate_ordinary_ancestor_not_object(self):
        subs = self.h.subobjects(self.cls["C"])
        self.assertEqual([self.h.path_str(self.cls["C"], s.path) for s in subs], ["C", "C.A", "C.A.X", "C.B", "C.B.X"])
        self.assertEqual([s.enclosing for s in subs], [-1, 0, 1, 0, 3])
        self.assertNotIn("Object", [s.cls.name for s in subs])     # Object — только заголовок

    def test_paths_to(self):
        C, X, A = self.cls["C"], self.cls["X"], self.cls["A"]
        self.assertEqual(len(self.h.paths_to(C, X)), 2)
        self.assertEqual(len(self.h.paths_to(C, A)), 1)

    def test_override_applies_only_to_resolved_branch(self):
        C, A, B, X = (self.cls[n] for n in "CABX")
        sig = ("m", ())
        self.assertEqual(self.h.effective(C, (A, X), sig).decl.owner.name, "C")
        self.assertEqual(self.h.effective(C, (A,), sig).decl.owner.name, "C")
        self.assertEqual(self.h.effective(C, (B, X), sig).decl.owner.name, "X")
        self.assertEqual(self.h.effective(C, (B,), sig).path, (B, X))
        who = ("who", ())
        self.assertEqual(self.h.effective(C, (B,), who).path, (B, X))

    def test_first_parent_priority_for_inherited_methods(self):
        C, A, X = self.cls["C"], self.cls["A"], self.cls["X"]
        self.assertEqual(self.h.method_table(C)[("who", ())].path, (A, X))

    def test_field_resolution(self):
        C, A, X = self.cls["C"], self.cls["A"], self.cls["X"]
        path, f = self.h.field_lookup(C, "f")
        self.assertEqual(path, (A, X))


class InheritanceRunTests(CompilerTestCase):
    def test_diamond_object_single_ordinary_ancestor_duplicated(self):
        code = DIAMOND + main_wrap("""
            C c = new C();
            A a = (A) c; B b = (B) c;
            X xa = (X) a; X xb = (X) b;
            System.out.println(xa == xb);                       // два разных подобъекта X
            System.out.println((Object) xa == (Object) xb);     // один и тот же Object
            System.out.println((Object) a == (Object) c);
            System.out.println(xa.f + xb.f * 10);
            xa.f = 5;
            System.out.println(c.A.f + " " + String.valueOf(c.B.f));
            Object o = (Object) c;
            System.out.println(o instanceof X);                 // неоднозначно во время выполнения
            System.out.println(o instanceof A);
            System.out.println(((A) o).f);
        """.replace('c.A.f + " "', 'String.valueOf(c.A.f) + " "'))
        self.assertOutput(code, "false\ntrue\ntrue\n21\n5 2\nfalse\ntrue\n5\n")

    def test_ambiguous_runtime_cast_fails(self):
        code = DIAMOND + main_wrap("Object o = (Object) new C(); X x = (X) o;")
        self.assertRuntimeError(code, "ClassCast", "ambiguous")

    def test_separate_vtables_and_first_branch_override(self):
        code = DIAMOND + main_wrap("""
            C c = new C();
            System.out.println(c.m());
            System.out.println(((A) c).m());
            System.out.println(((X) (A) c).m());
            System.out.println(((B) c).m());
            System.out.println(((X) (B) c).m());
            System.out.println(c.who());
            System.out.println(c.B.who());
            System.out.println(c.B.m());
        """)
        self.assertOutput(code, "100\n100\n100\n2\n2\n11\n12\n2\n")

    def test_same_field_and_method_in_two_parents(self):
        code = """
        class A { int v = 1; String id() { return "A"; } }
        class B { int v = 2; String id() { return "B"; } String onlyB() { return "b"; } }
        class C extends A, B { C() { super.A(); super.B(); } }
        class D extends A, B { int v = 3; D() { super.A(); super.B(); } }
        """ + main_wrap("""
            C c = new C();
            System.out.println(c.v);
            System.out.println(c.id());
            System.out.println(c.B.v);
            System.out.println(c.B.id());
            System.out.println(c.onlyB());
            D d = new D();
            System.out.println(d.v);
            System.out.println(d.A.v);
        """)
        self.assertOutput(code, "1\nA\n2\nB\nb\n3\n1\n")

    def test_override_reaches_second_parent_when_first_lacks_it(self):
        code = """
        class A { int a() { return 1; } }
        abstract class B { abstract int b(); int callB() { return b(); } }
        class C extends A, B { C() { super.A(); super.B(); } int b() { return 42; } }
        """ + main_wrap("C c = new C(); System.out.println(c.callB()); System.out.println(((B) c).b());")
        self.assertOutput(code, "42\n42\n")

    def test_abstract_in_both_branches_needs_two_overrides(self):
        code = """
        abstract class A { abstract int m(); }
        abstract class B { abstract int m(); }
        class C extends A, B { C() { super.A(); super.B(); } int m() { return 1; } }
        """ + main_wrap("")
        self.assertCompileError(code, "подобъект C.B")

    def test_qualified_and_super_calls(self):
        code = """
        class P { int v; P(int v) { this.v = v; } int get() { return v; } int twice() { return get() * 2; } }
        class Q { int get() { return 7; } }
        class R extends P, Q {
          R() { super.P(3); super.Q(); }
          int get() { return 1000; }
          int both() { return this.P.get() + this.Q.get() + super.Q.get() + super.P.get(); }
        }
        """ + main_wrap("""
            R r = new R();
            System.out.println(r.twice());
            System.out.println(r.Q.get());
            System.out.println(r.P.get());
            System.out.println(r.both());
        """)
        self.assertOutput(code, "2000\n7\n1000\n1017\n")

    def test_constructor_order(self):
        code = """
        class L { L(String s) { System.out.println("L " + s); } }
        class A extends L { int a = trace("A.field"); A() { super.L("via A"); System.out.println("A body"); }
          int trace(String s) { System.out.println(s); return 1; } }
        class B { B() { System.out.println("B body"); } }
        class C extends A, B { int c = trace("C.field");
          C() { super.A(); super.B(); System.out.println("C body"); } }
        """ + main_wrap("C c = new C();")
        self.assertOutput(code, "L via A\nA.field\nA body\nB body\nC.field\nC body\n")

    def test_virtual_call_from_constructor_sees_dynamic_class(self):
        code = """
        class A { A() { System.out.println(name()); } String name() { return "A"; } }
        class C extends A { C() { super.A(); } String name() { return "C"; } }
        """ + main_wrap("C c = new C();")
        self.assertOutput(code, "C\n")

    def test_object_methods_dispatch_rule_12_4(self):
        code = """
        class A { int x; }
        class B { public String toString() { return "B.toString"; }
                  public boolean equals(Object o) { return true; } }
        class C extends A, B { C() { super.A(); super.B(); } }
        class D extends C { D() { super.C(); } public String toString() { return "D!"; } }
        class E { }
        """ + main_wrap("""
            C c = new C();
            System.out.println((Object) c);
            System.out.println(c.toString());
            System.out.println(((A) c).toString());
            System.out.println(((B) c).toString());
            System.out.println(c.equals((Object) new E()));
            System.out.println(((A) c).equals((Object) new E()));
            D d = new D();
            System.out.println(((B) d).toString());
            System.out.println(((A) d).toString());
            System.out.println(c.getClass().getName());
            System.out.println(((A) d).getClass().getName());
        """)
        self.assertOutput(code, "B.toString\nB.toString\nB.toString\nB.toString\ntrue\ntrue\nD!\nD!\nC\nD\n")

    def test_super_to_object_base_implementation(self):
        code = """
        class A { public String toString() { return "A:" + super.Object.toString(); } }
        """ + main_wrap("")
        self.assertCompileError(code, "не является непосредственным родителем")
        code = """
        class P { }
        class A extends P { A() { super.P(); } public String toString() { return "A/" + super.P.toString(); } }
        """ + main_wrap('String s = new A().toString(); System.out.println(s.substring(0, 4));')
        self.assertOutput(code, "A/A@\n")     # базовый Object.toString использует динамический класс

    def test_downcasts(self):
        # регрессия: проверка направления «предок → потомок»
        code = DIAMOND + main_wrap("""
            C c = new C();
            A a = (A) c;
            C back = (C) a;
            System.out.println(back == c);
            X x = (X) (B) c;
            C c2 = (C) x;
            System.out.println(c2 == c);
            B b2 = (B) (Object) c;
            System.out.println(b2 == (B) c);
            System.out.println(x instanceof C);
            System.out.println(new X(5) instanceof A);
        """)
        self.assertOutput(code, "true\ntrue\ntrue\ntrue\nfalse\n")

    def test_failed_downcast(self):
        self.assertRuntimeError(DIAMOND + main_wrap("X x = new X(1); A a = (A) x;"), "ClassCast", "cannot cast X to A")
        self.assertRuntimeError(DIAMOND + main_wrap("Object o = (Object) \"s\"; C c = (C) o;"), "ClassCast")

    def test_downcast_from_branch_not_enclosing(self):
        # (A) от подобъекта B.X: X внутри B, а не внутри A — приведение по объемлющей цепочке
        code = DIAMOND + main_wrap("C c = new C(); X x = (X) (B) c; A a = (A) x;")
        self.assertRuntimeError(code, "ClassCast")


class InterfaceRunTests(CompilerTestCase):
    def test_interfaces(self):
        code = """
        interface Shape { double area(); String name(); }
        interface Named { String name(); }
        interface Both extends Shape, Named { }
        abstract class Base implements Shape {
          protected int id;
          Base(int id) { this.id = id; }
          public String name() { return "base" + String.valueOf(id); }
        }
        final class Sq extends Base implements Both {
          private double side;
          Sq(double s, int id) { super.Base(id); side = s; }
          public double area() { return side * side; }
        }
        class Other { }
        """ + main_wrap("""
            Sq s = new Sq(3.0, 4);
            Shape sh = (Shape) s;
            Named nm = (Named) s;
            Both bo = (Both) s;
            System.out.println(sh.area());
            System.out.println(nm.name());
            System.out.println(((Named) bo).name());
            Object o = (Object) s;
            System.out.println(o instanceof Named);
            System.out.println((Object) new Other() instanceof Named);
            Named n2 = (Named) o;
            System.out.println(n2.name());
            Sq back = (Sq) n2;
            System.out.println(back == s);
            Shape[] arr = new Shape[2];
            arr[0] = sh;
            System.out.println(arr[0].area() + arr.length);
            System.out.println(sh.toString().substring(0, 3));
            System.out.println(sh.equals((Object) s));
        """).replace("arr[0].area() + arr.length", "arr[0].area()")
        self.assertOutput(code, "9.0\nbase4\nbase4\ntrue\nfalse\nbase4\ntrue\n9.0\nSq@\ntrue\n")

    def test_interface_inherited_through_class_and_branch_views(self):
        code = """
        interface I { String who(); }
        class A implements I { public String who() { return "A"; } }
        class B implements I { public String who() { return "B"; } }
        class C extends A, B { C() { super.A(); super.B(); } }
        """ + main_wrap("""
            C c = new C();
            System.out.println(((I) c).who());
            System.out.println(((I) (B) c).who());
            System.out.println(((I) (A) c).who());
            Object o = (Object) c;
            System.out.println(((I) o).who());
            A a = (A) c;
            I viaA = (I) a;
            System.out.println(viaA.who());
        """)
        self.assertOutput(code, "A\nB\nA\nA\nA\n")

    def test_dynamic_interface_cast_from_class(self):
        code = """
        interface I { int v(); }
        class A { }
        class C extends A implements I { C() { super.A(); } public int v() { return 9; } }
        """ + main_wrap("""
            A a = (A) new C();
            System.out.println(a instanceof I);
            I i = (I) a;
            System.out.println(i.v());
            System.out.println(new A() instanceof I);
            I bad = (I) new A();
        """)
        r = self.assertRuntimeError(code, "ClassCast")
        self.assertEqual(r.stdout, "true\n9\nfalse\n")

    def test_same_signature_from_two_interfaces_is_one_obligation(self):
        code = """
        interface I { int m(); }
        interface J { int m(); }
        class K implements I, J { public int m() { return 5; } }
        """ + main_wrap("K k = new K(); System.out.println(((I) k).m() + ((J) k).m());")
        self.assertOutput(code, "10\n")

    def test_interface_return_type_conflict(self):
        self.assertCompileError("interface I { int m(); } interface J { long m(); } interface K extends I, J { }"
                                + main_wrap(""), "разными типами результата")


if __name__ == "__main__":
    unittest.main()
