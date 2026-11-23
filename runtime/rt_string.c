/*
 * rt_string.c -- immutable UTF-8 strings and printing of values.
 *
 * length(), charAt() and substring() index Unicode code points (section 11):
 * strings whose code points are all ASCII are indexed in O(1), others are
 * scanned from the start. Every string is valid UTF-8 by construction
 * (literals are checked by the compiler, char values are scalar values,
 * command line arguments are validated in rt_args.c).
 */
#include <stdio.h>
#include <string.h>
#include "runtime.h"

static const unsigned char null_bytes[] = "null";
static rt_string null_string = { RT_STATIC_HDR(&rt_class_String), 4, 4, true, null_bytes };

int rt_utf8_encode(jchar c, unsigned char *out)
{
    if (c < 0x80u) {
        out[0] = (unsigned char)c;
        return 1;
    }
    if (c < 0x800u) {
        out[0] = (unsigned char)(0xC0u | (c >> 6));
        out[1] = (unsigned char)(0x80u | (c & 0x3Fu));
        return 2;
    }
    if (c < 0x10000u) {
        out[0] = (unsigned char)(0xE0u | (c >> 12));
        out[1] = (unsigned char)(0x80u | ((c >> 6) & 0x3Fu));
        out[2] = (unsigned char)(0x80u | (c & 0x3Fu));
        return 3;
    }
    out[0] = (unsigned char)(0xF0u | (c >> 18));
    out[1] = (unsigned char)(0x80u | ((c >> 12) & 0x3Fu));
    out[2] = (unsigned char)(0x80u | ((c >> 6) & 0x3Fu));
    out[3] = (unsigned char)(0x80u | (c & 0x3Fu));
    return 4;
}

/* length of the UTF-8 sequence starting with byte b (input is valid) */
static int seq_len(unsigned char b)
{
    if (b < 0x80u)
        return 1;
    if (b < 0xE0u)
        return 2;
    if (b < 0xF0u)
        return 3;
    return 4;
}

static jchar decode_at(const unsigned char *p)
{
    switch (seq_len(p[0])) {
    case 1:
        return p[0];
    case 2:
        return ((jchar)(p[0] & 0x1Fu) << 6) | (p[1] & 0x3Fu);
    case 3:
        return ((jchar)(p[0] & 0x0Fu) << 12) | ((jchar)(p[1] & 0x3Fu) << 6) | (p[2] & 0x3Fu);
    default:
        return ((jchar)(p[0] & 0x07u) << 18) | ((jchar)(p[1] & 0x3Fu) << 12) |
               ((jchar)(p[2] & 0x3Fu) << 6) | (p[3] & 0x3Fu);
    }
}

/* byte offset of code point index i */
static jint byte_offset(const rt_string *s, jint i)
{
    jint off = 0, k;
    if (s->ascii)
        return i;
    for (k = 0; k < i; k++)
        off += seq_len(s->bytes[off]);
    return off;
}

void *rt_str_utf8(const unsigned char *bytes, size_t n, const rt_loc *loc)
{
    rt_string *s;
    unsigned char *data;
    size_t i;
    jint count = 0;
    jboolean ascii = true;
    if (n > (size_t)INT32_MAX)
        rt_error(RT_ERR_OOM, "string too long", loc);
    s = rt_alloc_sized(&rt_class_String, sizeof(rt_string) + n + 1, loc);
    data = (unsigned char *)(s + 1);
    memcpy(data, bytes, n);
    data[n] = '\0';
    for (i = 0; i < n; i += (size_t)seq_len(bytes[i])) {
        count++;
        if (bytes[i] >= 0x80u)
            ascii = false;
    }
    s->length = count;
    s->nbytes = (jint)n;
    s->ascii = ascii;
    s->bytes = data;
    return s;
}

void *rt_str_ascii(const char *text, const rt_loc *loc)
{
    return rt_str_utf8((const unsigned char *)text, strlen(text), loc);
}

jint rt_str_length(void *s, const rt_loc *loc)
{
    rt_nullchk(s, loc);
    return ((rt_string *)s)->length;
}

jchar rt_str_charAt(void *str, jint index, const rt_loc *loc)
{
    rt_string *s = str;
    char msg[96];
    rt_nullchk(s, loc);
    if (index < 0 || index >= s->length) {
        snprintf(msg, sizeof msg, "string index %ld out of bounds for length %ld", (long)index, (long)s->length);
        rt_error(RT_ERR_INDEX, msg, loc);
    }
    return decode_at(s->bytes + byte_offset(s, index));
}

void *rt_str_substring(void *str, jint begin, jint end, const rt_loc *loc)
{
    rt_string *s = str;
    char msg[128];
    jint b, e;
    rt_nullchk(s, loc);
    if (begin < 0 || end > s->length || begin > end) {
        snprintf(msg, sizeof msg, "begin %ld, end %ld, length %ld", (long)begin, (long)end, (long)s->length);
        rt_error(RT_ERR_INDEX, msg, loc);
    }
    b = byte_offset(s, begin);
    e = byte_offset(s, end);
    /* s stays reachable through the caller's root slot during the allocation */
    return rt_str_utf8(s->bytes + b, (size_t)(e - b), loc);
}

jboolean rt_str_equals(void *a, void *b, const rt_loc *loc)
{
    const rt_string *s = a, *t = b;
    rt_nullchk(s, loc);
    if (t == NULL)
        return false;
    if (s == t)
        return true;
    return s->nbytes == t->nbytes && memcmp(s->bytes, t->bytes, (size_t)s->nbytes) == 0;
}

void *rt_str_concat(void *a, void *b, const rt_loc *loc)
{
    const rt_string *x = a != NULL ? a : &null_string;
    const rt_string *y = b != NULL ? b : &null_string;
    rt_string *s;
    unsigned char *data;
    size_t n;
    if (x->nbytes > INT32_MAX - y->nbytes)
        rt_error(RT_ERR_OOM, "string too long", loc);
    n = (size_t)x->nbytes + (size_t)y->nbytes;
    /* a and b are kept alive by the caller's root slots */
    s = rt_alloc_sized(&rt_class_String, sizeof(rt_string) + n + 1, loc);
    data = (unsigned char *)(s + 1);
    memcpy(data, x->bytes, (size_t)x->nbytes);
    memcpy(data + x->nbytes, y->bytes, (size_t)y->nbytes);
    data[n] = '\0';
    s->length = x->length + y->length;
    s->nbytes = (jint)n;
    s->ascii = x->ascii && y->ascii;
    s->bytes = data;
    return s;
}

void *rt_str_from_bool(jboolean v, const rt_loc *loc)
{
    return rt_str_ascii(v ? "true" : "false", loc);
}

void *rt_str_from_char(jchar v, const rt_loc *loc)
{
    unsigned char buf[4];
    int n = rt_utf8_encode(v, buf);
    return rt_str_utf8(buf, (size_t)n, loc);
}

void *rt_str_from_int(jint v, const rt_loc *loc)
{
    char buf[32];
    rt_fmt_int(v, buf);
    return rt_str_ascii(buf, loc);
}

void *rt_str_from_long(jlong v, const rt_loc *loc)
{
    char buf[32];
    rt_fmt_long(v, buf);
    return rt_str_ascii(buf, loc);
}

void *rt_str_from_float(jfloat v, const rt_loc *loc)
{
    char buf[64];
    rt_fmt_float(v, buf);
    return rt_str_ascii(buf, loc);
}

void *rt_str_from_double(jdouble v, const rt_loc *loc)
{
    char buf[64];
    rt_fmt_double(v, buf);
    return rt_str_ascii(buf, loc);
}

/* ------------------------------------------------------------ printing */
static void out_text(const char *s)
{
    rt_out_bytes((const unsigned char *)s, strlen(s));
}

void rt_print_bool(jboolean v)
{
    out_text(v ? "true" : "false");
}

void rt_print_char(jchar v)
{
    unsigned char buf[4];
    rt_out_bytes(buf, (size_t)rt_utf8_encode(v, buf));
}

void rt_print_int(jint v)
{
    char buf[32];
    rt_fmt_int(v, buf);
    out_text(buf);
}

void rt_print_long(jlong v)
{
    char buf[32];
    rt_fmt_long(v, buf);
    out_text(buf);
}

void rt_print_float(jfloat v)
{
    char buf[64];
    rt_fmt_float(v, buf);
    out_text(buf);
}

void rt_print_double(jdouble v)
{
    char buf[64];
    rt_fmt_double(v, buf);
    out_text(buf);
}

void rt_print_string(void *str)
{
    const rt_string *s = str != NULL ? str : &null_string;
    rt_out_bytes(s->bytes, (size_t)s->nbytes);
}

void rt_print_object(void *o, const rt_loc *loc)
{
    if (o == NULL) {
        out_text("null");
        return;
    }
    rt_print_string(rt_obj_toString(o, loc));
}

void rt_print_newline(void)
{
    /* '\n' on a text stream: the C library writes the platform line
       separator ("\r\n" on Windows), as Java's println does */
    out_text("\n");
}
