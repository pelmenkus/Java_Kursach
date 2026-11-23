"""Узлы абстрактного синтаксического дерева.

AST отражает только синтаксис. Семантический анализ дописывает в узлы
выражений атрибуты `ty` (тип) и `info` (результат разрешения имён/вызовов).
Это аннотации, а не поля dataclass, поэтому парсер о них не знает.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .source import SourcePos


@dataclass(eq=False)
class Node:
    pos: SourcePos


# ---------------------------------------------------------------- types
@dataclass(eq=False)
class TypeRef(Node):
    name: str            # имя примитива, класса/интерфейса или 'void'
    is_primitive: bool
    is_array: bool = False

    def __str__(self) -> str:
        return self.name + ("[]" if self.is_array else "")


# ---------------------------------------------------------------- declarations
@dataclass(eq=False)
class Modifier(Node):
    name: str


@dataclass(eq=False)
class Param(Node):
    type: TypeRef
    name: str


@dataclass(eq=False)
class VarDeclarator(Node):
    name: str
    init: Optional["Expr"]


@dataclass(eq=False)
class FieldDecl(Node):
    modifiers: list[Modifier]
    type: TypeRef
    declarators: list[VarDeclarator]


@dataclass(eq=False)
class MethodDecl(Node):
    modifiers: list[Modifier]
    ret: TypeRef
    name: str
    params: list[Param]
    body: Optional["Block"]
    end_pos: Optional[SourcePos] = None


@dataclass(eq=False)
class ConstructorDecl(Node):
    modifiers: list[Modifier]
    name: str
    params: list[Param]
    body: "Block"


@dataclass(eq=False)
class ClassDecl(Node):
    modifiers: list[Modifier]
    name: str
    parents: list[TypeRef]
    interfaces: list[TypeRef]
    members: list[Node]
    file: str = ""


@dataclass(eq=False)
class InterfaceDecl(Node):
    modifiers: list[Modifier]
    name: str
    supers: list[TypeRef]
    methods: list[MethodDecl]
    file: str = ""


@dataclass(eq=False)
class CompilationUnit(Node):
    types: list[Node]


# ---------------------------------------------------------------- statements
@dataclass(eq=False)
class Stmt(Node):
    pass


@dataclass(eq=False)
class Block(Stmt):
    stmts: list[Stmt]
    end_pos: Optional[SourcePos] = None


@dataclass(eq=False)
class LocalVarDecl(Stmt):
    type: TypeRef
    declarators: list[VarDeclarator]


@dataclass(eq=False)
class EmptyStmt(Stmt):
    pass


@dataclass(eq=False)
class ExprStmt(Stmt):
    expr: "Expr"


@dataclass(eq=False)
class If(Stmt):
    cond: "Expr"
    then: Stmt
    els: Optional[Stmt]


@dataclass(eq=False)
class While(Stmt):
    cond: "Expr"
    body: Stmt


@dataclass(eq=False)
class For(Stmt):
    init: list[Stmt]           # LocalVarDecl или ExprStmt
    cond: Optional["Expr"]
    update: list["Expr"]
    body: Stmt


@dataclass(eq=False)
class ForEach(Stmt):
    type: TypeRef
    name: str
    iterable: "Expr"
    body: Stmt


@dataclass(eq=False)
class Break(Stmt):
    pass


@dataclass(eq=False)
class Continue(Stmt):
    pass


@dataclass(eq=False)
class Return(Stmt):
    value: Optional["Expr"]


@dataclass(eq=False)
class SuperCtorCall(Stmt):
    parent: str
    args: list["Expr"]


# ---------------------------------------------------------------- expressions
@dataclass(eq=False)
class Expr(Node):
    # аннотации семантического анализа (не поля dataclass)
    ty = None
    info = None
    parenthesized = False


@dataclass(eq=False)
class Literal(Expr):
    kind: str          # int long float double char string boolean null
    value: object


@dataclass(eq=False)
class Name(Expr):
    name: str


@dataclass(eq=False)
class This(Expr):
    pass


@dataclass(eq=False)
class FieldAccess(Expr):
    target: Expr
    name: str
    name_pos: Optional[SourcePos] = None


@dataclass(eq=False)
class MethodCall(Expr):
    target: Optional[Expr]
    name: str
    args: list[Expr]


@dataclass(eq=False)
class SuperMethodCall(Expr):
    parent: str
    name: str
    args: list[Expr]


@dataclass(eq=False)
class New(Expr):
    type: TypeRef
    args: list[Expr]


@dataclass(eq=False)
class NewArray(Expr):
    elem: TypeRef
    length: Expr


@dataclass(eq=False)
class ArrayIndex(Expr):
    array: Expr
    index: Expr


@dataclass(eq=False)
class Unary(Expr):
    op: str            # + - ! ~
    operand: Expr


@dataclass(eq=False)
class IncDec(Expr):
    op: str            # ++ --
    prefix: bool
    target: Expr


@dataclass(eq=False)
class Binary(Expr):
    op: str            # арифметика, сравнения, битовые операции, сдвиги
    left: Expr
    right: Expr


@dataclass(eq=False)
class Logical(Expr):
    op: str            # && ||
    left: Expr
    right: Expr


@dataclass(eq=False)
class Conditional(Expr):
    cond: Expr
    then: Expr
    els: Expr


@dataclass(eq=False)
class Assign(Expr):
    op: str            # = += -= *= /= %=
    target: Expr
    value: Expr


@dataclass(eq=False)
class Cast(Expr):
    type: TypeRef
    operand: Expr


@dataclass(eq=False)
class InstanceOf(Expr):
    operand: Expr
    type: TypeRef
