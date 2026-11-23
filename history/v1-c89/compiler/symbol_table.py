"""Символы программы и вложенные области видимости."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .errors import SemanticError

if TYPE_CHECKING:
    from .source import SourcePos
    from .type_system import Type

ACCESS_RANK = {"private": 0, "package": 1, "protected": 2, "public": 3}


def access_of(modifiers: set[str]) -> str:
    for a in ("public", "protected", "private"):
        if a in modifiers:
            return a
    return "package"


class TypeSymbol:
    name: str
    builtin: bool


@dataclass(eq=False)
class FieldSymbol:
    owner: "ClassSymbol"
    name: str
    type: "Type"
    modifiers: set[str]
    decl: object = None
    pos: Optional["SourcePos"] = None

    @property
    def access(self) -> str:
        return access_of(self.modifiers)

    @property
    def is_final(self) -> bool:
        return "final" in self.modifiers

    @property
    def is_private(self) -> bool:
        return "private" in self.modifiers

    def __repr__(self) -> str:
        return f"{self.owner.name}.{self.name}"


@dataclass(eq=False)
class MethodSymbol:
    owner: "ClassSymbol | InterfaceSymbol"
    name: str
    params: list[tuple[str, "Type"]]
    ret: "Type"
    modifiers: set[str]
    decl: object = None
    pos: Optional["SourcePos"] = None
    is_ctor: bool = False
    builtin: Optional[str] = None       # имя встроенной реализации
    implicit: bool = False              # автоматически созданный конструктор (D7)

    @property
    def param_types(self) -> tuple["Type", ...]:
        return tuple(t for _, t in self.params)

    @property
    def sig(self) -> tuple[str, tuple["Type", ...]]:
        return (self.name, self.param_types)

    @property
    def access(self) -> str:
        if isinstance(self.owner, InterfaceSymbol):
            return "public"
        return access_of(self.modifiers)

    @property
    def is_abstract(self) -> bool:
        return "abstract" in self.modifiers or isinstance(self.owner, InterfaceSymbol)

    @property
    def is_final(self) -> bool:
        return "final" in self.modifiers

    @property
    def is_private(self) -> bool:
        return "private" in self.modifiers

    @property
    def is_static(self) -> bool:
        return "static" in self.modifiers

    def display(self) -> str:
        params = ", ".join(str(t) for t in self.param_types)
        return f"{self.owner.name}.{self.name}({params})"

    def __repr__(self) -> str:
        return self.display()


@dataclass(eq=False)
class ClassSymbol(TypeSymbol):
    name: str
    modifiers: set[str] = field(default_factory=set)
    decl: object = None
    builtin: bool = False
    parents: list["ClassSymbol"] = field(default_factory=list)          # без Object
    interfaces: list["InterfaceSymbol"] = field(default_factory=list)
    fields: dict[str, FieldSymbol] = field(default_factory=dict)
    methods: dict[str, list[MethodSymbol]] = field(default_factory=dict)
    ctors: list[MethodSymbol] = field(default_factory=list)
    main: Optional[MethodSymbol] = None                                 # public static void main

    @property
    def is_abstract(self) -> bool:
        return "abstract" in self.modifiers

    @property
    def is_final(self) -> bool:
        return "final" in self.modifiers

    def own_method(self, sig) -> Optional[MethodSymbol]:
        for m in self.methods.get(sig[0], ()):
            if m.param_types == sig[1]:
                return m
        return None

    def all_own_methods(self) -> list[MethodSymbol]:
        return [m for ms in self.methods.values() for m in ms]

    def __repr__(self) -> str:
        return self.name


@dataclass(eq=False)
class InterfaceSymbol(TypeSymbol):
    name: str
    modifiers: set[str] = field(default_factory=set)
    decl: object = None
    builtin: bool = False
    supers: list["InterfaceSymbol"] = field(default_factory=list)
    methods: dict[str, list[MethodSymbol]] = field(default_factory=dict)

    def own_method(self, sig) -> Optional[MethodSymbol]:
        for m in self.methods.get(sig[0], ()):
            if m.param_types == sig[1]:
                return m
        return None

    def __repr__(self) -> str:
        return self.name


_local_ids = 0


@dataclass(eq=False)
class LocalSymbol:
    name: str
    type: "Type"
    pos: Optional["SourcePos"]
    is_param: bool = False
    id: int = 0

    def __post_init__(self):
        global _local_ids
        _local_ids += 1
        self.id = _local_ids

    def __repr__(self) -> str:
        return f"local {self.name}"


class Scope:
    """Лексическая область видимости локальных переменных одного метода."""

    def __init__(self, parent: Optional["Scope"] = None):
        self.parent = parent
        self.symbols: dict[str, LocalSymbol] = {}

    def lookup(self, name: str) -> Optional[LocalSymbol]:
        s = self
        while s is not None:
            if name in s.symbols:
                return s.symbols[name]
            s = s.parent
        return None

    def declare(self, sym: LocalSymbol) -> None:
        prev = self.lookup(sym.name)
        if prev is not None:
            raise SemanticError(f"переменная '{sym.name}' уже объявлена в видимой области", sym.pos,
                                [f"предыдущее объявление: {prev.pos}"] if prev.pos else None)
        self.symbols[sym.name] = sym


class GlobalTable:
    """Таблица имён типов всего набора исходников."""

    def __init__(self):
        self.types: dict[str, TypeSymbol] = {}

    def lookup(self, name: str) -> Optional[TypeSymbol]:
        return self.types.get(name)

    def declare(self, sym: TypeSymbol, pos) -> None:
        prev = self.types.get(sym.name)
        if prev is not None:
            what = "встроенным типом" if prev.builtin else "другим типом"
            raise SemanticError(f"имя типа '{sym.name}' уже занято {what}", pos)
        self.types[sym.name] = sym
