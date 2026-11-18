#pragma once
#pragma fp_contract(off)

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace ng {

inline int popcnt64(uint64_t x) noexcept {
    x = x - ((x >> 1) & 0x5555555555555555ULL);
    x = (x & 0x3333333333333333ULL) + ((x >> 2) & 0x3333333333333333ULL);
    x = (x + (x >> 4)) & 0x0F0F0F0F0F0F0F0FULL;
    return static_cast<int>((x * 0x0101010101010101ULL) >> 56);
}

struct TimeoutError : public std::runtime_error {
    using std::runtime_error::runtime_error;
};

// UTF-8 <-> Wide string conversion
std::wstring utf8_to_wide(std::string_view s);
std::string wide_to_utf8(std::wstring_view w);

// Environment & Path utilities (all paths stored as UTF-8 internally)
std::string get_env(const char* name);
void set_env(const char* name, const std::string& val);
void unset_env(const char* name);

std::string resolve_path(std::string_view p);
std::string posix_path(std::string_view p);
std::string path_join(std::string_view a, std::string_view b);
std::string parent_dir(std::string_view p);
std::string file_name(std::string_view p);
std::string file_stem(std::string_view p);
std::string file_ext(std::string_view p);
bool is_absolute_path(std::string_view p);
bool is_subpath_of(std::string_view child, std::string_view parent);
std::string relative_posix_path(std::string_view target, std::string_view base);

// Filesystem operations (KERNEL32-only)
bool file_exists(std::string_view p);
bool dir_exists(std::string_view p);
bool path_exists(std::string_view p);
uint64_t file_size_bytes(std::string_view p);
uint64_t file_mtime_100ns(std::string_view p);
void make_dirs(std::string_view p, bool exist_ok = true);
void remove_file_if_exists(std::string_view p);
void remove_all(std::string_view p);
void copy_file_overwrite(std::string_view src, std::string_view dst);
void hardlink_or_copy(std::string_view src, std::string_view dst);
void rename_path(std::string_view src, std::string_view dst);

struct DirEntry {
    std::string path;
    std::string name;
    bool is_dir = false;
    bool is_file = false;
    bool is_reparse = false;
};
std::vector<DirEntry> list_dir(std::string_view dir);
std::vector<std::string> rglob_files(std::string_view dir);

std::vector<uint8_t> read_file_bytes(std::string_view p);
std::string read_file_text(std::string_view p);
void write_file_bytes(std::string_view p, const void* data, size_t len);
void write_file_text(std::string_view p, std::string_view text);
void append_file_text(std::string_view p, std::string_view text);

// Pure C++ SHA-256 & RFC-1952 Gzip (.gz) compression/decompression
std::string sha256_bytes(const void* data, size_t len);
inline std::string sha256_str(std::string_view s) { return sha256_bytes(s.data(), s.size()); }
std::string sha256_file(std::string_view p);

std::vector<uint8_t> gzip_compress(const void* data, size_t len);
void write_gzip_file(std::string_view p, std::string_view text);

// Monotonic high-resolution timer
inline double perf_counter() noexcept {
    using clk = std::chrono::steady_clock;
    return std::chrono::duration<double>(clk::now().time_since_epoch()).count();
}

unsigned int hardware_threads() noexcept;

// Shared 1-hour flow wall-clock budget
class FlowBudget {
public:
    FlowBudget() = default;
    FlowBudget(int total_seconds, double prior_seconds = 0.0);

    double used() const noexcept;
    double remaining() const;
    double timeout(double requested, double reserve_sec = 0.0) const;
    int total_seconds() const noexcept { return total_seconds_; }
    bool active() const noexcept { return active_; }

private:
    bool active_ = false;
    int total_seconds_ = 3600;
    double prior_seconds_ = 0.0;
    double start_time_ = 0.0;
};

void set_active_budget(const FlowBudget* b) noexcept;
double clipped_timeout(double requested);

// Subprocess runner with timeout & environment control
struct ProcResult {
    int returncode = -1;
    bool timed_out = false;
    double wall_seconds = 0.0;
    std::string output;
};

ProcResult run_process_captured(
    const std::vector<std::string>& args,
    std::string_view cwd,
    const std::vector<std::pair<std::string, std::string>>& extra_env,
    double timeout_sec
);

ProcResult run_process_to_log(
    const std::vector<std::string>& args,
    std::string_view cwd,
    std::string_view log_file,
    const std::vector<std::pair<std::string, std::string>>& extra_env,
    double timeout_sec
);

// Runtime context resolved at startup (Tier 1 baseline: x86-64 + SSE2 + SSE3)
struct RuntimePaths {
    std::string env_dir;
    std::string bin_dir;
    std::string res_root;
    std::string gowin_sdk;   // .../IDE
    std::string nextgowin_exe;
    std::string core_dll;
    std::string syn_accel_dll;
    std::string bridge_dll;
    std::string abc_exe;
    std::string yosys_exe;
    bool allow_unsupported_gowin = false;
    std::string expected_gowin_version = "Gowin V1.9.12.04 x64";
    std::string detected_gowin_version;
    std::string gowin_version_warning;
};

const RuntimePaths& runtime_paths();
void init_runtime_paths(const RuntimePaths& rp);

// Returns true if path contains whitespace or non-ASCII bytes that ANSI/cmd.do tools cannot open directly
bool needs_ascii_alias(std::string_view p) noexcept;

// RAII ASCII space-free directory alias (NTFS junction with copy/mirror fallback for non-NTFS drives)
class AsciiDirAlias {
public:
    explicit AsciiDirAlias(std::string_view target_dir, bool mirror_back_if_copied = false);
    ~AsciiDirAlias();
    AsciiDirAlias(const AsciiDirAlias&) = delete;
    AsciiDirAlias& operator=(const AsciiDirAlias&) = delete;

    const std::string& path() const noexcept { return alias_dir_; }
    const std::string& real_path() const noexcept { return real_dir_; }
    bool aliased() const noexcept { return aliased_; }
    void sync_back();

private:
    void cleanup() noexcept;
    std::string real_dir_;
    std::string alias_dir_;
    bool aliased_ = false;
    bool is_junction_ = false;
    bool mirror_back_ = false;
};

// Transient Gowin SDK host staging (_ng_gowin_host/bin/nextgowin_host.exe)
class GowinHostStaging {
public:
    GowinHostStaging(std::string_view work_parent, std::string_view sdk_ide_dir);
    ~GowinHostStaging();
    GowinHostStaging(const GowinHostStaging&) = delete;
    GowinHostStaging& operator=(const GowinHostStaging&) = delete;

    const std::string& host_exe() const noexcept { return host_exe_; }
    const std::string& host_bin_dir() const noexcept { return bin_dir_; }

private:
    void cleanup() noexcept;
    std::string host_root_;
    std::string bin_dir_;
    std::string host_exe_;
    std::string cmd_exe_;
    std::vector<std::string> junctions_;
};

// String / Lexer / Verilog helpers
std::string trim(std::string_view s);
std::string ltrim(std::string_view s);
std::string rtrim(std::string_view s);
std::string to_lower_ascii(std::string_view s);
std::string to_upper_ascii(std::string_view s);
std::vector<std::string> split_lines(std::string_view s);
std::vector<std::string> split_ws(std::string_view s);
std::vector<std::string> split_char(std::string_view s, char sep);
std::string join_strings(const std::vector<std::string>& items, std::string_view sep);
bool starts_with(std::string_view s, std::string_view prefix) noexcept;
bool ends_with(std::string_view s, std::string_view suffix) noexcept;
bool fnmatch_case(std::string_view name, std::string_view pattern) noexcept;
bool is_valid_verilog_ident(std::string_view tok) noexcept;
bool is_simple_ident(std::string_view tok) noexcept;

// Linear-time Verilog comment/string masker (`masked(text, mask_strings)`)
std::string masked_verilog(std::string_view text, bool mask_strings = false);

struct UnitSpan {
    std::string name;
    std::string kind; // "module" or "primitive"
    size_t start = 0;
    size_t end = 0;
};

// Equivalent to verific_bridge.units(text): preserves order of appearance
std::vector<UnitSpan> verilog_units(std::string_view text);
// Equivalent to split_primitive_models.units(text): modules + primitives
std::vector<UnitSpan> primitive_model_units(std::string_view text);

// Equivalent to gowin_cones.TOKEN.findall(s)
std::vector<std::string> tokenize_verilog(std::string_view s);

// Equivalent to gowin_cones.connections(words) -> ordered vector of (pin_name, token_list)
std::vector<std::pair<std::string, std::vector<std::string>>> parse_verilog_connections(const std::vector<std::string>& words);

// Equivalent to gowin_cones.literal(token) -> (width, uint64_value) (or arbitrary-width hex/bin string)
std::pair<int, uint64_t> parse_verilog_literal_u64(std::string_view token);
std::string format_verilog_Parameter_bits(std::string_view raw_val, int target_width, char base_ch);

// Dynamic syn_accel.dll wrapper
class SynAccelLib {
public:
    static SynAccelLib& instance();

    int32_t tier_id() const;
    void permute_lut_batch(int32_t count, const uint16_t* inits, const uint8_t* ks, const uint8_t* orders4, uint16_t* out_inits) const;
    std::string strip_line_comments(std::string_view src) const;
    int32_t write_blif_file(
        const char* path_utf8,
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
    ) const;
    void gfni_bitmatrix_u64(int32_t count, const uint64_t* src, uint64_t matrix64, uint8_t imm8, uint64_t* dst) const;
    int32_t audit_fs_frames(
        const char* ascii_frames,
        int32_t frame_count,
        int32_t* bad_crc_indices,
        int32_t max_bad,
        int32_t* out_bad_crc_count,
        int32_t* bad_tail_indices,
        int32_t* out_bad_tail_count
    ) const;

private:
    SynAccelLib();
    HMODULE mod_ = nullptr;
    void* fn_tier_ = nullptr;
    void* fn_perm_ = nullptr;
    void* fn_strip_ = nullptr;
    void* fn_blif_ = nullptr;
    void* fn_gfni_ = nullptr;
    void* fn_fs_ = nullptr;
};

} // namespace ng
