"""Семантический анализ (п. 17 ТЗ).

Этапы:
  1. объявления типов;  2. заголовки (родители, интерфейсы) и циклы;
  3. члены классов;     4. проверки наследования (override, final, abstract,
     интерфейсы, конструкторы);  5. тела методов (типы, разрешение имён);
  6. flow-анализ (достижимость, возвраты, definite assignment) — flow.py.

Результат — аннотированное AST: у каждого выражения есть `ty` и `info`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import ast_nodes as A
from . import type_system as T
from .errors import CompilationFailed, CompileError, SemanticError, TypeCheckError, type_notes
from .inheritance import Hierarchy
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
    obj: Optional[A.Expr]          # None — неявный this
    path: tuple                    # от статического класса obj до владельца поля
    field: FieldSymbol


@dataclass
class ArrayLengthRef:
    pass


@dataclass
class Qualifier:
    base: Optional[A.Expr]         # None — this
    path: tuple
    cls: ClassSymbol


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
    recv_path: tuple = ()          # upcast-путь от статического класса recv (квалификатор)
    view: object = None            # ClassSymbol/InterfaceSymbol представления
    impl_path: tuple = ()          # direct: путь от представления до подобъекта реализации
    builtin: Optional[str] = None
    has_recv: bool = False         # у builtin есть вычисляемый получатель


@dataclass
class AnalyzedProgram:
    classes: list[ClassSymbol]
    interfaces: list[InterfaceSymbol]
    hierarchy: Hierarchy
    entry: MethodSymbol
    entry_class: ClassSymbol
    bodies: list["MethodBody"]
    object_methods: dict


@dataclass
class MethodBody:
    method: MethodSymbol
    cls: ClassSymbol
    params: list[LocalSymbol]
    body: A.Block
    super_calls: list[A.SuperCtorCall] = field(default_factory=list)
    field_inits: list[tuple[FieldSymbol, A.Expr]] = field(default_factory=list)


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
MATH_CONSTANTS = {"PI": T.DOUBLE, "E": T.DOUBLE}


# ---------------------------------------------------------------- context
@dataclass
class Ctx:
    cls: Optional[ClassSymbol]
    method: Optional[MethodSymbol]
    is_static: bool
    is_ctor: bool
    scope: Scope
    loop_depth: int = 0
    in_field_init: bool = False


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
        for sym in T.BUILTIN_CLASSES:
            self.globals.types[sym.name] = sym

    # ------------------------------------------------------------ driver
    def error(self, err: CompileError) -> None:
        self.errors.append(err)

    def checkpoint(self) -> None:
        if self.errors:
            raise CompilationFailed(self.errors)

    def run(self) -> AnalyzedProgram:
        self.declare_types()
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
        return AnalyzedProgram(self.ordered_classes(), self.interfaces, self.h, entry, entry_cls,
                               self.bodies, self.object_methods)

    def ordered_classes(self) -> list[ClassSymbol]:
        """Классы в топологическом порядке: родители раньше потомков."""
        out: list[ClassSymbol] = []
        seen: set = set()

        def visit(c: ClassSymbol) -> None:
            if c in seen:
                return
            seen.add(c)
            for p in c.parents:
                visit(p)
            out.append(c)

        for c in self.classes:
            visit(c)
        return out

    # ------------------------------------------------------------ 1. types
    def declare_types(self) -> None:
        for unit in self.units:
            for decl in unit.types:
                if decl.name in RESERVED_TYPE_NAMES:
                    self.error(SemanticError(f"имя '{decl.name}' зарезервировано встроенным типом", decl.pos))
                    continue
                mods = {m.name for m in decl.modifiers}
                if isinstance(decl, A.ClassDecl):
                    sym = ClassSymbol(decl.name, mods, decl)
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

    # ------------------------------------------------------------ 2. headers
    def resolve_headers(self) -> None:
        for c in self.classes:
            decl: A.ClassDecl = c.decl
            seen = set()
            names = [p.name for p in decl.parents]
            for ref in decl.parents:
                sym = self.globals.lookup(ref.name)
                if ref.name in seen:
                    self.error(SemanticError(f"родитель '{ref.name}' указан повторно", ref.pos))
                    continue
                seen.add(ref.name)
                if sym is T.OBJECT_SYM:
                    if len(names) > 1:
                        self.error(SemanticError("Object нельзя указывать вместе с другими родителями "
                                                 "(он всегда единственный общий корень)", ref.pos))
                    continue
                if sym is None:
                    self.error(SemanticError(f"неизвестный класс '{ref.name}'", ref.pos))
                elif not isinstance(sym, ClassSymbol):
                    self.error(SemanticError(f"'{ref.name}' — интерфейс; классы наследуют интерфейсы через "
                                             f"implements", ref.pos))
                elif sym.builtin:
                    self.error(SemanticError(f"нельзя наследоваться от встроенного класса {ref.name}", ref.pos))
                elif sym.is_final:
                    self.error(SemanticError(f"нельзя наследоваться от final-класса {ref.name}", ref.pos))
                elif sym is c:
                    self.error(SemanticError(f"класс {c.name} не может наследоваться от самого себя", ref.pos))
                else:
                    c.parents.append(sym)
            seen = set()
            for ref in decl.interfaces:
                sym = self.globals.lookup(ref.name)
                if ref.name in seen:
                    self.error(SemanticError(f"интерфейс '{ref.name}' указан повторно", ref.pos))
                    continue
                seen.add(ref.name)
                if sym is None:
                    self.error(SemanticError(f"неизвестный интерфейс '{ref.name}'", ref.pos))
                elif not isinstance(sym, InterfaceSymbol):
                    self.error(SemanticError(f"'{ref.name}' не является интерфейсом", ref.pos))
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
        def find_cycle(start, edges):
            stack = [(start, [start])]
            while stack:
                node, path = stack.pop()
                for nxt in edges(node):
                    if nxt is start:
                        return path + [start]
                    if nxt not in path:
                        stack.append((nxt, path + [nxt]))
            return None

        reported = set()
        for group, edges in ((self.classes, lambda c: c.parents), (self.interfaces, lambda i: i.supers)):
            for sym in group:
                cyc = find_cycle(sym, edges)
                if cyc and frozenset(cyc) not in reported:
                    reported.add(frozenset(cyc))
                    self.error(SemanticError("цикл в графе наследования: " + " -> ".join(s.name for s in cyc),
                                             sym.decl.pos))

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

    # ------------------------------------------------------------ 3. members
    def declare_members(self) -> None:
        for i in self.interfaces:
            for md in i.decl.methods:
                try:
                    ret = self.resolve_type(md.ret, allow_void=True)
                    params = self._params(md.params)
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
                if c.parents:
                    self.error(SemanticError(
                        f"класс {c.name} имеет родителей и должен явно объявить конструктор с вызовами "
                        + ", ".join(f"super.{p.name}(...)" for p in c.parents) + " (D7)", c.decl.pos))
                else:
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
        for bad in ("abstract", "static"):
            if bad in mods:
                what = "статические поля не поддерживаются" if bad == "static" else "поле не может быть abstract"
                raise SemanticError(what, fd.pos)
        self._check_access_mods(mods, fd.pos)
        ftype = self.resolve_type(fd.type)
        for d in fd.declarators:
            if d.name in c.fields:
                self.error(SemanticError(f"поле '{d.name}' уже объявлено в классе {c.name}", d.pos))
                continue
            if any(p.name == d.name for p in c.parents):
                self.error(SemanticError(f"поле '{d.name}' совпадает с именем родителя: "
                                         f"неоднозначный квалификатор (I4)", d.pos))
                continue
            c.fields[d.name] = FieldSymbol(c, d.name, ftype, set(mods), d, d.pos)

    def _check_access_mods(self, mods: set[str], pos) -> None:
        if len(mods & {"public", "protected", "private"}) > 1:
            raise SemanticError("несовместимые модификаторы доступа", pos)

    def _declare_method(self, c: ClassSymbol, md: A.MethodDecl) -> None:
        mods = {m.name for m in md.modifiers}
        self._check_access_mods(mods, md.pos)
        ret = self.resolve_type(md.ret, allow_void=True)
        params = self._params(md.params)
        m = MethodSymbol(c, md.name, params, ret, mods, md, md.pos)
        if "static" in mods:
            is_main = (md.name == "main" and ret is T.VOID and m.param_types == (T.ArrayType(T.STRING),)
                       and "public" in mods and not (mods & {"abstract", "final"}))
            if not is_main:
                raise SemanticError("статические методы не поддерживаются (кроме точки входа "
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
        if any(p.name == md.name for p in c.parents):
            pass  # имена методов и родителей различаются синтаксически (вызов со скобками)
        c.methods.setdefault(md.name, []).append(m)

    def _declare_ctor(self, c: ClassSymbol, cd: A.ConstructorDecl) -> None:
        mods = {m.name for m in cd.modifiers}
        if mods & {"abstract", "final", "static"}:
            raise SemanticError("конструктор допускает только модификаторы доступа", cd.pos)
        self._check_access_mods(mods, cd.pos)
        params = self._params(cd.params)
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

    def _ancestor_decls(self, c: ClassSymbol, sig) -> list[MethodSymbol]:
        out = []
        for anc in self.h.ancestors(c):
            m = anc.own_method(sig)
            if m is not None and not m.is_private:
                out.append(m)
        if sig in self.object_methods:
            out.append(self.object_methods[sig])
        return out

    def _check_class_inheritance(self, c: ClassSymbol) -> None:
        for m in c.all_own_methods():
            if m.is_private:
                continue
            for base in self._ancestor_decls(c, m.sig):
                if base.is_final:
                    self.error(SemanticError(f"метод {m.display()} переопределяет final-метод {base.display()}",
                                             m.pos))
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
                if r is None:
                    continue
                if not T.same_type(r.decl.ret, im.ret):
                    self.error(TypeCheckError(f"{r.decl.display()} не может реализовать {im.display()}: "
                                              f"разные типы результата", c.decl.pos,
                                              type_notes(im.ret, r.decl.ret)))
                elif not isinstance(r.decl.owner, InterfaceSymbol) and r.decl.access != "public":
                    self.error(SemanticError(f"{r.decl.display()} реализует метод интерфейса "
                                             f"{im.display()} и должен быть public", c.decl.pos))
        if not c.is_abstract:
            self._check_concrete(c)

    def _check_concrete(self, c: ClassSymbol) -> None:
        missing = []
        for sub in self.h.subobjects(c):
            for sig in self.h.vtable_sigs(sub.cls):
                r = self.h.effective(c, sub.path, sig)
                if r.decl.is_abstract:
                    where = self.h.path_str(c, sub.path)
                    missing.append(f"{r.decl.display()} (подобъект {where})")
        if missing:
            uniq = list(dict.fromkeys(missing))
            self.error(SemanticError(f"класс {c.name} не абстрактный, но не реализует абстрактные методы",
                                     c.decl.pos, ["не реализовано: " + x for x in uniq]))

    # ------------------------------------------------------------ 5. bodies
    def check_bodies(self) -> None:
        for c in self.classes:
            for m in c.all_own_methods() + ([c.main] if c.main else []):
                if m.decl.body is None:
                    continue
                try:
                    self._check_method(c, m)
                except CompileError as e:
                    self.error(e)
            inits = self._check_field_inits(c)
            for ctor in c.ctors:
                try:
                    self._check_ctor(c, ctor, inits)
                except CompileError as e:
                    self.error(e)

    def _check_field_inits(self, c: ClassSymbol) -> list[tuple[FieldSymbol, A.Expr]]:
        """Инициализаторы полей проверяются один раз; выполняются в каждом
        конструкторе после родителей и до тела (п. 4.2.7)."""
        out = []
        fake_ctor = c.ctors[0] if c.ctors else None
        for fname, f in c.fields.items():
            init = f.decl.init
            if init is None:
                continue
            try:
                fctx = Ctx(c, fake_ctor, False, True, Scope(), in_field_init=True)
                t = self.expr(init, fctx)
                self.require_assignable(f.type, t, init.pos, f"инициализация поля {fname}")
                out.append((f, init))
            except CompileError as e:
                self.error(e)
        return out

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
        self.bodies.append(MethodBody(m, c, params, m.decl.body))

    def _check_ctor(self, c: ClassSymbol, ctor: MethodSymbol, inits) -> None:
        if ctor.implicit:
            body = A.Block(c.decl.pos, [], c.decl.pos)
            scope, params = Scope(), []
        else:
            body = ctor.decl.body
            scope, params = self._new_scope_with_params(ctor)
        ctx = Ctx(c, ctor, False, True, scope)
        mb = MethodBody(ctor, c, params, body)
        # вызовы конструкторов родителей: первые операторы, по одному на родителя, по порядку (D7)
        stmts = body.stmts
        k = 0
        for parent in c.parents:
            if k >= len(stmts) or not isinstance(stmts[k], A.SuperCtorCall):
                pos = stmts[k].pos if k < len(stmts) else body.pos
                raise SemanticError(f"ожидается вызов конструктора родителя super.{parent.name}(...) "
                                    f"(вызовы родителей — первые операторы, в порядке объявления)", pos)
            call: A.SuperCtorCall = stmts[k]
            if call.parent != parent.name:
                raise SemanticError(f"ожидается super.{parent.name}(...), найдено super.{call.parent}(...): "
                                    f"порядок вызовов должен совпадать с порядком родителей", call.pos)
            args = [self.expr(a, ctx) for a in call.args]
            target = self.resolve_overload(parent.ctors, args, call.pos, f"конструктора {parent.name}")
            if target.is_private:
                raise SemanticError(f"конструктор {target.display()} недоступен (private)", call.pos)
            call.info = target
            mb.super_calls.append(call)
            k += 1
        for s in stmts[k:]:
            if isinstance(s, A.SuperCtorCall):
                if any(p.name == s.parent for p in c.parents):
                    raise SemanticError(f"повторный или поздний вызов super.{s.parent}(...): вызовы "
                                        f"конструкторов родителей должны идти первыми", s.pos)
                raise SemanticError(f"'{s.parent}' не является непосредственным родителем {c.name}", s.pos)
        mb.field_inits = list(inits)
        rest = A.Block(body.pos, stmts[k:], body.end_pos)
        self.block(rest, ctx, new_scope=False)
        mb.body = rest
        self.bodies.append(mb)

    # ------------------------------------------------------------ statements
    def block(self, b: A.Block, ctx: Ctx, new_scope: bool = True) -> None:
        inner = Ctx(ctx.cls, ctx.method, ctx.is_static, ctx.is_ctor,
                    Scope(ctx.scope) if new_scope else ctx.scope, ctx.loop_depth)
        for s in b.stmts:
            try:
                self.stmt(s, inner)
            except CompileError as e:
                self.error(e)

    def sub_ctx(self, ctx: Ctx, loop: bool = False) -> Ctx:
        return Ctx(ctx.cls, ctx.method, ctx.is_static, ctx.is_ctor, Scope(ctx.scope),
                   ctx.loop_depth + (1 if loop else 0))

    def stmt(self, s: A.Stmt, ctx: Ctx) -> None:
        if isinstance(s, A.Block):
            self.block(s, ctx)
        elif isinstance(s, A.LocalVarDecl):
            ty = self.resolve_type(s.type)
            s.symbols = []
            for d in s.declarators:
                if d.init is not None:
                    t = self.expr(d.init, ctx)
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
            at = self.expr(s.iterable, ctx)
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
                t = self.expr(s.value, ctx)
                self.require_assignable(ret, t, s.value.pos, "возвращаемое значение")
        elif isinstance(s, A.SuperCtorCall):
            raise SemanticError("вызов конструктора родителя допустим только в начале конструктора", s.pos)
        else:
            raise SemanticError(f"неподдерживаемый оператор {type(s).__name__}", s.pos)

    def require_boolean(self, e: A.Expr, ctx: Ctx, what: str) -> None:
        t = self.expr(e, ctx)
        if t is not T.BOOLEAN:
            raise TypeCheckError(f"{what} должно иметь тип boolean (числа не преобразуются в boolean)",
                                 e.pos, type_notes("boolean", t))

    def require_assignable(self, target: T.Type, value: T.Type, pos, what: str) -> None:
        if isinstance(value, T.QualifierType):
            raise SemanticError("квалификатор родителя не является выражением", pos)
        if value is T.VOID:
            raise TypeCheckError(f"{what}: выражение типа void не имеет значения", pos)
        if not T.assignable(target, value):
            raise TypeCheckError(f"{what}: несовместимые типы (неявных преобразований нет)", pos,
                                 type_notes(target, value))

    # ------------------------------------------------------------ expressions
    def expr(self, e: A.Expr, ctx: Ctx, statement: bool = False) -> T.Type:
        method = getattr(self, "e_" + type(e).__name__)
        t = method(e, ctx)
        e.ty = t
        if not statement and t is T.VOID:
            raise TypeCheckError("выражение типа void не имеет значения", e.pos)
        return t

    def value(self, e: A.Expr, ctx: Ctx) -> T.Type:
        t = self.expr(e, ctx)
        if isinstance(t, T.QualifierType):
            raise SemanticError(f"квалификатор родителя {t.sym.name} не является выражением", e.pos)
        if isinstance(e.info, BuiltinName):
            raise SemanticError(f"'{e.info.name}' не является значением", e.pos)
        return t

    def e_Literal(self, e: A.Literal, ctx: Ctx) -> T.Type:
        return {"int": T.INT, "long": T.LONG, "float": T.FLOAT, "double": T.DOUBLE, "char": T.CHAR,
                "string": T.STRING, "boolean": T.BOOLEAN, "null": T.NULL}[e.kind]

    def require_instance(self, ctx: Ctx, pos, what: str = "this") -> None:
        if ctx.is_static:
            raise SemanticError(f"'{what}' недоступен в статическом методе main", pos)

    def e_This(self, e: A.This, ctx: Ctx) -> T.Type:
        self.require_instance(ctx, e.pos)
        return T.ClassType(ctx.cls)

    def e_Name(self, e: A.Name, ctx: Ctx) -> T.Type:
        sym = ctx.scope.lookup(e.name)
        if sym is not None:
            e.info = LocalRef(sym)
            return sym.type
        if ctx.cls is not None:
            r = self.h.field_lookup(ctx.cls, e.name)
            if r is not None:
                self.require_instance(ctx, e.pos, e.name)
                self.check_field_access(r[1], ctx, e.pos)
                e.info = FieldRef(None, r[0], r[1])
                return r[1].type
            if any(p.name == e.name for p in ctx.cls.parents):
                self.require_instance(ctx, e.pos, e.name)
                e.info = Qualifier(None, (self._parent(ctx.cls, e.name),), self._parent(ctx.cls, e.name))
                return T.QualifierType(e.info.cls)
        if e.name in ("System", "Math", "String"):
            e.info = BuiltinName(e.name)
            return _BUILTIN_NAME_TYPE
        if self.globals.lookup(e.name) is not None:
            raise SemanticError(f"'{e.name}' — имя типа; статические члены не поддерживаются", e.pos)
        raise SemanticError(f"неизвестное имя '{e.name}'", e.pos)

    @staticmethod
    def _parent(c: ClassSymbol, name: str) -> ClassSymbol:
        return next(p for p in c.parents if p.name == name)

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
                return MATH_CONSTANTS[e.name]
            raise SemanticError(f"неизвестный член '{info.name}.{e.name}'", pos)
        if isinstance(tt, T.QualifierType):
            base_cls = tt.sym
            base_expr, base_path = info.base, info.path
            return self._member_field(e, ctx, base_cls, base_expr, base_path, pos)
        if isinstance(tt, T.ArrayType):
            if e.name == "length":
                e.info = ArrayLengthRef()
                return T.INT
            raise SemanticError(f"у массива нет поля '{e.name}' (есть только length)", pos)
        if tt is T.NULL:
            raise TypeCheckError("обращение к полю null", pos)
        if isinstance(tt, T.ClassType) and not tt.sym.builtin:
            return self._member_field(e, ctx, tt.sym, target, (), pos)
        raise SemanticError(f"у типа {tt} нет поля '{e.name}'", pos)

    def _member_field(self, e, ctx, cls: ClassSymbol, base_expr, base_path, pos) -> T.Type:
        is_parent = any(p.name == e.name for p in cls.parents)
        r = self.h.field_lookup(cls, e.name)
        if is_parent and r is not None:
            raise SemanticError(f"неоднозначный квалификатор '{e.name}': это и родитель, и поле класса "
                                f"{cls.name} (I4)", pos)
        if is_parent:
            parent = self._parent(cls, e.name)
            e.info = Qualifier(base_expr, base_path + (parent,), parent)
            return T.QualifierType(parent)
        if r is None:
            raise SemanticError(f"в классе {cls.name} нет поля '{e.name}'", pos)
        self.check_field_access(r[1], ctx, pos)
        e.info = FieldRef(base_expr, base_path + r[0], r[1])
        return r[1].type

    # ---------------------------------------------------------- calls
    def resolve_overload(self, candidates: list[MethodSymbol], arg_types: list[T.Type], pos, what: str
                         ) -> MethodSymbol:
        for t in arg_types:
            if isinstance(t, T.QualifierType):
                raise SemanticError("квалификатор родителя не является выражением", pos)
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
            return self._call_on_class(e, ctx, ctx.cls, None, (), implicit_this=True)
        tt = self.expr(e.target, ctx)
        info = e.target.info
        if isinstance(info, BuiltinName):
            return self._builtin_static(e, ctx, f"{info.name}.{e.name}")
        if isinstance(tt, T.QualifierType):
            return self._call_on_class(e, ctx, tt.sym, info.base, info.path, qualified=True)
        if tt is T.NULL:
            raise TypeCheckError("вызов метода у null", e.pos)
        if isinstance(tt, T.ClassType):
            if tt.sym.builtin:
                return self._call_on_builtin_class(e, ctx, tt.sym)
            return self._call_on_class(e, ctx, tt.sym, e.target, ())
        if isinstance(tt, T.InterfaceType):
            arg_types = self.args(e.args, ctx)
            table = {**{s: m for s, m in self.object_methods.items()}, **self.h.iface_methods(tt.sym)}
            cands = [m for m in table.values() if m.name == e.name]
            m = self.resolve_overload(cands, arg_types, e.pos, f"метода {tt.sym.name}.{e.name}")
            if m.sig in self.object_methods:
                e.info = CallInfo("object", m, e.target)
            else:
                e.info = CallInfo("interface", m, e.target, view=tt.sym)
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

    def _call_on_class(self, e, ctx: Ctx, cls: ClassSymbol, recv, recv_path, implicit_this=False,
                       qualified=False) -> T.Type:
        if implicit_this:
            if ctx.is_static:
                raise SemanticError(f"вызов метода '{e.name}' без объекта недоступен в статическом main", e.pos)
        arg_types = self.args(e.args, ctx)
        table = self.h.method_table(cls)
        cands = [r.decl for sig, r in table.items() if sig[0] == e.name]
        m = self.resolve_overload(cands, arg_types, e.pos, f"метода {cls.name}.{e.name}")
        self.check_method_access(m, ctx, e.pos)
        if m.sig in self.object_methods:
            e.info = CallInfo("object", m, recv, recv_path)
        elif m.is_private:
            if recv_path:
                raise SemanticError(f"private-метод {m.display()} нельзя вызвать через квалификатор", e.pos)
            e.info = CallInfo("direct", m, recv, (), view=cls, impl_path=table[m.sig].path)
        else:
            e.info = CallInfo("virtual", m, recv, recv_path, view=cls)
        return m.ret

    def e_SuperMethodCall(self, e: A.SuperMethodCall, ctx: Ctx) -> T.Type:
        self.require_instance(ctx, e.pos, "super")
        cls = ctx.cls
        if not any(p.name == e.parent for p in cls.parents):
            raise SemanticError(f"'{e.parent}' не является непосредственным родителем {cls.name}", e.pos)
        parent = self._parent(cls, e.parent)
        arg_types = self.args(e.args, ctx)
        table = self.h.method_table(parent)
        cands = [r.decl for sig, r in table.items() if sig[0] == e.name and not r.decl.is_private]
        m = self.resolve_overload(cands, arg_types, e.pos, f"метода {parent.name}.{e.name}")
        if m.sig in self.object_methods:
            r = self.h.objres(parent, m.sig)
            if r is None:
                e.info = CallInfo("super_object", m, None, (parent,), view=parent)
            else:
                e.info = CallInfo("direct", r.decl, None, (parent,), view=parent, impl_path=r.path)
            return m.ret
        r = table[m.sig]
        if r.decl.is_abstract:
            raise SemanticError(f"нельзя вызвать абстрактный метод {r.decl.display()} через super", e.pos)
        e.info = CallInfo("direct", r.decl, None, (parent,), view=parent, impl_path=r.path)
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
            return T.unary_result(e.op, t)
        except OpError as err:
            raise TypeCheckError(err.message, e.pos, err.notes)

    def lvalue(self, e: A.Expr, ctx: Ctx) -> T.Type:
        t = self.value(e, ctx)
        if isinstance(e.info, ArrayLengthRef):
            raise SemanticError("поле length массива нельзя изменять", e.pos)
        if isinstance(e.info, MathConst):
            raise SemanticError("константу Math нельзя изменять", e.pos)
        if isinstance(e.info, FieldRef) and e.info.field.is_final:
            f = e.info.field
            own_this = e.info.obj is None or isinstance(e.info.obj, A.This)
            if not (ctx.is_ctor and not ctx.in_field_init and ctx.cls is f.owner and own_this
                    and not e.info.path and f.decl.init is None):
                raise SemanticError(f"final-поле {f!r} можно присвоить только в конструкторе своего класса "
                                    f"(и только если у него нет инициализатора)", e.pos)
        if not isinstance(e.info, (LocalRef, FieldRef)) and not isinstance(e, A.ArrayIndex):
            raise SemanticError("левая часть не является переменной", e.pos)
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
            return T.binary_result(e.op, lt, rt)
        except OpError as err:
            raise TypeCheckError(err.message, e.pos, err.notes)

    def e_Logical(self, e: A.Logical, ctx: Ctx) -> T.Type:
        for side in (e.left, e.right):
            t = self.value(side, ctx)
            if t is not T.BOOLEAN:
                raise TypeCheckError(f"операнды '{e.op}' должны иметь тип boolean", side.pos,
                                     type_notes("boolean", t))
        return T.BOOLEAN

    def e_Conditional(self, e: A.Conditional, ctx: Ctx) -> T.Type:
        ct = self.value(e.cond, ctx)
        if ct is not T.BOOLEAN:
            raise TypeCheckError("условие ?: должно иметь тип boolean", e.cond.pos, type_notes("boolean", ct))
        a = self.value(e.then, ctx)
        b = self.value(e.els, ctx)
        if T.same_type(a, b):
            return a
        if a is T.NULL and b.is_reference:
            return b
        if b is T.NULL and a.is_reference:
            return a
        raise TypeCheckError("ветви ?: должны иметь одинаковый тип (неявных преобразований нет)", e.pos,
                             [f"ветви: {a} и {b}"])

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
        wanted = getattr(self, "main_class", None)
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


class _BuiltinNameType(T.Type):
    def __str__(self) -> str:
        return "<встроенное имя>"


_BUILTIN_NAME_TYPE = _BuiltinNameType()


def analyze(units: list[A.CompilationUnit], main_class: str | None = None) -> AnalyzedProgram:
    an = Analyzer(units)
    an.main_class = main_class
    return an.run()
