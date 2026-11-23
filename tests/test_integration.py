"""Сквозные тесты: Java → IR → C99 → cc -std=c99 -pedantic-errors -O0/-O2 → запуск → stdout.

Для каждой программы проверяется, что сгенерированный C собирается в режиме
C99 без ошибок и предупреждений на всех уровнях оптимизации, линкуется с
runtime и выдаёт одинаковый ожидаемый результат (helpers.run_java).
"""
import os
import re
import unittest

from tests.helpers import (OPT_LEVELS, CompilerTestCase, build_c, compile_to_dir, main_wrap, run_exe,
                           run_java)


class C99ComplianceTests(unittest.TestCase):
    def test_generated_code_is_portable_c99(self):
        r = run_java(main_wrap('String s = "x"; for (int i = 0; i < 3; i++) { s = s + "y"; } '
                               "System.out.println(s); long a = 1L; a = a * 3L; System.out.println(a);"))
        self.assertEqual(r.stdout, "xyyy\n3\n")
        src = r.c_source
        for bad in ("__attribute__", "__builtin", "asm", "windows.h", "__declspec", "long long",
                    "__GNUC__", "typeof", "alloca", "_MSC_VER"):
            self.assertIsNone(re.search(r"\b" + bad + r"\b", src), bad)
        self.assertTrue(src.isascii())
        # точные корни: явный кадр, никакого сканирования стека
        self.assertIn("rt_frame_push", src)
        self.assertIsNone(re.search(r"setjmp|__builtin_frame_address", src))


class PrimitiveTests(CompilerTestCase):
    def test_int_arithmetic_wraps(self):
        self.assertOutput(main_wrap("""
            int big = 2147483647, small = -2147483648, m1 = -1;
            System.out.println(big + 1);
            System.out.println(small - 1);
            System.out.println(65536 * 65536);
            System.out.println(small / m1);
            System.out.println(small % m1);
            System.out.println(-7 / 2);
            System.out.println(7 / -2);
            System.out.println(-7 % 2);
            System.out.println(7 % -2);
            System.out.println(-small);
            System.out.println(~5);
            System.out.println(6 & 3 | 8 ^ 1);
            System.out.println(2147483647 + 1);
        """), "-2147483648\n2147483647\n0\n-2147483648\n0\n-3\n-3\n-1\n1\n-2147483648\n-6\n11\n-2147483648\n")

    def test_shifts(self):
        self.assertOutput(main_wrap("""
            int one = 1;
            System.out.println(one << 31);
            System.out.println(one << 32);
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
            byte d = (byte) -128; System.out.println(-d);
            byte e = (byte) -128; System.out.println(e / (byte) -1);
        """), "-128\n-56\n-32768\nb\n-128\n-128\n")

    def test_char_is_unicode_scalar_value(self):
        # решение N2: char — скалярное значение Unicode (U+0000..U+10FFFF без суррогатов)
        r = run_java(main_wrap("""
            char c = '\U0001F600';
            System.out.println((int) c);
            System.out.println(c);
            char z = (char) 1114111;
            System.out.println((int) z);
            char x = 'Ж'; x -= (char) 1;
            System.out.println(x);
            System.out.println((char) ('a' + (char) 2));
        """))
        self.assertEqual((r.code, r.stdout), (0, "128512\n\U0001F600\n1114111\nЕ\nc\n"))

    def test_invalid_char_at_runtime(self):
        for body in ("char z = (char) 0; z--;",
                     "char d = (char) 55295; d++;",             # 0xD7FF + 1 = суррогат
                     "int big = 1114112; char c = (char) big;",
                     "char a = (char) 1114111; char b = (char) 1; char s = (char) (a + b);",
                     "double v = -1.0; char c = (char) v;"):
            self.assertRuntimeError(main_wrap(body), "InvalidChar")
        self.assertCompileError(main_wrap("char c = (char) 0xD800;"), "Unicode scalar value")
        self.assertCompileError(main_wrap("char c = (char) ((char) 0 - (char) 1);"), "Unicode scalar value")

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
            double nan = 0.0 / 0.0; double inf = 1.0 / 0.0;
            System.out.println((int) nan);
            System.out.println((long) -inf);
            System.out.println((byte) inf);
        """), "3\n-3\n0\n2147483647\n-2147483648\n9223372036854775807\n-9223372036854775808\n123456789\n44\nA\n"
              "25536\n-1\nInfinity\n0.1\n0.10000000149011612\n9.007199254740992E15\n1.6777216E7\n"
              "9223372036854775807\n66\nA\n0\n-9223372036854775808\n-1\n")

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
            float fa = 16777216.0f, fb = 1.0f;
            System.out.println(fa + fb);
            float tiny = 1.4E-45f;
            System.out.println(tiny / 2.0f);
        """), "Infinity\n-Infinity\nNaN\n-0.0\ntrue\nfalse\ntrue\nfalse\n1.5\n-1.5\nNaN\nInfinity\n"
              "0.30000000000000004\n0.33333334\n110.0\n1.6777216E7\n0.0\n")

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
    def test_string_api_code_points(self):
        self.assertOutput(main_wrap("""
            String s = "Привет";
            System.out.println(s.length());
            System.out.println(s.charAt(1));
            System.out.println(s.substring(1, 4));
            System.out.println(s.substring(6, 6).length());
            String e = "a\U0001F600b";
            System.out.println(e.length());
            System.out.println((int) e.charAt(1));
            System.out.println(e.substring(1, 2));
            System.out.println("ab" == "ab");
            String built = "a" + "b";
            System.out.println(built == "ab");
            String a1 = "a";
            String dyn = a1 + "b";
            System.out.println(dyn == "ab");
            System.out.println(dyn.equals("ab"));
            System.out.println(dyn.equals((Object) "ab"));
            System.out.println(((Object) dyn).equals((Object) "ab"));
            System.out.println("x".equals((String) null));
            String n = null;
            System.out.println("v=" + n);
            String acc = "";
            acc += "1"; acc += "2";
            System.out.println(acc);
            System.out.println(((Object) "s").getClass().getName());
            System.out.println("abc".toString());
            System.out.println("Unicode: \\u00e9 \\u4e16\\u754c");
        """), "6\nр\nрив\n0\n3\n128512\n\U0001F600\ntrue\ntrue\nfalse\ntrue\ntrue\ntrue\nfalse\nv=null\n12\n"
              "java.lang.String\nabc\nUnicode: é 世界\n")

    def test_constant_strings_are_interned(self):
        code = """
        class K { static final String A = "ab"; static final String B = "a" + "b"; static final int N = 4;
                  static final String C = "n"; }
        """ + main_wrap("""
            System.out.println(K.A == K.B);
            System.out.println(K.A == "ab");
            System.out.println(K.C + K.A);
            System.out.println(K.N * 2);
        """)
        self.assertOutput(code, "true\ntrue\nnab\n8\n")

    def test_output_is_utf8(self):
        r = run_java(main_wrap('System.out.println("Ж\U0001F600 ✓"); System.out.println(\'\U0001F600\');'))
        self.assertEqual(r.stdout, "Ж\U0001F600 ✓\n\U0001F600\n")

    def test_args_are_utf8_code_points(self):
        # переносимый rt_args.c ожидает UTF-8 в argv (POSIX); в Windows argv приходит в кодовой
        # странице ANSI, поэтому там подключается необязательный адаптер (решение N3)
        r = run_java(main_wrap("for (String a : args) { System.out.println(a + \":\" + String.valueOf(a.length())); }"
                               "System.out.println(args.length);"), ["abc", "Жук", "\U0001F600", ""],
                     win_args=(os.name == "nt"))
        self.assertEqual(r.stdout, "abc:3\nЖук:3\n\U0001F600:1\n:0\n4\n")


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


class ThrowTests(CompilerTestCase):
    """Решение N6: неперехваченный throw."""

    def test_uncaught_user_exception(self):
        code = """
        class MyEx extends RuntimeException { MyEx(String m) { super(m); } }
        class W { void go(int n) { if (n == 0) throw new MyEx("boom"); go(n - 1); } }
        """ + main_wrap('System.out.println("start"); new W().go(3); System.out.println("never");')
        r = self.assertThrows(code, 'Exception in thread "main" MyEx: boom', "start\n")
        self.assertIn("Test.java:3:", r.stderr)
        self.assertIn("W.go(int)", r.stderr)
        self.assertNotIn("Runtime error", r.stderr)

    def test_builtin_exceptions_and_null_message(self):
        self.assertThrows(main_wrap("throw new RuntimeException();"),
                          'Exception in thread "main" java.lang.RuntimeException')
        self.assertThrows(main_wrap('throw new Exception("x y");'),
                          'Exception in thread "main" java.lang.Exception: x y')
        self.assertThrows(main_wrap('throw new Throwable("Ж ✓");'),
                          'Exception in thread "main" java.lang.Throwable: Ж ✓')

    def test_message_with_nul_is_printed_completely(self):
        # сообщение выводится по длине (fwrite), а не как C-строка до первого нулевого байта
        self.assertThrows(main_wrap('throw new RuntimeException("a\\u0000b ✓");'),
                          'Exception in thread "main" java.lang.RuntimeException: a\x00b ✓')

    def test_overridden_get_message_is_used(self):
        code = """
        class E extends Exception { int code; E(int c) { code = c; }
          public String getMessage() { return "code=" + String.valueOf(code); } }
        """ + main_wrap("""
            E e = new E(7);
            System.out.println(e.toString());
            Throwable t = (Throwable) e;
            System.out.println(t.getMessage());
            throw e;
        """)
        self.assertThrows(code, 'Exception in thread "main" E: code=7', "E: code=7\ncode=7\n")

    def test_throwable_api(self):
        self.assertOutput(main_wrap("""
            RuntimeException r = new RuntimeException("m");
            System.out.println(r.getMessage());
            System.out.println(r.toString());
            System.out.println(new Exception().getMessage());
            System.out.println(new Exception().toString());
            Object o = (Object) r;
            System.out.println(o instanceof Throwable);
            System.out.println(o instanceof Exception);
            System.out.println(o.getClass().getName());
        """), "m\njava.lang.RuntimeException: m\nnull\njava.lang.Exception\ntrue\ntrue\n"
              "java.lang.RuntimeException\n")

    def test_throw_null_is_null_pointer(self):
        self.assertRuntimeError(main_wrap("throw null;"), "NullPointer", "throw null")
        self.assertRuntimeError(main_wrap("RuntimeException r = null; throw r;"), "NullPointer")

    def test_throw_from_constructor_and_static_init(self):
        code = """
        class C { C() { throw new RuntimeException("ctor"); } }
        """ + main_wrap("C c = new C();")
        self.assertThrows(code, 'Exception in thread "main" java.lang.RuntimeException: ctor')
        code = """
        class H { int f() { throw new RuntimeException("init"); } }
        class S { static int v = new H().f(); }
        """ + main_wrap('System.out.println("a"); int x = S.v;')
        self.assertThrows(code, 'Exception in thread "main" java.lang.RuntimeException: init', "a\n")


class StaticTests(CompilerTestCase):
    """Решения N4/N5: ленивая инициализация классов, константы, статические поля."""

    def test_lazy_initialization_order(self):
        code = """
        class Log { static String s = ""; }
        class T { String t(String m) { Log.s = Log.s + m; return m; } }
        class A { static String a = new T().t("A"); static int n; }
        class B extends A { static String b = new T().t("B"); static final int K = 5; }
        class C { static String c = new T().t("C"); }
        """ + main_wrap("""
            System.out.println("[" + Log.s + "]");
            int k = B.K;                         // константа: инициализации нет
            System.out.println("[" + Log.s + "]");
            String x = B.b;                      // сначала родитель A, затем B
            System.out.println("[" + Log.s + "]");
            x = B.b; x = A.a;                    // повторно не инициализируются
            System.out.println("[" + Log.s + "]");
            C[] arr = new C[3];                  // создание массива не инициализирует класс
            System.out.println("[" + Log.s + "]");
            C c = new C();                       // new инициализирует
            System.out.println("[" + Log.s + "]");
        """)
        self.assertOutput(code, "[]\n[]\n[AB]\n[AB]\n[AB]\n[ABC]\n")

    def test_access_to_parent_static_through_subclass_initializes_only_parent(self):
        code = """
        class Log { static String s = ""; }
        class T { int t(String m) { Log.s = Log.s + m; return 1; } }
        class P { static int p = new T().t("P"); }
        class Q extends P { static int q = new T().t("Q"); }
        """ + main_wrap("""
            int v = Q.p;
            System.out.println(Log.s);
            Q obj = new Q();
            System.out.println(Log.s);
        """)
        self.assertOutput(code, "P\nPQ\n")

    def test_static_fields_defaults_and_mutation(self):
        code = """
        class S { static int i; static long l; static double d; static boolean b; static char c;
                  static String str; static Object o; static int[] arr = new int[3];
                  static int counter = 10; }
        class U extends S { int bump() { counter++; return counter; } }
        """ + main_wrap("""
            System.out.println(S.i); System.out.println(S.l); System.out.println(S.d);
            System.out.println(S.b); System.out.println((int) S.c); System.out.println(S.str);
            System.out.println(S.o);
            S.arr[1] = 4;
            System.out.println(S.arr[1] + S.arr.length);
            U u = new U();
            u.bump(); u.bump();
            System.out.println(S.counter);
            S.counter += 5;
            System.out.println(U.counter);
        """)
        self.assertOutput(code, "0\n0\n0.0\nfalse\n0\nnull\nnull\n7\n12\n17\n")

    def test_recursive_initialization_sees_defaults(self):
        code = """
        class A { static int a = B.b + 1; }
        class B { static int b = A.a + 10; }
        """ + main_wrap("System.out.println(A.a); System.out.println(B.b);")
        # Java: A начинает инициализацию, B видит A.a == 0 → B.b = 10, A.a = 11
        self.assertOutput(code, "11\n10\n")

    def test_constant_folding_matches_runtime(self):
        code = """
        class K { static final int A = 2147483647 + 1; static final long L = 1L << 63L;
                  static final double D = 1.0 / 3.0; static final float F = 0.1f * 3.0f;
                  static final char C = (char) ('a' + (char) 1); static final boolean Z = 1 < 2 && 3 > 4;
                  static final int SH = -16 >>> 28; static final byte BY = (byte) 300; }
        """ + main_wrap("""
            int a = 2147483647, one = 1; long l1 = 1L; double d1 = 1.0, d3 = 3.0; float f1 = 0.1f, f3 = 3.0f;
            System.out.println(K.A == a + one);
            System.out.println(K.L == l1 << 63L);
            System.out.println(K.D == d1 / d3);
            System.out.println(K.F == f1 * f3);
            System.out.println(K.C);
            System.out.println(K.Z);
            System.out.println(K.SH);
            System.out.println(K.BY);
        """)
        self.assertOutput(code, "true\ntrue\ntrue\ntrue\nb\nfalse\n15\n44\n")

    def test_static_references_are_gc_roots(self):
        code = """
        class Node { Node next; int v; Node(int v, Node n) { this.v = v; next = n; } }
        class Reg { static Node head; static Object[] keep = new Object[1]; }
        """ + main_wrap("""
            for (int i = 0; i < 2000; i++) { Reg.head = new Node(i, Reg.head); String junk = String.valueOf(i); }
            Reg.keep[0] = (Object) Reg.head;
            Reg.head = null;
            long s = 0L;
            for (int i = 0; i < 3000; i++) { int[] g = new int[16]; }
            for (Node n = (Node) Reg.keep[0]; n != null; n = n.next) s += (long) n.v;
            System.out.println(s);
        """)
        self.assertOutput(code, "1999000\n")


class GcIntegrationTests(CompilerTestCase):
    def test_many_allocations_with_live_structure(self):
        code = """
        class Node { Node next; Node other; int val; Node(int v, Node n) { val = v; next = n; } }
        """ + main_wrap("""
            Node head = null;
            for (int i = 0; i < 4000; i++) {
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
        self.assertOutput(code, "4000\n23994000\n300\n56789\n")

    def test_objects_reachable_only_through_arrays_and_interfaces_survive(self):
        code = """
        interface V { int get(); }
        class A { int a = 1; }
        class B extends A implements V { int b; B(int b) { this.b = b; } public int get() { return b + a - 1; } }
        """ + main_wrap("""
            V[] vs = new V[50];
            for (int i = 0; i < 50; i++) { vs[i] = (V) new B(i); String junk = String.valueOf(i); }
            Object[] os = new Object[1];
            os[0] = (Object) vs;
            int sum = 0;
            for (int i = 0; i < 2000; i++) { Object tmp = (Object) new A(); }
            V[] back = (V[]) os[0];
            for (V v : back) sum += v.get();
            System.out.println(sum);
        """)
        self.assertOutput(code, "1225\n")

    def test_temporaries_survive_collection_inside_expression(self):
        # промежуточные значения выражений тоже лежат в слотах кадра
        code = """
        class P { String s; P(String s) { this.s = s; } String cat(P o) { return s + o.s; } }
        class Mk { P mk(String s) { for (int i = 0; i < 300; i++) { int[] g = new int[64]; } return new P(s); } }
        """ + main_wrap("""
            Mk m = new Mk();
            String r = "";
            for (int i = 0; i < 20; i++) r = m.mk("a").cat(m.mk(String.valueOf(i % 10))) + r.substring(0, 0);
            System.out.println(r);
            System.out.println(m.mk("x").s + m.mk("y").s + m.mk("z").s);
        """)
        self.assertOutput(code, "a9\nxyz\n")


class PreciseLivenessTests(unittest.TestCase):
    """Точные корни + обнуление мёртвых слотов перед safepoint: число живых
    объектов после каждой сборки известно заранее и одинаково на -O0/-O2."""

    def gc_samples(self, code: str) -> tuple[str, list[int]]:
        d, c_path, _ = compile_to_dir(code)
        probe = os.path.join(os.path.dirname(os.path.abspath(__file__)), "c", "gc_probe.c")
        results = []
        for opt in OPT_LEVELS:
            exe = os.path.join(d, f"probe_O{opt}.exe")
            r = build_c([c_path, probe], exe, d, opt, with_main=False)
            self.assertFalse(r.returncode or r.stderr.strip(), r.stderr)
            out, err, rc = run_exe(exe)
            self.assertEqual(rc, 0, err)
            results.append(out)
        self.assertEqual(len(set(results)), 1, results)
        text, gc_line = results[0].rsplit("gc:", 1)
        return text, [int(x) for x in gc_line.split()]

    def test_dead_locals_are_released_exactly(self):
        code = """
        class N { N next; int v; }
        """ + main_wrap("""
            N a = new N();          // #1: {args}
            N b = new N();          // #2: {args, a}
            b.next = a;
            N c = new N();          // #3: a мёртв как переменная, но достижим через b: {args, b, a}
            c.next = b;
            N d = new N();          // #4: b мёртв, c жив: {args, c, b, a}
            c.next = null;
            N e = new N();          // #5: {args, c, d}
            System.out.println(c == d);
            System.out.println(e == null);
            N f = new N();          // #6: всё мертво: {args}
            f.v = 1;
        """)
        out, samples = self.gc_samples(code)
        self.assertEqual(out, "false\nfalse\n")
        self.assertEqual(samples, [1, 2, 3, 4, 3, 1])

    def test_loop_garbage_does_not_accumulate(self):
        code = """
        class N { N next; }
        """ + main_wrap("""
            N keep = new N();
            for (int i = 0; i < 5; i++) {
                N tmp = new N();
                tmp.next = keep;
            }
            System.out.println(keep.next == null);
        """)
        out, samples = self.gc_samples(code)
        self.assertEqual(out, "true\n")
        # перед каждой аллокацией в цикле: args + keep (tmp предыдущей итерации мёртв)
        self.assertEqual(samples, [1, 2, 2, 2, 2, 2])

    def test_static_fields_and_temporaries(self):
        code = """
        class N { N next; }
        class G { static N root; }
        class Mk { N two() { N x = new N(); x.next = new N(); return x; } }
        """ + main_wrap("""
            G.root = new Mk().two();    // Mk, N, N
            Mk m = new Mk();
            G.root = null;
            N z = new N();
            System.out.println(z.next == null);
        """)
        out, samples = self.gc_samples(code)
        self.assertEqual(out, "true\n")
        # new Mk: {args}; x: {args, Mk}; x.next: {args, Mk, x}; new Mk: {args, root, root.next};
        # new N (z): {args, ...} — m мёртв, root обнулён
        self.assertEqual(samples, [1, 2, 3, 3, 1])


class ExamplesTests(CompilerTestCase):
    """Примеры из examples/ компилируются и работают одинаково на всех уровнях оптимизации."""
    EXPECTED = {
        "Hello": (0, "Привет, мир! Hello, 世界 😀\nдлина в кодовых точках: 24\nаргументов: 0\n"),
        "GcDemo": (0, "sum=500500\n100\n"),
        "Shapes": (0, "circle area=3.141592653589793\n[rect area=7.0]\ncircle area=12.566370614359172\n"
                      "total=22.707963267948966\nfalse\nRect\nrect\n"),
        "Bank": (1, "Аня: 70 ₽\nопераций: 1\n"),
    }

    def test_examples(self):
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")
        names = sorted(f[:-5] for f in os.listdir(root) if f.endswith(".java"))
        self.assertEqual(names, ["Bank", "GcDemo", "Hello", "Numbers", "Shapes"])
        for name in names:
            with self.subTest(example=name):
                with open(os.path.join(root, name + ".java"), encoding="utf-8") as fh:
                    r = run_java(fh.read())
                if name in self.EXPECTED:
                    self.assertEqual((r.code, r.stdout), self.EXPECTED[name], r.stderr)
                else:
                    self.assertEqual(r.code, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
