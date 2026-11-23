/*
 * rt_core.c -- builtin classes, type checks and casts over the single class
 * chain, Object API, arrays, runtime errors and uncaught throw.
 */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "runtime.h"

/* ------------------------------------------------------------ builtin classes */
static void *string_to_string(void *self);
static jboolean string_equals(void *self, void *other);

const rt_vtbl_Object rt_object_vtable = { rt_obj_default_toString, rt_obj_default_equals };
static const rt_vtbl_Object string_vtable = { string_to_string, string_equals };

static const unsigned char n_object[] = "java.lang.Object";
static const unsigned char n_string[] = "java.lang.String";
static const unsigned char n_class[] = "java.lang.Class";
#define NAME(var, text) \
    static rt_string var = { RT_STATIC_HDR(&rt_class_String), (jint)(sizeof(text) - 1), \
                             (jint)(sizeof(text) - 1), true, text }
NAME(name_object, n_object);
NAME(name_string, n_string);
NAME(name_class, n_class);

#define BASE_CLASS(var, nm, kind, size, vt) \
    rt_class var = { RT_STATIC_HDR(&rt_class_Class), nm, NULL, kind, size, 0, NULL, vt, 0, NULL, 0, 0, \
                     NULL, NULL }
BASE_CLASS(rt_class_Object, &name_object, RT_K_OBJECT, sizeof(rt_object), &rt_object_vtable);
rt_class rt_class_String = { RT_STATIC_HDR(&rt_class_Class), &name_string, &rt_class_Object, RT_K_STRING,
                             0, 0, NULL, &string_vtable, 0, NULL, 0, 0, NULL, NULL };
rt_class rt_class_Class = { RT_STATIC_HDR(&rt_class_Class), &name_class, &rt_class_Object, RT_K_CLASSOBJ,
                            0, 0, NULL, &rt_object_vtable, 0, NULL, 0, 0, NULL, NULL };

#define ARRAY_CLASS(var, text, ekind, esize, ecls) \
    static const unsigned char var##_n[] = text; \
    NAME(var##_name, var##_n); \
    rt_class var = { RT_STATIC_HDR(&rt_class_Class), &var##_name, &rt_class_Object, RT_K_ARRAY, 0, 0, NULL, \
                     &rt_object_vtable, 0, NULL, ekind, esize, ecls, NULL }
ARRAY_CLASS(rt_class_arr_Z, "[Z", RT_E_BOOLEAN, sizeof(jboolean), NULL);
ARRAY_CLASS(rt_class_arr_C, "[C", RT_E_CHAR, sizeof(jchar), NULL);
ARRAY_CLASS(rt_class_arr_B, "[B", RT_E_BYTE, sizeof(jbyte), NULL);
ARRAY_CLASS(rt_class_arr_S, "[S", RT_E_SHORT, sizeof(jshort), NULL);
ARRAY_CLASS(rt_class_arr_I, "[I", RT_E_INT, sizeof(jint), NULL);
ARRAY_CLASS(rt_class_arr_J, "[J", RT_E_LONG, sizeof(jlong), NULL);
ARRAY_CLASS(rt_class_arr_F, "[F", RT_E_FLOAT, sizeof(jfloat), NULL);
ARRAY_CLASS(rt_class_arr_D, "[D", RT_E_DOUBLE, sizeof(jdouble), NULL);
ARRAY_CLASS(rt_class_arr_Object, "[Ljava.lang.Object;", RT_E_OBJECT, sizeof(void *), NULL);
ARRAY_CLASS(rt_class_arr_String, "[Ljava.lang.String;", RT_E_EXACT, sizeof(void *), &rt_class_String);
ARRAY_CLASS(rt_class_arr_Class, "[Ljava.lang.Class;", RT_E_EXACT, sizeof(void *), &rt_class_Class);

void rt_init(void)
{
    rt_rets = NULL;
}

/* ------------------------------------------------------------ errors */
static void name_text(const void *str, char *buf, size_t size)
{
    /* class names for messages; names are ASCII (identifiers are ASCII-only) */
    const rt_string *s = str;
    size_t n = 0;
    jint i;
    if (s == NULL) {
        snprintf(buf, size, "?");
        return;
    }
    for (i = 0; i < s->nbytes && n + 1 < size; i++)
        buf[n++] = (char)s->bytes[i];
    buf[n] = '\0';
}

void rt_error(int kind, const char *message, const rt_loc *loc)
{
    static const char *const names[] = { "?", "NullPointer", "ArrayIndexOutOfBounds", "DivisionByZero",
                                         "ClassCast", "ArrayStore", "OutOfMemory", "InvalidRuntimeType",
                                         "NegativeArraySize", "InvalidChar", "InvalidArgument",
                                         "CorruptedRootFrame" };
    char buf[1024];
    const char *name = (kind >= 1 && kind <= 11) ? names[kind] : names[0];
    rt_out_flush();
    snprintf(buf, sizeof buf, "Runtime error [%s]: %s\n", name, message);
    rt_err_text(buf);
    if (loc != NULL && loc->file != NULL) {
        snprintf(buf, sizeof buf, "  at %.400s:%d:%d in %.400s\n", loc->file, loc->line, loc->column,
                 loc->function != NULL ? loc->function : "?");
        rt_err_text(buf);
    }
    exit(1);
}

void rt_nullchk(const void *p, const rt_loc *loc)
{
    if (p == NULL)
        rt_error(RT_ERR_NULL, "null dereference", loc);
}

/* uncaught throw (decision N6): Java-like first line, source position, exit 1 */
void rt_throw(void *ex, const rt_loc *loc)
{
    static const rt_frame_desc desc = { 2, "rt_throw" };
    void *frame[RT_FRAME_WORDS(2)];
    char name[256], buf[1024];
    const rt_string *msg;
    if (ex == NULL)
        rt_error(RT_ERR_NULL, "throw null", loc);
    rt_frame_push(frame, &desc);
    frame[2] = ex;
    frame[3] = rt_user_throwable_message(ex);      /* virtual getMessage(): user code */
    msg = frame[3];
    rt_out_flush();
    name_text(((rt_object *)ex)->cls->name, name, sizeof name);
    snprintf(buf, sizeof buf, "Exception in thread \"main\" %s", name);
    rt_err_text(buf);
    if (msg != NULL) {
        /* by length, not as a C string: the message may contain U+0000 */
        rt_err_text(": ");
        rt_err_bytes(msg->bytes, (size_t)msg->nbytes);
    }
    rt_err_text("\n");
    if (loc != NULL && loc->file != NULL) {
        snprintf(buf, sizeof buf, "  at %.400s:%d:%d in %.400s\n", loc->file, loc->line, loc->column,
                 loc->function != NULL ? loc->function : "?");
        rt_err_text(buf);
    }
    exit(1);
}

static void cast_fail(const rt_class *from, const char *to, const rt_loc *loc)
{
    char a[200], msg[600];
    name_text(from->name, a, sizeof a);
    snprintf(msg, sizeof msg, "cannot cast %s to %s", a, to);
    rt_error(RT_ERR_CAST, msg, loc);
}

/* ------------------------------------------------------------ objects */
const void *rt_vtable(const void *obj)
{
    return ((const rt_object *)obj)->cls->vtable;
}

static const void *find_itable(const rt_class *c, const rt_iface *iface)
{
    size_t k;
    for (k = 0; k < c->nitables; k++)
        if (c->itables[k].iface == iface)
            return c->itables[k].table;
    return NULL;
}

const void *rt_itable(const void *obj, const rt_iface *iface, const rt_loc *loc)
{
    const void *t;
    rt_nullchk(obj, loc);
    t = find_itable(((const rt_object *)obj)->cls, iface);
    if (t == NULL)
        rt_error(RT_ERR_TYPE, "interface table not found for interface reference", loc);
    return t;
}

jboolean rt_is_subclass(const rt_class *c, const rt_class *target)
{
    for (; c != NULL; c = c->super)
        if (c == target)
            return true;
    return false;
}

jboolean rt_implements(const rt_class *c, const rt_iface *iface)
{
    return find_itable(c, iface) != NULL;
}

jboolean rt_inst_class(const void *obj, const rt_class *target)
{
    return obj != NULL && rt_is_subclass(((const rt_object *)obj)->cls, target);
}

jboolean rt_inst_iface(const void *obj, const rt_iface *iface)
{
    return obj != NULL && rt_implements(((const rt_object *)obj)->cls, iface);
}

jboolean rt_inst_exact(const void *obj, const rt_class *target)
{
    return obj != NULL && ((const rt_object *)obj)->cls == target;
}

void *rt_cast_class(void *obj, const rt_class *target, const rt_loc *loc)
{
    char t[200];
    if (obj != NULL && !rt_inst_class(obj, target)) {
        name_text(target->name, t, sizeof t);
        cast_fail(((rt_object *)obj)->cls, t, loc);
    }
    return obj;
}

void *rt_cast_iface(void *obj, const rt_iface *iface, const rt_loc *loc)
{
    if (obj != NULL && !rt_inst_iface(obj, iface))
        cast_fail(((rt_object *)obj)->cls, iface->name, loc);
    return obj;
}

void *rt_cast_exact(void *obj, const rt_class *target, const rt_loc *loc)
{
    char t[200];
    if (obj != NULL && !rt_inst_exact(obj, target)) {
        name_text(target->name, t, sizeof t);
        cast_fail(((rt_object *)obj)->cls, t, loc);
    }
    return obj;
}

/* ------------------------------------------------------------ Object API */
void *rt_new_object(const rt_loc *loc)
{
    return rt_alloc_object(&rt_class_Object, loc);
}

void *rt_obj_default_toString(void *self)
{
    static const rt_frame_desc desc = { 1, "Object.toString" };
    void *frame[RT_FRAME_WORDS(1)];
    void *result;
    char buf[32];
    const rt_object *o = self;
    rt_frame_push(frame, &desc);
    snprintf(buf, sizeof buf, "@%" PRIx64, o->id);
    frame[2] = rt_str_ascii(buf, NULL);                  /* rooted across the next allocation */
    result = rt_str_concat(o->cls->name, frame[2], NULL);
    rt_frame_pop(frame);
    return result;
}

jboolean rt_obj_default_equals(void *self, void *other)
{
    return self == other;
}

void *rt_obj_toString(void *obj, const rt_loc *loc)
{
    rt_nullchk(obj, loc);
    return ((const rt_vtbl_Object *)rt_vtable(obj))->m_toString_0(obj);
}

void *rt_obj_getClass(void *obj, const rt_loc *loc)
{
    rt_nullchk(obj, loc);
    return (void *)&((rt_object *)obj)->cls->hdr;
}

void *rt_class_getName(void *cls, const rt_loc *loc)
{
    rt_nullchk(cls, loc);
    return ((rt_class *)cls)->name;
}

static void *string_to_string(void *self)
{
    return self;
}

static jboolean string_equals(void *self, void *other)
{
    if (other == NULL || ((rt_object *)other)->cls != &rt_class_String)
        return false;
    return rt_str_equals(self, other, NULL);
}

/* ------------------------------------------------------------ arrays */
static int elem_is_ref(int kind)
{
    return kind == RT_E_OBJECT || kind == RT_E_EXACT || kind == RT_E_CLASS || kind == RT_E_IFACE;
}

void *rt_new_array(const rt_class *cls, jint length, const rt_loc *loc)
{
    rt_array *a;
    jint i;
    char msg[64];
    if (length < 0) {
        snprintf(msg, sizeof msg, "negative array size %ld", (long)length);
        rt_error(RT_ERR_NEGSIZE, msg, loc);
    }
    if ((size_t)length > (SIZE_MAX - offsetof(rt_array_layout, data)) / cls->elem_size)
        rt_error(RT_ERR_OOM, "array too large", loc);
    a = rt_alloc_sized(cls, offsetof(rt_array_layout, data) + (size_t)length * cls->elem_size, loc);
    a->length = length;
    /* explicit default values: null pointers and +0.0 need not be all-bits-zero */
    if (elem_is_ref(cls->elem_kind)) {
        void **p = RT_ADATA(a);
        for (i = 0; i < length; i++)
            p[i] = NULL;
    } else if (cls->elem_kind == RT_E_FLOAT) {
        jfloat *p = RT_ADATA(a);
        for (i = 0; i < length; i++)
            p[i] = 0.0f;
    } else if (cls->elem_kind == RT_E_DOUBLE) {
        jdouble *p = RT_ADATA(a);
        for (i = 0; i < length; i++)
            p[i] = 0.0;
    } else if (cls->elem_kind == RT_E_BOOLEAN) {
        jboolean *p = RT_ADATA(a);
        for (i = 0; i < length; i++)
            p[i] = false;
    }
    return a;
}

void rt_arr_check(const void *arr, jint index, const rt_loc *loc)
{
    const rt_array *a = arr;
    char msg[96];
    if (a == NULL)
        rt_error(RT_ERR_NULL, "null array reference", loc);
    if (index < 0 || index >= a->length) {
        snprintf(msg, sizeof msg, "index %ld out of bounds for length %ld", (long)index, (long)a->length);
        rt_error(RT_ERR_INDEX, msg, loc);
    }
}

jint rt_arr_length(const void *arr, const rt_loc *loc)
{
    if (arr == NULL)
        rt_error(RT_ERR_NULL, "null array reference", loc);
    return ((const rt_array *)arr)->length;
}

void rt_arr_store_check(const void *arr, const void *value, const rt_loc *loc)
{
    const rt_class *cls = ((const rt_object *)arr)->cls;
    const rt_class *vc;
    int ok;
    if (value == NULL)
        return;
    vc = ((const rt_object *)value)->cls;
    switch (cls->elem_kind) {
    case RT_E_OBJECT:
        ok = 1;
        break;
    case RT_E_EXACT:
        ok = vc == cls->elem_cls;
        break;
    case RT_E_CLASS:
        ok = rt_is_subclass(vc, cls->elem_cls);
        break;
    case RT_E_IFACE:
        ok = rt_implements(vc, cls->elem_iface);
        break;
    default:
        ok = 0;
    }
    if (!ok)
        rt_error(RT_ERR_STORE, "value is not a valid element of this array", loc);
}
