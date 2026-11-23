/*
 * Common definitions for the C runtime tests (ISO C99). A test program plays
 * the role of a generated program: it defines globals[], a class laid out like
 * generated code and rt_user_throwable_message().
 */
#ifndef TEST_SUPPORT_H
#define TEST_SUPPORT_H

#include <stdio.h>
#include <stdlib.h>
#include "runtime.h"

/* static reference fields of the "program" */
void *globals[4];
const size_t globals_count = 4;

void *rt_user_throwable_message(void *ex)
{
    (void)ex;
    return NULL;
}

/* class Node { Node next; Node other; int val; } */
struct node {
    rt_object hdr;
    void *next;
    void *other;
    jint val;
};
#define NODE(p) ((struct node *)(p))

static const size_t node_refs[2] = { offsetof(struct node, next), offsetof(struct node, other) };
rt_class node_class = { RT_STATIC_HDR(&rt_class_Class), NULL, &rt_class_Object, RT_K_CLASS, sizeof(struct node),
                        2, node_refs, &rt_object_vtable, 0, NULL, 0, 0, NULL, NULL };
rt_class node_array = { RT_STATIC_HDR(&rt_class_Class), NULL, &rt_class_Object, RT_K_ARRAY, 0, 0, NULL,
                        &rt_object_vtable, 0, NULL, RT_E_CLASS, sizeof(void *), &node_class, NULL };

/* the result is not rooted: the caller stores it into a frame slot at once */
void *new_node(jint v);
void *new_node(jint v)
{
    void *o = rt_alloc_object(&node_class, NULL);
    NODE(o)->val = v;
    return o;
}

#endif
