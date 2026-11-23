/*
 * rt_args_win.c -- OPTIONAL Windows adapter for the command line (decision N3).
 *
 * Not part of the default build. `python -m compiler.build --win-args` links
 * this file instead of runtime/rt_args.c:
 *   - arguments are taken in UTF-16 from GetCommandLineW/CommandLineToArgvW
 *     and converted to UTF-8 (unpaired surrogates -> [InvalidArgument]);
 *   - the console output code page is switched to UTF-8, so the UTF-8 that
 *     the runtime writes is displayed correctly in cmd/PowerShell; the code
 *     page belongs to the console, not to the process, so the previous one
 *     is restored at exit (normal return, runtime error, uncaught throw).
 * Requires linking with -lshell32.
 */
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <stdlib.h>
#include "runtime.h"

static UINT saved_output_cp;

static void restore_output_cp(void)
{
    fflush(stdout);
    fflush(stderr);
    SetConsoleOutputCP(saved_output_cp);
}

static void use_utf8_console(void)
{
    UINT cp = GetConsoleOutputCP();          /* 0: no console attached */
    if (cp == 0 || cp == CP_UTF8)
        return;
    if (SetConsoleOutputCP(CP_UTF8)) {
        saved_output_cp = cp;
        if (atexit(restore_output_cp) != 0)
            SetConsoleOutputCP(cp);          /* cannot restore later: keep the console as it was */
    }
}

void *rt_args_init(int argc, char **argv)
{
    static const rt_frame_desc desc = { 1, "rt_args_init" };
    void *frame[RT_FRAME_WORDS(1)];
    void *args;
    int wargc = 0, i, n;
    LPWSTR *wargv;
    (void)argc;
    (void)argv;
    use_utf8_console();
    wargv = CommandLineToArgvW(GetCommandLineW(), &wargc);
    n = (wargv != NULL && wargc > 0) ? wargc - 1 : 0;
    rt_frame_push(frame, &desc);
    frame[2] = rt_new_array(&rt_class_arr_String, n, NULL);
    for (i = 0; i < n; i++) {
        const WCHAR *w = wargv[i + 1];
        size_t len = (size_t)lstrlenW(w), k, out = 0;
        unsigned char *buf = malloc(len * 3 + 1);
        if (buf == NULL)
            rt_error(RT_ERR_OOM, "out of memory (command line)", NULL);
        for (k = 0; k < len; k++) {
            jchar c = w[k];
            if (c >= 0xD800u && c <= 0xDBFFu && k + 1 < len && w[k + 1] >= 0xDC00u && w[k + 1] <= 0xDFFFu) {
                c = 0x10000u + ((c - 0xD800u) << 10) + ((jchar)w[k + 1] - 0xDC00u);
                k++;
            } else if (c >= 0xD800u && c <= 0xDFFFu) {
                char msg[96];
                snprintf(msg, sizeof msg, "command line argument %d contains an unpaired surrogate", i);
                rt_error(RT_ERR_ARG, msg, NULL);
            }
            out += (size_t)rt_utf8_encode(c, buf + out);
        }
        ((void **)RT_ADATA(frame[2]))[i] = rt_str_utf8(buf, out, NULL);
        free(buf);
    }
    if (wargv != NULL)
        LocalFree(wargv);
    args = frame[2];
    rt_frame_pop(frame);
    return args;
}
