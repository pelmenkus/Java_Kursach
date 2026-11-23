// Привет, мир: исходник UTF-8, строки из кодовых точек, аргументы и вывод в UTF-8
public class Hello {
    static final String GREETING = "Привет, мир! Hello";

    public static void main(String[] args) {
        System.out.println(GREETING);
        System.out.println("длина в кодовых точках: " + String.valueOf(GREETING.length()));
        System.out.println("аргументов: " + String.valueOf(args.length));
        for (String a : args) {
            System.out.println("  " + a + " (кодовых точек: " + String.valueOf(a.length()) + ")");
        }
    }
}
