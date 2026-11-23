/*
 * rt_main.c -- C entry point: runtime initialisation, String[] args, call of
 * the generated Java main, output flush.
 */
#include "runtime.h"

int main(int argc, char **argv)
{
    void *args;
    rt_init();
    args = rt_args_init(argc, argv);
    /* between here and the call no allocation happens; Java main roots args */
    rt_user_main(args);
    rt_out_flush();
    return 0;
}
