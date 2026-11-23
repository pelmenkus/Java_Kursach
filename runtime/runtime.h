/*
 * runtime.h -- public API of the runtime library (ISO C99).
 *
 * Object model (README section 7):
 *   - every managed object (class instance, String, array) starts with
 *     rt_object: dynamic class, allocation identity, GC mark;
 *   - all object references are void * pointing to that header;
 *   - single class inheritance: struct jo_B { struct jo_A base; fields },
 *     so a field is always accessed through the struct of its owner class;
 *   - one VTable per class, its first member is the VTable of the parent;
 *     interfaces use separate tables found through rt_class.itables.
 *
 * Portability: only standard C99. The checks below stop the build with a
 * diagnostic on platforms without the required exact-width integers or
 * without IEEE 754 binary32/binary64 floating point (README section 16).
 */
#ifndef RT_RUNTIME_H
#define RT_RUNTIME_H

#include <float.h>
#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#if !defined(INT8_MAX) || !defined(INT16_MAX) || !defined(INT32_MAX) || !defined(INT64_MAX) || \
    !defined(UINT8_MAX) || !defined(UINT16_MAX) || !defined(UINT32_MAX) || !defined(UINT64_MAX)
#error "runtime: int8_t..int64_t and uint8_t..uint64_t are required (Java byte/short/int/long)"
#endif
#if FLT_RADIX != 2 || FLT_MANT_DIG != 24 || FLT_MAX_EXP != 128 || FLT_MIN_EXP != -125 || \
    DBL_MANT_DIG != 53 || DBL_MAX_EXP != 1024 || DBL_MIN_EXP != -1021
#error "runtime: float/double must be IEEE 754 binary32/binary64"
#endif
#if !defined(FLT_EVAL_METHOD) || FLT_EVAL_METHOD != 0
#error "runtime: FLT_EVAL_METHOD must be 0 (no excess precision, e.g. SSE2 instead of x87)"
#endif

/* ------------------------------------------------------------ primitive types */
typedef int8_t jbyte;
typedef int16_t jshort;
typedef int32_t jint;
typedef int64_t jlong;
typedef uint32_t jchar;        /* Unicode scalar value U+0000..U+10FFFF without surrogates */
typedef bool jboolean;
typedef float jfloat;
typedef double jdouble;

/* ------------------------------------------------------------ metadata */
typedef struct rt_class rt_class;

typedef struct rt_object {
    const rt_class *cls;       /* dynamic class */
    uint64_t id;               /* allocation sequence number, 0 for static objects */
    unsigned char mark;        /* GC mark */
} rt_object;

#define RT_STATIC_HDR(cls) { (cls), 0u, 0u }

typedef struct rt_iface {
    const char *name;
} rt_iface;

typedef struct rt_itable_entry {
    const rt_iface *iface;
    const void *table;         /* const struct ji_<I> * */
} rt_itable_entry;

/* VTable prefix shared by every class: the virtual methods of Object.
   Member names are the mangled slot names the code generator uses. */
typedef struct rt_vtbl_Object {
    void *(*m_toString_0)(void *self);
    jboolean (*m_equals_0LObject_9)(void *self, void *other);
} rt_vtbl_Object;

/* VTable of Object itself, also used by arrays */
extern const rt_vtbl_Object rt_object_vtable;

/* class initialisation states (generated jcs_<C> variables, decision N4) */
#define RT_CLS_UNINITIALIZED 0
#define RT_CLS_INITIALIZING  1
#define RT_CLS_INITIALIZED   2

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
#define RT_E_OBJECT  9      /* Object[]: any object */
#define RT_E_EXACT   10     /* String[] / Class[]: exact class */
#define RT_E_CLASS   11     /* user class: elem_cls or a subclass */
#define RT_E_IFACE   12     /* interface: implementing class */

struct rt_class {
    rt_object hdr;                      /* the Class object returned by getClass() */
    void *name;                         /* rt_string *, Java-compatible name */
    const rt_class *super;              /* single parent class, NULL for Object */
    int kind;
    size_t size;                        /* size of an instance (RT_K_CLASS) */
    size_t nrefs;
    const size_t *ref_offsets;          /* reference fields, offsets from the header */
    const void *vtable;                 /* const struct jv_<C> * (rt_vtbl_Object prefix) */
    size_t nitables;
    const rt_itable_entry *itables;     /* all interfaces of the class, inherited included */
    int elem_kind;                      /* arrays */
    size_t elem_size;
    const rt_class *elem_cls;
    const rt_iface *elem_iface;
};

/* Strings are immutable UTF-8; length and indices are in code points. */
typedef struct rt_string {
    rt_object hdr;
    jint length;                        /* code points */
    jint nbytes;                        /* UTF-8 bytes */
    jboolean ascii;                     /* every code point < 0x80: O(1) indexing */
    const unsigned char *bytes;
} rt_string;

typedef struct rt_array {
    rt_object hdr;
    jint length;
} rt_array;

/* element data follows the header at an offset suitable for every element type */
typedef struct rt_array_layout {
    rt_array head;
    union { jlong l; jdouble d; void *p; } data[1];
} rt_array_layout;
#define RT_ADATA(a) ((void *)((char *)(a) + offsetof(rt_array_layout, data)))

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
#define RT_ERR_CHAR     9
#define RT_ERR_ARG      10
#define RT_ERR_FRAME    11

void rt_error(int kind, const char *message, const rt_loc *loc);
void rt_nullchk(const void *p, const rt_loc *loc);
void rt_throw(void *ex, const rt_loc *loc);           /* uncaught throw: never returns */

/* ------------------------------------------------------------ GC: precise roots */
/* Root frame (decision N1): frame[0] = previous frame, frame[1] = descriptor,
   frame[2 .. root_count+1] = reference slots, frame[root_count+2] = frame. */
typedef struct rt_frame_desc {
    size_t root_count;
    const char *function;
} rt_frame_desc;

#define RT_FRAME_WORDS(k) ((k) + 3)
extern void **rt_rets;
void rt_frame_push(void **frame, const rt_frame_desc *desc);
void rt_frame_pop(void **frame);

/* static reference fields: defined by the generated program */
extern void *globals[];
extern const size_t globals_count;

void rt_gc_collect(void);
void *rt_gc_alloc(size_t size, const rt_loc *loc);    /* zeroed; full collection first */
size_t rt_gc_live_count(void);
unsigned long rt_gc_collections(void);
extern void (*rt_gc_hook)(void);                      /* test hook: after every collection */
void *rt_alloc_object(const rt_class *cls, const rt_loc *loc);
void *rt_alloc_sized(const rt_class *cls, size_t size, const rt_loc *loc);

/* ------------------------------------------------------------ objects, casts */
const void *rt_vtable(const void *obj);
const void *rt_itable(const void *obj, const rt_iface *iface, const rt_loc *loc);
jboolean rt_is_subclass(const rt_class *c, const rt_class *target);
jboolean rt_implements(const rt_class *c, const rt_iface *iface);
void *rt_cast_class(void *obj, const rt_class *target, const rt_loc *loc);
void *rt_cast_iface(void *obj, const rt_iface *iface, const rt_loc *loc);
void *rt_cast_exact(void *obj, const rt_class *target, const rt_loc *loc);
jboolean rt_inst_class(const void *obj, const rt_class *target);
jboolean rt_inst_iface(const void *obj, const rt_iface *iface);
jboolean rt_inst_exact(const void *obj, const rt_class *target);

void *rt_new_object(const rt_loc *loc);
void *rt_obj_toString(void *obj, const rt_loc *loc);   /* virtual, null-checked */
void *rt_obj_default_toString(void *self);             /* Object.toString */
jboolean rt_obj_default_equals(void *self, void *other);
void *rt_obj_getClass(void *obj, const rt_loc *loc);
void *rt_class_getName(void *cls, const rt_loc *loc);

/* the generated program provides virtual Throwable.getMessage() for rt_throw */
void *rt_user_throwable_message(void *ex);

/* ------------------------------------------------------------ arrays */
void *rt_new_array(const rt_class *cls, jint length, const rt_loc *loc);
void rt_arr_check(const void *arr, jint index, const rt_loc *loc);
jint rt_arr_length(const void *arr, const rt_loc *loc);
void rt_arr_store_check(const void *arr, const void *value, const rt_loc *loc);

/* ------------------------------------------------------------ strings */
void *rt_str_utf8(const unsigned char *bytes, size_t n, const rt_loc *loc);  /* bytes must be valid */
void *rt_str_ascii(const char *s, const rt_loc *loc);
jint rt_str_length(void *s, const rt_loc *loc);
jchar rt_str_charAt(void *s, jint index, const rt_loc *loc);
void *rt_str_substring(void *s, jint begin, jint end, const rt_loc *loc);
jboolean rt_str_equals(void *s, void *other, const rt_loc *loc);
void *rt_str_concat(void *a, void *b, const rt_loc *loc);
void *rt_str_from_bool(jboolean v, const rt_loc *loc);
void *rt_str_from_char(jchar v, const rt_loc *loc);
void *rt_str_from_int(jint v, const rt_loc *loc);
void *rt_str_from_long(jlong v, const rt_loc *loc);
void *rt_str_from_float(jfloat v, const rt_loc *loc);
void *rt_str_from_double(jdouble v, const rt_loc *loc);
int rt_utf8_encode(jchar c, unsigned char *out);      /* 1..4 bytes */

/* ------------------------------------------------------------ output (UTF-8) */
void rt_out_bytes(const unsigned char *bytes, size_t n);
void rt_out_flush(void);
void rt_err_text(const char *utf8);
void rt_err_bytes(const unsigned char *bytes, size_t n);   /* may contain zero bytes */
void rt_print_bool(jboolean v);
void rt_print_char(jchar v);
void rt_print_int(jint v);
void rt_print_long(jlong v);
void rt_print_float(jfloat v);
void rt_print_double(jdouble v);
void rt_print_string(void *s);
void rt_print_object(void *o, const rt_loc *loc);
void rt_print_newline(void);

/* ------------------------------------------------------------ command line */
/* rt_args.c (portable: argv must be UTF-8) or an optional platform adapter */
void *rt_args_init(int argc, char **argv);            /* String[], invalid UTF-8 -> error */

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
jint rt_l2i(jlong v);

/* char arithmetic (decision N2): exact result, InvalidChar unless a scalar value */
jchar rt_cchk(jlong v, const rt_loc *loc);
jchar rt_cdiv(jchar a, jchar b, const rt_loc *loc);
jchar rt_crem(jchar a, jchar b, const rt_loc *loc);

/* ------------------------------------------------------------ floating point */
jfloat rt_fdiv(jfloat a, jfloat b);
jdouble rt_ddiv(jdouble a, jdouble b);
jfloat rt_frem(jfloat a, jfloat b);
jdouble rt_drem(jdouble a, jdouble b);
jint rt_d2i(jdouble v);
jlong rt_d2l(jdouble v);
jfloat rt_d2f(jdouble v);
jdouble rt_dbits(uint64_t bits);
jfloat rt_fbits(uint32_t bits);
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

/* ------------------------------------------------------------ entry point */
void rt_init(void);
void rt_user_main(void *args);      /* generated */

#endif
