/*
 * capshim: lets FRR start in a sandboxed (unprivileged) container.
 *
 * FRR's daemons ask for CAP_SYS_ADMIN when they start (it's only used for network-namespace
 * VRFs, which Patchyard doesn't use) and exit if they can't have it: "privs_init: initial
 * cap_set_proc failed". Giving lab containers CAP_SYS_ADMIN would undo most of the sandbox,
 * so instead this library is preloaded into FRR (LD_PRELOAD) and wraps cap_set_proc(): any
 * capability the container's bounding set doesn't allow is dropped from the request first.
 * The daemons then start with what they really need (NET_ADMIN, NET_RAW, NET_BIND_SERVICE).
 *
 * No libcap headers or linking: cap_set_flag/cap_set_proc are found at run time.
 * Built for musl (Alpine, like the FRR image) in the Patchyard Dockerfile.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <sys/prctl.h>

#ifndef PR_CAPBSET_READ
#define PR_CAPBSET_READ 23
#endif

typedef void *cap_t;
typedef int cap_value_t;
typedef int (*set_proc_fn)(cap_t);
typedef int (*set_flag_fn)(cap_t, int, int, const cap_value_t *, int);

int cap_set_proc(cap_t caps)
{
    static set_proc_fn real;
    static set_flag_fn set_flag;
    if (!real)
        real = (set_proc_fn)dlsym(RTLD_NEXT, "cap_set_proc");
    if (!set_flag)
        set_flag = (set_flag_fn)dlsym(RTLD_DEFAULT, "cap_set_flag");
    if (!real)
        return -1;
    if (caps && set_flag) {
        for (cap_value_t v = 0; v < 64; v++) {
            if (prctl(PR_CAPBSET_READ, v, 0, 0, 0) == 0) {      /* 0 = not allowed here; -1 = no such cap */
                for (int flag = 0; flag < 3; flag++)              /* effective, permitted, inheritable */
                    set_flag(caps, flag, 1, &v, 0 /* CAP_CLEAR */);
            }
        }
    }
    return real(caps);
}
