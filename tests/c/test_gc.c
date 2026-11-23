/*
 * Precise GC tests (ISO C99). Linked with the whole runtime including
 * rt_main.c, so rt_user_main() below is the driver. Functions keep their
 * references in root frames exactly like generated code. The collector is
 * precise, therefore every live count below is EXACT, at every optimisation
 * level. Prints "ok <name>" lines; a failure prints "FAIL ..." and exits 3.
 */
#include "test_support.h"

static int failures = 0;
static unsigned long hook_calls = 0;

static void check(int cond, const char *name)
{
    printf("%s %s (live=%lu)\n", cond ? "ok" : "FAIL", name, (unsigned long)rt_gc_live_count());
    if (!cond)
        failures++;
}

static size_t live_after_collect(void)
{
    rt_gc_collect();
    return rt_gc_live_count();
}

static void counting_hook(void)
{
    hook_calls++;
}

static void make_garbage(int n)
{
    static const rt_frame_desc d = { 2, "make_garbage" };
    void *f[RT_FRAME_WORDS(2)];
    int i;
    rt_frame_push(f, &d);
    for (i = 0; i < n; i++) {
        f[2] = new_node(i);
        f[3] = new_node(-i);
        NODE(f[2])->other = f[3];
        NODE(f[3])->other = f[2];        /* unreachable cycle after the next iteration */
    }
    rt_frame_pop(f);
}

static void *make_list(int n)
{
    static const rt_frame_desc d = { 2, "make_list" };
    void *f[RT_FRAME_WORDS(2)];
    void *ret;
    int i;
    rt_frame_push(f, &d);
    for (i = 0; i < n; i++) {
        f[3] = new_node(i);
        NODE(f[3])->next = f[2];
        f[2] = f[3];
    }
    ret = f[2];
    rt_frame_pop(f);
    return ret;
}

/* every level roots one node; the deepest level counts the live objects */
static size_t nested(int depth)
{
    static const rt_frame_desc d = { 1, "nested" };
    void *f[RT_FRAME_WORDS(1)];
    size_t r;
    rt_frame_push(f, &d);
    f[2] = new_node(depth);
    r = depth > 0 ? nested(depth - 1) : live_after_collect();
    if (NODE(f[2])->val != depth)
        r = 0;
    rt_frame_pop(f);
    return r;
}

static void make_arrays_and_strings(int n)
{
    static const rt_frame_desc d = { 2, "make_arrays_and_strings" };
    void *f[RT_FRAME_WORDS(2)];
    int i;
    rt_frame_push(f, &d);
    for (i = 0; i < n; i++) {
        f[2] = rt_new_array(&node_array, 4, NULL);
        ((void **)RT_ADATA(f[2]))[0] = new_node(i);
        f[3] = rt_str_ascii("garbage", NULL);
        f[3] = rt_str_concat(f[3], f[3], NULL);
    }
    rt_frame_pop(f);
}

static const unsigned char lit_bytes[] = "literal";
static rt_string literal = { RT_STATIC_HDR(&rt_class_String), 7, 7, true, lit_bytes };

void rt_user_main(void *args)
{
    static const rt_frame_desc d = { 6, "rt_user_main(test)" };
    void *f[RT_FRAME_WORDS(6)];
    size_t base, count;
    unsigned long before;
    jint sum, i;
    void *x;
    rt_frame_push(f, &d);
    f[2] = args;                                      /* String[0] */

    base = live_after_collect();
    check(base == 1, "only the rooted args array is live");

    make_garbage(500);
    check(live_after_collect() == base, "unreachable objects and cycles are freed exactly");

    f[3] = make_list(50);
    make_garbage(50);
    check(live_after_collect() == base + 50, "list rooted in a frame slot survives, nothing else");
    sum = 0;
    for (x = f[3]; x != NULL; x = NODE(x)->next)
        sum += NODE(x)->val;
    check(sum == 1225, "surviving objects keep their contents");

    NODE(NODE(f[3])->next)->next = NULL;              /* cut: 48 nodes become garbage */
    check(live_after_collect() == base + 2, "objects are freed after becoming unreachable");
    check(NODE(f[3])->val == 49 && NODE(NODE(f[3])->next)->val == 48, "reachable part intact");

    f[3] = NULL;                                      /* what ClearRefs does for a dead slot */
    check(live_after_collect() == base, "a cleared (dead) slot no longer retains objects");

    count = nested(9);
    check(count == base + 10, "nested frames: every level's slots are roots");
    check(live_after_collect() == base, "popped frames are no longer roots");

    f[4] = rt_new_array(&node_array, 10, NULL);
    for (i = 0; i < 10; i++)
        ((void **)RT_ADATA(f[4]))[i] = new_node(i);
    make_arrays_and_strings(10);
    check(live_after_collect() == base + 11, "elements reachable only through an array survive");
    sum = 0;
    for (i = 0; i < 10; i++)
        sum += NODE(((void **)RT_ADATA(f[4]))[i])->val;
    check(sum == 45, "array element contents intact");
    f[4] = NULL;

    globals[1] = new_node(7);
    NODE(globals[1])->next = new_node(8);
    check(live_after_collect() == base + 2, "static fields (globals) are roots");
    globals[1] = NULL;
    check(live_after_collect() == base, "cleared static field releases its objects");

    f[5] = &literal;                                  /* static object: ignored by the collector */
    globals[2] = &literal;
    check(live_after_collect() == base && f[5] == &literal, "static objects in roots are ignored");

    f[6] = rt_str_ascii("hello", NULL);
    before = rt_gc_collections();
    rt_gc_hook = counting_hook;
    f[6] = rt_str_concat(f[6], f[6], NULL);
    f[7] = new_node(1);
    rt_gc_hook = NULL;
    check(rt_gc_collections() == before + 2 && hook_calls == 2,
          "a full collection (and the hook) runs before every allocation");
    check(((rt_string *)f[6])->length == 10 && rt_str_charAt(f[6], 9, NULL) == 'o', "strings survive collection");
    check(live_after_collect() == base + 2, "exact count with strings and nodes");
    f[6] = NULL;
    f[7] = NULL;

    make_arrays_and_strings(300);
    check(live_after_collect() == base, "arrays and strings are collected exactly");

    f[3] = make_list(3000);                           /* long chain: explicit worklist, no recursion */
    NODE(f[3])->other = f[3];                         /* plus a cycle through the root */
    check(live_after_collect() == base + 3000, "long chain survives");
    f[3] = NULL;
    check(live_after_collect() == base, "long chain freed");

    rt_frame_pop(f);
    check(rt_rets == NULL, "frame chain balanced");
    if (failures != 0)
        exit(3);
}
