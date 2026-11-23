/*
 * rt_gc.c -- precise mark-and-sweep collector (section 13 of the spec).
 *
 * - A full collection runs before every managed allocation.
 * - Roots are exact: every slot of globals[0..globals_count-1] and every slot
 *   of the root frame chain that starts at rt_rets. Generated functions keep
 *   all live references in their frames; nothing on the C stack is scanned,
 *   so the collector needs no knowledge of the ABI, registers or the
 *   direction of stack growth, and works at any optimisation level.
 * - Every non-null slot value is looked up in the allocation registry
 *   (sorted array, binary search). Values that are not registered objects
 *   are static objects (string literals, Class objects) and are ignored.
 * - Heap tracing is precise: reference fields by rt_class.ref_offsets,
 *   elements of reference arrays by the element kind. An explicit worklist
 *   avoids recursion on deep or cyclic structures.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "runtime.h"

void **rt_rets = NULL;
void (*rt_gc_hook)(void) = NULL;

static rt_object **registry = NULL;          /* sorted by address */
static size_t reg_count = 0, reg_cap = 0;
static rt_object **work = NULL;
static size_t work_count = 0, work_cap = 0;
static unsigned long collections = 0;
static uint64_t next_id = 0;

size_t rt_gc_live_count(void)
{
    return reg_count;
}

unsigned long rt_gc_collections(void)
{
    return collections;
}

/* ------------------------------------------------------------ root frames */
void rt_frame_push(void **frame, const rt_frame_desc *desc)
{
    size_t i;
    frame[0] = (void *)rt_rets;
    frame[1] = (void *)desc;
    for (i = 0; i < desc->root_count; i++)
        frame[2 + i] = NULL;
    frame[desc->root_count + 2] = (void *)frame;
    rt_rets = frame;
}

void rt_frame_pop(void **frame)
{
    if (rt_rets != frame)
        rt_error(RT_ERR_FRAME, "unbalanced root frame chain (internal runtime error)", NULL);
    rt_rets = (void **)frame[0];
}

/* ------------------------------------------------------------ registry */
/* Relational comparison of pointers to different objects is undefined in C,
   so the registry is ordered by the integer image of the address. uintptr_t
   is optional in C99; the build stops with a diagnostic where it is missing. */
#if !defined(UINTPTR_MAX)
#error "runtime: uintptr_t is required by the allocation registry"
#endif
#define ADDR(p) ((uintptr_t)(const void *)(p))

static size_t lower_bound(const void *p)
{
    size_t lo = 0, hi = reg_count;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        if (ADDR(registry[mid]) < ADDR(p))
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo;
}

static rt_object *find_object(const void *p)
{
    size_t i = lower_bound(p);
    if (i < reg_count && (const void *)registry[i] == p)
        return registry[i];
    return NULL;
}

/* ------------------------------------------------------------ mark */
static void push_work(rt_object *o)
{
    if (work_count == work_cap) {
        size_t cap = work_cap ? work_cap * 2 : 256;
        rt_object **grown = realloc(work, cap * sizeof *grown);
        if (grown == NULL)
            rt_error(RT_ERR_OOM, "out of memory in the garbage collector", NULL);
        work = grown;
        work_cap = cap;
    }
    work[work_count++] = o;
}

static void mark_reference(void *p)
{
    rt_object *o;
    if (p == NULL)
        return;
    o = find_object(p);
    if (o == NULL || o->mark)
        return;          /* static object, or already marked */
    o->mark = 1;
    push_work(o);
}

static void trace(rt_object *o)
{
    const rt_class *cls = o->cls;
    size_t k;
    if (cls->kind == RT_K_CLASS) {
        for (k = 0; k < cls->nrefs; k++) {
            void *p;
            memcpy(&p, (char *)o + cls->ref_offsets[k], sizeof p);
            mark_reference(p);
        }
    } else if (cls->kind == RT_K_ARRAY) {
        int e = cls->elem_kind;
        if (e == RT_E_OBJECT || e == RT_E_EXACT || e == RT_E_CLASS || e == RT_E_IFACE) {
            rt_array *a = (rt_array *)o;
            void **data = RT_ADATA(a);
            jint i;
            for (i = 0; i < a->length; i++)
                mark_reference(data[i]);
        }
    }
}

static void mark_roots(void)
{
    size_t i;
    void **frame;
    for (i = 0; i < globals_count; i++)
        mark_reference(globals[i]);
    for (frame = rt_rets; frame != NULL; frame = (void **)frame[0]) {
        const rt_frame_desc *d = (const rt_frame_desc *)frame[1];
        /* the terminator is checked at the position given by the descriptor,
           so a slot that happens to point to the frame is never taken for it */
        if (d == NULL || frame[d->root_count + 2] != (void *)frame)
            rt_error(RT_ERR_FRAME, "corrupted root frame (terminator mismatch)", NULL);
        for (i = 0; i < d->root_count; i++)
            mark_reference(frame[2 + i]);
    }
}

void rt_gc_collect(void)
{
    size_t i, kept = 0;
    mark_roots();
    while (work_count > 0)
        trace(work[--work_count]);
    for (i = 0; i < reg_count; i++) {
        rt_object *o = registry[i];
        if (o->mark) {
            o->mark = 0;
            registry[kept++] = o;
        } else {
            free(o);
        }
    }
    reg_count = kept;
    collections++;
    if (rt_gc_hook != NULL)
        rt_gc_hook();
}

/* ------------------------------------------------------------ allocation */
void *rt_gc_alloc(size_t size, const rt_loc *loc)
{
    rt_object *o;
    size_t pos;
    rt_gc_collect();
    if (size < sizeof(rt_object))
        size = sizeof(rt_object);
    if (reg_count == reg_cap) {
        size_t cap = reg_cap ? reg_cap * 2 : 1024;
        rt_object **grown = realloc(registry, cap * sizeof *grown);
        if (grown == NULL)
            rt_error(RT_ERR_OOM, "out of memory (allocation registry)", loc);
        registry = grown;
        reg_cap = cap;
    }
    o = calloc(1, size);
    if (o == NULL)
        rt_error(RT_ERR_OOM, "out of memory", loc);
    pos = lower_bound(o);
    memmove(registry + pos + 1, registry + pos, (reg_count - pos) * sizeof *registry);
    registry[pos] = o;
    reg_count++;
    return o;
}

void *rt_alloc_sized(const rt_class *cls, size_t size, const rt_loc *loc)
{
    rt_object *o = rt_gc_alloc(size, loc);
    o->cls = cls;
    o->id = ++next_id;
    o->mark = 0;
    return o;
}

void *rt_alloc_object(const rt_class *cls, const rt_loc *loc)
{
    return rt_alloc_sized(cls, cls->size, loc);
}
