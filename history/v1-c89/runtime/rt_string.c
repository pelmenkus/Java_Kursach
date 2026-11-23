/*
 * rt_string.c -- immutable UTF-16 strings and output of values.
 * Length, charAt and substring work in UTF-16 code units.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "runtime.h"

static jchar null_units[] = { 'n', 'u', 'l', 'l' };
static rt_string null_string = { { &rt_class_String, 0UL, 0 }, 4, null_units };
static jchar true_units[] = { 't', 'r', 'u', 'e' };
static jchar false_units[] = { 'f', 'a', 'l', 's', 'e' };

static rt_string *alloc_string(jint length, const rt_loc *loc)
{
    rt_string *s;
    size_t size = sizeof(rt_string) + (size_t)length * sizeof(jchar);
    s = (rt_string *)rt_alloc_object(&rt_class_String, size, loc);
    s->length = length;
    s->chars = (jchar *)((char *)s + sizeof(rt_string));
    return s;
}

rt_string *rt_str_new(const jchar *units, jint length, const rt_loc *loc)
{
    rt_string *s = alloc_string(length, loc);
    jint i;
    for (i = 0; i < length; i++)
        s->chars[i] = units[i];
    return s;
}

rt_string *rt_str_ascii(const char *text, const rt_loc *loc)
{
    jint n = (jint)strlen(text), i;
    rt_string *s = alloc_string(n, loc);
    for (i = 0; i < n; i++)
        s->chars[i] = (jchar)(unsigned char)text[i];
    return s;
}

rt_string *rt_str_permanent(const char *text)
{
    size_t n = strlen(text), i;
    rt_string *s = (rt_string *)malloc(sizeof(rt_string) + (n + 1) * sizeof(jchar));
    if (s == NULL)
        rt_error(RT_ERR_OOM, "out of memory at startup", NULL);
    s->hdr.cls = &rt_class_String;
    s->hdr.id = 0;
    s->hdr.mark = 0;
    s->length = (jint)n;
    s->chars = (jchar *)((char *)s + sizeof(rt_string));
    for (i = 0; i < n; i++)
        s->chars[i] = (jchar)(unsigned char)text[i];
    return s;
}

jint rt_str_length(rt_string *s, const rt_loc *loc)
{
    rt_nullchk(s, loc);
    return s->length;
}

jchar rt_str_charAt(rt_string *s, jint index, const rt_loc *loc)
{
    char msg[96];
    rt_nullchk(s, loc);
    if (index < 0 || index >= s->length) {
        sprintf(msg, "string index %d out of bounds for length %d", index, s->length);
        rt_error(RT_ERR_INDEX, msg, loc);
    }
    return s->chars[index];
}

rt_string *rt_str_substring(rt_string *s, jint begin, jint end, const rt_loc *loc)
{
    char msg[128];
    rt_nullchk(s, loc);
    if (begin < 0 || end > s->length || begin > end) {
        sprintf(msg, "begin %d, end %d, length %d", begin, end, s->length);
        rt_error(RT_ERR_INDEX, msg, loc);
    }
    return rt_str_new(s->chars + begin, end - begin, loc);
}

jboolean rt_str_equals(rt_string *s, rt_string *other, const rt_loc *loc)
{
    jint i;
    rt_nullchk(s, loc);
    if (other == NULL)
        return 0;
    if (s == other)
        return 1;
    if (s->length != other->length)
        return 0;
    for (i = 0; i < s->length; i++)
        if (s->chars[i] != other->chars[i])
            return 0;
    return 1;
}

rt_string *rt_str_concat(rt_string *a, rt_string *b, const rt_loc *loc)
{
    rt_string *s;
    jint i;
    if (a == NULL)
        a = &null_string;
    if (b == NULL)
        b = &null_string;
    if (a->length > 0x7FFFFFFF - b->length)
        rt_error(RT_ERR_OOM, "string too long", loc);
    s = alloc_string(a->length + b->length, loc);
    for (i = 0; i < a->length; i++)
        s->chars[i] = a->chars[i];
    for (i = 0; i < b->length; i++)
        s->chars[a->length + i] = b->chars[i];
    return s;
}

rt_string *rt_str_from_bool(jboolean v, const rt_loc *loc)
{
    return v ? rt_str_new(true_units, 4, loc) : rt_str_new(false_units, 5, loc);
}

rt_string *rt_str_from_char(jchar v, const rt_loc *loc)
{
    return rt_str_new(&v, 1, loc);
}

rt_string *rt_str_from_int(jint v, const rt_loc *loc)
{
    char buf[32];
    rt_fmt_int(v, buf);
    return rt_str_ascii(buf, loc);
}

rt_string *rt_str_from_long(jlong v, const rt_loc *loc)
{
    char buf[32];
    rt_fmt_long(v, buf);
    return rt_str_ascii(buf, loc);
}

rt_string *rt_str_from_float(jfloat v, const rt_loc *loc)
{
    char buf[64];
    rt_fmt_float(v, buf);
    return rt_str_ascii(buf, loc);
}

rt_string *rt_str_from_double(jdouble v, const rt_loc *loc)
{
    char buf[64];
    rt_fmt_double(v, buf);
    return rt_str_ascii(buf, loc);
}

/* ------------------------------------------------------------ output */
static void out_ascii(const char *s)
{
    jchar buf[64];
    jint n = 0;
    while (*s != '\0') {
        buf[n++] = (jchar)(unsigned char)*s++;
        if (n == 64) {
            rt_platform_out(buf, n);
            n = 0;
        }
    }
    if (n > 0)
        rt_platform_out(buf, n);
}

void rt_print_bool(jboolean v)
{
    out_ascii(v ? "true" : "false");
}

void rt_print_char(jchar v)
{
    rt_platform_out(&v, 1);
}

void rt_print_int(jint v)
{
    char buf[32];
    rt_fmt_int(v, buf);
    out_ascii(buf);
}

void rt_print_long(jlong v)
{
    char buf[32];
    rt_fmt_long(v, buf);
    out_ascii(buf);
}

void rt_print_float(jfloat v)
{
    char buf[64];
    rt_fmt_float(v, buf);
    out_ascii(buf);
}

void rt_print_double(jdouble v)
{
    char buf[64];
    rt_fmt_double(v, buf);
    out_ascii(buf);
}

void rt_print_string(rt_string *s)
{
    if (s == NULL)
        s = &null_string;
    rt_platform_out(s->chars, s->length);
}

void rt_print_object(rt_object *o, const rt_loc *loc)
{
    if (o == NULL) {
        out_ascii("null");
        return;
    }
    rt_print_string(rt_obj_toString(o, loc));
}

void rt_print_newline(void)
{
    /* line separator of the target platform, as Java's println on Windows */
    out_ascii("\r\n");
}
