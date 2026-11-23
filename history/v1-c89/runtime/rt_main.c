/*
 * rt_main.c -- C entry point: runtime initialisation, String[] args in UTF-16,
 * call of the generated Java main, output flush.
 */
#include "runtime.h"

int main(void)
{
    rt_array *args;
    int i, n;
    rt_platform_init();
    rt_platform_gc_init();
    rt_gc_init();
    rt_core_init();
    n = rt_platform_argc();
    args = rt_new_array(&rt_class_arr_String, n, NULL);
    for (i = 0; i < n; i++) {
        jint len;
        const jchar *units = rt_platform_argv(i, &len);
        rt_string *s = rt_str_new(units, len, NULL);
        ((rt_string **)RT_ADATA(args))[i] = s;
    }
    rt_user_main(args);
    rt_platform_flush();
    return 0;
}
