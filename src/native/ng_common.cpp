#include "ng_common.hpp"

#include <atomic>
#include <cctype>
#include <map>
#include <mutex>
#include <thread>
#include <unordered_map>

namespace ng {

std::wstring utf8_to_wide(std::string_view s) {
    if (s.empty()) return L"";
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
    if (n <= 0) return L"";
    std::wstring w(static_cast<size_t>(n), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), w.data(), n);
    return w;
}

std::string wide_to_utf8(std::wstring_view w) {
    if (w.empty()) return "";
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), nullptr, 0, nullptr, nullptr);
    if (n <= 0) return "";
    std::string s(static_cast<size_t>(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), s.data(), n, nullptr, nullptr);
    return s;
}

std::string get_env(const char* name) {
    std::wstring wn = utf8_to_wide(name);
    DWORD need = GetEnvironmentVariableW(wn.c_str(), nullptr, 0);
    if (need == 0) return "";
    std::wstring buf(need, L'\0');
    DWORD got = GetEnvironmentVariableW(wn.c_str(), buf.data(), need);
    if (got == 0 || got >= need) return "";
    buf.resize(got);
    return wide_to_utf8(buf);
}

void set_env(const char* name, const std::string& val) {
    SetEnvironmentVariableW(utf8_to_wide(name).c_str(), utf8_to_wide(val).c_str());
}

void unset_env(const char* name) {
    SetEnvironmentVariableW(utf8_to_wide(name).c_str(), nullptr);
}

static std::wstring win_long_path(std::string_view p) {
    std::wstring w = utf8_to_wide(p);
    for (auto& ch : w) {
        if (ch == L'/') ch = L'\\';
    }
    if (w.size() >= 240 && w.rfind(L"\\\\?\\", 0) != 0) {
        std::wstring abs_w;
        DWORD need = GetFullPathNameW(w.c_str(), 0, nullptr, nullptr);
        if (need > 0) {
            abs_w.resize(need);
            DWORD got = GetFullPathNameW(w.c_str(), need, abs_w.data(), nullptr);
            if (got > 0 && got < need) {
                abs_w.resize(got);
                if (abs_w.rfind(L"\\\\", 0) == 0) {
                    return L"\\\\?\\UNC\\" + abs_w.substr(2);
                }
                return L"\\\\?\\" + abs_w;
            }
        }
    }
    return w;
}

std::string resolve_path(std::string_view p) {
    if (p.empty()) return "";
    std::wstring w = utf8_to_wide(p);
    for (auto& ch : w) {
        if (ch == L'/') ch = L'\\';
    }
    DWORD need = GetFullPathNameW(w.c_str(), 0, nullptr, nullptr);
    if (need == 0) return std::string(p);
    std::wstring buf(need, L'\0');
    DWORD got = GetFullPathNameW(w.c_str(), need, buf.data(), nullptr);
    if (got == 0 || got >= need) return std::string(p);
    buf.resize(got);
    while (buf.size() > 3 && (buf.back() == L'\\' || buf.back() == L'/')) {
        buf.pop_back();
    }
    return wide_to_utf8(buf);
}

std::string posix_path(std::string_view p) {
    std::string r(p);
    for (char& c : r) {
        if (c == '\\') c = '/';
    }
    return r;
}

bool is_absolute_path(std::string_view p) {
    if (p.size() >= 2 && std::isalpha(static_cast<unsigned char>(p[0])) && p[1] == ':') return true;
    if (p.size() >= 2 && (p[0] == '\\' || p[0] == '/') && (p[1] == '\\' || p[1] == '/')) return true;
    if (!p.empty() && (p[0] == '\\' || p[0] == '/')) return true;
    return false;
}

std::string path_join(std::string_view a, std::string_view b) {
    if (b.empty()) return std::string(a);
    if (a.empty() || is_absolute_path(b)) return std::string(b);
    std::string res(a);
    while (!res.empty() && (res.back() == '\\' || res.back() == '/')) res.pop_back();
    res.push_back('\\');
    size_t i = 0;
    while (i < b.size() && (b[i] == '\\' || b[i] == '/')) ++i;
    res.append(b.substr(i));
    return res;
}

std::string parent_dir(std::string_view p) {
    std::string s(p);
    while (s.size() > 3 && (s.back() == '\\' || s.back() == '/')) s.pop_back();
    size_t pos = s.find_last_of("\\/");
    if (pos == std::string::npos) return ".";
    if (pos == 2 && s[1] == ':') return s.substr(0, 3);
    return s.substr(0, pos);
}

std::string file_name(std::string_view p) {
    std::string s(p);
    while (s.size() > 3 && (s.back() == '\\' || s.back() == '/')) s.pop_back();
    size_t pos = s.find_last_of("\\/");
    return (pos == std::string::npos) ? s : s.substr(pos + 1);
}

std::string file_stem(std::string_view p) {
    std::string fn = file_name(p);
    size_t dot = fn.find_last_of('.');
    if (dot == std::string::npos || dot == 0) return fn;
    return fn.substr(0, dot);
}

std::string file_ext(std::string_view p) {
    std::string fn = file_name(p);
    size_t dot = fn.find_last_of('.');
    if (dot == std::string::npos || dot == 0) return "";
    return fn.substr(dot);
}

bool is_subpath_of(std::string_view child, std::string_view parent) {
    std::string c = to_lower_ascii(posix_path(resolve_path(child)));
    std::string p = to_lower_ascii(posix_path(resolve_path(parent)));
    while (p.size() > 1 && p.back() == '/') p.pop_back();
    while (c.size() > 1 && c.back() == '/') c.pop_back();
    if (c == p) return true;
    return c.size() > p.size() && c.compare(0, p.size(), p) == 0 && c[p.size()] == '/';
}

std::string relative_posix_path(std::string_view target, std::string_view base) {
    std::string t = posix_path(resolve_path(target));
    std::string b = posix_path(resolve_path(base));
    while (b.size() > 1 && b.back() == '/') b.pop_back();
    std::string tl = to_lower_ascii(t);
    std::string bl = to_lower_ascii(b);
    if (tl == bl) return ".";
    if (tl.size() > bl.size() && tl.compare(0, bl.size(), bl) == 0 && t[b.size()] == '/') {
        return t.substr(b.size() + 1);
    }
    throw std::runtime_error("Target path is not relative to base: " + t + " vs " + b);
}

bool file_exists(std::string_view p) {
    if (p.empty()) return false;
    DWORD a = GetFileAttributesW(win_long_path(p).c_str());
    return (a != INVALID_FILE_ATTRIBUTES) && ((a & FILE_ATTRIBUTE_DIRECTORY) == 0);
}

bool dir_exists(std::string_view p) {
    if (p.empty()) return false;
    DWORD a = GetFileAttributesW(win_long_path(p).c_str());
    return (a != INVALID_FILE_ATTRIBUTES) && ((a & FILE_ATTRIBUTE_DIRECTORY) != 0);
}

bool path_exists(std::string_view p) {
    if (p.empty()) return false;
    return GetFileAttributesW(win_long_path(p).c_str()) != INVALID_FILE_ATTRIBUTES;
}

uint64_t file_size_bytes(std::string_view p) {
    WIN32_FILE_ATTRIBUTE_DATA fad{};
    if (!GetFileAttributesExW(win_long_path(p).c_str(), GetFileExInfoStandard, &fad)) {
        throw std::runtime_error("Cannot stat file size: " + std::string(p));
    }
    return (static_cast<uint64_t>(fad.nFileSizeHigh) << 32) | fad.nFileSizeLow;
}

uint64_t file_mtime_100ns(std::string_view p) {
    WIN32_FILE_ATTRIBUTE_DATA fad{};
    if (!GetFileAttributesExW(win_long_path(p).c_str(), GetFileExInfoStandard, &fad)) {
        throw std::runtime_error("Cannot stat file mtime: " + std::string(p));
    }
    return (static_cast<uint64_t>(fad.ftLastWriteTime.dwHighDateTime) << 32) | fad.ftLastWriteTime.dwLowDateTime;
}

void make_dirs(std::string_view p, bool exist_ok) {
    std::string full = resolve_path(p);
    if (dir_exists(full)) {
        if (!exist_ok) {
            throw std::runtime_error("Directory already exists: " + full);
        }
        return;
    }
    std::string par = parent_dir(full);
    if (!par.empty() && par != full && !dir_exists(par)) {
        make_dirs(par, true);
    }
    std::wstring wf = win_long_path(full);
    if (!CreateDirectoryW(wf.c_str(), nullptr)) {
        DWORD err = GetLastError();
        if (err == ERROR_ALREADY_EXISTS && exist_ok && dir_exists(full)) return;
        throw std::runtime_error("CreateDirectoryW failed for " + full + " (err=" + std::to_string(err) + ")");
    }
}

void remove_file_if_exists(std::string_view p) {
    std::wstring wp = win_long_path(p);
    DWORD attr = GetFileAttributesW(wp.c_str());
    if (attr == INVALID_FILE_ATTRIBUTES) return;
    if (attr & FILE_ATTRIBUTE_READONLY) {
        SetFileAttributesW(wp.c_str(), attr & ~FILE_ATTRIBUTE_READONLY);
    }
    DeleteFileW(wp.c_str());
}

std::vector<DirEntry> list_dir(std::string_view dir) {
    std::vector<DirEntry> out;
    std::string base = resolve_path(dir);
    std::wstring pat = win_long_path(base + "\\*");
    WIN32_FIND_DATAW fd{};
    HANDLE h = FindFirstFileExW(pat.c_str(), FindExInfoBasic, &fd, FindExSearchNameMatch, nullptr, 0);
    if (h == INVALID_HANDLE_VALUE) return out;
    do {
        if (std::wcscmp(fd.cFileName, L".") == 0 || std::wcscmp(fd.cFileName, L"..") == 0) continue;
        DirEntry e;
        e.name = wide_to_utf8(fd.cFileName);
        e.path = path_join(base, e.name);
        e.is_dir = (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
        e.is_file = !e.is_dir;
        e.is_reparse = (fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0;
        out.push_back(std::move(e));
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    std::sort(out.begin(), out.end(), [](const DirEntry& a, const DirEntry& b) { return a.name < b.name; });
    return out;
}

std::vector<std::string> rglob_files(std::string_view dir) {
    std::vector<std::string> res;
    std::vector<std::string> stack{resolve_path(dir)};
    while (!stack.empty()) {
        std::string cur = std::move(stack.back());
        stack.pop_back();
        auto entries = list_dir(cur);
        for (auto it = entries.rbegin(); it != entries.rend(); ++it) {
            if (it->is_dir) stack.push_back(it->path);
        }
        for (const auto& e : entries) {
            if (e.is_file) res.push_back(e.path);
        }
    }
    std::sort(res.begin(), res.end());
    return res;
}

void remove_all(std::string_view p) {
    std::wstring wp = win_long_path(p);
    DWORD attr = GetFileAttributesW(wp.c_str());
    if (attr == INVALID_FILE_ATTRIBUTES) return;
    if ((attr & FILE_ATTRIBUTE_DIRECTORY) == 0) {
        remove_file_if_exists(p);
        return;
    }
    // If NTFS junction / symlink directory, remove directory link directly without recursing into target!
    if (attr & FILE_ATTRIBUTE_REPARSE_POINT) {
        if (attr & FILE_ATTRIBUTE_READONLY) {
            SetFileAttributesW(wp.c_str(), attr & ~FILE_ATTRIBUTE_READONLY);
        }
        RemoveDirectoryW(wp.c_str());
        return;
    }
    for (const auto& e : list_dir(p)) {
        remove_all(e.path);
    }
    if (attr & FILE_ATTRIBUTE_READONLY) {
        SetFileAttributesW(wp.c_str(), attr & ~FILE_ATTRIBUTE_READONLY);
    }
    RemoveDirectoryW(wp.c_str());
}

void copy_file_overwrite(std::string_view src, std::string_view dst) {
    make_dirs(parent_dir(dst), true);
    remove_file_if_exists(dst);
    std::wstring ws = win_long_path(src);
    std::wstring wd = win_long_path(dst);
    if (!CopyFileW(ws.c_str(), wd.c_str(), FALSE)) {
        throw std::runtime_error("CopyFileW failed from " + std::string(src) + " to " + std::string(dst) + " (err=" + std::to_string(GetLastError()) + ")");
    }
    DWORD attr = GetFileAttributesW(wd.c_str());
    if (attr != INVALID_FILE_ATTRIBUTES && (attr & FILE_ATTRIBUTE_READONLY)) {
        SetFileAttributesW(wd.c_str(), attr & ~FILE_ATTRIBUTE_READONLY);
    }
}

void hardlink_or_copy(std::string_view src, std::string_view dst) {
    make_dirs(parent_dir(dst), true);
    remove_file_if_exists(dst);
    std::wstring ws = win_long_path(src);
    std::wstring wd = win_long_path(dst);
    if (CreateHardLinkW(wd.c_str(), ws.c_str(), nullptr)) {
        return;
    }
    copy_file_overwrite(src, dst);
}

void rename_path(std::string_view src, std::string_view dst) {
    std::wstring ws = win_long_path(src);
    std::wstring wd = win_long_path(dst);
    if (!MoveFileExW(ws.c_str(), wd.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_COPY_ALLOWED)) {
        throw std::runtime_error("MoveFileExW failed from " + std::string(src) + " to " + std::string(dst));
    }
}

std::vector<uint8_t> read_file_bytes(std::string_view p) {
    std::wstring wp = win_long_path(p);
    HANDLE h = CreateFileW(wp.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        throw std::runtime_error("FileNotFoundError: " + std::string(p));
    }
    LARGE_INTEGER sz{};
    if (!GetFileSizeEx(h, &sz)) {
        CloseHandle(h);
        throw std::runtime_error("GetFileSizeEx failed: " + std::string(p));
    }
    std::vector<uint8_t> buf(static_cast<size_t>(sz.QuadPart));
    size_t pos = 0;
    while (pos < buf.size()) {
        DWORD chunk = static_cast<DWORD>(std::min<size_t>(buf.size() - pos, 1u << 26));
        DWORD got = 0;
        if (!ReadFile(h, buf.data() + pos, chunk, &got, nullptr)) {
            CloseHandle(h);
            throw std::runtime_error("ReadFile failed: " + std::string(p));
        }
        if (got == 0) break;
        pos += got;
    }
    CloseHandle(h);
    buf.resize(pos);
    return buf;
}

std::string read_file_text(std::string_view p) {
    auto bytes = read_file_bytes(p);
    // Normalize CRLF -> LF just like Python Path.read_text() universal newlines!
    std::string out;
    out.reserve(bytes.size());
    for (size_t i = 0; i < bytes.size(); ++i) {
        if (bytes[i] == '\r' && i + 1 < bytes.size() && bytes[i + 1] == '\n') {
            continue;
        }
        out.push_back(static_cast<char>(bytes[i]));
    }
    return out;
}

void write_file_bytes(std::string_view p, const void* data, size_t len) {
    make_dirs(parent_dir(p), true);
    std::wstring wp = win_long_path(p);
    DWORD attr = GetFileAttributesW(wp.c_str());
    if (attr != INVALID_FILE_ATTRIBUTES && (attr & FILE_ATTRIBUTE_READONLY)) {
        SetFileAttributesW(wp.c_str(), attr & ~FILE_ATTRIBUTE_READONLY);
    }
    HANDLE h = CreateFileW(wp.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        throw std::runtime_error("CreateFileW write failed: " + std::string(p) + " (err=" + std::to_string(GetLastError()) + ")");
    }
    const uint8_t* ptr = static_cast<const uint8_t*>(data);
    size_t pos = 0;
    while (pos < len) {
        DWORD chunk = static_cast<DWORD>(std::min<size_t>(len - pos, 1u << 26));
        DWORD wr = 0;
        if (!WriteFile(h, ptr + pos, chunk, &wr, nullptr)) {
            CloseHandle(h);
            throw std::runtime_error("WriteFile failed: " + std::string(p));
        }
        pos += wr;
    }
    CloseHandle(h);
}

void write_file_text(std::string_view p, std::string_view text) {
    write_file_bytes(p, text.data(), text.size());
}

void append_file_text(std::string_view p, std::string_view text) {
    make_dirs(parent_dir(p), true);
    std::wstring wp = win_long_path(p);
    HANDLE h = CreateFileW(wp.c_str(), FILE_APPEND_DATA, FILE_SHARE_READ, nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        throw std::runtime_error("CreateFileW append failed: " + std::string(p));
    }
    const uint8_t* ptr = reinterpret_cast<const uint8_t*>(text.data());
    size_t pos = 0;
    while (pos < text.size()) {
        DWORD chunk = static_cast<DWORD>(std::min<size_t>(text.size() - pos, 1u << 26));
        DWORD wr = 0;
        if (!WriteFile(h, ptr + pos, chunk, &wr, nullptr)) {
            CloseHandle(h);
            throw std::runtime_error("WriteFile append failed: " + std::string(p));
        }
        pos += wr;
    }
    CloseHandle(h);
}

// ============================================================================
// Pure C++20 SHA-256 (FIPS 180-4) — Zero external DLL dependency
// ============================================================================
namespace {

constexpr uint32_t K256[64] = {
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
    0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
    0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
    0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
    0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
    0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
    0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
    0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u, 0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u
};

inline uint32_t rotr32(uint32_t x, int n) noexcept { return (x >> n) | (x << (32 - n)); }

struct Sha256Ctx {
    uint32_t state[8] = {
        0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
        0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u
    };
    uint64_t total_len = 0;
    uint8_t buf[64]{};
    size_t buf_len = 0;

    void transform(const uint8_t block[64]) noexcept {
        uint32_t w[64];
        for (int i = 0; i < 16; ++i) {
            w[i] = (static_cast<uint32_t>(block[i * 4 + 0]) << 24)
                 | (static_cast<uint32_t>(block[i * 4 + 1]) << 16)
                 | (static_cast<uint32_t>(block[i * 4 + 2]) << 8)
                 | (static_cast<uint32_t>(block[i * 4 + 3]));
        }
        for (int i = 16; i < 64; ++i) {
            uint32_t s0 = rotr32(w[i - 15], 7) ^ rotr32(w[i - 15], 18) ^ (w[i - 15] >> 3);
            uint32_t s1 = rotr32(w[i - 2], 17) ^ rotr32(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        uint32_t a = state[0], b = state[1], c = state[2], d = state[3];
        uint32_t e = state[4], f = state[5], g = state[6], h = state[7];
        for (int i = 0; i < 64; ++i) {
            uint32_t S1 = rotr32(e, 6) ^ rotr32(e, 11) ^ rotr32(e, 25);
            uint32_t ch = (e & f) ^ ((~e) & g);
            uint32_t temp1 = h + S1 + ch + K256[i] + w[i];
            uint32_t S0 = rotr32(a, 2) ^ rotr32(a, 13) ^ rotr32(a, 22);
            uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
            uint32_t temp2 = S0 + maj;
            h = g; g = f; f = e; e = d + temp1;
            d = c; c = b; b = a; a = temp1 + temp2;
        }
        state[0] += a; state[1] += b; state[2] += c; state[3] += d;
        state[4] += e; state[5] += f; state[6] += g; state[7] += h;
    }

    void update(const uint8_t* data, size_t len) noexcept {
        total_len += len;
        if (buf_len > 0) {
            size_t take = std::min<size_t>(64 - buf_len, len);
            std::memcpy(buf + buf_len, data, take);
            buf_len += take;
            data += take;
            len -= take;
            if (buf_len == 64) {
                transform(buf);
                buf_len = 0;
            }
        }
        while (len >= 64) {
            transform(data);
            data += 64;
            len -= 64;
        }
        if (len > 0) {
            std::memcpy(buf, data, len);
            buf_len = len;
        }
    }

    std::string finish_hex() noexcept {
        uint64_t bit_len = total_len * 8ULL;
        buf[buf_len++] = 0x80u;
        if (buf_len > 56) {
            std::memset(buf + buf_len, 0, 64 - buf_len);
            transform(buf);
            buf_len = 0;
        }
        std::memset(buf + buf_len, 0, 56 - buf_len);
        for (int i = 0; i < 8; ++i) {
            buf[56 + i] = static_cast<uint8_t>((bit_len >> (56 - 8 * i)) & 0xFFu);
        }
        transform(buf);
        static constexpr char HEX[] = "0123456789abcdef";
        std::string res(64, '0');
        for (int i = 0; i < 8; ++i) {
            uint32_t v = state[i];
            for (int j = 0; j < 8; ++j) {
                res[i * 8 + j] = HEX[(v >> (28 - 4 * j)) & 0xFu];
            }
        }
        return res;
    }
};

} // namespace

std::string sha256_bytes(const void* data, size_t len) {
    Sha256Ctx ctx;
    if (len > 0) ctx.update(static_cast<const uint8_t*>(data), len);
    return ctx.finish_hex();
}

std::string sha256_file(std::string_view p) {
    std::wstring wp = win_long_path(p);
    HANDLE h = CreateFileW(wp.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        throw std::runtime_error("sha256_file cannot open: " + std::string(p));
    }
    Sha256Ctx ctx;
    std::vector<uint8_t> chunk(1u << 20);
    for (;;) {
        DWORD got = 0;
        if (!ReadFile(h, chunk.data(), static_cast<DWORD>(chunk.size()), &got, nullptr)) {
            CloseHandle(h);
            throw std::runtime_error("sha256_file ReadFile failed: " + std::string(p));
        }
        if (got == 0) break;
        ctx.update(chunk.data(), got);
    }
    CloseHandle(h);
    return ctx.finish_hex();
}

// ============================================================================
// Pure C++20 RFC-1951 / RFC-1952 Gzip (.gz) Compressor (LZ77 + Fixed Huffman)
// ============================================================================
namespace {

uint32_t crc32_ieee(const uint8_t* data, size_t len) noexcept {
    static uint32_t tbl[256];
    static std::once_flag flag;
    std::call_once(flag, []() {
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t c = i;
            for (int k = 0; k < 8; ++k) {
                c = (c & 1u) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
            }
            tbl[i] = c;
        }
    });
    uint32_t c = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; ++i) {
        c = tbl[(c ^ data[i]) & 0xFFu] ^ (c >> 8);
    }
    return c ^ 0xFFFFFFFFu;
}

struct BitWriter {
    std::vector<uint8_t> out;
    uint64_t bit_buf = 0;
    int bit_cnt = 0;

    void put_lsb(uint32_t val, int nbits) {
        bit_buf |= (static_cast<uint64_t>(val) << bit_cnt);
        bit_cnt += nbits;
        while (bit_cnt >= 8) {
            out.push_back(static_cast<uint8_t>(bit_buf & 0xFFu));
            bit_buf >>= 8;
            bit_cnt -= 8;
        }
    }

    void put_huff_msb(uint32_t code, int nbits) {
        uint32_t rev = 0;
        for (int i = 0; i < nbits; ++i) {
            rev = (rev << 1) | ((code >> i) & 1u);
        }
        put_lsb(rev, nbits);
    }

    void put_fixed_litlen(int sym) {
        if (sym <= 143) {
            put_huff_msb(0x30u + static_cast<uint32_t>(sym), 8);
        } else if (sym <= 255) {
            put_huff_msb(0x190u + static_cast<uint32_t>(sym - 144), 9);
        } else if (sym <= 279) {
            put_huff_msb(static_cast<uint32_t>(sym - 256), 7);
        } else {
            put_huff_msb(0xC0u + static_cast<uint32_t>(sym - 280), 8);
        }
    }

    void flush_byte_align() {
        if (bit_cnt > 0) {
            out.push_back(static_cast<uint8_t>(bit_buf & 0xFFu));
            bit_buf = 0;
            bit_cnt = 0;
        }
    }
};

} // namespace

std::vector<uint8_t> gzip_compress(const void* raw_data, size_t len) {
    const uint8_t* src = static_cast<const uint8_t*>(raw_data);
    BitWriter bw;
    bw.out.reserve(32 + len / 4);
    // RFC 1952 10-byte header
    const uint8_t hdr[10] = {0x1Fu, 0x8Bu, 0x08u, 0x00u, 0u, 0u, 0u, 0u, 0x00u, 0x03u};
    bw.out.insert(bw.out.end(), hdr, hdr + 10);

    // Final fixed-Huffman block: BFINAL=1, BTYPE=01 (LSB-first: 1, then 01 -> 0b011)
    bw.put_lsb(1u, 1);
    bw.put_lsb(1u, 2);

    static constexpr int LEN_BASE[29] = {
        3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258
    };
    static constexpr int LEN_EXTRA[29] = {
        0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0
    };
    static constexpr int DIST_BASE[30] = {
        1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,
        1025,1537,2049,3073,4097,6145,8193,12289,16385,24577
    };
    static constexpr int DIST_EXTRA[30] = {
        0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13
    };

    constexpr size_t HASH_BITS = 15;
    constexpr size_t HASH_SZ = 1u << HASH_BITS;
    constexpr size_t WIN_MASK = 32767;
    std::vector<int32_t> head(HASH_SZ, -1);
    std::vector<int32_t> prev(32768, -1);

    auto hash3 = [&](size_t p) noexcept -> uint32_t {
        uint32_t v = (static_cast<uint32_t>(src[p]) << 16)
                   | (static_cast<uint32_t>(src[p + 1]) << 8)
                   | static_cast<uint32_t>(src[p + 2]);
        return (v * 2654435761u) >> (32 - HASH_BITS);
    };

    size_t pos = 0;
    while (pos < len) {
        int best_len = 0;
        int best_dist = 0;
        if (pos + 3 <= len) {
            uint32_t h = hash3(pos);
            int32_t cand = head[h];
            prev[pos & WIN_MASK] = cand;
            head[h] = static_cast<int32_t>(pos);

            int max_l = static_cast<int>(std::min<size_t>(258, len - pos));
            int chain = 0;
            while (cand >= 0 && (pos - static_cast<size_t>(cand)) <= 32768 && chain < 24) {
                ++chain;
                size_t cp = static_cast<size_t>(cand);
                if (src[cp + best_len] == src[pos + best_len] && src[cp] == src[pos]) {
                    int l = 0;
                    while (l < max_l && src[cp + l] == src[pos + l]) ++l;
                    if (l >= 3 && l > best_len) {
                        best_len = l;
                        best_dist = static_cast<int>(pos - cp);
                        if (l == max_l) break;
                    }
                }
                int32_t nxt = prev[cp & WIN_MASK];
                if (nxt >= cand) break;
                cand = nxt;
            }
        }
        if (best_len >= 3) {
            int lcode = 28;
            for (int i = 0; i < 28; ++i) {
                if (best_len < LEN_BASE[i + 1]) {
                    lcode = i;
                    break;
                }
            }
            bw.put_fixed_litlen(257 + lcode);
            if (LEN_EXTRA[lcode] > 0) {
                bw.put_lsb(static_cast<uint32_t>(best_len - LEN_BASE[lcode]), LEN_EXTRA[lcode]);
            }
            int dcode = 29;
            for (int i = 0; i < 29; ++i) {
                if (best_dist < DIST_BASE[i + 1]) {
                    dcode = i;
                    break;
                }
            }
            bw.put_huff_msb(static_cast<uint32_t>(dcode), 5);
            if (DIST_EXTRA[dcode] > 0) {
                bw.put_lsb(static_cast<uint32_t>(best_dist - DIST_BASE[dcode]), DIST_EXTRA[dcode]);
            }
            for (int k = 1; k < best_len; ++k) {
                size_t pk = pos + static_cast<size_t>(k);
                if (pk + 3 <= len) {
                    uint32_t hk = hash3(pk);
                    prev[pk & WIN_MASK] = head[hk];
                    head[hk] = static_cast<int32_t>(pk);
                }
            }
            pos += static_cast<size_t>(best_len);
        } else {
            bw.put_fixed_litlen(src[pos]);
            ++pos;
        }
    }

    // End-of-block symbol 256
    bw.put_fixed_litlen(256);
    bw.flush_byte_align();

    // RFC 1952 8-byte trailer: CRC32 (LE) + ISIZE mod 2^32 (LE)
    uint32_t crc = crc32_ieee(src, len);
    uint32_t isz = static_cast<uint32_t>(len & 0xFFFFFFFFu);
    for (int i = 0; i < 4; ++i) bw.out.push_back(static_cast<uint8_t>((crc >> (8 * i)) & 0xFFu));
    for (int i = 0; i < 4; ++i) bw.out.push_back(static_cast<uint8_t>((isz >> (8 * i)) & 0xFFu));
    return bw.out;
}

void write_gzip_file(std::string_view p, std::string_view text) {
    auto gz = gzip_compress(text.data(), text.size());
    write_file_bytes(p, gz.data(), gz.size());
}

unsigned int hardware_threads() noexcept {
    SYSTEM_INFO si{};
    GetSystemInfo(&si);
    return si.dwNumberOfProcessors > 0 ? si.dwNumberOfProcessors : 4u;
}

// ============================================================================
// FlowBudget
// ============================================================================
static const FlowBudget* g_active_budget = nullptr;

FlowBudget::FlowBudget(int total_seconds, double prior_seconds)
    : active_(true), total_seconds_(total_seconds), prior_seconds_(prior_seconds), start_time_(perf_counter()) {
    if (total_seconds <= 0 || total_seconds > 3600) {
        throw std::invalid_argument("Total flow budget must be an integer within one hour");
    }
    if (!std::isfinite(prior_seconds) || prior_seconds < 0.0) {
        throw std::invalid_argument("Retained stage cost must be finite and nonnegative");
    }
}

double FlowBudget::used() const noexcept {
    if (!active_) return 0.0;
    return prior_seconds_ + (perf_counter() - start_time_);
}

double FlowBudget::remaining() const {
    if (!active_) return 3600.0;
    double v = static_cast<double>(total_seconds_) - used();
    if (v <= 0.0) {
        throw TimeoutError("Total synthesis and P&R exceeded the one-hour flow budget");
    }
    return v;
}

double FlowBudget::timeout(double requested, double reserve_sec) const {
    if (!active_) return requested;
    double v = std::min(requested, remaining() - reserve_sec);
    if (v <= 0.0) {
        throw TimeoutError("Insufficient total flow budget to start the next operation");
    }
    return v;
}

void set_active_budget(const FlowBudget* b) noexcept {
    g_active_budget = b;
}

double clipped_timeout(double requested) {
    return g_active_budget ? g_active_budget->timeout(requested) : requested;
}

// ============================================================================
// Win32 Subprocess Runner
// ============================================================================
namespace {

std::wstring quote_win32_arg(std::wstring_view arg) {
    if (!arg.empty() && arg.find_first_of(L" \t\n\v\"") == std::wstring_view::npos) {
        return std::wstring(arg);
    }
    std::wstring res = L"\"";
    for (size_t i = 0; i < arg.size();) {
        size_t bs = 0;
        while (i < arg.size() && arg[i] == L'\\') {
            ++bs;
            ++i;
        }
        if (i == arg.size()) {
            res.append(bs * 2, L'\\');
            break;
        } else if (arg[i] == L'"') {
            res.append(bs * 2 + 1, L'\\');
            res.push_back(L'"');
            ++i;
        } else {
            res.append(bs, L'\\');
            res.push_back(arg[i]);
            ++i;
        }
    }
    res.push_back(L'"');
    return res;
}

std::vector<wchar_t> build_unicode_env_block(const std::vector<std::pair<std::string, std::string>>& extra_env) {
    struct CiLess {
        bool operator()(const std::wstring& a, const std::wstring& b) const noexcept {
            return _wcsicmp(a.c_str(), b.c_str()) < 0;
        }
    };
    std::map<std::wstring, std::wstring, CiLess> env_map;
    LPWCH raw = GetEnvironmentStringsW();
    if (raw) {
        const wchar_t* p = raw;
        while (*p) {
            std::wstring entry(p);
            p += entry.size() + 1;
            size_t eq = entry.find(L'=', 1);
            if (eq != std::wstring::npos) {
                env_map[entry.substr(0, eq)] = entry.substr(eq + 1);
            }
        }
        FreeEnvironmentStringsW(raw);
    }
    for (const auto& [k, v] : extra_env) {
        env_map[utf8_to_wide(k)] = utf8_to_wide(v);
    }
    std::vector<wchar_t> blk;
    for (const auto& [k, v] : env_map) {
        blk.insert(blk.end(), k.begin(), k.end());
        blk.push_back(L'=');
        blk.insert(blk.end(), v.begin(), v.end());
        blk.push_back(L'\0');
    }
    blk.push_back(L'\0');
    return blk;
}

} // namespace

ProcResult run_process_captured(
    const std::vector<std::string>& args,
    std::string_view cwd,
    const std::vector<std::pair<std::string, std::string>>& extra_env,
    double timeout_sec
) {
    if (args.empty()) throw std::invalid_argument("Empty process args");
    std::wstring cmdline;
    for (size_t i = 0; i < args.size(); ++i) {
        if (i > 0) cmdline.push_back(L' ');
        cmdline += quote_win32_arg(utf8_to_wide(args[i]));
    }
    std::wstring wcwd = cwd.empty() ? L"" : utf8_to_wide(resolve_path(cwd));
    std::vector<wchar_t> env_blk = build_unicode_env_block(extra_env);

    SECURITY_ATTRIBUTES sa{};
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;

    HANDLE hRead = nullptr, hWrite = nullptr;
    if (!CreatePipe(&hRead, &hWrite, &sa, 1 << 20)) {
        throw std::runtime_error("CreatePipe failed");
    }
    SetHandleInformation(hRead, HANDLE_FLAG_INHERIT, 0);

    HANDLE hNul = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, &sa, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = hNul;
    si.hStdOutput = hWrite;
    si.hStdError = hWrite;

    PROCESS_INFORMATION pi{};
    double t0 = perf_counter();
    BOOL ok = CreateProcessW(
        utf8_to_wide(args[0]).c_str(),
        cmdline.data(),
        nullptr,
        nullptr,
        TRUE,
        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
        env_blk.data(),
        wcwd.empty() ? nullptr : wcwd.c_str(),
        &si,
        &pi
    );
    CloseHandle(hWrite);
    if (hNul != INVALID_HANDLE_VALUE) CloseHandle(hNul);
    if (!ok) {
        CloseHandle(hRead);
        throw std::runtime_error("CreateProcessW failed for " + args[0] + " (err=" + std::to_string(GetLastError()) + ")");
    }

    std::string captured;
    std::thread reader([&]() {
        char buf[65536];
        for (;;) {
            DWORD got = 0;
            if (!ReadFile(hRead, buf, sizeof(buf), &got, nullptr) || got == 0) break;
            for (DWORD i = 0; i < got; ++i) {
                if (buf[i] == '\r') continue;
                captured.push_back(buf[i]);
            }
        }
    });

    DWORD wait_ms = (timeout_sec > 0.0 && std::isfinite(timeout_sec))
        ? static_cast<DWORD>(std::min<double>(timeout_sec * 1000.0, 36000000.0))
        : INFINITE;
    DWORD wres = WaitForSingleObject(pi.hProcess, wait_ms);
    ProcResult res;
    if (wres == WAIT_TIMEOUT) {
        res.timed_out = true;
        TerminateProcess(pi.hProcess, 124);
        WaitForSingleObject(pi.hProcess, 5000);
    }
    DWORD exit_code = 1;
    GetExitCodeProcess(pi.hProcess, &exit_code);
    res.returncode = static_cast<int>(exit_code);

    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    reader.join();
    CloseHandle(hRead);

    res.wall_seconds = perf_counter() - t0;
    res.output = std::move(captured);
    return res;
}

ProcResult run_process_to_log(
    const std::vector<std::string>& args,
    std::string_view cwd,
    std::string_view log_file,
    const std::vector<std::pair<std::string, std::string>>& extra_env,
    double timeout_sec
) {
    ProcResult r = run_process_captured(args, cwd, extra_env, timeout_sec);
    write_file_text(log_file, r.output);
    return r;
}

// ============================================================================
// RuntimePaths, AsciiDirAlias & GowinHostStaging
// ============================================================================
static RuntimePaths g_rt_paths;

const RuntimePaths& runtime_paths() { return g_rt_paths; }
void init_runtime_paths(const RuntimePaths& rp) { g_rt_paths = rp; }

bool needs_ascii_alias(std::string_view p) noexcept {
    for (unsigned char c : p) {
        if (c <= 0x20 || c >= 0x7F) return true;
    }
    return false;
}

namespace {

std::atomic<uint64_t> g_alias_seq{1};

std::string choose_ascii_temp_base() {
    for (const char* ev : {"TEMP", "TMP"}) {
        std::string t = get_env(ev);
        if (!t.empty()) {
            std::string rt = resolve_path(t);
            if (!needs_ascii_alias(rt) && dir_exists(rt)) return rt;
        }
    }
    std::string sys_drv = get_env("SystemDrive");
    if (sys_drv.empty() || needs_ascii_alias(sys_drv)) sys_drv = "C:";
    for (const char* sub : {"Users\\Public\\AppData\\Local\\Temp", "Users\\Public\\NextGowinTemp", "ProgramData\\NextGowinTemp"}) {
        std::string cand = sys_drv + "\\" + sub;
        try {
            make_dirs(cand, true);
            if (dir_exists(cand) && !needs_ascii_alias(cand)) return cand;
        } catch (...) {
        }
    }
    throw std::runtime_error("Cannot locate a writable ASCII space-free temporary directory for Unicode/spaced path aliasing");
}

void copy_tree_overwrite(const std::string& src_dir, const std::string& dst_dir) {
    make_dirs(dst_dir, true);
    for (const auto& f : rglob_files(src_dir)) {
        std::string rel = relative_posix_path(f, src_dir);
        copy_file_overwrite(f, path_join(dst_dir, rel));
    }
}

} // namespace

AsciiDirAlias::AsciiDirAlias(std::string_view target_dir, bool mirror_back_if_copied)
    : mirror_back_(mirror_back_if_copied) {
    real_dir_ = resolve_path(target_dir);
    if (real_dir_.empty() || !needs_ascii_alias(real_dir_)) {
        alias_dir_ = real_dir_;
        return;
    }

    // 1. Check if Windows 8.3 short path is already pure ASCII & space-free
    std::wstring wreal = win_long_path(real_dir_);
    DWORD sn = GetShortPathNameW(wreal.c_str(), nullptr, 0);
    if (sn > 0) {
        std::wstring sbuf(sn, L'\0');
        DWORD sgot = GetShortPathNameW(wreal.c_str(), sbuf.data(), sn);
        if (sgot > 0 && sgot < sn) {
            sbuf.resize(sgot);
            if (sbuf.rfind(L"\\\\?\\", 0) == 0) sbuf.erase(0, 4);
            std::string short_u8 = wide_to_utf8(sbuf);
            if (!short_u8.empty() && !needs_ascii_alias(short_u8) && dir_exists(short_u8)) {
                alias_dir_ = short_u8;
                return;
            }
        }
    }

    // 2. Create NTFS directory junction in an ASCII space-free workspace directory
    std::string base = choose_ascii_temp_base();
    uint64_t seq = g_alias_seq.fetch_add(1);
    DWORD pid = GetCurrentProcessId();
    alias_dir_ = path_join(base, "ng_a_" + std::to_string(pid) + "_" + std::to_string(seq));
    if (path_exists(alias_dir_)) remove_all(alias_dir_);

    std::string sys_root = get_env("SystemRoot");
    if (sys_root.empty()) sys_root = "C:\\Windows";
    std::string cmd_exe = path_join(sys_root, "System32\\cmd.exe");
    auto pr = run_process_captured({cmd_exe, "/d", "/c", "mklink", "/J", alias_dir_, real_dir_}, "", {}, 15.0);
    if (pr.returncode == 0 && dir_exists(alias_dir_)) {
        aliased_ = true;
        is_junction_ = true;
        return;
    }

    // 3. Fallback for non-NTFS filesystems (e.g. FAT32/exFAT USB drives): copy staging + mirror back
    copy_tree_overwrite(real_dir_, alias_dir_);
    aliased_ = true;
    is_junction_ = false;
}

void AsciiDirAlias::sync_back() {
    if (aliased_ && !is_junction_ && mirror_back_ && dir_exists(alias_dir_)) {
        copy_tree_overwrite(alias_dir_, real_dir_);
    }
}

void AsciiDirAlias::cleanup() noexcept {
    if (!aliased_ || alias_dir_.empty()) return;
    try {
        sync_back();
    } catch (...) {
    }
    try {
        if (is_junction_) {
            std::wstring wa = win_long_path(alias_dir_);
            RemoveDirectoryW(wa.c_str());
        } else {
            remove_all(alias_dir_);
        }
    } catch (...) {
    }
    aliased_ = false;
}

AsciiDirAlias::~AsciiDirAlias() {
    cleanup();
}

GowinHostStaging::GowinHostStaging(std::string_view work_parent, std::string_view sdk_ide_dir) {
    std::string sdk = resolve_path(sdk_ide_dir);
    host_root_ = path_join(resolve_path(work_parent), "_ng_gowin_host");
    bin_dir_ = path_join(host_root_, "bin");
    std::string sys_root = get_env("SystemRoot");
    if (sys_root.empty()) sys_root = "C:\\Windows";
    cmd_exe_ = path_join(sys_root, "System32\\cmd.exe");

    cleanup();
    make_dirs(bin_dir_, true);
    host_exe_ = path_join(bin_dir_, "nextgowin_host.exe");
    hardlink_or_copy(runtime_paths().nextgowin_exe, host_exe_);

    bool junction_ok = true;
    for (const char* rel : {"data", "share", "bin\\prim_syns", "bin\\vhdl_packages"}) {
        std::string target = path_join(sdk, rel);
        if (dir_exists(target)) {
            std::string link_p = path_join(host_root_, rel);
            make_dirs(parent_dir(link_p), true);
            auto pr = run_process_captured({cmd_exe_, "/d", "/c", "mklink", "/J", link_p, target}, "", {}, 15.0);
            if (pr.returncode == 0 && dir_exists(link_p)) {
                junctions_.push_back(link_p);
            } else {
                junction_ok = false;
                break;
            }
        }
    }

    if (!junction_ok) {
        for (auto it = junctions_.rbegin(); it != junctions_.rend(); ++it) {
            run_process_captured({cmd_exe_, "/d", "/c", "rmdir", *it}, "", {}, 10.0);
        }
        junctions_.clear();
        const char* subset_items[] = {
            "share\\device\\device_info.csv",
            "share\\device\\pn_voltage.csv",
            "share\\device\\GW5AST-138B",
            "share\\device\\GW5AST-138C",
            "share\\config",
            "data\\device\\GW5AST-138B",
            "data\\device\\GW5AST-138C",
            "data\\iotable",
            "data\\config",
            "data\\ipcores",
            "data\\hardware_core",
            "bin\\prim_syns",
            "bin\\vhdl_packages",
        };
        for (const char* rel : subset_items) {
            std::string src_item = path_join(sdk, rel);
            if (file_exists(src_item)) {
                hardlink_or_copy(src_item, path_join(host_root_, rel));
            } else if (dir_exists(src_item)) {
                for (const auto& sf : rglob_files(src_item)) {
                    std::string rel_sf = relative_posix_path(sf, sdk);
                    hardlink_or_copy(sf, path_join(host_root_, rel_sf));
                }
            }
        }
    }
}

GowinHostStaging::~GowinHostStaging() {
    cleanup();
}

void GowinHostStaging::cleanup() noexcept {
    try {
        for (auto it = junctions_.rbegin(); it != junctions_.rend(); ++it) {
            if (path_exists(*it)) {
                run_process_captured({cmd_exe_, "/d", "/c", "rmdir", *it}, "", {}, 10.0);
            }
        }
        junctions_.clear();
        if (path_exists(host_root_)) {
            remove_all(host_root_);
        }
    } catch (...) {
    }
}

// ============================================================================
// String / Lexer / Verilog Helpers
// ============================================================================
std::string ltrim(std::string_view s) {
    size_t i = 0;
    while (i < s.size() && std::isspace(static_cast<unsigned char>(s[i]))) ++i;
    return std::string(s.substr(i));
}

std::string rtrim(std::string_view s) {
    size_t n = s.size();
    while (n > 0 && std::isspace(static_cast<unsigned char>(s[n - 1]))) --n;
    return std::string(s.substr(0, n));
}

std::string trim(std::string_view s) {
    size_t i = 0, n = s.size();
    while (i < n && std::isspace(static_cast<unsigned char>(s[i]))) ++i;
    while (n > i && std::isspace(static_cast<unsigned char>(s[n - 1]))) --n;
    return std::string(s.substr(i, n - i));
}

std::string to_lower_ascii(std::string_view s) {
    std::string r(s);
    for (char& c : r) {
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
    }
    return r;
}

std::string to_upper_ascii(std::string_view s) {
    std::string r(s);
    for (char& c : r) {
        if (c >= 'a' && c <= 'z') c = static_cast<char>(c - 'a' + 'A');
    }
    return r;
}

bool starts_with(std::string_view s, std::string_view prefix) noexcept {
    return s.size() >= prefix.size() && s.compare(0, prefix.size(), prefix) == 0;
}

bool ends_with(std::string_view s, std::string_view suffix) noexcept {
    return s.size() >= suffix.size() && s.compare(s.size() - suffix.size(), suffix.size(), suffix) == 0;
}

std::vector<std::string> split_lines(std::string_view s) {
    std::vector<std::string> out;
    size_t pos = 0;
    while (pos < s.size()) {
        size_t nl = s.find('\n', pos);
        if (nl == std::string_view::npos) {
            std::string_view ln = s.substr(pos);
            if (!ln.empty() && ln.back() == '\r') ln.remove_suffix(1);
            out.emplace_back(ln);
            break;
        }
        std::string_view ln = s.substr(pos, nl - pos);
        if (!ln.empty() && ln.back() == '\r') ln.remove_suffix(1);
        out.emplace_back(ln);
        pos = nl + 1;
    }
    return out;
}

std::vector<std::string> split_ws(std::string_view s) {
    std::vector<std::string> out;
    size_t i = 0, n = s.size();
    while (i < n) {
        while (i < n && std::isspace(static_cast<unsigned char>(s[i]))) ++i;
        if (i >= n) break;
        size_t j = i;
        while (j < n && !std::isspace(static_cast<unsigned char>(s[j]))) ++j;
        out.emplace_back(s.substr(i, j - i));
        i = j;
    }
    return out;
}

std::vector<std::string> split_char(std::string_view s, char sep) {
    std::vector<std::string> out;
    size_t pos = 0;
    for (;;) {
        size_t nxt = s.find(sep, pos);
        if (nxt == std::string_view::npos) {
            out.emplace_back(s.substr(pos));
            break;
        }
        out.emplace_back(s.substr(pos, nxt - pos));
        pos = nxt + 1;
    }
    return out;
}

std::string join_strings(const std::vector<std::string>& items, std::string_view sep) {
    std::string out;
    size_t tot = 0;
    for (const auto& x : items) tot += x.size() + sep.size();
    out.reserve(tot);
    for (size_t i = 0; i < items.size(); ++i) {
        if (i > 0) out.append(sep);
        out.append(items[i]);
    }
    return out;
}

bool fnmatch_case(std::string_view name, std::string_view pattern) noexcept {
    size_t ni = 0, pi = 0;
    size_t star_p = std::string_view::npos, star_n = 0;
    while (ni < name.size()) {
        if (pi < pattern.size() && (pattern[pi] == '?' || pattern[pi] == name[ni])) {
            ++ni;
            ++pi;
        } else if (pi < pattern.size() && pattern[pi] == '*') {
            star_p = pi++;
            star_n = ni;
        } else if (star_p != std::string_view::npos) {
            pi = star_p + 1;
            ni = ++star_n;
        } else {
            return false;
        }
    }
    while (pi < pattern.size() && pattern[pi] == '*') ++pi;
    return pi == pattern.size();
}

bool is_simple_ident(std::string_view tok) noexcept {
    if (tok.empty()) return false;
    auto c0 = static_cast<unsigned char>(tok[0]);
    if (!(std::isalpha(c0) || c0 == '_' || c0 == '$')) return false;
    for (size_t i = 1; i < tok.size(); ++i) {
        auto c = static_cast<unsigned char>(tok[i]);
        if (!(std::isalnum(c) || c == '_' || c == '$')) return false;
    }
    return true;
}

bool is_valid_verilog_ident(std::string_view tok) noexcept {
    if (tok.empty()) return false;
    if (tok[0] == '\\') {
        if (tok.size() < 2) return false;
        for (size_t i = 1; i < tok.size(); ++i) {
            if (std::isspace(static_cast<unsigned char>(tok[i]))) return false;
        }
        return true;
    }
    return is_simple_ident(tok);
}

std::string masked_verilog(std::string_view text, bool mask_strings) {
    std::string out(text);
    const size_t n = out.size();
    size_t i = 0;
    while (i < n) {
        char c = out[i];
        if (c == '"') {
            size_t start = i++;
            while (i < n) {
                if (out[i] == '\\' && i + 1 < n) {
                    i += 2;
                } else if (out[i] == '"') {
                    ++i;
                    break;
                } else {
                    ++i;
                }
            }
            if (mask_strings) {
                for (size_t k = start; k < i; ++k) {
                    if (out[k] != '\r' && out[k] != '\n') out[k] = ' ';
                }
            }
        } else if (c == '/' && i + 1 < n && out[i + 1] == '/') {
            while (i < n && out[i] != '\r' && out[i] != '\n') {
                out[i++] = ' ';
            }
        } else if (c == '/' && i + 1 < n && out[i + 1] == '*') {
            out[i++] = ' ';
            out[i++] = ' ';
            while (i < n) {
                if (out[i] == '*' && i + 1 < n && out[i + 1] == '/') {
                    out[i++] = ' ';
                    out[i++] = ' ';
                    break;
                }
                if (out[i] != '\r' && out[i] != '\n') out[i] = ' ';
                ++i;
            }
        } else {
            ++i;
        }
    }
    return out;
}

static bool is_word_char(char ch) noexcept {
    auto c = static_cast<unsigned char>(ch);
    return std::isalnum(c) || c == '_' || c == '$';
}

std::vector<UnitSpan> verilog_units(std::string_view text) {
    std::string clean = masked_verilog(text, false);
    if (clean.size() >= 3 &&
        static_cast<unsigned char>(clean[0]) == 0xEF &&
        static_cast<unsigned char>(clean[1]) == 0xBB &&
        static_cast<unsigned char>(clean[2]) == 0xBF) {
        clean[0] = ' '; clean[1] = ' '; clean[2] = ' ';
    }
    std::vector<UnitSpan> res;
    std::unordered_map<std::string, size_t> seen;
    const size_t n = clean.size();
    size_t pos = 0;
    while (pos < n) {
        size_t line_end = clean.find('\n', pos);
        size_t next_pos = (line_end == std::string::npos) ? n : (line_end + 1);
        size_t kw = pos;
        while (kw < line_end && (clean[kw] == ' ' || clean[kw] == '\t')) ++kw;
        if (kw + 7 <= n && clean.compare(kw, 6, "module") == 0 && std::isspace(static_cast<unsigned char>(clean[kw + 6]))) {
            size_t i = kw + 6;
            while (i < n && std::isspace(static_cast<unsigned char>(clean[i]))) ++i;
            size_t id_start = i;
            if (i < n && clean[i] == '\\') {
                ++i;
                while (i < n && !std::isspace(static_cast<unsigned char>(clean[i]))) ++i;
            } else {
                while (i < n && is_word_char(clean[i])) ++i;
            }
            std::string mname = clean.substr(id_start, i - id_start);
            if (!mname.empty() && is_valid_verilog_ident(mname)) {
                while (i < n && std::isspace(static_cast<unsigned char>(clean[i]))) ++i;
                // Search for word-bounded `endmodule` from `i`
                size_t search_p = i;
                size_t end_mod = std::string::npos;
                while (search_p < n) {
                    size_t hit = clean.find("endmodule", search_p);
                    if (hit == std::string::npos) break;
                    bool left_ok = (hit == 0) || !is_word_char(clean[hit - 1]);
                    bool right_ok = (hit + 9 == n) || !is_word_char(clean[hit + 9]);
                    if (left_ok && right_ok) {
                        end_mod = hit + 9;
                        break;
                    }
                    search_p = hit + 9;
                }
                if (end_mod == std::string::npos || seen.count(mname)) {
                    throw std::runtime_error("Missing endmodule or duplicate definition: " + mname);
                }
                seen[mname] = res.size();
                res.push_back(UnitSpan{mname, "module", kw, end_mod});
            }
        }
        pos = next_pos;
    }
    if (res.empty()) {
        throw std::runtime_error("No exported module definitions");
    }
    return res;
}

std::vector<UnitSpan> primitive_model_units(std::string_view text) {
    std::string clean = masked_verilog(text, true);
    std::vector<UnitSpan> res;
    std::unordered_map<std::string, size_t> seen;
    const size_t n = clean.size();
    size_t pos = 0;
    while (pos < n) {
        size_t line_end = clean.find('\n', pos);
        size_t next_pos = (line_end == std::string::npos) ? n : (line_end + 1);
        std::string kind;
        if (pos + 7 <= n && clean.compare(pos, 6, "module") == 0 && std::isspace(static_cast<unsigned char>(clean[pos + 6]))) {
            kind = "module";
        } else if (pos + 10 <= n && clean.compare(pos, 9, "primitive") == 0 && std::isspace(static_cast<unsigned char>(clean[pos + 9]))) {
            kind = "primitive";
        }
        if (!kind.empty()) {
            size_t i = pos + kind.size();
            while (i < n && std::isspace(static_cast<unsigned char>(clean[i]))) ++i;
            size_t id_start = i;
            while (i < n && (std::isalnum(static_cast<unsigned char>(clean[i])) || clean[i] == '_')) ++i;
            std::string uname = clean.substr(id_start, i - id_start);
            if (!uname.empty()) {
                std::string end_kw = "end" + kind;
                size_t search_p = i;
                size_t stop_pos = std::string::npos;
                while (search_p < n) {
                    size_t hit = clean.find(end_kw, search_p);
                    if (hit == std::string::npos) break;
                    bool at_bol = (hit == 0 || clean[hit - 1] == '\n');
                    bool right_ok = (hit + end_kw.size() == n) || !is_word_char(clean[hit + end_kw.size()]);
                    if (at_bol && right_ok) {
                        size_t eol = hit + end_kw.size();
                        while (eol < n && clean[eol] != '\r' && clean[eol] != '\n') ++eol;
                        stop_pos = eol;
                        break;
                    }
                    search_p = hit + end_kw.size();
                }
                if (stop_pos == std::string::npos || seen.count(uname)) {
                    throw std::runtime_error("Missing end/duplicate source unit: " + uname);
                }
                seen[uname] = res.size();
                res.push_back(UnitSpan{uname, kind, pos, stop_pos});
            }
        }
        pos = next_pos;
    }
    return res;
}

std::vector<std::string> tokenize_verilog(std::string_view s) {
    // Matches TOKEN = r'\\[^\s]+|"(?:\\.|[^"\\])*"|\d+\s*\x27[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|[a-zA-Z_$][a-zA-Z0-9_$]*|\d+|[^\s]'
    std::vector<std::string> toks;
    const size_t n = s.size();
    size_t i = 0;
    while (i < n) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        if (std::isspace(c)) {
            ++i;
            continue;
        }
        if (c == '\\') {
            size_t j = i + 1;
            while (j < n && !std::isspace(static_cast<unsigned char>(s[j]))) ++j;
            if (j > i + 1) {
                toks.emplace_back(s.substr(i, j - i));
                i = j;
                continue;
            }
            toks.emplace_back("\\");
            ++i;
            continue;
        }
        if (c == '"') {
            size_t j = i + 1;
            while (j < n) {
                if (s[j] == '\\' && j + 1 < n) j += 2;
                else if (s[j] == '"') { ++j; break; }
                else ++j;
            }
            toks.emplace_back(s.substr(i, j - i));
            i = j;
            continue;
        }
        if (std::isdigit(c)) {
            size_t j = i;
            while (j < n && std::isdigit(static_cast<unsigned char>(s[j]))) ++j;
            size_t k = j;
            while (k < n && std::isspace(static_cast<unsigned char>(s[k]))) ++k;
            if (k < n && s[k] == '\'') {
                size_t m = k + 1;
                if (m < n && (s[m] == 's' || s[m] == 'S')) ++m;
                if (m < n && std::strchr("bBoOdDhH", s[m]) != nullptr) {
                    ++m;
                    size_t d0 = m;
                    while (m < n && (std::isxdigit(static_cast<unsigned char>(s[m])) || std::strchr("_xXzZ?", s[m]) != nullptr)) {
                        ++m;
                    }
                    if (m > d0) {
                        toks.emplace_back(s.substr(i, m - i));
                        i = m;
                        continue;
                    }
                }
            }
            toks.emplace_back(s.substr(i, j - i));
            i = j;
            continue;
        }
        if (std::isalpha(c) || c == '_' || c == '$') {
            size_t j = i + 1;
            while (j < n && is_word_char(s[j])) ++j;
            toks.emplace_back(s.substr(i, j - i));
            i = j;
            continue;
        }
        toks.emplace_back(1, static_cast<char>(c));
        ++i;
    }
    return toks;
}

std::vector<std::pair<std::string, std::vector<std::string>>> parse_verilog_connections(const std::vector<std::string>& words) {
    std::vector<std::pair<std::string, std::vector<std::string>>> pins;
    std::unordered_map<std::string, size_t> seen;
    size_t i = 0;
    while (i < words.size()) {
        if (words[i] == ",") {
            ++i;
            continue;
        }
        if (i + 3 >= words.size() || words[i] != "." || words[i + 2] != "(") {
            throw std::runtime_error("Expected named structural cell pins");
        }
        std::string pname = words[i + 1];
        i += 3;
        size_t begin = i;
        int depth = 1;
        while (i < words.size() && depth > 0) {
            if (words[i] == "(") ++depth;
            else if (words[i] == ")") --depth;
            if (depth > 0) ++i;
        }
        if (depth != 0 || seen.count(pname)) {
            throw std::runtime_error("Unbalanced/duplicate cell pin: " + pname);
        }
        seen[pname] = pins.size();
        pins.emplace_back(pname, std::vector<std::string>(words.begin() + begin, words.begin() + i));
        ++i;
    }
    return pins;
}

std::pair<int, uint64_t> parse_verilog_literal_u64(std::string_view token) {
    std::string t = trim(token);
    size_t q = t.find('\'');
    if (q == std::string::npos) {
        throw std::runtime_error("Only fully specified Verilog integer literals supported: " + t);
    }
    std::string wstr = rtrim(std::string_view(t).substr(0, q));
    if (wstr.empty()) throw std::runtime_error("Missing literal width: " + t);
    int width = std::stoi(wstr);
    size_t m = q + 1;
    if (m < t.size() && (t[m] == 's' || t[m] == 'S')) ++m;
    if (m >= t.size()) throw std::runtime_error("Malformed literal base: " + t);
    char base_ch = static_cast<char>(std::tolower(static_cast<unsigned char>(t[m++])));
    int base = (base_ch == 'b') ? 2 : (base_ch == 'o') ? 8 : (base_ch == 'd') ? 10 : (base_ch == 'h') ? 16 : 0;
    if (base == 0 || m >= t.size()) throw std::runtime_error("Unsupported literal base: " + t);
    uint64_t val = 0;
    for (; m < t.size(); ++m) {
        char ch = t[m];
        if (ch == '_') continue;
        int d = -1;
        if (ch >= '0' && ch <= '9') d = ch - '0';
        else if (ch >= 'a' && ch <= 'f') d = 10 + (ch - 'a');
        else if (ch >= 'A' && ch <= 'F') d = 10 + (ch - 'A');
        if (d < 0 || d >= base) throw std::runtime_error("Invalid literal digit in " + t);
        val = val * static_cast<uint64_t>(base) + static_cast<uint64_t>(d);
    }
    if (width < 1 || (width < 64 && (val >> width) != 0)) {
        throw std::runtime_error("Literal value exceeds width: " + t);
    }
    return {width, val};
}

// Arbitrary-precision integer formatting for gowin_netlist_format.py:normalize() (handles up to 512-bit INIT_xx BRAM hex/bin params!)
namespace {

struct BigUint {
    std::vector<uint32_t> limbs; // little-endian base 2^32

    bool is_zero() const noexcept {
        for (uint32_t x : limbs) if (x != 0) return false;
        return true;
    }

    void trim_limbs() {
        while (!limbs.empty() && limbs.back() == 0) limbs.pop_back();
    }

    void mul_add(uint32_t mul, uint32_t add) {
        uint64_t carry = add;
        for (size_t i = 0; i < limbs.size(); ++i) {
            uint64_t cur = static_cast<uint64_t>(limbs[i]) * mul + carry;
            limbs[i] = static_cast<uint32_t>(cur & 0xFFFFFFFFu);
            carry = cur >> 32;
        }
        if (carry > 0) limbs.push_back(static_cast<uint32_t>(carry));
    }

    uint32_t div_rem(uint32_t div) {
        uint64_t rem = 0;
        for (size_t i = limbs.size(); i-- > 0;) {
            uint64_t cur = (rem << 32) | limbs[i];
            limbs[i] = static_cast<uint32_t>(cur / div);
            rem = cur % div;
        }
        trim_limbs();
        return static_cast<uint32_t>(rem);
    }

    int bit_length() const noexcept {
        for (size_t i = limbs.size(); i-- > 0;) {
            uint32_t v = limbs[i];
            if (v != 0) {
                int b = 0;
                while (v != 0) { ++b; v >>= 1; }
                return static_cast<int>(i * 32) + b;
            }
        }
        return 0;
    }

    int get_bit(int idx) const noexcept {
        size_t li = static_cast<size_t>(idx / 32);
        if (li >= limbs.size()) return 0;
        return static_cast<int>((limbs[li] >> (idx % 32)) & 1u);
    }

    static BigUint parse_verilog_int(std::string_view raw) {
        std::string s = trim(raw);
        BigUint u;
        bool all_digits = !s.empty();
        for (char c : s) if (!std::isdigit(static_cast<unsigned char>(c))) { all_digits = false; break; }
        if (all_digits) {
            for (char c : s) u.mul_add(10, static_cast<uint32_t>(c - '0'));
            u.trim_limbs();
            return u;
        }
        // Check zero-width repeat {0{1'b0}}
        std::string nospace;
        for (char c : s) if (!std::isspace(static_cast<unsigned char>(c))) nospace.push_back(c);
        if (nospace == "{0{1'b0}}" || nospace == "{0{1'B0}}") {
            return u;
        }
        size_t q = s.find('\'');
        if (q == std::string::npos) {
            throw std::runtime_error("Unsupported Verilog integer parameter: " + s);
        }
        std::string wstr = trim(std::string_view(s).substr(0, q));
        int width = std::stoi(wstr);
        size_t m = q + 1;
        if (m < s.size() && (s[m] == 's' || s[m] == 'S')) ++m;
        if (m >= s.size()) throw std::runtime_error("Malformed Verilog parameter literal: " + s);
        char bc = static_cast<char>(std::tolower(static_cast<unsigned char>(s[m++])));
        uint32_t base = (bc == 'b') ? 2u : (bc == 'o') ? 8u : (bc == 'd') ? 10u : (bc == 'h') ? 16u : 0u;
        if (base == 0 || m >= s.size()) throw std::runtime_error("Unsupported base in parameter: " + s);
        for (; m < s.size(); ++m) {
            char ch = s[m];
            if (ch == '_') continue;
            int d = -1;
            if (ch >= '0' && ch <= '9') d = ch - '0';
            else if (ch >= 'a' && ch <= 'f') d = 10 + (ch - 'a');
            else if (ch >= 'A' && ch <= 'F') d = 10 + (ch - 'A');
            if (d < 0 || static_cast<uint32_t>(d) >= base) {
                throw std::runtime_error("Only fully specified Verilog integer literals supported: " + s);
            }
            u.mul_add(base, static_cast<uint32_t>(d));
        }
        u.trim_limbs();
        if (width < 1 || u.bit_length() > width) {
            throw std::runtime_error("Literal value exceeds width: " + s);
        }
        return u;
    }

    std::string to_decimal() const {
        if (is_zero()) return "0";
        BigUint tmp = *this;
        std::string rev;
        while (!tmp.is_zero()) {
            uint32_t rem = tmp.div_rem(10u);
            rev.push_back(static_cast<char>('0' + rem));
        }
        std::reverse(rev.begin(), rev.end());
        return rev;
    }

    std::string to_octal() const {
        if (is_zero()) return "0";
        BigUint tmp = *this;
        std::string rev;
        while (!tmp.is_zero()) {
            uint32_t rem = tmp.div_rem(8u);
            rev.push_back(static_cast<char>('0' + rem));
        }
        std::reverse(rev.begin(), rev.end());
        return rev;
    }
};

} // namespace

std::string format_verilog_Parameter_bits(std::string_view raw_val, int target_width, char base_ch) {
    // Handle 4-state sized Verilog literals containing X/Z (e.g. PLL ICP_SEL=6'bxxxxxx, LPF_RES=3'bxxx)
    // without collapsing X/Z to 0, so Gowin FuseGen auto-calculation sentinels are preserved intact.
    {
        std::string s = trim(raw_val);
        size_t q = s.find('\'');
        bool has_xz = false;
        if (q != std::string::npos) {
            for (size_t i = q + 1; i < s.size(); ++i) {
                if (s[i] == 'x' || s[i] == 'X' || s[i] == 'z' || s[i] == 'Z') { has_xz = true; break; }
            }
        }
        if (has_xz) {
            if (target_width <= 0) {
                throw std::runtime_error("4-state Verilog literal unsupported for decimal parameter: " + s);
            }
            std::string wstr = trim(std::string_view(s).substr(0, q));
            int lit_w = std::stoi(wstr);
            size_t m = q + 1;
            if (m < s.size() && (s[m] == 's' || s[m] == 'S')) ++m;
            if (m >= s.size()) throw std::runtime_error("Malformed 4-state Verilog parameter literal: " + s);
            char bc = static_cast<char>(std::tolower(static_cast<unsigned char>(s[m++])));
            int bpd = (bc == 'b') ? 1 : (bc == 'o') ? 3 : (bc == 'h') ? 4 : 0;
            if (bpd == 0 || m >= s.size()) throw std::runtime_error("Unsupported 4-state base in parameter: " + s);
            std::string bits;
            for (; m < s.size(); ++m) {
                char ch = s[m];
                if (ch == '_') continue;
                if (ch == 'x' || ch == 'X') { bits.append(static_cast<size_t>(bpd), 'X'); continue; }
                if (ch == 'z' || ch == 'Z') { bits.append(static_cast<size_t>(bpd), 'Z'); continue; }
                int d = -1;
                if (ch >= '0' && ch <= '9') d = ch - '0';
                else if (ch >= 'a' && ch <= 'f') d = 10 + (ch - 'a');
                else if (ch >= 'A' && ch <= 'F') d = 10 + (ch - 'A');
                if (d < 0 || d >= (1 << bpd)) {
                    throw std::runtime_error("Invalid digit in 4-state Verilog parameter literal: " + s);
                }
                for (int bi = bpd - 1; bi >= 0; --bi) bits.push_back(static_cast<char>('0' + ((d >> bi) & 1)));
            }
            if (lit_w < 1) throw std::runtime_error("Invalid width in 4-state literal: " + s);
            // Adjust expanded bit-string to lit_w first (Verilog left-extends X/Z or 0)
            if (static_cast<int>(bits.size()) > lit_w) {
                size_t drop = bits.size() - static_cast<size_t>(lit_w);
                for (size_t i = 0; i < drop; ++i) {
                    if (bits[i] != '0' && bits[i] != bits[drop]) {
                        throw std::runtime_error("4-state literal exceeds declared width: " + s);
                    }
                }
                bits.erase(0, drop);
            } else if (static_cast<int>(bits.size()) < lit_w) {
                char pad = (!bits.empty() && (bits[0] == 'X' || bits[0] == 'Z')) ? bits[0] : '0';
                bits.insert(0, static_cast<size_t>(lit_w - static_cast<int>(bits.size())), pad);
            }
            if (lit_w > target_width) {
                throw std::runtime_error("Primitive 4-state parameter exceeds vendor width: " + s);
            }
            if (static_cast<int>(bits.size()) < target_width) {
                char pad = (!bits.empty() && (bits[0] == 'X' || bits[0] == 'Z')) ? bits[0] : '0';
                bits.insert(0, static_cast<size_t>(target_width - static_cast<int>(bits.size())), pad);
            }
            char tb = static_cast<char>(std::tolower(static_cast<unsigned char>(base_ch)));
            if (tb == 'h') {
                int ndig = (target_width + 3) / 4;
                std::string padded = bits;
                if (static_cast<int>(padded.size()) < ndig * 4) {
                    char pad = (!padded.empty() && (padded[0] == 'X' || padded[0] == 'Z')) ? padded[0] : '0';
                    padded.insert(0, static_cast<size_t>(ndig * 4 - static_cast<int>(padded.size())), pad);
                }
                std::string hex;
                bool hex_ok = true;
                static constexpr char HEX[] = "0123456789abcdef";
                for (int i = 0; i < ndig; ++i) {
                    std::string nib = padded.substr(static_cast<size_t>(i * 4), 4);
                    if (nib == "XXXX") hex.push_back('X');
                    else if (nib == "ZZZZ") hex.push_back('Z');
                    else if (nib.find('X') == std::string::npos && nib.find('Z') == std::string::npos) {
                        int v = ((nib[0] - '0') << 3) | ((nib[1] - '0') << 2) | ((nib[2] - '0') << 1) | (nib[3] - '0');
                        hex.push_back(HEX[v]);
                    } else {
                        hex_ok = false;
                        break;
                    }
                }
                if (hex_ok) return std::to_string(target_width) + "'h" + hex;
            }
            return std::to_string(target_width) + "'b" + bits;
        }
    }
    BigUint num = BigUint::parse_verilog_int(raw_val);
    if (target_width <= 0) {
        return num.to_decimal();
    }
    if (num.bit_length() > target_width) {
        throw std::runtime_error("Primitive parameter exceeds vendor width");
    }
    char b = static_cast<char>(std::tolower(static_cast<unsigned char>(base_ch)));
    std::string digits;
    if (b == 'b') {
        digits.resize(static_cast<size_t>(target_width));
        for (int i = 0; i < target_width; ++i) {
            digits[static_cast<size_t>(target_width - 1 - i)] = static_cast<char>('0' + num.get_bit(i));
        }
    } else if (b == 'h') {
        int ndig = (target_width + 3) / 4;
        digits.resize(static_cast<size_t>(ndig));
        static constexpr char HEX[] = "0123456789abcdef";
        for (int i = 0; i < ndig; ++i) {
            int nib = num.get_bit(4 * i)
                    | (num.get_bit(4 * i + 1) << 1)
                    | (num.get_bit(4 * i + 2) << 2)
                    | (num.get_bit(4 * i + 3) << 3);
            digits[static_cast<size_t>(ndig - 1 - i)] = HEX[nib];
        }
    } else if (b == 'd') {
        digits = num.to_decimal();
    } else {
        digits = num.to_octal();
    }
    return std::to_string(target_width) + "'" + std::string(1, b) + digits;
}

// ============================================================================
// SynAccelLib Dynamic Wrapper
// ============================================================================
SynAccelLib::SynAccelLib() {
    std::wstring dll_w = utf8_to_wide(runtime_paths().syn_accel_dll);
    mod_ = LoadLibraryExW(dll_w.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!mod_) {
        throw std::runtime_error("Failed to load syn_accel.dll: " + runtime_paths().syn_accel_dll);
    }
    fn_tier_  = reinterpret_cast<void*>(GetProcAddress(mod_, "accel_tier_id"));
    fn_perm_  = reinterpret_cast<void*>(GetProcAddress(mod_, "accel_permute_lut_batch"));
    fn_strip_ = reinterpret_cast<void*>(GetProcAddress(mod_, "accel_strip_line_comments"));
    fn_blif_  = reinterpret_cast<void*>(GetProcAddress(mod_, "accel_write_blif_file"));
    fn_gfni_  = reinterpret_cast<void*>(GetProcAddress(mod_, "accel_gfni_bitmatrix_u64"));
    fn_fs_    = reinterpret_cast<void*>(GetProcAddress(mod_, "accel_audit_fs_frames"));
    if (!fn_tier_ || !fn_perm_ || !fn_strip_ || !fn_blif_ || !fn_gfni_ || !fn_fs_) {
        throw std::runtime_error("Missing required exports in syn_accel.dll");
    }
}

SynAccelLib& SynAccelLib::instance() {
    static SynAccelLib inst;
    return inst;
}

int32_t SynAccelLib::tier_id() const {
    return reinterpret_cast<int32_t(__cdecl*)()>(fn_tier_)();
}

void SynAccelLib::permute_lut_batch(int32_t count, const uint16_t* inits, const uint8_t* ks, const uint8_t* orders4, uint16_t* out_inits) const {
    reinterpret_cast<void(__cdecl*)(int32_t, const uint16_t*, const uint8_t*, const uint8_t*, uint16_t*)>(fn_perm_)(
        count, inits, ks, orders4, out_inits
    );
}

std::string SynAccelLib::strip_line_comments(std::string_view src) const {
    if (src.empty()) return "";
    std::string dst(src.size(), '\0');
    int64_t n = reinterpret_cast<int64_t(__cdecl*)(const char*, int64_t, char*)>(fn_strip_)(
        src.data(), static_cast<int64_t>(src.size()), dst.data()
    );
    dst.resize(static_cast<size_t>(std::max<int64_t>(0, n)));
    return dst;
}

int32_t SynAccelLib::write_blif_file(
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
) const {
    return reinterpret_cast<int32_t(__cdecl*)(
        const char*, int32_t, const char*, const int32_t*, int32_t, const int32_t*,
        int32_t, const int32_t*, int32_t, const int32_t*, const int32_t*, const int32_t*, const uint64_t*, int32_t
    )>(fn_blif_)(
        path_utf8, num_names, names_blob, name_offsets, num_inputs, input_ids,
        num_outputs, output_ids, num_nodes, node_in_offsets, node_in_ids, node_out_ids, node_truths, emit_zero_const_cube
    );
}

void SynAccelLib::gfni_bitmatrix_u64(int32_t count, const uint64_t* src, uint64_t matrix64, uint8_t imm8, uint64_t* dst) const {
    reinterpret_cast<void(__cdecl*)(int32_t, const uint64_t*, uint64_t, uint8_t, uint64_t*)>(fn_gfni_)(
        count, src, matrix64, imm8, dst
    );
}

int32_t SynAccelLib::audit_fs_frames(
    const char* ascii_frames,
    int32_t frame_count,
    int32_t* bad_crc_indices,
    int32_t max_bad,
    int32_t* out_bad_crc_count,
    int32_t* bad_tail_indices,
    int32_t* out_bad_tail_count
) const {
    return reinterpret_cast<int32_t(__cdecl*)(const char*, int32_t, int32_t*, int32_t, int32_t*, int32_t*, int32_t*)>(fn_fs_)(
        ascii_frames, frame_count, bad_crc_indices, max_bad, out_bad_crc_count, bad_tail_indices, out_bad_tail_count
    );
}

} // namespace ng
