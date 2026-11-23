/*
 * rt_args.c -- portable command line adapter (decision N3).
 *
 * argv is expected in UTF-8 (POSIX environments). Every argument is checked
 * strictly: overlong forms, surrogates, values above U+10FFFF and truncated
 * sequences are a runtime error [InvalidArgument]. argv[0] (the program name)
 * is not passed to Java main. Other environments may link an optional
 * adapter instead of this file (runtime/adapters/), the rest of the runtime
 * does not depend on the operating system.
 */
#include <stdio.h>
#include <string.h>
#include "runtime.h"

/* length of a valid UTF-8 sequence at p (n bytes available), 0 if invalid */
static size_t valid_seq(const unsigned char *p, size_t n)
{
    unsigned char b = p[0];
    size_t len, i;
    uint32_t cp;
    if (b < 0x80u)
        return 1;
    if (b >= 0xC2u && b <= 0xDFu) {
        len = 2;
        cp = b & 0x1Fu;
    } else if (b >= 0xE0u && b <= 0xEFu) {
        len = 3;
        cp = b & 0x0Fu;
    } else if (b >= 0xF0u && b <= 0xF4u) {
        len = 4;
        cp = b & 0x07u;
    } else {
        return 0;
    }
    if (len > n)
        return 0;
    for (i = 1; i < len; i++) {
        if ((p[i] & 0xC0u) != 0x80u)
            return 0;
        cp = (cp << 6) | (p[i] & 0x3Fu);
    }
    if ((len == 3 && cp < 0x800u) || (len == 4 && (cp < 0x10000u || cp > 0x10FFFFu)) ||
        (cp >= 0xD800u && cp <= 0xDFFFu))
        return 0;
    return len;
}

void *rt_args_init(int argc, char **argv)
{
    static const rt_frame_desc desc = { 1, "rt_args_init" };
    void *frame[RT_FRAME_WORDS(1)];
    void *args;
    int i, n = argc > 0 ? argc - 1 : 0;
    rt_frame_push(frame, &desc);
    frame[2] = rt_new_array(&rt_class_arr_String, n, NULL);
    for (i = 0; i < n; i++) {
        const unsigned char *s = (const unsigned char *)argv[i + 1];
        size_t len = strlen(argv[i + 1]), k = 0;
        void *str;
        while (k < len) {
            size_t step = valid_seq(s + k, len - k);
            if (step == 0) {
                char msg[128];
                snprintf(msg, sizeof msg, "command line argument %d is not valid UTF-8 (byte offset %lu)",
                         i, (unsigned long)k);
                rt_error(RT_ERR_ARG, msg, NULL);
            }
            k += step;
        }
        str = rt_str_utf8(s, len, NULL);        /* the array is rooted in frame[2] */
        ((void **)RT_ADATA(frame[2]))[i] = str;
    }
    args = frame[2];
    rt_frame_pop(frame);
    return args;
}
