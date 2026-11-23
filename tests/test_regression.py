"""Регрессионные тесты на найденные при разработке ошибки и на коллизии имён C."""
from tests.helpers import CompilerTestCase, main_wrap


class RegressionTests(CompilerTestCase):
    def test_downcast_direction(self):
        # ошибка: проверка «S — предок T» была записана наоборот, (C) a отвергалось
        code = "class A { } class C extends A { C() { super(); } }" + main_wrap(
            "A a = (A) new C(); C c = (C) a; System.out.println(c == (C) a);")
        self.assertOutput(code, "true\n")

    def test_parameter_named_self(self):
        # ошибка: параметр «self» совпадал с C-именем указателя на this
        code = "class A { int v = 3; int add(int self) { return v + self; } }" + main_wrap(
            "System.out.println(new A().add(4));")
        self.assertOutput(code, "7\n")

    def test_discarded_call_results_compile_cleanly(self):
        # неиспользуемые результаты вызовов не должны давать предупреждений GCC
        code = "class A { int f() { return 1; } }" + main_wrap('new A().f(); "abc".length(); new A();')
        self.assertOutput(code, "")

    def test_names_that_clash_with_c(self):
        code = """
        class int_ { }
        class top { int hdr; int vt; int self; int top; top() { hdr = 1; vt = 2; self = 3; top = 4; }
          int sum() { int auto = hdr; int register = vt; int jm_x = self; return auto + register + jm_x + top; } }
        class main2 extends top { main2() { super(); } }
        class jm_Box { int jo_x = 5; int main() { return jo_x; } int jv_() { return 1; } }
        class rt_error { int rt_gc_collect = 2; }
        """ + main_wrap("""
            System.out.println(new main2().sum());
            System.out.println(new jm_Box().main() + new jm_Box().jv_());
            System.out.println(new rt_error().rt_gc_collect);
            int $x = 1; int _y = 2; int a_1 = 3; int a_ = 4;
            System.out.println($x + _y + a_1 + a_);
        """)
        self.assertOutput(code, "10\n6\n2\n10\n")

    def test_overloads_produce_distinct_c_names(self):
        code = """
        class O { String f(int a) { return "I"; } String f(long a) { return "J"; } String f(int[] a) { return "AI"; }
          String f(O o) { return "LO"; } String f(int a, int b) { return "II"; } String f() { return "-"; } }
        """ + main_wrap("""
            O o = new O();
            System.out.println(o.f(1) + o.f(1L) + o.f(new int[1]) + o.f(o) + o.f(1, 2) + o.f());
        """)
        self.assertOutput(code, "IJAILOII-\n")

    def test_string_literal_with_trigraph_and_quotes(self):
        self.assertOutput(main_wrap('System.out.println("??= \\"q\\" \\\\ */ //");'), '??= "q" \\ */ //\n')

    def test_empty_interface_and_class(self):
        code = "interface E { } class K implements E { }" + main_wrap(
            "Object o = (Object) new K(); System.out.println(o instanceof E);")
        self.assertOutput(code, "true\n")

    def test_long_constant_extremes(self):
        self.assertOutput(main_wrap("long a = -9223372036854775808L; long b = 0xFFFFFFFFL; "
                                    "System.out.println(a); System.out.println(b);"),
                          "-9223372036854775808\n4294967295\n")

    def test_char_increment_and_float_increment(self):
        self.assertOutput(main_wrap("char c = 'y'; c++; c++; float f = 1.5f; f++; double d = 0.5; --d; "
                                    "System.out.println(c); System.out.println(f); System.out.println(d);"),
                          "{\n2.5\n-0.5\n")

    def test_interface_array_class_name(self):
        # ошибка v2: имя класса массива интерфейсов брало java_name у InterfaceSymbol
        code = "interface I { }" + main_wrap(
            "I[] a = new I[2]; System.out.println(((Object) a).getClass().getName());")
        self.assertOutput(code, "[LI;\n")

    def test_reference_live_across_loop_back_edge(self):
        # переменная, используемая только в начале следующей итерации, жива на safepoint в конце тела
        code = "class N { int v; N(int v) { this.v = v; } }" + main_wrap("""
            N prev = new N(0);
            int s = 0;
            for (int i = 1; i < 6; i++) { s += prev.v; prev = null; N t = new N(i); prev = t; }
            N keep = new N(100);
            int k = 0;
            while (k < 3) { String junk = String.valueOf(k); k++; s += keep.v; }
            System.out.println(s);
        """)
        self.assertOutput(code, "310\n")

    def test_static_init_from_subclass_method_and_constructor(self):
        code = """
        class P { static int[] data = new int[3]; static int base = 5; }
        class Q extends P { Q() { data[0] = base; } int get() { return data[0] + base; } }
        """ + main_wrap("System.out.println(new Q().get());")
        self.assertOutput(code, "10\n")