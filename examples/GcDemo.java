// Точная сборка мусора: полная сборка перед каждой аллокацией, корни — кадры и static-поля,
// мёртвые ссылки обнуляются перед safepoint, циклы освобождаются
class Node {
    Node next;
    Node cycle;
    int value;
    Node(int v, Node n) { value = v; next = n; }
}

class Registry {
    static Node last;            // static-поле — корень GC (globals)
}

public class GcDemo {
    public static void main(String[] args) {
        Node list = null;
        for (int i = 1; i <= 1000; i++) {
            list = new Node(i, list);          // живая структура
            Node a = new Node(-i, null);       // мусор: цикл a <-> b
            Node b = new Node(-i, a);
            a.cycle = b;
        }
        Registry.last = list;
        list = null;
        long sum = 0L;
        for (Node n = Registry.last; n != null; n = n.next) {
            sum += (long) n.value;
        }
        System.out.println("sum=" + String.valueOf(sum));   // 500500
        String s = "";
        for (int i = 0; i < 100; i++) { s = s + String.valueOf(i % 10); }
        System.out.println(s.length());                     // 100
    }
}
