#include "ng_pipeline.hpp"

#include <cmath>
#include <deque>
#include <map>
#include <set>
#include <thread>
#include <unordered_map>
#include <unordered_set>

namespace ng {

// ============================================================================
// extract_module_boolean_cut & readback_acd_module_blif (map_resource_json.py)
// ============================================================================
namespace {

std::string sigbit_to_cut_net(SigBit b) {
    if (b >= 0) return "b" + std::to_string(b);
    if (b == SIG_0) return "c0";
    if (b == SIG_1) return "c1";
    throw std::runtime_error("Undefined Boolean input requires an explicit semantic policy");
}

} // namespace

std::optional<ExtractedCut> extract_module_boolean_cut(const RtlilModule& module) {
    OrderedMap<bool> gates;
    for (const auto& [name, cell] : module.cells) {
        if (find_bool_gate(cell.type) != nullptr) {
            gates.set(name, true);
        }
    }
    if (gates.empty()) return std::nullopt;

    std::unordered_set<SigBit> outside;
    for (const auto& [_, p] : module.ports) {
        for (SigBit b : p.bits) outside.insert(b);
    }
    for (const auto& [name, cell] : module.cells) {
        if (!gates.contains(name)) {
            for (const auto& [__, bs] : cell.connections) {
                for (SigBit b : bs) outside.insert(b);
            }
        }
    }
    for (const auto& [_, row] : module.netnames) {
        if (row.attributes.contains("init") || is_attr_truthy(row.attributes, "keep")) {
            for (SigBit b : row.bits) outside.insert(b);
        }
    }

    std::vector<BlifNode> nodes;
    nodes.reserve(gates.size() + 2);
    nodes.push_back(BlifNode{{}, "c0", 0ULL, 0, false});
    nodes.push_back(BlifNode{{}, "c1", 1ULL, 0, false});

    std::unordered_set<std::string> driven;
    std::unordered_set<std::string> used;

    for (const auto& [name, _] : gates) {
        const RtlilCell& cell = module.cells.at(name);
        const BoolGateDef* gd = find_bool_gate(cell.type);
        if (static_cast<int>(cell.connections.size()) != gd->arity + 1 || !cell.connections.contains("Y")) {
            throw std::runtime_error("Unexpected Boolean cell ports: " + name);
        }
        std::vector<std::string> ins;
        ins.reserve(static_cast<size_t>(gd->arity));
        for (int i = 0; i < gd->arity; ++i) {
            std::string pk(1, gd->pins[i]);
            const auto* bs = cell.connections.find_ptr(pk);
            if (!bs || bs->size() != 1) throw std::runtime_error("Expected bit-level Boolean cells: " + name);
            std::string in_s = sigbit_to_cut_net((*bs)[0]);
            used.insert(in_s);
            ins.push_back(std::move(in_s));
        }
        const auto& ybs = cell.connections.at("Y");
        if (ybs.size() != 1) throw std::runtime_error("Expected bit-level Boolean output: " + name);
        std::string out_s = sigbit_to_cut_net(ybs[0]);
        if (driven.count(out_s) || out_s == "c0" || out_s == "c1") {
            throw std::runtime_error("Invalid/multiply-driven Boolean gate output: " + name);
        }
        driven.insert(out_s);
        nodes.push_back(BlifNode{std::move(ins), std::move(out_s), gd->truth, 0, false});
    }

    std::vector<std::string> inputs;
    for (const auto& s : used) {
        if (!driven.count(s) && s != "c0" && s != "c1") inputs.push_back(s);
    }
    std::sort(inputs.begin(), inputs.end());

    std::unordered_set<std::string> out_cand;
    for (SigBit b : outside) {
        if (b == SIG_X || b == SIG_Z) continue;
        std::string ns = sigbit_to_cut_net(b);
        if (driven.count(ns)) out_cand.insert(std::move(ns));
    }
    std::vector<std::string> outputs(out_cand.begin(), out_cand.end());
    std::sort(outputs.begin(), outputs.end());
    if (outputs.empty()) {
        throw std::runtime_error("Nonempty Boolean module without observable boundaries");
    }

    ExtractedCut cut;
    cut.gates = std::move(gates);
    cut.inputs = inputs;
    cut.outputs = outputs;
    cut.blif_net.inputs = std::move(inputs);
    cut.blif_net.outputs = std::move(outputs);
    cut.blif_net.nodes = std::move(nodes);
    return cut;
}

std::string readback_acd_module_blif(const RtlilModule& module, const ExtractedCut& cut) {
    BlifNetwork rb;
    rb.inputs = cut.inputs;
    rb.outputs = cut.outputs;
    rb.nodes.push_back(BlifNode{{}, "c0", 0ULL, 0, false});
    rb.nodes.push_back(BlifNode{{}, "c1", 1ULL, 0, false});

    auto sig = [](SigBit b) -> std::string {
        if (b == SIG_0) return "c0";
        if (b == SIG_1) return "c1";
        return "b" + std::to_string(b);
    };

    for (const auto& [name, cell] : module.cells) {
        if (!starts_with(name, "ng_acd_")) continue;
        if (cell.type == "VCC" || cell.type == "GND") {
            std::string pin = (cell.type == "VCC") ? "V" : "G";
            std::string out = sig(cell.connections.at(pin)[0]);
            if (out == "c0" || out == "c1") continue;
            rb.nodes.push_back(BlifNode{{}, out, cell.type == "VCC" ? 1ULL : 0ULL, 0, false});
        } else if (cell.type == "MUX2_LUT5" || cell.type == "MUX2_LUT6") {
            if (!cell.parameters.empty() || cell.connections.size() != 4) {
                throw std::runtime_error("Malformed architecture mux");
            }
            rb.nodes.push_back(BlifNode{
                {sig(cell.connections.at("I0")[0]), sig(cell.connections.at("I1")[0]), sig(cell.connections.at("S0")[0])},
                sig(cell.connections.at("O")[0]),
                0xCAULL, 0, false
            });
        } else if (cell.type == "LUT1" || cell.type == "LUT2" || cell.type == "LUT3" || cell.type == "LUT4") {
            int k = cell.type.back() - '0';
            uint64_t truth = parse_bin_u64(cell.parameters.at("INIT"));
            std::vector<std::string> ins;
            for (int i = 0; i < k; ++i) ins.push_back(sig(cell.connections.at("I" + std::to_string(i))[0]));
            rb.nodes.push_back(BlifNode{std::move(ins), sig(cell.connections.at("F")[0]), truth, 0, false});
        } else {
            throw std::runtime_error("Unknown generated primitive: " + cell.type);
        }
    }
    return format_blif_text(rb, "readback", true);
}

// ============================================================================
// gw5a_abc9_models::generate
// ============================================================================
namespace {

std::string generate_abc9_timing_models(std::string_view out_dir) {
    std::string out = resolve_path(out_dir);
    make_dirs(out, false);

    std::string source = path_join(runtime_paths().res_root, "share\\yosys\\gowin\\cells_sim.v");
    if (!file_exists(source)) {
        source = path_join(runtime_paths().env_dir, "share\\yosys\\gowin\\cells_sim.v");
    }
    std::string text = read_file_text(source);

    std::string ff_model_p = path_join(runtime_paths().res_root, "results\\timing\\gowin138_model_ff_checked.json");
    std::string lut_model_p = path_join(runtime_paths().res_root, "results\\timing\\gowin138_model.json");
    std::string cost_p = path_join(runtime_paths().res_root, "configs\\gw5a_arch_cost_v2.json");

    JsonValue ff_model_j = parse_json_file(ff_model_p);
    JsonValue lut_model_j = parse_json_file(lut_model_p);
    int64_t data_setup = ff_model_j["ff_data_constraints"]["setup"]["envelope_ps"].as_i64();
    std::vector<int> lut_pins_ps;
    for (const auto& p : lut_model_j["physical_lut_inputs"].as_arr()) {
        lut_pins_ps.push_back(static_cast<int>(std::llround(p["setup_max_ns"].as_f64() * 1000.0)));
    }
    if (lut_pins_ps.size() != 4) throw std::runtime_error("Expected 4 calibrated LUT input pin delays");

    static const std::unordered_set<std::string> targets = {
        "LUT1","LUT2","LUT3","LUT4","__APICULA_LUT5","__APICULA_LUT6","DFFRE","ALU"
    };
    struct Edit { size_t a; size_t b; std::string repl; };
    std::vector<Edit> edits;
    JsonValue changed = JsonValue::array();

    for (const auto& u : verilog_units(text)) {
        if (!targets.count(u.name)) continue;
        std::string body = text.substr(u.start, u.end - u.start);
        // Find `\bspecify\b ... \bendspecify\b`
        size_t sp_s = std::string::npos, sp_e = std::string::npos;
        for (size_t p = 0; (p = body.find("specify", p)) != std::string::npos; p += 7) {
            bool lok = (p == 0) || !(std::isalnum(static_cast<unsigned char>(body[p - 1])) || body[p - 1] == '_');
            bool rok = (p + 7 == body.size()) || !(std::isalnum(static_cast<unsigned char>(body[p + 7])) || body[p + 7] == '_');
            if (lok && rok) { sp_s = p; break; }
        }
        if (sp_s != std::string::npos) {
            for (size_t p = sp_s + 7; (p = body.find("endspecify", p)) != std::string::npos; p += 10) {
                bool lok = !(std::isalnum(static_cast<unsigned char>(body[p - 1])) || body[p - 1] == '_');
                bool rok = (p + 10 == body.size()) || !(std::isalnum(static_cast<unsigned char>(body[p + 10])) || body[p + 10] == '_');
                if (lok && rok) { sp_e = p + 10; break; }
            }
        }
        if (sp_s == std::string::npos || sp_e == std::string::npos) {
            throw std::runtime_error("Expected upstream timing block missing in " + u.name);
        }

        std::string spec;
        if (u.name == "DFFRE") {
            spec = "specify\nif (CE) (posedge CLK => (Q : D)) = (344,344);\n$setup(D, posedge CLK &&& CE, "
                 + std::to_string(data_setup) + ");\n$setup(CE, posedge CLK, 335);\n$setup(RESET, posedge CLK, 335);\nendspecify";
        } else if (u.name == "ALU") {
            spec = "specify\n(I0 => SUM) = (520,520);\n(I1 => SUM) = (510,510);\n(I3 => SUM) = (460,460);\n(CIN => SUM) = (260,260);\n"
                   "(I0 => COUT) = (450,450);\n(I1 => COUT) = (450,450);\n(I3 => COUT) = (320,320);\n(CIN => COUT) = (40,40);\nendspecify";
        } else {
            int k = u.name.back() - '0';
            std::vector<int> delays;
            std::vector<std::string> pins;
            if (k <= 4) {
                for (int i = 0; i < k; ++i) { delays.push_back(lut_pins_ps[static_cast<size_t>(i)]); pins.push_back("I" + std::to_string(i)); }
            } else if (k == 5) {
                for (int p : lut_pins_ps) delays.push_back(p + 145);
                delays.push_back(227);
                pins = {"I0","I1","I2","I3","M0"};
            } else {
                for (int p : lut_pins_ps) delays.push_back(p + 290);
                delays.push_back(365); delays.push_back(227);
                pins = {"I0","I1","I2","I3","M0","M1"};
            }
            std::vector<std::string> slines;
            for (size_t i = 0; i < pins.size(); ++i) {
                slines.push_back("(" + pins[i] + " => F) = (" + std::to_string(delays[i]) + "," + std::to_string(delays[i]) + ");");
            }
            spec = "specify\n" + join_strings(slines, "\n") + "\nendspecify";
        }
        std::string repl = body.substr(0, sp_s) + spec + body.substr(sp_e);
        edits.push_back(Edit{u.start, u.end, std::move(repl)});
        changed.push_back(u.name);
    }

    for (auto it = edits.rbegin(); it != edits.rend(); ++it) {
        text = text.substr(0, it->a) + it->repl + text.substr(it->b);
    }
    std::string out_sim = path_join(out, "cells_sim.v");
    write_file_text(out_sim, text);

    static const std::unordered_set<std::string> compatible = {
        "LUT1","LUT2","LUT3","LUT4","__APICULA_LUT5","__APICULA_LUT6","__APICULA_LUT7","__APICULA_LUT8",
        "DFF","DFFE","DFFR","DFFS","DFFRE","DFFSE",
        "ALU","MUX2","MUX2_LUT5","MUX2_LUT6","MUX2_LUT7","MUX2_LUT8"
    };
    std::vector<std::string> parts;
    std::vector<std::string> sel_names;
    for (const auto& u : verilog_units(text)) {
        if (!compatible.count(u.name)) continue;
        sel_names.push_back(u.name);
        size_t start = u.start;
        size_t p0 = (u.start > 150) ? (u.start - 150) : 0;
        std::string_view prefix(text.data() + p0, u.start - p0);
        size_t attr_open = prefix.rfind("(*");
        if (attr_open != std::string_view::npos) {
            size_t attr_close = prefix.find("*)", attr_open + 2);
            if (attr_close != std::string_view::npos && trim(prefix.substr(attr_close + 2)).empty()) {
                start = p0 + attr_open;
            }
        }
        parts.push_back(text.substr(start, u.end - start));
    }
    std::sort(sel_names.begin(), sel_names.end());
    std::string selected_v = path_join(out, "abc9_cells.v");
    write_file_text(selected_v, join_strings(parts, "\n"));

    JsonValue sm_arr = JsonValue::array();
    for (const auto& s : sel_names) sm_arr.push_back(s);

    JsonValue rec = JsonValue::object();
    rec["source_sha256"] = sha256_file(source);
    rec["output_sha256"] = sha256_file(out_sim);
    rec["changed_timing_only"] = std::move(changed);
    rec["selected_model_sha256"] = sha256_file(selected_v);
    rec["selected_modules"] = std::move(sm_arr);
    rec["source_ff_model_sha256"] = sha256_file(ff_model_p);
    rec["source_cost_sha256"] = sha256_file(cost_p);
    rec["source_lut_model_sha256"] = sha256_file(lut_model_p);
    rec["scope"] = "Calibrated per-pin GW5A LUT1-4 setup delays [521, 516, 457, 287] ps, dedicated MUX2_LUT5/6 cascade costs, and DFFRE costs. Native signoff required.";
    write_json_pretty(path_join(out, "manifest.json"), rec);
    return selected_v;
}

// ============================================================================
// gw5a_aig_rewrite::rewrite (Parallel multi-worker AIG rewrite + O(1) gate LUT)
// ============================================================================
struct AigGateChoice {
    const char* kind = nullptr;
    const char* pins = nullptr;
    int perm[2] = {0, 1};
};

const AigGateChoice& lookup_aig_gate(int arity, uint64_t truth) {
    static std::unordered_map<int, AigGateChoice> table;
    static std::once_flag flag;
    std::call_once(flag, []() {
        for (const auto& gd : bool_gate_defs()) {
            if (gd.arity == 1) {
                int key = (1 << 8) | static_cast<int>(gd.truth & 0xFF);
                if (!table.count(key)) {
                    table[key] = AigGateChoice{gd.cell_type, gd.pins, {0, 0}};
                }
            } else if (gd.arity == 2) {
                const int perms[2][2] = {{0, 1}, {1, 0}};
                for (const auto& pm : perms) {
                    int actual = 0;
                    for (int a = 0; a < 4; ++a) {
                        int b0 = (a >> pm[0]) & 1;
                        int b1 = (a >> pm[1]) & 1;
                        int orig_a = b0 | (b1 << 1);
                        actual |= static_cast<int>((gd.truth >> orig_a) & 1ULL) << a;
                    }
                    int key = (2 << 8) | actual;
                    if (!table.count(key)) {
                        table[key] = AigGateChoice{gd.cell_type, gd.pins, {pm[0], pm[1]}};
                    }
                }
            }
        }
    });
    int key = (arity << 8) | static_cast<int>(truth & 0xFF);
    auto it = table.find(key);
    if (it == table.end()) throw std::runtime_error("AIG reconstruction encountered an unsupported truth function");
    return it->second;
}

std::pair<RtlilDesign, JsonValue> rewrite_aig_modules(const RtlilDesign& lowered, std::string_view out_dir) {
    std::string out = resolve_path(out_dir);
    make_dirs(out, false);

    struct Task {
        size_t idx;
        std::string mn;
        ExtractedCut cut;
    };
    std::vector<Task> tasks;
    std::vector<std::string> mut_mods;
    for (const auto& [mn, mptr] : lowered.modules) {
        if (is_module_blackbox(*mptr)) continue;
        auto cut = extract_module_boolean_cut(*mptr);
        if (!cut.has_value()) continue;
        mut_mods.push_back(mn);
        tasks.push_back(Task{tasks.size(), mn, std::move(*cut)});
    }

    RtlilDesign design = lowered.clone_cow(&mut_mods);
    if (tasks.empty()) {
        return {std::move(design), JsonValue::array()};
    }

    struct TaskResult {
        OrderedMap<RtlilCell> new_cells;
        JsonValue proof;
        std::string err;
    };
    std::vector<TaskResult> results(tasks.size());

    size_t max_w = std::min<size_t>(tasks.size(), std::min<size_t>(8, std::max<size_t>(1, hardware_threads() / 2)));
    std::atomic<size_t> next_task{0};
    double eff_timeout = clipped_timeout(300.0);

    std::vector<std::thread> pool;
    pool.reserve(max_w);
    for (size_t w = 0; w < max_w; ++w) {
        pool.emplace_back([&]() {
            for (;;) {
                size_t ti = next_task.fetch_add(1);
                if (ti >= tasks.size()) break;
                const Task& t = tasks[ti];
                try {
                    std::string pdir = path_join(out, "module_" + std::to_string(t.idx));
                    make_dirs(pdir, false);
                    std::string src_blif = path_join(pdir, "source.blif");
                    std::string tgt_blif = path_join(pdir, "rewritten.blif");
                    write_blif_fast(src_blif, t.cut.blif_net, false);

                    std::string phase = "&get -n; &syn2 -d -R 0; &if -g -K 6; &syn2 -d -R 0; &b -d; &put";
                    std::string cmd = "read \"" + posix_path(src_blif) + "\"; strash; " + phase + "; " + phase
                                    + "; strash; write_blif \"" + posix_path(tgt_blif) + "\"; echo NEXTGOWIN_DONE";
                    ProcResult pr = run_process_to_log({runtime_paths().abc_exe, "-c", cmd}, pdir, path_join(pdir, "rewrite.log"), {}, eff_timeout);
                    if (pr.timed_out || pr.returncode != 0 || pr.output.find("NEXTGOWIN_DONE") == std::string::npos) {
                        throw std::runtime_error("AIG rewriting failed on " + t.mn);
                    }

                    std::string ref_sha = sha256_file(src_blif);
                    std::string cand_sha = sha256_file(tgt_blif);
                    std::string cec_cmd = "cec -n \"" + posix_path(src_blif) + "\" \"" + posix_path(tgt_blif) + "\"; echo NEXTGOWIN_DONE";
                    ProcResult cpr = run_process_to_log({runtime_paths().abc_exe, "-c", cec_cmd}, pdir, path_join(pdir, "cec.log"), {}, eff_timeout);
                    bool eq = (!cpr.timed_out && cpr.returncode == 0 && cpr.output.find("Networks are equivalent") != std::string::npos);
                    if (!eq) throw std::runtime_error("Rewritten AIG boundary contract failed on " + t.mn);

                    JsonValue proof = JsonValue::object();
                    proof["status"] = "ok";
                    proof["returncode"] = 0;
                    proof["wall_seconds"] = cpr.wall_seconds;
                    proof["command"] = cec_cmd;
                    proof["log"] = path_join(pdir, "cec.log");
                    proof["reference_sha256"] = ref_sha;
                    proof["candidate_sha256"] = cand_sha;
                    proof["inputs_unchanged"] = true;
                    proof["equivalent"] = true;

                    BlifNetwork n = read_blif_file(tgt_blif, 2);
                    if (n.inputs.size() != t.cut.inputs.size() || n.outputs.size() != t.cut.outputs.size()) {
                        throw std::runtime_error("AIG interface changed on " + t.mn);
                    }

                    const RtlilModule& m = lowered.mod_ref(t.mn);
                    std::unordered_map<std::string, SigBit> bits;
                    for (size_t i = 0; i < n.inputs.size(); ++i) bits[n.inputs[i]] = std::stoi(t.cut.inputs[i].substr(1));
                    for (size_t i = 0; i < n.outputs.size(); ++i) bits[n.outputs[i]] = std::stoi(t.cut.outputs[i].substr(1));
                    int32_t maximum = max_module_bit(m);
                    auto get_bit = [&](const std::string& nm) -> SigBit {
                        auto it = bits.find(nm);
                        if (it != bits.end()) return it->second;
                        SigBit nb = ++maximum;
                        bits.emplace(nm, nb);
                        return nb;
                    };

                    OrderedMap<RtlilCell> cells;
                    for (const auto& [cn, cell] : m.cells) {
                        if (!t.cut.gates.contains(cn)) cells.set(cn, cell);
                    }
                    for (size_t i = 0; i < n.nodes.size(); ++i) {
                        auto [rins, rtruth] = reduce_truth_function(n.nodes[i].inputs, n.nodes[i].truth);
                        std::string gname = "ng_preabc9_" + std::to_string(i);
                        if (cells.contains(gname)) throw std::runtime_error("AIG gate namespace collision");
                        RtlilCell gc;
                        if (rins.empty()) {
                            gc.type = "$_BUF_";
                            gc.connections.set("A", {rtruth ? SIG_1 : SIG_0});
                            gc.port_directions.set("A", "input");
                        } else {
                            const auto& ch = lookup_aig_gate(static_cast<int>(rins.size()), rtruth);
                            gc.type = ch.kind;
                            for (size_t j = 0; j < rins.size(); ++j) {
                                std::string pk(1, ch.pins[j]);
                                gc.connections.set(pk, {get_bit(rins[static_cast<size_t>(ch.perm[j])])});
                                gc.port_directions.set(pk, "input");
                            }
                        }
                        gc.connections.set("Y", {get_bit(n.nodes[i].output)});
                        gc.port_directions.set("Y", "output");
                        cells.set(gname, std::move(gc));
                    }

                    JsonValue p_entry = JsonValue::object();
                    p_entry["module"] = t.mn;
                    p_entry["proof"] = std::move(proof);
                    results[ti].new_cells = std::move(cells);
                    results[ti].proof = std::move(p_entry);
                } catch (const std::exception& ex) {
                    results[ti].err = ex.what();
                }
            }
        });
    }
    for (auto& th : pool) th.join();

    JsonValue proofs = JsonValue::array();
    for (size_t i = 0; i < tasks.size(); ++i) {
        if (!results[i].err.empty()) throw std::runtime_error(results[i].err);
        RtlilModule& dm = design.mod_mut(tasks[i].mn);
        dm.cells = std::move(results[i].new_cells);
        std::vector<std::string> drop_json_auto;
        for (const auto& [nn, nr] : dm.netnames) {
            if (nr.hide_name != 0 && nr.attributes.empty() && starts_with(nn, "$auto$jsonparse.cc:")) {
                drop_json_auto.push_back(nn);
            }
        }
        for (const auto& nn : drop_json_auto) dm.netnames.erase(nn);
        proofs.push_back(std::move(results[i].proof));
    }
    return {std::move(design), std::move(proofs)};
}

// ============================================================================
// gw5a_box_mapper helpers: native_interfaces, legalize_dedicated_mux_trees, permute_lut_pins
// ============================================================================
std::vector<std::string> write_native_interfaces(std::string_view out_path, const std::unordered_set<std::string>& kinds) {
    std::string lib_p = path_join(runtime_paths().gowin_sdk, "simlib\\gw5a\\prim_sim.v");
    (void)verify_or_record_sdk_file_sha(lib_p, EXPECTED_PRIM_SIM_SHA, "Native functional library (prim_sim.v)");
    std::string text = read_file_text(lib_p);
    std::vector<std::string> rows;
    std::vector<std::string> found;
    for (const auto& u : verilog_units(text)) {
        if (!kinds.count(u.name)) continue;
        std::string body = masked_verilog(std::string_view(text).substr(u.start, u.end - u.start));
        size_t semi = body.find(';');
        std::vector<std::string> decls;
        size_t p = 0;
        while (p < body.size()) {
            size_t ls = p;
            size_t k = ls;
            while (k < body.size() && (body[k] == ' ' || body[k] == '\t')) ++k;
            bool match_kw = false;
            for (const char* kw : {"parameter", "localparam", "input", "output", "inout"}) {
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
        rows.push_back("(* blackbox=1 *) " + body.substr(0, semi + 1) + "\n" + join_strings(decls, "\n") + "\nendmodule\n");
        found.push_back(u.name);
    }
    if (found.size() < kinds.size()) {
        std::unordered_set<std::string> found_set(found.begin(), found.end());
        std::string ysim = path_join(runtime_paths().res_root, "share\\yosys\\gowin\\cells_sim.v");
        if (!file_exists(ysim)) ysim = path_join(runtime_paths().env_dir, "share\\yosys\\gowin\\cells_sim.v");
        if (file_exists(ysim)) {
            std::string ytext = read_file_text(ysim);
            for (const auto& u : verilog_units(ytext)) {
                if (!kinds.count(u.name) || found_set.count(u.name)) continue;
                std::string body = masked_verilog(std::string_view(ytext).substr(u.start, u.end - u.start));
                size_t semi = body.find(';');
                if (semi == std::string::npos) continue;
                std::vector<std::string> decls;
                size_t p = 0;
                while (p < body.size()) {
                    size_t ls = p;
                    size_t k = ls;
                    while (k < body.size() && (body[k] == ' ' || body[k] == '\t')) ++k;
                    bool match_kw = false;
                    for (const char* kw : {"parameter", "localparam", "input", "output", "inout"}) {
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
                rows.push_back("(* blackbox=1 *) " + body.substr(0, semi + 1) + "\n" + join_strings(decls, "\n") + "\nendmodule\n");
                found.push_back(u.name);
                found_set.insert(u.name);
            }
        }
    }
    write_file_text(out_path, join_strings(rows, "\n"));
    std::sort(found.begin(), found.end());
    return found;
}

JsonValue legalize_dedicated_mux_trees(RtlilDesign& mapped, const std::vector<std::string>& active) {
    int64_t mux6_total = 0, mux5_total = 0, mux5_cloned = 0, lut_cloned = 0, dead_pruned = 0;
    auto is_lut = [](const std::string& t) { return t == "LUT1" || t == "LUT2" || t == "LUT3" || t == "LUT4"; };
    auto is_comb = [&](const std::string& t) { return is_lut(t) || t == "MUX2_LUT5" || t == "MUX2_LUT6"; };

    for (const auto& mname : active) {
        RtlilModule& m = mapped.mod_mut(mname);
        if (m.cells.empty()) continue;

        std::unordered_set<SigBit> ext_bits;
        for (const auto& [_, pr] : m.ports) {
            for (SigBit b : pr.bits) if (b >= 0) ext_bits.insert(b);
        }
        for (const auto& [_, nr] : m.netnames) {
            if (is_attr_truthy(nr.attributes, "keep")) {
                for (SigBit b : nr.bits) if (b >= 0) ext_bits.insert(b);
            }
        }
        int32_t max_bit = max_module_bit(m);
        int uid_counter = 0;

        auto alloc_bit = [&]() -> SigBit { return ++max_bit; };
        auto alloc_cell = [&](const char* pfx) -> std::string {
            for (;;) {
                std::string cand = std::string(pfx) + "_" + std::to_string(++uid_counter);
                if (!m.cells.contains(cand)) return cand;
            }
        };

        struct Usage {
            std::unordered_map<SigBit, std::string> drivers;
            std::unordered_map<SigBit, int> loads;
            std::unordered_map<SigBit, int> const_bits;
        };
        auto build_usage = [&]() -> Usage {
            Usage u;
            for (const auto& [cname, c] : m.cells) {
                if (c.type == "GND" || c.type == "VCC") {
                    std::string pin = (c.type == "GND") ? "G" : "V";
                    if (const auto* bs = c.connections.find_ptr(pin)) {
                        for (SigBit b : *bs) if (b >= 0) u.const_bits[b] = (c.type == "GND") ? 0 : 1;
                    }
                }
                for (const auto& [p, bs] : c.connections) {
                    std::string pd = c.port_directions.value_or(p, "");
                    for (SigBit b : bs) {
                        if (b < 0) continue;
                        if (pd == "output") u.drivers[b] = cname;
                        else if (pd == "input" || pd == "inout") u.loads[b] += 1;
                    }
                }
            }
            return u;
        };

        auto prune_dead_comb = [&]() -> int64_t {
            int64_t pruned = 0;
            for (;;) {
                auto u = build_usage();
                std::vector<std::string> dead;
                for (const auto& [cname, c] : m.cells) {
                    if (!is_comb(c.type)) continue;
                    std::string opin = starts_with(c.type, "MUX2_") ? "O" : "F";
                    const auto* obs = c.connections.find_ptr(opin);
                    if (obs && obs->size() == 1 && (*obs)[0] >= 0 && !ext_bits.count((*obs)[0]) && u.loads[(*obs)[0]] == 0) {
                        dead.push_back(cname);
                    }
                }
                if (dead.empty()) break;
                for (const auto& dn : dead) { m.cells.erase(dn); ++pruned; }
            }
            return pruned;
        };

        dead_pruned += prune_dead_comb();

        // Step A: MUX2_LUT6 -> 2 private MUX2_LUT5
        {
            auto u = build_usage();
            std::vector<std::string> mux6_names;
            for (const auto& [cn, c] : m.cells) if (c.type == "MUX2_LUT6") mux6_names.push_back(cn);
            for (const auto& cname : mux6_names) {
                ++mux6_total;
                for (const char* pin : {"I0", "I1"}) {
                    SigBit b = m.cells.at(cname).connections.at(pin)[0];
                    auto dit = (b >= 0) ? u.drivers.find(b) : u.drivers.end();
                    if (b >= 0 && !ext_bits.count(b) && u.loads[b] == 1 && dit != u.drivers.end() && m.cells.at(dit->second).type == "MUX2_LUT5") {
                        m.cells.at(dit->second).attributes.set("keep", "1");
                        continue;
                    }
                    SigBit fb = alloc_bit();
                    if (b >= 0) u.loads[b] -= 1;
                    m.cells.at(cname).connections.set(pin, {fb});
                    u.loads[fb] = 1;

                    RtlilCell new_c;
                    if (b >= 0 && dit != u.drivers.end() && m.cells.at(dit->second).type == "MUX2_LUT5") {
                        new_c = m.cells.at(dit->second);
                        new_c.connections.set("O", {fb});
                        new_c.attributes.set("keep", "1");
                        new_c.attributes.set("ng_generated", "1");
                        for (const char* ip : {"I0", "I1", "S0"}) {
                            SigBit ib = new_c.connections.at(ip)[0];
                            if (ib >= 0) u.loads[ib] += 1;
                        }
                    } else {
                        SigBit src_b = (b == SIG_X || b == SIG_Z) ? SIG_0 : b;
                        new_c.hide_name = 0;
                        new_c.type = "MUX2_LUT5";
                        new_c.attributes.set("keep", "1");
                        new_c.attributes.set("ng_generated", "1");
                        new_c.port_directions.set("I0", "input");
                        new_c.port_directions.set("I1", "input");
                        new_c.port_directions.set("S0", "input");
                        new_c.port_directions.set("O", "output");
                        new_c.connections.set("I0", {src_b});
                        new_c.connections.set("I1", {SIG_0});
                        new_c.connections.set("S0", {SIG_0});
                        new_c.connections.set("O", {fb});
                        if (src_b >= 0) u.loads[src_b] += 1;
                    }
                    std::string new_name = alloc_cell("ng_mux5_leg");
                    m.cells.set(new_name, std::move(new_c));
                    u.drivers[fb] = new_name;
                    ++mux5_cloned;
                }
            }
        }

        // Step B: MUX2_LUT5 -> 2 private LUT1..4 leaves
        {
            auto u = build_usage();
            std::vector<std::string> mux5_names;
            for (const auto& [cn, c] : m.cells) if (c.type == "MUX2_LUT5") mux5_names.push_back(cn);
            for (const auto& cname : mux5_names) {
                ++mux5_total;
                for (const char* pin : {"I0", "I1"}) {
                    SigBit b = m.cells.at(cname).connections.at(pin)[0];
                    auto dit = (b >= 0) ? u.drivers.find(b) : u.drivers.end();
                    if (b >= 0 && !ext_bits.count(b) && u.loads[b] == 1 && dit != u.drivers.end() && is_lut(m.cells.at(dit->second).type)) {
                        m.cells.at(dit->second).attributes.set("keep", "1");
                        continue;
                    }
                    SigBit fb = alloc_bit();
                    if (b >= 0) u.loads[b] -= 1;
                    m.cells.at(cname).connections.set(pin, {fb});
                    u.loads[fb] = 1;

                    RtlilCell new_c;
                    if (b < 0 || u.const_bits.count(b)) {
                        int val = (b < 0) ? ((b == SIG_1) ? 1 : 0) : u.const_bits[b];
                        new_c.hide_name = 0;
                        new_c.type = "LUT1";
                        new_c.attributes.set("keep", "1");
                        new_c.attributes.set("ng_generated", "1");
                        new_c.parameters.set("INIT", val ? "11" : "00");
                        new_c.port_directions.set("I0", "input");
                        new_c.port_directions.set("F", "output");
                        new_c.connections.set("I0", {SIG_0});
                        new_c.connections.set("F", {fb});
                    } else if (dit != u.drivers.end() && is_lut(m.cells.at(dit->second).type)) {
                        new_c = m.cells.at(dit->second);
                        new_c.connections.set("F", {fb});
                        new_c.attributes.set("keep", "1");
                        new_c.attributes.set("ng_generated", "1");
                        int kl = new_c.type.back() - '0';
                        for (int i = 0; i < kl; ++i) {
                            SigBit ib = new_c.connections.at("I" + std::to_string(i))[0];
                            if (ib >= 0) u.loads[ib] += 1;
                        }
                    } else {
                        new_c.hide_name = 0;
                        new_c.type = "LUT1";
                        new_c.attributes.set("keep", "1");
                        new_c.attributes.set("ng_generated", "1");
                        new_c.parameters.set("INIT", "10");
                        new_c.port_directions.set("I0", "input");
                        new_c.port_directions.set("F", "output");
                        new_c.connections.set("I0", {b});
                        new_c.connections.set("F", {fb});
                        if (b >= 0) u.loads[b] += 1;
                    }
                    std::string new_name = alloc_cell("ng_lut_leg");
                    m.cells.set(new_name, std::move(new_c));
                    u.drivers[fb] = new_name;
                    ++lut_cloned;
                }
            }
        }

        dead_pruned += prune_dead_comb();
    }

    JsonValue stats = JsonValue::object();
    stats["mux6_total"] = mux6_total;
    stats["mux5_total"] = mux5_total;
    stats["mux5_cloned_or_wrapped"] = mux5_cloned;
    stats["lut_cloned_or_wrapped"] = lut_cloned;
    stats["dead_pruned"] = dead_pruned;
    return stats;
}

JsonValue permute_box_lut_pins(RtlilDesign& mapped, const std::vector<std::string>& active, const std::vector<int>& lut_pins_ps) {
    int64_t reordered = 0, total_luts = 0;
    constexpr double wire_ps = 550.0, mux_data_ps = 160.0, mux_sel_ps = 250.0;

    for (const auto& mname : active) {
        RtlilModule& m = mapped.mod_mut(mname);
        if (m.cells.empty()) continue;

        std::unordered_set<SigBit> const_bits;
        std::vector<std::string> comb_names;
        std::unordered_set<std::string> comb_set;
        std::unordered_map<SigBit, std::string> bit_driver;

        for (const auto& [cname, c] : m.cells) {
            if (c.type == "GND" || c.type == "VCC") {
                std::string pin = (c.type == "GND") ? "G" : "V";
                if (const auto* bs = c.connections.find_ptr(pin)) {
                    for (SigBit b : *bs) if (b >= 0) const_bits.insert(b);
                }
                continue;
            }
            if (c.type == "LUT1" || c.type == "LUT2" || c.type == "LUT3" || c.type == "LUT4"
                || c.type == "MUX2_LUT5" || c.type == "MUX2_LUT6" || c.type == "ALU") {
                comb_names.push_back(cname);
                comb_set.insert(cname);
                for (const auto& [p, bs] : c.connections) {
                    if (c.port_directions.value_or(p, "") == "output") {
                        for (SigBit b : bs) if (b >= 0) bit_driver[b] = cname;
                    }
                }
            }
        }

        std::unordered_map<std::string, int> indeg;
        std::unordered_map<std::string, std::vector<std::string>> bit_comb_consumers;
        for (const auto& cname : comb_names) {
            const RtlilCell& c = m.cells.at(cname);
            std::unordered_set<std::string> deps;
            for (const auto& [p, bs] : c.connections) {
                if (c.port_directions.value_or(p, "") == "input") {
                    for (SigBit b : bs) {
                        if (b >= 0) {
                            auto dit = bit_driver.find(b);
                            if (dit != bit_driver.end() && dit->second != cname) {
                                deps.insert(dit->second);
                                bit_comb_consumers[dit->second].push_back(cname);
                            }
                        }
                    }
                }
            }
            indeg[cname] = static_cast<int>(deps.size());
        }

        std::vector<std::string> init_ready;
        for (const auto& cname : comb_names) {
            if (indeg[cname] == 0) init_ready.push_back(cname);
        }
        std::sort(init_ready.begin(), init_ready.end());
        std::deque<std::string> ready(init_ready.begin(), init_ready.end());

        std::unordered_set<std::string> visited;
        std::unordered_map<SigBit, double> arrivals;

        auto sig_at = [&](SigBit b, bool add_wire) -> double {
            if (b < 0 || const_bits.count(b)) return 0.0;
            auto it = arrivals.find(b);
            double base = (it == arrivals.end()) ? 0.0 : it->second;
            return base + (add_wire ? wire_ps : 0.0);
        };

        std::vector<std::string> to_perm_names;
        std::vector<uint16_t> batch_inits;
        std::vector<uint8_t> batch_ks;
        std::vector<uint8_t> batch_ords;

        while (visited.size() < comb_names.size()) {
            if (ready.empty()) {
                std::string best_n;
                int best_d = 1 << 30;
                for (const auto& n : comb_names) {
                    if (visited.count(n)) continue;
                    int d = indeg[n];
                    if (best_n.empty() || d < best_d || (d == best_d && n < best_n)) {
                        best_d = d;
                        best_n = n;
                    }
                }
                ready.push_back(best_n);
            }
            std::string cname = std::move(ready.front());
            ready.pop_front();
            if (visited.count(cname)) continue;
            visited.insert(cname);

            RtlilCell& c = m.cells.at(cname);
            if (c.type == "LUT1" || c.type == "LUT2" || c.type == "LUT3" || c.type == "LUT4") {
                int k = c.type.back() - '0';
                ++total_luts;
                SigBit ins[4]{};
                double at[4]{};
                for (int i = 0; i < k; ++i) {
                    ins[i] = c.connections.at("I" + std::to_string(i))[0];
                    at[i] = sig_at(ins[i], true);
                }
                double out_arr = 0.0;
                if (k >= 2) {
                    std::vector<int> fastest(static_cast<size_t>(k)), by_at(static_cast<size_t>(k));
                    for (int i = 0; i < k; ++i) { fastest[static_cast<size_t>(i)] = i; by_at[static_cast<size_t>(i)] = i; }
                    std::sort(fastest.begin(), fastest.end(), [&](int a, int b) {
                        if (lut_pins_ps[static_cast<size_t>(a)] != lut_pins_ps[static_cast<size_t>(b)]) {
                            return lut_pins_ps[static_cast<size_t>(a)] < lut_pins_ps[static_cast<size_t>(b)];
                        }
                        return a < b;
                    });
                    std::sort(by_at.begin(), by_at.end(), [&](int a, int b) {
                        if (at[a] != at[b]) return at[a] > at[b];
                        return a < b;
                    });
                    int order[4] = {0, 1, 2, 3};
                    for (int i = 0; i < k; ++i) order[fastest[static_cast<size_t>(i)]] = by_at[static_cast<size_t>(i)];

                    double before_arr = 0.0, after_arr = 0.0;
                    for (int i = 0; i < k; ++i) {
                        before_arr = std::max(before_arr, at[i] + static_cast<double>(lut_pins_ps[static_cast<size_t>(i)]));
                        after_arr  = std::max(after_arr, at[order[i]] + static_cast<double>(lut_pins_ps[static_cast<size_t>(i)]));
                    }
                    if (after_arr < before_arr - 1e-6) {
                        uint16_t truth = static_cast<uint16_t>(parse_bin_u64(c.parameters.at("INIT")));
                        for (int i = 0; i < k; ++i) {
                            c.connections.set("I" + std::to_string(i), {ins[order[i]]});
                        }
                        to_perm_names.push_back(cname);
                        batch_inits.push_back(truth);
                        batch_ks.push_back(static_cast<uint8_t>(k));
                        for (int i = 0; i < 4; ++i) batch_ords.push_back(static_cast<uint8_t>(i < k ? order[i] : 0));
                        before_arr = after_arr;
                        ++reordered;
                    }
                    out_arr = before_arr;
                } else {
                    out_arr = at[0] + static_cast<double>(lut_pins_ps[0]);
                }
                SigBit ob = c.connections.at("F")[0];
                if (ob >= 0) arrivals[ob] = out_arr;
            } else if (c.type == "MUX2_LUT5" || c.type == "MUX2_LUT6") {
                double a0 = sig_at(c.connections.at("I0")[0], false) + mux_data_ps;
                double a1 = sig_at(c.connections.at("I1")[0], false) + mux_data_ps;
                double as0 = sig_at(c.connections.at("S0")[0], true) + mux_sel_ps;
                SigBit ob = c.connections.at("O")[0];
                if (ob >= 0) arrivals[ob] = std::max({a0, a1, as0});
            } else if (c.type == "ALU") {
                double ai = 0.0;
                for (const char* p : {"I0", "I1", "I3"}) {
                    if (const auto* bs = c.connections.find_ptr(p); bs && !bs->empty()) {
                        ai = std::max(ai, sig_at((*bs)[0], true));
                    }
                }
                double acin = 0.0;
                if (const auto* bs = c.connections.find_ptr("CIN"); bs && !bs->empty()) {
                    acin = sig_at((*bs)[0], false);
                }
                if (const auto* bs = c.connections.find_ptr("COUT"); bs && !bs->empty() && (*bs)[0] >= 0) {
                    arrivals[(*bs)[0]] = std::max(ai + 450.0, acin + 60.0);
                }
                if (const auto* bs = c.connections.find_ptr("SUM"); bs && !bs->empty() && (*bs)[0] >= 0) {
                    arrivals[(*bs)[0]] = std::max(ai + 520.0, acin + 260.0);
                }
            }

            if (auto sit = bit_comb_consumers.find(cname); sit != bit_comb_consumers.end()) {
                for (const auto& succ : sit->second) {
                    if (!visited.count(succ)) {
                        if (--indeg[succ] <= 0) ready.push_back(succ);
                    }
                }
            }
        }

        if (!to_perm_names.empty()) {
            std::vector<uint16_t> out_inits(to_perm_names.size(), 0);
            SynAccelLib::instance().permute_lut_batch(
                static_cast<int32_t>(to_perm_names.size()),
                batch_inits.data(),
                batch_ks.data(),
                batch_ords.data(),
                out_inits.data()
            );
            for (size_t i = 0; i < to_perm_names.size(); ++i) {
                int k = static_cast<int>(batch_ks[i]);
                m.cells.at(to_perm_names[i]).parameters.set("INIT", format_bin_u64(out_inits[i], 1 << k));
            }
        }
    }

    JsonValue res = JsonValue::object();
    res["total_luts"] = total_luts;
    res["reordered_luts"] = reordered;
    JsonValue lp_arr = JsonValue::array();
    for (int v : lut_pins_ps) lp_arr.push_back(v);
    res["lut_pins_ps"] = std::move(lp_arr);
    return res;
}

} // namespace

std::tuple<RtlilDesign, RtlilDesign, std::array<int64_t, 2>> box_scoped_candidate(
    const RtlilDesign& before,
    const RtlilDesign& mapped,
    const std::vector<std::string>& active
) {
    for (const auto& [mn, _] : before.modules) {
        if (!mapped.modules.contains(mn)) {
            throw std::runtime_error("ABC9 removed input module definitions: " + mn);
        }
    }
    RtlilDesign reference = before.clone_cow(&active);
    RtlilDesign result = before.clone_cow(&active);
    std::array<int64_t, 2> zeros{0, 0};

    for (const auto& name : active) {
        result.modules.set(name, std::make_shared<RtlilModule>(mapped.mod_ref(name)));
        result.mod_mut(name).attributes = before.mod_ref(name).attributes;

        // Reconnect pure pad-side blackbox `inout` pins (e.g. IOBUF.IO) detached onto a dangling wire by abc9 XAIGER roundtrip
        {
            const RtlilModule& bm = before.mod_ref(name);
            RtlilModule& rm = result.mod_mut(name);
            std::unordered_set<SigBit> same_inout_port_bits;
            std::unordered_set<SigBit> res_all_port_bits;
            for (const auto& [pn, rpr] : rm.ports) {
                for (SigBit rb : rpr.bits) if (rb >= 0) res_all_port_bits.insert(rb);
                if (rpr.direction == "inout") {
                    if (const RtlilPort* bpr = bm.ports.find_ptr(pn); bpr && bpr->direction == "inout" && bpr->bits.size() == rpr.bits.size()) {
                        for (size_t pi = 0; pi < rpr.bits.size(); ++pi) {
                            if (bpr->bits[pi] >= 0 && bpr->bits[pi] == rpr.bits[pi]) {
                                same_inout_port_bits.insert(rpr.bits[pi]);
                            }
                        }
                    }
                }
            }
            if (!same_inout_port_bits.empty()) {
                std::unordered_map<SigBit, int> b_uses, r_uses;
                for (const auto& [_, bc] : bm.cells) {
                    for (const auto& [__, bs] : bc.connections) {
                        for (SigBit b : bs) if (b >= 0) b_uses[b] += 1;
                    }
                }
                for (const auto& [_, rc] : rm.cells) {
                    for (const auto& [__, bs] : rc.connections) {
                        for (SigBit b : bs) if (b >= 0) r_uses[b] += 1;
                    }
                }
                std::unordered_map<SigBit, SigBit> dangling_to_port;
                for (auto [cname, rc] : rm.cells) {
                    const RtlilCell* bc = bm.cells.find_ptr(cname);
                    if (!bc || bc->type != rc.type) continue;
                    for (auto [pin, rbs] : rc.connections) {
                        if (rc.port_directions.value_or(pin, "") != "inout") continue;
                        const auto* bbs = bc->connections.find_ptr(pin);
                        if (!bbs || bbs->size() != rbs.size()) continue;
                        for (size_t bi = 0; bi < rbs.size(); ++bi) {
                            SigBit ob = (*bbs)[bi], cb = rbs[bi];
                            if (ob >= 0 && cb >= 0 && ob != cb &&
                                same_inout_port_bits.count(ob) &&
                                b_uses[ob] == 1 && r_uses[ob] == 0 &&
                                r_uses[cb] == 1 && !res_all_port_bits.count(cb)) {
                                rbs[bi] = ob;
                                r_uses[ob] += 1;
                                r_uses[cb] -= 1;
                                dangling_to_port[cb] = ob;
                            }
                        }
                    }
                }
                if (!dangling_to_port.empty()) {
                    for (auto [_, nn] : rm.netnames) {
                        for (SigBit& b : nn.bits) {
                            auto it = dangling_to_port.find(b);
                            if (it != dangling_to_port.end()) b = it->second;
                        }
                    }
                }
            }
        }

        RtlilDesign* both[2] = {&reference, &result};
        for (int idx = 0; idx < 2; ++idx) {
            RtlilModule& dm = both[idx]->mod_mut(name);
            for (auto [_, cell] : dm.cells) {
                for (auto [pin, bits] : cell.connections) {
                    std::string pdir = cell.port_directions.value_or(pin, "");
                    if (pdir != "input" && pdir != "inout") continue;
                    for (SigBit& b : bits) {
                        if (b == SIG_X || b == SIG_Z) {
                            b = SIG_0;
                            zeros[static_cast<size_t>(idx)] += 1;
                        }
                    }
                }
            }
        }
    }
    return {std::move(reference), std::move(result), zeros};
}

// ============================================================================
// native_boundary_cec::prove
// ============================================================================
namespace {

std::vector<CellLogicFunc> boundary_cell_functions(const RtlilCell& cell) {
    if (cell.type != "ALU") {
        if (auto lf = extract_lut_or_mux_logic(cell); lf.has_value()) {
            return {*lf};
        }
        if (const BoolGateDef* gd = find_bool_gate(cell.type)) {
            std::vector<SigBit> ins;
            for (int i = 0; i < gd->arity; ++i) ins.push_back(cell.connections.at(std::string(1, gd->pins[i]))[0]);
            return {CellLogicFunc{std::move(ins), cell.connections.at("Y")[0], gd->truth}};
        }
        if (cell.type == "MUX2_LUT7" || cell.type == "MUX2_LUT8" || cell.type == "MUX2") {
            return {CellLogicFunc{
                {cell.connections.at("I0")[0], cell.connections.at("I1")[0], cell.connections.at("S0")[0]},
                cell.connections.at("O")[0],
                0xCAULL
            }};
        }
        if (cell.type == "INV") {
            return {CellLogicFunc{{cell.connections.at("I")[0]}, cell.connections.at("O")[0], 1ULL}};
        }
        if (cell.type == "GND" || cell.type == "VCC") {
            std::string p = (cell.type == "GND") ? "G" : "V";
            return {CellLogicFunc{{}, cell.connections.at(p)[0], cell.type == "VCC" ? 1ULL : 0ULL}};
        }
        return {};
    }

    int mode = static_cast<int>(parse_bin_u64(cell.parameters.value_or("ALU_MODE", "0")));
    if (mode < 0 || mode >= 9) throw std::runtime_error("Unmodeled native ALU mode");
    uint64_t sums = 0, carry = 0;
    for (int v = 0; v < 16; ++v) {
        int a = v & 1, b = (v >> 1) & 1, s = (v >> 2) & 1, ci = (v >> 3) & 1;
        int prop_tbl[9] = {
            a ^ b, a ^ (1 - b), s ? (a ^ b) : (a ^ (1 - b)),
            a ^ (1 - b), a ^ (1 - b), (1 - a) ^ b, a, 1 - a, s ? a : (1 - a)
        };
        int prop = prop_tbl[mode];
        int gen = (mode == 3 || mode == 7) ? 1 : (mode == 6) ? 0 : (mode == 5) ? b : a;
        sums  |= static_cast<uint64_t>(prop ^ ci) << v;
        carry |= static_cast<uint64_t>(prop ? ci : gen) << v;
    }
    std::vector<SigBit> ins = {
        cell.connections.at("I0")[0], cell.connections.at("I1")[0],
        cell.connections.at("I3")[0], cell.connections.at("CIN")[0]
    };
    return {
        CellLogicFunc{ins, cell.connections.at("SUM")[0], sums},
        CellLogicFunc{ins, cell.connections.at("COUT")[0], carry}
    };
}

std::unordered_map<SigBit, char> module_initial_values(const RtlilModule& m) {
    std::unordered_map<SigBit, char> res;
    for (const auto& [_, nr] : m.netnames) {
        if (const JsonValue* iv = nr.attributes.find_ptr("init"); iv && iv->is_string()) {
            const std::string& val = iv->as_str();
            for (size_t i = 0; i < nr.bits.size() && i < val.size(); ++i) {
                SigBit b = nr.bits[i];
                char vc = val[val.size() - 1 - i];
                auto it = res.find(b);
                if (it != res.end() && it->second != vc) {
                    throw std::runtime_error("Conflicting state initialization");
                }
                res[b] = vc;
            }
        }
    }
    return res;
}

char cell_bit_initial(const std::unordered_map<SigBit, char>& vals, const RtlilCell& c, SigBit b) {
    auto it = vals.find(b);
    if (it != vals.end()) return it->second;
    if (starts_with(c.type, "DFF")) {
        std::string def_i = (c.type == "DFFSE" || c.type == "DFFPE") ? "1" : "0";
        std::string iv = c.parameters.value_or("INIT", def_i);
        return iv.empty() ? def_i[0] : iv.back();
    }
    if (starts_with(c.type, "DL") && c.type != "DLLDLY") {
        std::string def_i = (c.type == "DLPE" || c.type == "DLP" || c.type == "DLNP" || c.type == "DLNPE") ? "1" : "0";
        std::string iv = c.parameters.value_or("INIT", def_i);
        return iv.empty() ? def_i[0] : iv.back();
    }
    return 0;
}

} // namespace

JsonValue prove_native_boundary_cec(
    const RtlilDesign& before,
    const RtlilDesign& after,
    std::string_view out_dir,
    double timeout_sec
) {
    std::string out = resolve_path(out_dir);
    make_dirs(out, true);
    JsonValue reports = JsonValue::array();

    for (const auto& [mn, old_ptr] : before.modules) {
        const RtlilModule& old_m = *old_ptr;
        if (is_module_blackbox(old_m) || old_m.cells.empty()) continue;
        const RtlilModule& new_m = after.mod_ref(mn);
        if (old_ptr.get() == after.modules.at(mn).get() || old_m == new_m) continue;

        OrderedMap<const RtlilCell*> hard0, hard1;
        for (const auto& [n, c] : old_m.cells) {
            if (c.type != "$scopeinfo" && boundary_cell_functions(c).empty()) hard0.set(n, &c);
        }
        for (const auto& [n, c] : new_m.cells) {
            if (c.type != "$scopeinfo" && boundary_cell_functions(c).empty()) hard1.set(n, &c);
        }
        if (hard0.size() != hard1.size()) {
            throw std::runtime_error("Named hard/state instance set changed: " + mn);
        }
        for (const auto& [n, _] : hard0) {
            if (!hard1.contains(n)) throw std::runtime_error("Named hard/state instance set changed: " + mn);
        }

        if (old_m.ports.size() != new_m.ports.size()) throw std::runtime_error("Module port contract changed: " + mn);
        for (const auto& [pn, pr] : old_m.ports) {
            const RtlilPort* npr = new_m.ports.find_ptr(pn);
            if (!npr || npr->direction != pr.direction || npr->bits.size() != pr.bits.size()) {
                throw std::runtime_error("Module port contract changed: " + mn + "." + pn);
            }
        }

        auto init0 = module_initial_values(old_m);
        auto init1 = module_initial_values(new_m);

        std::vector<std::pair<SigBit, SigBit>> inputs;
        std::vector<std::pair<SigBit, SigBit>> observations;

        for (const auto& [pn, pr] : old_m.ports) {
            const auto& nbits = new_m.ports.at(pn).bits;
            for (size_t i = 0; i < pr.bits.size(); ++i) {
                if (pr.direction == "input" || pr.direction == "inout") inputs.emplace_back(pr.bits[i], nbits[i]);
                if (pr.direction != "input") observations.emplace_back(pr.bits[i], nbits[i]);
            }
        }

        for (const auto& [nn, nr] : old_m.netnames) {
            if (!is_attr_truthy(nr.attributes, "keep")) continue;
            const RtlilNetname* ret = new_m.netnames.find_ptr(nn);
            if (!ret || ret->bits.size() != nr.bits.size() || !is_attr_truthy(ret->attributes, "keep")) {
                throw std::runtime_error("Kept internal signal contract changed: " + nn);
            }
            for (size_t i = 0; i < nr.bits.size(); ++i) {
                observations.emplace_back(nr.bits[i], ret->bits[i]);
            }
        }

        for (const auto& [name, c_ptr] : hard0) {
            const RtlilCell& c = *c_ptr;
            const RtlilCell& d = *hard1.at(name);
            if (c.type != d.type || c.parameters != d.parameters || c.port_directions != d.port_directions) {
                throw std::runtime_error("Hard/state function or parameters changed: " + name);
            }
            if (c.connections.size() != d.connections.size()) {
                throw std::runtime_error("Hard/state pin contract changed: " + name);
            }
            for (const auto& [p, bs] : c.connections) {
                const auto* ds = d.connections.find_ptr(p);
                if (!ds || bs.size() != ds->size()) throw std::runtime_error("Hard/state pin width changed: " + name);
                const std::string& pdir = c.port_directions.at(p);
                for (size_t i = 0; i < bs.size(); ++i) {
                    SigBit a = bs[i], b = (*ds)[i];
                    if (pdir == "output" || pdir == "inout") {
                        if (cell_bit_initial(init0, c, a) != cell_bit_initial(init1, d, b)) {
                            throw std::runtime_error("Hard/state initialization changed: " + name);
                        }
                        inputs.emplace_back(a, b);
                    }
                    if (pdir != "output") {
                        observations.emplace_back(a, b);
                    }
                }
            }
        }

        std::unordered_map<SigBit, std::string> bind0, bind1;
        std::vector<std::string> in_names;
        for (auto [a, b] : inputs) {
            if (a < 0 || b < 0) {
                if (a != b) throw std::runtime_error("Constant hard output/input-port contract changed");
                continue;
            }
            auto it0 = bind0.find(a);
            auto it1 = bind1.find(b);
            if (it0 != bind0.end() || it1 != bind1.end()) {
                std::string s0 = (it0 != bind0.end()) ? it0->second : "";
                std::string s1 = (it1 != bind1.end()) ? it1->second : "";
                if (s0 != s1) throw std::runtime_error("Boundary alias relationship changed");
            } else {
                std::string nm = "input_" + std::to_string(in_names.size());
                in_names.push_back(nm);
                bind0[a] = nm;
                bind1[b] = nm;
            }
        }

        auto build_side_net = [&](const RtlilModule& m, const std::unordered_map<SigBit, std::string>& bmap, int side) -> BlifNetwork {
            auto sig = [&](SigBit b) -> std::string {
                if (b == SIG_0) return "c0";
                if (b == SIG_1) return "c1";
                if (b < 0) throw std::runtime_error("Undefined boundary needs explicit normalization");
                auto it = bmap.find(b);
                return (it != bmap.end()) ? it->second : ("internal_" + std::to_string(b));
            };
            BlifNetwork net;
            net.inputs = in_names;
            std::unordered_set<std::string> driven(in_names.begin(), in_names.end());
            driven.insert("c0");
            driven.insert("c1");

            for (const auto& [_, c] : m.cells) {
                for (const auto& cf : boundary_cell_functions(c)) {
                    if (cf.output < 0) continue;
                    std::vector<std::string> ins;
                    ins.reserve(cf.inputs.size());
                    for (SigBit ib : cf.inputs) ins.push_back(sig(ib));
                    std::string out_s = sig(cf.output);
                    driven.insert(out_s);
                    net.nodes.push_back(BlifNode{std::move(ins), std::move(out_s), cf.truth, 0, false});
                }
            }
            net.outputs.reserve(observations.size());
            for (size_t i = 0; i < observations.size(); ++i) {
                SigBit ob = (side == 0) ? observations[i].first : observations[i].second;
                std::string nm = "observe_" + std::to_string(i);
                driven.insert(nm);
                net.nodes.push_back(BlifNode{{sig(ob)}, nm, 2ULL, 0, false});
                net.outputs.push_back(std::move(nm));
            }
            net.nodes.push_back(BlifNode{{}, "c0", 0ULL, 0, false});
            net.nodes.push_back(BlifNode{{}, "c1", 1ULL, 0, false});

            for (const auto& nd : net.nodes) {
                for (const auto& inp : nd.inputs) {
                    if (!driven.count(inp)) {
                        throw std::runtime_error("Unbound native combinational input: " + inp + " in " + mn);
                    }
                }
            }
            return net;
        };

        std::string pdir = path_join(out, "module_" + std::to_string(reports.as_arr().size()));
        BlifNetwork na = build_side_net(old_m, bind0, 0);
        BlifNetwork nb = build_side_net(new_m, bind1, 1);
        JsonValue cec_res = parallel_sliced_cec(na, nb, pdir, timeout_sec);
        if (!cec_res["equivalent"].as_bool()) {
            throw std::runtime_error("Native hard-boundary actual-cell CEC failed: " + mn);
        }

        JsonValue rep = JsonValue::object();
        rep["module"] = mn;
        rep["hard_instances"] = static_cast<int64_t>(hard0.size());
        rep["observed_hard_inputs_and_ports"] = static_cast<int64_t>(observations.size());
        rep["cec"] = std::move(cec_res);
        reports.push_back(std::move(rep));
    }
    return reports;
}

// ============================================================================
// gw5a_box_mapper & map_resource_design
// ============================================================================
namespace {

std::pair<JsonValue, RtlilDesign> run_box_mapper(
    std::string_view source_json,
    std::string_view out_dir,
    std::string_view vendor_library,
    std::string_view dsp_library,
    double period_ps,
    std::string_view architecture_recipe
) {
    double t0 = perf_counter();
    std::string source = resolve_path(source_json);
    std::string out = resolve_path(out_dir);
    make_dirs(out, false);

    std::string raw_src_bytes = read_file_text(source);
    std::string src_sha = sha256_file(source);
    RtlilDesign original = parse_rtlil_json(raw_src_bytes);
    auto [lowered, lowering] = lower_carry_only_chains(original, 10);

    std::string lut_model_p = path_join(runtime_paths().res_root, "results\\timing\\gowin138_model.json");
    JsonValue lut_model = parse_json_file(lut_model_p);
    std::vector<int> lut_pins_ps;
    for (const auto& p : lut_model["physical_lut_inputs"].as_arr()) {
        lut_pins_ps.push_back(static_cast<int>(std::llround(p["setup_max_ns"].as_f64() * 1000.0)));
    }

    std::vector<std::string> active;
    for (const auto& [name, mptr] : lowered.modules) {
        if (is_module_blackbox(*mptr)) continue;
        for (const auto& [_, c] : mptr->cells) {
            if (find_bool_gate(c.type) != nullptr) {
                active.push_back(name);
                break;
            }
        }
    }

    JsonValue act_arr = JsonValue::array();
    for (const auto& a : active) act_arr.push_back(a);

    JsonValue impl_shas = JsonValue::object();
    impl_shas["nextgowin_core.dll"] = sha256_file(runtime_paths().core_dll);
    impl_shas["syn_accel.dll"] = sha256_file(runtime_paths().syn_accel_dll);
    impl_shas["abc.exe"] = sha256_file(runtime_paths().abc_exe);

    JsonValue record = JsonValue::object();
    record["schema"] = "gw5a-box-mapping-v1";
    record["status"] = "running";
    record["strategy"] = "abc9_box";
    record["architecture_recipe"] = std::string(architecture_recipe);
    record["source_sha256"] = src_sha;
    record["vendor_synthesis_seed"] = false;
    record["model_sha256"] = sha256_file(lut_model_p);
    record["period_ps"] = period_ps;
    record["all_local_budgets_satisfied"] = JsonValue(nullptr);
    record["comparison_lowering"] = std::move(lowering);
    record["timing_scope"] = "Cell/pin/FF-box delay-driven ABC9 with calibrated GW5A pin delays and MUX2_LUT5/6 cascades. Common boundary target only; original SDC and hold are checked by the native physical backend.";
    record["mapped_user_modules"] = std::move(act_arr);
    record["modules"] = JsonValue::array();
    record["implementation_sha256"] = std::move(impl_shas);

    auto save = [&]() { write_json_pretty(path_join(out, "manifest.json"), record); };
    save();

    try {
        std::string timing_lib = generate_abc9_timing_models(path_join(out, "timing_models"));
        record["timing_models"] = parse_json_file(path_join(out, "timing_models\\manifest.json"));

        RtlilDesign candidate;
        if (!active.empty()) {
            auto [rebuilt, rewriting] = rewrite_aig_modules(lowered, path_join(out, "rewrite"));
            record["rewrite_proofs"] = std::move(rewriting);
            std::string rewritten_p = path_join(out, "rewritten.json");
            write_rtlil_json_file(rewritten_p, rebuilt);

            std::string vlib_clean = masked_verilog(read_file_text(vendor_library), false);
            std::unordered_set<std::string> supplied;
            for (size_t p = 0; p < vlib_clean.size(); ) {
                size_t hit = vlib_clean.find("module", p);
                if (hit == std::string::npos) break;
                auto is_w = [](char ch) noexcept {
                    auto uc = static_cast<unsigned char>(ch);
                    return std::isalnum(uc) || ch == '_' || ch == '$';
                };
                bool lok = (hit == 0) || !is_w(vlib_clean[hit - 1]);
                bool rok = (hit + 6 < vlib_clean.size()) && std::isspace(static_cast<unsigned char>(vlib_clean[hit + 6]));
                if (lok && rok) {
                    size_t i = hit + 6;
                    while (i < vlib_clean.size() && std::isspace(static_cast<unsigned char>(vlib_clean[i]))) ++i;
                    size_t id_s = i;
                    if (i < vlib_clean.size() && vlib_clean[i] == '\\') {
                        ++i;
                        while (i < vlib_clean.size() && !std::isspace(static_cast<unsigned char>(vlib_clean[i]))) ++i;
                    } else if (i < vlib_clean.size() && (std::isalpha(static_cast<unsigned char>(vlib_clean[i])) || vlib_clean[i] == '_' || vlib_clean[i] == '$')) {
                        ++i;
                        while (i < vlib_clean.size() && is_w(vlib_clean[i])) ++i;
                    }
                    if (i > id_s) {
                        supplied.insert(vlib_clean.substr(id_s, i - id_s));
                    }
                    p = (i > hit + 6) ? i : (hit + 6);
                } else {
                    p = hit + 6;
                }
            }
            std::unordered_set<std::string> used_missing;
            for (const auto& [_, mptr] : rebuilt.modules) {
                if (is_module_blackbox(*mptr)) continue;
                for (const auto& [__, c] : mptr->cells) {
                    if (!supplied.count(c.type)) used_missing.insert(c.type);
                }
            }
            std::string native_lib_p = path_join(out, "native_interfaces.v");
            auto sdk_ifaces = write_native_interfaces(native_lib_p, used_missing);
            JsonValue si_arr = JsonValue::array();
            for (const auto& s : sdk_ifaces) si_arr.push_back(s);
            record["sdk_interfaces"] = std::move(si_arr);

            std::vector<std::string> libs = {
                "read_verilog -overwrite -lib " + quote_yosys_path(native_lib_p),
                "read_verilog -overwrite -lib " + quote_yosys_path(vendor_library),
            };
            if (!dsp_library.empty() && file_exists(dsp_library)) {
                libs.push_back("read_verilog -overwrite -lib " + quote_yosys_path(dsp_library));
            }
            std::string delay_arg = " -D " + std::to_string(static_cast<int64_t>(period_ps));
            bool is_wide = (architecture_recipe == "wide" || architecture_recipe == "wide_pins" || architecture_recipe == "wide_no_recovery");
            std::string abc9_cmd = std::string(is_wide ? "abc9 -maxlut 6 -W 450" : "abc9 -maxlut 4 -W 500") + delay_arg;
            record["abc9_command"] = abc9_cmd;

            std::string gm_p = path_join(out, "global_mapped.json");
            std::string map_seed_il = path_join(out, "seed_autoidx.il");
            write_file_text(map_seed_il, "autoidx 10000000\n");
            std::vector<std::string> ycmds = {
                "read_rtlil " + quote_yosys_path(map_seed_il),
                "read_json " + quote_yosys_path(rewritten_p),
                "read_rtlil " + quote_yosys_path(map_seed_il),
                "setattr -mod -unset abc9_box =*",
                "setattr -mod -unset abc9_box_id =*",
                "setattr -mod -unset abc9_lut =*",
                "setattr -mod -unset abc9_flop =*",
            };
            ycmds.insert(ycmds.end(), libs.begin(), libs.end());
            ycmds.push_back("read_verilog -overwrite -specify -lib " + quote_yosys_path(timing_lib));
            ycmds.push_back("sort");
            ycmds.push_back("read_verilog -icells -lib -specify +/abc9_model.v");
            ycmds.push_back("select " + join_strings(active, " ") + " A:abc9_lut A:abc9_box A:abc9_box_id A:abc9_flop");
            ycmds.push_back(abc9_cmd);
            ycmds.push_back("techmap -map +/gowin/cells_map.v");
            ycmds.push_back("opt_lut_ins -tech gowin");
            ycmds.push_back("clean");
            ycmds.push_back("select *");
            ycmds.push_back("bufnorm -conn");
            ycmds.push_back("blackbox =A:whitebox");
            ycmds.push_back("write_json " + quote_yosys_path(gm_p));

            run_yosys_commands(ycmds, out, "abc9", 900.0);
            remove_file_if_exists(map_seed_il);

            RtlilDesign mapped = read_rtlil_json_file(gm_p);
            record["dedicated_mux_legalization"] = legalize_dedicated_mux_trees(mapped, active);
            record["lut_pin_permutation"] = permute_box_lut_pins(mapped, active, lut_pins_ps);
            auto [reference, cand_des, zeros] = box_scoped_candidate(original, mapped, active);
            candidate = std::move(cand_des);
            record["dedicated_mux_packing"] = validate_dedicated_mux_packing(candidate);
            JsonValue z_arr = JsonValue::array();
            z_arr.push_back(zeros[0]);
            z_arr.push_back(zeros[1]);
            record["undefined_input_zero_fill"] = std::move(z_arr);

            std::string pref_p = path_join(out, "proof_reference.json");
            std::string pref_txt = dump_rtlil_json_compact(reference);
            write_file_text(pref_p, pref_txt);
            record["proof_reference_sha256"] = sha256_str(pref_txt);

            record["native_proofs"] = prove_native_boundary_cec(reference, candidate, path_join(out, "native_proof"), 300.0);
            JsonValue mods_arr = JsonValue::array();
            for (const auto& name : active) {
                for (const auto& [_, c] : candidate.mod_ref(name).cells) {
                    if (find_bool_gate(c.type) != nullptr) {
                        throw std::runtime_error("Unmapped Boolean cells remain: " + name);
                    }
                }
                JsonValue mr = JsonValue::object();
                mr["name"] = name;
                mods_arr.push_back(std::move(mr));
            }
            record["modules"] = std::move(mods_arr);
        } else {
            candidate = original;
            std::string pref_p = path_join(out, "proof_reference.json");
            std::string pref_txt = dump_rtlil_json_compact(original);
            write_file_text(pref_p, pref_txt);
            record["proof_reference_sha256"] = sha256_str(pref_txt);
            record["native_proofs"] = JsonValue::array();
        }

        std::string target = path_join(out, "mapped.json");
        std::string cand_txt = dump_rtlil_json_compact(candidate);
        write_file_text(target, cand_txt);
        record["status"] = "complete";
        record["output_sha256"] = sha256_str(cand_txt);
        record["wall_seconds"] = perf_counter() - t0;
        save();
        return {std::move(record), std::move(candidate)};
    } catch (const std::exception& ex) {
        record["status"] = "failed";
        record["error"] = std::string(ex.what());
        record["wall_seconds"] = perf_counter() - t0;
        save();
        throw;
    }
}

// Replace cut gates with mapped BlifNetwork (map_resource_json.py:replace)
RtlilModule replace_module_boolean_cut(const RtlilModule& module, const ExtractedCut& cut, const BlifNetwork& network) {
    RtlilModule result;
    result.attributes = module.attributes;
    result.parameter_default_values = module.parameter_default_values;
    result.ports = module.ports;
    result.memories = module.memories;
    result.netnames = module.netnames;

    for (const auto& [n, c] : module.cells) {
        if (!cut.gates.contains(n)) result.cells.set(n, c);
    }

    int32_t maximum = max_module_bit(module);
    std::unordered_map<std::string, SigBit> mapping{{"c0", SIG_0}, {"c1", SIG_1}};
    for (const auto& s : cut.inputs) mapping[s] = std::stoi(s.substr(1));
    for (const auto& s : cut.outputs) mapping[s] = std::stoi(s.substr(1));

    auto get_bit = [&](const std::string& nm) -> SigBit {
        auto it = mapping.find(nm);
        if (it != mapping.end()) return it->second;
        SigBit nb = ++maximum;
        mapping.emplace(nm, nb);
        return nb;
    };

    for (size_t idx = 0; idx < network.nodes.size(); ++idx) {
        const BlifNode& nd = network.nodes[idx];
        if (nd.output == "c0" || nd.output == "c1") {
            if (!nd.inputs.empty() || nd.truth != static_cast<uint64_t>(nd.output.back() - '0')) {
                throw std::runtime_error("Literal constant net was changed by mapping");
            }
            continue;
        }
        std::string name = "ng_acd_" + std::to_string(idx);
        if (result.cells.contains(name)) throw std::runtime_error("Generated cell name collision: " + name);
        int k = static_cast<int>(nd.inputs.size());
        if (k > 4) throw std::runtime_error("Mapped LUT exceeds native LUT4 interface");

        RtlilCell cell;
        cell.hide_name = 0;
        if (nd.native_mux != 0) {
            if (k != 3 || nd.truth != 0xCAULL || (nd.native_mux != 5 && nd.native_mux != 6)) {
                throw std::runtime_error("Invalid architecture mux");
            }
            cell.type = "MUX2_LUT" + std::to_string(nd.native_mux);
            if (nd.keep) cell.attributes.set("keep", "1");
            cell.attributes.set("ng_generated", "1");
            cell.port_directions.set("I0", "input");
            cell.port_directions.set("I1", "input");
            cell.port_directions.set("S0", "input");
            cell.port_directions.set("O", "output");
            cell.connections.set("I0", {get_bit(nd.inputs[0])});
            cell.connections.set("I1", {get_bit(nd.inputs[1])});
            cell.connections.set("S0", {get_bit(nd.inputs[2])});
            cell.connections.set("O", {get_bit(nd.output)});
        } else if (k > 0) {
            cell.type = "LUT" + std::to_string(k);
            if (nd.keep) cell.attributes.set("keep", "1");
            cell.attributes.set("ng_generated", "1");
            cell.parameters.set("INIT", format_bin_u64(nd.truth, 1 << k));
            for (int i = 0; i < k; ++i) {
                std::string pk = "I" + std::to_string(i);
                cell.port_directions.set(pk, "input");
                cell.connections.set(pk, {get_bit(nd.inputs[static_cast<size_t>(i)])});
            }
            cell.port_directions.set("F", "output");
            cell.connections.set("F", {get_bit(nd.output)});
        } else {
            bool is_vcc = (nd.truth & 1ULL) != 0;
            cell.type = is_vcc ? "VCC" : "GND";
            std::string pk = is_vcc ? "V" : "G";
            cell.port_directions.set(pk, "output");
            cell.connections.set(pk, {get_bit(nd.output)});
        }
        result.cells.set(name, std::move(cell));
    }
    return result;
}

std::string strip_blif_timing_directives(std::string_view text) {
    static const std::unordered_set<std::string> dirs = {
        ".and_gate_delay",".input_arrival",".output_required",
        ".default_input_arrival",".default_output_required",
        ".default_input_drive",".default_output_load",".input_drive",".output_load"
    };
    std::vector<std::string> kept;
    for (const auto& ln : split_lines(text)) {
        auto ws = split_ws(ln);
        if (ws.empty() || !dirs.count(ws[0])) kept.push_back(ln);
    }
    return join_strings(kept, "\n") + "\n";
}

// Constrained ACD mapping (`baseline`, `acd`, `acd_remap`)
std::pair<JsonValue, BlifNetwork> run_constrained_acd_module(
    std::string_view input_blif,
    double period_ps,
    std::string_view model_path,
    std::string_view out_dir,
    std::string_view strategy
) {
    std::string out = resolve_path(out_dir);
    make_dirs(out, false);
    JsonValue model = parse_json_file(model_path);
    double delay = model["lut_envelope_ps"].as_f64();

    std::string strat_cmd = (strategy == "baseline") ? "dch; if -K 4"
                          : (strategy == "acd") ? "dch; if -Z 4 -K 6"
                          : "dch; if -Z 4 -K 6; strash; dch -f; if -K 4";

    std::string source_p = path_join(out, "source.blif");
    auto [ld, _] = invoke_abc("read \"" + posix_path(resolve_path(input_blif)) + "\"; write_blif \"" + posix_path(source_p) + "\"; echo NEXTGOWIN_DONE", path_join(out, "read.log"), 180.0);
    if (ld["status"].as_str() != "ok") throw std::runtime_error("Input conversion failed");
    std::string src_logic = strip_blif_timing_directives(read_file_text(source_p));
    write_file_text(source_p, src_logic);

    BlifNetwork orig = read_blif_file(source_p, 11);
    std::vector<std::string> ann_lines = {".and_gate_delay 1", ".default_input_arrival 0 0"};
    int req_q = static_cast<int>(std::floor(period_ps / delay + 1e-12));
    for (const auto& p : orig.inputs) ann_lines.push_back(".input_arrival " + p + " 0 0");
    for (const auto& p : orig.outputs) ann_lines.push_back(".output_required " + p + " " + std::to_string(req_q) + " " + std::to_string(req_q));

    std::string annotated = src_logic;
    size_t ep = annotated.rfind(".end");
    annotated = annotated.substr(0, ep) + join_strings(ann_lines, "\n") + "\n.end\n";
    std::string ann_p = path_join(out, "annotated.blif");
    write_file_text(ann_p, annotated);

    std::string mapped_p = path_join(out, "mapped.blif");
    auto [map_res, map_log] = invoke_abc(
        "read_blif -n \"" + posix_path(ann_p) + "\"; strash; " + strat_cmd + "; print_stats; write_blif \"" + posix_path(mapped_p) + "\"; echo NEXTGOWIN_DONE",
        path_join(out, "mapping.log"), 180.0
    );
    if (map_res["status"].as_str() != "ok") throw std::runtime_error("Constrained mapping failed");

    std::string logic_p = path_join(out, "mapped_logic.blif");
    write_file_text(logic_p, strip_blif_timing_directives(read_file_text(mapped_p)));
    JsonValue ver = run_abc_cec(source_p, logic_p, path_join(out, "cec.log"), 180.0);
    if (!ver["equivalent"].as_bool()) throw std::runtime_error("Constrained mapping CEC failed");

    BlifNetwork net = read_blif_file(logic_p, 4);
    std::unordered_map<std::string, const BlifNode*> drv;
    for (const auto& nd : net.nodes) drv[nd.output] = &nd;
    std::unordered_map<std::string, double> arr;
    for (const auto& p : net.inputs) arr[p] = 0.0;
    for (const auto& nm : net.topological) {
        const BlifNode& nd = *drv.at(nm);
        auto [sup_in, _] = reduce_truth_function(nd.inputs, nd.truth);
        if (sup_in.empty()) arr[nm] = 0.0;
        else {
            double mx = 0.0;
            for (const auto& s : sup_in) mx = std::max(mx, arr[s]);
            arr[nm] = mx + delay;
        }
    }
    double wns = 1e18, tns = 0.0;
    bool meets = true;
    for (const auto& p : net.outputs) {
        double slk = period_ps - arr[p];
        wns = std::min(wns, slk);
        if (slk < 0.0) tns += slk;
        if (slk < -1e-6) meets = false;
    }
    JsonValue tim = JsonValue::object();
    tim["wns_ps"] = wns;
    tim["tns_ps"] = tns;
    tim["meets_required"] = meets;
    JsonValue out_r = JsonValue::object();
    out_r["independent_timing"] = std::move(tim);
    write_json_pretty(path_join(out, "manifest.json"), out_r);
    return {std::move(out_r), std::move(net)};
}

} // namespace

std::pair<JsonValue, std::optional<RtlilDesign>> map_resource_design(
    std::string_view source_json,
    std::string_view out_dir,
    double period_ps,
    std::string_view strategy,
    std::string_view architecture_recipe,
    bool return_in_memory
) {
    static const std::unordered_set<std::string> valid_recipes = {
        "wide","lut4","wide_no_recovery","lut4_local_wide","lut4_pins","wide_pins","local_wide_pins"
    };
    if (!valid_recipes.count(std::string(architecture_recipe))) {
        throw std::runtime_error("Unknown architecture recipe: " + std::string(architecture_recipe));
    }
    if (strategy != "arch_delay" && strategy != "abc9_box" && architecture_recipe != "wide") {
        throw std::runtime_error("Architecture recipe requires arch_delay or abc9_box mapping");
    }
    if (!std::isfinite(period_ps) || period_ps <= 0.0) {
        throw std::runtime_error("Positive finite synthesis boundary budget required");
    }

    std::string source = resolve_path(source_json);
    std::string res_parent = parent_dir(source);
    if (strategy == "abc9_box") {
        if (file_exists(path_join(res_parent, "register_choices.json"))) {
            throw std::runtime_error("abc9_box currently requires register_alternatives=false");
        }
        std::string vlib = path_join(parent_dir(res_parent), "02_bridge\\vendor_cells.v");
        std::string dlib = path_join(res_parent, "dsp_cells.v");
        auto [rec, des] = run_box_mapper(source, out_dir, vlib, dlib, period_ps, architecture_recipe);
        if (return_in_memory) return {std::move(rec), std::move(des)};
        return {std::move(rec), std::nullopt};
    }

    // Non-box module-by-module mapping (`acd_remap`, `acd`, `baseline`, `arch_delay`)
    std::string out = resolve_path(out_dir);
    make_dirs(out, false);
    std::string model_p = path_join(runtime_paths().res_root, "results\\timing\\gowin138_model.json");

    RtlilDesign design = read_rtlil_json_file(source);
    JsonValue result = JsonValue::object();
    result["schema"] = "resource-acd-mapping-v1";
    result["status"] = "running";
    result["source_sha256"] = sha256_file(source);
    result["period_ps"] = period_ps;
    result["strategy"] = std::string(strategy);
    result["architecture_recipe"] = std::string(architecture_recipe);
    result["modules"] = JsonValue::array();
    result["model_sha256"] = sha256_file(model_p);

    auto save = [&]() { write_json_pretty(path_join(out, "manifest.json"), result); };
    save();

    bool all_sat = true;
    for (const auto& [name, mptr] : design.modules) {
        if (is_module_blackbox(*mptr)) continue;
        for (const auto& [_, c] : mptr->cells) {
            if (starts_with(c.type, "$") && find_bool_gate(c.type) == nullptr && c.type != "$scopeinfo") {
                throw std::runtime_error("Unlowered cells remain after resource inference: " + c.type);
            }
        }
        auto cut = extract_module_boolean_cut(*mptr);
        if (!cut.has_value()) continue;

        std::string folder = path_join(out, "module_" + std::to_string(result["modules"].as_arr().size()));
        make_dirs(folder, false);
        std::string orig_blif = path_join(folder, "source.blif");
        write_blif_fast(orig_blif, cut->blif_net, false);

        std::string eff_strat = (strategy == "arch_delay") ? "acd_remap" : std::string(strategy);
        auto [mapped_info, network] = run_constrained_acd_module(orig_blif, period_ps, model_p, path_join(folder, "mapper"), eff_strat);
        RtlilModule cand_mod = replace_module_boolean_cut(*mptr, *cut, network);

        std::string actual_blif = path_join(folder, "readback.blif");
        write_file_text(actual_blif, readback_acd_module_blif(cand_mod, *cut));
        JsonValue proof = run_abc_cec(orig_blif, actual_blif, path_join(folder, "readback_cec.log"), 300.0);
        if (!proof["equivalent"].as_bool()) {
            throw std::runtime_error("Actual JSON LUT INITs did not pass boundary CEC: " + name);
        }
        design.modules.set(name, std::make_shared<RtlilModule>(std::move(cand_mod)));

        bool mod_meets = mapped_info["independent_timing"]["meets_required"].as_bool();
        if (!mod_meets) all_sat = false;

        JsonValue mrow = JsonValue::object();
        mrow["name"] = name;
        mrow["gate_count"] = static_cast<int64_t>(cut->gates.size());
        mrow["inputs"] = static_cast<int64_t>(cut->inputs.size());
        mrow["outputs"] = static_cast<int64_t>(cut->outputs.size());
        mrow["readback_cec"] = std::move(proof);
        mrow["non_boolean_cells_preserved"] = true;
        mrow["timing"] = mapped_info["independent_timing"];
        result["modules"].push_back(std::move(mrow));
        save();
        std::printf("%s %zu gates mapped; readback CEC passed\n", name.c_str(), cut->gates.size());
        std::fflush(stdout);
    }

    std::string target = path_join(out, "mapped.json");
    std::string out_txt = dump_rtlil_json_compact(design);
    write_file_text(target, out_txt);
    result["status"] = "complete";
    result["output_sha256"] = sha256_str(out_txt);
    result["all_local_budgets_satisfied"] = all_sat;
    save();
    if (return_in_memory) return {std::move(result), std::move(design)};
    return {std::move(result), std::nullopt};
}

} // namespace ng
