// Статические поля, ленивая инициализация классов, исключения (throw без перехвата)
class Ledger {
    static final String CURRENCY = "₽";              // константа: не инициализирует класс
    static int operations;                           // счётчик в статическом поле
    static int[] history = new int[8];               // выполняется при первой инициализации Ledger
}

class InsufficientFunds extends RuntimeException {
    private int missing;
    InsufficientFunds(int missing) { super("не хватает " + String.valueOf(missing)); this.missing = missing; }
    int missing() { return missing; }
}

class Account {
    private final String owner;
    private int balance;
    Account(String owner, int balance) { this.owner = owner; this.balance = balance; }

    void withdraw(int amount) {
        Ledger.history[Ledger.operations % Ledger.history.length] = amount;
        Ledger.operations++;
        if (amount > balance) throw new InsufficientFunds(amount - balance);
        balance -= amount;
    }

    public String toString() { return owner + ": " + String.valueOf(balance) + " " + Ledger.CURRENCY; }
}

public class Bank {
    public static void main(String[] args) {
        Account a = new Account("Аня", 100);
        a.withdraw(30);
        System.out.println((Object) a);
        System.out.println("операций: " + String.valueOf(Ledger.operations));
        a.withdraw(500);                            // Exception in thread "main" InsufficientFunds: ...
        System.out.println("сюда не дойдём");
    }
}
