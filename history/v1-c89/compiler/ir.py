"""Типизированное промежуточное представление (README §5).

Управление структурное, выражения линеаризованы в трёхадресные инструкции
над типизированными переменными: порядок вычислений задан явно. Каждая
инструкция знает типы операндов и результата, целевые символы, способ
вызова и путь к подобъекту. Генератор C работает только с IR.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .inheritance import Res, SubObject
from .symbol_table import ClassSymbol, FieldSymbol, InterfaceSymbol, MethodSymbol
from .type_system import Type


@dataclass(eq=False)
class Var:
    id: int
    ty: Type
    hint: str = ""
    is_param: bool = False

    def __repr__(self) -> str:
        return f"%{self.id}:{self.ty}"


@dataclass(frozen=True)
class Loc:
    """Позиция исходника для сообщений runtime errors."""
    index: int


# ---------------------------------------------------------------- statements
class Stmt:
    pass


@dataclass(eq=False)
class Block(Stmt):
    stmts: list = field(default_factory=list)


@dataclass(eq=False)
class If(Stmt):
    cond: Var
    then: Block
    els: Optional[Block] = None


@dataclass(eq=False)
class Loop(Stmt):
    """for(;;) { cond_block; if (!cond) break; body; continue_label: update }"""
    cond_block: Block
    cond: Optional[Var]
    body: Block
    update: Block
    id: int = 0
    has_continue: bool = False


@dataclass(eq=False)
class Break(Stmt):
    pass


@dataclass(eq=False)
class Continue(Stmt):
    loop: Loop


@dataclass(eq=False)
class Return(Stmt):
    value: Optional[Var] = None


# ---------------------------------------------------------------- instructions
@dataclass(eq=False)
class Const(Stmt):
    dst: Var
    value: object            # int | bool | FloatBits | None(null)


@dataclass(eq=False)
class StrLit(Stmt):
    dst: Var
    index: int


@dataclass(eq=False)
class Move(Stmt):
    dst: Var
    src: Var


@dataclass(eq=False)
class UnOp(Stmt):
    dst: Var
    op: str                  # neg not bitnot
    src: Var


@dataclass(eq=False)
class BinOp(Stmt):
    dst: Var
    op: str                  # add sub mul div rem shl shr ushr and or xor concat
    a: Var
    b: Var
    loc: Optional[Loc] = None


@dataclass(eq=False)
class Cmp(Stmt):
    dst: Var
    op: str                  # eq ne lt gt le ge
    a: Var
    b: Var


@dataclass(eq=False)
class Conv(Stmt):
    dst: Var                 # примитивное преобразование src.ty -> dst.ty
    src: Var


@dataclass(eq=False)
class GetField(Stmt):
    dst: Var
    obj: Var                 # представление класса obj.ty
    path: tuple              # подобъекты от obj.ty до владельца поля
    field: FieldSymbol
    loc: Optional[Loc]       # None — проверка null не нужна (this)


@dataclass(eq=False)
class SetField(Stmt):
    obj: Var
    path: tuple
    field: FieldSymbol
    src: Var
    loc: Optional[Loc]


@dataclass(eq=False)
class GetElem(Stmt):
    dst: Var
    arr: Var
    idx: Var
    loc: Loc


@dataclass(eq=False)
class SetElem(Stmt):
    arr: Var
    idx: Var
    src: Var
    loc: Loc
    store_check: bool        # проверка допустимости ссылочного элемента


@dataclass(eq=False)
class ArrLen(Stmt):
    dst: Var
    arr: Var
    loc: Loc


@dataclass(eq=False)
class NewArr(Stmt):
    dst: Var
    elem: Type
    length: Var
    loc: Loc


@dataclass(eq=False)
class NewObj(Stmt):
    dst: Var
    cls: ClassSymbol
    ctor: Optional[MethodSymbol]     # None — new Object()
    args: list
    loc: Loc


@dataclass(eq=False)
class RefCast(Stmt):
    dst: Var
    src: Var
    kind: str                # см. type_system.CastInfo.kind
    path: tuple
    target: object
    loc: Optional[Loc]


@dataclass(eq=False)
class InstOf(Stmt):
    dst: Var
    src: Var
    kind: str
    target: object


@dataclass(eq=False)
class CallVirt(Stmt):
    dst: Optional[Var]
    recv: Var                # представление view
    view: ClassSymbol
    method: MethodSymbol     # слот sig в VTable view
    args: list
    loc: Loc


@dataclass(eq=False)
class CallIface(Stmt):
    dst: Optional[Var]
    recv: Var
    iface: InterfaceSymbol
    method: MethodSymbol
    args: list
    loc: Loc


@dataclass(eq=False)
class CallObj(Stmt):
    dst: Optional[Var]
    recv: Var                # ссылка любого ссылочного типа
    which: str               # toString equals getClass
    args: list
    loc: Loc


@dataclass(eq=False)
class CallDirect(Stmt):
    dst: Optional[Var]
    recv: Var                # представление статического класса recv.ty
    path: tuple              # вниз до подобъекта реализации
    method: MethodSymbol     # конкретная реализация
    args: list
    loc: Optional[Loc]


@dataclass(eq=False)
class CallObjBase(Stmt):
    """super.P.toString()/equals() с базовой реализацией Object."""
    dst: Var
    recv: Var
    which: str
    args: list


@dataclass(eq=False)
class CallInit(Stmt):
    """Инициализация подобъекта родителя: super.P(args)."""
    recv: Var
    parent: ClassSymbol
    ctor: MethodSymbol
    args: list


@dataclass(eq=False)
class CallBuiltin(Stmt):
    dst: Optional[Var]
    name: str
    args: list
    loc: Optional[Loc]


# ---------------------------------------------------------------- program
@dataclass(eq=False)
class Function:
    kind: str                         # method main init
    cls: ClassSymbol
    method: MethodSymbol
    self_var: Optional[Var]
    params: list
    vars: list = field(default_factory=list)
    body: Block = field(default_factory=Block)


@dataclass(eq=False)
class Slot:
    method: MethodSymbol              # сигнатура/тип слота (объявление в MS(view))
    site: tuple                       # путь подобъекта реализации от верхнего класса
    impl: MethodSymbol


@dataclass(eq=False)
class VTable:
    sub: SubObject
    slots: list                       # list[Slot] в порядке hierarchy.vtable_sigs(sub.cls)
    itables: list                     # list[ITable]


@dataclass(eq=False)
class ITable:
    iface: InterfaceSymbol
    slots: list                       # list[Slot] в порядке iface_slots(iface)


@dataclass(eq=False)
class ConcreteClass:
    """Всё, что нужно для экземпляров динамического класса."""
    cls: ClassSymbol
    subobjects: list                  # list[SubObject]
    vtables: list                     # list[VTable], по одному на подобъект
    to_string: Optional[Res]          # objres(D, toString), None — база Object
    equals: Optional[Res]
    ref_fields: list                  # list[(path, FieldSymbol)]
    init_fields: list                 # list[(path, FieldSymbol)] с явной инициализацией


@dataclass(eq=False)
class Program:
    classes: list                     # все пользовательские классы (родители раньше)
    interfaces: list
    concrete: list                    # list[ConcreteClass]
    functions: list
    strings: list                     # tuple[int,...] UTF-16
    locations: list                   # (file, line, column, function)
    array_elems: list                 # типы элементов пользовательских массивов
    entry: Function
    hierarchy: object
    iface_slots: dict                 # InterfaceSymbol -> list[MethodSymbol]
    vtable_slots: dict                # ClassSymbol -> list[MethodSymbol]
