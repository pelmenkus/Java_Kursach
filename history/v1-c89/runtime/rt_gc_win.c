/*
 * rt_gc_win.c -- PLATFORM MODULE of the garbage collector:
 * Windows x86-64, GCC MinGW-w64, Microsoft x64 calling convention.
 *
 * Root scanning:
 *   1. setjmp() stores the callee-saved registers (rbx, rbp, rsi, rdi,
 *      r12-r15, xmm6-xmm15) into a jmp_buf that lives in this stack frame.
 *      Caller-saved registers cannot hold values that are live across the call
 *      into the collector (the ABI lets the callee clobber them), so after the
 *      spill every live reference is somewhere on the stack.
 *   2. The upper bound of the stack is StackBase from the Thread Information
 *      Block (NtCurrentTeb()). The stack grows downwards, so the scan runs from
 *      the address of a local variable of a deeper helper frame up to
 *      StackBase, word by word (8-byte aligned).
 *
 * Restrictions: one thread only (the stacks of other threads are not
 * scanned); generated code is compiled with -O0 (README section 8).
 */
#include <windows.h>
#include <setjmp.h>
#include <string.h>
#include "runtime.h"

void rt_platform_gc_init(void)
{
}

static void scan_from_here(void (*visit)(void *word), char *top)
{
    volatile char marker = 0;
    char *p = (char *)&marker;
    size_t addr;
    memcpy(&addr, &p, sizeof addr);
    p -= addr % sizeof(void *);
    for (; p + sizeof(void *) <= top; p += sizeof(void *)) {
        void *word;
        memcpy(&word, p, sizeof word);
        visit(word);
    }
    (void)marker;
}

/* called through a volatile pointer so that the helper frame is never inlined */
static void (*volatile scan_helper)(void (*)(void *), char *) = scan_from_here;

void rt_platform_scan_stack(void (*visit)(void *word))
{
    jmp_buf regs;
    NT_TIB *tib;
    if (setjmp(regs) != 0)
        return;
    tib = (NT_TIB *)NtCurrentTeb();
    scan_helper(visit, (char *)tib->StackBase);
}
