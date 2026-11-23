"""Встроенные классы исключений, описанные на поддерживаемом подмножестве Java.

Раздел 13.4 ТЗ: Throwable, Exception, RuntimeException с конструкторами
() и (String), getMessage() и toString(). Классы компилируются вместе с
программой пользователя тем же конвейером (лексер → ... → C99), поэтому
пользовательские наследники, виртуальные вызовы и GC работают для них
без особых случаев. Имена для getClass().getName() — java.lang.*.
"""
from __future__ import annotations

from .lexer import tokenize
from .parser import parse
from .source import SourceFile

PRELUDE_FILE = "<prelude>"

PRELUDE_SOURCE = """\
public class Throwable {
    private String message;

    public Throwable() {
        super();
    }

    public Throwable(String message) {
        super();
        this.message = message;
    }

    public String getMessage() {
        return message;
    }

    public String toString() {
        String name = getClass().getName();
        String m = getMessage();
        if (m == null) {
            return name;
        }
        return name + ": " + m;
    }
}

public class Exception extends Throwable {
    public Exception() {
        super();
    }

    public Exception(String message) {
        super(message);
    }
}

public class RuntimeException extends Exception {
    public RuntimeException() {
        super();
    }

    public RuntimeException(String message) {
        super(message);
    }
}
"""

PRELUDE_CLASSES = ("Throwable", "Exception", "RuntimeException")


def prelude_source() -> SourceFile:
    return SourceFile(PRELUDE_FILE, PRELUDE_SOURCE)


def prelude_unit():
    """AST встроенных классов (разбирается заново для каждой компиляции)."""
    return parse(tokenize(prelude_source()), PRELUDE_FILE)
