// Baseline x86-64 + SSE2/SSE3 Synthesis & Netlist Accelerator (Tier 1 only: CPUs > 2007)
// Compiled with /O2 /MT /fp:precise /d2Qvec-sse2only /utf-8
// Zero SSE4/POPCNT/VEX/EVEX instructions; zero external C/C++ runtime DLL dependencies.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <intrin.h>
#include <emmintrin.h>
#include <pmmintrin.h>

#define EXPORT extern "C" __declspec(dllexport)

namespace {

inline int ctz64_fast(uint64_t x) {
    unsigned long idx = 0;
    _BitScanForward64(&idx, x);
    return static_cast<int>(idx);
}

inline uint16_t permute_lut4_scalar(uint16_t truth, const uint8_t ord[4], int k) {
    uint16_t out = 0;
    int lim = 1 << k;
    for (int a = 0; a < lim; ++a) {
        int old_a = 0;
        for (int ni = 0; ni < k; ++ni) {
            old_a |= ((a >> ni) & 1) << ord[ni];
        }
        out |= static_cast<uint16_t>(((truth >> old_a) & 1U) << a);
    }
    return out;
}

inline uint8_t pack_ord4(const uint8_t ord[4]) {
    return static_cast<uint8_t>((ord[0] & 3U) | ((ord[1] & 3U) << 2) | ((ord[2] & 3U) << 4) | ((ord[3] & 3U) << 6));
}

struct PermTables {
    alignas(16) uint8_t shuf16[256][16];

    PermTables() {
        for (int code = 0; code < 256; ++code) {
            int o0 = (code >> 0) & 3;
            int o1 = (code >> 2) & 3;
            int o2 = (code >> 4) & 3;
            int o3 = (code >> 6) & 3;
            for (int a = 0; a < 16; ++a) {
                int old_a = (((a >> 0) & 1) << o0) |
                            (((a >> 1) & 1) << o1) |
                            (((a >> 2) & 1) << o2) |
                            (((a >> 3) & 1) << o3);
                shuf16[code][a] = static_cast<uint8_t>(old_a);
            }
        }
    }
};

const PermTables g_perm_tables;

FILE* open_utf8_wb(const char* path_utf8) {
    if (!path_utf8 || !*path_utf8) return nullptr;
    int wlen = MultiByteToWideChar(CP_UTF8, 0, path_utf8, -1, nullptr, 0);
    if (wlen <= 0) return std::fopen(path_utf8, "wb");
    wchar_t stack_buf[512];
    wchar_t* wbuf = (wlen <= 512) ? stack_buf : static_cast<wchar_t*>(std::malloc(static_cast<size_t>(wlen) * sizeof(wchar_t)));
    if (!wbuf) return nullptr;
    MultiByteToWideChar(CP_UTF8, 0, path_utf8, -1, wbuf, wlen);
    FILE* fp = _wfopen(wbuf, L"wb");
    if (wbuf != stack_buf) std::free(wbuf);
    return fp;
}

} // namespace

EXPORT int32_t accel_tier_id() {
    return 10;
}

EXPORT void accel_permute_lut_batch(
    int32_t count,
    const uint16_t* inits,
    const uint8_t* ks,
    const uint8_t* orders4,
    uint16_t* out_inits
) {
    for (int32_t i = 0; i < count; ++i) {
        if (ks[i] == 4) {
            uint16_t t = inits[i];
            const uint8_t* sh = g_perm_tables.shuf16[pack_ord4(orders4 + i * 4)];
            alignas(16) uint8_t permuted_bytes[16];
            for (int a = 0; a < 16; ++a) {
                permuted_bytes[a] = ((t >> sh[a]) & 1U) ? 0xFFU : 0x00U;
            }
            __m128i v = _mm_load_si128(reinterpret_cast<const __m128i*>(permuted_bytes));
            out_inits[i] = static_cast<uint16_t>(_mm_movemask_epi8(v));
        } else {
            out_inits[i] = permute_lut4_scalar(inits[i], orders4 + i * 4, ks[i]);
        }
    }
}

// SSE2 16-byte XMM line-comment stripper matching `re.sub(r"//[^\n\r]*", "", vg_text)` byte-for-byte
EXPORT int64_t accel_strip_line_comments(const char* src, int64_t len, char* dst) {
    int64_t i = 0;
    int64_t out = 0;
    const __m128i vslash = _mm_set1_epi8('/');
    const __m128i vnl = _mm_set1_epi8('\n');
    const __m128i vcr = _mm_set1_epi8('\r');

    while (i < len) {
        int64_t scan = i;
        while (scan + 16 <= len) {
            __m128i chunk = _mm_loadu_si128(reinterpret_cast<const __m128i*>(src + scan));
            uint32_t m = static_cast<uint32_t>(_mm_movemask_epi8(_mm_cmpeq_epi8(chunk, vslash)));
            if (m != 0) {
                unsigned long bit = 0;
                _BitScanForward(&bit, m);
                scan += static_cast<int64_t>(bit);
                break;
            }
            scan += 16;
        }
        while (scan < len && src[scan] != '/') ++scan;
        if (scan > i) {
            std::memcpy(dst + out, src + i, static_cast<size_t>(scan - i));
            out += (scan - i);
            i = scan;
        }
        if (i >= len) break;
        if (i + 1 < len && src[i + 1] == '/') {
            i += 2;
            while (i + 16 <= len) {
                __m128i chunk = _mm_loadu_si128(reinterpret_cast<const __m128i*>(src + i));
                uint32_t m = static_cast<uint32_t>(_mm_movemask_epi8(
                    _mm_or_si128(_mm_cmpeq_epi8(chunk, vnl), _mm_cmpeq_epi8(chunk, vcr))));
                if (m != 0) {
                    unsigned long bit = 0;
                    _BitScanForward(&bit, m);
                    i += static_cast<int64_t>(bit);
                    break;
                }
                i += 16;
            }
            while (i < len && src[i] != '\n' && src[i] != '\r') ++i;
        } else {
            dst[out++] = src[i++];
        }
    }
    return out;
}

EXPORT int32_t accel_write_blif_file(
    const char* out_path,
    int32_t num_names,
    const char* names_blob,
    const int32_t* name_offsets,
    int32_t num_inputs,
    const int32_t* input_ids,
    int32_t num_outputs,
    const int32_t* output_ids,
    int32_t num_nodes,
    const int32_t* node_in_offsets,
    const int32_t* node_in_ids,
    const int32_t* node_out_ids,
    const uint64_t* node_truths,
    int32_t emit_zero_const_cube
) {
    (void)num_names;
    FILE* fp = open_utf8_wb(out_path);
    if (!fp) return -1;

    constexpr size_t BUF_CAP = 1 << 20;
    char* buf = static_cast<char*>(std::malloc(BUF_CAP));
    if (!buf) { std::fclose(fp); return -2; }
    size_t pos = 0;

    auto flush_buf = [&]() {
        if (pos > 0) {
            std::fwrite(buf, 1, pos, fp);
            pos = 0;
        }
    };
    auto append_mem = [&](const char* s, size_t n) {
        if (pos + n > BUF_CAP) flush_buf();
        if (n > BUF_CAP) {
            std::fwrite(s, 1, n, fp);
        } else {
            std::memcpy(buf + pos, s, n);
            pos += n;
        }
    };
    auto append_id = [&](int32_t id) {
        int32_t b = name_offsets[id];
        int32_t e = name_offsets[id + 1];
        append_mem(names_blob + b, static_cast<size_t>(e - b));
    };

    append_mem(".model logic\n.inputs", 20);
    if (num_inputs > 0) {
        for (int32_t i = 0; i < num_inputs; ++i) {
            append_mem(" ", 1);
            append_id(input_ids[i]);
        }
    } else {
        append_mem(" ", 1);
    }
    append_mem("\n.outputs", 9);
    if (num_outputs > 0) {
        for (int32_t i = 0; i < num_outputs; ++i) {
            append_mem(" ", 1);
            append_id(output_ids[i]);
        }
    } else {
        append_mem(" ", 1);
    }
    append_mem("\n", 1);

    char cube[16];
    for (int32_t ni = 0; ni < num_nodes; ++ni) {
        int32_t ib = node_in_offsets[ni];
        int32_t ie = node_in_offsets[ni + 1];
        int32_t k = ie - ib;
        append_mem(".names", 6);
        for (int32_t j = ib; j < ie; ++j) {
            append_mem(" ", 1);
            append_id(node_in_ids[j]);
        }
        append_mem(" ", 1);
        append_id(node_out_ids[ni]);
        append_mem("\n", 1);

        uint64_t t = node_truths[ni];
        if (k == 0) {
            if (t & 1ULL) {
                append_mem("1\n", 2);
            } else if (emit_zero_const_cube) {
                append_mem("0\n", 2);
            }
            continue;
        }
        if (k < 6) {
            uint64_t mask = (1ULL << (1 << k)) - 1ULL;
            t &= mask;
        }
        if (t == 0ULL && emit_zero_const_cube) {
            for (int bit = 0; bit < k; ++bit) cube[bit] = '-';
            cube[k] = ' ';
            cube[k + 1] = '0';
            cube[k + 2] = '\n';
            append_mem(cube, static_cast<size_t>(k + 3));
            continue;
        }
        while (t != 0ULL) {
            int a = ctz64_fast(t);
            t &= (t - 1ULL);
            for (int bit = 0; bit < k; ++bit) {
                cube[bit] = ((a >> bit) & 1) ? '1' : '0';
            }
            cube[k] = ' ';
            cube[k + 1] = '1';
            cube[k + 2] = '\n';
            append_mem(cube, static_cast<size_t>(k + 3));
        }
    }
    append_mem(".end\n", 5);
    flush_buf();
    std::free(buf);
    std::fclose(fp);
    return 0;
}

// Batch 64-bit GF(2) byte-wise affine bit-matrix transform (SWAR parity, zero post-SSE3 ops)
EXPORT void accel_gfni_bitmatrix_u64(
    int32_t count,
    const uint64_t* inputs,
    uint64_t matrix64,
    uint8_t imm8,
    uint64_t* outputs
) {
    for (int32_t i = 0; i < count; ++i) {
        uint64_t v = inputs[i], o = 0;
        for (int b = 0; b < 8; ++b) {
            uint8_t x = static_cast<uint8_t>((v >> (b * 8)) & 0xFFULL);
            uint8_t r = imm8;
            for (int bit = 0; bit < 8; ++bit) {
                uint8_t row = static_cast<uint8_t>((matrix64 >> ((7 - bit) * 8)) & 0xFFULL);
                uint8_t prod = static_cast<uint8_t>(x & row);
                prod ^= prod >> 4;
                prod ^= prod >> 2;
                prod ^= prod >> 1;
                if (prod & 1U) r ^= static_cast<uint8_t>(1U << bit);
            }
            o |= static_cast<uint64_t>(r) << (b * 8);
        }
        outputs[i] = o;
    }
}

// Fast native 21,872-frame CRC-16 (0x8005 / reflected 0xA001) & tail-padding verifier for Gowin .fs bitstreams.
EXPORT int32_t accel_audit_fs_frames(
    const char* frame_concat_ascii, // 21872 * 1584 contiguous '0'/'1' bytes
    int32_t frame_count,
    int32_t* out_bad_crc_indices,
    int32_t max_bad_out,
    int32_t* out_crc_bad_count,
    int32_t* out_tail_bad_indices,
    int32_t* out_tail_bad_count
) {
    if (!frame_concat_ascii || frame_count <= 0) return -1;
    uint16_t crc_tbl[256];
    for (int v = 0; v < 256; ++v) {
        uint16_t cur = static_cast<uint16_t>(v);
        for (int b = 0; b < 8; ++b) {
            cur = (cur & 1U) ? static_cast<uint16_t>((cur >> 1) ^ 0xA001U) : static_cast<uint16_t>(cur >> 1);
        }
        crc_tbl[v] = cur;
    }
    uint8_t bitrev8[256];
    for (int v = 0; v < 256; ++v) {
        uint8_t r = 0;
        for (int b = 0; b < 8; ++b) {
            if ((v >> b) & 1) r |= static_cast<uint8_t>(0x80U >> b);
        }
        bitrev8[v] = r;
    }

    int32_t crc_bad = 0;
    int32_t tail_bad = 0;
    constexpr int FRAME_BYTES = 198;
    constexpr int DATA_BYTES = 190;
    constexpr int FRAME_BITS = FRAME_BYTES * 8; // 1584
    const __m128i v_one_ch = _mm_set1_epi8('1');

    for (int32_t fi = 0; fi < frame_count; ++fi) {
        const char* row = frame_concat_ascii + static_cast<size_t>(fi) * FRAME_BITS;
        uint8_t packed[FRAME_BYTES];
        int bi = 0;
        for (; bi + 2 <= FRAME_BYTES; bi += 2) {
            __m128i ch = _mm_loadu_si128(reinterpret_cast<const __m128i*>(row + bi * 8));
            uint32_t m = static_cast<uint32_t>(_mm_movemask_epi8(_mm_cmpeq_epi8(ch, v_one_ch)));
            packed[bi + 0] = bitrev8[(m >> 0) & 0xFFU];
            packed[bi + 1] = bitrev8[(m >> 8) & 0xFFU];
        }
        for (; bi < FRAME_BYTES; ++bi) {
            const char* p = row + bi * 8;
            uint8_t v = 0;
            for (int k = 0; k < 8; ++k) {
                if (p[k] == '1') v |= static_cast<uint8_t>(0x80U >> k);
            }
            packed[bi] = v;
        }

        uint16_t st = (fi == 0) ? 0x8373U : 0x8F01U;
        for (int col = 0; col < DATA_BYTES; ++col) {
            st = static_cast<uint16_t>((st >> 8) ^ crc_tbl[(st ^ packed[col]) & 0xFFU]);
        }
        if (packed[190] != static_cast<uint8_t>(st & 0xFFU) || packed[191] != static_cast<uint8_t>(st >> 8)) {
            if (out_bad_crc_indices && crc_bad < max_bad_out) out_bad_crc_indices[crc_bad] = fi;
            ++crc_bad;
        }
        if ((packed[192] & packed[193] & packed[194] & packed[195] & packed[196] & packed[197]) != 0xFFU) {
            if (out_tail_bad_indices && tail_bad < max_bad_out) out_tail_bad_indices[tail_bad] = fi;
            ++tail_bad;
        }
    }
    if (out_crc_bad_count) *out_crc_bad_count = crc_bad;
    if (out_tail_bad_count) *out_tail_bad_count = tail_bad;
    return 0;
}
