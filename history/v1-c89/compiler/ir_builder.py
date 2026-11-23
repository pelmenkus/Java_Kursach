"""Построение типизированного IR из проверенного аннотированного AST.

Каждое подвыражение вычисляется во временную переменную в порядке слева
направо, поэтому неуказанный в C порядок вычисления операндов не влияет на
поведение. Чтение локальной переменной копируется во временную: так значение
фиксируется до побочных эффектов правого операнда (`x + (x = 5)`).
"""
from __future__ import annotations

import struct
from contextlib import contextmanager

from . import ast_nodes as A
from . import ir
from . import type_system as T
from .errors import InternalCompilerError
from .lexer import FloatBits
from .semantic import (AnalyzedProgram, ArrayLengthRef, CallInfo, FieldRef, LocalRef, MathConst,
                       MethodBody, Qualifier)
from .symbol_table import ClassSymbol

BIN_OPS = {"+": "add", "-": "sub", "*": "mul", "/": "div", "%": "rem", "<<": "shl", ">>": "shr",
           ">>>": "ushr", "&": "and", "|": "or", "^": "xor"}
CMP_OPS = {"==": "eq", "!=": "ne", "<": "lt", ">": "gt", "<=": "le", ">=": "ge"}
MATH_BITS = {"PI": struct.unpack("<Q", struct.pack("<d", 3.141592653589793))[0],
             "E": struct.unpack("<Q", struct.pack("<d", 2.718281828459045))[0]}


class FunctionBuilder:
    def __init__(self, prog: "IRBuilder", fn: ir.Function):
        self.prog = prog
        self.fn = fn
        self.cur: list = fn.body.stmts
        self.locals: dict = {}
        self.loops: list[ir.Loop] = []

    # ------------------------------------------------------------ helpers
    def var(self, ty: T.Type, hint: str = "") -> ir.Var:
        v = ir.Var(self.prog.next_id(), ty, hint)
        self.fn.vars.append(v)
        return v

    def emit(self, s) -> None:
        self.cur.append(s)

    @contextmanager
    def into(self, block: ir.Block):
        saved = self.cur
        self.cur = block.stmts
        try:
            yield block
        finally:
            self.cur = saved

    def loc(self, pos) -> ir.Loc:
        return self.prog.location(pos, self.fn)

    def const(self, ty: T.Type, value) -> ir.Var:
        v = self.var(ty)
        self.emit(ir.Const(v, value))
        return v

    @property
    def self_var(self) -> ir.Var:
        if self.fn.self_var is None:
            raise InternalCompilerError("this в статическом контексте")
        return self.fn.self_var

    # ------------------------------------------------------------ statements
    def block(self, stmts) -> None:
        for s in stmts:
            self.stmt(s)

    def stmt(self, s: A.Stmt) -> None:
        if isinstance(s, A.Block):
            self.block(s.stmts)
        elif isinstance(s, A.LocalVarDecl):
            for d, sym in zip(s.declarators, s.symbols):
                v = self.var(sym.type, sym.name)
                self.locals[sym] = v
                if d.init is not None:
                    self.emit(ir.Move(v, self.rv(d.init)))
        elif isinstance(s, A.ExprStmt):
            self.rv(s.expr, discard=True)
        elif isinstance(s, A.EmptyStmt):
            pass
        elif isinstance(s, A.If):
            c = self.rv(s.cond)
            then = ir.Block()
            with self.into(then):
                self.stmt(s.then)
            els = None
            if s.els is not None:
                els = ir.Block()
                with self.into(els):
                    self.stmt(s.els)
            self.emit(ir.If(c, then, els))
        elif isinstance(s, A.While):
            self.loop(s.cond, s.body, [])
        elif isinstance(s, A.For):
            for init in s.init:
                self.stmt(init)
            self.loop(s.cond, s.body, s.update)
        elif isinstance(s, A.ForEach):
            self.foreach(s)
        elif isinstance(s, A.Break):
            self.emit(ir.Break())
        elif isinstance(s, A.Continue):
            loop = self.loops[-1]
            loop.has_continue = True
            self.emit(ir.Continue(loop))
        elif isinstance(s, A.Return):
            self.emit(ir.Return(self.rv(s.value) if s.value is not None else None))
        else:
            raise InternalCompilerError(f"оператор {type(s).__name__} не поддержан IR builder")

    def loop(self, cond, body, update) -> None:
        loop = ir.Loop(ir.Block(), None, ir.Block(), ir.Block(), self.prog.next_id())
        if cond is not None:
            with self.into(loop.cond_block):
                loop.cond = self.rv(cond)
        self.loops.append(loop)
        with self.into(loop.body):
            self.stmt(body)
        self.loops.pop()
        with self.into(loop.update):
            for u in update:
                self.rv(u, discard=True)
        self.emit(loop)

    def foreach(self, s: A.ForEach) -> None:
        arr = self.rv(s.iterable)
        n = self.var(T.INT, "n")
        self.emit(ir.ArrLen(n, arr, self.loc(s.iterable.pos)))
        i = self.const(T.INT, 0)
        loop = ir.Loop(ir.Block(), None, ir.Block(), ir.Block(), self.prog.next_id())
        with self.into(loop.cond_block):
            c = self.var(T.BOOLEAN)
            self.emit(ir.Cmp(c, "lt", i, n))
            loop.cond = c
        elem = self.var(s.symbol.type, s.symbol.name)
        self.locals[s.symbol] = elem
        self.loops.append(loop)
        with self.into(loop.body):
            self.emit(ir.GetElem(elem, arr, i, self.loc(s.pos)))
            self.stmt(s.body)
        self.loops.pop()
        with self.into(loop.update):
            one = self.const(T.INT, 1)
            self.emit(ir.BinOp(i, "add", i, one))
        self.emit(loop)

    # ------------------------------------------------------------ expressions
    def rv(self, e: A.Expr, discard: bool = False):
        fn = getattr(self, "x_" + type(e).__name__)
        return fn(e, discard) if fn.__code__.co_argcount == 3 else fn(e)

    def x_Literal(self, e: A.Literal):
        if e.kind == "string":
            v = self.var(T.STRING)
            self.emit(ir.StrLit(v, self.prog.string(e.value)))
            return v
        ty = e.ty
        return self.const(ty, e.value)

    def x_This(self, e):
        return self.self_var

    def x_Name(self, e: A.Name):
        info = e.info
        if isinstance(info, LocalRef):
            src = self.locals[info.sym]
            v = self.var(src.ty)
            self.emit(ir.Move(v, src))
            return v
        if isinstance(info, FieldRef):
            return self.load(self.lvalue(e))
        raise InternalCompilerError(f"имя {e.name} без значения в IR")

    def x_FieldAccess(self, e: A.FieldAccess):
        info = e.info
        if isinstance(info, ArrayLengthRef):
            arr = self.rv(e.target)
            v = self.var(T.INT)
            self.emit(ir.ArrLen(v, arr, self.loc(e.pos)))
            return v
        if isinstance(info, MathConst):
            return self.const(T.DOUBLE, FloatBits(MATH_BITS[info.name], 64))
        if isinstance(info, FieldRef):
            return self.load(self.lvalue(e))
        raise InternalCompilerError("квалификатор как значение")

    def receiver(self, recv_expr) -> tuple[ir.Var, bool]:
        """(переменная получателя, нужна ли проверка null)."""
        if recv_expr is None:
            return self.self_var, False
        if isinstance(recv_expr, A.This):
            return self.self_var, False
        return self.rv(recv_expr), True

    def upcast(self, v: ir.Var, path: tuple) -> ir.Var:
        if not path:
            return v
        dst = self.var(T.ClassType(path[-1]))
        self.emit(ir.RefCast(dst, v, "up", path, path[-1], None))
        return dst

    def call(self, e, discard: bool):
        info: CallInfo = e.info
        ret = e.ty
        if info.kind == "builtin":
            args = []
            if info.has_recv:
                args.append(self.rv(e.target))
            args += [self.rv(a) for a in e.args]
            dst = None if ret is T.VOID or discard else self.var(ret)
            self.emit(ir.CallBuiltin(dst, info.builtin, args, self.loc(e.pos)))
            return dst
        if info.kind == "super_object":
            args = [self.rv(a) for a in e.args]
            dst = self.var(ret)
            self.emit(ir.CallObjBase(dst, self.self_var, info.method.name, args))
            return dst
        recv, _ = self.receiver(info.recv)
        args = [self.rv(a) for a in e.args]
        dst = None if ret is T.VOID or discard else self.var(ret)
        loc = self.loc(e.pos)
        if info.kind == "object":
            self.emit(ir.CallObj(dst, recv, info.method.name, args, loc))
        elif info.kind == "virtual":
            view = self.upcast(recv, info.recv_path)
            self.emit(ir.CallVirt(dst, view, info.view, info.method, args, loc))
        elif info.kind == "interface":
            self.emit(ir.CallIface(dst, recv, info.view, info.method, args, loc))
        elif info.kind == "direct":
            self.emit(ir.CallDirect(dst, recv, info.recv_path + info.impl_path, info.method, args,
                                    loc if info.recv is not None and not isinstance(info.recv, A.This)
                                    else None))
        else:
            raise InternalCompilerError(f"вид вызова {info.kind}")
        return dst

    def x_MethodCall(self, e, discard):
        return self.call(e, discard)

    def x_SuperMethodCall(self, e, discard):
        return self.call(e, discard)

    def x_New(self, e: A.New):
        args = [self.rv(a) for a in e.args]
        dst = self.var(e.ty)
        cls = e.ty.sym
        self.emit(ir.NewObj(dst, cls, e.info, args, self.loc(e.pos)))
        return dst

    def x_NewArray(self, e: A.NewArray):
        n = self.rv(e.length)
        dst = self.var(e.ty)
        self.prog.note_array(e.ty)
        self.emit(ir.NewArr(dst, e.ty.elem, n, self.loc(e.pos)))
        return dst

    def x_ArrayIndex(self, e: A.ArrayIndex):
        return self.load(self.lvalue(e))

    def x_Unary(self, e: A.Unary):
        v = self.rv(e.operand)
        if e.op == "+":
            return v
        dst = self.var(e.ty)
        self.emit(ir.UnOp(dst, {"-": "neg", "~": "bitnot", "!": "not"}[e.op], v))
        return dst

    def binop(self, op: str, a: ir.Var, b: ir.Var, ty: T.Type, pos) -> ir.Var:
        dst = self.var(ty)
        if T.is_string(ty) and op == "+":
            self.emit(ir.BinOp(dst, "concat", a, b))
            return dst
        loc = self.loc(pos) if op in ("/", "%") and T.is_integral(ty) else None
        self.emit(ir.BinOp(dst, BIN_OPS[op], a, b, loc))
        return dst

    def x_Binary(self, e: A.Binary):
        a = self.rv(e.left)
        b = self.rv(e.right)
        if e.op in CMP_OPS:
            dst = self.var(T.BOOLEAN)
            self.emit(ir.Cmp(dst, CMP_OPS[e.op], a, b))
            return dst
        return self.binop(e.op, a, b, e.ty, e.pos)

    def x_Logical(self, e: A.Logical):
        res = self.var(T.BOOLEAN)
        self.emit(ir.Move(res, self.rv(e.left)))
        rest = ir.Block()
        with self.into(rest):
            self.emit(ir.Move(res, self.rv(e.right)))
        if e.op == "&&":
            self.emit(ir.If(res, rest, None))
        else:
            self.emit(ir.If(res, ir.Block(), rest))
        return res

    def x_Conditional(self, e: A.Conditional):
        c = self.rv(e.cond)
        res = self.var(e.ty)
        then, els = ir.Block(), ir.Block()
        with self.into(then):
            self.emit(ir.Move(res, self.rv(e.then)))
        with self.into(els):
            self.emit(ir.Move(res, self.rv(e.els)))
        self.emit(ir.If(c, then, els))
        return res

    def x_Assign(self, e: A.Assign):
        lv = self.lvalue(e.target)
        if e.op == "=":
            v = self.rv(e.value)
            self.store(lv, v)
            return v
        old = self.load(lv)
        rhs = self.rv(e.value)
        new = self.binop(e.op[:-1], old, rhs, e.target.ty, e.pos)
        self.store(lv, new)
        return new

    def x_IncDec(self, e: A.IncDec):
        lv = self.lvalue(e.target)
        old = self.load(lv)
        ty = e.target.ty
        one = self.const(ty, FloatBits(_one_bits(ty), 32 if ty is T.FLOAT else 64)
                         if ty in (T.FLOAT, T.DOUBLE) else 1)
        new = self.binop("+" if e.op == "++" else "-", old, one, ty, e.pos)
        self.store(lv, new)
        return new if e.prefix else old

    def x_Cast(self, e: A.Cast):
        info: T.CastInfo = e.info
        v = self.rv(e.operand)
        if info.kind == "identity":
            return v
        dst = self.var(e.ty)
        if info.kind == "prim":
            self.emit(ir.Conv(dst, v))
            return dst
        if info.kind == "null":
            self.emit(ir.Const(dst, None))
            return dst
        if isinstance(info.target, T.ArrayType):
            self.prog.note_array(info.target)
        self.emit(ir.RefCast(dst, v, info.kind, info.path, info.target, self.loc(e.pos)))
        return dst

    def x_InstanceOf(self, e: A.InstanceOf):
        info: T.CastInfo = e.info
        v = self.rv(e.operand)
        dst = self.var(T.BOOLEAN)
        if info.kind == "null":
            self.emit(ir.Const(dst, False))
            return dst
        if isinstance(info.target, T.ArrayType):
            self.prog.note_array(info.target)
        kind = "static" if info.static_true else info.kind
        self.emit(ir.InstOf(dst, v, kind, info.target))
        return dst

    # ------------------------------------------------------------ lvalues
    def lvalue(self, e: A.Expr):
        info = e.info
        if isinstance(e, A.ArrayIndex):
            arr = self.rv(e.array)
            idx = self.rv(e.index)
            return ("elem", arr, idx, self.loc(e.pos))
        if isinstance(info, LocalRef):
            return ("local", self.locals[info.sym])
        if isinstance(info, FieldRef):
            if info.obj is None or isinstance(info.obj, A.This):
                return ("field", self.self_var, info.path, info.field, None)
            obj = self.rv(info.obj)
            return ("field", obj, info.path, info.field, self.loc(e.pos))
        raise InternalCompilerError("недопустимое lvalue")

    def load(self, lv) -> ir.Var:
        kind = lv[0]
        if kind == "local":
            v = self.var(lv[1].ty)
            self.emit(ir.Move(v, lv[1]))
            return v
        if kind == "field":
            _, obj, path, f, loc = lv
            v = self.var(f.type)
            self.emit(ir.GetField(v, obj, path, f, loc))
            return v
        _, arr, idx, loc = lv
        v = self.var(arr.ty.elem)
        self.emit(ir.GetElem(v, arr, idx, loc))
        return v

    def store(self, lv, v: ir.Var) -> None:
        kind = lv[0]
        if kind == "local":
            self.emit(ir.Move(lv[1], v))
        elif kind == "field":
            _, obj, path, f, loc = lv
            self.emit(ir.SetField(obj, path, f, v, loc))
        else:
            _, arr, idx, loc = lv
            self.emit(ir.SetElem(arr, idx, v, loc, arr.ty.elem.is_reference))


def _one_bits(ty) -> int:
    if ty is T.FLOAT:
        return struct.unpack("<I", struct.pack("<f", 1.0))[0]
    return struct.unpack("<Q", struct.pack("<d", 1.0))[0]


class IRBuilder:
    def __init__(self, prog: AnalyzedProgram):
        self.src = prog
        self.h = prog.hierarchy
        self._id = 0
        self.strings: list = []
        self._string_index: dict = {}
        self.locations: list = []
        self._loc_index: dict = {}
        self.array_elems: list = []

    def next_id(self) -> int:
        self._id += 1
        return self._id

    def string(self, units: tuple) -> int:
        if units not in self._string_index:
            self._string_index[units] = len(self.strings)
            self.strings.append(units)
        return self._string_index[units]

    def location(self, pos, fn: ir.Function) -> ir.Loc:
        key = (pos.file, pos.line, pos.column, fn.method.display())
        if key not in self._loc_index:
            self._loc_index[key] = len(self.locations)
            self.locations.append(key)
        return ir.Loc(self._loc_index[key])

    def note_array(self, at: T.ArrayType) -> None:
        elem = at.elem
        if (T.is_user_class(elem) or isinstance(elem, T.InterfaceType)) and elem not in self.array_elems:
            self.array_elems.append(elem)

    # ------------------------------------------------------------ program
    def build(self) -> ir.Program:
        functions = [self.function(b) for b in self.src.bodies]
        entry = next(f for f in functions if f.kind == "main" and f.method is self.src.entry)
        classes = self.src.classes
        vtable_slots = {c: [self.h.method_table(c)[sig].decl for sig in self.h.vtable_sigs(c)] for c in classes}
        iface_slots = {i: list(self.h.iface_methods(i).values()) for i in self.src.interfaces}
        concrete = [self.concrete(c, iface_slots) for c in classes if not c.is_abstract]
        for c in classes:
            if c.decl is not None:
                self.string(tuple(_utf16(c.name)))
        return ir.Program(classes, self.src.interfaces, concrete, functions, self.strings, self.locations,
                          self.array_elems, entry, self.h, iface_slots, vtable_slots)

    def function(self, body: MethodBody) -> ir.Function:
        m = body.method
        if m.is_static:
            kind = "main"
        elif m.is_ctor:
            kind = "init"
        else:
            kind = "method"
        fn = ir.Function(kind, body.cls, m, None, [])
        fb = FunctionBuilder(self, fn)
        if kind != "main":
            fn.self_var = ir.Var(self.next_id(), T.ClassType(body.cls), "#self", is_param=True)
        for p in body.params:
            v = ir.Var(self.next_id(), p.type, p.name, is_param=True)
            fn.params.append(v)
            fb.locals[p] = v
        if kind == "init":
            for call in body.super_calls:
                args = [fb.rv(a) for a in call.args]
                parent = next(p for p in body.cls.parents if p.name == call.parent)
                fb.emit(ir.CallInit(fn.self_var, parent, call.info, args))
            for f, init in body.field_inits:
                fb.emit(ir.SetField(fn.self_var, (), f, fb.rv(init), None))
        fb.block(body.body.stmts)
        return fn

    def concrete(self, d: ClassSymbol, iface_slots: dict) -> ir.ConcreteClass:
        h = self.h
        subs = h.subobjects(d)
        vtables = []
        for sub in subs:
            table = h.method_table(sub.cls)
            slots = []
            for sig in h.vtable_sigs(sub.cls):
                r = h.effective(d, sub.path, sig)
                slots.append(ir.Slot(table[sig].decl, r.path, r.decl))
            itables = []
            for iface in h.implemented(sub.cls):
                islots = []
                for im in iface_slots[iface]:
                    r = h.effective(d, sub.path, im.sig)
                    islots.append(ir.Slot(im, r.path, r.decl))
                itables.append(ir.ITable(iface, islots))
            vtables.append(ir.VTable(sub, slots, itables))
        obj = self.src.object_methods
        to_string = h.objres(d, next(s for s in obj if s[0] == "toString"))
        equals = h.objres(d, next(s for s in obj if s[0] == "equals"))
        ref_fields, init_fields = [], []
        for sub in subs:
            for f in sub.cls.fields.values():
                if f.type.is_reference:
                    ref_fields.append((sub.path, f))
                    init_fields.append((sub.path, f))
                elif f.type in (T.FLOAT, T.DOUBLE):
                    init_fields.append((sub.path, f))
        return ir.ConcreteClass(d, subs, vtables, to_string, equals, ref_fields, init_fields)


def _utf16(s: str) -> list[int]:
    out = []
    for ch in s:
        cp = ord(ch)
        if cp > 0xFFFF:
            cp -= 0x10000
            out += [0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)]
        else:
            out.append(cp)
    return out


def build_ir(prog: AnalyzedProgram) -> ir.Program:
    return IRBuilder(prog).build()
