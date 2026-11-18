#pragma once
// Baseline CPU Feature Verifier (Tier 1 only: x86-64 + SSE2 + SSE3, all x86 CPUs > 2007)
// Zero multi-tier runtime dispatch; verifies minimum SSE2/SSE3 support cleanly.

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <intrin.h>
#include <windows.h>

namespace nextgowin_cpu {

struct CpuFeatures {
    char vendor[13] = {0};
    char brand[49] = {0};
    uint32_t logical_cpus = 1;
    bool sse2 = false;
    bool sse3 = false;
    bool baseline_ok = false; // x86-64 + SSE2 + SSE3 (all x86 CPUs released after 2007)
};

inline CpuFeatures detect_cpu_features() {
    CpuFeatures f{};
    SYSTEM_INFO si{};
    GetSystemInfo(&si);
    f.logical_cpus = si.dwNumberOfProcessors > 0 ? si.dwNumberOfProcessors : 1;

    int r[4] = {0, 0, 0, 0};
    __cpuid(r, 0);
    int max_std = r[0];
    std::memcpy(f.vendor + 0, &r[1], 4);
    std::memcpy(f.vendor + 4, &r[3], 4);
    std::memcpy(f.vendor + 8, &r[2], 4);
    f.vendor[12] = '\0';

    __cpuid(r, 0x80000000);
    uint32_t max_ext = static_cast<uint32_t>(r[0]);
    if (max_ext >= 0x80000004U) {
        int b0[4], b1[4], b2[4];
        __cpuid(b0, 0x80000002);
        __cpuid(b1, 0x80000003);
        __cpuid(b2, 0x80000004);
        std::memcpy(f.brand + 0, b0, 16);
        std::memcpy(f.brand + 16, b1, 16);
        std::memcpy(f.brand + 32, b2, 16);
        f.brand[48] = '\0';
        for (int i = 47; i >= 0 && f.brand[i] == ' '; --i) f.brand[i] = '\0';
    }

    if (max_std >= 1) {
        int l1[4] = {0};
        __cpuid(l1, 1);
        f.sse2 = ((l1[3] >> 26) & 1) != 0;
        f.sse3 = ((l1[2] >> 0) & 1) != 0;
    }

    f.baseline_ok = f.sse2 && f.sse3;
    return f;
}

inline std::string format_cpu_json(const CpuFeatures& f) {
    char buf[1024];
    std::snprintf(
        buf, sizeof(buf),
        "{\n"
        "  \"vendor\": \"%s\",\n"
        "  \"brand\": \"%s\",\n"
        "  \"logical_cpus\": %u,\n"
        "  \"target_isa\": \"x86-64 + SSE2 + SSE3 (Tier 1 baseline)\",\n"
        "  \"isa_flags\": {\n"
        "    \"sse2\": %s,\n"
        "    \"sse3\": %s\n"
        "  },\n"
        "  \"baseline_supported\": %s\n"
        "}\n",
        f.vendor,
        f.brand,
        f.logical_cpus,
        f.sse2 ? "true" : "false",
        f.sse3 ? "true" : "false",
        f.baseline_ok ? "true" : "false"
    );
    return std::string(buf);
}

} // namespace nextgowin_cpu
