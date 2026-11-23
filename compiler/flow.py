"""Анализ потока управления.

* Достижимость (упрощённые правила JLS 14.22): оператор после return/throw/
  break/continue или после бесконечного цикла без break недостижим; тело
  while/for с константно ложным условием недостижимо. Недостижимый код —
  ошибка компиляции (п. 7 ТЗ). throw завершает путь (п. 13.4).
* Все пути non-void метода возвращают значение (или завершаются throw).
* Definite assignment (упрощённые правила JLS гл. 16): локальная
  переменная читается только после гарантированного присваивания.

Значения константных выражений вычисляет семантический анализ (атрибут
`const`, модуль constfold) — здесь они только используются.
"""
from __future__ import annotations

from typing import Optional

from . import ast_nodes as A
from . import type_system as T
from .errors import SemanticError

ALL = None   # «все переменные» — состояние недостижимой точки (вакуумно истинно)


def meet(a, b):
    if a is ALL:
        return b
    if b is ALL:
        return a
    return a & b


def with_var(s, sym):
    return ALL if s is ALL else s | {sym}


def const_value(e: A.Expr):
    """Значение константного выражения или None."""
    return e.const


class _Loop:
    def __init__(self):
        self.break_states: list = []
        self.has_break = False


class FlowAnalyzer:
    def __init__(self, analyzer):
        self.an = analyzer

    def error(self, msg: str, pos) -> None:
        self.an.error(SemanticError(msg, pos))

    def analyze(self, body) -> None:
        self.loops: list[_Loop] = []
        da = frozenset(body.params)
        if body.super_call is not None:
            for a in body.super_call.args:
                da = self.expr(a, da)
        for _, init in body.field_inits:
            da = self.expr(init, da)
        completes, _ = self.block(body.body.stmts, da)
        m = body.method
        if completes and body.kind == "method" and m.ret is not T.VOID:
            pos = getattr(m.decl, "end_pos", None) or m.pos
            self.error(f"метод {m.display()} должен возвращать значение типа {m.ret} на всех путях", pos)

    # ------------------------------------------------------------ statements
    def block(self, stmts: list[A.Stmt], da):
        completes = True
        for s in stmts:
            if not completes:
                self.error("недостижимый код", s.pos)
                return False, ALL
            completes, da = self.stmt(s, da)
        return completes, da

    def stmt(self, s: A.Stmt, da):
        if isinstance(s, A.Block):
            return self.block(s.stmts, da)
        if isinstance(s, A.LocalVarDecl):
            for d, sym in zip(s.declarators, s.symbols):
                if d.init is not None:
                    da = with_var(self.expr(d.init, da), sym)
            return True, da
        if isinstance(s, A.ExprStmt):
            return True, self.expr(s.expr, da)
        if isinstance(s, A.EmptyStmt):
            return True, da
        if isinstance(s, A.If):
            t, f = self.cond(s.cond, da)
            c1, d1 = self.stmt(s.then, t)
            if s.els is None:
                return True, meet(d1, f)
            c2, d2 = self.stmt(s.els, f)
            return c1 or c2, meet(d1, d2)
        if isinstance(s, A.While):
            return self.loop(s.cond, s.body, [], da)
        if isinstance(s, A.For):
            for init in s.init:
                _, da = self.stmt(init, da)
            return self.loop(s.cond, s.body, s.update, da)
        if isinstance(s, A.ForEach):
            da = self.expr(s.iterable, da)
            self.loops.append(_Loop())
            self.stmt(s.body, with_var(da, s.symbol))
            self.loops.pop()
            return True, da
        if isinstance(s, A.Break):
            loop = self.loops[-1]
            loop.has_break = True
            loop.break_states.append(da)
            return False, ALL
        if isinstance(s, A.Continue):
            return False, ALL
        if isinstance(s, A.Return):
            if s.value is not None:
                self.expr(s.value, da)
            return False, ALL
        if isinstance(s, A.Throw):
            self.expr(s.value, da)
            return False, ALL
        return True, da

    def loop(self, cond: Optional[A.Expr], body: A.Stmt, update: list[A.Expr], da):
        cv = True if cond is None else const_value(cond)
        if cond is not None:
            t, f = self.cond(cond, da)
        else:
            t, f = da, ALL
        loop = _Loop()
        self.loops.append(loop)
        if cv is False:
            self.error("недостижимый код: условие цикла всегда ложно", body.pos)
            self.loops.pop()
            return True, f
        _, body_out = self.stmt(body, t)
        self.loops.pop()
        upd = meet(body_out, da) if body_out is not ALL else da
        for u in update:
            self.expr(u, upd)
        out = f
        for b in loop.break_states:
            out = meet(out, b)
        completes = cv is not True or loop.has_break
        return completes, (out if completes else ALL)

    # ------------------------------------------------------------ expressions
    def cond(self, e: A.Expr, da):
        """(состояние при true, состояние при false)."""
        cv = const_value(e) if e.ty is T.BOOLEAN else None
        if isinstance(e, A.Logical):
            lt, lf = self.cond(e.left, da)
            if e.op == "&&":
                rt, rf = self.cond(e.right, lt)
                t, f = rt, meet(lf, rf)
            else:
                rt, rf = self.cond(e.right, lf)
                t, f = meet(lt, rt), rf
        elif isinstance(e, A.Unary) and e.op == "!":
            f, t = self.cond(e.operand, da)
        else:
            out = self.expr(e, da)
            t, f = out, out
        if cv is True:
            f = ALL
        elif cv is False:
            t = ALL
        return t, f

    def expr(self, e: A.Expr, da):
        from .semantic import LocalRef
        if isinstance(e, A.Name):
            if isinstance(e.info, LocalRef):
                self.read(e.info.sym, da, e.pos)
            return da
        if isinstance(e, (A.Literal, A.This, A.SuperFieldAccess)):
            return da
        if isinstance(e, A.Assign):
            tgt = e.target
            if isinstance(tgt, A.Name) and isinstance(tgt.info, LocalRef):
                if e.op != "=":
                    self.read(tgt.info.sym, da, tgt.pos)
                da = self.expr(e.value, da)
                return with_var(da, tgt.info.sym)
            da = self.expr(tgt, da)
            return self.expr(e.value, da)
        if isinstance(e, A.Logical) or (isinstance(e, A.Unary) and e.op == "!"):
            t, f = self.cond(e, da)
            return meet(t, f)
        if isinstance(e, A.Conditional):
            t, f = self.cond(e.cond, da)
            if e.ty is T.BOOLEAN:
                at, af = self.cond(e.then, t)
                bt, bf = self.cond(e.els, f)
                return meet(meet(at, af), meet(bt, bf))
            return meet(self.expr(e.then, t), self.expr(e.els, f))
        for child in _children(e):
            da = self.expr(child, da)
        return da

    def read(self, sym, da, pos) -> None:
        if da is not ALL and sym not in da:
            self.error(f"переменная '{sym.name}' может быть не инициализирована", pos)


def _children(e: A.Expr) -> list[A.Expr]:
    if isinstance(e, A.FieldAccess):
        return [e.target]
    if isinstance(e, A.MethodCall):
        return ([e.target] if e.target is not None else []) + list(e.args)
    if isinstance(e, A.SuperMethodCall):
        return list(e.args)
    if isinstance(e, A.New):
        return list(e.args)
    if isinstance(e, A.NewArray):
        return [e.length]
    if isinstance(e, A.ArrayIndex):
        return [e.array, e.index]
    if isinstance(e, A.Unary):
        return [e.operand]
    if isinstance(e, A.IncDec):
        return [e.target]
    if isinstance(e, A.Binary):
        return [e.left, e.right]
    if isinstance(e, A.Cast):
        return [e.operand]
    if isinstance(e, A.InstanceOf):
        return [e.operand]
    return []
