"""Генерация ISO C99 из типизированного IR (п. 18 ТЗ).

Генератор получает только ir.Program и выдаёт пару (заголовок, исходник).

Модель (README §7):
  * все объектные ссылки — void *; значения ссылочных переменных живут
    только в слотах корневого кадра функции jf[2 + i] (решение N1);
  * объект класса C — struct jo_C { struct jo_P base; поля C }, у класса
    без extends первый член — rt_object hdr; поле читается через структуру
    класса-владельца: ((struct jo_Owner *)p)->f_x;
  * одна VTable на класс: struct jv_C { struct jv_P base; слоты C }, корень —
    rt_vtbl_Object (toString, equals). Виртуальный вызов приводит VTable к
    структуре класса, где слот впервые появился;
  * интерфейсные таблицы struct ji_I отдельно, ищутся через rt_class.itables;
  * ссылочные static-поля — void *globals[N], примитивные — jsv_*.
Генерируемый код не использует расширений компиляторов и Windows API.
"""
from __future__ import annotations

import math
import struct

from . import ir
from . import mangle as M
from . import type_system as T
from .errors import InternalCompilerError

PRIM_C = {"byte": "jbyte", "short": "jshort", "int": "jint", "long": "jlong", "float": "jfloat",
          "double": "jdouble", "char": "jchar", "boolean": "jboolean"}
NARROW = {"byte": "rt_i2b", "short": "rt_i2s"}
PRIM_ARRAY_DESC = {"boolean": "rt_class_arr_Z", "char": "rt_class_arr_C", "byte": "rt_class_arr_B",
                   "short": "rt_class_arr_S", "int": "rt_class_arr_I", "long": "rt_class_arr_J",
                   "float": "rt_class_arr_F", "double": "rt_class_arr_D"}
CMP_C = {"eq": "==", "ne": "!=", "lt": "<", "gt": ">", "le": "<=", "ge": ">="}
INT_LIKE = (T.BYTE, T.SHORT, T.INT)


def ctype(t: T.Type) -> str:
    if t.is_primitive:
        return PRIM_C[str(t)]
    if t is T.VOID:
        return "void"
    if t.is_reference:
        return "void *"
    raise InternalCompilerError(f"нет C-типа для {t}")


def decl(t: T.Type, name: str) -> str:
    c = ctype(t)
    return f"{c}{name}" if c.endswith("*") else f"{c} {name}"


def zero(t: T.Type) -> str:
    if t is T.FLOAT:
        return "0.0f"
    if t is T.DOUBLE:
        return "0.0"
    if t is T.BOOLEAN:
        return "false"
    if t.is_reference:
        return "NULL"
    return "0"


def c_string(text: str) -> str:
    """C-литерал из UTF-8 байтов; '?' экранируется (триграфы C99)."""
    out = ['"']
    for b in text.encode("utf-8"):
        ch = chr(b)
        if 32 <= b < 127 and ch not in '\\"?':
            out.append(ch)
        else:
            out.append(f"\\{b:03o}")
    out.append('"')
    return "".join(out)


def c_comment(text: str) -> str:
    safe = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in text)
    return safe.replace("*/", "* /").replace("??", "? ?")


def float_literal(x: float, is_float: bool) -> str:
    """Точный литерал: шестнадцатеричная запись C99; NaN/Inf — через биты."""
    if math.isnan(x) or math.isinf(x):
        if is_float:
            bits = struct.unpack("<I", struct.pack("<f", x))[0]
            return f"rt_fbits(UINT32_C(0x{bits:08X}))"
        bits = struct.unpack("<Q", struct.pack("<d", x))[0]
        return f"rt_dbits(UINT64_C(0x{bits:016X}))"
    text = float.hex(x)
    return f"{text}f" if is_float else text


def int_literal(t: T.Type, v: int) -> str:
    if t is T.LONG:
        if v == -2 ** 63:
            return "(-INT64_C(9223372036854775807) - 1)"
        return f"INT64_C({v})"
    if t is T.INT:
        if v == -2 ** 31:
            return "(-2147483647 - 1)"
        return f"({v})" if v < 0 else str(v)
    if t is T.CHAR:
        return f"UINT32_C({v})"
    return f"({PRIM_C[str(t)]})({v})"


def const_literal(t: T.Type, v) -> str:
    if v is None:
        return "NULL"
    if t is T.BOOLEAN:
        return "true" if v else "false"
    if t in T.FLOATING:
        return float_literal(float(v), t is T.FLOAT)
    return int_literal(t, int(v))


def utf8_of(cps) -> bytes:
    return "".join(chr(c) for c in cps).encode("utf-8")


class CodeGen:
    def __init__(self, prog: ir.Program, header_name: str):
        self.p = prog
        self.h = prog.hierarchy
        self.header_name = header_name
        self.strings = list(prog.strings)
        self.string_index = {s: i for i, s in enumerate(self.strings)}
        self.hdr: list[str] = []
        self.frame_descs: list[str] = []
        self.concrete = [c for c in prog.classes if not c.is_abstract]

    # ------------------------------------------------------------ helpers
    def string(self, text: str) -> str:
        key = tuple(ord(ch) for ch in text)
        if key not in self.string_index:
            self.string_index[key] = len(self.strings)
            self.strings.append(key)
        return M.string_obj(self.string_index[key])

    @staticmethod
    def loc(loc) -> str:
        return "NULL" if loc is None else f"&jloc[{loc.index}]"

    def x(self, v: ir.Var) -> str:
        """C-выражение значения переменной: слот кадра для ссылок."""
        if v.is_ref:
            return f"jf[{2 + self.fn.slots[v]}]"
        return M.var(v)

    def array_desc(self, elem: T.Type) -> str:
        if elem.is_primitive:
            return "&" + PRIM_ARRAY_DESC[str(elem)]
        if elem == T.OBJECT:
            return "&rt_class_arr_Object"
        if elem == T.STRING:
            return "&rt_class_arr_String"
        if elem == T.CLASS:
            return "&rt_class_arr_Class"
        return "&" + M.array_desc(elem)

    def class_ref(self, c) -> str:
        if c is T.OBJECT_SYM:
            return "&rt_class_Object"
        return "&" + M.class_desc(c)

    def java_name(self, c) -> str:
        return getattr(c, "java_name", None) or c.name

    def depth(self, c) -> int:
        return self.h.depth(c)

    def field_designator(self, cls, f) -> str:
        """Член struct jo_<cls>, соответствующий полю f предка (или cls)."""
        return "base." * (self.depth(cls) - self.depth(f.owner)) + M.field(f)

    def field_lvalue(self, obj: str, f) -> str:
        return f"((struct {M.obj_struct(f.owner)} *){obj})->{M.field(f)}"

    @staticmethod
    def fn_name(fn: ir.Function) -> str:
        if fn.kind == "ctor":
            return M.ctor_fn(fn.cls, fn.method)
        if fn.kind == "clinit":
            return M.clinit_fn(fn.cls)
        return M.method_fn(fn.cls, fn.method)

    def method_params(self, m, names=None, with_self=True) -> str:
        names = names or [f"a{i}" for i in range(len(m.params))]
        parts = ["void *self"] if with_self else []
        parts += [decl(t, n) for t, n in zip(m.param_types, names)]
        return ", ".join(parts) if parts else "void"

    def slot_type(self, m) -> str:
        return f"{decl(m.ret, '(*' + M.slot(m) + ')')}({self.method_params(m)})"

    # ------------------------------------------------------------ driver
    def generate(self) -> tuple[str, str]:
        self.gen_header_types()
        body: list[str] = []
        body += self.gen_globals()
        body += self.gen_functions()
        body += self.gen_class_inits()
        body += self.gen_allocators()
        body += self.gen_tables()
        body += self.gen_class_descs()
        body += self.gen_entry()
        head = ["/* Generated by the Java-subset to ISO C99 compiler. Do not edit. */",
                f'#include "{self.header_name}"', ""]
        head += self.gen_strings()
        head += self.gen_locations()
        head += ["/* root frame descriptors: root_count and function name (decision N1) */"]
        head += self.frame_descs + [""]
        source = "\n".join(head + body) + "\n"
        guard = "J_" + "".join(c if c.isalnum() else "_" for c in self.header_name.upper())
        header = "\n".join(
            ["/* Generated by the Java-subset to ISO C99 compiler. Do not edit. */",
             f"#ifndef {guard}", f"#define {guard}", "", '#include "runtime.h"', ""]
            + self.hdr + ["", "#endif"]) + "\n"
        return header, source

    # ------------------------------------------------------------ header types
    def gen_header_types(self) -> None:
        h = self.hdr
        h.append("/* ---- object layouts: single inheritance, parent struct first ---- */")
        for c in self.p.classes:
            h.append(f"struct {M.obj_struct(c)} {{")
            if c.parent is T.OBJECT_SYM:
                h.append("    rt_object hdr;")
            else:
                h.append(f"    struct {M.obj_struct(c.parent)} base;")
            for f in c.fields.values():
                if not f.is_static:
                    h.append(f"    {decl(f.type, M.field(f))};")
            h.append("};")
        h.append("")
        h.append("/* ---- VTables: one per class, parent VTable first, rt_vtbl_Object at the root ---- */")
        for c in self.p.classes:
            h.append(f"struct {M.vtable_type(c)} {{")
            if c.parent is T.OBJECT_SYM:
                h.append("    rt_vtbl_Object base;")
            else:
                h.append(f"    struct {M.vtable_type(c.parent)} base;")
            for sig, owner in self.h.vtable_slots(c):
                if owner is c:
                    h.append(f"    {self.slot_type(self.h.method_table(c)[sig])};")
            h.append("};")
        h.append("")
        h.append("/* ---- interface tables ---- */")
        for i in self.p.interfaces:
            h.append(f"struct {M.itable_type(i)} {{")
            methods = list(self.h.iface_methods(i).values())
            h += [f"    {self.slot_type(m)};" for m in methods] or ["    int unused;"]
            h.append("};")
            h.append(f"extern const rt_iface {M.iface_desc(i)};")
        h.append("")
        h.append("/* ---- metadata, static fields, class initialisation ---- */")
        for c in self.p.classes:
            h.append(f"extern rt_class {M.class_desc(c)};")
        for elem in self.p.array_elems:
            h.append(f"extern rt_class {M.array_desc(elem)};")
        for c in self.p.classes:
            for f in c.fields.values():
                if f.is_static and f.type.is_primitive:
                    h.append(f"extern {decl(f.type, M.static_var(f))};")
        for c in self.p.classes:
            if c in self.p.init_classes:
                h.append(f"extern int {M.clinit_state(c)};")
                h.append(f"void {M.clinit_fn(c)}(void);")
        h.append("")
        h.append("/* ---- functions ---- */")

    def proto(self, line: str) -> None:
        self.hdr.append(line + ";")

    # ------------------------------------------------------------ strings / locations
    def gen_strings(self) -> list[str]:
        for c in self.p.classes:
            self.string(self.java_name(c))
        for elem in self.p.array_elems:
            self.string("[L" + self.java_name(elem.sym) + ";")
        out = ["/* string literals and names: interned static objects outside the GC heap */"]
        for i, cps in enumerate(self.strings):
            data = utf8_of(cps)
            body = ", ".join(f"0x{b:02X}" for b in data + b"\0")
            ascii_ = "true" if all(c < 0x80 for c in cps) else "false"
            out.append(f"static const unsigned char {M.string_data(i)}[] = {{ {body} }};")
            out.append(f"static rt_string {M.string_obj(i)} = {{ RT_STATIC_HDR(&rt_class_String), {len(cps)}, "
                       f"{len(data)}, {ascii_}, {M.string_data(i)} }};")
        out.append("")
        return out

    def gen_locations(self) -> list[str]:
        out = ["/* source positions for runtime errors and uncaught throw */", "static const rt_loc jloc[] = {"]
        if not self.p.locations:
            out.append("    { NULL, 0, 0, NULL }")
        for k, (file, line, col, fn) in enumerate(self.p.locations):
            sep = "," if k + 1 < len(self.p.locations) else ""
            out.append(f"    {{ {c_string(file)}, {line}, {col}, {c_string(fn)} }}{sep}")
        out.append("};")
        out.append("")
        return out

    # ------------------------------------------------------------ globals
    def gen_globals(self) -> list[str]:
        out = ["/* static reference fields: GC roots globals[0..N-1] (section 12.2) */"]
        n = self.p.globals_count
        inits = []
        prims = []
        for c in self.p.classes:
            for f in c.fields.values():
                if not f.is_static:
                    continue
                init = f.decl.init
                const = init.const if init is not None else None
                if f.type.is_reference:
                    if const is not None:
                        inits.append(f"[{f.global_index}] = &{self.string(''.join(map(chr, const)))}")
                else:
                    value = const_literal(f.type, const) if const is not None else zero(f.type)
                    prims.append(f"{decl(f.type, M.static_var(f))} = {value};   /* {c_comment(repr(f))} */")
        size = n if n > 0 else 1
        if inits:
            out.append(f"void *globals[{size}] = {{ {', '.join(inits)} }};")
        else:
            out.append(f"void *globals[{size}];")
        out.append(f"const size_t globals_count = {n};")
        for c in self.p.classes:
            for f in c.fields.values():
                if f.is_static and f.type.is_reference:
                    out.append(f"/* globals[{f.global_index}]: {c_comment(repr(f))} */")
        out += prims
        for c in self.p.classes:
            if c in self.p.init_classes:
                out.append(f"int {M.clinit_state(c)} = RT_CLS_UNINITIALIZED;")
        out.append("")
        return out

    # ------------------------------------------------------------ functions
    def gen_functions(self) -> list[str]:
        out = []
        self.clinit_fns = {}
        for fn in self.p.functions:
            if fn.kind == "clinit":
                self.clinit_fns[fn.cls] = fn
                continue
            out += self.function(fn)
        return out

    def signature(self, fn: ir.Function) -> str:
        m = fn.method
        parts = []
        if fn.self_var is not None:
            parts.append("void *self")
        for v in fn.params:
            parts.append(f"void *p{v.id}" if v.is_ref else decl(v.ty, M.var(v)))
        params = ", ".join(parts) if parts else "void"
        ret = m.ret if fn.kind == "method" else T.VOID
        return f"{decl(ret, self.fn_name(fn))}({params})"

    def function(self, fn: ir.Function, prologue=None, epilogue=None, signature=None) -> list[str]:
        self.fn = fn
        sig = signature or self.signature(fn)
        if signature is None:
            self.proto(sig)
        ret = fn.method.ret if fn.kind == "method" else T.VOID
        k = len(fn.slots)
        lines = [f"/* {c_comment(fn.method.display())} */", sig, "{"]
        lines += prologue or []
        if k:
            dname = M.frame_desc(len(self.frame_descs))
            self.frame_descs.append(f"static const rt_frame_desc {dname} = {{ {k}, "
                                    f"{c_string(fn.method.display())} }};")
            lines.append(f"    void *jf[RT_FRAME_WORDS({k})];")
        if ret.is_reference:
            lines.append("    void *jret;")
        prims = [v for v in fn.vars if not v.is_ref]
        for v in prims:
            lines.append(f"    {decl(v.ty, M.var(v))} = {zero(v.ty)};")
        for v in prims + [p for p in fn.params if not p.is_ref]:
            lines.append(f"    (void){M.var(v)};")
        if k:
            lines.append(f"    rt_frame_push(jf, &{dname});")
            if fn.self_var is not None:
                lines.append(f"    {self.x(fn.self_var)} = self;")
            for p in fn.params:
                if p.is_ref:
                    lines.append(f"    {self.x(p)} = p{p.id};")
        self.emit_block(fn.body, lines, 1)
        lines += epilogue or []
        if k:
            lines.append("    rt_frame_pop(jf);")
        if ret is not T.VOID:
            lines.append(f"    return {zero(ret)};   /* not reached: every path returns or throws */")
        lines += ["}", ""]
        return lines

    def emit_block(self, block: ir.Block, out: list[str], depth: int) -> None:
        for s in block.stmts:
            self.emit_stmt(s, out, depth)

    def emit_stmt(self, s, out: list[str], depth: int) -> None:
        ind = "    " * depth
        if isinstance(s, ir.If):
            out.append(f"{ind}if ({self.x(s.cond)}) {{")
            self.emit_block(s.then, out, depth + 1)
            if s.els is not None and s.els.stmts:
                out.append(f"{ind}}} else {{")
                self.emit_block(s.els, out, depth + 1)
            out.append(f"{ind}}}")
        elif isinstance(s, ir.Loop):
            out.append(f"{ind}for (;;) {{")
            self.emit_block(s.cond_block, out, depth + 1)
            if s.cond is not None:
                out.append(f"{ind}    if (!{self.x(s.cond)}) break;")
            self.emit_block(s.body, out, depth + 1)
            if s.has_continue:
                out.append(f"{ind}jcont_{s.id}: ;")
            self.emit_block(s.update, out, depth + 1)
            out.append(f"{ind}}}")
        elif isinstance(s, ir.Break):
            out.append(f"{ind}break;")
        elif isinstance(s, ir.Continue):
            out.append(f"{ind}goto jcont_{s.loop.id};")
        elif isinstance(s, ir.Return):
            out += [ind + line for line in self.ret(s)]
        else:
            for line in self.instr(s):
                out.append(ind + line)

    def ret(self, s: ir.Return) -> list[str]:
        """return: кадр снимается при любом выходе; ссылка-результат
        сохраняется в jret и сразу попадает в слот вызывающего."""
        pop = ["rt_frame_pop(jf);"] if self.fn.slots else []
        if s.value is None:
            return pop + ["return;"]
        if s.value.is_ref:
            return [f"jret = {self.x(s.value)};"] + pop + ["return jret;"]
        return pop + [f"return {self.x(s.value)};"]

    # ------------------------------------------------------------ instructions
    def instr(self, s) -> list[str]:
        fn = getattr(self, "i_" + type(s).__name__, None)
        if fn is None:
            raise InternalCompilerError(f"нет генерации для {type(s).__name__}")
        return fn(s)

    def assign(self, dst, expr: str) -> list[str]:
        return [f"{self.x(dst)} = {expr};"] if dst is not None else [f"{expr};"]

    def i_ClearRefs(self, s: ir.ClearRefs) -> list[str]:
        return [" ".join(f"{self.x(v)} = NULL;" for v in s.vars) + "   /* dead references */"]

    def i_Const(self, s: ir.Const) -> list[str]:
        return self.assign(s.dst, const_literal(s.dst.ty, s.value))

    def i_StrLit(self, s: ir.StrLit) -> list[str]:
        return self.assign(s.dst, "&" + M.string_obj(s.index))

    def i_Move(self, s: ir.Move) -> list[str]:
        return self.assign(s.dst, self.x(s.src))

    def i_UnOp(self, s: ir.UnOp) -> list[str]:
        t, a = s.src.ty, self.x(s.src)
        if s.op == "not":
            return self.assign(s.dst, f"!{a}")
        fname = {"neg": "neg", "bitnot": "not"}[s.op]
        if t is T.LONG:
            return self.assign(s.dst, f"rt_l{fname}({a})")
        if t is T.INT:
            return self.assign(s.dst, f"rt_i{fname}({a})")
        if t is T.CHAR:
            op = "-" if s.op == "neg" else "~"
            return self.assign(s.dst, f"rt_cchk({op}(jlong){a}, {self.loc(s.loc)})")
        if str(t) in NARROW:
            return self.assign(s.dst, f"{NARROW[str(t)]}(rt_i{fname}((jint){a}))")
        return self.assign(s.dst, f"-{a}")   # float/double: смена знака IEEE

    def i_BinOp(self, s: ir.BinOp) -> list[str]:
        t, a, b = s.a.ty, self.x(s.a), self.x(s.b)
        op = s.op
        loc = self.loc(s.loc)
        if op == "concat":
            return self.assign(s.dst, f"rt_str_concat({a}, {b}, {loc})")
        div_loc = f", {loc}" if op in ("div", "rem") else ""
        if t is T.BOOLEAN:
            c = {"and": f"{a} && {b}", "or": f"{a} || {b}", "xor": f"{a} != {b}"}[op]
            return self.assign(s.dst, f"({c})")
        if t is T.LONG:
            return self.assign(s.dst, f"rt_l{op}({a}, {b}{div_loc})")
        if t is T.INT:
            return self.assign(s.dst, f"rt_i{op}({a}, {b}{div_loc})")
        if t is T.CHAR:
            return self.assign(s.dst, self.char_op(op, a, b, loc))
        if str(t) in NARROW:
            return self.assign(s.dst, f"{NARROW[str(t)]}(rt_i{op}((jint){a}, (jint){b}{div_loc}))")
        p = "f" if t is T.FLOAT else "d"
        if op in ("add", "sub", "mul"):
            c = {"add": "+", "sub": "-", "mul": "*"}[op]
            return self.assign(s.dst, f"{a} {c} {b}")
        return self.assign(s.dst, f"rt_{p}{op}({a}, {b})")

    @staticmethod
    def char_op(op: str, a: str, b: str, loc: str) -> str:
        """char-арифметика (решение N2): точный результат в jlong, затем
        проверка Unicode scalar value — иначе [InvalidChar]."""
        if op in ("add", "sub", "mul"):
            c = {"add": "+", "sub": "-", "mul": "*"}[op]
            return f"rt_cchk((jlong){a} {c} (jlong){b}, {loc})"
        if op == "div":
            return f"rt_cdiv({a}, {b}, {loc})"
        if op == "rem":
            return f"rt_crem({a}, {b}, {loc})"
        if op in ("and", "or", "xor"):
            c = {"and": "&", "or": "|", "xor": "^"}[op]
            return f"rt_cchk((jlong)({a} {c} {b}), {loc})"
        if op == "shl":
            return f"rt_cchk((jlong){a} << ({b} & 31u), {loc})"
        return f"rt_cchk((jlong)({a} >> ({b} & 31u)), {loc})"     # shr, ushr: значения неотрицательны

    def i_Cmp(self, s: ir.Cmp) -> list[str]:
        return self.assign(s.dst, f"({self.x(s.a)} {CMP_C[s.op]} {self.x(s.b)})")

    def conv(self, src: T.Type, dst: T.Type, x: str, loc: str) -> str:
        if src is dst:
            return x
        if dst is T.CHAR:
            if src is T.LONG:
                return f"rt_cchk({x}, {loc})"
            if src in T.FLOATING:
                return f"rt_cchk((jlong)rt_d2i((jdouble){x}), {loc})"
            return f"rt_cchk((jlong){x}, {loc})"
        if dst is T.LONG:
            if src in T.FLOATING:
                return f"rt_d2l((jdouble){x})"
            return f"(jlong){x}"
        if dst in T.FLOATING:
            if dst is T.FLOAT and src is T.DOUBLE:
                return f"rt_d2f({x})"
            return f"({PRIM_C[str(dst)]}){x}"
        # dst ∈ byte short int
        if src is T.LONG:
            wide = f"rt_l2i({x})"
        elif src in T.FLOATING:
            wide = f"rt_d2i((jdouble){x})"
        else:
            wide = f"(jint){x}"
        if dst is T.INT:
            return wide
        if dst is T.SHORT and src is T.BYTE:
            return f"(jshort){x}"
        return f"{NARROW[str(dst)]}({wide})"

    def i_Conv(self, s: ir.Conv) -> list[str]:
        return self.assign(s.dst, self.conv(s.src.ty, s.dst.ty, self.x(s.src), self.loc(s.loc)))

    def nullchk(self, obj: str, loc) -> list[str]:
        return [f"rt_nullchk({obj}, {self.loc(loc)});"] if loc is not None else []

    def i_GetField(self, s: ir.GetField) -> list[str]:
        obj = self.x(s.obj)
        return self.nullchk(obj, s.loc) + self.assign(s.dst, self.field_lvalue(obj, s.field))

    def i_SetField(self, s: ir.SetField) -> list[str]:
        obj = self.x(s.obj)
        return self.nullchk(obj, s.loc) + [f"{self.field_lvalue(obj, s.field)} = {self.x(s.src)};"]

    @staticmethod
    def static_lvalue(f) -> str:
        if f.type.is_reference:
            return f"globals[{f.global_index}]"
        return M.static_var(f)

    def i_GetStatic(self, s: ir.GetStatic) -> list[str]:
        return self.assign(s.dst, self.static_lvalue(s.field))

    def i_SetStatic(self, s: ir.SetStatic) -> list[str]:
        return [f"{self.static_lvalue(s.field)} = {self.x(s.src)};"]

    def i_ClassInit(self, s: ir.ClassInit) -> list[str]:
        return [f"if ({M.clinit_state(s.cls)} != RT_CLS_INITIALIZED) {M.clinit_fn(s.cls)}();"]

    def elem_ptr(self, arr: ir.Var) -> str:
        c = ctype(arr.ty.elem)
        ptr = f"{c}*" if c.endswith("*") else f"{c} *"
        return f"(({ptr})RT_ADATA({self.x(arr)}))"

    def i_GetElem(self, s: ir.GetElem) -> list[str]:
        a, i = self.x(s.arr), self.x(s.idx)
        return [f"rt_arr_check({a}, {i}, {self.loc(s.loc)});"] + \
            self.assign(s.dst, f"{self.elem_ptr(s.arr)}[{i}]")

    def i_SetElem(self, s: ir.SetElem) -> list[str]:
        a, i, v = self.x(s.arr), self.x(s.idx), self.x(s.src)
        out = [f"rt_arr_check({a}, {i}, {self.loc(s.loc)});"]
        if s.store_check:
            out.append(f"rt_arr_store_check({a}, {v}, {self.loc(s.loc)});")
        out.append(f"{self.elem_ptr(s.arr)}[{i}] = {v};")
        return out

    def i_ArrLen(self, s: ir.ArrLen) -> list[str]:
        return self.assign(s.dst, f"rt_arr_length({self.x(s.arr)}, {self.loc(s.loc)})")

    def i_NewArr(self, s: ir.NewArr) -> list[str]:
        return self.assign(s.dst, f"rt_new_array({self.array_desc(s.elem)}, {self.x(s.length)}, "
                                  f"{self.loc(s.loc)})")

    def args(self, args, first=None) -> str:
        parts = ([first] if first is not None else []) + [self.x(a) for a in args]
        return ", ".join(parts)

    def i_AllocObj(self, s: ir.AllocObj) -> list[str]:
        return self.assign(s.dst, f"{M.alloc_fn(s.cls)}({self.loc(s.loc)})")

    def i_NewObject(self, s: ir.NewObject) -> list[str]:
        return self.assign(s.dst, f"rt_new_object({self.loc(s.loc)})")

    def i_CallCtor(self, s: ir.CallCtor) -> list[str]:
        return [f"{M.ctor_fn(s.cls, s.ctor)}({self.args(s.args, self.x(s.recv))});"]

    def target_desc(self, target) -> str:
        if isinstance(target, T.ArrayType):
            return self.array_desc(target.elem)
        if isinstance(target, T.ClassType):
            sym = target.sym
            if sym.builtin:
                return {"String": "&rt_class_String", "Class": "&rt_class_Class",
                        "Object": "&rt_class_Object"}[sym.name]
            return "&" + M.class_desc(sym)
        if hasattr(target, "supers"):
            return "&" + M.iface_desc(target)
        return "&" + M.class_desc(target)

    def i_RefCast(self, s: ir.RefCast) -> list[str]:
        src, loc = self.x(s.src), self.loc(s.loc)
        d = self.target_desc(s.target)
        fn = {"class": "rt_cast_class", "iface": "rt_cast_iface", "exact": "rt_cast_exact"}.get(s.kind)
        if fn is None:
            raise InternalCompilerError(f"вид приведения {s.kind}")
        return self.assign(s.dst, f"{fn}({src}, {d}, {loc})")

    def i_InstOf(self, s: ir.InstOf) -> list[str]:
        src = self.x(s.src)
        if s.kind == "static":
            return self.assign(s.dst, f"({src} != NULL)")
        fn = {"class": "rt_inst_class", "iface": "rt_inst_iface", "exact": "rt_inst_exact"}.get(s.kind)
        if fn is None:
            raise InternalCompilerError(f"вид instanceof {s.kind}")
        return self.assign(s.dst, f"{fn}({src}, {self.target_desc(s.target)})")

    def i_CallVirt(self, s: ir.CallVirt) -> list[str]:
        r = self.x(s.recv)
        call = (f"((const struct {M.vtable_type(s.slot_owner)} *)rt_vtable({r}))->{M.slot(s.method)}"
                f"({self.args(s.args, r)})")
        return [f"rt_nullchk({r}, {self.loc(s.loc)});"] + self.assign(s.dst, call)

    def i_CallIface(self, s: ir.CallIface) -> list[str]:
        r = self.x(s.recv)
        table = f"((const struct {M.itable_type(s.iface)} *)rt_itable({r}, &{M.iface_desc(s.iface)}, " \
                f"{self.loc(s.loc)}))"
        return self.assign(s.dst, f"{table}->{M.slot(s.method)}({self.args(s.args, r)})")

    def i_CallObj(self, s: ir.CallObj) -> list[str]:
        r, loc = self.x(s.recv), self.loc(s.loc)
        if s.which == "toString":
            return self.assign(s.dst, f"rt_obj_toString({r}, {loc})")
        if s.which == "equals":
            call = f"((const rt_vtbl_Object *)rt_vtable({r}))->m_equals_0LObject_9({self.args(s.args, r)})"
            return [f"rt_nullchk({r}, {loc});"] + self.assign(s.dst, call)
        return self.assign(s.dst, f"rt_obj_getClass({r}, {loc})")

    def i_CallObjBase(self, s: ir.CallObjBase) -> list[str]:
        r = self.x(s.recv)
        if s.which == "toString":
            return self.assign(s.dst, f"rt_obj_default_toString({r})")
        return self.assign(s.dst, f"rt_obj_default_equals({self.args(s.args, r)})")

    def i_CallDirect(self, s: ir.CallDirect) -> list[str]:
        r = self.x(s.recv)
        m = s.method
        return self.nullchk(r, s.loc) + self.assign(s.dst, f"{M.method_fn(m.owner, m)}({self.args(s.args, r)})")

    def i_Throw(self, s: ir.Throw) -> list[str]:
        return [f"rt_throw({self.x(s.value)}, {self.loc(s.loc)});"]

    def i_CallBuiltin(self, s: ir.CallBuiltin) -> list[str]:
        name = s.name
        a = [self.x(v) for v in s.args]
        loc = self.loc(s.loc)
        prints = {"Z": "rt_print_bool({0})", "C": "rt_print_char({0})", "B": "rt_print_int((jint){0})",
                  "S": "rt_print_int((jint){0})", "I": "rt_print_int({0})", "J": "rt_print_long({0})",
                  "F": "rt_print_float({0})", "D": "rt_print_double({0})", "Str": "rt_print_string({0})",
                  "Obj": "rt_print_object({0}, " + loc + ")"}
        if name == "println":
            return ["rt_print_newline();"]
        if name.startswith("print_"):
            return [prints[name[6:]].format(*a) + ";"]
        if name.startswith("println_"):
            return [prints[name[8:]].format(*a) + ";", "rt_print_newline();"]
        if name.startswith("valueOf_"):
            fn = {"Z": "rt_str_from_bool({0}", "C": "rt_str_from_char({0}", "B": "rt_str_from_int((jint){0}",
                  "S": "rt_str_from_int((jint){0}", "I": "rt_str_from_int({0}", "J": "rt_str_from_long({0}",
                  "F": "rt_str_from_float({0}", "D": "rt_str_from_double({0}"}[name[8:]]
            return self.assign(s.dst, fn.format(*a) + f", {loc})")
        table = {
            "str_length": "rt_str_length", "str_charAt": "rt_str_charAt",
            "str_substring": "rt_str_substring", "str_equals": "rt_str_equals",
            "class_getName": "rt_class_getName",
        }
        if name in table:
            return self.assign(s.dst, f"{table[name]}({', '.join(a + [loc])})")
        if name.startswith("math_"):
            return self.assign(s.dst, f"rt_{name}({', '.join(a)})")
        raise InternalCompilerError(f"встроенная функция {name}")

    # ------------------------------------------------------------ class initialisation
    def gen_class_inits(self) -> list[str]:
        """jci_C: однократная инициализация (N4): UNINITIALIZED -> INITIALIZING,
        инициализация родителя, static-инициализаторы по порядку, INITIALIZED."""
        out = []
        for c in self.p.classes:
            if c not in self.p.init_classes:
                continue
            state = M.clinit_state(c)
            pro = [f"    if ({state} != RT_CLS_UNINITIALIZED)",
                   "        return;     /* initialised, or in progress (recursive request) */",
                   f"    {state} = RT_CLS_INITIALIZING;"]
            if c.parent in self.p.init_classes:
                pro.append(f"    if ({M.clinit_state(c.parent)} != RT_CLS_INITIALIZED) "
                           f"{M.clinit_fn(c.parent)}();")
            epi = [f"    {state} = RT_CLS_INITIALIZED;"]
            fn = self.clinit_fns.get(c)
            if fn is None:
                from .symbol_table import MethodSymbol
                fn = ir.Function("clinit", c, MethodSymbol(c, "<clinit>", [], T.VOID, {"static"}), None, [])
            out += self.function(fn, prologue=pro, epilogue=epi, signature=f"void {M.clinit_fn(c)}(void)")
        return out

    # ------------------------------------------------------------ allocators
    def gen_allocators(self) -> list[str]:
        """jn_C: выделение экземпляра и явные значения по умолчанию для полей,
        у которых нулевые биты не обязаны означать NULL / +0.0 (п. 12.2)."""
        out = []
        for c in self.concrete:
            sig = f"void *{M.alloc_fn(c)}(const rt_loc *loc)"
            self.proto(sig)
            out += [f"/* new {c_comment(c.name)}: allocation and default field values */", sig, "{",
                    f"    void *o = rt_alloc_object(&{M.class_desc(c)}, loc);"]
            for f in self.h.all_fields(c):
                if f.type.is_reference or f.type in T.FLOATING:
                    out.append(f"    {self.field_lvalue('o', f)} = {zero(f.type)};")
            out += ["    return o;", "}", ""]
        return out

    # ------------------------------------------------------------ tables
    def vtable_init(self, c) -> list[str]:
        inits = []
        d = self.depth(c)
        for sig, m in self.p.object_methods.items():
            if m.name == "getClass":
                continue
            impl = self.h.object_impl(c, sig)
            if impl is None:
                fn = "rt_obj_default_toString" if m.name == "toString" else "rt_obj_default_equals"
            else:
                fn = M.method_fn(impl.owner, impl)
            inits.append(f"    {'.base' * d}.{M.slot(m)} = {fn}")
        table = self.h.method_table(c)
        for sig, owner in self.h.vtable_slots(c):
            impl = table[sig]
            if impl.is_abstract:
                raise InternalCompilerError(f"абстрактный слот {impl.display()} в конкретном классе {c.name}")
            inits.append(f"    {'.base' * (d - self.depth(owner))}.{M.slot(impl)} = "
                         f"{M.method_fn(impl.owner, impl)}")
        return inits

    def gen_tables(self) -> list[str]:
        out = ["/* interface descriptors */"]
        for i in self.p.interfaces:
            out.append(f"const rt_iface {M.iface_desc(i)} = {{ {c_string(i.name)} }};")
        out.append("")
        for c in self.concrete:
            out.append(f"/* ===== class {c_comment(c.name)}: VTable and interface tables ===== */")
            out.append(f"static const struct {M.vtable_type(c)} {M.vtable_inst(c)} = {{")
            out.append(",\n".join(self.vtable_init(c)))
            out.append("};")
            table = self.h.method_table(c)
            entries = []
            for i in self.h.implemented(c):
                inst = M.itable_inst(c, i)
                inits = []
                for sig, im in self.h.iface_methods(i).items():
                    impl = table[sig]
                    inits.append(f".{M.slot(im)} = {M.method_fn(impl.owner, impl)}")
                out.append(f"static const struct {M.itable_type(i)} {inst} = {{ "
                           f"{', '.join(inits) if inits else '0'} }};")
                entries.append(f"{{ &{M.iface_desc(i)}, &{inst} }}")
            if entries:
                out.append(f"static const rt_itable_entry {M.itable_entries(c)}[] = {{ {', '.join(entries)} }};")
            out.append("")
        return out

    def gen_class_descs(self) -> list[str]:
        out = ["/* class metadata: single parent, reference field offsets, VTable, itables */"]
        for c in self.p.classes:
            name = "&" + self.string(self.java_name(c))
            refs = [f for f in self.h.all_fields(c) if f.type.is_reference]
            refs_name = "NULL"
            if refs:
                refs_name = M.refoffs(c)
                offs = ", ".join(f"offsetof(struct {M.obj_struct(c)}, {self.field_designator(c, f)})"
                                 for f in refs)
                out.append(f"static const size_t {refs_name}[] = {{ {offs} }};")
            concrete = not c.is_abstract
            vt = f"&{M.vtable_inst(c)}" if concrete else "NULL"
            nit = len(self.h.implemented(c)) if concrete else 0
            ie = M.itable_entries(c) if concrete and nit else "NULL"
            out.append(f"rt_class {M.class_desc(c)} = {{ RT_STATIC_HDR(&rt_class_Class), {name}, "
                       f"{self.class_ref(c.parent)}, RT_K_CLASS, sizeof(struct {M.obj_struct(c)}), {len(refs)}, "
                       f"{refs_name}, {vt}, {nit}, {ie}, 0, 0, NULL, NULL }};")
        for elem in self.p.array_elems:
            name = "&" + self.string("[L" + self.java_name(elem.sym) + ";")
            if isinstance(elem, T.InterfaceType):
                kind, cls, iface = "RT_E_IFACE", "NULL", "&" + M.iface_desc(elem.sym)
            else:
                kind, cls, iface = "RT_E_CLASS", "&" + M.class_desc(elem.sym), "NULL"
            out.append(f"rt_class {M.array_desc(elem)} = {{ RT_STATIC_HDR(&rt_class_Class), {name}, "
                       f"&rt_class_Object, RT_K_ARRAY, 0, 0, NULL, &rt_object_vtable, 0, NULL, {kind}, "
                       f"sizeof(void *), {cls}, {iface} }};")
        out.append("")
        return out

    def gen_entry(self) -> list[str]:
        e = self.p.entry
        c = self.p.entry_class
        out = ["/* Java entry point: class initialisation, then main(args) */",
               "void rt_user_main(void *args)", "{",
               '    static const rt_frame_desc desc = { 1, "rt_user_main" };',
               "    void *frame[RT_FRAME_WORDS(1)];",
               "    rt_frame_push(frame, &desc);",
               "    frame[2] = args;"]
        if c in self.p.init_classes:
            out.append(f"    if ({M.clinit_state(c)} != RT_CLS_INITIALIZED) {M.clinit_fn(c)}();")
        out += [f"    {M.method_fn(e.cls, e.method)}(frame[2]);",
                "    rt_frame_pop(frame);", "}", ""]
        thr = next(k for k in self.p.classes if k.prelude and k.name == "Throwable")
        get_msg = self.h.method_table(thr)[("getMessage", ())]
        owner = self.h.slot_owner(thr, get_msg.sig)
        out += ["/* message of an uncaught throw: virtual Throwable.getMessage() (decision N6) */",
                "void *rt_user_throwable_message(void *ex)", "{",
                f"    return ((const struct {M.vtable_type(owner)} *)rt_vtable(ex))->{M.slot(get_msg)}(ex);",
                "}"]
        return out


def generate(prog: ir.Program, header_name: str) -> tuple[str, str]:
    return CodeGen(prog, header_name).generate()
