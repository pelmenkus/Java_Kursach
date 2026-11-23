/*
 * rt_num.c -- Java semantics for int/long/char arithmetic, floating point
 * helpers and number formatting.
 *
 * Signed arithmetic is done on uint32_t/uint64_t and converted back without
 * implementation-defined conversions (wrap-around as in Java). Right shifts
 * of negative values are built from unsigned shifts. Float/double use the
 * IEEE 754 hardware (checked in runtime.h); the cases where C99 has undefined
 * behaviour (division by zero, out of range conversions) are handled here.
 */
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "runtime.h"

/* ------------------------------------------------------------ int */
static jint u2i(uint32_t x)
{
    if (x <= (uint32_t)INT32_MAX)
        return (jint)x;
    return -(jint)(UINT32_MAX - x) - 1;
}

static jlong u2l(uint64_t x)
{
    if (x <= (uint64_t)INT64_MAX)
        return (jlong)x;
    return -(jlong)(UINT64_MAX - x) - 1;
}

jint rt_iadd(jint a, jint b) { return u2i((uint32_t)a + (uint32_t)b); }
jint rt_isub(jint a, jint b) { return u2i((uint32_t)a - (uint32_t)b); }
jint rt_imul(jint a, jint b) { return u2i((uint32_t)a * (uint32_t)b); }
jint rt_ineg(jint a) { return u2i(0u - (uint32_t)a); }
jint rt_inot(jint a) { return u2i(~(uint32_t)a); }
jint rt_iand(jint a, jint b) { return u2i((uint32_t)a & (uint32_t)b); }
jint rt_ior(jint a, jint b) { return u2i((uint32_t)a | (uint32_t)b); }
jint rt_ixor(jint a, jint b) { return u2i((uint32_t)a ^ (uint32_t)b); }
jint rt_ishl(jint a, jint n) { return u2i((uint32_t)a << (n & 31)); }
jint rt_iushr(jint a, jint n) { return u2i((uint32_t)a >> (n & 31)); }

jint rt_ishr(jint a, jint n)
{
    if (a >= 0)
        return a >> (n & 31);
    return u2i(~(~(uint32_t)a >> (n & 31)));
}

jint rt_idiv(jint a, jint b, const rt_loc *loc)
{
    if (b == 0)
        rt_error(RT_ERR_DIVZERO, "integer division by zero", loc);
    if (a == INT32_MIN && b == -1)
        return INT32_MIN;
    return a / b;          /* C99: truncation toward zero */
}

jint rt_irem(jint a, jint b, const rt_loc *loc)
{
    if (b == 0)
        rt_error(RT_ERR_DIVZERO, "integer remainder by zero", loc);
    if (b == -1)
        return 0;
    return a % b;
}

jbyte rt_i2b(jint v)
{
    uint32_t x = (uint32_t)v & 0xFFu;
    return (jbyte)(x < 128u ? (jint)x : (jint)x - 256);
}

jshort rt_i2s(jint v)
{
    uint32_t x = (uint32_t)v & 0xFFFFu;
    return (jshort)(x < 32768u ? (jint)x : (jint)x - 65536);
}

/* ------------------------------------------------------------ long */
jlong rt_ladd(jlong a, jlong b) { return u2l((uint64_t)a + (uint64_t)b); }
jlong rt_lsub(jlong a, jlong b) { return u2l((uint64_t)a - (uint64_t)b); }
jlong rt_lmul(jlong a, jlong b) { return u2l((uint64_t)a * (uint64_t)b); }
jlong rt_lneg(jlong a) { return u2l(0u - (uint64_t)a); }
jlong rt_lnot(jlong a) { return u2l(~(uint64_t)a); }
jlong rt_land(jlong a, jlong b) { return u2l((uint64_t)a & (uint64_t)b); }
jlong rt_lor(jlong a, jlong b) { return u2l((uint64_t)a | (uint64_t)b); }
jlong rt_lxor(jlong a, jlong b) { return u2l((uint64_t)a ^ (uint64_t)b); }
jlong rt_lshl(jlong a, jlong n) { return u2l((uint64_t)a << (n & 63)); }
jlong rt_lushr(jlong a, jlong n) { return u2l((uint64_t)a >> (n & 63)); }

jlong rt_lshr(jlong a, jlong n)
{
    if (a >= 0)
        return a >> (n & 63);
    return u2l(~(~(uint64_t)a >> (n & 63)));
}

jlong rt_ldiv(jlong a, jlong b, const rt_loc *loc)
{
    if (b == 0)
        rt_error(RT_ERR_DIVZERO, "long division by zero", loc);
    if (a == INT64_MIN && b == -1)
        return INT64_MIN;
    return a / b;
}

jlong rt_lrem(jlong a, jlong b, const rt_loc *loc)
{
    if (b == 0)
        rt_error(RT_ERR_DIVZERO, "long remainder by zero", loc);
    if (b == -1)
        return 0;
    return a % b;
}

jint rt_l2i(jlong v)
{
    return u2i((uint32_t)((uint64_t)v & 0xFFFFFFFFu));
}

/* ------------------------------------------------------------ char (N2) */
jchar rt_cchk(jlong v, const rt_loc *loc)
{
    char msg[96];
    if (v < 0 || v > 0x10FFFF || (v >= 0xD800 && v <= 0xDFFF)) {
        snprintf(msg, sizeof msg, "value %" PRId64 " is not a Unicode scalar value", (int64_t)v);
        rt_error(RT_ERR_CHAR, msg, loc);
    }
    return (jchar)v;
}

jchar rt_cdiv(jchar a, jchar b, const rt_loc *loc)
{
    if (b == 0)
        rt_error(RT_ERR_DIVZERO, "char division by zero", loc);
    return a / b;
}

jchar rt_crem(jchar a, jchar b, const rt_loc *loc)
{
    if (b == 0)
        rt_error(RT_ERR_DIVZERO, "char remainder by zero", loc);
    return a % b;
}

/* ------------------------------------------------------------ IEEE helpers */
jdouble rt_dbits(uint64_t bits)
{
    double d;
    memcpy(&d, &bits, sizeof d);
    return d;
}

jfloat rt_fbits(uint32_t bits)
{
    float f;
    memcpy(&f, &bits, sizeof f);
    return f;
}

int rt_signbit(jdouble v)
{
    uint64_t b;
    memcpy(&b, &v, sizeof b);
    return (b >> 63) != 0;
}

static int is_inf(jdouble v)
{
    return v > DBL_MAX || v < -DBL_MAX;
}

#define D_NAN  rt_dbits(UINT64_C(0x7FF8000000000000))
#define D_PINF rt_dbits(UINT64_C(0x7FF0000000000000))
#define D_NINF rt_dbits(UINT64_C(0xFFF0000000000000))

jdouble rt_ddiv(jdouble a, jdouble b)
{
    if (b == 0.0) {
        if (a != a || a == 0.0)
            return D_NAN;
        return rt_signbit(a) != rt_signbit(b) ? D_NINF : D_PINF;
    }
    return a / b;
}

jfloat rt_fdiv(jfloat a, jfloat b)
{
    if (b == 0.0f)
        return (jfloat)rt_ddiv((jdouble)a, (jdouble)b);
    return a / b;
}

jdouble rt_drem(jdouble a, jdouble b)
{
    if (a != a || b != b || is_inf(a) || b == 0.0)
        return D_NAN;
    if (is_inf(b) || a == 0.0)
        return a;
    return fmod(a, b);
}

jfloat rt_frem(jfloat a, jfloat b)
{
    /* fmod is exact, so the result is representable in float */
    return (jfloat)rt_drem((jdouble)a, (jdouble)b);
}

jint rt_d2i(jdouble v)
{
    if (v != v)
        return 0;
    if (v >= 2147483647.0)
        return INT32_MAX;
    if (v <= -2147483648.0)
        return INT32_MIN;
    return (jint)v;
}

jlong rt_d2l(jdouble v)
{
    if (v != v)
        return 0;
    if (v >= 9223372036854775807.0)
        return INT64_MAX;
    if (v <= -9223372036854775808.0)
        return INT64_MIN;
    return (jlong)v;
}

jfloat rt_d2f(jdouble v)
{
    /* 2^128 - 2^103: halfway between FLT_MAX and 2^128 rounds to infinity */
    const double limit = 340282356779733661637539395458142568448.0;
    if (v != v)
        return (jfloat)v;
    if (v >= limit)
        return (jfloat)D_PINF;
    if (v <= -limit)
        return (jfloat)D_NINF;
    return (jfloat)v;
}

/* ------------------------------------------------------------ formatting */
int rt_fmt_int(jint v, char *buf)
{
    return sprintf(buf, "%" PRId32, (int32_t)v);
}

int rt_fmt_long(jlong v, char *buf)
{
    return sprintf(buf, "%" PRId64, (int64_t)v);
}

/* split "%.*e" output into digits and decimal exponent */
static void parse_e(const char *s, char *digits, int *nd, int *exp10)
{
    int n = 0;
    while (*s != 'e') {
        if (*s >= '0' && *s <= '9')
            digits[n++] = *s;
        s++;
    }
    digits[n] = '\0';
    *nd = n;
    *exp10 = atoi(s + 1);
}

static void make_e(char *out, const char *digits, int nd, int exp10)
{
    int k = 0, i;
    out[k++] = digits[0];
    out[k++] = '.';
    for (i = 1; i < nd; i++)
        out[k++] = digits[i];
    if (nd == 1)
        out[k++] = '0';
    sprintf(out + k, "e%d", exp10);
}

/* digits +/- one unit in the last place; returns 0 if the digit count changes */
static int bump(char *digits, int nd, int *exp10, int up)
{
    int i = nd - 1;
    if (up) {
        while (i >= 0 && digits[i] == '9')
            digits[i--] = '0';
        if (i < 0) {
            digits[0] = '1';
            for (i = 1; i < nd; i++)
                digits[i] = '0';
            (*exp10)++;
            return 1;
        }
        digits[i]++;
        return 1;
    }
    while (i >= 0 && digits[i] == '0')
        digits[i--] = '9';
    if (i < 0)
        return 0;
    digits[i]--;
    return digits[0] != '0';
}

static int same_value(const char *text, double v, int as_float)
{
    double r = strtod(text, NULL);
    if (as_float)
        return (float)r == (float)v;
    return r == v;
}

/*
 * Shortest decimal (at least 2 digits) that reads back as v -- the selection
 * rule of Double.toString / Float.toString since JDK 19. The correctly rounded
 * p-digit candidate is tried first, then its neighbours (they matter only near
 * powers of two, where the rounding interval is asymmetric).
 */
static void shortest(double v, int maxp, int as_float, char *digits, int *nd, int *exp10)
{
    char buf[64], cand[32], text[64];
    int p, cexp;
    for (p = 2; p <= maxp; p++) {
        sprintf(buf, "%.*e", p - 1, v);
        parse_e(buf, digits, nd, exp10);
        if (same_value(buf, v, as_float))
            return;
        strcpy(cand, digits);
        cexp = *exp10;
        if (bump(cand, *nd, &cexp, 0)) {
            make_e(text, cand, *nd, cexp);
            if (same_value(text, v, as_float)) {
                strcpy(digits, cand);
                *exp10 = cexp;
                return;
            }
        }
        strcpy(cand, digits);
        cexp = *exp10;
        if (bump(cand, *nd, &cexp, 1)) {
            make_e(text, cand, *nd, cexp);
            if (same_value(text, v, as_float)) {
                strcpy(digits, cand);
                *exp10 = cexp;
                return;
            }
        }
    }
    sprintf(buf, "%.*e", maxp - 1, v);
    parse_e(buf, digits, nd, exp10);
}

static int render(int neg, char *digits, int nd, int e, char *out)
{
    int k = 0, i;
    while (nd > 1 && digits[nd - 1] == '0')
        nd--;
    if (neg)
        out[k++] = '-';
    if (e >= -3 && e < 7) {
        if (e >= 0) {
            for (i = 0; i <= e; i++)
                out[k++] = i < nd ? digits[i] : '0';
            out[k++] = '.';
            if (nd > e + 1)
                for (i = e + 1; i < nd; i++)
                    out[k++] = digits[i];
            else
                out[k++] = '0';
        } else {
            out[k++] = '0';
            out[k++] = '.';
            for (i = 0; i < -e - 1; i++)
                out[k++] = '0';
            for (i = 0; i < nd; i++)
                out[k++] = digits[i];
        }
        out[k] = '\0';
        return k;
    }
    out[k++] = digits[0];
    out[k++] = '.';
    if (nd > 1)
        for (i = 1; i < nd; i++)
            out[k++] = digits[i];
    else
        out[k++] = '0';
    k += sprintf(out + k, "E%d", e);
    return k;
}

static int fmt_special(double v, char *out)
{
    if (v != v) {
        strcpy(out, "NaN");
        return 3;
    }
    if (is_inf(v)) {
        strcpy(out, v > 0 ? "Infinity" : "-Infinity");
        return (int)strlen(out);
    }
    if (v == 0.0) {
        strcpy(out, rt_signbit(v) ? "-0.0" : "0.0");
        return (int)strlen(out);
    }
    return -1;
}

int rt_fmt_double(jdouble v, char *out)
{
    char digits[32];
    int nd, e, n = fmt_special(v, out);
    if (n >= 0)
        return n;
    shortest(v < 0 ? -v : v, 17, 0, digits, &nd, &e);
    return render(v < 0, digits, nd, e, out);
}

int rt_fmt_float(jfloat v, char *out)
{
    char digits[32];
    double d = (double)v;
    int nd, e, n = fmt_special(d, out);
    if (n >= 0)
        return n;
    shortest(d < 0 ? -d : d, 9, 1, digits, &nd, &e);
    return render(d < 0, digits, nd, e, out);
}
