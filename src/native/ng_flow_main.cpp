#pragma fp_contract(off)

#include "ng_pipeline.hpp"

#include <cstdarg>
#include <unordered_map>
#include <unordered_set>

extern "C" __declspec(dllexport) int32_t ng_core_tier_id() {
    return 10;
}

namespace ng {

namespace {

// Process-lifetime aliases if env/resources/sdk themselves sit on paths with spaces or Unicode
struct GlobalPathAliases {
    std::unique_ptr<AsciiDirAlias> env_alias;
    std::unique_ptr<AsciiDirAlias> res_alias;
    std::unique_ptr<AsciiDirAlias> sdk_alias;
};
GlobalPathAliases g_global_aliases;

RuntimePaths build_runtime_paths_from_env() {
    RuntimePaths rp;
    std::string raw_env = resolve_path(get_env("NEXTGOWIN_ENV_DIR"));
    if (!raw_env.empty() && dir_exists(raw_env)) {
        g_global_aliases.env_alias = std::make_unique<AsciiDirAlias>(raw_env, false);
        rp.env_dir = g_global_aliases.env_alias->path();
    } else {
        rp.env_dir = raw_env;
    }

    std::string raw_bin = get_env("NEXTGOWIN_BIN_DIR");
    rp.bin_dir = raw_bin.empty() ? path_join(rp.env_dir, "bin") : resolve_path(raw_bin);
    if (dir_exists(path_join(rp.env_dir, "bin"))) {
        rp.bin_dir = path_join(rp.env_dir, "bin");
    }

    std::string res = get_env("NEXTGOWIN_RESOURCES");
    if (!res.empty()) {
        std::string rres = resolve_path(res);
        if (dir_exists(rres)) {
            g_global_aliases.res_alias = std::make_unique<AsciiDirAlias>(rres, false);
            rp.res_root = g_global_aliases.res_alias->path();
        } else {
            rp.res_root = rres;
        }
    }

    std::string sdk = get_env("NEXTGOWIN_SDK");
    if (!sdk.empty()) {
        std::string rsdk = resolve_path(sdk);
        if (dir_exists(rsdk)) {
            g_global_aliases.sdk_alias = std::make_unique<AsciiDirAlias>(rsdk, false);
            rp.gowin_sdk = g_global_aliases.sdk_alias->path();
        } else {
            rp.gowin_sdk = rsdk;
        }
    }

    rp.allow_unsupported_gowin = (get_env("NEXTGOWIN_ALLOW_UNSUPPORTED_GOWIN") == "1");
    rp.expected_gowin_version  = SUPPORTED_GOWIN_VERSION;
    rp.detected_gowin_version  = get_env("NEXTGOWIN_DETECTED_GOWIN_VER");
    rp.gowin_version_warning   = get_env("NEXTGOWIN_GOWIN_WARNING");

    rp.nextgowin_exe = path_join(rp.env_dir, "nextgowin.exe");
    rp.core_dll      = path_join(rp.bin_dir, "nextgowin_core.dll");
    rp.syn_accel_dll = path_join(rp.bin_dir, "syn_accel.dll");
    rp.bridge_dll    = path_join(rp.bin_dir, "ownpr_bridge.dll");
    rp.abc_exe       = path_join(rp.bin_dir, "abc.exe");
    rp.yosys_exe     = path_join(rp.bin_dir, "yosys.exe");
    return rp;
}

JsonValue build_gowin_version_manifest_entry() {
    const auto& rp = runtime_paths();
    JsonValue v = JsonValue::object();
    v["expected_version"] = rp.expected_gowin_version;
    v["detected_version"] = rp.detected_gowin_version.empty() ? rp.expected_gowin_version : rp.detected_gowin_version;
    v["official_validated_match"] = !rp.allow_unsupported_gowin;
    v["status"] = rp.allow_unsupported_gowin ? "warning_user_continued" : "validated_exact";
    if (!rp.gowin_version_warning.empty()) {
        v["warning"] = rp.gowin_version_warning;
    }
    return v;
}

int run_native_self_test() {
    const auto& rp = runtime_paths();
    constexpr int32_t expected_code = 10;
    if (ng_core_tier_id() != expected_code) {
        throw std::runtime_error("nextgowin_core.dll baseline tier mismatch: " + std::to_string(ng_core_tier_id()));
    }

    // Verify ZERO Python files / runtime directories exist inside nextgowin-env
    bool py_present = dir_exists(path_join(rp.env_dir, "rt")) || dir_exists(path_join(rp.env_dir, "app"));
    if (!py_present) {
        for (const auto& f : rglob_files(rp.env_dir)) {
            std::string fl = to_lower_ascii(file_name(f));
            std::string ext = to_lower_ascii(file_ext(fl));
            if (ext == ".py" || ext == ".pyc" || ext == ".pyd" || starts_with(fl, "python3")) {
                py_present = true;
                break;
            }
        }
    }
    if (py_present) {
        throw std::runtime_error("Python runtime artifacts detected inside standalone nextgowin-env!");
    }

    // Verify ZERO multi-tier binaries remain
    if (dir_exists(path_join(rp.env_dir, "tiers"))) {
        throw std::runtime_error("Legacy multi-tier directory 'tiers/' detected inside Tier-1-only nextgowin-env!");
    }

    JsonValue checks = JsonValue::object();

    // 1. Native Tier 1 baseline DLLs
    int32_t syn_tid = SynAccelLib::instance().tier_id();
    if (syn_tid != expected_code) {
        throw std::runtime_error("syn_accel.dll tier mismatch: " + std::to_string(syn_tid));
    }
    std::wstring wbrg = utf8_to_wide(rp.bridge_dll);
    HMODULE hBrg = LoadLibraryExW(wbrg.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!hBrg) throw std::runtime_error("Failed to load ownpr_bridge.dll: " + rp.bridge_dll);
    auto fn_brg_tid = reinterpret_cast<int32_t (*)()>(GetProcAddress(hBrg, "bridge_tier_id"));
    if (!fn_brg_tid || fn_brg_tid() != expected_code) {
        throw std::runtime_error("ownpr_bridge.dll tier mismatch");
    }
    JsonValue c_dll = JsonValue::object();
    c_dll["status"] = "pass";
    c_dll["isa_baseline"] = "x86-64 + SSE2 + SSE3 (Tier 1 only)";
    checks["native_dlls"] = std::move(c_dll);

    // 2. SSE2/SSE3 LUT4 permutation + GF(2) bit-matrix + comment stripping
    uint16_t inits[2] = {0xCA80U, 0xF0AAU};
    uint8_t ks[2] = {4U, 4U};
    uint8_t orders[8] = {3, 2, 1, 0, 1, 0, 2, 3};
    uint16_t perm_out[2] = {0, 0};
    SynAccelLib::instance().permute_lut_batch(2, inits, ks, orders, perm_out);
    if (perm_out[0] == 0 && perm_out[1] == 0) {
        throw std::runtime_error("SIMD LUT4 permutation returned unexpected zero");
    }
    uint64_t gf_in[8] = {
        0x0123456789ABCDEFULL, 0xFEDCBA9876543210ULL, 0xA5A55A5AA5A55A5AULL, 0x13579BDF2468ACE0ULL,
        0xCAFEBABEDEADBEEFULL, 0x0F1E2D3C4B5A6978ULL, 0x8040201008040201ULL, 0x0102040810204080ULL
    };
    uint64_t gf_out[8]{};
    SynAccelLib::instance().gfni_bitmatrix_u64(8, gf_in, 0x0102040810204080ULL, 0U, gf_out);
    for (int gi = 0; gi < 8; ++gi) {
        if (gf_out[gi] != gf_in[gi]) throw std::runtime_error("GF(2) identity bit-matrix check failed");
    }
    std::string stripped = SynAccelLib::instance().strip_line_comments("module m();\n// comment\nwire a; // tail\nendmodule\n");
    if (stripped != "module m();\n\nwire a; \nendmodule\n") {
        throw std::runtime_error("SIMD strip_line_comments mismatch");
    }
    JsonValue c_simd = JsonValue::object();
    c_simd["status"] = "pass";
    checks["simd_kernels"] = std::move(c_simd);

    // 3. Bundled 64-bit abc.exe & yosys.exe
    auto abc_pr = run_process_captured({rp.abc_exe, "-c", "version"}, rp.env_dir, {}, 30.0);
    if (abc_pr.returncode != 0) throw std::runtime_error("abc.exe version check failed");
    auto abc_lns = split_lines(trim(abc_pr.output));
    JsonValue c_abc = JsonValue::object();
    c_abc["status"] = "pass";
    c_abc["version"] = abc_lns.empty() ? "" : abc_lns.back();
    checks["abc_exe"] = std::move(c_abc);

    std::string y_bin = parent_dir(rp.yosys_exe);
    std::string cur_path = get_env("PATH");
    auto y_pr = run_process_captured({rp.yosys_exe, "-V"}, rp.env_dir, {{"PATH", y_bin + ";" + cur_path}}, 30.0);
    if (y_pr.returncode != 0) throw std::runtime_error("yosys.exe -V check failed");
    JsonValue c_yos = JsonValue::object();
    c_yos["status"] = "pass";
    c_yos["version"] = trim(y_pr.output);
    checks["yosys_exe"] = std::move(c_yos);

    // 4. Resource package SHA-256 manifest
    std::string res_man = rp.res_root.empty() ? "" : path_join(rp.res_root, "resource_manifest.json");
    if (!res_man.empty() && file_exists(res_man)) {
        JsonValue rm = parse_json_file(res_man);
        int64_t n_ver = 0;
        for (const auto& [rel, meta] : rm["files"].as_obj()) {
            std::string fp = path_join(rp.res_root, rel);
            if (!file_exists(fp) || sha256_file(fp) != meta["sha256"].as_str()) {
                throw std::runtime_error("Resource SHA-256 mismatch: " + rel);
            }
            ++n_ver;
        }
        JsonValue cr = JsonValue::object();
        cr["status"] = "pass";
        cr["verified_files"] = n_ver;
        checks["resource_package"] = std::move(cr);
    } else {
        JsonValue cr = JsonValue::object();
        cr["status"] = "skipped";
        checks["resource_package"] = std::move(cr);
    }

    // 5. Gowin SDK verification
    if (!rp.gowin_sdk.empty() && dir_exists(rp.gowin_sdk)) {
        static const std::pair<const char*, const char*> REQ_SHAS[] = {
            {"bin\\gwsyn.dll", EXPECTED_GWSYN_DLL_SHA},
            {"bin\\gowin.dll", EXPECTED_GOWIN_DLL_SHA},
            {"simlib\\gw5a\\prim_sim.v", EXPECTED_PRIM_SIM_SHA},
            {"share\\device\\GW5AST-138C\\GW5AST-138C.dat", EXPECTED_DAT_SHA},
            {"share\\device\\GW5AST-138C\\GW5AST-138C.fse", EXPECTED_FSE_SHA},
            {"share\\device\\GW5AST-138C\\GW5AST-138C.tm",  EXPECTED_TM_SHA},
        };
        for (auto [rel, exp] : REQ_SHAS) {
            (void)verify_or_record_sdk_file_sha(path_join(rp.gowin_sdk, rel), exp, rel);
        }
        checks["gowin_sdk"] = build_gowin_version_manifest_entry();
    } else {
        JsonValue cg = JsonValue::object();
        cg["status"] = "skipped";
        checks["gowin_sdk"] = std::move(cg);
    }

    JsonValue report = JsonValue::object();
    report["execution_engine"] = "native_cpp20_tier1_sse3";
    report["python_runtime_present"] = false;
    report["target_isa"] = "x86-64 + SSE2 + SSE3";
    report["env_dir"] = g_global_aliases.env_alias ? g_global_aliases.env_alias->real_path() : rp.env_dir;
    report["resources_dir"] = g_global_aliases.res_alias ? g_global_aliases.res_alias->real_path() : rp.res_root;
    report["checks"] = std::move(checks);
    report["status"] = "pass";

    std::string js = dump_json(report, 2, false) + "\n";
    std::fwrite(js.data(), 1, js.size(), stdout);
    std::fflush(stdout);
    return 0;
}

std::string resolve_flow_input_path(const JsonValue& cfg, const char* key, std::string_view cfg_parent) {
    if (!cfg.contains(key) || cfg[key].is_null() || !cfg[key].is_str()) return "";
    std::string val = cfg[key].as_str();
    if (val.empty()) return "";
    if (is_absolute_path(val)) return resolve_path(val);
    std::string cwd = resolve_path(".");
    for (const std::string& base_d : {cwd, std::string(cfg_parent), runtime_paths().res_root}) {
        if (base_d.empty()) continue;
        std::string cand = resolve_path(path_join(base_d, val));
        if (path_exists(cand)) return cand;
    }
    return resolve_path(path_join(cwd, val));
}

std::tuple<JsonValue, JsonValue, JsonValue> check_flow_checkpoint(std::string_view out_dir, std::string_view config_path) {
    std::string out = resolve_path(out_dir);
    std::string cfg_p = resolve_path(config_path);
    std::string cfg_parent = parent_dir(cfg_p);
    const auto& rp = runtime_paths();

    JsonValue previous = parse_json_file(path_join(out, "manifest.json"));
    if (previous["config_sha256"].as_str() != sha256_file(cfg_p)) {
        throw std::runtime_error("Flow configuration changed");
    }
    JsonValue frontend = parse_json_file(path_join(out, "01_frontend\\manifest.json"));
    if (frontend["elaborated_sha256"].as_str() != sha256_file(path_join(out, "01_frontend\\elaborated.v"))) {
        throw std::runtime_error("Frontend checkpoint changed");
    }
    for (const auto& [p, h] : frontend["inputs"].as_obj()) {
        if (sha256_file(p) != h.as_str()) throw std::runtime_error("Frontend inputs changed");
    }

    std::string exported = read_file_text(path_join(out, "01_frontend\\elaborated.v"));
    const JsonValue& pcfg = previous["config"];
    std::string macro_prof = resolve_flow_input_path(pcfg, "macro_profile", cfg_parent);
    if (!macro_prof.empty()) {
        std::string rao_p = resolve_flow_input_path(pcfg, "rao", cfg_parent);
        auto [exp2, payload, _] = gowin_macros_prepare(exported, macro_prof, rao_p);
        exported = std::move(exp2);
        if (payload != read_file_text(path_join(out, "02_bridge\\macro.vg"))) {
            throw std::runtime_error("Macro binding changed");
        }
    }
    auto [b_text, b_rec] = verific_bridge_convert(exported);
    if (b_text != read_file_text(path_join(out, "02_bridge\\design.v")) ||
        b_rec["vendor_library_verilog"].as_str() != read_file_text(path_join(out, "02_bridge\\vendor_cells.v"))) {
        throw std::runtime_error("Bridge interpretation changed; rerun upstream synthesis");
    }

    JsonValue inv = parse_json_file(path_join(out, "03_resources\\infer.invocation.json"));
    if (inv["returncode"].as_i64(-1) != 0 || inv["binary_sha256"].as_str() != sha256_file(rp.yosys_exe)) {
        throw std::runtime_error("Resource inference tool changed or failed");
    }
    std::string inf_ys = path_join(out, "03_resources\\infer.ys");
    if (file_exists(inf_ys) && inv["script_sha256"].as_str() != sha256_file(inf_ys)) {
        throw std::runtime_error("Resource inference script changed");
    }

    std::string dsp_p = path_join(out, "03_resources\\dsp_map.json");
    if (pcfg.contains("resource_optimizations") && pcfg["resource_optimizations"].as_bool() && !file_exists(dsp_p)) {
        throw std::runtime_error("Optimized resource checkpoint is missing");
    }
    if (file_exists(dsp_p)) {
        if (parse_json_file(dsp_p)["model_sha256"].as_str() != effective_prim_sim_sha256() ||
            read_file_text(path_join(out, "03_resources\\dsp_cells.v")) != gw5a_dsp_primitive_library()) {
            throw std::runtime_error("DSP interface/model checkpoint changed");
        }
    }

    JsonValue mapped = parse_json_file(path_join(out, "04_mapping\\manifest.json"));
    if (mapped["status"].as_str() != "complete" ||
        mapped["source_sha256"].as_str() != sha256_file(path_join(out, "03_resources\\resources.json")) ||
        mapped["output_sha256"].as_str() != sha256_file(path_join(out, "04_mapping\\mapped.json"))) {
        throw std::runtime_error("Mapping checkpoint incomplete or changed");
    }

    RtlilDesign before = read_rtlil_json_file(path_join(out, "03_resources\\resources.json"));
    RtlilDesign after  = read_rtlil_json_file(path_join(out, "04_mapping\\mapped.json"));
    if (before.modules.size() != after.modules.size()) throw std::runtime_error("Mapped module set changed");
    for (const auto& [mn, _] : before.modules) if (!after.modules.contains(mn)) throw std::runtime_error("Mapped module set changed");

    if (mapped.contains("strategy") && mapped["strategy"].as_str() == "abc9_box") {
        std::vector<std::string> active;
        for (const auto& m : mapped["mapped_user_modules"].as_arr()) active.push_back(m.as_str());
        auto [ref_des, cand_des, _] = box_scoped_candidate(before, after, active);
        std::string pr_ref_p = path_join(out, "04_mapping\\proof_reference.json");
        if (sha256_file(pr_ref_p) != mapped["proof_reference_sha256"].as_str()) {
            throw std::runtime_error("Actual ABC9 input disagrees with the retained proof reference");
        }
        RtlilDesign recorded = read_rtlil_json_file(pr_ref_p);
        int ridx = 0;
        for (const auto& de : list_dir(path_join(out, "04_mapping"))) {
            if (starts_with(de.name, "resume_native_proof_")) ++ridx;
        }
        prove_native_boundary_cec(recorded, after, path_join(out, "04_mapping\\resume_native_proof_" + std::to_string(ridx)), 300.0);
        return {std::move(previous), std::move(frontend), std::move(mapped)};
    }

    return {std::move(previous), std::move(frontend), std::move(mapped)};
}

int run_standalone_ownpr_cli(const std::vector<std::string>& args) {
    std::string mapped_vg, final_json, sdc, cst, dev_cfg, phys_prof, out_dir, dev_p_arg, part_arg;
    int timeout_sec = 1800;
    for (size_t i = 0; i < args.size(); ++i) {
        const std::string& a = args[i];
        if (a == "--ownpr") continue;
        else if (a == "--mapped-vg" && i + 1 < args.size()) mapped_vg = args[++i];
        else if (a == "--final-json" && i + 1 < args.size()) final_json = args[++i];
        else if (a == "--sdc" && i + 1 < args.size()) sdc = args[++i];
        else if (a == "--cst" && i + 1 < args.size()) cst = args[++i];
        else if (a == "--device-config" && i + 1 < args.size()) dev_cfg = args[++i];
        else if (a == "--physical-profile" && i + 1 < args.size()) phys_prof = args[++i];
        else if (a == "--device-p" && i + 1 < args.size()) dev_p_arg = args[++i];
        else if (a == "--part" && i + 1 < args.size()) part_arg = args[++i];
        else if (a == "--out" && i + 1 < args.size()) out_dir = args[++i];
        else if (a == "--timeout" && i + 1 < args.size()) timeout_sec = std::stoi(args[++i]);
        else throw std::runtime_error("Unknown --ownpr option: " + a);
    }
    if (mapped_vg.empty() || sdc.empty() || out_dir.empty()) {
        throw std::runtime_error("--ownpr requires --mapped-vg, --sdc, and --out");
    }
    std::string prof_part, prof_dev_p;
    if (!phys_prof.empty() && file_exists(phys_prof)) {
        JsonValue pf = parse_json_file(phys_prof);
        if (pf.contains("part") && pf["part"].is_str()) prof_part = trim(pf["part"].as_str());
        if (pf.contains("device_p") && pf["device_p"].is_str()) prof_dev_p = trim(pf["device_p"].as_str());
    }
    if (!part_arg.empty() && !prof_part.empty() && to_upper_ascii(part_arg) != to_upper_ascii(prof_part)) {
        throw std::runtime_error(
            "Conflicting --ownpr target part: --part '" + part_arg + "' disagrees with --physical-profile part '" + prof_part + "'"
        );
    }
    if (!dev_p_arg.empty() && !prof_dev_p.empty() && to_upper_ascii(dev_p_arg) != to_upper_ascii(prof_dev_p)) {
        throw std::runtime_error(
            "Conflicting --ownpr target device_p: --device-p '" + dev_p_arg + "' disagrees with --physical-profile device_p '" + prof_dev_p + "'"
        );
    }
    std::string chosen_part = !part_arg.empty() ? part_arg : (!prof_part.empty() ? prof_part : std::string(PINNED_PART));
    std::string chosen_dev_p = !dev_p_arg.empty() ? dev_p_arg : prof_dev_p;
    std::string dev_name_hint;
    if (!chosen_dev_p.empty()) {
        auto dp_parts = split_char(chosen_dev_p, '-');
        if (dp_parts.size() >= 2) dev_name_hint = dp_parts[0] + "-" + dp_parts[1];
    }
    TargetDeviceSpec ownpr_spec = resolve_target_device("", dev_name_hint, chosen_part);
    if (!chosen_dev_p.empty() && to_upper_ascii(chosen_dev_p) != to_upper_ascii(ownpr_spec.dev_p)) {
        throw std::runtime_error(
            "Conflicting --ownpr target specification: device_p '" + chosen_dev_p + "' disagrees with catalog device_p '"
            + ownpr_spec.dev_p + "' for partNumber '" + ownpr_spec.part_number + "'"
        );
    }
    dev_p_arg = ownpr_spec.dev_p;
    part_arg = ownpr_spec.part_number;
    std::string real_out = resolve_path(out_dir);
    make_dirs(parent_dir(real_out), true);
    FlowBudget budget(std::min(3600, std::max(1, timeout_sec)));
    set_active_budget(&budget);

    std::pair<JsonValue, JsonValue> pnr_pair;
    if (needs_ascii_alias(real_out)) {
        make_dirs(real_out, false);
        AsciiDirAlias out_alias(real_out, true);
        pnr_pair = run_ownpr_full(mapped_vg, final_json, sdc, cst, dev_cfg, phys_prof, path_join(out_alias.path(), "pnr"), timeout_sec, dev_p_arg, part_arg);
        out_alias.sync_back();
    } else {
        pnr_pair = run_ownpr_full(mapped_vg, final_json, sdc, cst, dev_cfg, phys_prof, real_out, timeout_sec, dev_p_arg, part_arg);
    }
    set_active_budget(nullptr);

    const auto& [res, chk] = pnr_pair;
    JsonValue summary = JsonValue::object();
    summary["status"] = res["status"];
    summary["pnr_wall_seconds"] = res["pnr_wall_seconds"];
    summary["vendor_placement_executed"] = res["vendor_placement_executed"];
    summary["vendor_routing_executed"] = res["vendor_routing_executed"];
    summary["gowin_version_check"] = build_gowin_version_manifest_entry();
    summary["timing_closed"] = chk.contains("timing_closed") ? chk["timing_closed"] : JsonValue(nullptr);
    summary["frequency_reports"] = chk.contains("frequency_reports") ? chk["frequency_reports"] : JsonValue(nullptr);
    summary["violated_endpoints"] = chk.contains("violated_endpoints") ? chk["violated_endpoints"] : JsonValue(nullptr);
    std::string js = dump_json(summary, 2, false) + "\n";
    std::fwrite(js.data(), 1, js.size(), stdout);
    std::fflush(stdout);
    return 0;
}

// ============================================================================
// General `.gprj` Project Importer & Drag-and-Drop Flow Helpers
// ============================================================================
std::string xml_unescape_local(std::string_view s) {
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

OrderedMap<std::string> parse_xml_attrs_local(std::string_view tag_body) {
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
            attrs.set(key, xml_unescape_local(tag_body.substr(vs, i - vs)));
            if (i < n) ++i;
        }
    }
    return attrs;
}

std::string xml_escape_attr(std::string_view s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (char c : s) {
        if (c == '&') out += "&amp;";
        else if (c == '"') out += "&quot;";
        else if (c == '<') out += "&lt;";
        else if (c == '>') out += "&gt;";
        else out.push_back(c);
    }
    return out;
}

std::string infer_unique_top_module(const std::vector<std::string>& verilog_files) {
    std::vector<std::string> mod_order;
    std::unordered_set<std::string> mod_set;
    std::unordered_map<std::string, std::string> mod_bodies;

    for (const auto& vf : verilog_files) {
        std::string txt = read_file_text(vf);
        std::vector<UnitSpan> units;
        try {
            units = verilog_units(txt);
        } catch (...) {
            // Encrypted IP (`pragma protect`), package-only, or macro-only source file
            continue;
        }
        for (const auto& u : units) {
            if (u.kind != "module") continue;
            if (!mod_set.count(u.name)) {
                mod_set.insert(u.name);
                mod_order.push_back(u.name);
                mod_bodies[u.name] = masked_verilog(std::string_view(txt).substr(u.start, u.end - u.start), true);
            }
        }
    }
    if (mod_order.empty()) {
        throw std::runtime_error("No Verilog module declarations found across enabled project sources");
    }
    if (mod_order.size() == 1) return mod_order[0];

    std::unordered_set<std::string> instantiated;
    for (const auto& m : mod_order) {
        auto toks = tokenize_verilog(mod_bodies[m]);
        // Skip module header up to first ';'
        size_t i = 0;
        while (i < toks.size() && toks[i] != ";") ++i;
        if (i < toks.size()) ++i;
        for (; i + 2 < toks.size(); ++i) {
            const std::string& t = toks[i];
            if (t != m && mod_set.count(t)) {
                // `mod_name #( ... ) inst_name (` or `mod_name inst_name (`
                size_t k = i + 1;
                if (k < toks.size() && toks[k] == "#") {
                    ++k;
                    if (k < toks.size() && toks[k] == "(") {
                        int depth = 1;
                        ++k;
                        while (k < toks.size() && depth > 0) {
                            if (toks[k] == "(") ++depth;
                            else if (toks[k] == ")") --depth;
                            ++k;
                        }
                    }
                }
                if (k + 1 < toks.size() && is_valid_verilog_ident(toks[k]) && toks[k + 1] == "(") {
                    instantiated.insert(t);
                }
            }
        }
    }

    std::vector<std::string> roots;
    for (const auto& m : mod_order) {
        if (!instantiated.count(m)) roots.push_back(m);
    }
    if (roots.size() == 1) return roots[0];
    for (const auto& r : roots) {
        if (r == "top") return r;
    }
    throw std::runtime_error(
        "Multiple candidate top modules detected (" + join_strings(roots.empty() ? mod_order : roots, ", ") +
        "); specify <Option type=\"top_module\" value=\"...\"/> in the .gprj or \"TopModule\" in impl/<project>_process_config.json"
    );
}

// General hierarchy preservation: keep module types along `/` paths referenced by .sdc / .cst, plus protected `~*.<ip>*` wrappers
std::vector<std::string> derive_general_keep_hierarchy(
    std::string_view design_v_text,
    std::string_view top,
    std::string_view sdc_text,
    std::string_view cst_text
) {
    std::vector<std::string> keep;
    std::unordered_set<std::string> keep_seen;
    auto add_keep = [&](const std::string& pat) {
        if (!pat.empty() && !keep_seen.count(pat)) {
            keep_seen.insert(pat);
            keep.push_back(pat);
        }
    };

    // Build module -> (instance_name -> child_module_type) map from 02_bridge/design.v
    std::unordered_set<std::string> all_mods;
    auto units = verilog_units(design_v_text);
    for (const auto& u : units) {
        std::string bare = starts_with(u.name, "\\") ? u.name.substr(1) : u.name;
        all_mods.insert(bare);
        // Encrypted/protected Gowin IP sub-units have Verific names `~submod.IP_TOP`
        if (starts_with(bare, "~")) {
            size_t dot = bare.rfind('.');
            if (dot != std::string::npos && dot + 1 < bare.size()) {
                std::string ip_root = bare.substr(dot + 1);
                add_keep(ip_root);
                add_keep("~*." + ip_root + "*");
            }
        }
    }

    std::unordered_map<std::string, std::unordered_map<std::string, std::string>> inst_mod_map;
    std::unordered_map<std::string, std::unordered_set<std::string>> mod_cell_insts;
    static const std::unordered_set<std::string> non_cell_kw = {
        "module", "endmodule", "input", "output", "inout", "wire", "reg", "logic",
        "parameter", "localparam", "assign", "defparam", "always", "initial",
        "begin", "end", "if", "else", "case", "endcase", "for", "generate", "endgenerate",
        "function", "endfunction", "task", "endtask", "specify", "endspecify"
    };
    for (const auto& u : units) {
        std::string m_bare = starts_with(u.name, "\\") ? u.name.substr(1) : u.name;
        if (starts_with(m_bare, "~")) continue;
        std::string body = masked_verilog(design_v_text.substr(u.start, u.end - u.start), true);
        bool has_always = false;
        bool has_pll_prim = false;
        size_t prim_cnt = 0;
        for (const auto& raw_stmt : split_char(body, ';')) {
            std::string lt = trim(raw_stmt);
            if (lt.empty()) continue;
            auto toks = tokenize_verilog(lt);
            for (const auto& tk : toks) {
                if (tk == "always") { has_always = true; break; }
            }
            // Skip `endmodule` or `module <name>(...)` header statement
            size_t s0 = 0;
            if (!toks.empty() && toks[0] == "module") continue;
            while (s0 < toks.size() && toks[s0] == "endmodule") ++s0;
            if (s0 + 3 <= toks.size()) {
                std::string ctype = starts_with(toks[s0], "\\") ? toks[s0].substr(1) : toks[s0];
                if (!non_cell_kw.count(ctype) && ctype != m_bare) {
                    size_t k = s0 + 1;
                    if (k < toks.size() && toks[k] == "#") {
                        ++k;
                        if (k < toks.size() && toks[k] == "(") {
                            int d = 1; ++k;
                            while (k < toks.size() && d > 0) {
                                if (toks[k] == "(") ++d;
                                else if (toks[k] == ")") --d;
                                ++k;
                            }
                        }
                    }
                    if (k + 1 < toks.size() && toks[k + 1] == "(") {
                        std::string iname = starts_with(toks[k], "\\") ? toks[k].substr(1) : toks[k];
                        bool is_verific_helper = starts_with(ctype, "VERIFIC_") || starts_with(ctype, "Decoder_")
                            || starts_with(ctype, "Select_") || starts_with(ctype, "Mux_") || starts_with(ctype, "NgSelectOH");
                        if (!is_verific_helper) {
                            mod_cell_insts[m_bare].insert(iname);
                            ++prim_cnt;
                            if (ctype == "PLL" || ctype == "rPLL" || ctype == "PLLA") has_pll_prim = true;
                            if (all_mods.count(ctype)) {
                                inst_mod_map[m_bare][iname] = ctype;
                            }
                        }
                    }
                }
            }
        }
        // Keep thin IP wrapper modules around PLL/rPLL so default generated clock names retain `<inst>/PLL_inst/...`
        if (has_pll_prim && !has_always && prim_cnt <= 8 && m_bare != top) {
            add_keep(m_bare);
        }
    }

    // Collect hierarchical paths `inst0/inst1/.../leaf` from .sdc and .cst
    std::vector<std::pair<std::string, bool>> hier_refs; // (path, last_segment_is_pin)
    for (const char* kw : {"get_pins", "get_cells", "get_nets"}) {
        bool is_pin = (std::strcmp(kw, "get_pins") == 0 || std::strcmp(kw, "get_nets") == 0);
        size_t p = 0;
        while ((p = sdc_text.find(kw, p)) != std::string_view::npos) {
            size_t k = p + std::strlen(kw);
            while (k < sdc_text.size() && std::isspace(static_cast<unsigned char>(sdc_text[k]))) ++k;
            if (k < sdc_text.size() && sdc_text[k] == '{') {
                size_t rb = sdc_text.find('}', k + 1);
                if (rb != std::string_view::npos) {
                    for (const auto& tok : split_ws(sdc_text.substr(k + 1, rb - k - 1))) {
                        if (tok.find('/') != std::string::npos) hier_refs.emplace_back(tok, is_pin);
                    }
                    p = rb + 1;
                    continue;
                }
            }
            p = k;
        }
    }
    if (!cst_text.empty()) {
        std::string clean_cst = SynAccelLib::instance().strip_line_comments(cst_text);
        size_t pos = 0;
        while (pos < clean_cst.size()) {
            size_t semi = clean_cst.find(';', pos);
            if (semi == std::string::npos) break;
            std::string stmt = trim(std::string_view(clean_cst).substr(pos, semi - pos));
            pos = semi + 1;
            if (starts_with(stmt, "INS_LOC") || starts_with(stmt, "SET_PARAM")) {
                size_t q1 = stmt.find('"');
                size_t q2 = (q1 != std::string::npos) ? stmt.find('"', q1 + 1) : std::string::npos;
                if (q2 != std::string::npos) {
                    std::string obj = trim(std::string_view(stmt).substr(q1 + 1, q2 - q1 - 1));
                    if (obj.find('/') != std::string::npos) hier_refs.emplace_back(obj, false);
                }
            }
        }
    }

    for (const auto& [hpath, drop_last_pin] : hier_refs) {
        auto segs = split_char(hpath, '/');
        if (segs.size() < 2) continue;
        size_t limit = drop_last_pin ? (segs.size() - 1) : segs.size();
        // Walk from top through intermediate module instances; stop once hitting a leaf primitive/IP
        std::string cur_mod(top);
        for (size_t si = 0; si < limit; ++si) {
            auto mit = inst_mod_map.find(cur_mod);
            if (mit == inst_mod_map.end()) break;
            auto iit = mit->second.find(segs[si]);
            if (iit == mit->second.end()) break;
            // Keep `iit->second` when `segs[si]` is the leaf target (`si + 1 == limit`) or when the remaining
            // hierarchy suffix `segs[si + 1 .. limit - 1]` resolves to an explicit cell/IP instance in `design.v`
            // (do not block flattening when `.sdc` references a synthesis-inferred BSRAM like `uFIFO_TX/array_reg_array_reg_0_0_s`).
            bool suffix_explicit = (si + 1 >= limit);
            if (!suffix_explicit) {
                std::string m_walk = iit->second;
                bool ok = true;
                for (size_t sk = si + 1; sk < limit; ++sk) {
                    if (starts_with(m_walk, "~") || keep_seen.count(m_walk)) { ok = true; break; }
                    const std::string& seg_k = segs[sk];
                    bool has_cell = false;
                    if (auto cit = mod_cell_insts.find(m_walk); cit != mod_cell_insts.end()) {
                        if (seg_k.find('*') == std::string::npos && seg_k.find('?') == std::string::npos) {
                            has_cell = cit->second.count(seg_k) > 0;
                        } else {
                            for (const auto& cand_i : cit->second) {
                                if (fnmatch_case(cand_i, seg_k)) { has_cell = true; break; }
                            }
                        }
                    }
                    if (!has_cell) { ok = false; break; }
                    if (sk + 1 < limit) {
                        auto nmit = inst_mod_map.find(m_walk);
                        if (nmit == inst_mod_map.end()) break;
                        auto niit = nmit->second.find(seg_k);
                        if (niit == nmit->second.end()) break;
                        m_walk = niit->second;
                    }
                }
                suffix_explicit = ok;
            }
            if (suffix_explicit) {
                add_keep(iit->second);
            }
            cur_mod = iit->second;
        }
    }
    return keep;
}

// Strip both `//` and `#` line comments from `.sdc` files
std::string strip_sdc_comments(std::string_view sdc_text) {
    std::string no_slash = SynAccelLib::instance().strip_line_comments(sdc_text);
    std::vector<std::string> out_lines;
    for (const auto& ln : split_lines(no_slash)) {
        std::string tr = ltrim(ln);
        if (!tr.empty() && tr[0] == '#') {
            out_lines.emplace_back();
            continue;
        }
        out_lines.push_back(ln);
    }
    return join_strings(out_lines, "\n");
}

// General `.sdc` `[get_nets ...]` and `.cst` `CLOCK_LOC` scalar net alias preservation across `clean -purge`
OrderedMap<JsonValue> derive_general_cst_clock_aliases(
    const RtlilDesign& mapped_design,
    std::string_view top,
    std::string_view cst_text,
    std::string_view sdc_text = ""
) {
    OrderedMap<JsonValue> out;
    if ((cst_text.empty() && sdc_text.empty()) || !mapped_design.modules.contains(std::string(top))) return out;
    const RtlilModule& tmod = mapped_design.mod_ref(std::string(top));

    std::unordered_set<SigBit> port_bits;
    for (const auto& [_, pr] : tmod.ports) {
        for (SigBit b : pr.bits) if (b >= 0) port_bits.insert(b);
    }

    std::vector<std::pair<std::string, bool>> requested; // (name, require_z_suffix_fallback_only)
    std::unordered_set<std::string> seen_req;
    auto push_req = [&](const std::string& nm, bool cst_mode) {
        if (nm.empty() || nm[0] == '$' || !is_simple_ident(nm) || tmod.ports.contains(nm)) return;
        if (seen_req.insert(nm).second) requested.emplace_back(nm, cst_mode);
    };

    // 1. Extract `[get_nets ...]` scalar net names from active `.sdc` lines
    if (!sdc_text.empty()) {
        std::string clean_sdc = strip_sdc_comments(sdc_text);
        size_t pos = 0;
        while ((pos = clean_sdc.find("get_nets", pos)) != std::string::npos) {
            bool prev_ok = (pos == 0) || !(std::isalnum(static_cast<unsigned char>(clean_sdc[pos - 1])) || clean_sdc[pos - 1] == '_');
            size_t k = pos + 8;
            bool next_ok = (k == clean_sdc.size()) || !(std::isalnum(static_cast<unsigned char>(clean_sdc[k])) || clean_sdc[k] == '_');
            if (!prev_ok || !next_ok) { pos = k; continue; }

            while (k < clean_sdc.size()) {
                while (k < clean_sdc.size() && std::isspace(static_cast<unsigned char>(clean_sdc[k]))) ++k;
                if (k < clean_sdc.size() && clean_sdc[k] == '-') {
                    while (k < clean_sdc.size() && !std::isspace(static_cast<unsigned char>(clean_sdc[k])) &&
                           clean_sdc[k] != '{' && clean_sdc[k] != ']') ++k;
                } else {
                    break;
                }
            }
            std::string arg_body;
            if (k < clean_sdc.size() && clean_sdc[k] == '{') {
                size_t close_b = clean_sdc.find('}', k + 1);
                if (close_b != std::string::npos) {
                    arg_body = clean_sdc.substr(k + 1, close_b - (k + 1));
                    k = close_b + 1;
                }
            } else if (k < clean_sdc.size() && clean_sdc[k] == '"') {
                size_t close_q = clean_sdc.find('"', k + 1);
                if (close_q != std::string::npos) {
                    arg_body = clean_sdc.substr(k + 1, close_q - (k + 1));
                    k = close_q + 1;
                }
            } else if (k < clean_sdc.size() && clean_sdc[k] != ']') {
                size_t e = k;
                while (e < clean_sdc.size() && !std::isspace(static_cast<unsigned char>(clean_sdc[e])) && clean_sdc[e] != ']') ++e;
                arg_body = clean_sdc.substr(k, e - k);
                k = e;
            }
            for (const auto& tok : split_ws(arg_body)) {
                push_req(tok, false);
            }
            pos = k;
        }
    }

    // 2. Extract `CLOCK_LOC "<net>"` scalar net names from active `.cst` lines
    if (!cst_text.empty()) {
        std::string clean_cst = SynAccelLib::instance().strip_line_comments(cst_text);
        size_t pos = 0;
        while (pos < clean_cst.size()) {
            size_t semi = clean_cst.find(';', pos);
            if (semi == std::string::npos) break;
            std::string stmt = trim(std::string_view(clean_cst).substr(pos, semi - pos));
            pos = semi + 1;
            if (!starts_with(stmt, "CLOCK_LOC")) continue;
            size_t q1 = stmt.find('"', 9);
            size_t q2 = (q1 != std::string::npos) ? stmt.find('"', q1 + 1) : std::string::npos;
            if (q2 == std::string::npos) continue;
            push_req(trim(std::string_view(stmt).substr(q1 + 1, q2 - q1 - 1)), true);
        }
    }

    std::unordered_set<SigBit> claimed_bits;
    for (const auto& [cname, is_cst] : requested) {
        // Direct match in `tmod.netnames`: preserve this exact scalar alias across `clean -purge`
        if (const RtlilNetname* ex = tmod.netnames.find_ptr(cname)) {
            if (ex->bits.size() == 1 && ex->bits[0] >= 0 && !port_bits.count(ex->bits[0]) && !claimed_bits.count(ex->bits[0])) {
                claimed_bits.insert(ex->bits[0]);
                JsonValue av = JsonValue::object();
                av["module"] = std::string(top);
                av["net"] = cname;
                out.set(cname, std::move(av));
            }
            continue;
        }

        // Hierarchical `.cname` or `<stem>_Z` match resolving to a unique internal scalar SigBit
        std::vector<std::string> stems = {cname};
        if (ends_with(cname, "_Z") && cname.size() > 2) {
            stems.push_back(cname.substr(0, cname.size() - 2));
        }
        std::string chosen_nn;
        std::unordered_set<SigBit> cand_bits;
        for (const auto& [nn, nr] : tmod.netnames) {
            if (nr.bits.size() != 1 || nr.bits[0] < 0 || port_bits.count(nr.bits[0])) continue;
            bool hit = false;
            for (const auto& st : stems) {
                if (nn == st || ends_with(nn, "." + st)) { hit = true; break; }
            }
            if (hit) {
                cand_bits.insert(nr.bits[0]);
                if (chosen_nn.empty()) chosen_nn = nn;
            }
        }
        if (cand_bits.size() == 1 && !claimed_bits.count(*cand_bits.begin())) {
            claimed_bits.insert(*cand_bits.begin());
            JsonValue av = JsonValue::object();
            av["module"] = std::string(top);
            av["net"] = chosen_nn;
            out.set(cname, std::move(av));
        }
    }
    return out;
}

double derive_period_ps_from_sdc_and_freq(std::string_view sdc_text, std::optional<double> global_freq_mhz) {
    double base_ps = 10000.0; // Gowin standard synthesis default (100.000 MHz = 10,000 ps)
    if (global_freq_mhz.has_value() && *global_freq_mhz > 0.0) {
        base_ps = std::round(1000000.0 / (*global_freq_mhz));
    }
    std::string clean_sdc = strip_sdc_comments(sdc_text);
    std::vector<double> base_clk_periods_ns;
    std::unordered_map<std::string, double> named_clk_period_ns;

    for (const auto& raw_ln : split_lines(clean_sdc)) {
        std::string ln = trim(raw_ln);
        if (ln.empty()) continue;
        auto ws = split_ws(ln);
        if (!ws.empty() && ws[0] == "create_clock") {
            std::string c_name;
            double c_per = 0.0;
            for (size_t i = 1; i + 1 < ws.size(); ++i) {
                if (ws[i] == "-name") c_name = ws[i + 1];
                else if (ws[i] == "-period") {
                    try { double p = std::stod(ws[i + 1]); if (p > 0.0) c_per = p; } catch (...) {}
                }
            }
            if (c_per > 0.0) {
                base_clk_periods_ns.push_back(c_per);
                if (!c_name.empty()) named_clk_period_ns[c_name] = c_per;
            }
        }
    }

    double min_sdc_ns = 0.0;
    if (base_clk_periods_ns.size() == 1) min_sdc_ns = base_clk_periods_ns[0];
    else if (!base_clk_periods_ns.empty()) {
        min_sdc_ns = *std::min_element(base_clk_periods_ns.begin(), base_clk_periods_ns.end());
    }

    for (const auto& raw_ln : split_lines(clean_sdc)) {
        std::string ln = trim(raw_ln);
        if (ln.empty()) continue;
        auto ws = split_ws(ln);
        if (!ws.empty() && ws[0] == "create_generated_clock") {
            std::string master_nm;
            double mult = 1.0, div = 1.0;
            bool has_ratio = false;
            for (size_t i = 1; i + 1 < ws.size(); ++i) {
                if (ws[i] == "-master_clock") master_nm = ws[i + 1];
                else if (ws[i] == "-multiply_by") {
                    try { double v = std::stod(ws[i + 1]); if (v > 0.0) { mult = v; has_ratio = true; } } catch (...) {}
                } else if (ws[i] == "-divide_by") {
                    try { double v = std::stod(ws[i + 1]); if (v > 0.0) { div = v; has_ratio = true; } } catch (...) {}
                }
            }
            double m_per = 0.0;
            if (!master_nm.empty() && named_clk_period_ns.count(master_nm)) m_per = named_clk_period_ns[master_nm];
            else if (base_clk_periods_ns.size() == 1) m_per = base_clk_periods_ns[0];
            if (has_ratio && m_per > 0.0) {
                double gen_p = m_per * div / mult;
                if (gen_p > 0.0 && (min_sdc_ns <= 0.0 || gen_p < min_sdc_ns)) min_sdc_ns = gen_p;
            }
        }
    }

    if (min_sdc_ns > 0.0 && !global_freq_mhz.has_value()) {
        double sdc_ps = std::round(min_sdc_ns * 1000.0);
        if (sdc_ps > 0.0 && sdc_ps < base_ps) base_ps = sdc_ps;
    }
    return base_ps;
}

int run_internal_gao_worker(
    const std::string& rao_path,
    const std::string& dev_name,
    const std::string& part_pn,
    const std::string& out_rtl_gao_dir
) {
    try {
        const auto& rp = runtime_paths();
        std::string sdk_bin = path_join(rp.gowin_sdk, "bin");
        std::string ipcores_dir = posix_path(path_join(rp.gowin_sdk, "data\\ipcores")) + "/";

        SetErrorMode(0x8003);
        SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
        std::wstring w_sdk_bin = utf8_to_wide(sdk_bin);
        AddDllDirectory(w_sdk_bin.c_str());

        HMODULE hQt = LoadLibraryExW(utf8_to_wide(path_join(sdk_bin, "Qt5Core.dll")).c_str(), nullptr,
                                     LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
        HMODULE hGu = LoadLibraryExW(utf8_to_wide(path_join(sdk_bin, "GaoUtils.dll")).c_str(), nullptr,
                                     LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
        if (!hQt || !hGu) throw std::runtime_error("Failed to load Qt5Core.dll / GaoUtils.dll from Gowin SDK");

        auto fn_from_utf8 = reinterpret_cast<void* (*)(void*, const char*, int)>(
            GetProcAddress(hQt, "?fromUtf8@QString@@SA?AV1@PEBDH@Z"));
        auto fn_arg_ctor = reinterpret_cast<void* (*)(void*)>(
            GetProcAddress(hGu, "??0GaoArg@GAO@@QEAA@XZ"));
        auto fn_proxy_ctor = reinterpret_cast<void* (*)(void*, void*)>(
            GetProcAddress(hGu, "??0GAOProxyGwSyn@GAO@@QEAA@PEAVQObject@@@Z"));
        auto fn_start_gen = reinterpret_cast<int (*)(void*, const void*)>(
            GetProcAddress(hGu, "?startGenerate@GAOProxyGwSyn@GAO@@QEAAHAEBUGaoArg@2@@Z"));
        if (!fn_from_utf8 || !fn_arg_ctor || !fn_proxy_ctor || !fn_start_gen) {
            throw std::runtime_error("Missing required GAO preparation exports in Qt5Core.dll / GaoUtils.dll");
        }

        auto mk_qstr = [&](const std::string& s) -> uint64_t {
            uint64_t q = 0;
            fn_from_utf8(&q, s.data(), static_cast<int>(s.size()));
            return q;
        };

        if (dir_exists(out_rtl_gao_dir)) remove_all(out_rtl_gao_dir);
        make_dirs(parent_dir(out_rtl_gao_dir), true);

        alignas(16) uint64_t gao_arg[16]{};
        fn_arg_ctor(gao_arg);
        gao_arg[0] = mk_qstr(posix_path(rao_path));
        gao_arg[2] = mk_qstr(dev_name);
        gao_arg[3] = mk_qstr(part_pn);
        gao_arg[4] = mk_qstr(posix_path(out_rtl_gao_dir));
        gao_arg[5] = mk_qstr(ipcores_dir);
        gao_arg[6] = mk_qstr(dev_name);

        alignas(16) uint64_t proxy_buf[32]{};
        fn_proxy_ctor(proxy_buf, nullptr);
        (void)fn_start_gen(proxy_buf, gao_arg);

        if (!file_exists(path_join(out_rtl_gao_dir, "gw_gao_top.v"))) {
            throw std::runtime_error("GAO RTL generation did not produce gw_gao_top.v");
        }
        return 0;
    } catch (const std::exception& ex) {
        std::fprintf(stderr, "ERROR (gao_worker): %s\n", ex.what());
        std::fflush(stderr);
        return 1;
    }
}

int run_gprj_project_flow(const std::string& gprj_arg, const std::vector<std::string>& extra_args) {
    double entry_t = perf_counter();
    std::string out_arg, stop_after = "bitstream";
    std::optional<int> pnr_timeout_opt, total_timeout_opt;
    std::vector<std::string> net_alias_args;
    std::optional<bool> sync_ide_override;

    for (size_t i = 0; i < extra_args.size(); ++i) {
        const std::string& a = extra_args[i];
        if (a == "--out" && i + 1 < extra_args.size()) out_arg = extra_args[++i];
        else if (a == "--stop-after" && i + 1 < extra_args.size()) stop_after = extra_args[++i];
        else if (a == "--pnr-timeout" && i + 1 < extra_args.size()) pnr_timeout_opt = std::stoi(extra_args[++i]);
        else if (a == "--total-timeout" && i + 1 < extra_args.size()) total_timeout_opt = std::stoi(extra_args[++i]);
        else if (a == "--net-alias" && i + 1 < extra_args.size()) net_alias_args.push_back(extra_args[++i]);
        else if (a == "--sync-ide") sync_ide_override = true;
        else if (a == "--no-sync-ide") sync_ide_override = false;
        else throw std::runtime_error("Unrecognized option for .gprj project mode: " + a);
    }
    bool sync_ide = sync_ide_override.value_or(out_arg.empty());

    std::string gprj_real = resolve_path(gprj_arg);
    if (!file_exists(gprj_real)) {
        throw std::runtime_error("Gowin project file (.gprj) not found: " + gprj_real);
    }
    std::string gprj_dir = parent_dir(gprj_real);
    std::string gprj_stem = file_stem(gprj_real);
    if (gprj_stem.empty()) gprj_stem = "project";

    std::string out_real = out_arg.empty()
        ? resolve_path(path_join(gprj_dir, gprj_stem + "_nextgowin"))
        : resolve_path(out_arg);
    if (to_lower_ascii(posix_path(out_real)) == to_lower_ascii(posix_path(gprj_dir))) {
        throw std::runtime_error("Output directory must be separate from the project source directory");
    }

    // If default `<stem>_nextgowin` exists from an earlier NextGowin run, clean previous artifacts cleanly
    if (out_arg.empty() && dir_exists(out_real)) {
        if (file_exists(path_join(out_real, "manifest.json")) || file_exists(path_join(out_real, "nextgowin.log"))) {
            remove_all(out_real);
        } else if (!list_dir(out_real).empty()) {
            throw std::runtime_error(
                "Output directory already exists and contains non-NextGowin files: " + out_real +
                " (pass --out <dir> or remove the directory)"
            );
        }
    }
    make_dirs(out_real, out_arg.empty());

    std::string run_log_path = path_join(out_real, "nextgowin.log");
    write_file_text(run_log_path, "");

    auto log_msg = [&](bool is_err, const std::string& msg) {
        std::string line = msg + "\n";
        FILE* stream = is_err ? stderr : stdout;
        std::fwrite(line.data(), 1, line.size(), stream);
        std::fflush(stream);
        try { append_file_text(run_log_path, line); } catch (...) {}
    };

    const auto& rp = runtime_paths();
    log_msg(false, "========================================================================");
    log_msg(false, "NextGowin v0.1.1 Standalone Flow (x86-64 + SSE2/SSE3 Baseline)");
    log_msg(false, "  Project   : " + gprj_real);
    log_msg(false, "  Output    : " + out_real);
    log_msg(false, "  Gowin SDK : " + (g_global_aliases.sdk_alias ? g_global_aliases.sdk_alias->real_path() : rp.gowin_sdk));
    if (rp.allow_unsupported_gowin) {
        log_msg(false, "  WARNING   : " + (rp.gowin_version_warning.empty()
            ? ("Unvalidated Gowin version detected (" + rp.detected_gowin_version + "); expected " + rp.expected_gowin_version + ". User chose Continue.")
            : rp.gowin_version_warning));
    } else {
        log_msg(false, "  Version   : " + rp.expected_gowin_version + " (validated)");
    }
    log_msg(false, "========================================================================");

    int err_stage_idx = 0;
    std::string err_stage_name = "project_setup";
    std::string err_dev_info;

    try {
        // Snapshot project file before reading
        OrderedMap<std::string> source_guard_shas;
        source_guard_shas.set(gprj_real, sha256_file(gprj_real));

        std::string xml = read_file_text(gprj_real);
        std::string dev_code, dev_name, dev_pn;
        OrderedMap<std::vector<std::string>> xml_opts;
        std::vector<std::string> verilog_files, vhdl_files, cst_files, sdc_files, csr_files, rao_files, gao_v_files, other_files, disabled_files;

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
            size_t sp = 0;
            while (sp < inside.size() && !std::isspace(static_cast<unsigned char>(inside[sp])) && inside[sp] != '/') ++sp;
            std::string_view tag = inside.substr(0, sp);
            auto attrs = parse_xml_attrs_local(inside.substr(sp));
            if (tag == "Device") {
                dev_name = attrs.value_or("name", attrs.value_or("id", ""));
                dev_pn   = attrs.value_or("pn", attrs.value_or("partNumber", ""));
                if (!inside.empty() && inside.back() != '/') {
                    size_t close_d = xml.find("</Device>", pos);
                    if (close_d != std::string::npos) {
                        std::string dc = trim(std::string_view(xml).substr(pos, close_d - pos));
                        if (!dc.empty()) dev_code = dc;
                    }
                }
            } else if (tag == "Option") {
                xml_opts[attrs.value_or("type", "")].push_back(attrs.value_or("value", ""));
            } else if (tag == "File") {
                std::string raw_p = trim(attrs.value_or("path", ""));
                std::string ftype = trim(attrs.value_or("type", ""));
                std::string en    = to_lower_ascii(trim(attrs.value_or("enable", "1")));
                if (raw_p.empty()) continue;
                std::string full_f = is_absolute_path(raw_p) ? resolve_path(raw_p) : resolve_path(path_join(gprj_dir, raw_p));
                if (en == "0" || en == "false") {
                    disabled_files.push_back(full_f);
                    continue;
                }
                if (!file_exists(full_f)) {
                    throw std::runtime_error("Enabled project file does not exist: " + full_f + " (from path='" + raw_p + "')");
                }
                source_guard_shas.set(full_f, sha256_file(full_f));
                std::string ext_low = to_lower_ascii(file_ext(full_f));
                if (ftype == "file.verilog" || ftype == "verilog") {
                    if (ext_low == ".vh") other_files.push_back(full_f);
                    else verilog_files.push_back(full_f);
                }
                else if (ftype == "file.vhdl" || ftype == "vhdl") vhdl_files.push_back(full_f);
                else if (ftype == "file.cst" || ftype == "cst") cst_files.push_back(full_f);
                else if (ftype == "file.sdc" || ftype == "sdc") sdc_files.push_back(full_f);
                else if (ftype == "file.csr" || ftype == "csr" || ext_low == ".csr") csr_files.push_back(full_f);
                else if (ftype == "file.other" || ftype == "file.hex" || ftype == "file.mi") other_files.push_back(full_f);
                else if (ftype == "file.gpa" || ftype == "file.gsc") continue;
                else if (ftype == "file.gao" || ftype == "gao") {
                    if (ext_low == ".rao") rao_files.push_back(full_f);
                    else gao_v_files.push_back(full_f);
                } else {
                    throw std::runtime_error("Unsupported enabled project file '" + full_f + "' of type '" + ftype + "'");
                }
            }
        }

        TargetDeviceSpec dev_spec = resolve_target_device(dev_code, dev_name, dev_pn);
        err_dev_info = dev_spec.dev_p + " / " + dev_spec.part_number;
        log_msg(false, "[device] Target: " + dev_spec.device_name + " | Package: " + dev_spec.package_name
                     + " | Speed: " + dev_spec.speed_grade + " | Part: " + dev_spec.part_number);
        if (verilog_files.empty() && vhdl_files.empty()) {
            throw std::runtime_error("Project '" + file_name(gprj_real) + "' has no enabled Verilog/SystemVerilog/VHDL source files.");
        }

        // Read optional Gowin IDE `impl/<stem>_process_config.json` (fallback to `impl/project_process_config.json` or unique `*_process_config.json`)
        std::string impl_dir = path_join(gprj_dir, "impl");
        std::string proc_cfg_path = path_join(impl_dir, gprj_stem + "_process_config.json");
        if (!file_exists(proc_cfg_path) && dir_exists(impl_dir)) {
            std::string alt_proj = path_join(impl_dir, "project_process_config.json");
            if (file_exists(alt_proj)) {
                proc_cfg_path = alt_proj;
            } else {
                std::vector<std::string> found_cfgs;
                for (const auto& de : list_dir(impl_dir)) {
                    if (de.is_file && ends_with(to_lower_ascii(de.name), "_process_config.json")) {
                        found_cfgs.push_back(de.path);
                    }
                }
                if (found_cfgs.size() == 1) proc_cfg_path = found_cfgs[0];
            }
        }
        JsonValue proc_cfg = JsonValue::object();
        if (file_exists(proc_cfg_path)) {
            source_guard_shas.set(proc_cfg_path, sha256_file(proc_cfg_path));
            proc_cfg = parse_json_file(proc_cfg_path);
        }

        // Check unsupported critical settings
        if ((proc_cfg.contains("Disable_Insert_Pad") && proc_cfg["Disable_Insert_Pad"].is_bool() && proc_cfg["Disable_Insert_Pad"].as_bool()) ||
            (xml_opts.contains("disable_insert_pad") && !xml_opts.at("disable_insert_pad").empty() && xml_opts.at("disable_insert_pad").back() == "1")) {
            throw std::runtime_error("Unsupported project setting Disable_Insert_Pad=true: NextGowin bitstream flow requires I/O pad insertion.");
        }
        if (proc_cfg.contains("Synthesize_tool") && proc_cfg["Synthesize_tool"].is_str()) {
            std::string st = proc_cfg["Synthesize_tool"].is_str() ? proc_cfg["Synthesize_tool"].as_str() : "";
            if (!st.empty() && st != "GowinSyn") {
                throw std::runtime_error("Unsupported project Synthesize_tool='" + st + "' (expected GowinSyn).");
            }
        }

        // Auto-discover any SerDes `.csr` file under `<gprj_dir>/src` if none listed explicitly in `.gprj`
        if (csr_files.empty() && dir_exists(path_join(gprj_dir, "src"))) {
            for (const auto& f : rglob_files(path_join(gprj_dir, "src"))) {
                if (to_lower_ascii(file_ext(f)) == ".csr") {
                    source_guard_shas.set(f, sha256_file(f));
                    csr_files.push_back(f);
                }
            }
            std::sort(csr_files.begin(), csr_files.end());
        }

        // Resolve include directories relative to <gprj_dir> (plus src/ and source parent dirs)
        std::vector<std::string> inc_dirs;
        auto add_inc = [&](const std::string& raw_ip, bool strict_exists) {
            std::string t = trim(raw_ip);
            if (t.empty()) return;
            std::string rp_inc = is_absolute_path(t) ? resolve_path(t) : resolve_path(path_join(gprj_dir, t));
            if (!dir_exists(rp_inc)) {
                if (strict_exists) throw std::runtime_error("Project include path directory not found: " + rp_inc + " (from '" + t + "')");
                return;
            }
            if (!is_subpath_of(out_real, rp_inc)) {
                inc_dirs.push_back(rp_inc);
            }
        };
        if (const auto* xips = xml_opts.find_ptr("include_path")) {
            for (const auto& ip : *xips) add_inc(ip, true);
        }
        if (proc_cfg.contains("IncludePath") && proc_cfg["IncludePath"].is_arr()) {
            for (const auto& ip : proc_cfg["IncludePath"].as_arr()) {
                if (ip.is_str()) add_inc(ip.as_str(), false);
            }
        }
        if (dir_exists(path_join(gprj_dir, "src")) && !is_subpath_of(out_real, path_join(gprj_dir, "src"))) {
            inc_dirs.push_back(resolve_path(path_join(gprj_dir, "src")));
        }
        for (const auto& vf : verilog_files) {
            std::string vp = parent_dir(vf);
            if (!is_subpath_of(out_real, vp)) inc_dirs.push_back(vp);
        }
        for (const auto& of : other_files) {
            std::string op = parent_dir(of);
            if (!is_subpath_of(out_real, op)) inc_dirs.push_back(op);
        }
        std::sort(inc_dirs.begin(), inc_dirs.end());
        inc_dirs.erase(std::unique(inc_dirs.begin(), inc_dirs.end()), inc_dirs.end());

        // Resolve TopModule
        std::string top_mod;
        if (const auto* xtm = xml_opts.find_ptr("top_module"); xtm && !xtm->empty() && !trim(xtm->back()).empty()) {
            top_mod = trim(xtm->back());
        } else if (proc_cfg.contains("TopModule") && proc_cfg["TopModule"].is_str() && !trim(proc_cfg["TopModule"].as_str()).empty()) {
            top_mod = trim(proc_cfg["TopModule"].as_str());
        } else if (!verilog_files.empty()) {
            top_mod = infer_unique_top_module(verilog_files);
            log_msg(false, "[project] Auto-detected top module: " + top_mod);
        } else {
            top_mod = "top";
            log_msg(false, "[project] Defaulted VHDL top module: " + top_mod);
        }

        // Resolve Verilog & VHDL standards
        std::string vlang;
        if (const auto* xvl = xml_opts.find_ptr("verilog_language"); xvl && !xvl->empty()) {
            vlang = trim(xvl->back());
        } else if (proc_cfg.contains("Verilog_Standard") && proc_cfg["Verilog_Standard"].is_str()) {
            std::string vs = proc_cfg["Verilog_Standard"].as_str();
            if (vs == "Vlg_Std_Sysv2017") vlang = "sysv-2017";
            else if (vs == "Vlg_Std_1995") vlang = "verilog-1995";
            else if (vs == "Vlg_Std_2001") vlang = "verilog-2001";
            else throw std::runtime_error("Unsupported Verilog_Standard in process config: " + vs);
        }
        bool any_sv = false;
        for (const auto& vf : verilog_files) {
            std::string ext = to_lower_ascii(file_ext(vf));
            if (ext == ".sv" || ext == ".svh") { any_sv = true; break; }
        }
        if (vlang.empty()) vlang = any_sv ? "sysv-2017" : "verilog-2001";
        else if (any_sv && vlang == "verilog-2001") vlang = "sysv-2017";

        std::string vhlang = "vhdl-1993";
        if (const auto* xvhl = xml_opts.find_ptr("vhdl_language"); xvhl && !xvhl->empty() && !trim(xvhl->back()).empty()) {
            vhlang = trim(xvhl->back());
        } else if (proc_cfg.contains("Vhdl_Standard") && proc_cfg["Vhdl_Standard"].is_str()) {
            std::string vhs = proc_cfg["Vhdl_Standard"].as_str();
            if (vhs.find("2008") != std::string::npos) vhlang = "vhdl-2008";
            else if (vhs.find("2019") != std::string::npos) vhlang = "vhdl-2019";
            else vhlang = "vhdl-1993";
        }

        // Resolve distributed SSRAM (`no_lutram` is FALSE by default on SSRAM-capable devices!)
        bool no_lutram = !dev_spec.has_ssram;
        if (const auto* xrs = xml_opts.find_ptr("ram_style"); xrs && !xrs->empty()) {
            std::string rs = to_lower_ascii(trim(xrs->back()));
            if (rs == "block" || rs == "registers" || rs == "logic") no_lutram = true;
        } else if (proc_cfg.contains("RAM_Style") && proc_cfg["RAM_Style"].is_str()) {
            std::string rs = to_lower_ascii(trim(proc_cfg["RAM_Style"].as_str()));
            if (rs == "block" || rs == "registers" || rs == "logic") no_lutram = true;
        }

        // Global frequency option (if numeric; default to 100.0 MHz when project has no .sdc file)
        std::optional<double> global_freq_mhz;
        if (const auto* xgf = xml_opts.find_ptr("global_freq"); xgf && !xgf->empty()) {
            try { double v = std::stod(xgf->back()); if (v > 0.0) global_freq_mhz = v; } catch (...) {}
        } else if (proc_cfg.contains("Global_Freq") && proc_cfg["Global_Freq"].is_str()) {
            std::string gf = trim(proc_cfg["Global_Freq"].as_str());
            if (!gf.empty() && to_lower_ascii(gf) != "default") {
                try { double v = std::stod(gf); if (v > 0.0) global_freq_mhz = v; } catch (...) {}
            }
        }
        double pnr_global_freq = (sdc_files.empty()) ? global_freq_mhz.value_or(100.0) : 0.0;

        int pnr_timeout = pnr_timeout_opt.value_or(3600);
        int total_timeout = total_timeout_opt.value_or(3600);
        if (pnr_timeout <= 0 || pnr_timeout > 3600) throw std::runtime_error("P&R timeout must be 1..3600 seconds");

        FlowBudget budget(total_timeout, perf_counter() - entry_t);
        set_active_budget(&budget);

        AsciiDirAlias work_alias(out_real, true);
        std::string out = work_alias.path();

        // Prepare merged SDC, merged CST, optional RTL_GAO, and generated frontend.prj inside `00_project`
        std::string proj_stage_dir = path_join(out, "00_project");
        make_dirs(proj_stage_dir, true);

        // If an enabled `.rao` file is present, generate `RTL_GAO/gw_gao_top.v` via local Gowin `GaoUtils.dll`
        std::vector<std::string> eff_verilog_files = verilog_files;
        std::vector<std::string> eff_inc_dirs = inc_dirs;
        std::string gao_top_v_path = gao_v_files.empty() ? "" : gao_v_files.back();
        if (!rao_files.empty()) {
            std::string rtl_gao_dir = path_join(proj_stage_dir, "RTL_GAO");
            GowinHostStaging gao_stage(proj_stage_dir, rp.gowin_sdk);
            auto gpr = run_process_to_log(
                {gao_stage.host_exe(), "--internal-gao-worker", rao_files.back(), dev_spec.device_name, dev_spec.part_number, rtl_gao_dir},
                gao_stage.host_bin_dir(),
                path_join(proj_stage_dir, "gao_gen.log"),
                {},
                120.0
            );
            if (gpr.returncode != 0 || !file_exists(path_join(rtl_gao_dir, "gw_gao_top.v"))) {
                throw std::runtime_error("Failed to prepare RTL GAO from '" + rao_files.back() + "'; inspect " + path_join(out_real, "00_project\\gao_gen.log"));
            }
            gao_top_v_path = path_join(rtl_gao_dir, "gw_gao_top.v");
            bool is_138_dev = (dev_spec.device_name.find("138") != std::string::npos);
            for (const char* gp_nm : {"gao_std.prj", "gao_lite.prj"}) {
                std::string gp_p = path_join(rtl_gao_dir, gp_nm);
                if (!file_exists(gp_p)) continue;
                std::string gxml = read_file_text(gp_p);
                size_t gp_pos = 0;
                while (gp_pos < gxml.size()) {
                    size_t glt = gxml.find('<', gp_pos);
                    if (glt == std::string::npos) break;
                    size_t ggt = gxml.find('>', glt + 1);
                    if (ggt == std::string::npos) break;
                    std::string_view gin(gxml.data() + glt + 1, ggt - glt - 1);
                    gp_pos = ggt + 1;
                    if (gin.empty() || gin[0] == '?' || gin[0] == '/' || gin[0] == '!') continue;
                    size_t gsp = 0;
                    while (gsp < gin.size() && !std::isspace(static_cast<unsigned char>(gin[gsp])) && gin[gsp] != '/') ++gsp;
                    std::string_view gtag = gin.substr(0, gsp);
                    auto gattrs = parse_xml_attrs_local(gin.substr(gsp));
                    if (gtag == "File") {
                        std::string fp = resolve_path(gattrs.value_or("path", ""));
                        if (to_lower_ascii(file_name(fp)) == "gw_gao_top.v") continue;
                        if (is_138_dev && to_lower_ascii(file_name(fp)) == "gw_con_top.v") {
                            std::string fp138 = path_join(parent_dir(fp), "gw_con_top_138.v");
                            if (file_exists(fp138)) fp = fp138;
                        }
                        if (file_exists(fp)) eff_verilog_files.push_back(fp);
                    } else if (gtag == "Option" && gattrs.value_or("type", "") == "include_path") {
                        std::string ip = resolve_path(gattrs.value_or("value", ""));
                        if (dir_exists(ip)) eff_inc_dirs.push_back(ip);
                    }
                }
            }
            eff_inc_dirs.push_back(rtl_gao_dir);
            std::sort(eff_inc_dirs.begin(), eff_inc_dirs.end());
            eff_inc_dirs.erase(std::unique(eff_inc_dirs.begin(), eff_inc_dirs.end()), eff_inc_dirs.end());
        } else if (!gao_v_files.empty()) {
            for (size_t gi = 0; gi + 1 < gao_v_files.size(); ++gi) eff_verilog_files.push_back(gao_v_files[gi]);
        }

        std::vector<std::string> sdc_chunks;
        for (const auto& sf : sdc_files) sdc_chunks.push_back(read_file_text(sf));
        std::string merged_sdc_txt = sdc_chunks.empty() ? "" : (join_strings(sdc_chunks, "\n") + "\n");
        std::string merged_sdc_p = path_join(proj_stage_dir, "project.sdc");
        write_file_text(merged_sdc_p, merged_sdc_txt.empty() ? "# No user .sdc supplied; using Global_Freq\n" : merged_sdc_txt);

        std::string merged_cst_txt;
        std::string merged_cst_p;
        if (!cst_files.empty()) {
            std::vector<std::string> cst_chunks;
            for (const auto& cf : cst_files) cst_chunks.push_back(read_file_text(cf));
            merged_cst_txt = join_strings(cst_chunks, "\n") + "\n";
            merged_cst_p = path_join(proj_stage_dir, "project.cst");
            write_file_text(merged_cst_p, merged_cst_txt);
        }

        double period_ps = derive_period_ps_from_sdc_and_freq(merged_sdc_txt, global_freq_mhz);

        std::vector<std::string> prj_lines = {
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
            "<Project>",
            "  <Device id=\"" + xml_escape_attr(dev_spec.device_name) + "\" package=\"" + xml_escape_attr(dev_spec.package_name)
                + "\" speed=\"" + xml_escape_attr(dev_spec.speed_grade) + "\" partNumber=\"" + xml_escape_attr(dev_spec.part_number) + "\">"
                + xml_escape_attr(dev_spec.code) + "</Device>",
            "  <FileList>"
        };
        for (const auto& vf : eff_verilog_files) {
            prj_lines.push_back("    <File path=\"" + xml_escape_attr(vf) + "\" type=\"verilog\"/>");
        }
        for (const auto& vhf : vhdl_files) {
            prj_lines.push_back("    <File path=\"" + xml_escape_attr(vhf) + "\" type=\"vhdl\"/>");
        }
        if (!gao_top_v_path.empty()) {
            prj_lines.push_back("    <File path=\"" + xml_escape_attr(gao_top_v_path) + "\" type=\"gao\"/>");
        }
        prj_lines.push_back("  </FileList>");
        prj_lines.push_back("  <OptionList>");
        prj_lines.push_back("    <Option type=\"top_module\" value=\"" + xml_escape_attr(top_mod) + "\"/>");
        prj_lines.push_back("    <Option type=\"verilog_language\" value=\"" + xml_escape_attr(vlang) + "\"/>");
        if (!vhdl_files.empty()) {
            prj_lines.push_back("    <Option type=\"vhdl_language\" value=\"" + xml_escape_attr(vhlang) + "\"/>");
        }
        if (proc_cfg.contains("Enable_DSRM") && proc_cfg["Enable_DSRM"].is_bool() && proc_cfg["Enable_DSRM"].as_bool()) {
            prj_lines.push_back("    <Option type=\"enable_dsrm\" value=\"1\"/>");
        }
        for (const auto& idir : eff_inc_dirs) {
            prj_lines.push_back("    <Option type=\"include_path\" value=\"" + xml_escape_attr(idir) + "\"/>");
        }
        prj_lines.push_back("  </OptionList>");
        prj_lines.push_back("</Project>");
        std::string gen_prj_p = path_join(proj_stage_dir, "frontend.prj");
        write_file_text(gen_prj_p, join_strings(prj_lines, "\n") + "\n");

        OrderedMap<JsonValue> aliases;
        for (const auto& na : net_alias_args) {
            size_t eq = na.find('=');
            size_t col = (eq != std::string::npos) ? na.find(':', eq + 1) : std::string::npos;
            if (eq == std::string::npos || col == std::string::npos) {
                throw std::runtime_error("Invalid --net-alias (expected LEGACY=MODULE:NET): " + na);
            }
            JsonValue av = JsonValue::object();
            av["module"] = na.substr(eq + 1, col - eq - 1);
            av["net"] = na.substr(col + 1);
            aliases.set(na.substr(0, eq), std::move(av));
        }

        // General synthesis parameters matching NextGowin's general co-optimization flow
        JsonValue log_rep = JsonValue::object();
        log_rep["limit"] = 12; log_rep["max_groups"] = 32; log_rep["area_allowance"] = 0.06;
        JsonValue reg_rep = JsonValue::object();
        reg_rep["limit"] = 16; reg_rep["max_groups"] = 32; reg_rep["area_allowance"] = 0.02;

        JsonValue result = JsonValue::object();
        result["schema"] = "nextgowin-gprj-flow-v1";
        result["tool_version"] = "0.1.1";
        result["status"] = "running";
        result["execution"] = "native-cpp20-tier1-sse3";
        result["python_runtime_present"] = false;
        result["project_gprj"] = gprj_real;
        result["project_sha256"] = sha256_file(gprj_real);
        result["top_module"] = top_mod;
        result["target_device"] = dev_spec.device_name;
        result["target_package"] = dev_spec.package_name;
        result["target_speed"] = dev_spec.speed_grade;
        result["target_dev_p"] = dev_spec.dev_p;
        result["target_part"] = dev_spec.part_number;
        result["no_lutram"] = no_lutram;
        result["gowin_version_check"] = build_gowin_version_manifest_entry();
        result["stages"] = JsonValue::array();
        result["board_programmed"] = false;
        result["frontend_only_vendor"] = true;
        result["own_mapping_source"] = "Resource-inferred Boolean graph; not vendor-mapped user LUTs";
        result["total_timeout_seconds"] = total_timeout;

        auto save_manifest = [&]() { write_json_file(path_join(out, "manifest.json"), result, 2); };
        save_manifest();

        auto run_stage = [&](int idx, const char* name, const char* desc, auto&& fn) -> JsonValue {
            (void)budget.remaining();
            double st0 = perf_counter();
            err_stage_idx = idx;
            err_stage_name = name;
            result["active_stage"] = name;
            save_manifest();
            log_msg(false, "[" + std::to_string(idx) + "/6] Starting " + std::string(name) + " (" + desc + ")...");
            JsonValue val = fn();
            double dt = perf_counter() - st0;
            JsonValue srec = JsonValue::object();
            srec["name"] = name;
            srec["wall_seconds"] = dt;
            srec["result"] = val;
            result["stages"].push_back(std::move(srec));
            save_manifest();
            (void)budget.remaining();
            char tbuf[64];
            std::snprintf(tbuf, sizeof(tbuf), "%.2fs", dt);
            log_msg(false, "[" + std::to_string(idx) + "/6] Completed " + std::string(name) + " in " + tbuf);
            return val;
        };

        JsonValue frontend = run_stage(1, "frontend", "Gowin RTL elaboration before synthesis/mapping", [&]() {
            return export_gowin_frontend(gen_prj_p, path_join(out, "01_frontend"));
        });
        std::string top = frontend["top"].as_str();

        std::vector<std::string> keep_h;
        std::unordered_map<std::string, std::vector<std::string>> restored_mem_map;
        if (stop_after != "frontend") {
            run_stage(2, "bridge", "NextGowin Verific bridge & memory array restoration", [&]() {
                std::string folder = path_join(out, "02_bridge");
                make_dirs(folder, false);
                std::string elab_p = path_join(out, "01_frontend\\elaborated.v");
                std::string exported = read_file_text(elab_p);
                auto [des_v, rec] = verific_bridge_convert(exported);
                std::string des_p = path_join(folder, "design.v");
                std::string vc_p = path_join(folder, "vendor_cells.v");
                write_file_text(des_p, des_v);
                write_file_text(vc_p, rec["vendor_library_verilog"].as_str());
                rec.as_obj().erase("vendor_library_verilog");
                rec["source_sha256"] = sha256_file(elab_p);
                rec["output_sha256"] = sha256_file(des_p);
                rec["vendor_library_sha256"] = sha256_file(vc_p);
                keep_h = derive_general_keep_hierarchy(des_v, top, merged_sdc_txt, merged_cst_txt);
                for (const auto& mrec : rec["restored_memories"].as_arr()) {
                    if (mrec.contains("array") && mrec.contains("net")) {
                        std::string arr_nm = mrec["array"].as_str();
                        std::string orig_nm = mrec["net"].as_str();
                        if (!orig_nm.empty() && orig_nm.front() == '\\') orig_nm = trim(orig_nm.substr(1));
                        if (!arr_nm.empty() && !orig_nm.empty()) restored_mem_map[arr_nm].push_back(orig_nm);
                    }
                }
                int64_t n_init = static_cast<int64_t>(rec["initial_values"].size());
                int64_t n_mem  = static_cast<int64_t>(rec["restored_memories"].size());
                int64_t n_spec = static_cast<int64_t>(rec["primitive_specializations"].size());
                write_json_file(path_join(folder, "manifest.json"), rec, 2);
                JsonValue ret = JsonValue::object();
                ret["initial_values"] = n_init;
                ret["memories"] = n_mem;
                ret["primitive_specializations"] = n_spec;
                return ret;
            });

            run_stage(3, "resources", "NextGowin DSP, BSRAM, distributed SSRAM, and ALU carry resource inference", [&]() {
                JsonValue inv = infer_resources_stage(
                    path_join(out, "02_bridge"),
                    path_join(out, "03_resources"),
                    top, keep_h, true, false, false, JsonValue(true), false, false, no_lutram
                );
                if (!dev_spec.has_ssram) {
                    RtlilDesign chk_des = read_rtlil_json_file(path_join(out, "03_resources\\resources.json"));
                    for (const auto& [mn, mptr] : chk_des.modules) {
                        if (is_module_blackbox(*mptr)) continue;
                        for (const auto& [cn, c] : mptr->cells) {
                            if (starts_with(c.type, "RAM16") || c.type == "ROM16") {
                                throw std::runtime_error(
                                    "ERROR (RP0007): Primitive '" + c.type + "' (instance '" + cn + "' in module '" + mn +
                                    "') is not supported on target device " + dev_spec.device_name + " (0Kb SSRAM capacity)."
                                );
                            }
                        }
                    }
                }
                if (!merged_sdc_txt.empty() || !merged_cst_txt.empty()) {
                    std::string res_json_p = path_join(out, "03_resources\\resources.json");
                    RtlilDesign res_des = read_rtlil_json_file(res_json_p);
                    bool modified = false;

                    // Align inferred BSRAM cell names (`<hier>.<mem>.<r>.<c>`) with `.sdc` `<hier>/<mem>_<mem>_<r>_<c>_s` references
                    if (merged_sdc_txt.find("_s") != std::string::npos && res_des.modules.contains(top)) {
                        static const std::unordered_set<std::string> bsram_types = {
                            "SDPB", "SDPX9B", "DPB", "DPX9B", "SP", "SPX9", "pROM", "pROMX9"
                        };
                        auto is_digits = [](const std::string& s) {
                            if (s.empty()) return false;
                            for (char ch : s) if (!std::isdigit(static_cast<unsigned char>(ch))) return false;
                            return true;
                        };
                        RtlilModule& tmod = res_des.mod_mut(top);
                        std::vector<std::pair<std::string, std::string>> renames;
                        for (const auto& [cn, c] : tmod.cells) {
                            if (!bsram_types.count(c.type)) continue;
                            std::string clean_cn = (!cn.empty() && cn[0] == '\\') ? cn.substr(1) : cn;
                            auto parts = split_char(clean_cn, '.');
                            if (parts.size() >= 3 && is_digits(parts[parts.size() - 2]) && is_digits(parts.back())) {
                                const std::string& mem_nm = parts[parts.size() - 3];
                                const std::string& r_idx  = parts[parts.size() - 2];
                                const std::string& c_idx  = parts.back();
                                std::string h_slash;
                                for (size_t pi = 0; pi + 3 < parts.size(); ++pi) {
                                    if (!h_slash.empty()) h_slash.push_back('/');
                                    h_slash += parts[pi];
                                }
                                std::vector<std::string> cand_mems{mem_nm};
                                if (auto mit = restored_mem_map.find(mem_nm); mit != restored_mem_map.end()) {
                                    for (const auto& om : mit->second) cand_mems.push_back(om);
                                }
                                for (const auto& cm : cand_mems) {
                                    std::string probe_pfx = (h_slash.empty() ? std::string("") : (h_slash + "/")) + cm + "_" + cm + "_";
                                    if (merged_sdc_txt.find(probe_pfx) != std::string::npos) {
                                        std::string new_nm = probe_pfx + r_idx + "_" + c_idx + "_s";
                                        if (!tmod.cells.contains(new_nm)) renames.emplace_back(cn, new_nm);
                                        break;
                                    }
                                }
                            }
                        }
                        for (const auto& [old_nm, new_nm] : renames) {
                            RtlilCell bc = std::move(tmod.cells.at(old_nm));
                            bc.hide_name = 0;
                            tmod.cells.erase(old_nm);
                            tmod.cells.set(new_nm, std::move(bc));
                            modified = true;
                        }
                    }

                    if (merged_sdc_txt.find("get_nets") != std::string::npos ||
                        merged_cst_txt.find("CLOCK_LOC") != std::string::npos) {
                        auto pre_aliases = derive_general_cst_clock_aliases(res_des, top, merged_cst_txt, merged_sdc_txt);
                        for (const auto& [ak, av] : pre_aliases) {
                            std::string mname = av["module"].as_str();
                            std::string snet  = av["net"].as_str();
                            if (res_des.modules.contains(mname)) {
                                if (RtlilNetname* nr = res_des.mod_mut(mname).netnames.find_ptr(snet)) {
                                    if (!is_attr_truthy(nr->attributes, "keep")) {
                                        nr->attributes.set("keep", "1");
                                        modified = true;
                                    }
                                }
                            }
                            if (!aliases.contains(ak)) aliases.set(ak, av);
                        }
                    }
                    if (modified) {
                        write_rtlil_json_file(res_json_p, res_des);
                    }
                }
                return inv;
            });
        }

        if (stop_after != "frontend" && stop_after != "resources") {
            std::optional<RtlilDesign> in_mem_mapped;
            JsonValue mapped = run_stage(4, "mapping", "NextGowin AIG rewrite, ABC9 wide LUT mapping & CEC proof", [&]() {
                auto [mrec, mdes] = map_resource_design(
                    path_join(out, "03_resources\\resources.json"),
                    path_join(out, "04_mapping"),
                    period_ps, "abc9_box", "wide", true
                );
                in_mem_mapped = std::move(mdes);
                return mrec;
            });
            result["local_model_budgets_satisfied"] = mapped["all_local_budgets_satisfied"];

            run_stage(5, "emit", "NextGowin pad insertion, fanout replication & structural emission", [&]() {
                if (in_mem_mapped) {
                    auto auto_aliases = derive_general_cst_clock_aliases(*in_mem_mapped, top, merged_cst_txt, merged_sdc_txt);
                    for (const auto& [ak, av] : auto_aliases) {
                        if (!aliases.contains(ak)) aliases.set(ak, av);
                    }
                }
                JsonValue val = materialize_netlist_stage(
                    path_join(out, "04_mapping\\mapped.json"),
                    in_mem_mapped ? &(*in_mem_mapped) : nullptr,
                    path_join(out, "05_netlist"),
                    top,
                    path_join(out, "02_bridge\\vendor_cells.v"),
                    138240, aliases, true, false, false, log_rep, reg_rep, JsonValue(false), 0.0
                );
                in_mem_mapped.reset();
                val["final_netlist_sha256"] = sha256_file(path_join(out, "05_netlist\\mapped.vg"));
                return val;
            });
        }

        // Resolve Gowin IDE base output name & optional `impl/pnr/device.cfg`
        std::string ide_base = gprj_stem;
        if (proc_cfg.contains("OUTPUT_BASE_NAME") && proc_cfg["OUTPUT_BASE_NAME"].is_str()) {
            std::string ob = trim(proc_cfg["OUTPUT_BASE_NAME"].as_str());
            if (!ob.empty()) ide_base = ob;
        }
        std::string existing_dev_cfg = path_join(gprj_dir, "impl\\pnr\\device.cfg");
        std::string use_dev_cfg;
        if (file_exists(existing_dev_cfg)) {
            source_guard_shas.set(existing_dev_cfg, sha256_file(existing_dev_cfg));
        }
        // Precedence contract: explicit `.gprj` <Option> and `impl/*_process_config.json` settings
        // override matching keys in any existing `impl/pnr/device.cfg` (preserving extra vendor keys
        // such as `DOWNLOAD_MODE`, `user_code`, or `spi_flash_address`), so stale IDE `device.cfg`
        // files never mask updated project options.
        {
            OrderedMap<std::string> explicit_cfg_kv;
            auto set_explicit_bool = [&](const char* cfg_lhs, const char* jkey, const char* xkey, bool def_v, bool* out_val = nullptr) -> bool {
                bool has_exp = false;
                bool v = def_v;
                if (const auto* xv = xml_opts.find_ptr(xkey); xv && !xv->empty()) {
                    has_exp = true;
                    std::string s = to_lower_ascii(trim(xv->back()));
                    v = (s == "1" || s == "true");
                } else if (proc_cfg.contains(jkey) && proc_cfg[jkey].is_bool()) {
                    has_exp = true;
                    v = proc_cfg[jkey].as_bool();
                }
                if (has_exp) explicit_cfg_kv.set(cfg_lhs, v ? "true" : "false");
                if (out_val) *out_val = v;
                return v;
            };

            bool jtag_io  = set_explicit_bool("set JTAG regular_io",         "JTAG",        "use_jtag_as_gpio",  false);
            bool sspi_io  = set_explicit_bool("set SSPI regular_io",         "SSPI",        "use_sspi_as_gpio",  false);
            bool mspi_io  = set_explicit_bool("set MSPI regular_io",         "MSPI",        "use_mspi_as_gpio",  false);
            bool ready_io = set_explicit_bool("set READY regular_io",        "READY",       "use_ready_as_gpio", false);
            bool done_io  = set_explicit_bool("set DONE regular_io",         "DONE",        "use_done_as_gpio",  false);
            bool i2c_io   = set_explicit_bool("set I2C regular_io",          "I2C",         "use_i2c_as_gpio",   false);
            bool cpu_io   = set_explicit_bool("set CPU regular_io",          "CPU",         "use_cpu_as_gpio",   false);
            bool crc_chk  = set_explicit_bool("set CRC_check",               "CRC_CHECK",   "bit_crc_check",     true);
            bool bit_comp = set_explicit_bool("set compress",                "COMPRESS",    "bit_compress",      false);
            bool sec_bit  = set_explicit_bool("set security_bit_enable",     "SECURITY_BIT","bit_security",      true);
            bool bsram_p  = set_explicit_bool("set bsram_init_fuse_print",   "PRINT_BSRAM_VALUE", "print_bsram_value", true);
            bool por_mon  = set_explicit_bool("set power_on_reset_monitor",  "POWER_ON_RESET_MONITOR", "power_on_reset_monitor", true);

            std::string vccx_val = "3.3";
            if (proc_cfg.contains("VCCX") && proc_cfg["VCCX"].is_str() && !trim(proc_cfg["VCCX"].as_str()).empty()) {
                vccx_val = trim(proc_cfg["VCCX"].as_str());
                explicit_cfg_kv.set("set vccx", vccx_val);
            }
            std::string unused_pin_cfg = "default";
            if (proc_cfg.contains("Unused_Pin") && proc_cfg["Unused_Pin"].is_str()) {
                std::string up = to_lower_ascii(trim(proc_cfg["Unused_Pin"].as_str()));
                if (up == "as_input_tri_stated_with_pull_down" || up == "pull_down") {
                    unused_pin_cfg = "pull_down";
                    explicit_cfg_kv.set("set unused_pin", unused_pin_cfg);
                } else if (up == "as_input_tri_stated" || up == "open") {
                    unused_pin_cfg = "open";
                    explicit_cfg_kv.set("set unused_pin", unused_pin_cfg);
                } else if (up == "as_output_driving_ground" || up == "ground") {
                    unused_pin_cfg = "ground";
                    explicit_cfg_kv.set("set unused_pin", unused_pin_cfg);
                } else if (up == "as_input_tri_stated_with_pull_up" || up == "default" || up == "pull_up") {
                    unused_pin_cfg = "default";
                    explicit_cfg_kv.set("set unused_pin", unused_pin_cfg);
                }
            }

            if (file_exists(existing_dev_cfg)) {
                use_dev_cfg = path_join(proj_stage_dir, "device.cfg");
                if (explicit_cfg_kv.empty()) {
                    copy_file_overwrite(existing_dev_cfg, use_dev_cfg);
                } else {
                    auto base_lines = split_lines(read_file_text(existing_dev_cfg));
                    std::unordered_set<std::string> replaced_lhs;
                    for (std::string& ln : base_lines) {
                        size_t eq = ln.find('=');
                        if (eq == std::string::npos) continue;
                        std::string lhs_norm = to_lower_ascii(trim(std::string_view(ln).substr(0, eq)));
                        for (const auto& [ek, ev] : explicit_cfg_kv) {
                            if (to_lower_ascii(ek) == lhs_norm) {
                                ln = ek + " = " + ev;
                                replaced_lhs.insert(lhs_norm);
                                break;
                            }
                        }
                    }
                    for (const auto& [ek, ev] : explicit_cfg_kv) {
                        if (!replaced_lhs.count(to_lower_ascii(ek))) {
                            base_lines.push_back(ek + " = " + ev);
                        }
                    }
                    write_file_text(use_dev_cfg, join_strings(base_lines, "\n") + "\n");
                }
            } else if (!explicit_cfg_kv.empty()) {
                auto tf = [](bool v) { return v ? "true" : "false"; };
                std::vector<std::string> cfg_lns = {
                    std::string("set JTAG regular_io = ") + tf(jtag_io),
                    std::string("set SSPI regular_io = ") + tf(sspi_io),
                    std::string("set MSPI regular_io = ") + tf(mspi_io),
                    std::string("set READY regular_io = ") + tf(ready_io),
                    std::string("set DONE regular_io = ") + tf(done_io),
                    std::string("set I2C regular_io = ") + tf(i2c_io),
                    std::string("set CPU regular_io = ") + tf(cpu_io),
                    std::string("set CRC_check = ") + tf(crc_chk),
                    std::string("set compress = ") + tf(bit_comp),
                    std::string("set security_bit_enable = ") + tf(sec_bit),
                    std::string("set bsram_init_fuse_print = ") + tf(bsram_p),
                    std::string("set power_on_reset_monitor = ") + tf(por_mon),
                    std::string("set vccx = ") + vccx_val,
                    std::string("set unused_pin = ") + unused_pin_cfg,
                };
                use_dev_cfg = path_join(proj_stage_dir, "device.cfg");
                write_file_text(use_dev_cfg, join_strings(cfg_lns, "\n") + "\n");
            }
        }
        std::string use_csr_path = csr_files.empty() ? "" : csr_files.back();

        JsonValue timing_summary = JsonValue::object();
        if (stop_after == "bitstream") {
            JsonValue physical = run_stage(6, "pnr_bitstream", "NextGowin custom placement & routing + Gowin STA & bitstream", [&]() {
                int eff_timeout = static_cast<int>(budget.timeout(static_cast<double>(pnr_timeout), 5.0));
                if (eff_timeout < 1) throw TimeoutError("Insufficient total flow budget to launch native P&R");
                result["effective_pnr_timeout"] = eff_timeout;
                save_manifest();

                // When a project has no user .sdc, synthesize default Global_Freq base clocks ONLY for non-PLL
                // clock roots driving sequential cells (leaving pure-PLL designs unforced so gowin.dll uses FCLKIN).
                std::string eff_sdc_for_pnr = sdc_files.empty() ? std::string("") : merged_sdc_p;
                if (sdc_files.empty() && pnr_global_freq > 0.0) {
                    std::string vg_txt = read_file_text(path_join(out, "05_netlist\\mapped.vg"));
                    auto norm_id = [](std::string_view sv) -> std::string {
                        std::string s = trim(sv);
                        if (!s.empty() && s[0] == '\\') return s.substr(1);
                        return s;
                    };
                    // Parse top module cells in mapped.vg
                    auto v_units = verilog_units(vg_txt);
                    for (const auto& vu : v_units) {
                        std::string mb = starts_with(vu.name, "\\") ? vu.name.substr(1) : vu.name;
                        if (mb != top) continue;
                        auto mlines = split_lines(vg_txt.substr(vu.start, vu.end - vu.start));
                        struct CellConn { std::string ctype, inst_norm; std::vector<std::pair<std::string, std::string>> ports; };
                        std::vector<CellConn> tcells;
                        std::unordered_map<std::string, double> pll_fclkin;
                        for (size_t li = 1; li + 1 < mlines.size(); ++li) {
                            std::string tr = trim(mlines[li]);
                            if (starts_with(tr, "defparam ") && tr.back() == ';') {
                                std::string rest = trim(std::string_view(tr).substr(9, tr.size() - 10));
                                size_t eq = rest.find('=');
                                if (eq != std::string::npos) {
                                    std::string lhs = rtrim(std::string_view(rest).substr(0, eq));
                                    std::string rhs = trim(std::string_view(rest).substr(eq + 1));
                                    if (ends_with(lhs, ".FCLKIN")) {
                                        std::string in_n = norm_id(std::string_view(lhs).substr(0, lhs.size() - 7));
                                        if (rhs.size() >= 2 && rhs.front() == '"' && rhs.back() == '"') rhs = rhs.substr(1, rhs.size() - 2);
                                        try { pll_fclkin[in_n] = std::stod(rhs); } catch (...) {}
                                    }
                                }
                                continue;
                            }
                            if (!tr.empty() && tr.back() == '(' && !starts_with(tr, "input ") && !starts_with(tr, "output ") && !starts_with(tr, "wire ")) {
                                auto ws = split_ws(std::string_view(tr).substr(0, tr.size() - 1));
                                if (ws.size() == 2) {
                                    size_t cl = li + 1;
                                    while (cl + 1 < mlines.size() && trim(mlines[cl]) != ");") ++cl;
                                    if (cl + 1 < mlines.size()) {
                                        CellConn cc{ws[0], norm_id(ws[1]), {}};
                                        for (size_t pl = li + 1; pl < cl; ++pl) {
                                            std::string pt = trim(mlines[pl]);
                                            if (pt.empty() || pt[0] != '.') continue;
                                            size_t lp = pt.find('('), rp = pt.rfind(')');
                                            if (lp != std::string::npos && rp != std::string::npos && rp > lp) {
                                                cc.ports.emplace_back(
                                                    trim(std::string_view(pt).substr(1, lp - 1)),
                                                    norm_id(std::string_view(pt).substr(lp + 1, rp - lp - 1))
                                                );
                                            }
                                        }
                                        tcells.push_back(std::move(cc));
                                        li = cl;
                                    }
                                }
                            }
                        }

                        std::unordered_map<std::string, std::string> inv_in_map;        // out_w -> in_w (for INV)
                        std::unordered_map<std::string, std::string> ibuf_port_map;     // out_w -> top_in_port
                        std::unordered_map<std::string, std::string> obuf_port_map;     // in_w  -> top_out_port
                        std::unordered_map<std::string, std::tuple<std::string, std::string, std::string>> drv_map; // w -> (ctype, inst, pin)
                        std::unordered_map<std::string, double> pll_in_mhz;
                        std::vector<std::string> seq_clk_wires;
                        std::unordered_set<std::string> seq_clk_seen;

                        static const std::unordered_set<std::string> hard_clk_gen = {
                            "PLL", "rPLL", "PLLA", "CLKDIV", "CLKDIV2", "DHCE", "DHCEN", "DQS", "OSC"
                        };

                        std::unordered_set<std::string> kept_mod_names;
                        for (const auto& u2 : v_units) {
                            std::string m2 = starts_with(u2.name, "\\") ? u2.name.substr(1) : u2.name;
                            if (m2 != top) kept_mod_names.insert(m2);
                        }

                        for (const auto& cc : tcells) {
                            if (cc.ctype == "INV") {
                                std::string wi, wo;
                                for (const auto& [pn, wn] : cc.ports) {
                                    if (pn == "I") wi = wn;
                                    else if (pn == "O") wo = wn;
                                }
                                if (!wi.empty() && !wo.empty()) inv_in_map[wo] = wi;
                            } else if (cc.ctype == "IBUF" || cc.ctype == "TLVDS_IBUF" || cc.ctype == "ELVDS_IBUF") {
                                std::string wi, wo;
                                for (const auto& [pn, wn] : cc.ports) {
                                    if (pn == "I") wi = wn;
                                    else if (pn == "O") wo = wn;
                                }
                                if (!wi.empty() && !wo.empty()) ibuf_port_map[wo] = wi;
                            } else if (cc.ctype == "OBUF" || cc.ctype == "TBUF" || cc.ctype == "IOBUF") {
                                std::string wi, wo;
                                for (const auto& [pn, wn] : cc.ports) {
                                    if (pn == "I") wi = wn;
                                    else if (pn == "O" || pn == "IO") wo = wn;
                                }
                                if (!wi.empty() && !wo.empty()) obuf_port_map[wi] = wo;
                            }
                            if (cc.ctype == "PLL" || cc.ctype == "rPLL" || cc.ctype == "PLLA") {
                                double fm = pll_fclkin.count(cc.inst_norm) ? pll_fclkin[cc.inst_norm] : 0.0;
                                for (const auto& [pn, wn] : cc.ports) {
                                    if (pn == "CLKIN" && fm > 0.0) pll_in_mhz[wn] = fm;
                                }
                            }
                            for (const auto& [pn, wn] : cc.ports) {
                                if (wn.empty() || wn.find('{') != std::string::npos || wn == "1'b0" || wn == "1'b1") continue;
                                std::string lpn = to_lower_ascii(pn);
                                if (pn == "Q" || pn == "F" || pn == "O" || pn == "SUM" || starts_with(lpn, "clkout") || lpn == "oscout"
                                    || ends_with(pn, "_O_FABRIC_CLK") || ends_with(pn, "_CLKOUT")) {
                                    drv_map[wn] = {cc.ctype, cc.inst_norm, pn};
                                }
                                bool is_clk_in = (pn == "CLK" || pn == "CLKA" || pn == "CLKB" || pn == "PCLK" || pn == "FCLK");
                                if (!is_clk_in && kept_mod_names.count(cc.ctype)) {
                                    std::string lp = to_lower_ascii(pn);
                                    if (lp.find("clk") != std::string::npos && !starts_with(lp, "clkout")) {
                                        // Check if child kept module is a PLL wrapper: if its name contains pll, skip treating its CLKIN as fabric sequential
                                        if (to_lower_ascii(cc.ctype).find("pll") == std::string::npos) is_clk_in = true;
                                    }
                                }
                                if (is_clk_in && !hard_clk_gen.count(cc.ctype)) {
                                    std::string root_w = wn;
                                    while (inv_in_map.count(root_w)) root_w = inv_in_map[root_w];
                                    if (seq_clk_seen.insert(root_w).second) seq_clk_wires.push_back(root_w);
                                }
                            }
                        }

                        // Resolve any INV forward order
                        std::vector<std::string> norm_roots;
                        std::unordered_set<std::string> norm_seen;
                        for (std::string rw : seq_clk_wires) {
                            int guard = 0;
                            while (inv_in_map.count(rw) && ++guard < 8) rw = inv_in_map[rw];
                            if (norm_seen.insert(rw).second) norm_roots.push_back(rw);
                        }

                        std::vector<std::string> auto_sdc;
                        std::unordered_set<std::string> used_clk_names;
                        double def_per = 1000.0 / pnr_global_freq;
                        char pbuf[64];

                        for (const auto& rw : norm_roots) {
                            auto dit = drv_map.find(rw);
                            if (dit != drv_map.end()) {
                                const auto& [dct, dinst, dpin] = dit->second;
                                if (hard_clk_gen.count(dct) || kept_mod_names.count(dct)) continue;
                            }
                            if (auto iit = ibuf_port_map.find(rw); iit != ibuf_port_map.end()) {
                                const std::string& pnm = iit->second;
                                double per = pll_in_mhz.count(rw) ? (1000.0 / pll_in_mhz[rw]) : def_per;
                                std::snprintf(pbuf, sizeof(pbuf), "%.3f", per);
                                if (used_clk_names.insert(pnm).second) {
                                    auto_sdc.push_back("create_clock -name " + pnm + " -period " + pbuf + " [get_ports {" + pnm + "}]");
                                }
                            } else if (dit != drv_map.end()) {
                                const auto& [dct, dinst, dpin] = dit->second;
                                if (dct != "VCC" && dct != "GND") {
                                    std::string cnm = obuf_port_map.count(rw) ? (obuf_port_map[rw] + "_d") : rw;
                                    int sfx = 1;
                                    while (used_clk_names.count(cnm)) cnm += "_" + std::to_string(++sfx);
                                    used_clk_names.insert(cnm);
                                    std::snprintf(pbuf, sizeof(pbuf), "%.3f", def_per);
                                    auto_sdc.push_back("create_clock -name " + cnm + " -period " + pbuf + " [get_pins {" + dinst + "/" + dpin + "}]");
                                }
                            }
                        }

                        if (!auto_sdc.empty()) {
                            // Also preserve explicit FCLKIN base clocks on any PLL top input ports if auto_sdc is active
                            for (const auto& [pw, fm] : pll_in_mhz) {
                                if (auto iit = ibuf_port_map.find(pw); iit != ibuf_port_map.end()) {
                                    const std::string& pnm = iit->second;
                                    if (used_clk_names.insert(pnm).second) {
                                        std::snprintf(pbuf, sizeof(pbuf), "%.3f", 1000.0 / fm);
                                        auto_sdc.push_back("create_clock -name " + pnm + " -period " + pbuf + " [get_ports {" + pnm + "}]");
                                    }
                                }
                            }
                            eff_sdc_for_pnr = path_join(proj_stage_dir, "auto_default_clocks.sdc");
                            write_file_text(eff_sdc_for_pnr, join_strings(auto_sdc, "\n") + "\n");
                        }
                        break;
                    }
                } else if (!sdc_files.empty() && global_freq_mhz.value_or(100.0) > 0.0) {
                    // Official Gowin assigns Global_Freq default base clocks to unconstrained GTR12_QUAD PCS fabric
                    // clock outputs that drive sequential elements even when a user .sdc file is present.
                    double gtr_def_freq = global_freq_mhz.value_or(100.0);
                    std::string vg_txt = read_file_text(path_join(out, "05_netlist\\mapped.vg"));
                    if (vg_txt.find("GTR12_QUAD") != std::string::npos) {
                        auto norm_id = [](std::string_view sv) -> std::string {
                            std::string s = trim(sv);
                            if (!s.empty() && s[0] == '\\') return s.substr(1);
                            return s;
                        };
                        auto v_units = verilog_units(vg_txt);
                        for (const auto& vu : v_units) {
                            std::string mb = starts_with(vu.name, "\\") ? vu.name.substr(1) : vu.name;
                            if (mb != top) continue;
                            auto mlines = split_lines(vg_txt.substr(vu.start, vu.end - vu.start));
                            std::vector<std::pair<std::string, std::string>> gtr_clk_outs; // (inst/pin, wire)
                            std::unordered_set<std::string> seq_clk_wires;
                            for (size_t li = 1; li + 1 < mlines.size(); ++li) {
                                std::string tr = trim(mlines[li]);
                                if (!tr.empty() && tr.back() == '(' && !starts_with(tr, "input ") && !starts_with(tr, "output ") && !starts_with(tr, "wire ")) {
                                    auto ws = split_ws(std::string_view(tr).substr(0, tr.size() - 1));
                                    if (ws.size() == 2) {
                                        size_t cl = li + 1;
                                        while (cl + 1 < mlines.size() && trim(mlines[cl]) != ");") ++cl;
                                        if (cl + 1 < mlines.size()) {
                                            std::string ctype = ws[0], inst_n = norm_id(ws[1]);
                                            for (size_t pl = li + 1; pl < cl; ++pl) {
                                                std::string pt = trim(mlines[pl]);
                                                if (pt.empty() || pt[0] != '.') continue;
                                                size_t lp = pt.find('('), rp = pt.rfind(')');
                                                if (lp == std::string::npos || rp == std::string::npos || rp <= lp) continue;
                                                std::string pn = trim(std::string_view(pt).substr(1, lp - 1));
                                                std::string wn = norm_id(std::string_view(pt).substr(lp + 1, rp - lp - 1));
                                                if (wn.empty() || wn.find('{') != std::string::npos) continue;
                                                if (ctype == "GTR12_QUAD" && ends_with(pn, "_O_FABRIC_CLK")) {
                                                    gtr_clk_outs.emplace_back(inst_n + "/" + pn, wn);
                                                }
                                                if (ctype != "GTR12_QUAD" && (pn == "CLK" || pn == "CLKA" || pn == "CLKB" || pn == "PCLK" || pn == "FCLK")) {
                                                    seq_clk_wires.insert(wn);
                                                }
                                            }
                                            li = cl;
                                        }
                                    }
                                }
                            }
                            if (!gtr_clk_outs.empty()) {
                                std::unordered_map<std::string, std::vector<std::string>> wire_aliases;
                                // Also collect direct scalar wire `assign lhs = rhs;` and `buf (lhs, rhs);` aliases strictly
                                // inside the `top` module of 02_bridge/design.v (before Yosys opt_clean collapses them).
                                std::string dv_p = path_join(out, "02_bridge\\design.v");
                                if (file_exists(dv_p)) {
                                    auto valid_w = [](const std::string& w) {
                                        return !w.empty() && w.find_first_of(" \t?~&|^+-*/{}[](),") == std::string::npos;
                                    };
                                    auto link_alias = [&](const std::string& lhs, const std::string& rhs) {
                                        if (valid_w(lhs) && valid_w(rhs) && lhs != rhs) {
                                            auto& dl = wire_aliases[lhs];
                                            if (std::find(dl.begin(), dl.end(), lhs) == dl.end()) dl.push_back(lhs);
                                            if (std::find(dl.begin(), dl.end(), rhs) == dl.end()) dl.push_back(rhs);
                                            auto& dr = wire_aliases[rhs];
                                            if (std::find(dr.begin(), dr.end(), rhs) == dr.end()) dr.push_back(rhs);
                                            if (std::find(dr.begin(), dr.end(), lhs) == dr.end()) dr.push_back(lhs);
                                        }
                                    };
                                    std::string dv_txt = SynAccelLib::instance().strip_line_comments(read_file_text(dv_p));
                                    for (const auto& du : verilog_units(dv_txt)) {
                                        std::string dmb = starts_with(du.name, "\\") ? du.name.substr(1) : du.name;
                                        if (dmb != top) continue;
                                        for (const auto& dln : split_lines(dv_txt.substr(du.start, du.end - du.start))) {
                                            std::string dt = trim(dln);
                                            size_t sc = dt.find(';');
                                            if (sc == std::string::npos) continue;
                                            if (starts_with(dt, "assign ")) {
                                                size_t eq = dt.find('=');
                                                if (eq == std::string::npos || eq >= sc) continue;
                                                link_alias(
                                                    norm_id(std::string_view(dt).substr(7, eq - 7)),
                                                    norm_id(std::string_view(dt).substr(eq + 1, sc - eq - 1)));
                                            } else if (starts_with(dt, "buf ") || starts_with(dt, "buf(")) {
                                                size_t lp = dt.find('('), rp = dt.rfind(')', sc);
                                                if (lp != std::string::npos && rp != std::string::npos && rp > lp) {
                                                    size_t cm = dt.find(',', lp + 1);
                                                    if (cm != std::string::npos && cm < rp && dt.find(',', cm + 1) >= rp) {
                                                        link_alias(
                                                            norm_id(std::string_view(dt).substr(lp + 1, cm - lp - 1)),
                                                            norm_id(std::string_view(dt).substr(cm + 1, rp - cm - 1)));
                                                    }
                                                }
                                            }
                                        }
                                        break;
                                    }
                                }
                                // Map SigBit -> wire aliases from 03_resources/resources.json and 05_netlist/final.json
                                // so output-side wire aliases (e.g. *_clkout_o) are checked and preserved even if pruned in final.json
                                for (const char* rel_j : {"03_resources\\resources.json", "05_netlist\\final.json"}) {
                                    std::string fj_p = path_join(out, rel_j);
                                    if (!file_exists(fj_p)) continue;
                                    JsonValue fj = parse_json_file(fj_p);
                                    if (fj.contains("modules") && fj["modules"].is_obj() && fj["modules"].contains(top)) {
                                        const JsonValue& tm = fj["modules"][top];
                                        if (tm.contains("netnames") && tm["netnames"].is_obj()) {
                                            std::unordered_map<int64_t, std::vector<std::string>> bit_names;
                                            for (const auto& [nn, nv] : tm["netnames"].as_obj()) {
                                                if (nv.is_obj() && nv.contains("bits") && nv["bits"].is_arr() && nv["bits"].size() == 1 && nv["bits"][size_t(0)].is_int()) {
                                                    bit_names[nv["bits"][size_t(0)].as_i64()].push_back(nn);
                                                }
                                            }
                                            for (const auto& [_, names] : bit_names) {
                                                for (const auto& nm : names) {
                                                    auto& dst = wire_aliases[nm];
                                                    for (const auto& cand : names) {
                                                        if (std::find(dst.begin(), dst.end(), cand) == dst.end()) dst.push_back(cand);
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                                std::string sdc_raw = read_file_text(merged_sdc_p);
                                // Semantically extract active clock names and target patterns from create_clock / create_generated_clock
                                // (ignoring comments, set_false_path / set_clock_groups, and accidental substring overlaps).
                                std::unordered_set<std::string> sdc_clk_exact;
                                std::vector<std::string> sdc_clk_globs;
                                {
                                    std::string sdc_joined;
                                    for (const auto& raw_l : split_lines(sdc_raw)) {
                                        size_t hp = raw_l.find('#');
                                        std::string sub = (hp == std::string::npos) ? raw_l : raw_l.substr(0, hp);
                                        std::string rt = rtrim(sub);
                                        if (!rt.empty() && rt.back() == '\\') {
                                            rt.pop_back();
                                            sdc_joined += rt + " ";
                                        } else {
                                            sdc_joined += rt + "\n";
                                        }
                                    }
                                    auto add_sdc_tok = [&](std::string tok) {
                                        while (!tok.empty() && (tok.front() == '{' || tok.front() == '"' || tok.front() == '[' || tok.front() == '\\')) tok.erase(tok.begin());
                                        while (!tok.empty() && (tok.back() == '}' || tok.back() == '"' || tok.back() == ']')) tok.pop_back();
                                        tok = trim(tok);
                                        if (tok.empty() || tok == "get_nets" || tok == "get_pins" || tok == "get_ports" || tok == "get_clocks") return;
                                        if (tok.find('*') != std::string::npos || tok.find('?') != std::string::npos) {
                                            sdc_clk_globs.push_back(tok);
                                        } else {
                                            sdc_clk_exact.insert(tok);
                                        }
                                    };
                                    for (const auto& cln : split_lines(sdc_joined)) {
                                        std::string ct = trim(cln);
                                        if (!starts_with(ct, "create_clock") && !starts_with(ct, "create_generated_clock")) continue;
                                        auto toks = split_ws(ct);
                                        for (size_t ti = 1; ti < toks.size(); ++ti) {
                                            const std::string& tk = toks[ti];
                                            if (tk == "-period" || tk == "-waveform" || tk == "-divide_by" || tk == "-multiply_by" || tk == "-duty_cycle" || tk == "-edges") {
                                                ++ti;
                                                continue;
                                            }
                                            if (tk == "-name" || tk == "-source" || tk == "-master_clock") {
                                                if (ti + 1 < toks.size()) add_sdc_tok(toks[++ti]);
                                                continue;
                                            }
                                            if (!tk.empty() && tk[0] == '-') continue;
                                            add_sdc_tok(tk);
                                        }
                                    }
                                }
                                auto glob_match = [](std::string_view pat, std::string_view str) -> bool {
                                    size_t pi = 0, si = 0, star_p = std::string_view::npos, star_s = 0;
                                    while (si < str.size()) {
                                        if (pi < pat.size() && (pat[pi] == '?' || pat[pi] == str[si])) {
                                            ++pi; ++si;
                                        } else if (pi < pat.size() && pat[pi] == '*') {
                                            star_p = pi++; star_s = si;
                                        } else if (star_p != std::string_view::npos) {
                                            pi = star_p + 1; si = ++star_s;
                                        } else {
                                            return false;
                                        }
                                    }
                                    while (pi < pat.size() && pat[pi] == '*') ++pi;
                                    return pi == pat.size();
                                };
                                auto is_sdc_constrained = [&](const std::string& cand) -> bool {
                                    if (sdc_clk_exact.count(cand)) return true;
                                    for (const auto& gp : sdc_clk_globs) {
                                        if (glob_match(gp, cand)) return true;
                                    }
                                    return false;
                                };

                                std::vector<std::string> extra_clks;
                                std::unordered_set<std::string> added_wires;
                                char pbuf[64];
                                std::snprintf(pbuf, sizeof(pbuf), "%.3f", 1000.0 / gtr_def_freq);
                                for (const auto& [ip, wn] : gtr_clk_outs) {
                                    if (!seq_clk_wires.count(wn) || !added_wires.insert(wn).second) continue;
                                    bool already_in_sdc = is_sdc_constrained(wn) || is_sdc_constrained(ip);
                                    std::string best_nm = wn;
                                    {
                                        std::vector<std::string> q{wn};
                                        std::unordered_set<std::string> vis{wn};
                                        for (size_t qi = 0; qi < q.size(); ++qi) {
                                            auto ait = wire_aliases.find(q[qi]);
                                            if (ait == wire_aliases.end()) continue;
                                            for (const auto& al : ait->second) {
                                                if (vis.insert(al).second) q.push_back(al);
                                            }
                                        }
                                        auto clk_alias_rank = [](const std::string& s) -> int {
                                            bool is_top = (s.find('.') == std::string::npos && s.find('/') == std::string::npos);
                                            if (ends_with(s, "_clkout_o")) return is_top ? 4 : 2;
                                            if (ends_with(s, "_clkout")) return is_top ? 3 : 1;
                                            return 0;
                                        };
                                        int best_rank = clk_alias_rank(best_nm);
                                        for (const auto& al : q) {
                                            if (is_sdc_constrained(al)) { already_in_sdc = true; break; }
                                            int rk = clk_alias_rank(al);
                                            if (rk > best_rank) {
                                                best_nm = al;
                                                best_rank = rk;
                                            }
                                        }
                                    }
                                    if (!already_in_sdc) {
                                        extra_clks.push_back("create_clock -name " + best_nm + " -period " + pbuf + " [get_nets {" + wn + "}]");
                                    }
                                }
                                if (!extra_clks.empty()) {
                                    eff_sdc_for_pnr = path_join(proj_stage_dir, "auto_augmented_clocks.sdc");
                                    write_file_text(eff_sdc_for_pnr, sdc_raw + "\n" + join_strings(extra_clks, "\n") + "\n");
                                }
                            }
                            break;
                        }
                    }
                }

                SetEnvironmentVariableW(L"NEXTGOWIN_EMIT_IDE_HTML", L"1");
                auto [prec, pchk] = run_ownpr_full(
                    path_join(out, "05_netlist\\mapped.vg"),
                    path_join(out, "05_netlist\\final.json"),
                    eff_sdc_for_pnr,
                    merged_cst_p,
                    use_dev_cfg,
                    "",
                    path_join(out, "06_pnr"),
                    eff_timeout,
                    dev_spec.dev_p,
                    dev_spec.part_number,
                    use_csr_path,
                    pnr_global_freq
                );
                SetEnvironmentVariableW(L"NEXTGOWIN_EMIT_IDE_HTML", nullptr);
                if (prec["vendor_placement_executed"].as_bool(true) || prec["vendor_routing_executed"].as_bool(true)) {
                    throw std::runtime_error("Custom ownpr backend executed vendor P&R");
                }
                JsonValue ret = JsonValue::object();
                ret["native"] = std::move(prec);
                ret["timing"] = std::move(pchk);
                return ret;
            });
            timing_summary = physical["timing"];
            result["timing_closed"] = timing_summary["timing_closed"];
            result["frequency_reports"] = timing_summary.contains("frequency_reports") ? timing_summary["frequency_reports"] : JsonValue::object();
            result["violated_endpoints"] = timing_summary.contains("violated_endpoints") ? timing_summary["violated_endpoints"] : JsonValue::object();

            auto rewrite_tr_html_for_base = [](std::string txt, const std::string& base_nm) -> std::string {
                auto repl_all = [](std::string& s, const std::string& from, const std::string& to) {
                    size_t p = 0;
                    while ((p = s.find(from, p)) != std::string::npos) {
                        s.replace(p, from.size(), to);
                        p += to.size();
                    }
                };
                repl_all(txt, "mapped_tr_cata.html", base_nm + "_tr_cata.html");
                repl_all(txt, "mapped_tr_content.html", base_nm + "_tr_content.html");
                return txt;
            };

            // Copy convenient top-level deliverables named after the project into <out>
            const std::pair<const char*, std::string> deliverables[] = {
                {"mapped.fs",              gprj_stem + ".fs"},
                {"mapped.tr",              gprj_stem + ".tr"},
                {"mapped.rpt.txt",         gprj_stem + ".rpt.txt"},
                {"mapped.rpt.html",        gprj_stem + ".rpt.html"},
                {"mapped.pin.html",        gprj_stem + ".pin.html"},
                {"mapped.pin.txt",         gprj_stem + ".pin.txt"},
                {"mapped.vo",              gprj_stem + ".vo"},
                {"mapped_tr_content.html", gprj_stem + "_tr_content.html"},
            };
            JsonValue deliv_j = JsonValue::object();
            for (const auto& [src_fn, dst_fn] : deliverables) {
                std::string sp = path_join(out, std::string("06_pnr\\") + src_fn);
                if (file_exists(sp)) {
                    std::string dp = path_join(out, dst_fn);
                    copy_file_overwrite(sp, dp);
                    deliv_j[dst_fn] = path_join(out_real, dst_fn);
                }
            }
            for (const auto& [src_fn, dst_fn] : {
                std::pair{"mapped.tr.html", gprj_stem + ".tr.html"},
                std::pair{"mapped_tr_cata.html", gprj_stem + "_tr_cata.html"}
            }) {
                std::string sp = path_join(out, std::string("06_pnr\\") + src_fn);
                if (file_exists(sp)) {
                    std::string dp = path_join(out, dst_fn);
                    write_file_text(dp, rewrite_tr_html_for_base(read_file_text(sp), gprj_stem));
                    deliv_j[dst_fn] = path_join(out_real, dst_fn);
                }
            }
            result["deliverables"] = std::move(deliv_j);
        }

        // Verify 100% of user project & source files remained completely untouched
        for (const auto& [src_p, orig_sha] : source_guard_shas) {
            if (!file_exists(src_p) || sha256_file(src_p) != orig_sha) {
                throw std::runtime_error("Source immutability check failed — user file changed during run: " + src_p);
            }
        }
        result["source_files_unmodified"] = true;

        // Synchronize / overwrite Gowin IDE `impl/gwsynthesis` & `impl/pnr` outputs so Gowin IDE & Programmer work directly
        std::string ide_fs_path;
        if (sync_ide) {
            JsonValue ide_sync_j = JsonValue::object();
            ide_sync_j["output_base_name"] = ide_base;
            std::string mapped_vg_real = path_join(out, "05_netlist\\mapped.vg");
            if (file_exists(mapped_vg_real)) {
                std::string gwsyn_dir = path_join(gprj_dir, "impl\\gwsynthesis");
                make_dirs(gwsyn_dir, true);
                std::string ide_vg = path_join(gwsyn_dir, ide_base + ".vg");
                copy_file_overwrite(mapped_vg_real, ide_vg);
                ide_sync_j["gwsynthesis_vg"] = ide_vg;
            }
            if (stop_after == "bitstream") {
                std::string ide_pnr_dir = path_join(gprj_dir, "impl\\pnr");
                make_dirs(ide_pnr_dir, true);

                // Remove stale vendor-only artifacts so Gowin IDE / Programmer never reads outdated files
                for (const std::string& stale_sfx : {".bin", ".power.html"}) {
                    std::string sp = path_join(ide_pnr_dir, ide_base + stale_sfx);
                    if (file_exists(sp)) remove_all(sp);
                }

                const std::pair<const char*, std::string> ide_copies[] = {
                    {"mapped.fs",              ide_base + ".fs"},
                    {"mapped.tr",              ide_base + ".tr"},
                    {"mapped.rpt.txt",         ide_base + ".rpt.txt"},
                    {"mapped.rpt.html",        ide_base + ".rpt.html"},
                    {"mapped.pin.html",        ide_base + ".pin.html"},
                    {"mapped.pin.txt",         ide_base + ".pin.txt"},
                    {"mapped.vo",              ide_base + ".vo"},
                    {"mapped.cst",             ide_base + ".cst"},
                    {"mapped_tr_content.html", ide_base + "_tr_content.html"},
                };
                JsonValue pnr_files_j = JsonValue::object();
                for (const auto& [src_fn, dst_fn] : ide_copies) {
                    std::string sp = path_join(out, std::string("06_pnr\\") + src_fn);
                    if (file_exists(sp)) {
                        std::string dp = path_join(ide_pnr_dir, dst_fn);
                        copy_file_overwrite(sp, dp);
                        pnr_files_j[dst_fn] = dp;
                    }
                }
                auto rewrite_tr = [](std::string txt, const std::string& base_nm) -> std::string {
                    for (const auto& [from, to] : {
                        std::pair{std::string("mapped_tr_cata.html"), base_nm + "_tr_cata.html"},
                        std::pair{std::string("mapped_tr_content.html"), base_nm + "_tr_content.html"}
                    }) {
                        size_t p = 0;
                        while ((p = txt.find(from, p)) != std::string::npos) {
                            txt.replace(p, from.size(), to);
                            p += to.size();
                        }
                    }
                    return txt;
                };
                for (const auto& [src_fn, dst_fn] : {
                    std::pair{"mapped.tr.html", ide_base + ".tr.html"},
                    std::pair{"mapped_tr_cata.html", ide_base + "_tr_cata.html"}
                }) {
                    std::string sp = path_join(out, std::string("06_pnr\\") + src_fn);
                    if (file_exists(sp)) {
                        std::string dp = path_join(ide_pnr_dir, dst_fn);
                        write_file_text(dp, rewrite_tr(read_file_text(sp), ide_base));
                        pnr_files_j[dst_fn] = dp;
                    }
                }
                ide_fs_path = path_join(ide_pnr_dir, ide_base + ".fs");
                ide_sync_j["pnr_files"] = std::move(pnr_files_j);
            }
            result["gowin_ide_sync"] = std::move(ide_sync_j);
        }

        double total_sec = perf_counter() - entry_t;
        result["status"] = "complete";
        result["stop_after"] = stop_after;
        result["wall_seconds"] = total_sec;
        result["total_budget_used_seconds"] = budget.used();
        save_manifest();
        work_alias.sync_back();
        set_active_budget(nullptr);

        // Print concise human-readable final summary
        log_msg(false, "========================================================================");
        char hbuf[128];
        std::snprintf(hbuf, sizeof(hbuf), "FLOW COMPLETE in %.2fs", total_sec);
        log_msg(false, hbuf);
        if (stop_after == "bitstream") {
            bool closed = timing_summary["timing_closed"].as_bool(false);
            int64_t su_v = timing_summary["violated_endpoints"]["Setup"].as_i64(0);
            int64_t hd_v = timing_summary["violated_endpoints"]["Hold"].as_i64(0);
            int64_t rc_v = timing_summary["violated_endpoints"].contains("Recovery") ? timing_summary["violated_endpoints"]["Recovery"].as_i64(0) : 0;
            int64_t rm_v = timing_summary["violated_endpoints"].contains("Removal")  ? timing_summary["violated_endpoints"]["Removal"].as_i64(0)  : 0;
            int64_t pw_v = timing_summary["violated_endpoints"].contains("PulseWidth") ? timing_summary["violated_endpoints"]["PulseWidth"].as_i64(0) : 0;
            log_msg(false, std::string("  Timing Closure : ") + (closed ? "CLOSED (PASS)" : "VIOLATED") +
                           "  [Setup=" + std::to_string(su_v) + ", Hold=" + std::to_string(hd_v) +
                           ", Recovery=" + std::to_string(rc_v) + ", Removal=" + std::to_string(rm_v) +
                           ", PulseWidth=" + std::to_string(pw_v) + "]");
            if (timing_summary.contains("frequency_reports") && timing_summary["frequency_reports"].is_obj()) {
                for (const auto& [clk_n, fj] : timing_summary["frequency_reports"].as_obj()) {
                    char cbuf[256];
                    std::snprintf(cbuf, sizeof(cbuf), "  Clock %-14s : Target %7.3f MHz | Reported Fmax %7.3f MHz",
                                  clk_n.c_str(), fj["constraint_mhz"].as_f64(), fj["reported_fmax_mhz"].as_f64());
                    log_msg(false, cbuf);
                }
            }
            log_msg(false, "  Bitstream (.fs): " + path_join(out_real, gprj_stem + ".fs"));
            if (!ide_fs_path.empty()) {
                log_msg(false, "  Gowin IDE (.fs): " + ide_fs_path + " (impl/pnr & impl/gwsynthesis synced)");
            }
            log_msg(false, "  Timing (.tr)   : " + path_join(out_real, gprj_stem + ".tr"));
            log_msg(false, "  Resource (.rpt): " + path_join(out_real, gprj_stem + ".rpt.txt"));
        }
        log_msg(false, "  Run Log        : " + run_log_path);
        log_msg(false, "========================================================================");
        return 0;
    } catch (const std::exception& ex) {
        set_active_budget(nullptr);
        log_msg(true, "========================================================================");
        std::string stg_hdr = (err_stage_idx > 0)
            ? ("Stage [" + std::to_string(err_stage_idx) + "/6] " + err_stage_name)
            : ("Stage " + err_stage_name);
        log_msg(true, "[nextgowin] ERROR in " + stg_hdr + ":");
        log_msg(true, "  Project : " + gprj_real);
        if (!err_dev_info.empty()) log_msg(true, "  Target  : " + err_dev_info);
        log_msg(true, "  Details : " + std::string(ex.what()));
        log_msg(true, "  Run Log : " + run_log_path);
        log_msg(true, "========================================================================");
        return 1;
    }
}

int run_full_flow_cli(const std::vector<std::string>& args) {
    // Check if invoked with a positional `<project.gprj>` (drag-and-drop) or `--project <project.gprj>`
    std::string gprj_arg;
    std::vector<std::string> rem_args;
    for (size_t i = 0; i < args.size(); ++i) {
        const std::string& a = args[i];
        if (a == "--project" && i + 1 < args.size()) {
            gprj_arg = args[++i];
        } else if (!starts_with(a, "-") && gprj_arg.empty() &&
                   (to_lower_ascii(file_ext(a)) == ".gprj" || to_lower_ascii(file_ext(a)) == ".prj")) {
            gprj_arg = a;
        } else {
            rem_args.push_back(a);
        }
    }
    if (!gprj_arg.empty()) {
        return run_gprj_project_flow(gprj_arg, rem_args);
    }

    double entry_t = perf_counter();
    std::string cfg_arg, out_arg, stop_after = "bitstream";
    bool resume = false;
    std::optional<int> pnr_timeout_opt, total_timeout_opt;
    std::vector<std::string> net_alias_args;

    for (size_t i = 0; i < args.size(); ++i) {
        const std::string& a = args[i];
        if (a == "--config" && i + 1 < args.size()) cfg_arg = args[++i];
        else if (a == "--out" && i + 1 < args.size()) out_arg = args[++i];
        else if (a == "--stop-after" && i + 1 < args.size()) stop_after = args[++i];
        else if (a == "--resume") resume = true;
        else if (a == "--pnr-timeout" && i + 1 < args.size()) pnr_timeout_opt = std::stoi(args[++i]);
        else if (a == "--total-timeout" && i + 1 < args.size()) total_timeout_opt = std::stoi(args[++i]);
        else if (a == "--net-alias" && i + 1 < args.size()) net_alias_args.push_back(args[++i]);
        else throw std::runtime_error("Unrecognized CLI option: " + a);
    }

    if (cfg_arg.empty() || out_arg.empty()) {
        throw std::runtime_error("Pass a <project.gprj> file (or both --config <path> and --out <path>)");
    }
    if (stop_after != "frontend" && stop_after != "resources" && stop_after != "mapping" && stop_after != "bitstream") {
        throw std::runtime_error("Invalid --stop-after stage: " + stop_after);
    }

    std::string cfg_path = resolve_path(cfg_arg);
    std::string cfg_parent = parent_dir(cfg_path);
    JsonValue config = parse_json_file(cfg_path);

    static const std::unordered_set<std::string> ALLOWED_KEYS = {
        "project", "sdc", "cst", "rao", "device_config", "period_ps", "strategy", "physical_profile",
        "timeout", "total_timeout", "macro_profile", "no_iob_registers", "output_iob_registers",
        "keep_hierarchy", "auto_hierarchy", "max_logic_cells", "net_aliases", "resource_optimizations",
        "soft_comparisons", "register_nextstate_mapping", "register_alternatives", "architecture_recipe",
        "logic_replication", "pack_dsp_registers", "pack_dsp_inverted_enable", "register_replication",
        "dedicated_cluster_replication", "register_control_penalty_ps", "no_lutram", "pnr_backend"
    };
    for (const auto& [k, _] : config.as_obj()) {
        if (!ALLOWED_KEYS.count(k)) throw std::runtime_error("Unknown flow configuration key: " + k);
    }

    std::string strategy = config.contains("strategy") ? config["strategy"].as_str() : "acd_remap";
    bool reg_alt = config.contains("register_alternatives") && config["register_alternatives"].as_bool();
    bool pack_dsp = config.contains("pack_dsp_registers") && config["pack_dsp_registers"].as_bool();
    bool pack_dsp_inv = config.contains("pack_dsp_inverted_enable") && config["pack_dsp_inverted_enable"].as_bool();
    bool no_lutram = config.contains("no_lutram") && config["no_lutram"].as_bool();
    if (strategy == "abc9_box" && reg_alt) {
        throw std::runtime_error("abc9_box requires register_alternatives=false");
    }
    if (strategy == "abc9_box" && pack_dsp) {
        throw std::runtime_error("DSP packing certificates require the stable-wire architecture mapper");
    }
    if (pack_dsp_inv && !pack_dsp) {
        throw std::runtime_error("Inverted DSP enables require DSP register packing");
    }

    std::string pnr_backend = config.contains("pnr_backend") ? config["pnr_backend"].as_str() : "ownpr";
    if (pnr_backend != "ownpr") {
        throw std::runtime_error("Standalone native flow only permits pnr_backend='ownpr' (vendor P&R is disabled)");
    }

    int pnr_timeout = pnr_timeout_opt.has_value() ? *pnr_timeout_opt
                    : (config.contains("timeout") ? static_cast<int>(config["timeout"].as_i64()) : 3600);
    if (pnr_timeout <= 0 || pnr_timeout > 3600) {
        throw std::runtime_error("P&R timeout must be 1..3600 seconds");
    }
    int total_timeout = total_timeout_opt.has_value() ? *total_timeout_opt
                      : (config.contains("total_timeout") ? static_cast<int>(config["total_timeout"].as_i64()) : 3600);

    OrderedMap<JsonValue> aliases;
    if (config.contains("net_aliases") && config["net_aliases"].is_obj()) {
        aliases = config["net_aliases"].as_obj();
    }
    for (const auto& na : net_alias_args) {
        size_t eq = na.find('=');
        size_t col = (eq != std::string::npos) ? na.find(':', eq + 1) : std::string::npos;
        if (eq == std::string::npos || col == std::string::npos) {
            throw std::runtime_error("Invalid --net-alias (expected LEGACY=MODULE:NET): " + na);
        }
        JsonValue av = JsonValue::object();
        av["module"] = na.substr(eq + 1, col - eq - 1);
        av["net"] = na.substr(col + 1);
        aliases.set(na.substr(0, eq), std::move(av));
    }

    std::string out_real = resolve_path(out_arg);
    double prior_sec = 0.0;
    if (resume) {
        JsonValue retained = parse_json_file(path_join(out_real, "manifest.json"));
        for (const auto& st : retained["stages"].as_arr()) {
            std::string sn = st["name"].as_str();
            if (sn == "frontend" || sn == "bridge" || sn == "resources" || sn == "mapping") {
                prior_sec += st["wall_seconds"].as_f64();
            }
        }
    }

    FlowBudget budget(total_timeout, prior_sec + (perf_counter() - entry_t));
    set_active_budget(&budget);
    (void)budget.remaining();

    std::optional<std::tuple<JsonValue, JsonValue, JsonValue>> resumed;
    if (resume) {
        if (stop_after != "bitstream") throw std::runtime_error("Resume targets final emission/P&R only");
        resumed = check_flow_checkpoint(out_real, cfg_path);
        const auto& prev_m = std::get<0>(*resumed);
        if (prev_m.contains("effective_net_aliases") && prev_m["effective_net_aliases"].is_obj()) {
            OrderedMap<JsonValue> merged = prev_m["effective_net_aliases"].as_obj();
            for (const auto& [k, v] : aliases) merged.set(k, v);
            aliases = std::move(merged);
        }
        std::string hist_dir = path_join(out_real, "attempts");
        make_dirs(hist_dir, true);
        int att_idx = static_cast<int>(list_dir(hist_dir).size());
        write_json_file(path_join(hist_dir, "attempt_" + std::to_string(att_idx) + ".json"), prev_m, 2);
        for (const char* sdn : {"05_netlist", "06_pnr"}) {
            std::string sp = path_join(out_real, sdn);
            if (!path_exists(sp)) continue;
            int fcnt = 0;
            for (const auto& de : list_dir(out_real)) if (starts_with(de.name, std::string(sdn) + "_failed_")) ++fcnt;
            std::string tp = path_join(out_real, std::string(sdn) + "_failed_" + std::to_string(fcnt));
            if (!is_subpath_of(sp, out_real) || !is_subpath_of(tp, out_real)) throw std::runtime_error("Attempt archive path escapes flow directory");
            rename_path(sp, tp);
        }
    } else {
        make_dirs(out_real, false);
    }

    AsciiDirAlias work_alias(out_real, true);
    std::string out = work_alias.path();

    JsonValue stages_arr = JsonValue::array();
    if (resumed.has_value()) {
        for (const auto& st : std::get<0>(*resumed)["stages"].as_arr()) {
            std::string sn = st["name"].as_str();
            if (sn == "frontend" || sn == "bridge" || sn == "resources" || sn == "mapping") stages_arr.push_back(st);
        }
    }

    JsonValue alias_j = JsonValue::object();
    for (const auto& [k, v] : aliases) alias_j[k] = v;

    JsonValue result = JsonValue::object();
    result["schema"] = "nextgowin-cpu-flow-v1";
    result["status"] = "running";
    result["execution"] = "native-cpp20-tier1-sse3";
    result["python_runtime_present"] = false;
    result["acceleration_tier"] = 1;
    result["acceleration_variant"] = "tier1";
    result["gowin_version_check"] = build_gowin_version_manifest_entry();
    result["config"] = config;
    result["config_sha256"] = sha256_file(cfg_path);
    result["stages"] = std::move(stages_arr);
    result["board_programmed"] = false;
    result["resumed_verified_mapping"] = resumed.has_value();
    result["effective_net_aliases"] = std::move(alias_j);
    result["effective_pnr_timeout"] = pnr_timeout;
    result["frontend_only_vendor"] = true;
    result["own_mapping_source"] = "Resource-inferred Boolean graph; not vendor-mapped user LUTs";
    result["total_timeout_seconds"] = total_timeout;

    auto save = [&]() { write_json_file(path_join(out, "manifest.json"), result, 2); };

    auto run_stage = [&](const char* name, auto&& fn) -> JsonValue {
        (void)budget.remaining();
        double st0 = perf_counter();
        result["active_stage"] = name;
        save();
        std::fprintf(stdout, "Starting %s\n", name);
        std::fflush(stdout);
        JsonValue val = fn();
        JsonValue srec = JsonValue::object();
        srec["name"] = name;
        srec["wall_seconds"] = perf_counter() - st0;
        srec["result"] = val;
        result["stages"].push_back(std::move(srec));
        save();
        (void)budget.remaining();
        std::fprintf(stdout, "Completed %s\n", name);
        std::fflush(stdout);
        return val;
    };

    try {
        JsonValue frontend = resumed.has_value()
            ? std::get<1>(*resumed)
            : run_stage("frontend", [&]() {
                return export_gowin_frontend(resolve_flow_input_path(config, "project", cfg_parent), path_join(out, "01_frontend"));
            });
        std::string top = frontend["top"].as_str();

        if (!resumed.has_value() && stop_after != "frontend") {
            run_stage("bridge", [&]() {
                std::string folder = path_join(out, "02_bridge");
                make_dirs(folder, false);
                std::string elab_p = path_join(out, "01_frontend\\elaborated.v");
                std::string exported = read_file_text(elab_p);
                std::string macro_prof = resolve_flow_input_path(config, "macro_profile", cfg_parent);
                if (!macro_prof.empty()) {
                    std::string rao_p = resolve_flow_input_path(config, "rao", cfg_parent);
                    auto [exp2, payload, macro_rec] = gowin_macros_prepare(exported, macro_prof, rao_p);
                    exported = std::move(exp2);
                    write_file_text(path_join(folder, "macro.vg"), payload);
                    write_json_file(path_join(folder, "macro.json"), macro_rec, 2);
                }
                auto [des_v, rec] = verific_bridge_convert(exported);
                std::string des_p = path_join(folder, "design.v");
                std::string vc_p = path_join(folder, "vendor_cells.v");
                write_file_text(des_p, des_v);
                write_file_text(vc_p, rec["vendor_library_verilog"].as_str());
                rec.as_obj().erase("vendor_library_verilog");
                rec["source_sha256"] = sha256_file(elab_p);
                rec["output_sha256"] = sha256_file(des_p);
                rec["vendor_library_sha256"] = sha256_file(vc_p);
                int64_t n_init = static_cast<int64_t>(rec["initial_values"].size());
                int64_t n_mem  = static_cast<int64_t>(rec["restored_memories"].size());
                int64_t n_spec = static_cast<int64_t>(rec["primitive_specializations"].size());
                write_json_file(path_join(folder, "manifest.json"), rec, 2);
                JsonValue ret = JsonValue::object();
                ret["initial_values"] = n_init;
                ret["memories"] = n_mem;
                ret["primitive_specializations"] = n_spec;
                return ret;
            });

            run_stage("resources", [&]() {
                std::vector<std::string> keep_h;
                if (config.contains("keep_hierarchy") && config["keep_hierarchy"].is_arr()) {
                    for (const auto& kh : config["keep_hierarchy"].as_arr()) keep_h.push_back(kh.as_str());
                }
                bool res_opt = !config.contains("resource_optimizations") || config["resource_optimizations"].as_bool();
                bool soft_c  = config.contains("soft_comparisons") && config["soft_comparisons"].as_bool();
                JsonValue auto_h = config.contains("auto_hierarchy") ? config["auto_hierarchy"] : JsonValue(false);
                return infer_resources_stage(
                    path_join(out, "02_bridge"),
                    path_join(out, "03_resources"),
                    top, keep_h, res_opt, soft_c, reg_alt, auto_h, pack_dsp, pack_dsp_inv, no_lutram
                );
            });
        }

        if (stop_after != "frontend" && stop_after != "resources") {
            std::optional<RtlilDesign> in_mem_mapped;
            JsonValue mapped = resumed.has_value()
                ? std::get<2>(*resumed)
                : run_stage("mapping", [&]() {
                    double per_ps = config["period_ps"].as_f64();
                    std::string recipe = config.contains("architecture_recipe") ? config["architecture_recipe"].as_str() : "wide";
                    auto [mrec, mdes] = map_resource_design(
                        path_join(out, "03_resources\\resources.json"),
                        path_join(out, "04_mapping"),
                        per_ps, strategy, recipe, true
                    );
                    in_mem_mapped = std::move(mdes);
                    return mrec;
                });
            result["local_model_budgets_satisfied"] = mapped["all_local_budgets_satisfied"];

            run_stage("emit", [&]() {
                std::optional<int64_t> max_lc;
                if (config.contains("max_logic_cells") && !config["max_logic_cells"].is_null()) {
                    max_lc = config["max_logic_cells"].as_i64();
                }
                bool opt_res = !config.contains("resource_optimizations") || config["resource_optimizations"].as_bool();
                bool map_reg = config.contains("register_nextstate_mapping") && config["register_nextstate_mapping"].as_bool();
                JsonValue log_rep = config.contains("logic_replication") ? config["logic_replication"] : JsonValue(false);
                JsonValue reg_rep = config.contains("register_replication") ? config["register_replication"] : JsonValue(false);
                JsonValue cls_rep = config.contains("dedicated_cluster_replication") ? config["dedicated_cluster_replication"] : JsonValue(false);
                double reg_pen = config.contains("register_control_penalty_ps") ? config["register_control_penalty_ps"].as_f64() : 0.0;

                JsonValue val = materialize_netlist_stage(
                    path_join(out, "04_mapping\\mapped.json"),
                    in_mem_mapped ? &(*in_mem_mapped) : nullptr,
                    path_join(out, "05_netlist"),
                    top,
                    path_join(out, "02_bridge\\vendor_cells.v"),
                    max_lc, aliases, opt_res, map_reg, reg_alt, log_rep, reg_rep, cls_rep, reg_pen
                );
                in_mem_mapped.reset();

                std::string macro_vg = path_join(out, "02_bridge\\macro.vg");
                std::string mapped_vg = path_join(out, "05_netlist\\mapped.vg");
                if (file_exists(macro_vg)) {
                    append_file_text(mapped_vg, "\n" + read_file_text(macro_vg));
                    val["linked_native_macro_sha256"] = sha256_file(macro_vg);
                }
                val["final_netlist_sha256"] = sha256_file(mapped_vg);
                return val;
            });
        }

        if (stop_after == "bitstream") {
            JsonValue physical = run_stage("pnr_bitstream", [&]() {
                int eff_timeout = static_cast<int>(budget.timeout(static_cast<double>(pnr_timeout), 5.0));
                if (eff_timeout < 1) throw TimeoutError("Insufficient total flow budget to launch native P&R");
                result["effective_pnr_timeout"] = eff_timeout;
                save();

                std::string cfg_dev_p = PINNED_DEVICE;
                std::string cfg_part  = PINNED_PART;
                if (frontend.contains("target_device") && frontend["target_device"].is_obj()) {
                    const auto& td = frontend["target_device"];
                    if (td.contains("dev_p") && td["dev_p"].is_str() && !td["dev_p"].as_str().empty()) {
                        cfg_dev_p = td["dev_p"].as_str();
                    }
                    if (td.contains("part_number") && td["part_number"].is_str() && !td["part_number"].as_str().empty()) {
                        cfg_part = td["part_number"].as_str();
                    }
                }
                auto [prec, pchk] = run_ownpr_full(
                    path_join(out, "05_netlist\\mapped.vg"),
                    path_join(out, "05_netlist\\final.json"),
                    resolve_flow_input_path(config, "sdc", cfg_parent),
                    resolve_flow_input_path(config, "cst", cfg_parent),
                    resolve_flow_input_path(config, "device_config", cfg_parent),
                    resolve_flow_input_path(config, "physical_profile", cfg_parent),
                    path_join(out, "06_pnr"),
                    eff_timeout,
                    cfg_dev_p,
                    cfg_part
                );
                if (prec["vendor_placement_executed"].as_bool(true) || prec["vendor_routing_executed"].as_bool(true)) {
                    throw std::runtime_error("Custom ownpr backend executed vendor P&R");
                }
                JsonValue ret = JsonValue::object();
                ret["native"] = std::move(prec);
                ret["timing"] = std::move(pchk);
                return ret;
            });
            result["timing_closed"] = physical["timing"]["timing_closed"];
        }

        double tot_stage_sec = 0.0;
        for (const auto& st : result["stages"].as_arr()) tot_stage_sec += st["wall_seconds"].as_f64();
        result["status"] = "complete";
        result["stop_after"] = stop_after;
        result["wall_seconds"] = perf_counter() - entry_t;
        result["total_stage_wall_seconds"] = tot_stage_sec;
        result["total_budget_used_seconds"] = budget.used();
        save();
        work_alias.sync_back();
        set_active_budget(nullptr);

        JsonValue summary = result;
        summary.as_obj().erase("stages");
        std::string js = dump_json(summary, 2, false) + "\n";
        std::fwrite(js.data(), 1, js.size(), stdout);
        std::fflush(stdout);
        return 0;
    } catch (const std::exception& ex) {
        result["status"] = "failed";
        result["error"] = ex.what();
        result["wall_seconds"] = perf_counter() - entry_t;
        result["total_budget_used_seconds"] = budget.used();
        save();
        work_alias.sync_back();
        set_active_budget(nullptr);
        std::fprintf(stderr, "[nextgowin] ERROR: %s\n", ex.what());
        std::fflush(stderr);
        return 1;
    }
}

} // namespace

} // namespace ng

extern "C" __declspec(dllexport) int ng_core_entry(int argc, wchar_t** argv) {
    try {
        ng::init_runtime_paths(ng::build_runtime_paths_from_env());
        std::vector<std::string> args;
        for (int i = 1; i < argc; ++i) {
            args.push_back(ng::wide_to_utf8(argv[i]));
        }
        if (args.size() >= 5 && args[0] == "--internal-gao-worker") {
            return ng::run_internal_gao_worker(args[1], args[2], args[3], args[4]);
        }
        if (args.size() >= 2 && args[0] == "--internal-frontend-worker") {
            return ng::run_internal_frontend_worker(args[1]);
        }
        if (args.size() >= 2 && args[0] == "--internal-pnr-worker") {
            return ng::run_internal_pnr_worker(args[1]);
        }
        for (size_t i = 0; i < args.size(); ++i) {
            const std::string& a = args[i];
            if (a == "--version" || a == "-v") {
                std::fprintf(stdout, "NextGowin v0.1.1 (x86-64 + SSE2/SSE3 Baseline)\n");
                std::fflush(stdout);
                return 0;
            }
            if (a == "--verify-fs") {
                if (i + 1 >= args.size()) {
                    throw std::runtime_error("--verify-fs requires a path to a .fs bitstream file");
                }
                ng::JsonValue aud = ng::audit_fs_bitstream_integrity(args[i + 1]);
                std::string js = ng::dump_json(aud, 2, false) + "\n";
                std::fwrite(js.data(), 1, js.size(), stdout);
                std::fflush(stdout);
                return (aud["status"].as_str() == "frame_integrity_verified") ? 0 : 1;
            }
            if (a == "--self-test") return ng::run_native_self_test();
            if (a == "--ownpr") return ng::run_standalone_ownpr_cli(args);
        }
        return ng::run_full_flow_cli(args);
    } catch (const std::exception& ex) {
        std::fprintf(stderr, "[nextgowin-core] ERROR: %s\n", ex.what());
        std::fflush(stderr);
        return 1;
    }
}
