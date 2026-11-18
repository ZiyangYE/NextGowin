#include "ng_pipeline.hpp"

#include <cmath>
#include <cstring>
#include <deque>
#include <map>
#include <set>
#include <unordered_map>
#include <unordered_set>

namespace ng {

// ============================================================================
// Part A: Bitstream Frame Integrity, gowin_report, and project_timing Audits
// ============================================================================
JsonValue audit_fs_bitstream_integrity(std::string_view fs_path) {
    std::string p = resolve_path(fs_path);
    std::string raw = read_file_text(p);
    std::vector<std::string_view> payload;
    payload.reserve(22000);

    size_t pos = 0;
    while (pos < raw.size()) {
        size_t nl = raw.find('\n', pos);
        std::string_view ln = (nl == std::string::npos)
            ? std::string_view(raw).substr(pos)
            : std::string_view(raw).substr(pos, nl - pos);
        if (!ln.empty() && ln.back() == '\r') ln.remove_suffix(1);
        if (!ln.empty() && !(ln.size() >= 2 && ln[0] == '/' && ln[1] == '/')) {
            for (char c : ln) {
                if (c != '0' && c != '1') throw std::runtime_error("Unknown native ASCII payload in .fs");
            }
            payload.push_back(ln);
        }
        if (nl == std::string::npos) break;
        pos = nl + 1;
    }

    constexpr size_t FRAME_BITS = 198 * 8;
    constexpr int32_t FRAME_COUNT = 21872;
    int first_frame = -1;
    int count_frames = 0;
    for (size_t i = 0; i < payload.size(); ++i) {
        if (payload[i].size() == FRAME_BITS) {
            if (first_frame < 0) first_frame = static_cast<int>(i);
            else if (static_cast<int>(i) != first_frame + count_frames) {
                throw std::runtime_error("Non-contiguous frame sequence in .fs");
            }
            ++count_frames;
        }
    }
    if (count_frames != FRAME_COUNT || first_frame < 0) {
        throw std::runtime_error("Unrecognized native frame sequence count in .fs");
    }

    std::string hdr_concat;
    for (int i = 0; i < first_frame; ++i) hdr_concat.append(payload[static_cast<size_t>(i)]);
    std::string header_sha = sha256_str(hdr_concat);

    auto crc16_step_byte = [](uint16_t st, uint8_t b) -> uint16_t {
        uint16_t cur = static_cast<uint16_t>((st ^ b) & 0xFFU);
        for (int bit = 0; bit < 8; ++bit) {
            cur = (cur & 1U) ? static_cast<uint16_t>((cur >> 1) ^ 0xA001U) : static_cast<uint16_t>(cur >> 1);
        }
        return static_cast<uint16_t>((st >> 8) ^ cur);
    };

    // Validate GW5AST-138 header packet stream (sync 0xA5C3 -> IDCODE 0x06 -> control/CSR packets -> 0x3B805570)
    // and derive the exact hardware initial CRC-16 register seed `st0` entering Frame 0.
    int sync_idx = -1;
    for (int i = 0; i < first_frame; ++i) {
        if (payload[static_cast<size_t>(i)] == "1010010111000011") {
            sync_idx = i;
            break;
        }
    }
    bool hdr_struct_valid = (sync_idx >= 0 && sync_idx + 2 <= first_frame &&
        payload[static_cast<size_t>(sync_idx + 1)] == "0000011000000000000000000000000000000000000000010000100000011011" &&
        payload[static_cast<size_t>(first_frame - 1)] == "00111011100000000101010101110000");
    uint16_t derived_st0 = 0;
    if (hdr_struct_valid) {
        for (int k = sync_idx + 1; k < first_frame; ++k) {
            std::string_view pl = payload[static_cast<size_t>(k)];
            if (pl.size() < 32 || (pl.size() % 8) != 0) {
                hdr_struct_valid = false;
                break;
            }
            size_t n_bytes = pl.size() / 8;
            std::vector<uint8_t> hb(n_bytes, 0);
            for (size_t bi = 0; bi < n_bytes; ++bi) {
                uint8_t v = 0;
                for (int bit = 0; bit < 8; ++bit) {
                    if (pl[bi * 8 + static_cast<size_t>(bit)] == '1') v |= static_cast<uint8_t>(0x80U >> bit);
                }
                hb[bi] = v;
            }
            uint8_t op = hb[0];
            if (op == 0xD2U && n_bytes == 8) {
                continue; // Status-register mask packet does not advance hardware CRC-16
            } else if (op == 0x36U && n_bytes == 12) {
                for (size_t bi = 4; bi < 12; ++bi) {
                    if (hb[bi] != 0xFFU) { hdr_struct_valid = false; break; }
                }
                if (!hdr_struct_valid) break;
                for (size_t bi = 0; bi < 4; ++bi) derived_st0 = crc16_step_byte(derived_st0, hb[bi]);
            } else if ((op == 0x0BU || op == 0x12U || op == 0x3BU) && n_bytes == 4) {
                for (uint8_t b : hb) derived_st0 = crc16_step_byte(derived_st0, b);
            } else if ((op == 0x06U || op == 0x10U || op == 0x39U || op == 0x51U || op == 0x62U) && n_bytes == 8) {
                for (uint8_t b : hb) derived_st0 = crc16_step_byte(derived_st0, b);
            } else if (op == 0x3EU && (n_bytes == 8 || (n_bytes == 16 && hb[8] == 0x39U))) {
                for (uint8_t b : hb) derived_st0 = crc16_step_byte(derived_st0, b);
            } else {
                hdr_struct_valid = false;
                break;
            }
        }
    }

    if (!hdr_struct_valid) {
        throw std::runtime_error("Uncalibrated or malformed native bitstream header");
    }

    std::string blob;
    blob.reserve(static_cast<size_t>(FRAME_COUNT) * FRAME_BITS);
    for (int i = 0; i < FRAME_COUNT; ++i) {
        blob.append(payload[static_cast<size_t>(first_frame + i)]);
    }

    std::vector<int32_t> bad_crc(FRAME_COUNT, 0), bad_tail(FRAME_COUNT, 0);
    int32_t ncrc = 0, ntail = 0;
    int32_t rc = SynAccelLib::instance().audit_fs_frames(
        blob.data(), FRAME_COUNT, bad_crc.data(), FRAME_COUNT, &ncrc, bad_tail.data(), &ntail
    );
    if (rc != 0) throw std::runtime_error("accel_audit_fs_frames failed");

    // Verify Frame 0 against the exact header-derived CRC-16 seed `derived_st0` whenever it differs
    // from the default 0x8373U seed assumed by accel_audit_fs_frames (Frames 1..21871 always start from 0x8F01U).
    if (hdr_struct_valid && derived_st0 != 0x8373U) {
        uint8_t pk0[198]{};
        for (int bi = 0; bi < 198; ++bi) {
            uint8_t v = 0;
            for (int k = 0; k < 8; ++k) {
                if (blob[static_cast<size_t>(bi * 8 + k)] == '1') v |= static_cast<uint8_t>(0x80U >> k);
            }
            pk0[bi] = v;
        }
        uint16_t st0 = derived_st0;
        for (int col = 0; col < 190; ++col) {
            st0 = crc16_step_byte(st0, pk0[col]);
        }
        bool f0_ok = (pk0[190] == static_cast<uint8_t>(st0 & 0xFFU)) &&
                     (pk0[191] == static_cast<uint8_t>(st0 >> 8));
        if (f0_ok) {
            int32_t w = 0;
            for (int32_t i = 0; i < ncrc; ++i) {
                if (bad_crc[static_cast<size_t>(i)] != 0) bad_crc[static_cast<size_t>(w++)] = bad_crc[static_cast<size_t>(i)];
            }
            ncrc = w;
        } else if (ncrc == 0 || bad_crc[0] != 0) {
            bad_crc.insert(bad_crc.begin(), 0);
            ++ncrc;
        }
    }

    JsonValue crc_arr = JsonValue::array(), tail_arr = JsonValue::array();
    for (int32_t i = 0; i < ncrc; ++i) crc_arr.push_back(bad_crc[static_cast<size_t>(i)]);
    for (int32_t i = 0; i < ntail; ++i) tail_arr.push_back(bad_tail[static_cast<size_t>(i)]);

    std::string gowin_dll_p = path_join(runtime_paths().gowin_sdk, "bin\\gowin.dll");
    std::string dll_sha = file_exists(gowin_dll_p) ? sha256_file(gowin_dll_p) : std::string(EXPECTED_GOWIN_DLL_SHA);

    JsonValue out = JsonValue::object();
    out["path"] = p;
    out["file_sha256"] = sha256_file(p);
    out["header_sha256"] = header_sha;
    out["header_frame0_crc16_seed"] = static_cast<int64_t>(derived_st0);
    out["frames"] = FRAME_COUNT;
    out["crc_mismatch_frames"] = std::move(crc_arr);
    out["tail_mismatch_frames"] = std::move(tail_arr);
    out["status"] = (ncrc == 0 && ntail == 0) ? "frame_integrity_verified" : "failed";
    out["dll_sha256"] = dll_sha;
    out["full_bitstream_verified"] = false;
    out["scope"] = "Calibrated header and frame integrity only; footer/checksum commands, configuration semantics and hardware remain unverified.";
    return out;
}

namespace {

std::string extract_tr_section(std::string_view text, std::string_view title, bool general_numbered_end) {
    auto lines = split_lines(text);
    size_t start_line = lines.size();
    for (size_t i = 0; i < lines.size(); ++i) {
        if (rtrim(lines[i]) == title) {
            start_line = i + 1;
            break;
        }
    }
    if (start_line >= lines.size()) return "";
    std::vector<std::string> sec;
    for (size_t i = start_line; i < lines.size(); ++i) {
        const std::string& ln = lines[i];
        if (general_numbered_end) {
            // `^\d+(?:\.\d+)*\.?\s+[A-Za-z]`
            size_t j = 0;
            if (j < ln.size() && std::isdigit(static_cast<unsigned char>(ln[j]))) {
                while (j < ln.size() && (std::isdigit(static_cast<unsigned char>(ln[j])) || ln[j] == '.')) ++j;
                size_t k = j;
                while (k < ln.size() && std::isspace(static_cast<unsigned char>(ln[k]))) ++k;
                if (k > j && k < ln.size() && std::isalpha(static_cast<unsigned char>(ln[k]))) {
                    break;
                }
            }
        } else {
            // `^3\.\d+(?:\.\d+)?\s`
            if (starts_with(ln, "3.") && ln.size() > 3 && std::isdigit(static_cast<unsigned char>(ln[2]))) {
                size_t j = 2;
                while (j < ln.size() && (std::isdigit(static_cast<unsigned char>(ln[j])) || ln[j] == '.')) ++j;
                if (j < ln.size() && std::isspace(static_cast<unsigned char>(ln[j]))) break;
            }
        }
        sec.push_back(ln);
    }
    return join_strings(sec, "\n");
}

std::string normalize_ws_single(std::string_view s) {
    return join_strings(split_ws(s), " ");
}

} // namespace

JsonValue parse_gowin_tr_summaries(std::string_view tr_text, double hold_uncertainty_ns) {
    OrderedMap<int64_t> endpoints;
    for (const auto& ln : split_lines(tr_text)) {
        if (starts_with(ln, "<Numbers of Setup Violated Endpoints>:")) {
            endpoints.set("Setup", std::stoll(trim(std::string_view(ln).substr(38))));
        } else if (starts_with(ln, "<Numbers of Hold Violated Endpoints>:")) {
            endpoints.set("Hold", std::stoll(trim(std::string_view(ln).substr(37))));
        }
    }
    if (!endpoints.contains("Setup") || !endpoints.contains("Hold")) {
        throw std::runtime_error("Missing setup/hold endpoint summary in .tr");
    }
    int64_t rec_viol = 0, rem_viol = 0, mpw_viol = 0;
    double worst_mpw_slack = 1e9;
    std::string tns_sec = extract_tr_section(tr_text, "2.4 Total Negative Slack Summary", true);
    for (const auto& ln : split_lines(tns_sec)) {
        auto flds = split_ws(ln);
        if (flds.size() == 4) {
            std::string an = to_lower_ascii(flds[1]);
            try {
                if (an == "recovery") rec_viol += std::stoll(flds[3]);
                else if (an == "removal") rem_viol += std::stoll(flds[3]);
            } catch (...) {}
        }
    }
    std::string mpw_sec = extract_tr_section(tr_text, "3.2 Minimum Pulse Width Table", true);
    for (const auto& ln : split_lines(mpw_sec)) {
        auto flds = split_ws(ln);
        if (flds.size() >= 7 && std::all_of(flds[0].begin(), flds[0].end(), [](unsigned char c) { return std::isdigit(c); })) {
            try {
                double slk = std::stod(flds[1]);
                if (slk < worst_mpw_slack) worst_mpw_slack = slk;
                if (slk < 0.0) ++mpw_viol;
            } catch (...) {}
        }
    }

    JsonValue ep_obj = JsonValue::object();
    ep_obj["Setup"] = endpoints.at("Setup");
    ep_obj["Hold"] = endpoints.at("Hold");
    ep_obj["Recovery"] = rec_viol;
    ep_obj["Removal"] = rem_viol;
    ep_obj["PulseWidth"] = mpw_viol;

    JsonValue result = JsonValue::object();
    result["violated_endpoints"] = std::move(ep_obj);
    bool core_closed = (endpoints.at("Setup") == 0 && endpoints.at("Hold") == 0 && rec_viol == 0 && rem_viol == 0);
    result["core_timing_closed"] = core_closed;
    result["timing_closed"] = (core_closed && mpw_viol == 0);
    if (worst_mpw_slack < 1e8) result["worst_mpw_slack_ns"] = worst_mpw_slack;

    // Populate general multi-clock summaries from sections 2.2 and 2.3 when present
    std::string clk_sec = extract_tr_section(tr_text, "2.2 Clock Summary", true);
    if (!clk_sec.empty()) {
        JsonValue clks_j = JsonValue::object();
        for (const auto& ln : split_lines(clk_sec)) {
            auto flds = split_ws(ln);
            if (flds.size() >= 7 && std::isdigit(static_cast<unsigned char>(flds[0][0]))) {
                try { clks_j[flds[1]] = std::stod(flds[3]); } catch (...) {}
            }
        }
        if (!clks_j.as_obj().empty()) result["clock_periods_ns"] = std::move(clks_j);
    }
    std::string freq_sec = extract_tr_section(tr_text, "2.3 Max Frequency Summary", true);
    if (!freq_sec.empty()) {
        JsonValue freq_j = JsonValue::object();
        for (const auto& ln : split_lines(freq_sec)) {
            auto flds = split_ws(ln);
            if (flds.size() >= 4 && std::isdigit(static_cast<unsigned char>(flds[0][0]))
                && ends_with(flds[2], "(MHz)") && ends_with(flds[3], "(MHz)")) {
                JsonValue fj = JsonValue::object();
                fj["constraint_mhz"] = std::stod(flds[2].substr(0, flds[2].size() - 5));
                fj["reported_fmax_mhz"] = std::stod(flds[3].substr(0, flds[3].size() - 5));
                freq_j[flds[1]] = std::move(fj);
            }
        }
        if (!freq_j.as_obj().empty()) result["frequency_reports"] = std::move(freq_j);
    }

    for (const auto& ln : split_lines(tr_text)) {
        auto ws = split_ws(ln);
        for (size_t i = 0; i + 2 < ws.size(); ++i) {
            if (ws[i] == "probe_clk" && ws[i + 1] == "100.000(MHz)" && ends_with(ws[i + 2], "(MHz)")) {
                result["reported_fmax_mhz"] = std::stod(ws[i + 2].substr(0, ws[i + 2].size() - 5));
            }
        }
    }

    if (hold_uncertainty_ns != 0.0) {
        std::string hold_sec = extract_tr_section(tr_text, "3.3.2 Hold Analysis Report", false);
        bool confirmed = false;
        for (const auto& ln : split_lines(hold_sec)) {
            auto ws = split_ws(ln);
            if (ws.size() >= 3 && ws[2] == "tUnc") {
                double v = std::stod(ws[1]);
                if (std::abs(v - hold_uncertainty_ns) < 0.000501) { confirmed = true; break; }
            }
        }
        result["report_confirms_hold_uncertainty"] = confirmed;
        if (!confirmed) throw std::runtime_error("Requested hold uncertainty not found in hold report");
    }

    std::string su_table = extract_tr_section(tr_text, "3.1.1 Setup Paths Table", false);
    double worst_req_per = -1e18;
    JsonValue worst_path = JsonValue(nullptr);
    for (const auto& ln : split_lines(su_table)) {
        auto flds = split_ws(ln);
        if (flds.size() < 9) continue;
        bool dig = !flds[0].empty();
        for (char c : flds[0]) if (!std::isdigit(static_cast<unsigned char>(c))) dig = false;
        if (!dig) continue;
        if (starts_with(flds[2], "launch") && ends_with(flds[2], "/Q") && starts_with(flds[3], "capture") && ends_with(flds[3], "/D")) {
            double slk = std::stod(flds[1]);
            double rel = std::stod(flds[6]);
            double req_p = rel - slk;
            if (req_p > worst_req_per) {
                worst_req_per = req_p;
                JsonValue wp = JsonValue::object();
                wp["from_node"] = flds[2];
                wp["to_node"] = flds[3];
                wp["slack_ns"] = slk;
                wp["required_period_ns"] = req_p;
                wp["data_delay_ns"] = std::stod(flds[8]);
                worst_path = std::move(wp);
            }
        }
    }
    if (!worst_path.is_null()) {
        result["core_setup_worst_reported_path"] = std::move(worst_path);
        result["core_setup_scope"] = "Worst register-to-register path appearing in the sorted native setup table; I/O paths excluded";
    }
    return result;
}

JsonValue audit_project_timing(std::string_view pnr_dir, const JsonValue& profile) {
    std::string dir = resolve_path(pnr_dir);
    JsonValue manifest = parse_json_file(path_join(dir, "manifest.json"));
    if (manifest["returncode"].as_i64(-1) != 0) {
        throw std::runtime_error("P&R is not terminal with success return code");
    }
    std::string vg_sha = sha256_file(path_join(dir, "mapped.vg"));
    if (manifest["netlist_sha256_before"].as_str() != vg_sha || manifest["netlist_sha256_after"].as_str() != vg_sha) {
        throw std::runtime_error("P&R input changed or hash evidence incomplete");
    }
    if (sha256_file(path_join(dir, "project.sdc")) != manifest["sdc_sha256"].as_str()) {
        throw std::runtime_error("SDC constraint snapshot changed");
    }
    if (const JsonValue* cs = manifest.get("cst_sha256"); cs && !cs->is_null()) {
        if (sha256_file(path_join(dir, "project.cst")) != cs->as_str()) {
            throw std::runtime_error("CST constraint snapshot changed");
        }
    }

    std::string log = read_file_text(path_join(dir, "pnr.log"));
    std::string routing = read_file_text(path_join(dir, "mapped.rpt.txt"));
    if (log.find("Placement and routing completed") == std::string::npos || routing.find("Generate output files:") == std::string::npos) {
        throw std::runtime_error("Native implementation completion evidence missing");
    }
    std::string combined_up = to_upper_ascii(log + "\n" + routing);
    if (combined_up.find("ERROR(") != std::string::npos || combined_up.find("ERROR (") != std::string::npos || combined_up.find("PR0004") != std::string::npos) {
        throw std::runtime_error("Native implementation reported an error");
    }
    if (log.find("CT1135") != std::string::npos) {
        throw std::runtime_error("A physical constraint object was not found");
    }

    std::vector<std::string> req_files = {"mapped.vg","mapped.vo","mapped.tr","mapped.rpt.txt","project.sdc","cmd.do"};
    if (const JsonValue* cs = manifest.get("cst_sha256"); cs && !cs->is_null()) req_files.push_back("project.cst");
    std::string cmd_do_txt = read_file_text(path_join(dir, "cmd.do"));
    bool bitstream_requested = false;
    for (const auto& ln : split_lines(cmd_do_txt)) if (trim(ln) == "-bit") bitstream_requested = true;
    if (bitstream_requested) {
        if (const JsonValue* gid = manifest.get("gao_id"); gid && !gid->is_null() && !gid->as_str().empty()) {
            bool any_ao = false;
            for (const auto& de : list_dir(dir)) {
                if (de.is_file && starts_with(de.name, "ao_") && ends_with(de.name, ".fs")) {
                    req_files.push_back(de.name);
                    any_ao = true;
                }
            }
            if (!any_ao) throw std::runtime_error("Requested GAO configuration output missing");
        } else {
            req_files.push_back("mapped.fs");
        }
    }
    for (const auto& f : req_files) {
        if (!file_exists(path_join(dir, f))) throw std::runtime_error("Missing native output: " + f);
    }
    uint64_t src_mtime = file_mtime_100ns(path_join(dir, "mapped.vg"));
    for (const char* out_f : {"mapped.vo", "mapped.tr", "mapped.rpt.txt"}) {
        if (file_mtime_100ns(path_join(dir, out_f)) < src_mtime) {
            throw std::runtime_error(std::string("Stale native output: ") + out_f);
        }
    }

    std::string tr_text = read_file_text(path_join(dir, "mapped.tr"));
    std::string sdc_text = read_file_text(path_join(dir, "project.sdc"));

    OrderedMap<std::string> metadata;
    OrderedMap<int64_t> violated;
    for (const auto& ln : split_lines(tr_text)) {
        if (starts_with(ln, "<") && ln.find(">:") != std::string::npos) {
            size_t gt = ln.find(">:");
            std::string k = ln.substr(1, gt - 1);
            std::string v = trim(std::string_view(ln).substr(gt + 2));
            metadata.set(k, v);
            if (k == "Numbers of Setup Violated Endpoints") violated.set("Setup", std::stoll(v));
            else if (k == "Numbers of Hold Violated Endpoints") violated.set("Hold", std::stoll(v));
        }
    }
    if (metadata.value_or("Part Number", "") != profile["part"].as_str()) {
        throw std::runtime_error("Wrong/missing native part in .tr");
    }
    if (!violated.contains("Setup") || !violated.contains("Hold")) {
        throw std::runtime_error("Incomplete violated-endpoint summary");
    }

    auto numbered_rows = [&](std::string_view title) {
        std::string sec = extract_tr_section(tr_text, title, true);
        if (sec.empty()) throw std::runtime_error("Missing native section " + std::string(title));
        std::vector<std::vector<std::string>> rows;
        for (const auto& ln : split_lines(sec)) {
            auto flds = split_ws(ln);
            if (flds.empty()) continue;
            bool dig = true;
            for (char c : flds[0]) if (!std::isdigit(static_cast<unsigned char>(c))) dig = false;
            if (dig) rows.push_back(std::move(flds));
        }
        return rows;
    };

    OrderedMap<double> clocks;
    for (const auto& flds : numbered_rows("2.2 Clock Summary")) {
        if (flds.size() < 7) throw std::runtime_error("Incomplete clock summary row");
        if (clocks.contains(flds[1])) throw std::runtime_error("Duplicate native clock: " + flds[1]);
        clocks.set(flds[1], std::stod(flds[3]));
    }
    const auto& exp_clks = profile["clock_periods_ns"].as_obj();
    if (clocks.size() != exp_clks.size()) {
        throw std::runtime_error("Physical clock set differs from expected profile");
    }
    for (const auto& [ck, cv] : exp_clks) {
        const double* actual_p = clocks.find_ptr(ck);
        if (!actual_p || std::abs(*actual_p - cv.as_f64()) > 0.000501) {
            throw std::runtime_error("Physical clock period differs from expected profile: " + ck);
        }
    }

    JsonValue freq = JsonValue::object();
    for (const auto& flds : numbered_rows("2.3 Max Frequency Summary")) {
        if (flds.size() >= 4 && ends_with(flds[2], "(MHz)") && ends_with(flds[3], "(MHz)")) {
            JsonValue fj = JsonValue::object();
            fj["constraint_mhz"] = std::stod(flds[2].substr(0, flds[2].size() - 5));
            fj["reported_fmax_mhz"] = std::stod(flds[3].substr(0, flds[3].size() - 5));
            freq[flds[1]] = std::move(fj);
        }
    }

    JsonValue tns = JsonValue::array();
    std::set<std::pair<std::string, std::string>> seen_tns_keys;
    for (const auto& ln : split_lines(extract_tr_section(tr_text, "2.4 Total Negative Slack Summary", true))) {
        auto flds = split_ws(ln);
        if (flds.size() == 4) {
            std::string an = to_lower_ascii(flds[1]);
            if (an == "setup" || an == "hold" || an == "recovery" || an == "removal") {
                seen_tns_keys.emplace(flds[0], an);
                JsonValue tr = JsonValue::object();
                tr["clock"] = flds[0];
                tr["analysis"] = an;
                tr["tns_ns"] = std::stod(flds[2]);
                tr["violated_endpoints"] = static_cast<int64_t>(std::stoll(flds[3]));
                tns.push_back(std::move(tr));
            }
        }
    }
    for (const auto& [ck, _] : exp_clks) {
        if (!seen_tns_keys.count({ck, "setup"}) || !seen_tns_keys.count({ck, "hold"})) {
            throw std::runtime_error("Missing per-clock setup/hold TNS for " + ck);
        }
    }

    JsonValue paths = JsonValue::array();
    OrderedMap<double> worst_by_clk;
    const std::pair<int, const char*> path_secs[4] = {{1, "setup"}, {2, "hold"}, {3, "recovery"}, {4, "removal"}};
    for (auto [num, an] : path_secs) {
        std::string title = "3.1." + std::to_string(num) + " " + std::string(1, static_cast<char>(std::toupper(an[0]))) + std::string(an + 1) + " Paths Table";
        for (const auto& flds : numbered_rows(title)) {
            if (flds.size() != 9) throw std::runtime_error("Unexpected detailed path format");
            double slk = std::stod(flds[1]);
            std::string wkey = flds[5] + "/" + an;
            if (!worst_by_clk.contains(wkey) || slk < worst_by_clk.at(wkey)) {
                worst_by_clk.set(wkey, slk);
            }
            JsonValue pr = JsonValue::object();
            pr["analysis"] = an;
            pr["slack_ns"] = slk;
            pr["source"] = flds[2];
            pr["destination"] = flds[3];
            pr["launch_clock"] = flds[4];
            pr["capture_clock"] = flds[5];
            pr["relationship_ns"] = std::stod(flds[6]);
            pr["clock_skew_ns"] = std::stod(flds[7]);
            pr["data_delay_ns"] = std::stod(flds[8]);
            paths.push_back(std::move(pr));
        }
    }

    JsonValue pulse = JsonValue::array();
    for (const auto& flds : numbered_rows("3.2 Minimum Pulse Width Table")) {
        if (flds.size() < 7) throw std::runtime_error("Unexpected pulse-width row");
        JsonValue pw = JsonValue::object();
        pw["slack_ns"] = std::stod(flds[1]);
        pw["actual_ns"] = std::stod(flds[2]);
        pw["required_ns"] = std::stod(flds[3]);
        pw["clock"] = flds[flds.size() - 2];
        pw["object"] = flds.back();
        pulse.push_back(std::move(pw));
    }

    // Active SDC commands check
    std::string joined_sdc;
    for (size_t i = 0; i < sdc_text.size(); ++i) {
        if (sdc_text[i] == '\\' && i + 1 < sdc_text.size() && sdc_text[i + 1] == '\n') {
            joined_sdc.push_back(' ');
            ++i;
        } else {
            joined_sdc.push_back(sdc_text[i]);
        }
    }
    static const std::unordered_set<std::string> allowed_sdc = {
        "create_clock","create_generated_clock","set_clock_groups","set_false_path",
        "set_input_delay","set_output_delay","set_clock_uncertainty","set_max_delay","set_min_delay","set_multicycle_path"
    };
    std::vector<std::string> req_sdc_cmds;
    for (const auto& raw_l : split_lines(joined_sdc)) {
        std::string l = trim(raw_l);
        if (l.empty() || l[0] == '#') continue;
        auto ws = split_ws(l);
        if (starts_with(ws[0], "report_")) continue;
        if (!allowed_sdc.count(ws[0])) throw std::runtime_error("Unsupported SDC audit command: " + ws[0]);
        req_sdc_cmds.push_back(normalize_ws_single(l));
    }
    if (req_sdc_cmds.empty()) throw std::runtime_error("Empty audited timing constraint set");

    std::unordered_set<std::string> active_set;
    JsonValue active_cmds = JsonValue::array();
    for (const auto& ln : split_lines(extract_tr_section(tr_text, "3.8 Timing Constraints Report", true))) {
        std::string lt = ltrim(ln);
        if (starts_with(lt, "TC_")) {
            size_t act_p = lt.find("Actived");
            if (act_p != std::string::npos) {
                std::string norm_c = normalize_ws_single(std::string_view(lt).substr(act_p + 7));
                active_set.insert(norm_c);
                active_cmds.push_back(norm_c);
            }
        }
    }
    for (const auto& rc : req_sdc_cmds) {
        if (!active_set.count(rc)) {
            throw std::runtime_error("Timing constraint not active: " + rc);
        }
    }

    JsonValue failures = JsonValue::array();
    for (const auto& [ak, av] : violated) {
        if (av != 0) {
            JsonValue f = JsonValue::object();
            f["kind"] = "endpoint_summary"; f["analysis"] = ak; f["violated_endpoints"] = av;
            failures.push_back(std::move(f));
        }
    }
    for (const auto& r : tns.as_arr()) {
        if (r["tns_ns"].as_f64() < 0.0 || r["violated_endpoints"].as_i64() != 0) {
            JsonValue f = r;
            f["kind"] = "per_clock_tns";
            failures.push_back(std::move(f));
        }
    }
    for (const auto& r : paths.as_arr()) {
        if (r["slack_ns"].as_f64() < 0.0) {
            JsonValue f = r;
            f["kind"] = "reported_path";
            failures.push_back(std::move(f));
        }
    }
    for (const auto& r : pulse.as_arr()) {
        if (r["slack_ns"].as_f64() < 0.0) {
            JsonValue f = r;
            f["kind"] = "pulse_width";
            failures.push_back(std::move(f));
        }
    }

    // Parse implementation resource rows from mapped.rpt.txt
    OrderedMap<std::string> rpt_rows;
    for (const auto& ln : split_lines(routing)) {
        auto flds = split_char(ln, '|');
        if (flds.size() == 2 || flds.size() == 3) {
            std::string f0 = trim(flds[0]), f1 = trim(flds[1]);
            if (!f0.empty() && f0 != "Resources") rpt_rows.set(f0, f1);
        }
    }
    JsonValue impl = JsonValue::object();
    JsonValue nr_obj = JsonValue::object();
    for (const auto& [rk, rv] : rpt_rows) {
        nr_obj[rk] = rv;
        std::string rkl = to_lower_ascii(rk);
        if ((rkl.find("unrout") != std::string::npos || rkl.find("not routed") != std::string::npos) && rv != "0") {
            throw std::runtime_error("Unrouted native resources: " + rk + "=" + rv);
        }
    }
    impl["native_resource_rows"] = std::move(nr_obj);
    if (const std::string* lg = rpt_rows.find_ptr("--LUT,ALU,ROM16")) {
        auto ws = split_ws(*lg);
        for (size_t i = 0; i + 1 < ws.size(); ++i) {
            if (ws[i + 1] == "LUT" || ws[i + 1] == "ALU" || ws[i + 1] == "ROM16") {
                bool d = !ws[i].empty();
                for (char c : ws[i]) if (!std::isdigit(static_cast<unsigned char>(c))) d = false;
                if (d) impl[to_lower_ascii(ws[i + 1])] = static_cast<int64_t>(std::stoll(ws[i]));
            }
        }
    }
    if (const std::string* rg = rpt_rows.find_ptr("Register")) {
        auto sp = split_char(*rg, '/');
        if (!sp.empty()) impl["registers"] = static_cast<int64_t>(std::stoll(trim(sp[0])));
    }
    if (const std::string* bs = rpt_rows.find_ptr("BSRAM")) {
        auto sp = split_char(*bs, '/');
        if (sp.size() == 2 && !sp[0].empty() && std::isdigit(static_cast<unsigned char>(sp[0][0]))) {
            impl["bsram"] = static_cast<int64_t>(std::stoll(trim(sp[0])));
        }
    }
    if (const JsonValue* mbs = profile.get("max_bsram"); mbs && mbs->is_int()) {
        if (!impl.get("bsram") || impl["bsram"].as_i64() > mbs->as_i64()) {
            throw std::runtime_error("Physical BSRAM budget missing/exceeded");
        }
    }

    JsonValue clks_j = JsonValue::object();
    for (const auto& [ck, cv] : clocks) clks_j[ck] = cv;
    JsonValue viol_j = JsonValue::object();
    for (const auto& [vk, vv] : violated) viol_j[vk] = vv;
    JsonValue worst_j = JsonValue::object();
    for (const auto& [wk, wv] : worst_by_clk) worst_j[wk] = wv;
    JsonValue dm_j = JsonValue::object();
    dm_j["setup"] = metadata.value_or("Setup Delay Model", "");
    dm_j["hold"] = metadata.value_or("Hold Delay Model", "");
    JsonValue arts_sha = JsonValue::object();
    for (const auto& f : req_files) arts_sha[f] = sha256_file(path_join(dir, f));
    JsonValue impl_opts = JsonValue::array();
    for (const auto& ln : split_lines(cmd_do_txt)) {
        std::string t = trim(ln);
        if (t.empty()) continue;
        auto ws = split_ws(t);
        if (ws[0] != "-d" && ws[0] != "-sdc" && ws[0] != "-cst" && ws[0] != "-o") impl_opts.push_back(t);
    }

    JsonValue out = JsonValue::object();
    out["part"] = profile["part"];
    out["native_tool_version"] = metadata.value_or("Tool Version", "");
    out["delay_models"] = std::move(dm_j);
    out["clock_periods_ns"] = std::move(clks_j);
    out["frequency_reports"] = std::move(freq);
    out["violated_endpoints"] = std::move(viol_j);
    out["per_clock_tns"] = std::move(tns);
    out["active_sdc_commands"] = std::move(active_cmds);
    out["worst_reported_slack_by_capture_clock"] = std::move(worst_j);
    out["reported_paths"] = std::move(paths);
    out["pulse_width"] = std::move(pulse);
    out["timing_closed"] = failures.as_arr().empty();
    out["violations"] = std::move(failures);
    out["scope"] = "Native endpoint/TNS totals plus reported setup/hold/recovery/removal/pulse paths. Positive slack values cover reported paths only; no hardware acceptance.";
    out["pnr_artifacts_valid"] = true;
    out["source_manifest_sha256"] = sha256_file(path_join(dir, "manifest.json"));
    out["artifacts_sha256"] = std::move(arts_sha);
    out["runner_recorded_status"] = manifest["status"];
    out["bitstream_requested"] = bitstream_requested;
    out["dll_sha256"] = manifest["dll_sha256"];
    out["implementation_options"] = std::move(impl_opts);
    out["implementation"] = std::move(impl);
    return out;
}

// ============================================================================
// Part B: Native FSE Parser, CST/SDC/Defparam Parsers & Pin Exemption Rules
// ============================================================================
namespace {

std::vector<std::vector<int32_t>> load_fse_grid_checked(std::string_view sdk_ide_dir, std::string_view dev_name = "GW5AST-138C") {
    std::string fse_p = path_join(sdk_ide_dir, "share\\device\\" + std::string(dev_name) + "\\" + std::string(dev_name) + ".fse");
    auto data = read_file_bytes(fse_p);
    std::string_view exp_sha = (dev_name == "GW5AST-138B") ? EXPECTED_138B_FSE_SHA : EXPECTED_FSE_SHA;
    if (sha256_bytes(data.data(), data.size()) != exp_sha && !allow_unsupported_gowin_sdk()) {
        throw std::runtime_error("Native FSE version changed; re-audit format");
    }
    size_t pos = 0;
    auto req = [&](size_t n) {
        if (pos + n > data.size()) throw std::runtime_error("Truncated/invalid native FSE table");
    };
    auto u32 = [&]() -> uint32_t {
        req(4);
        uint32_t v = 0;
        std::memcpy(&v, data.data() + pos, 4);
        pos += 4;
        return v;
    };

    if (u32() != 0x124f4d7u) throw std::runtime_error("Unexpected FSE header magic");
    bool first = true;
    std::unordered_set<uint32_t> block_types;
    size_t grid_offset = 0;
    uint32_t grid_rows = 0, grid_cols = 0;

    auto cols_for_kind = [](uint32_t kind) -> int {
        if (kind == 1) return 512;
        if (kind == 2 || kind == 0x26 || kind == 0x30 || kind == 0x5a || kind == 0x5b) return 9;
        if (kind == 3) return 3;
        if (kind == 4) return 1;
        if (kind == 6 || kind == 0x45) return 15;
        if (kind == 0x12 || kind == 0x13 || kind == 0x3a) return 17;
        if (kind == 0x35 || kind == 0x36) return 14;
        if (kind == 0x17 || kind == 0x18 || kind == 0x25 || (kind >= 0x28 && kind <= 0x2f)) return 28;
        if ((kind >= 7 && kind <= 0x10) || kind == 0x27 || kind == 0x31 || kind == 0x34 || kind == 0x37
            || kind == 0x39 || kind == 0x3b || kind == 0x3e || kind == 0x3f || kind == 0x41 || kind == 0x43
            || kind == 0x46 || kind == 0x48 || kind == 0x4a || kind == 0x4c || kind == 0x4e || kind == 0x51
            || kind == 0x53 || kind == 0x55 || kind == 0x57 || kind == 0x5c || kind == 0x86 || kind == 0x87 || kind == 0x9a) {
            return 3;
        }
        if (kind == 0x8b) return 10;
        if (kind == 5 || kind == 0x11 || kind == 0x14 || kind == 0x15 || kind == 0x16
            || (kind >= 0x19 && kind <= 0x24) || kind == 0x32 || kind == 0x33 || kind == 0x38 || kind == 0x3c
            || kind == 0x40 || kind == 0x42 || kind == 0x44 || kind == 0x47 || kind == 0x49 || kind == 0x4b
            || kind == 0x4d || kind == 0x4f || kind == 0x50 || kind == 0x52 || kind == 0x54 || kind == 0x56
            || kind == 0x58 || kind == 0x59 || (kind >= 0x5d && kind <= 0x85)
            || kind == 0x88 || kind == 0x89 || kind == 0x8a || (kind >= 0x8c && kind <= 0x99)
            || kind == 0x9b || kind == 0x9c || kind == 0x9e) {
            return 14;
        }
        return -1;
    };

    for (;;) {
        uint32_t typ = u32();
        if (typ == 0x9a1d85u) break;
        bool is_hdr = first;
        first = false;
        if (!is_hdr) block_types.insert(typ);
        (void)u32(); // h
        (void)u32(); // w
        uint32_t count = u32();
        if (count > 256) throw std::runtime_error("Invalid FSE block table count");
        for (uint32_t ti = 0; ti < count; ++ti) {
            uint32_t kind = u32();
            uint32_t rows = u32();
            uint32_t cols = 0;
            size_t unit = 2;
            if (kind == 61) {
                cols = u32();
                unit = 4;
                if (is_hdr) {
                    grid_offset = pos;
                    grid_rows = rows;
                    grid_cols = cols;
                }
            } else {
                int c = cols_for_kind(kind);
                if (c < 0) throw std::runtime_error("Unknown native FSE table kind");
                cols = static_cast<uint32_t>(c);
            }
            size_t sz = static_cast<size_t>(rows) * static_cast<size_t>(cols) * unit;
            req(sz);
            pos += sz;
        }
    }
    if (pos != data.size()) throw std::runtime_error("Unconsumed FSE bytes");
    if (grid_rows == 0 || grid_cols == 0) throw std::runtime_error("Missing FSE grid table");

    std::vector<std::vector<int32_t>> grid(grid_rows, std::vector<int32_t>(grid_cols, 0));
    for (uint32_t r = 0; r < grid_rows; ++r) {
        for (uint32_t c = 0; c < grid_cols; ++c) {
            int32_t tv = 0;
            std::memcpy(&tv, data.data() + grid_offset + (static_cast<size_t>(r) * grid_cols + c) * 4, 4);
            if (!block_types.count(static_cast<uint32_t>(tv))) {
                throw std::runtime_error("Missing tile type in FSE grid");
            }
            grid[r][c] = tv;
        }
    }
    return grid;
}

std::pair<int16_t, int16_t> load_vss_vcc_wire_ids() {
    std::string dir = path_join(runtime_paths().res_root, "results\\native_route_nodes_003");
    JsonValue man = parse_json_file(path_join(dir, "manifest.json"));
    if (man["status"].as_str() != "captured" || man["open_chip_database_used"].as_bool(true)
        || man["dll_sha256"].as_str() != EXPECTED_GOWIN_DLL_SHA) {
        throw std::runtime_error("Unverified native route-node table");
    }
    std::string snap_p = path_join(dir, "snapshot.log");
    if (file_exists(snap_p) && sha256_file(snap_p) != man["snapshot_sha256"].as_str()) {
        throw std::runtime_error("Route-node snapshot changed");
    }
    int16_t vss = -1, vcc = -1;
    for (const auto& [k, v] : man["nodes"].as_obj()) {
        if (v.as_str() == "VSS") vss = static_cast<int16_t>(std::stoi(k));
        else if (v.as_str() == "VCC") vcc = static_cast<int16_t>(std::stoi(k));
    }
    if (vss < 0 || vcc < 0) throw std::runtime_error("Missing VSS/VCC indices in route-node table");
    return {vss, vcc};
}

uint16_t parse_lut_init_w16(std::string_view init_str, int width) {
    std::string s = trim(init_str);
    uint64_t val = 0;
    if (!s.empty()) {
        size_t q = s.find('\'');
        if (q != std::string::npos) {
            std::string_view tail(s.data() + q + 1, s.size() - q - 1);
            if (!tail.empty()) {
                char bc = static_cast<char>(std::tolower(static_cast<unsigned char>(tail[0])));
                int base = (bc == 'h') ? 16 : (bc == 'b') ? 2 : (bc == 'd') ? 10 : (bc == 'o') ? 8 : 10;
                for (size_t i = 1; i < tail.size(); ++i) {
                    char ch = tail[i];
                    if (ch == '_') continue;
                    int d = (ch >= '0' && ch <= '9') ? (ch - '0')
                          : (ch >= 'a' && ch <= 'f') ? (10 + ch - 'a')
                          : (ch >= 'A' && ch <= 'F') ? (10 + ch - 'A') : 0;
                    val = val * static_cast<uint64_t>(base) + static_cast<uint64_t>(d);
                }
            }
        } else {
            val = std::strtoull(s.c_str(), nullptr, 0);
        }
    }
    uint64_t mask = (1ULL << (1 << width)) - 1ULL;
    val &= mask;
    uint16_t out = 0;
    int idx_mask = (1 << width) - 1;
    for (int i = 0; i < 16; ++i) {
        out |= static_cast<uint16_t>(((val >> (i & idx_mask)) & 1ULL) << i);
    }
    return out;
}

std::pair<std::pair<int, int>, std::pair<int, int>> mux_child_slots(int level, int slot) {
    if (level == 5 && (slot == 0 || slot == 2 || slot == 4 || slot == 6)) {
        return {{5, slot}, {5, slot + 1}};
    }
    if (level == 6 && (slot == 1 || slot == 5)) {
        return {{5, slot + 1}, {5, slot - 1}};
    }
    if (level == 7 && slot == 3) {
        return {{6, 5}, {6, 1}};
    }
    throw std::runtime_error("Unsupported dedicated mux level/slot");
}

struct CstConstraints {
    OrderedMap<std::pair<std::string, std::string>> io_locs;
    OrderedMap<std::string> ins_locs;
    OrderedMap<std::string> set_params;
};

CstConstraints parse_cst_file(std::string_view cst_text) {
    CstConstraints res;
    std::string clean = SynAccelLib::instance().strip_line_comments(cst_text);
    size_t pos = 0;
    while (pos < clean.size()) {
        size_t semi = clean.find(';', pos);
        if (semi == std::string::npos) break;
        std::string stmt = trim(std::string_view(clean).substr(pos, semi - pos));
        pos = semi + 1;
        if (starts_with(stmt, "IO_LOC")) {
            size_t q1 = stmt.find('"', 6);
            size_t q2 = (q1 != std::string::npos) ? stmt.find('"', q1 + 1) : std::string::npos;
            if (q2 != std::string::npos) {
                std::string port = trim(std::string_view(stmt).substr(q1 + 1, q2 - q1 - 1));
                std::string tail = trim(std::string_view(stmt).substr(q2 + 1));
                auto pads = split_char(tail, ',');
                std::string p1 = pads.empty() ? "" : trim(pads[0]);
                std::string p2 = (pads.size() >= 2) ? trim(pads[1]) : "";
                if (!port.empty() && !p1.empty()) res.io_locs.set(port, {p1, p2});
            }
        } else if (starts_with(stmt, "INS_LOC")) {
            size_t q1 = stmt.find('"', 7);
            size_t q2 = (q1 != std::string::npos) ? stmt.find('"', q1 + 1) : std::string::npos;
            if (q2 != std::string::npos) {
                std::string inst = trim(std::string_view(stmt).substr(q1 + 1, q2 - q1 - 1));
                auto ws = split_ws(std::string_view(stmt).substr(q2 + 1));
                if (!inst.empty() && !ws.empty()) res.ins_locs.set(inst, ws[0]);
            }
        } else if (starts_with(stmt, "SET_PARAM")) {
            size_t q1 = stmt.find('"', 9);
            size_t q2 = (q1 != std::string::npos) ? stmt.find('"', q1 + 1) : std::string::npos;
            if (q2 != std::string::npos) {
                std::string inst = trim(std::string_view(stmt).substr(q1 + 1, q2 - q1 - 1));
                std::string tail = trim(std::string_view(stmt).substr(q2 + 1));
                if (!inst.empty() && !tail.empty()) {
                    if (const auto* ex = res.set_params.find(inst)) res.set_params.set(inst, *ex + " " + tail);
                    else res.set_params.set(inst, tail);
                }
            }
        }
    }
    return res;
}

struct SdcParsed {
    std::unordered_map<std::string, double> clk_period_by_name;
    OrderedMap<double> port_clk_period;
    OrderedMap<double> pin_clk_period;
    OrderedMap<double> net_clk_period;
    std::unordered_set<std::string> false_from_ports;
};

SdcParsed parse_sdc_clocks_and_exceptions(std::string_view sdc_text) {
    SdcParsed out;
    std::string no_slash_comments = SynAccelLib::instance().strip_line_comments(sdc_text);
    std::string joined;
    for (size_t i = 0; i < no_slash_comments.size(); ++i) {
        if (no_slash_comments[i] == '\\' && i + 1 < no_slash_comments.size() && no_slash_comments[i + 1] == '\n') {
            joined.push_back(' ');
            ++i;
        } else if (no_slash_comments[i] == '\\' && i + 2 < no_slash_comments.size() &&
                   no_slash_comments[i + 1] == '\r' && no_slash_comments[i + 2] == '\n') {
            joined.push_back(' ');
            i += 2;
        } else {
            joined.push_back(no_slash_comments[i]);
        }
    }

    auto find_flag_val = [](const std::vector<std::string>& ws, std::string_view flag) -> std::string {
        for (size_t i = 0; i + 1 < ws.size(); ++i) {
            if (ws[i] == flag) return ws[i + 1];
        }
        return "";
    };

    auto strip_source_bracket = [](std::string_view ln) -> std::string {
        std::string res(ln);
        size_t sp = res.find("-source");
        if (sp != std::string::npos) {
            size_t k = sp + 7;
            while (k < res.size() && std::isspace(static_cast<unsigned char>(res[k]))) ++k;
            if (k < res.size() && res[k] == '[') {
                int depth = 1;
                size_t r = k + 1;
                while (r < res.size() && depth > 0) {
                    if (res[r] == '[') ++depth;
                    else if (res[r] == ']') --depth;
                    ++r;
                }
                res.erase(sp, r - sp);
            }
        }
        return res;
    };

    auto extract_get_targets = [](std::string_view ln, std::string_view cmd_kw) -> std::vector<std::vector<std::string>> {
        std::vector<std::vector<std::string>> groups;
        size_t p = 0;
        while ((p = ln.find(cmd_kw, p)) != std::string_view::npos) {
            size_t k = p + cmd_kw.size();
            while (k < ln.size() && std::isspace(static_cast<unsigned char>(ln[k]))) ++k;
            if (k < ln.size() && ln[k] == '{') {
                size_t rb = ln.find('}', k + 1);
                if (rb != std::string_view::npos) {
                    groups.push_back(split_ws(ln.substr(k + 1, rb - k - 1)));
                    p = rb + 1;
                    continue;
                }
            } else if (k < ln.size()) {
                size_t rb = ln.find_first_of("] \t", k);
                std::string_view tok = (rb == std::string_view::npos) ? ln.substr(k) : ln.substr(k, rb - k);
                if (!tok.empty()) groups.push_back({std::string(tok)});
            }
            p = k;
        }
        return groups;
    };

    auto record_clk_targets = [&](std::string_view tgt_ln, double per) {
        for (const auto& grp : extract_get_targets(tgt_ln, "get_ports")) {
            for (const auto& tok : grp) out.port_clk_period.set(tok, per);
        }
        for (const auto& grp : extract_get_targets(tgt_ln, "get_pins")) {
            for (const auto& tok : grp) out.pin_clk_period.set(tok, per);
        }
        for (const auto& grp : extract_get_targets(tgt_ln, "get_nets")) {
            for (const auto& tok : grp) out.net_clk_period.set(tok, per);
        }
    };

    for (const auto& raw_ln : split_lines(joined)) {
        std::string ln = trim(raw_ln);
        if (ln.empty() || ln[0] == '#') continue;
        auto ws = split_ws(ln);
        if (ws.empty()) continue;
        if (ws[0] == "create_clock") {
            std::string nm = find_flag_val(ws, "-name");
            std::string per_s = find_flag_val(ws, "-period");
            if (!per_s.empty()) {
                double per = std::stod(per_s);
                if (!nm.empty()) out.clk_period_by_name[nm] = per;
                record_clk_targets(ln, per);
            }
        } else if (ws[0] == "create_generated_clock") {
            std::string nm = find_flag_val(ws, "-name");
            std::string mc = find_flag_val(ws, "-master_clock");
            std::string div_s = find_flag_val(ws, "-divide_by");
            std::string mul_s = find_flag_val(ws, "-multiply_by");
            double base_p = 12.5;
            if (!mc.empty() && out.clk_period_by_name.count(mc)) {
                base_p = out.clk_period_by_name[mc];
            } else if (out.clk_period_by_name.size() == 1) {
                base_p = out.clk_period_by_name.begin()->second;
            }
            double div_v = div_s.empty() ? 1.0 : std::stod(div_s);
            double mul_v = mul_s.empty() ? 1.0 : std::stod(mul_s);
            double per = base_p * div_v / std::max(mul_v, 1e-6);
            if (!nm.empty()) out.clk_period_by_name[nm] = per;
            std::string tgt_ln = strip_source_bracket(ln);
            record_clk_targets(tgt_ln, per);
        } else if (ws[0] == "set_false_path") {
            size_t fp = ln.find("-from");
            if (fp != std::string::npos) {
                size_t lb = ln.find('[', fp);
                size_t rb = (lb != std::string::npos) ? ln.find(']', lb + 1) : std::string::npos;
                if (rb != std::string::npos) {
                    std::string_view sub(ln.data() + lb + 1, rb - lb - 1);
                    for (const auto& grp : extract_get_targets(sub, "get_ports")) {
                        for (const auto& tok : grp) out.false_from_ports.insert(tok);
                    }
                }
            }
        }
    }
    return out;
}

std::unordered_map<std::string, std::unordered_map<std::string, std::string>> parse_module_scoped_defparams(std::string_view vg_text) {
    std::string clean = SynAccelLib::instance().strip_line_comments(vg_text);
    auto ulist = verilog_units(clean);
    if (ulist.empty()) return {};

    std::unordered_set<std::string> mod_set;
    std::vector<std::string> mod_order;
    std::unordered_map<std::string, std::string_view> mod_bodies;
    for (const auto& u : ulist) {
        std::string mname = starts_with(u.name, "\\") ? u.name.substr(1) : u.name;
        std::string_view full_u(clean.data() + u.start, u.end - u.start);
        size_t nm_pos = full_u.find(u.name, 6);
        size_t search_from = (nm_pos == std::string_view::npos) ? 0 : (nm_pos + u.name.size());
        size_t lp = full_u.find('(', search_from);
        if (lp == std::string_view::npos) continue;
        mod_bodies[mname] = full_u.substr(lp + 1, full_u.size() - (lp + 1) - 9);
        mod_set.insert(mname);
        mod_order.push_back(mname);
    }

    std::unordered_map<std::string, std::unordered_map<std::string, std::unordered_map<std::string, std::string>>> local_dp;
    std::unordered_map<std::string, std::vector<std::pair<std::string, std::string>>> submod_insts;
    std::unordered_set<std::string> referenced_mods;

    for (const auto& mname : mod_order) {
        std::string_view body = mod_bodies[mname];
        for (const auto& raw_ln : split_lines(body)) {
            std::string lt = trim(raw_ln);
            if (lt.empty()) continue;
            if (starts_with(lt, "defparam") && lt.size() > 8 && std::isspace(static_cast<unsigned char>(lt[8])) && lt.back() == ';') {
                std::string rest = trim(std::string_view(lt).substr(8, lt.size() - 9));
                size_t eq = rest.find('=');
                if (eq != std::string::npos) {
                    std::string lhs = rtrim(std::string_view(rest).substr(0, eq));
                    std::string val = trim(std::string_view(rest).substr(eq + 1));
                    size_t dot = lhs.rfind('.');
                    if (dot != std::string::npos) {
                        std::string inst_id = trim(std::string_view(lhs).substr(0, dot));
                        if (starts_with(inst_id, "\\")) inst_id.erase(0, 1);
                        std::string pkey = trim(std::string_view(lhs).substr(dot + 1));
                        local_dp[mname][inst_id][pkey] = val;
                    }
                }
                continue;
            }
            // Check submodule instantiation `<cmod> <cinst> (`
            size_t ce = 0;
            if (lt[0] == '\\') {
                ++ce;
                while (ce < lt.size() && !std::isspace(static_cast<unsigned char>(lt[ce]))) ++ce;
            } else {
                while (ce < lt.size() && (std::isalnum(static_cast<unsigned char>(lt[ce])) || lt[ce] == '_' || lt[ce] == '$')) ++ce;
            }
            if (ce > 0 && ce < lt.size() && std::isspace(static_cast<unsigned char>(lt[ce]))) {
                std::string cmod = (lt[0] == '\\') ? lt.substr(1, ce - 1) : lt.substr(0, ce);
                if (mod_set.count(cmod) && cmod != mname) {
                    size_t k = ce;
                    while (k < lt.size() && std::isspace(static_cast<unsigned char>(lt[k]))) ++k;
                    size_t ie = k;
                    if (ie < lt.size() && lt[ie] == '\\') {
                        ++ie;
                        while (ie < lt.size() && !std::isspace(static_cast<unsigned char>(lt[ie]))) ++ie;
                    } else {
                        while (ie < lt.size() && (std::isalnum(static_cast<unsigned char>(lt[ie])) || lt[ie] == '_' || lt[ie] == '$')) ++ie;
                    }
                    size_t po = ie;
                    while (po < lt.size() && std::isspace(static_cast<unsigned char>(lt[po]))) ++po;
                    if (ie > k && po < lt.size() && lt[po] == '(') {
                        std::string cinst = (lt[k] == '\\') ? lt.substr(k + 1, ie - k - 1) : lt.substr(k, ie - k);
                        submod_insts[mname].emplace_back(cinst, cmod);
                        referenced_mods.insert(cmod);
                    }
                }
            }
        }
    }

    std::string root_mod = mod_order.back();
    for (const auto& m : mod_order) {
        if (!referenced_mods.count(m)) root_mod = m;
    }

    std::unordered_map<std::string, std::unordered_map<std::string, std::string>> resolved;
    std::function<void(const std::string&, const std::string&)> walk_m;
    walk_m = [&](const std::string& cur_m, const std::string& pfx) {
        if (auto it = local_dp.find(cur_m); it != local_dp.end()) {
            for (const auto& [leaf_id, kv] : it->second) {
                resolved[pfx.empty() ? leaf_id : (pfx + "/" + leaf_id)] = kv;
            }
        }
        if (auto sit = submod_insts.find(cur_m); sit != submod_insts.end()) {
            for (const auto& [cinst, cmod] : sit->second) {
                walk_m(cmod, pfx.empty() ? cinst : (pfx + "/" + cinst));
            }
        }
    };
    walk_m(root_mod, "");
    return resolved;
}

bool dsp_all_clks_explicitly_bypassed(int sb, const std::unordered_map<std::string, std::string>& params, int* out_count = nullptr) {
    static const std::vector<const char*> REQ_185 = {
        "ACCSEL_IREG_CLK","ACCSEL_PREG_CLK","ADDSUB0_IREG_CLK","ADDSUB0_PREG_CLK",
        "ADDSUB1_IREG_CLK","ADDSUB1_PREG_CLK","AREG_CLK","BREG_CLK",
        "CASISEL_IREG_CLK","CASISEL_PREG_CLK","CSEL_IREG_CLK","CSEL_PREG_CLK",
        "C_IREG_CLK","C_PREG_CLK","DREG_CLK","OREG_CLK",
        "PADDSUB_IREG_CLK","PREG_CLK","PSEL_IREG_CLK"
    };
    static const std::vector<const char*> REQ_187 = {"AREG_CLK","BREG_CLK","OREG_CLK","PREG_CLK"};
    static const std::vector<const char*> REQ_188 = {"AREG_CLK","BREG_CLK","DREG_CLK","OREG_CLK","PADDSUB_IREG_CLK","PREG_CLK","PSEL_IREG_CLK"};

    const std::vector<const char*>* req = (sb == 185) ? &REQ_185 : (sb == 187) ? &REQ_187 : (sb == 188) ? &REQ_188 : nullptr;
    if (!req || params.empty()) return false;
    if (out_count) *out_count = static_cast<int>(req->size());
    for (const char* k : *req) {
        auto it = params.find(k);
        if (it == params.end() || it->second != "\"BYPASS\"") return false;
    }
    for (const auto& [k, v] : params) {
        if (k.find("CLK") != std::string::npos && v != "\"BYPASS\"") return false;
    }
    return true;
}

bool parse_bracketed_index(std::string_view lbl, std::string_view pfx, int& out_idx) {
    if (!starts_with(lbl, pfx) || lbl.size() < pfx.size() + 3 || lbl[pfx.size()] != '[' || lbl.back() != ']') return false;
    std::string_view mid = lbl.substr(pfx.size() + 1, lbl.size() - pfx.size() - 2);
    if (mid.empty()) return false;
    for (char c : mid) if (!std::isdigit(static_cast<unsigned char>(c))) return false;
    out_idx = std::stoi(std::string(mid));
    return true;
}

std::optional<std::pair<std::string, JsonValue>> validate_const_tied_disabled_pin(
    int t,
    int sb,
    const std::string& lbl,
    int ck_val,
    const std::unordered_map<std::string, std::string>& params,
    const std::unordered_set<int>& unread_do_bits
) {
    if (ck_val != 1 || params.empty()) return std::nullopt;
    auto pget = [&](const char* k, const char* def_v = "") -> std::string {
        auto it = params.find(k);
        return (it == params.end()) ? std::string(def_v) : it->second;
    };
    int bidx = -1;

    if (t == 99 && sb == 213) {
        if (lbl == "CLKFB" && pget("CLKFB_SEL") == "\"INTERNAL\"") {
            JsonValue ev = JsonValue::object(); ev["CLKFB_SEL"] = pget("CLKFB_SEL");
            return std::pair{std::string("PLL_INTERNAL_CLKFB"), std::move(ev)};
        }
        if (parse_bracketed_index(lbl, "FBDSEL", bidx) && bidx >= 0 && bidx <= 5 && pget("DYN_FBDIV_SEL") == "\"FALSE\"") {
            JsonValue ev = JsonValue::object(); ev["DYN_FBDIV_SEL"] = pget("DYN_FBDIV_SEL");
            return std::pair{std::string("PLL_STATIC_FBDIV"), std::move(ev)};
        }
        if (parse_bracketed_index(lbl, "IDSEL", bidx) && bidx >= 0 && bidx <= 5 && pget("DYN_IDIV_SEL") == "\"FALSE\"") {
            JsonValue ev = JsonValue::object(); ev["DYN_IDIV_SEL"] = pget("DYN_IDIV_SEL");
            return std::pair{std::string("PLL_STATIC_IDIV"), std::move(ev)};
        }
        return std::nullopt;
    }
    if (t == 7 && sb == 164) {
        bool is_dlystep = (starts_with(lbl, "DLYSTEP_") && lbl.size() == 9 && lbl[8] >= '0' && lbl[8] <= '7');
        if ((lbl == "SDTAP" || lbl == "VALUE" || is_dlystep) && pget("DYN_DLY_EN", "\"FALSE\"") == "\"FALSE\"" && params.count("C_STATIC_DLY")) {
            JsonValue ev = JsonValue::object();
            ev["DYN_DLY_EN"] = pget("DYN_DLY_EN", "\"FALSE\"");
            ev["C_STATIC_DLY"] = pget("C_STATIC_DLY");
            return std::pair{std::string("IODELAY_STATIC_DELAY_MODE"), std::move(ev)};
        }
        return std::nullopt;
    }
    if (t == 5) {
        auto parse_pos_bw = [](const std::string& s) -> int {
            if (s.empty()) return -1;
            for (char c : s) if (!std::isdigit(static_cast<unsigned char>(c))) return -1;
            try { return std::stoi(s); } catch (...) { return -1; }
        };
        if (parse_bracketed_index(lbl, "DI", bidx)) {
            std::string bw_key = params.count("BIT_WIDTH_0") ? "BIT_WIDTH_0" : "BIT_WIDTH";
            int bw = parse_pos_bw(pget(bw_key.c_str()));
            if (bw == 1 || bw == 2 || bw == 4 || bw == 8 || bw == 9 || bw == 16 || bw == 18 || bw == 32 || bw == 36) {
                if (bidx >= bw) {
                    JsonValue ev = JsonValue::object(); ev[bw_key] = bw; ev["bit"] = bidx;
                    return std::pair{std::string(sb == 200 ? "SDPX9B_DI_ABOVE_BIT_WIDTH_0" : "BSRAM_DI_ABOVE_BIT_WIDTH"), std::move(ev)};
                }
                int bw1 = params.count("BIT_WIDTH_1") ? parse_pos_bw(pget("BIT_WIDTH_1")) : bw;
                if ((sb == 200 || bw1 == bw) && unread_do_bits.count(bidx)) {
                    JsonValue ev = JsonValue::object(); ev[bw_key] = bw; ev["bit"] = bidx; ev["DO_fanout"] = 0;
                    return std::pair{std::string(sb == 200 ? "SDPX9B_GND_DI_WITH_UNREAD_DO_LANE" : "BSRAM_GND_DI_WITH_UNREAD_DO_LANE"), std::move(ev)};
                }
            }
        } else if (parse_bracketed_index(lbl, "DIA", bidx)) {
            int bw0 = parse_pos_bw(pget("BIT_WIDTH_0"));
            int bw1 = parse_pos_bw(pget("BIT_WIDTH_1"));
            if (bw0 == 1 || bw0 == 2 || bw0 == 4 || bw0 == 8 || bw0 == 9 || bw0 == 16 || bw0 == 18) {
                if (bidx >= bw0) {
                    JsonValue ev = JsonValue::object(); ev["BIT_WIDTH_0"] = bw0; ev["bit"] = bidx;
                    return std::pair{std::string("BSRAM_DIA_ABOVE_BIT_WIDTH_0"), std::move(ev)};
                }
                if (bw1 == bw0 && unread_do_bits.count(bidx)) {
                    JsonValue ev = JsonValue::object(); ev["BIT_WIDTH_0"] = bw0; ev["bit"] = bidx; ev["DOAB_fanout"] = 0;
                    return std::pair{std::string(sb == 202 ? "DPX9B_GND_DIA_WITH_UNREAD_DOAB_LANE" : "BSRAM_GND_DIA_WITH_UNREAD_DOAB_LANE"), std::move(ev)};
                }
            }
        } else if (parse_bracketed_index(lbl, "DIB", bidx)) {
            int bw0 = parse_pos_bw(pget("BIT_WIDTH_0"));
            int bw1 = parse_pos_bw(pget("BIT_WIDTH_1"));
            if (bw1 == 1 || bw1 == 2 || bw1 == 4 || bw1 == 8 || bw1 == 9 || bw1 == 16 || bw1 == 18) {
                if (bidx >= bw1) {
                    JsonValue ev = JsonValue::object(); ev["BIT_WIDTH_1"] = bw1; ev["bit"] = bidx;
                    return std::pair{std::string("BSRAM_DIB_ABOVE_BIT_WIDTH_1"), std::move(ev)};
                }
                if (bw0 == bw1 && unread_do_bits.count(bidx)) {
                    JsonValue ev = JsonValue::object(); ev["BIT_WIDTH_1"] = bw1; ev["bit"] = bidx; ev["DOAB_fanout"] = 0;
                    return std::pair{std::string(sb == 202 ? "DPX9B_GND_DIB_WITH_UNREAD_DOAB_LANE" : "BSRAM_GND_DIB_WITH_UNREAD_DOAB_LANE"), std::move(ev)};
                }
            }
        }
        return std::nullopt;
    }
    if (t == 6 && sb != 98) {
        if (parse_bracketed_index(lbl, "DI", bidx) && unread_do_bits.count(bidx)) {
            JsonValue ev = JsonValue::object(); ev["bit"] = bidx; ev["DO_fanout"] = 0;
            return std::pair{std::string("SSRAM_GND_DI_WITH_UNREAD_DO_LANE"), std::move(ev)};
        }
        return std::nullopt;
    }
    if (t == 4 && sb == 185) {
        int bcnt = 0;
        if ((lbl == "CE[0]" || lbl == "CE[1]" || lbl == "RESET[0]" || lbl == "RESET[1]") && dsp_all_clks_explicitly_bypassed(185, params, &bcnt)) {
            JsonValue ev = JsonValue::object(); ev["bypass_clk_params"] = bcnt;
            return std::pair{std::string("MULTALU27X18_BYPASS_SEQ_CTRL"), std::move(ev)};
        }
        if (lbl == "ACCSEL" && pget("DYN_ACC_SEL") == "\"FALSE\"") {
            JsonValue ev = JsonValue::object(); ev["DYN_ACC_SEL"] = pget("DYN_ACC_SEL");
            return std::pair{std::string("MULTALU27X18_STATIC_ACCSEL"), std::move(ev)};
        }
        if (lbl == "ADDSUB[0]" && pget("DYN_ADD_SUB_0") == "\"FALSE\"") {
            JsonValue ev = JsonValue::object(); ev["DYN_ADD_SUB_0"] = pget("DYN_ADD_SUB_0");
            return std::pair{std::string("MULTALU27X18_STATIC_ADDSUB0"), std::move(ev)};
        }
        if (lbl == "ADDSUB[1]" && pget("DYN_ADD_SUB_1") == "\"FALSE\"") {
            JsonValue ev = JsonValue::object(); ev["DYN_ADD_SUB_1"] = pget("DYN_ADD_SUB_1");
            return std::pair{std::string("MULTALU27X18_STATIC_ADDSUB1"), std::move(ev)};
        }
        if ((lbl == "ASEL" || parse_bracketed_index(lbl, "SIA", bidx)) && pget("DYN_A_SEL") == "\"FALSE\"" && pget("A_SEL") == "1'b0") {
            JsonValue ev = JsonValue::object(); ev["DYN_A_SEL"] = pget("DYN_A_SEL"); ev["A_SEL"] = pget("A_SEL");
            return std::pair{std::string("MULTALU27X18_STATIC_ASEL_PORT_A"), std::move(ev)};
        }
        if ((lbl == "CASISEL" || parse_bracketed_index(lbl, "CASI", bidx)) && pget("DYN_CASI_SEL") == "\"FALSE\"" && pget("CASI_SEL") == "1'b0") {
            JsonValue ev = JsonValue::object(); ev["DYN_CASI_SEL"] = pget("DYN_CASI_SEL"); ev["CASI_SEL"] = pget("CASI_SEL");
            return std::pair{std::string("MULTALU27X18_STATIC_CASI_DISABLED"), std::move(ev)};
        }
        if ((lbl == "CSEL" || parse_bracketed_index(lbl, "C", bidx)) && pget("DYN_C_SEL") == "\"FALSE\"" && pget("C_SEL") == "1'b0") {
            JsonValue ev = JsonValue::object(); ev["DYN_C_SEL"] = pget("DYN_C_SEL"); ev["C_SEL"] = pget("C_SEL");
            return std::pair{std::string("MULTALU27X18_STATIC_C_DISABLED"), std::move(ev)};
        }
        if ((lbl == "PSEL" || parse_bracketed_index(lbl, "D", bidx)) && pget("DYN_P_SEL") == "\"FALSE\"" && pget("P_SEL") == "1'b0") {
            JsonValue ev = JsonValue::object(); ev["DYN_P_SEL"] = pget("DYN_P_SEL"); ev["P_SEL"] = pget("P_SEL");
            return std::pair{std::string("MULTALU27X18_STATIC_PRE_ADDER_DISABLED"), std::move(ev)};
        }
        if (lbl == "PADDSUB" && pget("DYN_P_ADDSUB") == "\"FALSE\"") {
            JsonValue ev = JsonValue::object(); ev["DYN_P_ADDSUB"] = pget("DYN_P_ADDSUB");
            return std::pair{std::string("MULTALU27X18_STATIC_PADDSUB"), std::move(ev)};
        }
        return std::nullopt;
    }
    if (t == 4 && sb == 188) {
        int bcnt = 0;
        if ((lbl == "CE[0]" || lbl == "CE[1]" || lbl == "RESET[0]" || lbl == "RESET[1]") && dsp_all_clks_explicitly_bypassed(188, params, &bcnt)) {
            JsonValue ev = JsonValue::object(); ev["bypass_clk_params"] = bcnt;
            return std::pair{std::string("MULT27X36_BYPASS_SEQ_CTRL"), std::move(ev)};
        }
        if ((lbl == "PSEL" || parse_bracketed_index(lbl, "D", bidx)) && pget("DYN_P_SEL") == "\"FALSE\"" && pget("P_SEL") == "1'b0") {
            JsonValue ev = JsonValue::object(); ev["DYN_P_SEL"] = pget("DYN_P_SEL"); ev["P_SEL"] = pget("P_SEL");
            return std::pair{std::string("MULT27X36_STATIC_PRE_ADDER_DISABLED"), std::move(ev)};
        }
        if (lbl == "PADDSUB" && pget("DYN_P_ADDSUB") == "\"FALSE\"") {
            JsonValue ev = JsonValue::object(); ev["DYN_P_ADDSUB"] = pget("DYN_P_ADDSUB");
            return std::pair{std::string("MULT27X36_STATIC_PADDSUB"), std::move(ev)};
        }
        return std::nullopt;
    }
    if (t == 4 && sb == 187) {
        int bcnt = 0;
        if ((lbl == "CE[0]" || lbl == "CE[1]" || lbl == "RESET[0]" || lbl == "RESET[1]") && dsp_all_clks_explicitly_bypassed(187, params, &bcnt)) {
            JsonValue ev = JsonValue::object(); ev["bypass_clk_params"] = bcnt;
            return std::pair{std::string("MULT12X12_BYPASS_SEQ_CTRL"), std::move(ev)};
        }
        return std::nullopt;
    }
    return std::nullopt;
}

} // namespace

// ============================================================================
// Part C: Custom P&R Worker (`run_internal_pnr_worker`) & Orchestrator (`run_ownpr_full`)
// ============================================================================
namespace {

inline uint64_t rd_ptr(uint64_t addr) noexcept {
    uint64_t v = 0;
    std::memcpy(&v, reinterpret_cast<const void*>(addr), 8);
    return v;
}
inline void wr_ptr(uint64_t addr, uint64_t v) noexcept {
    std::memcpy(reinterpret_cast<void*>(addr), &v, 8);
}
inline int16_t rd_i16_mem(uint64_t addr) noexcept {
    int16_t v = 0;
    std::memcpy(&v, reinterpret_cast<const void*>(addr), 2);
    return v;
}
inline void wr_i16_mem(uint64_t addr, int16_t v) noexcept {
    std::memcpy(reinterpret_cast<void*>(addr), &v, 2);
}
inline uint16_t rd_u16_mem(uint64_t addr) noexcept {
    uint16_t v = 0;
    std::memcpy(&v, reinterpret_cast<const void*>(addr), 2);
    return v;
}
inline void wr_u16_mem(uint64_t addr, uint16_t v) noexcept {
    std::memcpy(reinterpret_cast<void*>(addr), &v, 2);
}
inline uint8_t rd_u8_mem(uint64_t addr) noexcept {
    return *reinterpret_cast<const uint8_t*>(addr);
}
inline void wr_u8_mem(uint64_t addr, uint8_t v) noexcept {
    *reinterpret_cast<uint8_t*>(addr) = v;
}
inline int32_t rd_i32_mem(uint64_t addr) noexcept {
    int32_t v = 0;
    std::memcpy(&v, reinterpret_cast<const void*>(addr), 4);
    return v;
}
inline uint32_t rd_u32_mem(uint64_t addr) noexcept {
    uint32_t v = 0;
    std::memcpy(&v, reinterpret_cast<const void*>(addr), 4);
    return v;
}

struct GwRawVec {
    uint64_t begin = 0;
    uint64_t end = 0;
    uint64_t cap = 0;
};

std::string gw_read_std_str(const uint8_t* buf) {
    uint64_t len = rd_ptr(reinterpret_cast<uint64_t>(buf) + 16);
    uint64_t cap = rd_ptr(reinterpret_cast<uint64_t>(buf) + 24);
    const char* p = (cap >= 16) ? reinterpret_cast<const char*>(rd_ptr(reinterpret_cast<uint64_t>(buf)))
                                : reinterpret_cast<const char*>(buf);
    return std::string(p, static_cast<size_t>(len));
}

void gw_make_sso_str(uint8_t (&buf)[32], std::string_view txt) {
    if (txt.size() >= 16) throw std::runtime_error("Unexpectedly long SSO string for gowin.dll: " + std::string(txt));
    std::memset(buf, 0, sizeof(buf));
    if (!txt.empty()) std::memcpy(buf, txt.data(), txt.size());
    wr_ptr(reinterpret_cast<uint64_t>(buf) + 16, static_cast<uint64_t>(txt.size()));
    wr_ptr(reinterpret_cast<uint64_t>(buf) + 24, 15ULL);
}

struct BridgeApi {
    HMODULE mod = nullptr;
    int32_t  (*init_rrg)(uint64_t, uint64_t, uint64_t) = nullptr;
    int32_t  (*num_edges)() = nullptr;
    int32_t  (*compute_delays)(uint64_t, int32_t) = nullptr;
    int32_t  (*coord_to_id)(int16_t, int16_t, int16_t) = nullptr;
    uint64_t (*id_to_ptr)(int32_t) = nullptr;
    int32_t  (*ptr_to_id)(uint64_t) = nullptr;
    void     (*id_coord)(int32_t, int16_t*) = nullptr;
    int32_t  (*batch_query_pins_all)(uint64_t, uint64_t, uint64_t, const uint64_t*, int32_t, int32_t*, int32_t*, int32_t, uint64_t*) = nullptr;
    int32_t  (*populate_p2r_carries)(uint64_t, const uint64_t*, const uint64_t*, const uint64_t*, int32_t, const uint64_t*, const uint64_t*, int32_t) = nullptr;
    int32_t  (*place_design)(
        const uint8_t*,
        const int16_t*, int32_t,
        const int16_t*, const uint8_t*, int32_t,
        int32_t,
        const int32_t*, const int32_t*, const int32_t*, const int32_t*, const int32_t*, const int32_t*,
        const float*, const float*,
        int32_t, const int32_t*, const int32_t*, const float*,
        int16_t*, int16_t*, int16_t*
    ) = nullptr;
    int32_t  (*find_legal_slot)(
        int32_t, int32_t, int32_t, int32_t,
        const int32_t*, const int32_t*, const int32_t*, const int32_t*,
        const int16_t*, const int16_t*,
        int16_t*, int16_t*, int16_t*
    ) = nullptr;
    int32_t  (*commit_unit_move)(
        int32_t, int32_t, int32_t, int32_t,
        const int32_t*, const int32_t*, const int32_t*, const int32_t*,
        int16_t*, int16_t*, int16_t*
    ) = nullptr;
    int32_t  (*route_design)(
        int32_t,
        const uint64_t*, const int32_t*, const float*,
        const int32_t*, const int32_t*,
        const int32_t*, const int32_t*, const float*,
        int16_t, int16_t,
        const int32_t*, const uint64_t*, int32_t,
        const int32_t*, int32_t,
        int32_t, int32_t*, int32_t*
    ) = nullptr;
    int32_t  (*install_last_routes)(
        uint64_t, uint64_t,
        const uint64_t*, int32_t,
        const uint64_t*, int32_t,
        int32_t*
    ) = nullptr;
    int32_t  (*verify_last_parents_preserved)() = nullptr;
    int32_t  (*query_routed_delays)(const int32_t*, int32_t, float*) = nullptr;

    void load(const std::string& dll_path) {
        std::wstring wp = utf8_to_wide(dll_path);
        mod = LoadLibraryExW(wp.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
        if (!mod) throw std::runtime_error("Failed to load ownpr_bridge.dll: " + dll_path);
        auto sym = [&](const char* name) -> FARPROC {
            FARPROC p = GetProcAddress(mod, name);
            if (!p) throw std::runtime_error(std::string("Missing symbol in ownpr_bridge.dll: ") + name);
            return p;
        };
        init_rrg = reinterpret_cast<decltype(init_rrg)>(sym("bridge_init_rrg"));
        num_edges = reinterpret_cast<decltype(num_edges)>(sym("bridge_num_edges"));
        compute_delays = reinterpret_cast<decltype(compute_delays)>(sym("bridge_compute_delays"));
        coord_to_id = reinterpret_cast<decltype(coord_to_id)>(sym("bridge_coord_to_id"));
        id_to_ptr = reinterpret_cast<decltype(id_to_ptr)>(sym("bridge_id_to_ptr"));
        ptr_to_id = reinterpret_cast<decltype(ptr_to_id)>(sym("bridge_ptr_to_id"));
        id_coord = reinterpret_cast<decltype(id_coord)>(sym("bridge_id_coord"));
        batch_query_pins_all = reinterpret_cast<decltype(batch_query_pins_all)>(sym("bridge_batch_query_pins_all"));
        populate_p2r_carries = reinterpret_cast<decltype(populate_p2r_carries)>(sym("bridge_populate_p2r_carries"));
        place_design = reinterpret_cast<decltype(place_design)>(sym("bridge_place_design"));
        find_legal_slot = reinterpret_cast<decltype(find_legal_slot)>(sym("bridge_find_legal_slot"));
        commit_unit_move = reinterpret_cast<decltype(commit_unit_move)>(sym("bridge_commit_unit_move"));
        route_design = reinterpret_cast<decltype(route_design)>(sym("bridge_route_design"));
        install_last_routes = reinterpret_cast<decltype(install_last_routes)>(sym("bridge_install_last_routes"));
        verify_last_parents_preserved = reinterpret_cast<decltype(verify_last_parents_preserved)>(sym("bridge_verify_last_parents_preserved"));
        query_routed_delays = reinterpret_cast<decltype(query_routed_delays)>(sym("bridge_query_routed_delays"));
    }
};

struct PinRec {
    std::string lbl;
    uint64_t pin = 0;
    uint64_t net = 0;
};

struct PinKey {
    uint64_t inst = 0;
    std::string lbl;
    bool operator==(const PinKey& o) const noexcept { return inst == o.inst && lbl == o.lbl; }
};
struct PinKeyHash {
    size_t operator()(const PinKey& k) const noexcept {
        return std::hash<uint64_t>{}(k.inst) ^ (std::hash<std::string>{}(k.lbl) * 0x9e3779b97f4a7c15ULL);
    }
};

struct NtNidKey {
    uint64_t nt = 0;
    int32_t nid = 0;
    bool operator==(const NtNidKey& o) const noexcept { return nt == o.nt && nid == o.nid; }
};
struct NtNidHash {
    size_t operator()(const NtNidKey& k) const noexcept {
        return std::hash<uint64_t>{}(k.nt) ^ (static_cast<size_t>(static_cast<uint32_t>(k.nid)) * 0x9e3779b97f4a7c15ULL);
    }
};

struct L1Term { int r = 0, c = 0; double s = 0.0, b = 0.0; };
struct L1Quad { double s = 0.0, c1 = 0.0, c2 = 0.0, c3 = 0.0, c4 = 0.0; };

std::vector<L1Quad> build_min_l1_env(const std::vector<L1Term>& terms) {
    std::vector<L1Quad> out;
    for (const auto& tm : terms) {
        double sr = tm.s * tm.r;
        double sc = tm.s * tm.c;
        double c1 = tm.b + sr + sc;
        double c2 = tm.b + sr - sc;
        double c3 = tm.b - sr + sc;
        double c4 = tm.b - sr - sc;
        bool found = false;
        for (auto& q : out) {
            if (q.s == tm.s) {
                if (c1 < q.c1) q.c1 = c1;
                if (c2 < q.c2) q.c2 = c2;
                if (c3 < q.c3) q.c3 = c3;
                if (c4 < q.c4) q.c4 = c4;
                found = true;
                break;
            }
        }
        if (!found) out.push_back({tm.s, c1, c2, c3, c4});
    }
    return out;
}

double eval_min_l1_env(const std::vector<L1Quad>& env, int r, int c) noexcept {
    if (env.empty()) return 99.0;
    double best = 99.0;
    double rp = static_cast<double>(r + c);
    double rm = static_cast<double>(r - c);
    for (const auto& q : env) {
        double sp = q.s * rp;
        double sm = q.s * rm;
        double v1 = q.c1 - sp;
        double v2 = q.c2 - sm;
        double v3 = q.c3 + sm;
        double v4 = q.c4 + sp;
        double v = (v1 < v2) ? v1 : v2;
        if (v3 < v) v = v3;
        if (v4 < v) v = v4;
        if (v < best) best = v;
    }
    return best;
}

inline double lut4_pin_d(std::string_view lbl) noexcept {
    if (lbl == "I0") return 0.52;
    if (lbl == "I1") return 0.51;
    if (lbl == "I2") return 0.46;
    if (lbl == "I3") return 0.27;
    return 0.46;
}
inline double mux2_pin_d(std::string_view lbl) noexcept {
    if (lbl == "S0") return 0.23;
    if (lbl == "I0" || lbl == "I1") return 0.195;
    return 0.21;
}

} // namespace

int run_internal_pnr_worker(std::string_view out_dir) {
    try {
        double t_worker0 = perf_counter();
        std::string out = resolve_path(out_dir);
        const auto& rp = runtime_paths();
        std::string sdk_ide = rp.gowin_sdk;
        std::string sdk_bin = path_join(sdk_ide, "bin");
        std::string gowin_dll_path = path_join(sdk_bin, "gowin.dll");

        std::fprintf(stdout, "[ownpr] Initializing NativeDeviceSession and import context...\n");
        std::fflush(stdout);

        SetErrorMode(0x8003);
        SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
        std::wstring w_sdk_bin = utf8_to_wide(sdk_bin);
        AddDllDirectory(w_sdk_bin.c_str());

        (void)verify_or_record_sdk_file_sha(gowin_dll_path, EXPECTED_GOWIN_DLL_SHA, "bin/gowin.dll");
        std::string cmd_do_raw = read_file_text(path_join(out, "cmd.do"));
        std::string cmd_dev_p = PINNED_DEVICE;
        double cmd_global_freq = 0.0;
        for (const auto& ln : split_lines(cmd_do_raw)) {
            std::string tr = trim(ln);
            if (starts_with(tr, "-p ")) cmd_dev_p = trim(std::string_view(tr).substr(3));
            else if (starts_with(tr, "-global_freq ")) {
                try { cmd_global_freq = std::stod(trim(std::string_view(tr).substr(13))); } catch (...) {}
            }
        }
        double default_clk_per_ns = (cmd_global_freq > 0.0) ? (1000.0 / cmd_global_freq) : 12.5;
        std::string worker_dev_name = starts_with(cmd_dev_p, "GW5AST-138B") ? "GW5AST-138B" : "GW5AST-138C";
        std::string worker_pkg_name = "FCPBGA676A";
        {
            auto dp = split_char(cmd_dev_p, '-');
            if (dp.size() >= 3 && !dp[2].empty()) worker_pkg_name = dp[2];
        }
        std::string dev_share = path_join(sdk_ide, "share\\device\\" + worker_dev_name);
        if (worker_dev_name == "GW5AST-138B") {
            (void)verify_or_record_sdk_file_sha(path_join(dev_share, "GW5AST-138B.dat"), EXPECTED_138B_DAT_SHA, "GW5AST-138B.dat");
            (void)verify_or_record_sdk_file_sha(path_join(dev_share, "GW5AST-138B.fse"), EXPECTED_138B_FSE_SHA, "GW5AST-138B.fse");
            (void)verify_or_record_sdk_file_sha(path_join(dev_share, "GW5AST-138B.tm"),  EXPECTED_138B_TM_SHA,  "GW5AST-138B.tm");
        } else {
            (void)verify_or_record_sdk_file_sha(path_join(dev_share, "GW5AST-138C.dat"), EXPECTED_DAT_SHA, "GW5AST-138C.dat");
            (void)verify_or_record_sdk_file_sha(path_join(dev_share, "GW5AST-138C.fse"), EXPECTED_FSE_SHA, "GW5AST-138C.fse");
            (void)verify_or_record_sdk_file_sha(path_join(dev_share, "GW5AST-138C.tm"),  EXPECTED_TM_SHA,  "GW5AST-138C.tm");
        }

        std::wstring w_out = utf8_to_wide(out);
        if (!SetCurrentDirectoryW(w_out.c_str())) {
            throw std::runtime_error("SetCurrentDirectoryW failed for: " + out);
        }

        std::wstring w_gowin_dll = utf8_to_wide(gowin_dll_path);
        HMODULE gw_mod = LoadLibraryExW(w_gowin_dll.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
        if (!gw_mod) {
            gw_mod = LoadLibraryExW(w_gowin_dll.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
        }
        if (!gw_mod) throw std::runtime_error("Failed to load gowin.dll from " + gowin_dll_path);
        uint64_t base = reinterpret_cast<uint64_t>(gw_mod);

        auto gw_opt = [&](const char* sym) -> FARPROC {
            return GetProcAddress(gw_mod, sym);
        };
        auto gw_req = [&](const char* sym) -> FARPROC {
            FARPROC p = GetProcAddress(gw_mod, sym);
            if (!p) throw std::runtime_error(std::string("Missing gowin.dll export: ") + sym);
            return p;
        };

        // Initialize gowin.dll session (full_timing = true, zero vendor P&R)
        std::string arg0_s = path_join(sdk_bin, "gowin.exe");
        std::string arg1_s = "-do";
        std::string arg2_s = path_join(out, "cmd.do");
        char* av[3] = {arg0_s.data(), arg1_s.data(), arg2_s.data()};
        auto fn_read_cmd = reinterpret_cast<int (*)(int, char**, bool)>(gw_req("?readInUserCommand@@YAHHPEAPEAD_N@Z"));
        if (fn_read_cmd(3, av, true) != 0) throw std::runtime_error("Native command parsing failed");

        uint64_t mem = reinterpret_cast<uint64_t (*)()>(gw_req("?getGlobalMemPtr@@YAPEAVglobalMem@@XZ"))();
        if (reinterpret_cast<int (*)(uint64_t)>(gw_req("?readDevice@NETLIST@@YAHPEAVglobalMem@@@Z"))(mem) != 0)
            throw std::runtime_error("readDevice failed");
        if (reinterpret_cast<int (*)(uint64_t)>(gw_req("?readConfigAttFile@@YAHPEAVglobalMem@@@Z"))(mem) != 0)
            throw std::runtime_error("readConfigAttFile failed");
        if (reinterpret_cast<int (*)(uint64_t)>(gw_req("?readInput@VLGPARSE@@YAHPEAVglobalMem@@@Z"))(mem) != 0)
            throw std::runtime_error("readInput failed");

        alignas(16) uint8_t switch_storage[0x80]{};
        uint64_t switch_ptr = reinterpret_cast<uint64_t>(switch_storage);
        reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("??0switchTable@TEMPLATE@@QEAA@XZ"))(switch_ptr);
        reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setSwitchTable@globalMem@@QEAAXPEAVswitchTable@TEMPLATE@@@Z"))(mem, switch_ptr);
        reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?buildSwitchTable@switchTable@TEMPLATE@@QEAAXPEAVglobalMem@@@Z"))(switch_ptr, mem);

        uint64_t chip = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getDevChip@globalMem@@QEAAPEAVchip@NETLIST@@XZ"))(mem);
        reinterpret_cast<void (*)(uint64_t)>(gw_req("?buildRRGraph@chip@NETLIST@@QEAAXXZ"))(chip);
        uint64_t monitor = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getTmMonitor@globalMem@@QEAAPEAVTmMonitor@STA@@XZ"))(mem);
        if (reinterpret_cast<int (*)(uint64_t)>(gw_req("?readConstraints@NETLIST@@YAHPEAVglobalMem@@@Z"))(mem) != 0)
            throw std::runtime_error("Native constraints initialization failed");
        if (!reinterpret_cast<bool (*)(uint64_t)>(gw_req("?setTimingData@@YA_NPEAVglobalMem@@@Z"))(mem))
            throw std::runtime_error("Native timing initialization failed");

        uint64_t tm_ctx = rd_ptr(monitor + 8);
        uint64_t tm_lib = rd_ptr(tm_ctx + 0x38);
        std::string sp_ctx = gw_read_std_str(reinterpret_cast<const uint8_t*>(tm_ctx + 0x308));
        static const std::unordered_set<std::string> valid_sp_ctxs = {
            "C1/I0", "ES", "C2/I1", "C0/I0", "C2", "C1", "C0", "I0", "I1", "A0", "A1"
        };
        if (!valid_sp_ctxs.count(sp_ctx) && !allow_unsupported_gowin_sdk()) {
            throw std::runtime_error("Unexpected part-derived speed context: " + sp_ctx);
        }
        if (!tm_lib || rd_i16_mem(tm_lib + 0x1c) != 4 || rd_i16_mem(tm_lib + 0x1e) != 9 || rd_i16_mem(tm_lib + 0x20) != 13) {
            throw std::runtime_error("Unreviewed native timing layout");
        }
        int32_t dev_kind = rd_i32_mem(mem + 0x180);
        int32_t exp_dev_kind = (worker_dev_name == "GW5AST-138B") ? 48 : 70;
        if ((dev_kind != exp_dev_kind && !allow_unsupported_gowin_sdk()) || rd_i16_mem(chip + 0x28) != 111 || rd_i16_mem(chip + 0x2a) != 184) {
            throw std::runtime_error("Unsupported physical device");
        }

        auto fn_getgrid = reinterpret_cast<uint64_t (*)(uint64_t, int16_t, int16_t)>(gw_req("?getGridByRowCol@chip@NETLIST@@QEAAPEAVgrid@2@FF@Z"));
        auto fn_getnode = reinterpret_cast<uint64_t (*)(uint64_t, int16_t)>(base + 0x20FCD0ULL);
        auto fn_getout  = reinterpret_cast<uint64_t (*)(uint64_t, int16_t)>(base + 0x211ED0ULL);

        auto sess_node = [&](int16_t r, int16_t c, int16_t idx) -> uint64_t {
            if (r < 0 || r >= 111 || c < 0 || c >= 184 || idx < 0) throw std::runtime_error("Invalid native node coordinate");
            uint64_t g = fn_getgrid(chip, r, c);
            uint64_t tbl = g ? rd_ptr(g + 0x10) : 0;
            if (!tbl || idx >= rd_i16_mem(tbl + 8)) throw std::runtime_error("Native node index absent");
            uint64_t nd = fn_getnode(tbl, idx);
            if (!nd || rd_i16_mem(nd) != r || rd_i16_mem(nd + 2) != c || rd_i16_mem(nd + 4) != idx) {
                throw std::runtime_error("Native node coordinate mismatch");
            }
            return nd;
        };

        // Initialize PlaceRoute import context (0x200 bytes with 0xA5 guard) — ZERO vendor P&R
        if (rd_ptr(mem + 0x18) != 0) throw std::runtime_error("Unexpected existing native P&R context");
        alignas(16) uint8_t import_ctx_storage[0x200]{};
        std::memset(import_ctx_storage + 0x1c0, 0xA5, 0x40);
        uint64_t pr_ctx_addr = reinterpret_cast<uint64_t>(import_ctx_storage);
        auto fn_pr_ctor = reinterpret_cast<uint64_t (*)(uint64_t, uint64_t)>(gw_req("??0PlaceRoute@PLACEROUTE@@QEAA@PEAVglobalMem@@@Z"));
        if (fn_pr_ctor(pr_ctx_addr, mem) != pr_ctx_addr) throw std::runtime_error("Native P&R context ctor mismatch");
        for (size_t gi = 0x1c0; gi < 0x200; ++gi) {
            if (import_ctx_storage[gi] != 0xA5) throw std::runtime_error("Native P&R context guard overwritten");
        }
        reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setPR@globalMem@@QEAAXPEAVPlaceRoute@PLACEROUTE@@@Z"))(mem, pr_ctx_addr);
        uint64_t p2r_ptr = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getP2R@PlaceRoute@PLACEROUTE@@QEAAPEAVP2R@2@XZ"))(pr_ctx_addr);
        if (!p2r_ptr || rd_ptr(mem + 0x18) != pr_ctx_addr) throw std::runtime_error("Native import context initialization failed");

        // Load ownpr_bridge.dll & initialize RRG
        BridgeApi bridge;
        bridge.load(rp.bridge_dll);
        double t_rrg0 = perf_counter();
        int32_t num_rrg_nodes = bridge.init_rrg(base, chip, reinterpret_cast<uint64_t>(fn_getgrid));
        if (num_rrg_nodes <= 0) throw std::runtime_error("bridge_init_rrg failed: " + std::to_string(num_rrg_nodes));
        if (bridge.compute_delays(tm_lib, dev_kind) != 0) throw std::runtime_error("bridge_compute_delays failed");
        std::fprintf(stdout, "[ownpr] RRG initialized: %d nodes, %d edges in %.2fs\n",
                     num_rrg_nodes, bridge.num_edges(), perf_counter() - t_rrg0);
        std::fflush(stdout);

        // Enumerate flat netlist instances & pins
        uint64_t design = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ"))(mem);
        GwRawVec hier_vec{};
        reinterpret_cast<void (*)(uint64_t, void*)>(gw_req("?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z"))(design, &hier_vec);
        uint64_t io_vec = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getIobufVecReference@Design@NETLIST@@QEAAAEAV?$vector@PEAVIobuf@NETLIST@@V?$allocator@PEAVIobuf@NETLIST@@@std@@@std@@XZ"))(design);

        auto fn_get_type = reinterpret_cast<int (*)(uint64_t)>(gw_req("?getType@Instance@NETLIST@@QEAA?AW4INS_TYPE@2@XZ"));
        auto fn_getname  = reinterpret_cast<void* (*)(uint64_t, void*)>(gw_req("?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ"));
        auto fn_gethier  = reinterpret_cast<void* (*)(uint64_t, void*)>(gw_req("?getHierName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ"));
        auto fn_netname  = reinterpret_cast<void* (*)(uint64_t, void*)>(gw_req("?getName@Net@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ"));
        auto fn_rpt_nm   = reinterpret_cast<void (*)(uint64_t, void*)>(gw_req("?getPinNameForRpt@Pin@NETLIST@@QEAAXAEAV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z"));
        auto fn_gnd      = reinterpret_cast<bool (*)(uint64_t)>(gw_req("?isGndNet@Net@NETLIST@@QEAA_NXZ"));
        auto fn_vcc      = reinterpret_cast<bool (*)(uint64_t)>(gw_req("?isVccNet@Net@NETLIST@@QEAA_NXZ"));
        auto fn_iob_in    = reinterpret_cast<bool (*)(uint64_t)>(gw_opt("?isInBuf@Iobuf@NETLIST@@QEAA_NXZ"));
        auto fn_iob_inout = reinterpret_cast<bool (*)(uint64_t)>(gw_opt("?isInOutBuf@Iobuf@NETLIST@@QEAA_NXZ"));

        auto iname = [&](uint64_t o) -> std::string {
            alignas(8) uint8_t b[64]{};
            fn_getname(o, b);
            return gw_read_std_str(b);
        };
        auto ihier = [&](uint64_t o) -> std::string {
            alignas(8) uint8_t b[64]{};
            fn_gethier(o, b);
            std::string s = gw_read_std_str(b);
            size_t i = 0;
            while (i < s.size() && s[i] == '/') ++i;
            return s.substr(i);
        };
        std::unordered_map<uint64_t, std::string> net_name_cache;
        auto nname = [&](uint64_t nt) -> std::string {
            if (!nt) return "";
            if (auto it = net_name_cache.find(nt); it != net_name_cache.end()) return it->second;
            alignas(8) uint8_t b[64]{};
            fn_netname(nt, b);
            std::string s = gw_read_std_str(b);
            net_name_cache.emplace(nt, s);
            return s;
        };
        auto plbl = [&](uint64_t p, const std::string& leaf_nm) -> std::string {
            alignas(8) uint8_t b[64]{};
            wr_ptr(reinterpret_cast<uint64_t>(b) + 24, 15ULL);
            fn_rpt_nm(p, b);
            std::string s = gw_read_std_str(b);
            bool stripped_full = false;
            if (!leaf_nm.empty() && s.size() > leaf_nm.size() && starts_with(s, leaf_nm)) {
                s = s.substr(leaf_nm.size());
                stripped_full = true;
            } else {
                size_t dot = s.rfind('.');
                if (dot != std::string::npos) s = s.substr(dot + 1);
            }
            if (!stripped_full && !leaf_nm.empty()) {
                size_t ld = leaf_nm.find_last_of("./");
                std::string short_leaf = (ld != std::string::npos) ? leaf_nm.substr(ld + 1) : leaf_nm;
                if (!short_leaf.empty() && s.size() > short_leaf.size() && starts_with(s, short_leaf)) {
                    s = s.substr(short_leaf.size());
                }
            }
            while (!s.empty() && (s.front() == '.' || s.front() == '/')) s.erase(s.begin());
            return s;
        };

        static const std::pair<int, const char*> CLS_BY_T[] = {
            {1, "Lut4"}, {2, "Mux2"}, {3, "Iobuf"}, {4, "NewDsp"}, {5, "Bsram"}, {6, "Ssram"},
            {7, "Iologic"}, {13, "Clksrc"}, {20, "Gtr12_quad_db"}, {50, "Reg"}, {70, "Dl"},
            {99, "Pll"}, {100, "Dll"}, {106, "Alu"}, {112, "Dhcen"}, {114, "Inv"},
            {166, "Vcc"}, {167, "Gnd"}, {168, "Gsr"}, {174, "Jtag"}, {211, "Ae350_soc"}
        };
        std::unordered_set<int> supported_t;
        std::unordered_map<int, int (*)(uint64_t)> sub_fns;
        std::unordered_map<int, void* (*)(uint64_t, void*)> in_fns, out_fns;
        for (auto [t, cn] : CLS_BY_T) {
            supported_t.insert(t);
            std::string s_sub = std::string("?getSubType@") + cn + "@NETLIST@@UEAA?AW4INS_TYPE@2@XZ";
            if (FARPROC p = gw_opt(s_sub.c_str())) sub_fns[t] = reinterpret_cast<int (*)(uint64_t)>(p);
            std::string s_in = std::string("?getAllInPins@") + cn + "@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ";
            if (FARPROC p = gw_opt(s_in.c_str())) in_fns[t] = reinterpret_cast<void* (*)(uint64_t, void*)>(p);
            std::string s_out = std::string("?getAllOutPins@") + cn + "@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ";
            if (FARPROC p = gw_opt(s_out.c_str())) out_fns[t] = reinterpret_cast<void* (*)(uint64_t, void*)>(p);
        }

        std::vector<uint64_t> insts;
        std::unordered_set<uint64_t> seen_inst;
        std::unordered_map<uint64_t, std::string> inst_name, inst_leaf_name;
        std::unordered_map<std::string, uint64_t> name_to_inst;
        std::unordered_map<uint64_t, int> inst_type, inst_sub;
        std::unordered_map<uint64_t, std::vector<PinRec>> inst_in_pins, inst_out_pins;

        struct NetDrvRec { uint64_t inst = 0; std::string lbl; uint64_t pin = 0; };
        struct NetSnkRec { uint64_t inst = 0; std::string lbl; uint64_t pin = 0; };
        std::unordered_map<uint64_t, NetDrvRec> net_drv;
        std::vector<uint64_t> net_drv_order;
        std::unordered_map<uint64_t, int> net_in_cnt;
        std::unordered_map<uint64_t, std::vector<NetSnkRec>> net_sinks_inst;
        std::unordered_map<uint64_t, int> is_const_net;
        std::unordered_set<uint64_t> iob_ext_pins;
        std::unordered_map<uint64_t, int32_t> net_cid;

        auto ensure_net_cid = [&](uint64_t nt) -> int32_t {
            if (!nt) return -1;
            auto [it, ins] = net_cid.emplace(nt, static_cast<int32_t>(net_cid.size()));
            return it->second;
        };
        auto check_const = [&](uint64_t nt) -> int {
            if (!nt) return 0;
            ensure_net_cid(nt);
            auto it = is_const_net.find(nt);
            if (it != is_const_net.end()) return it->second;
            int v = fn_gnd(nt) ? 1 : (fn_vcc(nt) ? 2 : 0);
            is_const_net.emplace(nt, v);
            return v;
        };

        std::vector<uint64_t> scan_addrs;
        for (uint64_t a = hier_vec.begin; a < hier_vec.end; a += 8) scan_addrs.push_back(a);
        uint64_t io_beg = rd_ptr(io_vec), io_end = rd_ptr(io_vec + 8);
        for (uint64_t a = io_beg; a < io_end; a += 8) scan_addrs.push_back(a);

        std::map<int, int> type_hist;
        for (uint64_t addr : scan_addrs) {
            uint64_t o = rd_ptr(addr);
            if (!o || !seen_inst.insert(o).second) continue;
            int t = fn_get_type(o);
            if (!supported_t.count(t)) {
                throw std::runtime_error("Unsupported native instance type " + std::to_string(t) + " on " + iname(o));
            }
            if (t == 114) continue; // Virtual merged clock/control inverter already absorbed into sink Pin invert flag by VLGPARSE
            int sb = sub_fns.count(t) ? sub_fns[t](o) : t;
            std::string lnm = iname(o);
            std::string hnm = ihier(o);
            std::string nm = hnm.empty() ? lnm : hnm;
            insts.push_back(o);
            inst_name[o] = nm;
            inst_leaf_name[o] = lnm;
            name_to_inst[nm] = o;
            name_to_inst.try_emplace(lnm, o);
            inst_type[o] = t;
            inst_sub[o] = sb;
            type_hist[t]++;

            std::vector<PinRec> raw_ins, raw_outs;
            if (auto it = in_fns.find(t); it != in_fns.end()) {
                GwRawVec vi{};
                it->second(o, &vi);
                for (uint64_t pa = vi.begin; pa < vi.end; pa += 8) {
                    uint64_t p = rd_ptr(pa);
                    raw_ins.push_back({plbl(p, lnm), p, rd_ptr(p + 8)});
                }
            }
            if (auto it = out_fns.find(t); it != out_fns.end()) {
                GwRawVec vo{};
                it->second(o, &vo);
                for (uint64_t pa = vo.begin; pa < vo.end; pa += 8) {
                    uint64_t p = rd_ptr(pa);
                    raw_outs.push_back({plbl(p, lnm), p, rd_ptr(p + 8)});
                }
            }

            if (t == 3) {
                bool is_bidir = fn_iob_inout ? fn_iob_inout(o) : false;
                bool has_ib = false;
                for (const auto& pr : raw_ins) {
                    if (pr.lbl == "IO" || pr.lbl == "IOB") is_bidir = true;
                    if (pr.lbl == "IB") has_ib = true;
                }
                bool is_ibuf = (!is_bidir) && ((fn_iob_in && fn_iob_in(o)) || sb == 115 || sb == 121 || has_ib);
                for (const auto& pr : raw_ins) {
                    if ((is_bidir && (pr.lbl == "IO" || pr.lbl == "IOB")) || (is_ibuf && (pr.lbl == "I" || pr.lbl == "IB"))) {
                        iob_ext_pins.insert(pr.pin);
                    }
                }
                for (const auto& pr : raw_outs) {
                    if (!is_bidir && !is_ibuf && (pr.lbl == "O" || pr.lbl == "OB")) {
                        iob_ext_pins.insert(pr.pin);
                    }
                }
            }

            for (const auto& pr : raw_ins) {
                if (pr.net) {
                    check_const(pr.net);
                    if (iob_ext_pins.count(pr.pin)) continue;
                    net_in_cnt[pr.net]++;
                    net_sinks_inst[pr.net].push_back({o, pr.lbl, pr.pin});
                }
            }
            inst_in_pins[o] = std::move(raw_ins);

            for (const auto& pr : raw_outs) {
                if (pr.net) {
                    check_const(pr.net);
                    if (iob_ext_pins.count(pr.pin)) continue;
                    if (!net_drv.count(pr.net)) net_drv_order.push_back(pr.net);
                    net_drv[pr.net] = {o, pr.lbl, pr.pin};
                }
            }
            inst_out_pins[o] = std::move(raw_outs);
        }

        std::fprintf(stdout, "[ownpr] Extracted %zu flat leaf instances: {", insts.size());
        bool first_th = true;
        for (auto [kt, vc] : type_hist) {
            if (!first_th) std::fprintf(stdout, ", ");
            first_th = false;
            std::fprintf(stdout, "%d: %d", kt, vc);
        }
        std::fprintf(stdout, "}\n");
        std::fflush(stdout);

        // =====================================================================
        // 1. Fixed Package Pads & Special Blocks (IOB, Iologic, PLL, DLL, CLKDIV, DQS, OSC, GTR12, JTAG, GSR)
        // =====================================================================
        JsonValue pkg_json = parse_json_file(path_join(sdk_ide, "data\\device\\" + worker_dev_name + "\\" + worker_pkg_name + ".json"));
        struct PadLoc { int r = 0, c = 0, sl = 0; };
        std::map<std::string, PadLoc> pad_to_rc;
        std::unordered_map<std::string, std::string> cfg_to_pad;
        for (const auto& row : pkg_json["PIN_DATA"].as_arr()) {
            std::string idx = row.contains("INDEX") && row["INDEX"].is_str() ? row["INDEX"].as_str() : "";
            std::string nm  = row.contains("NAME")  && row["NAME"].is_str()  ? row["NAME"].as_str()  : "";
            std::string cfg = row.contains("CFG")   && row["CFG"].is_str()   ? row["CFG"].as_str()   : "";
            if (idx.empty()) continue;
            size_t io_pos = nm.find("IO");
            while (io_pos != std::string::npos) {
                if (io_pos + 4 <= nm.size()) {
                    char side = nm[io_pos + 2];
                    if (side == 'T' || side == 'B' || side == 'L' || side == 'R') {
                        size_t k = io_pos + 3;
                        while (k < nm.size() && std::isdigit(static_cast<unsigned char>(nm[k]))) ++k;
                        if (k > io_pos + 3 && k < nm.size() && (nm[k] == 'A' || nm[k] == 'B')) {
                            int num = std::stoi(nm.substr(io_pos + 3, k - (io_pos + 3)));
                            int r_io = (side == 'T') ? 0 : (side == 'B') ? 110 : num;
                            int c_io = (side == 'L') ? 0 : (side == 'R') ? 183 : num;
                            pad_to_rc[idx] = {r_io, c_io, (nm[k] == 'A') ? 0 : 1};
                            if (cfg == "TCK" || cfg == "TMS" || cfg == "TDI" || cfg == "TDO") cfg_to_pad[cfg] = idx;
                            break;
                        }
                    }
                }
                io_pos = nm.find("IO", io_pos + 1);
            }
        }

        std::string cst_path = path_join(out, "project.cst");
        CstConstraints cst = file_exists(cst_path) ? parse_cst_file(read_file_text(cst_path)) : CstConstraints{};

        std::vector<uint64_t> jtag_objs;
        for (uint64_t o : insts) if (inst_type[o] == 174) jtag_objs.push_back(o);
        std::unordered_set<uint64_t> jtag_tdo_nets;
        std::unordered_map<uint64_t, std::string> jtag_pad_net_to_cfg;
        for (uint64_t jt_o : jtag_objs) {
            for (const auto* plist : {&inst_in_pins[jt_o], &inst_out_pins[jt_o]}) {
                for (const auto& pr : *plist) {
                    if (!pr.net) continue;
                    if (pr.lbl == "tdo_pad_o") jtag_tdo_nets.insert(pr.net);
                    std::string ck = (pr.lbl == "tck_pad_i") ? "TCK"
                                   : (pr.lbl == "tms_pad_i") ? "TMS"
                                   : (pr.lbl == "tdi_pad_i") ? "TDI"
                                   : (pr.lbl == "tdo_pad_o") ? "TDO" : "";
                    if (!ck.empty() && cfg_to_pad.count(ck)) jtag_pad_net_to_cfg[pr.net] = cfg_to_pad[ck];
                }
            }
        }

        std::unordered_set<std::string> used_pads;
        for (const auto& [_, pp] : cst.io_locs) {
            used_pads.insert(pp.first);
            if (!pp.second.empty()) used_pads.insert(pp.second);
        }
        for (const auto& [_, pd] : jtag_pad_net_to_cfg) used_pads.insert(pd);

        auto fn_getblk = reinterpret_cast<uint64_t (*)(uint64_t, int16_t)>(gw_req("?get_blk@iob@NETLIST@@QEAAPEAVioblk@2@F@Z"));
        std::vector<std::pair<std::string, PadLoc>> sorted_pads(pad_to_rc.begin(), pad_to_rc.end());
        std::sort(sorted_pads.begin(), sorted_pads.end(), [](const auto& a, const auto& b) {
            return std::make_tuple(a.second.r != 110, a.second.r, a.second.c, a.second.sl, a.first) <
                   std::make_tuple(b.second.r != 110, b.second.r, b.second.c, b.second.sl, b.first);
        });
        std::deque<std::string> free_pads;
        for (const auto& [pad, pl] : sorted_pads) {
            if (used_pads.count(pad)) continue;
            uint64_t g_io = fn_getgrid(chip, static_cast<int16_t>(pl.r), static_cast<int16_t>(pl.c));
            uint64_t blk = g_io ? fn_getblk(g_io, static_cast<int16_t>(pl.sl)) : 0;
            if (blk && rd_ptr(blk + 0x10)) free_pads.push_back(pad);
        }

        struct IobAssign { int r = 0, c = 0, sl = 0; std::string pad_p, pad_n; };
        std::vector<uint64_t> iob_objs;
        for (uint64_t o : insts) if (inst_type[o] == 3) iob_objs.push_back(o);
        std::unordered_map<uint64_t, IobAssign> iob_assign;
        std::unordered_map<uint64_t, uint64_t> net_to_iob;
        std::unordered_map<std::string, uint64_t> port_name_to_iob;

        auto is_iol_io_lbl = [](std::string_view l) {
            return l == "DI" || l == "DO" || l == "D" || l == "Q0" || l == "Q1" || l == "Q" || l == "TX";
        };
        std::unordered_set<uint64_t> net_has_16x_iol;
        for (uint64_t o : insts) {
            if (inst_type[o] == 7 && (inst_sub[o] == 154 || inst_sub[o] == 141)) {
                for (const auto* plist : {&inst_in_pins[o], &inst_out_pins[o]}) {
                    for (const auto& pr : *plist) if (pr.net && is_iol_io_lbl(pr.lbl)) net_has_16x_iol.insert(pr.net);
                }
            }
        }

        for (uint64_t io_o : iob_objs) {
            std::string pad_p, pad_n, matched_port;
            bool need_ioba = false;
            for (const auto* plist : {&inst_in_pins[io_o], &inst_out_pins[io_o]}) {
                for (const auto& pr : *plist) {
                    if (pr.lbl == "IB" || pr.lbl == "OB" || pr.lbl == "IOB") need_ioba = true;
                    if (!pr.net) continue;
                    if (net_has_16x_iol.count(pr.net)) need_ioba = true;
                    std::string nn = nname(pr.net);
                    if (iob_ext_pins.count(pr.pin)) {
                        if (pad_p.empty()) {
                            if (const auto* lp = cst.io_locs.find(nn)) {
                                pad_p = lp->first;
                                pad_n = lp->second;
                                matched_port = nn;
                            }
                        }
                    } else {
                        net_to_iob[pr.net] = io_o;
                        if (pad_p.empty() && jtag_pad_net_to_cfg.count(pr.net)) {
                            pad_p = jtag_pad_net_to_cfg[pr.net];
                            matched_port = nn;
                        }
                    }
                }
            }
            if (pad_p.empty()) {
                if (free_pads.empty()) throw std::runtime_error("No free package pads left for unconstrained IOB " + inst_name[io_o]);
                if (need_ioba) {
                    auto fit = std::find_if(free_pads.begin(), free_pads.end(), [&](const std::string& p) {
                        return pad_to_rc.at(p).sl == 0;
                    });
                    if (fit != free_pads.end()) {
                        pad_p = *fit;
                        free_pads.erase(fit);
                        auto pl0 = pad_to_rc.at(pad_p);
                        auto bit = std::find_if(free_pads.begin(), free_pads.end(), [&](const std::string& p) {
                            const auto& q = pad_to_rc.at(p);
                            return q.r == pl0.r && q.c == pl0.c && q.sl == 1;
                        });
                        if (bit != free_pads.end()) {
                            pad_n = *bit;
                            free_pads.erase(bit);
                        }
                    } else {
                        pad_p = free_pads.front();
                        free_pads.pop_front();
                    }
                } else {
                    pad_p = free_pads.front();
                    free_pads.pop_front();
                }
            }
            auto pl = pad_to_rc.at(pad_p);
            iob_assign[io_o] = {pl.r, pl.c, pl.sl, pad_p, pad_n};
            if (!matched_port.empty()) port_name_to_iob[matched_port] = io_o;
        }

        // Match Iologic (type=7) to co-located Iobuf
        std::vector<uint64_t> iol_objs;
        for (uint64_t o : insts) if (inst_type[o] == 7) iol_objs.push_back(o);
        std::unordered_map<uint64_t, uint64_t> iol_to_iob;
        std::unordered_map<uint64_t, std::vector<uint64_t>> net_to_iols;
        for (uint64_t iol_o : iol_objs) {
            for (const auto* plist : {&inst_in_pins[iol_o], &inst_out_pins[iol_o]}) {
                for (const auto& pr : *plist) {
                    if (pr.net && is_iol_io_lbl(pr.lbl)) {
                        net_to_iols[pr.net].push_back(iol_o);
                        if (auto it = net_to_iob.find(pr.net); it != net_to_iob.end()) iol_to_iob[iol_o] = it->second;
                    }
                }
            }
        }
        for (uint64_t iol_o : iol_objs) {
            if (iol_to_iob.count(iol_o)) continue;
            for (const auto* plist : {&inst_in_pins[iol_o], &inst_out_pins[iol_o]}) {
                for (const auto& pr : *plist) {
                    if (pr.net && is_iol_io_lbl(pr.lbl)) {
                        for (uint64_t peer : net_to_iols[pr.net]) {
                            if (auto it = iol_to_iob.find(peer); it != iol_to_iob.end()) {
                                iol_to_iob[iol_o] = it->second;
                                break;
                            }
                        }
                    }
                }
            }
            if (!iol_to_iob.count(iol_o)) {
                throw std::runtime_error("Could not resolve co-located IOBUF for Iologic " + inst_name[iol_o]);
            }
        }

        // PLL sites (0..7)
        GwRawVec plls_vec{};
        reinterpret_cast<void (*)(uint64_t, void*)>(gw_req("?acquireFullPlls@chip@NETLIST@@QEAAXAEAV?$vector@PEAVpll@NETLIST@@V?$allocator@PEAVpll@NETLIST@@@std@@@std@@@Z"))(chip, &plls_vec);
        std::vector<uint64_t> pll_phys;
        std::vector<std::pair<int, int>> pll_rcs;
        for (uint64_t a = plls_vec.begin; a < plls_vec.end; a += 8) {
            uint64_t p = rd_ptr(a);
            pll_phys.push_back(p);
            pll_rcs.emplace_back(rd_i16_mem(p + 0x12), rd_i16_mem(p + 0x14));
        }

        std::vector<int> pll_left_idxs, pll_right_idxs, pll_bot_idxs;
        for (int pi = 0; pi < static_cast<int>(pll_rcs.size()); ++pi) {
            if (pll_rcs[pi].first >= 108) pll_bot_idxs.push_back(pi);
            else if (pll_rcs[pi].second < 20) pll_left_idxs.push_back(pi);
            else if (pll_rcs[pi].second > 160) pll_right_idxs.push_back(pi);
        }
        std::sort(pll_left_idxs.begin(), pll_left_idxs.end(), [&](int a, int b) { return pll_rcs[a].first < pll_rcs[b].first; });
        std::sort(pll_right_idxs.begin(), pll_right_idxs.end(), [&](int a, int b) { return pll_rcs[a].first < pll_rcs[b].first; });
        std::sort(pll_bot_idxs.begin(), pll_bot_idxs.end(), [&](int a, int b) { return pll_rcs[a].second < pll_rcs[b].second; });

        std::vector<uint64_t> pll_objs;
        for (uint64_t o : insts) if (inst_type[o] == 99) pll_objs.push_back(o);
        std::unordered_map<uint64_t, int> pll_assign;
        std::unordered_set<int> used_pll_idx;
        auto pll_drives_ae350_core_clk = [&](uint64_t po) -> bool {
            for (const auto& pr : inst_out_pins[po]) {
                if (!pr.net || !starts_with(pr.lbl, "CLKOUT") || !net_sinks_inst.count(pr.net)) continue;
                for (const auto& snk : net_sinks_inst[pr.net]) {
                    if (inst_type[snk.inst] == 211 && snk.lbl == "CORE_CLK") return true;
                }
            }
            return false;
        };
        for (uint64_t po : pll_objs) {
            const std::string& nm = inst_name[po];
            const std::string* loc_p = cst.ins_locs.find(nm);
            if (!loc_p) loc_p = cst.ins_locs.find(inst_leaf_name[po]);
            if (loc_p && !loc_p->empty()) {
                int bidx = -1, idx = -1;
                if (starts_with(*loc_p, "PLL_L") && parse_bracketed_index(*loc_p, "PLL_L", bidx) && bidx >= 0 && bidx < static_cast<int>(pll_left_idxs.size())) {
                    idx = pll_left_idxs[bidx];
                } else if (starts_with(*loc_p, "PLL_R") && parse_bracketed_index(*loc_p, "PLL_R", bidx) && bidx >= 0 && bidx < static_cast<int>(pll_right_idxs.size())) {
                    idx = pll_right_idxs[bidx];
                } else if (starts_with(*loc_p, "PLL_B") && parse_bracketed_index(*loc_p, "PLL_B", bidx) && bidx >= 0 && bidx < static_cast<int>(pll_bot_idxs.size())) {
                    idx = pll_bot_idxs[bidx];
                } else {
                    throw std::runtime_error("Unsupported PLL INS_LOC " + *loc_p + " on " + nm);
                }
                if (pll_drives_ae350_core_clk(po) && (pll_right_idxs.empty() || idx != pll_right_idxs[0])) {
                    throw std::runtime_error("PLL " + nm + " driving AE350_SOC.CORE_CLK must be constrained to PLL_R[0], got " + *loc_p);
                }
                pll_assign[po] = idx;
                used_pll_idx.insert(idx);
            }
        }
        for (uint64_t po : pll_objs) {
            if (pll_assign.count(po)) continue;
            if (pll_drives_ae350_core_clk(po)) {
                if (pll_right_idxs.empty() || used_pll_idx.count(pll_right_idxs[0])) {
                    throw std::runtime_error("Dedicated site PLL_R[0] is unavailable for PLL driving AE350_SOC.CORE_CLK: " + inst_name[po]);
                }
                pll_assign[po] = pll_right_idxs[0];
                used_pll_idx.insert(pll_right_idxs[0]);
                continue;
            }
            std::pair<int, int> ref_rc{103, 183};
            for (const auto& pr : inst_in_pins[po]) {
                if (pr.lbl == "CLKIN" && net_drv.count(pr.net)) {
                    uint64_t drv_o = net_drv[pr.net].inst;
                    if (iob_assign.count(drv_o)) ref_rc = {iob_assign[drv_o].r, iob_assign[drv_o].c};
                    else if (pll_assign.count(drv_o)) ref_rc = pll_rcs[pll_assign[drv_o]];
                }
            }
            int best_i = -1, best_d = 1000000000;
            for (int i = 0; i < static_cast<int>(pll_phys.size()); ++i) {
                if (used_pll_idx.count(i)) continue;
                int d = std::abs(pll_rcs[i].first - ref_rc.first) + std::abs(pll_rcs[i].second - ref_rc.second);
                if (d < best_d) { best_d = d; best_i = i; }
            }
            pll_assign[po] = best_i;
            used_pll_idx.insert(best_i);
        }

        // DLL (type=100)
        std::vector<uint64_t> dll_objs;
        for (uint64_t o : insts) if (inst_type[o] == 100) dll_objs.push_back(o);
        static const std::pair<const char*, std::pair<int, int>> DLL_LOCS[] = {
            {"DDRDLLM_TL", {0, 0}}, {"DDRDLLM_BL", {110, 0}}, {"DDRDLLM_TR", {0, 183}}, {"DDRDLLM_BR", {110, 183}}
        };
        std::unordered_map<uint64_t, std::pair<int, int>> dll_assign;
        std::set<std::pair<int, int>> used_dll_rc;
        for (uint64_t do_o : dll_objs) {
            const std::string* loc_p = cst.ins_locs.find(inst_name[do_o]);
            if (!loc_p) loc_p = cst.ins_locs.find(inst_leaf_name[do_o]);
            std::pair<int, int> rc{-1, -1};
            if (loc_p) {
                for (const auto& [k, v] : DLL_LOCS) if (*loc_p == k) { rc = v; break; }
            }
            if (rc.first < 0) {
                int pref_c = -1, pref_r = 110;
                for (const auto& opr : inst_out_pins[do_o]) {
                    if (starts_with(opr.lbl, "STEP[") && opr.net && net_sinks_inst.count(opr.net)) {
                        for (const auto& snk : net_sinks_inst[opr.net]) {
                            if (inst_type[snk.inst] == 13 && inst_sub[snk.inst] == 102) {
                                for (const auto& dpr : inst_in_pins[snk.inst]) {
                                    if (dpr.lbl == "DQSIN" && net_to_iob.count(dpr.net)) {
                                        const auto& ia = iob_assign[net_to_iob[dpr.net]];
                                        if (ia.c == 0 || ia.c == 183) pref_c = ia.c;
                                        pref_r = ia.r;
                                        break;
                                    }
                                }
                            }
                            if (pref_c >= 0) break;
                        }
                    }
                    if (pref_c >= 0) break;
                }
                for (const auto& ipr : inst_in_pins[do_o]) {
                    if (ipr.lbl == "CLKIN" && net_drv.count(ipr.net)) {
                        uint64_t drv_o = net_drv[ipr.net].inst;
                        if (pll_assign.count(drv_o)) {
                            auto prc = pll_rcs[pll_assign[drv_o]];
                            if (pref_c < 0) pref_c = (prc.second < 92) ? 0 : 183;
                            pref_r = prc.first;
                        }
                    }
                }
                int best_d = 1000000000;
                for (const auto& [_, v] : DLL_LOCS) {
                    if (used_dll_rc.count(v)) continue;
                    if (pref_c >= 0 && v.second != pref_c) continue;
                    int d = std::abs(v.first - pref_r);
                    if (d < best_d) { best_d = d; rc = v; }
                }
                if (rc.first < 0) {
                    for (const auto& [_, v] : DLL_LOCS) if (!used_dll_rc.count(v)) { rc = v; break; }
                }
            }
            dll_assign[do_o] = rc;
            used_dll_rc.insert(rc);
        }

        // Clksrc (type=13: sub=101 CLKDIV, sub=102 DQS) & Dhcen (type=112: DHCE/DHCEN)
        auto fn_get_hclk5 = reinterpret_cast<uint64_t (*)(uint64_t, int16_t)>(gw_req("?getHclk5AT@chip@NETLIST@@QEAAPEAVHclk_5AT@2@F@Z"));
        struct HclkSlot { int r = 0, c = 0; uint64_t phy = 0; };
        std::map<std::pair<int, int>, HclkSlot> hclk_slots;
        std::map<std::pair<int, int>, HclkSlot> dhcen_slots;
        for (int16_t hi = 0; hi < 6; ++hi) {
            uint64_t h5 = fn_get_hclk5(chip, hi);
            if (!h5) continue;
            for (int ss = 0; ss < 4; ++ss) {
                uint64_t pcd = rd_ptr(h5 + 8 + 8 * ss);
                if (pcd) hclk_slots[{hi, ss}] = {rd_i16_mem(pcd + 0x12), rd_i16_mem(pcd + 0x14), pcd};
            }
            for (int ds = 0; ds < 12; ++ds) {
                uint64_t pdh = rd_ptr(h5 + 0x58 + 8 * ds);
                if (pdh) dhcen_slots[{hi, ds}] = {rd_i16_mem(pdh + 0x12), rd_i16_mem(pdh + 0x14), pdh};
            }
        }

        std::vector<uint64_t> clkdiv_objs, dqs_objs, osc_objs, gtr_objs, dhcen_objs, ae350_objs;
        for (uint64_t o : insts) {
            if (inst_type[o] == 13 && inst_sub[o] == 101) clkdiv_objs.push_back(o);
            else if (inst_type[o] == 13 && inst_sub[o] == 102) dqs_objs.push_back(o);
            else if (inst_type[o] == 13 && inst_sub[o] == 169) osc_objs.push_back(o);
            else if (inst_type[o] == 20) gtr_objs.push_back(o);
            else if (inst_type[o] == 112) dhcen_objs.push_back(o);
            else if (inst_type[o] == 211) ae350_objs.push_back(o);
        }
        std::unordered_map<uint64_t, std::pair<int, int>> clkdiv_assign;
        std::set<std::pair<int, int>> used_hclk;
        for (uint64_t co : clkdiv_objs) {
            const std::string* lp = cst.ins_locs.find(inst_name[co]);
            if (!lp) lp = cst.ins_locs.find(inst_leaf_name[co]);
            std::string loc = lp ? trim(*lp) : "";
            if (loc.empty()) continue;
            std::string uloc = to_upper_ascii(loc);
            std::pair<int, int> key_h{-1, -1};
            int k_s = -1;
            if (parse_bracketed_index(uloc, "RIGHTSIDE", k_s)) key_h = {(k_s < 4) ? 0 : 1, k_s % 4};
            else if (parse_bracketed_index(uloc, "BOTTOMSIDE", k_s)) key_h = {(k_s < 4) ? 2 : 3, k_s % 4};
            else if (parse_bracketed_index(uloc, "LEFTSIDE", k_s)) key_h = {(k_s < 4) ? 5 : 4, k_s % 4};
            else {
                size_t lb = loc.find('['), rb = loc.find(']');
                if (lb != std::string::npos && rb != std::string::npos && rb > lb + 1) {
                    key_h = {std::min(5, std::stoi(loc.substr(lb + 1, rb - lb - 1))), 0};
                }
            }
            if (hclk_slots.count(key_h) && !used_hclk.count(key_h)) {
                clkdiv_assign[co] = key_h;
                used_hclk.insert(key_h);
            }
        }
        auto resolve_clk_src_rc = [&](uint64_t drv_o, std::pair<int, int>& ref_rc) -> bool {
            if (inst_type.count(drv_o) && inst_type[drv_o] == 112) {
                for (const auto& dpr : inst_in_pins[drv_o]) {
                    if (dpr.lbl == "CLKIN" && net_drv.count(dpr.net)) {
                        drv_o = net_drv[dpr.net].inst;
                        break;
                    }
                }
            }
            if (pll_assign.count(drv_o)) { ref_rc = pll_rcs[pll_assign[drv_o]]; return true; }
            if (iob_assign.count(drv_o)) { ref_rc = {iob_assign[drv_o].r, iob_assign[drv_o].c}; return true; }
            if (dll_assign.count(drv_o)) { ref_rc = dll_assign[drv_o]; return true; }
            return false;
        };
        std::unordered_map<uint64_t, std::pair<int, int>> dhcen_assign;
        std::set<std::pair<int, int>> used_dhcen;

        // DQS sites on border IOBs
        auto fn_get_dqs = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getDqs@iob@NETLIST@@QEAAPEAVclksrc@2@XZ"));
        struct DqsCand { int r = 0, c = 0; uint64_t phy = 0; };
        std::vector<DqsCand> dqs_candidates;
        std::vector<std::pair<int, int>> border_rcs;
        for (int r = 1; r < 110; ++r) border_rcs.emplace_back(r, 0);
        for (int r = 1; r < 110; ++r) border_rcs.emplace_back(r, 183);
        for (int c = 1; c < 183; ++c) border_rcs.emplace_back(0, c);
        for (int c = 1; c < 183; ++c) border_rcs.emplace_back(110, c);
        for (auto [r_io, c_io] : border_rcs) {
            uint64_t g = fn_getgrid(chip, static_cast<int16_t>(r_io), static_cast<int16_t>(c_io));
            if (!g) continue;
            uint64_t vt = rd_ptr(g);
            if (!(base <= vt && vt < base + 0x2000000ULL)) continue;
            uint64_t ti = base + rd_u32_mem(rd_ptr(vt - 8) + 12);
            std::string_view rtti(reinterpret_cast<const char*>(ti + 16), 24);
            if (rtti.find("iob@NETLIST") != std::string_view::npos) {
                uint64_t dq = fn_get_dqs(g);
                if (dq && rd_i16_mem(dq + 0x12) == r_io && rd_i16_mem(dq + 0x14) == c_io) {
                    dqs_candidates.push_back({r_io, c_io, dq});
                }
            }
        }

        std::unordered_map<uint64_t, DqsCand> dqs_assign;
        std::set<std::pair<int, int>> used_dqs;
        for (uint64_t dqs_o : dqs_objs) {
            std::pair<int, int> ref_rc{-1, -1};
            for (const auto& pr : inst_in_pins[dqs_o]) {
                if (pr.lbl == "DQSIN" && net_to_iob.count(pr.net)) {
                    const auto& ia = iob_assign[net_to_iob[pr.net]];
                    ref_rc = {ia.r, ia.c};
                    break;
                }
            }
            if (ref_rc.first < 0) throw std::runtime_error("DQS " + inst_name[dqs_o] + " lacks dedicated DQSIN -> IOBUF connection");
            const DqsCand* best_c = nullptr;
            int best_d = 1000000000;
            for (const auto& cand : dqs_candidates) {
                if (used_dqs.count({cand.r, cand.c}) || cand.c != ref_rc.second || std::abs(cand.r - ref_rc.first) > 5) continue;
                int d = std::abs(cand.r - ref_rc.first);
                if (d < best_d) { best_d = d; best_c = &cand; }
            }
            if (!best_c) throw std::runtime_error("No legal same-lane DQS site found for " + inst_name[dqs_o]);
            dqs_assign[dqs_o] = *best_c;
            used_dqs.insert({best_c->r, best_c->c});
        }

        std::vector<uint64_t> gsr_objs;
        for (uint64_t o : insts) if (inst_type[o] == 168) gsr_objs.push_back(o);

        // =====================================================================
        // 1b. Bind All Fixed Blocks Early & Reserve Their RRG Entry CFU Tiles
        // =====================================================================
        auto fn_setphy    = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z"));
        auto fn_setlogic  = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setLogicInst@instance@NETLIST@@QEAAXPEAVInstance@2@@Z"));
        auto fn_addgrid   = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?addInstToGrid@grid@NETLIST@@QEAAXPEAVInstance@2@@Z"));
        auto fn_setnd_pin = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setNode@Pin@NETLIST@@QEAAXPEAVnode@2@@Z"));

        auto fn_setphy_io     = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setPhyIobuf@Iobuf@NETLIST@@QEAAXPEAViobuf@2@@Z"));
        auto fn_set_iolog_io  = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setIobufLogic@iobuf@NETLIST@@QEAAXPEAVIobuf@2@@Z"));
        auto fn_add_placed_io = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?addPlacediobuf@ioblk@NETLIST@@QEAAXPEAViobuf@2@@Z"));
        auto fn_set_used_blk  = reinterpret_cast<void (*)(uint64_t, bool)>(gw_req("?setUsed@ioblk@NETLIST@@QEAAX_N@Z"));
        auto fn_rst_p2n_io    = reinterpret_cast<void (*)(uint64_t)>(gw_req("?resetPin2Node@Iobuf@NETLIST@@UEAAXXZ"));
        auto fn_sp2n_io       = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Iobuf@NETLIST@@UEAAXXZ"));
        auto fn_get_bank_io   = reinterpret_cast<int16_t (*)(uint64_t)>(gw_req("?getBank@iobuf@NETLIST@@QEAAFXZ"));
        auto fn_get_pio_arr   = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getPioAttribute@Iobuf@NETLIST@@QEAAPEAW4PIO_VALUE@2@XZ"));
        auto fn_set_pio       = reinterpret_cast<int (*)(uint64_t, int, int)>(gw_req("?setPioAttribute@Iobuf@NETLIST@@QEAAHW4PIO_ATTR@2@W4PIO_VALUE@2@@Z"));
        auto fn_set_bank_v    = reinterpret_cast<void (*)(uint64_t, int16_t, int)>(gw_req("?setBankVccio@chip@NETLIST@@QEAAXFW4PIO_VALUE@2@@Z"));
        auto fn_set_bank_arr  = reinterpret_cast<void (*)(uint64_t, int16_t, int)>(gw_req("?setBankVccioToArr@chip@NETLIST@@QEAAXFW4PIO_VALUE@2@@Z"));

        struct IobBlkRec { int r = 0, c = 0, sl = 0; uint64_t blk = 0; };
        std::unordered_map<uint64_t, IobBlkRec> iob_blk_map;
        std::map<int16_t, int> bank_vccios;
        for (uint64_t io_o : iob_objs) {
            const auto& ia = iob_assign[io_o];
            uint64_t g_io = fn_getgrid(chip, static_cast<int16_t>(ia.r), static_cast<int16_t>(ia.c));
            uint64_t blk = fn_getblk(g_io, static_cast<int16_t>(ia.sl));
            uint64_t phy_io = rd_ptr(rd_ptr(blk + 0x10));
            fn_setphy(io_o, phy_io);
            fn_setlogic(phy_io, io_o);
            fn_setphy_io(io_o, phy_io);
            fn_set_iolog_io(phy_io, io_o);
            fn_add_placed_io(blk, phy_io);
            fn_set_used_blk(blk, true);
            if (!ia.pad_n.empty() && pad_to_rc.count(ia.pad_n)) {
                auto pln = pad_to_rc[ia.pad_n];
                uint64_t gn = fn_getgrid(chip, static_cast<int16_t>(pln.r), static_cast<int16_t>(pln.c));
                if (gn && fn_getblk(gn, static_cast<int16_t>(pln.sl))) {
                    fn_set_used_blk(fn_getblk(gn, static_cast<int16_t>(pln.sl)), true);
                }
            }
            fn_addgrid(g_io, io_o);
            iob_blk_map[io_o] = {ia.r, ia.c, ia.sl, blk};
            int16_t bk = fn_get_bank_io(phy_io);
            int vccio = rd_i32_mem(fn_get_pio_arr(io_o) + 10 * 4);
            if (!vccio) vccio = 93;
            bank_vccios[bk] = vccio;
            fn_set_pio(io_o, 10, vccio);
        }
        for (auto [bk, vccio] : bank_vccios) {
            fn_set_bank_v(chip, bk, vccio);
            fn_set_bank_arr(chip, bk, vccio);
        }

        auto fn_setphy_iol   = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setPhyIologic@Iologic@NETLIST@@QEAAXPEAViologic@2@@Z"));
        auto fn_rst_p2n_iol  = reinterpret_cast<void (*)(uint64_t)>(gw_req("?resetPin2Node@Iologic@NETLIST@@UEAAXXZ"));
        auto fn_sp2n_iol     = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Iologic@NETLIST@@UEAAXXZ"));
        auto fn_get_out_io   = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getOutNode@iobuf@NETLIST@@QEAAPEAVnode@2@XZ"));
        auto fn_get_iol_164  = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getIologic@ioblk@NETLIST@@QEAAPEAViologic@2@XZ"));
        auto fn_get_iol_165  = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getIem@ioblk@NETLIST@@QEAAPEAViologic@2@XZ"));
        auto fn_get_iol_iddr = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getIddrxn@ioblk@NETLIST@@QEAAPEAViologic@2@XZ"));
        auto fn_get_iol_odd  = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getOddrxn@ioblk@NETLIST@@QEAAPEAViologic@2@XZ"));

        for (uint64_t iol_o : iol_objs) {
            uint64_t io_o = iol_to_iob[iol_o];
            const auto& bm = iob_blk_map[io_o];
            int sb = inst_sub[iol_o];
            uint64_t phy_iol = (sb == 164) ? fn_get_iol_164(bm.blk)
                             : (sb == 165) ? fn_get_iol_165(bm.blk)
                             : (sb >= 140 && sb < 150) ? fn_get_iol_iddr(bm.blk)
                             : fn_get_iol_odd(bm.blk);
            fn_setphy(iol_o, phy_iol);
            fn_setlogic(phy_iol, iol_o);
            fn_setphy_iol(iol_o, phy_iol);
            fn_addgrid(fn_getgrid(chip, static_cast<int16_t>(bm.r), static_cast<int16_t>(bm.c)), iol_o);
        }
        for (uint64_t io_o : iob_objs) { fn_rst_p2n_io(io_o); fn_sp2n_io(io_o); }
        for (uint64_t iol_o : iol_objs) { fn_rst_p2n_iol(iol_o); fn_sp2n_iol(iol_o); }

        auto clear_pin_node = [&](uint64_t p_ptr) {
            uint64_t nd_old = rd_ptr(p_ptr + 0x18);
            if (nd_old && rd_ptr(nd_old + 8) == p_ptr) wr_ptr(nd_old + 8, 0ULL);
            fn_setnd_pin(p_ptr, 0ULL);
        };
        auto iob_owner_of = [&](uint64_t obj) -> uint64_t {
            auto it = inst_type.find(obj);
            if (it == inst_type.end()) return 0;
            if (it->second == 3) return obj;
            if (it->second == 7) {
                auto iit = iol_to_iob.find(obj);
                return (iit != iol_to_iob.end()) ? iit->second : 0;
            }
            return 0;
        };

        std::unordered_set<uint64_t> co_ioblk_sink_pins, intra_ioblk_nets;
        for (uint64_t iol_o : iol_objs) {
            if (inst_sub[iol_o] != 164) continue;
            uint64_t io_o = iol_to_iob[iol_o];
            const auto& bm = iob_blk_map[io_o];
            uint64_t phy_io = rd_ptr(rd_ptr(bm.blk + 0x10));
            uint64_t out_nd_io = phy_io ? fn_get_out_io(phy_io) : 0;

            // Output-delay IODELAY mode: DI driven from fabric/GCLK (not io_o) and DO driving co-located io_o.I
            uint64_t di_pin = 0, di_net = 0;
            for (const auto& ipr : inst_in_pins[iol_o]) {
                if (ipr.lbl == "DI") { di_pin = ipr.pin; di_net = ipr.net; break; }
            }
            bool di_from_fabric = (di_pin != 0 && di_net != 0 && (!net_drv.count(di_net) || iob_owner_of(net_drv[di_net].inst) != io_o));
            if (di_from_fabric) {
                uint64_t coloc_iob_in_pin = 0;
                for (const auto& opr : inst_out_pins[iol_o]) {
                    if (opr.lbl == "DO" && opr.net && net_sinks_inst.count(opr.net)) {
                        for (const auto& snk : net_sinks_inst[opr.net]) {
                            if (snk.inst == io_o && snk.lbl == "I") {
                                coloc_iob_in_pin = snk.pin;
                                break;
                            }
                        }
                    }
                }
                if (coloc_iob_in_pin != 0) {
                    uint64_t obuf_in_nd = rd_ptr(coloc_iob_in_pin + 0x18);
                    if (obuf_in_nd != 0) {
                        clear_pin_node(coloc_iob_in_pin);
                        co_ioblk_sink_pins.insert(coloc_iob_in_pin);
                        clear_pin_node(di_pin);
                        fn_setnd_pin(di_pin, obuf_in_nd);
                        wr_ptr(obuf_in_nd + 8, di_pin);
                    }
                }
            }

            if (!out_nd_io) continue;
            int16_t r_io = rd_i16_mem(out_nd_io), c_io = rd_i16_mem(out_nd_io + 2);
            for (const auto& pr : inst_out_pins[iol_o]) {
                if (pr.lbl == "DF") {
                    if (rd_ptr(pr.pin + 0x18) == 0) {
                        int32_t df_id = bridge.coord_to_id(r_io, c_io, (bm.sl == 0) ? 50 : 51);
                        if (df_id >= 0) {
                            uint64_t df_ptr = bridge.id_to_ptr(df_id);
                            fn_setnd_pin(pr.pin, df_ptr);
                            wr_ptr(df_ptr + 8, pr.pin);
                        }
                    }
                } else if (pr.lbl == "DO") {
                    bool has_coloc_iol = false;
                    bool has_coloc_iob = false;
                    if (pr.net && net_sinks_inst.count(pr.net)) {
                        for (const auto& snk : net_sinks_inst[pr.net]) {
                            if (snk.inst == io_o) has_coloc_iob = true;
                            else if (iob_owner_of(snk.inst) == io_o) has_coloc_iol = true;
                        }
                    }
                    if (has_coloc_iol) {
                        clear_pin_node(pr.pin);
                        fn_setnd_pin(pr.pin, out_nd_io);
                    } else if (!has_coloc_iob || rd_ptr(pr.pin + 0x18) == 0) {
                        int32_t do_id = bridge.coord_to_id(r_io, c_io, (bm.sl == 0) ? 52 : 53);
                        if (do_id >= 0) {
                            clear_pin_node(pr.pin);
                            uint64_t do_ptr = bridge.id_to_ptr(do_id);
                            fn_setnd_pin(pr.pin, do_ptr);
                            wr_ptr(do_ptr + 8, pr.pin);
                        }
                    }
                }
            }
        }

        for (uint64_t nt : net_drv_order) {
            if (!nt || check_const(nt)) continue;
            uint64_t drv_iob = iob_owner_of(net_drv[nt].inst);
            if (!drv_iob) continue;
            auto sit = net_sinks_inst.find(nt);
            if (sit == net_sinks_inst.end() || sit->second.empty()) continue;
            bool all_coloc = true;
            for (const auto& snk : sit->second) {
                if (iob_owner_of(snk.inst) == drv_iob) {
                    clear_pin_node(snk.pin);
                    co_ioblk_sink_pins.insert(snk.pin);
                } else {
                    all_coloc = false;
                }
            }
            if (all_coloc) intra_ioblk_nets.insert(nt);
        }

        auto fn_setphy_pll = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setPhyPll@Pll@NETLIST@@QEAAXPEAVpll@2@@Z"));
        auto fn_sp2n_pll   = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Pll@NETLIST@@UEAAXXZ"));
        auto fn_get_pll_in = reinterpret_cast<uint64_t (*)(uint64_t, int16_t)>(gw_req("?getInNodeOfPll@Pll@NETLIST@@QEAAPEAVnode@2@F@Z"));
        for (uint64_t po : pll_objs) {
            int p_idx = pll_assign[po];
            uint64_t phy_p = pll_phys[p_idx];
            auto [rp_r, cp_c] = pll_rcs[p_idx];
            fn_setphy(po, phy_p);
            fn_setlogic(phy_p, po);
            fn_setphy_pll(po, phy_p);
            fn_addgrid(fn_getgrid(chip, static_cast<int16_t>(rp_r), static_cast<int16_t>(cp_c)), po);
            fn_sp2n_pll(po);
            uint64_t rst_nd = fn_get_pll_in(po, 0);
            uint64_t clkin_nd = sess_node(rd_i16_mem(rst_nd), rd_i16_mem(rst_nd + 2), 124);
            for (const auto& pr : inst_in_pins[po]) {
                if (pr.lbl == "CLKIN") fn_setnd_pin(pr.pin, clkin_nd);
            }
        }

        auto fn_get_dll_loc = reinterpret_cast<uint64_t (*)(uint64_t, int16_t, int16_t)>(gw_req("?getDllByLoc@chip@NETLIST@@QEAAPEAVdll@2@FF@Z"));
        auto fn_setphy_dll  = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_opt("?setPhyDll@Dll@NETLIST@@QEAAXPEAVdll@2@@Z"));
        auto fn_sp2n_dll    = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Dll@NETLIST@@UEAAXXZ"));
        for (uint64_t do_o : dll_objs) {
            auto [rd, cd] = dll_assign[do_o];
            uint64_t phy_d = fn_get_dll_loc(chip, static_cast<int16_t>(rd), static_cast<int16_t>(cd));
            fn_setphy(do_o, phy_d);
            fn_setlogic(phy_d, do_o);
            if (fn_setphy_dll) fn_setphy_dll(do_o, phy_d);
            fn_addgrid(fn_getgrid(chip, static_cast<int16_t>(rd), static_cast<int16_t>(cd)), do_o);
            fn_sp2n_dll(do_o);

            const std::string* sp_s = cst.set_params.find(inst_name[do_o]);
            if (!sp_s) sp_s = cst.set_params.find(inst_leaf_name[do_o]);
            std::string usp = sp_s ? to_upper_ascii(*sp_s) : "";
            bool use_clkin5 = (usp.find("CLKIN_SEL=CLKIN5") != std::string::npos || usp.find("CLKIN_SEL = CLKIN5") != std::string::npos);

            int32_t dll_clkin_id = -1;
            if (rd == 110 && cd == 0 && (use_clkin5 || !sp_s)) {
                dll_clkin_id = bridge.coord_to_id(-2, 0, 840);
                if (dll_clkin_id < 0 && use_clkin5) dll_clkin_id = bridge.coord_to_id(110, -1, 458);
            }
            if (dll_clkin_id < 0) {
                if (rd == 110 && cd == 0) dll_clkin_id = bridge.coord_to_id(109, 2, 124);
                else if (rd == 0 && cd == 0) dll_clkin_id = bridge.coord_to_id(1, 2, 126);
                else if (rd == 0 && cd == 183) dll_clkin_id = bridge.coord_to_id(1, 181, 125);
                else if (rd == 110 && cd == 183) dll_clkin_id = bridge.coord_to_id(109, 181, 124);
            }
            if (dll_clkin_id >= 0) {
                uint64_t dll_clkin_ptr = bridge.id_to_ptr(dll_clkin_id);
                for (const auto& pr : inst_in_pins[do_o]) {
                    if (pr.lbl == "CLKIN") fn_setnd_pin(pr.pin, dll_clkin_ptr);
                }
            }
        }

        auto fn_setphy_clksrc = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_opt("?setPhyClksrc@Clksrc@NETLIST@@QEAAXPEAVclksrc@2@@Z"));
        auto fn_sp2n_clksrc   = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Clksrc@NETLIST@@UEAAXXZ"));
        auto fn_getnodes_sta  = reinterpret_cast<bool (*)(uint64_t, void*, uint64_t)>(gw_req("?getNodebyPinForSta@Pin@NETLIST@@QEAA_NAEAV?$vector@PEAVnode@NETLIST@@V?$allocator@PEAVnode@NETLIST@@@std@@@std@@PEAVglobalMem@@@Z"));
        auto collect_pin_nodes = [&](uint64_t p_ptr) -> std::vector<uint64_t> {
            std::vector<uint64_t> res;
            if (!p_ptr) return res;
            GwRawVec nv{};
            fn_getnodes_sta(p_ptr, &nv, mem);
            for (uint64_t a = nv.begin; a < nv.end; a += 8) {
                uint64_t nd = rd_ptr(a);
                if (nd) res.push_back(nd);
            }
            if (res.empty()) {
                uint64_t nd = rd_ptr(p_ptr + 0x18);
                if (nd) res.push_back(nd);
            }
            return res;
        };
        auto bfs_clk_nodes = [&](const std::vector<uint64_t>& starts, int max_depth = 12) -> std::unordered_set<uint64_t> {
            std::unordered_set<uint64_t> vis;
            std::deque<std::pair<uint64_t, int>> q;
            for (uint64_t s : starts) if (s && vis.insert(s).second) q.emplace_back(s, 0);
            while (!q.empty()) {
                auto [u, d] = q.front();
                q.pop_front();
                if (d >= max_depth) continue;
                int16_t cnt = rd_i16_mem(u + 6);
                for (int16_t k = 0; k < cnt; ++k) {
                    uint64_t v = fn_getout(u, k);
                    if (v && vis.insert(v).second) q.emplace_back(v, d + 1);
                }
            }
            return vis;
        };

        // 1) Bind explicitly CST-constrained CLKDIVs first so their HCLKIN nodes are fixed
        for (uint64_t co : clkdiv_objs) {
            if (!clkdiv_assign.count(co)) continue;
            uint64_t phy_cd = hclk_slots[clkdiv_assign[co]].phy;
            fn_setphy(co, phy_cd);
            fn_setlogic(phy_cd, co);
            if (fn_setphy_clksrc) fn_setphy_clksrc(co, phy_cd);
            fn_sp2n_clksrc(co);
        }

        // 2) Assign & bind DHCE/DHCEN instances using exact RRG clock reachability
        if (!dhcen_objs.empty()) {
            auto fn_sp2n_dhcen = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Dhcen@NETLIST@@UEAAXXZ"));
            for (uint64_t dho : dhcen_objs) {
                std::pair<int, int> ref_rc{82, 0};
                std::vector<uint64_t> clkin_srcs;
                for (const auto& ipr : inst_in_pins[dho]) {
                    if (ipr.lbl == "CLKIN" && net_drv.count(ipr.net)) {
                        resolve_clk_src_rc(net_drv[ipr.net].inst, ref_rc);
                        clkin_srcs = collect_pin_nodes(net_drv[ipr.net].pin);
                    }
                }
                std::unordered_set<uint64_t> clkin_reach = bfs_clk_nodes(clkin_srcs, 12);

                std::vector<std::vector<uint64_t>> out_sink_groups;
                bool needs_unconstrained_cd = false;
                for (const auto& opr : inst_out_pins[dho]) {
                    if (opr.lbl == "CLKOUT" && opr.net && net_sinks_inst.count(opr.net)) {
                        for (const auto& snk : net_sinks_inst[opr.net]) {
                            int st = inst_type[snk.inst];
                            if (st == 13 && inst_sub[snk.inst] == 101) {
                                if (clkdiv_assign.count(snk.inst)) {
                                    uint64_t pcd = hclk_slots[clkdiv_assign[snk.inst]].phy;
                                    if (pcd && rd_ptr(pcd + 0x18)) out_sink_groups.push_back({rd_ptr(pcd + 0x18)});
                                } else {
                                    needs_unconstrained_cd = true;
                                }
                            } else {
                                auto s_nds = collect_pin_nodes(snk.pin);
                                if (!s_nds.empty()) out_sink_groups.push_back(std::move(s_nds));
                            }
                        }
                    }
                }

                std::vector<int> hi_order;
                for (int hi = 0; hi < 6; ++hi) if (dhcen_slots.count({hi, 0})) hi_order.push_back(hi);
                std::sort(hi_order.begin(), hi_order.end(), [&](int a, int b) {
                    const auto& sa = dhcen_slots[{a, 0}];
                    const auto& sb = dhcen_slots[{b, 0}];
                    int da = std::abs(sa.r - ref_rc.first) + std::abs(sa.c - ref_rc.second);
                    int db = std::abs(sb.r - ref_rc.first) + std::abs(sb.c - ref_rc.second);
                    return da != db ? (da < db) : (a < b);
                });

                static const int DS_ORDER[12] = {0, 1, 2, 3, 8, 9, 10, 11, 4, 5, 6, 7};
                std::pair<int, int> chosen{-1, -1};
                for (int hi : hi_order) {
                    for (int k = 0; k < 12; ++k) {
                        std::pair<int, int> cand{hi, DS_ORDER[k]};
                        auto dit = dhcen_slots.find(cand);
                        if (dit == dhcen_slots.end() || used_dhcen.count(cand)) continue;
                        uint64_t phy_dh = dit->second.phy;
                        uint64_t nd_in  = rd_ptr(phy_dh + 0x18);
                        uint64_t nd_out = rd_ptr(phy_dh + 0x30);
                        if (!clkin_reach.empty() && (!nd_in || !clkin_reach.count(nd_in))) continue;
                        auto dh_reach = bfs_clk_nodes(nd_out ? std::vector<uint64_t>{nd_out} : std::vector<uint64_t>{}, 12);
                        bool sinks_ok = true;
                        for (const auto& grp : out_sink_groups) {
                            bool any_g = false;
                            for (uint64_t gnd : grp) if (dh_reach.count(gnd)) { any_g = true; break; }
                            if (!any_g) { sinks_ok = false; break; }
                        }
                        if (!sinks_ok) continue;
                        if (needs_unconstrained_cd) {
                            bool cd_ok = false;
                            for (const auto& [kh_cd, sl_cd] : hclk_slots) {
                                if (used_hclk.count(kh_cd)) continue;
                                uint64_t cd_in = rd_ptr(sl_cd.phy + 0x18);
                                if (cd_in && dh_reach.count(cd_in)) { cd_ok = true; break; }
                            }
                            if (!cd_ok) continue;
                        }
                        chosen = cand;
                        break;
                    }
                    if (chosen.first >= 0) break;
                }
                if (chosen.first < 0) {
                    for (int hi : hi_order) {
                        for (int k = 0; k < 12; ++k) {
                            std::pair<int, int> cand{hi, DS_ORDER[k]};
                            if (dhcen_slots.count(cand) && !used_dhcen.count(cand)) { chosen = cand; break; }
                        }
                        if (chosen.first >= 0) break;
                    }
                }
                if (chosen.first < 0) throw std::runtime_error("No available DHCE/DHCEN physical slot for " + inst_name[dho]);
                dhcen_assign[dho] = chosen;
                used_dhcen.insert(chosen);

                uint64_t phy_dh = dhcen_slots[chosen].phy;
                fn_setphy(dho, phy_dh);
                fn_setlogic(phy_dh, dho);
                fn_sp2n_dhcen(dho);
                uint64_t nd_clkin  = rd_ptr(phy_dh + 0x18);
                uint64_t nd_cen    = rd_ptr(phy_dh + 0x28);
                uint64_t nd_clkout = rd_ptr(phy_dh + 0x30);
                for (const auto& pr : inst_in_pins[dho]) {
                    if (pr.lbl == "CLKIN" && nd_clkin && rd_ptr(pr.pin + 0x18) == 0) fn_setnd_pin(pr.pin, nd_clkin);
                    else if (pr.lbl == "CEN" && nd_cen && rd_ptr(pr.pin + 0x18) == 0) fn_setnd_pin(pr.pin, nd_cen);
                }
                for (const auto& pr : inst_out_pins[dho]) {
                    if (pr.lbl == "CLKOUT" && nd_clkout && rd_ptr(pr.pin + 0x18) == 0) fn_setnd_pin(pr.pin, nd_clkout);
                }
            }
        }

        // 3) Assign & bind remaining unconstrained CLKDIVs using RRG reachability from upstream HCLKIN driver
        for (uint64_t co : clkdiv_objs) {
            if (clkdiv_assign.count(co)) continue;
            std::pair<int, int> ref_rc{82, 0};
            bool found_ref = false;
            std::vector<uint64_t> hclkin_srcs;
            for (const auto& pr : inst_in_pins[co]) {
                if (pr.lbl == "HCLKIN" && net_drv.count(pr.net)) {
                    uint64_t drv_o = net_drv[pr.net].inst;
                    if (dhcen_assign.count(drv_o)) {
                        const auto& ds = dhcen_slots[dhcen_assign[drv_o]];
                        ref_rc = {ds.r, ds.c};
                        found_ref = true;
                    } else {
                        found_ref = resolve_clk_src_rc(drv_o, ref_rc);
                    }
                    hclkin_srcs = collect_pin_nodes(net_drv[pr.net].pin);
                }
            }
            if (!found_ref) {
                for (const auto& opr : inst_out_pins[co]) {
                    if (opr.lbl == "CLKOUT" && opr.net && net_sinks_inst.count(opr.net)) {
                        for (const auto& snk : net_sinks_inst[opr.net]) {
                            if (inst_type[snk.inst] == 7 && iol_to_iob.count(snk.inst)) {
                                const auto& ia = iob_assign[iol_to_iob[snk.inst]];
                                ref_rc = {ia.r, ia.c};
                                found_ref = true;
                                break;
                            }
                        }
                    }
                }
            }
            auto hclkin_reach = bfs_clk_nodes(hclkin_srcs, 12);
            std::vector<std::pair<int, int>> cd_cands;
            for (const auto& [kh, _] : hclk_slots) if (!used_hclk.count(kh)) cd_cands.push_back(kh);
            std::sort(cd_cands.begin(), cd_cands.end(), [&](const auto& a, const auto& b) {
                const auto& sa = hclk_slots[a];
                const auto& sb = hclk_slots[b];
                int da = std::abs(sa.r - ref_rc.first) + std::abs(sa.c - ref_rc.second);
                int db = std::abs(sb.r - ref_rc.first) + std::abs(sb.c - ref_rc.second);
                return da != db ? (da < db) : (a < b);
            });
            std::pair<int, int> best_kh{-1, -1};
            for (const auto& kh : cd_cands) {
                uint64_t cd_in = rd_ptr(hclk_slots[kh].phy + 0x18);
                if (hclkin_reach.empty() || (cd_in && hclkin_reach.count(cd_in))) { best_kh = kh; break; }
            }
            if (best_kh.first < 0 && !cd_cands.empty()) best_kh = cd_cands.front();
            if (best_kh.first < 0) throw std::runtime_error("No available CLKDIV physical slot for " + inst_name[co]);
            clkdiv_assign[co] = best_kh;
            used_hclk.insert(best_kh);

            uint64_t phy_cd = hclk_slots[best_kh].phy;
            fn_setphy(co, phy_cd);
            fn_setlogic(phy_cd, co);
            if (fn_setphy_clksrc) fn_setphy_clksrc(co, phy_cd);
            fn_sp2n_clksrc(co);
        }
        for (uint64_t dqs_o : dqs_objs) {
            const auto& dc = dqs_assign[dqs_o];
            fn_setphy(dqs_o, dc.phy);
            fn_setlogic(dc.phy, dqs_o);
            if (fn_setphy_clksrc) fn_setphy_clksrc(dqs_o, dc.phy);
            fn_addgrid(fn_getgrid(chip, static_cast<int16_t>(dc.r), static_cast<int16_t>(dc.c)), dqs_o);
            fn_sp2n_clksrc(dqs_o);
        }

        auto fn_setphy_jt = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_opt("?setPhyJtag@Jtag@NETLIST@@QEAAXPEAVjtag@2@@Z"));
        auto fn_sp2n_jt   = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Jtag@NETLIST@@UEAAXXZ"));
        auto fn_get_jt    = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getJtag@chip@NETLIST@@QEAAPEAVjtag@2@XZ"));
        for (uint64_t jt_o : jtag_objs) {
            uint64_t phy_jt = fn_get_jt(chip);
            int16_t rj = rd_i16_mem(phy_jt + 0x12), cj = rd_i16_mem(phy_jt + 0x14);
            fn_setphy(jt_o, phy_jt);
            fn_setlogic(phy_jt, jt_o);
            if (fn_setphy_jt) fn_setphy_jt(jt_o, phy_jt);
            if (rj >= 0 && rj < 111 && cj >= 0 && cj < 184 && fn_getgrid(chip, rj, cj)) {
                fn_addgrid(fn_getgrid(chip, rj, cj), jt_o);
            }
            fn_sp2n_jt(jt_o);
        }

        auto fn_sp2n_gsr = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Gsr@NETLIST@@UEAAXXZ"));
        auto fn_get_gsr  = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getGsr@chip@NETLIST@@QEAAPEAVgsr@2@XZ"));
        for (uint64_t gsr_o : gsr_objs) {
            uint64_t phy_gsr = fn_get_gsr(chip);
            fn_setphy(gsr_o, phy_gsr);
            fn_setlogic(phy_gsr, gsr_o);
            fn_sp2n_gsr(gsr_o);
        }

        if (!osc_objs.empty()) {
            auto fn_get_osc = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getOsc@chip@NETLIST@@QEAAPEAVclksrc@2@XZ"));
            for (uint64_t oo : osc_objs) {
                uint64_t phy_osc = fn_get_osc(chip);
                if (!phy_osc) throw std::runtime_error("Device lacks physical OSC site for " + inst_name[oo]);
                int16_t ro = rd_i16_mem(phy_osc + 0x12), co = rd_i16_mem(phy_osc + 0x14);
                fn_setphy(oo, phy_osc);
                fn_setlogic(phy_osc, oo);
                if (fn_setphy_clksrc) fn_setphy_clksrc(oo, phy_osc);
                if (ro >= 0 && ro < 111 && co >= 0 && co < 184 && fn_getgrid(chip, ro, co)) {
                    fn_addgrid(fn_getgrid(chip, ro, co), oo);
                }
                fn_sp2n_clksrc(oo);
            }
        }

        std::unordered_map<uint64_t, bool> gtr_pos_flag;
        if (!gtr_objs.empty()) {
            auto fn_get_serdes = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getSerdes@chip@NETLIST@@QEBAPEAVserdes@2@XZ"));
            auto fn_pos_gtr    = reinterpret_cast<bool (*)(uint64_t)>(gw_req("?getPositionFlag@Gtr12_quad_db@NETLIST@@QEAA_NXZ"));
            auto fn_sp2n_gtr   = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Gtr12_quad_db@NETLIST@@UEAAXXZ"));
            uint64_t s_ptr = fn_get_serdes(chip);
            if (!s_ptr) throw std::runtime_error("Device lacks physical SerDes quad sites");
            for (uint64_t go : gtr_objs) {
                bool pos = fn_pos_gtr(go);
                gtr_pos_flag[go] = pos;
                uint64_t phy_q = rd_ptr(s_ptr + (pos ? 8ULL : 0ULL));
                if (!phy_q) throw std::runtime_error("Missing physical GTR12 quad site for " + inst_name[go]);
                fn_setphy(go, phy_q);
                fn_setlogic(phy_q, go);
                wr_ptr(go + 0x48, phy_q);
                fn_sp2n_gtr(go);
            }
        }

        if (!ae350_objs.empty()) {
            auto fn_get_ae350  = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getAe350Soc@chip@NETLIST@@QEAAPEAVae350_soc@2@XZ"));
            auto fn_sp2n_ae350 = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Ae350_soc@NETLIST@@UEAAXXZ"));
            uint64_t phy_ae = fn_get_ae350(chip);
            if (!phy_ae) throw std::runtime_error("Device lacks physical AE350_SOC site");
            for (uint64_t ao : ae350_objs) {
                fn_setphy(ao, phy_ae);
                fn_setlogic(phy_ae, ao);
                wr_ptr(ao + 0x48, phy_ae);
                fn_sp2n_ae350(ao);
            }
        }

        uint64_t getnodes_sta_addr = reinterpret_cast<uint64_t>(gw_req("?getNodebyPinForSta@Pin@NETLIST@@QEAA_NAEAV?$vector@PEAVnode@NETLIST@@V?$allocator@PEAVnode@NETLIST@@@std@@@std@@PEAVglobalMem@@@Z"));
        uint64_t getnodes_pin_addr = reinterpret_cast<uint64_t>(gw_req("?getNodebyPin@Pin@NETLIST@@QEAA_NAEAV?$vector@PEAVnode@NETLIST@@V?$allocator@PEAVnode@NETLIST@@@std@@@std@@PEAVglobalMem@@@Z"));

        std::vector<uint64_t> fixed_all_objs;
        for (const auto* vec : {&iob_objs, &iol_objs, &pll_objs, &dll_objs, &clkdiv_objs, &dhcen_objs, &dqs_objs, &osc_objs, &gtr_objs, &ae350_objs, &jtag_objs, &gsr_objs}) {
            fixed_all_objs.insert(fixed_all_objs.end(), vec->begin(), vec->end());
        }
        std::vector<uint64_t> fx_pins;
        std::vector<std::pair<uint64_t, uint64_t>> fx_pin_owners;
        for (uint64_t fo : fixed_all_objs) {
            for (const auto* plist : {&inst_in_pins[fo], &inst_out_pins[fo]}) {
                for (const auto& pr : *plist) {
                    if (iob_ext_pins.count(pr.pin) || co_ioblk_sink_pins.count(pr.pin) || intra_ioblk_nets.count(pr.net)) continue;
                    fx_pins.push_back(pr.pin);
                    fx_pin_owners.emplace_back(fo, pr.net);
                }
            }
        }

        std::vector<uint8_t> fixed_reserved_cfu_grid(111 * 184, 0U);
        size_t fixed_reserved_cfu_cnt = 0;
        std::unordered_map<uint64_t, std::vector<std::pair<int, int>>> fx_inst_rcs;
        if (!fx_pins.empty()) {
            int32_t nfq = static_cast<int32_t>(fx_pins.size());
            std::vector<int32_t> fq_off(nfq + 1, 0);
            int32_t max_fq = std::max<int32_t>(4096, nfq * 4);
            std::vector<int32_t> fq_nid(max_fq, -1);
            std::vector<uint64_t> fq_net(nfq, 0);
            int32_t tot_fq = bridge.batch_query_pins_all(mem, getnodes_sta_addr, getnodes_pin_addr, fx_pins.data(), nfq, fq_off.data(), fq_nid.data(), max_fq, fq_net.data());
            if (tot_fq > max_fq) {
                max_fq = tot_fq + 256;
                fq_nid.assign(max_fq, -1);
                bridge.batch_query_pins_all(mem, getnodes_sta_addr, getnodes_pin_addr, fx_pins.data(), nfq, fq_off.data(), fq_nid.data(), max_fq, fq_net.data());
            }
            int16_t rc_tmp[3]{};
            for (int32_t idx_f = 0; idx_f < nfq; ++idx_f) {
                auto [fo, nt] = fx_pin_owners[idx_f];
                for (int32_t k = fq_off[idx_f]; k < fq_off[idx_f + 1]; ++k) {
                    int32_t nid = fq_nid[k];
                    if (nid >= 0) {
                        bridge.id_coord(nid, rc_tmp);
                        int nr = rc_tmp[0], nc = rc_tmp[1];
                        if (nr >= 1 && nr <= 109 && nc >= 1 && nc <= 182) {
                            if (!fixed_reserved_cfu_grid[nr * 184 + nc]) {
                                fixed_reserved_cfu_grid[nr * 184 + nc] = 1U;
                                ++fixed_reserved_cfu_cnt;
                            }
                            if (nt && !check_const(nt)) fx_inst_rcs[fo].emplace_back(nr, nc);
                        }
                    }
                }
            }
        }

        auto fixed_anchor_rc = [&](const std::vector<uint64_t>& members, int fb_r, int fb_c) -> std::pair<double, double> {
            double sr = 0.0, sc = 0.0;
            size_t cnt = 0;
            for (uint64_t m : members) {
                if (auto it = fx_inst_rcs.find(m); it != fx_inst_rcs.end()) {
                    for (auto [pr_r, pr_c] : it->second) { sr += pr_r; sc += pr_c; ++cnt; }
                }
            }
            if (cnt > 0) return {sr / static_cast<double>(cnt), sc / static_cast<double>(cnt)};
            return {static_cast<double>(fb_r), static_cast<double>(fb_c)};
        };

        std::fprintf(stdout, "[ownpr] Bound %zu fixed blocks early; reserved %zu overlapping CFU tiles\n",
                     fixed_all_objs.size(), fixed_reserved_cfu_cnt);
        std::fflush(stdout);

        // =====================================================================
        // 2. Build Placement Units (MUX_TREE, ALU_CHAIN, LUT+FF, FF, LUT, BSRAM, DSP, FIXED)
        // =====================================================================
        std::unordered_map<uint64_t, int32_t> c60_net_map;
        auto get_c60_id = [&](uint64_t nt) -> int32_t {
            if (!nt || check_const(nt)) return -1;
            auto [it, _] = c60_net_map.emplace(nt, static_cast<int32_t>(c60_net_map.size()));
            return it->second;
        };
        auto pad_c60_pair = [](const std::vector<int32_t>& cids) -> std::pair<int32_t, int32_t> {
            std::vector<int32_t> uniq;
            for (int32_t c : cids) {
                if (c >= 0 && std::find(uniq.begin(), uniq.end(), c) == uniq.end()) uniq.push_back(c);
            }
            if (uniq.size() > 2) throw std::runtime_error("Non-exclusive unit exceeds 2 shared idx 60..63 nets");
            return {uniq.size() >= 1 ? uniq[0] : -1, uniq.size() >= 2 ? uniq[1] : -1};
        };

        struct CsKey {
            int sb = 0; uint64_t clk = 0, ce = 0, sr = 0;
            bool operator==(const CsKey& o) const noexcept { return sb == o.sb && clk == o.clk && ce == o.ce && sr == o.sr; }
        };
        struct CsKeyHash {
            size_t operator()(const CsKey& k) const noexcept {
                size_t h = static_cast<size_t>(k.sb);
                h ^= std::hash<uint64_t>{}(k.clk) * 0x9e3779b97f4a7c15ULL;
                h ^= std::hash<uint64_t>{}(k.ce)  * 0xbf58476d1ce4e5b9ULL;
                h ^= std::hash<uint64_t>{}(k.sr)  * 0x94d049bb133111ebULL;
                return h;
            }
        };

        std::unordered_map<uint64_t, int32_t> reg_cs_id;
        std::unordered_map<uint64_t, std::pair<int32_t, int32_t>> reg_c60_pair;
        std::unordered_map<CsKey, int32_t, CsKeyHash> cs_map;
        std::unordered_map<uint64_t, uint64_t> d_net_to_reg;
        std::unordered_map<uint64_t, bool> reg_clk_inv;
        auto fn_has_inv_pin = reinterpret_cast<bool (*)(uint64_t)>(gw_req("?hasInvertInPin@Pin@NETLIST@@QEAA_NXZ"));

        for (uint64_t o : insts) {
            int t = inst_type[o];
            if (t != 50 && t != 70) continue;
            uint64_t clk_nt = 0, ce_nt = 0, sr_nt = 0, d_nt = 0;
            bool clk_inv = false;
            std::vector<int32_t> c60_cands;
            for (const auto& pr : inst_in_pins[o]) {
                if (pr.lbl == "CLK" || (t == 70 && pr.lbl == "G")) {
                    clk_nt = pr.net;
                    if (pr.pin && fn_has_inv_pin(pr.pin)) clk_inv = true;
                }
                else if (pr.lbl == "CE" || (t == 70 && pr.lbl == "GE")) ce_nt = pr.net;
                else if (pr.lbl == "D") d_nt = pr.net;
                else if (!sr_nt && (pr.lbl == "RESET" || pr.lbl == "CLEAR" || pr.lbl == "SET" || pr.lbl == "PRESET" || pr.lbl == "LSR")) sr_nt = pr.net;
                if (pr.lbl != "D" && pr.lbl != "CLK" && !(t == 70 && pr.lbl == "G")) c60_cands.push_back(get_c60_id(pr.net));
            }
            reg_clk_inv[o] = clk_inv;
            int eff_sb = (t == 70 ? (1000 + inst_sub[o]) : inst_sub[o]) + (clk_inv ? 4000 : 0);
            CsKey ck{eff_sb, clk_nt, ce_nt, sr_nt};
            auto [cit, _] = cs_map.emplace(ck, static_cast<int32_t>(cs_map.size()));
            reg_cs_id[o] = cit->second;
            reg_c60_pair[o] = pad_c60_pair(c60_cands);
            if (t == 50 && d_nt && !check_const(d_nt) && net_in_cnt[d_nt] == 1) d_net_to_reg[d_nt] = o;
        }

        std::unordered_set<uint64_t> claimed_insts;

        // 2a. MUX_TREE units
        struct MuxChildInfo {
            uint64_t d0_o = 0, d1_o = 0, d0_p = 0, i0_p = 0, i0_nt = 0, d1_p = 0, i1_p = 0, i1_nt = 0;
        };
        struct MuxLink { uint64_t src_p = 0, snk_p = 0, nt = 0; };
        struct MuxTreeRec {
            uint64_t root = 0;
            int lvl = 0;
            std::vector<uint64_t> members;
            std::vector<MuxLink> links;
        };
        std::vector<uint64_t> mux_objs;
        for (uint64_t o : insts) if (inst_type[o] == 2) mux_objs.push_back(o);
        std::unordered_set<uint64_t> mux_as_child;
        std::unordered_map<uint64_t, MuxChildInfo> mux_children_map;
        for (uint64_t mo : mux_objs) {
            uint64_t i0_p = 0, i0_nt = 0, i1_p = 0, i1_nt = 0;
            for (const auto& pr : inst_in_pins[mo]) {
                if (pr.lbl == "I0") { i0_p = pr.pin; i0_nt = pr.net; }
                else if (pr.lbl == "I1") { i1_p = pr.pin; i1_nt = pr.net; }
            }
            if (!net_drv.count(i0_nt) || !net_drv.count(i1_nt)) {
                throw std::runtime_error("Mux2 " + inst_name[mo] + " missing dedicated I0/I1 driver");
            }
            auto d0 = net_drv[i0_nt], d1 = net_drv[i1_nt];
            mux_children_map[mo] = {d0.inst, d1.inst, d0.pin, i0_p, i0_nt, d1.pin, i1_p, i1_nt};
            if (inst_type[d0.inst] == 2) mux_as_child.insert(d0.inst);
            if (inst_type[d1.inst] == 2) mux_as_child.insert(d1.inst);
        }

        std::vector<MuxTreeRec> mux_tree_records;
        for (uint64_t root_m : mux_objs) {
            if (mux_as_child.count(root_m)) continue;
            int lvl_r = inst_sub[root_m] - 28;
            if (lvl_r != 5 && lvl_r != 6 && lvl_r != 7) {
                throw std::runtime_error("Unsupported Mux2 subtype on " + inst_name[root_m]);
            }
            MuxTreeRec rec{root_m, lvl_r, {}, {}};
            std::function<void(uint64_t, int)> collect_mux = [&](uint64_t cur_m, int lvl) {
                rec.members.push_back(cur_m);
                claimed_insts.insert(cur_m);
                const auto& mc = mux_children_map[cur_m];
                rec.links.push_back({mc.d0_p, mc.i0_p, mc.i0_nt});
                rec.links.push_back({mc.d1_p, mc.i1_p, mc.i1_nt});
                if (lvl == 5) {
                    if (inst_type[mc.d0_o] != 1 || inst_type[mc.d1_o] != 1 ||
                        claimed_insts.count(mc.d0_o) || claimed_insts.count(mc.d1_o) || mc.d0_o == mc.d1_o) {
                        throw std::runtime_error("Invalid MUX2_LUT5 leaf LUTs under " + inst_name[cur_m]);
                    }
                    rec.members.push_back(mc.d0_o);
                    rec.members.push_back(mc.d1_o);
                    claimed_insts.insert(mc.d0_o);
                    claimed_insts.insert(mc.d1_o);
                } else {
                    if (inst_type[mc.d0_o] != 2 || inst_type[mc.d1_o] != 2 || mc.d0_o == mc.d1_o) {
                        throw std::runtime_error("Invalid MUX2 child muxes under " + inst_name[cur_m]);
                    }
                    collect_mux(mc.d0_o, lvl - 1);
                    collect_mux(mc.d1_o, lvl - 1);
                }
            };
            collect_mux(root_m, lvl_r);
            mux_tree_records.push_back(std::move(rec));
        }

        // 2b. ALU_CHAIN units
        struct AluChainRec {
            std::vector<uint64_t> chain;
            std::vector<uint64_t> paired_regs; // 0 = None
            std::vector<uint64_t> internal_carries;
            uint64_t cin_fab_nt = 0;
            uint64_t cout_fab_nt = 0;
            uint64_t unused_tail_co = 0;
        };
        std::vector<uint64_t> alu_objs;
        for (uint64_t o : insts) if (inst_type[o] == 106) alu_objs.push_back(o);
        std::unordered_map<uint64_t, uint64_t> alu_cin, alu_cout, alu_sum, cout_to_alu, cin_to_alu;
        for (uint64_t ao : alu_objs) {
            for (const auto& pr : inst_in_pins[ao]) if (pr.lbl == "CIN") alu_cin[ao] = pr.net;
            for (const auto& pr : inst_out_pins[ao]) {
                if (pr.lbl == "COUT") alu_cout[ao] = pr.net;
                else if (pr.lbl == "SUM") alu_sum[ao] = pr.net;
            }
        }
        for (uint64_t ao : alu_objs) {
            uint64_t ci = alu_cin.count(ao) ? alu_cin[ao] : 0;
            if (ci && !check_const(ci) && net_in_cnt[ci] == 1) cin_to_alu[ci] = ao;
        }
        for (uint64_t ao : alu_objs) {
            uint64_t co = alu_cout.count(ao) ? alu_cout[ao] : 0;
            if (co && !check_const(co) && net_in_cnt[co] == 1 && cin_to_alu.count(co)) cout_to_alu[co] = ao;
        }

        std::vector<AluChainRec> alu_chain_records;
        for (uint64_t head_a : alu_objs) {
            uint64_t ci_head = alu_cin.count(head_a) ? alu_cin[head_a] : 0;
            if (ci_head && cout_to_alu.count(ci_head)) continue;
            AluChainRec rec;
            uint64_t cur = head_a;
            while (cur) {
                rec.chain.push_back(cur);
                claimed_insts.insert(cur);
                uint64_t co = alu_cout.count(cur) ? alu_cout[cur] : 0;
                if (co && cout_to_alu.count(co) && cout_to_alu[co] == cur && cin_to_alu.count(co)) {
                    rec.internal_carries.push_back(co);
                    cur = cin_to_alu[co];
                } else {
                    cur = 0;
                }
            }
            rec.cin_fab_nt = (ci_head && !check_const(ci_head)) ? ci_head : 0;
            uint64_t tail_co = alu_cout.count(rec.chain.back()) ? alu_cout[rec.chain.back()] : 0;
            rec.cout_fab_nt = (tail_co && net_in_cnt[tail_co] > 0) ? tail_co : 0;
            rec.unused_tail_co = (tail_co && net_in_cnt[tail_co] == 0) ? tail_co : 0;
            rec.paired_regs.assign(rec.chain.size(), 0ULL);

            std::unordered_map<size_t, std::array<int32_t, 3>> tile_dom_cs;
            std::unordered_map<size_t, std::set<int32_t>> tile_c60_sets;
            for (size_t k = 0; k < rec.chain.size(); ++k) {
                uint64_t ao = rec.chain[k];
                uint64_t s_nt = alu_sum.count(ao) ? alu_sum[ao] : 0;
                uint64_t ro = (s_nt && d_net_to_reg.count(s_nt)) ? d_net_to_reg[s_nt] : 0;
                if (ro && !claimed_insts.count(ro)) {
                    size_t t_idx = k / 6;
                    size_t sl = k % 6;
                    size_t dom = (sl < 2) ? 0 : ((sl < 4) ? 1 : 2);
                    if (!tile_dom_cs.count(t_idx)) tile_dom_cs[t_idx] = {-1, -1, -1};
                    int32_t cs = reg_cs_id[ro];
                    int32_t cur_cs = tile_dom_cs[t_idx][dom];
                    auto cand_c60 = tile_c60_sets[t_idx];
                    auto [c0_id, c1_id] = reg_c60_pair[ro];
                    if (c0_id >= 0) cand_c60.insert(c0_id);
                    if (c1_id >= 0) cand_c60.insert(c1_id);
                    if ((cur_cs == -1 || cur_cs == cs) && cand_c60.size() <= 3) {
                        tile_dom_cs[t_idx][dom] = cs;
                        tile_c60_sets[t_idx] = std::move(cand_c60);
                        rec.paired_regs[k] = ro;
                        claimed_insts.insert(ro);
                    }
                }
            }
            alu_chain_records.push_back(std::move(rec));
        }

        // 2c. LUT+FF pairs, standalone FFs, standalone LUTs
        struct LutFfRec { uint64_t lo = 0, ro = 0, f_nt = 0; };
        std::vector<LutFfRec> lut_ff_records;
        std::vector<uint64_t> standalone_luts, standalone_regs;
        for (uint64_t o : insts) {
            if (inst_type[o] != 1 || claimed_insts.count(o)) continue;
            claimed_insts.insert(o);
            uint64_t f_nt = 0;
            for (const auto& pr : inst_out_pins[o]) if (pr.lbl == "F") { f_nt = pr.net; break; }
            uint64_t ro = (f_nt && d_net_to_reg.count(f_nt)) ? d_net_to_reg[f_nt] : 0;
            if (ro && !claimed_insts.count(ro)) {
                claimed_insts.insert(ro);
                lut_ff_records.push_back({o, ro, f_nt});
            } else {
                standalone_luts.push_back(o);
            }
        }
        for (uint64_t o : insts) {
            if (inst_type[o] == 50 && !claimed_insts.count(o)) {
                claimed_insts.insert(o);
                standalone_regs.push_back(o);
            }
        }
        std::vector<uint64_t> standalone_dls, ram16_objs, rom16_objs;
        for (uint64_t o : insts) {
            if (inst_type[o] == 70 && !claimed_insts.count(o)) {
                claimed_insts.insert(o);
                standalone_dls.push_back(o);
            } else if (inst_type[o] == 6 && inst_sub[o] == 98 && !claimed_insts.count(o)) {
                claimed_insts.insert(o);
                rom16_objs.push_back(o);
            } else if (inst_type[o] == 6 && inst_sub[o] != 98 && !claimed_insts.count(o)) {
                claimed_insts.insert(o);
                ram16_objs.push_back(o);
            }
        }

        std::vector<uint64_t> bsram_objs, dsp_wide_objs, dsp_single_objs;
        for (uint64_t o : insts) {
            if (inst_type[o] == 5) bsram_objs.push_back(o);
            else if (inst_type[o] == 4 && inst_sub[o] == 188) dsp_wide_objs.push_back(o);
            else if (inst_type[o] == 4 && inst_sub[o] != 188) dsp_single_objs.push_back(o);
        }

        std::vector<int32_t> unit_kind, unit_sub, unit_cs, unit_c60, unit_chain_len, unit_flags;
        std::vector<float> init_r, init_c;
        std::unordered_map<uint64_t, int32_t> inst_to_unit;

        auto add_unit = [&](int32_t kind, int32_t sub, int32_t cs, std::pair<int32_t, int32_t> c60,
                            int32_t clen, int32_t ufl, double r0, double c0, const std::vector<uint64_t>& members) -> int32_t {
            int32_t uid = static_cast<int32_t>(unit_kind.size());
            unit_kind.push_back(kind);
            unit_sub.push_back(sub);
            unit_cs.push_back(cs);
            unit_c60.push_back(c60.first);
            unit_c60.push_back(c60.second);
            unit_chain_len.push_back(clen);
            unit_flags.push_back(ufl);
            init_r.push_back(static_cast<float>(r0));
            init_c.push_back(static_cast<float>(c0));
            for (uint64_t m : members) if (m) inst_to_unit[m] = uid;
            return uid;
        };

        std::unordered_map<uint64_t, std::vector<uint64_t>> iob_to_iols;
        for (uint64_t iol_o : iol_objs) iob_to_iols[iol_to_iob[iol_o]].push_back(iol_o);
        for (uint64_t io_o : iob_objs) {
            const auto& ia = iob_assign[io_o];
            std::vector<uint64_t> mems{io_o};
            for (uint64_t il : iob_to_iols[io_o]) mems.push_back(il);
            auto [ar, ac] = fixed_anchor_rc(mems, ia.r, ia.c);
            add_unit(0, 0, -1, {-1, -1}, 0, 0, ar, ac, mems);
        }
        for (uint64_t po : pll_objs) {
            auto [rp_r, cp_c] = pll_rcs[pll_assign[po]];
            auto [ar, ac] = fixed_anchor_rc({po}, rp_r, cp_c);
            add_unit(0, 0, -1, {-1, -1}, 0, 0, ar, ac, {po});
        }
        for (uint64_t do_o : dll_objs) {
            auto [rd, cd] = dll_assign[do_o];
            auto [ar, ac] = fixed_anchor_rc({do_o}, rd, cd);
            add_unit(0, 0, -1, {-1, -1}, 0, 0, ar, ac, {do_o});
        }
        for (uint64_t co : clkdiv_objs) {
            const auto& hs = hclk_slots[clkdiv_assign[co]];
            auto [ar, ac] = fixed_anchor_rc({co}, hs.r, hs.c);
            add_unit(0, 0, -1, {-1, -1}, 0, 0, ar, ac, {co});
        }
        for (uint64_t dho : dhcen_objs) {
            const auto& ds = dhcen_slots[dhcen_assign[dho]];
            auto [ar, ac] = fixed_anchor_rc({dho}, ds.r, ds.c);
            add_unit(0, 0, -1, {-1, -1}, 0, 0, ar, ac, {dho});
        }
        for (uint64_t dqs_o : dqs_objs) {
            const auto& dc = dqs_assign[dqs_o];
            auto [ar, ac] = fixed_anchor_rc({dqs_o}, dc.r, dc.c);
            add_unit(0, 0, -1, {-1, -1}, 0, 0, ar, ac, {dqs_o});
        }
        for (uint64_t jt_o : jtag_objs) {
            uint64_t phy_jt = fn_get_jt(chip);
            int rj = std::max(0, std::min(110, static_cast<int>(rd_i16_mem(phy_jt + 0x12))));
            int cj = std::max(0, std::min(183, static_cast<int>(rd_i16_mem(phy_jt + 0x14))));
            auto [ar, ac] = fixed_anchor_rc({jt_o}, rj, cj);
            add_unit(0, 0, -1, {-1, -1}, 0, 0, ar, ac, {jt_o});
        }
        for (uint64_t oo : osc_objs) {
            auto [ar, ac] = fixed_anchor_rc({oo}, 100, 2);
            add_unit(0, 0, -1, {-1, -1}, 0, 0, ar, ac, {oo});
        }
        for (uint64_t go : gtr_objs) {
            auto [ar, ac] = fixed_anchor_rc({go}, 109, gtr_pos_flag[go] ? 135 : 45);
            add_unit(0, 0, -1, {-1, -1}, 0, 0, ar, ac, {go});
        }
        for (uint64_t ao : ae350_objs) {
            auto [ar, ac] = fixed_anchor_rc({ao}, 20, 150);
            add_unit(0, 0, -1, {-1, -1}, 0, 0, ar, ac, {ao});
        }

        std::vector<int32_t> bsram_uids, dsp_single_uids, dsp_wide_uids, alu_uids, ram16_uids, mux_uids, lut_ff_uids, reg_only_uids, dl_only_uids, lut_only_uids, rom16_uids;
        for (uint64_t bo : bsram_objs) bsram_uids.push_back(add_unit(1, inst_sub[bo], -1, {-1, -1}, 0, 0, 55.0, 92.0, {bo}));
        for (uint64_t do_o : dsp_single_objs) dsp_single_uids.push_back(add_unit(2, inst_sub[do_o], -1, {-1, -1}, 0, 0, 55.0, 92.0, {do_o}));
        for (uint64_t do_o : dsp_wide_objs) dsp_wide_uids.push_back(add_unit(3, inst_sub[do_o], -1, {-1, -1}, 0, 0, 55.0, 92.0, {do_o}));

        for (const auto& rec : alu_chain_records) {
            int32_t fl = (rec.cin_fab_nt ? 1 : 0) | (rec.cout_fab_nt ? 2 : 0);
            std::vector<uint64_t> mems = rec.chain;
            for (uint64_t r : rec.paired_regs) if (r) mems.push_back(r);
            alu_uids.push_back(add_unit(4, 0, -1, {-1, -1}, static_cast<int32_t>(rec.chain.size()), fl, 55.0, 92.0, mems));
        }
        for (uint64_t sso : ram16_objs) {
            ram16_uids.push_back(add_unit(4, 1, -1, {-1, -1}, 1, 0, 55.0, 92.0, {sso}));
        }
        for (const auto& rec : mux_tree_records) {
            std::pair<int32_t, int32_t> m_c60{-1, -1};
            if (rec.lvl < 7) {
                std::vector<int32_t> cands;
                for (uint64_t m : rec.members) {
                    if (inst_type[m] == 2 && inst_sub[m] == 33) {
                        for (const auto& pr : inst_in_pins[m]) if (pr.lbl == "S0") cands.push_back(get_c60_id(pr.net));
                    }
                }
                m_c60 = pad_c60_pair(cands);
            }
            mux_uids.push_back(add_unit(5, rec.lvl, -1, m_c60, 0, 0, 55.0, 92.0, rec.members));
        }
        for (const auto& lf : lut_ff_records) {
            lut_ff_uids.push_back(add_unit(6, 1, reg_cs_id[lf.ro], reg_c60_pair[lf.ro], 0, 0, 55.0, 92.0, {lf.lo, lf.ro}));
        }
        for (uint64_t ro : standalone_regs) {
            reg_only_uids.push_back(add_unit(6, 0, reg_cs_id[ro], reg_c60_pair[ro], 0, 0, 55.0, 92.0, {ro}));
        }
        for (uint64_t dlo : standalone_dls) {
            dl_only_uids.push_back(add_unit(6, 0, reg_cs_id[dlo], reg_c60_pair[dlo], 0, 0, 55.0, 92.0, {dlo}));
        }
        for (uint64_t lo : standalone_luts) {
            lut_only_uids.push_back(add_unit(7, 0, -1, {-1, -1}, 0, 0, 55.0, 92.0, {lo}));
        }
        for (uint64_t rmo : rom16_objs) {
            rom16_uids.push_back(add_unit(7, 0, -1, {-1, -1}, 0, 0, 55.0, 92.0, {rmo}));
        }

        int32_t num_units = static_cast<int32_t>(unit_kind.size());
        std::fprintf(stdout,
            "[ownpr] Packed into %d placement units: BSRAM=%zu, DSP_W=%zu, DSP_S=%zu, ALU_CHAIN=%zu, RAM16=%zu, MUX_TREE=%zu, LUT_FF=%zu, FF_ONLY=%zu, DL_ONLY=%zu, LUT_ONLY=%zu, ROM16=%zu\n",
            num_units, bsram_uids.size(), dsp_wide_uids.size(), dsp_single_uids.size(),
            alu_uids.size(), ram16_uids.size(), mux_uids.size(), lut_ff_uids.size(), reg_only_uids.size(), dl_only_uids.size(), lut_only_uids.size(), rom16_uids.size());
        std::fflush(stdout);

        // =====================================================================
        // 3. General SDC-Driven Topological Timing Criticality Estimation
        // =====================================================================
        auto inst_defparams = parse_module_scoped_defparams(read_file_text(path_join(out, "mapped.vg")));
        auto parse_param_num = [](const std::unordered_map<std::string, std::string>& pm, const std::string& key, double def_v = -1.0) -> double {
            auto it = pm.find(key);
            if (it == pm.end()) return def_v;
            std::string s = trim(it->second);
            if (s.size() >= 2 && s.front() == '"' && s.back() == '"') s = trim(std::string_view(s).substr(1, s.size() - 2));
            if (s.empty()) return def_v;
            try {
                if (s.find('\'') != std::string::npos) {
                    auto [_, uv] = parse_verilog_literal_u64(s);
                    return static_cast<double>(uv);
                }
                return std::stod(s);
            } catch (...) {
                return def_v;
            }
        };

        SdcParsed sdc = parse_sdc_clocks_and_exceptions(read_file_text(path_join(out, "project.sdc")));
        std::unordered_map<uint64_t, double> net_clk_period;
        std::vector<uint64_t> net_clk_order;
        auto set_clk_per = [&](uint64_t nt, double per) {
            if (!nt) return;
            if (!net_clk_period.count(nt)) net_clk_order.push_back(nt);
            net_clk_period[nt] = per;
        };
        std::unordered_map<std::string, uint64_t> name_to_net;
        auto reg_net_lookup = [&](uint64_t nt) {
            if (!nt) return;
            std::string nn = trim(nname(nt));
            if (nn.empty()) return;
            name_to_net.try_emplace(nn, nt);
            std::string s = nn;
            while (!s.empty() && (s.front() == '/' || s.front() == '\\')) s.erase(s.begin());
            name_to_net.try_emplace(s, nt);
            std::string sl_to_dot = s, dot_to_sl = s;
            for (char& ch : sl_to_dot) if (ch == '/') ch = '.';
            for (char& ch : dot_to_sl) if (ch == '.') ch = '/';
            name_to_net.try_emplace(sl_to_dot, nt);
            name_to_net.try_emplace(dot_to_sl, nt);
        };
        for (uint64_t nt : net_drv_order) reg_net_lookup(nt);
        for (const auto& [nt, _] : net_in_cnt) reg_net_lookup(nt);

        std::unordered_set<uint64_t> false_nets;
        for (const auto& [p_nm, per] : sdc.port_clk_period) {
            if (auto it = port_name_to_iob.find(p_nm); it != port_name_to_iob.end()) {
                for (const auto& pr : inst_out_pins[it->second]) if (pr.net) set_clk_per(pr.net, per);
            } else if (auto nit = name_to_net.find(p_nm); nit != name_to_net.end()) {
                set_clk_per(nit->second, per);
            }
        }
        for (const auto& fp_nm : sdc.false_from_ports) {
            if (auto it = port_name_to_iob.find(fp_nm); it != port_name_to_iob.end()) {
                for (const auto& pr : inst_out_pins[it->second]) if (pr.net) false_nets.insert(pr.net);
            }
        }
        for (const auto& [full_pin, per] : sdc.pin_clk_period) {
            size_t sl = full_pin.rfind('/');
            if (sl != std::string::npos) {
                std::string i_nm = full_pin.substr(0, sl);
                std::string p_nm = full_pin.substr(sl + 1);
                uint64_t o = 0;
                if (auto it = name_to_inst.find(i_nm); it != name_to_inst.end()) o = it->second;
                else {
                    std::string stripped = (!i_nm.empty() && i_nm[0] == '/') ? i_nm.substr(1) : i_nm;
                    if (auto it2 = name_to_inst.find(stripped); it2 != name_to_inst.end()) o = it2->second;
                }
                if (o) {
                    for (const auto& pr : inst_out_pins[o]) if (pr.lbl == p_nm && pr.net) set_clk_per(pr.net, per);
                }
            }
        }
        for (const auto& [n_nm, per] : sdc.net_clk_period) {
            std::string key = trim(n_nm);
            while (!key.empty() && (key.front() == '/' || key.front() == '\\')) key.erase(key.begin());
            if (auto nit = name_to_net.find(key); nit != name_to_net.end()) {
                set_clk_per(nit->second, per);
            } else if (auto nit2 = name_to_net.find(n_nm); nit2 != name_to_net.end()) {
                set_clk_per(nit2->second, per);
            }
        }

        auto is_clk_pin_lbl = [](std::string_view lbl) {
            return starts_with(lbl, "CLK") || lbl == "PCLK" || lbl == "FCLK" || lbl == "ICLK" || lbl == "HCLKIN" || lbl == "tck_pad_i"
                || lbl == "G" || ends_with(lbl, "_CLK") || lbl == "DBG_TCK" || lbl == "FABRIC_CLK_REF_CORE_I";
        };
        std::unordered_set<uint64_t> clock_nets(net_clk_order.begin(), net_clk_order.end());
        for (uint64_t o : insts) {
            int t = inst_type[o];
            if (t == 4 || t == 5 || t == 6 || t == 7 || t == 13 || t == 20 || t == 50 || t == 70 || t == 99 || t == 100 || t == 112 || t == 174 || t == 211) {
                for (const auto& pr : inst_in_pins[o]) {
                    if (pr.net && !check_const(pr.net) && is_clk_pin_lbl(pr.lbl)) clock_nets.insert(pr.net);
                }
            }
            if (t == 13 || t == 20 || t == 99 || t == 112 || t == 174) {
                for (const auto& pr : inst_out_pins[o]) {
                    if (pr.net && !check_const(pr.net) && (starts_with(pr.lbl, "CLKOUT") || starts_with(pr.lbl, "DQS") || pr.lbl == "OSCOUT" || pr.lbl == "tck_o" || ends_with(pr.lbl, "_CLK") || starts_with(pr.lbl, "FABRIC_QUAD_CLK"))) {
                        clock_nets.insert(pr.net);
                    }
                }
            }
        }

        // Seed unconstrained PLL CLKIN nets from FCLKIN defparam when present
        for (uint64_t po : pll_objs) {
            auto pit = inst_defparams.find(inst_name[po]);
            if (pit == inst_defparams.end()) continue;
            double fclkin = parse_param_num(pit->second, "FCLKIN", -1.0);
            if (fclkin > 0.0) {
                for (const auto& pr : inst_in_pins[po]) {
                    if (pr.lbl == "CLKIN" && pr.net && !net_clk_period.count(pr.net)) {
                        set_clk_per(pr.net, 1000.0 / fclkin);
                    }
                }
            }
        }

        for (int hop = 0; hop < 4; ++hop) {
            bool changed = false;
            std::vector<uint64_t> cur_order = net_clk_order;
            for (uint64_t nt : cur_order) {
                double per = net_clk_period[nt];
                if (!net_sinks_inst.count(nt)) continue;
                for (const auto& snk : net_sinks_inst[nt]) {
                    int st = inst_type[snk.inst];
                    if (st == 1 || st == 2 || st == 13 || st == 99 || st == 112 || st == 174) {
                        const auto* dpm = inst_defparams.count(inst_name[snk.inst]) ? &inst_defparams.at(inst_name[snk.inst]) : nullptr;
                        for (const auto& opr : inst_out_pins[snk.inst]) {
                            if (opr.net && clock_nets.count(opr.net) && !net_clk_period.count(opr.net)) {
                                double scale = 1.0;
                                if (st == 13 && inst_sub[snk.inst] == 101 && snk.lbl == "HCLKIN") {
                                    scale = dpm ? parse_param_num(*dpm, "DIV_MODE", 4.0) : 4.0;
                                    if (scale <= 0.0) scale = 4.0;
                                } else if ((st == 13 || st == 99) && snk.lbl == "CLKIN" && dpm && starts_with(opr.lbl, "CLKOUT")) {
                                    double mdiv = parse_param_num(*dpm, "MDIV_SEL", -1.0);
                                    double idiv = parse_param_num(*dpm, "IDIV_SEL", -1.0);
                                    if (mdiv > 0.0 && idiv > 0.0 && opr.lbl.size() == 7 && opr.lbl[6] >= '0' && opr.lbl[6] <= '6') {
                                        std::string ok = std::string("ODIV") + opr.lbl[6] + "_SEL";
                                        double odiv = parse_param_num(*dpm, ok, -1.0);
                                        if (odiv > 0.0) scale = (idiv * odiv) / mdiv;
                                    } else {
                                        double fbdiv = parse_param_num(*dpm, "FBDIV_SEL", -1.0);
                                        if (fbdiv >= 0.0 && idiv >= 0.0) {
                                            scale = (idiv + 1.0) / (fbdiv + 1.0);
                                            if (opr.lbl == "CLKOUTD") {
                                                double sdiv = parse_param_num(*dpm, "DYN_SDIV_SEL", 2.0);
                                                if (sdiv > 0.0) scale *= sdiv;
                                            }
                                        }
                                    }
                                }
                                set_clk_per(opr.net, per * scale);
                                changed = true;
                            }
                        }
                    }
                }
            }
            if (!changed) break;
        }
        if (cmd_global_freq > 0.0) {
            for (uint64_t nt : net_drv_order) {
                if (clock_nets.count(nt) && !net_clk_period.count(nt)) set_clk_per(nt, default_clk_per_ns);
            }
            for (const auto& [nt, _] : net_in_cnt) {
                if (clock_nets.count(nt) && !net_clk_period.count(nt)) set_clk_per(nt, default_clk_per_ns);
            }
        }

        std::unordered_set<uint64_t> packed_zero_wire_nets;
        for (const auto& rec : mux_tree_records) for (const auto& lk : rec.links) if (lk.nt) packed_zero_wire_nets.insert(lk.nt);
        for (const auto& rec : alu_chain_records) {
            for (uint64_t ic : rec.internal_carries) packed_zero_wire_nets.insert(ic);
            for (size_t k = 0; k < rec.chain.size(); ++k) {
                if (rec.paired_regs[k] && alu_sum.count(rec.chain[k]) && alu_sum[rec.chain[k]]) {
                    packed_zero_wire_nets.insert(alu_sum[rec.chain[k]]);
                }
            }
        }
        for (const auto& lf : lut_ff_records) if (lf.f_nt) packed_zero_wire_nets.insert(lf.f_nt);

        std::unordered_map<std::string, double> measured_pin_w;
        std::string routed_del_p1 = path_join(out, "routed_delays_pass1.json");
        if (file_exists(routed_del_p1)) {
            JsonValue rd_j = parse_json_file(routed_del_p1);
            for (const auto& [k, v] : rd_j.as_obj()) measured_pin_w[k] = v.as_f64();
            std::fprintf(stdout, "[ownpr] Loaded %zu measured Pass-1 routed pin delays for Pass-2 STA\n", measured_pin_w.size());
            std::fflush(stdout);
        }

        auto net_wire_est = [&](uint64_t nt) -> double {
            if (!nt || packed_zero_wire_nets.count(nt)) return 0.0;
            int fo = net_in_cnt.count(nt) ? net_in_cnt[nt] : 0;
            return 0.48
                + (fo > 8 ? 0.22 : 0.0)
                + (fo > 32 ? 0.28 : 0.0)
                + (fo > 128 ? 0.35 : 0.0)
                + (fo > 256 ? 0.60 : 0.0)
                + (fo > 768 ? 0.85 : 0.0);
        };
        auto pin_wire_est = [&](uint64_t nt, uint64_t snk_o, const std::string& snk_lbl) -> double {
            if (!nt || packed_zero_wire_nets.count(nt)) return 0.0;
            double est = net_wire_est(nt);
            if (!measured_pin_w.empty()) {
                auto it = measured_pin_w.find(inst_name[snk_o] + "/" + snk_lbl);
                if (it != measured_pin_w.end()) {
                    return std::min({5.5, 6.0 * est, 0.58 * it->second + 0.42 * est});
                }
            }
            return est;
        };
        auto inst_capture_clk_and_period = [&](uint64_t o) -> std::pair<uint64_t, double> {
            uint64_t best_nt = 0;
            double best_p = -1.0;
            for (const auto& pr : inst_in_pins[o]) {
                if (pr.net && net_clk_period.count(pr.net) && is_clk_pin_lbl(pr.lbl)) {
                    double p = net_clk_period[pr.net];
                    if (best_p < 0.0 || p < best_p) { best_p = p; best_nt = pr.net; }
                }
            }
            return (best_p >= 0.0) ? std::pair{best_nt, best_p} : std::pair<uint64_t, double>{0ULL, default_clk_per_ns};
        };

        auto is_ssram_comb_pin = [&](uint64_t o, std::string_view lbl) -> bool {
            if (inst_type[o] != 6) return true;
            int sb = inst_sub[o];
            if (sb >= 95 && sb <= 97) return starts_with(lbl, "RAD");
            return starts_with(lbl, "AD");
        };
        std::unordered_map<uint64_t, bool> is_comb_inst;
        size_t total_comb = 0;
        for (uint64_t o : insts) {
            int t = inst_type[o];
            bool comb = false;
            if (t == 1 || t == 2 || t == 6 || t == 106) comb = true;
            else if (t == 4) {
                bool has_clk = false;
                for (const auto& pr : inst_in_pins[o]) {
                    if (pr.net && !check_const(pr.net) && starts_with(pr.lbl, "CLK")) { has_clk = true; break; }
                }
                comb = !has_clk;
            }
            is_comb_inst[o] = comb;
            if (comb) ++total_comb;
        }

        std::unordered_map<uint64_t, int> in_deg;
        std::unordered_map<uint64_t, std::vector<uint64_t>> comb_succ;
        for (uint64_t o : insts) {
            if (!is_comb_inst[o]) continue;
            std::vector<uint64_t> preds;
            for (const auto& pr : inst_in_pins[o]) {
                if (!is_ssram_comb_pin(o, pr.lbl)) continue;
                if (pr.net && !check_const(pr.net) && !clock_nets.count(pr.net) && net_drv.count(pr.net)) {
                    uint64_t drv_o = net_drv[pr.net].inst;
                    if (is_comb_inst[drv_o] && drv_o != o && std::find(preds.begin(), preds.end(), drv_o) == preds.end()) {
                        preds.push_back(drv_o);
                    }
                }
            }
            in_deg[o] = static_cast<int>(preds.size());
            for (uint64_t pr_o : preds) comb_succ[pr_o].push_back(o);
        }

        std::deque<uint64_t> q_topo;
        for (uint64_t o : insts) if (is_comb_inst[o] && in_deg[o] == 0) q_topo.push_back(o);
        std::vector<uint64_t> topo_comb;
        topo_comb.reserve(total_comb);
        while (!q_topo.empty()) {
            uint64_t u_o = q_topo.front();
            q_topo.pop_front();
            topo_comb.push_back(u_o);
            for (uint64_t v_o : comb_succ[u_o]) {
                if (--in_deg[v_o] == 0) q_topo.push_back(v_o);
            }
        }
        if (topo_comb.size() < total_comb) {
            std::unordered_set<uint64_t> in_topo(topo_comb.begin(), topo_comb.end());
            for (uint64_t o : insts) if (is_comb_inst[o] && !in_topo.count(o)) topo_comb.push_back(o);
        }

        std::unordered_map<uint64_t, double> net_drv_arr;
        std::unordered_map<uint64_t, uint64_t> net_launch_clk;
        for (uint64_t o : insts) {
            int t = inst_type[o];
            if (is_comb_inst[o] && !(t == 6 && inst_sub[o] != 98)) continue;
            auto [l_clk, _] = inst_capture_clk_and_period(o);
            double t_co = (t == 5) ? 2.15 : (t == 4) ? 2.30 : (t == 6) ? 0.56 : (t == 50 || t == 70) ? 0.36 : 0.70;
            for (const auto& pr : inst_out_pins[o]) {
                if (pr.net && !check_const(pr.net) && !clock_nets.count(pr.net) && !false_nets.count(pr.net)) {
                    if (t_co > net_drv_arr[pr.net]) {
                        net_drv_arr[pr.net] = t_co;
                        if (l_clk) net_launch_clk[pr.net] = l_clk;
                    }
                }
            }
        }
        for (uint64_t o : topo_comb) {
            int t = inst_type[o];
            double base_out = 0.0;
            uint64_t dom_cand = 0;
            for (const auto& pr : inst_in_pins[o]) {
                if (!is_ssram_comb_pin(o, pr.lbl)) continue;
                if (!pr.net || check_const(pr.net) || clock_nets.count(pr.net) || false_nets.count(pr.net)) continue;
                if (!net_drv_arr.count(pr.net) && !net_drv.count(pr.net)) continue;
                double pin_a = (net_drv_arr.count(pr.net) ? net_drv_arr[pr.net] : 0.0) + pin_wire_est(pr.net, o, pr.lbl);
                double cell_d = (t == 1) ? lut4_pin_d(pr.lbl)
                              : (t == 2) ? mux2_pin_d(pr.lbl)
                              : (t == 6) ? 0.52
                              : (t == 106) ? (pr.lbl == "CIN" ? 0.045 : 0.30)
                              : 2.55;
                double cand_out = pin_a + cell_d;
                if (cand_out > base_out) {
                    base_out = cand_out;
                    if (net_launch_clk.count(pr.net)) dom_cand = net_launch_clk[pr.net];
                }
            }
            for (const auto& pr : inst_out_pins[o]) {
                if (pr.net && !check_const(pr.net) && !false_nets.count(pr.net)) {
                    double out_a = base_out + ((t == 106 && pr.lbl == "SUM") ? 0.26 : 0.0);
                    if (out_a > net_drv_arr[pr.net]) {
                        net_drv_arr[pr.net] = out_a;
                        if (dom_cand) net_launch_clk[pr.net] = dom_cand;
                    }
                }
            }
        }

        struct DomEp { uint64_t o = 0; std::string lbl; uint64_t nt = 0; double req = 0.0; };
        std::unordered_map<uint64_t, std::vector<DomEp>> domain_endpoints;
        std::vector<uint64_t> domain_order;
        std::unordered_map<uint64_t, double> domain_periods;
        for (uint64_t o : insts) {
            int t = inst_type[o];
            bool is_ram16_seq = (t == 6 && inst_sub[o] != 98);
            if (is_comb_inst[o] && !is_ram16_seq) continue;
            if (!is_ram16_seq && t != 3 && t != 4 && t != 5 && t != 7 && t != 20 && t != 50 && t != 70) continue;
            auto [dom_nt, raw_per] = inst_capture_clk_and_period(o);
            for (const auto& pr : inst_in_pins[o]) {
                if (is_ram16_seq && starts_with(pr.lbl, "RAD")) continue;
                if (!pr.net || check_const(pr.net) || clock_nets.count(pr.net) || false_nets.count(pr.net)) continue;
                if (is_clk_pin_lbl(pr.lbl)) continue;
                uint64_t eff_dom = dom_nt;
                double eff_per = raw_per;
                if (eff_dom == 0 && net_launch_clk.count(pr.net)) {
                    eff_dom = net_launch_clk[pr.net];
                    eff_per = net_clk_period.count(eff_dom) ? net_clk_period[eff_dom] : default_clk_per_ns;
                }
                domain_periods[eff_dom] = eff_per;
                double per_use = (eff_per >= 100.0) ? 25.0 : eff_per;
                double t_su = (t == 5) ? 0.85 : (t == 4) ? 1.05 : is_ram16_seq ? 0.28 : (t == 50 || t == 70) ? ((pr.lbl != "D") ? 0.38 : 0.22) : 0.65;
                if (!domain_endpoints.count(eff_dom)) domain_order.push_back(eff_dom);
                domain_endpoints[eff_dom].push_back({o, pr.lbl, pr.net, per_use - t_su});
            }
        }

        std::unordered_map<PinKey, double, PinKeyHash> pin_crit_map;
        std::vector<PinKey> pin_crit_order;
        std::unordered_map<uint64_t, double> net_dom_crit;

        for (uint64_t dom_nt : domain_order) {
            const auto& ep_list = domain_endpoints[dom_nt];
            if (ep_list.empty()) continue;
            std::unordered_map<uint64_t, double> d_drv_req;
            std::unordered_map<PinKey, double, PinKeyHash> d_pin_req;
            std::vector<PinKey> d_pin_order;
            double d_worst = 1e9, d_max_arr = 2.5;

            for (const auto& ep : ep_list) {
                double w_ep = pin_wire_est(ep.nt, ep.o, ep.lbl);
                double arr_v = (net_drv_arr.count(ep.nt) ? net_drv_arr[ep.nt] : 0.36) + w_ep;
                PinKey pk{ep.o, ep.lbl};
                auto [pit, pins] = d_pin_req.emplace(pk, ep.req);
                if (pins) d_pin_order.push_back(pk);
                else if (ep.req < pit->second) pit->second = ep.req;

                double drv_r = ep.req - w_ep;
                auto [dit, dins] = d_drv_req.emplace(ep.nt, drv_r);
                if (!dins && drv_r < dit->second) dit->second = drv_r;
                if (arr_v > d_max_arr) d_max_arr = arr_v;
                if (ep.req - arr_v < d_worst) d_worst = ep.req - arr_v;
            }
            if (d_worst > 1e8) d_worst = 0.0;

            for (size_t ri = topo_comb.size(); ri > 0; --ri) {
                uint64_t o = topo_comb[ri - 1];
                int t = inst_type[o];
                bool has_out = false;
                double min_out = 0.0;
                for (const auto& pr : inst_out_pins[o]) {
                    if (pr.net && d_drv_req.count(pr.net)) {
                        double rv = d_drv_req[pr.net] - ((t == 106 && pr.lbl == "SUM") ? 0.26 : 0.0);
                        if (!has_out || rv < min_out) { min_out = rv; has_out = true; }
                    }
                }
                if (!has_out) continue;
                for (const auto& pr : inst_in_pins[o]) {
                    if (!is_ssram_comb_pin(o, pr.lbl)) continue;
                    if (!pr.net || check_const(pr.net) || clock_nets.count(pr.net) || false_nets.count(pr.net)) continue;
                    if (!net_drv.count(pr.net) && !net_drv_arr.count(pr.net)) continue;
                    double cell_d = (t == 106) ? (pr.lbl == "CIN" ? 0.045 : 0.30)
                                  : (t == 6) ? 0.52
                                  : (t == 2) ? mux2_pin_d(pr.lbl)
                                  : (t == 1) ? lut4_pin_d(pr.lbl) : 2.55;
                    double in_r = min_out - cell_d;
                    PinKey pk{o, pr.lbl};
                    auto [pit, pins] = d_pin_req.emplace(pk, in_r);
                    if (pins) d_pin_order.push_back(pk);
                    else if (in_r < pit->second) pit->second = in_r;

                    double drv_r = in_r - pin_wire_est(pr.net, o, pr.lbl);
                    auto [dit, dins] = d_drv_req.emplace(pr.net, drv_r);
                    if (!dins && drv_r < dit->second) dit->second = drv_r;
                }
            }

            double d_span = std::max(3.0, std::min(9.5, d_max_arr * 0.75));
            double raw_dom_per = domain_periods.count(dom_nt) ? domain_periods[dom_nt] : 12.5;
            double d_cap = (raw_dom_per >= 100.0 && (raw_dom_per - d_max_arr) > 50.0) ? 0.30
                         : (d_worst >= std::max(1.5, 0.18 * raw_dom_per)) ? 0.42 : 0.99;

            for (const auto& pk : d_pin_order) {
                double req_p = d_pin_req[pk];
                uint64_t nt_in = 0;
                for (const auto& pr : inst_in_pins[pk.inst]) if (pr.lbl == pk.lbl) { nt_in = pr.net; break; }
                if (!nt_in || false_nets.count(nt_in)) continue;
                double arr_p = (net_drv_arr.count(nt_in) ? net_drv_arr[nt_in] : 0.36) + pin_wire_est(nt_in, pk.inst, pk.lbl);
                double rel_slk = std::max(0.0, (req_p - arr_p) - d_worst);
                double cc = std::min(d_cap, std::max(0.05, d_cap * (1.0 - rel_slk / d_span)));
                auto [cit, cins] = pin_crit_map.emplace(pk, cc);
                if (cins) pin_crit_order.push_back(pk);
                else if (cc > cit->second) cit->second = cc;
                double cur_nd = net_dom_crit.count(nt_in) ? net_dom_crit[nt_in] : 0.05;
                if (cc > cur_nd) net_dom_crit[nt_in] = cc;
            }
        }

        // Optional post-STA timing_feedback.json
        std::unordered_set<PinKey, PinKeyHash> fb_edge_boost;
        std::unordered_map<std::string, double> fb_hold_pins;
        std::string fb_path = path_join(out, "timing_feedback.json");
        if (file_exists(fb_path)) {
            JsonValue fb_j = parse_json_file(fb_path);
            std::unordered_map<std::string, double> fb_pins;
            if (fb_j.contains("crit_pins")) for (const auto& [k, v] : fb_j["crit_pins"].as_obj()) fb_pins[k] = v.as_f64();
            if (fb_j.contains("hold_pins")) for (const auto& [k, v] : fb_j["hold_pins"].as_obj()) fb_hold_pins[k] = v.as_f64();
            std::unordered_set<std::string> matched_fb_insts;
            size_t boosted_pins = 0;
            for (const auto& pk : pin_crit_order) {
                const std::string& inm = inst_name[pk.inst];
                auto bit = fb_pins.find(inm + "/" + pk.lbl);
                if (bit != fb_pins.end() && bit->second > 0.0) {
                    pin_crit_map[pk] = std::max(pin_crit_map[pk], bit->second);
                    fb_edge_boost.insert(pk);
                    matched_fb_insts.insert(inm);
                    ++boosted_pins;
                }
            }
            if (fb_j.contains("crit_insts")) {
                for (const auto& [inm, v] : fb_j["crit_insts"].as_obj()) {
                    if (matched_fb_insts.count(inm)) continue;
                    double b_val = v.as_f64();
                    uint64_t so = 0;
                    if (auto it = name_to_inst.find(inm); it != name_to_inst.end()) so = it->second;
                    else if (!inm.empty() && inm[0] == '/' && name_to_inst.count(inm.substr(1))) so = name_to_inst[inm.substr(1)];
                    if (so && is_comb_inst[so]) {
                        for (const auto& pr : inst_in_pins[so]) {
                            if (pr.net && !check_const(pr.net) && !clock_nets.count(pr.net) && !false_nets.count(pr.net)) {
                                PinKey pk{so, pr.lbl};
                                auto [cit, cins] = pin_crit_map.emplace(pk, std::max(0.05, b_val * 0.95));
                                if (cins) pin_crit_order.push_back(pk);
                                else cit->second = std::max(cit->second, b_val * 0.95);
                                fb_edge_boost.insert(pk);
                                ++boosted_pins;
                            }
                        }
                    }
                }
            }
            std::fprintf(stdout, "[ownpr] Applied post-STA timing feedback boost to %zu critical pins\n", boosted_pins);
            std::fflush(stdout);
        }

        if (!measured_pin_w.empty()) {
            for (const auto& pk : pin_crit_order) {
                if (pin_crit_map[pk] >= 0.75) {
                    auto it = measured_pin_w.find(inst_name[pk.inst] + "/" + pk.lbl);
                    if (it != measured_pin_w.end() && it->second >= 1.60) fb_edge_boost.insert(pk);
                }
            }
        }

        auto conn_crit_of = [&](uint64_t nt, uint64_t snk_o, const std::string& snk_lbl) -> double {
            if (false_nets.count(nt)) return 0.04;
            if (auto it = pin_crit_map.find(PinKey{snk_o, snk_lbl}); it != pin_crit_map.end()) return it->second;
            return net_dom_crit.count(nt) ? net_dom_crit[nt] : 0.06;
        };

        std::unordered_map<uint64_t, double> net_crit_map;
        for (uint64_t nt : net_drv_order) {
            if (check_const(nt) || clock_nets.count(nt)) continue;
            if (false_nets.count(nt)) { net_crit_map[nt] = 0.04; continue; }
            auto sit = net_sinks_inst.find(nt);
            if (sit != net_sinks_inst.end() && !sit->second.empty()) {
                double mc = 0.0;
                for (const auto& snk : sit->second) mc = std::max(mc, conn_crit_of(nt, snk.inst, snk.lbl));
                net_crit_map[nt] = mc;
            } else {
                net_crit_map[nt] = 0.06;
            }
        }

        std::unordered_map<NtNidKey, double, NtNidHash> sink_nid_crit;

        // Build connection-based 2-pin star + clique placement hypergraph CSR in deterministic net_drv_order
        std::vector<int32_t> pnet_offsets{0};
        std::vector<int32_t> pnet_units;
        std::vector<float> pnet_weights;

        for (uint64_t nt : net_drv_order) {
            if (check_const(nt) || clock_nets.count(nt)) continue;
            uint64_t drv_o = net_drv[nt].inst;
            auto du_it = inst_to_unit.find(drv_o);
            if (du_it == inst_to_unit.end()) continue;
            int32_t u_drv = du_it->second;
            bool drv_hard = (inst_type[drv_o] == 4 || inst_type[drv_o] == 5);

            std::vector<int32_t> u_order;
            std::unordered_map<int32_t, double> u_max_c;
            std::unordered_set<int32_t> u_hard, u_fb;
            if (auto sit = net_sinks_inst.find(nt); sit != net_sinks_inst.end()) {
                for (const auto& snk : sit->second) {
                    auto su_it = inst_to_unit.find(snk.inst);
                    if (su_it == inst_to_unit.end() || su_it->second == u_drv) continue;
                    int32_t u_s = su_it->second;
                    double cc = conn_crit_of(nt, snk.inst, snk.lbl);
                    auto [mit, mins] = u_max_c.emplace(u_s, cc);
                    if (mins) u_order.push_back(u_s);
                    else if (cc > mit->second) mit->second = cc;
                    if (inst_type[snk.inst] == 4 || inst_type[snk.inst] == 5) u_hard.insert(u_s);
                    if (fb_edge_boost.count(PinKey{snk.inst, snk.lbl})) u_fb.insert(u_s);
                }
            }
            if (u_order.empty()) continue;
            if (false_nets.count(nt)) {
                pnet_units.push_back(u_drv);
                pnet_units.insert(pnet_units.end(), u_order.begin(), u_order.end());
                pnet_offsets.push_back(static_cast<int32_t>(pnet_units.size()));
                pnet_weights.push_back(0.06f);
                continue;
            }
            size_t deg_u = u_order.size();
            if (deg_u == 1) {
                int32_t u_s = u_order[0];
                double mc = u_max_c[u_s];
                double w = 1.05 + 14.5 * std::pow(mc, 3.5);
                if (drv_hard || u_hard.count(u_s)) w *= 1.35;
                if (u_fb.count(u_s)) w *= 1.35;
                pnet_units.push_back(u_drv);
                pnet_units.push_back(u_s);
                pnet_offsets.push_back(static_cast<int32_t>(pnet_units.size()));
                pnet_weights.push_back(static_cast<float>(w));
            } else {
                double max_c = 0.0, sum_c = 0.0;
                for (int32_t u_s : u_order) {
                    double mc = u_max_c[u_s];
                    if (mc > max_c) max_c = mc;
                    sum_c += mc;
                }
                double c_clq = (deg_u <= 8) ? max_c : (0.25 * max_c + 0.75 * (sum_c / static_cast<double>(deg_u)));
                double w_clique = (0.95 + 3.0 * (c_clq * c_clq * c_clq)) * std::pow(static_cast<double>(std::min<size_t>(deg_u, 64)), 0.40);
                pnet_units.push_back(u_drv);
                pnet_units.insert(pnet_units.end(), u_order.begin(), u_order.end());
                pnet_offsets.push_back(static_cast<int32_t>(pnet_units.size()));
                pnet_weights.push_back(static_cast<float>(w_clique));

                std::vector<std::pair<double, int32_t>> crit_stars;
                for (int32_t u_s : u_order) {
                    double mc = u_max_c[u_s];
                    if (mc >= 0.36 || u_fb.count(u_s)) crit_stars.emplace_back(mc, u_s);
                }
                std::sort(crit_stars.begin(), crit_stars.end(), [](const auto& a, const auto& b) { return a > b; });
                if (crit_stars.size() > 96) crit_stars.resize(96);
                if (!crit_stars.empty()) {
                    double top_mc = std::max(crit_stars[0].first, 1e-3);
                    double eff_n = 0.0;
                    for (auto [mc, _] : crit_stars) eff_n += std::pow(mc / top_mc, 3.5);
                    double norm_k = std::max(1.0, std::pow(eff_n, 0.62));
                    for (auto [mc, u_s] : crit_stars) {
                        double ws = (14.5 * std::pow(mc, 3.5)) / norm_k;
                        if ((drv_hard || u_hard.count(u_s)) && deg_u <= 32) ws *= 1.30;
                        if (u_fb.count(u_s)) ws *= (deg_u > 24) ? 1.30 : 1.75;
                        pnet_units.push_back(u_drv);
                        pnet_units.push_back(u_s);
                        pnet_offsets.push_back(static_cast<int32_t>(pnet_units.size()));
                        pnet_weights.push_back(static_cast<float>(ws));
                    }
                }
            }
        }
        int32_t num_pnets = static_cast<int32_t>(pnet_weights.size());

        // =====================================================================
        // 4. Discover Device Masks (CFU, BSRAM, DSP) & Run C++ Analytical Placement
        // =====================================================================
        auto fse_grid = load_fse_grid_checked(sdk_ide, worker_dev_name);
        std::vector<uint8_t> cfu_mask(111 * 184, 0U);
        int cfu_cnt = 0, avail_cfu_cnt = 0;
        for (int r = 1; r < 110; ++r) {
            const auto& row_g = fse_grid[r - 1];
            for (int c = 1; c < 183; ++c) {
                int32_t gt = row_g[c - 1];
                if (gt == 17 || (gt >= 221 && gt <= 228)) {
                    ++cfu_cnt;
                    if (!fixed_reserved_cfu_grid[r * 184 + c]) {
                        cfu_mask[r * 184 + c] = (gt == 17) ? 2U : 1U;
                        ++avail_cfu_cnt;
                    }
                }
            }
        }
        if (cfu_cnt != 17280) throw std::runtime_error("Unexpected CFU count: " + std::to_string(cfu_cnt));
        std::fprintf(stdout, "[ownpr] Available pin-disjoint CFU tiles: %d/%d\n", avail_cfu_cnt, cfu_cnt);
        std::fflush(stdout);

        auto fn_get_bsram = reinterpret_cast<uint64_t (*)(uint64_t, int16_t, int16_t, int16_t)>(gw_req("?getBsramByLoc@chip@NETLIST@@QEAAPEAVbsram@2@FFF@Z"));
        std::vector<int16_t> bsram_rc_arr;
        int32_t num_bsram_sites = 0;
        for (int16_t r = 1; r < 110; ++r) {
            for (int16_t c = 1; c < 183; ++c) {
                uint64_t g = fn_getgrid(chip, r, c);
                if (g && rd_u8_mem(g + 0x1c) && fn_get_bsram(chip, r, c, 0)) {
                    bsram_rc_arr.push_back(r);
                    bsram_rc_arr.push_back(c);
                    ++num_bsram_sites;
                }
            }
        }

        auto fn_get_dsp = reinterpret_cast<uint64_t (*)(uint64_t, int16_t, int16_t)>(gw_req("?getNewDspByLoc@chip@NETLIST@@QEAAPEAVnewDsp@2@FF@Z"));
        std::vector<std::pair<int16_t, int16_t>> dsp_sites;
        std::set<std::pair<int16_t, int16_t>> dsp_site_set;
        for (int16_t r = 1; r < 110; ++r) {
            for (int16_t c = 1; c < 183; ++c) {
                uint64_t g = fn_getgrid(chip, r, c);
                if (g && rd_u8_mem(g + 0x1c) && fn_get_dsp(chip, r, c)) {
                    dsp_sites.emplace_back(r, c);
                    dsp_site_set.emplace(r, c);
                }
            }
        }
        std::vector<int16_t> dsp_rc_arr;
        std::vector<uint8_t> dsp_wide_arr;
        for (auto [r, c] : dsp_sites) {
            dsp_rc_arr.push_back(r);
            dsp_rc_arr.push_back(c);
            dsp_wide_arr.push_back(dsp_site_set.count({r, static_cast<int16_t>(c + 3)}) ? 1U : 0U);
        }

        if (pnet_units.empty()) pnet_units.push_back(0);
        if (pnet_weights.empty()) pnet_weights.push_back(0.0f);
        if (bsram_rc_arr.empty()) { bsram_rc_arr.push_back(0); bsram_rc_arr.push_back(0); }
        if (dsp_rc_arr.empty()) { dsp_rc_arr.push_back(0); dsp_rc_arr.push_back(0); dsp_wide_arr.push_back(0); }

        std::vector<int16_t> out_r(num_units, 0), out_c(num_units, 0), out_slot(num_units, 0);
        double t_pl0 = perf_counter();
        std::fprintf(stdout, "[ownpr] Running C++ analytical placement + exact hardware legalization (%d hypernets)...\n", num_pnets);
        std::fflush(stdout);
        int32_t prc = bridge.place_design(
            cfu_mask.data(),
            bsram_rc_arr.data(), num_bsram_sites,
            dsp_rc_arr.data(), dsp_wide_arr.data(), static_cast<int32_t>(dsp_sites.size()),
            num_units,
            unit_kind.data(), unit_sub.data(), unit_cs.data(), unit_c60.data(), unit_chain_len.data(), unit_flags.data(),
            init_r.data(), init_c.data(),
            num_pnets, pnet_offsets.data(), pnet_units.data(), pnet_weights.data(),
            out_r.data(), out_c.data(), out_slot.data()
        );
        double t_place_sec = perf_counter() - t_pl0;
        if (prc != 0) throw std::runtime_error("bridge_place_design failed with rc=" + std::to_string(prc));
        std::fprintf(stdout, "[ownpr] Placement & legalization succeeded in %.2fs\n", t_place_sec);
        std::fflush(stdout);

        // =====================================================================
        // 4b. Post-Legalization Slack-Exact Minimax Relocation + Router Crit Refresh
        // =====================================================================
        double t_mm0 = perf_counter();
        std::vector<int> ur_py(num_units), uc_py(num_units);
        for (int32_t u = 0; u < num_units; ++u) { ur_py[u] = out_r[u]; uc_py[u] = out_c[u]; }
        std::unordered_map<uint64_t, int32_t> net_drv_unit;
        for (uint64_t nt : net_drv_order) {
            auto it = inst_to_unit.find(net_drv[nt].inst);
            net_drv_unit[nt] = (it != inst_to_unit.end()) ? it->second : -1;
        }
        std::unordered_map<uint64_t, double> net_slope;
        for (const auto& [nt, cnt] : net_in_cnt) {
            net_slope[nt] = 0.041 + ((cnt >= 64) ? 0.023 : ((cnt >= 16) ? 0.010 : 0.0));
        }
        std::unordered_map<uint64_t, double> seq_launch_arr;
        for (uint64_t o : insts) {
            int t = inst_type[o];
            if (is_comb_inst[o] && !(t == 6 && inst_sub[o] != 98)) continue;
            double t_co = (t == 5) ? 2.15 : (t == 4) ? 2.30 : (t == 6) ? 0.56 : (t == 50 || t == 70) ? 0.36 : 0.70;
            for (const auto& pr : inst_out_pins[o]) {
                if (pr.net && !check_const(pr.net) && !clock_nets.count(pr.net) && !false_nets.count(pr.net)) {
                    if (t_co > seq_launch_arr[pr.net]) seq_launch_arr[pr.net] = t_co;
                }
            }
        }

        std::unordered_map<int32_t, LutFfRec> lut_ff_by_uid;
        for (size_t i = 0; i < lut_ff_records.size(); ++i) lut_ff_by_uid[lut_ff_uids[i]] = lut_ff_records[i];
        std::unordered_map<int32_t, MuxTreeRec> mux_by_uid;
        for (size_t i = 0; i < mux_tree_records.size(); ++i) {
            if (mux_tree_records[i].lvl == 5 || mux_tree_records[i].lvl == 6) mux_by_uid[mux_uids[i]] = mux_tree_records[i];
        }
        std::unordered_map<int32_t, uint64_t> reg_by_uid, lut_by_uid;
        for (size_t i = 0; i < standalone_regs.size(); ++i) reg_by_uid[reg_only_uids[i]] = standalone_regs[i];
        for (size_t i = 0; i < standalone_dls.size(); ++i) reg_by_uid[dl_only_uids[i]] = standalone_dls[i];
        for (size_t i = 0; i < standalone_luts.size(); ++i) lut_by_uid[lut_only_uids[i]] = standalone_luts[i];

        auto pl_wire_geom = [&](uint64_t nt, uint64_t snk_o) -> double {
            if (!nt || packed_zero_wire_nets.count(nt)) return 0.0;
            int32_t du = net_drv_unit.count(nt) ? net_drv_unit[nt] : -1;
            int32_t su = inst_to_unit.count(snk_o) ? inst_to_unit[snk_o] : -1;
            if (du < 0 || su < 0 || du == su) return 0.0;
            double sl = net_slope.count(nt) ? net_slope[nt] : 0.041;
            return 0.34 + sl * (std::abs(ur_py[du] - ur_py[su]) + std::abs(uc_py[du] - uc_py[su]));
        };

        std::unordered_map<PinKey, double, PinKeyHash> pin_cong_excess;
        if (!measured_pin_w.empty()) {
            for (uint64_t o : insts) {
                const std::string& inm = inst_name[o];
                for (const auto& pr : inst_in_pins[o]) {
                    if (!pr.net || packed_zero_wire_nets.count(pr.net) || check_const(pr.net) || clock_nets.count(pr.net) || false_nets.count(pr.net)) continue;
                    auto it = measured_pin_w.find(inm + "/" + pr.lbl);
                    if (it != measured_pin_w.end()) {
                        double w0 = pl_wire_geom(pr.net, o);
                        if (w0 > 0.0 && it->second > w0 + 0.15) {
                            pin_cong_excess[{o, pr.lbl}] = std::min(3.20, 0.55 * (it->second - w0));
                        }
                    }
                }
            }
        }
        auto get_cong_ex = [&](uint64_t o, const std::string& lbl) -> double {
            if (pin_cong_excess.empty()) return 0.0;
            auto it = pin_cong_excess.find({o, lbl});
            return (it != pin_cong_excess.end()) ? it->second : 0.0;
        };
        auto pl_wire_delay = [&](uint64_t nt, uint64_t snk_o, const std::string& snk_lbl) -> double {
            double wg = pl_wire_geom(nt, snk_o);
            return (wg <= 0.0) ? 0.0 : (wg + get_cong_ex(snk_o, snk_lbl));
        };

        struct DomStaMeta {
            uint64_t dom_nt = 0;
            std::unordered_map<PinKey, double, PinKeyHash> d_pin_req;
            std::vector<PinKey> d_pin_order;
            double worst_slk = 0.0;
            double max_arr = 2.5;
        };

        std::unordered_map<uint64_t, double> pl_drv_arr;
        std::unordered_map<PinKey, double, PinKeyHash> pl_pin_req;
        std::vector<PinKey> pl_pin_order;
        std::vector<DomStaMeta> dom_sta_meta;

        auto compute_placed_sta = [&]() {
            pl_drv_arr = seq_launch_arr;
            pl_pin_req.clear();
            pl_pin_order.clear();
            dom_sta_meta.clear();

            for (uint64_t o : topo_comb) {
                int t = inst_type[o];
                double base_out = 0.0;
                for (const auto& pr : inst_in_pins[o]) {
                    if (!is_ssram_comb_pin(o, pr.lbl)) continue;
                    if (!pr.net || check_const(pr.net) || clock_nets.count(pr.net) || false_nets.count(pr.net)) continue;
                    if (!pl_drv_arr.count(pr.net) && !net_drv.count(pr.net)) continue;
                    double pin_a = (pl_drv_arr.count(pr.net) ? pl_drv_arr[pr.net] : 0.0) + pl_wire_delay(pr.net, o, pr.lbl);
                    double cell_d = (t == 1) ? lut4_pin_d(pr.lbl)
                                  : (t == 2) ? mux2_pin_d(pr.lbl)
                                  : (t == 6) ? 0.52
                                  : (t == 106) ? (pr.lbl == "CIN" ? 0.045 : 0.30) : 2.55;
                    if (pin_a + cell_d > base_out) base_out = pin_a + cell_d;
                }
                for (const auto& pr : inst_out_pins[o]) {
                    if (pr.net && !check_const(pr.net) && !false_nets.count(pr.net)) {
                        double out_a = base_out + ((t == 106 && pr.lbl == "SUM") ? 0.26 : 0.0);
                        if (out_a > pl_drv_arr[pr.net]) pl_drv_arr[pr.net] = out_a;
                    }
                }
            }

            for (uint64_t dom_nt : domain_order) {
                const auto& ep_list = domain_endpoints[dom_nt];
                if (ep_list.empty()) continue;
                DomStaMeta dm;
                dm.dom_nt = dom_nt;
                dm.worst_slk = 1e9;
                dm.max_arr = 2.5;
                std::unordered_map<uint64_t, double> d_drv_req;

                for (const auto& ep : ep_list) {
                    double w_ep = pl_wire_delay(ep.nt, ep.o, ep.lbl);
                    double arr_v = (pl_drv_arr.count(ep.nt) ? pl_drv_arr[ep.nt] : 0.36) + w_ep;
                    PinKey pk{ep.o, ep.lbl};
                    auto [pit, pins] = dm.d_pin_req.emplace(pk, ep.req);
                    if (pins) dm.d_pin_order.push_back(pk);
                    else if (ep.req < pit->second) pit->second = ep.req;

                    double drv_r = ep.req - w_ep;
                    auto [dit, dins] = d_drv_req.emplace(ep.nt, drv_r);
                    if (!dins && drv_r < dit->second) dit->second = drv_r;
                    if (arr_v > dm.max_arr) dm.max_arr = arr_v;
                    if (ep.req - arr_v < dm.worst_slk) dm.worst_slk = ep.req - arr_v;
                }
                if (dm.worst_slk > 1e8) dm.worst_slk = 0.0;

                for (size_t ri = topo_comb.size(); ri > 0; --ri) {
                    uint64_t o = topo_comb[ri - 1];
                    int t = inst_type[o];
                    bool has_out = false;
                    double min_out = 0.0;
                    for (const auto& pr : inst_out_pins[o]) {
                        if (pr.net && d_drv_req.count(pr.net)) {
                            double rv = d_drv_req[pr.net] - ((t == 106 && pr.lbl == "SUM") ? 0.26 : 0.0);
                            if (!has_out || rv < min_out) { min_out = rv; has_out = true; }
                        }
                    }
                    if (!has_out) continue;
                    for (const auto& pr : inst_in_pins[o]) {
                        if (!is_ssram_comb_pin(o, pr.lbl)) continue;
                        if (!pr.net || check_const(pr.net) || clock_nets.count(pr.net) || false_nets.count(pr.net)) continue;
                        if (!net_drv.count(pr.net) && !pl_drv_arr.count(pr.net)) continue;
                        double cell_d = (t == 106) ? (pr.lbl == "CIN" ? 0.045 : 0.30)
                                      : (t == 6) ? 0.52
                                      : (t == 2) ? mux2_pin_d(pr.lbl)
                                      : (t == 1) ? lut4_pin_d(pr.lbl) : 2.55;
                        double in_r = min_out - cell_d;
                        PinKey pk{o, pr.lbl};
                        auto [pit, pins] = dm.d_pin_req.emplace(pk, in_r);
                        if (pins) dm.d_pin_order.push_back(pk);
                        else if (in_r < pit->second) pit->second = in_r;

                        double drv_r = in_r - pl_wire_delay(pr.net, o, pr.lbl);
                        auto [dit, dins] = d_drv_req.emplace(pr.net, drv_r);
                        if (!dins && drv_r < dit->second) dit->second = drv_r;
                    }
                }

                for (const auto& pk : dm.d_pin_order) {
                    double v_pr = dm.d_pin_req[pk];
                    auto [git, gins] = pl_pin_req.emplace(pk, v_pr);
                    if (gins) pl_pin_order.push_back(pk);
                    else if (v_pr < git->second) git->second = v_pr;
                }
                dom_sta_meta.push_back(std::move(dm));
            }
        };

        int tot_mm_moved = 0, tot_mm_ff = 0, tot_mm_lut = 0;
        static constexpr std::pair<int, int> STENCIL_DIRS[8] = {
            {-1, -1}, {-1, 0}, {-1, 1}, {0, -1}, {0, 1}, {1, -1}, {1, 0}, {1, 1}
        };
        static constexpr int STEP_SIZES[6] = {32, 16, 8, 4, 2, 1};

        for (int mm_sweep = 0; mm_sweep < 6; ++mm_sweep) {
            compute_placed_sta();
            std::unordered_map<int32_t, double> unit_worst_slk;
            for (const auto& pk : pl_pin_order) {
                double req_p = pl_pin_req[pk];
                uint64_t nt_in = 0;
                for (const auto& pr : inst_in_pins[pk.inst]) if (pr.lbl == pk.lbl) { nt_in = pr.net; break; }
                if (!nt_in || false_nets.count(nt_in) || clock_nets.count(nt_in) || false_nets.count(nt_in)) continue;
                double slk_p = req_p - ((pl_drv_arr.count(nt_in) ? pl_drv_arr[nt_in] : 0.36) + pl_wire_delay(nt_in, pk.inst, pk.lbl));
                if (slk_p >= 2.80) continue;
                int32_t su = inst_to_unit.count(pk.inst) ? inst_to_unit[pk.inst] : -1;
                if (su >= 0 && (unit_kind[su] == 5 || unit_kind[su] == 6 || unit_kind[su] == 7)) {
                    auto [it, ins] = unit_worst_slk.emplace(su, slk_p);
                    if (!ins && slk_p < it->second) it->second = slk_p;
                }
                int32_t du = net_drv_unit.count(nt_in) ? net_drv_unit[nt_in] : -1;
                if (du >= 0 && (unit_kind[du] == 5 || unit_kind[du] == 6 || unit_kind[du] == 7)) {
                    auto [it, ins] = unit_worst_slk.emplace(du, slk_p);
                    if (!ins && slk_p < it->second) it->second = slk_p;
                }
            }
            if (unit_worst_slk.empty()) break;

            std::vector<std::pair<int32_t, double>> cand_units(unit_worst_slk.begin(), unit_worst_slk.end());
            std::sort(cand_units.begin(), cand_units.end(), [](const auto& a, const auto& b) {
                return std::make_pair(a.second, a.first) < std::make_pair(b.second, b.first);
            });
            if (cand_units.size() > 2400) cand_units.resize(2400);

            int sweep_moved = 0;
            for (auto [u, u_slk] : cand_units) {
                int cur_r = ur_py[u], cur_c = uc_py[u];
                int32_t uk = unit_kind[u];
                int min_dist = (u_slk < 0.45) ? 1 : 3;
                double min_gain = (u_slk < 0.45) ? 0.045 : ((u_slk < 0.85) ? 0.14 : 0.25);
                int r_lo_b = 999, r_hi_b = -999, c_lo_b = 999, c_hi_b = -999;
                auto upd_bbox = [&](int rr, int cc) {
                    if (rr < r_lo_b) r_lo_b = rr;
                    if (rr > r_hi_b) r_hi_b = rr;
                    if (cc < c_lo_b) c_lo_b = cc;
                    if (cc > c_hi_b) c_hi_b = cc;
                };

                std::vector<L1Quad> in_env, out_env;
                double cur_w = 0.0, cur_min_in = 99.0;
                int mid_r = cur_r, mid_c = cur_c;

                if (uk == 6) {
                    std::vector<L1Term> in_terms, out_terms;
                    uint64_t ro = 0;
                    if (auto rit = reg_by_uid.find(u); rit != reg_by_uid.end()) {
                        ro = rit->second;
                        for (const auto& pr : inst_in_pins[ro]) {
                            if (!pr.net || check_const(pr.net) || clock_nets.count(pr.net) || false_nets.count(pr.net)) continue;
                            int32_t du = net_drv_unit.count(pr.net) ? net_drv_unit[pr.net] : -1;
                            if (du >= 0 && du != u && pl_pin_req.count({ro, pr.lbl})) {
                                double b_i = pl_pin_req[{ro, pr.lbl}] - (pl_drv_arr.count(pr.net) ? pl_drv_arr[pr.net] : 0.36) - 0.34 - get_cong_ex(ro, pr.lbl);
                                in_terms.push_back({ur_py[du], uc_py[du], net_slope.count(pr.net) ? net_slope[pr.net] : 0.041, b_i});
                                upd_bbox(ur_py[du], uc_py[du]);
                            }
                        }
                    } else if (auto lfit = lut_ff_by_uid.find(u); lfit != lut_ff_by_uid.end()) {
                        uint64_t lo = lfit->second.lo;
                        ro = lfit->second.ro;
                        if (auto rdit = pl_pin_req.find({ro, "D"}); rdit != pl_pin_req.end()) {
                            double req_d = rdit->second;
                            for (const auto& pr : inst_in_pins[lo]) {
                                if (!pr.net || check_const(pr.net) || clock_nets.count(pr.net) || false_nets.count(pr.net)) continue;
                                int32_t du = net_drv_unit.count(pr.net) ? net_drv_unit[pr.net] : -1;
                                if (du >= 0 && du != u) {
                                    double b_i = (req_d - lut4_pin_d(pr.lbl)) - (pl_drv_arr.count(pr.net) ? pl_drv_arr[pr.net] : 0.36) - 0.34 - get_cong_ex(lo, pr.lbl);
                                    in_terms.push_back({ur_py[du], uc_py[du], net_slope.count(pr.net) ? net_slope[pr.net] : 0.041, b_i});
                                    upd_bbox(ur_py[du], uc_py[du]);
                                }
                            }
                        }
                        for (const auto& pr : inst_in_pins[ro]) {
                            if (pr.lbl == "D" || !pr.net || check_const(pr.net) || clock_nets.count(pr.net) || false_nets.count(pr.net)) continue;
                            int32_t du = net_drv_unit.count(pr.net) ? net_drv_unit[pr.net] : -1;
                            if (du >= 0 && du != u && pl_pin_req.count({ro, pr.lbl})) {
                                double b_i = pl_pin_req[{ro, pr.lbl}] - (pl_drv_arr.count(pr.net) ? pl_drv_arr[pr.net] : 0.36) - 0.34 - get_cong_ex(ro, pr.lbl);
                                in_terms.push_back({ur_py[du], uc_py[du], net_slope.count(pr.net) ? net_slope[pr.net] : 0.041, b_i});
                                upd_bbox(ur_py[du], uc_py[du]);
                            }
                        }
                    } else {
                        continue;
                    }

                    for (const auto& opr : inst_out_pins[ro]) {
                        if (!opr.net || check_const(opr.net) || clock_nets.count(opr.net) || false_nets.count(opr.net)) continue;
                        double s_q = net_slope.count(opr.net) ? net_slope[opr.net] : 0.041;
                        double arr_q = pl_drv_arr.count(opr.net) ? pl_drv_arr[opr.net] : 0.36;
                        if (auto sit = net_sinks_inst.find(opr.net); sit != net_sinks_inst.end()) {
                            for (const auto& snk : sit->second) {
                                int32_t su = inst_to_unit.count(snk.inst) ? inst_to_unit[snk.inst] : -1;
                                if (su >= 0 && su != u && pl_pin_req.count({snk.inst, snk.lbl})) {
                                    double b_o = pl_pin_req[{snk.inst, snk.lbl}] - arr_q - 0.34 - get_cong_ex(snk.inst, snk.lbl);
                                    out_terms.push_back({ur_py[su], uc_py[su], s_q, b_o});
                                    upd_bbox(ur_py[su], uc_py[su]);
                                }
                            }
                        }
                    }
                    if (in_terms.empty() && out_terms.empty()) continue;
                    mid_r = static_cast<int>(0.5 * (r_lo_b + r_hi_b));
                    mid_c = static_cast<int>(0.5 * (c_lo_b + c_hi_b));
                    in_env = build_min_l1_env(in_terms);
                    out_env = build_min_l1_env(out_terms);
                    cur_min_in = eval_min_l1_env(in_env, cur_r, cur_c);
                    double cur_out = eval_min_l1_env(out_env, cur_r, cur_c);
                    cur_w = std::min(cur_min_in, cur_out);
                } else if (uk == 7) {
                    auto lit = lut_by_uid.find(u);
                    if (lit == lut_by_uid.end()) continue;
                    uint64_t lo = lit->second;
                    std::vector<L1Term> in_neg_terms, out_terms;
                    for (const auto& pr : inst_in_pins[lo]) {
                        if (!pr.net || check_const(pr.net) || clock_nets.count(pr.net) || false_nets.count(pr.net)) continue;
                        int32_t du = net_drv_unit.count(pr.net) ? net_drv_unit[pr.net] : -1;
                        if (du >= 0 && du != u) {
                            double a0 = (pl_drv_arr.count(pr.net) ? pl_drv_arr[pr.net] : 0.36) + 0.34 + lut4_pin_d(pr.lbl) + get_cong_ex(lo, pr.lbl);
                            in_neg_terms.push_back({ur_py[du], uc_py[du], net_slope.count(pr.net) ? net_slope[pr.net] : 0.041, -a0});
                            upd_bbox(ur_py[du], uc_py[du]);
                        }
                    }
                    for (const auto& opr : inst_out_pins[lo]) {
                        if (!opr.net || check_const(opr.net) || clock_nets.count(opr.net) || false_nets.count(opr.net)) continue;
                        double s_f = net_slope.count(opr.net) ? net_slope[opr.net] : 0.041;
                        if (auto sit = net_sinks_inst.find(opr.net); sit != net_sinks_inst.end()) {
                            for (const auto& snk : sit->second) {
                                int32_t su = inst_to_unit.count(snk.inst) ? inst_to_unit[snk.inst] : -1;
                                if (su >= 0 && su != u && pl_pin_req.count({snk.inst, snk.lbl})) {
                                    double b_o = pl_pin_req[{snk.inst, snk.lbl}] - 0.34 - get_cong_ex(snk.inst, snk.lbl);
                                    out_terms.push_back({ur_py[su], uc_py[su], s_f, b_o});
                                    upd_bbox(ur_py[su], uc_py[su]);
                                }
                            }
                        }
                    }
                    if (in_neg_terms.empty() || out_terms.empty()) continue;
                    mid_r = static_cast<int>(0.5 * (r_lo_b + r_hi_b));
                    mid_c = static_cast<int>(0.5 * (c_lo_b + c_hi_b));
                    in_env = build_min_l1_env(in_neg_terms);
                    out_env = build_min_l1_env(out_terms);
                    cur_w = eval_min_l1_env(out_env, cur_r, cur_c) + eval_min_l1_env(in_env, cur_r, cur_c);
                } else if (uk == 5) {
                    auto mit = mux_by_uid.find(u);
                    if (mit == mux_by_uid.end()) continue;
                    const MuxTreeRec& mrec = mit->second;
                    uint64_t root_o = mrec.root;
                    double leaf_mux_d = (mrec.lvl == 6) ? 0.390 : 0.195;
                    std::vector<L1Term> in_neg_terms, out_terms;
                    for (uint64_t mo : mrec.members) {
                        for (const auto& pr : inst_in_pins[mo]) {
                            if (!pr.net || packed_zero_wire_nets.count(pr.net) || check_const(pr.net) || clock_nets.count(pr.net) || false_nets.count(pr.net)) continue;
                            int32_t du = net_drv_unit.count(pr.net) ? net_drv_unit[pr.net] : -1;
                            if (du >= 0 && du != u) {
                                double int_d = (mo == root_o) ? mux2_pin_d(pr.lbl)
                                             : (inst_type[mo] == 2) ? (mux2_pin_d(pr.lbl) + 0.195)
                                             : (lut4_pin_d(pr.lbl) + leaf_mux_d);
                                double a0 = (pl_drv_arr.count(pr.net) ? pl_drv_arr[pr.net] : 0.36) + 0.34 + int_d + get_cong_ex(mo, pr.lbl);
                                in_neg_terms.push_back({ur_py[du], uc_py[du], net_slope.count(pr.net) ? net_slope[pr.net] : 0.041, -a0});
                                upd_bbox(ur_py[du], uc_py[du]);
                            }
                        }
                    }
                    for (const auto& opr : inst_out_pins[root_o]) {
                        if (!opr.net || check_const(opr.net) || clock_nets.count(opr.net) || false_nets.count(opr.net)) continue;
                        double s_f = net_slope.count(opr.net) ? net_slope[opr.net] : 0.041;
                        if (auto sit = net_sinks_inst.find(opr.net); sit != net_sinks_inst.end()) {
                            for (const auto& snk : sit->second) {
                                int32_t su = inst_to_unit.count(snk.inst) ? inst_to_unit[snk.inst] : -1;
                                if (su >= 0 && su != u && pl_pin_req.count({snk.inst, snk.lbl})) {
                                    double b_o = pl_pin_req[{snk.inst, snk.lbl}] - 0.34 - get_cong_ex(snk.inst, snk.lbl);
                                    out_terms.push_back({ur_py[su], uc_py[su], s_f, b_o});
                                    upd_bbox(ur_py[su], uc_py[su]);
                                }
                            }
                        }
                    }
                    if (in_neg_terms.empty() || out_terms.empty()) continue;
                    mid_r = static_cast<int>(0.5 * (r_lo_b + r_hi_b));
                    mid_c = static_cast<int>(0.5 * (c_lo_b + c_hi_b));
                    in_env = build_min_l1_env(in_neg_terms);
                    out_env = build_min_l1_env(out_terms);
                    cur_w = eval_min_l1_env(out_env, cur_r, cur_c) + eval_min_l1_env(in_env, cur_r, cur_c);
                } else {
                    continue;
                }

                auto eval_raw = [&](int r, int c) -> std::pair<double, double> {
                    double mi = eval_min_l1_env(in_env, r, c);
                    double mo = eval_min_l1_env(out_env, r, c);
                    return (uk == 6) ? std::pair{std::min(mi, mo), mi} : std::pair{mo + mi, 99.0};
                };
                auto eval_obj = [&](int r, int c) -> double {
                    return eval_raw(r, c).first - 1e-5 * (std::abs(r - mid_r) + std::abs(c - mid_c));
                };

                int best_r = cur_r, best_c = cur_c;
                double best_val = eval_obj(cur_r, cur_c);
                int seed_r = std::max(1, std::min(109, mid_r));
                int seed_c = std::max(1, std::min(182, mid_c));
                double v_s = eval_obj(seed_r, seed_c);
                if (v_s > best_val) { best_val = v_s; best_r = seed_r; best_c = seed_c; }

                for (int step_sz : STEP_SIZES) {
                    for (int rep = 0; rep < 4; ++rep) {
                        bool improved = false;
                        for (auto [dr_s, dc_s] : STENCIL_DIRS) {
                            int nr = std::max(1, std::min(109, best_r + dr_s * step_sz));
                            int nc = std::max(1, std::min(182, best_c + dc_s * step_sz));
                            double nv = eval_obj(nr, nc);
                            if (nv > best_val + 1e-6) {
                                best_val = nv; best_r = nr; best_c = nc; improved = true;
                            }
                        }
                        if (!improved) break;
                    }
                }

                if (std::abs(best_r - cur_r) + std::abs(best_c - cur_c) < min_dist) continue;
                if (eval_raw(best_r, best_c).first - cur_w < min_gain) continue;

                int16_t cand_r = 0, cand_c = 0, cand_s = 0;
                if (bridge.find_legal_slot(u, best_r, best_c, 6, unit_kind.data(), unit_sub.data(), unit_cs.data(), unit_c60.data(),
                                           out_r.data(), out_c.data(), &cand_r, &cand_c, &cand_s) != 1) {
                    continue;
                }
                if (std::abs(cand_r - cur_r) + std::abs(cand_c - cur_c) < min_dist) continue;
                auto [hw_w, hw_min_in] = eval_raw(cand_r, cand_c);
                if (uk == 6 && hw_min_in < std::min(1.00, cur_min_in - 0.05)) continue;
                if (hw_w < cur_w + min_gain) continue;

                if (bridge.commit_unit_move(u, cand_r, cand_c, cand_s, unit_kind.data(), unit_sub.data(), unit_cs.data(), unit_c60.data(),
                                            out_r.data(), out_c.data(), out_slot.data()) == 1) {
                    ur_py[u] = cand_r;
                    uc_py[u] = cand_c;
                    ++sweep_moved;
                    ++tot_mm_moved;
                    if (uk == 6) ++tot_mm_ff; else ++tot_mm_lut;
                }
            }
            if (sweep_moved == 0) break;
        }

        // Refresh router criticalities using post-placement geometry slacks
        compute_placed_sta();
        for (const auto& dm : dom_sta_meta) {
            double raw_dom_per = domain_periods.count(dm.dom_nt) ? domain_periods[dm.dom_nt] : 12.5;
            double pl_cap = (raw_dom_per >= 100.0 && (raw_dom_per - dm.max_arr) > 50.0) ? 0.30
                          : (dm.worst_slk >= std::max(1.5, 0.18 * raw_dom_per)) ? 0.45 : 0.99;
            double pl_span = std::max(3.0, std::min(9.5, dm.max_arr * 0.75));
            for (const auto& pk : dm.d_pin_order) {
                double req_p = dm.d_pin_req.at(pk);
                uint64_t nt_in = 0;
                for (const auto& pr : inst_in_pins[pk.inst]) if (pr.lbl == pk.lbl) { nt_in = pr.net; break; }
                if (!nt_in || false_nets.count(nt_in) || clock_nets.count(nt_in) || check_const(nt_in)) continue;
                double slk_p = req_p - ((pl_drv_arr.count(nt_in) ? pl_drv_arr[nt_in] : 0.36) + pl_wire_delay(nt_in, pk.inst, pk.lbl));
                double rel_slk = std::max(0.0, slk_p - dm.worst_slk);
                double pl_cc = std::min(pl_cap, std::max(0.05, pl_cap * (1.0 - rel_slk / pl_span)));
                if (pl_cap > 0.90 && slk_p < 2.20) {
                    pl_cc = std::max(pl_cc, std::min(0.98, 0.72 + 0.12 * std::max(0.0, 2.20 - slk_p)));
                }
                auto [cit, cins] = pin_crit_map.emplace(pk, pl_cc);
                if (!cins && pl_cc > cit->second) cit->second = pl_cc;
                if (pl_cc > net_crit_map[nt_in]) net_crit_map[nt_in] = pl_cc;
            }
        }
        std::fprintf(stdout, "[ownpr] Post-placement minimax slack relocation moved %d units (FF=%d, LUT=%d) in %.2fs\n",
                     tot_mm_moved, tot_mm_ff, tot_mm_lut, perf_counter() - t_mm0);
        std::fflush(stdout);

        // =====================================================================
        // 5. Bind All Placed Fabric & Hard-Stripe Instances into gowin.dll
        // =====================================================================
        double t_bind0 = perf_counter();
        auto fn_setphy_bs = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setPhyBsram@Bsram@NETLIST@@QEAAXPEAVbsram@2@@Z"));
        auto fn_sp2n_bs   = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Bsram@NETLIST@@UEAAXXZ"));
        for (size_t i = 0; i < bsram_objs.size(); ++i) {
            uint64_t bo = bsram_objs[i];
            int16_t rb = out_r[bsram_uids[i]], cb = out_c[bsram_uids[i]];
            uint64_t phy_bs = fn_get_bsram(chip, rb, cb, 0);
            fn_setphy(bo, phy_bs);
            fn_setlogic(phy_bs, bo);
            fn_setphy_bs(bo, phy_bs);
            wr_ptr(phy_bs + 0x678, bo);
            fn_addgrid(fn_getgrid(chip, rb, cb), bo);
            fn_sp2n_bs(bo);
        }

        auto fn_setphy_dsp = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setNewPhyDsp@NewDsp@NETLIST@@QEAAXPEAVnewDsp@2@@Z"));
        auto fn_sp2n_dsp   = reinterpret_cast<void (*)(uint64_t)>(gw_opt("?setPin2Node@NewDsp@NETLIST@@UEAAXXZ"));
        auto call_dsp_sp2n = [&](uint64_t do_o) {
            if (fn_sp2n_dsp) fn_sp2n_dsp(do_o);
            else reinterpret_cast<void (*)(uint64_t)>(rd_ptr(rd_ptr(do_o) + 8 * 9))(do_o);
        };

        for (size_t i = 0; i < dsp_wide_objs.size(); ++i) {
            uint64_t do_o = dsp_wide_objs[i];
            int16_t rd = out_r[dsp_wide_uids[i]], cd = out_c[dsp_wide_uids[i]];
            uint64_t par1 = fn_get_dsp(chip, rd, cd);
            uint64_t par2 = fn_get_dsp(chip, rd, static_cast<int16_t>(cd + 3));
            fn_setphy(do_o, rd_ptr(par1 + 64));
            fn_setlogic(rd_ptr(par1 + 64), do_o);
            fn_setlogic(rd_ptr(par2 + 64), do_o);
            fn_setphy_dsp(do_o, par1);
            fn_addgrid(fn_getgrid(chip, rd, cd), do_o);
            fn_addgrid(fn_getgrid(chip, rd, static_cast<int16_t>(cd + 3)), do_o);
            call_dsp_sp2n(do_o);
        }

        for (size_t i = 0; i < dsp_single_objs.size(); ++i) {
            uint64_t do_o = dsp_single_objs[i];
            int16_t rd = out_r[dsp_single_uids[i]], cd = out_c[dsp_single_uids[i]], sl = out_slot[dsp_single_uids[i]];
            uint64_t par = fn_get_dsp(chip, rd, cd);
            int sb = inst_sub[do_o];
            uint64_t off = 48;
            if (sb == 187) off = (sl == 0) ? 32 : 40;
            else if (sb == 185) off = 48;
            else if (sb == 186) off = 56;
            else {
                for (uint64_t k : {32ULL, 40ULL, 48ULL, 56ULL, 64ULL}) {
                    if (rd_ptr(par + k) && rd_i16_mem(rd_ptr(par + k) + 0x10) == sb) { off = k; break; }
                }
            }
            uint64_t sub_p = rd_ptr(par + off);
            fn_setphy(do_o, sub_p);
            fn_setlogic(sub_p, do_o);
            fn_setphy_dsp(do_o, par);
            fn_addgrid(fn_getgrid(chip, rd, cd), do_o);
            call_dsp_sp2n(do_o);
        }

        auto fn_setphylut   = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setPhyLut4@Lut4@NETLIST@@QEAAXPEAVlut4@2@@Z"));
        auto fn_getinit_lut = reinterpret_cast<void* (*)(uint64_t, void*)>(gw_req("?getInitValue@Lut4@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ"));
        auto fn_setinit_lut = reinterpret_cast<void (*)(uint64_t, void*)>(gw_req("?setInitValueForRouted@Lut4@NETLIST@@QEAAXV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z"));
        auto fn_sp2n_lut    = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Lut4@NETLIST@@UEAAXXZ"));
        auto fn_setphymux   = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setPhyMux2@Mux2@NETLIST@@QEAAXPEAVmux2@2@@Z"));
        auto fn_sp2n_mux    = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Mux2@NETLIST@@UEAAXXZ"));
        auto fn_setphyalu   = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setPhyAlu@Alu@NETLIST@@QEAAXPEAValu@2@@Z"));
        auto fn_sp2n_alu    = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Alu@NETLIST@@UEAAXXZ"));
        auto fn_setphyreg   = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setPhyReg@Reg@NETLIST@@QEAAXPEAVreg@2@@Z"));
        auto fn_sp2n_reg    = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Reg@NETLIST@@UEAAXXZ"));

        auto bind_lut_at = [&](uint64_t lo, int16_t r, int16_t c, int16_t s) {
            uint64_t g = fn_getgrid(chip, r, c);
            uint64_t agg = rd_ptr(g + 0x70);
            uint64_t lp = rd_ptr(agg + 8 * s);
            fn_setphy(lo, lp);
            fn_setlogic(lp, lo);
            fn_setphylut(lo, lp);
            fn_addgrid(g, lo);
            alignas(8) uint8_t ibuf[64]{};
            fn_getinit_lut(lo, ibuf);
            std::string init_s = gw_read_std_str(ibuf);
            int width = inst_sub[lo] - 20;
            uint16_t w16 = parse_lut_init_w16(init_s, width);
            alignas(8) uint8_t sbuf[32]{};
            gw_make_sso_str(sbuf, init_s);
            fn_setinit_lut(lo, sbuf);
            wr_u16_mem(lp + 0x20, w16);
            wr_u8_mem(lp + 0x22, 0U);
            wr_u8_mem(agg + 0x326 + s, 1U);
            fn_sp2n_lut(lo);
        };

        std::vector<std::pair<int32_t, uint64_t>> fixed_root_pairs;
        std::unordered_set<uint64_t> skip_sink_pins, internal_only_nets;
        struct CinP2r { uint64_t nt = 0, b = 0, d = 0; };
        struct CoutP2r { uint64_t nt = 0, p = 0; };
        std::vector<CinP2r> cin_p2r_items;
        std::vector<CoutP2r> cout_p2r_items;
        std::unordered_map<uint64_t, std::vector<int32_t>> cout_src_nodes, cin_extra_sinks;

        struct MuxBoundRec {
            int16_t rm = 0, cm = 0;
            std::vector<std::pair<uint64_t, int>> l_slots, m_slots;
        };
        std::vector<MuxBoundRec> mux_bound_records;

        for (size_t i = 0; i < mux_tree_records.size(); ++i) {
            const auto& rec = mux_tree_records[i];
            int16_t rm = out_r[mux_uids[i]], cm = out_c[mux_uids[i]], root_sl = out_slot[mux_uids[i]];
            uint64_t gm = fn_getgrid(chip, rm, cm);
            uint64_t agg_m = rd_ptr(gm + 0x70);
            MuxBoundRec mb{rm, cm, {}, {}};
            std::function<void(uint64_t, int, int)> assign_mux = [&](uint64_t cur_m, int lvl, int sl) {
                mb.m_slots.emplace_back(cur_m, sl);
                const auto& mc = mux_children_map[cur_m];
                auto [c0_ls, c1_ls] = mux_child_slots(lvl, sl);
                if (lvl == 5) {
                    mb.l_slots.emplace_back(mc.d0_o, c0_ls.second);
                    mb.l_slots.emplace_back(mc.d1_o, c1_ls.second);
                } else {
                    assign_mux(mc.d0_o, lvl - 1, c0_ls.second);
                    assign_mux(mc.d1_o, lvl - 1, c1_ls.second);
                }
            };
            assign_mux(rec.root, rec.lvl, root_sl);
            for (auto [lo, sl] : mb.l_slots) {
                bind_lut_at(lo, rm, cm, static_cast<int16_t>(sl));
                uint64_t cls_grp = rd_ptr(agg_m + 0x360 + 8 * (sl / 2));
                if (cls_grp) wr_i16_mem(cls_grp + 6, 9);
            }
            auto sorted_m = mb.m_slots;
            std::stable_sort(sorted_m.begin(), sorted_m.end(), [&](const auto& a, const auto& b) {
                return inst_sub[a.first] < inst_sub[b.first];
            });
            for (auto [mo, sl] : sorted_m) {
                uint64_t mp = rd_ptr(agg_m + 0x150 + 8 * sl);
                fn_setphy(mo, mp);
                fn_setlogic(mp, mo);
                fn_setphymux(mo, mp);
                wr_ptr(mp + 0x18, mo);
                fn_addgrid(gm, mo);
                fn_sp2n_mux(mo);
            }
            mux_bound_records.push_back(std::move(mb));
            for (const auto& lk : rec.links) {
                skip_sink_pins.insert(lk.snk_p);
                uint64_t nd_p = rd_ptr(lk.src_p + 0x18);
                if (!nd_p) throw std::runtime_error("Mux dedicated child output lacks p+0x18 node");
                int32_t nid = bridge.ptr_to_id(nd_p);
                if (nid < 0) throw std::runtime_error("Mux dedicated child node absent from RRG");
                fixed_root_pairs.emplace_back(nid, lk.nt);
                if (net_in_cnt[lk.nt] == 1) internal_only_nets.insert(lk.nt);
            }
        }

        // Bind ALU_CHAINs
        for (size_t i = 0; i < alu_chain_records.size(); ++i) {
            const auto& rec = alu_chain_records[i];
            int16_t ra = out_r[alu_uids[i]], c0 = out_c[alu_uids[i]];
            size_t L = rec.chain.size();
            for (uint64_t ic : rec.internal_carries) internal_only_nets.insert(ic);
            if (rec.unused_tail_co) internal_only_nets.insert(rec.unused_tail_co);

            if (rec.cin_fab_nt) {
                int16_t cw = static_cast<int16_t>(c0 - 1);
                uint64_t gw = fn_getgrid(chip, ra, cw);
                uint64_t agg_w = rd_ptr(gw + 0x70);
                uint64_t pl_w5 = rd_ptr(agg_w + 8 * 5);
                wr_u8_mem(agg_w + 0x318 + 5, 1U);
                wr_u8_mem(agg_w + 0x326 + 5, 1U);
                wr_u16_mem(pl_w5 + 0x20, 0x30CCU);
                wr_u8_mem(pl_w5 + 0x22, 2U);
                int32_t b5_id = bridge.coord_to_id(ra, cw, 21);
                int32_t d5_id = bridge.coord_to_id(ra, cw, 23);
                cin_extra_sinks[rec.cin_fab_nt].push_back(b5_id);
                cin_extra_sinks[rec.cin_fab_nt].push_back(d5_id);
                double c_cin = conn_crit_of(rec.cin_fab_nt, rec.chain[0], "CIN");
                sink_nid_crit[{rec.cin_fab_nt, b5_id}] = std::max(sink_nid_crit[{rec.cin_fab_nt, b5_id}], c_cin);
                sink_nid_crit[{rec.cin_fab_nt, d5_id}] = std::max(sink_nid_crit[{rec.cin_fab_nt, d5_id}], c_cin);
                cin_p2r_items.push_back({rec.cin_fab_nt, bridge.id_to_ptr(b5_id), bridge.id_to_ptr(d5_id)});
                for (const auto& pr : inst_in_pins[rec.chain[0]]) if (pr.lbl == "CIN") skip_sink_pins.insert(pr.pin);
            }

            for (size_t k = 0; k < L; ++k) {
                uint64_t ao = rec.chain[k];
                int16_t ca = static_cast<int16_t>(c0 + (k / 6));
                int16_t sa = static_cast<int16_t>(k % 6);
                uint64_t ga = fn_getgrid(chip, ra, ca);
                uint64_t agg_a = rd_ptr(ga + 0x70);
                uint64_t pa_alu = rd_ptr(agg_a + 0x40 + 8 * sa);
                fn_setphy(ao, pa_alu);
                fn_setlogic(pa_alu, ao);
                fn_setphyalu(ao, pa_alu);
                wr_ptr(pa_alu + 0x20, ao);
                fn_addgrid(ga, ao);
                uint64_t cls_grp = rd_ptr(agg_a + 0x360 + 8 * (sa / 2));
                if (cls_grp) {
                    wr_i16_mem(cls_grp + 6, 10);
                    wr_i16_mem(cls_grp + 6 + 2 * (16 + (sa & 1)), 10);
                }
                wr_u8_mem(agg_a + 0x318 + sa, 1U);
                fn_sp2n_alu(ao);

                uint64_t ro = rec.paired_regs[k];
                if (ro) {
                    uint64_t pa_reg = rd_ptr(agg_a + 0xa0 + 8 * sa);
                    fn_setphy(ro, pa_reg);
                    fn_setlogic(pa_reg, ro);
                    fn_setphyreg(ro, pa_reg);
                    fn_addgrid(ga, ro);
                    wr_u8_mem(agg_a + 0x318 + sa, 1U);
                    fn_sp2n_reg(ro);
                    if (reg_clk_inv[ro] && cls_grp) wr_i16_mem(cls_grp, 0);
                    uint64_t s_nt = alu_sum[ao];
                    int32_t f_id = bridge.coord_to_id(ra, ca, static_cast<int16_t>(32 + sa));
                    fixed_root_pairs.emplace_back(f_id, s_nt);
                    internal_only_nets.insert(s_nt);
                }
                for (const auto& pr : inst_out_pins[ao]) if (pr.lbl == "COUT") skip_sink_pins.insert(pr.pin);
                if (k > 0) for (const auto& pr : inst_in_pins[ao]) if (pr.lbl == "CIN") skip_sink_pins.insert(pr.pin);
            }

            if (rec.cout_fab_nt) {
                int16_t c_ex = static_cast<int16_t>(c0 + (L / 6));
                int16_t s_ex = static_cast<int16_t>(L % 6);
                uint64_t g_ex = fn_getgrid(chip, ra, c_ex);
                uint64_t agg_ex = rd_ptr(g_ex + 0x70);
                uint64_t pl_ex = rd_ptr(agg_ex + 8 * s_ex);
                wr_u8_mem(agg_ex + 0x318 + s_ex, 1U);
                wr_u8_mem(agg_ex + 0x326 + s_ex, 1U);
                wr_u16_mem(pl_ex + 0x20, 0x0000U);
                wr_u8_mem(pl_ex + 0x22, 2U);
                int32_t ex_id = bridge.coord_to_id(ra, c_ex, static_cast<int16_t>(32 + s_ex));
                cout_src_nodes[rec.cout_fab_nt].push_back(ex_id);
                cout_p2r_items.push_back({rec.cout_fab_nt, bridge.id_to_ptr(ex_id)});
            }
        }

        // Bind LUT+FF pairs
        for (size_t i = 0; i < lut_ff_records.size(); ++i) {
            const auto& lf = lut_ff_records[i];
            int16_t rl = out_r[lut_ff_uids[i]], cl = out_c[lut_ff_uids[i]], sl = out_slot[lut_ff_uids[i]];
            bind_lut_at(lf.lo, rl, cl, sl);
            uint64_t g = fn_getgrid(chip, rl, cl);
            uint64_t agg = rd_ptr(g + 0x70);
            uint64_t rp_reg = rd_ptr(agg + 0xa0 + 8 * sl);
            fn_setphy(lf.ro, rp_reg);
            fn_setlogic(rp_reg, lf.ro);
            fn_setphyreg(lf.ro, rp_reg);
            fn_addgrid(g, lf.ro);
            wr_u8_mem(agg + 0x318 + sl, 1U);
            fn_sp2n_reg(lf.ro);
            if (reg_clk_inv[lf.ro]) {
                uint64_t cls_grp = rd_ptr(agg + 0x360 + 8 * (sl / 2));
                if (cls_grp) wr_i16_mem(cls_grp, 0);
            }
            int32_t f_id = bridge.coord_to_id(rl, cl, static_cast<int16_t>(32 + sl));
            fixed_root_pairs.emplace_back(f_id, lf.f_nt);
            internal_only_nets.insert(lf.f_nt);
        }

        // Bind standalone Regs
        for (size_t i = 0; i < standalone_regs.size(); ++i) {
            uint64_t ro = standalone_regs[i];
            int16_t rr = out_r[reg_only_uids[i]], cr = out_c[reg_only_uids[i]], sr = out_slot[reg_only_uids[i]];
            uint64_t g = fn_getgrid(chip, rr, cr);
            uint64_t agg = rd_ptr(g + 0x70);
            uint64_t rp_reg = rd_ptr(agg + 0xa0 + 8 * sr);
            uint64_t lp = rd_ptr(agg + 8 * sr);
            fn_setphy(ro, rp_reg);
            fn_setlogic(rp_reg, ro);
            fn_setphyreg(ro, rp_reg);
            fn_addgrid(g, ro);
            wr_u8_mem(agg + 0x318 + sr, 1U);
            wr_u8_mem(agg + 0x326 + sr, 1U);
            wr_u16_mem(lp + 0x20, 0xFF00U);
            wr_u8_mem(lp + 0x22, 1U);
            fn_sp2n_reg(ro);
            if (reg_clk_inv[ro]) {
                uint64_t cls_grp = rd_ptr(agg + 0x360 + 8 * (sr / 2));
                if (cls_grp) wr_i16_mem(cls_grp, 0);
            }
        }

        // Bind standalone LUTs
        for (size_t i = 0; i < standalone_luts.size(); ++i) {
            bind_lut_at(standalone_luts[i], out_r[lut_only_uids[i]], out_c[lut_only_uids[i]], out_slot[lut_only_uids[i]]);
        }

        // Bind standalone Dls (CFU latches, type=70)
        if (!standalone_dls.empty()) {
            auto fn_setphydl = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setPhyDl@Dl@NETLIST@@QEAAXPEAVdl@2@@Z"));
            auto fn_sp2n_dl  = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Dl@NETLIST@@UEAAXXZ"));
            for (size_t i = 0; i < standalone_dls.size(); ++i) {
                uint64_t dlo = standalone_dls[i];
                int16_t rr = out_r[dl_only_uids[i]], cr = out_c[dl_only_uids[i]], sr = out_slot[dl_only_uids[i]];
                uint64_t g = fn_getgrid(chip, rr, cr);
                uint64_t agg = rd_ptr(g + 0x70);
                uint64_t rp_dl = rd_ptr(agg + 0x110 + 8 * sr);
                uint64_t lp = rd_ptr(agg + 8 * sr);
                fn_setphy(dlo, rp_dl);
                fn_setlogic(rp_dl, dlo);
                fn_setphydl(dlo, rp_dl);
                fn_addgrid(g, dlo);
                wr_u8_mem(agg + 0x318 + sr, 1U);
                wr_u8_mem(agg + 0x326 + sr, 1U);
                wr_u16_mem(lp + 0x20, 0xFF00U);
                wr_u8_mem(lp + 0x22, 1U);
                fn_sp2n_dl(dlo);
            }
        }

        // Bind distributed SSRAM (RAM16*, type=6, sb=92..97) & ROM16 (type=6, sb=98)
        if (!ram16_objs.empty() || !rom16_objs.empty()) {
            auto fn_setphy_ss = reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setPhySsram@Ssram@NETLIST@@QEAAXPEAVssram@2@@Z"));
            auto fn_sp2n_ss   = reinterpret_cast<void (*)(uint64_t)>(gw_req("?setPin2Node@Ssram@NETLIST@@UEAAXXZ"));
            for (size_t i = 0; i < ram16_objs.size(); ++i) {
                uint64_t sso = ram16_objs[i];
                int16_t rs = out_r[ram16_uids[i]], cs = out_c[ram16_uids[i]];
                uint64_t g = fn_getgrid(chip, rs, cs);
                uint64_t agg = rd_ptr(g + 0x70);
                uint64_t phy_ss = rd_ptr(agg + 0x250);
                if (!phy_ss) throw std::runtime_error("CFU tile (" + std::to_string(rs) + "," + std::to_string(cs) + ") lacks physical SSRAM block for " + inst_name[sso]);
                fn_setphy(sso, phy_ss);
                fn_setlogic(phy_ss, sso);
                fn_setphy_ss(sso, phy_ss);
                fn_addgrid(g, sso);
                for (int k = 0; k < 6; ++k) wr_u8_mem(agg + 0x318 + k, 1U);
                wr_u8_mem(agg + 0x32a, 1U);
                wr_u8_mem(agg + 0x32b, 1U);
                for (int k = 0; k < 4; ++k) {
                    uint64_t cls_grp = rd_ptr(agg + 0x360 + 8 * k);
                    if (cls_grp) wr_i16_mem(cls_grp + 6, 11);
                }
                fn_sp2n_ss(sso);
            }
            for (size_t i = 0; i < rom16_objs.size(); ++i) {
                uint64_t rmo = rom16_objs[i];
                int16_t rr = out_r[rom16_uids[i]], cr = out_c[rom16_uids[i]], sr = out_slot[rom16_uids[i]];
                uint64_t g = fn_getgrid(chip, rr, cr);
                uint64_t agg = rd_ptr(g + 0x70);
                uint64_t phy_rom = rd_ptr(agg + 0x258 + 8 * sr);
                if (!phy_rom) throw std::runtime_error("CFU tile (" + std::to_string(rr) + "," + std::to_string(cr) + ") lacks physical ROM16 slot for " + inst_name[rmo]);
                fn_setphy(rmo, phy_rom);
                fn_setlogic(phy_rom, rmo);
                fn_addgrid(g, rmo);
                wr_u8_mem(agg + 0x318 + sr, 1U);
                fn_sp2n_ss(rmo);
            }
        }

        // Populate P2R carry RB-trees
        int32_t n_cin = static_cast<int32_t>(cin_p2r_items.size());
        int32_t n_cout = static_cast<int32_t>(cout_p2r_items.size());
        std::vector<uint64_t> cin_n(std::max(1, n_cin), 0), cin_b(std::max(1, n_cin), 0), cin_d(std::max(1, n_cin), 0);
        for (int32_t i = 0; i < n_cin; ++i) { cin_n[i] = cin_p2r_items[i].nt; cin_b[i] = cin_p2r_items[i].b; cin_d[i] = cin_p2r_items[i].d; }
        std::vector<uint64_t> cout_n(std::max(1, n_cout), 0), cout_p(std::max(1, n_cout), 0);
        for (int32_t i = 0; i < n_cout; ++i) { cout_n[i] = cout_p2r_items[i].nt; cout_p[i] = cout_p2r_items[i].p; }
        if (bridge.populate_p2r_carries(p2r_ptr, cin_n.data(), cin_b.data(), cin_d.data(), n_cin, cout_n.data(), cout_p.data(), n_cout) != 0) {
            throw std::runtime_error("bridge_populate_p2r_carries failed");
        }
        std::fprintf(stdout, "[ownpr] Bound all instances + P2R carries (cin=%d, cout=%d) in %.2fs\n",
                     n_cin, n_cout, perf_counter() - t_bind0);
        std::fflush(stdout);

        // =====================================================================
        // 6. Batch-Query Pin RRG Nodes & Build Routing Problem
        // =====================================================================
        double t_q0 = perf_counter();
        auto fn_is_direct = reinterpret_cast<bool (*)(uint64_t, uint64_t)>(gw_req("?isDirectConn@Pin@NETLIST@@QEAA_NPEAVglobalMem@@@Z"));

        for (uint64_t nt : intra_ioblk_nets) internal_only_nets.insert(nt);
        for (uint64_t sp : co_ioblk_sink_pins) skip_sink_pins.insert(sp);

        std::unordered_set<uint64_t> iob_ext_nets;
        for (uint64_t io_o : iob_objs) {
            for (const auto* plist : {&inst_in_pins[io_o], &inst_out_pins[io_o]}) {
                for (const auto& pr : *plist) if (iob_ext_pins.count(pr.pin) && pr.net) iob_ext_nets.insert(pr.net);
            }
        }

        std::unordered_map<uint64_t, std::unordered_set<int>> unread_do_bits_by_inst;
        for (uint64_t o_br : insts) {
            if (inst_type[o_br] == 5 || (inst_type[o_br] == 6 && inst_sub[o_br] != 98)) {
                std::unordered_set<int> ubits, u_doa, u_dob;
                bool has_single_do = false, has_dual_do = false;
                for (const auto& pr : inst_out_pins[o_br]) {
                    int bidx = -1;
                    bool unread = (!pr.net || (net_sinks_inst[pr.net].empty() && !iob_ext_nets.count(pr.net)));
                    if (parse_bracketed_index(pr.lbl, "DO", bidx)) {
                        has_single_do = true;
                        if (unread) ubits.insert(bidx);
                    } else if (parse_bracketed_index(pr.lbl, "DOA", bidx)) {
                        has_dual_do = true;
                        if (unread) u_doa.insert(bidx);
                    } else if (parse_bracketed_index(pr.lbl, "DOB", bidx)) {
                        has_dual_do = true;
                        if (unread) u_dob.insert(bidx);
                    }
                }
                if (has_single_do) {
                    unread_do_bits_by_inst[o_br] = std::move(ubits);
                } else if (has_dual_do) {
                    for (int b : u_doa) if (u_dob.count(b)) ubits.insert(b);
                    unread_do_bits_by_inst[o_br] = std::move(ubits);
                }
            }
        }

        auto fn_get_iob_dqs_grp    = reinterpret_cast<int16_t (*)(uint64_t)>(gw_req("?getDqs@iobuf@NETLIST@@QEAAFXZ"));
        auto fn_get_iob_drived_dqs = reinterpret_cast<bool (*)(uint64_t)>(gw_req("?getDrivedDqs@iobuf@NETLIST@@QEAA_NXZ"));
        auto phy_io_of = [&](uint64_t io_obj) -> uint64_t {
            return rd_ptr(rd_ptr(iob_blk_map[io_obj].blk + 0x10));
        };

        struct DqsLaneInfo {
            int r_dq = 0, c_dq = 0, r_io = 0, c_io = 0, bank = 0, dqs_group = 0;
            bool drived_dqs = false;
            std::string iob_inst, iob_pad;
        };
        std::unordered_map<uint64_t, DqsLaneInfo> dqs_lane_info;
        for (const auto& [dqs_o, dc] : dqs_assign) {
            uint64_t dq_io_o = 0;
            for (const auto& pr : inst_in_pins[dqs_o]) {
                if (pr.lbl == "DQSIN" && net_to_iob.count(pr.net)) { dq_io_o = net_to_iob[pr.net]; break; }
            }
            if (!dq_io_o) throw std::runtime_error("DQS " + inst_name[dqs_o] + " lacks DQSIN IOBUF");
            uint64_t p_io = phy_io_of(dq_io_o);
            bool drv_flag = fn_get_iob_drived_dqs(p_io);
            int grp_id = fn_get_iob_dqs_grp(p_io);
            int bk_id = fn_get_bank_io(p_io);
            const auto& ia = iob_assign[dq_io_o];
            if (!drv_flag || grp_id < 0 || dc.c != ia.c || std::abs(dc.r - ia.r) > 5) {
                throw std::runtime_error("Illegal DQS placement/lane binding for " + inst_name[dqs_o]);
            }
            dqs_lane_info[dqs_o] = {dc.r, dc.c, ia.r, ia.c, bk_id, grp_id, drv_flag, inst_name[dq_io_o], ia.pad_p};
        }

        std::map<std::string, int64_t> pin_inv_counts, exemption_rule_counts;
        std::vector<JsonValue> pin_exemption_witnesses;
        std::vector<uint64_t> all_routed_pins, query_pins;
        struct QueryMeta { uint64_t o = 0; int t = 0, sb = 0; bool is_out = false; std::string lbl; uint64_t p = 0, nt = 0; };
        std::vector<QueryMeta> query_meta;

        for (uint64_t o : insts) {
            int t = inst_type[o];
            if (t == 166 || t == 167) continue;
            int sb = inst_sub[o];
            for (bool is_out : {false, true}) {
                const auto& pin_list = is_out ? inst_out_pins[o] : inst_in_pins[o];
                for (const auto& pr : pin_list) {
                    if (!pr.net) {
                        pin_inv_counts["unconnected_pins"]++;
                        continue;
                    }
                    if (internal_only_nets.count(pr.net) || skip_sink_pins.count(pr.pin)) {
                        pin_inv_counts["dedicated_fabric_carry_mux_ff_exempt"]++;
                        all_routed_pins.push_back(pr.pin);
                        continue;
                    }
                    if (t == 106 && !is_out && pr.lbl == "CIN" && check_const(pr.net) != 0) {
                        pin_inv_counts["alu_const_cin_exempt"]++;
                        all_routed_pins.push_back(pr.pin);
                        continue;
                    }
                    if (t == 106 && is_out && pr.lbl == "COUT") {
                        pin_inv_counts["alu_dedicated_cout_exempt"]++;
                        all_routed_pins.push_back(pr.pin);
                        continue;
                    }
                    if ((t == 3 || t == 7) && fn_is_direct(pr.pin, mem)) {
                        uint64_t own_io = iob_owner_of(o);
                        bool peers_ok = (own_io != 0);
                        if (peers_ok && net_drv.count(pr.net) && iob_owner_of(net_drv[pr.net].inst) != own_io) peers_ok = false;
                        if (peers_ok && net_sinks_inst.count(pr.net)) {
                            for (const auto& snk : net_sinks_inst[pr.net]) {
                                if (iob_owner_of(snk.inst) != own_io) { peers_ok = false; break; }
                            }
                        }
                        if (peers_ok) {
                            pin_inv_counts["direct_iob_iologic_exempt"]++;
                            all_routed_pins.push_back(pr.pin);
                            continue;
                        }
                    }
                    if (iob_ext_pins.count(pr.pin) && iob_assign.count(o) && pad_to_rc.count(iob_assign[o].pad_p)) {
                        pin_inv_counts["external_pad_exempt"]++;
                        all_routed_pins.push_back(pr.pin);
                        continue;
                    }
                    query_pins.push_back(pr.pin);
                    query_meta.push_back({o, t, sb, is_out, pr.lbl, pr.pin, pr.net});
                }
            }
        }

        int32_t nq = static_cast<int32_t>(query_pins.size());
        if (query_pins.empty()) query_pins.push_back(0);
        std::vector<int32_t> q_off(nq + 1, 0);
        int32_t max_q_nodes = std::max<int32_t>(65536, nq * 4);
        std::vector<int32_t> q_nid(max_q_nodes, -1);
        std::vector<uint64_t> q_net(std::max<int32_t>(1, nq), 0);
        int32_t tot_q = bridge.batch_query_pins_all(mem, getnodes_sta_addr, getnodes_pin_addr, query_pins.data(), nq, q_off.data(), q_nid.data(), max_q_nodes, q_net.data());
        if (tot_q > max_q_nodes) {
            max_q_nodes = tot_q + 1024;
            q_nid.assign(max_q_nodes, -1);
            tot_q = bridge.batch_query_pins_all(mem, getnodes_sta_addr, getnodes_pin_addr, query_pins.data(), nq, q_off.data(), q_nid.data(), max_q_nodes, q_net.data());
        }

        std::unordered_map<uint64_t, std::vector<int32_t>> raw_srcs = cout_src_nodes;
        std::unordered_map<uint64_t, std::vector<int32_t>> raw_sinks = cin_extra_sinks;
        std::unordered_map<NtNidKey, double, NtNidHash> sink_nid_hold_min;
        std::unordered_map<NtNidKey, uint64_t, NtNidHash> sink_nid_same_ff_clk;
        std::unordered_map<uint64_t, uint64_t> lut_paired_ff;
        for (const auto& lf : lut_ff_records) lut_paired_ff[lf.lo] = lf.ro;

        std::vector<std::string> unexplained_empty_rrg_pins;
        static const std::unordered_map<std::string, std::string> EMPTY_PARAMS;
        static const std::unordered_set<int> EMPTY_UBITS;

        for (int32_t idx_q = 0; idx_q < nq; ++idx_q) {
            const auto& qm = query_meta[idx_q];
            std::vector<int32_t> ids;
            for (int32_t k = q_off[idx_q]; k < q_off[idx_q + 1]; ++k) if (q_nid[k] >= 0) ids.push_back(q_nid[k]);
            if (ids.empty()) {
                bool has_drv = net_drv.count(qm.nt) > 0;
                uint64_t drv_o = has_drv ? net_drv[qm.nt].inst : 0;
                std::string drv_lbl = has_drv ? net_drv[qm.nt].lbl : "";
                int drv_t = has_drv ? inst_type[drv_o] : -1;
                const auto& snk_list = net_sinks_inst[qm.nt];
                bool has_sinks = !snk_list.empty();
                int ck_val = check_const(qm.nt);
                const std::string& inm = inst_name[qm.o];
                const auto& params = inst_defparams.count(inm) ? inst_defparams[inm] : EMPTY_PARAMS;
                const auto& ubits = unread_do_bits_by_inst.count(qm.o) ? unread_do_bits_by_inst[qm.o] : EMPTY_UBITS;

                std::string matched_cat, matched_rule;
                JsonValue matched_ev = JsonValue::object();
                int bcnt = 0, b_idx = -1;

                if (qm.is_out && !has_sinks && !iob_ext_nets.count(qm.nt)) {
                    matched_cat = "unused_output_exempt";
                    matched_rule = "ZERO_FANOUT_INTERNAL_OUTPUT";
                    matched_ev["sink_count"] = 0;
                }
                if (matched_cat.empty() && !qm.is_out && ck_val == 1) {
                    if (auto dres = validate_const_tied_disabled_pin(qm.t, qm.sb, qm.lbl, ck_val, params, ubits)) {
                        matched_cat = "const_tied_disabled_mode_exempt";
                        matched_rule = dres->first;
                        matched_ev = std::move(dres->second);
                    }
                }
                if (matched_cat.empty() && qm.t == 4 && !qm.is_out && (qm.lbl == "CLK[0]" || qm.lbl == "CLK[1]") && dsp_all_clks_explicitly_bypassed(qm.sb, params, &bcnt)) {
                    matched_cat = "combinational_dsp_bypassed_clk_exempt";
                    matched_rule = "DSP_SUB" + std::to_string(qm.sb) + "_ALL_CLK_PARAMS_BYPASS";
                    matched_ev["dsp_sub"] = qm.sb;
                    matched_ev["verified_bypass_clk_params"] = bcnt;
                }
                if (matched_cat.empty() && qm.t == 13 && qm.sb == 102 && !qm.is_out && dqs_lane_info.count(qm.o)) {
                    const auto& dqi = dqs_lane_info[qm.o];
                    if (qm.lbl == "DQSIN" && drv_t == 3 && drv_lbl == "O" && iob_assign.count(drv_o)) {
                        uint64_t p_io = phy_io_of(drv_o);
                        if (fn_get_iob_drived_dqs(p_io) && fn_get_iob_dqs_grp(p_io) == dqi.dqs_group && fn_get_bank_io(p_io) == dqi.bank) {
                            matched_cat = "dedicated_ddr_phy_bus_exempt";
                            matched_rule = "DQS_DQSIN_FROM_STROBE_IOBUF";
                            matched_ev["drv_inst"] = inst_name[drv_o];
                            matched_ev["drv_pin"] = drv_lbl;
                            matched_ev["iob_pad"] = iob_assign[drv_o].pad_p;
                            matched_ev["bank"] = dqi.bank;
                            matched_ev["dqs_group"] = dqi.dqs_group;
                        }
                    } else if (parse_bracketed_index(qm.lbl, "DLLSTEP", b_idx) && b_idx >= 0 && b_idx <= 7 && drv_t == 100 && dll_assign.count(drv_o)) {
                        auto dll_rc = dll_assign[drv_o];
                        if (drv_lbl == "STEP[" + std::to_string(b_idx) + "]" && dll_rc.second == dqi.c_dq) {
                            matched_cat = "dedicated_ddr_phy_bus_exempt";
                            matched_rule = "DQS_DLLSTEP_FROM_SAME_BORDER_DDRDLL";
                            matched_ev["drv_inst"] = inst_name[drv_o];
                            matched_ev["drv_pin"] = drv_lbl;
                            matched_ev["bit"] = b_idx;
                        }
                    }
                }
                if (matched_cat.empty() && qm.t == 7 && qm.sb == 144 && !qm.is_out && drv_t == 13 && inst_sub[drv_o] == 102 && dqs_lane_info.count(drv_o) && iol_to_iob.count(qm.o)) {
                    const auto& dqi = dqs_lane_info[drv_o];
                    uint64_t io_o = iol_to_iob[qm.o];
                    uint64_t p_io = phy_io_of(io_o);
                    int io_bk = fn_get_bank_io(p_io), io_grp = fn_get_iob_dqs_grp(p_io);
                    bool pair_ok = (qm.lbl == "ICLK" && drv_lbl == "DQSR90");
                    if (!pair_ok && (starts_with(qm.lbl, "WADDR[") || starts_with(qm.lbl, "RADDR[")) && qm.lbl.size() == 8 && qm.lbl[6] >= '0' && qm.lbl[6] <= '2') {
                        std::string exp_drv = std::string(1, qm.lbl[0]) + "POINT[" + qm.lbl[6] + "]";
                        pair_ok = (drv_lbl == exp_drv);
                    }
                    if (pair_ok && io_bk == dqi.bank && io_grp == dqi.dqs_group) {
                        matched_cat = "dedicated_ddr_phy_bus_exempt";
                        matched_rule = "IDES8_MEM_FROM_BYTE_LANE_DQS";
                        matched_ev["drv_inst"] = inst_name[drv_o];
                        matched_ev["drv_pin"] = drv_lbl;
                        matched_ev["dq_pad"] = iob_assign[io_o].pad_p;
                        matched_ev["bank"] = io_bk;
                        matched_ev["dqs_group"] = io_grp;
                    }
                }
                if (matched_cat.empty() && qm.t == 20) {
                    bool is_serdes_bump = (!qm.is_out && ((starts_with(qm.lbl, "LN") && (ends_with(qm.lbl, "_RXM_I") || ends_with(qm.lbl, "_RXP_I")))
                                                        || starts_with(qm.lbl, "REFCLKM") || starts_with(qm.lbl, "REFCLKP")))
                                       || (qm.is_out && starts_with(qm.lbl, "LN") && (ends_with(qm.lbl, "_TXM_O") || ends_with(qm.lbl, "_TXP_O")));
                    if (is_serdes_bump) {
                        matched_cat = "dedicated_serdes_bump_exempt";
                        matched_rule = "GTR12_DEDICATED_SERDES_BUMP";
                        matched_ev["quad_pos"] = gtr_pos_flag[qm.o] ? 1 : 0;
                        matched_ev["pin"] = qm.lbl;
                    } else if (!qm.is_out && ck_val != 0 &&
                               (qm.lbl == "CK_AHB_I" || qm.lbl == "AHB_RSTN" || qm.lbl == "TEST_DEC_EN" || qm.lbl == "QUAD_PCIE_CLK"
                                || starts_with(qm.lbl, "PCIE_DIV") || starts_with(qm.lbl, "PMAC_LN_RSTN"))) {
                        matched_cat = "const_tied_disabled_mode_exempt";
                        matched_rule = "GTR12_CONST_TIED_HARD_SIDEBAND";
                        matched_ev["const_kind"] = ck_val;
                        matched_ev["pin"] = qm.lbl;
                    }
                }
                if (matched_cat.empty() && qm.t == 211 && !qm.is_out) {
                    if (ck_val != 0) {
                        matched_cat = "const_tied_disabled_mode_exempt";
                        matched_rule = "AE350_CONST_TIED_HARD_SIDEBAND";
                        matched_ev["const_kind"] = ck_val;
                        matched_ev["pin"] = qm.lbl;
                    } else if (qm.lbl == "CORE_CLK" && drv_t == 99 && starts_with(drv_lbl, "CLKOUT") &&
                               !pll_right_idxs.empty() && pll_assign.count(drv_o) && pll_assign.at(drv_o) == pll_right_idxs[0]) {
                        matched_cat = "dedicated_ae350_pll_core_clk_exempt";
                        matched_rule = "AE350_CORE_CLK_FROM_PLL_R0";
                        matched_ev["drv_inst"] = inst_name[drv_o];
                        matched_ev["drv_pin"] = drv_lbl;
                        matched_ev["pll_site"] = "PLL_R[0]";
                        matched_ev["pll_row"] = pll_rcs[pll_right_idxs[0]].first;
                        matched_ev["pll_col"] = pll_rcs[pll_right_idxs[0]].second;
                    }
                }
                if (matched_cat.empty() && qm.t == 174) {
                    if (!qm.is_out && (qm.lbl == "tck_pad_i" || qm.lbl == "tms_pad_i" || qm.lbl == "tdi_pad_i") && drv_t == 3 && drv_lbl == "O" && iob_assign.count(drv_o)) {
                        std::string cfg_fn = (qm.lbl == "tck_pad_i") ? "TCK" : (qm.lbl == "tms_pad_i") ? "TMS" : "TDI";
                        if (cfg_to_pad.count(cfg_fn) && iob_assign[drv_o].pad_p == cfg_to_pad[cfg_fn]) {
                            matched_cat = "dedicated_jtag_tap_pad_exempt";
                            matched_rule = "GW_JTAG_INPUT_FROM_CFG_PAD";
                            matched_ev["cfg"] = cfg_fn;
                            matched_ev["pad"] = iob_assign[drv_o].pad_p;
                            matched_ev["drv_inst"] = inst_name[drv_o];
                        }
                    } else if (qm.is_out && qm.lbl == "tdo_pad_o" && snk_list.size() == 1) {
                        const auto& s0 = snk_list[0];
                        if (inst_type[s0.inst] == 3 && s0.lbl == "I" && iob_assign.count(s0.inst) && cfg_to_pad.count("TDO") && iob_assign[s0.inst].pad_p == cfg_to_pad["TDO"]) {
                            matched_cat = "dedicated_jtag_tap_pad_exempt";
                            matched_rule = "GW_JTAG_TDO_TO_CFG_PAD";
                            matched_ev["cfg"] = "TDO";
                            matched_ev["pad"] = iob_assign[s0.inst].pad_p;
                            matched_ev["snk_inst"] = inst_name[s0.inst];
                        }
                    }
                }

                if (matched_cat.empty() && !qm.is_out && qm.t == 5 &&
                    (parse_bracketed_index(qm.lbl, "DI", b_idx) || parse_bracketed_index(qm.lbl, "DIA", b_idx) || parse_bracketed_index(qm.lbl, "DIB", b_idx)) &&
                    ubits.count(b_idx)) {
                    int bw0 = static_cast<int>(parse_param_num(params, params.count("BIT_WIDTH_0") ? "BIT_WIDTH_0" : "BIT_WIDTH", -1.0));
                    int bw1 = params.count("BIT_WIDTH_1") ? static_cast<int>(parse_param_num(params, "BIT_WIDTH_1", -1.0)) : bw0;
                    if (bw0 > 0 && (qm.sb == 200 || bw1 == bw0)) {
                        matched_cat = "unused_output_exempt";
                        matched_rule = "BSRAM_DI_WITH_UNREAD_DO_LANE";
                        matched_ev["BIT_WIDTH_0"] = bw0;
                        matched_ev["bit"] = b_idx;
                        matched_ev["DO_fanout"] = 0;
                    }
                }
                if (matched_cat.empty() && !qm.is_out && qm.t == 6 && qm.sb != 98 && parse_bracketed_index(qm.lbl, "DI", b_idx) && ubits.count(b_idx)) {
                    matched_cat = "unused_output_exempt";
                    matched_rule = "SSRAM_DI_WITH_UNREAD_DO_LANE";
                    matched_ev["bit"] = b_idx;
                    matched_ev["DO_fanout"] = 0;
                }

                if (!matched_cat.empty()) {
                    pin_inv_counts[matched_cat]++;
                    exemption_rule_counts[matched_rule]++;
                    JsonValue wrow = JsonValue::object();
                    wrow["inst"] = inm;
                    wrow["type"] = qm.t;
                    wrow["sub"] = qm.sb;
                    wrow["dir"] = qm.is_out ? "out" : "in";
                    wrow["pin"] = qm.lbl;
                    wrow["net"] = nname(qm.nt);
                    wrow["category"] = matched_cat;
                    wrow["rule"] = matched_rule;
                    wrow["evidence"] = std::move(matched_ev);
                    pin_exemption_witnesses.push_back(std::move(wrow));
                    all_routed_pins.push_back(qm.p);
                } else {
                    unexplained_empty_rrg_pins.push_back(inm + "." + qm.lbl + "(t=" + std::to_string(qm.t) + ",sub=" + std::to_string(qm.sb) + ",net=" + nname(qm.nt) + ")");
                }
                continue;
            }

            pin_inv_counts["rrg_mapped_pins"]++;
            all_routed_pins.push_back(qm.p);
            if (qm.t == 3 && !qm.is_out && qm.lbl == "I" && jtag_tdo_nets.count(qm.nt)) {
                for (int32_t nid : ids) fixed_root_pairs.emplace_back(nid, qm.nt);
                internal_only_nets.insert(qm.nt);
                continue;
            }
            if (qm.t == 7 && qm.sb == 157 && !qm.is_out && qm.lbl == "TCLK") {
                for (int32_t nid : ids) fixed_root_pairs.emplace_back(nid, qm.nt);
                continue;
            }
            if (qm.is_out) {
                raw_srcs[qm.nt].insert(raw_srcs[qm.nt].end(), ids.begin(), ids.end());
            } else {
                raw_sinks[qm.nt].insert(raw_sinks[qm.nt].end(), ids.begin(), ids.end());
                if (qm.nt && !check_const(qm.nt) && !clock_nets.count(qm.nt)) {
                    double pc = conn_crit_of(qm.nt, qm.o, qm.lbl);
                    double h_req = 0.0;
                    if (!fb_hold_pins.empty()) {
                        if (auto hit = fb_hold_pins.find(inst_name[qm.o] + "/" + qm.lbl); hit != fb_hold_pins.end()) h_req = hit->second;
                    }
                    uint64_t same_clk_nt = 0;
                    uint64_t cap_ff_o = (qm.t == 50) ? qm.o : ((qm.t == 1 && lut_paired_ff.count(qm.o)) ? lut_paired_ff[qm.o] : 0);
                    if (pc < 0.42 && cap_ff_o && !false_nets.count(qm.nt) && net_drv.count(qm.nt)) {
                        uint64_t drv_o = net_drv[qm.nt].inst;
                        int dt = inst_type[drv_o];
                        if (dt == 4 || dt == 5 || dt == 50) {
                            auto [dc_nt, _1] = inst_capture_clk_and_period(drv_o);
                            auto [sc_nt, _2] = inst_capture_clk_and_period(cap_ff_o);
                            if (dc_nt && dc_nt == sc_nt) same_clk_nt = dc_nt;
                        }
                    }
                    for (int32_t nid : ids) {
                        NtNidKey nk{qm.nt, nid};
                        if (pc > sink_nid_crit[nk]) sink_nid_crit[nk] = pc;
                        if (h_req > sink_nid_hold_min[nk]) sink_nid_hold_min[nk] = h_req;
                        if (same_clk_nt) sink_nid_same_ff_clk[nk] = same_clk_nt;
                    }
                }
            }
        }

        for (uint64_t iol_o : iol_objs) {
            for (const auto* plist : {&inst_in_pins[iol_o], &inst_out_pins[iol_o]}) {
                for (const auto& pr : *plist) {
                    if (pr.net && is_iol_io_lbl(pr.lbl) && !raw_sinks.count(pr.net) && !raw_srcs.count(pr.net)) {
                        internal_only_nets.insert(pr.net);
                    }
                }
            }
        }

        auto [vss_idx, vcc_idx] = load_vss_vcc_wire_ids();

        // Sort candidate nets deterministically by net_cid (100% ASLR-independent!)
        std::vector<uint64_t> all_candidate_nets;
        {
            std::unordered_set<uint64_t> cand_set;
            for (const auto& [nt, _] : raw_srcs) cand_set.insert(nt);
            for (const auto& [nt, _] : raw_sinks) cand_set.insert(nt);
            all_candidate_nets.assign(cand_set.begin(), cand_set.end());
            for (uint64_t nt : all_candidate_nets) ensure_net_cid(nt);
            std::sort(all_candidate_nets.begin(), all_candidate_nets.end(), [&](uint64_t a, uint64_t b) {
                return net_cid[a] < net_cid[b];
            });
        }

        std::unordered_map<uint64_t, int> clk_nt_to_ci;
        for (uint64_t nt : all_candidate_nets) {
            if (internal_only_nets.count(nt) || check_const(nt) != 0) continue;
            if (clock_nets.count(nt) && !raw_srcs[nt].empty() && !raw_sinks[nt].empty()) {
                if (clk_nt_to_ci.size() < 32) clk_nt_to_ci.emplace(nt, static_cast<int>(clk_nt_to_ci.size()));
            }
        }

        std::vector<uint64_t> r_net_ptrs;
        std::vector<int32_t> r_net_flags, r_src_offsets{0}, r_src_ids, r_snk_offsets{0}, r_snk_ids;
        std::vector<float> r_net_crit, r_snk_crit;
        int16_t rc_buf[3]{};
        std::vector<std::string> bad_sourceless;

        auto sort_dedup_ids = [](std::vector<int32_t> v) {
            std::sort(v.begin(), v.end());
            v.erase(std::unique(v.begin(), v.end()), v.end());
            return v;
        };

        for (uint64_t nt : all_candidate_nets) {
            if (internal_only_nets.count(nt)) continue;
            int ck = check_const(nt);
            auto sinks = raw_sinks.count(nt) ? sort_dedup_ids(raw_sinks[nt]) : std::vector<int32_t>{};
            if (ck == 1 || ck == 2) {
                if (sinks.empty()) { internal_only_nets.insert(nt); continue; }
                int32_t fl = (ck == 1) ? 2 : 4;
                std::map<std::pair<int, int>, std::vector<int32_t>> buckets;
                for (int32_t sk : sinks) {
                    bridge.id_coord(sk, rc_buf);
                    buckets[{std::max(0, static_cast<int>(rc_buf[0])) / 4, std::max(0, static_cast<int>(rc_buf[1])) / 4}].push_back(sk);
                }
                for (const auto& [_, b_sinks] : buckets) {
                    r_net_ptrs.push_back(nt);
                    r_net_flags.push_back(fl);
                    r_net_crit.push_back(0.05f);
                    r_src_offsets.push_back(static_cast<int32_t>(r_src_ids.size()));
                    r_snk_ids.insert(r_snk_ids.end(), b_sinks.begin(), b_sinks.end());
                    r_snk_crit.insert(r_snk_crit.end(), b_sinks.size(), 0.05f);
                    r_snk_offsets.push_back(static_cast<int32_t>(r_snk_ids.size()));
                }
            } else {
                auto srcs = raw_srcs.count(nt) ? sort_dedup_ids(raw_srcs[nt]) : std::vector<int32_t>{};
                if (srcs.empty()) {
                    if (!sinks.empty()) bad_sourceless.push_back(nname(nt) + " (" + std::to_string(sinks.size()) + " sinks)");
                    internal_only_nets.insert(nt);
                    continue;
                }
                if (sinks.empty()) { internal_only_nets.insert(nt); continue; }
                int32_t fl = clock_nets.count(nt) ? 1 : 0;
                float crit = (fl == 1) ? 0.99f : static_cast<float>(net_crit_map.count(nt) ? net_crit_map[nt] : 0.25);
                r_net_ptrs.push_back(nt);
                r_net_flags.push_back(fl);
                r_net_crit.push_back(crit);
                r_src_ids.insert(r_src_ids.end(), srcs.begin(), srcs.end());
                r_src_offsets.push_back(static_cast<int32_t>(r_src_ids.size()));
                r_snk_ids.insert(r_snk_ids.end(), sinks.begin(), sinks.end());
                if (fl == 1) {
                    r_snk_crit.insert(r_snk_crit.end(), sinks.size(), 0.99f);
                } else {
                    for (int32_t sk : sinks) {
                        NtNidKey nk{nt, sk};
                        double pc_v = sink_nid_crit.count(nk) ? sink_nid_crit[nk] : std::max(0.05, static_cast<double>(crit) * 0.5);
                        double h_min = sink_nid_hold_min.count(nk) ? sink_nid_hold_min[nk] : 0.0;
                        if (h_min > 0.0 && pc_v < 0.65) {
                            r_snk_crit.push_back(static_cast<float>(-(100.0 + std::min(4.0, h_min))));
                        } else {
                            uint64_t sc_clk = sink_nid_same_ff_clk.count(nk) ? sink_nid_same_ff_clk[nk] : 0;
                            int ci = (sc_clk && clk_nt_to_ci.count(sc_clk)) ? clk_nt_to_ci[sc_clk] : -1;
                            if (ci >= 0 && pc_v < 0.55) {
                                r_snk_crit.push_back(static_cast<float>(-(static_cast<double>(ci + 1) + std::min(0.55, std::max(0.04, pc_v)))));
                            } else {
                                r_snk_crit.push_back(static_cast<float>(pc_v));
                            }
                        }
                    }
                }
                r_snk_offsets.push_back(static_cast<int32_t>(r_snk_ids.size()));
            }
        }

        if (!unexplained_empty_rrg_pins.empty()) {
            std::string samples;
            for (size_t i = 0; i < std::min<size_t>(20, unexplained_empty_rrg_pins.size()); ++i) {
                if (i) samples += "; ";
                samples += unexplained_empty_rrg_pins[i];
            }
            throw std::runtime_error(std::to_string(unexplained_empty_rrg_pins.size()) +
                " connected pins resolved to empty RRG nodes without a validated dedicated-wire exemption: " + samples);
        }
        if (!bad_sourceless.empty()) {
            std::string samples;
            for (size_t i = 0; i < std::min<size_t>(20, bad_sourceless.size()); ++i) {
                if (i) samples += "; ";
                samples += bad_sourceless[i];
            }
            throw std::runtime_error(std::to_string(bad_sourceless.size()) + " signal nets have RRG sinks but 0 RRG sources: " + samples);
        }

        int32_t num_rnets = static_cast<int32_t>(r_net_ptrs.size());
        size_t n_clk_e = 0, n_sig_e = 0, n_cst_e = 0;
        for (int32_t f : r_net_flags) {
            if (f & 1) ++n_clk_e;
            else if (f == 0) ++n_sig_e;
            else if (f & 6) ++n_cst_e;
        }
        std::fprintf(stdout, "[ownpr] Queried %d pins (%d STA nodes) in %.2fs -> %d route entries (clocks=%zu, signals=%zu, const_tiles=%zu, fixed_roots=%zu)\n",
                     nq, tot_q, perf_counter() - t_q0, num_rnets, n_clk_e, n_sig_e, n_cst_e, fixed_root_pairs.size());
        std::fflush(stdout);

        // =====================================================================
        // 7. Run C++ Timing-Driven Negotiated-Congestion Router & Install Routes
        // =====================================================================
        auto dedup_fixed = fixed_root_pairs;
        for (auto& fp : dedup_fixed) ensure_net_cid(fp.second);
        std::sort(dedup_fixed.begin(), dedup_fixed.end(), [&](const auto& a, const auto& b) {
            return std::make_pair(a.first, net_cid[a.second]) < std::make_pair(b.first, net_cid[b.second]);
        });
        dedup_fixed.erase(std::unique(dedup_fixed.begin(), dedup_fixed.end()), dedup_fixed.end());

        std::unordered_map<int32_t, uint64_t> nid_net_check;
        for (auto [nid, nt] : dedup_fixed) {
            if (nid_net_check.count(nid) && nid_net_check[nid] != nt) {
                throw std::runtime_error("RRG pin-ownership collision on FIXED node " + std::to_string(nid));
            }
            nid_net_check[nid] = nt;
        }
        for (int32_t e_i = 0; e_i < num_rnets; ++e_i) {
            uint64_t nt = r_net_ptrs[e_i];
            for (int32_t k = r_src_offsets[e_i]; k < r_src_offsets[e_i + 1]; ++k) {
                int32_t nid = r_src_ids[k];
                if (nid_net_check.count(nid) && nid_net_check[nid] != nt)
                    throw std::runtime_error("RRG pin-ownership collision on SRC node " + std::to_string(nid));
                nid_net_check[nid] = nt;
            }
            for (int32_t k = r_snk_offsets[e_i]; k < r_snk_offsets[e_i + 1]; ++k) {
                int32_t nid = r_snk_ids[k];
                if (nid_net_check.count(nid) && nid_net_check[nid] != nt)
                    throw std::runtime_error("RRG pin-ownership collision on SNK node " + std::to_string(nid));
                nid_net_check[nid] = nt;
            }
        }

        std::vector<int32_t> fr_id_arr(std::max<size_t>(1, dedup_fixed.size()), 0);
        std::vector<uint64_t> fr_nt_arr(std::max<size_t>(1, dedup_fixed.size()), 0);
        for (size_t i = 0; i < dedup_fixed.size(); ++i) { fr_id_arr[i] = dedup_fixed[i].first; fr_nt_arr[i] = dedup_fixed[i].second; }

        if (r_net_ptrs.empty()) { r_net_ptrs.push_back(0); r_net_flags.push_back(0); r_net_crit.push_back(0.0f); }
        if (r_src_ids.empty()) r_src_ids.push_back(0);
        if (r_snk_ids.empty()) { r_snk_ids.push_back(0); r_snk_crit.push_back(0.0f); }

        int32_t blk_dummy = 0, out_iters = 0, out_overused = 0;
        double t_rt0 = perf_counter();
        int32_t num_route_records = bridge.route_design(
            num_rnets,
            r_net_ptrs.data(), r_net_flags.data(), r_net_crit.data(),
            r_src_offsets.data(), r_src_ids.data(),
            r_snk_offsets.data(), r_snk_ids.data(), r_snk_crit.data(),
            vss_idx, vcc_idx,
            fr_id_arr.data(), fr_nt_arr.data(), static_cast<int32_t>(dedup_fixed.size()),
            &blk_dummy, 0,
            100, &out_iters, &out_overused
        );
        double t_route_sec = perf_counter() - t_rt0;
        if (num_route_records < 0) {
            throw std::runtime_error("bridge_route_design failed with rc=" + std::to_string(num_route_records) + " overused=" + std::to_string(out_overused));
        }

        uint64_t set_net_r_addr = reinterpret_cast<uint64_t>(gw_req("?setRouted@Net@NETLIST@@QEAAX_N@Z"));
        uint64_t set_pin_r_addr = reinterpret_cast<uint64_t>(gw_req("?setRouted@Pin@NETLIST@@QEAAX_N@Z"));
        std::vector<uint64_t> extra_net_list(internal_only_nets.begin(), internal_only_nets.end());
        for (uint64_t nt : extra_net_list) ensure_net_cid(nt);
        std::sort(extra_net_list.begin(), extra_net_list.end(), [&](uint64_t a, uint64_t b) { return net_cid[a] < net_cid[b]; });
        int32_t n_extra = static_cast<int32_t>(extra_net_list.size());
        if (extra_net_list.empty()) extra_net_list.push_back(0);
        int32_t n_rpins = static_cast<int32_t>(all_routed_pins.size());
        if (all_routed_pins.empty()) all_routed_pins.push_back(0);
        int32_t err_idx = -1;

        int32_t irc = bridge.install_last_routes(
            set_net_r_addr, set_pin_r_addr,
            extra_net_list.data(), n_extra,
            all_routed_pins.data(), n_rpins,
            &err_idx
        );
        if (irc != 0) throw std::runtime_error("bridge_install_last_routes failed: rc=" + std::to_string(irc) + " err_idx=" + std::to_string(err_idx));
        std::fprintf(stdout, "[ownpr] Routing converged in %d iterations (%.2fs): %d physical RRG nodes installed, 0 overused\n",
                     out_iters, t_route_sec, num_route_records);
        std::fflush(stdout);

        // Export routed pin delays for optional post-STA feedback passes
        std::vector<int32_t> sq_nids;
        std::vector<std::string> sq_keys;
        for (int32_t idx_q = 0; idx_q < nq; ++idx_q) {
            const auto& qm = query_meta[idx_q];
            if (qm.is_out || !qm.nt || check_const(qm.nt) || clock_nets.count(qm.nt) || false_nets.count(qm.nt) || packed_zero_wire_nets.count(qm.nt)) continue;
            if (q_off[idx_q + 1] > q_off[idx_q] && q_nid[q_off[idx_q]] >= 0) {
                sq_nids.push_back(q_nid[q_off[idx_q]]);
                sq_keys.push_back(inst_name[qm.o] + "/" + qm.lbl);
            }
        }
        if (!sq_nids.empty()) {
            std::vector<float> sq_del(sq_nids.size(), 0.0f);
            if (bridge.query_routed_delays(sq_nids.data(), static_cast<int32_t>(sq_nids.size()), sq_del.data()) > 0) {
                JsonValue del_j = JsonValue::object();
                for (size_t i = 0; i < sq_keys.size(); ++i) {
                    double fv = std::round(static_cast<double>(sq_del[i]) * 10000.0) / 10000.0;
                    if (fv > 0.01) {
                        double prev = del_j.contains(sq_keys[i]) ? del_j[sq_keys[i]].as_f64() : 0.0;
                        if (fv > prev) del_j[sq_keys[i]] = fv;
                    }
                }
                std::string del_txt = dump_json(del_j, -1, false);
                write_file_text(path_join(out, "routed_delays_latest.json"), del_txt);
                if (!file_exists(routed_del_p1)) write_file_text(routed_del_p1, del_txt);
            }
        }

        // =====================================================================
        // 8. Post-Route Native STA, Reports, .vo, and .fs Bitstream Serialization
        // =====================================================================
        double t_sta0 = perf_counter();
        alignas(16) uint8_t rpt_buf[0x100]{};
        uint64_t rpt_ptr = reinterpret_cast<uint64_t>(rpt_buf);
        reinterpret_cast<uint64_t (*)(uint64_t)>(base + 0x24D070ULL)(rpt_ptr);
        reinterpret_cast<void (*)(uint64_t, uint64_t)>(gw_req("?setRptInf@globalMem@@QEAAXPEAVrpt_inf@RPT@@@Z"))(mem, rpt_ptr);

        reinterpret_cast<void (*)(uint64_t, int)>(gw_req("?setFlowFlag@TmMonitor@STA@@QEAAXW4FLOW_FLAG_STA@2@@Z"))(monitor, 7);
        reinterpret_cast<void (*)(uint64_t, bool)>(gw_req("?setPlaceFlag@TmMonitor@STA@@QEAAX_N@Z"))(monitor, false);
        wr_u8_mem(monitor + 0x12, 0U);
        reinterpret_cast<void (*)(uint64_t)>(gw_req("?restartTiming@TmMonitor@STA@@QEAAXXZ"))(monitor);
        if (!reinterpret_cast<bool (*)(uint64_t)>(base + 0x13B77B0ULL)(monitor)) {
            throw std::runtime_error("Native routed STA evaluation returned false");
        }

        int tr_rc  = reinterpret_cast<int (*)(uint64_t)>(gw_req("?generateTimingRptoutFile@@YAHPEAVglobalMem@@@Z"))(mem);
        int rpt_rc = reinterpret_cast<int (*)(uint64_t)>(gw_req("?generateTxtRptoutFile@@YAHPEAVglobalMem@@@Z"))(mem);
        if (get_env("NEXTGOWIN_EMIT_IDE_HTML") == "1") {
            if (FARPROC fn_hrpt = gw_opt("?generateHtmlRptoutFile@@YAHPEAVglobalMem@@_N@Z")) {
                (void)reinterpret_cast<int (*)(uint64_t, bool)>(fn_hrpt)(mem, false);
            }
            if (FARPROC fn_hpin = gw_opt("?generateHtmlPinoutFile@@YAHPEAVglobalMem@@@Z")) {
                (void)reinterpret_cast<int (*)(uint64_t)>(fn_hpin)(mem);
            }
        }
        int vo_rc  = reinterpret_cast<int (*)(uint64_t)>(gw_req("?generateOutputFile@@YAHPEAVglobalMem@@@Z"))(mem);
        int fs_rc  = reinterpret_cast<int (*)(uint64_t)>(gw_req("?generateFusemap@@YAHPEAVglobalMem@@@Z"))(mem);
        reinterpret_cast<void (*)(uint64_t)>(gw_req("?generateTxtPinoutFile@@YAXPEAVglobalMem@@@Z"))(mem);
        int cst_rc = reinterpret_cast<int (*)(uint64_t)>(gw_req("?generateCstoutFile@@YAHPEAVglobalMem@@@Z"))(mem);
        bool parents_preserved = (bridge.verify_last_parents_preserved() == 1);

        if (tr_rc != 0 || rpt_rc != 0 || vo_rc != 0 || fs_rc != 0) {
            throw std::runtime_error("Output generation failed");
        }
        if (!parents_preserved) {
            throw std::runtime_error("Physical RRG parent pointers mutated during STA/fusemap export");
        }

        // =====================================================================
        // 9. Independent Post-Fusemap Live NETLIST::node, Co-Location & Frame-CRC Audit
        // =====================================================================
        double t_aud0 = perf_counter();
        auto fn_get_phy = reinterpret_cast<uint64_t (*)(uint64_t)>(gw_req("?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ"));

        struct PairU64Hash {
            size_t operator()(const std::pair<uint64_t, uint64_t>& p) const noexcept {
                return std::hash<uint64_t>{}(p.first) ^ (std::hash<uint64_t>{}(p.second) * 0x9e3779b97f4a7c15ULL);
            }
        };
        std::unordered_map<uint64_t, std::unordered_set<uint64_t>> fanout_cache;
        std::unordered_set<std::pair<uint64_t, uint64_t>, PairU64Hash> verified_edges;
        std::unordered_map<uint64_t, uint64_t> node_owner_net;
        size_t unreached_sinks = 0, invalid_edges = 0, ownership_conflicts = 0;

        auto parent_has_child = [&](uint64_t pptr_v, uint64_t cptr_v) -> bool {
            if (verified_edges.count({pptr_v, cptr_v})) return true;
            auto it = fanout_cache.find(pptr_v);
            if (it == fanout_cache.end()) {
                int16_t fcnt = std::max<int16_t>(0, rd_i16_mem(pptr_v + 6));
                std::unordered_set<uint64_t> fset;
                fset.reserve(static_cast<size_t>(fcnt));
                for (int16_t k = 0; k < fcnt; ++k) fset.insert(fn_getout(pptr_v, k));
                it = fanout_cache.emplace(pptr_v, std::move(fset)).first;
            }
            if (it->second.count(cptr_v)) {
                verified_edges.insert({pptr_v, cptr_v});
                return true;
            }
            return false;
        };

        int64_t fixed_roots_verified = 0;
        for (auto [nid, nt] : dedup_fixed) {
            uint64_t nptr = bridge.id_to_ptr(nid);
            uint64_t own = rd_ptr(nptr + 0x20);
            uint64_t pptr = rd_ptr(nptr + 0x18);
            uint8_t rflg = rd_u8_mem(nptr + 0x2A) & 8U;
            auto [oit, _] = node_owner_net.emplace(nptr, nt);
            if (own != nt || pptr != 0 || rflg == 0 || oit->second != nt) ++ownership_conflicts;
            else ++fixed_roots_verified;
        }

        int64_t total_sinks_verified = 0, clock_entries_verified = 0, signal_entries_verified = 0, const_tile_entries_verified = 0;
        for (int32_t e_i = 0; e_i < num_rnets; ++e_i) {
            uint64_t nt = r_net_ptrs[e_i];
            int32_t fl = r_net_flags[e_i];
            std::unordered_set<uint64_t> src_ptrs;
            for (int32_t k = r_src_offsets[e_i]; k < r_src_offsets[e_i + 1]; ++k) src_ptrs.insert(bridge.id_to_ptr(r_src_ids[k]));
            std::unordered_set<uint64_t> entry_ok_nodes;
            bool entry_all_ok = true;
            for (int32_t k = r_snk_offsets[e_i]; k < r_snk_offsets[e_i + 1]; ++k) {
                uint64_t cur = bridge.id_to_ptr(r_snk_ids[k]);
                std::vector<uint64_t> trail;
                std::unordered_set<uint64_t> seen_in_walk;
                bool sink_ok = false;
                for (int step = 0; step < 4096; ++step) {
                    if (entry_ok_nodes.count(cur)) { sink_ok = true; break; }
                    if (!cur || !seen_in_walk.insert(cur).second) break;
                    trail.push_back(cur);
                    uint64_t own = rd_ptr(cur + 0x20);
                    uint8_t rflg = rd_u8_mem(cur + 0x2A) & 8U;
                    auto [oit, _] = node_owner_net.emplace(cur, nt);
                    if (own != nt || rflg == 0 || oit->second != nt) { ++ownership_conflicts; break; }
                    uint64_t pptr = rd_ptr(cur + 0x18);
                    if (pptr == 0) {
                        if (fl & 6) {
                            int16_t w_idx = rd_i16_mem(cur + 4);
                            sink_ok = (w_idx == (fl == 2 ? vss_idx : vcc_idx));
                        } else {
                            sink_ok = (src_ptrs.count(cur) > 0);
                        }
                        break;
                    }
                    if (!parent_has_child(pptr, cur)) { ++invalid_edges; break; }
                    cur = pptr;
                }
                if (sink_ok) {
                    entry_ok_nodes.insert(trail.begin(), trail.end());
                    ++total_sinks_verified;
                } else {
                    entry_all_ok = false;
                    ++unreached_sinks;
                }
            }
            if (entry_all_ok) {
                if (fl & 1) ++clock_entries_verified;
                else if (fl & 6) ++const_tile_entries_verified;
                else ++signal_entries_verified;
            }
        }

        size_t coloc_mismatches = 0;
        int64_t lut_ff_verified = 0, alu_chains_verified = 0, alu_cells_verified = 0, mux_trees_verified = 0, iologic_iob_verified = 0, dqs_sites_verified = 0;
        for (size_t i = 0; i < lut_ff_records.size(); ++i) {
            const auto& lf = lut_ff_records[i];
            int16_t rl = out_r[lut_ff_uids[i]], cl = out_c[lut_ff_uids[i]], sl = out_slot[lut_ff_uids[i]];
            uint64_t agg = rd_ptr(fn_getgrid(chip, rl, cl) + 0x70);
            if (fn_get_phy(lf.lo) == rd_ptr(agg + 8 * sl) && fn_get_phy(lf.ro) == rd_ptr(agg + 0xA0 + 8 * sl)) ++lut_ff_verified;
            else ++coloc_mismatches;
        }
        for (size_t i = 0; i < alu_chain_records.size(); ++i) {
            const auto& rec = alu_chain_records[i];
            int16_t ra = out_r[alu_uids[i]], c0 = out_c[alu_uids[i]];
            bool chain_ok = true;
            for (size_t k = 0; k < rec.chain.size(); ++k) {
                int16_t ca = static_cast<int16_t>(c0 + (k / 6)), sa = static_cast<int16_t>(k % 6);
                uint64_t agg_a = rd_ptr(fn_getgrid(chip, ra, ca) + 0x70);
                if (fn_get_phy(rec.chain[k]) != rd_ptr(agg_a + 0x40 + 8 * sa)) { chain_ok = false; ++coloc_mismatches; }
                else ++alu_cells_verified;
                if (rec.paired_regs[k] && fn_get_phy(rec.paired_regs[k]) != rd_ptr(agg_a + 0xA0 + 8 * sa)) { chain_ok = false; ++coloc_mismatches; }
            }
            if (chain_ok) ++alu_chains_verified;
        }
        for (const auto& mb : mux_bound_records) {
            uint64_t agg_m = rd_ptr(fn_getgrid(chip, mb.rm, mb.cm) + 0x70);
            bool m_ok = true;
            for (auto [lo, sl] : mb.l_slots) if (fn_get_phy(lo) != rd_ptr(agg_m + 8 * sl)) m_ok = false;
            for (auto [mo, sl] : mb.m_slots) if (fn_get_phy(mo) != rd_ptr(agg_m + 0x150 + 8 * sl)) m_ok = false;
            if (m_ok) ++mux_trees_verified; else ++coloc_mismatches;
        }
        for (uint64_t iol_o : iol_objs) {
            uint64_t io_o = iol_to_iob[iol_o];
            uint64_t blk = iob_blk_map[io_o].blk;
            uint64_t exp_io = rd_ptr(rd_ptr(blk + 0x10));
            uint64_t ip = fn_get_phy(iol_o);
            bool i_ok = (fn_get_phy(io_o) == exp_io) &&
                        (ip == fn_get_iol_164(blk) || ip == fn_get_iol_165(blk) || ip == fn_get_iol_iddr(blk) || ip == fn_get_iol_odd(blk));
            if (i_ok) ++iologic_iob_verified; else ++coloc_mismatches;
        }
        for (const auto& [dqs_o, dc] : dqs_assign) {
            if (fn_get_phy(dqs_o) == dc.phy) ++dqs_sites_verified; else ++coloc_mismatches;
        }
        int64_t ssram_verified = 0, rom16_verified = 0, dl_verified = 0;
        for (size_t i = 0; i < ram16_objs.size(); ++i) {
            int16_t rs = out_r[ram16_uids[i]], cs = out_c[ram16_uids[i]];
            uint64_t agg = rd_ptr(fn_getgrid(chip, rs, cs) + 0x70);
            if (fn_get_phy(ram16_objs[i]) == rd_ptr(agg + 0x250)) ++ssram_verified; else ++coloc_mismatches;
        }
        for (size_t i = 0; i < rom16_objs.size(); ++i) {
            int16_t rr = out_r[rom16_uids[i]], cr = out_c[rom16_uids[i]], sr = out_slot[rom16_uids[i]];
            uint64_t agg = rd_ptr(fn_getgrid(chip, rr, cr) + 0x70);
            if (fn_get_phy(rom16_objs[i]) == rd_ptr(agg + 0x258 + 8 * sr)) ++rom16_verified; else ++coloc_mismatches;
        }
        for (size_t i = 0; i < standalone_dls.size(); ++i) {
            int16_t rr = out_r[dl_only_uids[i]], cr = out_c[dl_only_uids[i]], sr = out_slot[dl_only_uids[i]];
            uint64_t agg = rd_ptr(fn_getgrid(chip, rr, cr) + 0x70);
            if (fn_get_phy(standalone_dls[i]) == rd_ptr(agg + 0x110 + 8 * sr)) ++dl_verified; else ++coloc_mismatches;
        }

        JsonValue frame_audit = audit_fs_bitstream_integrity(path_join(out, "mapped.fs"));
        if (unreached_sinks || invalid_edges || ownership_conflicts || coloc_mismatches || frame_audit["status"].as_str() != "frame_integrity_verified") {
            throw std::runtime_error(
                "Post-fusemap verification failed (unreached=" + std::to_string(unreached_sinks) +
                ", invalid_edges=" + std::to_string(invalid_edges) +
                ", ownership=" + std::to_string(ownership_conflicts) +
                ", coloc=" + std::to_string(coloc_mismatches) +
                ", fs=" + frame_audit["status"].as_str() + ")"
            );
        }

        std::string jsonl_buf;
        for (const auto& wrow : pin_exemption_witnesses) {
            jsonl_buf += dump_json(wrow, -1, false);
            jsonl_buf.push_back('\n');
        }
        std::string witness_gz_path = path_join(out, "pin_exemption_witnesses.jsonl.gz");
        write_gzip_file(witness_gz_path, jsonl_buf);
        std::string witness_gz_sha = sha256_file(witness_gz_path);

        double t_audit_sec = perf_counter() - t_aud0;

        JsonValue cat_j = JsonValue::object();
        for (const auto& [k, v] : pin_inv_counts) cat_j[k] = v;
        JsonValue rules_j = JsonValue::object();
        for (const auto& [k, v] : exemption_rule_counts) rules_j[k] = v;

        JsonValue pin_inv_j = JsonValue::object();
        pin_inv_j["queried_pins"] = nq;
        pin_inv_j["sta_nodes_returned"] = tot_q;
        pin_inv_j["all_routed_pins_marked"] = n_rpins;
        pin_inv_j["unexplained_empty_rrg_pins"] = 0;
        pin_inv_j["sourceless_signal_nets"] = 0;
        pin_inv_j["categories"] = std::move(cat_j);
        pin_inv_j["exemption_rule_counts"] = std::move(rules_j);
        pin_inv_j["witness_count"] = static_cast<int64_t>(pin_exemption_witnesses.size());
        pin_inv_j["witnesses_file"] = "pin_exemption_witnesses.jsonl.gz";
        pin_inv_j["witnesses_sha256"] = witness_gz_sha;

        JsonValue live_rt_j = JsonValue::object();
        live_rt_j["route_entries_total"] = num_rnets;
        live_rt_j["clock_entries_verified"] = clock_entries_verified;
        live_rt_j["signal_entries_verified"] = signal_entries_verified;
        live_rt_j["const_tile_entries_verified"] = const_tile_entries_verified;
        live_rt_j["total_sinks_verified"] = total_sinks_verified;
        live_rt_j["fixed_roots_verified"] = fixed_roots_verified;
        live_rt_j["unique_rrg_edges_verified"] = static_cast<int64_t>(verified_edges.size());
        live_rt_j["unique_owned_rrg_nodes"] = static_cast<int64_t>(node_owner_net.size());
        live_rt_j["unreached_sinks"] = 0;
        live_rt_j["invalid_parent_edges"] = 0;
        live_rt_j["ownership_conflicts"] = 0;

        std::map<std::string, DqsLaneInfo> sorted_lanes;
        for (const auto& [ko, dqi] : dqs_lane_info) sorted_lanes[inst_name[ko]] = dqi;
        JsonValue lanes_j = JsonValue::object();
        for (const auto& [kn, dqi] : sorted_lanes) {
            JsonValue lj = JsonValue::object();
            JsonValue drc = JsonValue::array(); drc.push_back(dqi.r_dq); drc.push_back(dqi.c_dq);
            JsonValue irc = JsonValue::array(); irc.push_back(dqi.r_io); irc.push_back(dqi.c_io);
            lj["dqs_rc"] = std::move(drc);
            lj["iob_inst"] = dqi.iob_inst;
            lj["iob_pad"] = dqi.iob_pad;
            lj["iob_rc"] = std::move(irc);
            lj["bank"] = dqi.bank;
            lj["dqs_group"] = dqi.dqs_group;
            lj["drived_dqs"] = dqi.drived_dqs;
            lanes_j[kn] = std::move(lj);
        }

        JsonValue coloc_full_j = JsonValue::object();
        coloc_full_j["lut_ff_pairs_verified"] = lut_ff_verified;
        coloc_full_j["alu_chains_verified"] = alu_chains_verified;
        coloc_full_j["alu_cells_verified"] = alu_cells_verified;
        coloc_full_j["mux_trees_verified"] = mux_trees_verified;
        coloc_full_j["iologic_iob_pairs_verified"] = iologic_iob_verified;
        coloc_full_j["dqs_sites_verified"] = dqs_sites_verified;
        coloc_full_j["ssram_verified"] = ssram_verified;
        coloc_full_j["rom16_verified"] = rom16_verified;
        coloc_full_j["dl_verified"] = dl_verified;
        coloc_full_j["ddr_byte_lanes"] = std::move(lanes_j);
        coloc_full_j["colocation_mismatches"] = 0;

        JsonValue route_audit = JsonValue::object();
        route_audit["status"] = "post_fusemap_routes_exemptions_and_frame_crc_verified";
        route_audit["unconditional_hardware_or_full_bitstream_signoff"] = false;
        route_audit["scope_qualification"] =
            "Independently verifies live post-fusemap NETLIST::node sink-to-source trees via gowin.dll node::getout, "
            "per-pin mode/parameter/byte-lane exemptions, dedicated site co-location via Instance::getPhyInst, and "
            "calibrated 21,872-frame CRC integrity. Footer/checksum commands, full silicon configuration bit-tables, "
            "and board hardware behavior remain unverified.";
        route_audit["audit_seconds"] = std::round(t_audit_sec * 10000.0) / 10000.0;
        route_audit["pin_connectivity_inventory"] = pin_inv_j;
        route_audit["live_route_tree_verification"] = live_rt_j;
        route_audit["dedicated_colocation_verification"] = std::move(coloc_full_j);
        route_audit["bitstream_frame_integrity"] = frame_audit;
        write_json_file(path_join(out, "route_and_exemption_audit.json"), route_audit, 2);

        double t_sta_sec = perf_counter() - t_sta0;
        JsonValue coloc_m_j = JsonValue::object();
        coloc_m_j["lut_ff_pairs_verified"] = lut_ff_verified;
        coloc_m_j["alu_chains_verified"] = alu_chains_verified;
        coloc_m_j["alu_cells_verified"] = alu_cells_verified;
        coloc_m_j["mux_trees_verified"] = mux_trees_verified;
        coloc_m_j["iologic_iob_pairs_verified"] = iologic_iob_verified;
        coloc_m_j["dqs_sites_verified"] = dqs_sites_verified;
        coloc_m_j["colocation_mismatches"] = 0;

        JsonValue metrics = JsonValue::object();
        metrics["flat_instances"] = static_cast<int64_t>(insts.size());
        metrics["placement_units"] = num_units;
        metrics["placement_seconds"] = t_place_sec;
        metrics["routing_iterations"] = out_iters;
        metrics["routing_seconds"] = t_route_sec;
        metrics["routed_rrg_nodes"] = num_route_records;
        metrics["sta_and_export_seconds"] = t_sta_sec;
        metrics["post_fusemap_audit_seconds"] = t_audit_sec;
        metrics["worker_total_seconds"] = perf_counter() - t_worker0;
        metrics["parents_preserved"] = parents_preserved;
        metrics["cst_export_returncode"] = cst_rc;
        metrics["pin_connectivity_inventory"] = std::move(pin_inv_j);
        metrics["live_route_tree_verification"] = std::move(live_rt_j);
        metrics["dedicated_colocation_verification"] = std::move(coloc_m_j);
        metrics["bitstream_frame_integrity_status"] = frame_audit["status"];
        metrics["vendor_placement_executed"] = false;
        metrics["vendor_routing_executed"] = false;
        write_json_file(path_join(out, "worker_metrics.json"), metrics, 2);

        std::fprintf(stdout, "[ownpr] STA + reports + fusemap + live route/exemption audit (%.2fs) completed in %.2fs\n", t_audit_sec, t_sta_sec);
        std::fprintf(stdout, "Placement and routing completed\n");
        std::fflush(stdout);
        return 0;
    } catch (const std::exception& ex) {
        std::fprintf(stderr, "ERROR (ownpr_worker): %s\n", ex.what());
        std::fflush(stderr);
        return 1;
    }
}

namespace {

struct StaFeedback {
    std::map<std::string, double> crit_pins;
    std::map<std::string, double> crit_insts;
    std::map<std::string, double> hold_pins;
};

StaFeedback extract_sta_timing_feedback(std::string_view tr_text) {
    StaFeedback fb;
    auto lines = split_lines(tr_text);

    auto parse_paths = [&](size_t start_ln, size_t end_ln, bool is_setup) {
        size_t i = start_ln;
        while (i < end_ln) {
            std::string t = trim(lines[i]);
            if (starts_with(t, "Path") && t.size() > 4 && std::isdigit(static_cast<unsigned char>(t[4]))) {
                size_t blk_end = i + 1;
                while (blk_end < end_ln) {
                    std::string bt = trim(lines[blk_end]);
                    if (starts_with(bt, "Path") && bt.size() > 4 && std::isdigit(static_cast<unsigned char>(bt[4]))) break;
                    ++blk_end;
                }
                bool has_slk = false;
                double slk = 0.0;
                std::string from_i, to_i;
                size_t arr_start = blk_end, req_start = blk_end;
                for (size_t k = i + 1; k < blk_end; ++k) {
                    std::string kl = trim(lines[k]);
                    if (!has_slk && starts_with(kl, "Slack")) {
                        size_t col = kl.find(':');
                        if (col != std::string::npos) {
                            auto ws = split_ws(std::string_view(kl).substr(col + 1));
                            if (!ws.empty()) { slk = std::stod(ws[0]); has_slk = true; }
                        }
                    } else if (starts_with(kl, "From")) {
                        size_t col = kl.find(':');
                        if (col != std::string::npos) {
                            auto ws = split_ws(std::string_view(kl).substr(col + 1));
                            if (!ws.empty()) from_i = ws[0];
                        }
                    } else if (starts_with(kl, "To")) {
                        size_t col = kl.find(':');
                        if (col != std::string::npos) {
                            auto ws = split_ws(std::string_view(kl).substr(col + 1));
                            if (!ws.empty()) to_i = ws[0];
                        }
                    } else if (kl == "Data Arrival Path:") {
                        arr_start = k + 1;
                    } else if (kl == "Data Required Path:") {
                        req_start = k;
                    }
                }
                if (has_slk) {
                    if (is_setup && slk < 0.0) {
                        double boost = std::min(0.995, 0.935 + 0.060 * std::min(1.0, -slk / 2.5));
                        if (!from_i.empty()) fb.crit_insts[from_i] = std::max(fb.crit_insts[from_i], boost);
                        if (!to_i.empty()) fb.crit_insts[to_i] = std::max(fb.crit_insts[to_i], boost);
                        if (arr_start < blk_end) {
                            size_t ae = std::min(req_start, blk_end);
                            for (size_t k = arr_start; k < ae; ++k) {
                                auto toks = split_ws(lines[k]);
                                if (toks.size() >= 7 && (toks[2] == "tNET" || toks[2] == "tINS" || toks[2] == "tC2Q")) {
                                    const std::string& ns = toks.back();
                                    size_t sl = ns.rfind('/');
                                    if (sl != std::string::npos) {
                                        std::string inm = ns.substr(0, sl), pl = ns.substr(sl + 1);
                                        if (pl != "CLK" && pl != "CLKA" && pl != "CLKB" && pl != "CLKOUT") {
                                            fb.crit_insts[inm] = std::max(fb.crit_insts[inm], boost * 0.98);
                                            if (toks[2] == "tNET") fb.crit_pins[inm + "/" + pl] = std::max(fb.crit_pins[inm + "/" + pl], boost);
                                        }
                                    }
                                }
                            }
                        }
                    } else if (!is_setup && slk < 0.045 && arr_start < blk_end) {
                        size_t ae = std::min(req_start, blk_end);
                        std::string last_pkey;
                        double last_fast_d = 0.0;
                        for (size_t k = arr_start; k < ae; ++k) {
                            auto toks = split_ws(lines[k]);
                            if (toks.size() >= 7 && toks[2] == "tNET") {
                                const std::string& ns = toks.back();
                                size_t sl = ns.rfind('/');
                                if (sl != std::string::npos) {
                                    std::string inm = ns.substr(0, sl), pl = ns.substr(sl + 1);
                                    if (pl != "CLK" && pl != "CLKA" && pl != "CLKB" && pl != "CLKOUT") {
                                        last_pkey = inm + "/" + pl;
                                        last_fast_d = std::stod(toks[1]);
                                    }
                                }
                            }
                        }
                        if (!last_pkey.empty()) {
                            double req_su = std::max(0.85, 2.15 * last_fast_d + 2.35 * (0.055 - slk));
                            fb.hold_pins[last_pkey] = std::max(fb.hold_pins[last_pkey], std::round(req_su * 1000.0) / 1000.0);
                        }
                    }
                }
                i = blk_end;
                continue;
            }
            ++i;
        }
    };

    size_t su_beg = lines.size(), su_end = lines.size();
    size_t hd_beg = lines.size(), hd_end = lines.size();
    for (size_t i = 0; i < lines.size(); ++i) {
        std::string rt = rtrim(lines[i]);
        if (su_beg == lines.size() && rt == "3.3.1 Setup Analysis Report") su_beg = i + 1;
        else if (su_beg < lines.size() && su_end == lines.size() && (starts_with(rt, "3.3.") || starts_with(rt, "3.4 ") || starts_with(rt, "3.5 "))) su_end = i;
        if (hd_beg == lines.size() && rt == "3.3.2 Hold Analysis Report") hd_beg = i + 1;
        else if (hd_beg < lines.size() && hd_end == lines.size() && (starts_with(rt, "3.3.") || starts_with(rt, "3.4 ") || starts_with(rt, "3.5 ") || starts_with(rt, "3.6 ") || starts_with(rt, "3.7 ") || starts_with(rt, "3.8 "))) hd_end = i;
    }
    if (su_beg < lines.size()) parse_paths(su_beg, su_end, true);
    if (hd_beg < lines.size()) parse_paths(hd_beg, hd_end, false);
    return fb;
}

using PassScore = std::tuple<int, int, double, int64_t, double>;

PassScore score_pnr_summary(const JsonValue& chk_d, std::string_view tr_s) {
    int64_t hold_v = 0, setup_v = 0, rec_v = 0, rem_v = 0;
    if (chk_d.contains("violated_endpoints") && chk_d["violated_endpoints"].is_obj()) {
        const auto& ve = chk_d["violated_endpoints"];
        if (ve.contains("Hold")) hold_v = ve["Hold"].as_i64();
        if (ve.contains("Setup")) setup_v = ve["Setup"].as_i64();
        if (ve.contains("Recovery")) rec_v = ve["Recovery"].as_i64();
        if (ve.contains("Removal")) rem_v = ve["Removal"].as_i64();
    }
    auto lines = split_lines(tr_s);
    double wns_su = 0.0, tns_su = 0.0;
    bool got_tbl = false, got_tns = false;
    for (size_t i = 0; i < lines.size(); ++i) {
        std::string rt = rtrim(lines[i]);
        if (!got_tbl && rt == "3.1.1 Setup Paths Table") {
            got_tbl = true;
            for (size_t k = i + 1; k < lines.size(); ++k) {
                if (starts_with(lines[k], "3.1.2 ") || starts_with(lines[k], "3.2 ")) break;
                auto flds = split_ws(lines[k]);
                if (flds.size() >= 9 && std::all_of(flds[0].begin(), flds[0].end(), [](unsigned char c) { return std::isdigit(c); })) {
                    wns_su = std::stod(flds[1]);
                    break;
                }
            }
        } else if (!got_tns && rt == "2.4 Total Negative Slack Summary") {
            got_tns = true;
            for (size_t k = i + 1; k < lines.size(); ++k) {
                if (starts_with(lines[k], "3.") || starts_with(lines[k], "2.5 ")) break;
                auto flds = split_ws(lines[k]);
                if (flds.size() == 4 && to_lower_ascii(flds[1]) == "setup") tns_su += std::stod(flds[2]);
            }
        }
    }
    bool core_cl = chk_d.contains("core_timing_closed") ? chk_d["core_timing_closed"].as_bool()
                 : (chk_d.contains("timing_closed") && chk_d["timing_closed"].as_bool());
    return {
        core_cl ? 1 : 0,
        (hold_v == 0 && rec_v == 0 && rem_v == 0) ? 1 : 0,
        std::round(wns_su * 1000.0) / 1000.0,
        -setup_v,
        std::round(tns_su * 1000.0) / 1000.0
    };
}

int64_t permute_vg_luts_from_routed_delays(
    const std::string& vg_path,
    std::map<std::string, double>& latest_routed_ns,
    const std::map<std::string, double>& fresh_crit_pins,
    std::map<std::string, double>& acc_routed_ns,
    std::map<std::string, double>& crit_pins,
    std::map<std::string, double>& hold_pins
) {
    if (!file_exists(vg_path) || latest_routed_ns.empty() || fresh_crit_pins.empty()) return 0;
    std::string raw_txt = read_file_text(vg_path);
    std::vector<std::string> lines = split_lines(raw_txt);
    if (lines.empty()) return 0;

    auto norm_ident = [](std::string_view sv) -> std::string {
        std::string t = trim(sv);
        if (!t.empty() && t.front() == '\\') t.erase(t.begin());
        return t;
    };

    std::unordered_map<std::string, int32_t> wire_to_id;
    std::vector<int32_t> dsu_parent;
    auto get_wid = [&](const std::string& w) -> int32_t {
        if (w.empty()) return -1;
        auto [it, ins] = wire_to_id.emplace(w, static_cast<int32_t>(dsu_parent.size()));
        if (ins) dsu_parent.push_back(it->second);
        return it->second;
    };
    auto dsu_find = [&](int32_t x) -> int32_t {
        if (x < 0) return -1;
        int32_t r = x;
        while (dsu_parent[r] != r) r = dsu_parent[r];
        while (x != r) { int32_t p = dsu_parent[x]; dsu_parent[x] = r; x = p; }
        return r;
    };
    auto dsu_unite = [&](int32_t a, int32_t b) {
        a = dsu_find(a); b = dsu_find(b);
        if (a >= 0 && b >= 0 && a != b) dsu_parent[b] = a;
    };

    auto parse_port_bits = [&](std::string_view raw_arg) -> std::vector<int32_t> {
        std::string t = trim(raw_arg);
        std::vector<int32_t> res;
        if (t.empty()) return res;
        if (t.front() == '{' && t.back() == '}') {
            auto parts = split_char(std::string_view(t).substr(1, t.size() - 2), ',');
            res.reserve(parts.size());
            for (const auto& p : parts) res.push_back(get_wid(norm_ident(p)));
        } else {
            res.push_back(get_wid(norm_ident(t)));
        }
        return res;
    };

    struct VgPort {
        std::string pin;
        std::string raw_arg;
        size_t line_idx = 0;
        std::string indent;
        bool has_comma = false;
        std::vector<int32_t> bits;
    };
    struct VgCell {
        std::string type;
        std::string raw_inst;
        std::string gw_inst;
        std::vector<VgPort> ports;
    };

    std::vector<VgCell> cells;
    std::unordered_set<int32_t> primary_in_wids;
    std::unordered_map<std::string, std::pair<size_t, uint16_t>> lut_defparams;

    static const std::unordered_set<std::string> skip_kw = {
        "module", "endmodule", "input", "output", "inout", "wire", "reg", "assign", "defparam", "parameter", "localparam"
    };

    for (size_t i = 0; i < lines.size(); ++i) {
        std::string t = trim(lines[i]);
        if (t.empty() || starts_with(t, "//") || starts_with(t, "/*")) continue;
        if ((starts_with(t, "input ") || starts_with(t, "inout ")) && t.back() == ';') {
            size_t sp = t.find(' ');
            std::string wn = norm_ident(std::string_view(t).substr(sp + 1, t.size() - sp - 2));
            if (!wn.empty()) primary_in_wids.insert(get_wid(wn));
            continue;
        }
        if (starts_with(t, "assign ") && t.back() == ';') {
            size_t eq = t.find('=');
            if (eq != std::string::npos) {
                std::string lhs = norm_ident(std::string_view(t).substr(7, eq - 7));
                std::string rhs = norm_ident(std::string_view(t).substr(eq + 1, t.size() - eq - 2));
                if (!lhs.empty() && !rhs.empty()) dsu_unite(get_wid(lhs), get_wid(rhs));
            }
            continue;
        }
        if (starts_with(t, "defparam ") && t.back() == ';') {
            size_t eq = t.find('=');
            if (eq != std::string::npos) {
                std::string lhs = trim(std::string_view(t).substr(9, eq - 9));
                if (ends_with(lhs, ".INIT")) {
                    std::string inst_nm = norm_ident(std::string_view(lhs).substr(0, lhs.size() - 5));
                    std::string rhs = trim(std::string_view(t).substr(eq + 1, t.size() - eq - 2));
                    try {
                        auto [_, val] = parse_verilog_literal_u64(rhs);
                        lut_defparams[inst_nm] = {i, static_cast<uint16_t>(val & 0xFFFFU)};
                    } catch (...) {}
                }
            }
            continue;
        }
        if (t.back() == '(') {
            auto ws = split_ws(std::string_view(t).substr(0, t.size() - 1));
            if (ws.size() == 2 && !skip_kw.count(ws[0])) {
                size_t close_i = i + 1;
                while (close_i < lines.size() && trim(lines[close_i]) != ");") ++close_i;
                if (close_i < lines.size()) {
                    VgCell vc;
                    vc.type = ws[0];
                    vc.raw_inst = ws[1];
                    vc.gw_inst = norm_ident(ws[1]);
                    for (size_t pl = i + 1; pl < close_i; ++pl) {
                        const std::string& raw_ln = lines[pl];
                        std::string pt = trim(raw_ln);
                        if (pt.empty() || pt.front() != '.') continue;
                        size_t lp = pt.find('(');
                        size_t rp = pt.rfind(')');
                        if (lp == std::string::npos || rp == std::string::npos || rp <= lp) continue;
                        VgPort vp;
                        vp.pin = trim(std::string_view(pt).substr(1, lp - 1));
                        vp.raw_arg = pt.substr(lp + 1, rp - lp - 1);
                        vp.line_idx = pl;
                        size_t dot_pos = raw_ln.find('.');
                        vp.indent = (dot_pos != std::string::npos) ? raw_ln.substr(0, dot_pos) : "    ";
                        vp.has_comma = ends_with(pt, ",");
                        vp.bits = parse_port_bits(vp.raw_arg);
                        vc.ports.push_back(std::move(vp));
                    }
                    cells.push_back(std::move(vc));
                    i = close_i;
                }
            }
        }
    }

    if (cells.empty() || lut_defparams.empty()) return 0;

    static const std::unordered_set<std::string> comb_types = {
        "LUT1","LUT2","LUT3","LUT4","MUX2_LUT5","MUX2_LUT6","MUX2_LUT7","MUX2_LUT8","ALU",
        "RAM16S1","RAM16S2","RAM16S4","RAM16SDP1","RAM16SDP2","RAM16SDP4","ROM16"
    };
    auto is_out_pin = [](const std::string& ct, const std::string& pn) -> bool {
        if (ct == "LUT1" || ct == "LUT2" || ct == "LUT3" || ct == "LUT4") return pn == "F";
        if (starts_with(ct, "MUX2_LUT")) return pn == "O";
        if (ct == "ALU") return (pn == "SUM" || pn == "COUT");
        if (starts_with(ct, "RAM16") || ct == "ROM16") return pn == "DO";
        if (ct == "GND") return pn == "G";
        if (ct == "VCC") return pn == "V";
        return starts_with(pn, "Q") || starts_with(pn, "DO") || starts_with(pn, "O") || starts_with(pn, "CLKOUT") || starts_with(pn, "CDOUT");
    };
    auto is_comb_in_pin = [&](const std::string& ct, const std::string& pn) -> bool {
        if (is_out_pin(ct, pn)) return false;
        if (starts_with(ct, "RAM16SDP")) return pn == "RAD";
        if (starts_with(ct, "RAM16S") || ct == "ROM16") return pn == "AD";
        return true;
    };

    std::unordered_map<int32_t, std::pair<size_t, std::string>> drivers;
    std::unordered_map<int32_t, int> load_cnt;
    std::unordered_set<int32_t> const_nets, ded_zero_nets;
    for (const char* clit : {"1'b0", "1'b1", "1'bx", "1'bz", "0", "1"}) {
        auto it = wire_to_id.find(clit);
        if (it != wire_to_id.end()) const_nets.insert(dsu_find(it->second));
    }

    for (size_t ci = 0; ci < cells.size(); ++ci) {
        VgCell& c = cells[ci];
        for (auto& vp : c.ports) {
            for (int32_t& b : vp.bits) b = dsu_find(b);
            if (is_out_pin(c.type, vp.pin)) {
                for (int32_t b : vp.bits) {
                    if (b >= 0) {
                        drivers[b] = {ci, vp.pin};
                        if (c.type == "GND" || c.type == "VCC") const_nets.insert(b);
                    }
                }
            } else {
                for (int32_t b : vp.bits) if (b >= 0) load_cnt[b] += 1;
            }
        }
    }
    for (size_t ci = 0; ci < cells.size(); ++ci) {
        const VgCell& c = cells[ci];
        if (starts_with(c.type, "MUX2_LUT")) {
            for (const auto& vp : c.ports) {
                if ((vp.pin == "I0" || vp.pin == "I1") && !vp.bits.empty() && vp.bits[0] >= 0) ded_zero_nets.insert(vp.bits[0]);
            }
        } else if (c.type == "ALU") {
            for (const auto& vp : c.ports) {
                if (vp.pin == "CIN" && !vp.bits.empty() && vp.bits[0] >= 0) {
                    auto dit = drivers.find(vp.bits[0]);
                    if (dit != drivers.end() && dit->second.second == "COUT") ded_zero_nets.insert(vp.bits[0]);
                }
            }
        }
    }

    std::vector<size_t> comb_idxs;
    std::unordered_set<size_t> comb_set;
    for (size_t ci = 0; ci < cells.size(); ++ci) {
        if (comb_types.count(cells[ci].type)) { comb_idxs.push_back(ci); comb_set.insert(ci); }
    }
    if (comb_idxs.empty()) return 0;

    std::unordered_map<size_t, int> in_deg;
    std::unordered_map<size_t, std::vector<size_t>> succ;
    for (size_t ci : comb_idxs) {
        const VgCell& c = cells[ci];
        std::unordered_set<size_t> preds;
        for (const auto& vp : c.ports) {
            if (!is_comb_in_pin(c.type, vp.pin)) continue;
            for (int32_t b : vp.bits) {
                if (b >= 0) {
                    auto dit = drivers.find(b);
                    if (dit != drivers.end() && comb_set.count(dit->second.first) && dit->second.first != ci) {
                        preds.insert(dit->second.first);
                    }
                }
            }
        }
        in_deg[ci] = static_cast<int>(preds.size());
        for (size_t p : preds) succ[p].push_back(ci);
    }

    std::deque<size_t> q;
    for (size_t ci : comb_idxs) if (in_deg[ci] == 0) q.push_back(ci);
    std::vector<size_t> topo;
    topo.reserve(comb_idxs.size());
    while (!q.empty()) {
        size_t u = q.front(); q.pop_front();
        topo.push_back(u);
        if (auto sit = succ.find(u); sit != succ.end()) {
            for (size_t v : sit->second) if (--in_deg[v] == 0) q.push_back(v);
        }
    }
    if (topo.size() < comb_idxs.size()) {
        std::unordered_set<size_t> seen(topo.begin(), topo.end());
        for (size_t ci : comb_idxs) if (!seen.count(ci)) topo.push_back(ci);
    }

    std::unordered_map<int32_t, double> net_arr;
    for (int32_t w : primary_in_wids) {
        int32_t r = dsu_find(w);
        if (r >= 0) net_arr[r] = 360.0;
    }
    for (size_t ci = 0; ci < cells.size(); ++ci) {
        if (comb_set.count(ci)) continue;
        const VgCell& c = cells[ci];
        if (c.type == "GND" || c.type == "VCC") continue;
        bool is_bsram = (c.type.find("SDP") != std::string::npos || c.type.find("BSRAM") != std::string::npos
                      || c.type.find("DPB") != std::string::npos || c.type.find("DPX9") != std::string::npos
                      || c.type == "SP" || c.type == "SPX9" || starts_with(c.type, "pROM"));
        double t_co = is_bsram ? 2150.0
                    : ((c.type.find("MULT") != std::string::npos || c.type.find("ALU54") != std::string::npos) ? 2300.0 : 360.0);
        for (const auto& vp : c.ports) {
            if (is_out_pin(c.type, vp.pin)) {
                for (int32_t b : vp.bits) if (b >= 0 && t_co > net_arr[b]) net_arr[b] = t_co;
            }
        }
    }

    auto wire_delay_ps = [&](const std::string& gw_inst, const std::string& pin_lbl, int32_t b) -> double {
        if (b < 0 || const_nets.count(b) || ded_zero_nets.count(b)) return 0.0;
        std::string pkey = gw_inst + "/" + pin_lbl;
        auto rit = latest_routed_ns.find(pkey);
        if (rit != latest_routed_ns.end() && rit->second > 0.0) {
            double w_ns = rit->second;
            if (!fresh_crit_pins.count(pkey) && !crit_pins.count(pkey)) {
                w_ns = std::min(w_ns, 0.62);
            }
            return w_ns * 1000.0;
        }
        int fo = load_cnt[b];
        return 480.0 + 115.0 * std::log2(1.0 + static_cast<double>(std::min(std::max(fo, 1), 256)));
    };
    auto lut_pin_active = [](uint16_t truth, int k, int pin) -> bool {
        int n = 1 << k;
        int step = 1 << pin;
        for (int m = 0; m < n; ++m) {
            if ((m & step) == 0) {
                if (((truth >> m) & 1U) != ((truth >> (m | step)) & 1U)) return true;
            }
        }
        return false;
    };
    constexpr double LUT_PIN_PS[4] = {525.0, 511.0, 435.0, 265.0};

    std::vector<size_t> perm_cis;
    std::vector<uint16_t> perm_inits;
    std::vector<uint8_t> perm_ks;
    std::vector<uint8_t> perm_ords;

    for (size_t ci : topo) {
        VgCell& c = cells[ci];
        if (c.type == "LUT1" || c.type == "LUT2" || c.type == "LUT3" || c.type == "LUT4") {
            int k = c.type.back() - '0';
            auto dpit = lut_defparams.find(c.gw_inst);
            int p_idx[4] = {-1, -1, -1, -1};
            int f_idx = -1;
            for (size_t pi = 0; pi < c.ports.size(); ++pi) {
                const std::string& pn = c.ports[pi].pin;
                if (pn == "F") f_idx = static_cast<int>(pi);
                else if (pn.size() == 2 && pn[0] == 'I' && pn[1] >= '0' && pn[1] <= '3') {
                    p_idx[pn[1] - '0'] = static_cast<int>(pi);
                }
            }
            bool ok_ports = (f_idx >= 0 && dpit != lut_defparams.end());
            for (int j = 0; j < k; ++j) {
                if (p_idx[j] < 0 || c.ports[p_idx[j]].bits.size() != 1) ok_ports = false;
            }
            if (!ok_ports) continue;

            uint16_t truth = dpit->second.second;
            double at[4]{};
            int crit_tier[4]{};
            double crit_val[4]{};
            bool active[4]{};
            int n_act = 0;
            bool has_subopt_crit = false;
            for (int j = 0; j < k; ++j) {
                int32_t b = c.ports[p_idx[j]].bits[0];
                active[j] = lut_pin_active(truth, k, j);
                std::string pkey = c.gw_inst + "/I" + std::to_string(j);
                if (active[j]) {
                    ++n_act;
                    at[j] = ((b >= 0) ? net_arr[b] : 0.0) + wire_delay_ps(c.gw_inst, "I" + std::to_string(j), b);
                    auto fit = fresh_crit_pins.find(pkey);
                    auto ait = crit_pins.find(pkey);
                    if (fit != fresh_crit_pins.end()) {
                        crit_tier[j] = 2;
                        crit_val[j] = fit->second;
                        if (j < k - 1) has_subopt_crit = true;
                    } else if (ait != crit_pins.end()) {
                        crit_tier[j] = 1;
                        crit_val[j] = ait->second;
                    }
                } else {
                    at[j] = -1e9 + static_cast<double>(j);
                    crit_tier[j] = -1;
                }
            }
            double out_a = 0.0;
            if (k >= 2 && n_act >= 2) {
                int order[4] = {0, 1, 2, 3};
                std::sort(order, order + k, [&](int a, int b) {
                    if (crit_tier[a] != crit_tier[b]) return crit_tier[a] < crit_tier[b];
                    if (std::abs(crit_val[a] - crit_val[b]) > 1e-4) return crit_val[a] < crit_val[b];
                    if (at[a] != at[b]) return at[a] < at[b];
                    return a < b;
                });
                double before = 0.0, after = 0.0;
                bool non_id = false;
                for (int j = 0; j < k; ++j) {
                    if (active[j])         before = std::max(before, at[j] + LUT_PIN_PS[j]);
                    if (active[order[j]])  after  = std::max(after, at[order[j]] + LUT_PIN_PS[j]);
                    if (order[j] != j) non_id = true;
                }
                if (non_id && (has_subopt_crit || after < before - 20.0)) {
                    std::string old_raw[4];
                    int32_t old_b[4];
                    for (int j = 0; j < k; ++j) {
                        old_raw[j] = c.ports[p_idx[j]].raw_arg;
                        old_b[j]   = c.ports[p_idx[j]].bits[0];
                    }
                    for (int np = 0; np < k; ++np) {
                        VgPort& vp = c.ports[p_idx[np]];
                        vp.raw_arg = old_raw[order[np]];
                        vp.bits[0] = old_b[order[np]];
                        lines[vp.line_idx] = vp.indent + ".I" + std::to_string(np) + "(" + vp.raw_arg + ")" + (vp.has_comma ? "," : "");
                    }
                    auto remap_pin_tbl = [&](std::map<std::string, double>& tbl) {
                        bool has_any = false;
                        double vals[4]{};
                        bool pres[4]{};
                        for (int j = 0; j < k; ++j) {
                            std::string key = c.gw_inst + "/I" + std::to_string(j);
                            auto it = tbl.find(key);
                            if (it != tbl.end()) {
                                pres[j] = true;
                                vals[j] = it->second;
                                tbl.erase(it);
                                has_any = true;
                            }
                        }
                        if (has_any) {
                            for (int np = 0; np < k; ++np) {
                                int op = order[np];
                                if (pres[op]) tbl[c.gw_inst + "/I" + std::to_string(np)] = vals[op];
                            }
                        }
                    };
                    remap_pin_tbl(latest_routed_ns);
                    remap_pin_tbl(acc_routed_ns);
                    remap_pin_tbl(crit_pins);
                    remap_pin_tbl(hold_pins);

                    perm_cis.push_back(ci);
                    perm_inits.push_back(truth);
                    perm_ks.push_back(static_cast<uint8_t>(k));
                    for (int np = 0; np < 4; ++np) perm_ords.push_back(static_cast<uint8_t>(np < k ? order[np] : 0));
                    out_a = after;
                } else {
                    out_a = before;
                }
            } else {
                for (int j = 0; j < k; ++j) if (active[j]) out_a = std::max(out_a, at[j] + LUT_PIN_PS[j]);
            }
            if (!c.ports[f_idx].bits.empty() && c.ports[f_idx].bits[0] >= 0 && out_a > net_arr[c.ports[f_idx].bits[0]]) {
                net_arr[c.ports[f_idx].bits[0]] = out_a;
            }
        } else if (starts_with(c.type, "MUX2_LUT")) {
            int32_t b0 = -1, b1 = -1, bs = -1, bo = -1;
            for (const auto& vp : c.ports) {
                if (vp.bits.empty()) continue;
                if (vp.pin == "I0") b0 = vp.bits[0];
                else if (vp.pin == "I1") b1 = vp.bits[0];
                else if (vp.pin == "S0") bs = vp.bits[0];
                else if (vp.pin == "O")  bo = vp.bits[0];
            }
            double out_a = std::max({
                ((b0 >= 0) ? net_arr[b0] : 0.0) + 195.0,
                ((b1 >= 0) ? net_arr[b1] : 0.0) + 195.0,
                ((bs >= 0) ? (net_arr[bs] + wire_delay_ps(c.gw_inst, "S0", bs)) : 0.0) + 227.0
            });
            if (bo >= 0 && out_a > net_arr[bo]) net_arr[bo] = out_a;
        } else if (c.type == "ALU") {
            double base_a = 0.0;
            for (const auto& vp : c.ports) {
                if (vp.bits.empty() || vp.bits[0] < 0) continue;
                if (vp.pin == "CIN" || vp.pin == "I0" || vp.pin == "I1" || vp.pin == "I3") {
                    int32_t b = vp.bits[0];
                    base_a = std::max(base_a, net_arr[b] + wire_delay_ps(c.gw_inst, vp.pin, b) + ((vp.pin == "CIN") ? 45.0 : 300.0));
                }
            }
            for (const auto& vp : c.ports) {
                if (vp.bits.empty() || vp.bits[0] < 0) continue;
                if (vp.pin == "COUT" && base_a > net_arr[vp.bits[0]]) net_arr[vp.bits[0]] = base_a;
                else if (vp.pin == "SUM" && base_a + 260.0 > net_arr[vp.bits[0]]) net_arr[vp.bits[0]] = base_a + 260.0;
            }
        } else if (starts_with(c.type, "RAM16") || c.type == "ROM16") {
            std::string apin = starts_with(c.type, "RAM16SDP") ? "RAD" : "AD";
            double out_a = starts_with(c.type, "RAM16") ? 560.0 : 0.0;
            for (const auto& vp : c.ports) {
                if (vp.pin == apin) {
                    int w = static_cast<int>(vp.bits.size());
                    for (int pos = 0; pos < w; ++pos) {
                        int32_t b = vp.bits[pos];
                        if (b >= 0) {
                            std::string pl = (w == 1) ? apin : (apin + "[" + std::to_string(w - 1 - pos) + "]");
                            out_a = std::max(out_a, net_arr[b] + wire_delay_ps(c.gw_inst, pl, b) + 520.0);
                        }
                    }
                }
            }
            for (const auto& vp : c.ports) {
                if (vp.pin == "DO") {
                    for (int32_t ob : vp.bits) if (ob >= 0 && out_a > net_arr[ob]) net_arr[ob] = out_a;
                }
            }
        }
    }

    if (perm_cis.empty()) return 0;

    std::vector<uint16_t> out_inits(perm_cis.size(), 0);
    SynAccelLib::instance().permute_lut_batch(
        static_cast<int32_t>(perm_cis.size()), perm_inits.data(), perm_ks.data(), perm_ords.data(), out_inits.data()
    );
    for (size_t i = 0; i < perm_cis.size(); ++i) {
        const VgCell& c = cells[perm_cis[i]];
        int k = static_cast<int>(perm_ks[i]);
        int width = 1 << k;
        int hex_digits = (width + 3) / 4;
        char hbuf[32];
        std::snprintf(hbuf, sizeof(hbuf), "%d'h%0*x", width, hex_digits, static_cast<unsigned>(out_inits[i]) & ((1U << width) - 1U));
        size_t dpln = lut_defparams.at(c.gw_inst).first;
        size_t dp_pos = lines[dpln].find("defparam");
        std::string indent = (dp_pos != std::string::npos) ? lines[dpln].substr(0, dp_pos) : "  ";
        lines[dpln] = indent + "defparam " + c.raw_inst + (starts_with(c.raw_inst, "\\") ? " " : "") + ".INIT = " + std::string(hbuf) + ";";
    }
    write_file_text(vg_path, join_strings(lines, "\n") + "\n");
    return static_cast<int64_t>(perm_cis.size());
}

} // namespace

std::pair<JsonValue, JsonValue> run_ownpr_full(
    std::string_view mapped_vg,
    std::string_view /*final_json*/,
    std::string_view sdc_path,
    std::string_view cst_path,
    std::string_view device_cfg_path,
    std::string_view physical_profile_path,
    std::string_view out_dir,
    int timeout_sec,
    std::string_view target_dev_p,
    std::string_view target_part,
    std::string_view csr_path,
    double global_freq_mhz
) {
    std::string out = resolve_path(out_dir);
    const auto& rp = runtime_paths();
    std::string sdk_bin = path_join(rp.gowin_sdk, "bin");
    std::string actual_gowin_sha = verify_or_record_sdk_file_sha(
        path_join(sdk_bin, "gowin.dll"), EXPECTED_GOWIN_DLL_SHA, "bin/gowin.dll"
    );

    std::string eff_dev_p = target_dev_p.empty() ? std::string(PINNED_DEVICE) : std::string(target_dev_p);
    std::string eff_part  = target_part.empty()  ? std::string(PINNED_PART)   : std::string(target_part);

    make_dirs(out, false);
    std::string netlist = path_join(out, "mapped.vg");
    copy_file_overwrite(mapped_vg, netlist);
    std::string sdc = path_join(out, "project.sdc");
    if (!sdc_path.empty() && file_exists(sdc_path)) {
        copy_file_overwrite(sdc_path, sdc);
    } else {
        write_file_text(sdc, "# Auto-generated empty SDC placeholder\n");
    }
    std::string cst;
    if (!cst_path.empty()) {
        cst = path_join(out, "project.cst");
        copy_file_overwrite(cst_path, cst);
    }
    std::string csr;
    if (!csr_path.empty() && file_exists(csr_path)) {
        csr = path_join(out, "serdes.csr");
        copy_file_overwrite(csr_path, csr);
    }

    bool has_user_sdc = (!sdc_path.empty() && file_exists(sdc_path));
    bool emit_ide_html = (get_env("NEXTGOWIN_EMIT_IDE_HTML") == "1");
    std::vector<std::string> commands = {
        "-d " + posix_path(netlist),
        "-p " + eff_dev_p,
        "-pn " + eff_part,
        "-bit",
        "-tr",
        "-timing",
    };
    if (has_user_sdc || global_freq_mhz <= 0.0) {
        commands.push_back("-sdc " + posix_path(sdc));
    }
    if (global_freq_mhz > 0.0) {
        char fbuf[64];
        std::snprintf(fbuf, sizeof(fbuf), "-global_freq %.3f", global_freq_mhz);
        commands.push_back(fbuf);
    }
    if (emit_ide_html) commands.push_back("-ph");
    if (!cst.empty()) commands.push_back("-cst " + posix_path(cst));
    if (!csr.empty()) commands.push_back("-csr " + posix_path(csr));
    if (!device_cfg_path.empty()) {
        std::string cfg = path_join(out, "device.cfg");
        copy_file_overwrite(device_cfg_path, cfg);
        commands.push_back("-cfg " + posix_path(cfg));
    }
    commands.push_back("-o " + posix_path(out) + "/mapped.vo");
    write_file_text(path_join(out, "cmd.do"), join_strings(commands, "\n") + "\n");

    JsonValue result = JsonValue::object();
    result["scope"] = "General custom placement and routing (ownpr) of structural project netlist. Zero vendor placement/routing; no board programming.";
    result["status"] = "pnr_running";
    result["device_p"] = eff_dev_p;
    result["part"] = eff_part;
    result["dll_sha256"] = actual_gowin_sha;
    result["input_sha256"] = sha256_file(mapped_vg);
    result["netlist_sha256_before"] = sha256_file(netlist);
    result["sdc_sha256"] = sha256_file(sdc);
    result["cst_sha256"] = cst.empty() ? JsonValue(nullptr) : JsonValue(sha256_file(cst));
    if (!csr.empty()) result["csr_sha256"] = sha256_file(csr);
    result["timeout"] = timeout_sec;
    result["gao_id"] = JsonValue(nullptr);
    result["rao_sha256"] = JsonValue(nullptr);
    result["bitstream_requested"] = true;
    result["vendor_placement_executed"] = false;
    result["vendor_routing_executed"] = false;
    result["runner_sha256"] = sha256_file(rp.core_dll);
    result["bridge_dll_sha256"] = sha256_file(rp.bridge_dll);
    result["accel_tier"] = 1;
    result["accel_variant"] = "tier1";

    auto save = [&]() { write_json_file(path_join(out, "manifest.json"), result, 2); };
    save();

    std::string delay_cache_name = "_rrg_delays_" + sha256_str(eff_dev_p + "|" + eff_part).substr(0, 12) + "_v1.bin";
    std::string delay_cache_file = path_join(out, delay_cache_name);
    auto run_worker_pass = [&](double t_sec) -> int {
        GowinHostStaging host_stage(parent_dir(out), rp.gowin_sdk);
        auto pr = run_process_to_log(
            {host_stage.host_exe(), "--internal-pnr-worker", out},
            host_stage.host_bin_dir(),
            path_join(out, "pnr.log"),
            {
                {"NEXTGOWIN_DELAY_CACHE", delay_cache_file},
                {"NEXTGOWIN_EMIT_IDE_HTML", emit_ide_html ? "1" : "0"}
            },
            t_sec
        );
        if (pr.timed_out) throw TimeoutError("Custom ownpr P&R worker timed out");
        return pr.returncode;
    };

    double start = perf_counter();
    auto finalize_and_audit = [&]() -> JsonValue {
        result["pnr_wall_seconds"] = perf_counter() - start;
        result["netlist_sha256_after"] = sha256_file(netlist);
        std::string log_txt = file_exists(path_join(out, "pnr.log")) ? read_file_text(path_join(out, "pnr.log")) : "";
        JsonValue errs = JsonValue::array();
        for (const auto& ln : split_lines(log_txt)) {
            if (starts_with(ln, "ERROR")) errs.push_back(ln);
        }
        result["errors"] = errs;
        if (file_exists(path_join(out, "worker_metrics.json"))) {
            result["ownpr_metrics"] = parse_json_file(path_join(out, "worker_metrics.json"));
        }
        JsonValue arts = JsonValue::object();
        for (const auto& de : list_dir(out)) {
            if (!de.is_file) continue;
            std::string ext = file_ext(de.name);
            if (ext == ".vo" || ext == ".tr" || ext == ".fs" ||
                de.name == "mapped.rpt.txt" || de.name == "mapped.pin.html" ||
                de.name == "route_and_exemption_audit.json" || de.name == "pin_exemption_witnesses.jsonl.gz") {
                JsonValue ai = JsonValue::object();
                ai["bytes"] = static_cast<int64_t>(file_size_bytes(de.path));
                ai["sha256"] = sha256_file(de.path);
                arts[de.name] = std::move(ai);
            }
        }
        result["artifacts"] = std::move(arts);

        static const char* EXP_FILES[] = {
            "mapped.vo", "mapped.tr", "mapped.rpt.txt", "mapped.fs",
            "route_and_exemption_audit.json", "pin_exemption_witnesses.jsonl.gz"
        };
        bool all_present = true;
        for (const char* ef : EXP_FILES) if (!file_exists(path_join(out, ef))) all_present = false;

        bool ok_cur = (result["returncode"].as_i64(-1) == 0)
                   && errs.empty()
                   && all_present
                   && (result["netlist_sha256_before"].as_str() == result["netlist_sha256_after"].as_str())
                   && !result["vendor_placement_executed"].as_bool()
                   && !result["vendor_routing_executed"].as_bool();
        result["status"] = ok_cur ? "complete" : "failed";
        save();
        if (!ok_cur) {
            std::string msg = "Custom ownpr P&R did not complete cleanly; inspect " + path_join(out, "pnr.log");
            if (!errs.as_arr().empty() && errs.as_arr().front().is_string()) {
                msg += " (" + trim(errs.as_arr().front().as_str()) + ")";
            }
            throw std::runtime_error(msg);
        }

        JsonValue chk = !physical_profile_path.empty()
            ? audit_project_timing(out, parse_json_file(physical_profile_path))
            : parse_gowin_tr_summaries(read_file_text(path_join(out, "mapped.tr")));
        write_json_file(path_join(out, "physical_summary.json"), chk, 2);
        return chk;
    };

    try {
        result["returncode"] = run_worker_pass(clipped_timeout(static_cast<double>(timeout_sec)));
    } catch (const TimeoutError&) {
        result["status"] = "timeout";
        result["pnr_wall_seconds"] = perf_counter() - start;
        save();
        remove_file_if_exists(delay_cache_file);
        throw;
    }

    JsonValue checked = finalize_and_audit();
    double pass1_sec = perf_counter() - start;

    int64_t su_v0 = checked["violated_endpoints"].is_obj() ? checked["violated_endpoints"]["Setup"].as_i64() : 0;
    int64_t hd_v0 = checked["violated_endpoints"].is_obj() ? checked["violated_endpoints"]["Hold"].as_i64() : 0;
    int64_t rc_v0 = (checked["violated_endpoints"].is_obj() && checked["violated_endpoints"].contains("Recovery")) ? checked["violated_endpoints"]["Recovery"].as_i64() : 0;
    int64_t rm_v0 = (checked["violated_endpoints"].is_obj() && checked["violated_endpoints"].contains("Removal")) ? checked["violated_endpoints"]["Removal"].as_i64() : 0;
    if ((su_v0 > 0 || hd_v0 > 0 || rc_v0 > 0 || rm_v0 > 0) &&
        (static_cast<double>(timeout_sec) - pass1_sec) >= std::max(180.0, pass1_sec * 1.15)) {

        std::unordered_set<std::string> keep_inputs = {
            "mapped.vg", "project.sdc", "project.cst", "serdes.csr", "device.cfg", "cmd.do", "_best_pass_backup", delay_cache_name
        };
        std::string best_backup = path_join(out, "_best_pass_backup");
        make_dirs(best_backup, true);
        std::string last_tr = read_file_text(path_join(out, "mapped.tr"));
        PassScore best_score = score_pnr_summary(checked, last_tr);
        int best_pass_idx = 1;

        std::map<std::string, double> acc_cp, acc_ci, acc_hp, acc_routed, latest_routed;
        if (file_exists(path_join(out, "routed_delays_latest.json"))) {
            JsonValue rd_init = parse_json_file(path_join(out, "routed_delays_latest.json"));
            for (const auto& [k, v] : rd_init.as_obj()) {
                acc_routed[k] = v.as_f64();
                latest_routed[k] = v.as_f64();
            }
        }
        for (const auto& de : list_dir(out)) {
            if (!keep_inputs.count(de.name) && de.is_file) copy_file_overwrite(de.path, path_join(best_backup, de.name));
        }
        copy_file_overwrite(netlist, path_join(best_backup, "_best_mapped.vg"));

        for (int fb_pass : {2, 3}) {
            double rem_now = static_cast<double>(timeout_sec) - (perf_counter() - start);
            int64_t su_vb = checked["violated_endpoints"].is_obj() ? checked["violated_endpoints"]["Setup"].as_i64() : 0;
            int64_t hd_vb = checked["violated_endpoints"].is_obj() ? checked["violated_endpoints"]["Hold"].as_i64() : 0;
            int64_t rc_vb = (checked["violated_endpoints"].is_obj() && checked["violated_endpoints"].contains("Recovery")) ? checked["violated_endpoints"]["Recovery"].as_i64() : 0;
            int64_t rm_vb = (checked["violated_endpoints"].is_obj() && checked["violated_endpoints"].contains("Removal")) ? checked["violated_endpoints"]["Removal"].as_i64() : 0;
            if ((su_vb == 0 && hd_vb == 0 && rc_vb == 0 && rm_vb == 0) || rem_now < std::max(180.0, pass1_sec * 1.15)) break;

            auto nfb = extract_sta_timing_feedback(last_tr);
            for (const auto& [k, v] : nfb.crit_pins)  acc_cp[k] = std::round(std::max(acc_cp[k] * 0.97, v) * 10000.0) / 10000.0;
            for (const auto& [k, v] : nfb.crit_insts) acc_ci[k] = std::round(std::max(acc_ci[k] * 0.97, v) * 10000.0) / 10000.0;
            for (const auto& [k, v] : nfb.hold_pins)  acc_hp[k] = std::round(std::max(acc_hp[k], v) * 1000.0) / 1000.0;
            if (acc_cp.empty() && acc_ci.empty() && acc_hp.empty() && acc_routed.empty()) break;

            if (physical_profile_path.empty() && su_vb > 0 && !latest_routed.empty() && !nfb.crit_pins.empty()) {
                int64_t n_perm = permute_vg_luts_from_routed_delays(netlist, latest_routed, nfb.crit_pins, acc_routed, acc_cp, acc_hp);
                if (n_perm > 0) {
                    result["netlist_sha256_before"] = sha256_file(netlist);
                    result["pass" + std::to_string(fb_pass) + "_routed_lut_pin_permutations"] = n_perm;
                }
            }

            for (const auto& de : list_dir(out)) {
                if (!keep_inputs.count(de.name)) {
                    if (de.is_dir) remove_all(de.path);
                    else remove_file_if_exists(de.path);
                }
            }
            if (!acc_routed.empty()) {
                JsonValue rj = JsonValue::object();
                for (const auto& [k, v] : acc_routed) rj[k] = v;
                write_file_text(path_join(out, "routed_delays_pass1.json"), dump_json(rj, -1, false));
            }
            if (!acc_cp.empty() || !acc_ci.empty() || !acc_hp.empty()) {
                JsonValue cp_j = JsonValue::object(), ci_j = JsonValue::object(), hp_j = JsonValue::object();
                for (const auto& [k, v] : acc_cp) cp_j[k] = v;
                for (const auto& [k, v] : acc_ci) ci_j[k] = v;
                for (const auto& [k, v] : acc_hp) hp_j[k] = v;
                JsonValue fbj = JsonValue::object();
                fbj["crit_pins"] = std::move(cp_j);
                fbj["crit_insts"] = std::move(ci_j);
                fbj["hold_pins"] = std::move(hp_j);
                write_json_file(path_join(out, "timing_feedback.json"), fbj, 2);
            }

            bool p_ok = false;
            JsonValue chk_cand;
            std::string tr_cand;
            try {
                result["status"] = "pnr_running_pass" + std::to_string(fb_pass);
                save();
                double rem_exec = clipped_timeout(static_cast<double>(timeout_sec) - (perf_counter() - start));
                result["returncode"] = run_worker_pass(rem_exec);
                chk_cand = finalize_and_audit();
                tr_cand = read_file_text(path_join(out, "mapped.tr"));
                p_ok = true;
            } catch (const std::exception& ex) {
                p_ok = false;
                result["pass" + std::to_string(fb_pass) + "_error"] = ex.what();
            }

            if (p_ok && file_exists(path_join(out, "routed_delays_latest.json"))) {
                JsonValue rd_lat = parse_json_file(path_join(out, "routed_delays_latest.json"));
                latest_routed.clear();
                for (const auto& [kd, vd] : rd_lat.as_obj()) {
                    latest_routed[kd] = vd.as_f64();
                    acc_routed[kd] = std::round(std::max(acc_routed[kd] * 0.88, vd.as_f64()) * 10000.0) / 10000.0;
                }
            }

            if (p_ok && score_pnr_summary(chk_cand, tr_cand) > best_score) {
                best_score = score_pnr_summary(chk_cand, tr_cand);
                best_pass_idx = fb_pass;
                checked = chk_cand;
                last_tr = tr_cand;
                remove_all(best_backup);
                make_dirs(best_backup, true);
                for (const auto& de : list_dir(out)) {
                    if (!keep_inputs.count(de.name) && de.is_file) copy_file_overwrite(de.path, path_join(best_backup, de.name));
                }
                copy_file_overwrite(netlist, path_join(best_backup, "_best_mapped.vg"));
            } else {
                for (const auto& de : list_dir(out)) {
                    if (!keep_inputs.count(de.name)) {
                        if (de.is_dir) remove_all(de.path);
                        else remove_file_if_exists(de.path);
                    }
                }
                for (const auto& bp : list_dir(best_backup)) {
                    if (bp.is_file && bp.name != "_best_mapped.vg") copy_file_overwrite(bp.path, path_join(out, bp.name));
                }
                if (!tr_cand.empty()) last_tr = tr_cand;
            }
        }
        if (file_exists(path_join(best_backup, "_best_mapped.vg"))) {
            copy_file_overwrite(path_join(best_backup, "_best_mapped.vg"), netlist);
        }
        if (physical_profile_path.empty()) {
            result["netlist_sha256_before"] = sha256_file(netlist);
            if (best_pass_idx >= 2 && file_exists(mapped_vg) && resolve_path(mapped_vg) != resolve_path(netlist)) {
                copy_file_overwrite(netlist, std::string(mapped_vg));
            }
        }
        remove_all(best_backup);
        result["returncode"] = 0;
        result["selected_pass"] = best_pass_idx;
        result["pass2_selected"] = (best_pass_idx >= 2);
        checked = finalize_and_audit();
    }

    for (const char* tmp_f : {"routed_delays_pass1.json", "routed_delays_latest.json", "timing_feedback.json"}) {
        remove_file_if_exists(path_join(out, tmp_f));
    }
    remove_file_if_exists(delay_cache_file);
    return {std::move(result), std::move(checked)};
}

} // namespace ng
