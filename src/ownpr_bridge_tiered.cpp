// Baseline x86-64 + SSE2/SSE3 Custom P&R Native Bridge (Tier 1 only: CPUs > 2007)
// Compiled with /O2 /MT /fp:precise /d2Qvec-sse2only /utf-8 (zero external C/C++ runtime DLL dependencies).

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <vector>
#include <queue>
#include <unordered_map>
#include <unordered_set>
#include <set>
#include <algorithm>
#include <numeric>
#include <chrono>
#include <malloc.h>
#include <intrin.h>
#include <emmintrin.h>
#include <pmmintrin.h>

// Disable automatic scalar FMA contraction for deterministic IEEE-754 single-rounding float arithmetic
// across placement and PathFinder routing.
#pragma fp_contract(off)

#define EXPORT extern "C" __declspec(dllexport)

EXPORT int32_t bridge_tier_id() {
    return 10;
}

namespace {

inline int ctz32_simd(uint32_t m) {
    unsigned long idx = 0;
    _BitScanForward(&idx, m);
    return static_cast<int>(idx);
}

inline int ctz64_simd(uint64_t m) {
    unsigned long idx = 0;
    _BitScanForward64(&idx, m);
    return static_cast<int>(idx);
}

// 64-byte-aligned SwissTable flat open-addressed map for 7M+ RRG pointer & coordinate lookups.
// Eliminates 14,000,000 std::unordered_map heap node allocations per worker process.
class SimdFlatMap64 {
public:
    SimdFlatMap64() = default;
    ~SimdFlatMap64() { release(); }
    SimdFlatMap64(const SimdFlatMap64&) = delete;
    SimdFlatMap64& operator=(const SimdFlatMap64&) = delete;
    SimdFlatMap64(SimdFlatMap64&& o) noexcept { swap_with(o); }
    SimdFlatMap64& operator=(SimdFlatMap64&& o) noexcept {
        if (this != &o) { release(); swap_with(o); }
        return *this;
    }

    void init_pow2(uint32_t cap_pow2) {
        release();
        cap_ = cap_pow2;
        mask_ = cap_pow2 - 1U;
        tags_ = static_cast<uint8_t*>(_aligned_malloc(cap_pow2 + 64U, 64));
        keys_ = static_cast<uint64_t*>(_aligned_malloc(static_cast<size_t>(cap_pow2) * sizeof(uint64_t), 64));
        vals_ = static_cast<int32_t*>(_aligned_malloc(static_cast<size_t>(cap_pow2) * sizeof(int32_t), 64));
        std::memset(tags_, 0x80, cap_pow2 + 64U);
    }

    inline int32_t find_val(uint64_t key) const {
        if (!tags_) return -1;
        uint64_t h = mix64(key);
        uint8_t h2 = static_cast<uint8_t>(h & 0x7FU);
        uint32_t pos = static_cast<uint32_t>(h >> 7) & mask_;
        const __m128i vh2 = _mm_set1_epi8(static_cast<char>(h2));
        const __m128i vemp = _mm_set1_epi8(static_cast<char>(0x80));
        for (;;) {
            uint32_t grp = pos & ~15U;
            __m128i vt = _mm_load_si128(reinterpret_cast<const __m128i*>(tags_ + grp));
            uint32_t mm = static_cast<uint32_t>(_mm_movemask_epi8(_mm_cmpeq_epi8(vt, vh2)));
            while (mm) {
                int bit = ctz32_simd(mm);
                uint32_t idx = grp + static_cast<uint32_t>(bit);
                if (keys_[idx] == key) return vals_[idx];
                mm &= (mm - 1U);
            }
            if (_mm_movemask_epi8(_mm_cmpeq_epi8(vt, vemp)) != 0) return -1;
            pos = (grp + 16U) & mask_;
        }
    }

    inline void insert_new(uint64_t key, int32_t val) {
        uint64_t h = mix64(key);
        uint8_t h2 = static_cast<uint8_t>(h & 0x7FU);
        uint32_t pos = static_cast<uint32_t>(h >> 7) & mask_;
        uint32_t grp = pos & ~15U;
        const __m128i vemp = _mm_set1_epi8(static_cast<char>(0x80));
        for (;;) {
            uint32_t emp = static_cast<uint32_t>(_mm_movemask_epi8(_mm_cmpeq_epi8(_mm_load_si128(reinterpret_cast<const __m128i*>(tags_ + grp)), vemp)));
            if (emp != 0U) {
                uint32_t idx = grp + static_cast<uint32_t>(ctz32_simd(emp));
                tags_[idx] = h2;
                keys_[idx] = key;
                vals_[idx] = val;
                return;
            }
            grp = (grp + 16U) & mask_;
        }
    }

    inline void upsert(uint64_t key, int32_t val) {
        if (find_val(key) >= 0) return;
        insert_new(key, val);
    }

private:
    static inline uint64_t mix64(uint64_t x) {
        x ^= x >> 30;
        x *= 0xbf58476d1ce4e5b9ULL;
        x ^= x >> 27;
        x *= 0x94d049bb133111ebULL;
        x ^= x >> 31;
        return x;
    }

    void release() {
        if (tags_) { _aligned_free(tags_); tags_ = nullptr; }
        if (keys_) { _aligned_free(keys_); keys_ = nullptr; }
        if (vals_) { _aligned_free(vals_); vals_ = nullptr; }
        cap_ = mask_ = 0;
    }

    void swap_with(SimdFlatMap64& o) noexcept {
        std::swap(tags_, o.tags_);
        std::swap(keys_, o.keys_);
        std::swap(vals_, o.vals_);
        std::swap(cap_, o.cap_);
        std::swap(mask_, o.mask_);
    }

    uint8_t* tags_ = nullptr;
    uint64_t* keys_ = nullptr;
    int32_t* vals_ = nullptr;
    uint32_t cap_ = 0;
    uint32_t mask_ = 0;
};

using GetGridFn     = void* (*)(void* chip, int16_t r, int16_t c);
using GetNodeFn     = void* (*)(void* table, int16_t idx);
using GetOutFn      = void* (*)(void* node, int16_t idx);
using WireFn        = void  (*)(void* lib, float* out4, void* node, int32_t selector, int32_t kind);
using GetNodesPinFn = bool  (*)(void* pin, void* std_vec, void* mem);
using SetRoutedFn   = void  (*)(void* obj, bool val);

struct NodeMeta {
    int16_t r;
    int16_t c;
    int16_t idx;
    int16_t fanout_cnt;
};

inline uint64_t pack_coord(int16_t r, int16_t c, int16_t idx) {
    return (static_cast<uint64_t>(static_cast<uint16_t>(r)) << 32) |
           (static_cast<uint64_t>(static_cast<uint16_t>(c)) << 16) |
            static_cast<uint64_t>(static_cast<uint16_t>(idx));
}

struct BridgeState {
    uint64_t dll_base = 0;
    uint64_t chip = 0;
    std::vector<uint64_t> id_to_ptr;
    std::vector<NodeMeta> meta;
    std::vector<int16_t> eff_r;
    std::vector<int16_t> eff_c;
    SimdFlatMap64 ptr_to_id;
    SimdFlatMap64 coord_to_id;
    std::vector<int32_t> row_ptr;
    std::vector<int32_t> col_idx;
    std::vector<int32_t> first_parent;
    std::vector<float> setup_ns;
    std::vector<float> hold_ns;
    std::vector<uint64_t> route_owner;
    std::vector<int32_t> bfs_parent;
    std::vector<int32_t> bfs_stamp;
    int32_t cur_stamp = 1;

    // Last completed PathFinder route records
    std::vector<int32_t> routed_children;
    std::vector<int32_t> routed_parents;
    std::vector<uint64_t> routed_nets;
    int32_t last_route_iters = 0;
    int32_t last_overused = 0;
};

BridgeState g_state;

inline uint64_t rd_u64(uint64_t addr) {
    return *reinterpret_cast<const uint64_t*>(addr);
}
inline void wr_u64(uint64_t addr, uint64_t val) {
    *reinterpret_cast<uint64_t*>(addr) = val;
}
inline int16_t rd_i16(uint64_t addr) {
    return *reinterpret_cast<const int16_t*>(addr);
}
inline uint8_t rd_u8(uint64_t addr) {
    return *reinterpret_cast<const uint8_t*>(addr);
}
inline void wr_u8(uint64_t addr, uint8_t val) {
    *reinterpret_cast<uint8_t*>(addr) = val;
}

int32_t ensure_node(uint64_t p, int16_t hint_r = -1, int16_t hint_c = -1) {
    int32_t existing_id = g_state.ptr_to_id.find_val(p);
    if (existing_id >= 0) {
        int32_t id = existing_id;
        if ((g_state.eff_r[id] < 0 || g_state.eff_c[id] < 0) && hint_r >= 0 && hint_c >= 0) {
            g_state.eff_r[id] = hint_r;
            g_state.eff_c[id] = hint_c;
        }
        return id;
    }
    int32_t id = static_cast<int32_t>(g_state.id_to_ptr.size());
    g_state.ptr_to_id.insert_new(p, id);
    g_state.id_to_ptr.push_back(p);
    NodeMeta m;
    m.r = rd_i16(p + 0);
    m.c = rd_i16(p + 2);
    m.idx = rd_i16(p + 4);
    m.fanout_cnt = rd_i16(p + 6);
    g_state.meta.push_back(m);
    if (m.r >= 0 && m.r <= 110 && m.c >= 0 && m.c <= 183) {
        g_state.eff_r.push_back(m.r);
        g_state.eff_c.push_back(m.c);
    } else if (hint_r >= 0 && hint_c >= 0) {
        g_state.eff_r.push_back(hint_r);
        g_state.eff_c.push_back(hint_c);
    } else {
        g_state.eff_r.push_back(-1);
        g_state.eff_c.push_back(-1);
    }
    g_state.coord_to_id.upsert(pack_coord(m.r, m.c, m.idx), id);
    return id;
}

void resolve_effective_coords(int32_t start_u) {
    int32_t N = static_cast<int32_t>(g_state.id_to_ptr.size());
    for (int pass = 0; pass < 3; ++pass) {
        for (int32_t u = start_u; u < N; ++u) {
            if (g_state.eff_r[u] >= 0 && g_state.eff_c[u] >= 0) continue;
            int64_t sr = 0, sc = 0, cnt = 0;
            int32_t beg = g_state.row_ptr[u];
            int32_t end = g_state.row_ptr[u + 1];
            for (int32_t ei = beg; ei < end; ++ei) {
                int32_t v = g_state.col_idx[ei];
                if (g_state.eff_r[v] >= 0 && g_state.eff_c[v] >= 0) {
                    sr += g_state.eff_r[v];
                    sc += g_state.eff_c[v];
                    ++cnt;
                }
            }
            if (cnt == 0 && g_state.first_parent[u] >= 0) {
                int32_t pr = g_state.first_parent[u];
                if (g_state.eff_r[pr] >= 0 && g_state.eff_c[pr] >= 0) {
                    sr = g_state.eff_r[pr];
                    sc = g_state.eff_c[pr];
                    cnt = 1;
                }
            }
            if (cnt > 0) {
                g_state.eff_r[u] = static_cast<int16_t>(sr / cnt);
                g_state.eff_c[u] = static_cast<int16_t>(sc / cnt);
            }
        }
    }
    for (int32_t u = start_u; u < N; ++u) {
        if (g_state.eff_r[u] < 0 || g_state.eff_c[u] < 0) {
            g_state.eff_r[u] = 55;
            g_state.eff_c[u] = 92;
        }
    }
}

} // namespace

EXPORT int32_t bridge_init_rrg(uint64_t dll_base, uint64_t chip, uint64_t getgrid_fn_addr) {
    g_state = BridgeState{};
    g_state.dll_base = dll_base;
    g_state.chip = chip;
    g_state.id_to_ptr.reserve(6500000);
    g_state.meta.reserve(6500000);
    g_state.eff_r.reserve(6500000);
    g_state.eff_c.reserve(6500000);
    g_state.ptr_to_id.init_pow2(1U << 24);
    g_state.coord_to_id.init_pow2(1U << 24);

    auto getgrid = reinterpret_cast<GetGridFn>(getgrid_fn_addr);
    auto getnode = reinterpret_cast<GetNodeFn>(dll_base + 0x20fcd0ULL);
    auto getout  = reinterpret_cast<GetOutFn>(dll_base + 0x211ed0ULL);

    void* chip_p = reinterpret_cast<void*>(chip);
    for (int16_t r = 0; r < 111; ++r) {
        for (int16_t c = 0; c < 184; ++c) {
            void* g = getgrid(chip_p, r, c);
            if (!g) continue;
            uint64_t tbl = rd_u64(reinterpret_cast<uint64_t>(g) + 0x10ULL);
            if (!tbl) continue;
            int16_t cnt = rd_i16(tbl + 8ULL);
            for (int16_t i = 0; i < cnt; ++i) {
                void* nd = getnode(reinterpret_cast<void*>(tbl), i);
                if (nd) {
                    ensure_node(reinterpret_cast<uint64_t>(nd), r, c);
                }
            }
        }
    }

    std::vector<std::vector<int32_t>> adj;
    adj.reserve(6500000);
    for (size_t u = 0; u < g_state.id_to_ptr.size(); ++u) {
        uint64_t up = g_state.id_to_ptr[u];
        int16_t hr = g_state.eff_r[u];
        int16_t hc = g_state.eff_c[u];
        int16_t cnt = rd_i16(up + 6ULL);
        if (cnt < 0) cnt = 0;
        std::vector<int32_t> outs;
        if (cnt > 0) {
            outs.reserve(cnt);
            for (int16_t k = 0; k < cnt; ++k) {
                void* vp = getout(reinterpret_cast<void*>(up), k);
                if (vp) {
                    int32_t vid = ensure_node(reinterpret_cast<uint64_t>(vp), hr, hc);
                    outs.push_back(vid);
                }
            }
            if (outs.size() > 1) {
                std::sort(outs.begin(), outs.end());
                outs.erase(std::unique(outs.begin(), outs.end()), outs.end());
            }
        }
        adj.push_back(std::move(outs));
    }

    int32_t N = static_cast<int32_t>(g_state.id_to_ptr.size());
    g_state.row_ptr.assign(N + 1, 0);
    g_state.first_parent.assign(N, -1);
    int64_t total_edges = 0;
    for (int32_t u = 0; u < N; ++u) {
        total_edges += static_cast<int64_t>(adj[u].size());
    }
    g_state.col_idx.resize(static_cast<size_t>(total_edges));
    int32_t pos = 0;
    for (int32_t u = 0; u < N; ++u) {
        g_state.row_ptr[u] = pos;
        for (int32_t v : adj[u]) {
            g_state.col_idx[pos++] = v;
            if (g_state.first_parent[v] < 0) {
                g_state.first_parent[v] = u;
            }
        }
    }
    g_state.row_ptr[N] = pos;
    resolve_effective_coords(0);
    g_state.setup_ns.assign(N, 0.05f);
    g_state.hold_ns.assign(N, 0.02f);
    g_state.route_owner.assign(N, 0ULL);
    g_state.bfs_parent.assign(N, -1);
    g_state.bfs_stamp.assign(N, 0);
    g_state.cur_stamp = 1;
    return N;
}

EXPORT int32_t bridge_ensure_extra_nodes(const uint64_t* node_ptrs, int32_t count) {
    if (!node_ptrs || count <= 0) return static_cast<int32_t>(g_state.id_to_ptr.size());
    auto getout = reinterpret_cast<GetOutFn>(g_state.dll_base + 0x211ed0ULL);
    size_t old_n = g_state.id_to_ptr.size();
    for (int32_t i = 0; i < count; ++i) {
        if (node_ptrs[i]) ensure_node(node_ptrs[i]);
    }
    if (g_state.id_to_ptr.size() == old_n) {
        return static_cast<int32_t>(old_n);
    }
    for (size_t u = old_n; u < g_state.id_to_ptr.size(); ++u) {
        uint64_t up = g_state.id_to_ptr[u];
        int16_t hr = g_state.eff_r[u];
        int16_t hc = g_state.eff_c[u];
        int16_t cnt = rd_i16(up + 6ULL);
        if (cnt < 0) cnt = 0;
        std::vector<int32_t> outs;
        for (int16_t k = 0; k < cnt; ++k) {
            void* vp = getout(reinterpret_cast<void*>(up), k);
            if (vp) outs.push_back(ensure_node(reinterpret_cast<uint64_t>(vp), hr, hc));
        }
        std::sort(outs.begin(), outs.end());
        outs.erase(std::unique(outs.begin(), outs.end()), outs.end());
        g_state.first_parent.resize(g_state.id_to_ptr.size(), -1);
        int32_t pos = static_cast<int32_t>(g_state.col_idx.size());
        if (static_cast<int32_t>(u) >= static_cast<int32_t>(g_state.row_ptr.size()) - 1) {
            g_state.row_ptr.resize(u + 2, pos);
        }
        g_state.row_ptr[u] = pos;
        for (int32_t v : outs) {
            g_state.col_idx.push_back(v);
            if (g_state.first_parent[v] < 0) g_state.first_parent[v] = static_cast<int32_t>(u);
        }
        g_state.row_ptr[u + 1] = static_cast<int32_t>(g_state.col_idx.size());
    }
    resolve_effective_coords(static_cast<int32_t>(old_n));
    int32_t N = static_cast<int32_t>(g_state.id_to_ptr.size());
    g_state.setup_ns.resize(N, 0.05f);
    g_state.hold_ns.resize(N, 0.02f);
    g_state.route_owner.resize(N, 0ULL);
    g_state.bfs_parent.resize(N, -1);
    g_state.bfs_stamp.resize(N, 0);
    return N;
}

static uint64_t compute_rrg_topology_fnv64() {
    uint64_t h = 14695981039346656037ULL;
    auto mix_bytes = [&](const void* p, size_t n) {
        const uint8_t* b = static_cast<const uint8_t*>(p);
        for (size_t i = 0; i < n; ++i) {
            h ^= static_cast<uint64_t>(b[i]);
            h *= 1099511628211ULL;
        }
    };
    int32_t N = static_cast<int32_t>(g_state.id_to_ptr.size());
    int32_t M = static_cast<int32_t>(g_state.col_idx.size());
    mix_bytes(&N, sizeof(N));
    mix_bytes(&M, sizeof(M));
    if (N > 0) {
        mix_bytes(g_state.first_parent.data(), static_cast<size_t>(N) * sizeof(int32_t));
        mix_bytes(g_state.meta.data(), static_cast<size_t>(N) * sizeof(NodeMeta));
    }
    return h;
}

EXPORT int32_t bridge_compute_delays(uint64_t tm_lib, int32_t dev_kind) {
    int32_t N = static_cast<int32_t>(g_state.id_to_ptr.size());
    if (N <= 0 || !tm_lib) return -1;
    const char* cache_path = std::getenv("NEXTGOWIN_DELAY_CACHE");
    uint64_t topo_sig = 0ULL;
    if (cache_path && cache_path[0]) {
        topo_sig = compute_rrg_topology_fnv64();
        FILE* cfp = std::fopen(cache_path, "rb");
        if (cfp) {
            uint64_t hdr[4] = {0, 0, 0, 0};
            if (std::fread(hdr, sizeof(uint64_t), 4, cfp) == 4 &&
                hdr[0] == 0x4E47444C59434831ULL &&
                hdr[1] == static_cast<uint64_t>(N) &&
                hdr[2] == static_cast<uint64_t>(dev_kind) &&
                hdr[3] == topo_sig) {
                g_state.setup_ns.resize(N);
                g_state.hold_ns.resize(N);
                size_t r1 = std::fread(g_state.setup_ns.data(), sizeof(float), N, cfp);
                size_t r2 = std::fread(g_state.hold_ns.data(), sizeof(float), N, cfp);
                std::fclose(cfp);
                if (r1 == static_cast<size_t>(N) && r2 == static_cast<size_t>(N)) {
                    std::fprintf(stdout, "[ownpr-delays] Loaded verified RRG delay cache (%d nodes, sig=0x%llx)\n",
                                 N, static_cast<unsigned long long>(topo_sig));
                    std::fflush(stdout);
                    return 0;
                }
            } else {
                std::fclose(cfp);
            }
        }
    }
    auto wire_fn = reinterpret_cast<WireFn>(g_state.dll_base + 0x14733b0ULL);
    void* lib_p = reinterpret_cast<void*>(tm_lib);
    g_state.setup_ns.assign(N, 0.0f);
    g_state.hold_ns.assign(N, 0.0f);

    int64_t nonzero_cnt = 0;
    double sum_s = 0.0;
    float max_s = 0.0f;
    for (int32_t v = 0; v < N; ++v) {
        int32_t par = g_state.first_parent[v];
        if (par < 0 || par == v) continue;
        uint64_t pp = g_state.id_to_ptr[par];
        uint64_t np = g_state.id_to_ptr[v];
        uint64_t saved_pp = rd_u64(pp + 0x18ULL);
        uint8_t  saved_pf = rd_u8(pp + 0x2aULL);
        uint64_t saved_p  = rd_u64(np + 0x18ULL);
        uint8_t  saved_f  = rd_u8(np + 0x2aULL);

        wr_u64(pp + 0x18ULL, 0ULL);
        wr_u8(pp + 0x2aULL, static_cast<uint8_t>(saved_pf | 8U));
        wr_u64(np + 0x18ULL, pp);
        wr_u8(np + 0x2aULL, static_cast<uint8_t>(saved_f | 8U));

        float d1[4] = {0, 0, 0, 0};
        float d2[4] = {0, 0, 0, 0};
        wire_fn(lib_p, d1, reinterpret_cast<void*>(np), 1, dev_kind);
        wire_fn(lib_p, d2, reinterpret_cast<void*>(np), 2, dev_kind);

        wr_u64(np + 0x18ULL, saved_p);
        wr_u8(np + 0x2aULL, saved_f);
        wr_u64(pp + 0x18ULL, saved_pp);
        wr_u8(pp + 0x2aULL, saved_pf);

        float s_val = std::max(d1[2], d1[3]);
        float h_val = std::min(d2[0], d2[1]);
        if (!std::isfinite(s_val) || s_val < 0.0f) s_val = 0.0f;
        if (!std::isfinite(h_val) || h_val < 0.0f) h_val = 0.0f;
        g_state.setup_ns[v] = s_val;
        g_state.hold_ns[v]  = h_val;
        if (s_val > 1e-6f) {
            ++nonzero_cnt;
            sum_s += s_val;
            if (s_val > max_s) max_s = s_val;
        }
    }
    if (cache_path && cache_path[0]) {
        std::string tmp_p = std::string(cache_path) + ".tmp";
        FILE* wfp = std::fopen(tmp_p.c_str(), "wb");
        if (wfp) {
            uint64_t hdr[4] = {0x4E47444C59434831ULL, static_cast<uint64_t>(N), static_cast<uint64_t>(dev_kind), topo_sig};
            std::fwrite(hdr, sizeof(uint64_t), 4, wfp);
            std::fwrite(g_state.setup_ns.data(), sizeof(float), N, wfp);
            std::fwrite(g_state.hold_ns.data(), sizeof(float), N, wfp);
            std::fclose(wfp);
            std::remove(cache_path);
            std::rename(tmp_p.c_str(), cache_path);
        }
    }
    std::fprintf(stdout, "[ownpr-delays] Populated RRG delays: nonzero=%lld/%d avg=%.4fns max=%.4fns\n",
                 static_cast<long long>(nonzero_cnt), N,
                 nonzero_cnt > 0 ? (sum_s / static_cast<double>(nonzero_cnt)) : 0.0, max_s);
    std::fflush(stdout);
    return 0;
}

EXPORT void bridge_reset_route_owners() {
    std::fill(g_state.route_owner.begin(), g_state.route_owner.end(), 0ULL);
}

EXPORT void bridge_set_route_owner(int32_t node_id, uint64_t net_ptr) {
    if (node_id >= 0 && node_id < static_cast<int32_t>(g_state.route_owner.size())) {
        g_state.route_owner[node_id] = net_ptr;
    }
}

EXPORT int32_t bridge_bfs_tree_to_sink(
    const int32_t* tree_ids,
    int32_t num_tree,
    int32_t dst_id,
    uint64_t net_ptr,
    int32_t max_hops,
    int32_t* out_ids,
    int32_t max_out
) {
    int32_t N = static_cast<int32_t>(g_state.id_to_ptr.size());
    if (dst_id < 0 || dst_id >= N || num_tree <= 0) return 0;
    int32_t st = ++g_state.cur_stamp;
    if (st >= 0x7ffffff0) {
        std::fill(g_state.bfs_stamp.begin(), g_state.bfs_stamp.end(), 0);
        st = g_state.cur_stamp = 1;
    }
    struct QItem { int32_t u; int32_t d; };
    std::vector<QItem> q;
    q.reserve(4096);
    for (int32_t i = 0; i < num_tree; ++i) {
        int32_t s = tree_ids[i];
        if (s < 0 || s >= N) continue;
        uint64_t ow = g_state.route_owner[s];
        if (ow != 0ULL && ow != net_ptr) continue;
        if (s == dst_id) {
            if (max_out >= 1) out_ids[0] = s;
            return 1;
        }
        if (g_state.bfs_stamp[s] != st) {
            g_state.bfs_stamp[s] = st;
            g_state.bfs_parent[s] = -1;
            q.push_back({s, 0});
        }
    }
    size_t head = 0;
    bool found = false;
    while (head < q.size()) {
        QItem cur = q[head++];
        if (max_hops > 0 && cur.d >= max_hops) continue;
        int32_t beg = g_state.row_ptr[cur.u];
        int32_t end = g_state.row_ptr[cur.u + 1];
        for (int32_t e = beg; e < end; ++e) {
            int32_t v = g_state.col_idx[e];
            if (g_state.bfs_stamp[v] == st) continue;
            uint64_t ow = g_state.route_owner[v];
            if (ow != 0ULL && ow != net_ptr) continue;
            g_state.bfs_stamp[v] = st;
            g_state.bfs_parent[v] = cur.u;
            if (v == dst_id) {
                found = true;
                break;
            }
            q.push_back({v, cur.d + 1});
        }
        if (found) break;
    }
    if (!found) return 0;
    std::vector<int32_t> path;
    for (int32_t x = dst_id; x != -1; x = g_state.bfs_parent[x]) {
        path.push_back(x);
    }
    std::reverse(path.begin(), path.end());
    int32_t len = static_cast<int32_t>(path.size());
    int32_t ncopy = std::min(len, max_out);
    for (int32_t i = 0; i < ncopy; ++i) out_ids[i] = path[i];
    return len;
}

EXPORT int32_t bridge_num_edges() {
    return static_cast<int32_t>(g_state.col_idx.size());
}

EXPORT int32_t bridge_ptr_to_id(uint64_t node_ptr) {
    if (!node_ptr) return -1;
    return g_state.ptr_to_id.find_val(node_ptr);
}

EXPORT int32_t bridge_register_or_find_ptr(uint64_t node_ptr) {
    if (!node_ptr) return -1;
    int32_t id = g_state.ptr_to_id.find_val(node_ptr);
    if (id >= 0) return id;
    bridge_ensure_extra_nodes(&node_ptr, 1);
    return g_state.ptr_to_id.find_val(node_ptr);
}

EXPORT uint64_t bridge_id_to_ptr(int32_t node_id) {
    if (node_id < 0 || node_id >= static_cast<int32_t>(g_state.id_to_ptr.size())) return 0;
    return g_state.id_to_ptr[node_id];
}

EXPORT int32_t bridge_coord_to_id(int16_t r, int16_t c, int16_t idx) {
    return g_state.coord_to_id.find_val(pack_coord(r, c, idx));
}

EXPORT void bridge_id_coord(int32_t node_id, int16_t* out_rc_idx) {
    if (node_id < 0 || node_id >= static_cast<int32_t>(g_state.meta.size())) {
        out_rc_idx[0] = -999; out_rc_idx[1] = -999; out_rc_idx[2] = -999;
        return;
    }
    out_rc_idx[0] = g_state.meta[node_id].r;
    out_rc_idx[1] = g_state.meta[node_id].c;
    out_rc_idx[2] = g_state.meta[node_id].idx;
}

EXPORT int32_t bridge_save_rrg_bin(uint64_t tm_lib, int32_t dev_kind, const char* path) {
    if (bridge_compute_delays(tm_lib, dev_kind) != 0) return -1;
    int32_t N = static_cast<int32_t>(g_state.id_to_ptr.size());
    int32_t M = static_cast<int32_t>(g_state.col_idx.size());
    FILE* fp = std::fopen(path, "wb");
    if (!fp) return -2;
    uint32_t magic = 0x52524731U;
    std::fwrite(&magic, sizeof(uint32_t), 1, fp);
    std::fwrite(&N, sizeof(int32_t), 1, fp);
    std::fwrite(&M, sizeof(int32_t), 1, fp);
    std::fwrite(g_state.meta.data(), sizeof(NodeMeta), N, fp);
    std::fwrite(g_state.setup_ns.data(), sizeof(float), N, fp);
    std::fwrite(g_state.hold_ns.data(), sizeof(float), N, fp);
    std::fwrite(g_state.row_ptr.data(), sizeof(int32_t), N + 1, fp);
    std::fwrite(g_state.col_idx.data(), sizeof(int32_t), M, fp);
    std::fclose(fp);
    return 0;
}

EXPORT int32_t bridge_bfs_path(int32_t src_id, int32_t dst_id, int32_t max_hops, int32_t* out_ids, int32_t max_out) {
    int32_t N = static_cast<int32_t>(g_state.id_to_ptr.size());
    if (src_id < 0 || src_id >= N || dst_id < 0 || dst_id >= N) return 0;
    if (src_id == dst_id) {
        if (max_out >= 1) out_ids[0] = src_id;
        return 1;
    }
    int32_t st = ++g_state.cur_stamp;
    if (st >= 0x7ffffff0) {
        std::fill(g_state.bfs_stamp.begin(), g_state.bfs_stamp.end(), 0);
        st = g_state.cur_stamp = 1;
    }
    struct QItem { int32_t u; int32_t d; };
    std::vector<QItem> q;
    q.reserve(1024);
    g_state.bfs_stamp[src_id] = st;
    g_state.bfs_parent[src_id] = -1;
    q.push_back({src_id, 0});
    size_t head = 0;
    bool found = false;
    while (head < q.size()) {
        QItem cur = q[head++];
        if (max_hops > 0 && cur.d >= max_hops) continue;
        int32_t beg = g_state.row_ptr[cur.u];
        int32_t end = g_state.row_ptr[cur.u + 1];
        for (int32_t e = beg; e < end; ++e) {
            int32_t v = g_state.col_idx[e];
            if (g_state.bfs_stamp[v] != st) {
                g_state.bfs_stamp[v] = st;
                g_state.bfs_parent[v] = cur.u;
                if (v == dst_id) {
                    found = true;
                    break;
                }
                q.push_back({v, cur.d + 1});
            }
        }
        if (found) break;
    }
    if (!found) return 0;
    std::vector<int32_t> path;
    for (int32_t cur = dst_id; cur != -1; cur = g_state.bfs_parent[cur]) {
        path.push_back(cur);
    }
    std::reverse(path.begin(), path.end());
    int32_t len = static_cast<int32_t>(path.size());
    int32_t ncopy = std::min(len, max_out);
    for (int32_t i = 0; i < ncopy; ++i) out_ids[i] = path[i];
    return len;
}

struct MsvcPtrVec {
    uint64_t* first = nullptr;
    uint64_t* last = nullptr;
    uint64_t* end = nullptr;
};

EXPORT int32_t bridge_batch_query_pins(
    uint64_t mem_addr,
    uint64_t getnodes_sta_addr,
    uint64_t getnodes_addr,
    const uint64_t* pin_ptrs,
    int32_t num_pins,
    int32_t* out_node_ids,
    uint64_t* out_net_ptrs
) {
    auto get_sta = reinterpret_cast<GetNodesPinFn>(getnodes_sta_addr);
    auto get_gn  = reinterpret_cast<GetNodesPinFn>(getnodes_addr);
    void* mem = reinterpret_cast<void*>(mem_addr);

    MsvcPtrVec vec{nullptr, nullptr, nullptr};
    for (int32_t i = 0; i < num_pins; ++i) {
        uint64_t pin = pin_ptrs[i];
        if (!pin) {
            out_node_ids[i] = -1;
            out_net_ptrs[i] = 0;
            continue;
        }
        out_net_ptrs[i] = rd_u64(pin + 8ULL);
        uint64_t chosen_ptr = 0;
        if (get_sta) {
            vec.last = vec.first;
            get_sta(reinterpret_cast<void*>(pin), &vec, mem);
            if (vec.last > vec.first && vec.first[0] != 0) chosen_ptr = vec.first[0];
        }
        if (!chosen_ptr && get_gn) {
            vec.last = vec.first;
            get_gn(reinterpret_cast<void*>(pin), &vec, mem);
            if (vec.last > vec.first && vec.first[0] != 0) chosen_ptr = vec.first[0];
        }
        if (!chosen_ptr) chosen_ptr = rd_u64(pin + 0x18ULL);
        out_node_ids[i] = chosen_ptr ? bridge_register_or_find_ptr(chosen_ptr) : -1;
    }
    return 0;
}

EXPORT int32_t bridge_batch_query_pins_all(
    uint64_t mem_addr,
    uint64_t getnodes_sta_addr,
    uint64_t getnodes_addr,
    const uint64_t* pin_ptrs,
    int32_t num_pins,
    int32_t* out_offsets,
    int32_t* out_node_ids,
    int32_t max_nodes,
    uint64_t* out_net_ptrs
) {
    auto get_sta = reinterpret_cast<GetNodesPinFn>(getnodes_sta_addr);
    auto get_gn  = reinterpret_cast<GetNodesPinFn>(getnodes_addr);
    void* mem = reinterpret_cast<void*>(mem_addr);

    MsvcPtrVec vec{nullptr, nullptr, nullptr};
    int32_t cursor = 0;
    for (int32_t i = 0; i < num_pins; ++i) {
        out_offsets[i] = cursor;
        uint64_t pin = pin_ptrs[i];
        if (!pin) {
            out_net_ptrs[i] = 0;
            continue;
        }
        out_net_ptrs[i] = rd_u64(pin + 8ULL);
        bool got = false;
        if (get_sta) {
            vec.last = vec.first;
            get_sta(reinterpret_cast<void*>(pin), &vec, mem);
            for (uint64_t* p = vec.first; p < vec.last; ++p) {
                if (*p) {
                    int32_t id = bridge_register_or_find_ptr(*p);
                    if (id >= 0) {
                        if (cursor < max_nodes) out_node_ids[cursor] = id;
                        ++cursor;
                        got = true;
                    }
                }
            }
        }
        if (!got && get_gn) {
            vec.last = vec.first;
            get_gn(reinterpret_cast<void*>(pin), &vec, mem);
            for (uint64_t* p = vec.first; p < vec.last; ++p) {
                if (*p) {
                    int32_t id = bridge_register_or_find_ptr(*p);
                    if (id >= 0) {
                        if (cursor < max_nodes) out_node_ids[cursor] = id;
                        ++cursor;
                        got = true;
                    }
                }
            }
        }
        if (!got) {
            uint64_t p18 = rd_u64(pin + 0x18ULL);
            if (p18) {
                int32_t id = bridge_register_or_find_ptr(p18);
                if (id >= 0) {
                    if (cursor < max_nodes) out_node_ids[cursor] = id;
                    ++cursor;
                }
            }
        }
    }
    out_offsets[num_pins] = cursor;
    return cursor;
}

// ============================================================================
// P2R Carry-In (P2R+0x00: map<Net*, list<node*>>) & Carry-Out (P2R+0x10: map<Net*, node*>)
// ============================================================================
namespace {

uint64_t build_rb_subtree(const std::vector<uint64_t>& nodes, int lo, int hi, uint64_t parent_addr, uint64_t head_addr) {
    if (lo > hi) return head_addr;
    int mid = lo + (hi - lo) / 2;
    uint64_t cur = nodes[mid];
    uint64_t left = build_rb_subtree(nodes, lo, mid - 1, cur, head_addr);
    uint64_t right = build_rb_subtree(nodes, mid + 1, hi, cur, head_addr);
    wr_u64(cur + 0x00ULL, left);
    wr_u64(cur + 0x08ULL, parent_addr);
    wr_u64(cur + 0x10ULL, right);
    wr_u8(cur + 0x18ULL, 1U); // Black
    wr_u8(cur + 0x19ULL, 0U); // _Isnil = false
    return cur;
}

void install_rb_tree(uint64_t map_addr, const std::vector<uint64_t>& sorted_nodes) {
    uint64_t head = rd_u64(map_addr + 0x00ULL);
    if (!head) return;
    if (sorted_nodes.empty()) {
        wr_u64(head + 0x00ULL, head);
        wr_u64(head + 0x08ULL, head);
        wr_u64(head + 0x10ULL, head);
        wr_u64(map_addr + 0x08ULL, 0ULL);
        return;
    }
    uint64_t root = build_rb_subtree(sorted_nodes, 0, static_cast<int>(sorted_nodes.size()) - 1, head, head);
    wr_u64(head + 0x00ULL, sorted_nodes.front());
    wr_u64(head + 0x08ULL, root);
    wr_u64(head + 0x10ULL, sorted_nodes.back());
    wr_u64(map_addr + 0x08ULL, static_cast<uint64_t>(sorted_nodes.size()));
}

} // namespace

EXPORT int32_t bridge_populate_p2r_carries(
    uint64_t p2r_addr,
    const uint64_t* cin_nets,
    const uint64_t* cin_b_ptrs,
    const uint64_t* cin_d_ptrs,
    int32_t num_cin,
    const uint64_t* cout_nets,
    const uint64_t* cout_ptrs,
    int32_t num_cout
) {
    if (!p2r_addr) return -1;

    // 1. P2R + 0x00: std::map<Net*, std::list<node*>>
    struct CinItem { uint64_t net; uint64_t b_ptr; uint64_t d_ptr; };
    std::vector<CinItem> cins;
    for (int32_t i = 0; i < num_cin; ++i) {
        if (cin_nets[i]) cins.push_back({cin_nets[i], cin_b_ptrs[i], cin_d_ptrs[i]});
    }
    std::sort(cins.begin(), cins.end(), [](const CinItem& a, const CinItem& b) { return a.net < b.net; });
    std::vector<uint64_t> cin_rb_nodes;
    for (size_t i = 0; i < cins.size(); ) {
        size_t j = i;
        std::vector<uint64_t> vals;
        while (j < cins.size() && cins[j].net == cins[i].net) {
            vals.push_back(cins[j].b_ptr);
            vals.push_back(cins[j].d_ptr);
            ++j;
        }
        // Build circular doubly-linked std::list<node*> (sentinel head + K elements, 0x18 bytes each)
        uint64_t lhead = reinterpret_cast<uint64_t>(std::calloc(1, 0x18));
        wr_u64(lhead + 0x00ULL, lhead);
        wr_u64(lhead + 0x08ULL, lhead);
        for (uint64_t vp : vals) {
            uint64_t ln = reinterpret_cast<uint64_t>(std::calloc(1, 0x18));
            uint64_t prev = rd_u64(lhead + 0x08ULL);
            wr_u64(ln + 0x00ULL, lhead);
            wr_u64(ln + 0x08ULL, prev);
            wr_u64(ln + 0x10ULL, vp);
            wr_u64(prev + 0x00ULL, ln);
            wr_u64(lhead + 0x08ULL, ln);
        }
        uint64_t rb = reinterpret_cast<uint64_t>(std::calloc(1, 0x40));
        wr_u64(rb + 0x20ULL, cins[i].net);
        wr_u64(rb + 0x28ULL, lhead);
        wr_u64(rb + 0x30ULL, static_cast<uint64_t>(vals.size()));
        cin_rb_nodes.push_back(rb);
        i = j;
    }
    install_rb_tree(p2r_addr + 0x00ULL, cin_rb_nodes);

    // 2. P2R + 0x10: std::map<Net*, node*>
    struct CoutItem { uint64_t net; uint64_t nd_ptr; };
    std::vector<CoutItem> couts;
    for (int32_t i = 0; i < num_cout; ++i) {
        if (cout_nets[i]) couts.push_back({cout_nets[i], cout_ptrs[i]});
    }
    std::sort(couts.begin(), couts.end(), [](const CoutItem& a, const CoutItem& b) { return a.net < b.net; });
    std::vector<uint64_t> cout_rb_nodes;
    for (size_t i = 0; i < couts.size(); ++i) {
        if (i > 0 && couts[i].net == couts[i - 1].net) continue;
        uint64_t rb = reinterpret_cast<uint64_t>(std::calloc(1, 0x38));
        wr_u64(rb + 0x20ULL, couts[i].net);
        wr_u64(rb + 0x28ULL, couts[i].nd_ptr);
        cout_rb_nodes.push_back(rb);
    }
    install_rb_tree(p2r_addr + 0x10ULL, cout_rb_nodes);
    return 0;
}

// ============================================================================
// Timing-Driven Analytical Global Placer + Legalizer + Swapper
// ============================================================================
// Unit kinds:
//   0 = FIXED (fixed at init_r, init_c)
//   1 = BSRAM (1 site from bsram_rc)
//   2 = DSP_SINGLE (1 site from dsp_rc, slot 0 or 1 if sub==187 MULT12X12)
//   3 = DSP_WIDE (MULT27X36: requires dsp_wide_ok[s] == 1, reserves (r,c) and (r,c+3))
//   4 = ALU_CHAIN (alu_len[u] >= 1, cin_fab = (unit_flags[u]&1), cout_fab = (unit_flags[u]&2))
//   5 = MUX_TREE (1 dedicated type-17 CFU)
//   6 = CFU_SLOT_FF (1 CFU slot with FF control-set unit_cs[u] >= 0)
//   7 = CFU_SLOT_LUT (1 CFU slot with no FF, unit_cs[u] == -1)

namespace {

inline int dom_of_slot(int s) {
    if (s < 2) return 0;
    if (s < 4) return 1;
    return 2;
}

struct PlacedCfuState {
    uint8_t valid = 0;     // 0=unavailable/reserved, 1=basic CFU (221..228), 2=type-17 CFU
    uint8_t exclusive = 0; // 1 if reserved by ALU_CHAIN, MUX2_LUT7, DSP, or BSRAM
    uint8_t used_cnt = 0;
    uint8_t c60_cnt = 0;   // Active distinct fabric nets on shared CFU IMUX idx 60..63 (hard max 4, soft max 3)
    uint8_t mux5_cnt = 0;  // Active MUX2_LUT5 primitives in this CFU (restricted S0 parents 60..63 + 171,179,203,211)
    uint8_t lut_cnt = 0;   // Active 4-input LUTs in this CFU (4 input pins each vs 1 D pin for FF-only)
    uint8_t slot_used[8] = {0,0,0,0,0,0,0,0};
    int32_t dom_cs[3] = {-1, -1, -1};
    int32_t slot_unit[8] = {-1,-1,-1,-1,-1,-1,-1,-1};
    int32_t c60_nets[4] = {-1, -1, -1, -1};
    int16_t c60_refs[4] = {0, 0, 0, 0};
};

std::vector<PlacedCfuState> g_last_cfus;

inline void cfu_unit_c60_pair(const int32_t* unit_c60, int32_t u, int32_t& c0, int32_t& c1) {
    if (!unit_c60) { c0 = -1; c1 = -1; return; }
    c0 = unit_c60[2 * u + 0];
    c1 = unit_c60[2 * u + 1];
    if (c1 == c0) c1 = -1;
    if (c0 < 0 && c1 >= 0) { c0 = c1; c1 = -1; }
}

inline int cfu_c60_after_add(const PlacedCfuState& st, int32_t c0, int32_t c1) {
    int tot = st.c60_cnt;
    if (c0 >= 0) {
        if (st.c60_nets[0] != c0 && st.c60_nets[1] != c0 && st.c60_nets[2] != c0 && st.c60_nets[3] != c0) ++tot;
    }
    if (c1 >= 0 && c1 != c0) {
        if (st.c60_nets[0] != c1 && st.c60_nets[1] != c1 && st.c60_nets[2] != c1 && st.c60_nets[3] != c1) ++tot;
    }
    return tot;
}

inline void cfu_c60_add(PlacedCfuState& st, const int32_t* unit_c60, int32_t u) {
    int32_t c0, c1;
    cfu_unit_c60_pair(unit_c60, u, c0, c1);
    const int32_t arr[2] = {c0, c1};
    for (int i = 0; i < 2; ++i) {
        int32_t nid = arr[i];
        if (nid < 0) continue;
        int found = -1, free_k = -1;
        for (int k = 0; k < 4; ++k) {
            if (st.c60_nets[k] == nid) { found = k; break; }
            if (free_k < 0 && st.c60_nets[k] < 0) free_k = k;
        }
        if (found >= 0) {
            ++st.c60_refs[found];
        } else if (free_k >= 0) {
            st.c60_nets[free_k] = nid;
            st.c60_refs[free_k] = 1;
            ++st.c60_cnt;
        }
    }
}

inline void cfu_c60_rem(PlacedCfuState& st, const int32_t* unit_c60, int32_t u) {
    int32_t c0, c1;
    cfu_unit_c60_pair(unit_c60, u, c0, c1);
    const int32_t arr[2] = {c0, c1};
    for (int i = 0; i < 2; ++i) {
        int32_t nid = arr[i];
        if (nid < 0) continue;
        for (int k = 0; k < 4; ++k) {
            if (st.c60_nets[k] == nid) {
                if (--st.c60_refs[k] <= 0) {
                    st.c60_refs[k] = 0;
                    st.c60_nets[k] = -1;
                    --st.c60_cnt;
                }
                break;
            }
        }
    }
}

struct SpreadBox {
    int r0, r1, c0, c1;
};

void spread_cfu_recursive(
    std::vector<int32_t>& idxs,
    int begin_i,
    int end_i,
    SpreadBox box,
    int depth,
    const uint8_t* cfu_mask,
    float cap_frac,
    const std::vector<float>& demand,
    const std::vector<float>& cur_r,
    const std::vector<float>& cur_c,
    std::vector<float>& tgt_r,
    std::vector<float>& tgt_c
) {
    if (begin_i >= end_i) return;
    int dr = box.r1 - box.r0;
    int dc = box.c1 - box.c0;
    if (depth >= 8 || (dr <= 4 && dc <= 4) || (end_i - begin_i) <= 4) {
        std::vector<std::pair<int, int>> tiles;
        for (int r = box.r0; r <= box.r1; ++r) {
            for (int c = box.c0; c <= box.c1; ++c) {
                if (cfu_mask[r * 184 + c]) tiles.push_back({r, c});
            }
        }
        if (tiles.empty()) {
            float mr = 0.5f * (box.r0 + box.r1);
            float mc = 0.5f * (box.c0 + box.c1);
            for (int i = begin_i; i < end_i; ++i) {
                tgt_r[idxs[i]] = mr;
                tgt_c[idxs[i]] = mc;
            }
            return;
        }
        float tot_leaf_dem = 0.0f, sum_r = 0.0f, sum_c = 0.0f;
        int cnt = end_i - begin_i;
        for (int i = begin_i; i < end_i; ++i) {
            tot_leaf_dem += demand[idxs[i]];
            sum_r += cur_r[idxs[i]];
            sum_c += cur_c[idxs[i]];
        }
        float per_tile_cap = std::clamp(8.0f * cap_frac * 0.85f, 3.5f, 5.5f);
        if (tot_leaf_dem <= per_tile_cap * static_cast<float>(tiles.size()) * 0.75f) {
            // Box is underfilled: cluster around units' natural centroid up to per_tile_cap per tile!
            float cr = sum_r / static_cast<float>(cnt);
            float cc = sum_c / static_cast<float>(cnt);
            std::sort(tiles.begin(), tiles.end(), [&](const auto& a, const auto& b) {
                float da = std::abs(a.first - cr) + std::abs(a.second - cc);
                float db = std::abs(b.first - cr) + std::abs(b.second - cc);
                return da < db;
            });
            std::sort(idxs.begin() + begin_i, idxs.begin() + end_i, [&](int32_t a, int32_t b) {
                float da = std::abs(cur_r[a] - cr) + std::abs(cur_c[a] - cc);
                float db = std::abs(cur_r[b] - cr) + std::abs(cur_c[b] - cc);
                return da < db;
            });
            int ti = 0;
            float used_t = 0.0f;
            int nt = static_cast<int>(tiles.size());
            for (int k = 0; k < cnt; ++k) {
                int32_t u = idxs[begin_i + k];
                if (used_t > 0.0f && used_t + demand[u] > per_tile_cap && ti + 1 < nt) {
                    ++ti;
                    used_t = 0.0f;
                }
                tgt_r[u] = static_cast<float>(tiles[ti].first);
                tgt_c[u] = static_cast<float>(tiles[ti].second);
                used_t += demand[u];
            }
            return;
        }
        // Dense box: sort units and tiles along major axis
        bool split_c = (dc >= dr);
        std::sort(idxs.begin() + begin_i, idxs.begin() + end_i, [&](int32_t a, int32_t b) {
            return split_c ? (cur_c[a] < cur_c[b]) : (cur_r[a] < cur_r[b]);
        });
        std::sort(tiles.begin(), tiles.end(), [&](const auto& a, const auto& b) {
            return split_c ? (a.second != b.second ? a.second < b.second : a.first < b.first)
                           : (a.first != b.first ? a.first < b.first : a.second < b.second);
        });
        int nt = static_cast<int>(tiles.size());
        for (int k = 0; k < cnt; ++k) {
            int ti = static_cast<int>((static_cast<int64_t>(k) * nt) / std::max(1, cnt));
            if (ti >= nt) ti = nt - 1;
            tgt_r[idxs[begin_i + k]] = static_cast<float>(tiles[ti].first);
            tgt_c[idxs[begin_i + k]] = static_cast<float>(tiles[ti].second);
        }
        return;
    }

    bool split_col = (dc >= dr);
    if (split_col && dc >= 2) {
        std::vector<int> col_cap(dc + 1, 0);
        int tot_cap = 0;
        for (int c = box.c0; c <= box.c1; ++c) {
            int cc = 0;
            for (int r = box.r0; r <= box.r1; ++r) cc += cfu_mask[r * 184 + c] ? 8 : 0;
            col_cap[c - box.c0] = cc;
            tot_cap += cc;
        }
        int half_cap = tot_cap / 2;
        int acc = 0, cut_c = (box.c0 + box.c1) / 2;
        for (int c = box.c0; c < box.c1; ++c) {
            acc += col_cap[c - box.c0];
            if (acc >= half_cap) { cut_c = c; break; }
        }
        int left_cap = 0;
        for (int c = box.c0; c <= cut_c; ++c) left_cap += col_cap[c - box.c0];

        std::sort(idxs.begin() + begin_i, idxs.begin() + end_i, [&](int32_t a, int32_t b) {
            return cur_c[a] < cur_c[b];
        });
        float tot_dem = 0.0f, natural_left_dem = 0.0f;
        float cut_f = static_cast<float>(cut_c) + 0.5f;
        for (int i = begin_i; i < end_i; ++i) {
            float d = demand[idxs[i]];
            tot_dem += d;
            if (cur_c[idxs[i]] <= cut_f) natural_left_dem += d;
        }
        float max_left = cap_frac * static_cast<float>(left_cap);
        float max_right = cap_frac * static_cast<float>(tot_cap - left_cap);
        float min_left = std::max(0.0f, tot_dem - max_right);
        float target_left_dem;
        if (min_left >= max_left) {
            target_left_dem = (tot_cap > 0) ? (tot_dem * static_cast<float>(left_cap) / static_cast<float>(tot_cap)) : (0.5f * tot_dem);
        } else {
            target_left_dem = std::clamp(natural_left_dem, min_left, max_left);
        }

        float run_dem = 0.0f;
        int mid_i = begin_i;
        while (mid_i < end_i && run_dem + 0.5f * demand[idxs[mid_i]] < target_left_dem) {
            run_dem += demand[idxs[mid_i]];
            ++mid_i;
        }
        SpreadBox bL{box.r0, box.r1, box.c0, cut_c};
        SpreadBox bR{box.r0, box.r1, cut_c + 1, box.c1};
        spread_cfu_recursive(idxs, begin_i, mid_i, bL, depth + 1, cfu_mask, cap_frac, demand, cur_r, cur_c, tgt_r, tgt_c);
        spread_cfu_recursive(idxs, mid_i, end_i, bR, depth + 1, cfu_mask, cap_frac, demand, cur_r, cur_c, tgt_r, tgt_c);
    } else {
        std::vector<int> row_cap(dr + 1, 0);
        int tot_cap = 0;
        for (int r = box.r0; r <= box.r1; ++r) {
            int rc = 0;
            for (int c = box.c0; c <= box.c1; ++c) rc += cfu_mask[r * 184 + c] ? 8 : 0;
            row_cap[r - box.r0] = rc;
            tot_cap += rc;
        }
        int half_cap = tot_cap / 2;
        int acc = 0, cut_r = (box.r0 + box.r1) / 2;
        for (int r = box.r0; r < box.r1; ++r) {
            acc += row_cap[r - box.r0];
            if (acc >= half_cap) { cut_r = r; break; }
        }
        int top_cap = 0;
        for (int r = box.r0; r <= cut_r; ++r) top_cap += row_cap[r - box.r0];

        std::sort(idxs.begin() + begin_i, idxs.begin() + end_i, [&](int32_t a, int32_t b) {
            return cur_r[a] < cur_r[b];
        });
        float tot_dem = 0.0f, natural_top_dem = 0.0f;
        float cut_f = static_cast<float>(cut_r) + 0.5f;
        for (int i = begin_i; i < end_i; ++i) {
            float d = demand[idxs[i]];
            tot_dem += d;
            if (cur_r[idxs[i]] <= cut_f) natural_top_dem += d;
        }
        float max_top = cap_frac * static_cast<float>(top_cap);
        float max_bot = cap_frac * static_cast<float>(tot_cap - top_cap);
        float min_top = std::max(0.0f, tot_dem - max_bot);
        float target_top_dem;
        if (min_top >= max_top) {
            target_top_dem = (tot_cap > 0) ? (tot_dem * static_cast<float>(top_cap) / static_cast<float>(tot_cap)) : (0.5f * tot_dem);
        } else {
            target_top_dem = std::clamp(natural_top_dem, min_top, max_top);
        }

        float run_dem = 0.0f;
        int mid_i = begin_i;
        while (mid_i < end_i && run_dem + 0.5f * demand[idxs[mid_i]] < target_top_dem) {
            run_dem += demand[idxs[mid_i]];
            ++mid_i;
        }
        SpreadBox bT{box.r0, cut_r, box.c0, box.c1};
        SpreadBox bB{cut_r + 1, box.r1, box.c0, box.c1};
        spread_cfu_recursive(idxs, begin_i, mid_i, bT, depth + 1, cfu_mask, cap_frac, demand, cur_r, cur_c, tgt_r, tgt_c);
        spread_cfu_recursive(idxs, mid_i, end_i, bB, depth + 1, cfu_mask, cap_frac, demand, cur_r, cur_c, tgt_r, tgt_c);
    }
}

} // namespace

EXPORT int32_t bridge_place_design(
    const uint8_t* cfu_mask,        // 111 * 184
    const int16_t* bsram_rc,        // 2 * num_bsram_sites
    int32_t num_bsram_sites,
    const int16_t* dsp_rc,          // 2 * num_dsp_sites
    const uint8_t* dsp_wide_ok,     // num_dsp_sites
    int32_t num_dsp_sites,
    int32_t num_units,
    const int32_t* unit_kind,
    const int32_t* unit_sub,
    const int32_t* unit_cs,
    const int32_t* unit_c60,        // 2 * num_units: active idx 60..63 control/mux5-select net IDs (-1 if none)
    const int32_t* unit_chain_len,
    const int32_t* unit_flags,      // bit0=cin_fab, bit1=cout_fab
    const float* init_r,
    const float* init_c,
    int32_t num_pnets,
    const int32_t* pnet_offsets,
    const int32_t* pnet_units,
    const float* pnet_weights,
    int16_t* out_r,
    int16_t* out_c,
    int16_t* out_slot
) {
    if (num_units <= 0) return 0;

    std::vector<float> r_pos(num_units, 55.0f);
    std::vector<float> c_pos(num_units, 92.0f);
    std::vector<float> r_tgt(num_units, 55.0f);
    std::vector<float> c_tgt(num_units, 92.0f);
    std::vector<float> demand(num_units, 1.0f);
    std::vector<float> unit_crit_sum(num_units, 0.0f);

    std::vector<int32_t> cfu_units;
    std::vector<int32_t> bsram_units;
    std::vector<int32_t> dsp_single_units;
    std::vector<int32_t> dsp_wide_units;

    for (int32_t u = 0; u < num_units; ++u) {
        if (unit_kind[u] == 0) {
            r_pos[u] = r_tgt[u] = init_r[u];
            c_pos[u] = c_tgt[u] = init_c[u];
            demand[u] = 0.0f;
        } else {
            // Deterministic tiny spread around center initially so symmetrical cones break ties smoothly
            r_pos[u] = 55.0f + 12.0f * std::sin(0.0173f * static_cast<float>(u + 1));
            c_pos[u] = 92.0f + 22.0f * std::cos(0.0131f * static_cast<float>(u + 1));
            r_tgt[u] = r_pos[u];
            c_tgt[u] = c_pos[u];
            if (unit_kind[u] == 1) {
                bsram_units.push_back(u);
                demand[u] = 0.0f;
            } else if (unit_kind[u] == 2) {
                dsp_single_units.push_back(u);
                demand[u] = 0.0f;
            } else if (unit_kind[u] == 3) {
                dsp_wide_units.push_back(u);
                demand[u] = 0.0f;
            } else if (unit_kind[u] == 4) {
                int L = std::max(1, unit_chain_len[u]);
                int n_cfus = (L + 5) / 6 + ((unit_flags[u] & 1) ? 1 : 0) + (((unit_flags[u] & 2) && (L % 6 == 0)) ? 1 : 0);
                demand[u] = static_cast<float>(8 * n_cfus);
                cfu_units.push_back(u);
            } else if (unit_kind[u] == 5) {
                demand[u] = (unit_sub[u] == 5) ? 2.0f : ((unit_sub[u] == 6) ? 4.0f : 8.0f);
                cfu_units.push_back(u);
            } else {
                demand[u] = 1.0f;
                cfu_units.push_back(u);
            }
        }
    }

    // Build CSR of unit -> incident nets for fast legalization & detailed swaps
    std::vector<int32_t> u_deg(num_units, 0);
    for (int32_t e = 0; e < num_pnets; ++e) {
        int32_t b = pnet_offsets[e], en = pnet_offsets[e + 1];
        int32_t deg = en - b;
        if (deg < 2) continue;
        float w = pnet_weights[e];
        for (int32_t p = b; p < en; ++p) {
            int32_t u = pnet_units[p];
            if (u >= 0 && u < num_units) {
                ++u_deg[u];
                if (deg <= 64 || p == b) unit_crit_sum[u] += w;
            }
        }
    }
    std::vector<int32_t> u_net_off(num_units + 1, 0);
    for (int32_t u = 0; u < num_units; ++u) u_net_off[u + 1] = u_net_off[u] + u_deg[u];
    std::vector<int32_t> u_nets(u_net_off[num_units], 0);
    std::fill(u_deg.begin(), u_deg.end(), 0);
    for (int32_t e = 0; e < num_pnets; ++e) {
        int32_t b = pnet_offsets[e], en = pnet_offsets[e + 1];
        if (en - b < 2) continue;
        for (int32_t p = b; p < en; ++p) {
            int32_t u = pnet_units[p];
            if (u >= 0 && u < num_units) {
                u_nets[u_net_off[u] + u_deg[u]++] = e;
            }
        }
    }

    // Phase 1: Topological Multi-Source BFS Seed + Analytical Global Placement + SimPL Lookahead Spreading
    {
        float io_sr = 0.0f, io_sc = 0.0f;
        int io_cnt = 0;
        std::vector<int32_t> wave_dist(num_units, -1);
        std::vector<int32_t> cur_wave;
        cur_wave.reserve(1024);
        for (int32_t u = 0; u < num_units; ++u) {
            if (unit_kind[u] == 0) {
                wave_dist[u] = 0;
                cur_wave.push_back(u);
                io_sr += r_pos[u];
                io_sc += c_pos[u];
                ++io_cnt;
            }
        }
        const float cent_r = (io_cnt > 0) ? std::clamp(io_sr / static_cast<float>(io_cnt), 15.0f, 95.0f) : 55.0f;
        const float cent_c = (io_cnt > 0) ? std::clamp(io_sc / static_cast<float>(io_cnt), 25.0f, 158.0f) : 92.0f;

        std::vector<float> bfs_nr(num_units, 0.0f), bfs_nc(num_units, 0.0f), bfs_dw(num_units, 0.0f);
        std::vector<uint8_t> in_next(num_units, 0);
        std::vector<int32_t> next_wave;
        next_wave.reserve(8192);

        int dist = 0;
        while (!cur_wave.empty()) {
            next_wave.clear();
            for (int32_t u : cur_wave) {
                float ur = r_pos[u], uc = c_pos[u];
                for (int32_t idx = u_net_off[u]; idx < u_net_off[u + 1]; ++idx) {
                    int32_t e = u_nets[idx];
                    int32_t b = pnet_offsets[e], en = pnet_offsets[e + 1];
                    int32_t deg = en - b;
                    if (deg < 2 || deg > 64) continue;
                    float wt = pnet_weights[e] / static_cast<float>(deg - 1);
                    for (int32_t p = b; p < en; ++p) {
                        int32_t w = pnet_units[p];
                        if (w < 0 || w >= num_units || wave_dist[w] >= 0) continue;
                        if (!in_next[w]) {
                            in_next[w] = 1;
                            next_wave.push_back(w);
                        }
                        bfs_nr[w] += wt * ur;
                        bfs_nc[w] += wt * uc;
                        bfs_dw[w] += wt;
                    }
                }
            }
            ++dist;
            for (int32_t w : next_wave) {
                in_next[w] = 0;
                wave_dist[w] = dist;
                float pr = (bfs_dw[w] > 0.0f) ? (bfs_nr[w] / bfs_dw[w]) : cent_r;
                float pc = (bfs_dw[w] > 0.0f) ? (bfs_nc[w] / bfs_dw[w]) : cent_c;
                float jr = 0.25f * std::sin(0.0173f * static_cast<float>(w + 1));
                float jc = 0.25f * std::cos(0.0131f * static_cast<float>(w + 1));
                r_pos[w] = r_tgt[w] = std::clamp(0.92f * pr + 0.08f * cent_r + jr, 1.0f, 109.0f);
                c_pos[w] = c_tgt[w] = std::clamp(0.92f * pc + 0.08f * cent_c + jc, 1.0f, 182.0f);
            }
            cur_wave.swap(next_wave);
        }
        for (int32_t u = 0; u < num_units; ++u) {
            if (unit_kind[u] != 0 && wave_dist[u] < 0) {
                float jr = 2.0f * std::sin(0.0173f * static_cast<float>(u + 1));
                float jc = 2.0f * std::cos(0.0131f * static_cast<float>(u + 1));
                r_pos[u] = r_tgt[u] = std::clamp(cent_r + jr, 1.0f, 109.0f);
                c_pos[u] = c_tgt[u] = std::clamp(cent_c + jc, 1.0f, 182.0f);
            }
        }
    }

    std::vector<float> net_sr(num_pnets, 0.0f), net_sc(num_pnets, 0.0f), net_wc(num_pnets, 0.0f);
    for (int32_t e = 0; e < num_pnets; ++e) {
        int32_t b = pnet_offsets[e], en = pnet_offsets[e + 1];
        int32_t deg = en - b;
        if (deg < 2) continue;
        float w = pnet_weights[e];
        if (deg <= 32) {
            net_wc[e] = w * (2.0f / static_cast<float>(deg));
        } else {
            net_wc[e] = w * (2.0f / (static_cast<float>(deg) * std::sqrt(static_cast<float>(deg) / 16.0f)));
        }
    }
    std::vector<int32_t> u_clid(num_units, -1);
    std::vector<float> cl_sr, cl_sc;
    std::vector<uint8_t> eff_cfu_mask(111 * 184, 0);
    const float alphas[8] = {0.002f, 0.014f, 0.035f, 0.080f, 0.18f, 0.40f, 0.85f, 1.60f};
    float tot_cfu_dem = 0.0f;
    for (int32_t u : cfu_units) tot_cfu_dem += demand[u];

    auto calc_total_hpwl = [&]() -> double {
        double tot = 0.0;
        for (int32_t e = 0; e < num_pnets; ++e) {
            int32_t b = pnet_offsets[e], en = pnet_offsets[e + 1];
            if (en - b < 2) continue;
            float r0 = 1e9f, r1 = -1e9f, c0 = 1e9f, c1 = -1e9f;
            for (int32_t p = b; p < en; ++p) {
                int32_t u = pnet_units[p];
                float ru = r_pos[u], cu = c_pos[u];
                if (ru < r0) r0 = ru;
                if (ru > r1) r1 = ru;
                if (cu < c0) c0 = cu;
                if (cu > c1) c1 = cu;
            }
            tot += static_cast<double>((r1 - r0) + (c1 - c0));
        }
        return tot;
    };

    auto p1_bsram_wire_cost = [&](int32_t u, float tr, float tc) -> float {
        float cost = 0.0f;
        for (int32_t idx = u_net_off[u]; idx < u_net_off[u + 1]; ++idx) {
            int32_t e = u_nets[idx];
            int32_t b = pnet_offsets[e], en = pnet_offsets[e + 1];
            int32_t deg = en - b;
            if (deg < 2 || deg > 48) continue;
            float w = pnet_weights[e] / static_cast<float>(deg - 1);
            int32_t drv = pnet_units[b];
            if (u == drv) {
                for (int32_t p = b + 1; p < en; ++p) {
                    int32_t v = pnet_units[p];
                    cost += w * (std::abs(tr - r_pos[v]) + std::abs(tc - c_pos[v]));
                }
            } else {
                cost += w * (std::abs(tr - r_pos[drv]) + std::abs(tc - c_pos[drv]));
            }
        }
        return cost;
    };

    // Pre-group FF units by control set for Phase-1 local quartet co-attraction
    struct P1CSCluster {
        int32_t cnt;
        int32_t u[4];
    };
    std::vector<int> p1_cs_keys;
    std::vector<std::vector<int32_t>> p1_cs_vecs;
    {
        std::unordered_map<int, std::vector<int32_t>> tmp_cs;
        tmp_cs.reserve(4096);
        for (int32_t u = 0; u < num_units; ++u) {
            if (unit_kind[u] == 6) tmp_cs[unit_cs[u]].push_back(u);
        }
        p1_cs_keys.reserve(tmp_cs.size());
        for (const auto& kv : tmp_cs) {
            if (kv.second.size() >= 2) p1_cs_keys.push_back(kv.first);
        }
        std::sort(p1_cs_keys.begin(), p1_cs_keys.end());
        p1_cs_vecs.reserve(p1_cs_keys.size());
        for (int cs : p1_cs_keys) p1_cs_vecs.push_back(std::move(tmp_cs[cs]));
    }
    std::vector<P1CSCluster> p1_clusters;

    for (int outer = 0; outer < 8; ++outer) {
        std::fill(u_clid.begin(), u_clid.end(), -1);
        cl_sr.clear();
        cl_sc.clear();
        if (outer >= 2 && !p1_cs_vecs.empty()) {
            p1_clusters.clear();
            auto build_p1_cl = [&](auto& self, std::vector<int32_t>& vec, int l, int r_end) -> void {
                int n = r_end - l;
                if (n < 2) return;
                float r0 = 1e9f, r1 = -1e9f, c0 = 1e9f, c1 = -1e9f;
                for (int i = l; i < r_end; ++i) {
                    int32_t u = vec[i];
                    if (r_pos[u] < r0) r0 = r_pos[u];
                    if (r_pos[u] > r1) r1 = r_pos[u];
                    if (c_pos[u] < c0) c0 = c_pos[u];
                    if (c_pos[u] > c1) c1 = c_pos[u];
                }
                float span = (r1 - r0) + (c1 - c0);
                if (n <= 4 && span <= 40.0f) {
                    P1CSCluster cl{};
                    cl.cnt = n;
                    int32_t cid = static_cast<int32_t>(p1_clusters.size());
                    float sr = 0.0f, sc = 0.0f;
                    for (int i = 0; i < n; ++i) {
                        cl.u[i] = vec[l + i];
                        u_clid[cl.u[i]] = cid;
                        sr += r_pos[cl.u[i]];
                        sc += c_pos[cl.u[i]];
                    }
                    p1_clusters.push_back(cl);
                    cl_sr.push_back(sr);
                    cl_sc.push_back(sc);
                    return;
                }
                if (n <= 2) return;
                bool split_c = (c1 - c0) >= (r1 - r0);
                std::sort(vec.begin() + l, vec.begin() + r_end, [&](int32_t a, int32_t b) {
                    if (split_c) return c_pos[a] != c_pos[b] ? c_pos[a] < c_pos[b] : a < b;
                    return r_pos[a] != r_pos[b] ? r_pos[a] < r_pos[b] : a < b;
                });
                int left_n = (n <= 8) ? 4 : (((n / 2) + 2) & ~3);
                left_n = std::clamp(left_n, 2, n - 1);
                self(self, vec, l, l + left_n);
                self(self, vec, l + left_n, r_end);
            };
            for (auto& vec : p1_cs_vecs) {
                build_p1_cl(build_p1_cl, vec, 0, static_cast<int>(vec.size()));
            }
        }

        auto rebuild_sums = [&]() {
            for (int32_t e = 0; e < num_pnets; ++e) {
                int32_t b = pnet_offsets[e], en = pnet_offsets[e + 1];
                float sr = 0.0f, sc = 0.0f;
                for (int32_t p = b; p < en; ++p) {
                    int32_t u = pnet_units[p];
                    sr += r_pos[u];
                    sc += c_pos[u];
                }
                net_sr[e] = sr;
                net_sc[e] = sc;
            }
            for (size_t cid = 0; cid < p1_clusters.size(); ++cid) {
                float sr = 0.0f, sc = 0.0f;
                for (int i = 0; i < p1_clusters[cid].cnt; ++i) {
                    int32_t u = p1_clusters[cid].u[i];
                    sr += r_pos[u];
                    sc += c_pos[u];
                }
                cl_sr[cid] = sr;
                cl_sc[cid] = sc;
            }
        };
        rebuild_sums();

        float anchor_w = alphas[outer];
        float w_cs_pull = (outer >= 2) ? (0.16f * static_cast<float>(outer - 1)) : 0.0f;
        int inner_iters = (outer == 0) ? 90 : 40;
        for (int it = 0; it < inner_iters; ++it) {
            if (it > 0 && (it & 31) == 0) rebuild_sums();
            const float omega = (outer == 0) ? ((it < 60) ? 1.50f : 1.15f) : ((outer <= 4) ? 1.35f : 1.05f);
            const bool rev_sweep = ((it & 1) == 1);
            for (int32_t step_u = 0; step_u < num_units; ++step_u) {
                int32_t u = rev_sweep ? (num_units - 1 - step_u) : step_u;
                if (unit_kind[u] == 0) continue;
                float ur_old = r_pos[u], uc_old = c_pos[u];
                float aw = anchor_w * (1.0f + 0.12f * std::min(demand[u], 8.0f)) + 1e-4f;
                float nr_acc = aw * r_tgt[u];
                float nc_acc = aw * c_tgt[u];
                float dw_acc = aw;

                int32_t cid = u_clid[u];
                if (w_cs_pull > 0.0f && cid >= 0) {
                    float inv_o = 1.0f / static_cast<float>(p1_clusters[cid].cnt - 1);
                    nr_acc += w_cs_pull * (cl_sr[cid] - ur_old) * inv_o;
                    nc_acc += w_cs_pull * (cl_sc[cid] - uc_old) * inv_o;
                    dw_acc += w_cs_pull;
                }

                for (int32_t idx = u_net_off[u]; idx < u_net_off[u + 1]; ++idx) {
                    int32_t e = u_nets[idx];
                    int32_t deg = pnet_offsets[e + 1] - pnet_offsets[e];
                    if (deg < 2) continue;
                    float wc = net_wc[e];
                    if (deg == 2) {
                        nr_acc += wc * (net_sr[e] - ur_old);
                        nc_acc += wc * (net_sc[e] - uc_old);
                    } else {
                        float inv_o = 1.0f / static_cast<float>(deg - 1);
                        nr_acc += wc * (net_sr[e] - ur_old) * inv_o;
                        nc_acc += wc * (net_sc[e] - uc_old) * inv_o;
                    }
                    dw_acc += wc;
                }

                float tr = nr_acc / dw_acc;
                float tc = nc_acc / dw_acc;
                float ur_new = std::clamp(ur_old + omega * (tr - ur_old), 1.0f, 109.0f);
                float uc_new = std::clamp(uc_old + omega * (tc - uc_old), 1.0f, 182.0f);
                float dr_u = ur_new - ur_old;
                float dc_u = uc_new - uc_old;
                if (std::abs(dr_u) > 1e-6f || std::abs(dc_u) > 1e-6f) {
                    r_pos[u] = ur_new;
                    c_pos[u] = uc_new;
                    if (cid >= 0) {
                        cl_sr[cid] += dr_u;
                        cl_sc[cid] += dc_u;
                    }
                    for (int32_t idx = u_net_off[u]; idx < u_net_off[u + 1]; ++idx) {
                        int32_t e = u_nets[idx];
                        net_sr[e] += dr_u;
                        net_sc[e] += dc_u;
                    }
                }
            }
        }

        std::copy(cfu_mask, cfu_mask + 111 * 184, eff_cfu_mask.begin());

        // Spread BSRAM units onto nearest distinct BSRAM sites + 2-opt co-placement swaps!
        if (!bsram_units.empty() && num_bsram_sites > 0) {
            std::vector<int32_t> b_own(num_bsram_sites, -1);
            std::vector<int32_t> u_bs(num_units, -1);
            std::vector<int32_t> order = bsram_units;
            std::sort(order.begin(), order.end(), [&](int32_t a, int32_t b) {
                return unit_crit_sum[a] > unit_crit_sum[b];
            });
            for (int32_t u : order) {
                int best_s = -1;
                float best_d = 1e18f;
                for (int s = 0; s < num_bsram_sites; ++s) {
                    if (b_own[s] >= 0) continue;
                    float d = std::abs(r_pos[u] - bsram_rc[2 * s]) + std::abs(c_pos[u] - bsram_rc[2 * s + 1]);
                    if (d < best_d) { best_d = d; best_s = s; }
                }
                if (best_s >= 0) {
                    b_own[best_s] = u;
                    u_bs[u] = best_s;
                }
            }
            if (outer >= 2) {
                for (int32_t u : order) {
                    int sc = u_bs[u];
                    if (sc < 0) continue;
                    float cur_v = p1_bsram_wire_cost(u, bsram_rc[2 * sc], bsram_rc[2 * sc + 1]);
                    int best_s = sc;
                    float best_gain = 1.0f;
                    for (int s2 = 0; s2 < num_bsram_sites; ++s2) {
                        if (s2 == sc) continue;
                        int32_t v = b_own[s2];
                        if (v < 0) {
                            float gain = cur_v - p1_bsram_wire_cost(u, bsram_rc[2 * s2], bsram_rc[2 * s2 + 1]);
                            if (gain > best_gain) { best_gain = gain; best_s = s2; }
                        } else {
                            float v_cur = p1_bsram_wire_cost(v, bsram_rc[2 * s2], bsram_rc[2 * s2 + 1]);
                            float u_new = p1_bsram_wire_cost(u, bsram_rc[2 * s2], bsram_rc[2 * s2 + 1]);
                            float v_new = p1_bsram_wire_cost(v, bsram_rc[2 * sc], bsram_rc[2 * sc + 1]);
                            float gain = (cur_v + v_cur) - (u_new + v_new);
                            if (gain > best_gain) { best_gain = gain; best_s = s2; }
                        }
                    }
                    if (best_s != sc) {
                        int32_t v = b_own[best_s];
                        b_own[best_s] = u; u_bs[u] = best_s;
                        b_own[sc] = v; if (v >= 0) u_bs[v] = sc;
                    }
                }
            }
            for (int32_t u : order) {
                int s = u_bs[u];
                if (s >= 0) {
                    int br = bsram_rc[2 * s], bc = bsram_rc[2 * s + 1];
                    r_tgt[u] = static_cast<float>(br);
                    c_tgt[u] = static_cast<float>(bc);
                    for (int j = 0; j < 3; ++j) {
                        if (br >= 1 && br <= 109 && bc + j >= 1 && bc + j <= 182) eff_cfu_mask[br * 184 + bc + j] = 0;
                    }
                }
            }
        }

        // Spread DSP units onto nearest distinct DSP sites for spread anchors
        if ((!dsp_wide_units.empty() || !dsp_single_units.empty()) && num_dsp_sites > 0) {
            std::vector<uint8_t> used_d(num_dsp_sites, 0);
            std::unordered_map<int, int> rc_to_dsp;
            for (int s = 0; s < num_dsp_sites; ++s) {
                rc_to_dsp[dsp_rc[2 * s] * 256 + dsp_rc[2 * s + 1]] = s;
            }
            for (int32_t u : dsp_wide_units) {
                int best_s = -1;
                float best_d = 1e18f;
                for (int s = 0; s < num_dsp_sites; ++s) {
                    if (used_d[s] || !dsp_wide_ok[s]) continue;
                    auto it2 = rc_to_dsp.find(dsp_rc[2 * s] * 256 + (dsp_rc[2 * s + 1] + 3));
                    if (it2 == rc_to_dsp.end() || used_d[it2->second]) continue;
                    float d = std::abs(r_pos[u] - dsp_rc[2 * s]) + std::abs(c_pos[u] - dsp_rc[2 * s + 1]);
                    if (d < best_d) { best_d = d; best_s = s; }
                }
                if (best_s >= 0) {
                    int dr = dsp_rc[2 * best_s], dc0 = dsp_rc[2 * best_s + 1];
                    int s2 = rc_to_dsp[dr * 256 + (dc0 + 3)];
                    used_d[best_s] = 1; used_d[s2] = 1;
                    r_tgt[u] = static_cast<float>(dr);
                    c_tgt[u] = static_cast<float>(dc0);
                    for (int j = 0; j < 6; ++j) {
                        if (dr >= 1 && dr <= 109 && dc0 + j >= 1 && dc0 + j <= 182) eff_cfu_mask[dr * 184 + dc0 + j] = 0;
                    }
                }
            }
            std::vector<int32_t> order = dsp_single_units;
            std::sort(order.begin(), order.end(), [&](int32_t a, int32_t b) {
                return unit_crit_sum[a] > unit_crit_sum[b];
            });
            for (int32_t u : order) {
                int best_s = -1;
                float best_d = 1e18f;
                for (int s = 0; s < num_dsp_sites; ++s) {
                    if (used_d[s]) continue;
                    float d = std::abs(r_pos[u] - dsp_rc[2 * s]) + std::abs(c_pos[u] - dsp_rc[2 * s + 1]);
                    if (d < best_d) { best_d = d; best_s = s; }
                }
                if (best_s >= 0) {
                    int dr = dsp_rc[2 * best_s], dc0 = dsp_rc[2 * best_s + 1];
                    used_d[best_s] = 1;
                    r_tgt[u] = static_cast<float>(dr);
                    c_tgt[u] = static_cast<float>(dc0);
                    for (int j = 0; j < 3; ++j) {
                        if (dr >= 1 && dr <= 109 && dc0 + j >= 1 && dc0 + j <= 182) eff_cfu_mask[dr * 184 + dc0 + j] = 0;
                    }
                }
            }
        }

        // Spread CFU units via exact capacity-aware SimPL lookahead cut-line spreading (around hard-stripe reservations!)
        if (!cfu_units.empty()) {
            int avail_cfu_cap = 0;
            for (int r = 1; r <= 109; ++r) {
                for (int c = 1; c <= 182; ++c) {
                    if (eff_cfu_mask[r * 184 + c]) avail_cfu_cap += 8;
                }
            }
            float extra_pin_dem = 0.0f;
            for (int32_t u : cfu_units) {
                if (unit_kind[u] == 5 && unit_sub[u] == 5) extra_pin_dem += 1.6f;
                else if (unit_kind[u] == 5 && unit_sub[u] == 6) extra_pin_dem += 1.2f;
                else if (unit_kind[u] == 7 || (unit_kind[u] == 6 && unit_sub[u] != 0)) extra_pin_dem += 0.35f;
            }
            float pin_inflate = std::clamp((0.82f * static_cast<float>(avail_cfu_cap) - tot_cfu_dem) / std::max(1.0f, extra_pin_dem), 0.0f, 1.0f);
            std::vector<float> eff_demand = demand;
            float eff_tot_dem = tot_cfu_dem + pin_inflate * extra_pin_dem;
            if (pin_inflate > 0.0f) {
                for (int32_t u : cfu_units) {
                    if (unit_kind[u] == 5 && unit_sub[u] == 5) eff_demand[u] += 1.6f * pin_inflate;
                    else if (unit_kind[u] == 5 && unit_sub[u] == 6) eff_demand[u] += 1.2f * pin_inflate;
                    else if (unit_kind[u] == 7 || (unit_kind[u] == 6 && unit_sub[u] != 0)) eff_demand[u] += 0.35f * pin_inflate;
                }
            }
            float util = eff_tot_dem / std::max(8.0f, static_cast<float>(avail_cfu_cap));
            float cap_frac = std::clamp(std::max(util * 1.10f, util + 0.04f), 0.60f, 0.92f);
            std::vector<int32_t> work = cfu_units;
            spread_cfu_recursive(work, 0, static_cast<int>(work.size()), SpreadBox{1, 109, 1, 182}, 0,
                                 eff_cfu_mask.data(), cap_frac, eff_demand, r_pos, c_pos, r_tgt, c_tgt);
        }
    }
    double hpwl_p1 = calc_total_hpwl();

    // ========================================================================
    // Phase 2: Exact Hardware Legalization
    // ========================================================================
    using CfuState = PlacedCfuState;
    std::vector<CfuState> cfus(111 * 184);
    for (int r = 1; r < 110; ++r) {
        for (int c = 1; c < 183; ++c) {
            cfus[r * 184 + c].valid = cfu_mask[r * 184 + c];
        }
    }

    auto unit_c60_pair = [&](int32_t u, int32_t& c0, int32_t& c1) {
        cfu_unit_c60_pair(unit_c60, u, c0, c1);
    };
    auto c60_after_add = [](const CfuState& st, int32_t c0, int32_t c1) -> int {
        return cfu_c60_after_add(st, c0, c1);
    };
    auto c60_add_unit = [&](CfuState& st, int32_t u) {
        cfu_c60_add(st, unit_c60, u);
    };
    auto c60_rem_unit = [&](CfuState& st, int32_t u) {
        cfu_c60_rem(st, unit_c60, u);
    };

    for (int32_t u = 0; u < num_units; ++u) {
        if (unit_kind[u] == 0) {
            out_r[u] = static_cast<int16_t>(std::lround(init_r[u]));
            out_c[u] = static_cast<int16_t>(std::lround(init_c[u]));
            out_slot[u] = 0;
        } else {
            out_r[u] = -1; out_c[u] = -1; out_slot[u] = -1;
        }
    }

    // 2a. Legalize DSP_WIDE & DSP_SINGLE
    {
        std::vector<int32_t> dsp_slot0_owner(num_dsp_sites, -1);
        std::vector<int32_t> dsp_slot1_owner(num_dsp_sites, -1);
        std::unordered_map<int, int> rc_to_dsp;
        for (int s = 0; s < num_dsp_sites; ++s) {
            rc_to_dsp[dsp_rc[2 * s] * 256 + dsp_rc[2 * s + 1]] = s;
        }
        for (int32_t u : dsp_wide_units) {
            int best_s = -1;
            float best_d = 1e18f;
            for (int s = 0; s < num_dsp_sites; ++s) {
                if (dsp_slot0_owner[s] >= 0 || !dsp_wide_ok[s]) continue;
                auto it2 = rc_to_dsp.find(dsp_rc[2 * s] * 256 + (dsp_rc[2 * s + 1] + 3));
                if (it2 == rc_to_dsp.end() || dsp_slot0_owner[it2->second] >= 0) continue;
                float d = std::abs(r_pos[u] - dsp_rc[2 * s]) + std::abs(c_pos[u] - dsp_rc[2 * s + 1]);
                if (d < best_d) { best_d = d; best_s = s; }
            }
            if (best_s < 0) return -10; // Insufficient wide DSP sites
            int s2 = rc_to_dsp[dsp_rc[2 * best_s] * 256 + (dsp_rc[2 * best_s + 1] + 3)];
            dsp_slot0_owner[best_s] = u; dsp_slot1_owner[best_s] = u;
            dsp_slot0_owner[s2] = u;     dsp_slot1_owner[s2] = u;
            out_r[u] = dsp_rc[2 * best_s];
            out_c[u] = dsp_rc[2 * best_s + 1];
            out_slot[u] = 0;
            r_pos[u] = out_r[u]; c_pos[u] = out_c[u];
            for (int j = 0; j < 6; ++j) {
                int cj = out_c[u] + j;
                if (out_r[u] >= 1 && out_r[u] <= 109 && cj >= 1 && cj <= 182) {
                    cfus[out_r[u] * 184 + cj].exclusive = 1;
                    cfus[out_r[u] * 184 + cj].used_cnt = 8;
                }
            }
        }

        std::vector<int32_t> d_ord = dsp_single_units;
        std::sort(d_ord.begin(), d_ord.end(), [&](int32_t a, int32_t b) {
            return unit_crit_sum[a] > unit_crit_sum[b];
        });
        for (int32_t u : d_ord) {
            int best_s = -1, best_sl = 0;
            float best_d = 1e18f;
            for (int s = 0; s < num_dsp_sites; ++s) {
                if (dsp_slot0_owner[s] < 0) {
                    float d = std::abs(r_pos[u] - dsp_rc[2 * s]) + std::abs(c_pos[u] - dsp_rc[2 * s + 1]);
                    if (d < best_d) { best_d = d; best_s = s; best_sl = 0; }
                } else if (unit_sub[u] == 187 && dsp_slot1_owner[s] < 0 && unit_sub[dsp_slot0_owner[s]] == 187) {
                    float d = std::abs(r_pos[u] - dsp_rc[2 * s]) + std::abs(c_pos[u] - dsp_rc[2 * s + 1]) + 4.0f;
                    if (d < best_d) { best_d = d; best_s = s; best_sl = 1; }
                }
            }
            if (best_s < 0) return -11; // Insufficient DSP sites
            if (best_sl == 0) {
                dsp_slot0_owner[best_s] = u;
                if (unit_sub[u] != 187) dsp_slot1_owner[best_s] = u;
            } else {
                dsp_slot1_owner[best_s] = u;
            }
            out_r[u] = dsp_rc[2 * best_s];
            out_c[u] = dsp_rc[2 * best_s + 1];
            out_slot[u] = static_cast<int16_t>(best_sl);
            r_pos[u] = out_r[u]; c_pos[u] = out_c[u];
            for (int j = 0; j < 3; ++j) {
                int cj = out_c[u] + j;
                if (out_r[u] >= 1 && out_r[u] <= 109 && cj >= 1 && cj <= 182) {
                    cfus[out_r[u] * 184 + cj].exclusive = 1;
                    cfus[out_r[u] * 184 + cj].used_cnt = 8;
                }
            }
        }
    }

    // 2b. Legalize BSRAM + 2-opt refinement across all 340 BSRAM sites
    {
        std::vector<int32_t> b_owner(num_bsram_sites, -1);
        std::vector<int32_t> u_to_bsite(num_units, -1);
        std::vector<int32_t> b_ord = bsram_units;
        std::sort(b_ord.begin(), b_ord.end(), [&](int32_t a, int32_t b) {
            return unit_crit_sum[a] > unit_crit_sum[b];
        });
        for (int32_t u : b_ord) {
            int best_s = -1;
            float best_d = 1e18f;
            for (int s = 0; s < num_bsram_sites; ++s) {
                if (b_owner[s] >= 0) continue;
                float d = std::abs(r_pos[u] - bsram_rc[2 * s]) + std::abs(c_pos[u] - bsram_rc[2 * s + 1]);
                if (d < best_d) { best_d = d; best_s = s; }
            }
            if (best_s < 0) return -12; // Insufficient BSRAM sites
            b_owner[best_s] = u;
            u_to_bsite[u] = best_s;
            out_r[u] = bsram_rc[2 * best_s];
            out_c[u] = bsram_rc[2 * best_s + 1];
            out_slot[u] = 0;
            r_pos[u] = out_r[u]; c_pos[u] = out_c[u];
        }

        // Fast 2-opt BSRAM swap refinement against connected neighbor positions
        auto unit_wire_cost = [&](int32_t u, float tr, float tc) -> float {
            float cost = 0.0f;
            for (int32_t idx = u_net_off[u]; idx < u_net_off[u + 1]; ++idx) {
                int32_t e = u_nets[idx];
                int32_t b = pnet_offsets[e], en = pnet_offsets[e + 1];
                int32_t deg = en - b;
                if (deg > 32) continue;
                float w = pnet_weights[e] / static_cast<float>(deg - 1);
                int32_t drv = pnet_units[b];
                if (u == drv) {
                    for (int32_t p = b + 1; p < en; ++p) {
                        int32_t v = pnet_units[p];
                        cost += w * (std::abs(tr - r_pos[v]) + std::abs(tc - c_pos[v]));
                    }
                } else {
                    cost += w * (std::abs(tr - r_pos[drv]) + std::abs(tc - c_pos[drv]));
                }
            }
            return cost;
        };

        for (int pass = 0; pass < 3; ++pass) {
            for (int32_t u : b_ord) {
                int s_cur = u_to_bsite[u];
                float cur_c_val = unit_wire_cost(u, bsram_rc[2 * s_cur], bsram_rc[2 * s_cur + 1]);
                int best_s = s_cur;
                float best_gain = 0.5f;
                for (int s2 = 0; s2 < num_bsram_sites; ++s2) {
                    if (s2 == s_cur) continue;
                    int32_t v = b_owner[s2];
                    if (v < 0) {
                        float new_c = unit_wire_cost(u, bsram_rc[2 * s2], bsram_rc[2 * s2 + 1]);
                        if (cur_c_val - new_c > best_gain) {
                            best_gain = cur_c_val - new_c;
                            best_s = s2;
                        }
                    } else {
                        float v_cur = unit_wire_cost(v, bsram_rc[2 * s2], bsram_rc[2 * s2 + 1]);
                        float u_new = unit_wire_cost(u, bsram_rc[2 * s2], bsram_rc[2 * s2 + 1]);
                        float v_new = unit_wire_cost(v, bsram_rc[2 * s_cur], bsram_rc[2 * s_cur + 1]);
                        float gain = (cur_c_val + v_cur) - (u_new + v_new);
                        if (gain > best_gain) {
                            best_gain = gain;
                            best_s = s2;
                        }
                    }
                }
                if (best_s != s_cur) {
                    int32_t v = b_owner[best_s];
                    b_owner[best_s] = u;
                    u_to_bsite[u] = best_s;
                    out_r[u] = bsram_rc[2 * best_s]; out_c[u] = bsram_rc[2 * best_s + 1];
                    r_pos[u] = out_r[u]; c_pos[u] = out_c[u];
                    b_owner[s_cur] = v;
                    if (v >= 0) {
                        u_to_bsite[v] = s_cur;
                        out_r[v] = bsram_rc[2 * s_cur]; out_c[v] = bsram_rc[2 * s_cur + 1];
                        r_pos[v] = out_r[v]; c_pos[v] = out_c[v];
                    }
                }
            }
        }
        for (int32_t u : b_ord) {
            for (int j = 0; j < 3; ++j) {
                int cj = out_c[u] + j;
                if (out_r[u] >= 1 && out_r[u] <= 109 && cj >= 1 && cj <= 182) {
                    cfus[out_r[u] * 184 + cj].exclusive = 1;
                    cfus[out_r[u] * 184 + cj].used_cnt = 8;
                }
            }
        }
    }

    // 2c. CFU Tile Legalization for ALU_CHAIN, MUX_TREE, CFU_SLOT_FF, CFU_SLOT_LUT
    std::vector<int32_t> alu_units, mux_units, ff_units, lut_units;
    for (int32_t u : cfu_units) {
        if (unit_kind[u] == 4) alu_units.push_back(u);
        else if (unit_kind[u] == 5) mux_units.push_back(u);
        else if (unit_kind[u] == 6) ff_units.push_back(u);
        else if (unit_kind[u] == 7) lut_units.push_back(u);
    }

    // Legalize ALU_CHAIN (longest chains first, then criticality; strictly type-17 CFUs valid == 2)
    std::sort(alu_units.begin(), alu_units.end(), [&](int32_t a, int32_t b) {
        if (unit_chain_len[a] != unit_chain_len[b]) return unit_chain_len[a] > unit_chain_len[b];
        return unit_crit_sum[a] > unit_crit_sum[b];
    });
    for (int32_t u : alu_units) {
        int L = std::max(1, unit_chain_len[u]);
        int cin_ext = (unit_flags[u] & 1) ? 1 : 0;
        int cout_ext = ((unit_flags[u] & 2) && (L % 6 == 0)) ? 1 : 0;
        int span_right = (L - 1) / 6 + cout_ext;
        int rt = std::clamp(static_cast<int>(std::lround(r_pos[u])), 1, 109);
        int ct = std::clamp(static_cast<int>(std::lround(c_pos[u])) - ((L - 1) / 12), 1 + cin_ext, 182 - span_right);

        int best_r = -1, best_c0 = -1;
        int best_dist = 1000000;
        for (int dr = 0; dr <= 109; ++dr) {
            if (dr > best_dist) break;
            for (int sr_i = 0; sr_i < (dr == 0 ? 1 : 2); ++sr_i) {
                int sr = (sr_i == 0) ? -dr : dr;
                int r = rt + sr;
                if (r < 1 || r > 109) continue;
                for (int dc = 0; dc <= 182; ++dc) {
                    if (dr + dc >= best_dist) break;
                    for (int sc_i = 0; sc_i < (dc == 0 ? 1 : 2); ++sc_i) {
                        int sc = (sc_i == 0) ? -dc : dc;
                        int c0 = ct + sc;
                        int c_beg = c0 - cin_ext;
                        int c_end = c0 + span_right;
                        if (c_beg < 1 || c_end > 182) continue;
                        bool ok = true;
                        for (int c = c_beg; c <= c_end; ++c) {
                            const auto& st = cfus[r * 184 + c];
                            if (st.valid != 2 || st.exclusive || st.used_cnt > 0) { ok = false; break; }
                        }
                        if (ok) {
                            best_dist = dr + dc;
                            best_r = r;
                            best_c0 = c0;
                            break;
                        }
                    }
                    if (best_r == r && dr + dc >= best_dist) break;
                }
            }
        }
        if (best_r < 0) return -13; // Failed ALU_CHAIN legalization
        int c_beg = best_c0 - cin_ext;
        int c_end = best_c0 + span_right;
        for (int c = c_beg; c <= c_end; ++c) {
            cfus[best_r * 184 + c].exclusive = 1;
            cfus[best_r * 184 + c].used_cnt = 8;
        }
        out_r[u] = static_cast<int16_t>(best_r);
        out_c[u] = static_cast<int16_t>(best_c0);
        out_slot[u] = 0;
        r_pos[u] = static_cast<float>(best_r);
        c_pos[u] = static_cast<float>(best_c0 + (L - 1) / 12.0f);
    }

    // Legalize MUX_TREE into type-17 CFUs (valid == 2) using exact non-exclusive CLS slot footprints:
    //   MUX2_LUT5 (sub==5): 2 slots {s0, s0+1} for s0 in {6, 4, 2, 0} (preserves remaining 6 slots & FF domains!)
    //   MUX2_LUT6 (sub==6): 4 slots {4..7} (root_slot=5, preserves dom0+dom1!) or {0..3} (root_slot=1, preserves dom2!)
    //   MUX2_LUT7 (sub>=7): 8 slots {0..7} (root_slot=3, exclusive=1)
    std::sort(mux_units.begin(), mux_units.end(), [&](int32_t a, int32_t b) {
        if (unit_sub[a] != unit_sub[b]) return unit_sub[a] > unit_sub[b]; // Place larger footprints (7 -> 6 -> 5) first
        return unit_crit_sum[a] > unit_crit_sum[b];
    });
    for (int32_t u : mux_units) {
        int rt = std::clamp(static_cast<int>(std::lround(r_pos[u])), 1, 109);
        int ct = std::clamp(static_cast<int>(std::lround(c_pos[u])), 1, 182);
        int lvl_m = unit_sub[u];
        int need_slots = (lvl_m == 5) ? 2 : ((lvl_m == 6) ? 4 : 8);
        int32_t mc0, mc1;
        unit_c60_pair(u, mc0, mc1);
        int best_r = -1, best_c = -1, best_root_s = -1;

        struct MuxStep { int rad; int cap; int c60_lim; int mux5_lim; int h_nbr_lim; };
        const MuxStep mux_sched[5] = {
            {4,   5, 2, 0, 2},
            {10,  6, 3, 1, 3},
            {24,  7, 3, 1, 5},
            {290, 8, 3, 1, 8},
            {290, 8, 4, 3, 8},
        };
        for (const auto& step : mux_sched) {
            if (best_r >= 0) break;
            int max_rad = step.rad;
            int cap_lim = std::max(step.cap, need_slots);
            int c60_lim = step.c60_lim;
            for (int rad = 0; rad <= max_rad && best_r < 0; ++rad) {
                for (int dr = -rad; dr <= rad && best_r < 0; ++dr) {
                    int r = rt + dr;
                    if (r < 1 || r > 109) continue;
                    int dc_abs = rad - std::abs(dr);
                    for (int sc_i = 0; sc_i < (dc_abs == 0 ? 1 : 2); ++sc_i) {
                        int sc = (sc_i == 0) ? -dc_abs : dc_abs;
                        int c = ct + sc;
                        if (c < 1 || c > 182) continue;
                        auto& st = cfus[r * 184 + c];
                        if (st.valid != 2 || st.exclusive || st.used_cnt + need_slots > cap_lim) continue;
                        if (lvl_m < 7 && c60_after_add(st, mc0, mc1) > c60_lim) continue;
                        if (lvl_m == 5) {
                            if (st.mux5_cnt > step.mux5_lim) continue;
                            if (step.h_nbr_lim < 8) {
                                int h_nbr = static_cast<int>(cfus[r * 184 + (c - 1)].mux5_cnt) +
                                            static_cast<int>(cfus[r * 184 + (c + 1)].mux5_cnt);
                                if (h_nbr > step.h_nbr_lim) continue;
                            }
                            const int cand_s0[4] = {6, 4, 2, 0}; // Prefer slots 6,7 first (preserves all 3 FF domains!)
                            for (int s0 : cand_s0) {
                                if (!st.slot_used[s0] && !st.slot_used[s0 + 1]) {
                                    best_r = r; best_c = c; best_root_s = s0; break;
                                }
                            }
                        } else if (lvl_m == 6) {
                            // Prefer root_slot=5 (slots 4..7, preserves dom0 & dom1!) before root_slot=1 (slots 0..3)
                            if (!st.slot_used[4] && !st.slot_used[5] && !st.slot_used[6] && !st.slot_used[7]) {
                                best_r = r; best_c = c; best_root_s = 5;
                            } else if (!st.slot_used[0] && !st.slot_used[1] && !st.slot_used[2] && !st.slot_used[3]) {
                                best_r = r; best_c = c; best_root_s = 1;
                            }
                        } else {
                            if (st.used_cnt == 0) {
                                best_r = r; best_c = c; best_root_s = 3;
                            }
                        }
                        if (best_r >= 0) break;
                    }
                }
            }
        }
        if (best_r < 0) return -14; // Failed MUX_TREE legalization
        auto& st = cfus[best_r * 184 + best_c];
        if (lvl_m == 5) {
            st.slot_used[best_root_s] = 1;
            st.slot_used[best_root_s + 1] = 1;
            st.slot_unit[best_root_s] = u;
            st.slot_unit[best_root_s + 1] = u;
            st.used_cnt += 2;
            ++st.mux5_cnt;
            st.lut_cnt += 2;
            c60_add_unit(st, u);
        } else if (lvl_m == 6) {
            int base_s = (best_root_s == 5) ? 4 : 0;
            for (int k = 0; k < 4; ++k) {
                st.slot_used[base_s + k] = 1;
                st.slot_unit[base_s + k] = u;
            }
            st.used_cnt += 4;
            st.lut_cnt += 4;
            c60_add_unit(st, u);
        } else {
            st.exclusive = 1;
            for (int k = 0; k < 8; ++k) {
                st.slot_used[k] = 1;
                st.slot_unit[k] = u;
            }
            st.used_cnt = 8;
        }
        out_r[u] = static_cast<int16_t>(best_r);
        out_c[u] = static_cast<int16_t>(best_c);
        out_slot[u] = static_cast<int16_t>(best_root_s);
        r_pos[u] = static_cast<float>(best_r);
        c_pos[u] = static_cast<float>(best_c);
    }

    const int dense_soft_cap = (cfu_units.size() > 20000) ? 6 : 7;

    // Legalize CFU_SLOT_FF via KD-Tree Spatial Quartet/Pair Packets + Local-Radius Cap Schedule
    std::unordered_map<int, int> cs_hist;
    cs_hist.reserve(4096);
    std::unordered_map<int, std::vector<int32_t>> cs_groups;
    cs_groups.reserve(4096);
    for (int32_t u : ff_units) {
        ++cs_hist[unit_cs[u]];
        cs_groups[unit_cs[u]].push_back(u);
    }

    const int large_cs_order[8] = {4, 5, 6, 7, 0, 1, 2, 3};
    const int small_cs_order[8] = {0, 1, 2, 3, 4, 5, 6, 7};

    struct FFPacket {
        int32_t cs;
        int32_t cnt;
        int32_t units[4];
        float crit_max;
        float r_cent;
        float c_cent;
    };
    std::vector<FFPacket> ff_packets;
    ff_packets.reserve(ff_units.size() / 2 + 16);

    std::vector<int> cs_keys;
    cs_keys.reserve(cs_groups.size());
    for (const auto& kv : cs_groups) cs_keys.push_back(kv.first);
    std::sort(cs_keys.begin(), cs_keys.end());

    auto build_ff_pkts = [&](auto& self, std::vector<int32_t>& vec, int l, int r_end, int cs) -> void {
        if (l >= r_end) return;
        int n = r_end - l;
        float r0 = 1e9f, r1 = -1e9f, c0 = 1e9f, c1 = -1e9f;
        float sr = 0.0f, sc = 0.0f, cmax = 0.0f;
        for (int i = l; i < r_end; ++i) {
            int32_t u = vec[i];
            float ru = r_pos[u], cu = c_pos[u];
            sr += ru; sc += cu;
            if (ru < r0) r0 = ru;
            if (ru > r1) r1 = ru;
            if (cu < c0) c0 = cu;
            if (cu > c1) c1 = cu;
            if (unit_crit_sum[u] > cmax) cmax = unit_crit_sum[u];
        }
        float span = (r1 - r0) + (c1 - c0);
        float max_span = (n == 2) ? 56.0f : 42.0f;
        if (n <= 4 && (n == 1 || span <= max_span)) {
            FFPacket pkt{};
            pkt.cs = cs;
            pkt.cnt = n;
            for (int i = 0; i < n; ++i) pkt.units[i] = vec[l + i];
            pkt.crit_max = cmax;
            pkt.r_cent = sr / static_cast<float>(n);
            pkt.c_cent = sc / static_cast<float>(n);
            ff_packets.push_back(pkt);
            return;
        }
        bool split_c = (c1 - c0) >= (r1 - r0);
        std::sort(vec.begin() + l, vec.begin() + r_end, [&](int32_t a, int32_t b) {
            if (split_c) return c_pos[a] != c_pos[b] ? c_pos[a] < c_pos[b] : a < b;
            return r_pos[a] != r_pos[b] ? r_pos[a] < r_pos[b] : a < b;
        });
        int left_n;
        if (n > 4) {
            left_n = (n <= 8) ? 4 : (((n / 2) + 2) & ~3);
            left_n = std::clamp(left_n, 4, n - 1);
        } else {
            left_n = n / 2;
        }
        self(self, vec, l, l + left_n, cs);
        self(self, vec, l + left_n, r_end, cs);
    };

    for (int cs : cs_keys) {
        auto& vec = cs_groups[cs];
        build_ff_pkts(build_ff_pkts, vec, 0, static_cast<int>(vec.size()), cs);
    }

    // Order: multi-FF packets & mandatory unique-CS singletons first, leftover singletons after
    std::sort(ff_packets.begin(), ff_packets.end(), [&](const FFPacket& a, const FFPacket& b) {
        bool a_leftover = (a.cnt == 1 && cs_hist[a.cs] > 1);
        bool b_leftover = (b.cnt == 1 && cs_hist[b.cs] > 1);
        if (a_leftover != b_leftover) return !a_leftover;
        float pa = a.crit_max + (a.cnt >= 3 ? 2.5f : (a.cnt == 2 ? 0.8f : 0.0f));
        float pb = b.crit_max + (b.cnt >= 3 ? 2.5f : (b.cnt == 2 ? 0.8f : 0.0f));
        if (pa != pb) return pa > pb;
        return a.units[0] < b.units[0];
    });

    int dom_free_rem = 0;
    for (int r = 1; r <= 109; ++r) {
        for (int c = 1; c <= 182; ++c) {
            const auto& st = cfus[r * 184 + c];
            if (!st.valid || st.exclusive) continue;
            if (!st.slot_used[0] || !st.slot_used[1]) ++dom_free_rem;
            if (!st.slot_used[2] || !st.slot_used[3]) ++dom_free_rem;
            if (!st.slot_used[4] || !st.slot_used[5] || !st.slot_used[6] || !st.slot_used[7]) ++dom_free_rem;
        }
    }
    std::unordered_map<int, uint8_t> cs_opened;
    cs_opened.reserve(cs_keys.size() * 2 + 16);
    int unopened_cs_cnt = static_cast<int>(cs_keys.size());

    auto place_ff_group = [&](auto& self, const int32_t* us, int cnt, int cs) -> bool {
        float sr = 0.0f, sc = 0.0f;
        for (int i = 0; i < cnt; ++i) { sr += r_pos[us[i]]; sc += c_pos[us[i]]; }
        int rt = std::clamp(static_cast<int>(std::lround(sr / static_cast<float>(cnt))), 1, 109);
        int ct = std::clamp(static_cast<int>(std::lround(sc / static_cast<float>(cnt))), 1, 182);

        int32_t fc0, fc1;
        unit_c60_pair(us[0], fc0, fc1);
        int lut_add = 0;
        for (int i = 0; i < cnt; ++i) if (unit_sub[us[i]] != 0) ++lut_add;
        int best_r = -1, best_c = -1;
        int best_s[4] = {-1, -1, -1, -1};
        bool must_reuse = (cs_opened[cs] != 0) && (dom_free_rem <= unopened_cs_cnt + 128);

        auto try_tile = [&](int r, int c, int cap_lim, bool existing_only, int c60_lim) -> bool {
            auto& st = cfus[r * 184 + c];
            if (!st.valid || st.exclusive || st.used_cnt + cnt > cap_lim) return false;
            if (lut_add > 0 && cap_lim < 8 && st.lut_cnt + lut_add > 5) return false;
            // 1. Check already-open matching domain in this tile (0 new hardware domains & 0 new c60 nets!)
            const int dom_try_exist[3] = {2, 0, 1};
            for (int d : dom_try_exist) {
                if (st.dom_cs[d] != cs) continue;
                if (c60_after_add(st, fc0, fc1) > 4) continue;
                int fnd = 0;
                int s_lo = (d == 0) ? 0 : ((d == 1) ? 2 : 4);
                int s_hi = (d == 0) ? 1 : ((d == 1) ? 3 : 7);
                for (int s = s_lo; s <= s_hi && fnd < cnt; ++s) {
                    if (!st.slot_used[s]) best_s[fnd++] = s;
                }
                if (fnd == cnt) { best_r = r; best_c = c; return true; }
            }
            if (existing_only || must_reuse) return false;
            if (c60_after_add(st, fc0, fc1) > c60_lim) return false;
            // 2. Claim an unclaimed domain (cnt>=3 -> dom2 only; cnt<=2 -> dom0/dom1 first, dom2 last!)
            const int dom_try_new[3] = {0, 1, 2};
            for (int d : dom_try_new) {
                if (cnt >= 3 && d != 2) continue;
                if (st.dom_cs[d] != -1) continue;
                int fnd = 0;
                int s_lo = (d == 0) ? 0 : ((d == 1) ? 2 : 4);
                int s_hi = (d == 0) ? 1 : ((d == 1) ? 3 : 7);
                for (int s = s_lo; s <= s_hi && fnd < cnt; ++s) {
                    if (!st.slot_used[s]) best_s[fnd++] = s;
                }
                if (fnd == cnt) { best_r = r; best_c = c; return true; }
            }
            return false;
        };

        auto spiral_search = [&](int max_rad, int cap_lim, bool existing_only, int c60_lim) {
            for (int rad = 0; rad <= max_rad && best_r < 0; ++rad) {
                for (int dr = -rad; dr <= rad && best_r < 0; ++dr) {
                    int r = rt + dr;
                    if (r < 1 || r > 109) continue;
                    int dc_abs = rad - std::abs(dr);
                    for (int sc_i = 0; sc_i < (dc_abs == 0 ? 1 : 2); ++sc_i) {
                        int sc_d = (sc_i == 0) ? -dc_abs : dc_abs;
                        int c = ct + sc_d;
                        if (c < 1 || c > 182) continue;
                        if (try_tile(r, c, cap_lim, existing_only, c60_lim)) break;
                    }
                }
            }
        };

        if (cnt <= 2 && cs_opened[cs] != 0) {
            int exist_rad = (dom_free_rem <= unopened_cs_cnt + 4096) ? ((cnt == 1) ? 12 : 6) : ((cnt == 1) ? 3 : 2);
            spiral_search(exist_rad, 7, true, 4);
        }
        if (best_r < 0) spiral_search(4, dense_soft_cap, false, 3);
        if (best_r < 0) spiral_search((cnt >= 3) ? 6 : 8, 7, false, 3);
        // When ample FF domains exist, split a locally-blocked 3/4-FF quartet into two 2-FF pairs right at (rt, ct)
        // (using dom0/dom1 alongside e.g. MUX2_LUT5 in slots 6..7) rather than flying 80+ columns across the chip!
        if (best_r < 0 && cnt >= 3 && !must_reuse && dom_free_rem > unopened_cs_cnt + 2048) {
            int half = cnt / 2;
            return self(self, us, half, cs) && self(self, us + half, cnt - half, cs);
        }
        if (best_r < 0) spiral_search(16, 8, false, 3);
        if (best_r < 0) spiral_search(290, 8, false, 3);
        if (best_r < 0) spiral_search(290, 8, false, 4);
        if (best_r < 0 && must_reuse) {
            // All open domains of cs were completely full; allow opening 1 new domain from safety margin
            must_reuse = false;
            spiral_search(290, 8, false, 3);
            if (best_r < 0) spiral_search(290, 8, false, 4);
        }

        if (best_r < 0 && cnt >= 2) {
            int half = cnt / 2;
            return self(self, us, half, cs) && self(self, us + half, cnt - half, cs);
        }
        if (best_r < 0) return false;

        auto& st = cfus[best_r * 184 + best_c];
        int chosen_dom = dom_of_slot(best_s[0]);
        if (st.dom_cs[chosen_dom] == -1) {
            --dom_free_rem;
        }
        if (cs_opened[cs] == 0) {
            cs_opened[cs] = 1;
            --unopened_cs_cnt;
        }
        for (int i = 0; i < cnt; ++i) {
            int32_t u = us[i];
            int s = best_s[i];
            st.slot_used[s] = 1;
            st.slot_unit[s] = u;
            st.dom_cs[dom_of_slot(s)] = cs;
            ++st.used_cnt;
            if (unit_sub[u] != 0) ++st.lut_cnt;
            c60_add_unit(st, u);
            out_r[u] = static_cast<int16_t>(best_r);
            out_c[u] = static_cast<int16_t>(best_c);
            out_slot[u] = static_cast<int16_t>(s);
            r_pos[u] = static_cast<float>(best_r);
            c_pos[u] = static_cast<float>(best_c);
        }
        return true;
    };

    for (size_t pi = 0; pi < ff_packets.size(); ++pi) {
        const auto& pkt = ff_packets[pi];
        if (!place_ff_group(place_ff_group, pkt.units, pkt.cnt, pkt.cs)) {
            std::fprintf(stdout, "[ownpr-place-err] pi=%zu/%zu cnt=%d cs=%d hist=%d dom_free=%d unopened=%d\n",
                         pi, ff_packets.size(), pkt.cnt, pkt.cs, cs_hist[pkt.cs], dom_free_rem, unopened_cs_cnt);
            std::fflush(stdout);
            return -15; // Failed CFU_SLOT_FF legalization
        }
    }

    // Legalize CFU_SLOT_LUT with Local-Radius Cap Schedule (never fly across chip before allowing 7th/8th slot!)
    std::sort(lut_units.begin(), lut_units.end(), [&](int32_t a, int32_t b) {
        return unit_crit_sum[a] > unit_crit_sum[b];
    });
    for (int32_t u : lut_units) {
        int rt = std::clamp(static_cast<int>(std::lround(r_pos[u])), 1, 109);
        int ct = std::clamp(static_cast<int>(std::lround(c_pos[u])), 1, 182);
        int best_r = -1, best_c = -1, best_s = -1;
        struct LutStep { int rad; int cap; int lut_lim; };
        const LutStep lut_sched[4] = {
            {4,  dense_soft_cap, 5},
            {8,  7,              6},
            {16, 8,              7},
            {290,8,              8},
        };
        for (const auto& step : lut_sched) {
            if (best_s >= 0) break;
            int max_rad = step.rad;
            int cap_lim = step.cap;
            for (int rad = 0; rad <= max_rad && best_s < 0; ++rad) {
                for (int dr = -rad; dr <= rad && best_s < 0; ++dr) {
                    int r = rt + dr;
                    if (r < 1 || r > 109) continue;
                    int dc_abs = rad - std::abs(dr);
                    for (int sc_i = 0; sc_i < (dc_abs == 0 ? 1 : 2); ++sc_i) {
                        int sc = (sc_i == 0) ? -dc_abs : dc_abs;
                        int c = ct + sc;
                        if (c < 1 || c > 182) continue;
                        auto& st = cfus[r * 184 + c];
                        if (!st.valid || st.exclusive || st.used_cnt >= cap_lim || st.lut_cnt >= step.lut_lim) continue;
                        if (step.lut_lim < 8 && st.mux5_cnt >= 1 && st.c60_cnt >= 3) continue;
                        for (int s = 0; s < 8; ++s) {
                            if (!st.slot_used[s]) {
                                best_r = r; best_c = c; best_s = s; break;
                            }
                        }
                    }
                }
            }
        }
        if (best_s < 0) return -16; // Failed CFU_SLOT_LUT legalization
        auto& st = cfus[best_r * 184 + best_c];
        st.slot_used[best_s] = 1;
        st.slot_unit[best_s] = u;
        ++st.used_cnt;
        ++st.lut_cnt;
        out_r[u] = static_cast<int16_t>(best_r);
        out_c[u] = static_cast<int16_t>(best_c);
        out_slot[u] = static_cast<int16_t>(best_s);
        r_pos[u] = static_cast<float>(best_r);
        c_pos[u] = static_cast<float>(best_c);
    }
    double hpwl_p2 = calc_total_hpwl();

    // Phase 3: Post-Legalization Congestion-Aware Detailed Relocation (L2 Weighted Centroid)
    std::vector<int32_t> single_units;
    single_units.reserve(ff_units.size() + lut_units.size());
    single_units.insert(single_units.end(), ff_units.begin(), ff_units.end());
    single_units.insert(single_units.end(), lut_units.begin(), lut_units.end());
    std::sort(single_units.begin(), single_units.end(), [&](int32_t a, int32_t b) {
        return unit_crit_sum[a] > unit_crit_sum[b];
    });
    std::vector<int32_t> mux5_reloc_units;
    for (int32_t u : mux_units) {
        if (unit_sub[u] == 5) mux5_reloc_units.push_back(u);
    }
    std::sort(mux5_reloc_units.begin(), mux5_reloc_units.end(), [&](int32_t a, int32_t b) {
        return unit_crit_sum[a] > unit_crit_sum[b];
    });
    std::vector<int32_t> alu_reloc_units = alu_units;
    std::sort(alu_reloc_units.begin(), alu_reloc_units.end(), [&](int32_t a, int32_t b) {
        return unit_crit_sum[a] > unit_crit_sum[b];
    });

    const size_t refine_cnt = single_units.size();
    const int phase3_cap = (cfu_units.size() > 20000) ? 6 : 8;

    for (int pass = 0; pass < 6; ++pass) {
        if (pass < 3 && !alu_reloc_units.empty()) {
            for (int32_t u : alu_reloc_units) {
                int L = std::max(1, unit_chain_len[u]);
                int cin_ext = (unit_flags[u] & 1) ? 1 : 0;
                int cout_ext = ((unit_flags[u] & 2) && (L % 6 == 0)) ? 1 : 0;
                int span_right = (L - 1) / 6 + cout_ext;
                float c_half = (L - 1) / 12.0f;

                float wr_sum = 0.0f, wc_sum = 0.0f, w_sum = 0.0f, cur_cost = 0.0f;
                float ur = r_pos[u], uc = c_pos[u];
                for (int32_t k = u_net_off[u]; k < u_net_off[u + 1]; ++k) {
                    int32_t e = u_nets[k];
                    int32_t b = pnet_offsets[e], en = pnet_offsets[e + 1];
                    int32_t deg = en - b;
                    if (deg < 2) continue;
                    int32_t drv = pnet_units[b];
                    if (u == drv && deg > 96) continue;
                    float w = (deg == 2) ? pnet_weights[e] : (pnet_weights[e] / static_cast<float>(deg - 1));
                    if (u == drv) {
                        for (int32_t p = b + 1; p < en; ++p) {
                            int32_t v = pnet_units[p];
                            cur_cost += w * (std::abs(ur - r_pos[v]) + std::abs(uc - c_pos[v]));
                            wr_sum += w * r_pos[v];
                            wc_sum += w * c_pos[v];
                            w_sum += w;
                        }
                    } else {
                        cur_cost += w * (std::abs(ur - r_pos[drv]) + std::abs(uc - c_pos[drv]));
                        wr_sum += w * r_pos[drv];
                        wc_sum += w * c_pos[drv];
                        w_sum += w;
                    }
                }
                if (w_sum <= 1e-4f) continue;
                int rt = std::clamp(static_cast<int>(std::lround(wr_sum / w_sum)), 1, 109);
                int ct = std::clamp(static_cast<int>(std::lround((wc_sum / w_sum) - c_half)), 1 + cin_ext, 182 - span_right);
                int orow = out_r[u], oc0 = out_c[u];
                int cur_dist = std::abs(rt - orow) + std::abs(ct - oc0);
                if (cur_dist <= 1) continue;

                int oc_beg = oc0 - cin_ext, oc_end = oc0 + span_right;
                for (int c = oc_beg; c <= oc_end; ++c) {
                    cfus[orow * 184 + c].exclusive = 0;
                    cfus[orow * 184 + c].used_cnt = 0;
                }

                int best_r = -1, best_c0 = -1;
                float best_cost = cur_cost - 1.5f;
                auto probe_alu = [&](int cr, int cc, int rad_w) {
                    for (int dr = -rad_w; dr <= rad_w; ++dr) {
                        int r = cr + dr;
                        if (r < 1 || r > 109) continue;
                        for (int dc = -rad_w; dc <= rad_w; ++dc) {
                            int c0 = cc + dc;
                            if (r == orow && c0 == oc0) continue;
                            int c_beg = c0 - cin_ext, c_end = c0 + span_right;
                            if (c_beg < 1 || c_end > 182) continue;
                            bool ok = true;
                            for (int c = c_beg; c <= c_end; ++c) {
                                const auto& st = cfus[r * 184 + c];
                                if (st.valid != 2 || st.exclusive || st.used_cnt > 0) { ok = false; break; }
                            }
                            if (!ok) continue;
                            float cand_uc = static_cast<float>(c0) + c_half;
                            float nc = 0.0f;
                            for (int32_t k = u_net_off[u]; k < u_net_off[u + 1]; ++k) {
                                int32_t e = u_nets[k];
                                int32_t b = pnet_offsets[e], en = pnet_offsets[e + 1];
                                int32_t deg = en - b;
                                if (deg < 2) continue;
                                int32_t drv = pnet_units[b];
                                if (u == drv && deg > 96) continue;
                                float w = (deg == 2) ? pnet_weights[e] : (pnet_weights[e] / static_cast<float>(deg - 1));
                                if (u == drv) {
                                    for (int32_t p = b + 1; p < en; ++p) {
                                        int32_t v = pnet_units[p];
                                        nc += w * (std::abs(r - r_pos[v]) + std::abs(cand_uc - c_pos[v]));
                                    }
                                } else {
                                    nc += w * (std::abs(r - r_pos[drv]) + std::abs(cand_uc - c_pos[drv]));
                                }
                            }
                            if (nc < best_cost) {
                                best_cost = nc; best_r = r; best_c0 = c0;
                            }
                        }
                    }
                };

                probe_alu(rt, ct, 6);
                if (best_r < 0 && cur_dist > 6) probe_alu(rt, ct, 16);

                if (best_r >= 0) {
                    int nc_beg = best_c0 - cin_ext, nc_end = best_c0 + span_right;
                    for (int c = nc_beg; c <= nc_end; ++c) {
                        cfus[best_r * 184 + c].exclusive = 1;
                        cfus[best_r * 184 + c].used_cnt = 8;
                    }
                    out_r[u] = static_cast<int16_t>(best_r);
                    out_c[u] = static_cast<int16_t>(best_c0);
                    r_pos[u] = static_cast<float>(best_r);
                    c_pos[u] = static_cast<float>(best_c0) + c_half;
                } else {
                    for (int c = oc_beg; c <= oc_end; ++c) {
                        cfus[orow * 184 + c].exclusive = 1;
                        cfus[orow * 184 + c].used_cnt = 8;
                    }
                }
            }
        }

        if (pass < 3 && !mux5_reloc_units.empty()) {
            for (int32_t u : mux5_reloc_units) {
                const int eff_cap = (unit_crit_sum[u] >= 8.0f) ? 8 : std::max(6, phase3_cap);
                float wr_sum = 0.0f, wc_sum = 0.0f, w_sum = 0.0f, cur_cost = 0.0f;
                float ur = r_pos[u], uc = c_pos[u];
                for (int32_t k = u_net_off[u]; k < u_net_off[u + 1]; ++k) {
                    int32_t e = u_nets[k];
                    int32_t b = pnet_offsets[e], en = pnet_offsets[e + 1];
                    int32_t deg = en - b;
                    if (deg < 2) continue;
                    int32_t drv = pnet_units[b];
                    if (u == drv && deg > 96) continue;
                    float w = (deg == 2) ? pnet_weights[e] : (pnet_weights[e] / static_cast<float>(deg - 1));
                    if (u == drv) {
                        for (int32_t p = b + 1; p < en; ++p) {
                            int32_t v = pnet_units[p];
                            cur_cost += w * (std::abs(ur - r_pos[v]) + std::abs(uc - c_pos[v]));
                            wr_sum += w * r_pos[v];
                            wc_sum += w * c_pos[v];
                            w_sum += w;
                        }
                    } else {
                        cur_cost += w * (std::abs(ur - r_pos[drv]) + std::abs(uc - c_pos[drv]));
                        wr_sum += w * r_pos[drv];
                        wc_sum += w * c_pos[drv];
                        w_sum += w;
                    }
                }
                if (w_sum <= 1e-4f) continue;
                int rt = std::clamp(static_cast<int>(std::lround(wr_sum / w_sum)), 1, 109);
                int ct = std::clamp(static_cast<int>(std::lround(wc_sum / w_sum)), 1, 182);
                int cur_dist = std::abs(rt - out_r[u]) + std::abs(ct - out_c[u]);
                if (cur_dist <= 1) continue;

                int32_t mc0, mc1;
                unit_c60_pair(u, mc0, mc1);
                int best_r = -1, best_c = -1, best_s = -1;
                float best_cost = cur_cost - 1.0f;

                auto probe_mux5 = [&](int cr, int cc, int rad_w) {
                    for (int dr = -rad_w; dr <= rad_w; ++dr) {
                        int r = cr + dr;
                        if (r < 1 || r > 109) continue;
                        for (int dc = -rad_w; dc <= rad_w; ++dc) {
                            int c = cc + dc;
                            if (c < 1 || c > 182) continue;
                            if (r == out_r[u] && c == out_c[u]) continue;
                            auto& st = cfus[r * 184 + c];
                            if (st.valid != 2 || st.exclusive || st.used_cnt + 2 > eff_cap) continue;
                            if (st.mux5_cnt >= 1) continue;
                            if (eff_cap < 8 && st.lut_cnt + 2 > 6) continue;
                            int h_nbr = static_cast<int>(cfus[r * 184 + (c - 1)].mux5_cnt) +
                                        static_cast<int>(cfus[r * 184 + (c + 1)].mux5_cnt);
                            if (h_nbr > 2) continue;
                            if (c60_after_add(st, mc0, mc1) > 3) continue;
                            int cand_s = -1;
                            const int cand_s0[4] = {6, 4, 2, 0};
                            for (int s0 : cand_s0) {
                                if (!st.slot_used[s0] && !st.slot_used[s0 + 1]) { cand_s = s0; break; }
                            }
                            if (cand_s < 0) continue;
                            float nc = 0.0f;
                            for (int32_t k = u_net_off[u]; k < u_net_off[u + 1]; ++k) {
                                int32_t e = u_nets[k];
                                int32_t b = pnet_offsets[e], en = pnet_offsets[e + 1];
                                int32_t deg = en - b;
                                if (deg < 2) continue;
                                int32_t drv = pnet_units[b];
                                if (u == drv && deg > 96) continue;
                                float w = (deg == 2) ? pnet_weights[e] : (pnet_weights[e] / static_cast<float>(deg - 1));
                                if (u == drv) {
                                    for (int32_t p = b + 1; p < en; ++p) {
                                        int32_t v = pnet_units[p];
                                        nc += w * (std::abs(r - r_pos[v]) + std::abs(c - c_pos[v]));
                                    }
                                } else {
                                    nc += w * (std::abs(r - r_pos[drv]) + std::abs(c - c_pos[drv]));
                                }
                            }
                            if (nc < best_cost) {
                                best_cost = nc; best_r = r; best_c = c; best_s = cand_s;
                            }
                        }
                    }
                };
                probe_mux5(rt, ct, 4);
                if (best_s < 0 && cur_dist > 5) probe_mux5(rt, ct, 8);
                if (best_s >= 0) {
                    int orow = out_r[u], ocol = out_c[u], osl = out_slot[u];
                    auto& ost = cfus[orow * 184 + ocol];
                    ost.slot_used[osl] = 0;
                    ost.slot_used[osl + 1] = 0;
                    ost.used_cnt -= 2;
                    --ost.mux5_cnt;
                    ost.lut_cnt -= 2;
                    c60_rem_unit(ost, u);

                    auto& nst = cfus[best_r * 184 + best_c];
                    nst.slot_used[best_s] = 1;
                    nst.slot_used[best_s + 1] = 1;
                    nst.used_cnt += 2;
                    ++nst.mux5_cnt;
                    nst.lut_cnt += 2;
                    c60_add_unit(nst, u);

                    out_r[u] = static_cast<int16_t>(best_r);
                    out_c[u] = static_cast<int16_t>(best_c);
                    out_slot[u] = static_cast<int16_t>(best_s);
                    r_pos[u] = static_cast<float>(best_r);
                    c_pos[u] = static_cast<float>(best_c);
                }
            }
        }

        for (size_t idx_u = 0; idx_u < refine_cnt; ++idx_u) {
            int32_t u = single_units[idx_u];
            const int eff_cap = (unit_crit_sum[u] >= 10.0f) ? 8 : ((unit_crit_sum[u] >= 4.0f) ? std::max(7, phase3_cap) : phase3_cap);
            float wr_sum = 0.0f, wc_sum = 0.0f, w_sum = 0.0f;
            float cur_cost = 0.0f;
            float ur = r_pos[u], uc = c_pos[u];

            for (int32_t k = u_net_off[u]; k < u_net_off[u + 1]; ++k) {
                int32_t e = u_nets[k];
                int32_t b = pnet_offsets[e], en = pnet_offsets[e + 1];
                int32_t deg = en - b;
                if (deg < 2) continue;
                int32_t drv = pnet_units[b];
                if (u == drv && deg > 96) continue;
                float w = (deg == 2) ? pnet_weights[e] : (pnet_weights[e] / static_cast<float>(deg - 1));
                if (u == drv) {
                    for (int32_t p = b + 1; p < en; ++p) {
                        int32_t v = pnet_units[p];
                        cur_cost += w * (std::abs(ur - r_pos[v]) + std::abs(uc - c_pos[v]));
                        wr_sum += w * r_pos[v];
                        wc_sum += w * c_pos[v];
                        w_sum += w;
                    }
                } else {
                    cur_cost += w * (std::abs(ur - r_pos[drv]) + std::abs(uc - c_pos[drv]));
                    wr_sum += w * r_pos[drv];
                    wc_sum += w * c_pos[drv];
                    w_sum += w;
                }
            }
            if (w_sum <= 1e-4f) continue;
            int rt = std::clamp(static_cast<int>(std::lround(wr_sum / w_sum)), 1, 109);
            int ct = std::clamp(static_cast<int>(std::lround(wc_sum / w_sum)), 1, 182);
            int cur_dist = std::abs(rt - out_r[u]) + std::abs(ct - out_c[u]);
            if (cur_dist <= 1) continue;

            int cs = unit_cs[u];
            int u_luts = (unit_kind[u] == 7 || unit_sub[u] != 0) ? 1 : 0;
            int32_t uc0 = -1, uc1 = -1;
            if (cs >= 0) unit_c60_pair(u, uc0, uc1);
            const int* slot_ord = (cs >= 0 && cs_hist[cs] <= 2) ? small_cs_order : large_cs_order;
            int best_r = -1, best_c = -1, best_s = -1;
            float best_cost = cur_cost - 1.0f;

            auto probe_window = [&](int cr, int cc, int rad_w) {
                for (int dr = -rad_w; dr <= rad_w; ++dr) {
                    int r = cr + dr;
                    if (r < 1 || r > 109) continue;
                    for (int dc = -rad_w; dc <= rad_w; ++dc) {
                        int c = cc + dc;
                        if (c < 1 || c > 182) continue;
                        if (r == out_r[u] && c == out_c[u]) continue;
                        auto& st = cfus[r * 184 + c];
                        if (!st.valid || st.exclusive || st.used_cnt >= eff_cap) continue;
                        if (u_luts > 0 && eff_cap < 8) {
                            if (st.lut_cnt + u_luts > 5) continue;
                            if (st.mux5_cnt >= 1 && st.c60_cnt >= 3) continue;
                        }
                        int cand_s = -1;
                        bool shared_dom = false;
                        int new_c60 = st.c60_cnt;
                        if (cs < 0) {
                            for (int s = 0; s < 8; ++s) if (!st.slot_used[s]) { cand_s = s; break; }
                        } else {
                            new_c60 = c60_after_add(st, uc0, uc1);
                            for (int k = 0; k < 8; ++k) {
                                int s = slot_ord[k];
                                if (!st.slot_used[s] && st.dom_cs[dom_of_slot(s)] == cs) { cand_s = s; shared_dom = true; break; }
                            }
                            if (cand_s >= 0 && new_c60 > 4) cand_s = -1;
                            if (cand_s < 0 && new_c60 <= 3) {
                                for (int k = 0; k < 8; ++k) {
                                    int s = slot_ord[k];
                                    if (!st.slot_used[s] && st.dom_cs[dom_of_slot(s)] == -1) { cand_s = s; break; }
                                }
                            }
                        }
                        if (cand_s < 0) continue;
                        float nc = shared_dom ? -0.35f : ((cs >= 0 && new_c60 == st.c60_cnt) ? -0.15f : 0.0f);
                        for (int32_t k = u_net_off[u]; k < u_net_off[u + 1]; ++k) {
                            int32_t e = u_nets[k];
                            int32_t b = pnet_offsets[e], en = pnet_offsets[e + 1];
                            int32_t deg = en - b;
                            if (deg < 2) continue;
                            int32_t drv = pnet_units[b];
                            if (u == drv && deg > 96) continue;
                            float w = (deg == 2) ? pnet_weights[e] : (pnet_weights[e] / static_cast<float>(deg - 1));
                            if (u == drv) {
                                for (int32_t p = b + 1; p < en; ++p) {
                                    int32_t v = pnet_units[p];
                                    nc += w * (std::abs(r - r_pos[v]) + std::abs(c - c_pos[v]));
                                }
                            } else {
                                nc += w * (std::abs(r - r_pos[drv]) + std::abs(c - c_pos[drv]));
                            }
                        }
                        if (nc < best_cost) {
                            best_cost = nc; best_r = r; best_c = c; best_s = cand_s;
                        }
                    }
                }
            };

            probe_window(rt, ct, 3);
            if (best_s < 0 && cur_dist > 4) {
                probe_window(rt, ct, 6);
            }
            if (best_s < 0 && cur_dist > 10) {
                probe_window(rt, ct, 10);
            }
            if (best_s < 0 && cur_dist > 12) {
                probe_window((rt + out_r[u]) / 2, (ct + out_c[u]) / 2, 4);
            }

            if (best_s >= 0) {
                int orow = out_r[u], ocol = out_c[u], osl = out_slot[u];
                auto& ost = cfus[orow * 184 + ocol];
                ost.slot_used[osl] = 0;
                ost.slot_unit[osl] = -1;
                --ost.used_cnt;
                ost.lut_cnt -= static_cast<uint8_t>(u_luts);
                if (cs >= 0) {
                    c60_rem_unit(ost, u);
                    int od = dom_of_slot(osl);
                    bool any_ff_left = false;
                    for (int s = 0; s < 8; ++s) {
                        if (dom_of_slot(s) == od && ost.slot_used[s] && ost.slot_unit[s] >= 0 && unit_cs[ost.slot_unit[s]] >= 0) {
                            any_ff_left = true; break;
                        }
                    }
                    if (!any_ff_left) ost.dom_cs[od] = -1;
                }
                auto& nst = cfus[best_r * 184 + best_c];
                nst.slot_used[best_s] = 1;
                nst.slot_unit[best_s] = u;
                if (cs >= 0) {
                    nst.dom_cs[dom_of_slot(best_s)] = cs;
                    c60_add_unit(nst, u);
                }
                ++nst.used_cnt;
                nst.lut_cnt += static_cast<uint8_t>(u_luts);
                out_r[u] = static_cast<int16_t>(best_r);
                out_c[u] = static_cast<int16_t>(best_c);
                out_slot[u] = static_cast<int16_t>(best_s);
                r_pos[u] = static_cast<float>(best_r);
                c_pos[u] = static_cast<float>(best_c);
            }
        }
    }
    double hpwl_p3 = calc_total_hpwl();
    int dom_used = 0, dom_avail = 0, max_c60_used = 0, c60_eq4 = 0;
    for (int r = 1; r <= 109; ++r) {
        for (int c = 1; c <= 182; ++c) {
            const auto& st = cfus[r * 184 + c];
            if (st.valid && !st.exclusive) {
                dom_avail += 3;
                for (int d = 0; d < 3; ++d) if (st.dom_cs[d] != -1) ++dom_used;
                if (st.c60_cnt > max_c60_used) max_c60_used = st.c60_cnt;
                if (st.c60_cnt == 4) ++c60_eq4;
            }
        }
    }
    std::fprintf(stdout, "[ownpr-place] Total HPWL: phase1=%.0f phase2=%.0f phase3=%.0f (ff_pkts=%zu, ff_dom=%d/%d, max_c60=%d, c60_eq4=%d)\n",
                 hpwl_p1, hpwl_p2, hpwl_p3, ff_packets.size(), dom_used, dom_avail, max_c60_used, c60_eq4);
    std::fflush(stdout);
    g_last_cfus = std::move(cfus);

    return 0;
}

EXPORT int32_t bridge_find_legal_slot(
    int32_t u,
    int32_t target_r,
    int32_t target_c,
    int32_t max_rad,
    const int32_t* unit_kind,
    const int32_t* unit_sub,
    const int32_t* unit_cs,
    const int32_t* unit_c60,
    const int16_t* out_r,
    const int16_t* out_c,
    int16_t* cand_r,
    int16_t* cand_c,
    int16_t* cand_s
) {
    if (g_last_cfus.size() != 111 * 184 || u < 0 || !cand_r || !cand_c || !cand_s) return 0;
    int kind = unit_kind[u];
    if (kind != 5 && kind != 6 && kind != 7) return 0;
    if (kind == 5 && unit_sub[u] != 5 && unit_sub[u] != 6) return 0;
    int orow = out_r[u], ocol = out_c[u];
    int rt = std::clamp<int>(target_r, 1, 109);
    int ct = std::clamp<int>(target_c, 1, 182);

    if (kind == 5) {
        int lvl_m = unit_sub[u];
        int need_slots = (lvl_m == 5) ? 2 : 4;
        int32_t mc0 = -1, mc1 = -1;
        cfu_unit_c60_pair(unit_c60, u, mc0, mc1);
        for (int cap_pass = 0; cap_pass < 2; ++cap_pass) {
            int eff_cap = (cap_pass == 0) ? 6 : 7;
            for (int rad = 0; rad <= max_rad; ++rad) {
                for (int dr = -rad; dr <= rad; ++dr) {
                    int r = rt + dr;
                    if (r < 1 || r > 109) continue;
                    int dc_abs = rad - std::abs(dr);
                    for (int sc_i = 0; sc_i < (dc_abs == 0 ? 1 : 2); ++sc_i) {
                        int c = ct + ((sc_i == 0) ? -dc_abs : dc_abs);
                        if (c < 1 || c > 182 || (r == orow && c == ocol)) continue;
                        const auto& st = g_last_cfus[r * 184 + c];
                        if (st.valid != 2 || st.exclusive || st.used_cnt + need_slots > eff_cap) continue;
                        if (st.lut_cnt + need_slots > 6) continue;
                        if (cfu_c60_after_add(st, mc0, mc1) > 3) continue;
                        int s_found = -1;
                        if (lvl_m == 5) {
                            if (st.mux5_cnt > (cap_pass == 0 ? 0 : 1)) continue;
                            const int cand_s0[4] = {6, 4, 2, 0};
                            for (int s0 : cand_s0) {
                                if (!st.slot_used[s0] && !st.slot_used[s0 + 1]) { s_found = s0; break; }
                            }
                        } else {
                            if (!st.slot_used[4] && !st.slot_used[5] && !st.slot_used[6] && !st.slot_used[7]) s_found = 5;
                            else if (!st.slot_used[0] && !st.slot_used[1] && !st.slot_used[2] && !st.slot_used[3]) s_found = 1;
                        }
                        if (s_found >= 0) {
                            *cand_r = static_cast<int16_t>(r);
                            *cand_c = static_cast<int16_t>(c);
                            *cand_s = static_cast<int16_t>(s_found);
                            return 1;
                        }
                    }
                }
            }
        }
        return 0;
    }

    int cs = unit_cs[u];
    int u_luts = (kind == 7 || unit_sub[u] != 0) ? 1 : 0;
    int32_t uc0 = -1, uc1 = -1;
    if (cs >= 0) cfu_unit_c60_pair(unit_c60, u, uc0, uc1);
    const int share_order[8] = {4, 5, 6, 7, 0, 1, 2, 3};
    const int new_order[8]   = {0, 1, 2, 3, 4, 5, 6, 7};

    for (int cap_pass = 0; cap_pass < 2; ++cap_pass) {
        int eff_cap = (cap_pass == 0) ? 6 : 7;
        for (int rad = 0; rad <= max_rad; ++rad) {
            for (int dr = -rad; dr <= rad; ++dr) {
                int r = rt + dr;
                if (r < 1 || r > 109) continue;
                int dc_abs = rad - std::abs(dr);
                for (int sc_i = 0; sc_i < (dc_abs == 0 ? 1 : 2); ++sc_i) {
                    int c = ct + ((sc_i == 0) ? -dc_abs : dc_abs);
                    if (c < 1 || c > 182) continue;
                    if (r == orow && c == ocol) continue;
                    const auto& st = g_last_cfus[r * 184 + c];
                    if (!st.valid || st.exclusive || st.used_cnt >= eff_cap) continue;
                    if (cap_pass == 0 && st.mux5_cnt > 0) continue;
                    if (u_luts > 0) {
                        if (st.lut_cnt + u_luts > 5) continue;
                        if (st.mux5_cnt >= 1 && st.c60_cnt >= 3) continue;
                    }
                    int s_found = -1;
                    if (cs < 0) {
                        for (int s = 0; s < 8; ++s) {
                            if (!st.slot_used[s]) { s_found = s; break; }
                        }
                    } else {
                        int new_c60 = cfu_c60_after_add(st, uc0, uc1);
                        if (new_c60 <= 4) {
                            for (int k = 0; k < 8; ++k) {
                                int s = share_order[k];
                                if (!st.slot_used[s] && st.dom_cs[dom_of_slot(s)] == cs) { s_found = s; break; }
                            }
                        }
                        if (s_found < 0 && new_c60 <= 3) {
                            for (int k = 0; k < 8; ++k) {
                                int s = new_order[k];
                                if (!st.slot_used[s] && st.dom_cs[dom_of_slot(s)] == -1) { s_found = s; break; }
                            }
                        }
                    }
                    if (s_found >= 0) {
                        *cand_r = static_cast<int16_t>(r);
                        *cand_c = static_cast<int16_t>(c);
                        *cand_s = static_cast<int16_t>(s_found);
                        return 1;
                    }
                }
            }
        }
    }
    return 0;
}

EXPORT int32_t bridge_commit_unit_move(
    int32_t u,
    int32_t nr,
    int32_t nc,
    int32_t ns,
    const int32_t* unit_kind,
    const int32_t* unit_sub,
    const int32_t* unit_cs,
    const int32_t* unit_c60,
    int16_t* out_r,
    int16_t* out_c,
    int16_t* out_slot
) {
    if (g_last_cfus.size() != 111 * 184 || u < 0) return 0;
    int kind = unit_kind[u];
    if (kind != 5 && kind != 6 && kind != 7) return 0;
    int orow = out_r[u], ocol = out_c[u], osl = out_slot[u];
    if (orow < 1 || orow > 109 || ocol < 1 || ocol > 182 || osl < 0 || osl > 7) return 0;
    if (nr < 1 || nr > 109 || nc < 1 || nc > 182 || ns < 0 || ns > 7) return 0;
    auto& ost = g_last_cfus[orow * 184 + ocol];
    auto& nst = g_last_cfus[nr * 184 + nc];

    if (kind == 5) {
        int lvl_m = unit_sub[u];
        if (lvl_m != 5 && lvl_m != 6) return 0;
        int need_slots = (lvl_m == 5) ? 2 : 4;
        int obase = (lvl_m == 5) ? osl : ((osl == 5) ? 4 : 0);
        int nbase = (lvl_m == 5) ? ns  : ((ns  == 5) ? 4 : 0);
        for (int k = 0; k < need_slots; ++k) {
            if (!ost.slot_used[obase + k] || ost.slot_unit[obase + k] != u || nst.slot_used[nbase + k]) return 0;
        }
        for (int k = 0; k < need_slots; ++k) {
            ost.slot_used[obase + k] = 0;
            ost.slot_unit[obase + k] = -1;
        }
        ost.used_cnt -= static_cast<uint8_t>(need_slots);
        ost.lut_cnt  -= static_cast<uint8_t>(need_slots);
        if (lvl_m == 5) --ost.mux5_cnt;
        cfu_c60_rem(ost, unit_c60, u);

        for (int k = 0; k < need_slots; ++k) {
            nst.slot_used[nbase + k] = 1;
            nst.slot_unit[nbase + k] = u;
        }
        nst.used_cnt += static_cast<uint8_t>(need_slots);
        nst.lut_cnt  += static_cast<uint8_t>(need_slots);
        if (lvl_m == 5) ++nst.mux5_cnt;
        cfu_c60_add(nst, unit_c60, u);

        out_r[u] = static_cast<int16_t>(nr);
        out_c[u] = static_cast<int16_t>(nc);
        out_slot[u] = static_cast<int16_t>(ns);
        return 1;
    }

    if (!ost.slot_used[osl] || ost.slot_unit[osl] != u || nst.slot_used[ns]) return 0;

    int cs = unit_cs[u];
    int u_luts = (kind == 7 || unit_sub[u] != 0) ? 1 : 0;

    ost.slot_used[osl] = 0;
    ost.slot_unit[osl] = -1;
    --ost.used_cnt;
    ost.lut_cnt -= static_cast<uint8_t>(u_luts);
    if (cs >= 0) {
        cfu_c60_rem(ost, unit_c60, u);
        int od = dom_of_slot(osl);
        bool any_ff_left = false;
        for (int s = 0; s < 8; ++s) {
            if (dom_of_slot(s) == od && ost.slot_used[s] && ost.slot_unit[s] >= 0 && unit_cs[ost.slot_unit[s]] >= 0) {
                any_ff_left = true;
                break;
            }
        }
        if (!any_ff_left) ost.dom_cs[od] = -1;
    }

    nst.slot_used[ns] = 1;
    nst.slot_unit[ns] = u;
    if (cs >= 0) {
        nst.dom_cs[dom_of_slot(ns)] = cs;
        cfu_c60_add(nst, unit_c60, u);
    }
    ++nst.used_cnt;
    nst.lut_cnt += static_cast<uint8_t>(u_luts);

    out_r[u] = static_cast<int16_t>(nr);
    out_c[u] = static_cast<int16_t>(nc);
    out_slot[u] = static_cast<int16_t>(ns);
    return 1;
}

// ============================================================================
// Timing-Driven Negotiated-Congestion PathFinder A* / Multi-Sink Dijkstra Router
// ============================================================================
// net_flags:
//   bit 0 (1): IS_CLOCK (routed first in Phase A, then locked)
//   bit 1 (2): IS_CONST_GND (uses tie vss_idx)
//   bit 2 (4): IS_CONST_VCC (uses tie vcc_idx)

namespace {

struct PQEntry {
    float f;
    float g;
    int32_t u;
    bool operator>(const PQEntry& o) const { return f > o.f; }
};

} // namespace

EXPORT int32_t bridge_route_design(
    int32_t num_nets,
    const uint64_t* net_ptrs,
    const int32_t* net_flags,
    const float* net_crit,
    const int32_t* src_offsets,
    const int32_t* src_ids,
    const int32_t* snk_offsets,
    const int32_t* snk_ids,
    const float* snk_crit,
    int16_t vss_idx,
    int16_t vcc_idx,
    const int32_t* fixed_root_ids,
    const uint64_t* fixed_root_nets,
    int32_t num_fixed_roots,
    const int32_t* blocked_ids,
    int32_t num_blocked,
    int32_t max_iters,
    int32_t* out_iters,
    int32_t* out_overused
) {
    int32_t N = static_cast<int32_t>(g_state.id_to_ptr.size());
    if (N <= 0) return -1;

    std::setvbuf(stdout, nullptr, _IONBF, 0);
    g_state.routed_children.clear();
    g_state.routed_parents.clear();
    g_state.routed_nets.clear();

    // Mark pin ownership & tie nodes
    std::vector<uint64_t> pin_owner(N, 0ULL);
    std::vector<uint8_t> is_tie_root(N, 0);
    std::vector<uint8_t> is_fixed_root(N, 0);
    std::vector<uint8_t> clock_locked(N, 0);

    for (int32_t v = 0; v < N; ++v) {
        int16_t idx = g_state.meta[v].idx;
        if (g_state.meta[v].r >= 0 && g_state.meta[v].c >= 0 && (idx == vss_idx || idx == vcc_idx)) {
            is_tie_root[v] = (idx == vss_idx) ? 1 : 2;
        }
    }
    for (int32_t i = 0; i < num_blocked; ++i) {
        int32_t v = blocked_ids[i];
        if (v >= 0 && v < N) pin_owner[v] = 0xFFFFFFFFFFFFFFFFULL;
    }
    for (int32_t i = 0; i < num_fixed_roots; ++i) {
        int32_t v = fixed_root_ids[i];
        if (v >= 0 && v < N) {
            pin_owner[v] = fixed_root_nets[i];
            is_fixed_root[v] = 1;
            clock_locked[v] = 1; // fixed root cannot be traversed by other nets
        }
    }
    int pin_collisions = 0;
    for (int32_t e = 0; e < num_nets; ++e) {
        uint64_t np = net_ptrs[e];
        for (int32_t k = src_offsets[e]; k < src_offsets[e + 1]; ++k) {
            int32_t v = src_ids[k];
            if (v >= 0 && v < N) {
                if (pin_owner[v] != 0ULL && pin_owner[v] != np) {
                    if (++pin_collisions <= 30) {
                        std::fprintf(stdout, "[pin-collision SRC] net=%d node=(%d,%d,%d) prev=0x%llx new=0x%llx fixed=%d\n",
                                     e, g_state.meta[v].r, g_state.meta[v].c, g_state.meta[v].idx,
                                     static_cast<unsigned long long>(pin_owner[v]), static_cast<unsigned long long>(np),
                                     static_cast<int>(is_fixed_root[v]));
                    }
                }
                pin_owner[v] = np;
            }
        }
        for (int32_t k = snk_offsets[e]; k < snk_offsets[e + 1]; ++k) {
            int32_t v = snk_ids[k];
            if (v >= 0 && v < N) {
                if (pin_owner[v] != 0ULL && pin_owner[v] != np) {
                    if (++pin_collisions <= 30) {
                        std::fprintf(stdout, "[pin-collision SNK] net=%d node=(%d,%d,%d) prev=0x%llx new=0x%llx fixed=%d\n",
                                     e, g_state.meta[v].r, g_state.meta[v].c, g_state.meta[v].idx,
                                     static_cast<unsigned long long>(pin_owner[v]), static_cast<unsigned long long>(np),
                                     static_cast<int>(is_fixed_root[v]));
                    }
                }
                pin_owner[v] = np;
            }
        }
    }
    if (pin_collisions > 0) {
        std::fprintf(stdout, "[pin-collision TOTAL] %d collisions detected!\n", pin_collisions);
        std::fflush(stdout);
        return -998;
    }

    // Build reverse CSR graph once for O(1) 64-pop reverse sink Dijkstra
    int64_t total_rrg_edges = g_state.row_ptr[N];
    std::vector<int32_t> rev_row_ptr(N + 1, 0);
    for (int64_t ei = 0; ei < total_rrg_edges; ++ei) {
        ++rev_row_ptr[g_state.col_idx[ei] + 1];
    }
    for (int32_t u = 0; u < N; ++u) {
        rev_row_ptr[u + 1] += rev_row_ptr[u];
    }
    std::vector<int32_t> rev_col_idx(static_cast<size_t>(total_rrg_edges));
    {
        std::vector<int32_t> rev_cur = rev_row_ptr;
        for (int32_t u = 0; u < N; ++u) {
            int32_t b = g_state.row_ptr[u], e_idx = g_state.row_ptr[u + 1];
            for (int32_t ei = b; ei < e_idx; ++ei) {
                int32_t v = g_state.col_idx[ei];
                rev_col_idx[rev_cur[v]++] = u;
            }
        }
    }

    struct alignas(32) HotNode {
        int32_t closed_stamp;
        int32_t vis_stamp;
        float   best_g;
        int32_t occ;
        float   hist_cost;
        int32_t rev_vis;
        float   rev_after;
        int16_t fwd_r;
        int16_t fwd_c;
    };
    static_assert(sizeof(HotNode) == 32, "HotNode must be 32 bytes (2 per 64-byte cache line)");
    std::vector<HotNode> hot(static_cast<size_t>(N), HotNode{0, 0, 0.0f, 0, 1.0f, 0, 0.0f, 0, 0});
    std::vector<std::vector<std::pair<int32_t, int32_t>>> net_trees(num_nets);

    // Search scratch buffers (stamp-based O(1) reset!)
    std::vector<int32_t> pred(N, -1);
    std::vector<int32_t> in_tree_stamp(N, 0);
    std::vector<int32_t> stem_only_stamp(N, 0);
    std::vector<int32_t> src_net_stamp(N, 0);
    std::vector<int32_t> target_stamp(N, 0);
    std::vector<int32_t> rev_closed(N, 0);
    std::vector<int32_t> rev_pred(N, -1);
    std::vector<float> tree_delay(N, 0.0f);
    int32_t stamp = 1;
    int32_t tree_stamp_gen = 1;
    int32_t tgt_stamp_gen = 1;
    int32_t rev_stamp_gen = 1;

    auto bump_stamp = [&]() -> int32_t {
        if (++stamp >= 0x7ffffff0) {
            for (int32_t i = 0; i < N; ++i) {
                hot[i].vis_stamp = 0;
                hot[i].closed_stamp = 0;
            }
            stamp = 1;
        }
        return stamp;
    };
    auto bump_tree_stamp = [&]() -> int32_t {
        if (++tree_stamp_gen >= 0x7ffffff0) {
            std::fill(in_tree_stamp.begin(), in_tree_stamp.end(), 0);
            std::fill(stem_only_stamp.begin(), stem_only_stamp.end(), 0);
            std::fill(src_net_stamp.begin(), src_net_stamp.end(), 0);
            tree_stamp_gen = 1;
        }
        return tree_stamp_gen;
    };
    auto bump_tgt_stamp = [&]() -> int32_t {
        if (++tgt_stamp_gen >= 0x7ffffff0) {
            std::fill(target_stamp.begin(), target_stamp.end(), 0);
            tgt_stamp_gen = 1;
        }
        return tgt_stamp_gen;
    };
    auto bump_rev_stamp = [&]() -> int32_t {
        if (++rev_stamp_gen >= 0x7ffffff0) {
            for (int32_t i = 0; i < N; ++i) hot[i].rev_vis = 0;
            std::fill(rev_closed.begin(), rev_closed.end(), 0);
            rev_stamp_gen = 1;
        }
        return rev_stamp_gen;
    };

    struct RevPQEntry {
        float f;
        float inc;
        int32_t u;
        bool operator>(const RevPQEntry& o) const { return f > o.f; }
    };
    std::vector<RevPQEntry> rev_pq;
    rev_pq.reserve(32768);

    std::vector<PQEntry> pq_storage;
    pq_storage.reserve(262144);
    int64_t stat_tier2 = 0;
    int64_t stat_tier3 = 0;

    // Spatial bins (16 x 24) for O(1) nearest Steiner tree attachment on high-fanout nets
    constexpr int BIN_R = 16;
    constexpr int BIN_C = 24;
    std::vector<int32_t> tree_bins[BIN_R * BIN_C];
    std::vector<int32_t> used_bin_idxs;
    used_bin_idxs.reserve(BIN_R * BIN_C);
    std::vector<int32_t> seed_buf;
    seed_buf.reserve(512);

    auto add_to_spatial = [&](int32_t nd, bool force_seed) {
        if (!force_seed && (pin_owner[nd] != 0ULL || g_state.meta[nd].fanout_cnt < 2)) return;
        int16_t nr = g_state.eff_r[nd];
        int16_t nc = g_state.eff_c[nd];
        int br = std::clamp(static_cast<int>(nr) / 7, 0, BIN_R - 1);
        int bc = std::clamp(static_cast<int>(nc) / 8, 0, BIN_C - 1);
        int bi = br * BIN_C + bc;
        if (tree_bins[bi].empty()) used_bin_idxs.push_back(bi);
        if (tree_bins[bi].size() < 16) tree_bins[bi].push_back(nd);
    };

    auto clear_spatial = [&]() {
        for (int bi : used_bin_idxs) tree_bins[bi].clear();
        used_bin_idxs.clear();
    };

    // Identify global/local clock spine/branch RRG wires so lookahead and general fabric
    // signals never hijack scarce chip-wide clock trunks (GB/GT/LT/GBO/LBO/SS/SPCLK or r<0).
    std::vector<uint8_t> is_global_clk_wire(N, 0);
    for (int32_t v = 0; v < N; ++v) {
        const auto& m = g_state.meta[v];
        if (m.r < 0 || m.r > 110 || m.c < 0 || m.c > 183) {
            is_global_clk_wire[v] = 1;
        } else if ((m.idx >= 261 && m.idx <= 276) || (m.idx >= 279 && m.idx <= 294) || m.idx >= 315 || m.idx < 0) {
            is_global_clk_wire[v] = 1;
        }
    }

    // Exact mandatory-exit coordinates for directional wire heads (e.g. E800->E804/E808):
    // eliminates head-mux sawtooth barrier in Forward A* and penalizes wrong-way L8 heads immediately.
    for (int32_t u = 0; u < N; ++u) {
        hot[u].fwd_r = g_state.eff_r[u];
        hot[u].fwd_c = g_state.eff_c[u];
    }
    for (int32_t u = 0; u < N; ++u) {
        if (is_global_clk_wire[u]) continue;
        int32_t b = g_state.row_ptr[u], e_i = g_state.row_ptr[u + 1];
        if (b >= e_i) continue;
        int16_t r_lo = 30000, r_hi = -30000, c_lo = 30000, c_hi = -30000;
        int valid = 0;
        for (int32_t ei = b; ei < e_i; ++ei) {
            int32_t w = g_state.col_idx[ei];
            if (is_global_clk_wire[w]) continue;
            int16_t wr = g_state.eff_r[w], wc = g_state.eff_c[w];
            if (wr < r_lo) r_lo = wr;
            if (wr > r_hi) r_hi = wr;
            if (wc < c_lo) c_lo = wc;
            if (wc > c_hi) c_hi = wc;
            ++valid;
        }
        if (valid > 0) {
            int16_t ur = g_state.eff_r[u], uc = g_state.eff_c[u];
            if (r_lo > ur && (r_lo - ur) <= 8) hot[u].fwd_r = r_lo;
            else if (r_hi < ur && (ur - r_hi) <= 8) hot[u].fwd_r = r_hi;
            if (c_lo > uc && (c_lo - uc) <= 8) hot[u].fwd_c = c_lo;
            else if (c_hi < uc && (uc - c_hi) <= 8) hot[u].fwd_c = c_hi;
        }
    }

    // Build exact 2D (dr, dc) VPR-8 Router Lookahead tables once from the real GW5AST-138 RRG!
    // Captures concave multi-length (L1/L2/L8) general fabric wire profile without global spine shortcuts.
    float look_tdel[112][185];
    float look_base[112][185];
    {
        auto t_lk0 = std::chrono::steady_clock::now();
        for (int dr = 0; dr <= 111; ++dr) {
            for (int dc = 0; dc <= 184; ++dc) {
                look_tdel[dr][dc] = 1e9f;
                look_base[dr][dc] = 1e9f;
            }
        }
        look_tdel[0][0] = 0.0f;
        look_base[0][0] = 0.0f;

        const int anchor_rc[2][2] = {{5, 8}, {55, 92}};
        for (int ai = 0; ai < 2; ++ai) {
            int sr = anchor_rc[ai][0], sc = anchor_rc[ai][1];
            // Pick nearest interior CFU tile and seed strictly from LUT/FF outputs F0..Q7 (idx 32..47)
            // so every wire hop pays its true head mux delay setup_ns[v]!
            std::vector<int32_t> lseeds;
            for (int dr0 = 0; dr0 <= 6 && lseeds.size() < 8; ++dr0) {
                for (int dc0 = 0; dc0 <= 6 && lseeds.size() < 8; ++dc0) {
                    lseeds.clear();
                    int tr = sr + dr0, tc = sc + dc0;
                    for (int16_t idx = 32; idx <= 47; ++idx) {
                        int32_t id = bridge_coord_to_id(static_cast<int16_t>(tr), static_cast<int16_t>(tc), idx);
                        if (id >= 0 && !is_global_clk_wire[id] && g_state.meta[id].fanout_cnt >= 8) {
                            lseeds.push_back(id);
                        }
                    }
                    if (lseeds.size() >= 8) {
                        sr = tr;
                        sc = tc;
                        break;
                    }
                }
            }
            if (lseeds.empty()) continue;

            int pop_budget = (ai == 0) ? 950000 : 400000;
            for (int mode = 0; mode < 2; ++mode) {
                float (*tbl)[185] = (mode == 0) ? look_tdel : look_base;
                int32_t lst = bump_stamp();
                pq_storage.clear();
                for (int32_t s : lseeds) {
                    hot[s].vis_stamp = lst;
                    hot[s].best_g = 0.0f;
                    pq_storage.push_back({0.0f, 0.0f, s});
                }
                std::make_heap(pq_storage.begin(), pq_storage.end(), std::greater<PQEntry>{});
                int lpops = 0;
                while (!pq_storage.empty() && lpops < pop_budget) {
                    std::pop_heap(pq_storage.begin(), pq_storage.end(), std::greater<PQEntry>{});
                    PQEntry cur = pq_storage.back();
                    pq_storage.pop_back();
                    if (hot[cur.u].closed_stamp == lst) continue;
                    hot[cur.u].closed_stamp = lst;
                    ++lpops;
                    int16_t uidx = g_state.meta[cur.u].idx;
                    if (!is_global_clk_wire[cur.u] && ((uidx >= 0 && uidx <= 31) || (uidx >= 56 && uidx <= 63))) {
                        int dr = std::min<int>(std::abs(g_state.eff_r[cur.u] - sr), 110);
                        int dc = std::min<int>(std::abs(g_state.eff_c[cur.u] - sc), 183);
                        if (cur.g < tbl[dr][dc]) tbl[dr][dc] = cur.g;
                    }
                    int32_t b = g_state.row_ptr[cur.u], e_i = g_state.row_ptr[cur.u + 1];
                    for (int32_t ei = b; ei < e_i; ++ei) {
                        int32_t v = g_state.col_idx[ei];
                        if (hot[v].closed_stamp == lst) continue;
                        if (is_global_clk_wire[v] || is_tie_root[v]) continue;
                        int raw_span = std::abs(g_state.eff_r[v] - g_state.eff_r[cur.u]) + std::abs(g_state.eff_c[v] - g_state.eff_c[cur.u]);
                        if (raw_span > 10) continue;
                        int span = std::min<int>(raw_span, 16);
                        float ec = (mode == 0)
                            ? (0.02f + g_state.setup_ns[v] + 0.006f * static_cast<float>(span))
                            : (0.06f + g_state.setup_ns[v] + 0.03f * static_cast<float>(span));
                        float ng = cur.g + ec;
                        if (g_state.row_ptr[v] == g_state.row_ptr[v + 1]) {
                            int16_t vidx = g_state.meta[v].idx;
                            if (vidx >= 0 && vidx <= 31) {
                                int dr = std::min<int>(std::abs(g_state.eff_r[v] - sr), 110);
                                int dc = std::min<int>(std::abs(g_state.eff_c[v] - sc), 183);
                                if (ng < tbl[dr][dc]) tbl[dr][dc] = ng;
                            }
                            continue;
                        }
                        if (hot[v].vis_stamp != lst || ng < hot[v].best_g - 1e-6f) {
                            hot[v].vis_stamp = lst;
                            hot[v].best_g = ng;
                            pq_storage.push_back({ng, ng, v});
                            std::push_heap(pq_storage.begin(), pq_storage.end(), std::greater<PQEntry>{});
                        }
                    }
                }
            }
        }
        for (int mode = 0; mode < 2; ++mode) {
            float (*tbl)[185] = (mode == 0) ? look_tdel : look_base;
            tbl[0][0] = 0.0f;
            for (int dr = 110; dr >= 0; --dr) {
                for (int dc = 183; dc >= 0; --dc) {
                    if (dr == 0 && dc == 0) continue;
                    if (dr < 110) tbl[dr][dc] = std::min(tbl[dr][dc], tbl[dr + 1][dc]);
                    if (dc < 183) tbl[dr][dc] = std::min(tbl[dr][dc], tbl[dr][dc + 1]);
                }
            }
            float step_r8 = (tbl[40][0] < 1e8f && tbl[32][0] < 1e8f) ? std::max(0.16f, tbl[40][0] - tbl[32][0]) : ((mode == 0) ? 0.35f : 0.60f);
            float step_c8 = (tbl[0][48] < 1e8f && tbl[0][40] < 1e8f) ? std::max(0.16f, tbl[0][48] - tbl[0][40]) : ((mode == 0) ? 0.35f : 0.60f);
            for (int dr = 0; dr <= 110; ++dr) {
                for (int dc = 0; dc <= 183; ++dc) {
                    if (tbl[dr][dc] <= 1e8f) continue; // NEVER overwrite true RRG Dijkstra entries!
                    if (dr >= 8 && tbl[dr - 8][dc] <= 1e8f) tbl[dr][dc] = std::min(tbl[dr][dc], tbl[dr - 8][dc] + step_r8);
                    if (dc >= 8 && tbl[dr][dc - 8] <= 1e8f) tbl[dr][dc] = std::min(tbl[dr][dc], tbl[dr][dc - 8] + step_c8);
                    if (dr >= 1 && tbl[dr - 1][dc] <= 1e8f) tbl[dr][dc] = std::min(tbl[dr][dc], tbl[dr - 1][dc] + step_r8 * 0.15f);
                    if (dc >= 1 && tbl[dr][dc - 1] <= 1e8f) tbl[dr][dc] = std::min(tbl[dr][dc], tbl[dr][dc - 1] + step_c8 * 0.15f);
                }
            }
            for (int dr = 110; dr >= 0; --dr) {
                for (int dc = 183; dc >= 0; --dc) {
                    if (dr == 0 && dc == 0) continue;
                    if (dr < 110) tbl[dr][dc] = std::min(tbl[dr][dc], tbl[dr + 1][dc]);
                    if (dc < 183) tbl[dr][dc] = std::min(tbl[dr][dc], tbl[dr][dc + 1]);
                }
            }
        }
        int32_t dead_fwd_cnt = 0, dead_rev_cnt = 0;
        for (int32_t v = 0; v < N; ++v) {
            if (g_state.row_ptr[v] == g_state.row_ptr[v + 1]) ++dead_fwd_cnt;
            if (rev_row_ptr[v] == rev_row_ptr[v + 1]) ++dead_rev_cnt;
        }
        double sec_lk = std::chrono::duration<double>(std::chrono::steady_clock::now() - t_lk0).count();
        std::fprintf(stdout, "[ownpr-lookahead] Built 2D wire profile in %.2fs (leaf_prune: fwd=%d rev=%d): base(0,1)=%.3f (0,4)=%.3f (0,8)=%.3f (0,40)=%.3f (20,60)=%.3f (90,150)=%.3f | tdel(0,1)=%.3f (0,4)=%.3f (0,8)=%.3f (20,60)=%.3f\n",
                     sec_lk, dead_fwd_cnt, dead_rev_cnt, look_base[0][1], look_base[0][4], look_base[0][8], look_base[0][40], look_base[20][60], look_base[90][150],
                     look_tdel[0][1], look_tdel[0][4], look_tdel[0][8], look_tdel[20][60]);
        std::fflush(stdout);
    }

    std::vector<int32_t> orig_clk_nets;
    for (int32_t e = 0; e < num_nets && orig_clk_nets.size() < 32; ++e) {
        if (net_flags[e] & 1) orig_clk_nets.push_back(e);
    }
    std::vector<float> clk_tile_arr(orig_clk_nets.size() * 111 * 184, 0.0f);
    std::vector<int32_t> tile_wp(111 * 184 * 4, -1);
    std::vector<uint8_t> tile_wp_cnt(111 * 184, 0);
    for (int32_t v = 0; v < N; ++v) {
        int r = g_state.eff_r[v], c = g_state.eff_c[v];
        if (r < 1 || r > 109 || c < 1 || c > 182) continue;
        int ti = r * 184 + c;
        if (tile_wp_cnt[ti] >= 4) continue;
        if (pin_owner[v] != 0ULL || is_tie_root[v] || is_fixed_root[v] || is_global_clk_wire[v]) continue;
        int16_t idx = g_state.meta[v].idx;
        if (idx < 64 || idx >= 216) continue;
        if ((g_state.row_ptr[v + 1] - g_state.row_ptr[v]) < 4) continue;
        if ((rev_row_ptr[v + 1] - rev_row_ptr[v]) < 2) continue;
        tile_wp[ti * 4 + tile_wp_cnt[ti]++] = v;
    }
    int32_t stat_hold_padded = 0;

    // Compact 1-byte per-node legality & leaf flags + precomputed static IEEE-754 edge costs!
    // Bit layout of node_fast_flags[v]:
    //   0x01: dead_fwd (row_ptr[v] == row_ptr[v+1])
    //   0x02: dead_rev (rev_row_ptr[v] == rev_row_ptr[v+1])
    //   0x04: is_tie_root[v] != 0
    //   0x08: is_fixed_root[v] != 0
    //   0x10: clock_locked[v] != 0
    //   0x20: is_global_clk_wire[v] != 0
    //   0x40: pin_owner[v] != 0ULL
    std::vector<uint8_t> node_fast_flags(N, 0U);
    for (int32_t v = 0; v < N; ++v) {
        uint8_t fl = 0U;
        if (g_state.row_ptr[v] == g_state.row_ptr[v + 1]) fl |= 0x01U;
        if (rev_row_ptr[v] == rev_row_ptr[v + 1]) fl |= 0x02U;
        if (is_tie_root[v]) fl |= 0x04U;
        if (is_fixed_root[v]) fl |= 0x08U;
        if (clock_locked[v]) fl |= 0x10U;
        if (is_global_clk_wire[v]) fl |= 0x20U;
        if (pin_owner[v] != 0ULL) fl |= 0x40U;
        node_fast_flags[v] = fl;
    }
    std::vector<float> fwd_e_tdel(static_cast<size_t>(total_rrg_edges));
    std::vector<float> fwd_e_base(static_cast<size_t>(total_rrg_edges));
    std::vector<uint8_t> fwd_e_nf(static_cast<size_t>(total_rrg_edges) + 64U, 0x04U);
    std::vector<int8_t>  fwd_e_vr(static_cast<size_t>(total_rrg_edges) + 64U, 0);
    std::vector<int8_t>  fwd_e_vcs(static_cast<size_t>(total_rrg_edges) + 64U, -128);
    for (int32_t u = 0; u < N; ++u) {
        int16_t ur = g_state.eff_r[u], uc = g_state.eff_c[u], uidx = g_state.meta[u].idx;
        bool u_c60 = (uidx >= 60 && uidx <= 63);
        int32_t b = g_state.row_ptr[u], e_idx = g_state.row_ptr[u + 1];
        for (int32_t ei = b; ei < e_idx; ++ei) {
            int32_t v = g_state.col_idx[ei];
            int16_t vr = g_state.eff_r[v], vc = g_state.eff_c[v];
            int span = std::min<int>(std::abs(vr - ur) + std::abs(vc - uc), 16);
            int16_t vidx = g_state.meta[v].idx;
            bool steal = u_c60 && (vidx >= 0 && vidx < 32);
            float sv = g_state.setup_ns[v];
            fwd_e_tdel[ei] = 0.02f + (steal ? 0.28f : 0.0f) + sv + 0.006f * static_cast<float>(span);
            fwd_e_base[ei] = 0.06f + (steal ? 0.55f : 0.0f) + sv + 0.03f * static_cast<float>(span);
            fwd_e_nf[ei]   = node_fast_flags[v];
            fwd_e_vr[ei]   = static_cast<int8_t>(std::clamp<int>(vr, 0, 110));
            fwd_e_vcs[ei]  = static_cast<int8_t>(static_cast<uint8_t>(std::clamp<int>(vc, 0, 183)) ^ 0x80U);
        }
    }
    std::vector<float> rev_e_tdel(static_cast<size_t>(total_rrg_edges));
    std::vector<float> rev_e_base(static_cast<size_t>(total_rrg_edges));
    for (int32_t v = 0; v < N; ++v) {
        int16_t vr = g_state.eff_r[v], vc = g_state.eff_c[v], vidx = g_state.meta[v].idx;
        bool v_imux = (vidx >= 0 && vidx < 32);
        float sv = g_state.setup_ns[v];
        int32_t rb = rev_row_ptr[v], re = rev_row_ptr[v + 1];
        for (int32_t rei = rb; rei < re; ++rei) {
            int32_t u = rev_col_idx[rei];
            int span = std::min<int>(std::abs(vr - g_state.eff_r[u]) + std::abs(vc - g_state.eff_c[u]), 16);
            int16_t uidx = g_state.meta[u].idx;
            bool steal = v_imux && (uidx >= 60 && uidx <= 63);
            rev_e_tdel[rei] = 0.02f + (steal ? 0.28f : 0.0f) + sv + 0.006f * static_cast<float>(span);
            rev_e_base[rei] = 0.06f + (steal ? 0.55f : 0.0f) + sv + 0.03f * static_cast<float>(span);
        }
    }

    auto route_single_net = [&](int32_t e, float pres_fac, bool allow_clock_nodes) -> bool {
        uint64_t my_net = net_ptrs[e];
        int32_t flags = net_flags[e];
        float crit = net_crit[e];
        int32_t sb = snk_offsets[e], se = snk_offsets[e + 1];
        int32_t num_sinks = se - sb;

        auto& tree = net_trees[e];
        std::vector<std::pair<int32_t, int32_t>> old_tree;
        if (flags == 0 && !allow_clock_nodes && num_sinks >= 2 && !tree.empty()) {
            old_tree = std::move(tree);
        }
        tree.clear();
        if (num_sinks <= 0) {
            for (int32_t k = src_offsets[e]; k < src_offsets[e + 1]; ++k) {
                int32_t s = src_ids[k];
                if (s >= 0 && s < N) tree.push_back({s, -1});
            }
            return true;
        }

        int32_t ts = bump_tree_stamp();
        std::vector<int32_t> tree_nodes;
        tree_nodes.reserve(static_cast<size_t>(num_sinks) * 6 + 8);

        bool is_gnd = (flags & 2) != 0;
        bool is_vcc = (flags & 4) != 0;

        if (is_gnd || is_vcc) {
            int16_t tie_i = is_gnd ? vss_idx : vcc_idx;
            uint8_t want_tie = is_gnd ? 1 : 2;
            int r_min = 110, r_max = 0, c_min = 183, c_max = 0;
            int valid_sinks = 0;
            for (int32_t si = sb; si < se; ++si) {
                int32_t sk = snk_ids[si];
                if (sk < 0 || sk >= N || in_tree_stamp[sk] == ts) continue;
                int r0 = std::clamp<int>(g_state.eff_r[sk], 1, 109);
                int c0 = std::clamp<int>(g_state.eff_c[sk], 1, 182);
                if (r0 < r_min) r_min = r0;
                if (r0 > r_max) r_max = r0;
                if (c0 < c_min) c_min = c0;
                if (c0 > c_max) c_max = c0;
                ++valid_sinks;
            }
            if (valid_sinks == 0) return true;

            for (int win : {5, 14, 40, 200}) {
                int32_t tgs = bump_tgt_stamp();
                int32_t rem = 0;
                for (int32_t si = sb; si < se; ++si) {
                    int32_t sk = snk_ids[si];
                    if (sk >= 0 && sk < N && in_tree_stamp[sk] != ts && target_stamp[sk] != tgs) {
                        target_stamp[sk] = tgs;
                        ++rem;
                    }
                }
                if (rem == 0) break;

                int32_t st = bump_stamp();
                pq_storage.clear();
                auto push_pq = [&](PQEntry item) {
                    pq_storage.push_back(item);
                    std::push_heap(pq_storage.begin(), pq_storage.end(), std::greater<PQEntry>{});
                };
                auto pop_pq = [&]() -> PQEntry {
                    std::pop_heap(pq_storage.begin(), pq_storage.end(), std::greater<PQEntry>{});
                    PQEntry top = pq_storage.back();
                    pq_storage.pop_back();
                    return top;
                };

                for (int32_t tn : tree_nodes) {
                    if (g_state.meta[tn].fanout_cnt <= 0) continue;
                    hot[tn].vis_stamp = st;
                    hot[tn].best_g = 0.0f;
                    pred[tn] = -1;
                    push_pq({0.0f, 0.0f, tn});
                }
                int r_lo = std::max(0, r_min - win), r_hi = std::min(110, r_max + win);
                int c_lo = std::max(0, c_min - win), c_hi = std::min(183, c_max + win);
                for (int r = r_lo; r <= r_hi; ++r) {
                    for (int c = c_lo; c <= c_hi; ++c) {
                        int32_t tid = bridge_coord_to_id(static_cast<int16_t>(r), static_cast<int16_t>(c), tie_i);
                        if (tid >= 0 && hot[tid].vis_stamp != st) {
                            hot[tid].vis_stamp = st;
                            hot[tid].best_g = 0.0f;
                            pred[tid] = -1;
                            push_pq({0.0f, 0.0f, tid});
                        }
                    }
                }

                bool use_box = (win < 200);
                int br0 = r_lo - 2, br1 = r_hi + 2, bc0 = c_lo - 2, bc1 = c_hi + 2;
                while (!pq_storage.empty() && rem > 0) {
                    PQEntry cur = pop_pq();
                    if (hot[cur.u].closed_stamp == st) continue;
                    hot[cur.u].closed_stamp = st;
                    if (target_stamp[cur.u] == tgs) {
                        target_stamp[cur.u] = 0;
                        --rem;
                        if (rem == 0) break;
                    }
                    int32_t beg = g_state.row_ptr[cur.u];
                    int32_t end = g_state.row_ptr[cur.u + 1];
                    for (int32_t ei = beg; ei < end; ++ei) {
                        int32_t v = g_state.col_idx[ei];
                        if (target_stamp[v] != tgs && g_state.row_ptr[v] == g_state.row_ptr[v + 1]) continue;
                        if (hot[v].closed_stamp == st) continue;
                        if (is_tie_root[v] || is_fixed_root[v]) continue;
                        if (!allow_clock_nodes && (clock_locked[v] || is_global_clk_wire[v]) && pin_owner[v] != my_net) continue;
                        uint64_t po = pin_owner[v];
                        if (po != 0ULL && po != my_net) continue;
                        if (use_box && target_stamp[v] != tgs) {
                            int16_t vr = g_state.eff_r[v], vc = g_state.eff_c[v];
                            if (vr < br0 || vr > br1 || vc < bc0 || vc > bc1) continue;
                        }
                        float e_base = 0.08f + g_state.setup_ns[v];
                        float p_cost = (hot[v].occ > 0) ? (1.0f + pres_fac * static_cast<float>(hot[v].occ)) : 1.0f;
                        float sc = e_base * hot[v].hist_cost * p_cost;
                        float ng = cur.g + sc;
                        if (hot[v].vis_stamp != st || ng < hot[v].best_g - 1e-6f) {
                            hot[v].vis_stamp = st;
                            hot[v].best_g = ng;
                            pred[v] = cur.u;
                            push_pq({ng, ng, v});
                        }
                    }
                }

                for (int32_t si = sb; si < se; ++si) {
                    int32_t sk = snk_ids[si];
                    if (sk < 0 || sk >= N || in_tree_stamp[sk] == ts) continue;
                    if (hot[sk].vis_stamp != st) continue;
                    std::vector<int32_t> branch;
                    int32_t cur = sk;
                    bool branch_overused = false;
                    while (cur != -1 && in_tree_stamp[cur] != ts) {
                        if (!is_tie_root[cur] && hot[cur].occ > 0) branch_overused = true;
                        branch.push_back(cur);
                        cur = pred[cur];
                    }
                    if (branch_overused && pres_fac >= 1.0f && win < 40) continue;
                    std::reverse(branch.begin(), branch.end());
                    if (!branch.empty() && cur == -1 && is_tie_root[branch[0]] != want_tie) return false;
                    for (size_t k = 0; k < branch.size(); ++k) {
                        int32_t node = branch[k];
                        int32_t par = (k == 0) ? cur : branch[k - 1];
                        in_tree_stamp[node] = ts;
                        tree_nodes.push_back(node);
                        tree.push_back({node, par});
                    }
                }
            }
            for (int32_t si = sb; si < se; ++si) {
                int32_t sk = snk_ids[si];
                if (sk >= 0 && sk < N && in_tree_stamp[sk] != ts) {
                    std::fprintf(stdout, "[ownpr-router] CONST FAIL net=%d sink=(%d,%d,%d)\n", e, g_state.meta[sk].r, g_state.meta[sk].c, g_state.meta[sk].idx);
                    std::fflush(stdout);
                    return false;
                }
            }
            return true;
        }

        // Normal signal or clock net
        int32_t src_b = src_offsets[e], src_e = src_offsets[e + 1];
        if (src_b >= src_e) return false;
        clear_spatial();
        for (int32_t k = src_b; k < src_e; ++k) {
            int32_t s = src_ids[k];
            if (s >= 0 && s < N && in_tree_stamp[s] != ts) {
                in_tree_stamp[s] = ts;
                src_net_stamp[s] = ts;
                tree_delay[s] = 0.0f;
                tree_nodes.push_back(s);
                tree.push_back({s, -1});
                add_to_spatial(s, true);
            }
        }

        // Global clock nets ONLY (Phase A, flags & 1):
        // Route core fabric + BSRAM/DSP clocks through a single Full-Chip Symmetric Center Spine Root
        // (r == -1 && c == -1 && fo == 2 && idx >= 269), which branches symmetrically into both Top Half
        // (-1, -2) and Bottom Half (-2, -1) with ~0.01 ns cross-half skew!
        if ((flags & 1) != 0) {
            int32_t core_clk_sinks = 0;
            for (int32_t si = sb; si < se; ++si) {
                int32_t sk = snk_ids[si];
                if (sk >= 0 && sk < N) {
                    int16_t sr = g_state.meta[sk].r, sc_m = g_state.meta[sk].c, si_m = g_state.meta[sk].idx;
                    if (sr >= 1 && sr <= 109 && sc_m >= 1 && sc_m <= 182) {
                        ++core_clk_sinks;
                    }
                }
            }

            auto push_pq = [&](PQEntry item) {
                pq_storage.push_back(item);
                std::push_heap(pq_storage.begin(), pq_storage.end(), std::greater<PQEntry>{});
            };
            auto pop_pq = [&]() -> PQEntry {
                std::pop_heap(pq_storage.begin(), pq_storage.end(), std::greater<PQEntry>{});
                PQEntry top = pq_storage.back();
                pq_storage.pop_back();
                return top;
            };

            int32_t chosen_cmux_root = -1;
            int32_t fallback_asym_cmux = -1;
            int32_t fallback_half_cmux = -1;
            bool is_asym_gb_root = false;
            if (core_clk_sinks >= 2) {
                // Step A1: Prefer one of the 12 True Full-Chip Symmetric GT Center Spine roots
                // (r == -1 && c == -1 && fo == 2 && idx in 291..302), which branch symmetrically via GT (291->293->269..276)
                // into BOTH Top Half (-1, -2) and Bottom Half (-2, -1) for all CFU, BSRAM, and DSP clock pins (~0.002ns skew)!
                int32_t st_stem = bump_stamp();
                pq_storage.clear();
                for (int32_t tn : tree_nodes) {
                    if (g_state.meta[tn].fanout_cnt <= 0) continue;
                    hot[tn].vis_stamp = st_stem;
                    hot[tn].best_g = 0.0f;
                    pred[tn] = -1;
                    push_pq({0.0f, 0.0f, tn});
                }
                while (!pq_storage.empty()) {
                    PQEntry cur = pop_pq();
                    if (hot[cur.u].closed_stamp == st_stem) continue;
                    hot[cur.u].closed_stamp = st_stem;
                    const auto& mu = g_state.meta[cur.u];
                    if (mu.r == -1 && mu.c == -1 && mu.fanout_cnt == 2 && mu.idx >= 291 && mu.idx <= 302 && hot[cur.u].occ == 0) {
                        chosen_cmux_root = cur.u;
                        break;
                    }
                    if (fallback_asym_cmux < 0 && mu.r == -1 && mu.c == -1 && mu.fanout_cnt == 2 && mu.idx >= 269 && mu.idx <= 284 && hot[cur.u].occ == 0) {
                        fallback_asym_cmux = cur.u;
                    }
                    if (fallback_half_cmux < 0 && mu.r < 0 && mu.c < 0 && mu.fanout_cnt >= 80 && mu.fanout_cnt <= 120 && hot[cur.u].occ == 0) {
                        fallback_half_cmux = cur.u;
                    }
                    int32_t beg = g_state.row_ptr[cur.u];
                    int32_t end = g_state.row_ptr[cur.u + 1];
                    for (int32_t ei = beg; ei < end; ++ei) {
                        int32_t v = g_state.col_idx[ei];
                        if (hot[v].closed_stamp == st_stem) continue;
                        if (is_tie_root[v] || is_fixed_root[v]) continue;
                        uint64_t po = pin_owner[v];
                        if (po != 0ULL && po != my_net) continue;
                        const auto& mv = g_state.meta[v];
                        // From 124..126 nodes, only allow dedicated Global Clock Mux entry (mv.r < 0), never expand into local fabric
                        if (mu.r >= 1 && mu.r <= 109 && mu.idx >= 124 && mu.idx <= 126 && mv.r >= 0) continue;
                        // Do not tap GB/LB spines back out into general fabric during stem search
                        if (mu.r >= 1 && mu.r <= 109 && ((mu.idx >= 261 && mu.idx <= 276) || (mu.idx >= 291 && mu.idx <= 294))) {
                            if (mv.r >= 1 && mv.r <= 109 && mv.c >= 1 && mv.c <= 182 && mv.idx < 261) continue;
                        }
                        float sc = (hot[v].occ == 0)
                            ? ((0.03f + g_state.setup_ns[v]) * (1.0f + 0.5f * (hot[v].hist_cost - 1.0f)))
                            : ((0.08f + g_state.setup_ns[v]) * (1.0f + hot[v].occ * pres_fac) * hot[v].hist_cost);
                        float ng = cur.g + sc;
                        if (hot[v].vis_stamp != st_stem || ng < hot[v].best_g - 1e-6f) {
                            hot[v].vis_stamp = st_stem;
                            hot[v].best_g = ng;
                            pred[v] = cur.u;
                            push_pq({ng, ng, v});
                        }
                    }
                }
                if (chosen_cmux_root < 0 && fallback_asym_cmux >= 0) {
                    chosen_cmux_root = fallback_asym_cmux;
                    is_asym_gb_root = true;
                }
                if (chosen_cmux_root < 0) chosen_cmux_root = fallback_half_cmux;
                if (chosen_cmux_root >= 0 && in_tree_stamp[chosen_cmux_root] != ts) {
                    std::vector<int32_t> stem_br;
                    int32_t cur = chosen_cmux_root;
                    while (cur != -1 && in_tree_stamp[cur] != ts) {
                        stem_br.push_back(cur);
                        cur = pred[cur];
                    }
                    if (cur != -1) {
                        // Mark all upstream stem nodes before chosen_cmux_root as stem_only so interior sinks
                        // NEVER tap an upstream half-chip/local shortcut!
                        for (int32_t orig_tn : tree_nodes) {
                            stem_only_stamp[orig_tn] = ts;
                        }
                        std::reverse(stem_br.begin(), stem_br.end());
                        for (size_t k = 0; k < stem_br.size(); ++k) {
                            int32_t node = stem_br[k];
                            int32_t par = (k == 0) ? cur : stem_br[k - 1];
                            in_tree_stamp[node] = ts;
                            if (k + 1 < stem_br.size()) {
                                stem_only_stamp[node] = ts;
                            }
                            tree_nodes.push_back(node);
                            tree.push_back({node, par});
                        }
                    } else {
                        chosen_cmux_root = -1;
                    }
                }
            }

            int start_pass = (core_clk_sinks >= 2 && chosen_cmux_root >= 0) ? 0 : 1;
            for (int pass = start_pass; pass <= 4; ++pass) {
                int32_t tgs = bump_tgt_stamp();
                int32_t rem = 0;
                for (int32_t si = sb; si < se; ++si) {
                    int32_t sk = snk_ids[si];
                    if (sk >= 0 && sk < N && in_tree_stamp[sk] != ts && target_stamp[sk] != tgs) {
                        if (pass == 0) {
                            const auto& msk = g_state.meta[sk];
                            if (!(msk.r >= 1 && msk.r <= 109 && msk.c >= 1 && msk.c <= 182 && msk.idx >= 124 && msk.idx <= 126)) {
                                continue;
                            }
                        }
                        target_stamp[sk] = tgs;
                        ++rem;
                    }
                }
                if (rem == 0) continue;

                int32_t st = bump_stamp();
                pq_storage.clear();
                if (pass == 0 && chosen_cmux_root >= 0) {
                    hot[chosen_cmux_root].vis_stamp = st;
                    hot[chosen_cmux_root].best_g = 0.0f;
                    pred[chosen_cmux_root] = -1;
                    push_pq({0.0f, 0.0f, chosen_cmux_root});
                } else if (pass <= 2 && chosen_cmux_root >= 0) {
                    for (int32_t tn : tree_nodes) {
                        if (stem_only_stamp[tn] == ts || g_state.meta[tn].fanout_cnt <= 0) continue;
                        hot[tn].vis_stamp = st;
                        hot[tn].best_g = 0.0f;
                        pred[tn] = -1;
                        push_pq({0.0f, 0.0f, tn});
                    }
                } else {
                    for (int32_t tn : tree_nodes) {
                        if (g_state.meta[tn].fanout_cnt <= 0) continue;
                        hot[tn].vis_stamp = st;
                        hot[tn].best_g = 0.0f;
                        pred[tn] = -1;
                        push_pq({0.0f, 0.0f, tn});
                    }
                }

                while (!pq_storage.empty() && rem > 0) {
                    PQEntry cur = pop_pq();
                    if (hot[cur.u].closed_stamp == st) continue;
                    hot[cur.u].closed_stamp = st;
                    if (target_stamp[cur.u] == tgs) {
                        target_stamp[cur.u] = 0;
                        --rem;
                        if (rem == 0) break;
                    }
                    const auto& mu = g_state.meta[cur.u];
                    if (pass == 0 && mu.r >= 1 && mu.r <= 109 && mu.idx >= 124 && mu.idx <= 126) continue;
                    int32_t beg = g_state.row_ptr[cur.u];
                    int32_t end = g_state.row_ptr[cur.u + 1];
                    for (int32_t ei = beg; ei < end; ++ei) {
                        int32_t v = g_state.col_idx[ei];
                        if (hot[v].closed_stamp == st) continue;
                        if (is_tie_root[v] || is_fixed_root[v]) continue;
                        if (pass <= 2 && chosen_cmux_root >= 0 && stem_only_stamp[v] == ts) continue;
                        if (!allow_clock_nodes && (clock_locked[v] || is_global_clk_wire[v]) && pin_owner[v] != my_net) continue;
                        uint64_t po = pin_owner[v];
                        if (po != 0ULL && po != my_net) continue;
                        const auto& mv = g_state.meta[v];
                        if (pass == 0) {
                            if (mv.r == -1 && mv.c == -1) continue; // Do not hop across another (-1, -1) root
                            bool pure_spine = (mv.r < 0) || (
                                mv.r >= 1 && mv.r <= 109 && (
                                    mv.idx == 285 || mv.idx == 297 ||
                                    (mv.idx >= 291 && mv.idx <= 294) ||
                                    (mv.idx >= 269 && mv.idx <= 276) ||
                                    (mv.idx >= 124 && mv.idx <= 126) ||
                                    (is_asym_gb_root && (mv.idx == 316 || (mv.idx >= 280 && mv.idx <= 288) || (mv.idx >= 261 && mv.idx <= 268)))
                                )
                            );
                            if (!pure_spine) continue;
                            if (mv.r >= 1 && mv.r <= 109 && mv.idx >= 124 && mv.idx <= 126) {
                                int lo_hclk = is_asym_gb_root ? 261 : 269;
                                if (!(mu.r >= 1 && mu.r <= 109 && mu.idx >= lo_hclk && mu.idx <= 276)) continue;
                            }
                        } else if (pass == 1) {
                            if (mv.r == -1 && mv.c == -1) continue;
                            // Keep GB/LB branches from tapping out into general interior fabric unless pass 2 is required
                            if (mu.r >= 1 && mu.r <= 109 && ((mu.idx >= 261 && mu.idx <= 276) || (mu.idx >= 291 && mu.idx <= 294))) {
                                if (mv.r >= 1 && mv.r <= 109 && mv.c >= 1 && mv.c <= 182 && mv.idx < 261 && !(mv.idx >= 124 && mv.idx <= 126) && target_stamp[v] != tgs) {
                                    continue;
                                }
                            }
                        } else if (pass == 3) {
                            // Pass 3 is purely for border dedicated clock targets (e.g. PLL -> CLKDIV.HCLKIN); never enter interior general fabric
                            if (mv.r >= 1 && mv.r <= 109 && mv.c >= 1 && mv.c <= 182 && mv.idx < 261 && target_stamp[v] != tgs) {
                                continue;
                            }
                        }
                        float sc;
                        if (hot[v].occ == 0) {
                            sc = (0.04f + g_state.setup_ns[v]) * (1.0f + (hot[v].hist_cost - 1.0f) * (1.0f - 0.5f * crit));
                            if (pass == 0 && mv.r >= 1 && mv.r <= 109 && (mv.idx == 291 || mv.idx == 292) && !(mu.r >= 1 && mu.r <= 109 && (mu.idx == 291 || mu.idx == 292))) {
                                sc += 0.60f; // Encourage 1 single GT trunk per quadrant
                            }
                        } else {
                            sc = (0.06f + g_state.setup_ns[v]) * (1.0f + hot[v].occ * pres_fac) * hot[v].hist_cost;
                        }
                        float ng = cur.g + sc;
                        if (hot[v].vis_stamp != st || ng < hot[v].best_g - 1e-6f) {
                            hot[v].vis_stamp = st;
                            hot[v].best_g = ng;
                            pred[v] = cur.u;
                            push_pq({ng, ng, v});
                        }
                    }
                }
                for (int32_t si = sb; si < se; ++si) {
                    int32_t sk = snk_ids[si];
                    if (sk < 0 || sk >= N || in_tree_stamp[sk] == ts) continue;
                    if (hot[sk].vis_stamp != st) continue;
                    std::vector<int32_t> branch;
                    int32_t cur = sk;
                    while (cur != -1 && in_tree_stamp[cur] != ts) {
                        branch.push_back(cur);
                        cur = pred[cur];
                    }
                    if (cur == -1) return false;
                    std::reverse(branch.begin(), branch.end());
                    for (size_t k = 0; k < branch.size(); ++k) {
                        int32_t node = branch[k];
                        int32_t par = (k == 0) ? cur : branch[k - 1];
                        in_tree_stamp[node] = ts;
                        tree_nodes.push_back(node);
                        tree.push_back({node, par});
                    }
                }
                bool all_done = true;
                for (int32_t si = sb; si < se; ++si) {
                    int32_t sk = snk_ids[si];
                    if (sk >= 0 && sk < N && in_tree_stamp[sk] != ts) { all_done = false; break; }
                }
                if (all_done) break;
            }
            for (int32_t si = sb; si < se; ++si) {
                int32_t sk = snk_ids[si];
                if (sk >= 0 && sk < N && in_tree_stamp[sk] != ts) {
                    std::fprintf(stdout, "[ownpr-router] MULTI-SINK FAIL net=%d flags=%d sinks=%d src0=(%d,%d,%d)\n",
                                 e, flags, num_sinks, g_state.meta[src_ids[src_b]].r, g_state.meta[src_ids[src_b]].c, g_state.meta[src_ids[src_b]].idx);
                    std::fflush(stdout);
                    return false;
                }
            }
            return true;
        }

        // All Signal Nets: Per-Sink Criticality + Upstream-Delay Steiner Trees + Hold Skew Padding!
        int32_t s0 = src_ids[src_b];
        int16_t s0_r = g_state.eff_r[s0];
        int16_t s0_c = g_state.eff_c[s0];

        struct SinkInfo {
            int32_t sk;
            float sk_c;
            float req_min_del;
        };
        std::vector<SinkInfo> dedup_sinks;
        dedup_sinks.reserve(num_sinks);
        for (int32_t si = sb; si < se; ++si) {
            int32_t sk = snk_ids[si];
            if (sk >= 0 && sk < N) {
                float sk_c_v = crit;
                float req_min_v = 0.0f;
                if (snk_crit) {
                    float raw_sc = snk_crit[si];
                    if (raw_sc >= 0.0f) {
                        sk_c_v = raw_sc;
                    } else if (raw_sc <= -100.0f) {
                        sk_c_v = 0.06f;
                        req_min_v = (-raw_sc) - 100.0f;
                    } else {
                        int ci = static_cast<int>(std::floor(-raw_sc + 1e-4f)) - 1;
                        sk_c_v = std::clamp((-raw_sc) - static_cast<float>(ci + 1), 0.04f, 0.56f);
                        if (ci >= 0 && static_cast<size_t>(ci) < orig_clk_nets.size()) {
                            int sr = std::clamp<int>(s0_r, 1, 109), sc_i = std::clamp<int>(s0_c, 1, 182);
                            int dr = std::clamp<int>(g_state.eff_r[sk], 1, 109), dc_i = std::clamp<int>(g_state.eff_c[sk], 1, 182);
                            float arr_s = clk_tile_arr[static_cast<size_t>(ci) * 20424 + sr * 184 + sc_i];
                            float arr_d = clk_tile_arr[static_cast<size_t>(ci) * 20424 + dr * 184 + dc_i];
                            if (arr_s > 0.0f && arr_d > 0.0f && (arr_d - arr_s) > 0.22f) {
                                req_min_v = (arr_d - arr_s) + 0.28f;
                            }
                        }
                    }
                }
                dedup_sinks.push_back({sk, sk_c_v, req_min_v});
            }
        }
        std::sort(dedup_sinks.begin(), dedup_sinks.end(), [](const SinkInfo& a, const SinkInfo& b) {
            return a.sk != b.sk ? (a.sk < b.sk) : (a.sk_c > b.sk_c);
        });
        {
            size_t wr = 0;
            for (size_t rd = 0; rd < dedup_sinks.size(); ++rd) {
                if (wr == 0 || dedup_sinks[rd].sk != dedup_sinks[wr - 1].sk) {
                    dedup_sinks[wr++] = dedup_sinks[rd];
                } else {
                    dedup_sinks[wr - 1].sk_c = std::max(dedup_sinks[wr - 1].sk_c, dedup_sinks[rd].sk_c);
                    dedup_sinks[wr - 1].req_min_del = std::max(dedup_sinks[wr - 1].req_min_del, dedup_sinks[rd].req_min_del);
                }
            }
            dedup_sinks.resize(wr);
        }
        auto get_sk_info = [&](int32_t nd) -> SinkInfo {
            auto it = std::lower_bound(dedup_sinks.begin(), dedup_sinks.end(), SinkInfo{nd, 0.0f, 0.0f},
                                       [](const SinkInfo& a, const SinkInfo& b) { return a.sk < b.sk; });
            if (it != dedup_sinks.end() && it->sk == nd) return *it;
            return SinkInfo{nd, 0.5f * crit, 0.0f};
        };

        float c_crit = std::min(crit, 0.85f);
        auto eval_edge_idx = [&](int32_t v, float e_tdel, float e_base) -> float {
            int32_t ov = hot[v].occ;
            float p_cost = (ov > 0) ? (1.0f + pres_fac * static_cast<float>(ov)) : 1.0f;
            float hv_raw = hot[v].hist_cost;
            float h_v = (ov == 0 && c_crit >= 0.86f) ? (1.0f + 0.25f * (hv_raw - 1.0f)) : hv_raw;
            float cong_cost = e_base * h_v * p_cost;
            return c_crit * e_tdel + (1.0f - c_crit) * cong_cost;
        };
        auto eval_edge = [&](int32_t u, int32_t v) -> float {
            int span = std::min<int>(std::abs(g_state.eff_r[v] - g_state.eff_r[u]) + std::abs(g_state.eff_c[v] - g_state.eff_c[u]), 16);
            int16_t u_idx = g_state.meta[u].idx;
            int16_t v_idx = g_state.meta[v].idx;
            bool steal_c60 = (u_idx >= 60 && u_idx <= 63 && v_idx >= 0 && v_idx < 32);
            float imux_tdel = steal_c60 ? 0.28f : 0.0f;
            float imux_bias = steal_c60 ? 0.55f : 0.0f;
            float e_tdel = 0.02f + imux_tdel + g_state.setup_ns[v] + 0.006f * static_cast<float>(span);
            float e_base = 0.06f + imux_bias + g_state.setup_ns[v] + 0.03f * static_cast<float>(span);
            return eval_edge_idx(v, e_tdel, e_base);
        };
        auto node_enter_lb = [&](int32_t u) -> float {
            float su = g_state.setup_ns[u];
            return eval_edge_idx(u, 0.02f + su, 0.06f + su);
        };
        bool allow_global_spine = allow_clock_nodes;
        uint8_t disallow_mask = static_cast<uint8_t>(
            0x0CU | // NF_TIE (bits 2,3) + NF_FIXED (bit 2)
            (allow_clock_nodes ? 0U : 0x10U) | // NF_CLK_LOCKED (bit 4)
            (allow_global_spine ? 0U : 0x20U)  // NF_GLOBAL_CLK (bit 5)
        );
        auto refresh_disallow_mask = [&]() {
            disallow_mask = static_cast<uint8_t>(
                0x0CU |
                (allow_clock_nodes ? 0U : 0x10U) |
                (allow_global_spine ? 0U : 0x20U)
            );
        };
        auto is_legal_node = [&](int32_t v) -> bool {
            uint8_t nf = node_fast_flags[v];
            if ((nf & 0x7CU) == 0U) return true; // 99.5% fast single-byte path!
            if (nf & 0x40U) {
                uint64_t po = pin_owner[v];
                if (po == my_net) return (nf & 0x04U) == 0U;
                if (po != 0ULL) return false;
            }
            return (nf & disallow_mask) == 0U;
        };

        // Incremental Partial Subtree Retention on multi-sink signal nets:
        // Retain conflict-free (occ == 0 after ripup) upstream branches that feed at least one sink
        // (dropping high-delay detoured branches on high-criticality sinks, or under-padded Hold branches).
        if (!old_tree.empty()) {
            int32_t sk_st = bump_tgt_stamp();
            for (const auto& kv : dedup_sinks) {
                target_stamp[kv.sk] = sk_st;
            }
            int32_t clean_st = bump_stamp();
            for (int32_t s : tree_nodes) {
                hot[s].vis_stamp = clean_st;
                hot[s].best_g = 0.0f;
                pred[s] = -1;
            }
            std::vector<uint8_t> keep_flag(old_tree.size(), 0);
            for (size_t i = 0; i < old_tree.size(); ++i) {
                int32_t nd = old_tree[i].first;
                int32_t pr = old_tree[i].second;
                if (pr == -1 || nd < 0 || nd >= N || pr < 0 || pr >= N) continue;
                if (hot[pr].vis_stamp == clean_st && hot[nd].occ == 0 && is_legal_node(nd)) {
                    hot[nd].vis_stamp = clean_st;
                    hot[nd].best_g = hot[pr].best_g + g_state.setup_ns[nd];
                    pred[nd] = static_cast<int32_t>(i);
                    if (target_stamp[nd] == sk_st) {
                        SinkInfo sinf = get_sk_info(nd);
                        int dr_s = std::min<int>(std::abs(g_state.eff_r[nd] - s0_r), 110);
                        int dc_s = std::min<int>(std::abs(g_state.eff_c[nd] - s0_c), 183);
                        float ideal_g = look_tdel[dr_s][dc_s];
                        float det_tol = (sinf.sk_c >= 0.90f) ? 0.16f : ((sinf.sk_c >= 0.75f) ? 0.28f : 0.45f);
                        bool slow_crit = (sinf.sk_c >= 0.65f && hot[nd].best_g > ideal_g + det_tol);
                        bool short_hold = (sinf.req_min_del > 0.0f && hot[nd].best_g < sinf.req_min_del);
                        if (!slow_crit && !short_hold) {
                            keep_flag[i] = 1;
                        }
                    }
                }
            }
            for (int32_t i = static_cast<int32_t>(old_tree.size()) - 1; i >= 0; --i) {
                if (!keep_flag[i]) continue;
                int32_t pr = old_tree[i].second;
                if (pr >= 0 && in_tree_stamp[pr] != ts && hot[pr].vis_stamp == clean_st) {
                    int32_t pi = pred[pr];
                    if (pi >= 0) keep_flag[pi] = 1;
                }
            }
            for (size_t i = 0; i < old_tree.size(); ++i) {
                if (!keep_flag[i]) continue;
                int32_t nd = old_tree[i].first;
                int32_t pr = old_tree[i].second;
                if (in_tree_stamp[nd] != ts) {
                    in_tree_stamp[nd] = ts;
                    tree_delay[nd] = tree_delay[pr] + g_state.setup_ns[nd];
                    tree_nodes.push_back(nd);
                    tree.push_back({nd, pr});
                    add_to_spatial(nd, false);
                }
            }
        }

        std::vector<SinkInfo> ordered_sinks;
        ordered_sinks.reserve(dedup_sinks.size());
        for (const auto& kv : dedup_sinks) {
            if (in_tree_stamp[kv.sk] != ts) {
                ordered_sinks.push_back(kv);
            }
        }
        if (ordered_sinks.size() > 1) {
            std::sort(ordered_sinks.begin(), ordered_sinks.end(), [&](const SinkInfo& a, const SinkInfo& b) {
                float tol = (std::max(a.sk_c, b.sk_c) >= 0.90f) ? 0.012f : 0.05f;
                if (std::abs(a.sk_c - b.sk_c) > tol) return a.sk_c > b.sk_c;
                int da = std::abs(g_state.eff_r[a.sk] - s0_r) + std::abs(g_state.eff_c[a.sk] - s0_c);
                int db = std::abs(g_state.eff_r[b.sk] - s0_r) + std::abs(g_state.eff_c[b.sk] - s0_c);
                return da != db ? da < db : a.sk < b.sk;
            });
        }

        auto push_rev = [&](RevPQEntry item) {
            rev_pq.push_back(item);
            std::push_heap(rev_pq.begin(), rev_pq.end(), std::greater<RevPQEntry>{});
        };
        auto pop_rev = [&]() -> RevPQEntry {
            std::pop_heap(rev_pq.begin(), rev_pq.end(), std::greater<RevPQEntry>{});
            RevPQEntry top = rev_pq.back();
            rev_pq.pop_back();
            return top;
        };
        auto push_pq = [&](PQEntry item) {
            pq_storage.push_back(item);
            std::push_heap(pq_storage.begin(), pq_storage.end(), std::greater<PQEntry>{});
        };
        auto pop_pq = [&]() -> PQEntry {
            std::pop_heap(pq_storage.begin(), pq_storage.end(), std::greater<PQEntry>{});
            PQEntry top = pq_storage.back();
            pq_storage.pop_back();
            return top;
        };

        for (const auto& sk_info : ordered_sinks) {
            int32_t sk = sk_info.sk;
            float sk_c = sk_info.sk_c;
            float req_min_del = sk_info.req_min_del;
            if (in_tree_stamp[sk] == ts) continue;
            c_crit = (sk_c >= 0.92f) ? std::min(sk_c, 0.985f) : std::min(sk_c, 0.96f);
            float up_w = (sk_c >= 0.92f) ? 0.97f : ((sk_c >= 0.55f) ? std::min(sk_c, 0.92f) : std::max(0.32f, 0.75f * sk_c));
            int16_t dst_r = g_state.eff_r[sk];
            int16_t dst_c = g_state.eff_c[sk];
            int direct_dr = std::min<int>(std::abs(dst_r - s0_r), 110);
            int direct_dc = std::min<int>(std::abs(dst_c - s0_c), 183);
            float ideal_direct_d = look_tdel[direct_dr][direct_dc];
            float max_up_del = (sk_c >= 0.90f) ? (ideal_direct_d + 0.18f)
                             : ((sk_c >= 0.72f) ? (ideal_direct_d + 0.32f)
                             : ((sk_c >= 0.50f) ? (ideal_direct_d + 0.55f)
                             : std::max(1.80f, 1.65f * ideal_direct_d + 1.15f)));

            // Fast 28-pop local Reverse Dijkstra (including 1-sink same/adjacent-tile nets when req_min_del <= 0):
            // accounts for upstream tree_delay[u] and finds direct 0.136ns local IMUX connections immediately!
            if (req_min_del <= 0.0f && tree_nodes.size() >= 1) {
                int32_t cs0 = bump_rev_stamp();
                rev_pq.clear();
                hot[sk].rev_vis = cs0;
                hot[sk].rev_after = 0.0f;
                rev_pred[sk] = -1;
                push_rev({0.0f, 0.0f, sk});

                int rpops = 0;
                int32_t direct_tree_u = -1;
                int32_t direct_rev_v = -1;
                float direct_cost = 1e35f;
                while (!rev_pq.empty() && rpops < 28) {
                    RevPQEntry rcur = pop_rev();
                    int32_t v = rcur.u;
                    if (rev_closed[v] == cs0) continue;
                    if (rcur.inc >= direct_cost) continue;
                    rev_closed[v] = cs0;
                    ++rpops;
                    int32_t rbeg = rev_row_ptr[v];
                    int32_t rend = rev_row_ptr[v + 1];
                    for (int32_t rei = rbeg; rei < rend; ++rei) {
                        int32_t u = rev_col_idx[rei];
                        float cand_after = rcur.inc + eval_edge_idx(v, rev_e_tdel[rei], rev_e_base[rei]);
                        if (in_tree_stamp[u] == ts) {
                            if (u == s0 || tree_delay[u] <= max_up_del) {
                                float tot_sc = cand_after + up_w * tree_delay[u];
                                if (tot_sc < direct_cost) {
                                    direct_cost = tot_sc;
                                    direct_tree_u = u;
                                    direct_rev_v = v;
                                }
                            }
                            continue;
                        }
                        if (rev_row_ptr[u] == rev_row_ptr[u + 1]) continue;
                        if (rev_closed[u] == cs0) continue;
                        if (!is_legal_node(u)) continue;
                        if (hot[u].rev_vis != cs0 || cand_after < hot[u].rev_after - 1e-6f) {
                            hot[u].rev_vis = cs0;
                            hot[u].rev_after = cand_after;
                            rev_pred[u] = v;
                            push_rev({cand_after + node_enter_lb(u), cand_after, u});
                        }
                    }
                }
                if (direct_tree_u >= 0 && direct_cost < 0.75f) {
                    bool clean_sc = true;
                    for (int32_t chk = direct_rev_v; chk != -1; chk = rev_pred[chk]) {
                        if (in_tree_stamp[chk] != ts && hot[chk].occ > 0) { clean_sc = false; break; }
                        if (chk == sk) break;
                    }
                    if (clean_sc) {
                        int32_t p_cur = direct_tree_u;
                        int32_t nxt = direct_rev_v;
                        while (nxt != -1) {
                            if (in_tree_stamp[nxt] != ts) {
                                in_tree_stamp[nxt] = ts;
                                tree_delay[nxt] = tree_delay[p_cur] + g_state.setup_ns[nxt];
                                tree_nodes.push_back(nxt);
                                tree.push_back({nxt, p_cur});
                                add_to_spatial(nxt, false);
                            }
                            if (nxt == sk) break;
                            p_cur = nxt;
                            nxt = rev_pred[nxt];
                        }
                        continue;
                    }
                }
            }

            // Symmetric Alternating Bidirectional Directed A* across 4 bounding-box tiers
            std::vector<int32_t> chosen_path;
            float chosen_cost = 1e35f;
            bool chosen_clean = false;
            bool prev_tier_exhausted = false;

            for (int tier = 0; tier < 4; ++tier) {
                bool tier_exhausted = false;
                allow_global_spine = allow_clock_nodes || (tier == 3 && prev_tier_exhausted);
                refresh_disallow_mask();
                float tier_max_up = (tier <= 2) ? max_up_del : 1e30f;
                seed_buf.clear();
                int16_t near_r = s0_r, near_c = s0_c;
                float best_aim_score = 0.032f * static_cast<float>(std::abs(s0_r - dst_r) + std::abs(s0_c - dst_c));

                if (tree_nodes.size() <= 160) {
                    for (int32_t k = src_b; k < src_e; ++k) {
                        if (src_ids[k] >= 0 && src_ids[k] < N) seed_buf.push_back(src_ids[k]);
                    }
                    for (int32_t tn : tree_nodes) {
                        if (g_state.meta[tn].fanout_cnt <= 0) continue;
                        if (tn != s0 && tree_delay[tn] > tier_max_up) continue;
                        seed_buf.push_back(tn);
                    }
                } else {
                    for (int32_t k = src_b; k < src_e; ++k) {
                        if (src_ids[k] >= 0 && src_ids[k] < N) seed_buf.push_back(src_ids[k]);
                    }
                    int cbr = std::clamp(static_cast<int>(dst_r) / 7, 0, BIN_R - 1);
                    int cbc = std::clamp(static_cast<int>(dst_c) / 8, 0, BIN_C - 1);
                    int max_md = (tier == 0) ? 4 : ((tier == 1) ? 14 : (BIN_R + BIN_C));
                    size_t seed_cap = (tier == 0) ? 160 : ((tier == 1) ? 384 : 768);
                    for (int md = 0; md <= max_md && seed_buf.size() < seed_cap; ++md) {
                        int dr_lo = std::max(-md, -cbr);
                        int dr_hi = std::min(md, BIN_R - 1 - cbr);
                        for (int dr = dr_lo; dr <= dr_hi; ++dr) {
                            int rem_c = md - std::abs(dr);
                            for (int sgn = 0; sgn < 2; ++sgn) {
                                if (rem_c == 0 && sgn == 1) continue;
                                int bc = cbc + (sgn == 0 ? -rem_c : rem_c);
                                if (bc < 0 || bc >= BIN_C) continue;
                                int bi = (cbr + dr) * BIN_C + bc;
                                for (int32_t tn : tree_bins[bi]) {
                                    if (tn != s0 && tree_delay[tn] > tier_max_up) continue;
                                    seed_buf.push_back(tn);
                                }
                            }
                        }
                    }
                    if (seed_buf.size() < 24 && !used_bin_idxs.empty()) {
                        int best_bi[6] = {-1, -1, -1, -1, -1, -1};
                        int best_bd[6] = {9999, 9999, 9999, 9999, 9999, 9999};
                        for (int bi : used_bin_idxs) {
                            int bd = std::abs((bi / BIN_C) - cbr) + std::abs((bi % BIN_C) - cbc);
                            if (bd >= best_bd[5]) continue;
                            int pos = 5;
                            while (pos > 0 && bd < best_bd[pos - 1]) {
                                best_bd[pos] = best_bd[pos - 1];
                                best_bi[pos] = best_bi[pos - 1];
                                --pos;
                            }
                            best_bd[pos] = bd;
                            best_bi[pos] = bi;
                        }
                        for (int k = 0; k < 6; ++k) {
                            if (best_bi[k] < 0) break;
                            for (int32_t tn : tree_bins[best_bi[k]]) {
                                if (tn != s0 && tree_delay[tn] > tier_max_up) continue;
                                seed_buf.push_back(tn);
                            }
                        }
                    }
                    if (tier >= 2 || sk_c >= 0.65f) {
                        size_t step = (tree_nodes.size() > 256) ? (tree_nodes.size() / 256) : 1;
                        for (size_t ti = 0; ti < tree_nodes.size(); ti += step) {
                            int32_t tn = tree_nodes[ti];
                            if (g_state.meta[tn].fanout_cnt > 0 && (tn == s0 || tree_delay[tn] <= tier_max_up)) {
                                seed_buf.push_back(tn);
                            }
                        }
                    }
                }
                for (int32_t tn : seed_buf) {
                    if (tn == s0 || (pin_owner[tn] == 0ULL && g_state.meta[tn].fanout_cnt >= 2)) {
                        int d = std::abs(g_state.eff_r[tn] - dst_r) + std::abs(g_state.eff_c[tn] - dst_c);
                        float aim_sc = up_w * tree_delay[tn] + 0.032f * static_cast<float>(d);
                        if (aim_sc < best_aim_score) {
                            best_aim_score = aim_sc;
                            near_r = g_state.eff_r[tn];
                            near_c = g_state.eff_c[tn];
                        }
                    }
                }

                bool use_bb = (tier < 3) && !(tier == 2 && prev_tier_exhausted);
                int pad_r = (tier == 0) ? 8 : ((tier == 1) ? 20 : 36);
                int pad_c = (tier == 0) ? 10 : ((tier == 1) ? 24 : 44);
                int bb_r0 = std::min({static_cast<int>(s0_r), static_cast<int>(near_r), static_cast<int>(dst_r)}) - pad_r;
                int bb_r1 = std::max({static_cast<int>(s0_r), static_cast<int>(near_r), static_cast<int>(dst_r)}) + pad_r;
                int bb_c0 = std::min({static_cast<int>(s0_c), static_cast<int>(near_c), static_cast<int>(dst_c)}) - pad_c;
                int bb_c1 = std::max({static_cast<int>(s0_c), static_cast<int>(near_c), static_cast<int>(dst_c)}) + pad_c;

                float t1_fac = 1.85f + 0.08f * std::min(pres_fac, 8.0f);
                float t2_fac = std::min(6.0f, 2.35f + 0.25f * std::min(pres_fac, 15.0f));
                float t3_fac = std::min(8.5f, 2.90f + 0.30f * std::min(pres_fac, 20.0f));
                float t0_fac = (sk_c >= 0.86f) ? ((direct_dr + direct_dc > 32) ? 1.14f : 1.04f) : ((sk_c >= 0.70f) ? 1.24f : 1.50f);
                float astar_fac = (tier == 0) ? t0_fac : ((tier == 1) ? t1_fac : ((tier == 2) ? t2_fac : t3_fac));
                float f_scale = std::max(1.0f, astar_fac);
                auto eval_look = [&](int r1, int c1, int r2, int c2) -> float {
                    int dr = std::min<int>(std::abs(r1 - r2), 110);
                    int dc = std::min<int>(std::abs(c1 - c2), 183);
                    return astar_fac * (c_crit * look_tdel[dr][dc] + (1.0f - c_crit) * look_base[dr][dc]);
                };
                int max_pops = (tier == 0) ? ((sk_c >= 0.86f) ? 16000 : ((pres_fac >= 1.5f) ? 5000 : 2500))
                             : ((tier == 1) ? 16000
                             : ((tier == 2) ? 40000
                             : (chosen_path.empty() ? (2 * N + 2) : 180000)));
                int post_meet_limit = (tier == 0) ? ((sk_c >= 0.86f) ? 420 : ((sk_c >= 0.75f) ? 160 : 48)) : 64;

                if (tier == 1) ++stat_tier2;
                if (tier >= 2) ++stat_tier3;

                int32_t st = bump_stamp();
                int32_t cs = bump_rev_stamp();
                pq_storage.clear();
                rev_pq.clear();

                int pops = 0;
                int first_clean_meet_pop = -1;
                int32_t best_meet = -1;
                float best_meet_cost = 1e35f;
                bool best_meet_clean = false;

                auto check_meet_clean = [&](int32_t m) -> bool {
                    int hops = 0;
                    int32_t cur = m;
                    while (cur != -1 && in_tree_stamp[cur] != ts) {
                        if (!is_tie_root[cur] && hot[cur].occ > 0) return false;
                        cur = pred[cur];
                        if (++hops > 8192) return false;
                    }
                    int32_t nxt = (m == sk) ? -1 : rev_pred[m];
                    while (nxt != -1) {
                        if (in_tree_stamp[nxt] != ts && !is_tie_root[nxt] && hot[nxt].occ > 0) return false;
                        if (nxt == sk) break;
                        nxt = rev_pred[nxt];
                        if (++hops > 8192) return false;
                    }
                    return true;
                };
                auto record_meet = [&](int32_t m, float cost, int cur_pops) {
                    if (cost < best_meet_cost - 1e-6f) {
                        best_meet_cost = cost;
                        best_meet = m;
                        best_meet_clean = check_meet_clean(m);
                        if (best_meet_clean && first_clean_meet_pop < 0) {
                            first_clean_meet_pop = cur_pops;
                        }
                    }
                };
                auto should_stop_tier = [&]() -> bool {
                    if (first_clean_meet_pop >= 0 && (pops - first_clean_meet_pop) >= post_meet_limit) {
                        float stop_margin = (sk_c >= 0.92f) ? 0.03f : 0.18f;
                        if (sk_c < 0.85f || (pops - first_clean_meet_pop) >= 1600 ||
                            pq_storage.empty() || (pq_storage.front().f / f_scale) + stop_margin >= best_meet_cost) {
                            return true;
                        }
                    }
                    if (best_meet >= 0) {
                        bool fwd_lb = pq_storage.empty() || (pq_storage.front().f / f_scale) >= best_meet_cost;
                        bool rev_lb = rev_pq.empty() || (rev_pq.front().f / f_scale) >= best_meet_cost;
                        if (fwd_lb && rev_lb) return true;
                    }
                    return false;
                };

                int eff_bb_r0 = std::clamp(bb_r0, 0, 110);
                int eff_bb_r1 = std::clamp(bb_r1, 0, 110);
                int eff_bb_c0 = std::clamp(bb_c0, 0, 183);
                int eff_bb_c1 = std::clamp(bb_c1, 0, 183);
                auto mark_rev_vis = [&](int32_t u_rv, int16_t ur_rv, int16_t uc_rv) {
                    hot[u_rv].rev_vis = cs;
                    if (ur_rv < eff_bb_r0) eff_bb_r0 = std::max<int>(0, ur_rv);
                    if (ur_rv > eff_bb_r1) eff_bb_r1 = std::min<int>(110, ur_rv);
                    if (uc_rv < eff_bb_c0) eff_bb_c0 = std::max<int>(0, uc_rv);
                    if (uc_rv > eff_bb_c1) eff_bb_c1 = std::min<int>(183, uc_rv);
                };

                mark_rev_vis(sk, dst_r, dst_c);
                hot[sk].rev_after = 0.0f;
                rev_pred[sk] = -1;
                float sk_h = eval_look(dst_r, dst_c, near_r, near_c);
                push_rev({sk_h, 0.0f, sk});

                for (int32_t tn : seed_buf) {
                    if (hot[tn].vis_stamp == st) continue;
                    hot[tn].vis_stamp = st;
                    float g0 = up_w * tree_delay[tn];
                    hot[tn].best_g = g0;
                    pred[tn] = -1;
                    float h = eval_look(hot[tn].fwd_r, hot[tn].fwd_c, dst_r, dst_c);
                    push_pq({g0 + h, g0, tn});
                }

                // Initial 8-pop reverse cone around sk so sink IMUX branches are ready immediately
                for (int rinit = 0; rinit < 8 && !rev_pq.empty(); ) {
                    RevPQEntry rcur = pop_rev();
                    int32_t v = rcur.u;
                    if (rev_closed[v] == cs) continue;
                    rev_closed[v] = cs;
                    ++rinit;
                    ++pops;
                    int32_t rbeg = rev_row_ptr[v];
                    int32_t rend = rev_row_ptr[v + 1];
                    for (int32_t rei = rbeg; rei < rend; ++rei) {
                        int32_t u = rev_col_idx[rei];
                        float cand_after = rcur.inc + eval_edge_idx(v, rev_e_tdel[rei], rev_e_base[rei]);
                        if (in_tree_stamp[u] == ts) {
                            if (u == s0 || tree_delay[u] <= tier_max_up) {
                                if (hot[u].rev_vis != cs || cand_after < hot[u].rev_after - 1e-6f) {
                                    mark_rev_vis(u, g_state.eff_r[u], g_state.eff_c[u]);
                                    hot[u].rev_after = cand_after;
                                    rev_pred[u] = v;
                                }
                                record_meet(u, up_w * tree_delay[u] + cand_after, pops);
                            }
                            continue;
                        }
                        if (rev_row_ptr[u] == rev_row_ptr[u + 1]) continue;
                        if (rev_closed[u] == cs) continue;
                        if (!is_legal_node(u)) continue;
                        int16_t ur = g_state.eff_r[u];
                        int16_t uc = g_state.eff_c[u];
                        if (use_bb && hot[u].vis_stamp != st) {
                            if (ur < bb_r0 || ur > bb_r1 || uc < bb_c0 || uc > bb_c1) continue;
                        }
                        if (hot[u].rev_vis != cs || cand_after < hot[u].rev_after - 1e-6f) {
                            mark_rev_vis(u, ur, uc);
                            hot[u].rev_after = cand_after;
                            rev_pred[u] = v;
                            if (hot[u].vis_stamp == st) {
                                record_meet(u, hot[u].best_g + cand_after, pops);
                            }
                            float hr = eval_look(ur, uc, near_r, near_c);
                            push_rev({cand_after + node_enter_lb(u) + hr, cand_after, u});
                        }
                    }
                }

                const int8_t dstr_s8 = static_cast<int8_t>(std::clamp<int>(dst_r, 0, 110));
                const int8_t dstc_s8 = static_cast<int8_t>(static_cast<uint8_t>(std::clamp<int>(dst_c, 0, 183)) ^ 0x80U);
                const bool high_crit = (c_crit >= 0.86f);
                const float om_crit = 1.0f - c_crit;

                while ((!pq_storage.empty() || !rev_pq.empty()) && pops < max_pops) {
                    if (should_stop_tier()) break;
                    if (best_meet < 0 && (pq_storage.empty() || rev_pq.empty())) {
                        tier_exhausted = true;
                        break;
                    }

                    const int8_t bbr0_s8 = static_cast<int8_t>(eff_bb_r0);
                    const int8_t bbr1_s8 = static_cast<int8_t>(eff_bb_r1);
                    const int8_t bbc0_s8 = static_cast<int8_t>(static_cast<uint8_t>(eff_bb_c0) ^ 0x80U);
                    const int8_t bbc1_s8 = static_cast<int8_t>(static_cast<uint8_t>(eff_bb_c1) ^ 0x80U);

                    // 1. Up to 4 Forward A* steps per 1 Reverse A* step
                    int fwd_steps = 0;
                    while (!pq_storage.empty() && fwd_steps < 4 && pops < max_pops) {
                        PQEntry cur = pop_pq();
                        HotNode& hcur = hot[cur.u];
                        if (hcur.closed_stamp == st) continue;
                        hcur.closed_stamp = st;
                        ++pops;
                        ++fwd_steps;
                        if (hcur.rev_vis == cs) {
                            record_meet(cur.u, cur.g + hcur.rev_after, pops);
                        }
                        int32_t beg = g_state.row_ptr[cur.u];
                        int32_t end = g_state.row_ptr[cur.u + 1];
                        int32_t ei = beg;
                        // Baseline 16-wide SSE2/SSE3 contiguous byte-mask edge filter (16 RRG neighbors per XMM op)
                        {
                            const __m128i v_zero = _mm_setzero_si128();
                            const __m128i v_leaf = _mm_set1_epi8(0x01);
                            const __m128i v_tie  = _mm_set1_epi8(0x04);
                            const __m128i v_dstr = _mm_set1_epi8(dstr_s8);
                            const __m128i v_dstc = _mm_set1_epi8(dstc_s8);
                            const __m128i v_r0   = _mm_set1_epi8(bbr0_s8);
                            const __m128i v_r1   = _mm_set1_epi8(bbr1_s8);
                            const __m128i v_c0   = _mm_set1_epi8(bbc0_s8);
                            const __m128i v_c1   = _mm_set1_epi8(bbc1_s8);
                            for (; ei < end; ei += 16) {
                                int32_t rem_e = end - ei;
                                uint32_t m_valid = (rem_e >= 16) ? 0xFFFFU : ((1U << rem_e) - 1U);
                                __m128i vnf  = _mm_loadu_si128(reinterpret_cast<const __m128i*>(fwd_e_nf.data() + ei));
                                __m128i vvr  = _mm_loadu_si128(reinterpret_cast<const __m128i*>(fwd_e_vr.data() + ei));
                                __m128i vvcs = _mm_loadu_si128(reinterpret_cast<const __m128i*>(fwd_e_vcs.data() + ei));
                                __m128i m_notie  = _mm_cmpeq_epi8(_mm_and_si128(vnf, v_tie), v_zero);
                                __m128i m_noleaf = _mm_cmpeq_epi8(_mm_and_si128(vnf, v_leaf), v_zero);
                                __m128i m_atdst  = _mm_and_si128(_mm_cmpeq_epi8(vvr, v_dstr), _mm_cmpeq_epi8(vvcs, v_dstc));
                                __m128i m_keep_v = _mm_and_si128(m_notie, _mm_or_si128(m_noleaf, m_atdst));
                                if (use_bb) {
                                    __m128i m_out_r = _mm_or_si128(_mm_cmpgt_epi8(v_r0, vvr), _mm_cmpgt_epi8(vvr, v_r1));
                                    __m128i m_out_c = _mm_or_si128(_mm_cmpgt_epi8(v_c0, vvcs), _mm_cmpgt_epi8(vvcs, v_c1));
                                    m_keep_v = _mm_andnot_si128(_mm_or_si128(m_out_r, m_out_c), m_keep_v);
                                }
                                uint32_t mask = static_cast<uint32_t>(_mm_movemask_epi8(m_keep_v)) & m_valid;
                                while (mask) {
                                    int lane = ctz32_simd(mask);
                                    mask &= (mask - 1U);
                                    if (mask) {
                                        int nxt_lane = ctz32_simd(mask);
                                        _mm_prefetch(reinterpret_cast<const char*>(&hot[g_state.col_idx[ei + nxt_lane]]), _MM_HINT_T0);
                                    }
                                    int32_t idx_e = ei + lane;
                                    int32_t v = g_state.col_idx[idx_e];
                                    uint8_t nf = fwd_e_nf[idx_e];
                                    if (v != sk && (nf & 0x01U)) continue;
                                    HotNode& hv = hot[v];
                                    if (hv.closed_stamp == st) continue;
                                    if ((nf & 0x7CU) != 0U && !is_legal_node(v)) continue;
                                    if (use_bb && hv.rev_vis != cs) {
                                        int16_t vr = g_state.eff_r[v], vc = g_state.eff_c[v];
                                        if (vr < bb_r0 || vr > bb_r1 || vc < bb_c0 || vc > bb_c1) continue;
                                    }
                                    int32_t ov = hv.occ;
                                    float p_cost = (ov > 0) ? (1.0f + pres_fac * static_cast<float>(ov)) : 1.0f;
                                    float hv_raw = hv.hist_cost;
                                    float h_v = (ov == 0 && high_crit) ? (1.0f + 0.25f * (hv_raw - 1.0f)) : hv_raw;
                                    float ecost = c_crit * fwd_e_tdel[idx_e] + om_crit * (fwd_e_base[idx_e] * h_v * p_cost);
                                    float ng = cur.g + ecost;
                                    if (hv.vis_stamp != st || ng < hv.best_g - 1e-6f) {
                                        hv.vis_stamp = st;
                                        hv.best_g = ng;
                                        pred[v] = cur.u;
                                        if (hv.rev_vis == cs) record_meet(v, ng + hv.rev_after, pops);
                                        float h = eval_look(hv.fwd_r, hv.fwd_c, dst_r, dst_c);
                                        push_pq({ng + h, ng, v});
                                    }
                                }
                            }
                        }
                        if (should_stop_tier()) break;
                    }

                    if (should_stop_tier()) break;
                    if (best_meet < 0 && pq_storage.empty()) {
                        tier_exhausted = true;
                        break;
                    }

                    // 2. 1 Reverse A* step (from sk toward near_r, near_c)
                    while (!rev_pq.empty() && pops < max_pops) {
                        RevPQEntry rcur = pop_rev();
                        int32_t v = rcur.u;
                        if (rev_closed[v] == cs) continue;
                        rev_closed[v] = cs;
                        ++pops;
                        if (hot[v].vis_stamp == st) {
                            record_meet(v, hot[v].best_g + hot[v].rev_after, pops);
                        }
                        int32_t rbeg = rev_row_ptr[v];
                        int32_t rend = rev_row_ptr[v + 1];
                        for (int32_t rei = rbeg; rei < rend; ++rei) {
                            int32_t u = rev_col_idx[rei];
                            float cand_after = rcur.inc + eval_edge_idx(v, rev_e_tdel[rei], rev_e_base[rei]);
                            if (in_tree_stamp[u] == ts) {
                                if (u == s0 || tree_delay[u] <= tier_max_up) {
                                    if (hot[u].rev_vis != cs || cand_after < hot[u].rev_after - 1e-6f) {
                                        mark_rev_vis(u, g_state.eff_r[u], g_state.eff_c[u]);
                                        hot[u].rev_after = cand_after;
                                        rev_pred[u] = v;
                                    }
                                    record_meet(u, up_w * tree_delay[u] + cand_after, pops);
                                }
                                continue;
                            }
                            if (rev_row_ptr[u] == rev_row_ptr[u + 1]) continue;
                            if (rev_closed[u] == cs) continue;
                            if (!is_legal_node(u)) continue;
                            int16_t ur = g_state.eff_r[u];
                            int16_t uc = g_state.eff_c[u];
                            if (use_bb && hot[u].vis_stamp != st) {
                                if (ur < bb_r0 || ur > bb_r1 || uc < bb_c0 || uc > bb_c1) continue;
                            }
                            if (hot[u].rev_vis != cs || cand_after < hot[u].rev_after - 1e-6f) {
                                mark_rev_vis(u, ur, uc);
                                hot[u].rev_after = cand_after;
                                rev_pred[u] = v;
                                if (hot[u].vis_stamp == st) {
                                    record_meet(u, hot[u].best_g + cand_after, pops);
                                }
                                float hr = eval_look(ur, uc, near_r, near_c);
                                push_rev({cand_after + node_enter_lb(u) + hr, cand_after, u});
                            }
                        }
                        break;
                    }
                }

                if (best_meet >= 0) {
                    std::vector<int32_t> cand_path;
                    bool path_ok = true;
                    if (in_tree_stamp[best_meet] == ts) {
                        cand_path.push_back(best_meet);
                    } else {
                        int32_t cur = best_meet;
                        int hops = 0;
                        while (cur != -1 && in_tree_stamp[cur] != ts) {
                            cand_path.push_back(cur);
                            cur = pred[cur];
                            if (++hops > 8192) { path_ok = false; break; }
                        }
                        if (!path_ok || cur == -1) path_ok = false;
                        else {
                            cand_path.push_back(cur);
                            std::reverse(cand_path.begin(), cand_path.end());
                        }
                    }
                    if (path_ok) {
                        int32_t nxt = (best_meet == sk) ? -1 : rev_pred[best_meet];
                        int hops = 0;
                        while (nxt != -1) {
                            cand_path.push_back(nxt);
                            if (nxt == sk) break;
                            nxt = rev_pred[nxt];
                            if (++hops > 8192) { path_ok = false; break; }
                        }
                    }
                    if (path_ok && !cand_path.empty() && cand_path.back() == sk) {
                        bool cand_clean = true;
                        for (int32_t nd : cand_path) {
                            if (in_tree_stamp[nd] != ts && !is_tie_root[nd] && hot[nd].occ > 0) {
                                cand_clean = false;
                                break;
                            }
                        }
                        if (chosen_path.empty() || (cand_clean && !chosen_clean) || (cand_clean == chosen_clean && best_meet_cost < chosen_cost)) {
                            chosen_path = std::move(cand_path);
                            chosen_cost = best_meet_cost;
                            chosen_clean = cand_clean;
                        }
                    }
                }
                if (!chosen_path.empty()) {
                    int max_esc_tier = (pres_fac >= 25.0f) ? 3 : ((pres_fac >= 4.0f) ? 2 : ((pres_fac >= 1.2f) ? 1 : 0));
                    if (chosen_clean || tier >= max_esc_tier) {
                        break;
                    }
                }
                prev_tier_exhausted = tier_exhausted;
                if (tier == 3) {
                    std::fprintf(stdout, "[ownpr-router] SIG TIER3 EXHAUSTED pops=%d fwd_empty=%d rev_empty=%d seeds=%zu\n",
                                 pops, pq_storage.empty() ? 1 : 0, rev_pq.empty() ? 1 : 0, seed_buf.size());
                }
            }
            if (chosen_path.empty() || chosen_path.back() != sk) {
                std::fprintf(stdout, "[ownpr-router] SIG FAIL net=%d src0=(%d,%d,%d,fan=%d,po=0x%llx,fx=%d) sink=(%d,%d,%d,po=0x%llx,fx=%d,clk=%d) my=0x%llx sinks=%d\n",
                             e, g_state.meta[s0].r, g_state.meta[s0].c, g_state.meta[s0].idx,
                             g_state.row_ptr[s0 + 1] - g_state.row_ptr[s0],
                             static_cast<unsigned long long>(pin_owner[s0]), static_cast<int>(is_fixed_root[s0]),
                             g_state.meta[sk].r, g_state.meta[sk].c, g_state.meta[sk].idx,
                             static_cast<unsigned long long>(pin_owner[sk]), static_cast<int>(is_fixed_root[sk]), static_cast<int>(clock_locked[sk]),
                             static_cast<unsigned long long>(my_net), num_sinks);
                std::fflush(stdout);
                return false;
            }

            const std::vector<int32_t>& full_path = chosen_path;

            size_t start_k = 0;
            for (size_t k = 0; k + 1 < full_path.size(); ++k) {
                if (in_tree_stamp[full_path[k]] == ts) {
                    start_k = k;
                }
            }
            std::vector<int32_t> clean_path;
            clean_path.reserve(full_path.size() - start_k);
            for (size_t k = start_k; k < full_path.size(); ++k) {
                int32_t nd = full_path[k];
                auto dup_it = std::find(clean_path.begin(), clean_path.end(), nd);
                if (dup_it != clean_path.end()) {
                    clean_path.erase(dup_it + 1, clean_path.end());
                } else {
                    clean_path.push_back(nd);
                }
            }

            if (req_min_del > 0.0f && !clean_path.empty()) {
                float cur_del = tree_delay[clean_path[0]];
                for (size_t k = 1; k < clean_path.size(); ++k) cur_del += g_state.setup_ns[clean_path[k]];
                if (cur_del < req_min_del) {
                    int32_t src_tap = clean_path[0];
                    int d_off = std::clamp(static_cast<int>(std::ceil((req_min_del - cur_del) * 4.5f)), 4, 12);
                    int cr0 = std::clamp<int>(dst_r, 2, 108), cc0 = std::clamp<int>(dst_c, 2, 181);
                    const int cand_rc[4][2] = {
                        {std::clamp(cr0 + d_off, 2, 108), cc0},
                        {std::clamp(cr0 - d_off, 2, 108), cc0},
                        {cr0, std::clamp(cc0 + d_off, 2, 181)},
                        {cr0, std::clamp(cc0 - d_off, 2, 181)},
                    };
                    std::vector<int32_t> cand_wps;
                    cand_wps.reserve(8);
                    for (int dir = 0; dir < 4 && cand_wps.size() < 6; ++dir) {
                        int ti = cand_rc[dir][0] * 184 + cand_rc[dir][1];
                        for (int wi = 0; wi < tile_wp_cnt[ti] && cand_wps.size() < 6; ++wi) {
                            int32_t wp = tile_wp[ti * 4 + wi];
                            if (wp >= 0 && hot[wp].occ == 0 && in_tree_stamp[wp] != ts && is_legal_node(wp)) {
                                if (std::find(cand_wps.begin(), cand_wps.end(), wp) == cand_wps.end()) {
                                    cand_wps.push_back(wp);
                                }
                            }
                        }
                    }
                    auto dijkstra_clean_leg = [&](int32_t u_start, int32_t u_goal, int32_t forbid_st,
                                                  std::vector<int32_t>& out_leg, float& out_del) -> bool {
                        out_leg.clear();
                        out_del = 0.0f;
                        int32_t st_l = bump_stamp();
                        pq_storage.clear();
                        int gr = g_state.eff_r[u_goal], gc = g_state.eff_c[u_goal];
                        hot[u_start].vis_stamp = st_l;
                        hot[u_start].best_g = 0.0f;
                        pred[u_start] = -1;
                        push_pq({0.025f * static_cast<float>(std::abs(g_state.eff_r[u_start] - gr) + std::abs(g_state.eff_c[u_start] - gc)), 0.0f, u_start});
                        int lpops = 0;
                        bool reached = false;
                        while (!pq_storage.empty() && lpops < 3000) {
                            PQEntry cur = pop_pq();
                            int32_t u = cur.u;
                            if (hot[u].closed_stamp == st_l) continue;
                            hot[u].closed_stamp = st_l;
                            if (u == u_goal) { reached = true; break; }
                            ++lpops;
                            int32_t beg = g_state.row_ptr[u], end = g_state.row_ptr[u + 1];
                            for (int32_t ei = beg; ei < end; ++ei) {
                                int32_t v = g_state.col_idx[ei];
                                if (hot[v].closed_stamp == st_l) continue;
                                if (v != u_goal) {
                                    if (hot[v].occ > 0 || in_tree_stamp[v] == ts) continue;
                                    if (forbid_st != 0 && target_stamp[v] == forbid_st) continue;
                                    if (!is_legal_node(v) || g_state.row_ptr[v] == g_state.row_ptr[v + 1]) continue;
                                }
                                float ng = cur.g + 0.02f + g_state.setup_ns[v];
                                if (hot[v].vis_stamp != st_l || ng < hot[v].best_g - 1e-6f) {
                                    hot[v].vis_stamp = st_l;
                                    hot[v].best_g = ng;
                                    pred[v] = u;
                                    float h = 0.025f * static_cast<float>(std::abs(g_state.eff_r[v] - gr) + std::abs(g_state.eff_c[v] - gc));
                                    push_pq({ng + h, ng, v});
                                }
                            }
                        }
                        if (!reached) return false;
                        for (int32_t cur = u_goal; cur != -1; cur = pred[cur]) {
                            out_leg.push_back(cur);
                            if (cur == u_start) break;
                            if (out_leg.size() > 2048) return false;
                        }
                        if (out_leg.empty() || out_leg.back() != u_start) return false;
                        std::reverse(out_leg.begin(), out_leg.end());
                        for (size_t k = 1; k < out_leg.size(); ++k) out_del += g_state.setup_ns[out_leg[k]];
                        return true;
                    };

                    std::vector<int32_t> best_pad_path, leg1, leg2;
                    float best_pad_del = -1.0f;
                    for (int32_t wp : cand_wps) {
                        float d1 = 0.0f, d2 = 0.0f;
                        if (!dijkstra_clean_leg(src_tap, wp, 0, leg1, d1)) continue;
                        int32_t l1_st = bump_tgt_stamp();
                        for (size_t k = 1; k < leg1.size(); ++k) target_stamp[leg1[k]] = l1_st;
                        if (!dijkstra_clean_leg(wp, sk, l1_st, leg2, d2)) continue;
                        float tot_d = tree_delay[src_tap] + d1 + d2;
                        if (tot_d <= cur_del + 0.12f) continue;
                        bool cur_meets = (best_pad_del >= req_min_del);
                        bool new_meets = (tot_d >= req_min_del);
                        bool take = false;
                        if (best_pad_path.empty()) take = true;
                        else if (new_meets && !cur_meets) take = true;
                        else if (new_meets && cur_meets && tot_d < best_pad_del) take = true;
                        else if (!new_meets && !cur_meets && tot_d > best_pad_del) take = true;
                        if (take) {
                            best_pad_del = tot_d;
                            best_pad_path = leg1;
                            best_pad_path.insert(best_pad_path.end(), leg2.begin() + 1, leg2.end());
                        }
                    }
                    if (!best_pad_path.empty()) {
                        clean_path.swap(best_pad_path);
                        ++stat_hold_padded;
                    }
                }
            }

            for (size_t k = 1; k < clean_path.size(); ++k) {
                int32_t node = clean_path[k];
                int32_t par = clean_path[k - 1];
                if (in_tree_stamp[node] != ts) {
                    in_tree_stamp[node] = ts;
                    tree_delay[node] = tree_delay[par] + g_state.setup_ns[node];
                    tree_nodes.push_back(node);
                    tree.push_back({node, par});
                    add_to_spatial(node, false);
                }
            }
        }
        return true;
    };

    auto commit_tree = [&](int32_t e) {
        for (const auto& pr : net_trees[e]) {
            if (!is_tie_root[pr.first]) ++hot[pr.first].occ;
        }
    };
    auto ripup_tree = [&](int32_t e) {
        for (const auto& pr : net_trees[e]) {
            if (!is_tie_root[pr.first]) --hot[pr.first].occ;
        }
    };

    auto t_phaseA0 = std::chrono::steady_clock::now();

    // Phase A: Route IS_CLOCK nets first and negotiate any clock-clock contention
    std::vector<int32_t> clk_nets, sig_nets;
    for (int32_t e = 0; e < num_nets; ++e) {
        if (net_flags[e] & 1) clk_nets.push_back(e);
        else sig_nets.push_back(e);
    }
    std::sort(clk_nets.begin(), clk_nets.end(), [&](int32_t a, int32_t b) {
        return (snk_offsets[a + 1] - snk_offsets[a]) > (snk_offsets[b + 1] - snk_offsets[b]);
    });

    if (!clk_nets.empty()) {
        float c_pres = 2.0f;
        for (int cit = 0; cit < 20; ++cit) {
            if (cit == 0) {
                for (int32_t e : clk_nets) {
                    if (!route_single_net(e, c_pres, true)) return -(1000 + e);
                    commit_tree(e);
                }
            } else {
                int clk_over = 0;
                for (int32_t e : clk_nets) {
                    for (const auto& pr : net_trees[e]) {
                        if (!is_tie_root[pr.first] && hot[pr.first].occ > 1) ++clk_over;
                    }
                }
                if (clk_over == 0) break;
                for (int32_t e : clk_nets) {
                    bool bad = false;
                    for (const auto& pr : net_trees[e]) {
                        if (!is_tie_root[pr.first] && hot[pr.first].occ > 1) {
                            hot[pr.first].hist_cost += 1.0f;
                            bad = true;
                        }
                    }
                    if (bad) {
                        ripup_tree(e);
                        if (!route_single_net(e, c_pres, true)) return -(1000 + e);
                        commit_tree(e);
                    }
                }
                c_pres *= 1.8f;
            }
        }
        size_t tot_clk_nodes = 0;
        for (int32_t e : clk_nets) {
            tot_clk_nodes += net_trees[e].size();
            for (const auto& pr : net_trees[e]) {
                if (!is_tie_root[pr.first]) {
                    if (hot[pr.first].occ > 1) return -200; // Unresolved clock conflict
                    clock_locked[pr.first] = 1;
                    node_fast_flags[pr.first] |= 0x10U;
                }
            }
        }
        for (int64_t ei = 0; ei < total_rrg_edges; ++ei) {
            fwd_e_nf[ei] = node_fast_flags[g_state.col_idx[ei]];
        }
        double sec_a = std::chrono::duration<double>(std::chrono::steady_clock::now() - t_phaseA0).count();
        for (size_t ci = 0; ci < orig_clk_nets.size(); ++ci) {
            int32_t ce = orig_clk_nets[ci];
            int32_t st_c = bump_stamp();
            for (const auto& pr : net_trees[ce]) {
                int32_t nd = pr.first, par = pr.second;
                if (par < 0) {
                    hot[nd].vis_stamp = st_c;
                    hot[nd].best_g = 0.0f;
                } else if (hot[par].vis_stamp == st_c) {
                    hot[nd].vis_stamp = st_c;
                    hot[nd].best_g = hot[par].best_g + g_state.setup_ns[nd];
                }
            }
            float min_a = 1e9f, max_a = 0.0f;
            int32_t csb = snk_offsets[ce], cse = snk_offsets[ce + 1];
            for (int32_t si = csb; si < cse; ++si) {
                int32_t sk = snk_ids[si];
                if (sk >= 0 && sk < N && hot[sk].vis_stamp == st_c) {
                    float a = hot[sk].best_g;
                    int r = g_state.eff_r[sk], c = g_state.eff_c[sk];
                    if (r >= 1 && r <= 109 && c >= 1 && c <= 182) {
                        clk_tile_arr[ci * 20424 + r * 184 + c] = a;
                        if (a < min_a) min_a = a;
                        if (a > max_a) max_a = a;
                    }
                }
            }
            if (min_a > max_a) min_a = max_a = 0.0f;
            std::fprintf(stdout, "[ownpr-clk] ci=%zu e=%d sinks=%d nodes=%zu arr=[%.3f..%.3f] skew=%.3fns\n",
                         ci, ce, cse - csb, net_trees[ce].size(), min_a, max_a, max_a - min_a);
        }
        std::fprintf(stdout, "[ownpr-router] Phase A routed %zu clock nets (%zu RRG nodes) in %.2fs\n",
                     clk_nets.size(), tot_clk_nodes, sec_a);
        std::fflush(stdout);
    }

    // Phase B: PathFinder Negotiated Congestion for All Signal & Constant Nets
    std::sort(sig_nets.begin(), sig_nets.end(), [&](int32_t a, int32_t b) {
        bool ca = (net_flags[a] & 6) != 0;
        bool cb = (net_flags[b] & 6) != 0;
        if (ca != cb) return !ca; // Signals before constants in iter 0
        if (std::abs(net_crit[a] - net_crit[b]) > 1e-3f) return net_crit[a] > net_crit[b];
        return (snk_offsets[a + 1] - snk_offsets[a]) > (snk_offsets[b + 1] - snk_offsets[b]);
    });

    float pres_fac = 0.18f;
    int32_t overused = 0;
    int32_t it_done = 0;
    std::vector<int32_t> overused_list;
    overused_list.reserve(65536);

    for (int32_t it = 0; it < max_iters; ++it) {
        auto t_it0 = std::chrono::steady_clock::now();
        it_done = it + 1;
        int32_t routed_this_iter = 0;
        if (it == 0) {
            for (size_t idx = 0; idx < sig_nets.size(); ++idx) {
                int32_t e = sig_nets[idx];
                if (!route_single_net(e, pres_fac, false)) return -(100000 + e);
                commit_tree(e);
                ++routed_this_iter;
                if ((idx + 1) % 20000 == 0) {
                    double sec_p = std::chrono::duration<double>(std::chrono::steady_clock::now() - t_it0).count();
                    std::fprintf(stdout, "[ownpr-router] iter=1 progress %zu/%zu (%.2fs, tier2=%lld, tier3=%lld)\n",
                                 idx + 1, sig_nets.size(), sec_p, static_cast<long long>(stat_tier2), static_cast<long long>(stat_tier3));
                    std::fflush(stdout);
                }
            }
        } else {
            std::vector<int32_t> rip_list;
            rip_list.reserve(overused_list.size() * 2 + 16);
            for (int32_t e : sig_nets) {
                bool touch = false;
                for (const auto& pr : net_trees[e]) {
                    if (!is_tie_root[pr.first] && hot[pr.first].occ > 1) { touch = true; break; }
                }
                if (touch) rip_list.push_back(e);
            }
            routed_this_iter = static_cast<int32_t>(rip_list.size());
            for (size_t ri = 0; ri < rip_list.size(); ++ri) {
                int32_t e = rip_list[ri];
                ripup_tree(e);
                if (!route_single_net(e, pres_fac, false)) return -(100000 + e);
                commit_tree(e);
                if ((ri + 1) % 20000 == 0) {
                    double sec_p = std::chrono::duration<double>(std::chrono::steady_clock::now() - t_it0).count();
                    std::fprintf(stdout, "[ownpr-router] iter=%d progress %zu/%zu (%.2fs, tier2=%lld, tier3=%lld)\n",
                                 it + 1, ri + 1, rip_list.size(), sec_p, static_cast<long long>(stat_tier2), static_cast<long long>(stat_tier3));
                    std::fflush(stdout);
                }
            }
        }

        overused_list.clear();
        int32_t max_occ = 0;
        for (int32_t v = 0; v < N; ++v) {
            if (hot[v].occ > 1) {
                overused_list.push_back(v);
                if (hot[v].occ > max_occ) max_occ = hot[v].occ;
            }
        }
        overused = static_cast<int32_t>(overused_list.size());
        double sec_it = std::chrono::duration<double>(std::chrono::steady_clock::now() - t_it0).count();
        std::fprintf(stdout, "[ownpr-router] iter=%d routed=%d pres_fac=%.2f overused_nodes=%d max_occ=%d (%.2fs, t2=%lld, t3=%lld)\n",
                     it + 1, routed_this_iter, pres_fac, overused, max_occ, sec_it,
                     static_cast<long long>(stat_tier2), static_cast<long long>(stat_tier3));
        if ((it == 0 || it == 19 || it + 1 == max_iters) && !overused_list.empty()) {
            size_t tot_tn = 0;
            for (int32_t e : sig_nets) tot_tn += net_trees[e].size();
            std::vector<int32_t> top_ov = overused_list;
            size_t ktop = std::min<size_t>(top_ov.size(), 5);
            std::partial_sort(top_ov.begin(), top_ov.begin() + ktop, top_ov.end(),
                              [&](int32_t a, int32_t b) { return hot[a].occ > hot[b].occ; });
            std::fprintf(stdout, "[ownpr-router] iter=%d tree_nodes=%zu top_ov:", it + 1, tot_tn);
            for (size_t k = 0; k < ktop; ++k) {
                int32_t v = top_ov[k];
                int flg_mask = 0;
                if (it > 0) {
                    for (int32_t e : sig_nets) {
                        for (const auto& pr : net_trees[e]) {
                            if (pr.first == v) { flg_mask |= (1 << (net_flags[e] & 7)); break; }
                        }
                    }
                }
                std::fprintf(stdout, " (%d,%d,%d,fan=%d,occ=%d,fm=%d)",
                             g_state.meta[v].r, g_state.meta[v].c, g_state.meta[v].idx,
                             g_state.meta[v].fanout_cnt, hot[v].occ, flg_mask);
            }
            std::fprintf(stdout, "\n");
        }
        std::fflush(stdout);
        if (overused == 0) break;

        float h_inc = (it == 0) ? 0.20f : ((it < 5) ? 0.50f : 1.00f);
        float ov_cap = (it < 4) ? 8.0f : 64.0f;
        for (int32_t v : overused_list) {
            hot[v].hist_cost += h_inc * std::min<float>(static_cast<float>(hot[v].occ - 1), ov_cap);
        }
        pres_fac = (it == 0) ? 0.55f : std::min(pres_fac * 1.35f, 50000.0f);
    }

    g_state.last_route_iters = it_done;
    g_state.last_overused = overused;
    if (stat_hold_padded > 0) {
        std::fprintf(stdout, "[ownpr-hold-pad] Applied clean hold-skew waypoint padding %d times across routing iterations\n", stat_hold_padded);
        std::fflush(stdout);
    }
    if (out_iters) *out_iters = it_done;
    if (out_overused) *out_overused = overused;
    if (overused != 0) return -300; // Did not converge to 0 overuse

    // Assemble final deduplicated route records in O(N) flat arrays (sorted by cid, zero heap churn!)
    std::vector<uint8_t> fm_has(N, 0U);
    std::vector<int32_t> fm_par(N, -2);
    std::vector<uint64_t> fm_net(N, 0ULL);
    size_t fm_cnt = 0;

    for (int32_t i = 0; i < num_fixed_roots; ++i) {
        int32_t cid = fixed_root_ids[i];
        if (cid < 0 || cid >= N) continue;
        if (fm_has[cid]) {
            if (fm_net[cid] != fixed_root_nets[i] || fm_par[cid] != -1) return -301;
            continue;
        }
        fm_has[cid] = 1U;
        fm_par[cid] = -1;
        fm_net[cid] = fixed_root_nets[i];
        ++fm_cnt;
    }
    for (int32_t e = 0; e < num_nets; ++e) {
        uint64_t np = net_ptrs[e];
        for (const auto& pr : net_trees[e]) {
            int32_t cid = pr.first;
            int32_t pid = pr.second;
            if (fm_has[cid]) {
                if (fm_net[cid] != np || fm_par[cid] != pid) return -302;
                continue;
            }
            fm_has[cid] = 1U;
            fm_par[cid] = pid;
            fm_net[cid] = np;
            ++fm_cnt;
        }
    }

    g_state.routed_children.reserve(fm_cnt);
    g_state.routed_parents.reserve(fm_cnt);
    g_state.routed_nets.reserve(fm_cnt);
    for (int32_t cid = 0; cid < N; ++cid) {
        if (!fm_has[cid]) continue;
        g_state.routed_children.push_back(cid);
        g_state.routed_parents.push_back(fm_par[cid]);
        g_state.routed_nets.push_back(fm_net[cid]);
    }

    return static_cast<int32_t>(g_state.routed_children.size());
}

EXPORT int32_t bridge_install_routes(
    uint64_t set_net_routed_addr,
    uint64_t set_pin_routed_addr,
    const int32_t* child_ids,
    const int32_t* parent_ids,
    const uint64_t* net_ptrs,
    int32_t num_records,
    const uint64_t* routed_pins,
    int32_t num_routed_pins,
    int32_t* out_err_idx
) {
    int32_t N = static_cast<int32_t>(g_state.id_to_ptr.size());
    std::vector<uint8_t> has_owner(N, 0U);
    std::vector<uint64_t> owner_by_node(N, 0ULL);
    std::vector<uint64_t> unique_nets;
    unique_nets.reserve(static_cast<size_t>(num_records));

    for (int32_t i = 0; i < num_records; ++i) {
        int32_t cid = child_ids[i];
        int32_t pid = parent_ids[i];
        uint64_t net = net_ptrs[i];
        if (cid < 0 || cid >= N) {
            if (out_err_idx) *out_err_idx = i;
            return -1;
        }
        if (has_owner[cid] && owner_by_node[cid] != net) {
            if (out_err_idx) *out_err_idx = i;
            return -2;
        }
        has_owner[cid] = 1U;
        owner_by_node[cid] = net;
        if (pid >= 0) {
            if (pid >= N) {
                if (out_err_idx) *out_err_idx = i;
                return -3;
            }
            const int32_t* beg = g_state.col_idx.data() + g_state.row_ptr[pid];
            const int32_t* end = g_state.col_idx.data() + g_state.row_ptr[pid + 1];
            if (!std::binary_search(beg, end, cid)) {
                if (out_err_idx) *out_err_idx = i;
                return -4;
            }
        }
        if (net) unique_nets.push_back(net);
    }
    std::sort(unique_nets.begin(), unique_nets.end());
    unique_nets.erase(std::unique(unique_nets.begin(), unique_nets.end()), unique_nets.end());

    for (int32_t i = 0; i < num_records; ++i) {
        uint64_t cptr = g_state.id_to_ptr[child_ids[i]];
        uint64_t pptr = (parent_ids[i] >= 0) ? g_state.id_to_ptr[parent_ids[i]] : 0ULL;
        wr_u64(cptr + 0x18ULL, pptr);
        wr_u8(cptr + 0x2aULL, static_cast<uint8_t>(rd_u8(cptr + 0x2aULL) | 8U));
        wr_u64(cptr + 0x20ULL, net_ptrs[i]);
    }

    if (set_net_routed_addr) {
        auto set_net = reinterpret_cast<SetRoutedFn>(set_net_routed_addr);
        for (uint64_t net : unique_nets) {
            set_net(reinterpret_cast<void*>(net), true);
        }
    }
    if (set_pin_routed_addr) {
        auto set_pin = reinterpret_cast<SetRoutedFn>(set_pin_routed_addr);
        for (int32_t i = 0; i < num_routed_pins; ++i) {
            if (routed_pins[i]) {
                set_pin(reinterpret_cast<void*>(routed_pins[i]), true);
            }
        }
    }
    return 0;
}

EXPORT int32_t bridge_install_last_routes(
    uint64_t set_net_routed_addr,
    uint64_t set_pin_routed_addr,
    const uint64_t* extra_nets,
    int32_t num_extra_nets,
    const uint64_t* routed_pins,
    int32_t num_routed_pins,
    int32_t* out_err_idx
) {
    int32_t rc = bridge_install_routes(
        set_net_routed_addr,
        set_pin_routed_addr,
        g_state.routed_children.data(),
        g_state.routed_parents.data(),
        g_state.routed_nets.data(),
        static_cast<int32_t>(g_state.routed_children.size()),
        routed_pins,
        num_routed_pins,
        out_err_idx
    );
    if (rc != 0) return rc;
    if (set_net_routed_addr && extra_nets && num_extra_nets > 0) {
        auto set_net = reinterpret_cast<SetRoutedFn>(set_net_routed_addr);
        for (int32_t i = 0; i < num_extra_nets; ++i) {
            if (extra_nets[i]) set_net(reinterpret_cast<void*>(extra_nets[i]), true);
        }
    }
    return 0;
}

EXPORT int32_t bridge_verify_last_parents_preserved() {
    int32_t N = static_cast<int32_t>(g_state.id_to_ptr.size());
    size_t M = g_state.routed_children.size();
    for (size_t i = 0; i < M; ++i) {
        int32_t cid = g_state.routed_children[i];
        int32_t pid = g_state.routed_parents[i];
        if (cid < 0 || cid >= N) return 0;
        uint64_t expected = (pid >= 0 && pid < N) ? g_state.id_to_ptr[pid] : 0ULL;
        if (rd_u64(g_state.id_to_ptr[cid] + 0x18ULL) != expected) return 0;
    }
    return 1;
}

EXPORT int32_t bridge_verify_parents_preserved(
    const int32_t* child_ids,
    const int32_t* parent_ids,
    int32_t num_records
) {
    int32_t N = static_cast<int32_t>(g_state.id_to_ptr.size());
    for (int32_t i = 0; i < num_records; ++i) {
        int32_t cid = child_ids[i];
        int32_t pid = parent_ids[i];
        if (cid < 0 || cid >= N) return 0;
        uint64_t expected = (pid >= 0 && pid < N) ? g_state.id_to_ptr[pid] : 0ULL;
        if (rd_u64(g_state.id_to_ptr[cid] + 0x18ULL) != expected) {
            return 0;
        }
    }
    return 1;
}

EXPORT int32_t bridge_query_routed_delays(
    const int32_t* node_ids,
    int32_t count,
    float* out_delays
) {
    int32_t N = static_cast<int32_t>(g_state.id_to_ptr.size());
    if (N <= 0 || count <= 0 || !node_ids || !out_delays) return 0;
    std::vector<int32_t> par_of(N, -2);
    size_t M = g_state.routed_children.size();
    for (size_t i = 0; i < M; ++i) {
        int32_t c = g_state.routed_children[i];
        if (c >= 0 && c < N) par_of[c] = g_state.routed_parents[i];
    }
    std::vector<float> memo(N, -1.0f);
    std::vector<int32_t> stk;
    stk.reserve(64);
    for (int32_t i = 0; i < count; ++i) {
        int32_t u = node_ids[i];
        if (u < 0 || u >= N || par_of[u] == -2) {
            out_delays[i] = 0.0f;
            continue;
        }
        stk.clear();
        int32_t cur = u;
        int guard = 0;
        while (cur >= 0 && cur < N && memo[cur] < 0.0f && par_of[cur] != -2 && guard < 256) {
            stk.push_back(cur);
            cur = par_of[cur];
            ++guard;
        }
        float acc = (cur >= 0 && cur < N && memo[cur] >= 0.0f) ? memo[cur] : 0.0f;
        for (int k = static_cast<int>(stk.size()) - 1; k >= 0; --k) {
            int32_t nd = stk[k];
            if (par_of[nd] >= 0) acc += g_state.setup_ns[nd];
            memo[nd] = acc;
        }
        out_delays[i] = memo[u] >= 0.0f ? memo[u] : 0.0f;
    }
    return count;
}
