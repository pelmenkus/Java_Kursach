"""Граф наследования и модель подобъектов (README §6).

Дерево подобъектов класса K: корень K, дети — деревья непосредственных
родителей в порядке объявления. Object в дерево не входит: он один и
представлен заголовком полного объекта (п. 4.2.1 ТЗ). Повторяющийся обычный
предок встречается в дереве несколько раз — это отдельные подобъекты.

Путь — кортеж ClassSymbol шагов от корня (() — сам K). Внутри одного уровня
родители различны, поэтому путь однозначно задаёт подобъект.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .symbol_table import ClassSymbol, FieldSymbol, InterfaceSymbol, MethodSymbol


@dataclass(frozen=True)
class Res:
    """Место реализации: путь подобъекта (относительно класса запроса) и объявление."""
    path: tuple
    decl: MethodSymbol


@dataclass(frozen=True)
class SubObject:
    index: int
    cls: ClassSymbol
    path: tuple
    enclosing: int          # индекс объемлющего подобъекта, -1 для верхнего


class Hierarchy:
    def __init__(self, object_methods: dict):
        # sig -> MethodSymbol встроенных методов Object
        self.object_methods = object_methods
        self._mt: dict[ClassSymbol, dict] = {}
        self._subs: dict[ClassSymbol, list[SubObject]] = {}
        self._impl: dict[ClassSymbol, list[InterfaceSymbol]] = {}

    # ------------------------------------------------------------ ancestry
    def ancestors(self, k: ClassSymbol) -> set[ClassSymbol]:
        out: set[ClassSymbol] = set()
        stack = list(k.parents)
        while stack:
            p = stack.pop()
            if p not in out:
                out.add(p)
                stack.extend(p.parents)
        return out

    def is_ancestor(self, anc: ClassSymbol, desc: ClassSymbol) -> bool:
        return anc is not desc and anc in self.ancestors(desc)

    def paths_to(self, s: ClassSymbol, t: ClassSymbol) -> list[tuple]:
        """Все пути подобъектов класса t внутри s (t == s → [()])."""
        if s is t:
            return [()]
        out: list[tuple] = []
        for p in s.parents:
            for rest in self.paths_to(p, t):
                out.append((p,) + rest)
        return out

    @staticmethod
    def path_str(k: ClassSymbol, path: tuple) -> str:
        return ".".join([k.name] + [p.name for p in path])

    def class_at(self, k: ClassSymbol, path: tuple) -> ClassSymbol:
        return path[-1] if path else k

    # ------------------------------------------------------------ interfaces
    def iface_closure(self, i: InterfaceSymbol) -> list[InterfaceSymbol]:
        out: list[InterfaceSymbol] = []
        stack = [i]
        while stack:
            x = stack.pop(0)
            if x not in out:
                out.append(x)
                stack.extend(x.supers)
        return out

    def implemented(self, k: ClassSymbol) -> list[InterfaceSymbol]:
        """Интерфейсы, которые реализует k, включая унаследованные (D9)."""
        if k in self._impl:
            return self._impl[k]
        out: list[InterfaceSymbol] = []
        for i in k.interfaces:
            for j in self.iface_closure(i):
                if j not in out:
                    out.append(j)
        for p in k.parents:
            for j in self.implemented(p):
                if j not in out:
                    out.append(j)
        self._impl[k] = out
        return out

    def iface_methods(self, i: InterfaceSymbol) -> dict:
        """sig -> MethodSymbol для всех методов интерфейса и его предков."""
        out: dict = {}
        for j in self.iface_closure(i):
            for ms in j.methods.values():
                for m in ms:
                    out.setdefault(m.sig, m)
        return out

    # ------------------------------------------------------------ methods
    def method_table(self, k: ClassSymbol) -> dict:
        """MS(K) с разрешением res(K, s) (README §6.3). Порядок ключей детерминирован."""
        if k in self._mt:
            return self._mt[k]
        table: dict = {}
        for m in k.all_own_methods():
            table[m.sig] = Res((), m)
        self._inherit_into(k, table)
        self._mt[k] = table
        return table

    def _inherit_into(self, k: ClassSymbol, table: dict) -> None:
        for p in k.parents:
            for sig, r in self.method_table(p).items():
                if r.decl.is_private or sig in table:
                    continue
                if r.decl.owner is self._object_owner():
                    continue
                table[sig] = Res((p,) + r.path, r.decl)
        for i in k.interfaces:
            for sig, m in self.iface_methods(i).items():
                table.setdefault(sig, Res((), m))
        for sig, m in self.object_methods.items():
            table.setdefault(sig, Res((), m))

    def _object_owner(self):
        for m in self.object_methods.values():
            return m.owner
        return None

    def inherited(self, k: ClassSymbol, sig) -> Optional[Res]:
        """inh(K, s): разрешение без учёта собственного объявления K."""
        table: dict = {}
        self._inherit_into(k, table)
        return table.get(sig)

    def is_object_sig(self, sig) -> bool:
        return sig in self.object_methods

    # ------------------------------------------------------------ fields
    def field_lookup(self, k: ClassSymbol, name: str, allow_private: bool = True
                     ) -> Optional[tuple[tuple, FieldSymbol]]:
        """fres(K, f): своё поле, затем родители слева направо в глубину.
        Private-поля родителей не наследуются."""
        f = k.fields.get(name)
        if f is not None and (allow_private or not f.is_private):
            return (), f
        for p in k.parents:
            r = self.field_lookup(p, name, allow_private=False)
            if r is not None:
                return (p,) + r[0], r[1]
        return None

    # ------------------------------------------------------------ subobjects
    def subobjects(self, d: ClassSymbol) -> list[SubObject]:
        if d in self._subs:
            return self._subs[d]
        out: list[SubObject] = []

        def walk(cls: ClassSymbol, path: tuple, enclosing: int) -> None:
            idx = len(out)
            out.append(SubObject(idx, cls, path, enclosing))
            for p in cls.parents:
                walk(p, path + (p,), idx)

        walk(d, (), -1)
        self._subs[d] = out
        return out

    def effective(self, d: ClassSymbol, path: tuple, sig) -> Res:
        """Реализация слота sig в подобъекте path полного объекта d (правило D5).
        Возвращает Res(site — путь от d, decl)."""
        t = self.class_at(d, path)
        r = self.method_table(t)[sig]
        site, impl = path + r.path, r.decl
        for cut in range(len(path) - 1, -1, -1):
            e = path[:cut]
            k = self.class_at(d, e)
            own = k.own_method(sig)
            if own is None or own.is_private or own.is_static:
                continue
            ih = self.inherited(k, sig)
            if ih is not None and e + ih.path == site:
                site, impl = e, own
        return Res(site, impl)

    def objres(self, k: ClassSymbol, sig) -> Optional[Res]:
        """Разрешение метода Object для класса k (README §6.5).
        None означает базовую реализацию Object."""
        own = k.own_method(sig)
        if own is not None:
            return Res((), own)
        for p in k.parents:
            r = self.objres(p, sig)
            if r is not None:
                return Res((p,) + r.path, r.decl)
        return None

    def vtable_sigs(self, t: ClassSymbol) -> list:
        """Сигнатуры, имеющие слот в VTable представления t: MS(t) без методов
        Object (диспетчеризуются через метаданные) и без private."""
        return [sig for sig, r in self.method_table(t).items()
                if not self.is_object_sig(sig) and not r.decl.is_private]
