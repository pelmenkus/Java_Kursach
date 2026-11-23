/*
 * rt_platform_win.c -- PLATFORM MODULE of input/output (decision D2).
 *
 * - Command line: GetCommandLineW + CommandLineToArgvW give UTF-16 arguments;
 *   argv[0] (the program name) is not passed to Java main.
 * - Output: buffered UTF-16 code units. A console receives them through
 *   WriteConsoleW; a file or pipe receives UTF-8. An unpaired surrogate is
 *   written as U+FFFD.
 */
#include <windows.h>
#include <shellapi.h>
#include <string.h>
#include "runtime.h"

#define OUT_CAP 4096

static LPWSTR *args = NULL;
static int args_count = 0;
static HANDLE out_handle;
static int out_is_console = 0;
static jchar out_buf[OUT_CAP];
static jint out_len = 0;

void rt_platform_init(void)
{
    DWORD mode;
    args = CommandLineToArgvW(GetCommandLineW(), &args_count);
    if (args == NULL)
        args_count = 0;
    out_handle = GetStdHandle(STD_OUTPUT_HANDLE);
    out_is_console = GetConsoleMode(out_handle, &mode) != 0;
}

int rt_platform_argc(void)
{
    return args_count > 0 ? args_count - 1 : 0;
}

const jchar *rt_platform_argv(int index, jint *length)
{
    LPWSTR s = args[index + 1];
    *length = (jint)lstrlenW(s);
    return (const jchar *)s;
}

static void write_bytes(HANDLE h, const char *data, DWORD n)
{
    DWORD written;
    while (n > 0) {
        if (!WriteFile(h, data, n, &written, NULL) || written == 0)
            return;
        data += written;
        n -= written;
    }
}

static int put_utf8(char *dst, unsigned long cp)
{
    if (cp < 0x80UL) {
        dst[0] = (char)cp;
        return 1;
    }
    if (cp < 0x800UL) {
        dst[0] = (char)(0xC0 | (cp >> 6));
        dst[1] = (char)(0x80 | (cp & 0x3F));
        return 2;
    }
    if (cp < 0x10000UL) {
        dst[0] = (char)(0xE0 | (cp >> 12));
        dst[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        dst[2] = (char)(0x80 | (cp & 0x3F));
        return 3;
    }
    dst[0] = (char)(0xF0 | (cp >> 18));
    dst[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
    dst[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
    dst[3] = (char)(0x80 | (cp & 0x3F));
    return 4;
}

/* writes units [0, n); returns how many were consumed (a trailing high
   surrogate is kept for the next call unless final) */
static jint emit(const jchar *u, jint n, int final)
{
    static char bytes[OUT_CAP * 3 + 8];
    jint i = 0, k = 0;
    if (n > 0 && !final && u[n - 1] >= 0xD800 && u[n - 1] <= 0xDBFF)
        n--;
    if (out_is_console) {
        DWORD written;
        WCHAR wide[OUT_CAP];
        for (i = 0; i < n; i++) {
            jchar c = u[i];
            int high = c >= 0xD800 && c <= 0xDBFF, low = c >= 0xDC00 && c <= 0xDFFF;
            if (high && i + 1 < n && u[i + 1] >= 0xDC00 && u[i + 1] <= 0xDFFF) {
                wide[k++] = (WCHAR)c;
                wide[k++] = (WCHAR)u[++i];
            } else {
                wide[k++] = (WCHAR)((high || low) ? 0xFFFD : c);
            }
        }
        if (k > 0)
            WriteConsoleW(out_handle, wide, (DWORD)k, &written, NULL);
        return n;
    }
    for (i = 0; i < n; i++) {
        unsigned long c = u[i];
        if (c >= 0xD800UL && c <= 0xDBFFUL && i + 1 < n && u[i + 1] >= 0xDC00 && u[i + 1] <= 0xDFFF) {
            c = 0x10000UL + ((c - 0xD800UL) << 10) + ((unsigned long)u[i + 1] - 0xDC00UL);
            i++;
        } else if (c >= 0xD800UL && c <= 0xDFFFUL) {
            c = 0xFFFDUL;
        }
        k += put_utf8(bytes + k, c);
    }
    write_bytes(out_handle, bytes, (DWORD)k);
    return n;
}

static void flush(int final)
{
    jint used = emit(out_buf, out_len, final);
    if (used < out_len)
        memmove(out_buf, out_buf + used, (size_t)(out_len - used) * sizeof(jchar));
    out_len -= used;
}

void rt_platform_out(const jchar *units, jint count)
{
    jint i;
    for (i = 0; i < count; i++) {
        if (out_len == OUT_CAP)
            flush(0);
        out_buf[out_len++] = units[i];
    }
}

void rt_platform_flush(void)
{
    flush(1);
}

void rt_platform_err(const char *text)
{
    HANDLE h = GetStdHandle(STD_ERROR_HANDLE);
    DWORD mode, written;
    int n = (int)strlen(text);
    if (GetConsoleMode(h, &mode)) {
        WCHAR wide[2048];
        int k = MultiByteToWideChar(CP_UTF8, 0, text, n, wide, 2048);
        if (k > 0) {
            WriteConsoleW(h, wide, (DWORD)k, &written, NULL);
            return;
        }
    }
    write_bytes(h, text, (DWORD)n);
}
