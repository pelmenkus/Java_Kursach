/*
 * runtime.h -- public API of the runtime library (ANSI C89 / ISO C90).
 *
 * Object model (README section 7):
 *   - every heap object (class instance, array, string) starts with rt_object,
 *     the single shared Object subobject: dynamic class, identity number, GC mark;
 *   - a class-typed reference points to the subobject of its static class;
 *     every subobject starts with a pointer to the vtable of its branch;
 *   - Object/String/array/Class references point to the header itself;
 *   - interface references point to the subobject (view) implementing them.
 */
#ifndef RT_RUNTIME_H
#define RT_RUNTIME_H

#include <stddef.h>

/* ------------------------------------------------------------ primitive types */
typedef signed char jbyte;
typedef short jshort;
typedef int jint;
typedef unsigned short jchar;
typedef unsigned char jboolean;
typedef float jfloat;
typedef double jdouble;
/* 64-bit long emulated in pure C89 (decision D1): two's complement value hi:lo */
typedef struct jlong { unsigned long lo; unsigned long hi; } jlong;

/* compile-time checks of the target assumptions */
typedef char rt_assert_int_is_32[(sizeof(int) == 4) ? 1 : -1];
typedef char rt_assert_short_is_16[(sizeof(short) == 2) ? 1 : -1];
typedef char rt_assert_float_is_32[(sizeof(float) == 4) ? 1 : -1];
typedef char rt_assert_double_is_64[(sizeof(double) == 8) ? 1 : -1];

/* ------------------------------------------------------------ metadata */
typedef struct rt_class rt_class;
typedef struct rt_string rt_string;

typedef struct rt_object {
    rt_class *cls;          /* dynamic class */
    unsigned long id;       /* allocation sequence number (0 for static objects) */
    int mark;               /* GC mark */
} rt_object;

typedef struct rt_iface {
    const char *name;
} rt_iface;

typedef struct rt_itable_entry {
    const rt_iface *iface;
    const void *table;      /* const struct ji_<I> * */
} rt_itable_entry;

typedef struct rt_vtbl_hdr {
    long top_offset;        /* offset of this subobject from the object header */
    int sub_index;          /* index in rt_class.subs of the dynamic class */
    int nitables;
    const rt_itable_entry *itables;
} rt_vtbl_hdr;

typedef struct rt_subdesc {
    const rt_class *cls;    /* static class of the subobject */
    long offset;            /* from the object header */
    int enclosing;          /* index of the enclosing subobject, -1 for the top one */
} rt_subdesc;

#define RT_K_CLASS    1
#define RT_K_STRING   2
#define RT_K_ARRAY    3
#define RT_K_CLASSOBJ 4
#define RT_K_OBJECT   5

/* array element kinds */
#define RT_E_BOOLEAN 1
#define RT_E_CHAR    2
#define RT_E_BYTE    3
#define RT_E_SHORT   4
#define RT_E_INT     5
#define RT_E_LONG    6
#define RT_E_FLOAT   7
#define RT_E_DOUBLE  8
#define RT_E_OBJECT  9      /* Object: header pointers, any object */
#define RT_E_EXACT   10     /* String/Class: header pointers, exact class */
#define RT_E_CLASS   11     /* user class: subobject views of elem_cls */
#define RT_E_IFACE   12     /* interface views */

struct rt_class {
    rt_object hdr;                      /* a Class object for getClass() */
    rt_string *name;                    /* Java-compatible name (I16) */
    int kind;
    unsigned long size;                 /* size of a complete instance */
    int nsubs;
    const rt_subdesc *subs;             /* [0] is the top subobject */
    int nrefs;
    const unsigned long *ref_offsets;   /* reference fields, from the header */
    rt_string *(*to_string)(rt_object *self);               /* NULL: Object's */
    jboolean (*equals)(rt_object *self, rt_object *other);  /* NULL: Object's */
    int elem_kind;                      /* arrays */
    unsigned long elem_size;
    const rt_class *elem_cls;
    const rt_iface *elem_iface;
};

struct rt_string {
    rt_object hdr;
    jint length;
    jchar *chars;
};

typedef struct rt_array {
    rt_object hdr;
    jint length;
} rt_array;

/* element data follows the array header at an 8-aligned offset */
#define RT_ARRAY_DATA_OFFSET ((sizeof(rt_array) + 7) / 8 * 8)
#define RT_ADATA(a) ((void *)((char *)(a) + RT_ARRAY_DATA_OFFSET))

typedef struct rt_loc {
    const char *file;
    int line;
    int column;
    const char *function;
} rt_loc;

extern rt_class rt_class_Object;
extern rt_class rt_class_String;
extern rt_class rt_class_Class;
extern rt_class rt_class_arr_Z, rt_class_arr_C, rt_class_arr_B, rt_class_arr_S;
extern rt_class rt_class_arr_I, rt_class_arr_J, rt_class_arr_F, rt_class_arr_D;
extern rt_class rt_class_arr_Object, rt_class_arr_String, rt_class_arr_Class;

/* ------------------------------------------------------------ errors */
#define RT_ERR_NULL     1
#define RT_ERR_INDEX    2
#define RT_ERR_DIVZERO  3
#define RT_ERR_CAST     4
#define RT_ERR_STORE    5
#define RT_ERR_OOM      6
#define RT_ERR_TYPE     7
#define RT_ERR_NEGSIZE  8

void rt_error(int kind, const char *message, const rt_loc *loc);
void rt_nullchk(const void *p, const rt_loc *loc);

/* ------------------------------------------------------------ memory and GC */
void rt_gc_init(void);
void rt_gc_collect(void);
void *rt_gc_alloc(size_t size, const rt_loc *loc);   /* zeroed; full collection first */
unsigned long rt_gc_live_count(void);
unsigned long rt_gc_collections(void);
rt_object *rt_alloc_object(rt_class *cls, size_t size, const rt_loc *loc);
void rt_core_init(void);
rt_string *rt_str_permanent(const char *ascii);      /* outside the GC heap */
rt_object *rt_new_object(const rt_loc *loc);

/* ------------------------------------------------------------ objects, casts */
const rt_vtbl_hdr *rt_vt(const void *sub);
rt_object *rt_top(const void *sub);                 /* NULL -> NULL */
const void *rt_itable(const void *view, const rt_iface *iface, const rt_loc *loc);
void *rt_cast_enclosing(void *sub, const rt_class *target, const rt_loc *loc);
jboolean rt_is_enclosing(void *sub, const rt_class *target);
void *rt_cast_obj_class(rt_object *o, const rt_class *target, const rt_loc *loc);
jboolean rt_obj_is_class(rt_object *o, const rt_class *target);
rt_object *rt_cast_obj_exact(rt_object *o, const rt_class *target, const rt_loc *loc);
jboolean rt_obj_is_exact(rt_object *o, const rt_class *target);
void *rt_cast_sub_iface(void *view, const rt_iface *iface, const rt_loc *loc);
jboolean rt_is_sub_iface(void *view, const rt_iface *iface);
void *rt_cast_obj_iface(rt_object *o, const rt_iface *iface, const rt_loc *loc);
jboolean rt_obj_is_iface(rt_object *o, const rt_iface *iface);

rt_string *rt_obj_toString(rt_object *o, const rt_loc *loc);
jboolean rt_obj_equals(rt_object *o, rt_object *other, const rt_loc *loc);
rt_object *rt_obj_getClass(rt_object *o, const rt_loc *loc);
rt_string *rt_obj_default_toString(rt_object *o);
jboolean rt_obj_default_equals(rt_object *o, rt_object *other);
rt_string *rt_class_getName(rt_object *cls, const rt_loc *loc);

/* ------------------------------------------------------------ arrays */
rt_array *rt_new_array(rt_class *cls, jint length, const rt_loc *loc);
void rt_arr_check(rt_array *a, jint index, const rt_loc *loc);
jint rt_arr_length(rt_array *a, const rt_loc *loc);
void rt_arr_store_check(rt_array *a, const void *value, const rt_loc *loc);

/* ------------------------------------------------------------ strings */
rt_string *rt_str_new(const jchar *units, jint length, const rt_loc *loc);
rt_string *rt_str_ascii(const char *s, const rt_loc *loc);
jint rt_str_length(rt_string *s, const rt_loc *loc);
jchar rt_str_charAt(rt_string *s, jint index, const rt_loc *loc);
rt_string *rt_str_substring(rt_string *s, jint begin, jint end, const rt_loc *loc);
jboolean rt_str_equals(rt_string *s, rt_string *other, const rt_loc *loc);
rt_string *rt_str_concat(rt_string *a, rt_string *b, const rt_loc *loc);
rt_string *rt_str_from_bool(jboolean v, const rt_loc *loc);
rt_string *rt_str_from_char(jchar v, const rt_loc *loc);
rt_string *rt_str_from_int(jint v, const rt_loc *loc);
rt_string *rt_str_from_long(jlong v, const rt_loc *loc);
rt_string *rt_str_from_float(jfloat v, const rt_loc *loc);
rt_string *rt_str_from_double(jdouble v, const rt_loc *loc);

/* ------------------------------------------------------------ output */
void rt_print_bool(jboolean v);
void rt_print_char(jchar v);
void rt_print_int(jint v);
void rt_print_long(jlong v);
void rt_print_float(jfloat v);
void rt_print_double(jdouble v);
void rt_print_string(rt_string *s);
void rt_print_object(rt_object *o, const rt_loc *loc);
void rt_print_newline(void);

/* ------------------------------------------------------------ int arithmetic */
jint rt_iadd(jint a, jint b);
jint rt_isub(jint a, jint b);
jint rt_imul(jint a, jint b);
jint rt_idiv(jint a, jint b, const rt_loc *loc);
jint rt_irem(jint a, jint b, const rt_loc *loc);
jint rt_ineg(jint a);
jint rt_inot(jint a);
jint rt_iand(jint a, jint b);
jint rt_ior(jint a, jint b);
jint rt_ixor(jint a, jint b);
jint rt_ishl(jint a, jint n);
jint rt_ishr(jint a, jint n);
jint rt_iushr(jint a, jint n);
jbyte rt_i2b(jint v);
jshort rt_i2s(jint v);
jchar rt_i2c(jint v);

/* ------------------------------------------------------------ long arithmetic */
jlong rt_lmake(unsigned long hi, unsigned long lo);
jlong rt_ladd(jlong a, jlong b);
jlong rt_lsub(jlong a, jlong b);
jlong rt_lmul(jlong a, jlong b);
jlong rt_ldiv(jlong a, jlong b, const rt_loc *loc);
jlong rt_lrem(jlong a, jlong b, const rt_loc *loc);
jlong rt_lneg(jlong a);
jlong rt_lnot(jlong a);
jlong rt_land(jlong a, jlong b);
jlong rt_lor(jlong a, jlong b);
jlong rt_lxor(jlong a, jlong b);
jlong rt_lshl(jlong a, jlong n);
jlong rt_lshr(jlong a, jlong n);
jlong rt_lushr(jlong a, jlong n);
int rt_lcmp(jlong a, jlong b);          /* -1, 0, 1 */
jlong rt_i2l(jint v);
jint rt_l2i(jlong v);
jfloat rt_l2f(jlong v);
jdouble rt_l2d(jlong v);

/* ------------------------------------------------------------ floating point */
jfloat rt_fdiv(jfloat a, jfloat b);
jdouble rt_ddiv(jdouble a, jdouble b);
jfloat rt_frem(jfloat a, jfloat b);
jdouble rt_drem(jdouble a, jdouble b);
jint rt_d2i(jdouble v);
jlong rt_d2l(jdouble v);
jfloat rt_d2f(jdouble v);
jdouble rt_nan(void);
jdouble rt_inf(int sign);
int rt_isnan(jdouble v);
int rt_signbit(jdouble v);
int rt_fmt_double(jdouble v, char *buf);    /* Java Double.toString, returns length */
int rt_fmt_float(jfloat v, char *buf);      /* Java Float.toString */
int rt_fmt_long(jlong v, char *buf);
int rt_fmt_int(jint v, char *buf);

/* ------------------------------------------------------------ Math */
jint rt_math_abs_I(jint a);
jlong rt_math_abs_J(jlong a);
jfloat rt_math_abs_F(jfloat a);
jdouble rt_math_abs_D(jdouble a);
jint rt_math_min_I(jint a, jint b);
jint rt_math_max_I(jint a, jint b);
jlong rt_math_min_J(jlong a, jlong b);
jlong rt_math_max_J(jlong a, jlong b);
jfloat rt_math_min_F(jfloat a, jfloat b);
jfloat rt_math_max_F(jfloat a, jfloat b);
jdouble rt_math_min_D(jdouble a, jdouble b);
jdouble rt_math_max_D(jdouble a, jdouble b);
jdouble rt_math_sqrt(jdouble a);
jdouble rt_math_pow(jdouble a, jdouble b);
jdouble rt_math_sin(jdouble a);
jdouble rt_math_cos(jdouble a);

/* ------------------------------------------------------------ platform modules */
/* rt_gc_win.c: conservative stack scan (callee-saved registers spilled by setjmp) */
void rt_platform_gc_init(void);
void rt_platform_scan_stack(void (*visit)(void *word));
/* rt_platform_win.c: UTF-16 command line and UTF-8/console output */
void rt_platform_init(void);
int rt_platform_argc(void);
const jchar *rt_platform_argv(int index, jint *length);
void rt_platform_out(const jchar *units, jint count);
void rt_platform_flush(void);
void rt_platform_err(const char *ascii_or_utf8);

/* ------------------------------------------------------------ entry point */
void rt_user_main(rt_array *args);      /* generated */

#endif
