/*
 * rt_num.c -- Java semantics for int arithmetic, floating point helpers and
 * number formatting.
 *
 * Signed int arithmetic is performed on unsigned values and converted back
 * without implementation-defined behaviour (wrap-around as in Java). Division
 * truncates toward zero explicitly (C89 leaves it implementation-defined for
 * negative operands). Float/double use the IEEE 754 hardware of the target
 * (decision D3); the cases where C89 has undefined behaviour (division by
 * zero, out of range conversions) are handled here.
 */
#include <float.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "runtime.h"

#define M32 0xFFFFFFFFUL

static jint u2i(unsigned long x)
{
    x &= M32;
    if (x <= 0x7FFFFFFFUL)
        return (jint)x;
    return -(jint)(M32 - x) - 1;
}

static unsigned long i2u(jint v)
{
    return (unsigned long)v & M32;
}

jint rt_iadd(jint a, jint b) { return u2i(i2u(a) + i2u(b)); }
jint rt_isub(jint a, jint b) { return u2i(i2u(a) - i2u(b)); }
jint rt_imul(jint a, jint b) { return u2i(i2u(a) * i2u(b)); }
jint rt_ineg(jint a) { return u2i(0UL - i2u(a)); }
jint rt_inot(jint a) { return u2i(~i2u(a)); }
jint rt_iand(jint a, jint b) { return u2i(i2u(a) & i2u(b)); }
jint rt_ior(jint a, jint b) { return u2i(i2u(a) | i2u(b)); }
jint rt_ixor(jint a, jint b) { return u2i(i2u(a) ^ i2u(b)); }
jint rt_ishl(jint a, jint n) { return u2i(i2u(a) << (n & 31)); }
jint rt_iushr(jint a, jint n) { return u2i(i2u(a) >> (n & 31)); }

jint rt_ishr(jint a, jint n)
{
    if (a >= 0)
        return u2i(i2u(a) >> (n & 31));
    return u2i(~((~i2u(a) & M32) >> (n & 31)));
}

static unsigned long magnitude(jint v)
{
    return v < 0 ? (0UL - i2u(v)) & M32 : (unsigned long)v;
}

jint rt_idiv(jint a, jint b, const rt_loc *loc)
{
    unsigned long q;
    if (b == 0)
        rt_error(RT_ERR_DIVZERO, "integer division by zero", loc);
    q = magnitude(a) / magnitude(b);
    return ((a < 0) != (b < 0)) ? u2i(0UL - q) : u2i(q);
}

jint rt_irem(jint a, jint b, const rt_loc *loc)
{
    unsigned long r;
    if (b == 0)
        rt_error(RT_ERR_DIVZERO, "integer remainder by zero", loc);
    r = magnitude(a) % magnitude(b);
    return a < 0 ? u2i(0UL - r) : u2i(r);
}

jbyte rt_i2b(jint v)
{
    unsigned long x = i2u(v) & 0xFFUL;
    return (jbyte)(x < 128UL ? (jint)x : (jint)x - 256);
}

jshort rt_i2s(jint v)
{
    unsigned long x = i2u(v) & 0xFFFFUL;
    return (jshort)(x < 32768UL ? (jint)x : (jint)x - 65536);
}

jchar rt_i2c(jint v)
{
    return (jchar)(i2u(v) & 0xFFFFUL);
}

/* ------------------------------------------------------------ IEEE helpers */
jdouble rt_nan(void)
{
    static const unsigned char bits[8] = { 0, 0, 0, 0, 0, 0, 0xF8, 0x7F };
    double d;
    memcpy(&d, bits, sizeof d);
    return d;
}

jdouble rt_inf(int sign)
{
    static const unsigned char pos[8] = { 0, 0, 0, 0, 0, 0, 0xF0, 0x7F };
    static const unsigned char neg[8] = { 0, 0, 0, 0, 0, 0, 0xF0, 0xFF };
    double d;
    memcpy(&d, sign < 0 ? neg : pos, sizeof d);
    return d;
}

int rt_isnan(jdouble v)
{
    return v != v;
}

int rt_signbit(jdouble v)
{
    unsigned char b[8];
    memcpy(b, &v, sizeof b);
    return (b[7] & 0x80) != 0;
}

static int is_inf(jdouble v)
{
    return v > DBL_MAX || v < -DBL_MAX;
}

jdouble rt_ddiv(jdouble a, jdouble b)
{
    if (b == 0.0) {
        if (a != a || a == 0.0)
            return rt_nan();
        return rt_inf(rt_signbit(a) != rt_signbit(b) ? -1 : 1);
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
        return rt_nan();
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
        return 0x7FFFFFFF;
    if (v <= -2147483648.0)
        return -0x7FFFFFFF - 1;
    return (jint)v;
}

jfloat rt_d2f(jdouble v)
{
    /* 2^128 - 2^103: halfway between FLT_MAX and 2^128 rounds to infinity */
    const double limit = 340282356779733661637539395458142568448.0;
    if (v != v)
        return (jfloat)v;
    if (v >= limit)
        return (jfloat)rt_inf(1);
    if (v <= -limit)
        return (jfloat)rt_inf(-1);
    return (jfloat)v;
}

/* ------------------------------------------------------------ formatting */
int rt_fmt_int(jint v, char *buf)
{
    char tmp[16];
    int n = 0, len = 0;
    unsigned long m = magnitude(v);
    do {
        tmp[n++] = (char)('0' + (int)(m % 10UL));
        m /= 10UL;
    } while (m != 0);
    if (v < 0)
        buf[len++] = '-';
    while (n > 0)
        buf[len++] = tmp[--n];
    buf[len] = '\0';
    return len;
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
