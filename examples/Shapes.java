// Одиночное наследование, интерфейсы, абстрактные классы, массивы интерфейсных ссылок,
// явные приведения (в том числе к родителю и интерфейсу)
interface Shape { double area(); String name(); }
interface Scalable { Shape scaled(double k); }

abstract class Base implements Shape {
    protected String title;
    Base(String t) { title = t; }
    public String name() { return title; }
    public String toString() { return name() + " area=" + String.valueOf(area()); }
}

final class Circle extends Base implements Scalable {
    private double r;
    Circle(double r) { super("circle"); this.r = r; }
    public double area() { return Math.PI * r * r; }
    public Shape scaled(double k) { return (Shape) new Circle(r * k); }
}

final class Rect extends Base {
    private double w, h;
    Rect(double w, double h) { super("rect"); this.w = w; this.h = h; }
    public double area() { return w * h; }
    public String toString() { return "[" + super.toString() + "]"; }
}

public class Shapes {
    public static void main(String[] args) {
        Shape[] shapes = new Shape[3];
        shapes[0] = (Shape) new Circle(1.0);
        shapes[1] = (Shape) new Rect(2.0, 3.5);
        shapes[2] = ((Scalable) new Circle(1.0)).scaled(2.0);
        double total = 0.0;
        for (Shape s : shapes) {
            System.out.println((Object) s);
            total += s.area();
        }
        System.out.println("total=" + String.valueOf(total));
        Object o = (Object) shapes[1];
        System.out.println(o instanceof Scalable);
        System.out.println(o.getClass().getName());
        Base base = (Base) o;
        System.out.println(base.name());
    }
}
