/*
 * rt_gc.c -- portable part of the mark-and-sweep collector.
 *
 * - A full collection runs before every allocation (section 13.1 of the spec).
 * - Roots: conservative scan of the C stack (rt_gc_win.c). Every machine word
 *   that points into a heap block -- to its header or anywhere inside, e.g. to
 *   a subobject of the second parent -- marks that block.
 * - Heap tracing is precise: reference fields by rt_class.ref_offsets, the
 *   elements of reference arrays by the element kind. An explicit mark stack
 *   is used, so deep or cyclic structures do not recurse in C.
 * - Blocks are kept in an array sorted by address; lookup is a binary search.
 */
#include <stdlib.h>
#include <string.h>
#include "runtime.h"

typedef struct rt_block {
    char *start;
    size_t size;
} rt_block;

static rt_block *heap = NULL;
static size_t heap_count = 0, heap_cap = 0;
static rt_object **mark_stack = NULL;
static size_t mark_count = 0, mark_cap = 0;
static unsigned long collections = 0;

void rt_gc_init(void)
{
    heap_count = 0;
    mark_count = 0;
    collections = 0;
}

unsigned long rt_gc_live_count(void)
{
    return (unsigned long)heap_count;
}

unsigned long rt_gc_collections(void)
{
    return collections;
}

/* index of the block containing p, or -1 */
static long find_block(const char *p)
{
    size_t lo = 0, hi = heap_count;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        if (heap[mid].start <= p)
            lo = mid + 1;
        else
            hi = mid;
    }
    if (lo == 0)
        return -1;
    lo--;
    if (p < heap[lo].start + heap[lo].size)
        return (long)lo;
    return -1;
}

static void push(rt_object *o)
{
    if (mark_count == mark_cap) {
        size_t cap = mark_cap ? mark_cap * 2 : 256;
        rt_object **grown = (rt_object **)realloc(mark_stack, cap * sizeof(rt_object *));
        if (grown == NULL)
            rt_error(RT_ERR_OOM, "out of memory in the garbage collector", NULL);
        mark_stack = grown;
        mark_cap = cap;
    }
    mark_stack[mark_count++] = o;
}

static void mark_pointer(void *p)
{
    long i;
    rt_object *o;
    if (p == NULL || heap_count == 0)
        return;
    i = find_block((const char *)p);
    if (i < 0)
        return;
    o = (rt_object *)heap[i].start;
    if (o->mark)
        return;
    o->mark = 1;
    push(o);
}

static void trace(rt_object *o)
{
    const rt_class *cls = o->cls;
    int k;
    if (cls->kind == RT_K_CLASS) {
        for (k = 0; k < cls->nrefs; k++) {
            void *p;
            memcpy(&p, (char *)o + cls->ref_offsets[k], sizeof p);
            mark_pointer(p);
        }
    } else if (cls->kind == RT_K_ARRAY) {
        int e = cls->elem_kind;
        if (e == RT_E_OBJECT || e == RT_E_EXACT || e == RT_E_CLASS || e == RT_E_IFACE) {
            rt_array *a = (rt_array *)o;
            void **data = (void **)RT_ADATA(a);
            jint i;
            for (i = 0; i < a->length; i++)
                mark_pointer(data[i]);
        }
    }
}

void rt_gc_collect(void)
{
    size_t i, kept = 0;
    rt_platform_scan_stack(mark_pointer);
    while (mark_count > 0)
        trace(mark_stack[--mark_count]);
    for (i = 0; i < heap_count; i++) {
        rt_object *o = (rt_object *)heap[i].start;
        if (o->mark) {
            o->mark = 0;
            heap[kept++] = heap[i];
        } else {
            free(heap[i].start);
        }
    }
    heap_count = kept;
    collections++;
}

void *rt_gc_alloc(size_t size, const rt_loc *loc)
{
    char *p;
    size_t lo, hi;
    rt_gc_collect();
    if (size < sizeof(rt_object))
        size = sizeof(rt_object);
    if (heap_count == heap_cap) {
        size_t cap = heap_cap ? heap_cap * 2 : 1024;
        rt_block *grown = (rt_block *)realloc(heap, cap * sizeof(rt_block));
        if (grown == NULL)
            rt_error(RT_ERR_OOM, "out of memory (heap index)", loc);
        heap = grown;
        heap_cap = cap;
    }
    p = (char *)calloc(1, size);
    if (p == NULL)
        rt_error(RT_ERR_OOM, "out of memory", loc);
    lo = 0;
    hi = heap_count;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        if (heap[mid].start < p)
            lo = mid + 1;
        else
            hi = mid;
    }
    memmove(heap + lo + 1, heap + lo, (heap_count - lo) * sizeof(rt_block));
    heap[lo].start = p;
    heap[lo].size = size;
    heap_count++;
    return p;
}
