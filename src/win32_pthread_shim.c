#define PTW32_STATIC_LIB 1
#define HAVE_STRUCT_TIMESPEC 1
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <process.h>
#include <stdlib.h>
#include <time.h>

typedef struct {
    void *p;
    unsigned int x;
} ptw32_handle_t;

typedef ptw32_handle_t pthread_t;
typedef struct pthread_attr_t_ *pthread_attr_t;
typedef struct pthread_mutex_t_ { SRWLOCK lock; } *pthread_mutex_t;
typedef struct pthread_mutexattr_t_ *pthread_mutexattr_t;
typedef struct pthread_cond_t_ { CONDITION_VARIABLE cv; } *pthread_cond_t;
typedef struct pthread_condattr_t_ *pthread_condattr_t;

typedef struct {
    void *(*start)(void *);
    void *arg;
    void *ret;
    HANDLE hThread;
} ng_pth_ctx_t;

static __declspec(thread) ng_pth_ctx_t *g_cur_pth = NULL;

static unsigned __stdcall ng_thread_entry(void *raw) {
    ng_pth_ctx_t *ctx = (ng_pth_ctx_t *)raw;
    g_cur_pth = ctx;
    ctx->ret = ctx->start(ctx->arg);
    return 0;
}

static void ensure_mutex(pthread_mutex_t *m) {
    if (*m == NULL) {
        pthread_mutex_t nm = (pthread_mutex_t)calloc(1, sizeof(struct pthread_mutex_t_));
        InitializeSRWLock(&nm->lock);
        if (InterlockedCompareExchangePointer((PVOID *)m, nm, NULL) != NULL) {
            free(nm);
        }
    }
}

static void ensure_cond(pthread_cond_t *c) {
    if (*c == NULL) {
        pthread_cond_t nc = (pthread_cond_t)calloc(1, sizeof(struct pthread_cond_t_));
        InitializeConditionVariable(&nc->cv);
        if (InterlockedCompareExchangePointer((PVOID *)c, nc, NULL) != NULL) {
            free(nc);
        }
    }
}

int __cdecl pthread_mutex_init(pthread_mutex_t *m, const pthread_mutexattr_t *a) {
    (void)a;
    *m = (pthread_mutex_t)calloc(1, sizeof(struct pthread_mutex_t_));
    InitializeSRWLock(&(*m)->lock);
    return 0;
}

int __cdecl pthread_mutex_destroy(pthread_mutex_t *m) {
    if (m && *m) { free(*m); *m = NULL; }
    return 0;
}

int __cdecl pthread_mutex_lock(pthread_mutex_t *m) {
    ensure_mutex(m);
    AcquireSRWLockExclusive(&(*m)->lock);
    return 0;
}

int __cdecl pthread_mutex_unlock(pthread_mutex_t *m) {
    ensure_mutex(m);
    ReleaseSRWLockExclusive(&(*m)->lock);
    return 0;
}

int __cdecl pthread_cond_init(pthread_cond_t *c, const pthread_condattr_t *a) {
    (void)a;
    *c = (pthread_cond_t)calloc(1, sizeof(struct pthread_cond_t_));
    InitializeConditionVariable(&(*c)->cv);
    return 0;
}

int __cdecl pthread_cond_destroy(pthread_cond_t *c) {
    if (c && *c) { free(*c); *c = NULL; }
    return 0;
}

int __cdecl pthread_cond_signal(pthread_cond_t *c) {
    ensure_cond(c);
    WakeConditionVariable(&(*c)->cv);
    return 0;
}

int __cdecl pthread_cond_broadcast(pthread_cond_t *c) {
    ensure_cond(c);
    WakeAllConditionVariable(&(*c)->cv);
    return 0;
}

int __cdecl pthread_cond_wait(pthread_cond_t *c, pthread_mutex_t *m) {
    ensure_cond(c);
    ensure_mutex(m);
    SleepConditionVariableSRW(&(*c)->cv, &(*m)->lock, INFINITE, 0);
    return 0;
}

int __cdecl pthread_cond_timedwait(pthread_cond_t *c, pthread_mutex_t *m, const struct timespec *abstime) {
    ensure_cond(c);
    ensure_mutex(m);
    DWORD ms = INFINITE;
    if (abstime) {
        struct timespec now_ts;
        timespec_get(&now_ts, TIME_UTC);
        long long diff_ms = ((long long)abstime->tv_sec - (long long)now_ts.tv_sec) * 1000LL +
                            ((long long)abstime->tv_nsec - (long long)now_ts.tv_nsec) / 1000000LL;
        ms = (diff_ms <= 0) ? 0 : (DWORD)diff_ms;
    }
    if (!SleepConditionVariableSRW(&(*c)->cv, &(*m)->lock, ms, 0)) {
        if (GetLastError() == ERROR_TIMEOUT) return 10060; /* ETIMEDOUT */
    }
    return 0;
}

int __cdecl pthread_create(pthread_t *tid, const pthread_attr_t *attr, void *(*start)(void *), void *arg) {
    (void)attr;
    ng_pth_ctx_t *ctx = (ng_pth_ctx_t *)calloc(1, sizeof(ng_pth_ctx_t));
    ctx->start = start;
    ctx->arg = arg;
    uintptr_t h = _beginthreadex(NULL, 0, ng_thread_entry, ctx, 0, NULL);
    if (!h) { free(ctx); return -1; }
    ctx->hThread = (HANDLE)h;
    tid->p = ctx;
    tid->x = 0;
    return 0;
}

int __cdecl pthread_join(pthread_t thread, void **value_ptr) {
    ng_pth_ctx_t *ctx = (ng_pth_ctx_t *)thread.p;
    if (!ctx) return -1;
    WaitForSingleObject(ctx->hThread, INFINITE);
    CloseHandle(ctx->hThread);
    if (value_ptr) *value_ptr = ctx->ret;
    free(ctx);
    return 0;
}

void __cdecl pthread_exit(void *value_ptr) {
    if (g_cur_pth) g_cur_pth->ret = value_ptr;
    _endthreadex(0);
}
