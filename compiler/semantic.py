"""Семантический анализ (п. 17 ТЗ).

Этапы:
  1. объявления типов (сначала prelude: Throwable, Exception, RuntimeException);
  2. заголовки: единственный родитель, интерфейсы, циклы;
  3. члены классов: поля (в т.ч. static), методы, конструкторы;
  4. наследование: override, final, abstract, реализация интерфейсов;
  5. тела: типы выражений, разрешение имён, константы (N4), static-доступ (N5);
  6. flow-анализ (достижимость, возвраты, definite assignment) — flow.py.

Результат — аннотированное AST: у каждого выражения есть `ty`, `info` и,
для константных выражений, `const`.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from typing import Optional

from . import ast_nodes as A
from . import constfold as CF
from . import type_system as T
from .errors import CompilationFailed, CompileError, SemanticError, TypeCheckError, type_notes
from .inheritance import Hierarchy
from .prelude import PRELUDE_CLASSES, prelude_unit
from .symbol_table import (ACCESS_RANK, ClassSymbol, FieldSymbol, GlobalTable, InterfaceSymbol,
                           LocalSymbol, MethodSymbol, Scope)
from .type_system import OpError

RESERVED_TYPE_NAMES = ("Object", "String", "Class", "System", "Math")


# ---------------------------------------------------------------- annotations
@dataclass
class LocalRef:
    sym: LocalSymbol


@dataclass
class FieldRef:
    """Поле экземпляра. obj None — неявный this (простое имя или super.f)."""
    obj: Optional[A.Expr]
    field: FieldSymbol


@dataclass
class StaticRef:
    """Static-поле: ИмяКласса.поле или простое имя в классе/наследнике (N5)."""
    field: FieldSymbol


@dataclass
class ArrayLengthRef:
    pass


@dataclass
class TypeName:
    sym: object                    # ClassSymbol | InterfaceSymbol — только квалификатор


@dataclass
class BuiltinName:
    name: str                      # System, System.out, Math, String


@dataclass
class MathConst:
    name: str                      # PI, E


@dataclass
class CallInfo:
    kind: str        # virtual interface object direct super_object builtin
    method: Optional[MethodSymbol]
    recv: Optional[A.Expr] = None  # None — this (или нет получателя у builtin)
    slot_owner: object = None      # virtual: класс, в VTable которого лежит слот
    iface: object = None           # interface: интерфейс статического типа получателя
    builtin: Optional[str] = None
    has_recv: bool = False         # у builtin есть вычисляемый получатель


@dataclass
class MethodBody:
    kind: str                      # method main ctor clinit
    method: MethodSymbol
    cls: ClassSymbol
    params: list[LocalSymbol]
    body: A.Block
    super_call: Optional[A.SuperCtorCall] = None       # ctor: явный или неявный super(...)
    field_inits: list = field(default_factory=list)    # ctor/clinit: [(FieldSymbol, Expr)]


@dataclass
class AnalyzedProgram:
    classes: list[ClassSymbol]     # родители раньше потомков, prelude включён
    interfaces: list[InterfaceSymbol]
    hierarchy: Hierarchy
    entry: MethodSymbol
    entry_class: ClassSymbol
    bodies: list[MethodBody]
    object_methods: dict
    init_classes: set              # классы с нетривиальной инициализацией (jci_)
    globals_count: int             # число слотов globals[N]


# ---------------------------------------------------------------- builtins
def _m(owner, name, params, ret, builtin=None, mods=("public",)):
    return MethodSymbol(owner, name, [(f"p{i}", t) for i, t in enumerate(params)], ret,
                        set(mods), builtin=builtin)


def install_builtins() -> dict:
    """Методы встроенных классов. Возвращает sig -> MethodSymbol методов Object."""
    O, S, C = T.OBJECT_SYM, T.STRING_SYM, T.CLASS_SYM
    for sym in T.BUILTIN_CLASSES:
        sym.methods.clear()
    obj_methods = [
        _m(O, "toString", [], T.STRING, "obj_toString"),
        _m(O, "equals", [T.OBJECT], T.BOOLEAN, "obj_equals"),
        _m(O, "getClass", [], T.CLASS, "obj_getClass", ("public", "final")),
    ]
    for m in obj_methods:
        O.methods.setdefault(m.name, []).append(m)
    for m in (_m(S, "length", [], T.INT, "str_length"),
              _m(S, "charAt", [T.INT], T.CHAR, "str_charAt"),
              _m(S, "substring", [T.INT, T.INT], T.STRING, "str_substring"),
              _m(S, "equals", [T.STRING], T.BOOLEAN, "str_equals")):
        S.methods.setdefault(m.name, []).append(m)
    C.methods["getName"] = [_m(C, "getName", [], T.STRING, "class_getName")]
    return {m.sig: m for m in obj_methods}


PRINTABLE = [T.BOOLEAN, T.CHAR, T.BYTE, T.SHORT, T.INT, T.LONG, T.FLOAT, T.DOUBLE, T.STRING, T.OBJECT]


def _code(t: T.Type) -> str:
    return {"boolean": "Z", "char": "C", "byte": "B", "short": "S", "int": "I", "long": "J",
            "float": "F", "double": "D"}.get(str(t), "Str" if T.is_string(t) else "Obj")


def builtin_static_methods() -> dict[str, list[tuple[tuple, T.Type, str]]]:
    """'Класс.метод' -> [(типы параметров, тип результата, builtin-id)]."""
    out: dict[str, list] = {"System.out.print": [], "System.out.println": [((), T.VOID, "println")]}
    for t in PRINTABLE:
        out["System.out.print"].append(((t,), T.VOID, "print_" + _code(t)))
        out["System.out.println"].append(((t,), T.VOID, "println_" + _code(t)))
    num = [T.INT, T.LONG, T.FLOAT, T.DOUBLE]
    out["Math.abs"] = [((t,), t, "math_abs_" + _code(t)) for t in num]
    out["Math.min"] = [((t, t), t, "math_min_" + _code(t)) for t in num]
    out["Math.max"] = [((t, t), t, "math_max_" + _code(t)) for t in num]
    out["Math.sqrt"] = [((T.DOUBLE,), T.DOUBLE, "math_sqrt")]
    out["Math.pow"] = [((T.DOUBLE, T.DOUBLE), T.DOUBLE, "math_pow")]
    out["Math.sin"] = [((T.DOUBLE,), T.DOUBLE, "math_sin")]
    out["Math.cos"] = [((T.DOUBLE,), T.DOUBLE, "math_cos")]
    out["String.valueOf"] = [((t,), T.STRING, "valueOf_" + _code(t))
                             for t in (T.BOOLEAN, T.CHAR, T.BYTE, T.SHORT, T.INT, T.LONG, T.FLOAT, T.DOUBLE)]
    return out


BUILTIN_STATICS = builtin_static_methods()
MATH_CONSTANTS = {"PI": math.pi, "E": math.e}


class _PseudoType(T.Type):
    def __init__(self, text: str):
        self.text = text

    def __str__(self) -> str:
        return self.text


_BUILTIN_NAME_TYPE = _PseudoType("<встроенное имя>")
_TYPE_NAME_TYPE = _PseudoType("<имя типа>")


# ---------------------------------------------------------------- context
@dataclass
class Ctx:
    cls: Optional[ClassSymbol]
    method: Optional[MethodSymbol]
    is_static: bool                # main или static-инициализатор: нет this
    is_ctor: bool
    scope: Scope
    loop_depth: int = 0
    in_field_init: bool = False    # инициализатор поля (static или экземпляра)
    field_index: int = -1          # индекс инициализируемого поля (опережающие ссылки)
    pre_super: bool = False        # аргументы super(...): this ещё недоступен


def derive(ctx: Ctx, **changes) -> Ctx:
    return dataclasses.replace(ctx, **changes)


class Analyzer:
    def __init__(self, units: list[A.CompilationUnit]):
        self.units = units
        self.errors: list[CompileError] = []
        self.globals = GlobalTable()
        self.object_methods = install_builtins()
        self.h = Hierarchy(self.object_methods)
        self.classes: list[ClassSymbol] = []
        self.interfaces: list[InterfaceSymbol] = []
        self.bodies: list[MethodBody] = []
        self.main_class: Optional[str] = None
        for sym in T.BUILTIN_CLASSES:
            self.globals.types[sym.name] = sym

    # ------------------------------------------------------------ driver
    def error(self, err: CompileError) -> None:
        self.errors.append(err)

    def checkpoint(self) -> None:
        if self.errors:
            raise CompilationFailed(self.errors)

    def run(self) -> AnalyzedProgram:
        self.declare_types(prelude_unit(), prelude=True)
        for unit in self.units:
            self.declare_types(unit)
        self.checkpoint()
        self.resolve_headers()
        self.checkpoint()
        self.check_cycles()
        self.checkpoint()
        self.declare_members()
        self.checkpoint()
        self.check_inheritance()
        self.checkpoint()
        self.check_bodies()
        self.checkpoint()
        entry_cls, entry = self.find_entry()
        self.checkpoint()
        from .flow import FlowAnalyzer
        flow = FlowAnalyzer(self)
        for body in self.bodies:
            flow.analyze(body)
        self.checkpoint()
        classes = self.ordered_classes()
        n_globals = self.assign_globals(classes)
        init = {c for c in classes if self.needs_init(c)}
        return AnalyzedProgram(classes, self.interfaces, self.h, entry, entry_cls, self.bodies,
                               self.object_methods, init, n_globals)

    def ordered_classes(self) -> list[ClassSymbol]:
        """Классы в порядке: родители раньше потомков, иначе — порядок объявления."""
        out: list[ClassSymbol] = []
        seen: set = set()

        def visit(c: ClassSymbol) -> None:
            if c in seen or c.builtin:
                return
            seen.add(c)
            visit(c.parent)
            out.append(c)

        for c in self.classes:
            visit(c)
        return out

    def assign_globals(self, classes: list[ClassSymbol]) -> int:
        """Детерминированные индексы globals[N] ссылочных static-полей (п. 12.2)."""
        n = 0
        for c in classes:
            for f in c.fields.values():
                if f.is_static and f.type.is_reference:
                    f.global_index = n
                    n += 1
        return n

    def nontrivial_static_init(self, c: ClassSymbol) -> bool:
        """Есть static-инициализатор, значение которого не константа. Иначе все
        static-поля получают значения статической инициализацией C."""
        return any(f.is_static and f.decl.init is not None and f.decl.init.const is None
                   for f in c.fields.values())

    def needs_init(self, c: ClassSymbol) -> bool:
        """Нужна ли функция инициализации класса: есть static-инициализаторы,
        не являющиеся константами, у класса или его предков."""
        while c is not None and not c.builtin:
            if self.nontrivial_static_init(c):
                return True
            c = c.parent
        return False

    # ------------------------------------------------------------ 1. types
    def declare_types(self, unit: A.CompilationUnit, prelude: bool = False) -> None:
        for decl in unit.types:
            if decl.name in RESERVED_TYPE_NAMES:
                self.error(SemanticError(f"имя '{decl.name}' зарезервировано встроенным типом", decl.pos))
                continue
            mods = {m.name for m in decl.modifiers}
            if isinstance(decl, A.ClassDecl):
                sym = ClassSymbol(decl.name, mods, decl)
                if prelude:
                    sym.prelude = True
                    sym.java_name = "java.lang." + decl.name
                if "abstract" in mods and "final" in mods:
                    self.error(SemanticError("класс не может быть одновременно abstract и final", decl.pos))
                self._declare(sym, decl.pos, self.classes)
            else:
                sym = InterfaceSymbol(decl.name, mods, decl)
                self._declare(sym, decl.pos, self.interfaces)

    def _declare(self, sym, pos, bucket) -> None:
        try:
            self.globals.declare(sym, pos)
            bucket.append(sym)
        except CompileError as e:
            self.error(e)

    @property
    def throwable(self) -> ClassSymbol:
        return self.globals.lookup("Throwable")

    # ------------------------------------------------------------ 2. headers
    def resolve_headers(self) -> None:
        for c in self.classes:
            decl: A.ClassDecl = c.decl
            c.parent = T.OBJECT_SYM
            ref = decl.parent
            if ref is not None:
                sym = self.globals.lookup(ref.name)
                if sym is None:
                    self.error(SemanticError(f"неизвестный класс '{ref.name}'", ref.pos))
                elif isinstance(sym, InterfaceSymbol):
                    self.error(SemanticError(f"'{ref.name}' — интерфейс; классы реализуют интерфейсы через "
                                             f"implements", ref.pos))
                elif sym is T.OBJECT_SYM:
                    pass
                elif sym.builtin:
                    self.error(SemanticError(f"нельзя наследоваться от встроенного final-класса {ref.name}",
                                             ref.pos))
                elif sym.is_final:
                    self.error(SemanticError(f"нельзя наследоваться от final-класса {ref.name}", ref.pos))
                elif sym is c:
                    self.error(SemanticError(f"класс {c.name} не может наследоваться от самого себя", ref.pos))
                else:
                    c.parent = sym
            seen = set()
            for iref in decl.interfaces:
                sym = self.globals.lookup(iref.name)
                if iref.name in seen:
                    self.error(SemanticError(f"интерфейс '{iref.name}' указан повторно", iref.pos))
                    continue
                seen.add(iref.name)
                if sym is None:
                    self.error(SemanticError(f"неизвестный интерфейс '{iref.name}'", iref.pos))
                elif not isinstance(sym, InterfaceSymbol):
                    self.error(SemanticError(f"'{iref.name}' не является интерфейсом", iref.pos))
                else:
                    c.interfaces.append(sym)
        for i in self.interfaces:
            seen = set()
            for ref in i.decl.supers:
                sym = self.globals.lookup(ref.name)
                if ref.name in seen:
                    self.error(SemanticError(f"интерфейс '{ref.name}' указан повторно", ref.pos))
                    continue
                seen.add(ref.name)
                if sym is None:
                    self.error(SemanticError(f"неизвестный интерфейс '{ref.name}'", ref.pos))
                elif not isinstance(sym, InterfaceSymbol):
                    self.error(SemanticError(f"интерфейс может расширять только интерфейсы, "
                                             f"'{ref.name}' — класс", ref.pos))
                elif sym is i:
                    self.error(SemanticError(f"интерфейс {i.name} не может расширять сам себя", ref.pos))
                else:
                    i.supers.append(sym)

    def check_cycles(self) -> None:
        # цепочка классов: идём по единственному родителю
        reported = set()
        for c in self.classes:
            path, cur = [c], c.parent
            while cur is not None and not cur.builtin:
                if cur is c:
                    cyc = frozenset(path)
                    if cyc not in reported:
                        reported.add(cyc)
                        self.error(SemanticError("цикл в цепочке наследования классов: "
                                                 + " -> ".join(s.name for s in path + [c]), c.decl.pos))
                    break
                if cur in path:
                    break
                path.append(cur)
                cur = cur.parent
        if self.errors:
            for c in self.classes:          # разрываем цикл, чтобы chain() завершалась
                c.parent = T.OBJECT_SYM
            return

        def find_cycle(start):
            stack = [(start, [start])]
            while stack:
                node, path = stack.pop()
                for nxt in node.supers:
                    if nxt is start:
                        return path + [start]
                    if nxt not in path:
                        stack.append((nxt, path + [nxt]))
            return None

        for i in self.interfaces:
            cyc = find_cycle(i)
            if cyc and frozenset(cyc) not in reported:
                reported.add(frozenset(cyc))
                self.error(SemanticError("цикл в графе наследования интерфейсов: "
                                         + " -> ".join(s.name for s in cyc), i.decl.pos))

    # ------------------------------------------------------------ types
    def resolve_type(self, ref: A.TypeRef, allow_void: bool = False) -> T.Type:
        if ref.name == "void":
            if not allow_void:
                raise SemanticError("тип void здесь недопустим", ref.pos)
            return T.VOID
        if ref.is_primitive:
            base = T.PRIMITIVES[ref.name]
        else:
            sym = self.globals.lookup(ref.name)
            if sym is None:
                raise SemanticError(f"неизвестный тип '{ref.name}'", ref.pos)
            base = T.ClassType(sym) if isinstance(sym, ClassSymbol) else T.InterfaceType(sym)
        return T.ArrayType(base) if ref.is_array else base

    def check_throws(self, refs: list[A.TypeRef]) -> None:
        """throws T1, T2 — информативно (13.4), но типы обязаны быть Throwable."""
        for ref in refs:
            try:
                t = self.resolve_type(ref)
                if not (isinstance(t, T.ClassType) and t.sym.is_subclass_of(self.throwable)):
                    raise TypeCheckError(f"в throws допустимы только подклассы Throwable, найдено {t}", ref.pos)
            except CompileError as e:
                self.error(e)

    # ------------------------------------------------------------ 3. members
    def declare_members(self) -> None:
        for i in self.interfaces:
            for md in i.decl.methods:
                try:
                    ret = self.resolve_type(md.ret, allow_void=True)
                    params = self._params(md.params)
                    self.check_throws(md.throws)
                    m = MethodSymbol(i, md.name, params, ret, {"public", "abstract"}, md, md.pos)
                    if m.sig in self.object_methods:
                        raise SemanticError(f"интерфейс не может объявлять метод Object {md.name}", md.pos)
                    if i.own_method(m.sig) is not None:
                        raise SemanticError(f"метод {m.display()} уже объявлен", md.pos)
                    i.methods.setdefault(md.name, []).append(m)
                except CompileError as e:
                    self.error(e)
        for c in self.classes:
            for member in c.decl.members:
                try:
                    if isinstance(member, A.FieldDecl):
                        self._declare_fields(c, member)
                    elif isinstance(member, A.MethodDecl):
                        self._declare_method(c, member)
                    else:
                        self._declare_ctor(c, member)
                except CompileError as e:
                    self.error(e)
            if not c.ctors:
                # конструктор по умолчанию с неявным super() (п. 4.2)
                ctor = MethodSymbol(c, c.name, [], T.VOID, {"public"}, None, c.decl.pos, is_ctor=True,
                                    implicit=True)
                c.ctors.append(ctor)

    def _params(self, params: list[A.Param]) -> list[tuple[str, T.Type]]:
        out, seen = [], set()
        for p in params:
            if p.name in seen:
                raise SemanticError(f"параметр '{p.name}' объявлен повторно", p.pos)
            seen.add(p.name)
            out.append((p.name, self.resolve_type(p.type)))
        return out

    def _declare_fields(self, c: ClassSymbol, fd: A.FieldDecl) -> None:
        mods = {m.name for m in fd.modifiers}
        if "abstract" in mods:
            raise SemanticError("поле не может быть abstract", fd.pos)
        self._check_access_mods(mods, fd.pos)
        ftype = self.resolve_type(fd.type)
        for d in fd.declarators:
            if d.name in c.fields:
                self.error(SemanticError(f"поле '{d.name}' уже объявлено в классе {c.name}", d.pos))
                continue
            if "static" in mods and "final" in mods and d.init is None:
                self.error(SemanticError(f"static final поле '{d.name}' должно иметь инициализатор", d.pos))
                continue
            c.fields[d.name] = FieldSymbol(c, d.name, ftype, set(mods), d, d.pos, index=len(c.fields))

    def _check_access_mods(self, mods: set[str], pos) -> None:
        if len(mods & {"public", "protected", "private"}) > 1:
            raise SemanticError("несовместимые модификаторы доступа", pos)

    def _declare_method(self, c: ClassSymbol, md: A.MethodDecl) -> None:
        mods = {m.name for m in md.modifiers}
        self._check_access_mods(mods, md.pos)
        ret = self.resolve_type(md.ret, allow_void=True)
        params = self._params(md.params)
        self.check_throws(md.throws)
        m = MethodSymbol(c, md.name, params, ret, mods, md, md.pos)
        if "static" in mods:
            is_main = (md.name == "main" and ret is T.VOID and m.param_types == (T.ArrayType(T.STRING),)
                       and "public" in mods and not (mods & {"abstract", "final"}))
            if not is_main:
                raise SemanticError("пользовательские статические методы не поддерживаются (кроме точки входа "
                                    "public static void main(String[] args))", md.pos)
            if md.body is None:
                raise SemanticError("main должен иметь тело", md.pos)
            c.main = m
            return
        if "abstract" in mods:
            if md.body is not None:
                raise SemanticError("абстрактный метод не может иметь тело", md.pos)
            if not c.is_abstract:
                raise SemanticError(f"абстрактный метод в неабстрактном классе {c.name}", md.pos)
            if mods & {"private", "final"}:
                raise SemanticError("абстрактный метод не может быть private или final", md.pos)
        elif md.body is None:
            raise SemanticError("у метода должно быть тело (или модификатор abstract)", md.pos)
        if c.own_method(m.sig) is not None:
            raise SemanticError(f"метод {m.display()} уже объявлен", md.pos)
        c.methods.setdefault(md.name, []).append(m)

    def _declare_ctor(self, c: ClassSymbol, cd: A.ConstructorDecl) -> None:
        mods = {m.name for m in cd.modifiers}
        if mods & {"abstract", "final", "static"}:
            raise SemanticError("конструктор допускает только модификаторы доступа", cd.pos)
        self._check_access_mods(mods, cd.pos)
        params = self._params(cd.params)
        self.check_throws(cd.throws)
        m = MethodSymbol(c, c.name, params, T.VOID, mods, cd, cd.pos, is_ctor=True)
        if any(x.param_types == m.param_types for x in c.ctors):
            raise SemanticError(f"конструктор {m.display()} уже объявлен", cd.pos)
        c.ctors.append(m)

    # ------------------------------------------------------------ 4. inheritance
    def check_inheritance(self) -> None:
        for i in self.interfaces:
            seen: dict = {}
            for j in self.h.iface_closure(i):
                for ms in j.methods.values():
                    for m in ms:
                        prev = seen.get(m.sig)
                        if prev is not None and not T.same_type(prev.ret, m.ret):
                            self.error(TypeCheckError(
                                f"интерфейс {i.name} наследует {m.name} с разными типами результата: "
                                f"{prev.display()} -> {prev.ret}, {m.display()} -> {m.ret}", i.decl.pos))
                        seen.setdefault(m.sig, m)
        for c in self.classes:
            try:
                self._check_class_inheritance(c)
            except CompileError as e:
                self.error(e)

    def _check_class_inheritance(self, c: ClassSymbol) -> None:
        for m in c.all_own_methods():
            if m.is_static:
                continue
            base = self.h.inherited(c, m.sig)
            if base is None:
                continue
            if base.is_final:
                self.error(SemanticError(f"метод {m.display()} переопределяет final-метод {base.display()}", m.pos))
            if not T.same_type(base.ret, m.ret):
                self.error(TypeCheckError(f"метод {m.display()} переопределяет {base.display()} "
                                          f"с другим типом результата", m.pos, type_notes(base.ret, m.ret)))
            if ACCESS_RANK[m.access] < ACCESS_RANK[base.access]:
                self.error(SemanticError(f"метод {m.display()} сужает доступ {base.access} "
                                         f"метода {base.display()} до {m.access}", m.pos))
        table = self.h.method_table(c)
        for iface in self.h.implemented(c):
            for sig, im in self.h.iface_methods(iface).items():
                r = table.get(sig)
                if r is None or r is im:
                    continue
                if not T.same_type(r.ret, im.ret):
                    self.error(TypeCheckError(f"{r.display()} не может реализовать {im.display()}: "
                                              f"разные типы результата", c.decl.pos, type_notes(im.ret, r.ret)))
                elif not isinstance(r.owner, InterfaceSymbol) and r.access != "public":
                    self.error(SemanticError(f"{r.display()} реализует метод интерфейса "
                                             f"{im.display()} и должен быть public", c.decl.pos))
        if not c.is_abstract:
            missing = [m.display() for m in table.values() if m.is_abstract]
            if missing:
                self.error(SemanticError(f"класс {c.name} не абстрактный, но не реализует абстрактные методы",
                                         c.decl.pos, ["не реализовано: " + x for x in missing]))

    # ------------------------------------------------------------ 5. bodies
    def check_bodies(self) -> None:
        for c in self.classes:
            for f in c.fields.values():
                if f.decl.init is not None:
                    self.check_field_init(f)
                    self.ensure_const(f)
            for m in c.all_own_methods() + ([c.main] if c.main else []):
                if m.decl.body is None:
                    continue
                try:
                    self._check_method(c, m)
                except CompileError as e:
                    self.error(e)
            inits = [(f, f.decl.init) for f in c.fields.values() if not f.is_static and f.decl.init is not None]
            for ctor in c.ctors:
                try:
                    self._check_ctor(c, ctor, inits)
                except CompileError as e:
                    self.error(e)
            # порядок объявления; константные переменные (N4) инициализирует C
            statics = [(f, f.decl.init) for f in c.fields.values()
                       if f.is_static and f.decl.init is not None and f.const is None]
            if self.nontrivial_static_init(c):
                clinit = MethodSymbol(c, "<clinit>", [], T.VOID, {"static"}, None, c.decl.pos)
                self.bodies.append(MethodBody("clinit", clinit, c, [], A.Block(c.decl.pos, []),
                                              field_inits=statics))

    def field_ctx(self, f: FieldSymbol) -> Ctx:
        return Ctx(f.owner, None, f.is_static, not f.is_static, Scope(), in_field_init=True, field_index=f.index)

    def check_field_init(self, f: FieldSymbol) -> None:
        """Инициализатор поля проверяется один раз: при обходе тел или раньше,
        если значение понадобилось как константа (N4)."""
        if f.init_checked:
            return
        f.init_checked = True
        init = f.decl.init
        try:
            t = self.expr(init, self.field_ctx(f))
            self.require_assignable(f.type, t, init.pos, f"инициализация поля {f.name}")
        except CompileError as e:
            self.error(e)

    def ensure_const(self, f: FieldSymbol) -> None:
        """Константная переменная: final, примитив или String, константный
        инициализатор. Циклические зависимости делают значения неконстантными."""
        if f.const_state != 0:
            return
        if not (f.is_final and f.decl.init is not None and (f.type.is_primitive or T.is_string(f.type))):
            f.const_state = 2
            return
        f.const_state = 1
        self.check_field_init(f)
        f.const = getattr(f.decl.init, "const", None)
        f.const_state = 2

    def _new_scope_with_params(self, m: MethodSymbol) -> tuple[Scope, list[LocalSymbol]]:
        scope = Scope()
        params = []
        decl_params = m.decl.params if m.decl is not None else []
        for (name, ty), p in zip(m.params, decl_params):
            sym = LocalSymbol(name, ty, p.pos, is_param=True)
            scope.declare(sym)
            params.append(sym)
        return scope, params

    def _check_method(self, c: ClassSymbol, m: MethodSymbol) -> None:
        scope, params = self._new_scope_with_params(m)
        ctx = Ctx(c, m, m.is_static, False, scope)
        self.block(m.decl.body, ctx, new_scope=False)
        self.bodies.append(MethodBody("main" if m.is_static else "method", m, c, params, m.decl.body))

    def _check_ctor(self, c: ClassSymbol, ctor: MethodSymbol, inits) -> None:
        if ctor.implicit:
            body = A.Block(c.decl.pos, [], c.decl.pos)
            scope, params = Scope(), []
        else:
            body = ctor.decl.body
            scope, params = self._new_scope_with_params(ctor)
        ctx = Ctx(c, ctor, False, True, scope)
        stmts = body.stmts
        if stmts and isinstance(stmts[0], A.SuperCtorCall):
            call, rest = stmts[0], stmts[1:]
        else:
            call, rest = A.SuperCtorCall(body.pos, [], implicit=True), stmts
        for s in rest:
            if isinstance(s, A.SuperCtorCall):
                raise SemanticError("вызов super(...) должен быть первым оператором конструктора", s.pos)
        parent = c.parent
        pre = derive(ctx, pre_super=True)
        arg_types = [self.value(a, pre) for a in call.args]
        if parent is T.OBJECT_SYM:
            if arg_types:
                raise TypeCheckError("конструктор Object не принимает аргументов", call.pos)
            call.info = None
        else:
            try:
                target = self.resolve_overload(parent.ctors, arg_types, call.pos, f"конструктора {parent.name}")
            except CompileError:
                if call.implicit:
                    raise SemanticError(f"у родителя {parent.name} нет конструктора без аргументов: "
                                        f"вызовите super(...) явно первым оператором конструктора",
                                        ctor.pos) from None
                raise
            if target.is_private:
                raise SemanticError(f"конструктор {target.display()} недоступен (private)", call.pos)
            call.info = target
        rest_block = A.Block(body.pos, list(rest), body.end_pos)
        self.block(rest_block, ctx, new_scope=False)
        self.bodies.append(MethodBody("ctor", ctor, c, params, rest_block, super_call=call,
                                      field_inits=list(inits)))

    # ------------------------------------------------------------ statements
    def block(self, b: A.Block, ctx: Ctx, new_scope: bool = True) -> None:
        inner = derive(ctx, scope=Scope(ctx.scope)) if new_scope else ctx
        for s in b.stmts:
            try:
                self.stmt(s, inner)
            except CompileError as e:
                self.error(e)

    def sub_ctx(self, ctx: Ctx, loop: bool = False) -> Ctx:
        return derive(ctx, scope=Scope(ctx.scope), loop_depth=ctx.loop_depth + (1 if loop else 0))

    def stmt(self, s: A.Stmt, ctx: Ctx) -> None:
        if isinstance(s, A.Block):
            self.block(s, ctx)
        elif isinstance(s, A.LocalVarDecl):
            ty = self.resolve_type(s.type)
            s.symbols = []
            for d in s.declarators:
                if d.init is not None:
                    t = self.value(d.init, ctx)
                    self.require_assignable(ty, t, d.init.pos, f"инициализация переменной {d.name}")
                sym = LocalSymbol(d.name, ty, d.pos)
                ctx.scope.declare(sym)
                s.symbols.append(sym)
        elif isinstance(s, A.ExprStmt):
            self.expr(s.expr, ctx, statement=True)
        elif isinstance(s, A.EmptyStmt):
            pass
        elif isinstance(s, A.If):
            self.require_boolean(s.cond, ctx, "условие if")
            self.stmt(s.then, self.sub_ctx(ctx))
            if s.els is not None:
                self.stmt(s.els, self.sub_ctx(ctx))
        elif isinstance(s, A.While):
            self.require_boolean(s.cond, ctx, "условие while")
            self.stmt(s.body, self.sub_ctx(ctx, loop=True))
        elif isinstance(s, A.For):
            fctx = self.sub_ctx(ctx)
            for init in s.init:
                self.stmt(init, fctx)
            if s.cond is not None:
                self.require_boolean(s.cond, fctx, "условие for")
            for u in s.update:
                self.expr(u, fctx, statement=True)
            self.stmt(s.body, self.sub_ctx(fctx, loop=True))
        elif isinstance(s, A.ForEach):
            ty = self.resolve_type(s.type)
            at = self.value(s.iterable, ctx)
            if not isinstance(at, T.ArrayType):
                raise TypeCheckError("foreach применим только к одномерным массивам", s.iterable.pos,
                                     [f"тип выражения: {at}"])
            if not T.same_type(at.elem, ty):
                raise TypeCheckError("тип переменной foreach должен совпадать с типом элемента массива",
                                     s.type.pos, type_notes(at.elem, ty))
            fctx = self.sub_ctx(ctx, loop=True)
            sym = LocalSymbol(s.name, ty, s.pos)
            fctx.scope.declare(sym)
            s.symbol = sym
            self.stmt(s.body, fctx)
        elif isinstance(s, (A.Break, A.Continue)):
            if ctx.loop_depth == 0:
                word = "break" if isinstance(s, A.Break) else "continue"
                raise SemanticError(f"{word} вне цикла", s.pos)
        elif isinstance(s, A.Return):
            m = ctx.method
            ret = T.VOID if m.is_ctor else m.ret
            if s.value is None:
                if ret is not T.VOID:
                    raise TypeCheckError(f"метод должен вернуть значение типа {ret}", s.pos)
            else:
                if ret is T.VOID:
                    raise TypeCheckError("return со значением в методе void/конструкторе", s.value.pos)
                t = self.value(s.value, ctx)
                self.require_assignable(ret, t, s.value.pos, "возвращаемое значение")
        elif isinstance(s, A.Throw):
            t = self.value(s.value, ctx)
            ok = t is T.NULL or (isinstance(t, T.ClassType) and t.sym.is_subclass_of(self.throwable))
            if not ok:
                raise TypeCheckError("throw требует выражения типа Throwable или его подкласса (или null)",
                                     s.value.pos, type_notes("Throwable", t))
        elif isinstance(s, A.SuperCtorCall):
            raise SemanticError("вызов super(...) допустим только первым оператором конструктора", s.pos)
        else:
            raise SemanticError(f"неподдерживаемый оператор {type(s).__name__}", s.pos)

    def require_boolean(self, e: A.Expr, ctx: Ctx, what: str) -> None:
        t = self.value(e, ctx)
        if t is not T.BOOLEAN:
            raise TypeCheckError(f"{what} должно иметь тип boolean (числа не преобразуются в boolean)",
                                 e.pos, type_notes("boolean", t))

    def require_assignable(self, target: T.Type, value: T.Type, pos, what: str) -> None:
        if value is T.VOID:
            raise TypeCheckError(f"{what}: выражение типа void не имеет значения", pos)
        if not T.assignable(target, value):
            raise TypeCheckError(f"{what}: несовместимые типы (неявных преобразований нет)", pos,
                                 type_notes(target, value))

    # ------------------------------------------------------------ expressions
    def expr(self, e: A.Expr, ctx: Ctx, statement: bool = False) -> T.Type:
        e.const = None
        method = getattr(self, "e_" + type(e).__name__)
        t = method(e, ctx)
        e.ty = t
        if not statement and t is T.VOID:
            raise TypeCheckError("выражение типа void не имеет значения", e.pos)
        return t

    def value(self, e: A.Expr, ctx: Ctx) -> T.Type:
        t = self.expr(e, ctx)
        if isinstance(e.info, BuiltinName):
            raise SemanticError(f"'{e.info.name}' не является значением", e.pos)
        if isinstance(e.info, TypeName):
            raise SemanticError(f"'{e.info.sym.name}' — имя типа, а не значение", e.pos)
        return t

    def fold(self, e: A.Expr, fn, *args) -> None:
        try:
            e.const = fn(*args)
        except CF.FoldError as err:
            raise TypeCheckError(str(err), e.pos) from None

    def e_Literal(self, e: A.Literal, ctx: Ctx) -> T.Type:
        t = {"int": T.INT, "long": T.LONG, "float": T.FLOAT, "double": T.DOUBLE, "char": T.CHAR,
             "string": T.STRING, "boolean": T.BOOLEAN, "null": T.NULL}[e.kind]
        if e.kind in ("float", "double"):
            e.const = e.value.value
        elif e.kind != "null":
            e.const = e.value
        return t

    def require_instance(self, ctx: Ctx, pos, what: str = "this") -> None:
        if ctx.is_static:
            where = "в статическом инициализаторе" if ctx.in_field_init else "в статическом методе main"
            raise SemanticError(f"'{what}' недоступен {where}", pos)
        if ctx.pre_super:
            raise SemanticError(f"'{what}' недоступен до вызова конструктора родителя (аргументы super(...))",
                                pos)

    def e_This(self, e: A.This, ctx: Ctx) -> T.Type:
        self.require_instance(ctx, e.pos)
        return T.ClassType(ctx.cls)

    def e_Name(self, e: A.Name, ctx: Ctx) -> T.Type:
        sym = ctx.scope.lookup(e.name)
        if sym is not None:
            e.info = LocalRef(sym)
            return sym.type
        if ctx.cls is not None:
            f = self.h.field_lookup(ctx.cls, e.name)
            if f is not None:
                if (ctx.in_field_init and f.owner is ctx.cls and f.is_static == ctx.is_static
                        and f.index >= ctx.field_index):
                    raise SemanticError(f"опережающая ссылка на поле '{e.name}' в инициализаторе "
                                        f"(поле объявлено ниже или инициализируется сейчас)", e.pos)
                if f.is_static:
                    e.info = StaticRef(f)
                else:
                    self.require_instance(ctx, e.pos, e.name)
                    e.info = FieldRef(None, f)
                self.ensure_const(f)
                e.const = f.const
                return f.type
        if e.name in ("System", "Math", "String"):
            e.info = BuiltinName(e.name)
            return _BUILTIN_NAME_TYPE
        tsym = self.globals.lookup(e.name)
        if tsym is not None:
            e.info = TypeName(tsym)
            return _TYPE_NAME_TYPE
        raise SemanticError(f"неизвестное имя '{e.name}'", e.pos)

    def check_field_access(self, f: FieldSymbol, ctx: Ctx, pos) -> None:
        if f.is_private and ctx.cls is not f.owner:
            raise SemanticError(f"поле {f!r} недоступно (private)", pos)

    def check_method_access(self, m: MethodSymbol, ctx: Ctx, pos) -> None:
        if m.is_private and ctx.cls is not m.owner:
            raise SemanticError(f"метод {m.display()} недоступен (private)", pos)

    def e_FieldAccess(self, e: A.FieldAccess, ctx: Ctx) -> T.Type:
        target = e.target
        tt = self.expr(target, ctx)
        info = target.info
        pos = e.name_pos or e.pos
        if isinstance(info, BuiltinName):
            if info.name == "System" and e.name == "out":
                e.info = BuiltinName("System.out")
                return _BUILTIN_NAME_TYPE
            if info.name == "Math" and e.name in MATH_CONSTANTS:
                e.info = MathConst(e.name)
                e.const = MATH_CONSTANTS[e.name]
                return T.DOUBLE
            raise SemanticError(f"неизвестный член '{info.name}.{e.name}'", pos)
        if isinstance(info, TypeName):
            sym = info.sym
            if not isinstance(sym, ClassSymbol) or sym.builtin:
                raise SemanticError(f"у типа {sym.name} нет static-полей", pos)
            f = self.h.field_lookup(sym, e.name)
            if f is None:
                raise SemanticError(f"в классе {sym.name} нет поля '{e.name}'", pos)
            self.check_field_access(f, ctx, pos)
            if not f.is_static:
                raise SemanticError(f"поле экземпляра {f!r} нельзя читать через имя класса", pos)
            e.info = StaticRef(f)
            self.ensure_const(f)
            e.const = f.const
            return f.type
        if isinstance(tt, T.ArrayType):
            if e.name == "length":
                e.info = ArrayLengthRef()
                return T.INT
            raise SemanticError(f"у массива нет поля '{e.name}' (есть только length)", pos)
        if tt is T.NULL:
            raise TypeCheckError("обращение к полю null", pos)
        if isinstance(tt, T.ClassType) and not tt.sym.builtin:
            f = self.h.field_lookup(tt.sym, e.name)
            if f is None:
                raise SemanticError(f"в классе {tt.sym.name} нет поля '{e.name}'", pos)
            self.check_field_access(f, ctx, pos)
            if f.is_static:
                raise SemanticError(f"static-поле {f!r} доступно только как {f.owner.name}.{f.name} "
                                    f"(доступ через экземпляр запрещён)", pos)
            e.info = FieldRef(target, f)
            return f.type
        raise SemanticError(f"у типа {tt} нет поля '{e.name}'", pos)

    def e_SuperFieldAccess(self, e: A.SuperFieldAccess, ctx: Ctx) -> T.Type:
        self.require_instance(ctx, e.pos, "super")
        parent = ctx.cls.parent
        f = None if parent.builtin else self.h.field_lookup(parent, e.name)
        if f is None or f.is_private:
            raise SemanticError(f"у родителя {parent.name} нет доступного поля '{e.name}'", e.pos)
        if f.is_static:
            raise SemanticError(f"static-поле {f!r} доступно только как {f.owner.name}.{f.name}", e.pos)
        e.info = FieldRef(None, f)
        return f.type

    # ---------------------------------------------------------- calls
    def resolve_overload(self, candidates: list[MethodSymbol], arg_types: list[T.Type], pos, what: str
                         ) -> MethodSymbol:
        matches = [m for m in candidates
                   if len(m.params) == len(arg_types)
                   and all(T.assignable(pt, at) for pt, at in zip(m.param_types, arg_types))]
        shown = ", ".join(str(t) for t in arg_types)
        if not matches:
            notes = ["кандидаты: " + "; ".join(m.display() for m in candidates)] if candidates else []
            raise TypeCheckError(f"нет {what} с точно совпадающими типами аргументов ({shown})", pos, notes)
        if len(matches) > 1:
            raise TypeCheckError(f"неоднозначный вызов {what} ({shown})", pos,
                                 ["подходят: " + "; ".join(m.display() for m in matches)])
        return matches[0]

    def args(self, args: list[A.Expr], ctx: Ctx) -> list[T.Type]:
        return [self.value(a, ctx) for a in args]

    def e_MethodCall(self, e: A.MethodCall, ctx: Ctx) -> T.Type:
        if e.target is None:
            self.require_instance(ctx, e.pos, f"{e.name}(...) без объекта")
            return self._call_on_class(e, ctx, ctx.cls, None)
        tt = self.expr(e.target, ctx)
        info = e.target.info
        if isinstance(info, BuiltinName):
            return self._builtin_static(e, ctx, f"{info.name}.{e.name}")
        if isinstance(info, TypeName):
            raise SemanticError(f"вызов {info.sym.name}.{e.name}(...): пользовательские статические методы "
                                f"не поддерживаются", e.pos)
        if tt is T.NULL:
            raise TypeCheckError("вызов метода у null", e.pos)
        if isinstance(tt, T.ClassType):
            if tt.sym.builtin:
                return self._call_on_builtin_class(e, ctx, tt.sym)
            return self._call_on_class(e, ctx, tt.sym, e.target)
        if isinstance(tt, T.InterfaceType):
            arg_types = self.args(e.args, ctx)
            table = {**self.object_methods, **self.h.iface_methods(tt.sym)}
            cands = [m for m in table.values() if m.name == e.name]
            m = self.resolve_overload(cands, arg_types, e.pos, f"метода {tt.sym.name}.{e.name}")
            if m.sig in self.object_methods:
                e.info = CallInfo("object", m, e.target)
            else:
                e.info = CallInfo("interface", m, e.target, iface=tt.sym)
            return m.ret
        if isinstance(tt, T.ArrayType):
            arg_types = self.args(e.args, ctx)
            cands = [m for m in self.object_methods.values() if m.name == e.name]
            m = self.resolve_overload(cands, arg_types, e.pos, f"метода массива {e.name}")
            e.info = CallInfo("object", m, e.target)
            return m.ret
        raise TypeCheckError(f"у типа {tt} нет методов", e.pos)

    def _call_on_builtin_class(self, e: A.MethodCall, ctx: Ctx, sym: ClassSymbol) -> T.Type:
        arg_types = self.args(e.args, ctx)
        table = dict(self.object_methods)
        for ms in sym.methods.values():
            for m in ms:
                table[m.sig] = m
        cands = [m for m in table.values() if m.name == e.name]
        m = self.resolve_overload(cands, arg_types, e.pos, f"метода {sym.name}.{e.name}")
        if m.owner is T.OBJECT_SYM:
            e.info = CallInfo("object", m, e.target)
        else:
            e.info = CallInfo("builtin", m, e.target, builtin=m.builtin, has_recv=True)
        return m.ret

    def _call_on_class(self, e, ctx: Ctx, cls: ClassSymbol, recv) -> T.Type:
        arg_types = self.args(e.args, ctx)
        table = self.h.method_table(cls)
        cands = [m for sig, m in table.items() if sig[0] == e.name]
        m = self.resolve_overload(cands, arg_types, e.pos, f"метода {cls.name}.{e.name}")
        self.check_method_access(m, ctx, e.pos)
        if m.sig in self.object_methods:
            e.info = CallInfo("object", m, recv)
        elif m.is_private:
            e.info = CallInfo("direct", m, recv)
        else:
            e.info = CallInfo("virtual", m, recv, slot_owner=self.h.slot_owner(cls, m.sig))
        return m.ret

    def e_SuperMethodCall(self, e: A.SuperMethodCall, ctx: Ctx) -> T.Type:
        self.require_instance(ctx, e.pos, "super")
        parent = ctx.cls.parent
        arg_types = self.args(e.args, ctx)
        table = self.h.method_table(parent)
        cands = [m for sig, m in table.items() if sig[0] == e.name and not m.is_private]
        m = self.resolve_overload(cands, arg_types, e.pos, f"метода {parent.name}.{e.name}")
        if m.sig in self.object_methods:
            if m.name == "getClass":
                e.info = CallInfo("object", m, None)
                return m.ret
            impl = self.h.object_impl(parent, m.sig)
            if impl is None:
                e.info = CallInfo("super_object", m, None)
            else:
                e.info = CallInfo("direct", impl, None)
            return m.ret
        if m.is_abstract:
            raise SemanticError(f"нельзя вызвать абстрактный метод {m.display()} через super", e.pos)
        e.info = CallInfo("direct", m, None)
        return m.ret

    def _builtin_static(self, e: A.MethodCall, ctx: Ctx, qname: str) -> T.Type:
        overloads = BUILTIN_STATICS.get(qname)
        if overloads is None:
            raise SemanticError(f"неизвестный встроенный метод {qname}", e.pos)
        arg_types = self.args(e.args, ctx)
        fake = [MethodSymbol(T.OBJECT_SYM, qname, [(f"p{i}", t) for i, t in enumerate(ps)], ret, {"public"},
                             builtin=bid) for ps, ret, bid in overloads]
        m = self.resolve_overload(fake, arg_types, e.pos, f"метода {qname}")
        e.info = CallInfo("builtin", m, None, builtin=m.builtin)
        return m.ret

    def e_New(self, e: A.New, ctx: Ctx) -> T.Type:
        if e.type.is_primitive:
            raise SemanticError("new с примитивным типом без []", e.pos)
        sym = self.globals.lookup(e.type.name)
        if sym is None:
            raise SemanticError(f"неизвестный класс '{e.type.name}'", e.type.pos)
        if isinstance(sym, InterfaceSymbol):
            raise SemanticError(f"нельзя создать экземпляр интерфейса {sym.name}", e.pos)
        arg_types = self.args(e.args, ctx)
        if sym is T.OBJECT_SYM:
            if arg_types:
                raise TypeCheckError("конструктор Object не принимает аргументов", e.pos)
            e.info = None
            return T.OBJECT
        if sym.builtin:
            raise SemanticError(f"создание {sym.name} через new не поддерживается", e.pos)
        if sym.is_abstract:
            raise SemanticError(f"нельзя создать экземпляр абстрактного класса {sym.name}", e.pos)
        ctor = self.resolve_overload(sym.ctors, arg_types, e.pos, f"конструктора {sym.name}")
        if ctor.is_private and ctx.cls is not sym:
            raise SemanticError(f"конструктор {ctor.display()} недоступен (private)", e.pos)
        e.info = ctor
        return T.ClassType(sym)

    def e_NewArray(self, e: A.NewArray, ctx: Ctx) -> T.Type:
        elem = self.resolve_type(e.elem)
        lt = self.value(e.length, ctx)
        if lt is not T.INT:
            raise TypeCheckError("размер массива должен иметь тип int", e.length.pos, type_notes("int", lt))
        return T.ArrayType(elem)

    def e_ArrayIndex(self, e: A.ArrayIndex, ctx: Ctx) -> T.Type:
        at = self.value(e.array, ctx)
        if not isinstance(at, T.ArrayType):
            raise TypeCheckError("индексация применима только к массивам", e.array.pos, [f"тип: {at}"])
        it = self.value(e.index, ctx)
        if it is not T.INT:
            raise TypeCheckError("индекс массива должен иметь тип int", e.index.pos, type_notes("int", it))
        return at.elem

    def e_Unary(self, e: A.Unary, ctx: Ctx) -> T.Type:
        t = self.value(e.operand, ctx)
        try:
            rt = T.unary_result(e.op, t)
        except OpError as err:
            raise TypeCheckError(err.message, e.pos, err.notes)
        if e.operand.const is not None:
            self.fold(e, CF.unary, e.op, t, e.operand.const)
        return rt

    def lvalue(self, e: A.Expr, ctx: Ctx) -> T.Type:
        t = self.value(e, ctx)
        info = e.info
        if isinstance(info, ArrayLengthRef):
            raise SemanticError("поле length массива нельзя изменять", e.pos)
        if isinstance(info, MathConst):
            raise SemanticError("константу Math нельзя изменять", e.pos)
        if isinstance(info, StaticRef) and info.field.is_final:
            raise SemanticError(f"static final поле {info.field!r} нельзя изменять", e.pos)
        if isinstance(info, FieldRef) and info.field.is_final:
            f = info.field
            own_this = info.obj is None or isinstance(info.obj, A.This)
            if not (ctx.is_ctor and not ctx.in_field_init and ctx.cls is f.owner and own_this
                    and not isinstance(e, A.SuperFieldAccess) and f.decl.init is None):
                raise SemanticError(f"final-поле {f!r} можно присвоить только в конструкторе своего класса "
                                    f"(и только если у него нет инициализатора)", e.pos)
        if not isinstance(info, (LocalRef, FieldRef, StaticRef)) and not isinstance(e, A.ArrayIndex):
            raise SemanticError("левая часть не является переменной", e.pos)
        e.const = None          # переменная как цель присваивания — не константа
        return t

    def e_IncDec(self, e: A.IncDec, ctx: Ctx) -> T.Type:
        t = self.lvalue(e.target, ctx)
        if not T.is_numeric(t):
            raise TypeCheckError(f"'{e.op}' требует числовой переменной", e.pos, [f"тип: {t}"])
        return t

    def e_Binary(self, e: A.Binary, ctx: Ctx) -> T.Type:
        lt = self.value(e.left, ctx)
        rt = self.value(e.right, ctx)
        try:
            res = T.binary_result(e.op, lt, rt)
        except OpError as err:
            raise TypeCheckError(err.message, e.pos, err.notes)
        a, b = e.left.const, e.right.const
        if a is not None and b is not None and T.same_type(lt, rt):
            self.fold(e, CF.binary, e.op, lt, a, b)
        return res

    def e_Logical(self, e: A.Logical, ctx: Ctx) -> T.Type:
        for side in (e.left, e.right):
            t = self.value(side, ctx)
            if t is not T.BOOLEAN:
                raise TypeCheckError(f"операнды '{e.op}' должны иметь тип boolean", side.pos,
                                     type_notes("boolean", t))
        a, b = e.left.const, e.right.const
        if a is not None and b is not None:
            e.const = (a and b) if e.op == "&&" else (a or b)
        return T.BOOLEAN

    def e_Conditional(self, e: A.Conditional, ctx: Ctx) -> T.Type:
        ct = self.value(e.cond, ctx)
        if ct is not T.BOOLEAN:
            raise TypeCheckError("условие ?: должно иметь тип boolean", e.cond.pos, type_notes("boolean", ct))
        a = self.value(e.then, ctx)
        b = self.value(e.els, ctx)
        if T.same_type(a, b):
            res = a
        elif a is T.NULL and b.is_reference:
            res = b
        elif b is T.NULL and a.is_reference:
            res = a
        else:
            raise TypeCheckError("ветви ?: должны иметь одинаковый тип (неявных преобразований нет)", e.pos,
                                 [f"ветви: {a} и {b}"])
        c, x, y = e.cond.const, e.then.const, e.els.const
        if c is not None and x is not None and y is not None:
            e.const = x if c else y
        return res

    def e_Assign(self, e: A.Assign, ctx: Ctx) -> T.Type:
        tt = self.lvalue(e.target, ctx)
        vt = self.value(e.value, ctx)
        if e.op == "=":
            self.require_assignable(tt, vt, e.value.pos, "присваивание")
            return tt
        op = e.op[:-1]
        try:
            rt = T.binary_result(op, tt, vt)
        except OpError as err:
            raise TypeCheckError(err.message, e.pos, err.notes)
        if not T.same_type(rt, tt):
            raise TypeCheckError(f"результат '{op}' не совпадает с типом переменной", e.pos, type_notes(tt, rt))
        return tt

    def e_Cast(self, e: A.Cast, ctx: Ctx) -> T.Type:
        dst = self.resolve_type(e.type)
        src = self.value(e.operand, ctx)
        try:
            e.info = T.classify_cast(src, dst, self.h)
        except OpError as err:
            raise TypeCheckError(err.message, e.pos, err.notes)
        v = e.operand.const
        if v is not None:
            if e.info.kind == "prim":
                self.fold(e, CF.cast, src, dst, v)
            elif e.info.kind == "identity":
                e.const = v
        return dst

    def e_InstanceOf(self, e: A.InstanceOf, ctx: Ctx) -> T.Type:
        dst = self.resolve_type(e.type)
        src = self.value(e.operand, ctx)
        try:
            e.info = T.classify_instanceof(src, dst, self.h)
        except OpError as err:
            raise TypeCheckError(err.message, e.pos, err.notes)
        return T.BOOLEAN

    # ------------------------------------------------------------ entry
    def find_entry(self):
        mains = [c for c in self.classes if c.main is not None]
        wanted = self.main_class
        if wanted is not None:
            mains = [c for c in mains if c.name == wanted]
            if not mains:
                self.error(SemanticError(f"класс {wanted} с public static void main(String[] args) не найден"))
                return None, None
        if not mains:
            self.error(SemanticError("не найдена точка входа public static void main(String[] args)"))
            return None, None
        if len(mains) > 1:
            self.error(SemanticError("несколько точек входа main: " + ", ".join(c.name for c in mains)
                                     + "; выберите одну опцией --main"))
            return None, None
        return mains[0], mains[0].main


def analyze(units: list[A.CompilationUnit], main_class: str | None = None) -> AnalyzedProgram:
    an = Analyzer(units)
    an.main_class = main_class
    return an.run()
