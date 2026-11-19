#include "ng_pipeline.hpp"

#include <cctype>
#include <cmath>
#include <map>
#include <set>
#include <unordered_map>
#include <unordered_set>

namespace ng {

// ============================================================================
// Internal Frontend Worker (`--internal-frontend-worker <frontend.tcl>`)
// ============================================================================
int run_internal_frontend_worker(std::string_view script_path) {
    std::string abs_script = resolve_path(script_path);
    std::string sdk_bin = path_join(runtime_paths().gowin_sdk, "bin");
    if (!dir_exists(sdk_bin)) {
        std::fprintf(stderr, "[frontend-worker] Missing Gowin bin directory: %s\n", sdk_bin.c_str());
        return 2;
    }

    SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    std::wstring wbin = utf8_to_wide(sdk_bin);
    AddDllDirectory(wbin.c_str());
    SetCurrentDirectoryW(wbin.c_str());

    std::wstring wtcl = utf8_to_wide(path_join(sdk_bin, "tcl86t.dll"));
    HMODULE hTcl = LoadLibraryExW(wtcl.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!hTcl) {
        std::fprintf(stderr, "[frontend-worker] LoadLibraryExW(tcl86t.dll) failed (err=%lu)\n", GetLastError());
        return 3;
    }

    using FnCreateInterp = void* (__cdecl*)();
    using FnFindExec     = void  (__cdecl*)(const char*);
    using FnTclInit      = int   (__cdecl*)(void*);
    using FnTclEval      = int   (__cdecl*)(void*, const char*);
    using FnGetStrRes    = const char* (__cdecl*)(void*);
    using FnGwAppInit    = int   (__cdecl*)(void*);

    auto fnCreate = reinterpret_cast<FnCreateInterp>(GetProcAddress(hTcl, "Tcl_CreateInterp"));
    auto fnFind   = reinterpret_cast<FnFindExec>(GetProcAddress(hTcl, "Tcl_FindExecutable"));
    auto fnInit   = reinterpret_cast<FnTclInit>(GetProcAddress(hTcl, "Tcl_Init"));
    auto fnEval   = reinterpret_cast<FnTclEval>(GetProcAddress(hTcl, "Tcl_Eval"));
    auto fnRes    = reinterpret_cast<FnGetStrRes>(GetProcAddress(hTcl, "Tcl_GetStringResult"));
    if (!fnCreate || !fnFind || !fnInit || !fnEval || !fnRes) {
        std::fprintf(stderr, "[frontend-worker] Missing Tcl API exports\n");
        return 4;
    }

    std::string gw_sh = path_join(sdk_bin, "gw_sh.exe");
    fnFind(gw_sh.c_str());
    void* interp = fnCreate();
    int rc_init = fnInit(interp);
    std::printf("TCL_INIT %d\n", rc_init);
    std::fflush(stdout);

    std::wstring wgw = utf8_to_wide(path_join(sdk_bin, "gwsyn.dll"));
    HMODULE hGw = LoadLibraryExW(wgw.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!hGw) {
        std::fprintf(stderr, "[frontend-worker] LoadLibraryExW(gwsyn.dll) failed (err=%lu)\n", GetLastError());
        return 5;
    }
    auto fnGwInit = reinterpret_cast<FnGwAppInit>(GetProcAddress(hGw, "?TCL_appInit@SYN@@YAHPEAUTcl_Interp@@@Z"));
    if (!fnGwInit) {
        std::fprintf(stderr, "[frontend-worker] Missing SYN::TCL_appInit in gwsyn.dll\n");
        return 6;
    }
    int rc_gw = fnGwInit(interp);
    std::printf("GW_INIT %d\n", rc_gw);
    std::fflush(stdout);

    std::string content = read_file_text(abs_script);
    if (content.size() >= 3 && static_cast<uint8_t>(content[0]) == 0xEF && static_cast<uint8_t>(content[1]) == 0xBB && static_cast<uint8_t>(content[2]) == 0xBF) {
        content.erase(0, 3);
    }
    for (const auto& cmd : split_lines(content)) {
        std::string lt = ltrim(cmd);
        if (lt.empty() || lt[0] == '#') continue;
        std::printf("COMMAND %s\n", cmd.c_str());
        std::fflush(stdout);
        int ret = fnEval(interp, cmd.c_str());
        const char* rstr = fnRes(interp);
        std::printf("RESULT %d %s\n", ret, rstr ? rstr : "");
        std::fflush(stdout);
        if (ret != 0) return ret;
    }
    return 0;
}

// ============================================================================
// Stage 1: export_gowin_frontend
// ============================================================================
namespace {

std::string xml_unescape(std::string_view s) {
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '&') {
            size_t semi = s.find(';', i + 1);
            if (semi != std::string_view::npos) {
                std::string_view ent = s.substr(i + 1, semi - i - 1);
                if (ent == "amp") { out.push_back('&'); i = semi; continue; }
                if (ent == "lt")  { out.push_back('<'); i = semi; continue; }
                if (ent == "gt")  { out.push_back('>'); i = semi; continue; }
                if (ent == "quot"){ out.push_back('"'); i = semi; continue; }
                if (ent == "apos"){ out.push_back('\''); i = semi; continue; }
            }
        }
        out.push_back(s[i]);
    }
    return out;
}

OrderedMap<std::string> parse_xml_attrs(std::string_view tag_body) {
    OrderedMap<std::string> attrs;
    size_t i = 0, n = tag_body.size();
    while (i < n) {
        while (i < n && (std::isspace(static_cast<unsigned char>(tag_body[i])) || tag_body[i] == '/')) ++i;
        if (i >= n) break;
        size_t ks = i;
        while (i < n && !std::isspace(static_cast<unsigned char>(tag_body[i])) && tag_body[i] != '=' && tag_body[i] != '/') ++i;
        std::string key(tag_body.substr(ks, i - ks));
        while (i < n && std::isspace(static_cast<unsigned char>(tag_body[i]))) ++i;
        if (i >= n || tag_body[i] != '=') continue;
        ++i;
        while (i < n && std::isspace(static_cast<unsigned char>(tag_body[i]))) ++i;
        if (i < n && (tag_body[i] == '"' || tag_body[i] == '\'')) {
            char q = tag_body[i++];
            size_t vs = i;
            while (i < n && tag_body[i] != q) ++i;
            attrs.set(key, xml_unescape(tag_body.substr(vs, i - vs)));
            if (i < n) ++i;
        }
    }
    return attrs;
}

std::string tcl_quote_path(std::string_view p) {
    std::string v = posix_path(resolve_path(p));
    for (char c : v) {
        if (c == '{' || c == '}' || c == '\r' || c == '\n') {
            throw std::runtime_error("Unsupported Tcl path: " + v);
        }
    }
    return "{" + v + "}";
}

std::vector<std::string> split_csv_line_simple(std::string_view ln) {
    std::vector<std::string> cols;
    std::string cur;
    bool in_q = false;
    for (size_t i = 0; i < ln.size(); ++i) {
        char c = ln[i];
        if (c == '"') {
            in_q = !in_q;
        } else if (c == ',' && !in_q) {
            cols.push_back(trim(cur));
            cur.clear();
        } else {
            cur.push_back(c);
        }
    }
    cols.push_back(trim(cur));
    return cols;
}

} // namespace

TargetDeviceSpec resolve_target_device(
    std::string_view dev_code,
    std::string_view dev_name,
    std::string_view part_num
) {
    std::string code = trim(dev_code);
    std::string name = trim(dev_name);
    std::string pn   = trim(part_num);

    auto format_supported_catalog = [](const std::vector<std::vector<std::string>>& rows) -> std::string {
        std::string cat = "\n  Supported GW5AST-138 parts in Gowin V1.9.12.04 x64 (share/device/device_info.csv):";
        for (const auto& r : rows) {
            if (r.size() >= 13 && (r[3] == "GW5AST-138B" || r[3] == "GW5AST-138C")) {
                cat += "\n    - " + r[0] + " : device=" + r[3] + ", pn=" + r[1]
                     + ", package=" + r[4] + ", speed=" + r[6] + " (-" + r[7] + "), ssram=" + r[12];
            }
        }
        cat += "\n  Note: GW5AST-138 484-ball package is PBGA484A (PG484A in part number); FCPBGA484A does not exist.";
        return cat;
    };

    auto verify_sdk_pkg_files = [](const TargetDeviceSpec& spec) {
        if (runtime_paths().gowin_sdk.empty()) return;
        std::string pkg_json = path_join(runtime_paths().gowin_sdk, "data\\device\\" + spec.device_name + "\\" + spec.package_name + ".json");
        std::string fse_file = path_join(runtime_paths().gowin_sdk, "share\\device\\" + spec.device_name + "\\" + spec.device_name + ".fse");
        if (!file_exists(pkg_json)) {
            throw std::runtime_error(
                "Resolved target part '" + spec.part_number + "' (" + spec.dev_p
                + "), but package pin database does not exist in Gowin SDK: " + pkg_json
            );
        }
        if (!file_exists(fse_file)) {
            throw std::runtime_error(
                "Resolved target part '" + spec.part_number + "' (" + spec.dev_p
                + "), but device fuse database does not exist in Gowin SDK: " + fse_file
            );
        }
    };

    std::string csv_path = path_join(runtime_paths().gowin_sdk, "share\\device\\device_info.csv");
    if (!runtime_paths().gowin_sdk.empty() && file_exists(csv_path)) {
        std::string csv_txt = read_file_text(csv_path);
        const std::vector<std::string>* matched = nullptr;
        std::vector<std::vector<std::string>> rows;
        for (const auto& ln : split_lines(csv_txt)) {
            std::string t = trim(ln);
            if (t.empty() || t[0] == '#') continue;
            auto cols = split_csv_line_simple(t);
            if (cols.size() >= 13) rows.push_back(std::move(cols));
        }

        if (!code.empty()) {
            std::string cl = to_lower_ascii(code);
            for (const auto& r : rows) {
                if (to_lower_ascii(r[0]) == cl) { matched = &r; break; }
            }
            if (!matched) {
                throw std::runtime_error(
                    "Unrecognized Gowin device code '" + code + "' (device='" + (name.empty() ? "<unspecified>" : name)
                    + "', partNumber='" + (pn.empty() ? "<unspecified>" : pn) + "'): not found in " + csv_path
                    + format_supported_catalog(rows)
                );
            }
            if (!name.empty() && to_lower_ascii((*matched)[3]) != to_lower_ascii(name)) {
                throw std::runtime_error(
                    "Conflicting <Device> specification in project: device code '" + code + "' maps to device '"
                    + (*matched)[3] + "' (part '" + (*matched)[1] + "', package '" + (*matched)[4]
                    + "'), which disagrees with specified device name '" + name + "'" + format_supported_catalog(rows)
                );
            }
            if (!pn.empty() && to_lower_ascii((*matched)[1]) != to_lower_ascii(pn)) {
                throw std::runtime_error(
                    "Conflicting <Device> specification in project: device code '" + code + "' maps to partNumber '"
                    + (*matched)[1] + "' (device '" + (*matched)[3] + "', package '" + (*matched)[4] + "', speed '"
                    + (*matched)[6] + "'), which disagrees with specified partNumber '" + pn + "'" + format_supported_catalog(rows)
                );
            }
        }

        if (!matched && !pn.empty() && !name.empty()) {
            std::string pl = to_lower_ascii(pn), nl = to_lower_ascii(name);
            const std::vector<std::string>* other_dev_match = nullptr;
            for (const auto& r : rows) {
                if (to_lower_ascii(r[1]) == pl) {
                    if (to_lower_ascii(r[3]) == nl) { matched = &r; break; }
                    if (!other_dev_match) other_dev_match = &r;
                }
            }
            if (!matched) {
                std::string hint;
                if (other_dev_match) {
                    hint = " Note: partNumber '" + pn + "' exists under device '" + (*other_dev_match)[3]
                         + "' (code '" + (*other_dev_match)[0] + "', package '" + (*other_dev_match)[4]
                         + "', speed '" + (*other_dev_match)[6] + "'), not '" + name + "'.";
                }
                throw std::runtime_error(
                    "Unsupported or mismatched device/part combination: device='" + name + "', partNumber='" + pn
                    + "' is not in Gowin's official device catalog." + hint + format_supported_catalog(rows)
                );
            }
        }

        if (!matched && !pn.empty()) {
            // Default to GW5AST-138C when only part_num is supplied (legacy .prj compatibility)
            std::string pl = to_lower_ascii(pn);
            for (const auto& r : rows) {
                if (to_lower_ascii(r[1]) == pl && r[3] == "GW5AST-138C") { matched = &r; break; }
            }
            if (!matched) {
                for (const auto& r : rows) {
                    if (to_lower_ascii(r[1]) == pl) { matched = &r; break; }
                }
            }
            if (!matched) {
                throw std::runtime_error(
                    "Unrecognized Gowin partNumber '" + pn + "': not found in " + csv_path + format_supported_catalog(rows)
                );
            }
        }

        if (!matched && !name.empty()) {
            // If only device name is given with no code and no partNumber, only accept exact default GW5AST-138C
            if (to_upper_ascii(name) == "GW5AST-138C") {
                for (const auto& r : rows) {
                    if (r[0] == "gw5ast138c-003") { matched = &r; break; }
                }
            } else {
                throw std::runtime_error(
                    "Incomplete or unsupported target device '" + name
                    + "' without explicit partNumber or device code." + format_supported_catalog(rows)
                );
            }
        }

        if (matched) {
            const auto& r = *matched;
            if (r[3] != "GW5AST-138C" && r[3] != "GW5AST-138B") {
                throw std::runtime_error(
                    "Unsupported target device '" + r[3] + "' (code='" + r[0] + "', part='" + r[1] + "', package='" + r[4]
                    + "'). NextGowin supports GW5AST-138B and GW5AST-138C devices only." + format_supported_catalog(rows)
                );
            }
            TargetDeviceSpec spec;
            spec.code         = r[0];
            spec.part_number  = r[1];
            spec.series       = r[2];
            spec.device_name  = r[3];
            spec.package_name = r[4];
            spec.speed_grade  = r[6];
            spec.speed_num    = r[7];
            spec.dev_p        = r[3] + "-" + r[4] + "-" + r[7];
            std::string ssram_col = to_lower_ascii(r[12]);
            spec.has_ssram    = (!ssram_col.empty() && ssram_col != "0" && ssram_col != "0kb");
            verify_sdk_pkg_files(spec);
            return spec;
        }
    }

    if (code.empty() && (pn.empty() || pn == PINNED_PART) && (name.empty() || name == "GW5AST-138C")) {
        TargetDeviceSpec spec;
        spec.code         = "gw5ast138c-003";
        spec.series       = "GW5AST";
        spec.device_name  = "GW5AST-138C";
        spec.package_name = "FCPBGA676A";
        spec.speed_num    = "1";
        spec.speed_grade  = "C1/I0";
        spec.part_number  = PINNED_PART;
        spec.dev_p        = PINNED_DEVICE;
        spec.has_ssram    = true;
        verify_sdk_pkg_files(spec);
        return spec;
    }

    throw std::runtime_error(
        "Unsupported target device specification (code='" + (code.empty() ? std::string("<unspecified>") : code)
        + "', device='" + (name.empty() ? std::string("<unspecified>") : name)
        + "', part='" + (pn.empty() ? std::string("<unspecified>") : pn)
        + "'). NextGowin supports GW5AST-138B and GW5AST-138C (packages PBGA484A, PBGA676A, FCPBGA676A) only."
    );
}

JsonValue export_gowin_frontend(std::string_view project_path, std::string_view out_dir, double timeout_sec) {
    double eff_timeout = clipped_timeout(timeout_sec);
    std::string prj = resolve_path(project_path);
    std::string prj_parent = parent_dir(prj);
    std::string xml = read_file_text(prj);

    std::string found_code, found_dev_name, found_part;
    OrderedMap<std::vector<std::string>> options;
    std::vector<std::string> sources, gao, vhdl;

    size_t pos = 0;
    while (pos < xml.size()) {
        size_t lt = xml.find('<', pos);
        if (lt == std::string::npos) break;
        if (lt + 4 <= xml.size() && xml.compare(lt, 4, "<!--") == 0) {
            size_t ce = xml.find("-->", lt + 4);
            pos = (ce == std::string::npos) ? xml.size() : (ce + 3);
            continue;
        }
        size_t gt = xml.find('>', lt + 1);
        if (gt == std::string::npos) break;
        std::string_view inside(xml.data() + lt + 1, gt - lt - 1);
        pos = gt + 1;
        if (inside.empty() || inside[0] == '?' || inside[0] == '/' || inside[0] == '!') continue;
        bool self_closing = (!inside.empty() && inside.back() == '/');
        size_t sp = 0;
        while (sp < inside.size() && !std::isspace(static_cast<unsigned char>(inside[sp])) && inside[sp] != '/') ++sp;
        std::string_view tag = inside.substr(0, sp);
        auto attrs = parse_xml_attrs(inside.substr(sp));
        if (tag == "Device") {
            found_dev_name = attrs.value_or("name", attrs.value_or("id", ""));
            found_part = attrs.value_or("partNumber", "");
            if (found_part.empty()) found_part = attrs.value_or("pn", "");
            if (!self_closing) {
                size_t close_dev = xml.find("</Device>", pos);
                if (close_dev != std::string::npos) {
                    found_code = xml_unescape(trim(std::string_view(xml.data() + pos, close_dev - pos)));
                }
            }
        } else if (tag == "Option") {
            std::string otype = attrs.value_or("type", "");
            std::string oval  = attrs.value_or("value", "");
            options[otype].push_back(oval);
        } else if (tag == "File") {
            std::string en = to_lower_ascii(trim(attrs.value_or("enable", "1")));
            if (en == "0" || en == "false") continue;
            std::string fpath = attrs.value_or("path", "");
            std::string full_f = is_absolute_path(fpath) ? resolve_path(fpath) : resolve_path(path_join(prj_parent, fpath));
            if (!file_exists(full_f)) {
                throw std::runtime_error("Project input file not found: " + full_f);
            }
            std::string ftype = attrs.value_or("type", "");
            if (ftype == "gao") gao.push_back(full_f);
            else if (ftype == "verilog" || ftype == "file.verilog") sources.push_back(full_f);
            else if (ftype == "vhdl" || ftype == "file.vhdl") vhdl.push_back(full_f);
            else if (ftype == "cst" || ftype == "file.cst" || ftype == "sdc" || ftype == "file.sdc" ||
                     ftype == "file.gpa" || ftype == "file.other" || ftype == "csr" || ftype == "file.csr") {
                continue;
            } else {
                throw std::runtime_error("Unsupported synthesis project file type '" + ftype + "' for file: " + full_f);
            }
        }
    }

    TargetDeviceSpec dev_spec = resolve_target_device(found_code, found_dev_name, found_part);
    if (sources.empty() && vhdl.empty()) {
        throw std::runtime_error("No enabled Verilog/SystemVerilog/VHDL source files found in project");
    }
    if (!gao.empty() && file_name(gao.back()) != "gw_gao_top.v") {
        throw std::runtime_error("Expected generated RTL_GAO wrapper (gw_gao_top.v) as final GAO input");
    }

    static const std::unordered_set<std::string> known_opts = {
        "disable_insert_pad","dsp_style","enable_dsrm","global_freq","include_path","looplimit",
        "map_option","max_fanout","netlist_hierarchy","opt_goal","output_file",
        "print_all_synthesis_warning","ram_rw_check","ram_style","rom_style",
        "shift_reg","top_module","use_file_list","vcc","verilog_language","vhdl_language"
    };
    for (const auto& [ok, _] : options) {
        if (!known_opts.count(ok)) {
            throw std::runtime_error("Unsupported project option: " + ok);
        }
    }
    if (const auto* dip = options.find_ptr("disable_insert_pad"); dip && !dip->empty() && dip->back() == "1") {
        throw std::runtime_error("Unsupported project setting disable_insert_pad=1: top-level I/O pad insertion is required");
    }

    std::string out_real = resolve_path(out_dir);
    make_dirs(out_real, false);
    AsciiDirAlias out_alias(out_real, true);
    std::string out = out_alias.path();

    std::string gwsyn_dll = path_join(runtime_paths().gowin_sdk, "bin\\gwsyn.dll");
    std::string actual_gwsyn_sha = verify_or_record_sdk_file_sha(gwsyn_dll, EXPECTED_GWSYN_DLL_SHA, "bin/gwsyn.dll");

    std::string top = "top";
    if (const auto* tm = options.find_ptr("top_module"); tm && !tm->empty()) {
        top = tm->back();
    }
    if (!is_simple_ident(top) || top[0] == '$') {
        throw std::runtime_error("Unsupported top identifier: " + top);
    }

    OrderedMap<JsonValue> snapshots;
    for (const auto& p : vhdl) snapshots.set(p, sha256_file(p));
    for (const auto& p : sources) snapshots.set(p, sha256_file(p));
    for (const auto& p : gao) snapshots.set(p, sha256_file(p));
    snapshots.set(prj, sha256_file(prj));

    bool use_file_list = !vhdl.empty();
    if (const auto* ufl = options.find_ptr("use_file_list"); ufl && !ufl->empty() && ufl->back() == "1") {
        use_file_list = true;
    }

    // Stage any source/GAO files whose path contains non-ASCII bytes or Tcl braces into ASCII-safe staging inside `out`
    std::string staged_src_dir = path_join(out, "_staged_inputs");
    bool created_staged_dir = false;
    auto safe_verilog_input_path = [&](const std::string& orig_p, size_t idx) -> std::string {
        bool has_non_ascii_or_brace = false;
        for (unsigned char c : orig_p) {
            if (c < 0x20 || c >= 0x7F || c == '{' || c == '}' || c == '"') {
                has_non_ascii_or_brace = true;
                break;
            }
        }
        if (!has_non_ascii_or_brace) return orig_p;
        if (!created_staged_dir) {
            make_dirs(staged_src_dir, true);
            created_staged_dir = true;
        }
        std::string ext = file_ext(orig_p);
        if (ext.empty() || needs_ascii_alias(ext)) ext = ".v";
        std::string dst = path_join(staged_src_dir, (file_name(orig_p) == "gw_gao_top.v")
            ? std::string("gw_gao_top.v")
            : ("input_" + std::to_string(idx) + ext));
        copy_file_overwrite(orig_p, dst);
        return dst;
    };

    std::string wrapper = path_join(out, "frontend_inputs.v");
    std::string file_list_path = path_join(out, "frontend_files.f");
    std::string gao_back_safe;
    if (use_file_list) {
        std::vector<std::string> flines;
        size_t sidx = 0;
        for (const auto& vf : vhdl) flines.push_back(posix_path(safe_verilog_input_path(vf, sidx++)));
        for (const auto& sf : sources) flines.push_back(posix_path(safe_verilog_input_path(sf, sidx++)));
        for (size_t i = 0; i + 1 < gao.size(); ++i) {
            flines.push_back(posix_path(safe_verilog_input_path(gao[i], sidx++)));
        }
        if (!gao.empty()) gao_back_safe = safe_verilog_input_path(gao.back(), sidx++);
        write_file_text(file_list_path, join_strings(flines, "\n") + "\n");
    } else {
        std::vector<std::string> inc_lines;
        for (size_t i = 0; i < sources.size(); ++i) {
            inc_lines.push_back("`include \"" + posix_path(safe_verilog_input_path(sources[i], i)) + "\"");
        }
        for (size_t i = 0; i + 1 < gao.size(); ++i) {
            inc_lines.push_back("`include \"" + posix_path(safe_verilog_input_path(gao[i], sources.size() + i)) + "\"");
        }
        gao_back_safe = gao.empty() ? "" : safe_verilog_input_path(gao.back(), sources.size() + gao.size());
        write_file_text(wrapper, join_strings(inc_lines, "\n") + "\n");
    }

    std::vector<std::string> roots_raw;
    if (const auto* ips = options.find_ptr("include_path")) {
        for (const auto& ip : *ips) {
            std::string rp;
            if (is_absolute_path(ip)) {
                rp = resolve_path(ip);
            } else {
                std::string rel_prj = resolve_path(path_join(prj_parent, ip));
                rp = dir_exists(rel_prj) ? rel_prj : resolve_path(ip);
            }
            if (!dir_exists(rp)) {
                throw std::runtime_error("Project include_path directory not found: " + rp);
            }
            roots_raw.push_back(rp);
        }
    }
    if (!gao.empty()) roots_raw.push_back(parent_dir(gao.back()));
    {
        std::unordered_set<std::string> seen_roots;
        std::vector<std::string> dedup_roots;
        for (const auto& r : roots_raw) {
            std::string rk = to_lower_ascii(r);
            if (seen_roots.insert(rk).second) dedup_roots.push_back(r);
        }
        roots_raw = std::move(dedup_roots);
    }

    std::string include_dir = path_join(out, "includes");
    make_dirs(include_dir, false);
    OrderedMap<JsonValue> staged;
    uint64_t total_inc_bytes = 0;
    static const std::unordered_set<std::string> inc_exts = {
        ".v", ".vh", ".sv", ".svh", ".hex", ".mi", ".bin", ".txt", ".dat", ".mem", ".mif", ".init", ".inc"
    };
    for (const auto& directory : roots_raw) {
        if (is_subpath_of(out_real, directory)) {
            throw std::runtime_error("Include root contains the output directory; choose a separate include root");
        }
        for (const auto& src_f : rglob_files(directory)) {
            std::string fext = to_lower_ascii(file_ext(src_f));
            if (!inc_exts.count(fext)) continue;
            std::string rel_p = relative_posix_path(src_f, directory);
            std::string key = to_lower_ascii(rel_p);
            if (starts_with(key, "temp/") || key.find("/temp/") != std::string::npos) continue;
            auto data = read_file_bytes(src_f);
            std::string digest = sha256_bytes(data.data(), data.size());
            snapshots.set(src_f, digest);
            if (staged.contains(key)) continue;
            total_inc_bytes += data.size();
            if (total_inc_bytes > 256ULL * 1024ULL * 1024ULL || staged.size() > 10000) {
                throw std::runtime_error("Include snapshot exceeds the bounded importer budget");
            }
            std::string target = path_join(include_dir, rel_p);
            write_file_bytes(target, data.data(), data.size());
            staged.set(key, digest);
            std::string base_nm = file_name(src_f);
            std::string flat_key = to_lower_ascii(base_nm);
            if (!staged.contains(flat_key)) {
                write_file_bytes(path_join(include_dir, base_nm), data.data(), data.size());
                staged.set(flat_key, digest);
            }
        }
    }

    std::string destination = path_join(out, "elaborated.v");
    std::string vlang = "verilog-2001";
    if (const auto* vl = options.find_ptr("verilog_language"); vl && !vl->empty()) {
        vlang = vl->back();
    } else {
        for (const auto& s : sources) {
            std::string ext = to_lower_ascii(file_ext(s));
            if (ext == ".sv" || ext == ".svh") { vlang = "sysv-2017"; break; }
        }
    }
    std::string vhlang = "vhdl-1993";
    if (const auto* vhl = options.find_ptr("vhdl_language"); vhl && !vhl->empty() && !vhl->back().empty()) {
        vhlang = vhl->back();
    }

    std::string cmd = use_file_list
        ? ("extract -if " + tcl_quote_path(file_list_path))
        : ("extract -i " + tcl_quote_path(wrapper));
    if (!gao.empty()) cmd += " -gao " + tcl_quote_path(gao_back_safe);
    if (!roots_raw.empty()) cmd += " -inc " + tcl_quote_path(include_dir);
    if (!sources.empty() || !gao.empty()) cmd += " -verilog_language " + vlang;
    if (!vhdl.empty()) cmd += " -vhdl_language " + vhlang;
    cmd += " -e " + top + " -o " + tcl_quote_path(destination);

    std::string dev_cmd = "dev -p " + dev_spec.dev_p + " -pn " + dev_spec.part_number;
    if (const auto* eds = options.find_ptr("enable_dsrm"); eds && !eds->empty() && !eds->back().empty()) {
        dev_cmd += " -enable_dsrm " + eds->back();
    }
    std::vector<std::string> commands = {
        dev_cmd,
        cmd
    };
    std::string script = path_join(out, "frontend.tcl");
    write_file_text(script, join_strings(commands, "\n") + "\n");

    JsonValue tdev = JsonValue::object();
    tdev["code"] = dev_spec.code;
    tdev["device_name"] = dev_spec.device_name;
    tdev["package_name"] = dev_spec.package_name;
    tdev["speed_grade"] = dev_spec.speed_grade;
    tdev["speed_num"] = dev_spec.speed_num;
    tdev["part_number"] = dev_spec.part_number;
    tdev["dev_p"] = dev_spec.dev_p;
    tdev["has_ssram"] = dev_spec.has_ssram;

    JsonValue result = JsonValue::object();
    result["schema"] = "gowin-frontend-export-v1";
    result["status"] = "running";
    result["top"] = top;
    result["target_device"] = std::move(tdev);
    result["boundary"] = "After elaboration and optional RTL GAO merge; before cvt/infer/map";
    result["synthesis_dll_sha256"] = actual_gwsyn_sha;
    result["inputs"] = JsonValue(snapshots);
    JsonValue cmd_arr = JsonValue::array();
    for (const auto& c : commands) cmd_arr.push_back(c);
    result["commands"] = std::move(cmd_arr);
    result["staged_include_files"] = JsonValue(staged);
    result["gao_wrapper"] = gao.empty() ? JsonValue(nullptr) : JsonValue(gao.back());
    result["gao_generation"] = "Consumes vendor-generated project inputs; does not reinterpret .rao";
    result["runner_sha256"] = sha256_file(runtime_paths().core_dll);

    auto save = [&]() { write_json_pretty(path_join(out, "manifest.json"), result); };
    save();

    double t0 = perf_counter();
    ProcResult pr;
    {
        GowinHostStaging staging(out, runtime_paths().gowin_sdk);
        pr = run_process_to_log(
            {staging.host_exe(), "--internal-frontend-worker", script},
            staging.host_bin_dir(),
            path_join(out, "frontend.log"),
            {},
            eff_timeout
        );
    }
    if (pr.timed_out) {
        result["status"] = "timeout";
        result["wall_seconds"] = perf_counter() - t0;
        save();
        throw TimeoutError("Frontend export timed out");
    }

    result["wall_seconds"] = perf_counter() - t0;
    result["returncode"] = pr.returncode;
    JsonValue errs = JsonValue::array();
    for (const auto& ln : split_lines(pr.output)) {
        std::string lt = ltrim(ln);
        if (starts_with(lt, "ERROR") && (lt.size() == 5 || std::isspace(static_cast<unsigned char>(lt[5])) || lt[5] == '(')) {
            errs.push_back(ln);
        }
    }
    result["errors"] = errs;

    bool inputs_unchanged = true;
    for (const auto& [p, dig] : snapshots) {
        if (sha256_file(p) != dig.as_str()) {
            inputs_unchanged = false;
            break;
        }
    }
    result["inputs_unchanged"] = inputs_unchanged;

    bool ok = (pr.returncode == 0) && errs.as_arr().empty() && file_exists(destination) && inputs_unchanged;
    if (ok) {
        std::string elab = read_file_text(destination);
        // Verify `\bmodule\s+<top>\s*\(` (allow case-insensitive match when VHDL sources are present)
        auto find_top_module = [&](bool ignore_case, std::string& matched_name) -> bool {
            size_t sp = 0;
            std::string top_low = to_lower_ascii(top);
            while ((sp = elab.find("module", sp)) != std::string::npos) {
                bool left_ok = (sp == 0) || !(std::isalnum(static_cast<unsigned char>(elab[sp - 1])) || elab[sp - 1] == '_');
                size_t k = sp + 6;
                if (left_ok && k < elab.size() && std::isspace(static_cast<unsigned char>(elab[k]))) {
                    while (k < elab.size() && std::isspace(static_cast<unsigned char>(elab[k]))) ++k;
                    if (k + top.size() <= elab.size()) {
                        std::string cand = elab.substr(k, top.size());
                        bool name_ok = ignore_case ? (to_lower_ascii(cand) == top_low) : (cand == top);
                        if (name_ok) {
                            size_t m = k + top.size();
                            while (m < elab.size() && std::isspace(static_cast<unsigned char>(elab[m]))) ++m;
                            if (m < elab.size() && (elab[m] == '(' || elab[m] == ';' || elab[m] == '#')) {
                                matched_name = cand;
                                return true;
                            }
                        }
                    }
                }
                sp += 6;
            }
            return false;
        };
        std::string matched_top;
        bool found_top = find_top_module(false, matched_top);
        if (!found_top && !vhdl.empty()) {
            found_top = find_top_module(true, matched_top);
        }
        if (found_top && matched_top != top) {
            result["top"] = matched_top;
        }
        ok = found_top;
        result["elaborated_sha256"] = sha256_file(destination);
        result["bytes"] = static_cast<int64_t>(file_size_bytes(destination));
    }
    result["status"] = ok ? "complete" : "failed";
    if (created_staged_dir && dir_exists(staged_src_dir)) {
        remove_all(staged_src_dir);
    }
    save();
    out_alias.sync_back();
    if (!ok) {
        std::string flog = path_join(out_real, "frontend.log");
        std::string msg = "Frontend RTL elaboration failed (project='" + prj + "', top='" + top
                        + "', device='" + dev_spec.dev_p + "', part='" + dev_spec.part_number
                        + "', returncode=" + std::to_string(pr.returncode) + ", log='" + flog + "')";
        if (!inputs_unchanged) {
            msg += "\n  Reason: project source files changed on disk during elaboration";
        } else if (pr.returncode == 0 && errs.as_arr().empty() && file_exists(destination)) {
            msg += "\n  Reason: top module '" + top + "' was not found in elaborated netlist '" + destination + "'";
        }
        if (!errs.as_arr().empty()) {
            for (const auto& ev : errs.as_arr()) {
                if (ev.is_string()) msg += "\n  " + trim(ev.as_str());
            }
        } else if (!pr.output.empty()) {
            auto lns = split_lines(pr.output);
            std::vector<std::string> rel;
            for (const auto& l : lns) {
                std::string tl = trim(l);
                if (!tl.empty()) rel.push_back(tl);
            }
            size_t st_i = (rel.size() > 10) ? (rel.size() - 10) : 0;
            for (size_t i = st_i; i < rel.size(); ++i) msg += "\n  " + rel[i];
        }
        throw std::runtime_error(msg);
    }
    return result;
}

// ============================================================================
// Stage 2a: gowin_macros_prepare
// ============================================================================
std::tuple<std::string, std::string, JsonValue> gowin_macros_prepare(
    std::string_view exported_v,
    std::string_view profile_path,
    std::string_view rao_path
) {
    JsonValue profile = parse_json_file(profile_path);
    if (profile["schema"].as_str() != "native-gao-macro-v1") {
        throw std::runtime_error("Unknown macro binding schema");
    }
    std::string source_p = path_join(runtime_paths().res_root, profile["netlist"].as_str());
    if (sha256_file(source_p) != profile["netlist_sha256"].as_str()) {
        throw std::runtime_error("Native macro netlist changed");
    }
    if (rao_path.empty() || sha256_file(rao_path) != profile["rao_sha256"].as_str()) {
        throw std::runtime_error("Macro and .rao snapshot disagree");
    }
    if (const JsonValue* gi = profile.get("generated_inputs"); gi && gi->is_object()) {
        for (const auto& [rel, dig] : gi->as_obj()) {
            if (sha256_file(path_join(runtime_paths().res_root, rel)) != dig.as_str()) {
                throw std::runtime_error("Generated GAO configuration changed: " + rel);
            }
        }
    }

    std::string mod_name = profile["module"].as_str();
    const auto& portmap_obj = profile["ports"].as_obj();
    std::string native = read_file_text(source_p);
    std::string discard_top = profile["discard_top"].as_str();
    for (const auto& u : verilog_units(native)) {
        if (u.name == discard_top) {
            native = native.substr(0, u.start) + native.substr(u.end);
            break;
        }
    }

    size_t ma = 0, mb = 0;
    bool found_m = false;
    for (const auto& u : verilog_units(native)) {
        if (u.name == mod_name) { ma = u.start; mb = u.end; found_m = true; break; }
    }
    if (!found_m) throw std::runtime_error("Macro module absent from native source");

    std::string body = native.substr(ma, mb - ma);
    size_t stop = body.find(';');
    std::string header = body.substr(0, stop);
    size_t nm_p = header.find(mod_name, 6);
    size_t lp = header.find('(', nm_p == std::string::npos ? 0 : (nm_p + mod_name.size()));
    size_t rp = header.rfind(')');
    auto htoks = tokenize_verilog(std::string_view(header).substr(lp + 1, rp - lp - 1));
    std::unordered_set<std::string> native_ports, mapped_vals;
    for (const auto& t : htoks) if (t != ",") native_ports.insert(t);
    OrderedMap<std::string> reverse_map;
    for (const auto& [src_p, nat_p_jv] : portmap_obj) {
        mapped_vals.insert(nat_p_jv.as_str());
        reverse_map.set(nat_p_jv.as_str(), src_p);
    }
    if (native_ports != mapped_vals || mapped_vals.size() != portmap_obj.size()) {
        throw std::runtime_error("Native macro interface does not match port binding");
    }

    std::vector<std::string> aliases;
    for (const auto& [old_p, new_p] : reverse_map) {
        if (old_p == new_p) continue;
        std::string mbody = masked_verilog(body);
        auto mtoks = tokenize_verilog(mbody);
        for (const auto& tk : mtoks) {
            if (tk == new_p) throw std::runtime_error("Native macro port alias collides with internal net: " + new_p);
        }
        // Find `^(input|output)\s+<old_p>\s*;`
        bool replaced_decl = false;
        size_t pos = 0;
        while (pos < body.size()) {
            size_t nl = body.find('\n', pos);
            size_t end_l = (nl == std::string::npos) ? body.size() : nl;
            std::string_view ln(body.data() + pos, end_l - pos);
            std::string tr = trim(ln);
            for (const char* dir_kw : {"input", "output"}) {
                size_t kw_len = std::strlen(dir_kw);
                if (starts_with(tr, dir_kw) && tr.size() > kw_len && std::isspace(static_cast<unsigned char>(tr[kw_len])) && ends_with(tr, ";")) {
                    std::string mid = trim(std::string_view(tr).substr(kw_len, tr.size() - kw_len - 1));
                    if (mid == old_p) {
                        std::string repl = std::string(dir_kw) + " " + new_p + ";";
                        aliases.push_back("wire " + old_p + ";\nassign " + (std::strcmp(dir_kw, "input") == 0 ? (old_p + " = " + new_p) : (new_p + " = " + old_p)) + ";");
                        body = body.substr(0, pos) + repl + body.substr(end_l);
                        replaced_decl = true;
                        break;
                    }
                }
            }
            if (replaced_decl) break;
            pos = (nl == std::string::npos) ? body.size() : (nl + 1);
        }
        if (!replaced_decl) throw std::runtime_error("Only scalar input/output macro renaming supported: " + old_p);
    }

    stop = body.find(';');
    std::string new_hdr;
    for (size_t i = 0; i < stop;) {
        unsigned char c = static_cast<unsigned char>(body[i]);
        if (std::isalpha(c) || c == '_' || c == '$') {
            size_t j = i + 1;
            while (j < stop && (std::isalnum(static_cast<unsigned char>(body[j])) || body[j] == '_' || body[j] == '$')) ++j;
            std::string id = body.substr(i, j - i);
            new_hdr += reverse_map.value_or(id, id);
            i = j;
        } else {
            new_hdr.push_back(body[i++]);
        }
    }
    body = new_hdr + body.substr(stop);

    size_t last_decl_end = 0;
    size_t lpos = 0;
    while (lpos < body.size()) {
        size_t nl = body.find('\n', lpos);
        size_t end_l = (nl == std::string::npos) ? body.size() : nl;
        std::string_view ln(body.data() + lpos, end_l - lpos);
        if (starts_with(ln, "input ") || starts_with(ln, "output ") || starts_with(ln, "inout ")
            || starts_with(ln, "input\t") || starts_with(ln, "output\t") || starts_with(ln, "inout\t")) {
            size_t semi = body.find(';', lpos);
            if (semi != std::string::npos) last_decl_end = semi + 1;
        }
        lpos = (nl == std::string::npos) ? body.size() : (nl + 1);
    }
    body = body.substr(0, last_decl_end) + "\n" + join_strings(aliases, "\n") + "\n" + body.substr(last_decl_end);
    std::string payload = native.substr(0, ma) + body + native.substr(mb);

    size_t ea = 0, eb = 0;
    bool found_e = false;
    for (const auto& u : verilog_units(exported_v)) {
        if (u.name == mod_name) { ea = u.start; eb = u.end; found_e = true; break; }
    }
    if (!found_e) throw std::runtime_error("Exported macro definition not found: " + mod_name);

    std::string old_exp(exported_v.substr(ea, eb - ea));
    std::string clean_exp = masked_verilog(old_exp);
    std::string head_exp = clean_exp.substr(0, clean_exp.find(';'));
    std::vector<std::string> exp_decls;
    size_t dpos = 0;
    while (dpos < clean_exp.size()) {
        size_t nl = clean_exp.find('\n', dpos);
        size_t end_l = (nl == std::string::npos) ? clean_exp.size() : nl;
        std::string lt = ltrim(std::string_view(clean_exp.data() + dpos, end_l - dpos));
        for (const char* kw : {"input", "output", "inout"}) {
            size_t kl = std::strlen(kw);
            if (starts_with(lt, kw) && lt.size() > kl && !std::isalnum(static_cast<unsigned char>(lt[kl])) && lt[kl] != '_') {
                size_t semi = clean_exp.find(';', dpos);
                if (semi != std::string::npos) {
                    exp_decls.push_back(clean_exp.substr(dpos, semi - dpos + 1));
                    end_l = std::max(end_l, semi);
                }
                break;
            }
        }
        dpos = (end_l >= clean_exp.size()) ? clean_exp.size() : (end_l + 1);
    }

    std::vector<std::string> io_ports;
    if (const JsonValue* eio = profile.get("external_io_ports"); eio && eio->is_array()) {
        for (const auto& v : eio->as_arr()) io_ports.push_back(v.as_str());
    }
    std::string repl_mod = "(* blackbox = 1, ng_pad_ports = \"" + join_strings(io_ports, " ") + "\" *) " + head_exp + ";\n"
                         + join_strings(exp_decls, "\n") + "\nendmodule";
    std::string new_design = std::string(exported_v.substr(0, ea)) + repl_mod + std::string(exported_v.substr(eb));

    JsonValue rec = JsonValue::object();
    rec["profile_sha256"] = sha256_file(profile_path);
    rec["native_netlist_sha256"] = profile["netlist_sha256"];
    rec["macro"] = mod_name;
    rec["rao_sha256"] = profile["rao_sha256"];
    rec["ports"] = profile["ports"];
    rec["scope"] = "Native kernel and protected payload retained. Scalar formal aliases added; no native kernel mapping performed by the open mapper.";
    rec["payload_sha256"] = sha256_str(payload);
    return {std::move(new_design), std::move(payload), std::move(rec)};
}

// ============================================================================
// Stage 2b: restore_verific_memories + verific_bridge_convert
// ============================================================================
namespace {

struct MemOffsetTerm {
    bool neg = false;        // true when term is `-(sub_expr - (base)) * stride`
    std::string sub_expr;    // e.g. `write_address[5:2]` or `{write_address[0:0],2'b00}`
    int base = 0;            // integer inside `-(base)`
    int stride = 1;          // positive integer after `*`
};

struct MemHelperSpec {
    std::string kind; // ReadPort or ClockedWritePort
    int aw = 0;
    int width = 0;
    int ram_bits = 0;
    bool is_minus_slice = false;
    std::vector<std::pair<int, int>> name_pairs;
    std::vector<MemOffsetTerm> terms;
};

std::string pad_escaped_verilog_tail(std::string s) {
    if (s.empty()) return s;
    size_t e = s.size();
    while (e > 0 && std::isspace(static_cast<unsigned char>(s[e - 1]))) --e;
    if (e == 0) return s;
    size_t b = e;
    while (b > 0 && !std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
    if (s[b] == '\\') {
        if (e == s.size()) s.push_back(' ');
        return s;
    }
    return s;
}

std::string join_verilog_tokens_safe(const std::vector<std::string>& toks) {
    std::string out;
    for (size_t i = 0; i < toks.size(); ++i) {
        if (i > 0) out.push_back(' ');
        out += toks[i];
        if (!toks[i].empty() && toks[i][0] == '\\' && i + 1 == toks.size()) {
            out.push_back(' ');
        }
    }
    return out;
}

std::string replace_ident_token_in_expr(std::string_view expr, std::string_view from_id, std::string_view to_id) {
    std::string out;
    out.reserve(expr.size() + 16);
    size_t i = 0;
    while (i < expr.size()) {
        if (expr.compare(i, from_id.size(), from_id) == 0) {
            bool lok = (i == 0) || !(std::isalnum(static_cast<unsigned char>(expr[i - 1])) || expr[i - 1] == '_' || expr[i - 1] == '$' || expr[i - 1] == '\\');
            size_t j = i + from_id.size();
            bool rok = (j == expr.size()) || !(std::isalnum(static_cast<unsigned char>(expr[j])) || expr[j] == '_' || expr[j] == '$');
            if (lok && rok) {
                out.append(to_id);
                i = j;
                continue;
            }
        }
        out.push_back(expr[i++]);
    }
    return out;
}

bool parse_signed_int_str(std::string_view s, int& out_v) {
    if (s.empty()) return false;
    size_t i = 0;
    if (s[i] == '-' || s[i] == '+') ++i;
    if (i >= s.size()) return false;
    for (; i < s.size(); ++i) {
        if (!std::isdigit(static_cast<unsigned char>(s[i]))) return false;
    }
    try {
        out_v = std::stoi(std::string(s));
        return true;
    } catch (...) {
        return false;
    }
}

bool parse_helper_name(std::string_view name, MemHelperSpec& out) {
    std::string prefix;
    if (starts_with(name, "ReadPort_")) prefix = "ReadPort";
    else if (starts_with(name, "ClockedWritePort_")) prefix = "ClockedWritePort";
    else return false;
    auto parts = split_char(name.substr(prefix.size() + 1), '_');
    if (parts.size() < 4 || (parts.size() % 2) != 0) return false;
    std::vector<int> nums;
    nums.reserve(parts.size());
    for (size_t i = 0; i < parts.size(); ++i) {
        int v = 0;
        if (!parse_signed_int_str(parts[i], v)) return false;
        nums.push_back(v);
    }
    if (nums[0] <= 0 || nums[1] <= 0) return false;
    out = MemHelperSpec{};
    out.kind = prefix;
    out.aw = nums[0];
    out.width = nums[1];
    for (size_t i = 2; i + 1 < nums.size(); i += 2) {
        out.name_pairs.emplace_back(nums[i], nums[i + 1]);
    }
    return !out.name_pairs.empty();
}

bool parse_ram_offset_terms(std::string_view expr_nospace, std::vector<MemOffsetTerm>& out_terms) {
    out_terms.clear();
    size_t p = 0;
    const size_t n = expr_nospace.size();
    while (p < n) {
        bool neg = false;
        if (expr_nospace[p] == '-') { neg = true; ++p; }
        else if (expr_nospace[p] == '+') { ++p; }
        else if (!out_terms.empty()) return false;

        if (p >= n || expr_nospace[p] != '(') return false;
        int depth = 1;
        size_t rp = p + 1;
        while (rp < n && depth > 0) {
            if (expr_nospace[rp] == '(') ++depth;
            else if (expr_nospace[rp] == ')') --depth;
            ++rp;
        }
        if (depth != 0) return false;
        std::string_view inner = expr_nospace.substr(p + 1, (rp - 1) - (p + 1));
        // `inner` ends with `-(<base_int>)`
        if (inner.empty() || inner.back() != ')') return false;
        int b_depth = 1;
        size_t lp_base = inner.size() - 1;
        while (lp_base > 0 && b_depth > 0) {
            --lp_base;
            if (inner[lp_base] == ')') ++b_depth;
            else if (inner[lp_base] == '(') --b_depth;
        }
        if (b_depth != 0 || lp_base == 0 || inner[lp_base - 1] != '-') return false;
        int base_v = 0;
        if (!parse_signed_int_str(inner.substr(lp_base + 1, (inner.size() - 1) - (lp_base + 1)), base_v)) return false;
        std::string sub_expr(inner.substr(0, lp_base - 1));
        if (sub_expr.empty()) return false;

        p = rp;
        if (p >= n || expr_nospace[p] != '*') return false;
        ++p;
        size_t st_s = p;
        while (p < n && std::isdigit(static_cast<unsigned char>(expr_nospace[p]))) ++p;
        int stride_v = 0;
        if (p == st_s || !parse_signed_int_str(expr_nospace.substr(st_s, p - st_s), stride_v) || stride_v <= 0) {
            return false;
        }
        out_terms.push_back(MemOffsetTerm{neg, std::move(sub_expr), base_v, stride_v});
    }
    return !out_terms.empty();
}

OrderedMap<JsonValue> extract_memory_attributes(std::string_view decl) {
    OrderedMap<JsonValue> attrs;
    std::unordered_set<std::string> styles;
    size_t p = 0;
    while ((p = decl.find("ram_style", p)) != std::string_view::npos) {
        bool lok = (p == 0) || !(std::isalnum(static_cast<unsigned char>(decl[p - 1])) || decl[p - 1] == '_');
        size_t k = p + 9;
        if (lok && (k == decl.size() || !(std::isalnum(static_cast<unsigned char>(decl[k])) || decl[k] == '_'))) {
            while (k < decl.size() && std::isspace(static_cast<unsigned char>(decl[k]))) ++k;
            if (k < decl.size() && decl[k] == '=') {
                ++k;
                while (k < decl.size() && std::isspace(static_cast<unsigned char>(decl[k]))) ++k;
                if (k < decl.size() && decl[k] == '"') {
                    size_t q = decl.find('"', k + 1);
                    if (q != std::string_view::npos) {
                        styles.emplace(decl.substr(k + 1, q - k - 1));
                    }
                }
            }
        }
        p += 9;
    }
    if (styles.size() > 1) throw std::runtime_error("Conflicting memory styles in declaration: " + std::string(trim(decl)));
    if (!styles.empty()) {
        std::string st = *styles.begin();
        if (st != "block" && st != "distributed" && st != "logic" && st != "auto" && st != "registers") {
            throw std::runtime_error("Unsupported memory style '" + st + "' in declaration: " + std::string(trim(decl)));
        }
        attrs.set("ram_style", (st == "registers") ? "logic" : st);
    }
    if (decl.find("syn_ramstyle") != std::string_view::npos && decl.find("\"registers\"") != std::string_view::npos) {
        std::string cur = attrs.contains("ram_style") ? attrs.at("ram_style").as_str() : "logic";
        if (cur != "logic" && cur != "auto") throw std::runtime_error("Conflicting register/block memory intent in declaration: " + std::string(trim(decl)));
        attrs.set("ram_style", "logic");
    }
    std::unordered_set<std::string> checks;
    p = 0;
    while ((p = decl.find("no_rw_check", p)) != std::string_view::npos) {
        bool lok = (p == 0) || !(std::isalnum(static_cast<unsigned char>(decl[p - 1])) || decl[p - 1] == '_');
        size_t k = p + 11;
        if (lok && (k == decl.size() || !(std::isalnum(static_cast<unsigned char>(decl[k])) || decl[k] == '_'))) {
            while (k < decl.size() && std::isspace(static_cast<unsigned char>(decl[k]))) ++k;
            if (k < decl.size() && decl[k] == '=') {
                ++k;
                while (k < decl.size() && std::isspace(static_cast<unsigned char>(decl[k]))) ++k;
                size_t ve = k;
                while (ve < decl.size() && !std::isspace(static_cast<unsigned char>(decl[ve])) && decl[ve] != ',' && decl[ve] != '*') ++ve;
                checks.emplace(decl.substr(k, ve - k));
            }
        }
        p += 11;
    }
    if (!checks.empty()) {
        if (checks.size() != 1 || (*checks.begin() != "0" && *checks.begin() != "1")) {
            throw std::runtime_error("Unsupported memory collision-check attribute in declaration: " + std::string(trim(decl)));
        }
        attrs.set("no_rw_check", std::stoi(*checks.begin()));
    }
    return attrs;
}

std::pair<std::string, JsonValue> restore_verific_memories(std::string_view text) {
    auto index = verilog_units(text);
    std::unordered_map<std::string, MemHelperSpec> helpers;

    for (const auto& u : index) {
        MemHelperSpec spec;
        if (!parse_helper_name(u.name, spec)) continue;
        std::string body = masked_verilog(text.substr(u.start, u.end - u.start));

        // Extract `Ram` port bit-width (`input [H:0] Ram;` / `output [H:0] Ram;` / `inout [H:0] Ram;` or scalar)
        int ram_bits = 0;
        for (const auto& ln : split_lines(body)) {
            std::string lt = trim(ln);
            bool is_dir = starts_with(lt, "input") || starts_with(lt, "output") || starts_with(lt, "inout");
            if (!is_dir || lt.find("Ram") == std::string::npos) continue;
            size_t lb = lt.find('['), rb = lt.find(']');
            size_t ram_p = lt.find("Ram", (rb != std::string::npos ? rb + 1 : 0));
            if (ram_p == std::string::npos) continue;
            size_t after_r = ram_p + 3;
            if (after_r < lt.size() && (std::isalnum(static_cast<unsigned char>(lt[after_r])) || lt[after_r] == '_')) continue;
            if (lb != std::string::npos && rb != std::string::npos && lb < rb && rb < ram_p) {
                std::string rng;
                for (size_t z = lb + 1; z < rb; ++z) {
                    if (!std::isspace(static_cast<unsigned char>(lt[z]))) rng.push_back(lt[z]);
                }
                size_t col = rng.find(':');
                int hi_v = 0, lo_v = -1;
                if (col != std::string::npos && parse_signed_int_str(std::string_view(rng).substr(0, col), hi_v)
                    && parse_signed_int_str(std::string_view(rng).substr(col + 1), lo_v) && lo_v == 0 && hi_v >= 0) {
                    ram_bits = hi_v + 1;
                    break;
                }
            } else if (lb == std::string::npos) {
                ram_bits = 1;
                break;
            }
        }
        if (ram_bits <= 0) {
            throw std::runtime_error("Could not parse Ram vector width in Verific memory helper '" + u.name + "'");
        }
        spec.ram_bits = ram_bits;

        // Parse `Ram[<expr> +: <width>]` or `Ram[<expr> -: <width>]` (ascending packed partial slices)
        size_t rp = body.find("Ram[");
        bool offset_ok = false;
        if (rp != std::string::npos) {
            size_t plus_col = body.find("+:", rp + 4);
            size_t minus_col = body.find("-:", rp + 4);
            size_t slice_op = std::string::npos;
            if (plus_col != std::string::npos && (minus_col == std::string::npos || plus_col < minus_col)) {
                slice_op = plus_col;
                spec.is_minus_slice = false;
            } else if (minus_col != std::string::npos) {
                slice_op = minus_col;
                spec.is_minus_slice = true;
            }
            if (slice_op != std::string::npos) {
                size_t rb = body.find(']', slice_op + 2);
                if (rb != std::string::npos) {
                    std::string w_part = trim(std::string_view(body).substr(slice_op + 2, rb - (slice_op + 2)));
                    if (w_part == std::to_string(spec.width)) {
                        std::string actual_expr;
                        for (size_t i = rp + 4; i < slice_op; ++i) {
                            if (!std::isspace(static_cast<unsigned char>(body[i]))) actual_expr.push_back(body[i]);
                        }
                        offset_ok = parse_ram_offset_terms(actual_expr, spec.terms);
                    }
                }
            }
        }
        if (!offset_ok) {
            throw std::runtime_error("Unrecognized Ram[offset +:/-: width] expression in Verific memory helper '" + u.name + "'");
        }

        // Validate suffix products of `spec.name_pairs` against `ram_bits`
        int64_t prod = 1;
        for (auto [pl, pr] : spec.name_pairs) {
            prod *= (static_cast<int64_t>(std::abs(pr - pl)) + 1);
        }
        if (prod != static_cast<int64_t>(ram_bits)) {
            throw std::runtime_error(
                "Memory helper '" + u.name + "' dimension product (" + std::to_string(prod)
                + ") disagrees with Ram port width (" + std::to_string(ram_bits) + ")"
            );
        }

        int always_cnt = 0;
        for (const auto& tk : tokenize_verilog(body)) {
            if (tk == "always") ++always_cnt;
        }
        if (always_cnt != 1) {
            throw std::runtime_error("Unexpected always process count (" + std::to_string(always_cnt) + ") in memory helper '" + u.name + "'");
        }
        helpers.emplace(u.name, std::move(spec));
    }

    struct SpanEdit {
        size_t start;
        size_t end;
        std::string repl;
    };
    std::vector<SpanEdit> changes;
    JsonValue records = JsonValue::array();

    for (const auto& u : index) {
        if (helpers.count(u.name)) {
            changes.push_back(SpanEdit{u.start, u.end, ""});
            continue;
        }
        std::string body(text.substr(u.start, u.end - u.start));
        if (body.find("ReadPort_") == std::string::npos && body.find("ClockedWritePort_") == std::string::npos) {
            continue;
        }

        struct HelperCall {
            size_t start;
            size_t end;
            std::string helper_name;
            OrderedMap<std::vector<std::string>> pins;
            MemHelperSpec spec;
        };
        OrderedMap<std::vector<HelperCall>> groups;

        size_t pos = 0;
        while (pos < body.size()) {
            size_t ls = pos;
            while (pos < body.size() && (body[pos] == ' ' || body[pos] == '\t')) ++pos;
            bool is_rp = starts_with(std::string_view(body).substr(pos), "ReadPort_");
            bool is_wp = !is_rp && starts_with(std::string_view(body).substr(pos), "ClockedWritePort_");
            if (is_rp || is_wp) {
                size_t he = pos;
                while (he < body.size() && (std::isalnum(static_cast<unsigned char>(body[he])) || body[he] == '_' || body[he] == '-')) ++he;
                std::string hname = body.substr(pos, he - pos);
                size_t k = he;
                while (k < body.size() && std::isspace(static_cast<unsigned char>(body[k]))) ++k;
                if (k > he && k < body.size()) {
                    size_t ie = k;
                    if (body[ie] == '\\') {
                        ++ie;
                        while (ie < body.size() && !std::isspace(static_cast<unsigned char>(body[ie]))) ++ie;
                    } else {
                        while (ie < body.size() && (std::isalnum(static_cast<unsigned char>(body[ie])) || body[ie] == '_' || body[ie] == '$')) ++ie;
                    }
                    if (ie > k) {
                        size_t p_open = ie;
                        while (p_open < body.size() && std::isspace(static_cast<unsigned char>(body[p_open]))) ++p_open;
                        if (p_open < body.size() && body[p_open] == '(') {
                            size_t semi = body.find(';', p_open + 1);
                            if (semi != std::string::npos) {
                                std::string_view stmt_tail(body.data() + p_open + 1, semi - (p_open + 1));
                                size_t c_start = stmt_tail.rfind("/*");
                                size_t search_lim = stmt_tail.size();
                                if (c_start != std::string_view::npos && stmt_tail.find("*/", c_start + 2) != std::string_view::npos) {
                                    size_t c_end = stmt_tail.find("*/", c_start + 2) + 2;
                                    if (trim(stmt_tail.substr(c_end)).empty()) {
                                        search_lim = c_start;
                                    }
                                }
                                std::string rtrimmed = rtrim(stmt_tail.substr(0, search_lim));
                                if (!rtrimmed.empty() && rtrimmed.back() == ')') {
                                    std::string args_inside = rtrimmed.substr(0, rtrimmed.size() - 1);
                                    auto hit = helpers.find(hname);
                                    if (hit == helpers.end()) {
                                        throw std::runtime_error("Unknown memory helper '" + hname + "' instantiated in module '" + u.name + "'");
                                    }
                                    auto conn_vec = parse_verilog_connections(tokenize_verilog(args_inside));
                                    OrderedMap<std::vector<std::string>> pmap;
                                    for (auto& [pn, ptoks] : conn_vec) pmap.set(pn, std::move(ptoks));
                                    std::vector<std::string> ram_toks;
                                    for (const auto& t : pmap.at("Ram")) {
                                        if (t != "{" && t != "}") ram_toks.push_back(t);
                                    }
                                    if (ram_toks.size() != 1 || !is_valid_verilog_ident(ram_toks[0])) {
                                        throw std::runtime_error("Memory helper '" + hname + "' in module '" + u.name + "' requires a single named Ram net");
                                    }
                                    groups[ram_toks[0]].push_back(HelperCall{ls, semi + 1, hname, std::move(pmap), hit->second});
                                    pos = semi + 1;
                                    if (pos < body.size() && body[pos] == '\r') ++pos;
                                    if (pos < body.size() && body[pos] == '\n') ++pos;
                                    continue;
                                }
                            }
                        }
                    }
                }
            }
            size_t nl = body.find('\n', ls);
            pos = (nl == std::string::npos) ? body.size() : (nl + 1);
        }

        if (groups.empty()) continue;

        std::unordered_set<std::string> body_tokens;
        for (const auto& tk : tokenize_verilog(masked_verilog(body))) body_tokens.insert(tk);

        std::vector<SpanEdit> edits;
        int group_idx = 0;
        for (const auto& [ram, calls] : groups) {
            const MemHelperSpec& spec0 = calls[0].spec;
            const int ram_bits = spec0.ram_bits;
            const size_t K = spec0.name_pairs.size();
            for (const auto& hc : calls) {
                if (hc.spec.ram_bits != ram_bits || hc.spec.name_pairs != spec0.name_pairs) {
                    throw std::runtime_error(
                        "Read/write memory helper geometry disagrees on net '" + ram + "' in module '" + u.name
                        + "' ('" + calls[0].helper_name + "' vs '" + hc.helper_name + "')"
                    );
                }
            }

            // Determine packed element width (`word_width`) and unpacked dimension count (`num_unpacked`) across all ports of `ram`:
            // - If any port has `terms.size() == K - 1` or `width > 1`, `name_pairs[K - 1]` is the packed word range (`word_width = |R_{K-1} - L_{K-1}| + 1`, `num_unpacked = K - 1`).
            // - Otherwise all ports are 1-bit scalar accesses with `terms.size() == K`, so all `K` pairs are unpacked dimensions of 1-bit elements (`word_width = 1`, `num_unpacked = K`).
            bool has_packed_pair = false;
            for (const auto& hc : calls) {
                if (hc.spec.terms.size() + 1 == K || hc.spec.width > 1) {
                    has_packed_pair = true;
                    break;
                }
            }
            if (has_packed_pair && K < 2) {
                throw std::runtime_error("Invalid packed memory helper dimensions on '" + ram + "' in module '" + u.name + "'");
            }
            const size_t num_unpacked = has_packed_pair ? (K - 1) : K;
            const int word_width = has_packed_pair
                ? (std::abs(spec0.name_pairs[K - 1].second - spec0.name_pairs[K - 1].first) + 1)
                : 1;
            const int total_words = ram_bits / word_width;
            const int total_hi = ram_bits - 1;

            // Validate every port's offset terms against unpacked dimensions + optional partial sub-word offset
            std::vector<int> exp_word_strides(num_unpacked, 1);
            for (size_t d = num_unpacked; d > 1; --d) {
                int span_prev = std::abs(spec0.name_pairs[d - 1].second - spec0.name_pairs[d - 1].first) + 1;
                exp_word_strides[d - 2] = exp_word_strides[d - 1] * span_prev;
            }
            for (const auto& hc : calls) {
                const auto& sp = hc.spec;
                bool is_partial = (sp.width != word_width);
                size_t exp_terms = num_unpacked + (is_partial ? 1 : 0);
                if (sp.width > word_width || sp.terms.size() != exp_terms) {
                    throw std::runtime_error(
                        "Unsupported memory port term layout in '" + hc.helper_name + "' on net '" + ram
                        + "' in module '" + u.name + "' (width=" + std::to_string(sp.width)
                        + ", word_width=" + std::to_string(word_width) + ", terms=" + std::to_string(sp.terms.size()) + ")"
                    );
                }
                for (size_t d = 0; d < num_unpacked; ++d) {
                    auto [dl, dr] = spec0.name_pairs[d];
                    const auto& tm = sp.terms[d];
                    bool exp_neg = (dl < dr);
                    if (tm.base != dr || tm.stride != exp_word_strides[d] * word_width || (dl != dr && tm.neg != exp_neg)) {
                        throw std::runtime_error(
                            "Unexpected unpacked offset term " + std::to_string(d) + " in '" + hc.helper_name
                            + "' on net '" + ram + "' in module '" + u.name + "'"
                        );
                    }
                }
                if (is_partial) {
                    auto [pl, pr] = spec0.name_pairs[K - 1];
                    const auto& ctm = sp.terms[num_unpacked];
                    bool exp_cneg = (pl < pr);
                    if (ctm.base != pr || ctm.stride != 1 || (pl != pr && ctm.neg != exp_cneg)) {
                        throw std::runtime_error(
                            "Unexpected packed bit-slice offset term in '" + hc.helper_name
                            + "' on net '" + ram + "' in module '" + u.name + "'"
                        );
                    }
                }
            }

            std::string array_nm = "ng_memory_" + std::to_string(group_idx++);
            while (body_tokens.count(array_nm)) {
                array_nm = "ng_memory_" + std::to_string(group_idx++);
            }

            // Find declaration `^[ \t]*(?:wire|reg)(?:\s*\[\s*<total_hi>\s*:\s*0\s*\])?\s*<ram>(?=\s|/|;)\s*[^;]*;`
            size_t decl_s = std::string::npos, decl_e = std::string::npos;
            size_t lp = 0;
            while (lp < body.size()) {
                size_t ls = lp;
                size_t k = ls;
                while (k < body.size() && (body[k] == ' ' || body[k] == '\t')) ++k;
                std::string kw;
                if (starts_with(std::string_view(body).substr(k), "wire")) kw = "wire";
                else if (starts_with(std::string_view(body).substr(k), "reg")) kw = "reg";
                if (!kw.empty() && k + kw.size() < body.size() &&
                    !(std::isalnum(static_cast<unsigned char>(body[k + kw.size()])) || body[k + kw.size()] == '_')) {
                    size_t m = k + kw.size();
                    while (m < body.size() && std::isspace(static_cast<unsigned char>(body[m]))) ++m;
                    size_t semi = body.find(';', m);
                    if (semi != std::string::npos) {
                        bool range_ok = false;
                        size_t rn = m;
                        if (m < semi && body[m] == '[') {
                            size_t rb = body.find(']', m + 1);
                            if (rb != std::string::npos && rb < semi) {
                                std::string rng;
                                for (size_t z = m + 1; z < rb; ++z) {
                                    if (!std::isspace(static_cast<unsigned char>(body[z]))) rng.push_back(body[z]);
                                }
                                if (rng == std::to_string(total_hi) + ":0") {
                                    range_ok = true;
                                    rn = rb + 1;
                                    while (rn < semi && std::isspace(static_cast<unsigned char>(body[rn]))) ++rn;
                                }
                            }
                        } else if (total_hi == 0) {
                            range_ok = true;
                        }
                        if (range_ok && body.compare(rn, ram.size(), ram) == 0) {
                            size_t after_r = rn + ram.size();
                            bool end_ok = (!ram.empty() && ram[0] == '\\')
                                ? (after_r <= semi && std::isspace(static_cast<unsigned char>(body[after_r])))
                                : (after_r <= semi && (std::isspace(static_cast<unsigned char>(body[after_r])) || body[after_r] == '/' || body[after_r] == ';'));
                            if (end_ok) {
                                decl_s = ls;
                                decl_e = semi + 1;
                                break;
                            }
                        }
                    }
                }
                size_t nl = body.find('\n', ls);
                lp = (nl == std::string::npos) ? body.size() : (nl + 1);
            }
            if (decl_s == std::string::npos) {
                throw std::runtime_error(
                    "Memory vector declaration not recognized for net '" + ram + "' (expected [" + std::to_string(total_hi)
                    + ":0]) in module '" + u.name + "'"
                );
            }
            std::string decl_text = body.substr(decl_s, decl_e - decl_s);

            // Check remaining occurrences of `ram` after removing calls & declaration
            std::vector<std::pair<size_t, size_t>> rem_spans;
            rem_spans.emplace_back(decl_s, decl_e);
            for (const auto& hc : calls) rem_spans.emplace_back(hc.start, hc.end);
            std::sort(rem_spans.begin(), rem_spans.end());
            std::string remaining;
            size_t cur_c = 0;
            for (auto [rs, re] : rem_spans) {
                remaining.append(body, cur_c, rs - cur_c);
                cur_c = re;
            }
            remaining.append(body, cur_c, body.size() - cur_c);
            std::string rem_masked = masked_verilog(remaining);
            for (size_t rp_pos = 0; (rp_pos = rem_masked.find(ram, rp_pos)) != std::string::npos; rp_pos += ram.size()) {
                bool prev_ok = (rp_pos == 0) || std::isspace(static_cast<unsigned char>(rem_masked[rp_pos - 1]))
                            || (ram[0] != '\\' && (rem_masked[rp_pos - 1] == '(' || rem_masked[rp_pos - 1] == ',' || rem_masked[rp_pos - 1] == '{'));
                size_t after_i = rp_pos + ram.size();
                bool next_ok = (after_i < rem_masked.size()) && (
                    (!ram.empty() && ram[0] == '\\')
                        ? std::isspace(static_cast<unsigned char>(rem_masked[after_i]))
                        : (std::isspace(static_cast<unsigned char>(rem_masked[after_i])) ||
                           rem_masked[after_i] == ';' || rem_masked[after_i] == ',' ||
                           rem_masked[after_i] == ')' || rem_masked[after_i] == ']' || rem_masked[after_i] == '[')
                );
                if (prev_ok && next_ok) {
                    throw std::runtime_error("Memory vector '" + ram + "' in module '" + u.name + "' has non-port direct uses outside ReadPort/ClockedWritePort");
                }
            }

            auto mem_attrs = extract_memory_attributes(decl_text);
            std::string style_prefix;
            if (!mem_attrs.empty()) {
                std::vector<std::string> ap;
                for (const auto& [ak, av] : mem_attrs) {
                    ap.push_back(ak + " = " + (av.is_string() ? ("\"" + av.as_str() + "\"") : std::to_string(av.as_i64())));
                }
                style_prefix = "(* " + join_strings(ap, ", ") + " *) ";
            }

            // For 1D unpacked arrays (`num_unpacked == 1`), preserve original `[L_0:R_0]` bounds directly.
            // For multi-dimensional unpacked arrays (`num_unpacked >= 2`), flatten unpacked dimensions into `[0:total_words-1]`
            // choosing LSB-order when all unpacked dimensions are descending `[Hi:0]`, and MSB-order otherwise so power-of-2
            // 0-based multi-D arrays index via pure bit concatenation with zero arithmetic overhead.
            const bool is_1d = (num_unpacked == 1);
            bool multi_use_lsb_order = (!is_1d);
            if (!is_1d) {
                for (size_t d = 0; d < num_unpacked; ++d) {
                    auto [dl, dr] = spec0.name_pairs[d];
                    if (!(dl >= dr && dr == 0)) { multi_use_lsb_order = false; break; }
                }
            }
            const int decl_left  = is_1d ? spec0.name_pairs[0].first  : 0;
            const int decl_right = is_1d ? spec0.name_pairs[0].second : (total_words - 1);
            std::string decl_repl = style_prefix + "reg [" + std::to_string(word_width - 1) + ":0] " + array_nm
                                  + " [" + std::to_string(decl_left) + ":" + std::to_string(decl_right) + "];";

            // Extract RAM initializer from either `/* Init Val: <bits> */` or `:: initial value of the RAM: <N>'b<bits> */`
            std::string iv_bits;
            {
                size_t iv_p = decl_text.find("/* Init Val:");
                if (iv_p != std::string::npos) {
                    size_t iv_end = decl_text.find("*/", iv_p + 12);
                    if (iv_end != std::string::npos) {
                        iv_bits = to_lower_ascii(trim(std::string_view(decl_text).substr(iv_p + 12, iv_end - (iv_p + 12))));
                    }
                } else {
                    size_t rv_p = decl_text.find("initial value of the RAM:");
                    if (rv_p != std::string::npos) {
                        size_t rv_end = decl_text.find("*/", rv_p + 25);
                        if (rv_end != std::string::npos) {
                            std::string raw_lit = trim(std::string_view(decl_text).substr(rv_p + 25, rv_end - (rv_p + 25)));
                            size_t q = raw_lit.find('\'');
                            if (q != std::string::npos && q + 2 <= raw_lit.size() &&
                                (raw_lit[q + 1] == 'b' || raw_lit[q + 1] == 'B')) {
                                for (size_t bi = q + 2; bi < raw_lit.size(); ++bi) {
                                    char ch = static_cast<char>(std::tolower(static_cast<unsigned char>(raw_lit[bi])));
                                    if (ch == '_') continue;
                                    iv_bits.push_back(ch);
                                }
                            }
                        }
                    }
                }
            }

            bool has_init = false;
            if (!iv_bits.empty()) {
                if (static_cast<int>(iv_bits.size()) != ram_bits) {
                    throw std::runtime_error(
                        "Memory initializer width mismatch on '" + ram + "' in module '" + u.name
                        + "': got " + std::to_string(iv_bits.size()) + " bits, expected " + std::to_string(ram_bits)
                    );
                }
                std::vector<std::string> init_stmts;
                int step = (decl_left <= decl_right) ? 1 : -1;
                int row_num = decl_left;
                for (int i = 0; i < total_words; ++i, row_num += step) {
                    int src_word_from_msb = (!is_1d && multi_use_lsb_order) ? (total_words - 1 - i) : i;
                    std::string_view wslice(iv_bits.data() + static_cast<size_t>(src_word_from_msb * word_width), static_cast<size_t>(word_width));
                    bool any_def = false, any_undef = false;
                    for (char c : wslice) {
                        if (c == '0' || c == '1') any_def = true;
                        else any_undef = true;
                    }
                    if (!any_def) continue; // Leave completely undefined ('x'/'z') words uninitialized
                    if (!any_undef) {
                        init_stmts.push_back(
                            array_nm + "[" + std::to_string(row_num) + "] = " + std::to_string(word_width) + "'b" + std::string(wslice) + ";"
                        );
                    } else {
                        for (int bit_i = 0; bit_i < word_width; ++bit_i) {
                            char bc = wslice[static_cast<size_t>(word_width - 1 - bit_i)];
                            if (bc == '0' || bc == '1') {
                                init_stmts.push_back(
                                    array_nm + "[" + std::to_string(row_num) + "][" + std::to_string(bit_i) + "] = 1'b" + std::string(1, bc) + ";"
                                );
                            }
                        }
                    }
                }
                if (!init_stmts.empty()) {
                    has_init = true;
                    decl_repl += "\ninitial begin\n" + join_strings(init_stmts, "\n") + "\nend";
                }
            }
            edits.push_back(SpanEdit{decl_s, decl_e, std::move(decl_repl)});

            for (size_t pi = 0; pi < calls.size(); ++pi) {
                const auto& hc = calls[pi];
                const auto& sp = hc.spec;
                std::string address = array_nm + "_addr_" + std::to_string(pi);
                std::string pfx = (sp.kind == "ClockedWritePort") ? "write" : "read";
                std::string raw_addr_port = pfx + "_address";
                auto expr = [&](const std::string& pin_nm) {
                    return pad_escaped_verilog_tail(join_verilog_tokens_safe(hc.pins.at(pin_nm)));
                };
                std::string addr_expr = expr(raw_addr_port);
                std::string call_repl = "wire [" + std::to_string(sp.aw - 1) + ":0] " + address + " = " + addr_expr + ";\n";

                std::string row_s;
                std::string row_bounds_guard;
                if (is_1d) {
                    row_s = replace_ident_token_in_expr(sp.terms[0].sub_expr, raw_addr_port, address);
                } else {
                    // Check if all `num_unpacked` dimensions form a contiguous pure bit-slice of `address`
                    bool pure_concat = true;
                    int expected_next_hi = -1, first_hi = -1, last_lo = -1;
                    for (size_t d = 0; d < num_unpacked; ++d) {
                        auto [dl, dr] = spec0.name_pairs[d];
                        int span_d = std::abs(dr - dl) + 1;
                        bool zero_based = multi_use_lsb_order ? (dr == 0 && !sp.terms[d].neg) : (dl == 0 && sp.terms[d].neg);
                        std::string sub_d = replace_ident_token_in_expr(sp.terms[d].sub_expr, raw_addr_port, address);
                        // Check `address[hi:lo]`
                        int hi_d = -1, lo_d = -1;
                        if (zero_based && starts_with(sub_d, address + "[") && sub_d.back() == ']') {
                            std::string inside = sub_d.substr(address.size() + 1, sub_d.size() - (address.size() + 2));
                            size_t col = inside.find(':');
                            if (col != std::string::npos && parse_signed_int_str(std::string_view(inside).substr(0, col), hi_d)
                                && parse_signed_int_str(std::string_view(inside).substr(col + 1), lo_d) && hi_d >= lo_d) {
                                int w_d = hi_d - lo_d + 1;
                                if ((1 << w_d) == span_d && (d == 0 || hi_d == expected_next_hi)) {
                                    if (d == 0) first_hi = hi_d;
                                    last_lo = lo_d;
                                    expected_next_hi = lo_d - 1;
                                    continue;
                                }
                            }
                        }
                        pure_concat = false;
                        break;
                    }
                    if (pure_concat && first_hi >= last_lo && last_lo >= 0) {
                        row_s = address + "[" + std::to_string(first_hi) + ":" + std::to_string(last_lo) + "]";
                    } else {
                        std::vector<std::string> sum_parts;
                        bool may_exceed = false;
                        for (size_t d = 0; d < num_unpacked; ++d) {
                            auto [dl, dr] = spec0.name_pairs[d];
                            int span_d = std::abs(dr - dl) + 1;
                            if ((span_d & (span_d - 1)) != 0 || dl != 0 || dr < 0) may_exceed = true;
                            std::string sub_d = replace_ident_token_in_expr(sp.terms[d].sub_expr, raw_addr_port, address);
                            std::string rel_d;
                            if (multi_use_lsb_order) {
                                rel_d = sp.terms[d].neg
                                    ? ("(" + std::to_string(dr) + " - (" + sub_d + "))")
                                    : ((dr == 0) ? sub_d : ("((" + sub_d + ") - " + std::to_string(dr) + ")"));
                            } else {
                                rel_d = sp.terms[d].neg
                                    ? ((dl == 0) ? sub_d : ("((" + sub_d + ") - " + std::to_string(dl) + ")"))
                                    : ("(" + std::to_string(dl) + " - (" + sub_d + "))");
                            }
                            int ws = exp_word_strides[d];
                            sum_parts.push_back((ws == 1) ? rel_d : ("(" + rel_d + " * " + std::to_string(ws) + ")"));
                        }
                        row_s = join_strings(sum_parts, " + ");
                        if (may_exceed && pfx == "write") {
                            row_bounds_guard = " && ((" + row_s + ") < " + std::to_string(total_words) + ")";
                        }
                    }
                }

                std::string target = array_nm + "[" + row_s + "]";
                std::string extra_en_guard = row_bounds_guard;
                const bool is_partial = (sp.width != word_width);
                if (is_partial) {
                    const auto& ctm = sp.terms[num_unpacked];
                    std::string col_sub = replace_ident_token_in_expr(ctm.sub_expr, raw_addr_port, address);
                    std::string col_idx = ctm.neg
                        ? ("(" + std::to_string(ctm.base) + " - (" + col_sub + "))")
                        : ((ctm.base == 0) ? col_sub : ("((" + col_sub + ") - " + std::to_string(ctm.base) + ")"));
                    if (sp.width == 1) {
                        target += "[" + col_idx + "]";
                    } else if (sp.is_minus_slice) {
                        target += "[" + col_idx + " -: " + std::to_string(sp.width) + "]";
                    } else {
                        target += "[" + col_idx + " +: " + std::to_string(sp.width) + "]";
                    }
                    if (pfx == "write") {
                        if (sp.is_minus_slice) {
                            extra_en_guard += " && ((" + col_idx + ") >= " + std::to_string(sp.width - 1)
                                            + ") && ((" + col_idx + ") < " + std::to_string(word_width) + ")";
                        } else {
                            extra_en_guard += " && ((" + col_idx + ") <= " + std::to_string(word_width - sp.width) + ")";
                        }
                    }
                }
                if (pfx == "write") {
                    std::string we_expr = expr("write_enable");
                    std::string we_cond = extra_en_guard.empty()
                        ? we_expr
                        : ("(" + we_expr + ")" + extra_en_guard);
                    call_repl += "always @(posedge " + expr("clk") + ") if (" + we_cond + ") " + target + " <= " + expr("write_data") + ";";
                } else {
                    std::string temp = array_nm + "_read_" + std::to_string(pi);
                    call_repl += "reg [" + std::to_string(sp.width - 1) + ":0] " + temp + ";\n"
                               + "always @* if (" + expr("read_enable") + ") " + temp + " = " + target + ";\n"
                               + "assign " + expr("read_data") + " = " + temp + ";";
                }
                edits.push_back(SpanEdit{hc.start, hc.end, std::move(call_repl)});
            }

            JsonValue rec = JsonValue::object();
            rec["module"] = u.name;
            rec["net"] = ram;
            rec["array"] = array_nm;
            rec["depth"] = total_words;
            rec["width"] = word_width;
            rec["unpacked_dimensions"] = static_cast<int64_t>(num_unpacked);
            rec["ports"] = static_cast<int64_t>(calls.size());
            rec["initialized"] = has_init;
            rec["restored_attributes"] = JsonValue(mem_attrs);
            records.push_back(std::move(rec));
        }

        std::sort(edits.begin(), edits.end(), [](const SpanEdit& a, const SpanEdit& b) { return a.start > b.start; });
        for (const auto& ed : edits) {
            body = body.substr(0, ed.start) + ed.repl + body.substr(ed.end);
        }
        changes.push_back(SpanEdit{u.start, u.end, std::move(body)});
    }

    std::sort(changes.begin(), changes.end(), [](const SpanEdit& a, const SpanEdit& b) { return a.start < b.start; });
    std::string out_text;
    out_text.reserve(text.size());
    size_t cursor = 0;
    for (const auto& ch : changes) {
        out_text.append(text.data() + cursor, ch.start - cursor);
        out_text.append(ch.repl);
        cursor = ch.end;
    }
    out_text.append(text.data() + cursor, text.size() - cursor);
    return {std::move(out_text), std::move(records)};
}

std::string strip_all_ws(std::string_view s) {
    std::string r;
    r.reserve(s.size());
    for (char c : s) {
        if (!std::isspace(static_cast<unsigned char>(c))) r.push_back(c);
    }
    return r;
}

bool extract_named_port_arg(std::string_view stmt, std::string_view port_name, std::string& out_arg) {
    std::string needle = "." + std::string(port_name);
    bool in_esc_scan = false;
    for (size_t p = 0; p < stmt.size(); ++p) {
        char ch_p = stmt[p];
        if (in_esc_scan) {
            if (std::isspace(static_cast<unsigned char>(ch_p))) in_esc_scan = false;
            continue;
        }
        if (ch_p == '\\') {
            in_esc_scan = true;
            continue;
        }
        if (stmt.compare(p, needle.size(), needle) != 0) continue;
        if (p > 0 && (std::isalnum(static_cast<unsigned char>(stmt[p - 1])) || stmt[p - 1] == '_' || stmt[p - 1] == '$')) {
            p += needle.size() - 1;
            continue;
        }
        size_t k = p + needle.size();
        while (k < stmt.size() && std::isspace(static_cast<unsigned char>(stmt[k]))) ++k;
        if (k >= stmt.size() || stmt[k] != '(') {
            p += needle.size() - 1;
            continue;
        }
        size_t start = k + 1;
        int depth = 1;
        bool in_esc_arg = false;
        size_t i = start;
        for (; i < stmt.size(); ++i) {
            char c = stmt[i];
            if (in_esc_arg) {
                if (std::isspace(static_cast<unsigned char>(c))) in_esc_arg = false;
                continue;
            }
            if (c == '\\') {
                in_esc_arg = true;
                continue;
            }
            if (c == '(') ++depth;
            else if (c == ')') {
                if (--depth == 0) break;
            }
        }
        if (depth != 0) return false;
        out_arg = pad_escaped_verilog_tail(trim(stmt.substr(start, i - start)));
        return true;
    }
    return false;
}

std::string normalize_verific_decoder_selects(const std::string& text) {
    if (text.find("Decoder_") == std::string::npos && text.find("Select_") == std::string::npos) return text;

    struct ModSpanEdit {
        size_t start = 0;
        size_t end = 0;
        std::string repl;
    };
    std::unordered_set<int> valid_decoders;
    std::unordered_set<int> valid_selects;
    std::unordered_set<std::string> existing_modules;
    std::vector<ModSpanEdit> edits;

    auto units = verilog_units(text);
    for (const auto& u : units) {
        existing_modules.insert(u.name);
        std::string_view raw_u(text.data() + u.start, u.end - u.start);
        if (starts_with(u.name, "Decoder_")) {
            std::string num_s = u.name.substr(8);
            bool all_dig = !num_s.empty();
            for (char c : num_s) if (!std::isdigit(static_cast<unsigned char>(c))) all_dig = false;
            if (!all_dig) continue;
            int w = std::stoi(num_s);
            if (w < 1 || w > 10) continue;
            int m_sz = 1 << w;
            std::string nw = strip_all_ws(masked_verilog(raw_u));
            std::string exp1 = "module" + u.name + "(i,o);input[" + std::to_string(w - 1) + ":0]i;output[" + std::to_string(m_sz - 1)
                             + ":0]o;reg[" + std::to_string(m_sz - 1) + ":0]o;always@(i)begino=0;o[i]=1'b1;endendmodule";
            if (nw == exp1) {
                valid_decoders.insert(w);
                std::string repl = "module " + u.name + " (i, o);\n"
                                 + "    input [" + std::to_string(w - 1) + ":0] i;\n"
                                 + "    output [" + std::to_string(m_sz - 1) + ":0] o;\n";
                for (int k = 0; k < m_sz; ++k) {
                    repl += "    assign o[" + std::to_string(k) + "] = (i == " + std::to_string(w) + "'d" + std::to_string(k) + ");\n";
                }
                repl += "endmodule";
                edits.push_back(ModSpanEdit{u.start, u.end, std::move(repl)});
            }
        } else if (starts_with(u.name, "Select_")) {
            std::string num_s = u.name.substr(7);
            bool all_dig = !num_s.empty();
            for (char c : num_s) if (!std::isdigit(static_cast<unsigned char>(c))) all_dig = false;
            if (!all_dig) continue;
            int m_sz = std::stoi(num_s);
            if (m_sz < 2 || m_sz > 1024) continue;
            std::string nw = strip_all_ws(masked_verilog(raw_u));
            std::string exp_s = "module" + u.name + "(sel,data,o);input[" + std::to_string(m_sz - 1) + ":0]sel;input["
                              + std::to_string(m_sz - 1) + ":0]data;outputo;assigno=|(sel&data);endmodule";
            if (nw == exp_s) valid_selects.insert(m_sz);
        } else if (starts_with(u.name, "Mux_") && ends_with(u.name, "u")) {
            std::string body_nm = u.name.substr(4, u.name.size() - 5);
            size_t sep = body_nm.find("u_");
            if (sep != std::string::npos && sep > 0 && sep + 2 < body_nm.size()) {
                std::string ws_s = body_nm.substr(0, sep), ms_s = body_nm.substr(sep + 2);
                bool ok_d = true;
                for (char c : ws_s) if (!std::isdigit(static_cast<unsigned char>(c))) ok_d = false;
                for (char c : ms_s) if (!std::isdigit(static_cast<unsigned char>(c))) ok_d = false;
                if (ok_d) {
                    int w = std::stoi(ws_s), m_sz = std::stoi(ms_s);
                    if (w >= 1 && w <= 8 && m_sz == (1 << w)) {
                        std::string nw = strip_all_ws(masked_verilog(raw_u));
                        std::string exp_m = "module" + u.name + "(sel,data,o);input[" + std::to_string(w - 1) + ":0]sel;input["
                                          + std::to_string(m_sz - 1) + ":0]data;outputo;assigno=data[sel];endmodule";
                        if (nw == exp_m) {
                            std::vector<std::string> nodes(static_cast<size_t>(m_sz));
                            for (int i = 0; i < m_sz; ++i) nodes[static_cast<size_t>(i)] = "data[" + std::to_string(i) + "]";
                            for (int lvl = 0; lvl < w; ++lvl) {
                                int half = static_cast<int>(nodes.size()) / 2;
                                std::vector<std::string> nxt(static_cast<size_t>(half));
                                for (int i = 0; i < half; ++i) {
                                    nxt[static_cast<size_t>(i)] = "(sel[" + std::to_string(lvl) + "] ? "
                                        + nodes[static_cast<size_t>(2 * i + 1)] + " : "
                                        + nodes[static_cast<size_t>(2 * i)] + ")";
                                }
                                nodes = std::move(nxt);
                            }
                            std::string repl = "module " + u.name + " (sel, data, o);\n"
                                             + "    input [" + std::to_string(w - 1) + ":0] sel;\n"
                                             + "    input [" + std::to_string(m_sz - 1) + ":0] data;\n"
                                             + "    output o;\n"
                                             + "    assign o = " + nodes[0] + ";\n"
                                             + "endmodule";
                            edits.push_back(ModSpanEdit{u.start, u.end, std::move(repl)});
                        }
                    }
                }
            }
        }
    }

    if (valid_decoders.empty() && valid_selects.empty() && edits.empty()) return text;

    struct PrimGate {
        std::string op;
        std::vector<std::string> ins;
    };
    std::set<std::pair<int, int>> needed_muxes;
    std::set<int> needed_oh, needed_oh0;

    for (const auto& u : units) {
        if (starts_with(u.name, "Decoder_") || starts_with(u.name, "Select_")) continue;
        std::string_view raw_u(text.data() + u.start, u.end - u.start);
        if (raw_u.find("Select_") == std::string_view::npos) continue;

        std::string clean_u = masked_verilog(raw_u);
        std::map<std::pair<int, std::string>, std::pair<int, std::string>> dec_bus_map;
        std::set<std::pair<int, std::string>> ambig_dec;
        std::unordered_map<std::string, PrimGate> gate_drv;
        std::unordered_set<std::string> ambig_gate_out;

        // Scan `;`-delimited statements inside module u for Decoder_<W> and Verilog gate primitives
        size_t spos = clean_u.find(';');
        if (spos != std::string::npos) spos += 1;
        while (spos != std::string::npos && spos < clean_u.size()) {
            size_t semi = clean_u.find(';', spos);
            if (semi == std::string::npos) break;
            std::string_view st_clean(clean_u.data() + spos, semi - spos);
            size_t k = 0;
            while (k < st_clean.size() && std::isspace(static_cast<unsigned char>(st_clean[k]))) ++k;
            if (k < st_clean.size() && starts_with(st_clean.substr(k), "Decoder_")) {
                size_t te = k + 8;
                while (te < st_clean.size() && std::isdigit(static_cast<unsigned char>(st_clean[te]))) ++te;
                if (te > k + 8 && te < st_clean.size() && std::isspace(static_cast<unsigned char>(st_clean[te]))) {
                    int w = std::stoi(std::string(st_clean.substr(k + 8, te - (k + 8))));
                    if (valid_decoders.count(w)) {
                        std::string_view st_orig(raw_u.data() + spos + k, semi - (spos + k));
                        std::string i_arg, o_arg;
                        if (extract_named_port_arg(st_orig, "i", i_arg) && extract_named_port_arg(st_orig, "o", o_arg)) {
                            auto key = std::make_pair(1 << w, strip_all_ws(o_arg));
                            if (dec_bus_map.count(key) && dec_bus_map[key].second != i_arg) {
                                ambig_dec.insert(key);
                            } else {
                                dec_bus_map[key] = {w, i_arg};
                            }
                        }
                    }
                }
            } else if (k < st_clean.size()) {
                std::string_view rem = st_clean.substr(k);
                static const char* GATE_OPS[] = {"not", "buf", "and", "or", "nor", "nand", "xor", "xnor"};
                for (const char* gop : GATE_OPS) {
                    size_t gl = std::strlen(gop);
                    if (starts_with(rem, gop) && rem.size() > gl && (std::isspace(static_cast<unsigned char>(rem[gl])) || rem[gl] == '(')) {
                        size_t lp = rem.find('(', gl);
                        size_t rp = rem.rfind(')');
                        if (lp != std::string_view::npos && rp != std::string_view::npos && rp > lp) {
                            std::string_view orig_args(raw_u.data() + spos + k + lp + 1, rp - (lp + 1));
                            // Ensure no nested parentheses or braces inside primitive gate argument list
                            if (orig_args.find('(') == std::string_view::npos && orig_args.find('{') == std::string_view::npos) {
                                auto parts = split_char(orig_args, ',');
                                if (parts.size() >= 2) {
                                    std::string out_w = strip_all_ws(parts[0]);
                                    if (((std::strcmp(gop, "not") == 0 || std::strcmp(gop, "buf") == 0) && parts.size() != 2) || out_w.empty()) {
                                        break;
                                    }
                                    std::vector<std::string> gins;
                                    gins.reserve(parts.size() - 1);
                                    for (size_t pi = 1; pi < parts.size(); ++pi) {
                                        std::string iw = strip_all_ws(parts[pi]);
                                        if (!iw.empty()) gins.push_back(std::move(iw));
                                    }
                                    if (gins.size() + 1 == parts.size()) {
                                        if (gate_drv.count(out_w)) ambig_gate_out.insert(out_w);
                                        else gate_drv[out_w] = PrimGate{gop, std::move(gins)};
                                    }
                                }
                            }
                        }
                        break;
                    }
                }
            }
            spos = semi + 1;
        }
        for (const auto& ak : ambig_dec) dec_bus_map.erase(ak);
        for (const auto& aw : ambig_gate_out) gate_drv.erase(aw);

        // Truth-table prover for Select_<M> one-hot (`1`) or one-hot-zero (`2`) over <= 8 cone leaves
        std::unordered_map<std::string, int> sel_proof_cache;
        auto prove_select_onehot = [&](int m_sz, std::string_view sel_arg) -> int {
            if (m_sz < 2 || m_sz > 32) return 0;
            std::string key = std::to_string(m_sz) + ":" + strip_all_ws(sel_arg);
            if (auto cit = sel_proof_cache.find(key); cit != sel_proof_cache.end()) return cit->second;

            std::string s_tr = trim(sel_arg);
            if (s_tr.size() < 3 || s_tr.front() != '{' || s_tr.back() != '}') return (sel_proof_cache[key] = 0);
            std::string_view inner(s_tr.data() + 1, s_tr.size() - 2);
            if (inner.find('{') != std::string_view::npos || inner.find('}') != std::string_view::npos) return (sel_proof_cache[key] = 0);
            auto raw_items = split_char(inner, ',');
            if (static_cast<int>(raw_items.size()) != m_sz) return (sel_proof_cache[key] = 0);

            std::vector<std::string> s_sigs;
            s_sigs.reserve(raw_items.size());
            for (const auto& it : raw_items) {
                std::string sw = strip_all_ws(it);
                if (sw.empty()) return (sel_proof_cache[key] = 0);
                s_sigs.push_back(std::move(sw));
            }

            std::vector<std::string> leaves;
            std::unordered_map<std::string, int> leaf_idx;
            std::unordered_set<std::string> visiting, visited;
            bool cone_ok = true;

            std::function<void(const std::string&)> collect = [&](const std::string& w) {
                if (!cone_ok || w == "1'b0" || w == "1'b1" || w == "0" || w == "1") return;
                if (visiting.count(w)) { cone_ok = false; return; }
                if (visited.count(w)) return;
                auto git = gate_drv.find(w);
                if (git == gate_drv.end()) {
                    if (!leaf_idx.count(w)) {
                        if (leaves.size() >= 8) { cone_ok = false; return; }
                        leaf_idx[w] = static_cast<int>(leaves.size());
                        leaves.push_back(w);
                    }
                    visited.insert(w);
                    return;
                }
                visiting.insert(w);
                for (const auto& inp : git->second.ins) {
                    collect(inp);
                    if (!cone_ok) break;
                }
                visiting.erase(w);
                visited.insert(w);
            };
            for (const auto& sw : s_sigs) {
                collect(sw);
                if (!cone_ok) break;
            }
            if (!cone_ok) return (sel_proof_cache[key] = 0);

            int n_leaves = static_cast<int>(leaves.size());
            int n_rows = 1 << n_leaves;
            using TT256 = std::array<uint64_t, 4>;
            TT256 mask_all{0, 0, 0, 0};
            for (int r = 0; r < n_rows; ++r) mask_all[static_cast<size_t>(r >> 6)] |= (1ULL << (r & 63));

            std::unordered_map<std::string, TT256> tt_memo;
            std::function<TT256(const std::string&)> eval_tt = [&](const std::string& w) -> TT256 {
                if (w == "1'b0" || w == "0") return TT256{0, 0, 0, 0};
                if (w == "1'b1" || w == "1") return mask_all;
                if (auto mit = tt_memo.find(w); mit != tt_memo.end()) return mit->second;
                if (auto lit = leaf_idx.find(w); lit != leaf_idx.end()) {
                    int vi = lit->second;
                    TT256 t{0, 0, 0, 0};
                    for (int r = 0; r < n_rows; ++r) {
                        if ((r >> vi) & 1) t[static_cast<size_t>(r >> 6)] |= (1ULL << (r & 63));
                    }
                    return (tt_memo[w] = t);
                }
                const PrimGate& g = gate_drv.at(w);
                TT256 res{0, 0, 0, 0};
                if (g.op == "buf") {
                    res = eval_tt(g.ins[0]);
                } else if (g.op == "not") {
                    TT256 a = eval_tt(g.ins[0]);
                    for (size_t u64i = 0; u64i < 4; ++u64i) res[u64i] = (~a[u64i]) & mask_all[u64i];
                } else if (g.op == "and" || g.op == "nand") {
                    res = mask_all;
                    for (const auto& inp : g.ins) {
                        TT256 a = eval_tt(inp);
                        for (size_t u64i = 0; u64i < 4; ++u64i) res[u64i] &= a[u64i];
                    }
                    if (g.op == "nand") for (size_t u64i = 0; u64i < 4; ++u64i) res[u64i] = (~res[u64i]) & mask_all[u64i];
                } else if (g.op == "or" || g.op == "nor") {
                    for (const auto& inp : g.ins) {
                        TT256 a = eval_tt(inp);
                        for (size_t u64i = 0; u64i < 4; ++u64i) res[u64i] |= a[u64i];
                    }
                    if (g.op == "nor") for (size_t u64i = 0; u64i < 4; ++u64i) res[u64i] = (~res[u64i]) & mask_all[u64i];
                } else { // xor / xnor
                    for (const auto& inp : g.ins) {
                        TT256 a = eval_tt(inp);
                        for (size_t u64i = 0; u64i < 4; ++u64i) res[u64i] ^= a[u64i];
                    }
                    if (g.op == "xnor") for (size_t u64i = 0; u64i < 4; ++u64i) res[u64i] = (~res[u64i]) & mask_all[u64i];
                }
                return (tt_memo[w] = res);
            };

            std::vector<TT256> s_tts;
            s_tts.reserve(s_sigs.size());
            for (const auto& sw : s_sigs) s_tts.push_back(eval_tt(sw));

            bool exact_one = true, at_most_one = true;
            for (int r = 0; r < n_rows; ++r) {
                int cnt = 0;
                for (const auto& stt : s_tts) {
                    if ((stt[static_cast<size_t>(r >> 6)] >> (r & 63)) & 1ULL) ++cnt;
                }
                if (cnt != 1) exact_one = false;
                if (cnt > 1) { at_most_one = false; break; }
            }
            int ans = exact_one ? 1 : (at_most_one ? 2 : 0);
            return (sel_proof_cache[key] = ans);
        };

        spos = clean_u.find(';');
        if (spos != std::string::npos) spos += 1;
        while (spos != std::string::npos && spos < clean_u.size()) {
            size_t semi = clean_u.find(';', spos);
            if (semi == std::string::npos) break;
            std::string_view st_clean(clean_u.data() + spos, semi - spos);
            size_t k = 0;
            while (k < st_clean.size() && std::isspace(static_cast<unsigned char>(st_clean[k]))) ++k;
            if (k < st_clean.size() && starts_with(st_clean.substr(k), "Select_")) {
                size_t te = k + 7;
                while (te < st_clean.size() && std::isdigit(static_cast<unsigned char>(st_clean[te]))) ++te;
                if (te > k + 7 && te < st_clean.size() && std::isspace(static_cast<unsigned char>(st_clean[te]))) {
                    int m_sz = std::stoi(std::string(st_clean.substr(k + 7, te - (k + 7))));
                    if (valid_selects.count(m_sz)) {
                        size_t inst_s = te;
                        while (inst_s < st_clean.size() && std::isspace(static_cast<unsigned char>(st_clean[inst_s]))) ++inst_s;
                        size_t inst_e = inst_s;
                        while (inst_e < st_clean.size() && !std::isspace(static_cast<unsigned char>(st_clean[inst_e])) && st_clean[inst_e] != '(') ++inst_e;
                        std::string inst_nm(raw_u.substr(spos + inst_s, inst_e - inst_s));
                        std::string_view st_orig(raw_u.data() + spos + k, semi - (spos + k));
                        std::string sel_arg, data_arg, o_arg;
                        if (!inst_nm.empty()
                            && extract_named_port_arg(st_orig, "sel", sel_arg)
                            && extract_named_port_arg(st_orig, "data", data_arg)
                            && extract_named_port_arg(st_orig, "o", o_arg)) {
                            auto dit = dec_bus_map.find({m_sz, strip_all_ws(sel_arg)});
                            if (dit != dec_bus_map.end()) {
                                int w = dit->second.first;
                                const std::string& idx_expr = dit->second.second;
                                std::string mmod = "Mux_" + std::to_string(w) + "u_" + std::to_string(m_sz) + "u";
                                needed_muxes.insert({w, m_sz});
                                std::string repl = mmod + " " + inst_nm + " (.sel(" + idx_expr + "), .data(" + data_arg + "), .o(" + o_arg + "));";
                                edits.push_back(ModSpanEdit{u.start + spos + k, u.start + semi + 1, std::move(repl)});
                            } else {
                                int oh_kind = prove_select_onehot(m_sz, sel_arg);
                                if (oh_kind == 1 || oh_kind == 2) {
                                    std::string smod = (oh_kind == 1 ? "NgSelectOH_" : "NgSelectOH0_") + std::to_string(m_sz);
                                    if (oh_kind == 1) needed_oh.insert(m_sz);
                                    else needed_oh0.insert(m_sz);
                                    std::string repl = smod + " " + inst_nm + " (.sel(" + sel_arg + "), .data(" + data_arg + "), .o(" + o_arg + "));";
                                    edits.push_back(ModSpanEdit{u.start + spos + k, u.start + semi + 1, std::move(repl)});
                                }
                            }
                        }
                    }
                }
            }
            spos = semi + 1;
        }
    }

    if (edits.empty()) return text;
    std::sort(edits.begin(), edits.end(), [](const ModSpanEdit& a, const ModSpanEdit& b) { return a.start < b.start; });
    std::string out;
    out.reserve(text.size() + 8192);
    size_t cur = 0;
    for (const auto& e : edits) {
        if (e.start < cur) continue;
        out.append(text.data() + cur, e.start - cur);
        out.append(e.repl);
        cur = e.end;
    }
    out.append(text.data() + cur, text.size() - cur);

    for (auto [w, m_sz] : needed_muxes) {
        std::string mmod = "Mux_" + std::to_string(w) + "u_" + std::to_string(m_sz) + "u";
        if (!existing_modules.count(mmod)) {
            std::string assign_rhs = "data[sel]";
            if (w >= 1 && w <= 8 && m_sz == (1 << w)) {
                std::vector<std::string> nodes(static_cast<size_t>(m_sz));
                for (int i = 0; i < m_sz; ++i) nodes[static_cast<size_t>(i)] = "data[" + std::to_string(i) + "]";
                for (int lvl = 0; lvl < w; ++lvl) {
                    int half = static_cast<int>(nodes.size()) / 2;
                    std::vector<std::string> nxt(static_cast<size_t>(half));
                    for (int i = 0; i < half; ++i) {
                        nxt[static_cast<size_t>(i)] = "(sel[" + std::to_string(lvl) + "] ? "
                            + nodes[static_cast<size_t>(2 * i + 1)] + " : "
                            + nodes[static_cast<size_t>(2 * i)] + ")";
                    }
                    nodes = std::move(nxt);
                }
                assign_rhs = nodes[0];
            }
            out += "\nmodule " + mmod + " (sel, data, o);\n"
                 + "    input [" + std::to_string(w - 1) + ":0] sel;\n"
                 + "    input [" + std::to_string(m_sz - 1) + ":0] data;\n"
                 + "    output o;\n"
                 + "    assign o = " + assign_rhs + ";\n"
                 + "endmodule\n";
        }
    }
    for (int m_sz : needed_oh) {
        std::string smod = "NgSelectOH_" + std::to_string(m_sz);
        if (!existing_modules.count(smod)) {
            std::string expr = "data[0]";
            for (int j = 1; j < m_sz; ++j) {
                expr = "sel[" + std::to_string(j) + "] ? data[" + std::to_string(j) + "] : (" + expr + ")";
            }
            out += "\nmodule " + smod + " (sel, data, o);\n"
                 + "    input [" + std::to_string(m_sz - 1) + ":0] sel;\n"
                 + "    input [" + std::to_string(m_sz - 1) + ":0] data;\n"
                 + "    output o;\n"
                 + "    assign o = " + expr + ";\n"
                 + "endmodule\n";
        }
    }
    for (int m_sz : needed_oh0) {
        std::string smod = "NgSelectOH0_" + std::to_string(m_sz);
        if (!existing_modules.count(smod)) {
            std::string expr = "1'b0";
            for (int j = 0; j < m_sz; ++j) {
                expr = "sel[" + std::to_string(j) + "] ? data[" + std::to_string(j) + "] : (" + expr + ")";
            }
            out += "\nmodule " + smod + " (sel, data, o);\n"
                 + "    input [" + std::to_string(m_sz - 1) + ":0] sel;\n"
                 + "    input [" + std::to_string(m_sz - 1) + ":0] data;\n"
                 + "    output o;\n"
                 + "    assign o = " + expr + ";\n"
                 + "endmodule\n";
        }
    }
    return out;
}

} // namespace

std::pair<std::string, JsonValue> verific_bridge_convert(std::string_view exported_v) {
    struct PrimSpec {
        std::string cell;
        OrderedMap<std::string> params;
        std::optional<std::string> vendor_id;
        std::string stub_sha256;
    };
    struct LibEntry {
        std::string port_header;
        std::vector<std::string> declarations;
        OrderedMap<std::string> params;
        std::vector<std::string> signature;
    };

    OrderedMap<PrimSpec> primitive_specs;
    OrderedMap<LibEntry> libraries;
    std::vector<std::pair<size_t, size_t>> remove_spans;

    for (const auto& u : verilog_units(exported_v)) {
        std::string_view body = exported_v.substr(u.start, u.end - u.start);
        size_t semi = body.find(';');
        if (semi == std::string_view::npos) continue;
        size_t hdr_nl = body.find('\n', semi);
        std::string_view header = body.substr(0, hdr_nl == std::string_view::npos ? body.size() : hdr_nl);
        if (header.find("prim_syn.v") == std::string_view::npos) continue;

        size_t cn_p = header.find("cell_name=");
        if (cn_p == std::string_view::npos) {
            throw std::runtime_error("Primitive stub without original cell type: " + u.name);
        }
        size_t cs = cn_p + 10, ce = cs;
        while (ce < header.size() && (std::isalnum(static_cast<unsigned char>(header[ce])) || header[ce] == '_' || header[ce] == '$')) ++ce;
        std::string base(header.substr(cs, ce - cs));
        if (base.empty()) throw std::runtime_error("Empty cell_name in stub: " + u.name);

        OrderedMap<std::string> actual_params;
        std::optional<std::string> vendor_id;
        for (const auto& ln : split_lines(body)) {
            std::string tr = ltrim(ln);
            if (starts_with(tr, "// parameter ")) {
                std::string rest = trim(std::string_view(tr).substr(13));
                if (!rest.empty() && rest.back() == ';') {
                    rest.pop_back();
                    size_t eq = rest.find('=');
                    if (eq != std::string::npos) {
                        std::string pk = trim(std::string_view(rest).substr(0, eq));
                        std::string pv = trim(std::string_view(rest).substr(eq + 1));
                        if (pk == "ID") vendor_id = pv;
                        else actual_params.set(pk, pv);
                    }
                }
            }
        }

        std::string clean = masked_verilog(body);
        std::vector<std::string> declarations;
        size_t dpos = 0;
        while (dpos < clean.size()) {
            size_t ls = dpos;
            size_t k = ls;
            while (k < clean.size() && (clean[k] == ' ' || clean[k] == '\t')) ++k;
            bool match_dir = false;
            for (const char* kw : {"input", "output", "inout"}) {
                size_t kl = std::strlen(kw);
                if (clean.compare(k, kl, kw) == 0 && (k + kl == clean.size() || !(std::isalnum(static_cast<unsigned char>(clean[k + kl])) || clean[k + kl] == '_'))) {
                    match_dir = true;
                    break;
                }
            }
            if (match_dir) {
                size_t ds = clean.find(';', k);
                if (ds != std::string::npos) {
                    declarations.push_back(clean.substr(ls, ds - ls + 1));
                    size_t nl = clean.find('\n', ds);
                    dpos = (nl == std::string::npos) ? clean.size() : (nl + 1);
                    continue;
                }
            }
            size_t nl = clean.find('\n', ls);
            dpos = (nl == std::string::npos) ? clean.size() : (nl + 1);
        }

        size_t first_semi = clean.find(';');
        size_t nm_pos = clean.find(u.name, 6);
        size_t search_from = (nm_pos == std::string::npos) ? 0 : (nm_pos + u.name.size());
        size_t lp = clean.find('(', search_from);
        size_t rp = (lp != std::string::npos && lp < first_semi) ? clean.find(')', lp + 1) : std::string::npos;
        if (lp == std::string::npos || rp == std::string::npos || rp > first_semi || declarations.empty()) {
            throw std::runtime_error("Primitive interface was not recoverable: " + u.name);
        }
        std::string port_header = clean.substr(lp, rp - lp + 1);
        std::vector<std::string> sig = tokenize_verilog(port_header + "\n" + join_strings(declarations, "\n"));
        if (auto* existing = libraries.find_ptr(base)) {
            if (existing->signature != sig) {
                throw std::runtime_error("Primitive specializations disagree on port schema: " + base);
            }
            for (const auto& [pk, pv] : actual_params) {
                if (!existing->params.contains(pk)) {
                    existing->params.set(pk, pv);
                }
            }
        } else {
            libraries.set(base, LibEntry{port_header, declarations, actual_params, sig});
        }
        primitive_specs.set(u.name, PrimSpec{base, actual_params, vendor_id, sha256_str(body)});
        remove_spans.emplace_back(u.start, u.end);
    }

    std::string text;
    text.reserve(exported_v.size());
    size_t cur = 0;
    for (auto [a, b] : remove_spans) {
        text.append(exported_v.data() + cur, a - cur);
        cur = b;
    }
    text.append(exported_v.data() + cur, exported_v.size() - cur);

    auto [mem_restored_text, memories_json] = restore_verific_memories(text);
    text = normalize_verific_decoder_selects(mem_restored_text);

    OrderedMap<int64_t> replacements;
    for (const auto& [k, _] : primitive_specs) replacements.set(k, 0);

    // Count `/* Init Val:` before line pass
    size_t count_init = 0;
    for (size_t p = 0; (p = text.find("/* Init Val:", p)) != std::string::npos; p += 12) {
        ++count_init;
    }

    JsonValue initial_values = JsonValue::array();
    int64_t kept = 0;
    int64_t blackboxes = 0;

    // Single fast line-by-line pass handling:
    //   1. Primitive specialization `^([ \t]*)(<IDENT>)(?=\s+<IDENT>\s*\()`
    //   2. Initial-value comment `^([ \t]*)(wire|reg|output)\b([^;\n]*?)/\* Init Val:\s*([01xXzZ]+)\s*\*/`
    //   3. Preserve comment `^([ \t]*)(?:\(\*[^\n]*?\*\)\s*)?(?:wire|reg|input|output)\b[^\n]*` with `\bsyn_(?:keep|preserve)\s*=\s*1\b`
    //   4. Explicit blackbox `^module\s+[^\n]*?/\* verific ([^\n]*?\bsyn_black_box=1\b[^\n]*?)\*/`
    std::string rewritten;
    rewritten.reserve(text.size() + 65536);

    size_t lstart = 0;
    while (lstart < text.size()) {
        size_t nl = text.find('\n', lstart);
        size_t lend = (nl == std::string::npos) ? text.size() : nl;
        std::string line = text.substr(lstart, lend - lstart);

        // Step 1: Primitive specialization
        {
            size_t indent_len = 0;
            while (indent_len < line.size() && (line[indent_len] == ' ' || line[indent_len] == '\t')) ++indent_len;
            if (indent_len < line.size()) {
                size_t ie = indent_len;
                if (line[ie] == '\\') {
                    ++ie;
                    while (ie < line.size() && !std::isspace(static_cast<unsigned char>(line[ie]))) ++ie;
                } else if (std::isalpha(static_cast<unsigned char>(line[ie])) || line[ie] == '_' || line[ie] == '$') {
                    ++ie;
                    while (ie < line.size() && (std::isalnum(static_cast<unsigned char>(line[ie])) || line[ie] == '_' || line[ie] == '$')) ++ie;
                }
                if (ie > indent_len) {
                    std::string mod_tok = line.substr(indent_len, ie - indent_len);
                    if (const PrimSpec* ps = primitive_specs.find_ptr(mod_tok)) {
                        size_t k = ie;
                        while (k < line.size() && std::isspace(static_cast<unsigned char>(line[k]))) ++k;
                        if (k > ie && k < line.size()) {
                            size_t inst_e = k;
                            if (line[inst_e] == '\\') {
                                ++inst_e;
                                while (inst_e < line.size() && !std::isspace(static_cast<unsigned char>(line[inst_e]))) ++inst_e;
                            } else if (std::isalpha(static_cast<unsigned char>(line[inst_e])) || line[inst_e] == '_' || line[inst_e] == '$') {
                                ++inst_e;
                                while (inst_e < line.size() && (std::isalnum(static_cast<unsigned char>(line[inst_e])) || line[inst_e] == '_' || line[inst_e] == '$')) ++inst_e;
                            }
                            if (inst_e > k) {
                                size_t po = inst_e;
                                while (po < line.size() && std::isspace(static_cast<unsigned char>(line[po]))) ++po;
                                if (po < line.size() && line[po] == '(') {
                                    std::vector<std::string> plist;
                                    for (const auto& [pk, pv] : ps->params) {
                                        plist.push_back("." + pk + "(" + pv + ")");
                                    }
                                    std::string spec_head = ps->cell + (plist.empty() ? "" : (" #(" + join_strings(plist, ", ") + ")"));
                                    line = line.substr(0, indent_len) + spec_head + line.substr(ie);
                                    replacements[mod_tok] += 1;
                                }
                            }
                        }
                    }
                }
            }
        }

        // Step 2: /* Init Val: ... */
        if (line.find("/* Init Val:") != std::string::npos) {
            size_t ind = 0;
            while (ind < line.size() && (line[ind] == ' ' || line[ind] == '\t')) ++ind;
            std::string kw;
            for (const char* cand : {"wire", "reg", "output"}) {
                size_t cl = std::strlen(cand);
                if (line.compare(ind, cl, cand) == 0 && ind + cl < line.size() && !(std::isalnum(static_cast<unsigned char>(line[ind + cl])) || line[ind + cl] == '_')) {
                    kw = cand;
                    break;
                }
            }
            if (!kw.empty()) {
                size_t iv_pos = line.find("/* Init Val:", ind + kw.size());
                size_t semi_before = line.find(';', ind + kw.size());
                if (iv_pos != std::string::npos && (semi_before == std::string::npos || semi_before > iv_pos)) {
                    size_t iv_close = line.find("*/", iv_pos + 12);
                    if (iv_close != std::string::npos) {
                        std::string raw_bits = trim(std::string_view(line).substr(iv_pos + 12, iv_close - (iv_pos + 12)));
                        bool valid_bits = !raw_bits.empty();
                        for (char bc : raw_bits) {
                            if (std::strchr("01xXzZ", bc) == nullptr) { valid_bits = false; break; }
                        }
                        if (valid_bits) {
                            std::string low_bits = to_lower_ascii(raw_bits);
                            std::string mid = line.substr(ind + kw.size(), iv_pos - (ind + kw.size()));
                            JsonValue iv_rec = JsonValue::object();
                            iv_rec["declaration"] = trim(kw + mid);
                            iv_rec["bits"] = low_bits;
                            initial_values.push_back(std::move(iv_rec));
                            line = line.substr(0, ind) + "(* init = " + std::to_string(low_bits.size()) + "'b" + low_bits + " *) "
                                 + kw + mid + line.substr(iv_close + 2);
                        }
                    }
                }
            }
        }

        // Step 3: syn_keep / syn_preserve = 1
        if (line.find("syn_keep") != std::string::npos || line.find("syn_preserve") != std::string::npos) {
            size_t ind = 0;
            while (ind < line.size() && (line[ind] == ' ' || line[ind] == '\t')) ++ind;
            size_t k = ind;
            if (k + 2 <= line.size() && line[k] == '(' && line[k + 1] == '*') {
                size_t ac = line.find("*)", k + 2);
                if (ac != std::string::npos) {
                    k = ac + 2;
                    while (k < line.size() && std::isspace(static_cast<unsigned char>(line[k]))) ++k;
                }
            }
            bool is_net_decl = false;
            for (const char* cand : {"wire", "reg", "input", "output"}) {
                size_t cl = std::strlen(cand);
                if (line.compare(k, cl, cand) == 0 && (k + cl == line.size() || !(std::isalnum(static_cast<unsigned char>(line[k + cl])) || line[k + cl] == '_'))) {
                    is_net_decl = true;
                    break;
                }
            }
            if (is_net_decl) {
                auto check_kw_one = [&](const char* skw) -> bool {
                    size_t sp = 0;
                    size_t sl = std::strlen(skw);
                    while ((sp = line.find(skw, sp)) != std::string::npos) {
                        bool lok = (sp == 0) || !(std::isalnum(static_cast<unsigned char>(line[sp - 1])) || line[sp - 1] == '_');
                        size_t m = sp + sl;
                        if (lok && (m == line.size() || !(std::isalnum(static_cast<unsigned char>(line[m])) || line[m] == '_'))) {
                            while (m < line.size() && std::isspace(static_cast<unsigned char>(line[m]))) ++m;
                            if (m < line.size() && line[m] == '=') {
                                ++m;
                                while (m < line.size() && std::isspace(static_cast<unsigned char>(line[m]))) ++m;
                                if (m < line.size() && line[m] == '1' && (m + 1 == line.size() || !(std::isalnum(static_cast<unsigned char>(line[m + 1])) || line[m + 1] == '_'))) {
                                    return true;
                                }
                            }
                        }
                        sp += sl;
                    }
                    return false;
                };
                if (check_kw_one("syn_keep") || check_kw_one("syn_preserve")) {
                    ++kept;
                    line = line.substr(0, ind) + "(* keep = 1 *) " + line.substr(ind);
                }
            }
        }

        // Step 4: explicit blackbox `^module\s+[^\n]*?/\* verific ([^\n]*?\bsyn_black_box=1\b[^\n]*?)\*/`
        if (starts_with(line, "module ") || starts_with(line, "module\t")) {
            size_t vp = line.find("/* verific ");
            if (vp != std::string::npos) {
                size_t ve = line.find("*/", vp + 11);
                if (ve != std::string::npos) {
                    std::string_view vbody(line.data() + vp + 11, ve - (vp + 11));
                    size_t sbb = vbody.find("syn_black_box=1");
                    if (sbb != std::string_view::npos) {
                        bool lok = (sbb == 0) || !(std::isalnum(static_cast<unsigned char>(vbody[sbb - 1])) || vbody[sbb - 1] == '_');
                        bool rok = (sbb + 15 == vbody.size()) || !(std::isalnum(static_cast<unsigned char>(vbody[sbb + 15])) || vbody[sbb + 15] == '_');
                        if (lok && rok) {
                            ++blackboxes;
                            line = "(* blackbox = 1 *) " + line;
                        }
                    }
                }
            }
        }

        rewritten.append(line);
        if (nl != std::string::npos) rewritten.push_back('\n');
        lstart = (nl == std::string::npos) ? text.size() : (nl + 1);
    }

    if (initial_values.as_arr().size() != count_init) {
        throw std::runtime_error("Untranslated initial-value annotation");
    }

    std::string mclean = masked_verilog(rewritten);
    for (const auto& ln : split_lines(mclean)) {
        std::string lt = ltrim(ln);
        if (starts_with(lt, "module ")) {
            std::string rest = ltrim(std::string_view(lt).substr(7));
            if (starts_with(rest, "\\PLL(") || starts_with(rest, "\\SDP(") || starts_with(rest, "\\DPB(")
                || (rest.size() >= 6 && starts_with(rest, "\\LUT") && std::isdigit(static_cast<unsigned char>(rest[4])) && rest[5] == '(')) {
                throw std::runtime_error("Primitive specializations remain unresolved");
            }
        }
    }

    std::vector<std::string> lib_lines;
    for (const auto& [base, spec] : libraries) {
        lib_lines.push_back("(* blackbox = 1 *) module " + base + " " + spec.port_header + ";");
        for (const auto& [pk, pv] : spec.params) {
            lib_lines.push_back("parameter " + pk + " = " + pv + ";");
        }
        for (const auto& d : spec.declarations) {
            lib_lines.push_back(d);
        }
        lib_lines.push_back("endmodule");
    }

    JsonValue pspecs_json = JsonValue::object();
    for (const auto& [nm, ps] : primitive_specs) {
        JsonValue pj = JsonValue::object();
        pj["cell"] = ps.cell;
        JsonValue pm = JsonValue::object();
        for (const auto& [pk, pv] : ps.params) pm[pk] = pv;
        pj["parameters"] = std::move(pm);
        pj["vendor_id"] = ps.vendor_id.has_value() ? JsonValue(*ps.vendor_id) : JsonValue(nullptr);
        pj["stub_sha256"] = ps.stub_sha256;
        pspecs_json[nm] = std::move(pj);
    }
    JsonValue repl_json = JsonValue::object();
    for (const auto& [nm, cnt] : replacements) repl_json[nm] = cnt;

    JsonValue manifest = JsonValue::object();
    manifest["primitive_specializations"] = std::move(pspecs_json);
    manifest["rewritten_instances"] = std::move(repl_json);
    manifest["vendor_library_verilog"] = join_strings(lib_lines, "\n") + "\n";
    manifest["restored_memories"] = std::move(memories_json);
    manifest["initial_values"] = std::move(initial_values);
    manifest["kept_declarations"] = kept;
    manifest["explicit_blackboxes"] = blackboxes;
    manifest["scope"] = "Restores exported annotations. Primitive ID tags recorded separately; no IP decryption or RTL semantics inferred from names.";
    return {std::move(rewritten), std::move(manifest)};
}

// ============================================================================
// Stage 3 Helpers: gw5a_dsp, prim_sim formats, hierarchy_islands, carry lowering
// ============================================================================
const OrderedMap<OrderedMap<std::string>>& gowin_prim_sim_formats() {
    static OrderedMap<OrderedMap<std::string>> cached;
    static std::string cached_sdk;
    if (!cached.empty() && cached_sdk == runtime_paths().gowin_sdk) {
        return cached;
    }
    std::string lib_path = path_join(runtime_paths().gowin_sdk, "simlib\\gw5a\\prim_sim.v");
    (void)verify_or_record_sdk_file_sha(lib_path, EXPECTED_PRIM_SIM_SHA, "simlib/gw5a/prim_sim.v");
    std::string text = read_file_text(lib_path);
    OrderedMap<OrderedMap<std::string>> res;
    for (const auto& u : primitive_model_units(text)) {
        std::string_view body(text.data() + u.start, u.end - u.start);
        OrderedMap<std::string> pmap;
        for (const auto& ln : split_lines(body)) {
            std::string lt = ltrim(ln);
            if (starts_with(lt, "parameter ") || starts_with(lt, "parameter\t")) {
                size_t semi = lt.find(';');
                if (semi != std::string::npos) {
                    std::string mid = trim(std::string_view(lt).substr(9, semi - 9));
                    size_t eq = mid.find('=');
                    if (eq != std::string::npos) {
                        std::string pk = trim(std::string_view(mid).substr(0, eq));
                        std::string pv = trim(std::string_view(mid).substr(eq + 1));
                        if (is_simple_ident(pk)) pmap.set(pk, pv);
                    }
                }
            }
        }
        res.set(u.name, std::move(pmap));
    }
    cached = std::move(res);
    cached_sdk = runtime_paths().gowin_sdk;
    return cached;
}

std::string gw5a_dsp_primitive_library() {
    std::string lib_path = path_join(runtime_paths().gowin_sdk, "simlib\\gw5a\\prim_sim.v");
    (void)verify_or_record_sdk_file_sha(lib_path, EXPECTED_PRIM_SIM_SHA, "Audited GW5A model (prim_sim.v)");
    std::string source = read_file_text(lib_path);
    auto ulist = verilog_units(source);
    std::unordered_map<std::string, UnitSpan> umap;
    for (const auto& u : ulist) umap[u.name] = u;

    std::vector<std::string> rows;
    for (const char* kind : {"MULT12X12", "MULTALU27X18", "MULT27X36"}) {
        const auto& u = umap.at(kind);
        std::string body = masked_verilog(std::string_view(source).substr(u.start, u.end - u.start));
        size_t semi = body.find(';');
        std::string head = body.substr(0, semi + 1);
        std::vector<std::string> decls;
        size_t p = 0;
        while (p < body.size()) {
            size_t ls = p;
            size_t k = ls;
            while (k < body.size() && (body[k] == ' ' || body[k] == '\t')) ++k;
            bool match_kw = false;
            for (const char* kw : {"parameter", "input", "output"}) {
                size_t kl = std::strlen(kw);
                if (body.compare(k, kl, kw) == 0 && (k + kl == body.size() || !(std::isalnum(static_cast<unsigned char>(body[k + kl])) || body[k + kl] == '_'))) {
                    match_kw = true;
                    break;
                }
            }
            if (match_kw) {
                size_t ds = body.find(';', k);
                if (ds != std::string::npos) {
                    decls.push_back(body.substr(ls, ds - ls + 1));
                    size_t nl = body.find('\n', ds);
                    p = (nl == std::string::npos) ? body.size() : (nl + 1);
                    continue;
                }
            }
            size_t nl = body.find('\n', ls);
            p = (nl == std::string::npos) ? body.size() : (nl + 1);
        }
        rows.push_back("(* blackbox = 1 *) " + head + "\n" + join_strings(decls, "\n") + "\nendmodule\n");
    }
    return "// Interfaces extracted from hash-pinned Gowin GW5A prim_sim.v.\n" + join_strings(rows, "\n");
}

namespace {

std::string dsp_param_default_value(std::string_view raw) {
    std::string v = trim(raw);
    if (!v.empty() && v.front() == '"' && v.back() == '"' && v.size() >= 2) {
        return v.substr(1, v.size() - 2);
    }
    bool all_dig = !v.empty();
    for (char c : v) if (!std::isdigit(static_cast<unsigned char>(c))) { all_dig = false; break; }
    if (all_dig) {
        return format_bin_u64(std::stoull(v), 32);
    }
    auto [w, uval] = parse_verilog_literal_u64(v);
    return format_bin_u64(uval, w);
}

std::pair<std::string, JsonValue> gw5a_dsp_prepare(std::string_view text) {
    struct Edit { size_t a; size_t b; std::string body; };
    std::vector<Edit> edits;
    JsonValue records = JsonValue::array();

    auto no_ws_masked = [](std::string_view s) {
        std::string m = masked_verilog(s);
        std::string r;
        r.reserve(m.size());
        for (char c : m) if (!std::isspace(static_cast<unsigned char>(c))) r.push_back(c);
        return r;
    };

    auto extend_sig = [](const std::string& sig, int width, bool is_signed, int target) -> std::string {
        if (width == target) return sig;
        std::string pad = is_signed ? (sig + "[" + std::to_string(width - 1) + "]") : "1'b0";
        return "{{" + std::to_string(target - width) + "{" + pad + "}}," + sig + "}";
    };

    for (const auto& u : verilog_units(text)) {
        if (!starts_with(u.name, "mult_")) continue;
        auto parts = split_char(u.name, '_');
        if (parts.size() != 3 || parts[1].size() < 2 || parts[2].size() < 2) continue;
        char sa_ch = parts[1].back(), sb_ch = parts[2].back();
        if ((sa_ch != 's' && sa_ch != 'u') || (sb_ch != 's' && sb_ch != 'u')) continue;
        std::string aw_s = parts[1].substr(0, parts[1].size() - 1);
        std::string bw_s = parts[2].substr(0, parts[2].size() - 1);
        bool ok_num = !aw_s.empty() && !bw_s.empty();
        for (char c : aw_s) if (!std::isdigit(static_cast<unsigned char>(c))) ok_num = false;
        for (char c : bw_s) if (!std::isdigit(static_cast<unsigned char>(c))) ok_num = false;
        if (!ok_num) continue;

        int aw = std::stoi(aw_s), bw = std::stoi(bw_s);
        bool sa = (sa_ch == 's'), sb = (sb_ch == 's');
        if (sa != sb) continue;

        int ow = aw + bw;
        std::string header = "module " + u.name + " (a,b,o); input [" + std::to_string(aw - 1) + ":0] a; input ["
                           + std::to_string(bw - 1) + ":0] b; output [" + std::to_string(ow - 1) + ":0] o;";
        std::string expected = sa
            ? (header + "wire [" + std::to_string(aw - 1) + ":0] l; wire [" + std::to_string(bw - 1) + ":0] r; assign l=a["
               + std::to_string(aw - 1) + "]?-a:a; assign r=b[" + std::to_string(bw - 1) + "]?-b:b; assign o=(a["
               + std::to_string(aw - 1) + "]^b[" + std::to_string(bw - 1) + "])?-(l*r):l*r; endmodule")
            : (header + "assign o=a*b; endmodule");

        std::string_view orig = text.substr(u.start, u.end - u.start);
        if (no_ws_masked(orig) != no_ws_masked(expected)) {
            throw std::runtime_error("Unrecognized multiply semantics: " + u.name);
        }

        int aa = aw + (sa ? 0 : 1);
        int bb = bw + (sb ? 0 : 1);
        std::string kind;
        int pa = 0, pb = 0;
        bool swap_ab = false;
        if (std::max(aa, bb) <= 12) {
            kind = "MULT12X12"; pa = 12; pb = 12; swap_ab = false;
        } else if (aa <= 27 && bb <= 18) {
            kind = "MULTALU27X18"; pa = 27; pb = 18; swap_ab = false;
        } else if (bb <= 27 && aa <= 18) {
            kind = "MULTALU27X18"; pa = 27; pb = 18; swap_ab = true;
        } else if (aa <= 27 && bb <= 36) {
            kind = "MULT27X36"; pa = 27; pb = 36; swap_ab = false;
        } else if (bb <= 27 && aa <= 36) {
            kind = "MULT27X36"; pa = 27; pb = 36; swap_ab = true;
        } else {
            continue;
        }

        std::string ax = swap_ab ? "b" : "a";
        std::string bx = swap_ab ? "a" : "b";
        int wa = swap_ab ? bw : aw;
        int wb = swap_ab ? aw : bw;
        std::string ea = extend_sig(ax, wa, sa, pa);
        std::string eb = extend_sig(bx, wb, sb, pb);
        int pwidth = (kind == "MULT12X12") ? 24 : (kind == "MULTALU27X18" ? 48 : 63);

        std::string body = header + "\nwire [" + std::to_string(pwidth - 1) + ":0] product;\n";
        std::vector<std::string> pins = {
            ".A(" + ea + ")", ".B(" + eb + ")", ".CLK(2'b00)", ".CE(2'b00)", ".RESET(2'b00)", ".DOUT(product)"
        };
        std::string params;
        if (kind != "MULT12X12") {
            pins.push_back(".D(26'b0)");
            pins.push_back(".PSEL(1'b0)");
            pins.push_back(".PADDSUB(1'b0)");
        }
        if (kind == "MULTALU27X18") {
            params = "#(.C_SEL(1'b0)) ";
            for (const char* xp : {".C(48'b0)", ".SIA(27'b0)", ".CASI(48'b0)", ".ACCSEL(1'b0)", ".ASEL(1'b0)", ".CSEL(1'b0)", ".CASISEL(1'b0)", ".ADDSUB(2'b00)", ".CASO()", ".SOA()"}) {
                pins.push_back(xp);
            }
        }
        body += kind + " " + params + "ng_dsp(" + join_strings(pins, ",") + ");\nassign o=product[" + std::to_string(ow - 1) + ":0];\nendmodule";
        edits.push_back(Edit{u.start, u.end, std::move(body)});

        JsonValue rec = JsonValue::object();
        rec["module"] = u.name;
        rec["cell"] = kind;
        rec["a_width"] = aw;
        rec["b_width"] = bw;
        rec["signed"] = sa;
        rec["swapped"] = swap_ab;
        rec["output_width"] = ow;
        rec["latency"] = 0;
        rec["registers_absorbed"] = false;
        records.push_back(std::move(rec));
    }

    std::string out_text(text);
    for (auto it = edits.rbegin(); it != edits.rend(); ++it) {
        out_text = out_text.substr(0, it->a) + it->body + out_text.substr(it->b);
    }

    JsonValue out_rec = JsonValue::object();
    out_rec["schema"] = "gw5a-dsp-helper-map-v1";
    out_rec["multipliers"] = std::move(records);
    out_rec["model_sha256"] = effective_prim_sim_sha256();
    out_rec["scope"] = "Checked full-product signed/unsigned helpers; DSP registers bypassed; external sequential logic unchanged.";
    return {std::move(out_text), std::move(out_rec)};
}

JsonValue detect_hierarchy_islands(std::string_view text, const std::vector<std::string>& verified_multipliers, int min_state, int min_multipliers) {
    std::unordered_set<std::string> mult_kinds(verified_multipliers.begin(), verified_multipliers.end());
    for (const char* k : {"MULT12X12", "MULTALU27X18", "MULT27X36", "MULTADDALU12X12"}) mult_kinds.insert(k);

    JsonValue rows = JsonValue::array();
    for (const auto& u : verilog_units(text)) {
        std::string body = masked_verilog(text.substr(u.start, u.end - u.start));
        int m_cnt = 0, f_cnt = 0;
        for (const auto& ln : split_lines(body)) {
            std::string lt = ltrim(ln);
            if (lt.empty()) continue;
            size_t e = 0;
            if (lt[0] == '\\') {
                ++e;
                while (e < lt.size() && !std::isspace(static_cast<unsigned char>(lt[e]))) ++e;
            } else {
                while (e < lt.size() && (std::isalnum(static_cast<unsigned char>(lt[e])) || lt[e] == '_' || lt[e] == '$')) ++e;
            }
            if (e > 0 && e < lt.size() && std::isspace(static_cast<unsigned char>(lt[e]))) {
                std::string kw = lt.substr(0, e);
                if (mult_kinds.count(kw)) ++m_cnt;
                if (starts_with(kw, "VERIFIC_DFF") || kw == "DFFRE" || kw == "DFFSE" || kw == "DFFCE" || kw == "DFFPE") ++f_cnt;
            }
        }
        if (m_cnt >= min_multipliers && f_cnt >= min_state) {
            JsonValue r = JsonValue::object();
            r["module"] = u.name;
            r["multiply_instances"] = m_cnt;
            r["direct_state_instances"] = f_cnt;
            rows.push_back(std::move(r));
        }
    }
    return rows;
}

} // namespace

JsonValue gw5a_dsp_explicit_configuration(
    RtlilDesign& design,
    const std::vector<std::pair<std::string, std::string>>& authorized_packed
) {
    const auto& lib = gowin_prim_sim_formats();
    std::unordered_set<std::string> auth_set;
    for (const auto& [m, inst] : authorized_packed) auth_set.insert(m + "\n" + inst);

    JsonValue records = JsonValue::array();
    for (const auto& [mn, mptr] : design.modules) {
        if (is_module_blackbox(*mptr)) continue;
        RtlilModule& row = design.mod_mut(mn);
        const RtlilPort* clk_port = row.ports.find_ptr("clk");

        for (auto [name, cell] : row.cells) {
            if ((cell.type != "MULT12X12" && cell.type != "MULTALU27X18" && cell.type != "MULT27X36") || !ends_with(name, ".ng_dsp")) {
                continue;
            }
            const auto& defaults = lib.at(cell.type);
            bool packed = auth_set.count(mn + "\n" + name) > 0;
            for (const auto& [pk, pv] : defaults) {
                if (cell.parameters.contains(pk)) continue;
                cell.parameters.set(pk, dsp_param_default_value(pv));
            }
            OrderedMap<std::string> active;
            for (const auto& [pk, pv] : cell.parameters) {
                if (ends_with(pk, "_CLK") && pv != "BYPASS") active.set(pk, pv);
            }
            if (!active.empty() && (!packed || active.size() != 1 || active.value_or("OREG_CLK", "") != "CLK0")) {
                throw std::runtime_error("Combinational DSP acquired registered behavior: " + name);
            }
            auto sigbits_to_json = [](const std::vector<SigBit>& bs) {
                JsonValue a = JsonValue::array();
                for (SigBit b : bs) {
                    if (b >= 0) a.push_back(b);
                    else a.push_back(std::string(1, const_bit_char(b)));
                }
                return a;
            };
            std::vector<SigBit> old_clk = cell.connections.at("CLK");
            if (!packed && clk_port && clk_port->direction == "input" && clk_port->bits.size() == 1) {
                cell.connections.set("CLK", {clk_port->bits[0], clk_port->bits[0]});
            }
            JsonValue r = JsonValue::object();
            r["module"] = mn;
            r["instance"] = name;
            r["cell"] = cell.type;
            r["clock_before"] = sigbits_to_json(old_clk);
            r["clock_after"] = sigbits_to_json(cell.connections.at("CLK"));
            r["all_register_clocks_bypassed"] = !packed;
            r["proved_output_register"] = packed;
            r["explicit_parameters"] = static_cast<int64_t>(cell.parameters.size());
            records.push_back(std::move(r));
        }
    }
    return records;
}

std::pair<RtlilDesign, JsonValue> lower_carry_only_chains(const RtlilDesign& source, int max_chain_len) {
    RtlilDesign design = source.clone_cow();
    JsonValue records = JsonValue::array();
    int64_t alu_removed = 0;
    int64_t preserved_wide_chains = 0;
    int64_t preserved_wide_alus = 0;

    for (const auto& [mn, mptr] : design.modules) {
        if (starts_with(mn, "~") || is_module_blackbox(*mptr)) continue;
        bool has_alu = false;
        for (const auto& [_, c] : mptr->cells) {
            if (c.type == "ALU") { has_alu = true; break; }
        }
        if (!has_alu) continue;

        RtlilModule& m = design.mod_mut(mn);
        OrderedMap<bool> alus;
        std::unordered_map<SigBit, int> loads;
        for (const auto& [n, c] : m.cells) {
            if (c.type == "ALU") alus.set(n, true);
            for (const auto& [p, bs] : c.connections) {
                std::string pd = c.port_directions.value_or(p, "");
                if (pd == "input" || pd == "inout") {
                    for (SigBit b : bs) loads[b] += 1;
                }
            }
        }
        for (const auto& [_, pr] : m.ports) {
            if (pr.direction != "input") {
                for (SigBit b : pr.bits) loads[b] += 1;
            }
        }
        for (const auto& [_, nr] : m.netnames) {
            if (is_attr_truthy(nr.attributes, "keep")) {
                for (SigBit b : nr.bits) loads[b] += 1;
            }
        }

        std::unordered_map<SigBit, std::string> drivers;
        std::unordered_map<std::string, std::vector<std::string>> child;
        for (const auto& [n, _] : alus) {
            const auto& c = m.cells.at(n);
            drivers[c.connections.at("COUT")[0]] = n;
        }
        for (const auto& [n, _] : alus) {
            const auto& c = m.cells.at(n);
            auto it = drivers.find(c.connections.at("CIN")[0]);
            if (it != drivers.end()) child[it->second].push_back(n);
        }

        int32_t maximum = max_module_bit(m);
        auto make_gate = [&](const std::string& gname, const std::string& kind, const std::vector<std::pair<std::string, SigBit>>& ins, std::optional<SigBit> out_opt) -> SigBit {
            if (m.cells.contains(gname)) throw std::runtime_error("Carry lowering name collision: " + gname);
            SigBit out_b = out_opt.has_value() ? *out_opt : ++maximum;
            RtlilCell gc;
            gc.type = kind;
            for (const auto& [pk, pb] : ins) {
                gc.connections.set(pk, {pb});
                gc.port_directions.set(pk, "input");
            }
            gc.connections.set("Y", {out_b});
            gc.port_directions.set("Y", "output");
            m.cells.set(gname, std::move(gc));
            return out_b;
        };

        for (const auto& [root, _] : alus) {
            const auto* rc_ptr = m.cells.find_ptr(root);
            if (!rc_ptr) continue;
            const auto& rc_cell = *rc_ptr;
            if (drivers.count(rc_cell.connections.at("CIN")[0])) continue;
            std::vector<std::string> chain;
            std::unordered_set<std::string> in_chain;
            std::vector<std::string> stack{root};
            while (!stack.empty()) {
                std::string cur = std::move(stack.back());
                stack.pop_back();
                if (in_chain.count(cur)) throw std::runtime_error("Cyclic carry chain");
                in_chain.insert(cur);
                chain.push_back(cur);
                auto cit = child.find(cur);
                if (cit != child.end()) {
                    for (const auto& ch : cit->second) stack.push_back(ch);
                }
            }

            bool sum_used = false;
            bool bad_mode_or_keep = false;
            for (const auto& n : chain) {
                const auto& ac = m.cells.at(n);
                if (const auto* sum_bs = ac.connections.find_ptr("SUM")) {
                    for (SigBit sb : *sum_bs) {
                        if (loads[sb] > 0) { sum_used = true; break; }
                    }
                }
                int mode = static_cast<int>(parse_bin_u64(ac.parameters.at("ALU_MODE")));
                if ((mode != 0 && mode != 1 && mode != 2) || is_attr_truthy(ac.attributes, "keep")) {
                    bad_mode_or_keep = true;
                }
            }
            if (sum_used || bad_mode_or_keep) continue;
            if (static_cast<int>(chain.size()) > max_chain_len) {
                ++preserved_wide_chains;
                preserved_wide_alus += static_cast<int64_t>(chain.size());
                continue;
            }

            JsonValue ch_arr = JsonValue::array();
            for (const auto& n : chain) {
                ch_arr.push_back(n);
                RtlilCell ac = m.cells.at(n);
                int mode = static_cast<int>(parse_bin_u64(ac.parameters.at("ALU_MODE")));
                SigBit i0 = ac.connections.at("I0")[0];
                SigBit i1 = ac.connections.at("I1")[0];
                SigBit cin = ac.connections.at("CIN")[0];
                SigBit cout = ac.connections.at("COUT")[0];

                SigBit x = make_gate(n + "_ng_xor", "$_XOR_", {{"A", i0}, {"B", i1}}, std::nullopt);
                SigBit ctrl = (mode == 0) ? SIG_1 : ((mode == 1) ? SIG_0 : ac.connections.at("I3")[0]);
                if (ctrl == SIG_0) {
                    x = make_gate(n + "_ng_inv", "$_NOT_", {{"A", x}}, std::nullopt);
                } else if (ctrl != SIG_1) {
                    SigBit inv = make_gate(n + "_ng_ctrl", "$_NOT_", {{"A", ctrl}}, std::nullopt);
                    x = make_gate(n + "_ng_sel", "$_XOR_", {{"A", x}, {"B", inv}}, std::nullopt);
                }
                make_gate(n + "_ng_carry", "$_MUX_", {{"A", i0}, {"B", cin}, {"S", x}}, cout);
                m.cells.erase(n);
            }
            alu_removed += static_cast<int64_t>(chain.size());
            JsonValue rec = JsonValue::object();
            rec["module"] = mn;
            rec["root"] = root;
            rec["cells"] = std::move(ch_arr);
            records.push_back(std::move(rec));
        }
    }

    JsonValue out = JsonValue::object();
    if (max_chain_len < 100000000) {
        out["schema"] = "carry-only-boolean-short-v2";
        out["max_chain_len"] = max_chain_len;
        out["chains"] = std::move(records);
        out["alu_cells_removed"] = alu_removed;
        out["preserved_wide_chains"] = preserved_wide_chains;
        out["preserved_wide_alus"] = preserved_wide_alus;
    } else {
        out["schema"] = "carry-only-boolean-v1";
        out["chains"] = std::move(records);
        out["alu_cells_removed"] = alu_removed;
        out["scope"] = "Whole mode-0/1/2 chains with no consumed, exported or kept SUM; no state changes. Experimental, not default.";
    }
    return {std::move(design), std::move(out)};
}

bool is_kept_cell_or_net(const OrderedMap<JsonValue>& attrs) {
    for (const char* k : {"keep", "dont_touch", "syn_preserve", "syn_keep"}) {
        const JsonValue* v = attrs.find_ptr(k);
        if (!v || v->is_null()) continue;
        if (v->is_int()) {
            if (v->as_i64() != 0) return true;
        } else if (v->is_bool()) {
            if (v->as_bool()) return true;
        } else if (v->is_string()) {
            std::string s = to_lower_ascii(v->as_str());
            if (s == "false" || s == "no" || s.empty()) continue;
            bool all01 = true;
            for (char c : s) if (c != '0' && c != '1') { all01 = false; break; }
            if (!all01) return true;
            if (parse_bin_u64(s) != 0) return true;
        }
    }
    return false;
}

// ============================================================================
// Optional DSP Output Register Packing & Register Alternatives
// ============================================================================
std::pair<RtlilDesign, JsonValue> dsp_output_register_pack(const RtlilDesign& source, bool allow_inverted_enable) {
    RtlilDesign design = source.clone_deep();
    OrderedMap<std::string> base;
    for (const auto& [k, v] : gowin_prim_sim_formats().at("MULTALU27X18")) {
        base.set(k, dsp_param_default_value(v));
    }
    base.set("C_SEL", "0");

    for (const auto& [_, mptr] : design.modules) {
        for (const auto& [__, c] : mptr->cells) {
            if (c.type == "GSR") {
                const auto* g = c.connections.find_ptr("GSRI");
                if (!g || g->size() != 1 || (*g)[0] != SIG_1) {
                    JsonValue rec = JsonValue::object();
                    rec["status"] = "proposed";
                    rec["schema"] = "gw5a-undefined-state-oreg-pack-v1";
                    rec["packed"] = JsonValue::array();
                    rec["removed_flipflops"] = 0;
                    JsonValue sk = JsonValue::object(); sk["user_gsr"] = 1;
                    rec["skipped"] = std::move(sk);
                    rec["model_sha256"] = effective_prim_sim_sha256();
                    return {std::move(design), std::move(rec)};
                }
            }
        }
    }

    JsonValue rows = JsonValue::array();
    OrderedMap<int64_t> skipped;
    int64_t removed_ffs = 0;

    for (const auto& [mn, mptr] : design.modules) {
        if (starts_with(mn, "~") || is_module_blackbox(*mptr)) continue;
        RtlilModule& m = design.mod_mut(mn);
        int32_t maximum = max_module_bit(m);
        struct LoadRef { std::string cell; std::string pin; int idx; };
        std::unordered_map<SigBit, std::vector<LoadRef>> loads;
        std::unordered_set<SigBit> protected_bits, state_preserved, initialized;
        std::unordered_map<SigBit, std::pair<SigBit, std::string>> inverted_enables;

        for (const auto& [cn, c] : m.cells) {
            for (const auto& [p, bs] : c.connections) {
                std::string pd = c.port_directions.value_or(p, "");
                if (pd == "input" || pd == "inout") {
                    for (size_t i = 0; i < bs.size(); ++i) loads[bs[i]].push_back(LoadRef{cn, p, static_cast<int>(i)});
                }
            }
        }
        for (const auto& [_, pr] : m.ports) {
            if (pr.direction != "input") {
                for (SigBit b : pr.bits) protected_bits.insert(b);
            }
        }
        for (const auto& [_, nr] : m.netnames) {
            if (is_kept_cell_or_net(nr.attributes)) {
                for (SigBit b : nr.bits) { protected_bits.insert(b); state_preserved.insert(b); }
            }
            if (const JsonValue* iv = nr.attributes.find_ptr("init"); iv && iv->is_string()) {
                const std::string& s = iv->as_str();
                for (size_t i = 0; i < nr.bits.size() && i < s.size(); ++i) {
                    char vc = s[s.size() - 1 - i];
                    if (vc == '0' || vc == '1') initialized.insert(nr.bits[i]);
                }
            }
        }

        for (const auto& dsp_name : m.cells.keys()) {
            RtlilCell* dsp_p = m.cells.find_ptr(dsp_name);
            if (!dsp_p || dsp_p->type != "MULTALU27X18") continue;
            if (is_kept_cell_or_net(dsp_p->attributes)) { skipped["preserved_dsp"] += 1; continue; }
            bool non_bypass = false;
            for (const auto& [pk, pv] : dsp_p->parameters) {
                if (!base.contains(pk) || base.at(pk) != pv) { non_bypass = true; break; }
            }
            if (non_bypass) { skipped["non_bypass_configuration"] += 1; continue; }
            bool casc = false;
            if (const auto* caso = dsp_p->connections.find_ptr("CASO")) {
                for (SigBit b : *caso) {
                    if (!loads[b].empty() || protected_bits.count(b)) { casc = true; break; }
                }
            }
            if (casc) { skipped["cascaded_output"] += 1; continue; }

            struct BankItem { int index; std::string name; SigBit q_bit; };
            std::vector<BankItem> bank;
            bool has_ctrl = false;
            SigBit ctrl_clk = SIG_0, ctrl_en = SIG_1;
            int ctrl_pol = 1;
            std::string reason;

            const auto& dout = dsp_p->connections.at("DOUT");
            for (size_t idx = 0; idx < dout.size(); ++idx) {
                SigBit bit = dout[idx];
                if (protected_bits.count(bit)) { reason = "observable_unregistered_output"; break; }
                const auto& sinks = loads[bit];
                if (sinks.empty()) continue;
                if (idx >= 45) { reason = "outside_proved_product_bits"; break; }
                if (sinks.size() != 1) { reason = "shared_product_output"; break; }
                const auto& sk = sinks[0];
                const auto& ff = m.cells.at(sk.cell);
                if (sk.pin != "D" || sk.idx != 0 || (ff.type != "$dff" && ff.type != "$dffe")
                    || parse_bin_u64(ff.parameters.at("WIDTH")) != 1ULL) {
                    reason = "non_ff_consumer"; break;
                }
                SigBit qb = ff.connections.at("Q")[0];
                if (is_kept_cell_or_net(ff.attributes) || state_preserved.count(qb)) {
                    reason = "preserved_state"; break;
                }
                int pol = ff.parameters.contains("EN_POLARITY") ? static_cast<int>(parse_bin_u64(ff.parameters.at("EN_POLARITY"))) : 1;
                size_t exp_p = (ff.type == "$dffe") ? 3u : 2u;
                if (ff.parameters.size() != exp_p || parse_bin_u64(ff.parameters.at("CLK_POLARITY")) != 1ULL
                    || (pol != 0 && pol != 1) || (pol == 0 && !allow_inverted_enable)) {
                    reason = "unsupported_ff_semantics"; break;
                }
                if (initialized.count(qb)) { reason = "defined_initial_state"; break; }
                SigBit clk_b = ff.connections.at("CLK")[0];
                SigBit en_b = (ff.type == "$dffe") ? ff.connections.at("EN")[0] : SIG_1;
                if (!has_ctrl) {
                    has_ctrl = true; ctrl_clk = clk_b; ctrl_en = en_b; ctrl_pol = pol;
                } else if (ctrl_clk != clk_b || ctrl_en != en_b || ctrl_pol != pol) {
                    reason = "mixed_clock_or_enable"; break;
                }
                bank.push_back(BankItem{static_cast<int>(idx), sk.cell, qb});
            }
            if (!reason.empty() || bank.empty()) {
                skipped[reason.empty() ? "no_output_state" : reason] += 1;
                continue;
            }
            std::unordered_set<SigBit> qset;
            for (const auto& bi : bank) qset.insert(bi.q_bit);
            if (qset.size() != bank.size()) { skipped["aliased_state"] += 1; continue; }

            SigBit eff_en = ctrl_en;
            std::string en_inv_name;
            if (ctrl_pol == 0) {
                if (eff_en == SIG_0) eff_en = SIG_1;
                else if (eff_en == SIG_1) eff_en = SIG_0;
                else {
                    auto it = inverted_enables.find(eff_en);
                    if (it == inverted_enables.end()) {
                        std::string inv = "ng_dsp_oreg_enable_inv_" + std::to_string(inverted_enables.size());
                        SigBit out_b = ++maximum;
                        RtlilCell ic;
                        ic.hide_name = 0;
                        ic.type = "$_NOT_";
                        ic.connections.set("A", {eff_en});
                        ic.connections.set("Y", {out_b});
                        ic.port_directions.set("A", "input");
                        ic.port_directions.set("Y", "output");
                        m.cells.set(inv, std::move(ic));
                        RtlilNetname nn;
                        nn.hide_name = 0;
                        nn.bits = {out_b};
                        m.netnames.set(inv + "_net", std::move(nn));
                        inverted_enables[eff_en] = {out_b, inv};
                        it = inverted_enables.find(eff_en);
                    }
                    eff_en = it->second.first;
                    en_inv_name = it->second.second;
                }
            }

            RtlilCell& dsp = m.cells.at(dsp_name);
            dsp.parameters = base;
            dsp.parameters.set("OREG_CLK", "CLK0");
            dsp.parameters.set("OREG_CE", "CE0");
            dsp.parameters.set("OREG_RESET", "RESET0");
            dsp.connections.set("CLK", {ctrl_clk, ctrl_clk});
            dsp.connections.set("CE", {eff_en, eff_en});
            dsp.connections.set("RESET", {SIG_0, SIG_0});
            auto& dout_mut = dsp.connections.at("DOUT");
            for (const auto& bi : bank) {
                dout_mut[static_cast<size_t>(bi.index)] = bi.q_bit;
                m.cells.erase(bi.name);
            }
            dsp.attributes.set("ng_packed_oreg", "1");
            removed_ffs += static_cast<int64_t>(bank.size());

            JsonValue prow = JsonValue::object();
            prow["module"] = mn;
            prow["instance"] = dsp_name;
            prow["enable_polarity"] = ctrl_pol;
            prow["enable_inverter"] = en_inv_name.empty() ? JsonValue(nullptr) : JsonValue(en_inv_name);
            rows.push_back(std::move(prow));
        }
    }

    JsonValue sk_obj = JsonValue::object();
    for (const auto& [k, v] : skipped) sk_obj[k] = v;
    JsonValue out = JsonValue::object();
    out["status"] = "proposed";
    out["schema"] = "gw5a-undefined-state-oreg-pack-v1";
    out["packed"] = std::move(rows);
    out["removed_flipflops"] = removed_ffs;
    out["skipped"] = std::move(sk_obj);
    out["allow_inverted_enable"] = allow_inverted_enable;
    out["model_sha256"] = effective_prim_sim_sha256();
    return {std::move(design), std::move(out)};
}

JsonValue dsp_output_register_prove_template(std::string_view out_dir, bool negative_control, int enable_polarity) {
    std::string out = resolve_path(out_dir);
    make_dirs(out, false);
    std::string lib_path = path_join(runtime_paths().gowin_sdk, "simlib\\gw5a\\prim_sim.v");
    std::string model_sha = verify_or_record_sdk_file_sha(lib_path, EXPECTED_PRIM_SIM_SHA, "Native DSP model (prim_sim.v)");
    std::string text = read_file_text(lib_path);
    std::string model;
    for (const auto& u : verilog_units(text)) {
        if (u.name == "MULTALU27X18") { model = text.substr(u.start, u.end - u.start); break; }
    }
    size_t gp = model.find("wire grstn = GSR.GSRO;");
    if (gp == std::string::npos) throw std::runtime_error("Unknown GSR model in MULTALU27X18");
    model.replace(gp, 22, "wire grstn = 1'b1;");

    std::string pins = ".A(a),.B(b),.C(48'b0),.D(26'b0),.SIA(27'b0),.CASI(48'b0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b0),.CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'b0),.CLK({clk,clk}),.CE({ce,ce}),.RESET(2'b0)";
    std::string cond = (enable_polarity == 0) ? "if(!ce)" : "if(ce)";
    std::string gate_en = (enable_polarity == 1) ? "ce" : "~ce";
    if (negative_control) gate_en = (enable_polarity == 1) ? "~ce" : "ce";
    std::string gate_pins = pins;
    size_t cp = gate_pins.find(".CE({ce,ce})");
    gate_pins.replace(cp, 12, ".CE({" + gate_en + "," + gate_en + "})");

    std::string wrapper =
        "module miter(input clk,ce,input [26:0] a,input [17:0] b,output equal,\n"
        "output [44:0] old_observe,packed_observe,product_observe);\n"
        "wire [47:0] product,packed_q;reg [44:0] old_q;\n"
        "always @(posedge clk) " + cond + " old_q <= product[44:0];\n"
        "MULTALU27X18 #(.C_SEL(1'b0)) gold(" + pins + ",.DOUT(product));\n"
        "MULTALU27X18 #(.C_SEL(1'b0),.OREG_CLK(\"CLK0\")) gate(" + gate_pins + ",.DOUT(packed_q));\n"
        "assign old_observe=old_q;assign packed_observe=packed_q[44:0];assign product_observe=product[44:0];assign equal=(old_q==packed_q[44:0]);endmodule\n";

    std::string src_v = path_join(out, "proof.v");
    write_file_text(src_v, model + "\n" + wrapper);
    std::string raw_json = path_join(out, "raw_transition.json");
    run_yosys_commands({
        "read_verilog -sv " + quote_yosys_path(src_v),
        "hierarchy -top miter", "proc", "flatten", "opt_expr",
        "write_json " + quote_yosys_path(raw_json)
    }, out, "prepare", 90.0);

    RtlilDesign raw = read_rtlil_json_file(raw_json);
    RtlilModule& m = raw.mod_mut("miter");
    for (const char* sig : {"old_q", "gate.out_reg_sync"}) {
        std::unordered_set<SigBit> sbits(m.netnames.at(sig).bits.begin(), m.netnames.at(sig).bits.end());
        std::string found_ff;
        int cnt = 0;
        for (const auto& [cn, c] : m.cells) {
            if (starts_with(c.type, "$dff") || starts_with(c.type, "$adff")) {
                for (SigBit qb : c.connections.at("Q")) {
                    if (sbits.count(qb)) { found_ff = cn; ++cnt; break; }
                }
            }
        }
        if (cnt != 1) throw std::runtime_error(std::string("Could not identify SDK register state: ") + sig);
        m.cells.at(found_ff).attributes.set("keep", "1");
    }
    std::string prot_json = path_join(out, "protected_transition.json");
    write_rtlil_json_file(prot_json, raw);
    std::string trans_json = path_join(out, "transition.json");
    JsonValue inv = run_yosys_commands({
        "read_json " + quote_yosys_path(prot_json),
        "opt -full", "wreduce", "dffunmap", "opt_clean", "check -assert",
        "write_json " + quote_yosys_path(trans_json),
        "sat -seq 2 -set-at 1 equal 1 -prove-skip 1 -prove equal 1 -verify -timeout 60"
    }, out, "proof", 90.0);

    std::string plog = read_file_text(path_join(out, "proof.log"));
    if (plog.find("SUCCESS!") == std::string::npos) {
        throw std::runtime_error("DSP OREG induction step was not proved");
    }

    JsonValue rec = JsonValue::object();
    rec["status"] = "proved_transition_refinement";
    rec["model_sha256"] = model_sha;
    rec["invocation"] = std::move(inv);
    rec["proof_source_sha256"] = sha256_file(src_v);
    rec["proof_log_sha256"] = sha256_file(path_join(out, "proof.log"));
    rec["transition_sha256"] = sha256_file(trans_json);
    rec["guard_preserved_state_cells"] = 2;
    rec["symbolic_product_retained"] = true;
    rec["data_bits_proved"] = 45;
    rec["negative_control"] = negative_control;
    rec["enable_polarity"] = enable_polarity;
    write_json_pretty(path_join(out, "manifest.json"), rec);
    return rec;
}

std::vector<std::pair<std::string, std::string>> dsp_output_register_validate(
    const RtlilDesign& design,
    const JsonValue& record,
    std::string_view folder
) {
    if (record["status"].as_str() != "proved" || record["model_sha256"].as_str() != effective_prim_sim_sha256()) {
        throw std::runtime_error("DSP packing has no valid pinned-model certificate");
    }
    const JsonValue& proof = record["transition_proof"];
    std::string pf = path_join(folder, "pack_proof");
    if (sha256_file(path_join(pf, "proof.v")) != proof["proof_source_sha256"].as_str()
        || sha256_file(path_join(pf, "proof.log")) != proof["proof_log_sha256"].as_str()
        || sha256_file(path_join(pf, "transition.json")) != proof["transition_sha256"].as_str()
        || read_file_text(path_join(pf, "proof.log")).find("SUCCESS!") == std::string::npos) {
        throw std::runtime_error("DSP transition proof artifact changed or failed");
    }
    std::vector<std::pair<std::string, std::string>> admitted;
    for (const auto& r : record["admitted_cells"].as_arr()) {
        admitted.emplace_back(r["module"].as_str(), r["instance"].as_str());
    }
    return admitted;
}

std::pair<RtlilDesign, JsonValue> register_alternatives_add(const RtlilDesign& source) {
    RtlilDesign design = source.clone_deep();
    JsonValue choices = JsonValue::array();
    for (const auto& [mn, mptr] : design.modules) {
        if (starts_with(mn, "~") || is_module_blackbox(*mptr)) continue;
        RtlilModule& m = design.mod_mut(mn);
        int32_t maximum = max_module_bit(m);
        int counter = 0;
        std::unordered_map<SigBit, SigBit> constants;
        for (const auto& [_, c] : m.cells) {
            if (c.type == "VCC" || c.type == "GND") {
                std::string p = (c.type == "VCC") ? "V" : "G";
                SigBit cv = (c.type == "VCC") ? SIG_1 : SIG_0;
                for (SigBit b : c.connections.at(p)) constants[b] = cv;
            }
        }
        auto resolve_c = [&](SigBit b) -> SigBit {
            auto it = constants.find(b);
            return (it == constants.end()) ? b : it->second;
        };
        auto emit_mux = [&](SigBit a, SigBit b, SigBit s) -> SigBit {
            a = resolve_c(a); b = resolve_c(b); s = resolve_c(s);
            if (s == SIG_0 || a == b) return a;
            if (s == SIG_1) return b;
            SigBit out_b = ++maximum;
            std::string nm = "ng_register_choice_" + std::to_string(++counter);
            RtlilCell mc;
            mc.type = "$_MUX_";
            mc.connections.set("A", {a}); mc.port_directions.set("A", "input");
            mc.connections.set("B", {b}); mc.port_directions.set("B", "input");
            mc.connections.set("S", {s}); mc.port_directions.set("S", "input");
            mc.connections.set("Y", {out_b}); mc.port_directions.set("Y", "output");
            m.cells.set(nm, std::move(mc));
            return out_b;
        };

        for (const auto& ffname : m.cells.keys()) {
            const RtlilCell& ff = m.cells.at(ffname);
            if ((ff.type != "DFFRE" && ff.type != "DFFSE" && ff.type != "DFFCE" && ff.type != "DFFPE")
                || is_attr_truthy(ff.attributes, "keep")) {
                continue;
            }
            std::string pin = (ff.type == "DFFRE") ? "RESET" : (ff.type == "DFFSE") ? "SET" : (ff.type == "DFFCE") ? "CLEAR" : "PRESET";
            SigBit d = resolve_c(ff.connections.at("D")[0]);
            SigBit ce = resolve_c(ff.connections.at("CE")[0]);
            SigBit r = resolve_c(ff.connections.at(pin)[0]);
            SigBit q = resolve_c(ff.connections.at("Q")[0]);
            if (ce == SIG_1 && r == SIG_0) continue;
            if ((pin == "CLEAR" || pin == "PRESET") && r != SIG_0) continue;

            SigBit val = emit_mux(q, d, ce);
            val = emit_mux(val, (ff.type == "DFFSE" || ff.type == "DFFPE") ? SIG_1 : SIG_0, r);
            std::string marker = "ng_register_alternative_" + std::to_string(choices.as_arr().size());
            RtlilNetname nn;
            nn.hide_name = 0;
            nn.bits = {val};
            nn.attributes.set("keep", "1");
            nn.attributes.set("ng_register_alternative", "1");
            m.netnames.set(marker, std::move(nn));

            JsonValue rec = JsonValue::object();
            rec["module"] = mn;
            rec["name"] = ffname;
            rec["marker"] = marker;
            rec["alt_d_bit"] = val;
            rec["ctrl_pin"] = pin;
            choices.push_back(std::move(rec));
        }
    }
    JsonValue out = JsonValue::object();
    out["schema"] = "register-alternatives-v1";
    out["choices"] = std::move(choices);
    return {std::move(design), std::move(out)};
}

std::pair<RtlilDesign, JsonValue> register_alternatives_select(
    const RtlilDesign& source,
    const JsonValue& record,
    double control_penalty_ps
) {
    RtlilDesign design = source.clone_deep();
    std::string cost_p = path_join(runtime_paths().res_root, "configs\\gw5a_arch_cost_v2.json");
    double wire = parse_json_file(cost_p)["cut_wire_ps"].as_f64();
    std::string ff_p = path_join(runtime_paths().res_root, "results\\timing\\gowin138_model_ff_checked.json");
    JsonValue ff_mod = parse_json_file(ff_p);
    double data_setup = ff_mod["ff_data_constraints"]["setup"]["envelope_ps"].as_f64();
    double clock_to_q = std::ceil(ff_mod["ff_clock_to_q"]["setup_max_ns"].as_f64() * 1000.0);

    OrderedMap<std::vector<const JsonValue*>> by_mod;
    for (const auto& r : record["choices"].as_arr()) {
        by_mod[r["module"].as_str()].push_back(&r);
    }

    JsonValue reports = JsonValue::array();
    int64_t tot_sel = 0;
    for (const auto& [mn, alts] : by_mod) {
        RtlilModule& m = design.mod_mut(mn);
        for (const JsonValue* r : alts) {
            m.netnames.erase((*r)["marker"].as_str());
        }
        OrderedMap<bool> comb;
        std::unordered_map<SigBit, std::string> drivers;
        for (const auto& [cn, c] : m.cells) {
            if (extract_lut_or_mux_logic(c).has_value() || c.type == "VCC" || c.type == "GND") {
                comb.set(cn, true);
                for (const auto& [p, bs] : c.connections) {
                    if (c.port_directions.value_or(p, "") == "output") {
                        for (SigBit b : bs) drivers[b] = cn;
                    }
                }
            }
        }
        std::unordered_map<SigBit, int> refs;
        std::unordered_set<std::string> live;
        int64_t area_cnt = 0;

        auto ref_bit = [&](SigBit root_b) {
            std::vector<SigBit> st{root_b};
            while (!st.empty()) {
                SigBit b = st.back(); st.pop_back();
                if (b < 0) continue;
                int nv = ++refs[b];
                if (nv != 1) continue;
                auto dit = drivers.find(b);
                if (dit == drivers.end()) continue;
                live.insert(dit->second);
                const auto& dc = m.cells.at(dit->second);
                if (starts_with(dc.type, "LUT")) ++area_cnt;
                for (const auto& [p, bs] : dc.connections) {
                    if (dc.port_directions.value_or(p, "") == "input") {
                        for (SigBit ib : bs) st.push_back(ib);
                    }
                }
            }
        };
        auto deref_bit = [&](SigBit root_b) {
            std::vector<SigBit> st{root_b};
            while (!st.empty()) {
                SigBit b = st.back(); st.pop_back();
                if (b < 0) continue;
                int nv = --refs[b];
                if (nv != 0) continue;
                auto dit = drivers.find(b);
                if (dit == drivers.end()) continue;
                live.erase(dit->second);
                const auto& dc = m.cells.at(dit->second);
                if (starts_with(dc.type, "LUT")) --area_cnt;
                for (const auto& [p, bs] : dc.connections) {
                    if (dc.port_directions.value_or(p, "") == "input") {
                        for (SigBit ib : bs) st.push_back(ib);
                    }
                }
            }
        };

        for (const auto& [cn, c] : m.cells) {
            if (!comb.contains(cn)) {
                for (const auto& [p, bs] : c.connections) {
                    if (c.port_directions.value_or(p, "") == "input") {
                        for (SigBit b : bs) ref_bit(b);
                    }
                }
            }
        }
        for (const auto& [_, pr] : m.ports) {
            if (pr.direction != "input") for (SigBit b : pr.bits) ref_bit(b);
        }
        for (const auto& [_, nr] : m.netnames) {
            if (is_attr_truthy(nr.attributes, "keep")) for (SigBit b : nr.bits) ref_bit(b);
        }
        for (const auto& [cn, _] : comb) {
            const auto& c = m.cells.at(cn);
            if (is_attr_truthy(c.attributes, "keep") && !is_attr_truthy(c.attributes, "ng_generated")) {
                for (const auto& [p, bs] : c.connections) {
                    if (c.port_directions.value_or(p, "") == "output") for (SigBit b : bs) ref_bit(b);
                }
            }
        }

        int64_t baseline = area_cnt;
        int64_t cap = baseline + static_cast<int64_t>(std::floor(static_cast<double>(baseline) * 0.05));

        std::unordered_map<SigBit, double> arrival{{SIG_0, 0.0}, {SIG_1, 0.0}};
        std::unordered_set<SigBit> inactive{SIG_0, SIG_1};
        for (const auto& [_, pr] : m.ports) {
            if (pr.direction == "input") for (SigBit b : pr.bits) arrival[b] = 0.0;
        }
        OrderedMap<bool> todo;
        for (const auto& [cn, c] : m.cells) {
            if (extract_lut_or_mux_logic(c).has_value()) {
                todo.set(cn, true);
            } else {
                for (const auto& [p, bs] : c.connections) {
                    if (c.port_directions.value_or(p, "") == "output") {
                        for (SigBit b : bs) {
                            bool is_c = (c.type == "VCC" || c.type == "GND");
                            arrival[b] = is_c ? 0.0 : clock_to_q;
                            if (is_c) inactive.insert(b);
                        }
                    }
                }
            }
        }
        auto incoming = [&](SigBit b) { return arrival[b] + (inactive.count(b) ? 0.0 : wire); };
        while (!todo.empty()) {
            std::vector<std::string> ready;
            for (const auto& [cn, _] : todo) {
                const auto& c = m.cells.at(cn);
                auto lf = *extract_lut_or_mux_logic(c);
                bool all_r = true;
                for (SigBit ib : lf.inputs) if (!arrival.count(ib)) { all_r = false; break; }
                if (!all_r) continue;
                bool all_inact = true;
                for (SigBit ib : lf.inputs) if (!inactive.count(ib)) { all_inact = false; break; }
                if (all_inact) {
                    arrival[lf.output] = 0.0;
                    inactive.insert(lf.output);
                } else if (starts_with(c.type, "MUX")) {
                    arrival[lf.output] = std::max({arrival[lf.inputs[0]] + 200.0, arrival[lf.inputs[1]] + 200.0, incoming(lf.inputs[2]) + 250.0});
                } else {
                    double mx = 0.0;
                    for (SigBit ib : lf.inputs) mx = std::max(mx, incoming(ib));
                    arrival[lf.output] = mx + 521.0;
                }
                ready.push_back(cn);
            }
            if (ready.empty()) throw std::runtime_error("Invalid combinational register choice graph");
            for (const auto& cn : ready) todo.erase(cn);
        }

        auto sink_cost = [&](SigBit d_b, SigBit ce_b, SigBit ctrl_b) {
            double mx = 0.0;
            if (!inactive.count(d_b)) mx = std::max(mx, arrival[d_b] + wire + data_setup);
            if (!inactive.count(ce_b)) mx = std::max(mx, arrival[ce_b] + wire + 335.0 + control_penalty_ps);
            if (!inactive.count(ctrl_b)) mx = std::max(mx, arrival[ctrl_b] + wire + 335.0 + control_penalty_ps);
            return mx;
        };

        struct CandItem { double old_s; size_t idx; const JsonValue* r; };
        std::vector<CandItem> cands;
        for (size_t i = 0; i < alts.size(); ++i) {
            const JsonValue* r = alts[i];
            const auto& ff = m.cells.at((*r)["name"].as_str());
            std::string cp = (*r)["ctrl_pin"].as_str();
            double os = sink_cost(ff.connections.at("D")[0], ff.connections.at("CE")[0], ff.connections.at(cp)[0]);
            cands.push_back(CandItem{os, i, r});
        }
        std::stable_sort(cands.begin(), cands.end(), [](const CandItem& a, const CandItem& b) { return a.old_s > b.old_s; });

        JsonValue chosen = JsonValue::array();
        for (const auto& ci : cands) {
            const JsonValue& r = *ci.r;
            std::string ffn = r["name"].as_str();
            std::string cp = r["ctrl_pin"].as_str();
            SigBit alt_d = static_cast<SigBit>(r["alt_d_bit"].as_i64());
            RtlilCell& ff = m.cells.at(ffn);
            SigBit old_d = ff.connections.at("D")[0];
            SigBit old_ce = ff.connections.at("CE")[0];
            SigBit old_c = ff.connections.at(cp)[0];
            double before = sink_cost(old_d, old_ce, old_c);
            double after = sink_cost(alt_d, SIG_1, SIG_0);
            if (after > before - 1e-6) continue;

            ref_bit(alt_d);
            deref_bit(old_d);
            deref_bit(old_ce);
            deref_bit(old_c);
            if (area_cnt > cap) {
                ref_bit(old_d); ref_bit(old_ce); ref_bit(old_c);
                deref_bit(alt_d);
                continue;
            }
            ff.connections.set("D", {alt_d});
            ff.connections.set("CE", {SIG_1});
            ff.connections.set(cp, {SIG_0});
            JsonValue ch = JsonValue::object();
            ch["name"] = ffn;
            ch["old_setup_ps"] = before;
            ch["new_setup_ps"] = after;
            chosen.push_back(std::move(ch));
            ++tot_sel;
        }
        for (const auto& [cn, _] : comb) {
            if (!live.count(cn)) m.cells.erase(cn);
        }
        JsonValue rep = JsonValue::object();
        rep["module"] = mn;
        rep["baseline_live_luts"] = baseline;
        rep["final_live_luts"] = area_cnt;
        rep["cap"] = cap;
        rep["alternatives"] = static_cast<int64_t>(alts.size());
        rep["selected"] = std::move(chosen);
        reports.push_back(std::move(rep));
    }

    JsonValue out = JsonValue::object();
    out["schema"] = "register-alternative-selection-v1";
    out["modules"] = std::move(reports);
    out["selected"] = tot_sel;
    return {std::move(design), std::move(out)};
}

namespace {

struct ExprNodeKey {
    uint8_t op = 0;
    int32_t a = 0;
    int32_t b = 0;
    int32_t c = 0;
    bool operator==(const ExprNodeKey&) const noexcept = default;
};

struct ExprNodeKeyHash {
    size_t operator()(const ExprNodeKey& k) const noexcept {
        uint64_t h = static_cast<uint64_t>(k.op) * 0x9E3779B97F4A7C15ULL;
        h ^= static_cast<uint64_t>(static_cast<uint32_t>(k.a)) + 0x9E3779B97F4A7C15ULL + (h << 6) + (h >> 2);
        h ^= static_cast<uint64_t>(static_cast<uint32_t>(k.b)) + 0x9E3779B97F4A7C15ULL + (h << 6) + (h >> 2);
        h ^= static_cast<uint64_t>(static_cast<uint32_t>(k.c)) + 0x9E3779B97F4A7C15ULL + (h << 6) + (h >> 2);
        return static_cast<size_t>(h);
    }
};

bool has_preserve_or_sync_attr(const OrderedMap<JsonValue>& attrs) {
    if (is_kept_cell_or_net(attrs)) return true;
    for (const auto& [k, v] : attrs) {
        std::string kl = to_lower_ascii(k);
        if (kl == "async_reg" || kl == "syn_async_reg" || kl == "synchronizer_identification") {
            std::string vl = v.is_string() ? to_lower_ascii(v.as_str()) : (v.is_int() ? std::to_string(v.as_i64()) : "");
            if (vl != "0" && vl != "false" && vl != "no" && vl != "off" && vl != "never" && !vl.empty()) {
                return true;
            }
        }
    }
    return false;
}

int64_t coinductive_merge_equivalent_ffs(RtlilDesign& design) {
    static const std::unordered_set<std::string> ff_types = {
        "DFF", "DFFE", "DFFR", "DFFRE", "DFFS", "DFFSE", "DFFC", "DFFCE", "DFFP", "DFFPE"
    };
    static const std::unordered_set<std::string> comb_gate_types = {
        "$_NOT_", "$_AND_", "$_OR_", "$_XOR_", "$_NAND_", "$_NOR_", "$_XNOR_", "$_ANDNOT_", "$_ORNOT_", "$_MUX_"
    };

    int64_t total_merged = 0;
    for (const auto& [mname, mptr] : design.modules) {
        if (is_module_blackbox(*mptr)) continue;
        RtlilModule& m = design.mod_mut(mname);

        std::unordered_set<SigBit> boundary_bits, keep_bits, conflict_init_bits;
        for (const auto& [_, pr] : m.ports) {
            for (SigBit b : pr.bits) if (b >= 0) boundary_bits.insert(b);
        }
        std::unordered_map<SigBit, char> init_map;
        for (const auto& [_, nr] : m.netnames) {
            if (is_kept_cell_or_net(nr.attributes)) {
                for (SigBit b : nr.bits) if (b >= 0) keep_bits.insert(b);
            }
            if (const auto* iv = nr.attributes.find_ptr("init"); iv && iv->is_str()) {
                const std::string& val = iv->as_str();
                size_t n_zip = std::min(nr.bits.size(), val.size());
                for (size_t bi = 0; bi < n_zip; ++bi) {
                    SigBit bit = nr.bits[bi];
                    if (bit < 0) continue;
                    char ch = static_cast<char>(std::tolower(static_cast<unsigned char>(val[val.size() - 1 - bi])));
                    auto [it, ins] = init_map.emplace(bit, ch);
                    if (!ins && it->second != ch) conflict_init_bits.insert(bit);
                }
            }
        }

        std::unordered_set<SigBit> user_named_bits, rdreg_bits;
        for (const auto& [nn, nr] : m.netnames) {
            bool is_rdreg = (nn.find("$rdreg") != std::string::npos);
            bool is_user = (nr.hide_name == 0 && !starts_with(nn, "$") && !is_rdreg);
            if (!is_rdreg && !is_user) continue;
            for (SigBit b : nr.bits) {
                if (b < 0) continue;
                if (is_rdreg) rdreg_bits.insert(b);
                if (is_user) user_named_bits.insert(b);
            }
        }

        struct FfRec {
            std::string name;
            SigBit d = SIG_0;
            SigBit q = SIG_0;
            SigBit ce = SIG_1;
            SigBit sync_r = SIG_0;
            SigBit sync_s = SIG_0;
            char init_ch = '0';
            int pref_score = 0;
        };
        std::unordered_map<std::string, FfRec> ffs;
        std::map<std::string, std::vector<std::string>> init_groups;

        for (const auto& [cname, c] : m.cells) {
            if (!ff_types.count(c.type) || has_preserve_or_sync_attr(c.attributes)) continue;
            const auto* qbs = c.connections.find_ptr("Q");
            const auto* dbs = c.connections.find_ptr("D");
            const auto* clkbs = c.connections.find_ptr("CLK");
            if (!qbs || qbs->size() != 1 || !dbs || dbs->size() != 1 || !clkbs || clkbs->size() != 1) continue;
            SigBit q = (*qbs)[0], d = (*dbs)[0], clk = (*clkbs)[0];
            if (clk == SIG_X || clk == SIG_Z) continue;
            if (q < 0 || boundary_bits.count(q) || keep_bits.count(q) || conflict_init_bits.count(q)) continue;

            char ib = 0;
            if (auto it = init_map.find(q); it != init_map.end()) ib = it->second;
            if (ib == 'x' || ib == 'z') continue;
            const std::string* p_init = c.parameters.find_ptr("INIT");
            if (p_init && !p_init->empty()) {
                char pib = p_init->back();
                if (pib != '0' && pib != '1') continue;
                if (ib != 0 && ib != pib) continue;
                ib = pib;
            }
            if (ib == 0) {
                ib = (c.type == "DFFS" || c.type == "DFFSE" || c.type == "DFFP" || c.type == "DFFPE") ? '1' : '0';
            }
            if (ib != '0' && ib != '1') continue;

            SigBit ce = SIG_1, sync_r = SIG_0, sync_s = SIG_0, async_c = SIG_0, async_p = SIG_0;
            bool bad_conn = false;
            for (const auto& [ck, cv] : c.connections) {
                if (ck == "D" || ck == "Q" || ck == "CLK") continue;
                if (cv.size() != 1) { bad_conn = true; break; }
                SigBit cb = cv[0];
                if (cb == SIG_X || cb == SIG_Z) { bad_conn = true; break; }
                if (ck == "CE") {
                    ce = cb;
                } else if (ck == "RESET" && (c.type == "DFFR" || c.type == "DFFRE")) {
                    sync_r = cb;
                } else if (ck == "SET" && (c.type == "DFFS" || c.type == "DFFSE")) {
                    sync_s = cb;
                } else if (ck == "CLEAR" && (c.type == "DFFC" || c.type == "DFFCE")) {
                    if (cb != SIG_0 && cb < 0) { bad_conn = true; break; }
                    async_c = cb;
                } else if (ck == "PRESET" && (c.type == "DFFP" || c.type == "DFFPE")) {
                    if (cb != SIG_0 && cb < 0) { bad_conn = true; break; }
                    async_p = cb;
                } else {
                    bad_conn = true;
                    break;
                }
            }
            if (bad_conn) continue;

            std::string sig = "POSFF|CLK=" + std::to_string(clk) + "|init=" + std::string(1, ib);
            if (async_c != SIG_0) sig += "|AC=" + std::to_string(async_c);
            if (async_p != SIG_0) sig += "|AP=" + std::to_string(async_p);
            std::vector<std::string> pk_sorted = c.parameters.keys();
            std::sort(pk_sorted.begin(), pk_sorted.end());
            for (const auto& pk : pk_sorted) {
                if (pk == "INIT") continue;
                sig += "|P:" + pk + "=" + c.parameters.at(pk);
            }

            int pref = ((sync_r >= 0 || sync_s >= 0) ? 4 : 0)
                     + ((ce >= 0) ? 2 : 0)
                     + (user_named_bits.count(q) ? 1 : 0)
                     - (rdreg_bits.count(q) ? 4 : 0);
            ffs[cname] = FfRec{cname, d, q, ce, sync_r, sync_s, ib, pref};
            init_groups[sig].push_back(cname);
        }

        auto ff_less = [&](const std::string& a, const std::string& b) {
            const FfRec& fa = ffs.at(a);
            const FfRec& fb = ffs.at(b);
            if (fa.pref_score != fb.pref_score) return fa.pref_score > fb.pref_score;
            return a < b;
        };

        std::vector<std::vector<std::string>> classes;
        for (auto& [_, grp] : init_groups) {
            if (grp.size() > 1) {
                std::sort(grp.begin(), grp.end(), ff_less);
                classes.push_back(std::move(grp));
            }
        }
        if (classes.empty()) continue;

        std::unordered_map<SigBit, const RtlilCell*> comb_drv;
        for (const auto& [_, c] : m.cells) {
            if (!comb_gate_types.count(c.type)) continue;
            if (const auto* ybs = c.connections.find_ptr("Y"); ybs && ybs->size() == 1 && (*ybs)[0] >= 0) {
                comb_drv[(*ybs)[0]] = &c;
            }
        }

        for (int iter = 0; iter < 24 && !classes.empty(); ++iter) {
            std::unordered_map<SigBit, SigBit> rep_of_q;
            for (const auto& cl : classes) {
                SigBit rq = ffs.at(cl[0]).q;
                for (const auto& cn : cl) rep_of_q[ffs.at(cn).q] = rq;
            }

            constexpr int32_t ID_C0 = 1, ID_C1 = 2, ID_CX = 3, ID_CZ = 4;
            int32_t next_id = 5;
            std::unordered_map<ExprNodeKey, int32_t, ExprNodeKeyHash> intern;
            std::unordered_map<SigBit, int32_t> memo;
            std::unordered_map<int32_t, int32_t> not_inv;
            std::unordered_set<int32_t> xz_nodes = {ID_CX, ID_CZ};
            std::unordered_set<SigBit> visiting;

            auto get_leaf = [&](SigBit b, uint8_t tag) -> int32_t {
                ExprNodeKey k{tag, static_cast<int32_t>(b), 0, 0};
                auto [it, ins] = intern.emplace(k, next_id);
                if (ins) ++next_id;
                return it->second;
            };
            auto get_node = [&](uint8_t op, int32_t a, int32_t b, int32_t c) -> int32_t {
                ExprNodeKey k{op, a, b, c};
                auto [it, ins] = intern.emplace(k, next_id);
                if (ins) {
                    if (xz_nodes.count(a) || xz_nodes.count(b) || xz_nodes.count(c)) {
                        xz_nodes.insert(next_id);
                    }
                    ++next_id;
                }
                return it->second;
            };
            auto is_bool = [&](int32_t id) -> bool {
                return id != 0 && !xz_nodes.count(id);
            };
            auto make_not = [&](int32_t a) -> int32_t {
                if (a == ID_C0) return ID_C1;
                if (a == ID_C1) return ID_C0;
                if (is_bool(a)) {
                    if (auto it = not_inv.find(a); it != not_inv.end()) return it->second;
                }
                int32_t n = get_node(20, a, 0, 0);
                if (is_bool(a)) {
                    not_inv[a] = n;
                    not_inv[n] = a;
                }
                return n;
            };
            auto make_and = [&](int32_t a, int32_t b) -> int32_t {
                if (a == ID_C0 || b == ID_C0) return ID_C0;
                if (a == ID_C1) return b;
                if (b == ID_C1) return a;
                if (is_bool(a) && is_bool(b)) {
                    if (a == b) return a;
                    if (auto it = not_inv.find(a); it != not_inv.end() && it->second == b) return ID_C0;
                }
                if (a > b) std::swap(a, b);
                return get_node(22, a, b, 0);
            };
            auto make_or = [&](int32_t a, int32_t b) -> int32_t {
                if (a == ID_C1 || b == ID_C1) return ID_C1;
                if (a == ID_C0) return b;
                if (b == ID_C0) return a;
                if (is_bool(a) && is_bool(b)) {
                    return make_not(make_and(make_not(a), make_not(b)));
                }
                if (a > b) std::swap(a, b);
                return get_node(23, a, b, 0);
            };
            auto make_xor = [&](int32_t a, int32_t b) -> int32_t {
                if (is_bool(a) && is_bool(b)) {
                    if (a == b) return ID_C0;
                    if (a == ID_C0) return b;
                    if (b == ID_C0) return a;
                    if (a == ID_C1) return make_not(b);
                    if (b == ID_C1) return make_not(a);
                    if (auto it = not_inv.find(a); it != not_inv.end() && it->second == b) return ID_C1;
                    bool flip = false;
                    if (auto ia = not_inv.find(a); ia != not_inv.end() && ia->second < a) { a = ia->second; flip = !flip; }
                    if (auto ib2 = not_inv.find(b); ib2 != not_inv.end() && ib2->second < b) { b = ib2->second; flip = !flip; }
                    if (a > b) std::swap(a, b);
                    int32_t xr = get_node(24, a, b, 0);
                    return flip ? make_not(xr) : xr;
                }
                if (a > b) std::swap(a, b);
                return get_node(24, a, b, 0);
            };
            auto make_mux = [&](int32_t s, int32_t a, int32_t b) -> int32_t {
                if (s == ID_C0) return a;
                if (s == ID_C1) return b;
                if (is_bool(s) && is_bool(a) && is_bool(b)) {
                    if (a == b) return a;
                    if (auto is = not_inv.find(s); is != not_inv.end() && is->second < s) {
                        s = is->second;
                        std::swap(a, b);
                    }
                    if (a == ID_C0 && b == ID_C1) return s;
                    if (a == ID_C1 && b == ID_C0) return make_not(s);
                    if (a == ID_C0) return make_and(s, b);
                    if (b == ID_C0) return make_and(make_not(s), a);
                    if (b == ID_C1) return make_or(s, a);
                    if (a == ID_C1) return make_or(make_not(s), b);
                    if (a == s) return make_and(s, b);
                    if (b == s) return make_or(s, a);
                } else if (a == b && is_bool(s)) {
                    return a;
                }
                return get_node(21, s, a, b);
            };

            std::function<int32_t(SigBit)> eval_bit = [&](SigBit b) -> int32_t {
                if (b == SIG_0) return ID_C0;
                if (b == SIG_1) return ID_C1;
                if (b == SIG_X) return ID_CX;
                if (b < 0)      return ID_CZ;
                if (auto qit = rep_of_q.find(b); qit != rep_of_q.end()) {
                    return get_leaf(qit->second, 10);
                }
                if (auto mit = memo.find(b); mit != memo.end()) return mit->second;
                auto dit = comb_drv.find(b);
                if (dit == comb_drv.end() || visiting.count(b)) {
                    return get_leaf(b, 11);
                }
                visiting.insert(b);
                const RtlilCell& gc = *dit->second;
                const std::string& gt = gc.type;
                int32_t res = 0;
                if (gt == "$_NOT_") {
                    res = make_not(eval_bit(gc.connections.at("A")[0]));
                } else if (gt == "$_MUX_") {
                    int32_t a = eval_bit(gc.connections.at("A")[0]);
                    int32_t bb = eval_bit(gc.connections.at("B")[0]);
                    int32_t s = eval_bit(gc.connections.at("S")[0]);
                    res = make_mux(s, a, bb);
                } else {
                    int32_t a = eval_bit(gc.connections.at("A")[0]);
                    int32_t bb = eval_bit(gc.connections.at("B")[0]);
                    if (gt == "$_AND_")         res = make_and(a, bb);
                    else if (gt == "$_OR_")     res = make_or(a, bb);
                    else if (gt == "$_XOR_")    res = make_xor(a, bb);
                    else if (gt == "$_NAND_")   res = make_not(make_and(a, bb));
                    else if (gt == "$_NOR_")    res = make_not(make_or(a, bb));
                    else if (gt == "$_XNOR_")   res = make_not(make_xor(a, bb));
                    else if (gt == "$_ANDNOT_") res = make_and(a, make_not(bb));
                    else                        res = make_or(a, make_not(bb));
                }
                visiting.erase(b);
                memo[b] = res;
                return res;
            };

            auto eval_ff_next = [&](const FfRec& f) -> int32_t {
                int32_t nxt = make_mux(eval_bit(f.ce), eval_bit(f.q), eval_bit(f.d));
                if (f.sync_r != SIG_0) nxt = make_mux(eval_bit(f.sync_r), nxt, ID_C0);
                if (f.sync_s != SIG_0) nxt = make_mux(eval_bit(f.sync_s), nxt, ID_C1);
                return nxt;
            };

            std::vector<std::vector<std::string>> next_classes;
            bool any_split = false;
            for (const auto& cl : classes) {
                std::map<int32_t, std::vector<std::string>> buckets;
                for (const auto& cn : cl) {
                    buckets[eval_ff_next(ffs.at(cn))].push_back(cn);
                }
                if (buckets.size() > 1) any_split = true;
                for (auto& [_, sub] : buckets) {
                    if (sub.size() > 1) next_classes.push_back(std::move(sub));
                }
            }
            classes = std::move(next_classes);
            if (!any_split) break;
        }

        if (classes.empty()) continue;
        std::unordered_map<SigBit, SigBit> subst;
        std::vector<std::string> to_erase;
        for (const auto& cl : classes) {
            SigBit rq = ffs.at(cl[0]).q;
            for (size_t i = 1; i < cl.size(); ++i) {
                subst[ffs.at(cl[i]).q] = rq;
                to_erase.push_back(cl[i]);
            }
        }
        if (to_erase.empty()) continue;

        for (const auto& cn : to_erase) m.cells.erase(cn);
        for (auto [_, c] : m.cells) {
            for (auto [__, bs] : c.connections) {
                for (SigBit& b : bs) {
                    if (auto sit = subst.find(b); sit != subst.end()) b = sit->second;
                }
            }
        }
        for (auto [_, nr] : m.netnames) {
            for (SigBit& b : nr.bits) {
                if (auto sit = subst.find(b); sit != subst.end()) b = sit->second;
            }
        }
        for (auto [_, pr] : m.ports) {
            for (SigBit& b : pr.bits) {
                if (auto sit = subst.find(b); sit != subst.end()) b = sit->second;
            }
        }
        total_merged += static_cast<int64_t>(to_erase.size());
    }
    return total_merged;
}

// Factor multi-branch 2-leaf `{Q, X}` `$mux`/`$pmux` DAGs on flip-flop `D` inputs into synchronous `EN`
// before `synth_gowin` (`memory_dff`), unlocking BSRAM output-register absorption and removing self-loop cones.
int64_t factor_two_leaf_mux_dffe(RtlilDesign& design) {
    struct Mux2Bit {
        SigBit s = SIG_0;
        SigBit a = SIG_0;
        SigBit b = SIG_0;
    };
    struct MuxKey3 {
        SigBit s = 0, a = 0, b = 0;
        bool operator==(const MuxKey3& o) const noexcept { return s == o.s && a == o.a && b == o.b; }
    };
    struct MuxKey3Hash {
        size_t operator()(const MuxKey3& k) const noexcept {
            size_t h = static_cast<size_t>(static_cast<uint32_t>(k.s)) * 0x9e3779b97f4a7c15ULL;
            h ^= static_cast<size_t>(static_cast<uint32_t>(k.a)) * 0xbf58476d1ce4e5b9ULL;
            h ^= static_cast<size_t>(static_cast<uint32_t>(k.b)) * 0x94d049bb133111ebULL;
            return h;
        }
    };
    struct PairBitHash {
        size_t operator()(const std::pair<SigBit, SigBit>& p) const noexcept {
            return (static_cast<size_t>(static_cast<uint32_t>(p.first)) * 0x9e3779b97f4a7c15ULL)
                 ^ static_cast<size_t>(static_cast<uint32_t>(p.second));
        }
    };

    int64_t total_factored_bits = 0;
    const std::string c0_32 = format_bin_u64(0, 32);
    const std::string c1_32 = format_bin_u64(1, 32);

    for (const auto& [mn, mptr] : design.modules) {
        if (is_module_blackbox(*mptr)) continue;
        RtlilModule& m = design.mod_mut(mn);

        std::unordered_map<SigBit, int> drv_cnt;
        for (const auto& [_, c] : m.cells) {
            for (const auto& [p, bs] : c.connections) {
                std::string pd = c.port_directions.value_or(p, "");
                if (pd == "output" || pd == "inout") {
                    for (SigBit b : bs) if (b >= 0) drv_cnt[b] += 1;
                }
            }
        }

        std::unordered_map<SigBit, Mux2Bit> mux_map;
        SigBit next_virt = -1000;
        for (const auto& [_, c] : m.cells) {
            if (c.type == "$mux" || c.type == "$_MUX_") {
                const auto* ab = c.connections.find_ptr("A");
                const auto* bb = c.connections.find_ptr("B");
                const auto* sb = c.connections.find_ptr("S");
                const auto* yb = c.connections.find_ptr("Y");
                if (!ab || !bb || !sb || !yb || sb->size() != 1 || ab->size() != yb->size() || bb->size() != yb->size()) continue;
                for (size_t i = 0; i < yb->size(); ++i) {
                    SigBit y = (*yb)[i];
                    if (y >= 0 && drv_cnt[y] == 1) mux_map[y] = Mux2Bit{(*sb)[0], (*ab)[i], (*bb)[i]};
                }
            } else if (c.type == "$pmux") {
                const auto* ab = c.connections.find_ptr("A");
                const auto* bb = c.connections.find_ptr("B");
                const auto* sb = c.connections.find_ptr("S");
                const auto* yb = c.connections.find_ptr("Y");
                if (!ab || !bb || !sb || !yb || yb->empty() || sb->empty()) continue;
                size_t w = yb->size(), k_sz = sb->size();
                if (ab->size() != w || bb->size() != w * k_sz) continue;
                for (size_t i = 0; i < w; ++i) {
                    SigBit y = (*yb)[i];
                    if (y < 0 || drv_cnt[y] != 1) continue;
                    SigBit cur = (*ab)[i];
                    for (size_t k = 0; k + 1 < k_sz; ++k) {
                        SigBit vn = next_virt--;
                        mux_map[vn] = Mux2Bit{(*sb)[k], cur, (*bb)[k * w + i]};
                        cur = vn;
                    }
                    mux_map[y] = Mux2Bit{(*sb)[k_sz - 1], cur, (*bb)[(k_sz - 1) * w + i]};
                }
            }
        }
        if (mux_map.empty()) continue;

        int32_t max_b = max_module_bit(m);
        int cell_seq = 0;
        OrderedMap<RtlilCell> added_cells;
        std::unordered_map<SigBit, SigBit> not_cache;
        std::unordered_map<std::pair<SigBit, SigBit>, SigBit, PairBitHash> and_cache;
        std::unordered_map<MuxKey3, SigBit, MuxKey3Hash> mux1_cache;

        auto fresh_cell_name = [&](const char* pfx) {
            std::string nm;
            do {
                nm = std::string("$ng_ce_") + pfx + "_" + std::to_string(++cell_seq);
            } while (m.cells.contains(nm) || added_cells.contains(nm));
            return nm;
        };

        auto emit_not1 = [&](SigBit a) -> SigBit {
            if (a == SIG_0) return SIG_1;
            if (a == SIG_1) return SIG_0;
            if (auto it = not_cache.find(a); it != not_cache.end()) return it->second;
            SigBit y = ++max_b;
            RtlilCell nc;
            nc.hide_name = 1;
            nc.type = "$not";
            nc.parameters.set("A_SIGNED", c0_32);
            nc.parameters.set("A_WIDTH", c1_32);
            nc.parameters.set("Y_WIDTH", c1_32);
            nc.port_directions.set("A", "input");
            nc.port_directions.set("Y", "output");
            nc.connections.set("A", {a});
            nc.connections.set("Y", {y});
            added_cells.set(fresh_cell_name("not"), std::move(nc));
            not_cache[a] = y;
            return y;
        };

        auto emit_and1 = [&](SigBit a, SigBit b) -> SigBit {
            if (a == SIG_0 || b == SIG_0) return SIG_0;
            if (a == SIG_1) return b;
            if (b == SIG_1) return a;
            if (a == b) return a;
            if (b < a) std::swap(a, b);
            if (auto it = and_cache.find({a, b}); it != and_cache.end()) return it->second;
            SigBit y = ++max_b;
            RtlilCell ac;
            ac.hide_name = 1;
            ac.type = "$and";
            ac.parameters.set("A_SIGNED", c0_32);
            ac.parameters.set("B_SIGNED", c0_32);
            ac.parameters.set("A_WIDTH", c1_32);
            ac.parameters.set("B_WIDTH", c1_32);
            ac.parameters.set("Y_WIDTH", c1_32);
            ac.port_directions.set("A", "input");
            ac.port_directions.set("B", "input");
            ac.port_directions.set("Y", "output");
            ac.connections.set("A", {a});
            ac.connections.set("B", {b});
            ac.connections.set("Y", {y});
            added_cells.set(fresh_cell_name("and"), std::move(ac));
            and_cache[{a, b}] = y;
            return y;
        };

        auto emit_mux1 = [&](SigBit s, SigBit a, SigBit b) -> SigBit {
            if (a == SIG_X) return b;
            if (b == SIG_X) return a;
            if (a == b) return a;
            if (s == SIG_0) return a;
            if (s == SIG_1) return b;
            if (a == SIG_0 && b == SIG_1) return s;
            if (a == SIG_1 && b == SIG_0) return emit_not1(s);
            MuxKey3 key{s, a, b};
            if (auto it = mux1_cache.find(key); it != mux1_cache.end()) return it->second;
            SigBit y = ++max_b;
            RtlilCell mc;
            mc.hide_name = 1;
            mc.type = "$mux";
            mc.parameters.set("WIDTH", c1_32);
            mc.port_directions.set("A", "input");
            mc.port_directions.set("B", "input");
            mc.port_directions.set("S", "input");
            mc.port_directions.set("Y", "output");
            mc.connections.set("A", {a});
            mc.connections.set("B", {b});
            mc.connections.set("S", {s});
            mc.connections.set("Y", {y});
            added_cells.set(fresh_cell_name("mux"), std::move(mc));
            mux1_cache[key] = y;
            return y;
        };

        auto is_supported_ff = [](const std::string& t) {
            return t == "$dff" || t == "$dffe" || t == "$adff" || t == "$adffe"
                || t == "$sdff" || t == "$sdffe" || t == "$dffsr" || t == "$dffsre";
        };

        std::vector<std::string> ff_names = m.cells.keys();
        std::vector<std::string> erase_ffs;

        for (const auto& ffn : ff_names) {
            if (!m.cells.contains(ffn)) continue;
            const RtlilCell& c_orig = m.cells.at(ffn);
            if (!is_supported_ff(c_orig.type) || is_kept_cell_or_net(c_orig.attributes)) continue;
            const auto* d_vec = c_orig.connections.find_ptr("D");
            const auto* q_vec = c_orig.connections.find_ptr("Q");
            if (!d_vec || !q_vec || d_vec->empty() || d_vec->size() != q_vec->size()) continue;
            size_t w = d_vec->size();

            std::vector<bool> bit_ok(w, false);
            std::vector<SigBit> bit_x(w, SIG_0);
            std::vector<SigBit> bit_en(w, SIG_0);
            bool any_ok = false;

            for (size_t i = 0; i < w; ++i) {
                SigBit qi = (*q_vec)[i];
                SigBit di = (*d_vec)[i];
                if (qi < 0 || di == qi || !mux_map.count(di)) continue;

                std::unordered_map<SigBit, uint8_t> st;
                std::unordered_map<SigBit, bool> has_q;
                bool bad = false;
                int visited_mux = 0;

                std::function<bool(SigBit)> dfs_q = [&](SigBit u) -> bool {
                    if (bad) return false;
                    if (u == qi || u == SIG_X) { has_q[u] = true; return true; }
                    auto mit = mux_map.find(u);
                    if (mit == mux_map.end()) { has_q[u] = false; return false; }
                    uint8_t s_cur = st[u];
                    if (s_cur == 1) { bad = true; return false; }
                    if (s_cur == 2) return has_q[u];
                    if (++visited_mux > 512) { bad = true; return false; }
                    st[u] = 1;
                    bool ha = dfs_q(mit->second.a);
                    bool hb = dfs_q(mit->second.b);
                    st[u] = 2;
                    bool hq = ha || hb;
                    has_q[u] = hq;
                    return hq;
                };

                if (!dfs_q(di) || bad) continue;

                bool leaf_ok = true;
                bool have_ext = false;
                SigBit ext_x = SIG_0;
                std::unordered_set<SigBit> seen_front;
                std::function<void(SigBit)> collect_ext = [&](SigBit u) {
                    if (!leaf_ok || u == qi || u == SIG_X) return;
                    if (!has_q[u]) {
                        if (u == SIG_Z) { leaf_ok = false; return; }
                        if (!have_ext) { have_ext = true; ext_x = u; }
                        else if (ext_x != u) { leaf_ok = false; }
                        return;
                    }
                    if (!seen_front.insert(u).second) return;
                    const Mux2Bit& mb = mux_map.at(u);
                    collect_ext(mb.a);
                    collect_ext(mb.b);
                };
                collect_ext(di);
                if (!leaf_ok) continue;

                if (!have_ext) {
                    bit_ok[i] = true;
                    bit_x[i] = SIG_0;
                    bit_en[i] = SIG_0;
                    any_ok = true;
                    continue;
                }
                if (ext_x == qi) continue;

                std::unordered_map<SigBit, SigBit> en_memo;
                std::function<SigBit(SigBit)> build_en = [&](SigBit u) -> SigBit {
                    if (u == qi) return SIG_0;
                    if (u == SIG_X) return SIG_X;
                    if (!has_q[u]) return SIG_1;
                    if (auto eit = en_memo.find(u); eit != en_memo.end()) return eit->second;
                    const Mux2Bit& mb = mux_map.at(u);
                    SigBit ea = build_en(mb.a);
                    SigBit eb = build_en(mb.b);
                    SigBit res = emit_mux1(mb.s, ea, eb);
                    en_memo[u] = res;
                    return res;
                };

                SigBit ev = build_en(di);
                if (ev == SIG_X) ev = SIG_1;
                bit_ok[i] = true;
                bit_x[i] = ext_x;
                bit_en[i] = ev;
                any_ok = true;
            }

            if (!any_ok) continue;

            auto apply_ce_to_ff = [&](RtlilCell& fc, const std::vector<SigBit>& new_d_bits, SigBit sub_en_bit) {
                fc.connections.set("D", new_d_bits);
                if (fc.connections.contains("EN")) {
                    SigBit old_raw = fc.connections.at("EN")[0];
                    bool pol1 = (parse_bin_u64(fc.parameters.value_or("EN_POLARITY", "1")) & 1ULL) != 0;
                    SigBit old_hi = pol1 ? old_raw : emit_not1(old_raw);
                    SigBit comb_en = emit_and1(old_hi, sub_en_bit);
                    fc.parameters.set("EN_POLARITY", c1_32);
                    fc.connections.set("EN", {comb_en});
                } else if (sub_en_bit != SIG_1) {
                    fc.type += "e";
                    fc.parameters.set("EN_POLARITY", c1_32);
                    fc.port_directions.set("EN", "input");
                    fc.connections.set("EN", {sub_en_bit});
                }
            };

            bool all_same = bit_ok[0];
            for (size_t i = 1; i < w && all_same; ++i) {
                if (!bit_ok[i] || bit_en[i] != bit_en[0]) all_same = false;
            }
            if (all_same) {
                apply_ce_to_ff(m.cells.at(ffn), bit_x, bit_en[0]);
                total_factored_bits += static_cast<int64_t>(w);
                continue;
            }

            // Slice multi-bit FF into contiguous runs sharing `(bit_ok, bit_en)`
            size_t lo = 0;
            int slice_idx = 0;
            while (lo < w) {
                size_t hi = lo;
                while (hi + 1 < w && bit_ok[hi + 1] == bit_ok[lo] && (!bit_ok[lo] || bit_en[hi + 1] == bit_en[lo])) ++hi;
                size_t rw = hi - lo + 1;

                RtlilCell sc = c_orig;
                sc.parameters.set("WIDTH", format_bin_u64(rw, 32));
                for (const char* vp : {"ARST_VALUE", "SRST_VALUE"}) {
                    if (const std::string* vs = sc.parameters.find_ptr(vp); vs && vs->size() == w) {
                        sc.parameters.set(vp, vs->substr(w - 1 - hi, rw));
                    }
                }
                for (const char* vec_p : {"D", "Q", "SET", "CLR"}) {
                    if (const auto* pv = c_orig.connections.find_ptr(vec_p); pv && pv->size() == w) {
                        sc.connections.set(vec_p, std::vector<SigBit>(pv->begin() + lo, pv->begin() + hi + 1));
                    }
                }
                if (bit_ok[lo]) {
                    std::vector<SigBit> slice_x(bit_x.begin() + lo, bit_x.begin() + hi + 1);
                    apply_ce_to_ff(sc, slice_x, bit_en[lo]);
                    total_factored_bits += static_cast<int64_t>(rw);
                }
                std::string snm = ffn + "$ng_slice_" + std::to_string(slice_idx++);
                while (m.cells.contains(snm) || added_cells.contains(snm)) snm += "_";
                added_cells.set(snm, std::move(sc));
                lo = hi + 1;
            }
            erase_ffs.push_back(ffn);
        }

        for (const auto& fn : erase_ffs) m.cells.erase(fn);
        for (auto [an, ac] : added_cells) m.cells.set(an, std::move(ac));
    }
    return total_factored_bits;
}

int64_t decompose_async_ram_mux_reg(RtlilDesign& design, bool no_lutram) {
    struct PairBitHash {
        size_t operator()(const std::pair<SigBit, SigBit>& p) const noexcept {
            return (static_cast<size_t>(static_cast<uint32_t>(p.first)) * 1315423911ULL)
                 ^ static_cast<size_t>(static_cast<uint32_t>(p.second));
        }
    };
    struct Mux2Bit { SigBit s = SIG_0, a = SIG_0, b = SIG_0; };
    struct MuxKey3 {
        SigBit s, a, b;
        bool operator==(const MuxKey3& o) const noexcept { return s == o.s && a == o.a && b == o.b; }
    };
    struct MuxKey3Hash {
        size_t operator()(const MuxKey3& k) const noexcept {
            size_t h = static_cast<size_t>(static_cast<uint32_t>(k.s));
            h ^= static_cast<size_t>(static_cast<uint32_t>(k.a)) * 0x9e3779b97f4a7c15ULL;
            h ^= static_cast<size_t>(static_cast<uint32_t>(k.b)) * 0xbf58476d1ce4e5b9ULL;
            return h;
        }
    };

    auto norm_memid = [](std::string s) -> std::string {
        s = trim(s);
        if (!s.empty() && s[0] == '\\') s.erase(s.begin());
        return s;
    };
    auto is_decomp_ff = [](const std::string& t) {
        return t == "$dff" || t == "$dffe" || t == "$adff" || t == "$adffe" || t == "$sdff" || t == "$sdffe";
    };

    const std::string c0_32 = format_bin_u64(0, 32);
    const std::string c1_32 = format_bin_u64(1, 32);
    int64_t total_decomp_bits = 0;

    for (const auto& [mn, mptr] : design.modules) {
        if (is_module_blackbox(*mptr)) continue;
        RtlilModule& m = design.mod_mut(mn);

        // Map synchronous write ports by MEMID -> (clk_bit, clk_pol)
        std::unordered_map<std::string, std::pair<SigBit, uint64_t>> mem_wr_clk;
        std::unordered_set<std::string> multi_clk_mem;
        for (const auto& [_, c] : m.cells) {
            if (c.type != "$memwr" && c.type != "$memwr_v2") continue;
            if ((parse_bin_u64(c.parameters.value_or("CLK_ENABLE", "0")) & 1ULL) == 0) continue;
            const auto* cb = c.connections.find_ptr("CLK");
            if (!cb || cb->empty() || (*cb)[0] < 0) continue;
            std::string mid = norm_memid(c.parameters.value_or("MEMID", ""));
            if (mid.empty()) continue;
            uint64_t cpol = parse_bin_u64(c.parameters.value_or("CLK_POLARITY", "1")) & 1ULL;
            std::pair<SigBit, uint64_t> cp{(*cb)[0], cpol};
            auto it = mem_wr_clk.find(mid);
            if (it == mem_wr_clk.end()) mem_wr_clk[mid] = cp;
            else if (it->second != cp) multi_clk_mem.insert(mid);
        }
        if (mem_wr_clk.empty()) continue;

        // Collect eligible async $memrd / $memrd_v2 cells
        std::vector<std::string> rd_cells;
        for (const auto& [cn, c] : m.cells) {
            if (c.type != "$memrd" && c.type != "$memrd_v2") continue;
            if ((parse_bin_u64(c.parameters.value_or("CLK_ENABLE", "0")) & 1ULL) != 0) continue;
            std::string mid = norm_memid(c.parameters.value_or("MEMID", ""));
            if (mid.empty() || !mem_wr_clk.count(mid) || multi_clk_mem.count(mid)) continue;
            const auto* db = c.connections.find_ptr("DATA");
            if (!db || db->empty()) continue;
            uint64_t w = db->size();
            uint64_t abits = parse_bin_u64(c.parameters.value_or("ABITS", "0"));
            uint64_t depth = (abits > 0 && abits <= 30) ? (1ULL << abits) : 0ULL;
            if (const auto* mj = m.memories.find_ptr(mid); mj && mj->is_object()) {
                if (const auto* sv = mj->get("size"); sv && sv->is_int()) depth = static_cast<uint64_t>(sv->as_i64());
            } else if (const auto* mj2 = m.memories.find_ptr("\\" + mid); mj2 && mj2->is_object()) {
                if (const auto* sv = mj2->get("size"); sv && sv->is_int()) depth = static_cast<uint64_t>(sv->as_i64());
            }
            if (depth * w >= 2048ULL || (no_lutram && depth >= 32ULL)) {
                rd_cells.push_back(cn);
            }
        }
        if (rd_cells.empty()) continue;

        std::unordered_map<SigBit, int> drv_cnt;
        std::unordered_map<SigBit, int> sink_use_cnt;
        std::unordered_set<SigBit> port_ext_bits;
        for (const auto& [_, pr] : m.ports) {
            if (pr.direction != "input") {
                for (SigBit b : pr.bits) if (b >= 0) { port_ext_bits.insert(b); sink_use_cnt[b] += 1; }
            }
        }
        for (const auto& [_, c] : m.cells) {
            for (const auto& [p, bs] : c.connections) {
                std::string pd = c.port_directions.value_or(p, "");
                if (pd == "output" || pd == "inout") {
                    for (SigBit b : bs) if (b >= 0) drv_cnt[b] += 1;
                }
                if (pd != "output") {
                    for (SigBit b : bs) if (b >= 0) sink_use_cnt[b] += 1;
                }
            }
        }

        std::unordered_map<SigBit, Mux2Bit> mux_map;
        std::unordered_set<SigBit> mux_sel_bits;
        SigBit next_virt = -1000;
        for (const auto& [_, c] : m.cells) {
            if (c.type == "$mux" || c.type == "$_MUX_") {
                const auto* ab = c.connections.find_ptr("A");
                const auto* bb = c.connections.find_ptr("B");
                const auto* sb = c.connections.find_ptr("S");
                const auto* yb = c.connections.find_ptr("Y");
                if (!ab || !bb || !sb || !yb || sb->size() != 1 || ab->size() != yb->size() || bb->size() != yb->size()) continue;
                if ((*sb)[0] >= 0) mux_sel_bits.insert((*sb)[0]);
                for (size_t i = 0; i < yb->size(); ++i) {
                    SigBit y = (*yb)[i];
                    if (y >= 0 && drv_cnt[y] == 1) mux_map[y] = Mux2Bit{(*sb)[0], (*ab)[i], (*bb)[i]};
                }
            } else if (c.type == "$pmux") {
                const auto* ab = c.connections.find_ptr("A");
                const auto* bb = c.connections.find_ptr("B");
                const auto* sb = c.connections.find_ptr("S");
                const auto* yb = c.connections.find_ptr("Y");
                if (!ab || !bb || !sb || !yb || yb->empty() || sb->empty()) continue;
                for (SigBit sbit : *sb) if (sbit >= 0) mux_sel_bits.insert(sbit);
                size_t w = yb->size(), k_sz = sb->size();
                if (ab->size() != w || bb->size() != w * k_sz) continue;
                for (size_t i = 0; i < w; ++i) {
                    SigBit y = (*yb)[i];
                    if (y < 0 || drv_cnt[y] != 1) continue;
                    SigBit cur = (*ab)[i];
                    for (size_t k = 0; k + 1 < k_sz; ++k) {
                        SigBit vn = next_virt--;
                        mux_map[vn] = Mux2Bit{(*sb)[k], cur, (*bb)[k * w + i]};
                        cur = vn;
                    }
                    mux_map[y] = Mux2Bit{(*sb)[k_sz - 1], cur, (*bb)[(k_sz - 1) * w + i]};
                }
            } else if (c.type == "$bmux") {
                const auto* ab = c.connections.find_ptr("A");
                const auto* sb = c.connections.find_ptr("S");
                const auto* yb = c.connections.find_ptr("Y");
                if (!ab || !sb || !yb || yb->empty() || sb->empty() || sb->size() > 8) continue;
                for (SigBit sbit : *sb) if (sbit >= 0) mux_sel_bits.insert(sbit);
                size_t w = yb->size(), s_sz = sb->size(), n_in = (size_t(1) << s_sz);
                if (ab->size() != w * n_in) continue;
                for (size_t i = 0; i < w; ++i) {
                    SigBit y = (*yb)[i];
                    if (y < 0 || drv_cnt[y] != 1) continue;
                    std::vector<SigBit> layer(n_in);
                    for (size_t j = 0; j < n_in; ++j) layer[j] = (*ab)[j * w + i];
                    for (size_t lvl = 0; lvl < s_sz; ++lvl) {
                        size_t half = layer.size() / 2;
                        std::vector<SigBit> nxt(half);
                        for (size_t j = 0; j < half; ++j) {
                            SigBit out_b = (lvl + 1 == s_sz) ? y : next_virt--;
                            mux_map[out_b] = Mux2Bit{(*sb)[lvl], layer[2 * j], layer[2 * j + 1]};
                            nxt[j] = out_b;
                        }
                        layer = std::move(nxt);
                    }
                }
            }
        }

        // Index mux data uses so we can verify $memrd.DATA never leaks outside the decomposed FF's D cone
        std::unordered_map<SigBit, int> mux_data_uses;
        for (const auto& [_, c] : m.cells) {
            if (c.type == "$mux" || c.type == "$_MUX_" || c.type == "$pmux") {
                for (const char* pn : {"A", "B"}) {
                    if (const auto* vb = c.connections.find_ptr(pn)) {
                        for (SigBit b : *vb) if (b >= 0) mux_data_uses[b] += 1;
                    }
                }
            } else if (c.type == "$bmux") {
                if (const auto* vb = c.connections.find_ptr("A")) {
                    for (SigBit b : *vb) if (b >= 0) mux_data_uses[b] += 1;
                }
            }
        }

        // Also recognize opt_expr-simplified 2:1 constant-branch muxes ($mux(s, 0, d) -> $and(s, d),
        // $mux(s, d, 1) -> $or(s, d)) where exactly one operand is in the forward RAM-data cone.
        {
            std::unordered_set<SigBit> ram_tainted;
            for (const auto& rd_nm : rd_cells) {
                if (const auto* dv = m.cells.at(rd_nm).connections.find_ptr("DATA")) {
                    for (SigBit b : *dv) if (b >= 0) ram_tainted.insert(b);
                }
            }
            bool chg = true;
            while (chg) {
                chg = false;
                for (const auto& [yb, mb] : mux_map) {
                    if (yb >= 0 && !ram_tainted.count(yb) && (ram_tainted.count(mb.a) || ram_tainted.count(mb.b))) {
                        ram_tainted.insert(yb);
                        chg = true;
                    }
                }
                for (const auto& [_, c] : m.cells) {
                    if (c.type != "$and" && c.type != "$_AND_" && c.type != "$or" && c.type != "$_OR_") continue;
                    const auto* ab = c.connections.find_ptr("A");
                    const auto* bb = c.connections.find_ptr("B");
                    const auto* yb = c.connections.find_ptr("Y");
                    if (!ab || !bb || !yb || ab->empty() || ab->size() != yb->size() || bb->size() != yb->size()) continue;
                    if (c.parameters.contains("A_SIGNED") && (parse_bin_u64(c.parameters.value_or("A_SIGNED", "0")) & 1ULL) != 0) continue;
                    if (c.parameters.contains("B_SIGNED") && (parse_bin_u64(c.parameters.value_or("B_SIGNED", "0")) & 1ULL) != 0) continue;
                    for (size_t k = 0; k < yb->size(); ++k) {
                        SigBit y = (*yb)[k], a = (*ab)[k], b = (*bb)[k];
                        if (y >= 0 && drv_cnt[y] == 1 && !ram_tainted.count(y) && (ram_tainted.count(a) != ram_tainted.count(b))) {
                            ram_tainted.insert(y);
                            chg = true;
                        }
                    }
                }
            }
            // Lift virtual mux nodes too
            bool vchg = true;
            while (vchg) {
                vchg = false;
                for (const auto& [yb, mb] : mux_map) {
                    if (yb < -999 && !ram_tainted.count(yb) && (ram_tainted.count(mb.a) || ram_tainted.count(mb.b))) {
                        ram_tainted.insert(yb);
                        vchg = true;
                    }
                }
            }
            for (const auto& [_, c] : m.cells) {
                bool is_and = (c.type == "$and" || c.type == "$_AND_");
                bool is_or  = (c.type == "$or"  || c.type == "$_OR_");
                if (!is_and && !is_or) continue;
                const auto* ab = c.connections.find_ptr("A");
                const auto* bb = c.connections.find_ptr("B");
                const auto* yb = c.connections.find_ptr("Y");
                if (!ab || !bb || !yb || ab->empty() || ab->size() != yb->size() || bb->size() != yb->size()) continue;
                if (c.parameters.contains("A_SIGNED") && (parse_bin_u64(c.parameters.value_or("A_SIGNED", "0")) & 1ULL) != 0) continue;
                if (c.parameters.contains("B_SIGNED") && (parse_bin_u64(c.parameters.value_or("B_SIGNED", "0")) & 1ULL) != 0) continue;
                for (size_t k = 0; k < yb->size(); ++k) {
                    SigBit y = (*yb)[k], a = (*ab)[k], b = (*bb)[k];
                    if (y < 0 || drv_cnt[y] != 1 || mux_map.count(y)) continue;
                    bool ta = ram_tainted.count(a) > 0, tb = ram_tainted.count(b) > 0;
                    if (ta == tb) continue;
                    SigBit ram_in = ta ? a : b;
                    SigBit ctrl_s = ta ? b : a;
                    if (ctrl_s >= 0) mux_sel_bits.insert(ctrl_s);
                    if (ram_in >= 0) mux_data_uses[ram_in] += 1;
                    if (is_and) mux_map[y] = Mux2Bit{ctrl_s, SIG_0, ram_in};
                    else        mux_map[y] = Mux2Bit{ctrl_s, ram_in, SIG_1};
                }
            }
        }

        int32_t max_b = max_module_bit(m);
        int cell_seq = 0;
        OrderedMap<RtlilCell> added_cells;
        std::unordered_map<SigBit, SigBit> not_cache, not_inv_of;
        std::unordered_map<std::pair<SigBit, SigBit>, SigBit, PairBitHash> and_cache;
        std::unordered_map<SigBit, std::vector<SigBit>> and_terms_of;
        std::unordered_map<MuxKey3, SigBit, MuxKey3Hash> mux1_cache;
        std::unordered_set<std::string> decomposed_ffs;

        // Pre-seed existing 1-bit $not / $and gates for canonicalization & Boolean equivalence checks
        struct BoolGate1 { uint8_t op = 0; SigBit a = SIG_0, b = SIG_0, s = SIG_0; }; // 1:not, 2:and, 3:or, 4:xor, 5:mux
        std::unordered_map<SigBit, BoolGate1> bgates;
        for (const auto& [_, c] : m.cells) {
            const auto* yb = c.connections.find_ptr("Y");
            if (!yb || yb->size() != 1 || (*yb)[0] < 0 || drv_cnt[(*yb)[0]] != 1) continue;
            SigBit y = (*yb)[0];
            if (c.type == "$not" || c.type == "$_NOT_" || c.type == "$logic_not") {
                const auto* ab = c.connections.find_ptr("A");
                if (ab && ab->size() == 1 && (*ab)[0] >= 0) {
                    not_cache[(*ab)[0]] = y;
                    not_inv_of[y] = (*ab)[0];
                    bgates[y] = BoolGate1{1, (*ab)[0], SIG_0, SIG_0};
                }
            } else if (c.type == "$and" || c.type == "$_AND_" || c.type == "$logic_and"
                    || c.type == "$or"  || c.type == "$_OR_"  || c.type == "$logic_or"
                    || c.type == "$xor" || c.type == "$_XOR_") {
                const auto* ab = c.connections.find_ptr("A");
                const auto* bb = c.connections.find_ptr("B");
                if (ab && bb && ab->size() == 1 && bb->size() == 1) {
                    uint8_t gop = (c.type == "$and" || c.type == "$_AND_" || c.type == "$logic_and") ? 2
                                : (c.type == "$or"  || c.type == "$_OR_"  || c.type == "$logic_or")  ? 3 : 4;
                    bgates[y] = BoolGate1{gop, (*ab)[0], (*bb)[0], SIG_0};
                }
            } else if (c.type == "$mux" || c.type == "$_MUX_") {
                const auto* ab = c.connections.find_ptr("A");
                const auto* bb = c.connections.find_ptr("B");
                const auto* sb = c.connections.find_ptr("S");
                if (ab && bb && sb && ab->size() == 1 && bb->size() == 1 && sb->size() == 1) {
                    bgates[y] = BoolGate1{5, (*ab)[0], (*bb)[0], (*sb)[0]};
                }
            }
        }

        auto fresh_cell_name = [&](const char* pfx) {
            std::string nm;
            do {
                nm = std::string("$ng_ramsh_") + pfx + "_" + std::to_string(++cell_seq);
            } while (m.cells.contains(nm) || added_cells.contains(nm));
            return nm;
        };
        auto emit_not1 = [&](SigBit a) -> SigBit {
            if (a == SIG_0) return SIG_1;
            if (a == SIG_1) return SIG_0;
            if (auto iit = not_inv_of.find(a); iit != not_inv_of.end()) return iit->second;
            if (auto it = not_cache.find(a); it != not_cache.end()) return it->second;
            SigBit y = ++max_b;
            RtlilCell nc;
            nc.hide_name = 1;
            nc.type = "$not";
            nc.parameters.set("A_SIGNED", c0_32);
            nc.parameters.set("A_WIDTH", c1_32);
            nc.parameters.set("Y_WIDTH", c1_32);
            nc.port_directions.set("A", "input");
            nc.port_directions.set("Y", "output");
            nc.connections.set("A", {a});
            nc.connections.set("Y", {y});
            added_cells.set(fresh_cell_name("not"), std::move(nc));
            not_cache[a] = y;
            not_inv_of[y] = a;
            bgates[y] = BoolGate1{1, a, SIG_0, SIG_0};
            return y;
        };
        auto emit_and_pair = [&](SigBit a, SigBit b) -> SigBit {
            if (b < a) std::swap(a, b);
            if (auto it = and_cache.find({a, b}); it != and_cache.end()) return it->second;
            SigBit y = ++max_b;
            RtlilCell ac;
            ac.hide_name = 1;
            ac.type = "$and";
            ac.parameters.set("A_SIGNED", c0_32);
            ac.parameters.set("B_SIGNED", c0_32);
            ac.parameters.set("A_WIDTH", c1_32);
            ac.parameters.set("B_WIDTH", c1_32);
            ac.parameters.set("Y_WIDTH", c1_32);
            ac.port_directions.set("A", "input");
            ac.port_directions.set("B", "input");
            ac.port_directions.set("Y", "output");
            ac.connections.set("A", {a});
            ac.connections.set("B", {b});
            ac.connections.set("Y", {y});
            added_cells.set(fresh_cell_name("and"), std::move(ac));
            and_cache[{a, b}] = y;
            bgates[y] = BoolGate1{2, a, b, SIG_0};
            return y;
        };
        auto emit_and1 = [&](SigBit a, SigBit b) -> SigBit {
            if (a == SIG_0 || b == SIG_0) return SIG_0;
            if (a == SIG_1) return b;
            if (b == SIG_1) return a;
            if (a == b) return a;
            std::vector<SigBit> terms;
            auto push_t = [&](SigBit x) {
                auto tit = and_terms_of.find(x);
                if (tit != and_terms_of.end()) terms.insert(terms.end(), tit->second.begin(), tit->second.end());
                else terms.push_back(x);
            };
            push_t(a);
            push_t(b);
            std::sort(terms.begin(), terms.end());
            terms.erase(std::unique(terms.begin(), terms.end()), terms.end());
            std::unordered_set<SigBit> tset(terms.begin(), terms.end());
            for (SigBit t : terms) {
                if ((not_cache.count(t) && tset.count(not_cache[t])) || (not_inv_of.count(t) && tset.count(not_inv_of[t]))) {
                    return SIG_0;
                }
            }
            SigBit cur = terms[0];
            for (size_t k = 1; k < terms.size(); ++k) cur = emit_and_pair(cur, terms[k]);
            and_terms_of[cur] = std::move(terms);
            return cur;
        };
        auto emit_mux1 = [&](SigBit s, SigBit a, SigBit b) -> SigBit {
            if (a == SIG_X) return b;
            if (b == SIG_X) return a;
            if (a == b) return a;
            if (s == SIG_0) return a;
            if (s == SIG_1) return b;
            if (a == SIG_0 && b == SIG_1) return s;
            if (a == SIG_1 && b == SIG_0) return emit_not1(s);
            if (a == SIG_0) return emit_and1(s, b);
            if (b == SIG_0) return emit_and1(emit_not1(s), a);
            MuxKey3 key{s, a, b};
            if (auto it = mux1_cache.find(key); it != mux1_cache.end()) return it->second;
            SigBit y = ++max_b;
            RtlilCell mc;
            mc.hide_name = 1;
            mc.type = "$mux";
            mc.parameters.set("WIDTH", c1_32);
            mc.port_directions.set("A", "input");
            mc.port_directions.set("B", "input");
            mc.port_directions.set("S", "input");
            mc.port_directions.set("Y", "output");
            mc.connections.set("A", {a});
            mc.connections.set("B", {b});
            mc.connections.set("S", {s});
            mc.connections.set("Y", {y});
            added_cells.set(fresh_cell_name("mux1"), std::move(mc));
            mux1_cache[key] = y;
            bgates[y] = BoolGate1{5, a, b, s};
            return y;
        };
        auto are_conds_equiv = [&](SigBit u, SigBit v) -> bool {
            if (u < 0 && u != SIG_0 && u != SIG_1) return false;
            if (v < 0 && v != SIG_0 && v != SIG_1) return false;
            if (u == v) return true;
            std::vector<SigBit> leaves;
            std::unordered_map<SigBit, int> leaf_id;
            std::unordered_set<SigBit> vis;
            int depth_cnt = 0;
            bool bad_const = false;
            std::function<void(SigBit)> gather = [&](SigBit x) {
                if (x < 0) {
                    if (x != SIG_0 && x != SIG_1) bad_const = true;
                    return;
                }
                if (!vis.insert(x).second || ++depth_cnt > 128) return;
                auto git = bgates.find(x);
                if (git == bgates.end()) {
                    leaf_id[x] = static_cast<int>(leaves.size());
                    leaves.push_back(x);
                    return;
                }
                const BoolGate1& g = git->second;
                gather(g.a);
                if (g.op >= 2) gather(g.b);
                if (g.op == 5) gather(g.s);
            };
            gather(u);
            gather(v);
            if (bad_const || depth_cnt > 128 || leaves.empty() || leaves.size() > 8) return false;
            int n_rows = (1 << leaves.size());
            auto eval_row = [&](SigBit root, int row) -> bool {
                std::unordered_map<SigBit, bool> memo;
                std::function<bool(SigBit)> ev = [&](SigBit x) -> bool {
                    if (x == SIG_0) return false;
                    if (x == SIG_1) return true;
                    if (auto lit = leaf_id.find(x); lit != leaf_id.end()) return ((row >> lit->second) & 1) != 0;
                    if (auto mit = memo.find(x); mit != memo.end()) return mit->second;
                    const BoolGate1& g = bgates.at(x);
                    bool res = false;
                    if (g.op == 1) res = !ev(g.a);
                    else if (g.op == 2) res = ev(g.a) && ev(g.b);
                    else if (g.op == 3) res = ev(g.a) || ev(g.b);
                    else if (g.op == 4) res = ev(g.a) != ev(g.b);
                    else if (g.op == 5) res = ev(g.s) ? ev(g.b) : ev(g.a);
                    return (memo[x] = res);
                };
                return ev(root);
            };
            for (int r = 0; r < n_rows; ++r) {
                if (eval_row(u, r) != eval_row(v, r)) return false;
            }
            return true;
        };

        for (const auto& rd_nm : rd_cells) {
            const RtlilCell& rdc = m.cells.at(rd_nm);
            std::string mid = norm_memid(rdc.parameters.value_or("MEMID", ""));
            auto [wr_clk, wr_pol] = mem_wr_clk.at(mid);
            const std::vector<SigBit>& data_vec = rdc.connections.at("DATA");
            size_t w = data_vec.size();

            std::unordered_map<SigBit, size_t> ram_bit_idx;
            bool bad_ram = false;
            for (size_t i = 0; i < w; ++i) {
                SigBit rb = data_vec[i];
                if (rb < 0 || ram_bit_idx.count(rb) || port_ext_bits.count(rb) || mux_sel_bits.count(rb)) {
                    bad_ram = true;
                    break;
                }
                ram_bit_idx[rb] = i;
            }
            if (bad_ram) continue;

            // Map each bit i = 0..w-1 of data_vec to its unique target FF bit slot (ffn_i, slot_k),
            // supporting both vector FFs and Verific-elaborated scalar 1-bit FFs (e.g. VERIFIC_DFFRS).
            std::vector<std::pair<std::string, size_t>> slot_for_bit(w, {"", 0});
            std::vector<bool> slot_found(w, false);
            for (const auto& [ffn, fc] : m.cells) {
                if (!is_decomp_ff(fc.type) || decomposed_ffs.count(ffn) || is_kept_cell_or_net(fc.attributes)) continue;
                const auto* cb = fc.connections.find_ptr("CLK");
                const auto* dv = fc.connections.find_ptr("D");
                const auto* qv = fc.connections.find_ptr("Q");
                if (!cb || cb->empty() || (*cb)[0] != wr_clk || !dv || !qv || dv->empty() || dv->size() != qv->size()) continue;
                if ((parse_bin_u64(fc.parameters.value_or("CLK_POLARITY", "1")) & 1ULL) != wr_pol) continue;

                for (size_t k = 0; k < dv->size() && !bad_ram; ++k) {
                    std::unordered_set<SigBit> vis;
                    int hit_idx = -1;
                    std::function<void(SigBit)> chk = [&](SigBit u) {
                        if (bad_ram || !vis.insert(u).second) return;
                        if (auto rit = ram_bit_idx.find(u); rit != ram_bit_idx.end()) {
                            int idx_i = static_cast<int>(rit->second);
                            if (hit_idx >= 0 && hit_idx != idx_i) { bad_ram = true; return; }
                            hit_idx = idx_i;
                            return;
                        }
                        if (auto mit = mux_map.find(u); mit != mux_map.end()) {
                            chk(mit->second.a);
                            chk(mit->second.b);
                        }
                    };
                    chk((*dv)[k]);
                    if (hit_idx >= 0) {
                        size_t bi = static_cast<size_t>(hit_idx);
                        if (slot_found[bi]) { bad_ram = true; break; }
                        slot_found[bi] = true;
                        slot_for_bit[bi] = {ffn, k};
                    }
                }
                if (bad_ram) break;
            }
            if (bad_ram) continue;
            bool all_bits_matched = true;
            std::set<std::pair<std::string, size_t>> uniq_slots;
            std::unordered_map<std::string, size_t> ff_hit_cnt;
            for (size_t i = 0; i < w; ++i) {
                if (!slot_found[i] || !uniq_slots.insert(slot_for_bit[i]).second) { all_bits_matched = false; break; }
                ff_hit_cnt[slot_for_bit[i].first] += 1;
            }
            if (!all_bits_matched) continue;
            // Require every participating FF cell to be 100% covered by this RAM read group (e.g. w x 1-bit or 1 x w-bit)
            for (const auto& [fn, cnt] : ff_hit_cnt) {
                if (m.cells.at(fn).connections.at("D").size() != cnt) { all_bits_matched = false; break; }
            }
            if (!all_bits_matched) continue;

            auto ff_ctrl_sig = [&](const RtlilCell& fc) {
                SigBit en_hi = SIG_1;
                if (fc.connections.contains("EN")) {
                    SigBit er = fc.connections.at("EN")[0];
                    bool ep = (parse_bin_u64(fc.parameters.value_or("EN_POLARITY", "1")) & 1ULL) != 0;
                    en_hi = ep ? er : emit_not1(er);
                }
                SigBit sr_in = SIG_1;
                if (fc.connections.contains("SRST")) {
                    SigBit sr = fc.connections.at("SRST")[0];
                    bool sp = (parse_bin_u64(fc.parameters.value_or("SRST_POLARITY", "1")) & 1ULL) != 0;
                    sr_in = sp ? emit_not1(sr) : sr;
                }
                SigBit ar_sig = fc.connections.contains("ARST") ? fc.connections.at("ARST")[0] : SIG_0;
                uint64_t ar_pol = fc.connections.contains("ARST") ? (parse_bin_u64(fc.parameters.value_or("ARST_POLARITY", "1")) & 1ULL) : 2ULL;
                return std::tuple{fc.type, en_hi, sr_in, ar_sig, ar_pol};
            };

            const RtlilCell& ff0 = m.cells.at(slot_for_bit[0].first);
            auto ref_ctrl = ff_ctrl_sig(ff0);
            for (const auto& [fn, _] : ff_hit_cnt) {
                auto cur_ctrl = ff_ctrl_sig(m.cells.at(fn));
                if (std::get<0>(cur_ctrl) != std::get<0>(ref_ctrl)
                    || std::get<3>(cur_ctrl) != std::get<3>(ref_ctrl)
                    || std::get<4>(cur_ctrl) != std::get<4>(ref_ctrl)
                    || !are_conds_equiv(std::get<1>(cur_ctrl), std::get<1>(ref_ctrl))
                    || !are_conds_equiv(std::get<2>(cur_ctrl), std::get<2>(ref_ctrl))) {
                    all_bits_matched = false;
                    break;
                }
            }
            if (!all_bits_matched) continue;

            std::vector<SigBit> d_orig(w), q_orig(w);
            for (size_t i = 0; i < w; ++i) {
                const auto& [fn, k] = slot_for_bit[i];
                const RtlilCell& fc = m.cells.at(fn);
                d_orig[i] = fc.connections.at("D")[k];
                q_orig[i] = fc.connections.at("Q")[k];
            }

            // Verify every bit i = 0..w-1 selects data_vec[i] under the exact same load_cond,
            // and count exact mux/D uses of all RAM-tainted wires so data_vec has zero external fanout.
            std::unordered_map<SigBit, int> cone_uses;
            std::vector<SigBit> cond_bits(w, SIG_0);
            std::vector<SigBit> other_bits(w, SIG_0);
            bool cone_ok = true;

            for (size_t i = 0; i < w && cone_ok; ++i) {
                SigBit di = d_orig[i];
                if (di >= 0) cone_uses[di] += 1;
                std::unordered_map<SigBit, uint8_t> st;
                std::unordered_map<SigBit, bool> has_ram;
                int steps = 0;
                std::function<bool(SigBit)> scan_i = [&](SigBit u) -> bool {
                    if (!cone_ok) return false;
                    if (auto rit = ram_bit_idx.find(u); rit != ram_bit_idx.end()) {
                        if (rit->second != i) { cone_ok = false; return false; }
                        has_ram[u] = true;
                        return true;
                    }
                    auto mit = mux_map.find(u);
                    if (mit == mux_map.end()) { has_ram[u] = false; return false; }
                    if (st[u] == 1 || ++steps > 512) { cone_ok = false; return false; }
                    if (st[u] == 2) return has_ram[u];
                    st[u] = 1;
                    if (ram_bit_idx.count(mit->second.s)) { cone_ok = false; return false; }
                    bool ha = scan_i(mit->second.a);
                    bool hb = scan_i(mit->second.b);
                    st[u] = 2;
                    bool hr = ha || hb;
                    has_ram[u] = hr;
                    if (hr) {
                        if (mit->second.a >= 0 && ha) cone_uses[mit->second.a] += 1;
                        if (mit->second.b >= 0 && hb) cone_uses[mit->second.b] += 1;
                    }
                    return hr;
                };
                if (!scan_i(di) || !cone_ok) { cone_ok = false; break; }

                std::unordered_map<SigBit, SigBit> cmemo, omemo;
                std::function<SigBit(SigBit)> build_c = [&](SigBit u) -> SigBit {
                    if (u == data_vec[i]) return SIG_1;
                    if (!has_ram[u]) return SIG_0;
                    if (auto it = cmemo.find(u); it != cmemo.end()) return it->second;
                    const Mux2Bit& mb = mux_map.at(u);
                    return cmemo[u] = emit_mux1(mb.s, build_c(mb.a), build_c(mb.b));
                };
                std::function<SigBit(SigBit)> build_o = [&](SigBit u) -> SigBit {
                    if (u == data_vec[i]) return SIG_0;
                    if (!has_ram[u]) return u;
                    if (auto it = omemo.find(u); it != omemo.end()) return it->second;
                    const Mux2Bit& mb = mux_map.at(u);
                    return omemo[u] = emit_mux1(mb.s, build_o(mb.a), build_o(mb.b));
                };
                cond_bits[i] = build_c(di);
                other_bits[i] = build_o(di);
                if (cond_bits[i] == SIG_0 || !are_conds_equiv(cond_bits[i], cond_bits[0])) cone_ok = false;
            }
            if (!cone_ok) continue;

            // Ensure no RAM-carrying wire (data_vec or intermediate mux wire) feeds anything outside these FFs' D cones
            for (SigBit rb : data_vec) {
                if (!cone_uses.count(rb) || sink_use_cnt[rb] != cone_uses[rb]) { cone_ok = false; break; }
            }
            for (const auto& [wb, ucnt] : cone_uses) {
                if (wb >= 0 && sink_use_cnt[wb] != ucnt) { cone_ok = false; break; }
            }
            if (!cone_ok) continue;

            SigBit load_cond = cond_bits[0];
            bool has_arst = ff0.connections.contains("ARST");
            bool has_srst = ff0.connections.contains("SRST");
            if (load_cond == SIG_1 && !has_arst && !has_srst) continue; // Already pure sync $dff/$dffe on DATA

            std::vector<SigBit> q_ram(w), q_sh(w);
            for (size_t i = 0; i < w; ++i) {
                q_ram[i] = ++max_b;
                q_sh[i]  = ++max_b;
            }
            SigBit use_ram_q = ++max_b;

            auto [_, ff_en_hi, srst_inact, _a1, _a2] = ref_ctrl;
            SigBit ram_en = emit_and1(emit_and1(ff_en_hi, srst_inact), load_cond);

            // 1. Pure synchronous $dffe R_ram on $memrd.DATA so memory_dff absorbs it into BSRAM
            RtlilCell c_ram;
            c_ram.hide_name = 1;
            c_ram.type = "$dffe";
            c_ram.parameters.set("CLK_POLARITY", format_bin_u64(wr_pol, 32));
            c_ram.parameters.set("EN_POLARITY", c1_32);
            c_ram.parameters.set("WIDTH", format_bin_u64(w, 32));
            c_ram.port_directions.set("CLK", "input");
            c_ram.port_directions.set("EN", "input");
            c_ram.port_directions.set("D", "input");
            c_ram.port_directions.set("Q", "output");
            c_ram.connections.set("CLK", {wr_clk});
            c_ram.connections.set("EN", {ram_en});
            c_ram.connections.set("D", data_vec);
            c_ram.connections.set("Q", q_ram);
            added_cells.set(fresh_cell_name("rdreg"), std::move(c_ram));

            // 2. 1-bit shadow selector register R_sel matching ff0's reset/enable semantics
            RtlilCell c_sel = ff0;
            c_sel.hide_name = 1;
            c_sel.attributes.clear();
            c_sel.parameters.set("WIDTH", c1_32);
            if (has_arst) c_sel.parameters.set("ARST_VALUE", "0");
            if (has_srst) c_sel.parameters.set("SRST_VALUE", "0");
            c_sel.connections.set("D", {load_cond});
            c_sel.connections.set("Q", {use_ram_q});
            added_cells.set(fresh_cell_name("sel"), std::move(c_sel));

            // 3. Migrate any power-up `init` attribute from q_orig (now a $mux output) onto q_sh + use_ram_q=0
            std::unordered_map<SigBit, SigBit> q_orig_to_sh;
            for (size_t i = 0; i < w; ++i) q_orig_to_sh[q_orig[i]] = q_sh[i];
            bool had_init = false;
            std::vector<std::pair<std::string, RtlilNetname>> extra_init_nets;
            for (const auto& nkey : m.netnames.keys()) {
                RtlilNetname& nr = m.netnames.at(nkey);
                if (!nr.attributes.contains("init")) continue;
                bool touches_q = false;
                for (SigBit b : nr.bits) if (q_orig_to_sh.count(b)) { touches_q = true; break; }
                if (!touches_q) continue;
                had_init = true;
                JsonValue init_v = nr.attributes.at("init");
                nr.attributes.erase("init");
                RtlilNetname sh_nn;
                sh_nn.hide_name = 1;
                sh_nn.bits = nr.bits;
                for (SigBit& b : sh_nn.bits) {
                    if (auto qit = q_orig_to_sh.find(b); qit != q_orig_to_sh.end()) b = qit->second;
                }
                sh_nn.attributes.set("init", std::move(init_v));
                extra_init_nets.emplace_back("$ng_ramsh_init_" + std::to_string(++cell_seq), std::move(sh_nn));
            }
            for (auto& [ik, iv] : extra_init_nets) m.netnames.set(ik, std::move(iv));
            if (had_init) {
                RtlilNetname sel_nn;
                sel_nn.hide_name = 1;
                sel_nn.bits = {use_ram_q};
                sel_nn.attributes.set("init", JsonValue("0"));
                m.netnames.set("$ng_ramsh_selinit_" + std::to_string(++cell_seq), std::move(sel_nn));
            }

            // 4. Update all participating shadow FFs to latch other_bits -> q_sh, and mux q_orig = use_ram_q ? q_ram : q_sh
            for (size_t i = 0; i < w; ++i) {
                const auto& [fn, k] = slot_for_bit[i];
                RtlilCell& fc = m.cells.at(fn);
                fc.connections.at("D")[k] = other_bits[i];
                fc.connections.at("Q")[k] = q_sh[i];
                decomposed_ffs.insert(fn);
            }

            RtlilCell c_outm;
            c_outm.hide_name = 1;
            c_outm.type = "$mux";
            c_outm.parameters.set("WIDTH", format_bin_u64(w, 32));
            c_outm.port_directions.set("A", "input");
            c_outm.port_directions.set("B", "input");
            c_outm.port_directions.set("S", "input");
            c_outm.port_directions.set("Y", "output");
            c_outm.connections.set("A", q_sh);
            c_outm.connections.set("B", q_ram);
            c_outm.connections.set("S", {use_ram_q});
            c_outm.connections.set("Y", q_orig);
            added_cells.set(fresh_cell_name("outmux"), std::move(c_outm));

            total_decomp_bits += static_cast<int64_t>(w);
        }

        for (auto [an, ac] : added_cells) m.cells.set(an, std::move(ac));
    }
    return total_decomp_bits;
}

} // namespace

// ============================================================================
// Stage 3: infer_resources_stage
// ============================================================================
JsonValue infer_resources_stage(
    std::string_view bridge_dir,
    std::string_view out_dir,
    std::string_view top,
    const std::vector<std::string>& keep_hierarchy,
    bool resource_optimizations,
    bool soft_comparisons,
    bool register_alternatives,
    const JsonValue& auto_hierarchy,
    bool pack_dsp_registers,
    bool pack_dsp_inverted_enable,
    bool no_lutram
) {
    std::string bridge = resolve_path(bridge_dir);
    std::string out = resolve_path(out_dir);
    make_dirs(out, false);

    std::string text = read_file_text(path_join(bridge, "design.v"));
    std::vector<std::string> dsp_library_cmds;
    std::vector<std::string> verified_multipliers;

    if (resource_optimizations) {
        // recover_small_memory.py is a pinned empty-list no-op (`return text, []`)
        write_json_pretty(path_join(out, "memory_recovery.json"), JsonValue::array());
        auto [dsp_text, dsp_rec] = gw5a_dsp_prepare(text);
        text = std::move(dsp_text);
        for (const auto& r : dsp_rec["multipliers"].as_arr()) {
            verified_multipliers.push_back(r["module"].as_str());
        }
        write_json_pretty(path_join(out, "dsp_map.json"), dsp_rec);
        std::string dsp_cells_p = path_join(out, "dsp_cells.v");
        write_file_text(dsp_cells_p, gw5a_dsp_primitive_library());
        dsp_library_cmds.push_back("read_verilog -overwrite -lib " + quote_yosys_path(dsp_cells_p));
    }

    bool do_auto_hier = (auto_hierarchy.is_bool() && auto_hierarchy.as_bool()) || auto_hierarchy.is_string();
    int min_mults = (auto_hierarchy.is_string() && auto_hierarchy.as_str() == "stateful") ? 0 : 2;
    JsonValue islands = do_auto_hier ? detect_hierarchy_islands(text, verified_multipliers, 16, min_mults) : JsonValue::array();
    std::unordered_set<std::string> automatic_set;
    for (const auto& r : islands.as_arr()) automatic_set.insert(r["module"].as_str());

    auto spans = verilog_units(text);
    JsonValue kept_mods = JsonValue::array();
    for (auto it = spans.rbegin(); it != spans.rend(); ++it) {
        std::string_view bare = starts_with(it->name, "\\") ? std::string_view(it->name).substr(1) : std::string_view(it->name);
        bool keep_it = automatic_set.count(it->name) > 0;
        if (!keep_it) {
            for (const auto& pat : keep_hierarchy) {
                if (fnmatch_case(bare, pat)) { keep_it = true; break; }
            }
        }
        if (keep_it) {
            text = text.substr(0, it->start) + "(* keep_hierarchy = 1 *)\n" + text.substr(it->start);
            kept_mods.push_back(it->name);
        }
    }

    std::string source_v = path_join(out, "hierarchy_input.v");
    write_file_text(source_v, text);

    JsonValue h_rec = JsonValue::object();
    JsonValue kp_arr = JsonValue::array();
    for (const auto& p : keep_hierarchy) kp_arr.push_back(p);
    h_rec["keep_patterns"] = std::move(kp_arr);
    h_rec["kept_modules"] = std::move(kept_mods);
    h_rec["automatic_dsp_islands"] = std::move(islands);
    h_rec["automatic_policy"] = auto_hierarchy;
    h_rec["policy"] = "Flatten ordinary logic for cross-module constant propagation; retain selected IP and SDC hierarchy.";
    write_json_pretty(path_join(out, "hierarchy.json"), h_rec);

    std::string common = "-top " + std::string(top) + " -family gw5a -strict-gw5a-dffs -noflatten -nowidelut -noiopads"
                       + (no_lutram ? " -nolutram" : "");

    std::string pre_ce_json = path_join(out, "pre_mem_ce.json");
    std::vector<std::string> pre_cmds = {
        "read_verilog -lib +/gowin/cells_sim.v",
        "read_verilog -lib +/gowin/cells_xtra_gw5a.v",
        "read_verilog -overwrite -lib " + quote_yosys_path(path_join(bridge, "vendor_cells.v")),
    };
    pre_cmds.insert(pre_cmds.end(), dsp_library_cmds.begin(), dsp_library_cmds.end());
    pre_cmds.push_back("read_verilog -sv " + quote_yosys_path(source_v));
    pre_cmds.push_back("hierarchy -check -top " + std::string(top));
    pre_cmds.push_back("proc");
    pre_cmds.push_back("flatten");
    pre_cmds.push_back("hierarchy -check -top " + std::string(top));
    pre_cmds.push_back("techmap -map +/techmap.v t:$shiftx");
    pre_cmds.push_back("select *");
    pre_cmds.push_back("opt_expr");
    pre_cmds.push_back("opt_clean");
    pre_cmds.push_back("opt_dff");
    pre_cmds.push_back("opt_expr");
    pre_cmds.push_back("opt_clean");
    pre_cmds.push_back("opt_dff -sat");
    pre_cmds.push_back("opt_expr");
    pre_cmds.push_back("opt_merge");
    pre_cmds.push_back("opt_clean");
    pre_cmds.push_back("blackbox =A:whitebox");
    pre_cmds.push_back("write_json " + quote_yosys_path(pre_ce_json));
    run_yosys_commands(pre_cmds, out, "pre_mem_ce");

    int64_t ce_factored_bits = 0;
    int64_t ram_decomp_bits = 0;
    {
        RtlilDesign pre_des = read_rtlil_json_file(pre_ce_json);
        ram_decomp_bits = decompose_async_ram_mux_reg(pre_des, no_lutram);
        ce_factored_bits = factor_two_leaf_mux_dffe(pre_des);

        // Sanitize residual wire `init` attributes before `synth_gowin` / `check -assert`:
        // 1) Preserve defined ('0'/'1') power-up bits on live sequential state outputs ($dff*, $dlatch*, $sr, $ff, sync $memrd*)
        //    on a single canonical netname per SigBit (detecting conflicting '0' vs '1' aliases).
        // 2) Tie completely undriven non-port SigBits carrying a defined ('0'/'1') `init` bit to constant SIG_0/SIG_1 and clear `init`.
        // 3) Clear spurious combinational/blackbox/port `init` bits to 'x' (removing `init` when all bits are 'x'), leaving undefined
        //    ('x'/'z') bits undefined while keeping `check -assert` enabled in `infer.ys`.
        static const std::unordered_set<std::string> seq_q_cell_types = {
            "$dff", "$dffe", "$adff", "$adffe", "$aldff", "$aldffe",
            "$sdff", "$sdffe", "$sdffce", "$dffsr", "$dffsre",
            "$dlatch", "$dlatchsr", "$sr", "$ff"
        };
        auto is_seq_init_driver = [&](const RtlilCell& c, const std::string& port_nm) -> bool {
            if (port_nm == "Q") {
                if (seq_q_cell_types.count(c.type)) return true;
                if (starts_with(c.type, "$_DFF") || starts_with(c.type, "$_SDFF") ||
                    starts_with(c.type, "$_DLATCH") || starts_with(c.type, "$_ALDFF") ||
                    starts_with(c.type, "$_SR_") || c.type == "$_FF_") {
                    return true;
                }
            }
            if (port_nm == "DATA" && (c.type == "$memrd" || c.type == "$memrd_v2")) {
                const std::string* ce_p = c.parameters.find_ptr("CLK_ENABLE");
                if (ce_p) {
                    for (char ch : *ce_p) if (ch == '1') return true;
                }
            }
            return false;
        };

        for (const auto& [mn, mptr] : pre_des.modules) {
            if (is_module_blackbox(*mptr)) continue;
            RtlilModule& pm = pre_des.mod_mut(mn);

            std::unordered_set<SigBit> port_in_bits;
            for (const auto& [_, pr] : pm.ports) {
                if (pr.direction == "input" || pr.direction == "inout") {
                    for (SigBit b : pr.bits) if (b >= 0) port_in_bits.insert(b);
                }
            }
            std::unordered_set<SigBit> seq_drv_bits, other_drv_bits;
            for (const auto& [_, c] : pm.cells) {
                bool submod_is_nonbb = pre_des.modules.contains(c.type) && !is_module_blackbox(*pre_des.modules.at(c.type));
                for (const auto& [pnm, bs] : c.connections) {
                    std::string dir = c.port_directions.value_or(pnm, "");
                    if (dir.empty() && submod_is_nonbb) {
                        const auto* sp = pre_des.modules.at(c.type)->ports.find_ptr(pnm);
                        if (sp) dir = sp->direction;
                    }
                    bool is_drv = (dir == "output" || dir == "inout")
                               || (dir.empty() && (pnm == "Y" || pnm == "Q" || pnm == "O" || pnm == "F" || pnm == "DOUT" || pnm == "DO" || pnm == "DATA"));
                    if (!is_drv) continue;
                    bool seq_ok = is_seq_init_driver(c, pnm);
                    for (SigBit b : bs) {
                        if (b < 0) continue;
                        if (seq_ok) seq_drv_bits.insert(b);
                        else other_drv_bits.insert(b);
                    }
                }
            }

            struct InitOwner {
                char val = 'x';
                std::string net_name;
                int score = -1;
            };
            std::unordered_map<SigBit, InitOwner> best_seq_owner;
            std::unordered_map<SigBit, SigBit> undriven_const_tie;

            auto decode_net_init_msb = [](const RtlilNetname& nr, std::string& out_msb) -> bool {
                const JsonValue* iv = nr.attributes.find_ptr("init");
                if (!iv || iv->is_null()) return false;
                const size_t w = nr.bits.size();
                if (w == 0) return false;
                if (iv->is_int()) {
                    uint64_t u = static_cast<uint64_t>(iv->as_i64());
                    out_msb = format_bin_u64(u, static_cast<int>(w));
                    return true;
                }
                if (!iv->is_string()) return false;
                std::string s = to_lower_ascii(iv->as_str());
                if (s.size() < w) {
                    bool only_01 = true;
                    for (char ch : s) if (ch != '0' && ch != '1') { only_01 = false; break; }
                    s = std::string(w - s.size(), only_01 ? '0' : 'x') + s;
                } else if (s.size() > w) {
                    s = s.substr(s.size() - w);
                }
                out_msb = std::move(s);
                return true;
            };

            // Pass 1: inspect all defined ('0'/'1') `init` bits in module `mn`
            for (const auto& [nn, nr] : pm.netnames) {
                std::string msb;
                if (!decode_net_init_msb(nr, msb)) continue;
                const size_t w = nr.bits.size();
                int net_score = (nr.hide_name == 0 ? 10 : 0) + (w == 1 ? 4 : 0) + (is_attr_truthy(nr.attributes, "keep") ? 2 : 0);
                for (size_t bi = 0; bi < w; ++bi) {
                    char cv = msb[w - 1 - bi];
                    if (cv != '0' && cv != '1') continue;
                    SigBit sb = nr.bits[bi];
                    if (sb < 0) continue;
                    bool has_seq = seq_drv_bits.count(sb) > 0 && !other_drv_bits.count(sb) && !port_in_bits.count(sb);
                    bool is_undriven = !seq_drv_bits.count(sb) && !other_drv_bits.count(sb) && !port_in_bits.count(sb);
                    if (has_seq) {
                        auto it = best_seq_owner.find(sb);
                        if (it == best_seq_owner.end()) {
                            best_seq_owner[sb] = InitOwner{cv, nn, net_score};
                        } else {
                            if (it->second.val != cv) {
                                throw std::runtime_error(
                                    "Conflicting initial values ('" + std::string(1, it->second.val) + "' on '" + it->second.net_name
                                    + "' vs '" + std::string(1, cv) + "' on '" + nn + "') on sequential state bit in module '" + mn + "'"
                                );
                            }
                            if (net_score > it->second.score) {
                                it->second.net_name = nn;
                                it->second.score = net_score;
                            }
                        }
                    } else if (is_undriven) {
                        SigBit tie_v = (cv == '1') ? SIG_1 : SIG_0;
                        auto uit = undriven_const_tie.find(sb);
                        if (uit != undriven_const_tie.end() && uit->second != tie_v) {
                            throw std::runtime_error(
                                "Conflicting initial values on undriven net '" + nn + "' in module '" + mn + "'"
                            );
                        }
                        undriven_const_tie[sb] = tie_v;
                    }
                }
            }

            // Pass 2: rewrite `init` attributes so only canonical sequential owners retain defined '0'/'1' bits
            for (const auto& nn : pm.netnames.keys()) {
                RtlilNetname& nr = pm.netnames.at(nn);
                std::string msb;
                if (!decode_net_init_msb(nr, msb)) continue;
                const size_t w = nr.bits.size();
                bool any_kept = false;
                std::unordered_set<SigBit> seen_in_this_net;
                for (size_t bi = 0; bi < w; ++bi) {
                    char& cv = msb[w - 1 - bi];
                    if (cv != '0' && cv != '1') { cv = 'x'; continue; }
                    SigBit sb = nr.bits[bi];
                    auto oit = (sb >= 0) ? best_seq_owner.find(sb) : best_seq_owner.end();
                    if (oit != best_seq_owner.end() && oit->second.net_name == nn && seen_in_this_net.insert(sb).second) {
                        any_kept = true;
                    } else {
                        cv = 'x';
                    }
                }
                if (any_kept) {
                    nr.attributes.set("init", JsonValue(msb));
                } else {
                    nr.attributes.erase("init");
                }
            }

            // Pass 3: tie undriven defined-init SigBits to constant SIG_0/SIG_1 while leaving undefined 'x' SigBits untouched
            if (!undriven_const_tie.empty()) {
                auto map_vec = [&](std::vector<SigBit>& bs) {
                    for (SigBit& b : bs) {
                        auto tit = undriven_const_tie.find(b);
                        if (tit != undriven_const_tie.end()) b = tit->second;
                    }
                };
                for (const auto& pn : pm.ports.keys()) map_vec(pm.ports.at(pn).bits);
                for (const auto& cn : pm.cells.keys()) {
                    RtlilCell& c = pm.cells.at(cn);
                    for (const auto& cpn : c.connections.keys()) map_vec(c.connections.at(cpn));
                }
                for (const auto& nn : pm.netnames.keys()) map_vec(pm.netnames.at(nn).bits);
            }

            std::vector<std::string> drop_auto;
            for (const auto& [nn, nr] : pm.netnames) {
                if (nr.hide_name != 0 && nr.attributes.empty() && starts_with(nn, "$auto$jsonparse.cc:")) {
                    drop_auto.push_back(nn);
                }
            }
            for (const auto& nn : drop_auto) pm.netnames.erase(nn);
        }
        write_rtlil_json_file(pre_ce_json, pre_des);
    }

    std::string seed_autoidx_il = path_join(out, "seed_autoidx.il");
    write_file_text(seed_autoidx_il, "autoidx 2000000\n");

    std::vector<std::string> commands = {
        "read_rtlil " + quote_yosys_path(seed_autoidx_il),
        "read_json " + quote_yosys_path(pre_ce_json),
        "read_rtlil " + quote_yosys_path(seed_autoidx_il),
        "read_verilog -overwrite -lib +/gowin/cells_sim.v",
        "read_verilog -overwrite -lib +/gowin/cells_xtra_gw5a.v",
        "read_verilog -overwrite -lib " + quote_yosys_path(path_join(bridge, "vendor_cells.v")),
    };
    commands.insert(commands.end(), dsp_library_cmds.begin(), dsp_library_cmds.end());
    if (ce_factored_bits > 0 || ram_decomp_bits > 0) {
        commands.push_back("opt_expr");
        commands.push_back("opt_clean");
        commands.push_back("opt_dff");
        commands.push_back("opt_expr");
        commands.push_back("opt_clean");
    }
    commands.push_back("synth_gowin " + common + " -run coarse:map_gates");
    if (pack_dsp_registers) {
        commands.push_back("write_rtlil " + quote_yosys_path(path_join(out, "inferred.il")));
    }

    std::string res_out_json = path_join(out, soft_comparisons ? "resources_hard.json" : "resources.json");
    std::vector<std::string> tail = {
        "synth_gowin " + common + " -run map_gates:map_luts",
        "hierarchy -check -top " + std::string(top),
        "check -assert",
        "blackbox =A:whitebox",
        "check -assert -noinit",
        "write_json " + quote_yosys_path(res_out_json),
    };

    JsonValue invocation;
    std::optional<JsonValue> packing_opt;
    if (pack_dsp_registers) {
        std::string coarse_src = path_join(out, "coarse_source.json");
        std::vector<std::string> ccmds = commands;
        ccmds.push_back("blackbox =A:whitebox");
        ccmds.push_back("write_json " + quote_yosys_path(coarse_src));
        JsonValue coarse_inv = run_yosys_commands(ccmds, out, "infer_coarse");

        auto [packed_des, packing] = dsp_output_register_pack(read_rtlil_json_file(coarse_src), pack_dsp_inverted_enable);
        packing["transition_proof"] = dsp_output_register_prove_template(path_join(out, "pack_proof"), false, 1);
        bool has_inv_pol = false;
        for (const auto& r : packing["packed"].as_arr()) {
            if (r["enable_polarity"].as_i64(1) == 0) { has_inv_pol = true; break; }
        }
        if (has_inv_pol) {
            packing["inverted_enable_proof"] = dsp_output_register_prove_template(path_join(out, "pack_inverted_enable_proof"), false, 0);
        }
        packing["status"] = "proved";
        packing["implementation_sha256"] = sha256_file(runtime_paths().core_dll);
        packing["input_sha256"] = sha256_file(coarse_src);
        packing["input_rtlil_sha256"] = sha256_file(path_join(out, "inferred.il"));
        std::string coarse_packed_p = path_join(out, "coarse_packed.json");
        write_rtlil_json_file(coarse_packed_p, packed_des);
        packing["packed_sha256"] = sha256_file(coarse_packed_p);

        std::string il_head = read_file_text(path_join(out, "inferred.il"));
        int counter = -1;
        int lcnt = 0;
        for (const auto& ln : split_lines(il_head)) {
            if (++lcnt > 16) break;
            if (starts_with(ln, "autoidx ")) {
                counter = std::stoi(trim(std::string_view(ln).substr(8)));
                break;
            }
        }
        if (counter < 0) throw std::runtime_error("Coarse RTLIL has no automatic-name counter");
        std::string rest_il = path_join(out, "restore_autoidx.il");
        write_file_text(rest_il, "autoidx " + std::to_string(std::max(counter, 4000000)) + "\n");
        packing["autoidx_restore_sha256"] = sha256_file(rest_il);

        std::vector<std::string> reload = {
            "read_rtlil " + quote_yosys_path(rest_il),
            "read_json " + quote_yosys_path(coarse_packed_p),
            "read_rtlil " + quote_yosys_path(rest_il),
            "read_verilog -overwrite -lib +/gowin/cells_sim.v",
            "read_verilog -overwrite -lib +/gowin/cells_xtra_gw5a.v",
            "read_verilog -overwrite -lib " + quote_yosys_path(path_join(bridge, "vendor_cells.v")),
        };
        reload.insert(reload.end(), dsp_library_cmds.begin(), dsp_library_cmds.end());
        reload.insert(reload.end(), tail.begin(), tail.end());
        invocation = run_yosys_commands(reload, out, "infer");
        invocation["coarse_before_packing"] = std::move(coarse_inv);
        packing_opt = std::move(packing);
    } else {
        std::vector<std::string> full_cmds = commands;
        full_cmds.insert(full_cmds.end(), tail.begin(), tail.end());
        invocation = run_yosys_commands(full_cmds, out, "infer");
    }
    remove_file_if_exists(pre_ce_json);
    if (ce_factored_bits > 0) {
        invocation["factored_mux_dffe_bits"] = ce_factored_bits;
    }
    if (ram_decomp_bits > 0) {
        invocation["decomposed_async_ram_mux_bits"] = ram_decomp_bits;
    }

    if (read_file_text(res_out_json).find("\"$_TBUF_\"") != std::string::npos) {
        RtlilDesign tb_des = read_rtlil_json_file(res_out_json);
        std::vector<std::string> tbuf_sel;
        if (tb_des.modules.contains(std::string(top))) {
            const RtlilModule& tm = tb_des.mod_ref(std::string(top));
            std::unordered_set<SigBit> tbuf_y_bits;
            for (const auto& [_, c] : tm.cells) {
                if (c.type == "$_TBUF_") {
                    if (const auto* yb = c.connections.find_ptr("Y")) {
                        for (SigBit b : *yb) if (b >= 0) tbuf_y_bits.insert(b);
                    }
                }
            }
            for (const auto& [pnm, pr] : tm.ports) {
                bool hit_tb = false;
                for (SigBit b : pr.bits) {
                    if (b >= 0 && tbuf_y_bits.count(b)) { hit_tb = true; break; }
                }
                if (hit_tb) {
                    tbuf_sel.push_back(std::string(top) + "/" + pnm);
                }
            }
        }
        if (!tbuf_sel.empty()) {
            write_file_text(seed_autoidx_il, "autoidx 6000000\n");
            std::vector<std::string> tbuf_cmds = {
                "read_rtlil " + quote_yosys_path(seed_autoidx_il),
                "read_json " + quote_yosys_path(res_out_json),
                "read_rtlil " + quote_yosys_path(seed_autoidx_il),
                "read_verilog -overwrite -lib +/gowin/cells_sim.v",
                "read_verilog -overwrite -lib +/gowin/cells_xtra_gw5a.v",
                "read_verilog -overwrite -lib " + quote_yosys_path(path_join(bridge, "vendor_cells.v")),
            };
            tbuf_cmds.insert(tbuf_cmds.end(), dsp_library_cmds.begin(), dsp_library_cmds.end());
            tbuf_cmds.push_back("iopadmap -bits -toutpad TBUF ~OEN:I:O -tinoutpad IOBUF ~OEN:O:I:IO " + join_strings(tbuf_sel, " "));
            tbuf_cmds.push_back("simplemap");
            tbuf_cmds.push_back("opt_clean");
            tbuf_cmds.push_back("blackbox =A:whitebox");
            tbuf_cmds.push_back("check -assert -noinit");
            tbuf_cmds.push_back("write_json " + quote_yosys_path(res_out_json));
            invocation["tristate_iopad_lowering"] = run_yosys_commands(tbuf_cmds, out, "tristate_iopad");
        }
    }

    std::string final_res_json = path_join(out, "resources.json");
    if (soft_comparisons) {
        write_file_text(seed_autoidx_il, "autoidx 8000000\n");
        std::string hard_p = path_join(out, "resources_hard.json");
        auto [low_des, low_rec] = lower_carry_only_chains(read_rtlil_json_file(hard_p));
        std::string low_p = path_join(out, "comparison_lowered.json");
        write_rtlil_json_file(low_p, low_des);
        low_rec["input_sha256"] = sha256_file(hard_p);
        low_rec["lowered_sha256"] = sha256_file(low_p);
        low_rec["implementation_sha256"] = sha256_file(runtime_paths().core_dll);
        low_rec["native_model_sha256"] = effective_prim_sim_sha256();
        low_rec["optimization"] = run_yosys_commands({
            "read_rtlil " + quote_yosys_path(seed_autoidx_il),
            "read_json " + quote_yosys_path(low_p),
            "read_rtlil " + quote_yosys_path(seed_autoidx_il),
            "opt -full", "techmap", "opt -fast", "check -assert -noinit",
            "write_json " + quote_yosys_path(final_res_json),
            "write_verilog -noattr " + quote_yosys_path(path_join(out, "resources.v")),
        }, out, "comparison");
        low_rec["output_sha256"] = sha256_file(final_res_json);
        write_json_pretty(path_join(out, "comparison_lowering.json"), low_rec);
        invocation["comparison_lowering"] = std::move(low_rec);
    }
    remove_file_if_exists(seed_autoidx_il);

    {
        RtlilDesign eq_des = read_rtlil_json_file(final_res_json);
        for (const auto& [mn, mptr] : eq_des.modules) {
            if (is_module_blackbox(*mptr)) continue;
            RtlilModule& em = eq_des.mod_mut(mn);
            std::vector<std::string> drop_auto;
            for (const auto& [nn, nr] : em.netnames) {
                if (nr.hide_name != 0 && nr.attributes.empty() && starts_with(nn, "$auto$jsonparse.cc:")) {
                    drop_auto.push_back(nn);
                }
            }
            for (const auto& nn : drop_auto) em.netnames.erase(nn);
        }
        int64_t merged_ffs = coinductive_merge_equivalent_ffs(eq_des);
        if (merged_ffs > 0) {
            std::string pre_opt_p = path_join(out, "resources_ff_merged.json");
            std::string aidx_p = path_join(out, "ff_merged_autoidx.il");
            write_rtlil_json_file(pre_opt_p, eq_des);
            write_file_text(aidx_p, "autoidx 2000000\n");
            std::vector<std::string> mcmds = {
                "read_rtlil " + quote_yosys_path(aidx_p),
                "read_json " + quote_yosys_path(pre_opt_p),
                "read_verilog -overwrite -lib +/gowin/cells_sim.v",
                "read_verilog -overwrite -lib +/gowin/cells_xtra_gw5a.v",
                "read_verilog -overwrite -lib " + quote_yosys_path(path_join(bridge, "vendor_cells.v")),
            };
            mcmds.insert(mcmds.end(), dsp_library_cmds.begin(), dsp_library_cmds.end());
            mcmds.push_back("opt_merge");
            mcmds.push_back("opt_muxtree");
            mcmds.push_back("opt_reduce");
            mcmds.push_back("opt_expr");
            mcmds.push_back("opt_clean");
            mcmds.push_back("blackbox =A:whitebox");
            mcmds.push_back("check -assert -noinit");
            mcmds.push_back("write_json " + quote_yosys_path(final_res_json));
            run_yosys_commands(mcmds, out, "ff_equiv_clean");
            remove_file_if_exists(pre_opt_p);
            remove_file_if_exists(aidx_p);
            invocation["coinductive_merged_ffs"] = merged_ffs;
        } else {
            write_rtlil_json_file(final_res_json, eq_des);
        }
    }

    if (register_alternatives) {
        std::string before_ch = path_join(out, "resources_before_choices.json");
        copy_file_overwrite(final_res_json, before_ch);
        auto [ch_des, ch_rec] = register_alternatives_add(read_rtlil_json_file(before_ch));
        write_rtlil_json_file(final_res_json, ch_des);
        ch_rec["input_sha256"] = sha256_file(before_ch);
        ch_rec["output_sha256"] = sha256_file(final_res_json);
        ch_rec["implementation_sha256"] = sha256_file(runtime_paths().core_dll);
        int64_t n_ch = static_cast<int64_t>(ch_rec["choices"].as_arr().size());
        write_json_pretty(path_join(out, "register_choices.json"), ch_rec);
        invocation["register_alternatives"] = n_ch;
    }

    if (packing_opt.has_value()) {
        JsonValue& packing = *packing_opt;
        std::unordered_set<std::string> allowed;
        for (const auto& r : packing["packed"].as_arr()) {
            allowed.insert(r["module"].as_str() + "\n" + r["instance"].as_str());
        }
        JsonValue admitted = JsonValue::array();
        RtlilDesign fin = read_rtlil_json_file(final_res_json);
        for (const auto& [mn, mptr] : fin.modules) {
            for (const auto& [cn, c] : mptr->cells) {
                if (is_attr_truthy(c.attributes, "ng_packed_oreg")) {
                    if (!allowed.count(mn + "\n" + cn)) throw std::runtime_error("Unproved registered DSP marker");
                    JsonValue ar = JsonValue::object();
                    ar["module"] = mn;
                    ar["instance"] = cn;
                    admitted.push_back(std::move(ar));
                }
            }
        }
        int64_t n_adm = static_cast<int64_t>(admitted.as_arr().size());
        packing["admitted_cells"] = std::move(admitted);
        packing["resource_sha256"] = sha256_file(final_res_json);
        write_json_pretty(path_join(out, "dsp_output_packing.json"), packing);
        JsonValue sum = JsonValue::object();
        sum["dsps"] = n_adm;
        sum["removed_flipflops"] = packing["removed_flipflops"];
        invocation["dsp_output_packing"] = std::move(sum);
    }

    return invocation;
}

} // namespace ng
