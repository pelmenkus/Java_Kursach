/*
 * rt_math.c -- java.lang.Math subset. Special cases (NaN, infinities, signed
 * zero) follow the Java specification explicitly, because C89 leaves domain
 * errors implementation-defined. sqrt is correctly rounded (SSE2); sin, cos
 * and pow come from the C library (UCRT) and may differ from StrictMath in the
 * last bit.
 */
#include <float.h>
#include <math.h>
#include "runtime.h"

static int is_inf(double v)
{
    return v > DBL_MAX || v < -DBL_MAX;
}

jint rt_math_abs_I(jint a) { return a < 0 ? rt_ineg(a) : a; }
jlong rt_math_abs_J(jlong a) { return rt_lcmp(a, rt_lmake(0UL, 0UL)) < 0 ? rt_lneg(a) : a; }
jfloat rt_math_abs_F(jfloat a) { return rt_signbit((jdouble)a) ? -a : a; }
jdouble rt_math_abs_D(jdouble a) { return rt_signbit(a) ? -a : a; }
jint rt_math_min_I(jint a, jint b) { return a <= b ? a : b; }
jint rt_math_max_I(jint a, jint b) { return a >= b ? a : b; }
jlong rt_math_min_J(jlong a, jlong b) { return rt_lcmp(a, b) <= 0 ? a : b; }
jlong rt_math_max_J(jlong a, jlong b) { return rt_lcmp(a, b) >= 0 ? a : b; }

jdouble rt_math_min_D(jdouble a, jdouble b)
{
    if (a != a)
        return a;
    if (b != b)
        return b;
    if (a == 0.0 && b == 0.0)
        return rt_signbit(a) ? a : b;
    return a <= b ? a : b;
}

jdouble rt_math_max_D(jdouble a, jdouble b)
{
    if (a != a)
        return a;
    if (b != b)
        return b;
    if (a == 0.0 && b == 0.0)
        return rt_signbit(a) ? b : a;
    return a >= b ? a : b;
}

jfloat rt_math_min_F(jfloat a, jfloat b) { return (jfloat)rt_math_min_D((jdouble)a, (jdouble)b); }
jfloat rt_math_max_F(jfloat a, jfloat b) { return (jfloat)rt_math_max_D((jdouble)a, (jdouble)b); }

jdouble rt_math_sqrt(jdouble a)
{
    if (a != a || a < 0.0)
        return rt_nan();
    if (a == 0.0 || is_inf(a))
        return a;
    return sqrt(a);
}

static int is_odd_integer(double b)
{
    double h;
    if (is_inf(b) || b != floor(b))
        return 0;
    h = b / 2.0;
    return h != floor(h);
}

jdouble rt_math_pow(jdouble a, jdouble b)
{
    if (b != b)
        return rt_nan();
    if (b == 0.0)
        return 1.0;
    if (a != a)
        return rt_nan();
    if (is_inf(b) && (a == 1.0 || a == -1.0))
        return rt_nan();
    if (a == 0.0) {
        int odd = is_odd_integer(b);
        if (b > 0.0)
            return (rt_signbit(a) && odd) ? -0.0 : 0.0;
        return rt_inf((rt_signbit(a) && odd) ? -1 : 1);
    }
    if (a < 0.0 && !is_inf(a) && !is_inf(b) && b != floor(b))
        return rt_nan();
    return pow(a, b);
}

jdouble rt_math_sin(jdouble a)
{
    if (a != a || is_inf(a))
        return rt_nan();
    return sin(a);
}

jdouble rt_math_cos(jdouble a)
{
    if (a != a || is_inf(a))
        return rt_nan();
    return cos(a);
}
