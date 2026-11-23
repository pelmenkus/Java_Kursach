"""Граф наследования: одиночная цепочка классов и DAG интерфейсов (README §6).

У каждого класса ровно один родитель (у пользовательских классов без extends —
Object). Таблица методов класса строится по цепочке снизу вверх, VTable
класса — расширение VTable родителя: слоты родителя в том же порядке, затем
слоты, впервые появившиеся в классе. Корень любой VTable — методы Object
(toString, equals: rt_vtbl_Object в runtime.h); getClass слота не имеет.
"""
from __future__ import annotations

from typing import Optional

from .symbol_table import ClassSymbol, FieldSymbol, InterfaceSymbol, MethodSymbol


class Hierarchy:
    def __init__(self, object_methods: dict):
        # sig -> MethodSymbol встроенных методов Object (toString, equals, getClass)
        self.object_methods = object_methods
        self._mt: dict[ClassSymbol, dict] = {}
        self._slots: dict[ClassSymbol, list] = {}
        self._impl: dict[ClassSymbol, list[InterfaceSymbol]] = {}

    @staticmethod
    def depth(c: ClassSymbol) -> int:
        """Глубина в цепочке: Object — 0, класс без extends — 1."""
        return len(c.chain()) - 1

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
        """Интерфейсы, которые реализует k, включая унаследованные через
        родителя и через расширение интерфейсов."""
        if k in self._impl:
            return self._impl[k]
        out: list[InterfaceSymbol] = []
        if k.parent is not None:
            out.extend(self.implemented(k.parent))
        for i in k.interfaces:
            for j in self.iface_closure(i):
                if j not in out:
                    out.append(j)
        self._impl[k] = out
        return out

    def iface_methods(self, i: InterfaceSymbol) -> dict:
        """sig -> MethodSymbol всех методов интерфейса и его предков; равные
        сигнатуры дают одно обязательство."""
        out: dict = {}
        for j in self.iface_closure(i):
            for ms in j.methods.values():
                for m in ms:
                    out.setdefault(m.sig, m)
        return out

    # ------------------------------------------------------------ methods
    def method_table(self, k: ClassSymbol) -> dict:
        """sig -> объявление, видимое в k: собственные методы экземпляра,
        затем унаследованные по цепочке (private не наследуются), затем
        нереализованные методы интерфейсов (абстрактные), затем Object."""
        if k in self._mt:
            return self._mt[k]
        table: dict = {}
        if k.builtin:
            table.update(self.object_methods)
            self._mt[k] = table
            return table
        for m in k.all_own_methods():
            if not m.is_static:
                table[m.sig] = m
        for sig, m in self.method_table(k.parent).items():
            if sig not in table and not m.is_private:
                table[sig] = m
        for i in self.implemented(k):
            for sig, m in self.iface_methods(i).items():
                table.setdefault(sig, m)
        self._mt[k] = table
        return table

    def inherited(self, k: ClassSymbol, sig) -> Optional[MethodSymbol]:
        """Объявление sig, унаследованное k от родителя (без собственного)."""
        if k.parent is None:
            return None
        m = self.method_table(k.parent).get(sig)
        if m is None or m.is_private:
            return None
        return m

    def is_object_sig(self, sig) -> bool:
        return sig in self.object_methods

    def vtable_slots(self, k: ClassSymbol) -> list:
        """Слоты VTable класса k: [(sig, класс, впервые объявивший слот)].
        Слоты Object (toString, equals) — в rt_vtbl_Object, здесь не входят."""
        if k in self._slots:
            return self._slots[k]
        slots: list = []
        if k.parent is not None and not k.parent.builtin:
            slots = list(self.vtable_slots(k.parent))
        have = {s for s, _ in slots}
        for sig, m in self.method_table(k).items():
            if sig in have or self.is_object_sig(sig) or m.is_private:
                continue
            slots.append((sig, k))
            have.add(sig)
        self._slots[k] = slots
        return slots

    def slot_owner(self, k: ClassSymbol, sig) -> Optional[ClassSymbol]:
        """Класс, в структуре VTable которого лежит слот sig (None — Object)."""
        if self.is_object_sig(sig):
            return None
        for s, owner in self.vtable_slots(k):
            if s == sig:
                return owner
        raise KeyError(sig)

    def object_impl(self, k: ClassSymbol, sig) -> Optional[MethodSymbol]:
        """Переопределение метода Object в цепочке k; None — реализация Object."""
        m = self.method_table(k).get(sig)
        if m is None or m.owner is self.object_methods[sig].owner:
            return None
        return m

    # ------------------------------------------------------------ fields
    def field_lookup(self, k: ClassSymbol, name: str) -> Optional[FieldSymbol]:
        """Своё поле (скрывает унаследованные), затем по цепочке родителей.
        Private-поля родителей не наследуются."""
        f = k.fields.get(name)
        if f is not None:
            return f
        c = k.parent
        while c is not None and not c.builtin:
            f = c.fields.get(name)
            if f is not None and not f.is_private:
                return f
            c = c.parent
        return None

    def all_fields(self, k: ClassSymbol) -> list[FieldSymbol]:
        """Поля экземпляра k по всей цепочке (от корня к k)."""
        out: list[FieldSymbol] = []
        for c in reversed(k.chain()):
            if c.builtin:
                continue
            out.extend(f for f in c.fields.values() if not f.is_static)
        return out
