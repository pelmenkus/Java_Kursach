/*
 * Defensive runtime checks that well-typed programs never trigger:
 * an invalid array store must stop the program with ArrayStore.
 */
#include "runtime.h"

extern rt_class a_class;
extern rt_class b_class;
struct sub { const rt_vtbl_hdr *vt; };
struct full { rt_object hdr; struct sub top; };
static const rt_subdesc b_subs[1] = { { &b_class, (long)offsetof(struct full, top), -1 } };
static const rt_vtbl_hdr b_vt = { (long)offsetof(struct full, top), 0, 0, NULL };
rt_class a_class = { { &rt_class_Class, 0UL, 0 }, NULL, RT_K_CLASS, 0UL, 0, NULL, 0, NULL, NULL, NULL,
                     0, 0UL, NULL, NULL };
rt_class b_class = { { &rt_class_Class, 0UL, 0 }, NULL, RT_K_CLASS, sizeof(struct full), 1, b_subs, 0, NULL,
                     NULL, NULL, 0, 0UL, NULL, NULL };
rt_class a_array = { { &rt_class_Class, 0UL, 0 }, NULL, RT_K_ARRAY, 0UL, 0, NULL, 0, NULL, NULL, NULL,
                     RT_E_CLASS, sizeof(void *), &a_class, NULL };

void rt_user_main(rt_array *args)
{
    rt_array *arr = rt_new_array(&a_array, 1, NULL);
    struct full *b = (struct full *)rt_alloc_object(&b_class, sizeof(struct full), NULL);
    (void)args;
    b->top.vt = &b_vt;
    rt_arr_store_check(arr, &b->top, NULL);   /* a B view stored into A[] */
}
