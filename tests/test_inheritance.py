"""Одиночное наследование, единый Object, VTable с префиксной раскладкой, интерфейсы.

Модель проверяется на уровне inheritance.Hierarchy и сквозным запуском
(Java → C99 → компилятор C на -O0/-O2 → exe).
"""
import unittest

from tests.helpers import CompilerTestCase, frontend, main_wrap

CHAIN = """
class X { int f; X(int v) { f = v; } int m() { return f; } int who() { return 10 + f; } private int p() { return 0; } }
class A extends X { int g; A() { super(1); } int m() { return 100; } int extra() { return 7; } }
class B extends A { B() { } public String toString() { return "B!"; } int who() { return -1; } }
"""


class HierarchyModelTests(unittest.TestCase):
    def setUp(self):
        prog = frontend(CHAIN + main_wrap(""))
        self.h = prog.hierarchy
        self.cls = {c.name: c for c in prog.classes}

    def test_depth_and_chain(self):
        self.assertEqual([self.h.depth(self.cls[n]) for n in "XAB"], [1, 2, 3])

    def test_vtable_prefix_layout(self):
        X, A, B = (self.cls[n] for n in "XAB")
        sx = [s[0] for s, _ in self.h.vtable_slots(X)]
        sa = [s[0] for s, _ in self.h.vtable_slots(A)]
        sb = [s[0] for s, _ in self.h.vtable_slots(B)]
        self.assertEqual(sorted(sx), ["m", "who"])          # private p и Object-слоты не входят
        self.assertEqual(sa[:len(sx)], sx)                  # префикс родителя сохраняется
        self.assertEqual(sa[len(sx):], ["extra"])
        self.assertEqual(sb, sa)
        self.assertIs(self.h.slot_owner(B, ("m", ())), X)
        self.assertIs(self.h.slot_owner(B, ("extra", ())), A)
        self.assertIsNone(self.h.slot_owner(B, ("toString", ())))

    def test_method_resolution(self):
        X, A, B = (self.cls[n] for n in "XAB")
        mt = self.h.method_table(B)
        self.assertEqual(mt[("m", ())].owner.name, "A")
        self.assertEqual(mt[("who", ())].owner.name, "B")
        self.assertNotIn(("p", ()), mt)
        self.assertEqual(self.h.object_impl(B, ("toString", ())).owner.name, "B")
        self.assertIsNone(self.h.object_impl(A, ("toString", ())))

    def test_fields(self):
        B = self.cls["B"]
        self.assertEqual(self.h.field_lookup(B, "f").owner.name, "X")
        self.assertEqual([f.name for f in self.h.all_fields(B)], ["f", "g"])


class InheritanceRunTests(CompilerTestCase):
    def test_virtual_dispatch_and_upcasts(self):
        code = CHAIN + main_wrap("""
            B b = new B();
            X x = (X) b;
            A a = (A) b;
            System.out.println(x.m());
            System.out.println(x.who());
            System.out.println(a.extra());
            System.out.println(x.f);
            Object o = (Object) x;
            System.out.println(o);
            System.out.println(o == (Object) a);
            System.out.println(new X(5).who());
        """)
        self.assertOutput(code, "100\n-1\n7\n1\nB!\ntrue\n15\n")

    def test_field_hiding_and_super_field(self):
        code = """
        class P { int v = 1; String id() { return "P"; } }
        class Q extends P { int v = 2; String id() { return "Q"; }
          String all() { return String.valueOf(v) + String.valueOf(super.v) + super.id() + id(); }
          void set() { super.v = 9; } }
        """ + main_wrap("""
            Q q = new Q();
            System.out.println(q.all());
            q.set();
            P p = (P) q;
            System.out.println(p.v);
            System.out.println(q.v);
        """)
        self.assertOutput(code, "21PQ\n9\n2\n")

    def test_constructor_order(self):
        code = """
        class L { L(String s) { System.out.println("L " + s); } }
        class A extends L { int a = trace("A.field"); A() { super("via A"); System.out.println("A body"); }
          int trace(String s) { System.out.println(s); return 1; } }
        class C extends A { int c = trace("C.field"); C() { System.out.println("C body"); } }
        """ + main_wrap("C c = new C();")
        self.assertOutput(code, "L via A\nA.field\nA body\nC.field\nC body\n")

    def test_virtual_call_from_constructor_sees_dynamic_class(self):
        code = """
        class A { A() { System.out.println(name()); } String name() { return "A"; } }
        class C extends A { String s = "init"; String name() { return "C:" + (s == null ? "null" : s); } }
        """ + main_wrap("C c = new C(); System.out.println(c.name());")
        self.assertOutput(code, "C:null\nC:init\n")

    def test_object_methods_dispatch(self):
        code = """
        class A { int x; }
        class B extends A { public String toString() { return "B.toString"; }
                            public boolean equals(Object o) { return true; } }
        class D extends B { public String toString() { return "D!" + super.toString(); } }
        class E { }
        """ + main_wrap("""
            B b = new B();
            A a = (A) b;
            System.out.println((Object) b);
            System.out.println(a.toString());
            System.out.println(a.equals((Object) new E()));
            System.out.println(new A().equals((Object) new E()));
            D d = new D();
            System.out.println(((A) d).toString());
            System.out.println(a.getClass().getName());
            System.out.println(((A) d).getClass().getName());
            System.out.println(new A().toString().substring(0, 2));
        """)
        self.assertOutput(code, "B.toString\nB.toString\ntrue\nfalse\nD!B.toString\nB\nD\nA@\n")

    def test_super_to_object_base_implementation(self):
        code = """
        class P { }
        class A extends P { public String toString() { return "A/" + super.toString(); } }
        """ + main_wrap('String s = new A().toString(); System.out.println(s.substring(0, 4));')
        self.assertOutput(code, "A/A@\n")     # базовый Object.toString использует динамический класс

    def test_downcasts(self):
        code = CHAIN + main_wrap("""
            B b = new B();
            X x = (X) b;
            A a = (A) x;
            B back = (B) a;
            System.out.println(back == b);
            Object o = (Object) b;
            System.out.println(o instanceof X);
            System.out.println(o instanceof B);
            System.out.println(new X(5) instanceof A);
            System.out.println(x instanceof B);
            X nx = null;
            System.out.println(nx instanceof X);
            A na = (A) nx;
            System.out.println(na == null);
        """)
        self.assertOutput(code, "true\ntrue\ntrue\nfalse\ntrue\nfalse\ntrue\n")

    def test_failed_downcast(self):
        self.assertRuntimeError(CHAIN + main_wrap("X x = new X(1); A a = (A) x;"), "ClassCast", "X")
        self.assertRuntimeError(CHAIN + main_wrap("Object o = (Object) \"s\"; B c = (B) o;"), "ClassCast")
        self.assertRuntimeError(CHAIN + main_wrap("Object o = (Object) new X(1); String s = (String) o;"), "ClassCast")

    def test_unrelated_class_cast_is_compile_error(self):
        self.assertCompileError("class P { } class Q { }" + main_wrap("P p = new P(); Q q = (Q) p;"),
                                "(Q)(Object)")

    def test_deep_chain_slots(self):
        code = """
        abstract class S0 { abstract int a(); int base() { return a() * 10; } }
        class S1 extends S0 { int a() { return 1; } int b() { return 2; } }
        class S2 extends S1 { int b() { return 20; } int c() { return 3; } }
        class S3 extends S2 { int a() { return 100; } int c() { return 30; } }
        """ + main_wrap("""
            S0 s = (S0) new S3();
            S1 s1 = (S1) s;
            S2 s2 = (S2) s;
            System.out.println(s.base());
            System.out.println(s1.b());
            System.out.println(s2.c());
            S0 t = (S0) new S1();
            System.out.println(t.base());
        """)
        self.assertOutput(code, "1000\n20\n30\n10\n")


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
          Sq(double s, int id) { super(id); side = s; }
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
            System.out.println(arr[0].area());
            System.out.println(sh.toString().substring(0, 3));
            System.out.println(sh.equals((Object) s));
        """)
        self.assertOutput(code, "9.0\nbase4\nbase4\ntrue\nfalse\nbase4\ntrue\n9.0\nSq@\ntrue\n")

    def test_interface_inherited_through_parent(self):
        code = """
        interface I { String who(); }
        class A implements I { public String who() { return "A"; } }
        class C extends A { public String who() { return "C"; } }
        class D extends A { }
        """ + main_wrap("""
            I i = (I) new C();
            System.out.println(i.who());
            System.out.println(((I) new D()).who());
            Object o = (Object) new D();
            System.out.println(o instanceof I);
            System.out.println(((I) o).who());
        """)
        self.assertOutput(code, "C\nA\ntrue\nA\n")

    def test_dynamic_interface_cast_from_class(self):
        code = """
        interface I { int v(); }
        class A { }
        class C extends A implements I { public int v() { return 9; } }
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

    def test_final_class_not_implementing_interface(self):
        self.assertCompileError("interface I { } final class F { }" + main_wrap("F f = new F(); I i = (I) f;"),
                                "final")

    def test_same_signature_from_two_interfaces_is_one_obligation(self):
        code = """
        interface I { int m(); }
        interface J { int m(); }
        class K implements I, J { public int m() { return 5; } }
        """ + main_wrap("K k = new K(); System.out.println(((I) k).m() + ((J) k).m());")
        self.assertOutput(code, "10\n")

    def test_array_types_are_exact(self):
        # ковариантности массивов нет: I[] не является Object[] ни статически, ни во время выполнения
        code = """
        interface I { } class A implements I { }
        """ + main_wrap("""
            I[] arr = new I[1];
            arr[0] = (I) new A();
            Object o = (Object) arr;
            System.out.println(o instanceof I[]);
            System.out.println(o instanceof Object[]);
            I[] back = (I[]) o;
            System.out.println(back == arr);
            Object[] oa = (Object[]) o;
        """)
        r = self.assertRuntimeError(code, "ClassCast")
        self.assertEqual(r.stdout, "true\nfalse\ntrue\n")
        self.assertCompileError("interface I { }" + main_wrap("I[] a = new I[1]; Object[] o = (Object[]) a;"), "")


if __name__ == "__main__":
    unittest.main()
