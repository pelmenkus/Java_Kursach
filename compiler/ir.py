"""Типизированное промежуточное представление (README §5).

Управление структурное, выражения линеаризованы в трёхадресные инструкции
над типизированными переменными: порядок вычислений Java задан явно и не
зависит от неуказанного порядка вычисления операндов в C. Каждая инструкция
знает типы операндов и результата, целевые символы и способ вызова.

Сведения для точного GC (п. 13): каждая ссылочная переменная функции живёт
в слоте её корневого кадра; инструкции, внутри которых может начаться сборка
(аллокация, вызов, инициализация класса, throw), помечены `safepoint`. Перед
safepoint анализ живости (liveness.py) вставляет ClearRefs — обнуление слотов
мёртвых ссылок, чтобы сборщик не удерживал недостижимые объекты.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .symbol_table import ClassSymbol, FieldSymbol, InterfaceSymbol, MethodSymbol
from .type_system import Type


@dataclass(eq=False)
class Var:
    id: int
    ty: Type
    hint: str = ""
    is_param: bool = False

    @property
    def is_ref(self) -> bool:
        return self.ty.is_reference

    def __repr__(self) -> str:
        return f"%{self.id}:{self.ty}"


@dataclass(frozen=True)
class Loc:
    """Позиция исходника для сообщений runtime errors и throw."""
    index: int


# ---------------------------------------------------------------- statements
class Stmt:
    safepoint = False

    def uses(self) -> list:
        return []

    def defs(self) -> list:
        return []


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

    def uses(self):
        return [self.value] if self.value is not None else []


# ---------------------------------------------------------------- instructions
class Instr(Stmt):
    """Инструкция с результатом dst (или без него) и операндами."""

    def defs(self):
        dst = getattr(self, "dst", None)
        return [dst] if dst is not None else []


@dataclass(eq=False)
class Const(Instr):
    dst: Var
    value: object            # int | bool | float | None (null)


@dataclass(eq=False)
class StrLit(Instr):
    dst: Var
    index: int               # интернированный литерал jstr_<index>


@dataclass(eq=False)
class Move(Instr):
    dst: Var
    src: Var

    def uses(self):
        return [self.src]


@dataclass(eq=False)
class UnOp(Instr):
    dst: Var
    op: str                  # neg not bitnot
    src: Var
    loc: Optional[Loc] = None    # char: проверка результата (N2)

    def uses(self):
        return [self.src]


@dataclass(eq=False)
class BinOp(Instr):
    dst: Var
    op: str                  # add sub mul div rem shl shr ushr and or xor concat
    a: Var
    b: Var
    loc: Optional[Loc] = None

    @property
    def safepoint(self):
        return self.op == "concat"

    def uses(self):
        return [self.a, self.b]


@dataclass(eq=False)
class Cmp(Instr):
    dst: Var
    op: str                  # eq ne lt gt le ge
    a: Var
    b: Var

    def uses(self):
        return [self.a, self.b]


@dataclass(eq=False)
class Conv(Instr):
    dst: Var                 # примитивное преобразование src.ty -> dst.ty
    src: Var
    loc: Optional[Loc] = None    # приведение к char: проверка (N2)

    def uses(self):
        return [self.src]


@dataclass(eq=False)
class GetField(Instr):
    dst: Var
    obj: Var
    field: FieldSymbol
    loc: Optional[Loc]       # None — проверка null не нужна (this)

    def uses(self):
        return [self.obj]


@dataclass(eq=False)
class SetField(Instr):
    obj: Var
    field: FieldSymbol
    src: Var
    loc: Optional[Loc]

    def uses(self):
        return [self.obj, self.src]


@dataclass(eq=False)
class GetStatic(Instr):
    dst: Var
    field: FieldSymbol


@dataclass(eq=False)
class SetStatic(Instr):
    field: FieldSymbol
    src: Var

    def uses(self):
        return [self.src]


@dataclass(eq=False)
class ClassInit(Instr):
    """Однократная инициализация класса (N4): if (jcs_C != INITIALIZED) jci_C();"""
    cls: ClassSymbol
    safepoint = True


@dataclass(eq=False)
class GetElem(Instr):
    dst: Var
    arr: Var
    idx: Var
    loc: Loc

    def uses(self):
        return [self.arr, self.idx]


@dataclass(eq=False)
class SetElem(Instr):
    arr: Var
    idx: Var
    src: Var
    loc: Loc
    store_check: bool        # проверка допустимости ссылочного элемента

    def uses(self):
        return [self.arr, self.idx, self.src]


@dataclass(eq=False)
class ArrLen(Instr):
    dst: Var
    arr: Var
    loc: Loc

    def uses(self):
        return [self.arr]


@dataclass(eq=False)
class NewArr(Instr):
    dst: Var
    elem: Type
    length: Var
    loc: Loc
    safepoint = True

    def uses(self):
        return [self.length]


@dataclass(eq=False)
class AllocObj(Instr):
    """Выделение экземпляра класса cls со значениями полей по умолчанию."""
    dst: Var
    cls: ClassSymbol
    loc: Loc
    safepoint = True


@dataclass(eq=False)
class NewObject(Instr):
    """new Object()."""
    dst: Var
    loc: Loc
    safepoint = True


@dataclass(eq=False)
class CallCtor(Instr):
    """Вызов конструктора cls для уже выделенного объекта (new или super(...))."""
    recv: Var
    cls: ClassSymbol
    ctor: MethodSymbol
    args: list
    safepoint = True

    def uses(self):
        return [self.recv] + list(self.args)


@dataclass(eq=False)
class RefCast(Instr):
    dst: Var
    src: Var
    kind: str                # noop class iface exact (type_system.CastInfo)
    target: object
    loc: Optional[Loc]

    def uses(self):
        return [self.src]


@dataclass(eq=False)
class InstOf(Instr):
    dst: Var
    src: Var
    kind: str                # static class iface exact
    target: object

    def uses(self):
        return [self.src]


class _Call(Instr):
    safepoint = True

    def uses(self):
        return [self.recv] + list(self.args)


@dataclass(eq=False)
class CallVirt(_Call):
    dst: Optional[Var]
    recv: Var
    method: MethodSymbol
    slot_owner: ClassSymbol  # класс, в структуре VTable которого лежит слот
    args: list
    loc: Loc


@dataclass(eq=False)
class CallIface(_Call):
    dst: Optional[Var]
    recv: Var
    iface: InterfaceSymbol
    method: MethodSymbol
    args: list
    loc: Loc


@dataclass(eq=False)
class CallObj(_Call):
    dst: Optional[Var]
    recv: Var                # ссылка любого ссылочного типа
    which: str               # toString equals getClass
    args: list
    loc: Loc


@dataclass(eq=False)
class CallDirect(_Call):
    dst: Optional[Var]
    recv: Var
    method: MethodSymbol     # конкретная реализация (private, super.m())
    args: list
    loc: Optional[Loc]       # None — получатель this, проверка null не нужна


@dataclass(eq=False)
class CallObjBase(_Call):
    """super.toString()/equals() с реализацией Object."""
    dst: Var
    recv: Var
    which: str
    args: list


# встроенные функции runtime, которые не выделяют память (не safepoint)
NON_ALLOCATING = {"str_length", "str_charAt", "str_equals", "class_getName", "println"}
for _c in ("Z", "C", "B", "S", "I", "J", "F", "D", "Str"):
    NON_ALLOCATING.add("print_" + _c)
    NON_ALLOCATING.add("println_" + _c)


@dataclass(eq=False)
class CallBuiltin(Instr):
    dst: Optional[Var]
    name: str
    args: list
    loc: Optional[Loc]

    @property
    def safepoint(self):
        return not (self.name in NON_ALLOCATING or self.name.startswith("math_"))

    def uses(self):
        return list(self.args)


@dataclass(eq=False)
class Throw(Instr):
    value: Var
    loc: Loc
    safepoint = True

    def uses(self):
        return [self.value]


@dataclass(eq=False)
class ClearRefs(Instr):
    """Обнуление слотов мёртвых ссылок перед safepoint (вставляет liveness)."""
    vars: list


# ---------------------------------------------------------------- program
@dataclass(eq=False)
class Function:
    kind: str                         # method main ctor clinit
    cls: ClassSymbol
    method: MethodSymbol
    self_var: Optional[Var]
    params: list
    vars: list = field(default_factory=list)
    body: Block = field(default_factory=Block)
    slots: dict = field(default_factory=dict)   # Var -> индекс слота корневого кадра


@dataclass(eq=False)
class Program:
    classes: list                     # компилируемые классы (родители раньше)
    interfaces: list
    functions: list
    strings: list                     # tuple кодовых точек; индекс = номер jstr_
    locations: list                   # (file, line, column, function)
    array_elems: list                 # типы элементов массивов классов/интерфейсов
    entry: Function
    entry_class: ClassSymbol
    hierarchy: object
    init_classes: set
    globals_count: int
    object_methods: dict
