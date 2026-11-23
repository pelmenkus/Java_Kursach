/*
 * Runtime/GC tests in C89. Linked with the runtime (including rt_main.c),
 * so rt_user_main() below is the test driver. Prints "ok <name>" lines;
 * a failure prints "FAIL ..." and exits with code 3.
 *
 * The collector is conservative: stale words in uninitialised stack slots may
 * keep a few dead objects alive (allowed by section 13.2 of the spec). Checks
 * that something is freed therefore allow SLACK retained objects, while checks
 * that something survives are exact.
 */
#include <stdio.h>
#include <stdlib.h>
#include "runtime.h"

#define SLACK 8UL

/* a class with two reference fields, laid out by hand like generated code */
struct node { const rt_vtbl_hdr *vt; struct node *next; struct node *other; jint val; };
struct node_full { rt_object hdr; struct node top; };

static const unsigned long node_refs[2] = {
    (unsigned long)(offsetof(struct node_full, top) + offsetof(struct node, next)),
    (unsigned long)(offsetof(struct node_full, top) + offsetof(struct node, other))
};
extern rt_class node_class;
static const rt_subdesc node_subs[1] = { { &node_class, (long)offsetof(struct node_full, top), -1 } };
static const rt_vtbl_hdr node_vt = { (long)offsetof(struct node_full, top), 0, 0, NULL };
rt_class node_class = { { &rt_class_Class, 0UL, 0 }, NULL, RT_K_CLASS, sizeof(struct node_full), 1,
                        node_subs, 2, node_refs, NULL, NULL, 0, 0UL, NULL, NULL };
rt_class node_array = { { &rt_class_Class, 0UL, 0 }, NULL, RT_K_ARRAY, 0UL, 0, NULL, 0, NULL, NULL, NULL,
                        RT_E_CLASS, sizeof(void *), &node_class, NULL };

static int failures = 0;

static void check(int cond, const char *name)
{
    printf("%s %s (live=%lu)\n", cond ? "ok" : "FAIL", name, rt_gc_live_count());
    if (!cond)
        failures++;
}

static struct node *new_node(jint v)
{
    struct node_full *o = (struct node_full *)rt_alloc_object(&node_class, sizeof(struct node_full), NULL);
    o->top.vt = &node_vt;
    o->top.val = v;
    return &o->top;     /* interior pointer: the GC must recognise it */
}

/* helpers are called through volatile pointers: separate frames, not inlined */
static void make_garbage(int n)
{
    int i;
    for (i = 0; i < n; i++) {
        struct node *a = new_node(i), *b = new_node(-i);
        a->other = b;
        b->other = a;               /* unreachable cycle */
    }
}

static struct node *make_list(int n)
{
    struct node *head = NULL;
    int i;
    for (i = 0; i < n; i++) {
        struct node *x = new_node(i);
        x->next = head;
        head = x;
    }
    return head;
}

static void make_arrays_and_strings(int n)
{
    int i;
    for (i = 0; i < n; i++) {
        rt_array *a = rt_new_array(&node_array, 4, NULL);
        rt_string *s = rt_str_ascii("garbage", NULL);
        ((struct node **)RT_ADATA(a))[0] = new_node(i);
        s = rt_str_concat(s, s, NULL);
        (void)s;
    }
}

static void (*volatile garbage_ptr)(int) = make_garbage;
static struct node *(*volatile list_ptr)(int) = make_list;
static void (*volatile arrays_ptr)(int) = make_arrays_and_strings;

static unsigned long live_after_collect(void)
{
    rt_gc_collect();
    return rt_gc_live_count();
}

void rt_user_main(rt_array *args)
{
    unsigned long base, before, count;
    struct node *list, *x;
    rt_array *arr;
    rt_string *s;
    jint sum, i;
    (void)args;
    base = live_after_collect();

    garbage_ptr(1000);
    check(live_after_collect() <= base + SLACK, "unreachable objects and cycles are freed");

    list = list_ptr(50);
    garbage_ptr(50);
    count = live_after_collect();
    check(count >= base + 50 && count <= base + 50 + SLACK, "linked list reachable from the stack survives");
    sum = 0;
    for (x = list; x != NULL; x = x->next)
        sum += x->val;
    check(sum == 1225, "surviving objects keep their contents");

    list->next->next = NULL;          /* cut the list: 48 nodes become garbage */
    before = count;
    count = live_after_collect();
    check(count + 40 <= before && count <= base + 2 + SLACK, "objects are freed after becoming unreachable");
    check(list->val == 49 && list->next->val == 48, "still reachable part intact");

    arr = rt_new_array(&node_array, 10, NULL);
    for (i = 0; i < 10; i++)
        ((struct node **)RT_ADATA(arr))[i] = new_node(i);
    for (i = 0; i < 10; i++)
        arrays_ptr(1);
    count = live_after_collect();
    check(count >= base + 11, "elements reachable only through an array survive");
    sum = 0;
    for (i = 0; i < 10; i++)
        sum += ((struct node **)RT_ADATA(arr))[i]->val;
    check(sum == 45, "array element contents intact");

    s = rt_str_ascii("hello", NULL);
    before = rt_gc_collections();
    s = rt_str_concat(s, s, NULL);
    check(rt_gc_collections() == before + 1, "a full collection runs before every allocation");
    check(s->length == 10 && s->chars[9] == 'o', "strings survive collection");

    before = live_after_collect();
    arrays_ptr(500);
    check(live_after_collect() <= before + SLACK, "arrays and strings are collected");

    if (failures != 0)
        exit(3);
}
