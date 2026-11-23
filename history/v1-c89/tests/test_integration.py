"""Сквозные тесты: Java → IR → C89 → gcc -std=c89 -pedantic-errors → запуск → stdout.

Для каждой программы проверяется, что сгенерированный C собирается в режиме
C90 без ошибок и предупреждений, линкуется с runtime и выдаёт ожидаемый
результат (helpers.run_java).
"""
import re
import unittest

from tests.helpers import CompilerTestCase, main_wrap, run_java


class C89ComplianceTests(unittest.TestCase):
    def test_generated_code_has_no_c99_features(self):
        r = run_java(main_wrap('String s = "x"; for (int i = 0; i < 3; i++) { s = s + "y"; } '
                               "System.out.println(s); long a = 1L; a = a * 3L; System.out.println(a);"))
        self.assertEqual(r.stdout, "xyyy\n3\n")
        src = r.c_source
        self.assertNotIn("//", src.replace("://", ""))
        self.assertNotIn("long long", src)
        self.assertNotIn("inline", src)
        self.assertNotIn("stdint", src)
        self.assertIsNone(re.search(r"\.\w+\s*=\s*[^=]", src.split("/* class metadata */")[-1].split("{", 1)[0]))


class PrimitiveTests(CompilerTestCase):
    def test_int_arithmetic_wraps(self):
        self.assertOutput(main_wrap("""
            System.out.println(2147483647 + 1);
            System.out.println(-2147483648 - 1);
            System.out.println(65536 * 65536);
            System.out.println(-2147483648 / -1);
            System.out.println(-2147483648 % -1);
            System.out.println(-7 / 2);
            System.out.println(7 / -2);
            System.out.println(-7 % 2);
            System.out.println(7 % -2);
            System.out.println(-(-2147483648));
            System.out.println(~5);
            System.out.println(6 & 3 | 8 ^ 1);
        """), "-2147483648\n2147483647\n0\n-2147483648\n0\n-3\n-3\n-1\n1\n-2147483648\n-6\n11\n")

    def test_shifts(self):
        self.assertOutput(main_wrap("""
            System.out.println(1 << 31);
            System.out.println(1 << 32);
            System.out.println(-16 >> 2);
            System.out.println(-16 >>> 28);
            System.out.println(-1 >>> 0);
            System.out.println(1L << 63L);
            System.out.println(-1L >>> 1L);
            System.out.println(-256L >> 4L);
            System.out.println((byte) -16 >> (byte) 2);
            System.out.println((byte) -16 >>> (byte) 28);
        """), "-2147483648\n1\n-4\n15\n-1\n-9223372036854775808\n9223372036854775807\n-16\n-4\n15\n")

    def test_long_arithmetic(self):
        self.assertOutput(main_wrap("""
            long max = 9223372036854775807L;
            long min = -9223372036854775808L;
            System.out.println(max + 1L);
            System.out.println(min - 1L);
            System.out.println(min);
            System.out.println(min / -1L);
            System.out.println(min % -1L);
            System.out.println(123456789012L * 1000L);
            System.out.println(4294967296L * 4294967296L);
            System.out.println(-1234567890123L / 1000L);
            System.out.println(-1234567890123L % 1000L);
            System.out.println(max > min);
            System.out.println(-5L < 3L);
            System.out.println(0x7FFFFFFFFFFFFFFFL == max);
            System.out.println((long) -1 + 0L);
            System.out.println((int) 4294967297L);
            System.out.println(String.valueOf(-1L));
        """), "-9223372036854775808\n9223372036854775807\n-9223372036854775808\n-9223372036854775808\n0\n"
              "123456789012000\n0\n-1234567890\n-123\ntrue\ntrue\ntrue\n-1\n1\n-1\n")

    def test_small_types_no_promotion(self):
        self.assertOutput(main_wrap("""
            byte b = (byte) 127; b++; System.out.println(b);
            byte c = (byte) 100; c += (byte) 100; System.out.println(c);
            short s = (short) 32767; s = (short) (s + (short) 1); System.out.println(s);
            char ch = 'a'; ch += (char) 1; System.out.println(ch);
            char z = (char) 0; z--; System.out.println((int) z);
            byte d = (byte) -128; System.out.println(-d);
            byte e = (byte) -128; System.out.println(e / (byte) -1);
        """), "-128\n-56\n-32768\nb\n65535\n-128\n-128\n")

    def test_conversions(self):
        self.assertOutput(main_wrap("""
            System.out.println((int) 3.99);
            System.out.println((int) -3.99);
            System.out.println((int) (0.0 / 0.0));
            System.out.println((int) (1.0 / 0.0));
            System.out.println((int) (-1.0 / 0.0));
            System.out.println((long) 1e19);
            System.out.println((long) -1e19);
            System.out.println((long) 123456789.9);
            System.out.println((byte) 300);
            System.out.println((char) 65);
            System.out.println((short) -40000);
            System.out.println((byte) 3.9e10);
            System.out.println((float) 1e40);
            System.out.println((float) 0.1);
            System.out.println((double) 0.1f);
            System.out.println((double) 9007199254740993L);
            System.out.println((float) 16777217);
            System.out.println((long) (float) 9223372036854775807L);
            System.out.println((int) 'A' + 1);
            System.out.println((char) (int) 'A');
        """), "3\n-3\n0\n2147483647\n-2147483648\n9223372036854775807\n-9223372036854775808\n123456789\n44\nA\n"
              "25536\n-1\nInfinity\n0.1\n0.10000000149011612\n9.007199254740992E15\n1.6777216E7\n"
              "9223372036854775807\n66\nA\n")

    def test_floating_point_semantics(self):
        self.assertOutput(main_wrap("""
            double z = 0.0;
            System.out.println(1.0 / z);
            System.out.println(-1.0 / z);
            System.out.println(z / z);
            System.out.println(-z);
            System.out.println(z == -z);
            double nan = z / z;
            System.out.println(nan == nan);
            System.out.println(nan != nan);
            System.out.println(nan < 1.0);
            System.out.println(5.5 % 2.0);
            System.out.println(-5.5 % 2.0);
            System.out.println(5.0 % z);
            System.out.println(1.0f / 0.0f);
            System.out.println(0.1 + 0.2);
            System.out.println(1.0f / 3.0f);
            System.out.println(100.0f * 1.1f);
        """), "Infinity\n-Infinity\nNaN\n-0.0\ntrue\nfalse\ntrue\nfalse\n1.5\n-1.5\nNaN\nInfinity\n"
              "0.30000000000000004\n0.33333334\n110.0\n")

    def test_number_formatting_like_java(self):
        self.assertOutput(main_wrap("""
            System.out.println(1.0);
            System.out.println(100.0);
            System.out.println(0.001);
            System.out.println(0.0001);
            System.out.println(9999999.0);
            System.out.println(10000000.0);
            System.out.println(123456789.0);
            System.out.println(1.7976931348623157E308);
            System.out.println(4.9E-324);
            System.out.println(1e23);
            System.out.println(2e-3);
            System.out.println(-1.5);
            System.out.println(1.0E21);
            System.out.println(0.1f);
            System.out.println(3.4028235E38f);
            System.out.println(1.4E-45f);
            System.out.println(1.0E10f);
            System.out.println(123.456f);
            System.out.println(Math.PI);
            System.out.println(Math.E);
        """), "1.0\n100.0\n0.001\n1.0E-4\n9999999.0\n1.0E7\n1.23456789E8\n1.7976931348623157E308\n4.9E-324\n"
              "1.0E23\n0.002\n-1.5\n1.0E21\n0.1\n3.4028235E38\n1.4E-45\n1.0E10\n123.456\n3.141592653589793\n"
              "2.718281828459045\n")

    def test_print_formats(self):
        self.assertOutput(main_wrap("""
            System.out.println(true);
            System.out.println('x');
            System.out.println((byte) -5);
            System.out.println((short) 300);
            System.out.println(-2147483648);
            System.out.println(String.valueOf(false) + String.valueOf('c') + String.valueOf(1.5f));
            String s = null;
            System.out.println(s);
            Object o = null;
            System.out.println(o);
            System.out.print("a"); System.out.print(1); System.out.println();
        """), "true\nx\n-5\n300\n-2147483648\nfalsec1.5\nnull\nnull\na1\n")

    def test_math(self):
        self.assertOutput(main_wrap("""
            System.out.println(Math.abs(-5));
            System.out.println(Math.abs(-2147483648));
            System.out.println(Math.abs(-7L));
            System.out.println(Math.abs(-0.0));
            System.out.println(Math.abs(-2.5f));
            System.out.println(Math.min(3, 4));
            System.out.println(Math.max(-3L, -4L));
            System.out.println(Math.min(-0.0, 0.0));
            System.out.println(Math.max(-0.0, 0.0));
            System.out.println(Math.max(1.0, 0.0 / 0.0));
            System.out.println(Math.sqrt(2.0));
            System.out.println(Math.sqrt(-1.0));
            System.out.println(Math.pow(2.0, 10.0));
            System.out.println(Math.pow(1.0, 0.0 / 0.0));
            System.out.println(Math.pow(-8.0, 1.0 / 3.0));
            System.out.println(Math.pow(0.0, -1.0));
            System.out.println(Math.sin(0.0));
            System.out.println(Math.cos(0.0));
            System.out.println(Math.sin(1.0 / 0.0));
        """), "5\n-2147483648\n7\n0.0\n2.5\n3\n-3\n-0.0\n0.0\nNaN\n1.4142135623730951\nNaN\n1024.0\nNaN\nNaN\n"
              "Infinity\n0.0\n1.0\nNaN\n")


class StringTests(CompilerTestCase):
    def test_string_api(self):
        self.assertOutput(main_wrap("""
            String s = "Привет";
            System.out.println(s.length());
            System.out.println(s.charAt(1));
            System.out.println(s.substring(1, 4));
            System.out.println(s.substring(6, 6).length());
            String e = "\U0001F600";
            System.out.println(e.length());
            System.out.println((int) e.charAt(0));
            System.out.println("ab" == "ab");
            String built = "a" + "b";
            System.out.println(built == "ab");
            System.out.println(built.equals("ab"));
            System.out.println(built.equals((Object) "ab"));
            System.out.println(((Object) built).equals((Object) "ab"));
            System.out.println("x".equals((String) null));
            String n = null;
            System.out.println("v=" + n);
            String acc = "";
            acc += "1"; acc += "2";
            System.out.println(acc);
            System.out.println(((Object) "s").getClass().getName());
            System.out.println("abc".toString());
            System.out.println("Unicode: \\u00e9 \\u4e16\\u754c");
        """), "6\nр\nрив\n0\n2\n55357\ntrue\nfalse\ntrue\ntrue\ntrue\nfalse\nv=null\n12\njava.lang.String\nabc\n"
              "Unicode: é 世界\n")

    def test_output_is_utf8_with_surrogate_pairs(self):
        r = run_java(main_wrap('System.out.println("Ж\U0001F600"); System.out.println("\\ud800!");'))
        self.assertEqual(r.stdout, "Ж\U0001F600\n�!\n")

    def test_args_are_utf16(self):
        r = run_java(main_wrap("for (String a : args) { System.out.println(a + \":\" + String.valueOf(a.length())); }"
                               "System.out.println(args.length);"), ["abc", "Жук", "\U0001F600"])
        self.assertEqual(r.stdout, "abc:3\nЖук:3\n\U0001F600:2\n3\n")


class ControlFlowTests(CompilerTestCase):
    def test_loops_and_branches(self):
        self.assertOutput(main_wrap("""
            int n = 0;
            while (true) { n++; if (n > 10) break; if (n % 2 == 0) continue; }
            System.out.println(n);
            for (int i = 0; i < 5; i++) { if (i == 2) continue; System.out.print(i); }
            System.out.println();
            int s = 0;
            for (int i = 0; i < 4; i++) for (int j = 0; j < 4; j++) { if (j > i) break; s += j; }
            System.out.println(s);
            int k = 0;
            for (;;) { if (++k == 3) break; }
            System.out.println(k);
            int x = 5;
            String r = x > 3 ? (x > 4 ? "big" : "mid") : "small";
            System.out.println(r);
        """), "11\n0134\n10\n3\nbig\n")

    def test_short_circuit_and_ternary_evaluate_only_needed(self):
        code = """
        class T { int calls; boolean t(boolean v) { calls++; return v; } int v(int x) { calls++; return x; } }
        """ + main_wrap("""
            T t = new T();
            boolean a = t.t(false) && t.t(true);
            boolean b = t.t(true) || t.t(true);
            int c = t.t(true) ? t.v(1) : t.v(2);
            System.out.println(t.calls);
            boolean d = t.t(true) & t.t(false);
            System.out.println(t.calls);
        """)
        self.assertOutput(code, "4\n6\n")

    def test_left_to_right_evaluation(self):
        code = """
        class T { String log = ""; int v(int x) { log = log + String.valueOf(x); return x; } }
        """ + main_wrap("""
            T t = new T();
            int r = t.v(1) - t.v(2) * t.v(3);
            System.out.println(t.log);
            int x = 1;
            int y = x + (x = 5);
            System.out.println(y);
            int[] a = new int[3];
            int i = 0;
            a[i] = i = 2;
            System.out.println(a[0]);
            System.out.println(i);
        """)
        self.assertOutput(code, "123\n6\n2\n2\n")

    def test_recursion(self):
        code = "class F { int fib(int n) { return n < 2 ? n : fib(n - 1) + fib(n - 2); } }" + \
            main_wrap("System.out.println(new F().fib(20));")
        self.assertOutput(code, "6765\n")


class ArrayTests(CompilerTestCase):
    def test_arrays(self):
        code = """class P { int v; P(int v) { this.v = v; } }""" + main_wrap("""
            int[] a = new int[4];
            System.out.println(a.length);
            System.out.println(a[3]);
            for (int i = 0; i < a.length; i++) a[i] = i * i;
            int sum = 0;
            for (int v : a) sum += v;
            System.out.println(sum);
            boolean[] bs = new boolean[1]; System.out.println(bs[0]);
            char[] cs = new char[1]; System.out.println((int) cs[0]);
            double[] ds = new double[1]; System.out.println(ds[0]);
            long[] ls = new long[2]; ls[1] = 5L; ls[1] *= 3L; System.out.println(ls[1]);
            String[] ss = new String[2]; System.out.println(ss[0]);
            P[] ps = new P[3];
            ps[1] = new P(7);
            System.out.println(ps[1].v);
            System.out.println(ps[0] == null);
            int[] alias = a;
            alias[0] = 99;
            System.out.println(a[0]);
            Object o = (Object) a;
            int[] back = (int[]) o;
            System.out.println(back == a);
            System.out.println(o instanceof int[]);
            System.out.println(o instanceof long[]);
            System.out.println(o.getClass().getName());
            System.out.println(((Object) ps).getClass().getName());
            System.out.println(((Object) ss).getClass().getName());
            Object[] objs = new Object[2];
            objs[0] = (Object) "s"; objs[1] = (Object) ps;
            System.out.println(objs[0]);
            int[] empty = new int[0];
            System.out.println(empty.length);
        """)
        self.assertOutput(code, "4\n0\n14\nfalse\n0\n0.0\n15\nnull\n7\ntrue\n99\ntrue\ntrue\nfalse\n[I\n[LP;\n"
                                "[Ljava.lang.String;\ns\n0\n")

    def test_array_passed_by_reference(self):
        code = "class U { void fill(int[] a) { a[0] = 42; } int[] make() { return new int[2]; } }" + main_wrap("""
            int[] a = new int[1];
            new U().fill(a);
            System.out.println(a[0]);
            System.out.println(new U().make().length);
        """)
        self.assertOutput(code, "42\n2\n")


class RuntimeErrorTests(CompilerTestCase):
    def test_null_field(self):
        r = self.assertRuntimeError("class P { int v; }" + main_wrap("P p = null; int x = p.v;"), "NullPointer")
        self.assertIn("Test.java:4:", r.stderr)
        self.assertIn("Main.main(String[])", r.stderr)

    def test_null_call_and_string(self):
        self.assertRuntimeError("class P { int v() { return 1; } }" + main_wrap("P p = null; p.v();"), "NullPointer")
        self.assertRuntimeError(main_wrap("String s = null; int n = s.length();"), "NullPointer")
        self.assertRuntimeError(main_wrap("int[] a = null; int n = a.length;"), "NullPointer")
        self.assertRuntimeError(main_wrap("Object o = null; String s = o.toString();"), "NullPointer")

    def test_output_before_error_is_flushed(self):
        r = self.assertRuntimeError(main_wrap('System.out.println("before"); int[] a = new int[1]; a[1] = 0;'),
                                    "ArrayIndexOutOfBounds", "index 1 out of bounds for length 1")
        self.assertEqual(r.stdout, "before\n")

    def test_bounds(self):
        self.assertRuntimeError(main_wrap("int[] a = new int[2]; int x = a[-1];"), "ArrayIndexOutOfBounds")
        self.assertRuntimeError(main_wrap('char c = "ab".charAt(2);'), "ArrayIndexOutOfBounds")
        self.assertRuntimeError(main_wrap('String s = "ab".substring(1, 3);'), "ArrayIndexOutOfBounds")
        self.assertRuntimeError(main_wrap("int[] a = new int[-1];"), "NegativeArraySize")

    def test_division_by_zero(self):
        self.assertRuntimeError(main_wrap("int z = 0; int x = 1 / z;"), "DivisionByZero")
        self.assertRuntimeError(main_wrap("int z = 0; int x = 1 % z;"), "DivisionByZero")
        self.assertRuntimeError(main_wrap("long z = 0L; long x = 1L / z;"), "DivisionByZero")
        self.assertRuntimeError(main_wrap("byte z = (byte) 0; byte x = (byte) 1; x /= z;"), "DivisionByZero")

    def test_failed_casts(self):
        self.assertRuntimeError("class A { } class B { }" + main_wrap("Object o = (Object) new A(); B b = (B) o;"),
                                "ClassCast", "cannot cast A to B")
        self.assertRuntimeError(main_wrap('Object o = (Object) "s"; int[] a = (int[]) o;'), "ClassCast")


class GcIntegrationTests(CompilerTestCase):
    def test_many_allocations_with_live_structure(self):
        code = """
        class Node { Node next; Node other; int val; Node(int v, Node n) { val = v; next = n; } }
        """ + main_wrap("""
            Node head = null;
            for (int i = 0; i < 400; i++) {
                head = new Node(i, head);
                Node garbage = new Node(-1, null);
                garbage.other = garbage;            // недостижимый цикл
                head.other = new Node(i * 2, null);
            }
            long total = 0L;
            int count = 0;
            for (Node x = head; x != null; x = x.next) { total += (long) (x.val + x.other.val); count++; }
            System.out.println(count);
            System.out.println(total);
            String s = "";
            for (int i = 0; i < 300; i++) { s = s + String.valueOf(i % 10); }
            System.out.println(s.length());
            System.out.println(s.substring(295, 300));
        """)
        self.assertOutput(code, "400\n239400\n300\n56789\n")

    def test_objects_reachable_only_through_arrays_and_interfaces_survive(self):
        code = """
        interface V { int get(); }
        class A { int a = 1; }
        class B implements V { int b; B(int b) { this.b = b; } public int get() { return b; } }
        class C extends A, B { C(int v) { super.A(); super.B(v); } }
        """ + main_wrap("""
            V[] vs = new V[50];
            for (int i = 0; i < 50; i++) { vs[i] = (V) (B) new C(i); String junk = String.valueOf(i); }
            Object[] os = new Object[1];
            os[0] = (Object) vs;
            int sum = 0;
            for (int i = 0; i < 200; i++) { Object tmp = (Object) new A(); }
            V[] back = (V[]) os[0];
            for (V v : back) sum += v.get();
            System.out.println(sum);
        """)
        self.assertOutput(code, "1225\n")


if __name__ == "__main__":
    unittest.main()
