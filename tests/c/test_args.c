/*
 * Portable command line adapter rt_args.c (decision N3), linked without
 * rt_main.c. argv[1] selects the case; the arguments passed to
 * rt_args_init() are built here from exact byte sequences, independent of
 * how the operating system encodes the real command line.
 */
#include <string.h>
#include "test_support.h"

static char *bad[] = {
    "\xC0\xAF",              /* overlong '/' */
    "\xE0\x80\xAF",          /* overlong, 3 bytes */
    "\xED\xA0\x80",          /* surrogate U+D800 */
    "\xF4\x90\x80\x80",      /* above U+10FFFF */
    "ab\xE2\x82",            /* truncated */
    "\x80",                  /* stray continuation byte */
    "\xFF"
};

int main(int argc, char **argv)
{
    static const rt_frame_desc d = { 1, "main(test_args)" };
    void *f[RT_FRAME_WORDS(1)];
    rt_init();
    if (argc < 2)
        return 2;
    rt_frame_push(f, &d);
    if (strcmp(argv[1], "valid") == 0) {
        char *v[] = { "prog", "abc", "\xD0\x96\xD1\x83\xD0\xBA", "\xF0\x9F\x98\x80", "" };
        jint i, n;
        f[2] = rt_args_init(5, v);
        n = rt_arr_length(f[2], NULL);
        printf("%d", (int)n);
        for (i = 0; i < n; i++)
            printf(" %d", (int)rt_str_length(((void **)RT_ADATA(f[2]))[i], NULL));
        printf(" %lu\n", (unsigned long)rt_str_charAt(((void **)RT_ADATA(f[2]))[2], 0, NULL));
    } else {
        int k = atoi(argv[1]);
        char *v[3];
        v[0] = "prog";
        v[1] = "ok";
        v[2] = bad[k];
        f[2] = rt_args_init(3, v);
        printf("not detected\n");
    }
    rt_frame_pop(f);
    return 0;
}
