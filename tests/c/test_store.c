/*
 * Defensive runtime checks that well-typed programs never trigger
 * (linked without rt_main.c; argv[1] selects the case):
 *   store   -- storing an unrelated object into A[] stops with ArrayStore;
 *   corrupt -- a damaged frame terminator is detected (CorruptedRootFrame);
 *   unbal   -- popping a frame that is not the top one is detected.
 */
#include <string.h>
#include "test_support.h"

rt_class a_class = { RT_STATIC_HDR(&rt_class_Class), NULL, &rt_class_Object, RT_K_CLASS, sizeof(rt_object), 0,
                     NULL, &rt_object_vtable, 0, NULL, 0, 0, NULL, NULL };
rt_class a_array = { RT_STATIC_HDR(&rt_class_Class), NULL, &rt_class_Object, RT_K_ARRAY, 0, 0, NULL,
                     &rt_object_vtable, 0, NULL, RT_E_CLASS, sizeof(void *), &a_class, NULL };

int main(int argc, char **argv)
{
    static const rt_frame_desc d = { 2, "main(test_store)" };
    void *f[RT_FRAME_WORDS(2)];
    void *g[RT_FRAME_WORDS(2)];
    rt_init();
    if (argc < 2)
        return 2;
    rt_frame_push(f, &d);
    if (strcmp(argv[1], "store") == 0) {
        f[2] = rt_new_array(&a_array, 1, NULL);
        f[3] = new_node(1);
        rt_arr_store_check(f[2], f[3], NULL);          /* a Node into A[] */
    } else if (strcmp(argv[1], "corrupt") == 0) {
        f[4] = NULL;                                   /* terminator of a 2-slot frame */
        rt_gc_collect();
    } else if (strcmp(argv[1], "unbal") == 0) {
        rt_frame_push(g, &d);
        rt_frame_pop(f);
    }
    printf("not detected\n");
    return 0;
}
