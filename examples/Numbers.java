// Примитивы без неявных преобразований, явные приведения, IEEE 754, char как скалярное значение Unicode
public class Numbers {
    static final int MAX = 2147483647;
    static final long WIDE = (long) MAX + 1L;      // константа времени компиляции

    public static void main(String[] args) {
        int big = MAX;
        System.out.println(big + 1);                       // -2147483648
        System.out.println(WIDE);                          // 2147483648
        byte b = (byte) 200;
        System.out.println(b);                             // -56
        char c = 'A';
        c += (char) 2;
        System.out.println(c);                             // C
        char smile = '😀';
        System.out.println((int) smile);                   // 128512
        double d = 1.0 / 0.0;
        System.out.println(d);                             // Infinity
        System.out.println((int) (0.0 / 0.0));             // 0
        System.out.println((long) 1e30);                   // 9223372036854775807
        System.out.println(-7 / 2 + -7 % 2);               // -4
        System.out.println(-16 >>> 28);                    // 15
        System.out.println(0.1 + 0.2);                     // 0.30000000000000004
        System.out.println(1.0f / 3.0f);                   // 0.33333334
        System.out.println(Math.sqrt(2.0));                // 1.4142135623730951
        // int x = 5L;          — ошибка компиляции: неявных преобразований нет
        // char s = (char) 0xD800; — ошибка компиляции: суррогат не является скалярным значением
    }
}
