/*
 * rt_io.c -- portable output: UTF-8 bytes on the standard C streams.
 *
 * stdout is a text stream, so '\n' becomes the platform line separator.
 * Program output is flushed before any diagnostic on stderr.
 */
#include <stdio.h>
#include <string.h>
#include "runtime.h"

void rt_out_bytes(const unsigned char *bytes, size_t n)
{
    if (n > 0)
        fwrite(bytes, 1, n, stdout);
}

void rt_out_flush(void)
{
    fflush(stdout);
}

/* raw UTF-8 bytes to stderr; unlike rt_err_text, embedded zero bytes are kept */
void rt_err_bytes(const unsigned char *bytes, size_t n)
{
    if (n > 0)
        fwrite(bytes, 1, n, stderr);
    fflush(stderr);
}

void rt_err_text(const char *utf8)
{
    fputs(utf8, stderr);
    fflush(stderr);
}
