/*
 * GC probe for generated programs (ISO C99): replaces rt_main.c, installs
 * rt_gc_hook and records the number of live objects after every collection
 * that happens while Java main runs. Since a full collection precedes every
 * allocation and the roots are exact (dead slots are cleared before each
 * safepoint), the sequence is deterministic and identical at -O0 and -O2.
 * Output: the program's own stdout, then one line "gc: n1 n2 ...".
 */
#include <stdio.h>
#include "runtime.h"

#define MAX_SAMPLES 4096
static size_t samples[MAX_SAMPLES];
static size_t nsamples = 0;

static void probe(void)
{
    if (nsamples < MAX_SAMPLES)
        samples[nsamples++] = rt_gc_live_count();
}

int main(int argc, char **argv)
{
    void *args;
    size_t i;
    rt_init();
    args = rt_args_init(argc, argv);
    rt_gc_hook = probe;
    rt_user_main(args);
    rt_gc_hook = NULL;
    rt_out_flush();
    printf("gc:");
    for (i = 0; i < nsamples; i++)
        printf(" %lu", (unsigned long)samples[i]);
    printf("\n");
    return 0;
}
