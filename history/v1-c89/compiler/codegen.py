"""Генерация ANSI C89 из типизированного IR.

Генератор получает только ir.Program. Выход — пара (заголовок, исходник):
заголовок содержит структуры подобъектов, типы VTable/itable, extern-объявления
и прототипы; исходник — строки, таблицу позиций, функции, thunk-и, таблицы
и метаданные. Все локальные переменные функции объявляются в начале её тела.
"""
from __future__ import annotations

from . import ir
from . import mangle as M
from . import type_system as T
from .errors import InternalCompilerError
from .lexer import FloatBits

PRIM_C = {"byte": "jbyte", "short": "jshort", "int": "jint", "long": "jlong", "float": "jfloat",
          "double": "jdouble", "char": "jchar", "boolean": "jboolean"}
SMALL_INTS = ("byte", "short", "char")
NARROW = {"byte": "rt_i2b", "short": "rt_i2s", "char": "rt_i2c"}
PRIM_ARRAY_DESC = {"boolean": "rt_class_arr_Z", "char": "rt_class_arr_C", "byte": "rt_class_arr_B",
                   "short": "rt_class_arr_S", "int": "rt_class_arr_I", "long": "rt_class_arr_J",
                   "float": "rt_class_arr_F", "double": "rt_class_arr_D"}
CMP_C = {"eq": "==", "ne": "!=", "lt": "<", "gt": ">", "le": "<=", "ge": ">="}


def ctype(t: T.Type) -> str:
    if t.is_primitive:
        return PRIM_C[str(t)]
    if t is T.VOID:
        return "void"
    if isinstance(t, T.ClassType):
        if t.sym is T.OBJECT_SYM or t.sym is T.CLASS_SYM:
            return "rt_object *"
        if t.sym is T.STRING_SYM:
            return "rt_string *"
        return f"struct {M.sub_struct(t.sym)} *"
    if isinstance(t, T.InterfaceType):
        return "void *"
    if isinstance(t, T.ArrayType):
        return "rt_array *"
    if t is T.NULL:
        return "void *"
    raise InternalCompilerError(f"нет C-типа для {t}")


def decl(t: T.Type, name: str) -> str:
    c = ctype(t)
    return f"{c}{name}" if c.endswith("*") else f"{c} {name}"


def ptr_to(c: str) -> str:
    return f"{c}*" if c.endswith("*") else f"{c} *"


def zero(t: T.Type) -> str:
    if t is T.LONG:
        return "{ 0UL, 0UL }"
    if t is T.FLOAT:
        return "0.0f"
    if t is T.DOUBLE:
        return "0.0"
    if t.is_reference:
        return "NULL"
    return "0"


def c_string(text: str) -> str:
    """C-литерал из UTF-8 байтов; '?' экранируется (триграфы C89)."""
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


def float_literal(fb: FloatBits) -> str:
    v = fb.value
    if fb.width == 32:
        return f"{v:.9e}f"
    return f"{v:.17e}"


class CodeGen:
    def __init__(self, prog: ir.Program, header_name: str):
        self.p = prog
        self.h = prog.hierarchy
        self.header_name = header_name
        self.strings = list(prog.strings)
        self.string_index = {s: i for i, s in enumerate(self.strings)}
        self.hdr: list[str] = []
        self.src: list[str] = []

    # ------------------------------------------------------------ helpers
    def string(self, text: str) -> str:
        units = []
        for ch in text:
            cp = ord(ch)
            if cp > 0xFFFF:
                cp -= 0x10000
                units += [0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)]
            else:
                units.append(cp)
        key = tuple(units)
        if key not in self.string_index:
            self.string_index[key] = len(self.strings)
            self.strings.append(key)
        return M.string_obj(self.string_index[key])

    def loc(self, loc) -> str:
        return "NULL" if loc is None else f"&jloc[{loc.index}]"

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

    def offset(self, top, path: tuple) -> str:
        """Смещение подобъекта path внутри struct js_<top> (константное выражение)."""
        parts = []
        cur = top
        for step in path:
            parts.append(f"(long)offsetof(struct {M.sub_struct(cur)}, {M.parent(step)})")
            cur = step
        return " + ".join(parts) if parts else "0L"

    def top_offset(self, d, path: tuple) -> str:
        base = f"(long)offsetof(struct {M.full_struct(d)}, top)"
        rest = self.offset(d, path)
        return base if rest == "0L" else f"{base} + {rest}"

    @staticmethod
    def member_path(path: tuple) -> str:
        return ".".join(M.parent(p) for p in path)

    def sub_expr(self, var: str, path: tuple) -> str:
        if not path:
            return var
        return f"&{var}->{self.member_path(path)}"

    def field_expr(self, var: str, path: tuple, f) -> str:
        prefix = self.member_path(path)
        return f"{var}->{prefix + '.' if prefix else ''}{M.field(f)}"

    @staticmethod
    def params_c(self_type: str | None, types, names) -> str:
        parts = [] if self_type is None else [self_type]
        parts += [decl(t, n) for t, n in zip(types, names)]
        return ", ".join(parts) if parts else "void"

    def method_proto(self, cls, m, names=None) -> str:
        names = names or [f"a{i}" for i in range(len(m.params))]
        return (f"{decl(m.ret, M.method_fn(cls, m))}("
                f"{self.params_c(decl(T.ClassType(cls), 'self'), m.param_types, names)})")

    # ------------------------------------------------------------ driver
    def generate(self) -> tuple[str, str]:
        self.gen_header_types()
        body = []
        body += self.gen_functions()
        body += self.gen_allocators()
        body += self.gen_tables()
        body += self.gen_class_descs()
        body += self.gen_entry()
        head = [f"/* Generated by the Java-subset to ANSI C89 compiler. Do not edit. */",
                f'#include "{self.header_name}"', ""]
        head += self.gen_strings()
        head += self.gen_locations()
        source = "\n".join(head + body) + "\n"
        guard = "J_" + "".join(c if c.isalnum() else "_" for c in self.header_name.upper())
        header = "\n".join(
            ["/* Generated by the Java-subset to ANSI C89 compiler. Do not edit. */",
             f"#ifndef {guard}", f"#define {guard}", "", "#include <stddef.h>", '#include "runtime.h"', ""]
            + self.hdr + ["", f"#endif"]) + "\n"
        return header, source

    # ------------------------------------------------------------ header types
    def gen_header_types(self) -> None:
        h = self.hdr
        classes = self.p.classes
        for c in classes:
            h.append(f"struct {M.sub_struct(c)};")
        h.append("")
        for i in self.p.interfaces:
            h.append(f"/* interface {c_comment(i.name)} */")
            members = []
            for m in self.p.iface_slots[i]:
                params = self.params_c("void *self", m.param_types, [f"a{k}" for k in range(len(m.params))])
                members.append(f"    {decl(m.ret, '(*' + M.slot(m) + ')')}({params});")
            h.append(f"struct {M.itable_type(i)} {{")
            h += members or ["    int unused;"]
            h.append("};")
            h.append(f"extern const rt_iface {M.iface_desc(i)};")
        h.append("")
        for c in classes:
            h.append(f"/* class {c_comment(c.name)}: VTable of the {c_comment(c.name)} view */")
            h.append(f"struct {M.vtable_type(c)} {{")
            h.append("    rt_vtbl_hdr h;")
            for m in self.p.vtable_slots[c]:
                params = self.params_c(decl(T.ClassType(c), "self"), m.param_types,
                                       [f"a{k}" for k in range(len(m.params))])
                h.append(f"    {decl(m.ret, '(*' + M.slot(m) + ')')}({params});")
            h.append("};")
        h.append("")
        for c in classes:
            h.append(f"/* subobject of class {c_comment(c.name)}: vptr, parents in order, own fields */")
            h.append(f"struct {M.sub_struct(c)} {{")
            h.append("    const rt_vtbl_hdr *vt;")
            for p in c.parents:
                h.append(f"    struct {M.sub_struct(p)} {M.parent(p)};")
            for f in c.fields.values():
                h.append(f"    {decl(f.type, M.field(f))};")
            h.append("};")
            if not c.is_abstract:
                h.append(f"struct {M.full_struct(c)} {{ rt_object hdr; struct {M.sub_struct(c)} top; }};")
            h.append(f"extern rt_class {M.class_desc(c)};")
        for elem in self.p.array_elems:
            h.append(f"extern rt_class {M.array_desc(elem)};")
        h.append("extern const rt_loc jloc[];")
        h.append("")

    def proto(self, line: str) -> None:
        self.hdr.append(line + ";")

    # ------------------------------------------------------------ strings / locations
    def gen_strings(self) -> list[str]:
        # имена классов и массивов понадобятся метаданным: регистрируем заранее
        for c in self.p.classes:
            self.string(c.name)
        for elem in self.p.array_elems:
            self.string("[L" + elem.sym.name + ";")
        out = ["/* string literals and names: static objects outside the GC heap */"]
        for i, units in enumerate(self.strings):
            data = ", ".join(f"0x{u:04X}" for u in units) if units else "0"
            out.append(f"static jchar {M.string_data(i)}[] = {{ {data} }};")
            out.append(f"rt_string {M.string_obj(i)} = {{ {{ &rt_class_String, 0UL, 0 }}, {len(units)}, "
                       f"{M.string_data(i)} }};")
            self.hdr.append(f"extern rt_string {M.string_obj(i)};")
        out.append("")
        return out

    def gen_locations(self) -> list[str]:
        out = ["/* source positions for runtime error messages */", "const rt_loc jloc[] = {"]
        if not self.p.locations:
            out.append("    { NULL, 0, 0, NULL }")
        for k, (file, line, col, fn) in enumerate(self.p.locations):
            sep = "," if k + 1 < len(self.p.locations) else ""
            out.append(f"    {{ {c_string(file)}, {line}, {col}, {c_string(fn)} }}{sep}")
        out.append("};")
        out.append("")
        return out

    # ------------------------------------------------------------ functions
    def gen_functions(self) -> list[str]:
        out = []
        for fn in self.p.functions:
            out += self.function(fn)
        return out

    def function(self, fn: ir.Function) -> list[str]:
        m, cls = fn.method, fn.cls
        names = [M.var(v) for v in fn.params]
        if fn.kind == "main":
            sig = f"void {M.method_fn(cls, m)}({self.params_c(None, [v.ty for v in fn.params], names)})"
        elif fn.kind == "init":
            sig = (f"void {M.init_fn(cls, m)}("
                   f"{self.params_c(decl(T.ClassType(cls), 'self'), [v.ty for v in fn.params], names)})")
        else:
            sig = self.method_proto(cls, m, names)
        self.proto(sig)
        lines = [f"/* {c_comment(m.display())} */", sig, "{"]
        for v in fn.vars:
            lines.append(f"    {decl(v.ty, M.var(v))} = {zero(v.ty)};")
        ret = T.VOID if fn.kind != "method" else m.ret
        if ret is not T.VOID:
            lines.append(f"    {decl(ret, 'jrz')} = {zero(ret)};")
        for v in ([fn.self_var] if fn.self_var is not None else []) + fn.params + fn.vars:
            lines.append(f"    (void){M.var(v)};")
        self.emit_block(fn.body, lines, 1)
        if ret is not T.VOID:
            lines.append("    return jrz;  /* unreachable: every path returns (flow analysis) */")
        lines.append("}")
        lines.append("")
        return lines

    def emit_block(self, block: ir.Block, out: list[str], depth: int) -> None:
        for s in block.stmts:
            self.emit_stmt(s, out, depth)

    def emit_stmt(self, s, out: list[str], depth: int) -> None:
        ind = "    " * depth
        if isinstance(s, ir.If):
            out.append(f"{ind}if ({M.var(s.cond)}) {{")
            self.emit_block(s.then, out, depth + 1)
            if s.els is not None and s.els.stmts:
                out.append(f"{ind}}} else {{")
                self.emit_block(s.els, out, depth + 1)
            out.append(f"{ind}}}")
        elif isinstance(s, ir.Loop):
            out.append(f"{ind}for (;;) {{")
            self.emit_block(s.cond_block, out, depth + 1)
            if s.cond is not None:
                out.append(f"{ind}    if (!{M.var(s.cond)}) break;")
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
            out.append(f"{ind}return {M.var(s.value)};" if s.value is not None else f"{ind}return;")
        else:
            for line in self.instr(s):
                out.append(ind + line)

    # ------------------------------------------------------------ instructions
    def instr(self, s) -> list[str]:
        fn = getattr(self, "i_" + type(s).__name__, None)
        if fn is None:
            raise InternalCompilerError(f"нет генерации для {type(s).__name__}")
        return fn(s)

    @staticmethod
    def assign(dst, expr: str) -> list[str]:
        return [f"{M.var(dst)} = {expr};"] if dst is not None else [f"{expr};"]

    def i_Const(self, s: ir.Const) -> list[str]:
        t, v = s.dst.ty, s.value
        if v is None:
            return self.assign(s.dst, "NULL")
        if t is T.BOOLEAN:
            return self.assign(s.dst, "1" if v else "0")
        if t is T.LONG:
            u = v & 0xFFFFFFFFFFFFFFFF
            return self.assign(s.dst, f"rt_lmake(0x{u >> 32:08X}UL, 0x{u & 0xFFFFFFFF:08X}UL)")
        if t in (T.FLOAT, T.DOUBLE):
            return self.assign(s.dst, float_literal(v))
        if t is T.INT:
            if v == -2 ** 31:
                return self.assign(s.dst, "(-2147483647 - 1)")
            return self.assign(s.dst, f"({v})" if v < 0 else str(v))
        if t is T.CHAR:
            return self.assign(s.dst, f"(jchar){v}")
        return self.assign(s.dst, f"({PRIM_C[str(t)]})({v})")

    def i_StrLit(self, s: ir.StrLit) -> list[str]:
        return self.assign(s.dst, "&" + M.string_obj(s.index))

    def i_Move(self, s: ir.Move) -> list[str]:
        return self.assign(s.dst, M.var(s.src))

    def i_UnOp(self, s: ir.UnOp) -> list[str]:
        t, a = s.src.ty, M.var(s.src)
        k = str(t)
        if s.op == "not":
            return self.assign(s.dst, f"(jboolean)!{a}")
        fname = {"neg": "neg", "bitnot": "not"}[s.op]
        if t is T.LONG:
            return self.assign(s.dst, f"rt_l{fname}({a})")
        if t is T.INT:
            return self.assign(s.dst, f"rt_i{fname}({a})")
        if k in SMALL_INTS:
            return self.assign(s.dst, f"{NARROW[k]}(rt_i{fname}((jint){a}))")
        return self.assign(s.dst, f"-{a}")   # float/double: IEEE sign flip

    def i_BinOp(self, s: ir.BinOp) -> list[str]:
        t, a, b = s.a.ty, M.var(s.a), M.var(s.b)
        op = s.op
        if op == "concat":
            return self.assign(s.dst, f"rt_str_concat({a}, {b}, NULL)")
        k = str(t)
        loc = f", {self.loc(s.loc)}" if op in ("div", "rem") else ""
        if t is T.BOOLEAN:
            c = {"and": "&", "or": "|", "xor": "^"}[op]
            return self.assign(s.dst, f"(jboolean)({a} {c} {b})")
        if t is T.LONG:
            return self.assign(s.dst, f"rt_l{op}({a}, {b}{loc})")
        if t is T.INT:
            return self.assign(s.dst, f"rt_i{op}({a}, {b}{loc})")
        if k in SMALL_INTS:
            return self.assign(s.dst, f"{NARROW[k]}(rt_i{op}((jint){a}, (jint){b}{loc}))")
        p = "f" if t is T.FLOAT else "d"
        if op in ("add", "sub", "mul"):
            c = {"add": "+", "sub": "-", "mul": "*"}[op]
            return self.assign(s.dst, f"{a} {c} {b}")
        return self.assign(s.dst, f"rt_{p}{op}({a}, {b})")

    def i_Cmp(self, s: ir.Cmp) -> list[str]:
        t, a, b = s.a.ty, M.var(s.a), M.var(s.b)
        c = CMP_C[s.op]
        if t is T.LONG:
            return self.assign(s.dst, f"(jboolean)(rt_lcmp({a}, {b}) {c} 0)")
        if t.is_reference:
            return self.assign(s.dst, f"(jboolean)((const void *){a} {c} (const void *){b})")
        return self.assign(s.dst, f"(jboolean)({a} {c} {b})")

    def conv(self, src: T.Type, dst: T.Type, x: str) -> str:
        if src is dst:
            return x
        ints = (T.BYTE, T.SHORT, T.CHAR, T.INT)

        def narrow(t, e):
            return NARROW[str(t)] + f"({e})" if str(t) in NARROW else e

        if dst is T.LONG:
            return f"rt_i2l((jint){x})" if src in ints else f"rt_d2l((jdouble){x})"
        if src is T.LONG:
            if dst in ints:
                return narrow(dst, f"rt_l2i({x})")
            return f"rt_l2f({x})" if dst is T.FLOAT else f"rt_l2d({x})"
        if dst in ints:
            if src in ints:
                return narrow(dst, f"(jint){x}")
            return narrow(dst, f"rt_d2i((jdouble){x})")
        if dst is T.FLOAT:
            return f"(jfloat){x}" if src in ints else f"rt_d2f({x})"
        if dst is T.DOUBLE:
            return f"(jdouble){x}"
        raise InternalCompilerError(f"преобразование {src} -> {dst}")

    def i_Conv(self, s: ir.Conv) -> list[str]:
        return self.assign(s.dst, self.conv(s.src.ty, s.dst.ty, M.var(s.src)))

    def i_GetField(self, s: ir.GetField) -> list[str]:
        obj = M.var(s.obj)
        pre = [f"rt_nullchk({obj}, {self.loc(s.loc)});"] if s.loc is not None else []
        return pre + self.assign(s.dst, self.field_expr(obj, s.path, s.field))

    def i_SetField(self, s: ir.SetField) -> list[str]:
        obj = M.var(s.obj)
        pre = [f"rt_nullchk({obj}, {self.loc(s.loc)});"] if s.loc is not None else []
        return pre + [f"{self.field_expr(obj, s.path, s.field)} = {M.var(s.src)};"]

    def elem_ptr(self, arr: ir.Var) -> str:
        return f"(({ptr_to(ctype(arr.ty.elem))})RT_ADATA({M.var(arr)}))"

    def i_GetElem(self, s: ir.GetElem) -> list[str]:
        a, i = M.var(s.arr), M.var(s.idx)
        return [f"rt_arr_check({a}, {i}, {self.loc(s.loc)});"] + \
            self.assign(s.dst, f"{self.elem_ptr(s.arr)}[{i}]")

    def i_SetElem(self, s: ir.SetElem) -> list[str]:
        a, i, v = M.var(s.arr), M.var(s.idx), M.var(s.src)
        out = [f"rt_arr_check({a}, {i}, {self.loc(s.loc)});"]
        if s.store_check:
            out.append(f"rt_arr_store_check({a}, (const void *){v}, {self.loc(s.loc)});")
        out.append(f"{self.elem_ptr(s.arr)}[{i}] = {v};")
        return out

    def i_ArrLen(self, s: ir.ArrLen) -> list[str]:
        return self.assign(s.dst, f"rt_arr_length({M.var(s.arr)}, {self.loc(s.loc)})")

    def i_NewArr(self, s: ir.NewArr) -> list[str]:
        return self.assign(s.dst, f"rt_new_array({self.array_desc(s.elem)}, {M.var(s.length)}, "
                                  f"{self.loc(s.loc)})")

    def args(self, args, extra: list[str] | None = None) -> str:
        return ", ".join([M.var(a) for a in args] + (extra or []))

    def i_NewObj(self, s: ir.NewObj) -> list[str]:
        if s.ctor is None:
            return self.assign(s.dst, f"rt_new_object({self.loc(s.loc)})")
        return self.assign(s.dst, f"{M.alloc_fn(s.cls, s.ctor)}({self.args(s.args, [self.loc(s.loc)])})")

    def obj_of(self, v: ir.Var) -> str:
        """Выражение типа rt_object * (заголовок полного объекта) для ссылки v."""
        t, x = v.ty, M.var(v)
        if T.is_user_class(t) or isinstance(t, T.InterfaceType):
            return f"rt_top({x})"
        if t == T.STRING or isinstance(t, T.ArrayType):
            return f"(rt_object *){x}"
        return x

    def target_desc(self, target) -> str:
        if isinstance(target, T.ArrayType):
            return self.array_desc(target.elem)
        if isinstance(target, T.ClassType):
            sym = target.sym
            return {"String": "&rt_class_String", "Class": "&rt_class_Class",
                    "Object": "&rt_class_Object"}[sym.name] if sym.builtin else "&" + M.class_desc(sym)
        if hasattr(target, "supers"):
            return "&" + M.iface_desc(target)
        return "&" + M.class_desc(target)

    def i_RefCast(self, s: ir.RefCast) -> list[str]:
        src, kind = M.var(s.src), s.kind
        loc = self.loc(s.loc)
        dst_c = ctype(s.dst.ty)
        if kind == "up":
            return self.assign(s.dst, f"({src} == NULL) ? NULL : {self.sub_expr(src, s.path)}")
        if kind == "to_object":
            return self.assign(s.dst, self.obj_of(s.src))
        if kind == "down_enclosing":
            return self.assign(s.dst, f"({dst_c})rt_cast_enclosing((void *){src}, "
                                      f"{self.target_desc(s.target)}, {loc})")
        if kind == "obj_to_class":
            return self.assign(s.dst, f"({dst_c})rt_cast_obj_class({src}, {self.target_desc(s.target)}, {loc})")
        if kind == "obj_exact":
            return self.assign(s.dst, f"({dst_c})rt_cast_obj_exact({src}, {self.target_desc(s.target)}, {loc})")
        if kind == "iface_to_class":
            return self.assign(s.dst, f"({dst_c})rt_cast_obj_class(rt_top({src}), "
                                      f"{self.target_desc(s.target)}, {loc})")
        if kind == "to_iface_static":
            return self.assign(s.dst, f"(void *){src}")
        if kind == "to_iface_dynamic":
            return self.assign(s.dst, f"rt_cast_sub_iface((void *){src}, {self.target_desc(s.target)}, {loc})")
        if kind == "obj_to_iface":
            return self.assign(s.dst, f"rt_cast_obj_iface({src}, {self.target_desc(s.target)}, {loc})")
        raise InternalCompilerError(f"вид приведения {kind}")

    def i_InstOf(self, s: ir.InstOf) -> list[str]:
        src, kind = M.var(s.src), s.kind
        d = self.target_desc(s.target) if kind != "static" else ""
        expr = {
            "static": f"(jboolean)({src} != NULL)",
            "down_enclosing": f"rt_is_enclosing((void *){src}, {d})",
            "obj_to_class": f"rt_obj_is_class({src}, {d})",
            "obj_exact": f"rt_obj_is_exact({src}, {d})",
            "iface_to_class": f"rt_obj_is_class(rt_top({src}), {d})",
            "to_iface_dynamic": f"rt_is_sub_iface((void *){src}, {d})",
            "obj_to_iface": f"rt_obj_is_iface({src}, {d})",
        }.get(kind)
        if expr is None:
            raise InternalCompilerError(f"вид instanceof {kind}")
        return self.assign(s.dst, expr)

    def i_CallVirt(self, s: ir.CallVirt) -> list[str]:
        r = M.var(s.recv)
        call = (f"((const struct {M.vtable_type(s.view)} *){r}->vt)->{M.slot(s.method)}"
                f"({self.args([s.recv] + s.args)})")
        return [f"rt_nullchk({r}, {self.loc(s.loc)});"] + self.assign(s.dst, call)

    def i_CallIface(self, s: ir.CallIface) -> list[str]:
        r = M.var(s.recv)
        table = (f"((const struct {M.itable_type(s.iface)} *)"
                 f"rt_itable({r}, &{M.iface_desc(s.iface)}, {self.loc(s.loc)}))")
        return self.assign(s.dst, f"{table}->{M.slot(s.method)}({self.args([s.recv] + s.args)})")

    def i_CallObj(self, s: ir.CallObj) -> list[str]:
        o = self.obj_of(s.recv)
        loc = self.loc(s.loc)
        if s.which == "toString":
            return self.assign(s.dst, f"rt_obj_toString({o}, {loc})")
        if s.which == "equals":
            return self.assign(s.dst, f"rt_obj_equals({o}, {M.var(s.args[0])}, {loc})")
        return self.assign(s.dst, f"rt_obj_getClass({o}, {loc})")

    def i_CallObjBase(self, s: ir.CallObjBase) -> list[str]:
        o = f"rt_top({M.var(s.recv)})"
        if s.which == "toString":
            return self.assign(s.dst, f"rt_obj_default_toString({o})")
        return self.assign(s.dst, f"rt_obj_default_equals({o}, {M.var(s.args[0])})")

    def i_CallDirect(self, s: ir.CallDirect) -> list[str]:
        r = M.var(s.recv)
        pre = [f"rt_nullchk({r}, {self.loc(s.loc)});"] if s.loc is not None else []
        m = s.method
        call = f"{M.method_fn(m.owner, m)}({', '.join([self.sub_expr(r, s.path)] + [M.var(a) for a in s.args])})"
        return pre + self.assign(s.dst, call)

    def i_CallInit(self, s: ir.CallInit) -> list[str]:
        self_expr = f"&{M.var(s.recv)}->{M.parent(s.parent)}"
        return [f"{M.init_fn(s.parent, s.ctor)}({', '.join([self_expr] + [M.var(a) for a in s.args])});"]

    def i_CallBuiltin(self, s: ir.CallBuiltin) -> list[str]:
        name = s.name
        a = [M.var(x) for x in s.args]
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
            code = name[8:]
            fn = {"Z": "rt_str_from_bool({0}", "C": "rt_str_from_char({0}", "B": "rt_str_from_int((jint){0}",
                  "S": "rt_str_from_int((jint){0}", "I": "rt_str_from_int({0}", "J": "rt_str_from_long({0}",
                  "F": "rt_str_from_float({0}", "D": "rt_str_from_double({0}"}[code]
            return self.assign(s.dst, fn.format(*a) + f", {loc})")
        table = {
            "str_length": f"rt_str_length({', '.join(a + [loc])})",
            "str_charAt": f"rt_str_charAt({', '.join(a + [loc])})",
            "str_substring": f"rt_str_substring({', '.join(a + [loc])})",
            "str_equals": f"rt_str_equals({', '.join(a + [loc])})",
            "class_getName": f"rt_class_getName({', '.join(a + [loc])})",
        }
        if name in table:
            return self.assign(s.dst, table[name])
        if name.startswith("math_"):
            return self.assign(s.dst, f"rt_{name}({', '.join(a)})")
        raise InternalCompilerError(f"встроенная функция {name}")

    # ------------------------------------------------------------ allocators
    def gen_allocators(self) -> list[str]:
        out = []
        for cc in self.p.concrete:
            d = cc.cls
            for ctor in d.ctors:
                names = [f"a{k}" for k in range(len(ctor.params))]
                params = [decl(t, n) for t, n in zip(ctor.param_types, names)] + ["const rt_loc *loc"]
                sig = f"struct {M.sub_struct(d)} *{M.alloc_fn(d, ctor)}({', '.join(params)})"
                self.proto(sig)
                out += [f"/* new {c_comment(ctor.display())} */", sig, "{",
                        f"    struct {M.full_struct(d)} *o;",
                        f"    struct {M.sub_struct(d)} *t;",
                        f"    o = (struct {M.full_struct(d)} *)rt_alloc_object(&{M.class_desc(d)}, "
                        f"sizeof(struct {M.full_struct(d)}), loc);",
                        "    t = &o->top;"]
                for vt in cc.vtables:
                    path = self.member_path(vt.sub.path)
                    target = f"t->{path + '.' if path else ''}vt"
                    out.append(f"    {target} = &{M.vtable_inst(d, vt.sub.index)}.h;")
                for path, f in cc.init_fields:
                    out.append(f"    {self.field_expr('t', path, f)} = {zero(f.type)};")
                out.append(f"    {M.init_fn(d, ctor)}({', '.join(['t'] + names)});")
                out += ["    return t;", "}", ""]
        return out

    # ------------------------------------------------------------ tables
    def impl_call(self, impl, self_expr: str, n: int) -> str:
        args = ", ".join([self_expr] + [f"a{k}" for k in range(n)])
        return f"{M.method_fn(impl.owner, impl)}({args})"

    def adjusted(self, d, from_path: tuple, site: tuple, impl) -> str:
        delta = f"({self.offset(d, site)}) - ({self.offset(d, from_path)})"
        return f"(struct {M.sub_struct(impl.owner)} *)((char *)self + ({delta}))"

    def thunk(self, name: str, self_c: str, m, impl, adjusted: str) -> list[str]:
        names = [f"a{k}" for k in range(len(m.params))]
        sig = f"{decl(m.ret, name)}({self.params_c(self_c, m.param_types, names)})"
        self.proto(sig)
        call = self.impl_call(impl, adjusted, len(m.params))
        body = f"    return {call};" if m.ret is not T.VOID else f"    {call};"
        return [sig, "{", body, "}", ""]

    def gen_tables(self) -> list[str]:
        out = []
        for i in self.p.interfaces:
            out.append(f"const rt_iface {M.iface_desc(i)} = {{ {c_string(i.name)} }};")
        out.append("")
        for cc in self.p.concrete:
            d = cc.cls
            out.append(f"/* ===== dynamic class {c_comment(d.name)} ===== */")
            for vt in cc.vtables:
                sub = vt.sub
                view = sub.cls
                fptrs = []
                for slot in vt.slots:
                    if slot.site == sub.path and slot.impl.owner is view:
                        fptrs.append(M.method_fn(view, slot.impl))
                    else:
                        name = M.thunk(d, sub.index, slot.method)
                        out += self.thunk(name, decl(T.ClassType(view), "self"), slot.method, slot.impl,
                                          self.adjusted(d, sub.path, slot.site, slot.impl))
                        fptrs.append(name)
                entries = []
                for it in vt.itables:
                    inames = []
                    for slot in it.slots:
                        name = M.ithunk(d, sub.index, it.iface, slot.method)
                        out += self.thunk(name, "void *self", slot.method, slot.impl,
                                          self.adjusted(d, sub.path, slot.site, slot.impl))
                        inames.append(name)
                    inst = M.itable_inst(d, sub.index, it.iface)
                    self.hdr.append(f"extern const struct {M.itable_type(it.iface)} {inst};")
                    out.append(f"const struct {M.itable_type(it.iface)} {inst} = {{ "
                               f"{', '.join(inames) if inames else '0'} }};")
                    entries.append(f"{{ &{M.iface_desc(it.iface)}, &{inst} }}")
                ent_name = "NULL"
                if entries:
                    ent_name = M.itable_entries(d, sub.index)
                    self.hdr.append(f"extern const rt_itable_entry {ent_name}[];")
                    out.append(f"const rt_itable_entry {ent_name}[] = {{ {', '.join(entries)} }};")
                inst = M.vtable_inst(d, sub.index)
                self.hdr.append(f"extern const struct {M.vtable_type(view)} {inst};")
                head = f"{{ {self.top_offset(d, sub.path)}, {sub.index}, {len(entries)}, {ent_name} }}"
                out.append(f"/* VTable of subobject {c_comment(self.h.path_str(d, sub.path))} */")
                out.append(f"const struct {M.vtable_type(view)} {inst} = {{ {', '.join([head] + fptrs)} }};")
                out.append("")
            out += self.object_thunks(cc)
        return out

    def object_thunks(self, cc: ir.ConcreteClass) -> list[str]:
        out = []
        d = cc.cls
        for which, res in (("toString", cc.to_string), ("equals", cc.equals)):
            if res is None:
                continue
            name = M.obj_thunk(d, which)
            impl = res.decl
            target = f"(struct {M.sub_struct(impl.owner)} *)((char *)self + ({self.top_offset(d, res.path)}))"
            if which == "toString":
                sig = f"rt_string *{name}(rt_object *self)"
                body = f"    return {M.method_fn(impl.owner, impl)}({target});"
            else:
                sig = f"jboolean {name}(rt_object *self, rt_object *other)"
                body = f"    return {M.method_fn(impl.owner, impl)}({target}, other);"
            self.proto(sig)
            out += [f"/* Object.{which} of {c_comment(d.name)} (rule 12.4) */", sig, "{", body, "}", ""]
        return out

    def gen_class_descs(self) -> list[str]:
        out = ["/* class metadata */"]
        concrete = {cc.cls: cc for cc in self.p.concrete}
        hdr = "{ &rt_class_Class, 0UL, 0 }"
        for c in self.p.classes:
            name = "&" + self.string(c.name)
            cc = concrete.get(c)
            if cc is None:
                out.append(f"rt_class {M.class_desc(c)} = {{ {hdr}, {name}, RT_K_CLASS, 0UL, 0, NULL, 0, NULL, "
                           f"NULL, NULL, 0, 0UL, NULL, NULL }};")
                continue
            subs = []
            for sub in cc.subobjects:
                subs.append(f"{{ &{M.class_desc(sub.cls)}, {self.top_offset(c, sub.path)}, {sub.enclosing} }}")
            self.hdr.append(f"extern const rt_subdesc {M.subdescs(c)}[];")
            out.append(f"const rt_subdesc {M.subdescs(c)}[] = {{ {', '.join(subs)} }};")
            refs = "NULL"
            if cc.ref_fields:
                offs = []
                for path, f in cc.ref_fields:
                    owner = path[-1] if path else c
                    offs.append(f"(unsigned long)({self.top_offset(c, path)} + "
                                f"(long)offsetof(struct {M.sub_struct(owner)}, {M.field(f)}))")
                refs = M.refoffs(c)
                self.hdr.append(f"extern const unsigned long {refs}[];")
                out.append(f"const unsigned long {refs}[] = {{ {', '.join(offs)} }};")
            ts = M.obj_thunk(c, "toString") if cc.to_string is not None else "NULL"
            eq = M.obj_thunk(c, "equals") if cc.equals is not None else "NULL"
            out.append(f"rt_class {M.class_desc(c)} = {{ {hdr}, {name}, RT_K_CLASS, "
                       f"(unsigned long)sizeof(struct {M.full_struct(c)}), {len(cc.subobjects)}, {M.subdescs(c)}, "
                       f"{len(cc.ref_fields)}, {refs}, {ts}, {eq}, 0, 0UL, NULL, NULL }};")
        for elem in self.p.array_elems:
            name = "&" + self.string("[L" + elem.sym.name + ";")
            if isinstance(elem, T.InterfaceType):
                kind, cls, iface = "RT_E_IFACE", "NULL", "&" + M.iface_desc(elem.sym)
            else:
                kind, cls, iface = "RT_E_CLASS", "&" + M.class_desc(elem.sym), "NULL"
            out.append(f"rt_class {M.array_desc(elem)} = {{ {hdr}, {name}, RT_K_ARRAY, 0UL, 0, NULL, 0, NULL, "
                       f"NULL, NULL, {kind}, (unsigned long)sizeof(void *), {cls}, {iface} }};")
        out.append("")
        return out

    def gen_entry(self) -> list[str]:
        e = self.p.entry
        return ["/* Java entry point */", "void rt_user_main(rt_array *args)", "{",
                f"    {M.method_fn(e.cls, e.method)}(args);", "}"]


def generate(prog: ir.Program, header_name: str) -> tuple[str, str]:
    return CodeGen(prog, header_name).generate()
