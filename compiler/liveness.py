"""Корневые кадры и анализ живости ссылок для точного GC (п. 13 ТЗ, решение N1).

1. Каждой ссылочной переменной функции (this, параметры, локальные,
   временные) назначается слот корневого кадра jf[2 + i]. Значение ссылки
   существует только в слоте, поэтому любой объект, нужный после safepoint,
   достижим из цепочки rets независимо от уровня оптимизации C-компилятора.

2. Обратный анализ живости (с неподвижной точкой для циклов) даёт для
   каждого safepoint S множество live_in(S); операнды S живы в S.

3. Прямой анализ «грязных» слотов (может содержать не-NULL) с той же
   структурой: D_out(S) = (D_in(S) ∩ live_in(S)) ∪ defs(S) для safepoint и
   D_in ∪ defs для прочих инструкций. Перед S вставляется ClearRefs для
   D_in(S) − live_in(S): сборщик видит только живые ссылки, и после
   принудительной сборки число живых объектов совпадает с ожидаемым точно.
"""
from __future__ import annotations

from . import ir


class _LiveLoop:
    def __init__(self, break_live: frozenset, cont_live: frozenset):
        self.break_live = break_live
        self.cont_live = cont_live


class _DirtyLoop:
    def __init__(self):
        self.breaks: list = []
        self.conts: list = []


def _union(*states):
    """Объединение состояний; None — недостижимая точка."""
    out = None
    for s in states:
        if s is None:
            continue
        out = s if out is None else out | s
    return out


class _Analysis:
    def __init__(self, refs: set):
        self.refs = refs
        self.live_in: dict = {}       # id(safepoint) -> frozenset
        self.clears: dict = {}        # id(safepoint) -> frozenset

    def r(self, vs) -> frozenset:
        return frozenset(v for v in vs if v is not None and v in self.refs)

    # ------------------------------------------------------------ liveness
    def live_stmts(self, stmts: list, out: frozenset, lctx) -> frozenset:
        cur = out
        for s in reversed(stmts):
            cur = self.live_stmt(s, cur, lctx)
        return cur

    def live_stmt(self, s, out: frozenset, lctx) -> frozenset:
        if isinstance(s, ir.Block):
            return self.live_stmts(s.stmts, out, lctx)
        if isinstance(s, ir.If):
            t = self.live_stmts(s.then.stmts, out, lctx)
            e = self.live_stmts(s.els.stmts, out, lctx) if s.els is not None else out
            return t | e
        if isinstance(s, ir.Loop):
            after = out
            head: frozenset = frozenset()
            while True:
                upd_in = self.live_stmts(s.update.stmts, head, lctx)
                body_in = self.live_stmts(s.body.stmts, upd_in, _LiveLoop(after, upd_in))
                check_in = body_in | after if s.cond is not None else body_in
                new_head = self.live_stmts(s.cond_block.stmts, check_in, lctx)
                if new_head == head:
                    return head
                head = new_head
        if isinstance(s, ir.Break):
            return lctx.break_live
        if isinstance(s, ir.Continue):
            return lctx.cont_live
        if isinstance(s, (ir.Return, ir.Throw)):
            live = self.r(s.uses())
            if s.safepoint:
                self.live_in[id(s)] = live
            return live
        live = (out - self.r(s.defs())) | self.r(s.uses())
        if s.safepoint:
            self.live_in[id(s)] = live
        return live

    # ------------------------------------------------------------ dirty slots
    def dirty_stmts(self, stmts: list, d, lctx):
        for s in stmts:
            if d is None:
                break
            d = self.dirty_stmt(s, d, lctx)
        return d

    def dirty_stmt(self, s, d: frozenset, lctx):
        if isinstance(s, ir.Block):
            return self.dirty_stmts(s.stmts, d, lctx)
        if isinstance(s, ir.If):
            t = self.dirty_stmts(s.then.stmts, d, lctx)
            e = self.dirty_stmts(s.els.stmts, d, lctx) if s.els is not None else d
            return _union(t, e)
        if isinstance(s, ir.Loop):
            entry = d
            head = entry
            while True:
                loop = _DirtyLoop()
                d1 = self.dirty_stmts(s.cond_block.stmts, head, lctx)
                d2 = self.dirty_stmts(s.body.stmts, d1, loop)
                d3 = self.dirty_stmts(s.update.stmts, _union(d2, *loop.conts), lctx)
                new_head = _union(entry, d3)
                if new_head == head:
                    return _union(d1 if s.cond is not None else None, *loop.breaks)
                head = new_head
        if isinstance(s, ir.Break):
            lctx.breaks.append(d)
            return None
        if isinstance(s, ir.Continue):
            lctx.conts.append(d)
            return None
        if s.safepoint:
            live = self.live_in.get(id(s), frozenset())
            dead = d - live
            if dead:
                self.clears[id(s)] = dead
            else:
                self.clears.pop(id(s), None)
            d = d & live
        if isinstance(s, ir.Return) or isinstance(s, ir.Throw):
            return None
        defs = self.r(s.defs())
        if isinstance(s, ir.Const) and s.value is None:
            return d - defs           # присваивание null очищает слот
        return d | defs

    # ------------------------------------------------------------ insertion
    def insert(self, block: ir.Block, slots: dict) -> None:
        out = []
        for s in block.stmts:
            dead = self.clears.get(id(s))
            if dead:
                out.append(ir.ClearRefs(sorted(dead, key=lambda v: slots[v])))
            if isinstance(s, ir.If):
                self.insert(s.then, slots)
                if s.els is not None:
                    self.insert(s.els, slots)
            elif isinstance(s, ir.Loop):
                for b in (s.cond_block, s.body, s.update):
                    self.insert(b, slots)
            elif isinstance(s, ir.Block):
                self.insert(s, slots)
            out.append(s)
        block.stmts = out


def prepare_function(fn: ir.Function) -> None:
    """Назначить слоты кадра и вставить обнуление мёртвых ссылок."""
    own = ([fn.self_var] if fn.self_var is not None else []) + list(fn.params)
    refs = [v for v in own + fn.vars if v.is_ref]
    fn.slots = {v: i for i, v in enumerate(refs)}
    if not refs:
        return
    an = _Analysis(set(refs))
    an.live_stmts(fn.body.stmts, frozenset(), None)
    entry = frozenset(v for v in own if v.is_ref)        # параметры уже лежат в слотах
    an.dirty_stmts(fn.body.stmts, entry, None)
    an.insert(fn.body, fn.slots)
