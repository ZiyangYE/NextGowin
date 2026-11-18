// Self-Contained Native Launcher for NextGowin (nextgowin.exe)
// Baseline x86-64 + SSE2/SSE3 only (all x86 CPUs released after 2007).
// Compiled with /O2 /MT /d2Qvec-sse2only /utf-8 — imports ONLY inbox KERNEL32.dll and USER32.dll.
// Zero Python runtime, zero embedded interpreter, zero .pyd modules, zero bundled Gowin binaries.

#define WIN32_LEAN_AND_MEAN
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cwchar>
#include <string>
#include <string_view>
#include <vector>

#include "cpu_dispatch.hpp"

#pragma comment(lib, "user32.lib")

namespace {

constexpr const char* EXPECTED_GOWIN_DISPLAY = "Gowin V1.9.12.04 x64";
constexpr const char* EXPECTED_GWSYN_SHA     = "254eac1ed05c7d1168889473dcc85203032fa4cd641c83bd5bbe4ea61a55b6b0";
constexpr const char* EXPECTED_GOWIN_SHA     = "bc9472a5a22cf4a94e39e07439f261f3354c668f92ef9abc735e6678bea53b7b";
constexpr const char* EXPECTED_PRIM_SHA      = "7109c2568188627465d218bc099cf72f12e90807c21d4d862d2335b499043544";
constexpr const char* EXPECTED_DAT_SHA       = "19e3ec885f359ec571c09ed5d3face1b0c10971b9e0f569fdf703911993c9d33";
constexpr const char* EXPECTED_FSE_SHA       = "19f859e61df5b8da361330d42d1250bd31d03bcfa2912c3ccfbf983cc4329030";
constexpr const char* EXPECTED_TM_SHA        = "a3558f35685ebc58eefce834e82059ce651b9e2a6cbca2d6668094153ca0707f";
constexpr const char* EXPECTED_138B_DAT_SHA  = "481f2d3a5e373bac0779948b6b2f8eec43a0a2dd62b058a2cce4d7cb4e275586";
constexpr const char* EXPECTED_138B_FSE_SHA  = "495da9737f17f5545978cc4b68899826ff0806073b1e26251502614ce21a74ae";
constexpr const char* EXPECTED_138B_TM_SHA   = "fffb1704a2d262cf4d7d1db21bb7be0419d573242a0bd606132858db5a18a44e";

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

std::wstring win_long_w(const std::wstring& p) {
    std::wstring w = p;
    for (auto& ch : w) if (ch == L'/') ch = L'\\';
    if (w.size() >= 240 && w.rfind(L"\\\\?\\", 0) != 0) {
        DWORD need = GetFullPathNameW(w.c_str(), 0, nullptr, nullptr);
        if (need > 0) {
            std::wstring abs_w(need, L'\0');
            DWORD got = GetFullPathNameW(w.c_str(), need, abs_w.data(), nullptr);
            if (got > 0 && got < need) {
                abs_w.resize(got);
                if (abs_w.rfind(L"\\\\", 0) == 0) return L"\\\\?\\UNC\\" + abs_w.substr(2);
                return L"\\\\?\\" + abs_w;
            }
        }
    }
    return w;
}

bool dir_exists(const std::wstring& p) {
    if (p.empty()) return false;
    DWORD a = GetFileAttributesW(win_long_w(p).c_str());
    return (a != INVALID_FILE_ATTRIBUTES) && ((a & FILE_ATTRIBUTE_DIRECTORY) != 0);
}

bool file_exists(const std::wstring& p) {
    if (p.empty()) return false;
    DWORD a = GetFileAttributesW(win_long_w(p).c_str());
    return (a != INVALID_FILE_ATTRIBUTES) && ((a & FILE_ATTRIBUTE_DIRECTORY) == 0);
}

std::wstring full_path(const std::wstring& p) {
    if (p.empty()) return L"";
    std::wstring w = p;
    for (auto& ch : w) if (ch == L'/') ch = L'\\';
    DWORD need = GetFullPathNameW(w.c_str(), 0, nullptr, nullptr);
    if (need == 0) return w;
    std::wstring buf(need, L'\0');
    DWORD got = GetFullPathNameW(w.c_str(), need, buf.data(), nullptr);
    if (got == 0 || got >= need) return w;
    buf.resize(got);
    while (buf.size() > 3 && (buf.back() == L'\\' || buf.back() == L'/')) buf.pop_back();
    return buf;
}

std::wstring parent_dir(const std::wstring& p) {
    size_t pos = p.find_last_of(L"\\/");
    if (pos == std::wstring::npos) return L".";
    if (pos == 2 && p.size() >= 2 && p[1] == L':') return p.substr(0, 3);
    return p.substr(0, pos);
}

bool is_abs_w(const std::wstring& p) {
    if (p.size() >= 2 && ((p[0] >= L'A' && p[0] <= L'Z') || (p[0] >= L'a' && p[0] <= L'z')) && p[1] == L':') return true;
    if (p.size() >= 2 && (p[0] == L'\\' || p[0] == L'/') && (p[1] == L'\\' || p[1] == L'/')) return true;
    if (!p.empty() && (p[0] == L'\\' || p[0] == L'/')) return true;
    return false;
}

void set_env_w(const wchar_t* name, const std::wstring& val) {
    SetEnvironmentVariableW(name, val.c_str());
}

std::wstring get_env_w(const wchar_t* name) {
    DWORD need = GetEnvironmentVariableW(name, nullptr, 0);
    if (need == 0) return L"";
    std::wstring buf(need, L'\0');
    DWORD got = GetEnvironmentVariableW(name, buf.data(), need);
    if (got == 0 || got >= need) return L"";
    buf.resize(got);
    return buf;
}

std::string trim_u8(std::string_view sv) {
    size_t b = 0, e = sv.size();
    while (b < e && std::isspace(static_cast<unsigned char>(sv[b]))) ++b;
    while (e > b && std::isspace(static_cast<unsigned char>(sv[e - 1]))) --e;
    return std::string(sv.substr(b, e - b));
}

// ============================================================================
// Minimal FIPS 180-4 SHA-256 (for Gowin SDK validation in launcher)
// ============================================================================
struct Sha256Ctx {
    uint32_t state[8] = {
        0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
        0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u
    };
    uint64_t bitlen = 0;
    uint8_t buf[64] = {0};
    size_t buflen = 0;

    static inline uint32_t rotr(uint32_t x, uint32_t n) { return (x >> n) | (x << (32 - n)); }

    void transform(const uint8_t block[64]) {
        static constexpr uint32_t K[64] = {
            0x428a2f98u,0x71374491u,0xb5c0fbcfu,0xe9b5dba5u,0x3956c25bu,0x59f111f1u,0x923f82a4u,0xab1c5ed5u,
            0xd807aa98u,0x12835b01u,0x243185beu,0x550c7dc3u,0x72be5d74u,0x80deb1feu,0x9bdc06a7u,0xc19bf174u,
            0xe49b69c1u,0xefbe4786u,0x0fc19dc6u,0x240ca1ccu,0x2de92c6fu,0x4a7484aau,0x5cb0a9dcu,0x76f988dau,
            0x983e5152u,0xa831c66du,0xb00327c8u,0xbf597fc7u,0xc6e00bf3u,0xd5a79147u,0x06ca6351u,0x14292967u,
            0x27b70a85u,0x2e1b2138u,0x4d2c6dfcu,0x53380d13u,0x650a7354u,0x766a0abbu,0x81c2c92eu,0x92722c85u,
            0xa2bfe8a1u,0xa81a664bu,0xc24b8b70u,0xc76c51a3u,0xd192e819u,0xd6990624u,0xf40e3585u,0x106aa070u,
            0x19a4c116u,0x1e376c08u,0x2748774cu,0x34b0bcb5u,0x391c0cb3u,0x4ed8aa4au,0x5b9cca4fu,0x682e6ff3u,
            0x748f82eeu,0x78a5636fu,0x84c87814u,0x8cc70208u,0x90befffau,0xa4506cebu,0xbef9a3f7u,0xc67178f2u
        };
        uint32_t w[64];
        for (int i = 0; i < 16; ++i) {
            w[i] = (static_cast<uint32_t>(block[i * 4 + 0]) << 24) |
                   (static_cast<uint32_t>(block[i * 4 + 1]) << 16) |
                   (static_cast<uint32_t>(block[i * 4 + 2]) << 8)  |
                   (static_cast<uint32_t>(block[i * 4 + 3]));
        }
        for (int i = 16; i < 64; ++i) {
            uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
            uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        uint32_t a = state[0], b = state[1], c = state[2], d = state[3];
        uint32_t e = state[4], f = state[5], g = state[6], h = state[7];
        for (int i = 0; i < 64; ++i) {
            uint32_t S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
            uint32_t ch = (e & f) ^ ((~e) & g);
            uint32_t t1 = h + S1 + ch + K[i] + w[i];
            uint32_t S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
            uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
            uint32_t t2 = S0 + maj;
            h = g; g = f; f = e; e = d + t1;
            d = c; c = b; b = a; a = t1 + t2;
        }
        state[0] += a; state[1] += b; state[2] += c; state[3] += d;
        state[4] += e; state[5] += f; state[6] += g; state[7] += h;
    }

    void update(const uint8_t* data, size_t len) {
        for (size_t i = 0; i < len; ++i) {
            buf[buflen++] = data[i];
            if (buflen == 64) {
                transform(buf);
                bitlen += 512;
                buflen = 0;
            }
        }
    }

    std::string finish_hex() {
        uint64_t total_bits = bitlen + static_cast<uint64_t>(buflen) * 8ULL;
        buf[buflen++] = 0x80u;
        if (buflen > 56) {
            while (buflen < 64) buf[buflen++] = 0;
            transform(buf);
            buflen = 0;
        }
        while (buflen < 56) buf[buflen++] = 0;
        for (int i = 0; i < 8; ++i) {
            buf[63 - i] = static_cast<uint8_t>((total_bits >> (i * 8)) & 0xFFu);
        }
        transform(buf);
        static constexpr char HEX[] = "0123456789abcdef";
        std::string out(64, '0');
        for (int i = 0; i < 8; ++i) {
            uint32_t v = state[i];
            for (int j = 0; j < 8; ++j) {
                out[static_cast<size_t>(i * 8 + j)] = HEX[(v >> (28 - j * 4)) & 0xFu];
            }
        }
        return out;
    }
};

bool read_file_bytes_w(const std::wstring& path, std::vector<uint8_t>& out) {
    out.clear();
    std::wstring wp = win_long_w(path);
    HANDLE h = CreateFileW(wp.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER sz{};
    if (!GetFileSizeEx(h, &sz)) { CloseHandle(h); return false; }
    out.resize(static_cast<size_t>(sz.QuadPart));
    size_t pos = 0;
    while (pos < out.size()) {
        DWORD chunk = static_cast<DWORD>(std::min<size_t>(out.size() - pos, 1u << 22));
        DWORD got = 0;
        if (!ReadFile(h, out.data() + pos, chunk, &got, nullptr)) { CloseHandle(h); return false; }
        if (got == 0) break;
        pos += got;
    }
    CloseHandle(h);
    out.resize(pos);
    return true;
}

std::string sha256_file_w(const std::wstring& path) {
    std::wstring wp = win_long_w(path);
    HANDLE h = CreateFileW(wp.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (h == INVALID_HANDLE_VALUE) return "";
    Sha256Ctx ctx;
    std::vector<uint8_t> buf(1u << 20);
    for (;;) {
        DWORD got = 0;
        if (!ReadFile(h, buf.data(), static_cast<DWORD>(buf.size()), &got, nullptr) || got == 0) break;
        ctx.update(buf.data(), got);
    }
    CloseHandle(h);
    return ctx.finish_hex();
}

// Extract embedded Gowin tool version banner (e.g. `V1.9.12.04 (64-bit)` -> `Gowin V1.9.12.04 x64`)
std::string extract_dll_gowin_banner(const std::wstring& dll_path) {
    std::vector<uint8_t> bytes;
    if (!read_file_bytes_w(dll_path, bytes) || bytes.size() < 16) return "";
    std::string best_with_arch;
    std::string best_plain;

    for (size_t i = 0; i + 8 < bytes.size(); ++i) {
        if (bytes[i] != 'V' || !std::isdigit(bytes[i + 1]) || bytes[i + 2] != '.') continue;
        // Require preceding byte to be NUL or non-printable boundary
        if (i > 0 && bytes[i - 1] != 0 && bytes[i - 1] != '\n' && bytes[i - 1] != ':') continue;
        size_t j = i;
        while (j < bytes.size() && j - i < 48 && bytes[j] >= 0x20 && bytes[j] < 0x7F) ++j;
        if (j >= bytes.size() || bytes[j] != 0) continue;
        std::string cand(reinterpret_cast<const char*>(bytes.data() + i), j - i);
        // Must start with V<d>.<d>.<d>
        size_t p = 1;
        int dots = 0;
        bool ok_ver = true;
        while (p < cand.size() && (std::isdigit(static_cast<unsigned char>(cand[p])) || cand[p] == '.')) {
            if (cand[p] == '.') ++dots;
            ++p;
        }
        if (dots < 2 || p < 6) ok_ver = false;
        if (!ok_ver) continue;

        if (cand.find("(64-bit)") != std::string::npos) {
            std::string ver_core = trim_u8(std::string_view(cand).substr(0, cand.find("(64-bit)")));
            best_with_arch = "Gowin " + ver_core + " x64";
            break;
        } else if (cand.find("(32-bit)") != std::string::npos) {
            std::string ver_core = trim_u8(std::string_view(cand).substr(0, cand.find("(32-bit)")));
            best_with_arch = "Gowin " + ver_core + " x86";
            break;
        } else if (p == cand.size() && best_plain.empty()) {
            best_plain = "Gowin " + cand;
        }
    }
    return !best_with_arch.empty() ? best_with_arch : best_plain;
}

std::string detect_gowin_version_info(const std::wstring& gowin_ide_dir, bool& out_exact_validated, std::string& out_mismatch_detail) {
    static const std::pair<const wchar_t*, const char*> PINS[] = {
        {L"bin\\gwsyn.dll", EXPECTED_GWSYN_SHA},
        {L"bin\\gowin.dll", EXPECTED_GOWIN_SHA},
        {L"simlib\\gw5a\\prim_sim.v", EXPECTED_PRIM_SHA},
        {L"share\\device\\GW5AST-138C\\GW5AST-138C.dat", EXPECTED_DAT_SHA},
        {L"share\\device\\GW5AST-138C\\GW5AST-138C.fse", EXPECTED_FSE_SHA},
        {L"share\\device\\GW5AST-138C\\GW5AST-138C.tm",  EXPECTED_TM_SHA},
        {L"share\\device\\GW5AST-138B\\GW5AST-138B.dat", EXPECTED_138B_DAT_SHA},
        {L"share\\device\\GW5AST-138B\\GW5AST-138B.fse", EXPECTED_138B_FSE_SHA},
        {L"share\\device\\GW5AST-138B\\GW5AST-138B.tm",  EXPECTED_138B_TM_SHA},
    };
    out_exact_validated = true;
    out_mismatch_detail.clear();
    for (const auto& [rel, exp] : PINS) {
        std::wstring fp = gowin_ide_dir + L"\\" + rel;
        std::string act = sha256_file_w(fp);
        if (act != exp) {
            out_exact_validated = false;
            if (out_mismatch_detail.empty()) {
                out_mismatch_detail = wide_to_utf8(rel);
            }
        }
    }

    std::string banner = extract_dll_gowin_banner(gowin_ide_dir + L"\\bin\\gwsyn.dll");
    if (banner.empty()) banner = extract_dll_gowin_banner(gowin_ide_dir + L"\\bin\\gowin.dll");

    if (banner.empty()) {
        // Fallback: inspect installation parent directory name (e.g. `Gowin_V1.9.11.03_x64`)
        std::wstring root_dir = parent_dir(gowin_ide_dir);
        size_t sl = root_dir.find_last_of(L"\\/");
        std::string folder = wide_to_utf8((sl == std::wstring::npos) ? root_dir : root_dir.substr(sl + 1));
        if (folder.rfind("Gowin_", 0) == 0) {
            for (char& c : folder) if (c == '_') c = ' ';
            banner = folder;
        }
    }

    if (out_exact_validated && banner == EXPECTED_GOWIN_DISPLAY) {
        return EXPECTED_GOWIN_DISPLAY;
    }
    if (banner.empty()) {
        return "Unrecognized Gowin version";
    }
    if (!out_exact_validated && banner == EXPECTED_GOWIN_DISPLAY) {
        return banner + " (modified or unverified build: " + out_mismatch_detail + ")";
    }
    return banner;
}

// ============================================================================
// Modal Win32 Warning Dialog with Literal `Continue` and `Cancel` Buttons
// ============================================================================
constexpr int IDC_WARN_TEXT = 100;

INT_PTR CALLBACK VersionWarnDlgProc(HWND hDlg, UINT msg, WPARAM wParam, LPARAM lParam) {
    switch (msg) {
        case WM_INITDIALOG: {
            MessageBeep(MB_ICONWARNING);
            SetDlgItemTextW(hDlg, IDC_WARN_TEXT, reinterpret_cast<const wchar_t*>(lParam));
            SetForegroundWindow(hDlg);
            return TRUE;
        }
        case WM_COMMAND: {
            WORD id = LOWORD(wParam);
            if (id == IDOK || id == IDCANCEL) {
                EndDialog(hDlg, static_cast<INT_PTR>(id));
                return TRUE;
            }
            break;
        }
        case WM_CLOSE:
            EndDialog(hDlg, IDCANCEL);
            return TRUE;
    }
    return FALSE;
}

void push_u16(std::vector<uint8_t>& b, uint16_t v) {
    b.push_back(static_cast<uint8_t>(v & 0xFFu));
    b.push_back(static_cast<uint8_t>((v >> 8) & 0xFFu));
}

void push_u32(std::vector<uint8_t>& b, uint32_t v) {
    push_u16(b, static_cast<uint16_t>(v & 0xFFFFu));
    push_u16(b, static_cast<uint16_t>((v >> 16) & 0xFFFFu));
}

void push_wstr(std::vector<uint8_t>& b, const wchar_t* s) {
    while (*s) push_u16(b, static_cast<uint16_t>(*s++));
    push_u16(b, 0);
}

void align_dword(std::vector<uint8_t>& b) {
    while ((b.size() & 3u) != 0u) b.push_back(0);
}

// Returns true if user chose Continue, false if user chose Cancel
bool show_gowin_version_warning_dialog(const std::string& detected_version, const std::wstring& gowin_dir) {
    std::string msg_u8 =
        "NextGowin detected a Gowin installation other than the officially validated release.\r\n\r\n"
        "Expected version:  " + std::string(EXPECTED_GOWIN_DISPLAY) + "\r\n"
        "Detected version:  " + detected_version + "\r\n"
        "Gowin directory:   " + wide_to_utf8(gowin_dir) + "\r\n\r\n"
        "Gowin V1.9.12.04 x64 is the only officially supported and validated version by default.\r\n"
        "Choose Continue to proceed anyway (the warning will be recorded in the log), or Cancel to abort.";

    // Optional automated test hook for headless CI verification of Continue/Cancel paths
    std::wstring test_action = get_env_w(L"NEXTGOWIN_VERSION_DIALOG_TEST_ACTION");
    if (!test_action.empty()) {
        std::string act = trim_u8(wide_to_utf8(test_action));
        for (char& c : act) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        std::fprintf(
            stderr,
            "[nextgowin] Gowin Version Warning Dialog (Expected='%s', Detected='%s', Action='%s')\n",
            EXPECTED_GOWIN_DISPLAY, detected_version.c_str(), act.c_str()
        );
        std::fflush(stderr);
        return (act == "continue");
    }

    std::wstring msg_w = utf8_to_wide(msg_u8);

    std::vector<uint8_t> tpl;
    tpl.reserve(512);
    // DLGTEMPLATE header
    uint32_t dlg_style = DS_MODALFRAME | DS_SETFONT | DS_CENTER | WS_POPUP | WS_CAPTION | WS_SYSMENU | WS_VISIBLE;
    push_u32(tpl, dlg_style);
    push_u32(tpl, WS_EX_TOPMOST);
    push_u16(tpl, 3);     // cdit = 3 controls (message + Continue + Cancel)
    push_u16(tpl, 0);     // x
    push_u16(tpl, 0);     // y
    push_u16(tpl, 320);   // cx (dialog units)
    push_u16(tpl, 152);   // cy (dialog units)
    push_u16(tpl, 0);     // menu
    push_u16(tpl, 0);     // windowClass
    push_wstr(tpl, L"NextGowin - Gowin Version Warning");
    push_u16(tpl, 9);     // point size
    push_wstr(tpl, L"Segoe UI");

    // Item 1: Static warning text
    align_dword(tpl);
    push_u32(tpl, WS_CHILD | WS_VISIBLE | SS_LEFT);
    push_u32(tpl, 0);
    push_u16(tpl, 12);    // x
    push_u16(tpl, 10);    // y
    push_u16(tpl, 296);   // cx
    push_u16(tpl, 108);   // cy
    push_u16(tpl, IDC_WARN_TEXT);
    push_u16(tpl, 0xFFFFu); push_u16(tpl, 0x0082u); // Static class
    push_wstr(tpl, L"");
    push_u16(tpl, 0);

    // Item 2: Continue button (IDOK)
    align_dword(tpl);
    push_u32(tpl, WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_DEFPUSHBUTTON);
    push_u32(tpl, 0);
    push_u16(tpl, 140);   // x
    push_u16(tpl, 124);   // y
    push_u16(tpl, 80);    // cx
    push_u16(tpl, 18);    // cy
    push_u16(tpl, IDOK);
    push_u16(tpl, 0xFFFFu); push_u16(tpl, 0x0080u); // Button class
    push_wstr(tpl, L"Continue");
    push_u16(tpl, 0);

    // Item 3: Cancel button (IDCANCEL)
    align_dword(tpl);
    push_u32(tpl, WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON);
    push_u32(tpl, 0);
    push_u16(tpl, 228);   // x
    push_u16(tpl, 124);   // y
    push_u16(tpl, 80);    // cx
    push_u16(tpl, 18);    // cy
    push_u16(tpl, IDCANCEL);
    push_u16(tpl, 0xFFFFu); push_u16(tpl, 0x0080u); // Button class
    push_wstr(tpl, L"Cancel");
    push_u16(tpl, 0);

    INT_PTR r = DialogBoxIndirectParamW(
        GetModuleHandleW(nullptr),
        reinterpret_cast<LPCDLGTEMPLATEW>(tpl.data()),
        nullptr,
        VersionWarnDlgProc,
        reinterpret_cast<LPARAM>(msg_w.c_str())
    );
    return (r == IDOK);
}

// Read single-line UTF-8 `gowin-path.txt` beside `nextgowin.exe`
bool read_gowin_path_txt(const std::wstring& exe_dir, std::wstring& out_gowin_path, std::string& out_err) {
    std::wstring cfg_file = exe_dir + L"\\gowin-path.txt";
    std::vector<uint8_t> bytes;
    if (!read_file_bytes_w(cfg_file, bytes)) {
        out_err = "Missing configuration file '" + wide_to_utf8(cfg_file) +
                  "'. Create gowin-path.txt beside nextgowin.exe containing the single-line path to your Gowin V1.9.12.04 x64 installation (for example: C:\\Gowin\\Gowin_V1.9.12.04_x64).";
        return false;
    }

    std::string raw(reinterpret_cast<const char*>(bytes.data()), bytes.size());
    if (raw.size() >= 3 && static_cast<uint8_t>(raw[0]) == 0xEF &&
        static_cast<uint8_t>(raw[1]) == 0xBB && static_cast<uint8_t>(raw[2]) == 0xBF) {
        raw.erase(0, 3);
    }

    std::vector<std::string> active_lines;
    size_t pos = 0;
    while (pos < raw.size()) {
        size_t nl = raw.find('\n', pos);
        std::string_view ln = (nl == std::string::npos)
            ? std::string_view(raw).substr(pos)
            : std::string_view(raw).substr(pos, nl - pos);
        std::string t = trim_u8(ln);
        if (!t.empty() && t[0] != '#') {
            active_lines.push_back(std::move(t));
        }
        if (nl == std::string::npos) break;
        pos = nl + 1;
    }

    if (active_lines.empty()) {
        out_err = "Configuration file '" + wide_to_utf8(cfg_file) +
                  "' is empty. Enter the single-line path to your Gowin V1.9.12.04 x64 installation.";
        return false;
    }
    if (active_lines.size() > 1) {
        out_err = "Configuration file '" + wide_to_utf8(cfg_file) +
                  "' must contain a single-line path to the Gowin installation directory (found " +
                  std::to_string(active_lines.size()) + " non-empty lines).";
        return false;
    }

    std::string line = active_lines[0];
    if (line.size() >= 2 && ((line.front() == '"' && line.back() == '"') || (line.front() == '\'' && line.back() == '\''))) {
        line = trim_u8(std::string_view(line).substr(1, line.size() - 2));
    }
    if (line.empty()) {
        out_err = "Configuration file '" + wide_to_utf8(cfg_file) + "' contains an empty path.";
        return false;
    }

    std::wstring wp = utf8_to_wide(line);
    if (!is_abs_w(wp)) {
        wp = exe_dir + L"\\" + wp;
    }
    out_gowin_path = full_path(wp);
    return true;
}

void print_usage() {
    std::fprintf(
        stdout,
        "NextGowin Standalone Synthesis & Custom Place-and-Route Engine (x86-64 + SSE2/SSE3)\n\n"
        "Drag-and-Drop Usage:\n"
        "  1. Set the Gowin V1.9.12.04 x64 path in gowin-path.txt (beside nextgowin.exe).\n"
        "  2. Drag a Gowin project (.gprj) file onto nextgowin.exe.\n\n"
        "Command-Line Usage:\n"
        "  nextgowin.exe <project.gprj> [--out <OutDir>] [options]\n"
        "  nextgowin.exe --config <ConfigJson> --out <OutDir> [options]\n"
        "  nextgowin.exe --ownpr --mapped-vg <vg> --sdc <sdc> --out <OutDir> [--cst <cst>]\n"
        "  nextgowin.exe --cpu-info\n"
        "  nextgowin.exe --self-test [--gowin-dir <GowinDir>] [--resources <ResourcePkgDir>]\n\n"
        "Options:\n"
        "  --gowin-dir <path>      Override Gowin installation path (defaults to gowin-path.txt beside nextgowin.exe)\n"
        "  --resources <path>      Override nextgowin-resources directory (auto-detected beside nextgowin.exe)\n"
        "  --out <path>            Output directory (defaults to <project_dir>\\<project>_nextgowin for .gprj)\n"
        "  --sync-ide / --no-sync-ide\n"
        "                          Overwrite/skip updating <project_dir>\\impl\\gwsynthesis & impl\\pnr (default: on)\n"
        "  --stop-after <stage>    frontend | resources | mapping | bitstream (default: bitstream)\n"
        "  --pnr-timeout <sec>     Watchdog for P&R stage (1..3600)\n"
        "  --total-timeout <sec>   Total SYN+PR wall-clock budget (1..3600)\n"
        "  --resume                Resume from verified mapping checkpoint (--config mode)\n"
        "  --net-alias K=M:N       Emission scalar alias binding\n"
        "  --pause / --no-pause    Force or suppress end-of-run console pause\n"
    );
    std::fflush(stdout);
}

int dispatch_to_core_dll(const std::wstring& bin_dir, const std::vector<std::wstring>& call_args) {
    std::wstring core_dll = bin_dir + L"\\nextgowin_core.dll";
    if (!file_exists(core_dll)) {
        std::fprintf(stderr, "[nextgowin] ERROR: Missing core engine %s\n", wide_to_utf8(core_dll).c_str());
        return 2;
    }
    SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    AddDllDirectory(bin_dir.c_str());

    HMODULE hCore = LoadLibraryExW(win_long_w(core_dll).c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!hCore) {
        std::fprintf(stderr, "[nextgowin] ERROR: LoadLibraryExW(%s) failed (err=%lu)\n",
                     wide_to_utf8(core_dll).c_str(), GetLastError());
        return 3;
    }
    using FnCoreEntry = int (*)(int, wchar_t**);
    auto fnEntry = reinterpret_cast<FnCoreEntry>(GetProcAddress(hCore, "ng_core_entry"));
    if (!fnEntry) {
        std::fprintf(stderr, "[nextgowin] ERROR: Missing ng_core_entry in %s\n", wide_to_utf8(core_dll).c_str());
        return 4;
    }

    std::vector<std::wstring> mut_args = call_args;
    std::vector<wchar_t*> argv_ptrs;
    argv_ptrs.reserve(mut_args.size() + 1);
    for (auto& a : mut_args) argv_ptrs.push_back(a.data());
    argv_ptrs.push_back(nullptr);
    return fnEntry(static_cast<int>(mut_args.size()), argv_ptrs.data());
}

bool is_explorer_single_process_console() {
    HANDLE hIn = GetStdHandle(STD_INPUT_HANDLE);
    if (hIn == INVALID_HANDLE_VALUE || hIn == nullptr || GetFileType(hIn) != FILE_TYPE_CHAR) {
        return false;
    }
    DWORD pids[4] = {0};
    DWORD n = GetConsoleProcessList(pids, 4);
    return (n == 1);
}

void pause_console_before_exit() {
    std::fflush(stdout);
    std::fflush(stderr);
    std::fprintf(stdout, "\nPress Enter to close this window...");
    std::fflush(stdout);
    HANDLE hIn = GetStdHandle(STD_INPUT_HANDLE);
    if (hIn != INVALID_HANDLE_VALUE && hIn != nullptr && GetFileType(hIn) == FILE_TYPE_CHAR) {
        FlushConsoleInputBuffer(hIn);
        wchar_t wbuf[16];
        DWORD read_chars = 0;
        ReadConsoleW(hIn, wbuf, 15, &read_chars, nullptr);
    } else {
        int ch = 0;
        while ((ch = std::getchar()) != '\n' && ch != EOF) {}
    }
}

} // namespace

int wmain(int argc, wchar_t* argv[]) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    SetConsoleOutputCP(CP_UTF8);

    wchar_t mod_buf[4096];
    DWORD mod_len = GetModuleFileNameW(nullptr, mod_buf, 4096);
    std::wstring exe_path = full_path(std::wstring(mod_buf, mod_len));
    std::wstring exe_parent = parent_dir(exe_path);

    std::wstring env_dir = exe_parent;
    if (!dir_exists(env_dir + L"\\bin")) {
        std::wstring from_env = get_env_w(L"NEXTGOWIN_ENV_DIR");
        if (!from_env.empty() && dir_exists(from_env + L"\\bin")) {
            env_dir = full_path(from_env);
        }
    }
    std::wstring bin_dir = dir_exists(env_dir + L"\\bin") ? (env_dir + L"\\bin") : env_dir;

    // Internal child-worker mode (`--internal-frontend-worker`, `--internal-pnr-worker`, or `--internal-gao-worker`):
    // Inherit already-validated NEXTGOWIN_* environment from parent process and enter core DLL immediately.
    if (argc >= 3) {
        std::wstring a1 = argv[1];
        if (a1 == L"--internal-frontend-worker" || a1 == L"--internal-pnr-worker" || a1 == L"--internal-gao-worker") {
            std::wstring bdir = get_env_w(L"NEXTGOWIN_BIN_DIR");
            if (bdir.empty()) bdir = bin_dir;
            std::vector<std::wstring> call_args;
            call_args.reserve(static_cast<size_t>(argc));
            for (int i = 0; i < argc; ++i) call_args.emplace_back(argv[i]);
            return dispatch_to_core_dll(bdir, call_args);
        }
    }

    bool auto_pause = is_explorer_single_process_console();
    bool force_pause = false;
    bool force_no_pause = false;
    auto finish = [&](int code) -> int {
        if (force_pause || (auto_pause && !force_no_pause)) {
            pause_console_before_exit();
        }
        return code;
    };

    bool show_help = false;
    bool show_cpu_info = false;
    bool run_self_test = false;
    std::wstring gowin_arg;
    std::wstring res_arg;
    std::vector<std::wstring> forward_args;
    forward_args.push_back(exe_path);

    for (int i = 1; i < argc; ++i) {
        std::wstring a = argv[i];
        if (a == L"-h" || a == L"--help") {
            show_help = true;
        } else if (a == L"--cpu-info") {
            show_cpu_info = true;
        } else if (a == L"--self-test") {
            run_self_test = true;
            forward_args.push_back(a);
        } else if (a == L"--pause") {
            force_pause = true;
        } else if (a == L"--no-pause" || a == L"--batch") {
            force_no_pause = true;
        } else if (a == L"--gowin-dir" && i + 1 < argc) {
            gowin_arg = argv[++i];
        } else if (a == L"--resources" && i + 1 < argc) {
            res_arg = argv[++i];
        } else {
            forward_args.push_back(a);
        }
    }

    if (show_help || (argc == 1)) {
        print_usage();
        return finish(show_help ? 0 : 1);
    }

    nextgowin_cpu::CpuFeatures cpu = nextgowin_cpu::detect_cpu_features();
    if (!cpu.baseline_ok) {
        std::fprintf(
            stderr,
            "[nextgowin] ERROR: Unsupported CPU '%s' — NextGowin requires x86-64 with SSE2 and SSE3 (all x86 CPUs > 2007).\n",
            cpu.brand
        );
        return finish(5);
    }

    if (show_cpu_info) {
        std::string js = nextgowin_cpu::format_cpu_json(cpu);
        std::fwrite(js.data(), 1, js.size(), stdout);
        return finish(0);
    }

    // Auto-discover `nextgowin-resources` relative to `nextgowin.exe`
    std::wstring res_dir = res_arg.empty() ? get_env_w(L"NEXTGOWIN_RESOURCES") : res_arg;
    if (res_dir.empty()) {
        const std::wstring candidates[] = {
            env_dir + L"\\nextgowin-resources",
            parent_dir(env_dir) + L"\\nextgowin-resources",
            env_dir
        };
        for (const auto& c : candidates) {
            if (file_exists(c + L"\\resource_manifest.json")) {
                res_dir = c;
                break;
            }
        }
    }
    if (!res_dir.empty()) res_dir = full_path(res_dir);

    // Resolve Gowin path: `--gowin-dir` CLI override -> `gowin-path.txt` beside `nextgowin.exe`
    std::wstring gowin_raw = gowin_arg;
    if (gowin_raw.empty()) {
        std::string cfg_err;
        if (!read_gowin_path_txt(exe_parent, gowin_raw, cfg_err)) {
            // In `--self-test` mode without gowin-path.txt, allow skipping SDK check only if gowin-path.txt is absent;
            // for actual flows, require valid gowin-path.txt!
            if (!run_self_test) {
                std::fprintf(stderr, "[nextgowin] ERROR: %s\n", cfg_err.c_str());
                return finish(7);
            }
        }
    } else {
        gowin_raw = full_path(gowin_raw);
    }

    std::wstring gowin_ide = gowin_raw;
    if (!gowin_ide.empty() && dir_exists(gowin_ide + L"\\IDE\\bin")) {
        gowin_ide = gowin_ide + L"\\IDE";
    }

    if (!run_self_test || !gowin_ide.empty()) {
        if (gowin_ide.empty() || !file_exists(gowin_ide + L"\\bin\\gowin.dll") || !file_exists(gowin_ide + L"\\bin\\gwsyn.dll")) {
            std::fprintf(
                stderr,
                "[nextgowin] ERROR: Gowin installation at '%s' is missing IDE\\bin\\gwsyn.dll or IDE\\bin\\gowin.dll.\n"
                "Update '%s\\gowin-path.txt' to point to your Gowin V1.9.12.04 x64 installation directory.\n",
                wide_to_utf8(gowin_raw).c_str(),
                wide_to_utf8(exe_parent).c_str()
            );
            return finish(7);
        }
    }

    if (!run_self_test && (res_dir.empty() || !dir_exists(res_dir))) {
        std::fprintf(
            stderr,
            "[nextgowin] ERROR: Could not locate 'nextgowin-resources' beside '%s'.\n"
            "Extract nextgowin-resources.zip alongside nextgowin-env (or pass --resources <dir>).\n",
            wide_to_utf8(env_dir).c_str()
        );
        return finish(6);
    }

    // Validate Gowin V1.9.12.04 x64 version (or show Continue / Cancel warning dialog for another version)
    bool allow_unsupported = false;
    std::string detected_ver = EXPECTED_GOWIN_DISPLAY;
    std::string warn_record;
    if (!gowin_ide.empty() && dir_exists(gowin_ide)) {
        bool exact_ok = false;
        std::string mismatch_rel;
        detected_ver = detect_gowin_version_info(gowin_ide, exact_ok, mismatch_rel);
        if (!exact_ok || detected_ver != EXPECTED_GOWIN_DISPLAY) {
            bool cont = show_gowin_version_warning_dialog(detected_ver, gowin_raw);
            if (!cont) {
                std::fprintf(
                    stderr,
                    "[nextgowin] Execution cancelled by user: expected '%s', detected '%s' at '%s'.\n",
                    EXPECTED_GOWIN_DISPLAY, detected_ver.c_str(), wide_to_utf8(gowin_raw).c_str()
                );
                return finish(8);
            }
            allow_unsupported = true;
            warn_record = "Unsupported Gowin version warning acknowledged by user (Continue): expected '" +
                          std::string(EXPECTED_GOWIN_DISPLAY) + "', detected '" + detected_ver +
                          "' at '" + wide_to_utf8(gowin_raw) + "'.";
            std::fprintf(stderr, "[nextgowin] WARNING: %s\n", warn_record.c_str());
            std::fflush(stderr);
        }
    }

    // Scrub environment completely: zero host Python/MinGW/VC++ paths
    SetEnvironmentVariableW(L"PYTHONHOME", nullptr);
    SetEnvironmentVariableW(L"PYTHONPATH", nullptr);
    set_env_w(L"OMP_NUM_THREADS", L"1");
    set_env_w(L"NEXTGOWIN_ENV_DIR", env_dir);
    set_env_w(L"NEXTGOWIN_BIN_DIR", bin_dir);
    set_env_w(L"NEXTGOWIN_ALLOW_UNSUPPORTED_GOWIN", allow_unsupported ? L"1" : L"0");
    set_env_w(L"NEXTGOWIN_DETECTED_GOWIN_VER", utf8_to_wide(detected_ver));
    set_env_w(L"NEXTGOWIN_GOWIN_WARNING", utf8_to_wide(warn_record));
    if (!res_dir.empty())   set_env_w(L"NEXTGOWIN_RESOURCES", res_dir);
    if (!gowin_ide.empty()) set_env_w(L"NEXTGOWIN_SDK", gowin_ide);

    wchar_t sys_dir_buf[1024];
    UINT sys_len = GetSystemDirectoryW(sys_dir_buf, 1024);
    std::wstring sys32 = (sys_len > 0 && sys_len < 1024) ? std::wstring(sys_dir_buf, sys_len) : L"C:\\Windows\\System32";
    std::wstring clean_path = bin_dir + L";" + sys32;
    set_env_w(L"PATH", clean_path);

    if (!run_self_test) {
        std::fprintf(
            stdout,
            "[nextgowin] CPU='%s' | ISA='x86-64 + SSE2/SSE3 (Tier 1)' | Engine=Native-C++20\n",
            cpu.brand
        );
        std::fflush(stdout);
    }

    int rc = dispatch_to_core_dll(bin_dir, forward_args);
    return finish(rc);
}
