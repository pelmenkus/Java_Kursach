/*
 * rt_long.c -- 64-bit two's complement integers emulated with two 32-bit
 * halves (decision D1). Every operation masks to 32 bits, so the code is also
 * correct where unsigned long is wider than 32 bits.
 */
#include <math.h>
#include "runtime.h"

#define M32 0xFFFFFFFFUL
#define SIGN32 0x80000000UL

jlong rt_lmake(unsigned long hi, unsigned long lo)
{
    jlong r;
    r.hi = hi & M32;
    r.lo = lo & M32;
    return r;
}

static int is_neg(jlong a)
{
    return (a.hi & SIGN32) != 0;
}

static int is_zero(jlong a)
{
    return a.hi == 0 && a.lo == 0;
}

jlong rt_ladd(jlong a, jlong b)
{
    jlong r;
    r.lo = (a.lo + b.lo) & M32;
    r.hi = (a.hi + b.hi + (r.lo < a.lo ? 1UL : 0UL)) & M32;
    return r;
}

jlong rt_lsub(jlong a, jlong b)
{
    jlong r;
    r.lo = (a.lo - b.lo) & M32;
    r.hi = (a.hi - b.hi - (a.lo < b.lo ? 1UL : 0UL)) & M32;
    return r;
}

jlong rt_lneg(jlong a)
{
    return rt_lsub(rt_lmake(0UL, 0UL), a);
}

jlong rt_lnot(jlong a)
{
    return rt_lmake(~a.hi, ~a.lo);
}

jlong rt_land(jlong a, jlong b)
{
    return rt_lmake(a.hi & b.hi, a.lo & b.lo);
}

jlong rt_lor(jlong a, jlong b)
{
    return rt_lmake(a.hi | b.hi, a.lo | b.lo);
}

jlong rt_lxor(jlong a, jlong b)
{
    return rt_lmake(a.hi ^ b.hi, a.lo ^ b.lo);
}

/* full 32x32 -> 64 product through 16-bit limbs */
static jlong mul32(unsigned long x, unsigned long y)
{
    unsigned long xl = x & 0xFFFFUL, xh = (x >> 16) & 0xFFFFUL;
    unsigned long yl = y & 0xFFFFUL, yh = (y >> 16) & 0xFFFFUL;
    unsigned long ll = xl * yl, lh = xl * yh, hl = xh * yl, hh = xh * yh;
    unsigned long mid = (ll >> 16) + (lh & 0xFFFFUL) + (hl & 0xFFFFUL);
    jlong r;
    r.lo = ((mid & 0xFFFFUL) << 16 | (ll & 0xFFFFUL)) & M32;
    r.hi = (hh + (lh >> 16) + (hl >> 16) + (mid >> 16)) & M32;
    return r;
}

jlong rt_lmul(jlong a, jlong b)
{
    jlong r = mul32(a.lo, b.lo);
    r.hi = (r.hi + ((a.lo * b.hi) & M32) + ((a.hi * b.lo) & M32)) & M32;
    return r;
}

static int ucmp(jlong a, jlong b)
{
    if (a.hi != b.hi)
        return a.hi < b.hi ? -1 : 1;
    if (a.lo != b.lo)
        return a.lo < b.lo ? -1 : 1;
    return 0;
}

int rt_lcmp(jlong a, jlong b)
{
    unsigned long ah = a.hi ^ SIGN32, bh = b.hi ^ SIGN32;
    if (ah != bh)
        return ah < bh ? -1 : 1;
    if (a.lo != b.lo)
        return a.lo < b.lo ? -1 : 1;
    return 0;
}

static jlong ushl(jlong a, int n)
{
    if (n == 0)
        return a;
    if (n < 32)
        return rt_lmake((a.hi << n) | (a.lo >> (32 - n)), a.lo << n);
    return rt_lmake(a.lo << (n - 32), 0UL);
}

static jlong ushr(jlong a, int n)
{
    if (n == 0)
        return a;
    if (n < 32)
        return rt_lmake(a.hi >> n, (a.lo >> n) | (a.hi << (32 - n)));
    return rt_lmake(0UL, a.hi >> (n - 32));
}

jlong rt_lshl(jlong a, jlong n)
{
    return ushl(a, (int)(n.lo & 63UL));
}

jlong rt_lushr(jlong a, jlong n)
{
    return ushr(a, (int)(n.lo & 63UL));
}

jlong rt_lshr(jlong a, jlong n)
{
    int s = (int)(n.lo & 63UL);
    if (!is_neg(a))
        return ushr(a, s);
    return rt_lnot(ushr(rt_lnot(a), s));
}

/* unsigned 64/64 division by shift and subtract */
static void udivmod(jlong n, jlong d, jlong *q, jlong *r)
{
    jlong quo = rt_lmake(0UL, 0UL), rem = rt_lmake(0UL, 0UL);
    int i;
    for (i = 63; i >= 0; i--) {
        unsigned long bit = (i >= 32) ? (n.hi >> (i - 32)) & 1UL : (n.lo >> i) & 1UL;
        rem = ushl(rem, 1);
        rem.lo |= bit;
        if (ucmp(rem, d) >= 0) {
            rem = rt_lsub(rem, d);
            if (i >= 32)
                quo.hi |= 1UL << (i - 32);
            else
                quo.lo |= 1UL << i;
        }
    }
    *q = quo;
    *r = rem;
}

static jlong labs_u(jlong a)
{
    return is_neg(a) ? rt_lneg(a) : a;
}

jlong rt_ldiv(jlong a, jlong b, const rt_loc *loc)
{
    jlong q, r;
    if (is_zero(b))
        rt_error(RT_ERR_DIVZERO, "long division by zero", loc);
    udivmod(labs_u(a), labs_u(b), &q, &r);
    return (is_neg(a) != is_neg(b)) ? rt_lneg(q) : q;
}

jlong rt_lrem(jlong a, jlong b, const rt_loc *loc)
{
    jlong q, r;
    if (is_zero(b))
        rt_error(RT_ERR_DIVZERO, "long remainder by zero", loc);
    udivmod(labs_u(a), labs_u(b), &q, &r);
    return is_neg(a) ? rt_lneg(r) : r;
}

jlong rt_i2l(jint v)
{
    return rt_lmake(v < 0 ? M32 : 0UL, (unsigned long)v & M32);
}

jint rt_l2i(jlong v)
{
    unsigned long x = v.lo & M32;
    if (x <= 0x7FFFFFFFUL)
        return (jint)x;
    return -(jint)(M32 - x) - 1;
}

/* magnitude as double: hi*2^32 is exact, so the sum is rounded once */
static double u2d(jlong m)
{
    return (double)m.hi * 4294967296.0 + (double)m.lo;
}

jdouble rt_l2d(jlong v)
{
    if (is_neg(v))
        return -u2d(rt_lneg(v));   /* Long.MIN_VALUE: magnitude 2^63 is exact */
    return u2d(v);
}

jfloat rt_l2f(jlong v)
{
    /* keep 53 significant bits plus a sticky bit, so that the final rounding
       to float (24 bits) happens once and is correct */
    int neg = is_neg(v), bits = 0, k;
    jlong m = neg ? rt_lneg(v) : v, t;
    double d;
    t = m;
    while (!is_zero(t)) {
        t = ushr(t, 1);
        bits++;
    }
    k = bits - 53;
    if (k > 0) {
        jlong kept = ushr(m, k), back = ushl(kept, k);
        if (ucmp(back, m) != 0)
            kept.lo |= 1UL;
        d = ldexp(u2d(kept), k);
    } else {
        d = u2d(m);
    }
    return (jfloat)(neg ? -d : d);
}

jlong rt_d2l(jdouble v)
{
    double t, hi;
    jlong r;
    if (v != v)
        return rt_lmake(0UL, 0UL);
    if (v >= 9223372036854775808.0)
        return rt_lmake(0x7FFFFFFFUL, M32);
    if (v <= -9223372036854775808.0)
        return rt_lmake(SIGN32, 0UL);
    t = v < 0 ? -v : v;
    t = floor(t);
    hi = floor(t / 4294967296.0);
    r = rt_lmake((unsigned long)hi, (unsigned long)(t - hi * 4294967296.0));
    return v < 0 ? rt_lneg(r) : r;
}

int rt_fmt_long(jlong v, char *buf)
{
    char tmp[24];
    int n = 0, len = 0, neg = is_neg(v);
    jlong m = neg ? rt_lneg(v) : v;    /* MIN_VALUE: 2^63 as unsigned */
    do {
        /* divide the unsigned magnitude by 10 using 16-bit steps */
        unsigned long r = m.hi % 10UL, t;
        unsigned long q1, q2;
        m.hi /= 10UL;
        t = (r << 16) | (m.lo >> 16);
        q1 = t / 10UL;
        r = t % 10UL;
        t = (r << 16) | (m.lo & 0xFFFFUL);
        q2 = t / 10UL;
        r = t % 10UL;
        m.lo = ((q1 << 16) | q2) & M32;
        tmp[n++] = (char)('0' + (int)r);
    } while (!is_zero(m));
    if (neg)
        buf[len++] = '-';
    while (n > 0)
        buf[len++] = tmp[--n];
    buf[len] = '\0';
    return len;
}
