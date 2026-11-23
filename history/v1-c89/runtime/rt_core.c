/*
 * rt_core.c -- objects, metadata, casts, instanceof, Object API, arrays,
 * runtime errors.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "runtime.h"

/* ------------------------------------------------------------ builtin classes */
static rt_string *string_to_string(rt_object *self);
static jboolean string_equals(rt_object *self, rt_object *other);

#define RT_HDR { &rt_class_Class, 0UL, 0 }
rt_class rt_class_Object = { RT_HDR, NULL, RT_K_OBJECT, sizeof(rt_object), 0, NULL, 0, NULL,
                             NULL, NULL, 0, 0UL, NULL, NULL };
rt_class rt_class_String = { RT_HDR, NULL, RT_K_STRING, 0UL, 0, NULL, 0, NULL,
                             string_to_string, string_equals, 0, 0UL, NULL, NULL };
rt_class rt_class_Class = { RT_HDR, NULL, RT_K_CLASSOBJ, 0UL, 0, NULL, 0, NULL,
                            NULL, NULL, 0, 0UL, NULL, NULL };
#define RT_PRIM_ARRAY(name, kind, type) \
    rt_class name = { RT_HDR, NULL, RT_K_ARRAY, 0UL, 0, NULL, 0, NULL, NULL, NULL, \
                      kind, (unsigned long)sizeof(type), NULL, NULL }
RT_PRIM_ARRAY(rt_class_arr_Z, RT_E_BOOLEAN, jboolean);
RT_PRIM_ARRAY(rt_class_arr_C, RT_E_CHAR, jchar);
RT_PRIM_ARRAY(rt_class_arr_B, RT_E_BYTE, jbyte);
RT_PRIM_ARRAY(rt_class_arr_S, RT_E_SHORT, jshort);
RT_PRIM_ARRAY(rt_class_arr_I, RT_E_INT, jint);
RT_PRIM_ARRAY(rt_class_arr_J, RT_E_LONG, jlong);
RT_PRIM_ARRAY(rt_class_arr_F, RT_E_FLOAT, jfloat);
RT_PRIM_ARRAY(rt_class_arr_D, RT_E_DOUBLE, jdouble);
rt_class rt_class_arr_Object = { RT_HDR, NULL, RT_K_ARRAY, 0UL, 0, NULL, 0, NULL, NULL, NULL,
                                 RT_E_OBJECT, (unsigned long)sizeof(void *), NULL, NULL };
rt_class rt_class_arr_String = { RT_HDR, NULL, RT_K_ARRAY, 0UL, 0, NULL, 0, NULL, NULL, NULL,
                                 RT_E_EXACT, (unsigned long)sizeof(void *), &rt_class_String, NULL };
rt_class rt_class_arr_Class = { RT_HDR, NULL, RT_K_ARRAY, 0UL, 0, NULL, 0, NULL, NULL, NULL,
                                RT_E_EXACT, (unsigned long)sizeof(void *), &rt_class_Class, NULL };

void rt_core_init(void)
{
    rt_class_Object.name = rt_str_permanent("java.lang.Object");
    rt_class_String.name = rt_str_permanent("java.lang.String");
    rt_class_Class.name = rt_str_permanent("java.lang.Class");
    rt_class_arr_Z.name = rt_str_permanent("[Z");
    rt_class_arr_C.name = rt_str_permanent("[C");
    rt_class_arr_B.name = rt_str_permanent("[B");
    rt_class_arr_S.name = rt_str_permanent("[S");
    rt_class_arr_I.name = rt_str_permanent("[I");
    rt_class_arr_J.name = rt_str_permanent("[J");
    rt_class_arr_F.name = rt_str_permanent("[F");
    rt_class_arr_D.name = rt_str_permanent("[D");
    rt_class_arr_Object.name = rt_str_permanent("[Ljava.lang.Object;");
    rt_class_arr_String.name = rt_str_permanent("[Ljava.lang.String;");
    rt_class_arr_Class.name = rt_str_permanent("[Ljava.lang.Class;");
}

/* ------------------------------------------------------------ errors */
static void name_utf8(const rt_string *s, char *buf, size_t size)
{
    /* class names for messages; non-ASCII code units are shown as '?' */
    size_t n = 0;
    jint i;
    if (s == NULL) {
        strcpy(buf, "?");
        return;
    }
    for (i = 0; i < s->length && n + 1 < size; i++) {
        jchar c = s->chars[i];
        buf[n++] = (c >= 32 && c < 127) ? (char)c : '?';
    }
    buf[n] = '\0';
}

void rt_error(int kind, const char *message, const rt_loc *loc)
{
    static const char *names[] = { "?", "NullPointer", "ArrayIndexOutOfBounds", "DivisionByZero",
                                   "ClassCast", "ArrayStore", "OutOfMemory", "InvalidRuntimeType",
                                   "NegativeArraySize" };
    char buf[1024];
    const char *name = (kind >= 1 && kind <= 8) ? names[kind] : names[0];
    rt_platform_flush();
    sprintf(buf, "Runtime error [%s]: ", name);
    rt_platform_err(buf);
    rt_platform_err(message);
    rt_platform_err("\n");
    if (loc != NULL && loc->file != NULL) {
        sprintf(buf, "  at %.400s:%d:%d in %.400s\n", loc->file, loc->line, loc->column,
                loc->function != NULL ? loc->function : "?");
        rt_platform_err(buf);
    }
    exit(1);
}

void rt_nullchk(const void *p, const rt_loc *loc)
{
    if (p == NULL)
        rt_error(RT_ERR_NULL, "null dereference", loc);
}

static void cast_fail(const rt_class *from, const char *to, const char *why, const rt_loc *loc)
{
    char a[200], msg[600];
    name_utf8(from->name, a, sizeof a);
    sprintf(msg, "cannot cast %.200s to %.200s%.150s", a, to, why);
    rt_error(RT_ERR_CAST, msg, loc);
}

/* ------------------------------------------------------------ objects */
static unsigned long next_id = 0;

rt_object *rt_alloc_object(rt_class *cls, size_t size, const rt_loc *loc)
{
    rt_object *o = (rt_object *)rt_gc_alloc(size, loc);
    o->cls = cls;
    o->id = ++next_id;
    o->mark = 0;
    return o;
}

rt_object *rt_new_object(const rt_loc *loc)
{
    return rt_alloc_object(&rt_class_Object, sizeof(rt_object), loc);
}

const rt_vtbl_hdr *rt_vt(const void *sub)
{
    return *(const rt_vtbl_hdr * const *)sub;
}

rt_object *rt_top(const void *sub)
{
    if (sub == NULL)
        return NULL;
    return (rt_object *)((char *)sub - rt_vt(sub)->top_offset);
}

static const void *find_itable(const void *view, const rt_iface *iface)
{
    const rt_vtbl_hdr *h = rt_vt(view);
    int k;
    for (k = 0; k < h->nitables; k++)
        if (h->itables[k].iface == iface)
            return h->itables[k].table;
    return NULL;
}

const void *rt_itable(const void *view, const rt_iface *iface, const rt_loc *loc)
{
    const void *t;
    rt_nullchk(view, loc);
    t = find_itable(view, iface);
    if (t == NULL)
        rt_error(RT_ERR_TYPE, "interface table not found for interface reference", loc);
    return t;
}

static void *enclosing(void *sub, const rt_class *target)
{
    rt_object *o = rt_top(sub);
    int k = rt_vt(sub)->sub_index;
    while (k >= 0) {
        if (o->cls->subs[k].cls == target)
            return (char *)o + o->cls->subs[k].offset;
        k = o->cls->subs[k].enclosing;
    }
    return NULL;
}

void *rt_cast_enclosing(void *sub, const rt_class *target, const rt_loc *loc)
{
    void *r;
    char t[200];
    if (sub == NULL)
        return NULL;
    r = enclosing(sub, target);
    if (r == NULL) {
        name_utf8(target->name, t, sizeof t);
        cast_fail(rt_top(sub)->cls, t, " (the subobject is not inside such an object)", loc);
    }
    return r;
}

jboolean rt_is_enclosing(void *sub, const rt_class *target)
{
    return (jboolean)(sub != NULL && enclosing(sub, target) != NULL);
}

static int count_subs(const rt_object *o, const rt_class *target, int *found)
{
    int k, n = 0;
    if (o->cls->kind != RT_K_CLASS)
        return 0;
    for (k = 0; k < o->cls->nsubs; k++)
        if (o->cls->subs[k].cls == target) {
            *found = k;
            n++;
        }
    return n;
}

void *rt_cast_obj_class(rt_object *o, const rt_class *target, const rt_loc *loc)
{
    int found = -1, n;
    char t[200];
    if (o == NULL)
        return NULL;
    n = count_subs(o, target, &found);
    if (n != 1) {
        name_utf8(target->name, t, sizeof t);
        cast_fail(o->cls, t, n > 1 ? " (ambiguous: several subobjects of the target class)" : "", loc);
    }
    return (char *)o + o->cls->subs[found].offset;
}

jboolean rt_obj_is_class(rt_object *o, const rt_class *target)
{
    int found = -1;
    return (jboolean)(o != NULL && count_subs(o, target, &found) == 1);
}

rt_object *rt_cast_obj_exact(rt_object *o, const rt_class *target, const rt_loc *loc)
{
    char t[200];
    if (o == NULL)
        return NULL;
    if (o->cls != target) {
        name_utf8(target->name, t, sizeof t);
        cast_fail(o->cls, t, "", loc);
    }
    return o;
}

jboolean rt_obj_is_exact(rt_object *o, const rt_class *target)
{
    return (jboolean)(o != NULL && o->cls == target);
}

static void *sub_iface(void *view, const rt_iface *iface)
{
    rt_object *o = rt_top(view);
    int k = rt_vt(view)->sub_index;
    while (k >= 0) {
        void *s = (char *)o + o->cls->subs[k].offset;
        if (find_itable(s, iface) != NULL)
            return s;
        k = o->cls->subs[k].enclosing;
    }
    return NULL;
}

void *rt_cast_sub_iface(void *view, const rt_iface *iface, const rt_loc *loc)
{
    void *r;
    if (view == NULL)
        return NULL;
    r = sub_iface(view, iface);
    if (r == NULL)
        cast_fail(rt_top(view)->cls, iface->name, "", loc);
    return r;
}

jboolean rt_is_sub_iface(void *view, const rt_iface *iface)
{
    return (jboolean)(view != NULL && sub_iface(view, iface) != NULL);
}

static void *obj_iface(rt_object *o, const rt_iface *iface)
{
    void *top;
    if (o->cls->kind != RT_K_CLASS || o->cls->nsubs == 0)
        return NULL;
    top = (char *)o + o->cls->subs[0].offset;
    return find_itable(top, iface) != NULL ? top : NULL;
}

void *rt_cast_obj_iface(rt_object *o, const rt_iface *iface, const rt_loc *loc)
{
    void *r;
    if (o == NULL)
        return NULL;
    r = obj_iface(o, iface);
    if (r == NULL)
        cast_fail(o->cls, iface->name, "", loc);
    return r;
}

jboolean rt_obj_is_iface(rt_object *o, const rt_iface *iface)
{
    return (jboolean)(o != NULL && obj_iface(o, iface) != NULL);
}

/* ------------------------------------------------------------ Object API */
rt_string *rt_obj_default_toString(rt_object *o)
{
    char buf[32];
    rt_string *at, *name;
    sprintf(buf, "@%lx", o->id);
    at = rt_str_ascii(buf, NULL);
    name = o->cls->name;
    return rt_str_concat(name, at, NULL);
}

jboolean rt_obj_default_equals(rt_object *o, rt_object *other)
{
    return (jboolean)(o == other);
}

rt_string *rt_obj_toString(rt_object *o, const rt_loc *loc)
{
    rt_nullchk(o, loc);
    if (o->cls->to_string != NULL)
        return o->cls->to_string(o);
    return rt_obj_default_toString(o);
}

jboolean rt_obj_equals(rt_object *o, rt_object *other, const rt_loc *loc)
{
    rt_nullchk(o, loc);
    if (o->cls->equals != NULL)
        return o->cls->equals(o, other);
    return rt_obj_default_equals(o, other);
}

rt_object *rt_obj_getClass(rt_object *o, const rt_loc *loc)
{
    rt_nullchk(o, loc);
    return &o->cls->hdr;
}

rt_string *rt_class_getName(rt_object *cls, const rt_loc *loc)
{
    rt_nullchk(cls, loc);
    return ((rt_class *)cls)->name;
}

static rt_string *string_to_string(rt_object *self)
{
    return (rt_string *)self;
}

static jboolean string_equals(rt_object *self, rt_object *other)
{
    if (other == NULL || other->cls != &rt_class_String)
        return 0;
    return rt_str_equals((rt_string *)self, (rt_string *)other, NULL);
}

/* ------------------------------------------------------------ arrays */
static int elem_is_ref(int kind)
{
    return kind == RT_E_OBJECT || kind == RT_E_EXACT || kind == RT_E_CLASS || kind == RT_E_IFACE;
}

rt_array *rt_new_array(rt_class *cls, jint length, const rt_loc *loc)
{
    rt_array *a;
    size_t size;
    jint i;
    char msg[64];
    if (length < 0) {
        sprintf(msg, "negative array size %d", length);
        rt_error(RT_ERR_NEGSIZE, msg, loc);
    }
    if ((size_t)length > ((size_t)-1 - RT_ARRAY_DATA_OFFSET) / cls->elem_size)
        rt_error(RT_ERR_OOM, "array too large", loc);
    size = RT_ARRAY_DATA_OFFSET + (size_t)length * cls->elem_size;
    a = (rt_array *)rt_alloc_object(cls, size, loc);
    a->length = length;
    /* explicit default values (the block is already zeroed) */
    if (elem_is_ref(cls->elem_kind)) {
        void **p = (void **)RT_ADATA(a);
        for (i = 0; i < length; i++)
            p[i] = NULL;
    } else if (cls->elem_kind == RT_E_FLOAT) {
        jfloat *p = (jfloat *)RT_ADATA(a);
        for (i = 0; i < length; i++)
            p[i] = 0.0f;
    } else if (cls->elem_kind == RT_E_DOUBLE) {
        jdouble *p = (jdouble *)RT_ADATA(a);
        for (i = 0; i < length; i++)
            p[i] = 0.0;
    }
    return a;
}

void rt_arr_check(rt_array *a, jint index, const rt_loc *loc)
{
    char msg[96];
    if (a == NULL)
        rt_error(RT_ERR_NULL, "null array reference", loc);
    if (index < 0 || index >= a->length) {
        sprintf(msg, "index %d out of bounds for length %d", index, a->length);
        rt_error(RT_ERR_INDEX, msg, loc);
    }
}

jint rt_arr_length(rt_array *a, const rt_loc *loc)
{
    if (a == NULL)
        rt_error(RT_ERR_NULL, "null array reference", loc);
    return a->length;
}

void rt_arr_store_check(rt_array *a, const void *value, const rt_loc *loc)
{
    const rt_class *cls = a->hdr.cls;
    int ok = 1;
    if (value == NULL)
        return;
    switch (cls->elem_kind) {
    case RT_E_OBJECT:
        break;
    case RT_E_EXACT:
        ok = ((const rt_object *)value)->cls == cls->elem_cls;
        break;
    case RT_E_CLASS: {
        rt_object *o = rt_top(value);
        ok = o->cls->kind == RT_K_CLASS && o->cls->subs[rt_vt(value)->sub_index].cls == cls->elem_cls;
        break;
    }
    case RT_E_IFACE:
        ok = find_itable(value, cls->elem_iface) != NULL;
        break;
    default:
        ok = 0;
    }
    if (!ok)
        rt_error(RT_ERR_STORE, "value is not a valid element of this array", loc);
}
