"""Синтаксический анализ: рекурсивный спуск по грамматике README §9.

Парсер строит AST и останавливается на первой синтаксической ошибке.
Семантику (что такое `a.b.c`, допустимость модификаторов в контексте и т.п.)
он не проверяет, за исключением ограничений, выраженных самой грамматикой.
"""
from __future__ import annotations

from . import ast_nodes as A
from .errors import ParseError
from .tokens import PRIMITIVE_KEYWORDS, RESERVED, Token, TokenKind

MEMBER_MODIFIERS = ("public", "protected", "private", "abstract", "final", "static")
CLASS_MODIFIERS = ("public", "abstract", "final")
ASSIGN_OPS = ("=", "+=", "-=", "*=", "/=", "%=")
UNSUPPORTED_ASSIGN_OPS = ("&=", "|=", "^=", "<<=", ">>=", ">>>=")
LITERAL_KINDS = {
    TokenKind.INT_LIT: "int", TokenKind.LONG_LIT: "long", TokenKind.FLOAT_LIT: "float",
    TokenKind.DOUBLE_LIT: "double", TokenKind.CHAR_LIT: "char", TokenKind.STRING_LIT: "string",
    TokenKind.BOOL_LIT: "boolean", TokenKind.NULL_LIT: "null",
}
INT_MIN_MAGNITUDE = 1 << 31
LONG_MIN_MAGNITUDE = 1 << 63


class Parser:
    def __init__(self, tokens: list[Token], file: str = ""):
        self.toks = tokens
        self.k = 0
        self.file = file

    # ------------------------------------------------------------ helpers
    def peek(self, n: int = 0) -> Token:
        j = min(self.k + n, len(self.toks) - 1)
        return self.toks[j]

    def advance(self) -> Token:
        tok = self.toks[self.k]
        if tok.kind is not TokenKind.EOF:
            self.k += 1
        return tok

    def error(self, message: str, tok: Token | None = None) -> ParseError:
        tok = tok or self.peek()
        return ParseError(message, tok.pos, length=max(1, len(tok.text)))

    def expect_op(self, op: str, context: str = "") -> Token:
        tok = self.peek()
        if not tok.is_op(op):
            where = f" {context}" if context else ""
            raise self.error(f"ожидается '{op}'{where}, найдено {tok.describe()}")
        return self.advance()

    def expect_kw(self, kw: str) -> Token:
        tok = self.peek()
        if not tok.is_kw(kw):
            raise self.error(f"ожидается '{kw}', найдено {tok.describe()}")
        return self.advance()

    def expect_ident(self, what: str = "идентификатор") -> Token:
        tok = self.peek()
        if tok.kind is not TokenKind.IDENT:
            if tok.kind is TokenKind.KEYWORD:
                raise self.error(f"ожидается {what}, найдено ключевое слово '{tok.text}'")
            raise self.error(f"ожидается {what}, найдено {tok.describe()}")
        return self.advance()

    def unsupported(self, what: str, tok: Token | None = None) -> ParseError:
        return self.error(f"{what} не поддерживается", tok)

    def check_reserved(self, tok: Token) -> None:
        if tok.kind is TokenKind.KEYWORD and tok.text in RESERVED:
            raise self.unsupported(f"конструкция '{tok.text}'", tok)

    # ------------------------------------------------------------ compilation unit
    def parse_unit(self) -> A.CompilationUnit:
        start = self.peek().pos
        types: list[A.Node] = []
        while self.peek().kind is not TokenKind.EOF:
            types.append(self.parse_type_decl())
        return A.CompilationUnit(start, types)

    def parse_modifiers(self, allowed: tuple[str, ...]) -> list[A.Modifier]:
        mods: list[A.Modifier] = []
        while True:
            tok = self.peek()
            if tok.is_op("@"):
                raise self.unsupported("аннотации", tok)
            if tok.kind is TokenKind.KEYWORD and tok.text in ("native", "synchronized", "transient",
                                                              "volatile", "strictfp", "default"):
                raise self.unsupported(f"модификатор '{tok.text}'", tok)
            if tok.kind is not TokenKind.KEYWORD or tok.text not in MEMBER_MODIFIERS:
                return mods
            if tok.text not in allowed:
                raise self.error(f"модификатор '{tok.text}' здесь недопустим", tok)
            if any(m.name == tok.text for m in mods):
                raise self.error(f"повторный модификатор '{tok.text}'", tok)
            self.advance()
            mods.append(A.Modifier(tok.pos, tok.text))

    def parse_type_decl(self) -> A.Node:
        tok = self.peek()
        if tok.is_kw("package", "import"):
            raise self.unsupported(f"'{tok.text}' (пакеты и импорт)", tok)
        if tok.is_kw("enum"):
            raise self.unsupported("enum", tok)
        start = tok.pos
        mods = self.parse_modifiers(CLASS_MODIFIERS)
        tok = self.peek()
        if tok.is_kw("class"):
            return self.parse_class(start, mods)
        if tok.is_kw("interface"):
            for m in mods:
                if m.name != "public":
                    raise ParseError(f"модификатор '{m.name}' недопустим для интерфейса", m.pos)
            return self.parse_interface(start, mods)
        self.check_reserved(tok)
        raise self.error(f"ожидается объявление класса или интерфейса, найдено {tok.describe()}")

    def parse_type_name_list(self) -> list[A.TypeRef]:
        names = [self.parse_class_type_ref()]
        while self.peek().is_op(","):
            self.advance()
            names.append(self.parse_class_type_ref())
        return names

    def parse_class_type_ref(self) -> A.TypeRef:
        tok = self.expect_ident("имя типа")
        if self.peek().is_op("<"):
            raise self.unsupported("обобщённые типы", self.peek())
        if self.peek().is_op("."):
            raise self.unsupported("составные имена типов (пакеты, вложенные типы)", self.peek())
        return A.TypeRef(tok.pos, tok.text, False)

    def parse_class(self, start, mods) -> A.ClassDecl:
        self.expect_kw("class")
        name = self.expect_ident("имя класса")
        parents: list[A.TypeRef] = []
        interfaces: list[A.TypeRef] = []
        if self.peek().is_op("<"):
            raise self.unsupported("обобщённые классы", self.peek())
        if self.peek().is_kw("extends"):
            self.advance()
            parents = self.parse_type_name_list()
        if self.peek().is_kw("implements"):
            self.advance()
            interfaces = self.parse_type_name_list()
        self.expect_op("{", "в начале тела класса")
        members: list[A.Node] = []
        while not self.peek().is_op("}"):
            if self.peek().kind is TokenKind.EOF:
                raise self.error("незакрытое тело класса: ожидается '}'")
            members.append(self.parse_member(name.text))
        self.advance()
        return A.ClassDecl(start, mods, name.text, parents, interfaces, members, self.file)

    def parse_interface(self, start, mods) -> A.InterfaceDecl:
        self.expect_kw("interface")
        name = self.expect_ident("имя интерфейса")
        supers: list[A.TypeRef] = []
        if self.peek().is_kw("extends"):
            self.advance()
            supers = self.parse_type_name_list()
        if self.peek().is_kw("implements"):
            raise self.error("интерфейс не может использовать implements; используйте extends")
        self.expect_op("{", "в начале тела интерфейса")
        methods: list[A.MethodDecl] = []
        while not self.peek().is_op("}"):
            if self.peek().kind is TokenKind.EOF:
                raise self.error("незакрытое тело интерфейса: ожидается '}'")
            methods.append(self.parse_interface_method())
        self.advance()
        return A.InterfaceDecl(start, mods, name.text, supers, methods, self.file)

    def parse_interface_method(self) -> A.MethodDecl:
        start = self.peek().pos
        tok = self.peek()
        if tok.is_kw("default", "static", "private", "protected", "final"):
            raise self.unsupported(f"'{tok.text}' в интерфейсе (допустимы только абстрактные методы)", tok)
        mods = self.parse_modifiers(("public", "abstract"))
        ret = self.parse_result_type()
        name = self.expect_ident("имя метода")
        if not self.peek().is_op("("):
            raise self.unsupported("поля в интерфейсах", name)
        params = self.parse_params()
        if self.peek().is_op("{"):
            raise self.unsupported("реализация методов в интерфейсе", self.peek())
        self.expect_op(";", "после объявления метода интерфейса")
        return A.MethodDecl(start, mods, ret, name.text, params, None)

    # ------------------------------------------------------------ members
    def parse_member(self, class_name: str) -> A.Node:
        start = self.peek().pos
        tok = self.peek()
        if tok.is_op("{"):
            raise self.unsupported("блоки инициализации", tok)
        if tok.is_kw("class", "interface", "enum"):
            raise self.unsupported("вложенные типы", tok)
        mods = self.parse_modifiers(MEMBER_MODIFIERS)
        tok = self.peek()
        if tok.is_op("{"):
            raise self.unsupported("блоки инициализации", tok)
        if tok.is_kw("class", "interface", "enum"):
            raise self.unsupported("вложенные типы", tok)
        if tok.is_op("<"):
            raise self.unsupported("обобщённые методы", tok)
        if tok.kind is TokenKind.IDENT and tok.text == class_name and self.peek(1).is_op("("):
            self.advance()
            params = self.parse_params()
            if not self.peek().is_op("{"):
                raise self.error("ожидается тело конструктора '{'")
            body = self.parse_block()
            return A.ConstructorDecl(start, mods, tok.text, params, body)
        ret = self.parse_result_type()
        name = self.expect_ident("имя поля или метода")
        if self.peek().is_op("("):
            params = self.parse_params()
            if self.peek().is_kw("throws"):
                raise self.unsupported("throws", self.peek())
            if self.peek().is_op(";"):
                self.advance()
                return A.MethodDecl(start, mods, ret, name.text, params, None)
            if not self.peek().is_op("{"):
                raise self.error(f"ожидается тело метода '{{' или ';', найдено {self.peek().describe()}")
            body = self.parse_block()
            return A.MethodDecl(start, mods, ret, name.text, params, body, body.end_pos)
        if ret.name == "void":
            raise self.error("поле не может иметь тип void", name)
        declarators = [self.parse_declarator_rest(name)]
        while self.peek().is_op(","):
            self.advance()
            declarators.append(self.parse_declarator_rest(self.expect_ident("имя поля")))
        self.expect_op(";", "после объявления поля")
        return A.FieldDecl(start, mods, ret, declarators)

    def parse_declarator_rest(self, name: Token) -> A.VarDeclarator:
        if self.peek().is_op("["):
            raise self.unsupported("синтаксис 'имя[]' (используйте 'Тип[] имя')", self.peek())
        init = None
        if self.peek().is_op("="):
            self.advance()
            if self.peek().is_op("{"):
                raise self.unsupported("литералы массивов {...}", self.peek())
            init = self.parse_expr()
        return A.VarDeclarator(name.pos, name.text, init)

    def parse_params(self) -> list[A.Param]:
        self.expect_op("(")
        params: list[A.Param] = []
        if not self.peek().is_op(")"):
            while True:
                tok = self.peek()
                if tok.is_kw("final"):
                    raise self.unsupported("модификатор final у параметра", tok)
                ptype = self.parse_type()
                if self.peek().is_op("..."):
                    raise self.unsupported("переменное число аргументов", self.peek())
                pname = self.expect_ident("имя параметра")
                if self.peek().is_op("["):
                    raise self.unsupported("синтаксис 'имя[]' (используйте 'Тип[] имя')", self.peek())
                params.append(A.Param(ptype.pos, ptype, pname.text))
                if self.peek().is_op(","):
                    self.advance()
                    continue
                break
        self.expect_op(")", "после списка параметров")
        return params

    # ------------------------------------------------------------ types
    def at_primitive(self, n: int = 0) -> bool:
        tok = self.peek(n)
        return tok.kind is TokenKind.KEYWORD and tok.text in PRIMITIVE_KEYWORDS

    def parse_result_type(self) -> A.TypeRef:
        tok = self.peek()
        if tok.is_kw("void"):
            self.advance()
            return A.TypeRef(tok.pos, "void", True)
        return self.parse_type()

    def parse_type(self) -> A.TypeRef:
        tok = self.peek()
        if self.at_primitive():
            self.advance()
            ref = A.TypeRef(tok.pos, tok.text, True)
        elif tok.kind is TokenKind.IDENT:
            ref = self.parse_class_type_ref()
        elif tok.is_kw("void"):
            raise self.error("тип void здесь недопустим", tok)
        else:
            self.check_reserved(tok)
            raise self.error(f"ожидается тип, найдено {tok.describe()}")
        if self.peek().is_op("["):
            self.advance()
            self.expect_op("]", "в типе массива")
            ref.is_array = True
            if self.peek().is_op("["):
                raise self.unsupported("многомерные массивы", self.peek())
        return ref

    # ------------------------------------------------------------ statements
    def parse_block(self) -> A.Block:
        open_tok = self.expect_op("{")
        stmts: list[A.Stmt] = []
        while not self.peek().is_op("}"):
            if self.peek().kind is TokenKind.EOF:
                raise self.error("незакрытый блок: ожидается '}'")
            stmts.append(self.parse_block_stmt())
        close = self.advance()
        return A.Block(open_tok.pos, stmts, close.pos)

    def is_local_decl_start(self) -> bool:
        tok = self.peek()
        if self.at_primitive():
            return True
        if tok.kind is TokenKind.IDENT:
            nxt = self.peek(1)
            if nxt.kind is TokenKind.IDENT:
                return True
            if nxt.is_op("[") and self.peek(2).is_op("]"):
                return True
            if nxt.is_op("<") and self.peek(2).kind is TokenKind.IDENT and self.peek(3).is_op(">"):
                raise self.unsupported("обобщённые типы", nxt)
        return False

    def parse_block_stmt(self) -> A.Stmt:
        tok = self.peek()
        if tok.is_kw("final"):
            raise self.unsupported("модификатор final у локальной переменной", tok)
        if tok.is_kw("class", "interface", "abstract"):
            raise self.unsupported("локальные классы", tok)
        if self.is_local_decl_start():
            decl = self.parse_local_decl()
            self.expect_op(";", "после объявления переменной")
            return decl
        return self.parse_statement()

    def parse_local_decl(self) -> A.LocalVarDecl:
        ltype = self.parse_type()
        name = self.expect_ident("имя переменной")
        return self.parse_local_decl_rest(ltype, name)

    def parse_local_decl_rest(self, ltype: A.TypeRef, name: Token) -> A.LocalVarDecl:
        declarators = [self.parse_declarator_rest(name)]
        while self.peek().is_op(","):
            self.advance()
            declarators.append(self.parse_declarator_rest(self.expect_ident("имя переменной")))
        return A.LocalVarDecl(ltype.pos, ltype, declarators)

    def parse_statement(self) -> A.Stmt:
        tok = self.peek()
        pos = tok.pos
        if tok.is_op("{"):
            return self.parse_block()
        if tok.is_op(";"):
            self.advance()
            return A.EmptyStmt(pos)
        if tok.is_kw("if"):
            self.advance()
            self.expect_op("(", "после if")
            cond = self.parse_expr()
            self.expect_op(")", "после условия if")
            then = self.parse_statement_body()
            els = None
            if self.peek().is_kw("else"):
                self.advance()
                els = self.parse_statement_body()
            return A.If(pos, cond, then, els)
        if tok.is_kw("while"):
            self.advance()
            self.expect_op("(", "после while")
            cond = self.parse_expr()
            self.expect_op(")", "после условия while")
            return A.While(pos, cond, self.parse_statement_body())
        if tok.is_kw("for"):
            return self.parse_for()
        if tok.is_kw("break", "continue"):
            self.advance()
            if self.peek().kind is TokenKind.IDENT:
                raise self.unsupported("метки", self.peek())
            self.expect_op(";", f"после {tok.text}")
            return A.Break(pos) if tok.text == "break" else A.Continue(pos)
        if tok.is_kw("return"):
            self.advance()
            value = None
            if not self.peek().is_op(";"):
                value = self.parse_expr()
            self.expect_op(";", "после return")
            return A.Return(pos, value)
        if tok.is_kw("super") and self.peek(1).is_op(".") and self.peek(2).kind is TokenKind.IDENT \
                and self.peek(3).is_op("("):
            self.advance()
            self.advance()
            parent = self.advance()
            args = self.parse_arguments()
            self.expect_op(";", "после вызова конструктора родителя")
            return A.SuperCtorCall(pos, parent.text, args)
        if tok.is_kw("super") and self.peek(1).is_op("("):
            raise self.error("вызов super(...) без имени родителя не поддерживается; используйте super.Родитель(...)")
        if tok.is_kw("this") and self.peek(1).is_op("("):
            raise self.unsupported("вызов this(...) из конструктора", tok)
        if tok.is_kw("else"):
            raise self.error("'else' без 'if'")
        if tok.kind is TokenKind.IDENT and self.peek(1).is_op(":"):
            raise self.unsupported("метки", tok)
        self.check_reserved(tok)
        expr = self.parse_expr()
        self.check_statement_expr(expr)
        self.expect_op(";", "после выражения")
        return A.ExprStmt(pos, expr)

    def parse_statement_body(self) -> A.Stmt:
        tok = self.peek()
        if self.is_local_decl_start() or tok.is_kw("final"):
            raise self.error("объявление переменной здесь недопустимо (нужен блок {...})", tok)
        return self.parse_statement()

    def check_statement_expr(self, expr: A.Expr) -> None:
        ok = isinstance(expr, (A.Assign, A.IncDec, A.MethodCall, A.SuperMethodCall, A.New))
        if not ok or expr.parenthesized:
            raise ParseError("выражение не является оператором (допустимы присваивание, ++/--, вызов, new)",
                             expr.pos)

    def parse_for(self) -> A.Stmt:
        pos = self.advance().pos
        self.expect_op("(", "после for")
        init: list[A.Stmt] = []
        if self.peek().is_kw("final"):
            raise self.unsupported("модификатор final у локальной переменной", self.peek())
        if self.is_local_decl_start():
            ltype = self.parse_type()
            name = self.expect_ident("имя переменной")
            if self.peek().is_op(":"):
                self.advance()
                iterable = self.parse_expr()
                self.expect_op(")", "после заголовка for")
                return A.ForEach(pos, ltype, name.text, iterable, self.parse_statement_body())
            init.append(self.parse_local_decl_rest(ltype, name))
        elif not self.peek().is_op(";"):
            for e in self.parse_stmt_expr_list():
                init.append(A.ExprStmt(e.pos, e))
        self.expect_op(";", "в заголовке for")
        cond = None
        if not self.peek().is_op(";"):
            cond = self.parse_expr()
        self.expect_op(";", "в заголовке for")
        update: list[A.Expr] = []
        if not self.peek().is_op(")"):
            update = self.parse_stmt_expr_list()
        self.expect_op(")", "после заголовка for")
        return A.For(pos, init, cond, update, self.parse_statement_body())

    def parse_stmt_expr_list(self) -> list[A.Expr]:
        exprs = [self.parse_expr()]
        self.check_statement_expr(exprs[0])
        while self.peek().is_op(","):
            self.advance()
            e = self.parse_expr()
            self.check_statement_expr(e)
            exprs.append(e)
        return exprs

    # ------------------------------------------------------------ expressions
    def parse_expr(self) -> A.Expr:
        lhs = self.parse_conditional()
        tok = self.peek()
        if tok.kind is TokenKind.OP and tok.text in UNSUPPORTED_ASSIGN_OPS:
            raise self.unsupported(f"оператор '{tok.text}'", tok)
        if tok.kind is TokenKind.OP and tok.text in ASSIGN_OPS:
            self.check_lvalue(lhs)
            self.advance()
            if self.peek().is_op("{"):
                raise self.unsupported("литералы массивов {...}", self.peek())
            rhs = self.parse_expr()
            return A.Assign(tok.pos, tok.text, lhs, rhs)
        if tok.is_op("->"):
            raise self.unsupported("лямбда-выражения", tok)
        return lhs

    def check_lvalue(self, e: A.Expr) -> None:
        if not isinstance(e, (A.Name, A.FieldAccess, A.ArrayIndex)) or e.parenthesized:
            raise ParseError("левая часть присваивания должна быть переменной, полем или элементом массива",
                             e.pos)

    def parse_conditional(self) -> A.Expr:
        cond = self.parse_binary(0)
        if self.peek().is_op("?"):
            q = self.advance()
            then = self.parse_expr()
            self.expect_op(":", "в условном операторе")
            els = self.parse_conditional()
            return A.Conditional(q.pos, cond, then, els)
        return cond

    # уровни приоритета бинарных операций, от низкого к высокому
    LEVELS = (("||",), ("&&",), ("|",), ("^",), ("&",), ("==", "!="),
              ("<", ">", "<=", ">=", "instanceof"), ("<<", ">>", ">>>"), ("+", "-"), ("*", "/", "%"))

    def parse_binary(self, level: int) -> A.Expr:
        if level == len(self.LEVELS):
            return self.parse_unary()
        ops = self.LEVELS[level]
        left = self.parse_binary(level + 1)
        while True:
            tok = self.peek()
            if "instanceof" in ops and tok.is_kw("instanceof"):
                self.advance()
                ttype = self.parse_type()
                left = A.InstanceOf(tok.pos, left, ttype)
                continue
            if tok.kind is not TokenKind.OP or tok.text not in ops:
                return left
            self.advance()
            right = self.parse_binary(level + 1)
            if tok.text in ("&&", "||"):
                left = A.Logical(tok.pos, tok.text, left, right)
            else:
                left = A.Binary(tok.pos, tok.text, left, right)

    def parse_unary(self) -> A.Expr:
        tok = self.peek()
        if tok.is_op("-"):
            nxt = self.peek(1)
            if (nxt.kind is TokenKind.INT_LIT and nxt.value == INT_MIN_MAGNITUDE) or \
                    (nxt.kind is TokenKind.LONG_LIT and nxt.value == LONG_MIN_MAGNITUDE):
                self.advance()
                self.advance()
                kind = "int" if nxt.kind is TokenKind.INT_LIT else "long"
                return self.parse_postfix_ops(A.Literal(tok.pos, kind, -nxt.value))
        if tok.is_op("+", "-"):
            self.advance()
            return A.Unary(tok.pos, tok.text, self.parse_unary())
        if tok.is_op("++", "--"):
            self.advance()
            target = self.parse_unary()
            self.check_lvalue(target)
            return A.IncDec(tok.pos, tok.text, True, target)
        return self.parse_unary_not_pm()

    def parse_unary_not_pm(self) -> A.Expr:
        tok = self.peek()
        if tok.is_op("!", "~"):
            self.advance()
            return A.Unary(tok.pos, tok.text, self.parse_unary())
        if tok.is_op("("):
            cast = self.try_parse_cast()
            if cast is not None:
                return cast
        return self.parse_postfix()

    def starts_operand(self, tok: Token) -> bool:
        if tok.kind in (TokenKind.IDENT, TokenKind.INT_LIT, TokenKind.LONG_LIT, TokenKind.FLOAT_LIT,
                        TokenKind.DOUBLE_LIT, TokenKind.CHAR_LIT, TokenKind.STRING_LIT,
                        TokenKind.BOOL_LIT, TokenKind.NULL_LIT):
            return True
        return tok.is_op("(", "!", "~") or tok.is_kw("this", "new", "super")

    def try_parse_cast(self) -> A.Expr | None:
        open_tok = self.peek()
        first = self.peek(1)
        is_prim = first.kind is TokenKind.KEYWORD and first.text in PRIMITIVE_KEYWORDS
        if not is_prim and first.kind is not TokenKind.IDENT:
            return None
        k = 2
        is_array = False
        if self.peek(2).is_op("[") and self.peek(3).is_op("]"):
            is_array = True
            k = 4
            if self.peek(4).is_op("["):
                raise self.unsupported("многомерные массивы", self.peek(4))
        if not self.peek(k).is_op(")"):
            if is_prim:
                raise self.error(f"ожидается ')' в приведении типа, найдено {self.peek(k).describe()}")
            return None
        if not is_prim and not self.starts_operand(self.peek(k + 1)):
            return None
        ttype = A.TypeRef(first.pos, first.text, is_prim, is_array)
        for _ in range(k + 1):      # '(' Тип ['[' ']'] ')'
            self.advance()
        if is_prim and not is_array:
            operand = self.parse_unary()
        else:
            operand = self.parse_unary_not_pm()
        return A.Cast(open_tok.pos, ttype, operand)

    def parse_postfix(self) -> A.Expr:
        expr = self.parse_primary()
        while True:
            tok = self.peek()
            if tok.is_op("."):
                self.advance()
                name_tok = self.peek()
                if name_tok.is_kw("class", "this", "new", "super"):
                    raise self.unsupported(f"конструкция '.{name_tok.text}'", name_tok)
                name = self.expect_ident("имя поля или метода")
                if self.peek().is_op("("):
                    args = self.parse_arguments()
                    expr = A.MethodCall(name.pos, expr, name.text, args)
                else:
                    expr = A.FieldAccess(name.pos, expr, name.text, name.pos)
            elif tok.is_op("["):
                if isinstance(expr, A.NewArray) and not expr.parenthesized:
                    raise self.unsupported("многомерные массивы", tok)
                self.advance()
                index = self.parse_expr()
                self.expect_op("]", "после индекса")
                expr = A.ArrayIndex(tok.pos, expr, index)
            elif tok.is_op("::"):
                raise self.unsupported("ссылки на методы", tok)
            else:
                break
        return self.parse_postfix_ops(expr)

    def parse_postfix_ops(self, expr: A.Expr) -> A.Expr:
        while self.peek().is_op("++", "--"):
            tok = self.advance()
            self.check_lvalue(expr)
            expr = A.IncDec(tok.pos, tok.text, False, expr)
        return expr

    def parse_arguments(self) -> list[A.Expr]:
        self.expect_op("(")
        args: list[A.Expr] = []
        if not self.peek().is_op(")"):
            args.append(self.parse_expr())
            while self.peek().is_op(","):
                self.advance()
                args.append(self.parse_expr())
        self.expect_op(")", "после аргументов")
        return args

    def parse_primary(self) -> A.Expr:
        tok = self.peek()
        if tok.kind in LITERAL_KINDS:
            self.advance()
            kind = LITERAL_KINDS[tok.kind]
            if kind == "int" and tok.value == INT_MIN_MAGNITUDE:
                raise self.error("литерал 2147483648 допустим только как операнд унарного минуса", tok)
            if kind == "long" and tok.value == LONG_MIN_MAGNITUDE:
                raise self.error("литерал 9223372036854775808L допустим только как операнд унарного минуса", tok)
            return A.Literal(tok.pos, kind, tok.value)
        if tok.is_kw("this"):
            self.advance()
            return A.This(tok.pos)
        if tok.is_op("("):
            self.advance()
            expr = self.parse_expr()
            self.expect_op(")", "после выражения в скобках")
            expr.parenthesized = True
            return expr
        if tok.kind is TokenKind.IDENT:
            self.advance()
            if self.peek().is_op("("):
                return A.MethodCall(tok.pos, None, tok.text, self.parse_arguments())
            return A.Name(tok.pos, tok.text)
        if tok.is_kw("super"):
            self.advance()
            self.expect_op(".", "после super")
            parent = self.expect_ident("имя родительского класса")
            if self.peek().is_op("("):
                raise self.error("вызов конструктора родителя допустим только как отдельный оператор", parent)
            if not self.peek().is_op("."):
                raise self.unsupported("обращение super.поле (используйте this.Родитель.поле)", parent)
            self.advance()
            name = self.expect_ident("имя метода")
            if not self.peek().is_op("("):
                raise self.unsupported("обращение super.Родитель.поле (используйте this.Родитель.поле)", name)
            return A.SuperMethodCall(tok.pos, parent.text, name.text, self.parse_arguments())
        if tok.is_kw("new"):
            return self.parse_new()
        self.check_reserved(tok)
        raise self.error(f"ожидается выражение, найдено {tok.describe()}")

    def parse_new(self) -> A.Expr:
        new_tok = self.advance()
        tok = self.peek()
        if self.at_primitive():
            self.advance()
            elem = A.TypeRef(tok.pos, tok.text, True)
            if not self.peek().is_op("["):
                raise self.error("после примитивного типа в new ожидается '['")
        elif tok.kind is TokenKind.IDENT:
            self.advance()
            elem = A.TypeRef(tok.pos, tok.text, False)
            if self.peek().is_op("<"):
                raise self.unsupported("обобщённые типы", self.peek())
            if self.peek().is_op("("):
                args = self.parse_arguments()
                if self.peek().is_op("{"):
                    raise self.unsupported("анонимные классы", self.peek())
                return A.New(new_tok.pos, elem, args)
            if not self.peek().is_op("["):
                raise self.error(f"после 'new {tok.text}' ожидается '(' или '['")
        else:
            raise self.error(f"после new ожидается имя типа, найдено {tok.describe()}")
        self.advance()  # '['
        if self.peek().is_op("]"):
            raise self.unsupported("литералы массивов и new T[]{...}", self.peek())
        length = self.parse_expr()
        self.expect_op("]", "после размера массива")
        if self.peek().is_op("["):
            raise self.unsupported("многомерные массивы", self.peek())
        return A.NewArray(new_tok.pos, elem, length)


def parse(tokens: list[Token], file: str = "") -> A.CompilationUnit:
    return Parser(tokens, file).parse_unit()
