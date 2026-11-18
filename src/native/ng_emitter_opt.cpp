#include "ng_pipeline.hpp"

#include <cmath>
#include <deque>
#include <map>
#include <set>
#include <thread>
#include <unordered_map>
#include <unordered_set>

namespace ng {

namespace {

constexpr double OPT_LUT_PIN_PS[4] = {521.0, 511.0, 395.0, 255.0};

} // namespace

std::optional<CellLogicFunc> extract_lut_or_mux_logic(const RtlilCell& c) {
    if (c.type == "LUT1" || c.type == "LUT2" || c.type == "LUT3" || c.type == "LUT4") {
        int k = c.type.back() - '0';
        std::vector<SigBit> ins;
        ins.reserve(static_cast<size_t>(k));
        for (int i = 0; i < k; ++i) {
            ins.push_back(c.connections.at("I" + std::to_string(i))[0]);
        }
        return CellLogicFunc{std::move(ins), c.connections.at("F")[0], parse_bin_u64(c.parameters.at("INIT"))};
    }
    if (c.type == "MUX2_LUT5" || c.type == "MUX2_LUT6" || c.type == "MUX2_LUT7") {
        return CellLogicFunc{
            {c.connections.at("I0")[0], c.connections.at("I1")[0], c.connections.at("S0")[0]},
            c.connections.at("O")[0],
            0xCAULL
        };
    }
    return std::nullopt;
}

// ============================================================================
// 1. insert_gowin_pads (insert_gowin_pads.py)
// ============================================================================
namespace {

std::pair<RtlilDesign, JsonValue> insert_gowin_pads(const RtlilDesign& src_design, std::string_view top_sv) {
    std::string top(top_sv);
    RtlilDesign design = src_design.clone_cow();
    static const std::unordered_map<std::string, std::vector<std::string>> PAD_PINS = {
        {"IBUF", {"I"}}, {"OBUF", {"O"}}, {"TBUF", {"O"}}, {"IOBUF", {"IO"}},
        {"TLVDS_IBUF", {"I", "IB"}}, {"ELVDS_IBUF", {"I", "IB"}},
        {"TLVDS_OBUF", {"O", "OB"}}, {"ELVDS_OBUF", {"O", "OB"}},
        {"TLVDS_IOBUF", {"IO", "IOB"}}, {"ELVDS_IOBUF", {"IO", "IOB"}},
        {"TLVDS_TBUF", {"O", "OB"}}, {"ELVDS_TBUF", {"O", "OB"}},
        {"GTR12_QUAD", {
            "LN0_RXM_I", "LN0_RXP_I", "LN0_TXM_O", "LN0_TXP_O",
            "LN1_RXM_I", "LN1_RXP_I", "LN1_TXM_O", "LN1_TXP_O",
            "LN2_RXM_I", "LN2_RXP_I", "LN2_TXM_O", "LN2_TXP_O",
            "LN3_RXM_I", "LN3_RXP_I", "LN3_TXM_O", "LN3_TXP_O",
            "REFCLKM0_I", "REFCLKP0_I", "REFCLKM1_I", "REFCLKP1_I"
        }},
    };

    std::unordered_map<std::string, std::string> uf_parent;
    auto make_key = [](const std::string& path_s, SigBit b) {
        return path_s + "#" + std::to_string(b);
    };
    auto uf_find = [&](std::string x) -> std::string {
        std::string root = x;
        while (true) {
            auto it = uf_parent.find(root);
            if (it == uf_parent.end() || it->second == root) break;
            root = it->second;
        }
        while (x != root) {
            auto it = uf_parent.find(x);
            std::string nxt = (it == uf_parent.end()) ? root : it->second;
            uf_parent[x] = root;
            x = nxt;
        }
        return root;
    };
    auto uf_union = [&](const std::string& a, const std::string& b) {
        std::string ra = uf_find(a), rb = uf_find(b);
        if (ra != rb) uf_parent[rb] = ra;
    };

    std::vector<std::string> pad_keys;
    std::unordered_map<std::string, std::vector<std::string>> mod_inst_paths;
    int64_t gsr_count = 0;

    std::function<void(const std::string&, const std::string&, std::unordered_set<std::string>&)> walk;
    walk = [&](const std::string& mname, const std::string& path_s, std::unordered_set<std::string>& stack) {
        if (stack.count(mname)) throw std::runtime_error("Recursive hierarchy: " + mname);
        stack.insert(mname);
        mod_inst_paths[mname].push_back(path_s);
        const RtlilModule& mod = design.mod_ref(mname);
        for (const auto& [cname, cell] : mod.cells) {
            const std::string& child = cell.type;
            std::string child_path = path_s.empty() ? cname : (path_s + "/" + cname);
            if (child == "GSR") ++gsr_count;
            if (auto pit = PAD_PINS.find(child); pit != PAD_PINS.end()) {
                for (const auto& pin : pit->second) {
                    if (const auto* bs = cell.connections.find_ptr(pin)) {
                        for (SigBit b : *bs) if (b >= 0) pad_keys.push_back(make_key(path_s, b));
                    }
                }
            }
            if (const auto* cmod_ptr = design.modules.find_ptr(child); cmod_ptr && *cmod_ptr) {
                if (const JsonValue* npp = (*cmod_ptr)->attributes.find_ptr("ng_pad_ports"); npp && npp->is_string()) {
                    for (const auto& pin : split_ws(npp->as_str())) {
                        const auto* bs = cell.connections.find_ptr(pin);
                        if (!bs) throw std::runtime_error("Opaque macro I/O pin missing: " + pin);
                        for (SigBit b : *bs) if (b >= 0) pad_keys.push_back(make_key(path_s, b));
                    }
                }
                if (!is_module_blackbox(**cmod_ptr)) {
                    for (const auto& [port, prow] : (*cmod_ptr)->ports) {
                        const auto* conn = cell.connections.find_ptr(port);
                        if (!conn || conn->empty()) continue;
                        if (conn->size() != prow.bits.size()) {
                            throw std::runtime_error("Hierarchy port connection missing or wrong width: " + child_path + "/" + port);
                        }
                        for (size_t i = 0; i < conn->size(); ++i) {
                            if ((*conn)[i] >= 0 && prow.bits[i] >= 0) {
                                uf_union(make_key(path_s, (*conn)[i]), make_key(child_path, prow.bits[i]));
                            }
                        }
                    }
                    walk(child, child_path, stack);
                }
            }
        }
        stack.erase(mname);
    };

    std::unordered_set<std::string> init_stack;
    walk(top, "", init_stack);

    // Lower unbonded single-ended pad cells inside non-blackbox child modules whose external
    // pad pin does not connect to any top-level port (e.g. partially bonded GPIO buses in SoC IP).
    {
        std::unordered_set<std::string> top_port_roots;
        for (const auto& [_, prow] : design.mod_ref(top).ports) {
            for (SigBit b : prow.bits) {
                if (b >= 0) top_port_roots.insert(uf_find(make_key("", b)));
            }
        }
        // Count leaf/blackbox cell pin attachments per flattened net equivalence class
        // so IOBUF.IO / TBUF.O lowering never alters internal sibling drivers or readers.
        std::unordered_map<std::string, int> leaf_pin_refs;
        for (const auto& [mn_chk, ipaths] : mod_inst_paths) {
            const RtlilModule& m_chk = design.mod_ref(mn_chk);
            if (is_module_blackbox(m_chk)) continue;
            for (const auto& [_, c_chk] : m_chk.cells) {
                const auto* sub_m = design.modules.find_ptr(c_chk.type);
                if (sub_m && *sub_m && !is_module_blackbox(**sub_m)) continue;
                for (const auto& [__, bs_chk] : c_chk.connections) {
                    for (SigBit sb_chk : bs_chk) {
                        if (sb_chk < 0) continue;
                        for (const auto& p_s : ipaths) {
                            leaf_pin_refs[uf_find(make_key(p_s, sb_chk))]++;
                        }
                    }
                }
            }
        }

        bool any_lowered = false;
        for (const auto& [mname, inst_paths] : mod_inst_paths) {
            if (mname == top || inst_paths.empty()) continue;
            RtlilModule& cmod = design.mod_mut(mname);
            if (is_module_blackbox(cmod)) continue;
            std::vector<std::string> erase_cnames;
            std::unordered_map<SigBit, SigBit> sub_map;
            for (auto [cname, cell] : cmod.cells) {
                std::string ext_pin;
                if (cell.type == "IOBUF") ext_pin = "IO";
                else if (cell.type == "IBUF") ext_pin = "I";
                else if (cell.type == "OBUF" || cell.type == "TBUF") ext_pin = "O";
                else continue;

                for (const auto& [pn_chk, pv_chk] : cell.connections) {
                    if (pv_chk.size() > 1) {
                        throw std::runtime_error("Unsupported multi-bit pad port " + pn_chk + " on " + mname + "/" + cname);
                    }
                }

                auto scalar_bit = [&](const char* pn, SigBit def_b) -> SigBit {
                    const auto* v = cell.connections.find_ptr(pn);
                    return (v && !v->empty()) ? (*v)[0] : def_b;
                };

                SigBit eb = scalar_bit(ext_pin.c_str(), -99);
                size_t bonded_cnt = 0;
                if (eb >= 0) {
                    for (const auto& p_s : inst_paths) {
                        if (top_port_roots.count(uf_find(make_key(p_s, eb)))) ++bonded_cnt;
                    }
                }
                if (bonded_cnt == inst_paths.size()) continue;
                if (bonded_cnt > 0) {
                    throw std::runtime_error("Mixed bonded and unbonded submodule pad across instances: " + mname + "/" + cname);
                }

                if (cell.type == "IOBUF" || cell.type == "TBUF") {
                    if (eb >= 0) {
                        for (const auto& p_s : inst_paths) {
                            if (leaf_pin_refs[uf_find(make_key(p_s, eb))] > 1) {
                                throw std::runtime_error("Unbonded internal " + cell.type + " " + mname + "/" + cname +
                                                         " has internal sibling connections on " + ext_pin);
                            }
                        }
                    }
                    any_lowered = true;
                    if (cell.type == "IOBUF") {
                        SigBit o_b = scalar_bit("O", -99);
                        if (o_b >= 0) {
                            SigBit i_b = scalar_bit("I", SIG_0);
                            SigBit oen_b = scalar_bit("OEN", SIG_1);
                            if (i_b == SIG_Z) i_b = SIG_0;
                            if (oen_b == SIG_Z) oen_b = SIG_1;
                            if (i_b < 0 && i_b != SIG_0 && i_b != SIG_1) throw std::runtime_error("Unknown constant on unbonded IOBUF.I: " + mname + "/" + cname);
                            if (oen_b < 0 && oen_b != SIG_0 && oen_b != SIG_1) throw std::runtime_error("Unknown constant on unbonded IOBUF.OEN: " + mname + "/" + cname);
                            cell.type = "LUT2";
                            cell.parameters = OrderedMap<std::string>();
                            cell.parameters.set("INIT", "0010");
                            cell.port_directions = OrderedMap<std::string>();
                            cell.port_directions.set("I0", "input");
                            cell.port_directions.set("I1", "input");
                            cell.port_directions.set("F", "output");
                            cell.connections = OrderedMap<std::vector<SigBit>>();
                            cell.connections.set("I0", {i_b});
                            cell.connections.set("I1", {oen_b});
                            cell.connections.set("F", {o_b});
                        } else {
                            erase_cnames.push_back(cname);
                        }
                    } else {
                        erase_cnames.push_back(cname);
                    }
                } else if (cell.type == "IBUF" || cell.type == "OBUF") {
                    any_lowered = true;
                    SigBit o_b = scalar_bit("O", -99);
                    SigBit i_b = scalar_bit("I", SIG_0);
                    if (i_b == SIG_Z) i_b = SIG_0;
                    if (o_b >= 0 && o_b != i_b) {
                        auto it = sub_map.find(o_b);
                        if (it != sub_map.end() && it->second != i_b) {
                            throw std::runtime_error("Conflicting internal IBUF/OBUF drivers in " + mname + " on " + cname);
                        }
                        sub_map[o_b] = i_b;
                    }
                    erase_cnames.push_back(cname);
                }
            }
            for (const auto& en : erase_cnames) cmod.cells.erase(en);
            if (!sub_map.empty()) {
                auto resolve_sub = [&](SigBit b) -> SigBit {
                    SigBit cur = b;
                    std::unordered_set<SigBit> seen;
                    while (cur >= 0) {
                        auto it = sub_map.find(cur);
                        if (it == sub_map.end()) break;
                        if (!seen.insert(cur).second) {
                            throw std::runtime_error("Cyclic internal IBUF/OBUF alias chain in " + mname);
                        }
                        cur = it->second;
                    }
                    return cur;
                };
                for (auto [_, prow] : cmod.ports) {
                    for (SigBit& b : prow.bits) b = resolve_sub(b);
                }
                for (auto [_, cell] : cmod.cells) {
                    for (auto [__, bs] : cell.connections) {
                        for (SigBit& b : bs) b = resolve_sub(b);
                    }
                }
                for (auto [_, nr] : cmod.netnames) {
                    for (SigBit& b : nr.bits) b = resolve_sub(b);
                }
            }
        }
        if (any_lowered) {
            uf_parent.clear();
            pad_keys.clear();
            mod_inst_paths.clear();
            gsr_count = 0;
            init_stack.clear();
            walk(top, "", init_stack);
        }
    }

    std::unordered_set<std::string> padded;
    for (const auto& pk : pad_keys) padded.insert(uf_find(pk));

    RtlilModule& module = design.mod_mut(top);
    int32_t maximum = max_module_bit(module);
    std::unordered_map<SigBit, SigBit> replace_map;
    OrderedMap<RtlilCell> new_cells;
    JsonValue inserted = JsonValue::array();
    JsonValue retained = JsonValue::array();

    std::vector<std::string> ordered_ports = module.ports.keys();
    std::stable_sort(ordered_ports.begin(), ordered_ports.end(), [&](const std::string& a, const std::string& b) {
        bool a_non_in = (module.ports.at(a).direction != "input");
        bool b_non_in = (module.ports.at(b).direction != "input");
        return a_non_in < b_non_in;
    });

    for (const auto& port : ordered_ports) {
        RtlilPort& row = module.ports.at(port);
        for (size_t index = 0; index < row.bits.size(); ++index) {
            SigBit bit = row.bits[index];
            if (bit < 0) {
                if ((bit != SIG_0 && bit != SIG_1) || (row.direction != "input" && row.direction != "output")) {
                    throw std::runtime_error("Unknown or bidirectional constant root port: " + port);
                }
                SigBit external = ++maximum;
                row.bits[index] = external;
                if (auto* nn = module.netnames.find_ptr(port)) nn->bits[index] = external;
                std::string name = "ng_pad_" + std::to_string(inserted.as_arr().size());
                if (module.cells.contains(name) || new_cells.contains(name)) throw std::runtime_error("Generated I/O cell name collision");
                bool is_out = (row.direction == "output");
                std::string kind = is_out ? "OBUF" : "IBUF";
                SigBit inside = is_out ? bit : ++maximum;

                RtlilCell pc;
                pc.hide_name = 0;
                pc.type = kind;
                pc.port_directions.set("I", "input");
                pc.port_directions.set("O", "output");
                pc.connections.set("I", {is_out ? inside : external});
                pc.connections.set("O", {is_out ? external : inside});
                new_cells.set(name, std::move(pc));

                JsonValue ir = JsonValue::object();
                ir["port"] = port;
                ir["index"] = static_cast<int64_t>(index);
                ir["kind"] = kind;
                ir["original_constant"] = std::string(1, const_bit_char(bit));
                inserted.push_back(std::move(ir));
                continue;
            }
            if (padded.count(uf_find(make_key("", bit)))) {
                JsonValue rr = JsonValue::object();
                rr["port"] = port;
                rr["index"] = static_cast<int64_t>(index);
                retained.push_back(std::move(rr));
                continue;
            }
            if (auto rit = replace_map.find(bit); rit != replace_map.end()) {
                if (row.direction != "output") {
                    throw std::runtime_error("Aliased external inputs require explicit I/O policy");
                }
                SigBit external = ++maximum;
                row.bits[index] = external;
                if (auto* nn = module.netnames.find_ptr(port)) nn->bits[index] = external;
                std::string name = "ng_pad_" + std::to_string(inserted.as_arr().size());
                if (module.cells.contains(name) || new_cells.contains(name)) throw std::runtime_error("Generated I/O cell name collision");
                RtlilCell pc;
                pc.hide_name = 0;
                pc.type = "OBUF";
                pc.port_directions.set("I", "input");
                pc.port_directions.set("O", "output");
                pc.connections.set("I", {rit->second});
                pc.connections.set("O", {external});
                new_cells.set(name, std::move(pc));

                JsonValue ir = JsonValue::object();
                ir["port"] = port;
                ir["index"] = static_cast<int64_t>(index);
                ir["kind"] = "OBUF";
                ir["aliased_output"] = true;
                inserted.push_back(std::move(ir));
                continue;
            }
            if (row.direction != "input" && row.direction != "output") {
                throw std::runtime_error("Generic unbuffered inout is unsupported; use a native IOBUF: " + port);
            }
            SigBit inside = ++maximum;
            replace_map[bit] = inside;
            std::string name = "ng_pad_" + std::to_string(inserted.as_arr().size());
            if (module.cells.contains(name)) throw std::runtime_error("Generated I/O cell name collision");
            bool is_in = (row.direction == "input");
            RtlilCell pc;
            pc.hide_name = 0;
            pc.type = is_in ? "IBUF" : "OBUF";
            pc.port_directions.set("I", "input");
            pc.port_directions.set("O", "output");
            pc.connections.set("I", {is_in ? bit : inside});
            pc.connections.set("O", {is_in ? inside : bit});
            JsonValue ir = JsonValue::object();
            ir["port"] = port;
            ir["index"] = static_cast<int64_t>(index);
            ir["kind"] = pc.type;
            inserted.push_back(std::move(ir));
            new_cells.set(name, std::move(pc));
        }
    }

    for (auto [_, cell] : module.cells) {
        for (auto [__, bs] : cell.connections) {
            for (SigBit& b : bs) {
                auto it = replace_map.find(b);
                if (it != replace_map.end()) b = it->second;
            }
        }
    }

    for (const auto& nname : module.netnames.keys()) {
        RtlilNetname& row = module.netnames.at(nname);
        std::vector<SigBit> mapped_bits = row.bits;
        for (SigBit& b : mapped_bits) {
            auto it = replace_map.find(b);
            if (it != replace_map.end()) b = it->second;
        }
        if (module.ports.contains(nname)) {
            if (mapped_bits != row.bits) {
                std::string alias = "ng_internal_" + nname;
                if (module.netnames.contains(alias)) throw std::runtime_error("Internal alias name collision: " + alias);
                RtlilNetname internal = row;
                internal.bits = std::move(mapped_bits);
                row.attributes.erase("init");
                module.netnames.set(alias, std::move(internal));
            }
        } else {
            row.bits = std::move(mapped_bits);
        }
    }

    for (auto [cn, c] : new_cells) {
        module.cells.set(cn, std::move(c));
    }

    if (gsr_count == 0) {
        SigBit gsr_b = ++maximum;
        if (module.cells.contains("ng_gsr_vcc") || module.cells.contains("ng_gsr")) {
            throw std::runtime_error("Global-reset cell name collision");
        }
        RtlilCell vcc_c;
        vcc_c.hide_name = 0;
        vcc_c.type = "VCC";
        vcc_c.port_directions.set("V", "output");
        vcc_c.connections.set("V", {gsr_b});
        module.cells.set("ng_gsr_vcc", std::move(vcc_c));

        RtlilCell gsr_c;
        gsr_c.hide_name = 0;
        gsr_c.type = "GSR";
        gsr_c.port_directions.set("GSRI", "input");
        gsr_c.connections.set("GSRI", {gsr_b});
        module.cells.set("ng_gsr", std::move(gsr_c));
    }

    JsonValue info = JsonValue::object();
    info["inserted_pads"] = std::move(inserted);
    info["preserved_hierarchical_pads"] = std::move(retained);
    info["existing_global_reset_count"] = gsr_count;
    info["inserted_global_reset"] = (gsr_count == 0);
    return {std::move(design), std::move(info)};
}

// ============================================================================
// 2. gowin_netlist_format helpers
// ============================================================================
JsonValue bind_net_aliases(RtlilDesign& design, const OrderedMap<JsonValue>& bindings) {
    JsonValue records = JsonValue::array();
    for (const auto& [legacy, binding] : bindings) {
        if (!is_simple_ident(legacy) || legacy[0] == '$') {
            throw std::runtime_error("Unsupported legacy net name: " + legacy);
        }
        std::string mname = binding.get("module")->as_str();
        std::string src_net = binding.get("net")->as_str();
        RtlilModule& mod = design.mod_mut(mname);
        const RtlilNetname& target = mod.netnames.at(src_net);
        if (target.bits.size() != 1 || target.bits[0] < 0) {
            throw std::runtime_error("Legacy alias needs a scalar net: " + src_net);
        }
        SigBit bit = target.bits[0];
        for (const auto& [_, pr] : mod.ports) {
            for (SigBit pb : pr.bits) {
                if (pb == bit) throw std::runtime_error("Do not rename external ports through an internal alias");
            }
        }
        if (const RtlilNetname* ex = mod.netnames.find_ptr(legacy)) {
            if (ex->bits.size() != 1 || ex->bits[0] != bit) {
                throw std::runtime_error("Legacy name collision: " + legacy);
            }
        }
        RtlilNetname row = target;
        row.attributes.set("keep", "1");
        JsonValue old_arr = JsonValue::array();
        std::vector<std::string> to_del;
        for (const auto& [nn, nr] : mod.netnames) {
            if (nr.bits.size() == 1 && nr.bits[0] == bit) {
                to_del.push_back(nn);
                old_arr.push_back(nn);
            }
        }
        for (const auto& nn : to_del) mod.netnames.erase(nn);
        mod.netnames.set(legacy, std::move(row));

        JsonValue rec = JsonValue::object();
        rec["legacy"] = legacy;
        rec["module"] = mname;
        rec["source"] = src_net;
        rec["bit"] = bit;
        rec["replaced_scalar_aliases"] = std::move(old_arr);
        rec["connections_unchanged"] = true;
        records.push_back(std::move(rec));
    }
    return records;
}

JsonValue legalize_gw5a_aliases(RtlilDesign& design) {
    JsonValue changes = JsonValue::array();
    static const std::unordered_set<std::string> dl_aliases = {
        "DL", "DLE", "DLC", "DLP", "DLN", "DLNE", "DLNC", "DLNP", "DLNCE", "DLNPE"
    };
    for (const auto& [mn, mptr] : design.modules) {
        if (is_module_blackbox(*mptr)) continue;
        bool need_edit = false;
        for (const auto& [_, c] : mptr->cells) {
            if (c.type == "DFFE" || c.type == "INV" || dl_aliases.count(c.type)
                || ((c.type == "DLCE" || c.type == "DLPE") && c.connections.contains("CLK"))) {
                need_edit = true;
                break;
            }
        }
        if (!need_edit) continue;
        RtlilModule& row = design.mod_mut(mn);
        int32_t max_b = max_module_bit(row);
        std::vector<std::pair<std::string, RtlilCell>> extra_cells;
        for (auto [name, cell] : row.cells) {
            if (cell.type == "DFFE") {
                if (cell.connections.size() != 4 || !cell.connections.contains("D") || !cell.connections.contains("Q")
                    || !cell.connections.contains("CLK") || !cell.connections.contains("CE")) {
                    throw std::runtime_error("Unexpected DFFE alias interface: " + name);
                }
                std::string val = trim(cell.parameters.value_or("INIT", "0"));
                int init = val.empty() ? 0 : static_cast<int>(parse_bin_u64(val));
                if (init != 0 && init != 1) throw std::runtime_error("Invalid DFFE initializer");
                cell.type = "DFFRE";
                cell.connections.set("RESET", {SIG_0});
                cell.port_directions.set("RESET", "input");
                cell.parameters.set("INIT", std::to_string(init));

                JsonValue ch = JsonValue::object();
                ch["module"] = mn;
                ch["instance"] = name;
                ch["before"] = "DFFE";
                ch["after"] = "DFFRE";
                ch["reset"] = 0;
                ch["initial"] = init;
                changes.push_back(std::move(ch));
            } else if (cell.type == "INV") {
                if (cell.connections.size() != 2 || !cell.connections.contains("I") || !cell.connections.contains("O")) {
                    throw std::runtime_error("Unexpected INV alias interface: " + name);
                }
                auto in_b = cell.connections.at("I");
                auto out_b = cell.connections.at("O");
                cell.type = "LUT1";
                cell.connections.clear();
                cell.port_directions.clear();
                cell.connections.set("I0", std::move(in_b));
                cell.connections.set("F", std::move(out_b));
                cell.port_directions.set("I0", "input");
                cell.port_directions.set("F", "output");
                cell.parameters.set("INIT", "01");

                JsonValue ch = JsonValue::object();
                ch["module"] = mn;
                ch["instance"] = name;
                ch["before"] = "INV";
                ch["after"] = "LUT1";
                changes.push_back(std::move(ch));
            } else if (dl_aliases.count(cell.type) || ((cell.type == "DLCE" || cell.type == "DLPE") && cell.connections.contains("CLK"))) {
                std::string orig_t = cell.type;
                bool is_preset = (orig_t == "DLP" || orig_t == "DLNP" || orig_t == "DLPE" || orig_t == "DLNPE");
                bool is_neg = (orig_t == "DLN" || orig_t == "DLNE" || orig_t == "DLNC" || orig_t == "DLNP" || orig_t == "DLNCE" || orig_t == "DLNPE");
                auto d_b = cell.connections.at("D");
                auto q_b = cell.connections.at("Q");
                auto g_b = cell.connections.contains("G") ? cell.connections.at("G") : cell.connections.at("CLK");
                std::vector<SigBit> ge_b = {SIG_1};
                if (cell.connections.contains("GE")) ge_b = cell.connections.at("GE");
                else if (cell.connections.contains("CE")) ge_b = cell.connections.at("CE");
                std::vector<SigBit> ac_b = {SIG_0};
                if (is_preset && cell.connections.contains("PRESET")) ac_b = cell.connections.at("PRESET");
                else if (!is_preset && cell.connections.contains("CLEAR")) ac_b = cell.connections.at("CLEAR");

                if (is_neg && !g_b.empty()) {
                    SigBit inv_out = ++max_b;
                    std::string inv_nm = "ng_dl_ginv_" + std::to_string(extra_cells.size());
                    while (row.cells.contains(inv_nm)) inv_nm += "_x";
                    RtlilCell inv_c;
                    inv_c.hide_name = 0;
                    inv_c.type = "LUT1";
                    inv_c.parameters.set("INIT", "01");
                    inv_c.port_directions.set("I0", "input");
                    inv_c.port_directions.set("F", "output");
                    inv_c.connections.set("I0", {g_b[0]});
                    inv_c.connections.set("F", {inv_out});
                    extra_cells.emplace_back(std::move(inv_nm), std::move(inv_c));
                    g_b = {inv_out};
                }

                std::string def_init = is_preset ? "1" : "0";
                std::string val = trim(cell.parameters.value_or("INIT", def_init));
                int init = val.empty() ? (is_preset ? 1 : 0) : static_cast<int>(parse_bin_u64(val) & 1ULL);

                cell.type = is_preset ? "DLPE" : "DLCE";
                cell.connections.clear();
                cell.port_directions.clear();
                cell.connections.set("D", std::move(d_b));
                cell.connections.set("Q", std::move(q_b));
                cell.connections.set("G", std::move(g_b));
                cell.connections.set("GE", std::move(ge_b));
                cell.connections.set(is_preset ? "PRESET" : "CLEAR", std::move(ac_b));
                cell.port_directions.set("D", "input");
                cell.port_directions.set("Q", "output");
                cell.port_directions.set("G", "input");
                cell.port_directions.set("GE", "input");
                cell.port_directions.set(is_preset ? "PRESET" : "CLEAR", "input");
                cell.parameters.set("INIT", std::to_string(init));

                JsonValue ch = JsonValue::object();
                ch["module"] = mn;
                ch["instance"] = name;
                ch["before"] = orig_t;
                ch["after"] = cell.type;
                ch["initial"] = init;
                changes.push_back(std::move(ch));
            }
        }
        for (auto& [enm, ec] : extra_cells) {
            row.cells.set(enm, std::move(ec));
        }
    }
    return changes;
}

JsonValue complete_output_ports(RtlilDesign& design) {
    JsonValue changes = JsonValue::array();
    for (const auto& [name, mptr] : design.modules) {
        if (is_module_blackbox(*mptr)) continue;
        RtlilModule& module = design.mod_mut(name);
        int32_t maximum = max_module_bit(module);
        for (auto [instance, cell] : module.cells) {
            const auto* child_ptr = design.modules.find_ptr(cell.type);
            if (!child_ptr || !*child_ptr) continue;
            const RtlilModule& child = **child_ptr;
            for (const auto& [port, prow] : child.ports) {
                const auto* ex = cell.connections.find_ptr(port);
                if (ex && !ex->empty()) {
                    if (!is_module_blackbox(child) && prow.direction == "inout") {
                        bool any_neg = (ex->size() < prow.bits.size());
                        if (!any_neg) {
                            for (SigBit b : *ex) if (b < 0) { any_neg = true; break; }
                        }
                        if (any_neg) {
                            std::vector<SigBit> fixed_bits = *ex;
                            fixed_bits.resize(prow.bits.size(), SIG_Z);
                            std::vector<SigBit> alloc_bits;
                            for (SigBit& b : fixed_bits) {
                                if (b < 0) {
                                    b = ++maximum;
                                    alloc_bits.push_back(b);
                                }
                            }
                            std::string alias = "ng_unused_" + std::to_string(changes.as_arr().size());
                            if (module.netnames.contains(alias)) throw std::runtime_error("Unused port alias collision: " + alias);
                            RtlilNetname nn;
                            nn.hide_name = 0;
                            nn.bits = alloc_bits;
                            nn.attributes.set("keep", "1");
                            module.netnames.set(alias, std::move(nn));
                            cell.connections.set(port, std::move(fixed_bits));
                            cell.port_directions.set(port, "inout");

                            JsonValue ch = JsonValue::object();
                            ch["module"] = name;
                            ch["instance"] = instance;
                            ch["port"] = port;
                            changes.push_back(std::move(ch));
                        }
                    }
                    continue;
                }
                if (prow.direction != "output" && is_module_blackbox(child)) continue;
                if (prow.direction == "input") {
                    cell.connections.set(port, std::vector<SigBit>(prow.bits.size(), SIG_0));
                    cell.port_directions.set(port, "input");
                    JsonValue ch = JsonValue::object();
                    ch["module"] = name;
                    ch["instance"] = instance;
                    ch["port"] = port;
                    ch["tied_constant"] = "0";
                    changes.push_back(std::move(ch));
                    continue;
                }
                std::vector<SigBit> new_bits;
                new_bits.reserve(prow.bits.size());
                for (size_t i = 0; i < prow.bits.size(); ++i) new_bits.push_back(++maximum);
                std::string alias = "ng_unused_" + std::to_string(changes.as_arr().size());
                if (module.netnames.contains(alias)) throw std::runtime_error("Unused output alias collision: " + alias);
                RtlilNetname nn;
                nn.hide_name = 0;
                nn.bits = new_bits;
                nn.attributes.set("keep", "1");
                module.netnames.set(alias, std::move(nn));
                cell.connections.set(port, std::move(new_bits));
                cell.port_directions.set(port, prow.direction);

                JsonValue ch = JsonValue::object();
                ch["module"] = name;
                ch["instance"] = instance;
                ch["port"] = port;
                changes.push_back(std::move(ch));
            }
        }
    }
    return changes;
}

JsonValue name_unaliased_bits(RtlilDesign& design) {
    JsonValue records = JsonValue::array();
    for (const auto& [mn, mptr] : design.modules) {
        std::unordered_set<SigBit> covered;
        for (const auto& [_, nr] : mptr->netnames) for (SigBit b : nr.bits) if (b >= 0) covered.insert(b);
        for (const auto& [_, pr] : mptr->ports) for (SigBit b : pr.bits) if (b >= 0) covered.insert(b);

        std::vector<SigBit> missing;
        std::unordered_set<SigBit> seen_m;
        for (const auto& [_, c] : mptr->cells) {
            for (const auto& [__, bs] : c.connections) {
                for (SigBit b : bs) {
                    if (b >= 0 && !covered.count(b) && !seen_m.count(b)) {
                        seen_m.insert(b);
                        missing.push_back(b);
                    }
                }
            }
        }
        if (missing.empty()) continue;
        std::sort(missing.begin(), missing.end());

        RtlilModule& m = design.mod_mut(mn);
        std::unordered_set<std::string> occupied;
        for (const auto& [k, _] : m.netnames) occupied.insert(k);
        for (const auto& [k, _] : m.cells) occupied.insert(k);
        for (const auto& [k, _] : m.ports) occupied.insert(k);

        for (SigBit b : missing) {
            std::string base = "ng_json_bit_" + std::to_string(b);
            std::string name = base;
            int idx = 0;
            while (occupied.count(name)) {
                name = base + "_" + std::to_string(++idx);
            }
            occupied.insert(name);
            RtlilNetname nn;
            nn.hide_name = 0;
            nn.bits = {b};
            m.netnames.set(name, std::move(nn));
        }

        JsonValue rec = JsonValue::object();
        rec["module"] = mn;
        rec["bits_named"] = static_cast<int64_t>(missing.size());
        rec["connections_unchanged"] = true;
        records.push_back(std::move(rec));
    }
    return records;
}

std::pair<std::string, JsonValue> normalize_emitted_vg(std::string_view text) {
    const auto& library = gowin_prim_sim_formats();
    std::unordered_map<std::string, std::string> instances;
    std::string current_mod;
    JsonValue changed = JsonValue::array();
    std::vector<std::string> out_lines;

    auto is_zero_repeat = [](std::string_view val) {
        std::string s;
        for (char c : val) if (!std::isspace(static_cast<unsigned char>(c))) s.push_back(c);
        return s == "{0{1'b0}}" || s == "{0{1'B0}}";
    };

    for (const auto& raw_line : split_lines(text)) {
        std::string line = raw_line;
        if (starts_with(line, "module ")) {
            size_t i = 7;
            while (i < line.size() && std::isspace(static_cast<unsigned char>(line[i]))) ++i;
            size_t j = i;
            while (j < line.size() && !std::isspace(static_cast<unsigned char>(line[j])) && line[j] != '(') ++j;
            if (j > i) {
                current_mod = line.substr(i, j - i);
                instances.clear();
            }
        } else if (line.size() > 4 && line[0] == ' ' && line[1] == ' ' && line[2] != ' ') {
            size_t i = 2;
            size_t ce = i;
            while (ce < line.size() && !std::isspace(static_cast<unsigned char>(line[ce]))) ++ce;
            std::string ctype = line.substr(i, ce - i);
            if (ctype != "defparam" && ctype != "assign" && ctype != "wire" && ctype != "input" && ctype != "output" && ctype != "inout") {
                size_t k = ce;
                while (k < line.size() && std::isspace(static_cast<unsigned char>(line[k]))) ++k;
                size_t ie = k;
                if (ie < line.size() && line[ie] == '\\') {
                    ++ie;
                    while (ie < line.size() && !std::isspace(static_cast<unsigned char>(line[ie]))) ++ie;
                } else {
                    while (ie < line.size() && (std::isalnum(static_cast<unsigned char>(line[ie])) || line[ie] == '_' || line[ie] == '$')) ++ie;
                }
                size_t po = ie;
                while (po < line.size() && std::isspace(static_cast<unsigned char>(line[po]))) ++po;
                if (ie > k && po < line.size() && line[po] == '(') {
                    instances[line.substr(k, ie - k)] = ctype;
                }
            }
        }

        // Check `^(\s*defparam\s+)(.*?)\s*\.(\w+)\s*=\s*([^;]+);\s*$`
        std::string rtr = rtrim(line);
        size_t ind = 0;
        while (ind < rtr.size() && std::isspace(static_cast<unsigned char>(rtr[ind]))) ++ind;
        if (starts_with(std::string_view(rtr).substr(ind), "defparam") && !rtr.empty() && rtr.back() == ';') {
            size_t dp_end = ind + 8;
            if (dp_end < rtr.size() && std::isspace(static_cast<unsigned char>(rtr[dp_end]))) {
                while (dp_end < rtr.size() && std::isspace(static_cast<unsigned char>(rtr[dp_end]))) ++dp_end;
                std::string pfx = rtr.substr(0, dp_end);
                std::string rest = rtr.substr(dp_end, rtr.size() - dp_end - 1);
                size_t eq = rest.find('=');
                if (eq != std::string::npos) {
                    std::string lhs = rtrim(std::string_view(rest).substr(0, eq));
                    std::string val = trim(std::string_view(rest).substr(eq + 1));
                    size_t dot = lhs.rfind('.');
                    if (dot != std::string::npos) {
                        std::string inst_raw = lhs.substr(0, dot);
                        std::string inst = trim(inst_raw);
                        std::string key = trim(std::string_view(lhs).substr(dot + 1));
                        std::string cell_type;
                        if (auto it = instances.find(inst); it != instances.end()) cell_type = it->second;

                        const std::string* def_ptr = nullptr;
                        if (const auto* cm = library.find_ptr(cell_type)) {
                            def_ptr = cm->find_ptr(key);
                        }

                        if (is_zero_repeat(val) && (!def_ptr || starts_with(trim(*def_ptr), "\""))) {
                            JsonValue ch = JsonValue::object();
                            ch["module"] = current_mod;
                            ch["instance"] = inst;
                            ch["cell"] = cell_type.empty() ? JsonValue(nullptr) : JsonValue(cell_type);
                            ch["parameter"] = key;
                            ch["before"] = val;
                            ch["after"] = JsonValue(nullptr);
                            ch["policy"] = "Omit empty/unset vendor string property; use native primitive default";
                            changed.push_back(std::move(ch));
                            continue;
                        }

                        if (def_ptr != nullptr) {
                            std::string def_s = trim(*def_ptr);
                            bool all_dig = !def_s.empty();
                            for (char c : def_s) if (!std::isdigit(static_cast<unsigned char>(c))) { all_dig = false; break; }

                            int sized_w = -1;
                            char sized_b = 'd';
                            size_t q = def_s.find('\'');
                            if (!all_dig && q != std::string::npos && q > 0 && q + 2 <= def_s.size()) {
                                bool wok = true;
                                for (size_t z = 0; z < q; ++z) if (!std::isdigit(static_cast<unsigned char>(def_s[z]))) wok = false;
                                char bc = def_s[q + 1];
                                if (wok && std::strchr("bBoOdDhH", bc) != nullptr) {
                                    bool dok = (q + 2 < def_s.size());
                                    for (size_t z = q + 2; z < def_s.size(); ++z) {
                                        if (!(std::isxdigit(static_cast<unsigned char>(def_s[z])) || std::strchr("xXzZ_", def_s[z]) != nullptr)) dok = false;
                                    }
                                    if (dok) {
                                        sized_w = std::stoi(def_s.substr(0, q));
                                        sized_b = bc;
                                    }
                                }
                            }

                            if (all_dig || sized_w > 0) {
                                std::string repl = format_verilog_Parameter_bits(val, all_dig ? 0 : sized_w, sized_b);
                                if (repl != val) {
                                    JsonValue ch = JsonValue::object();
                                    ch["module"] = current_mod;
                                    ch["instance"] = inst;
                                    ch["cell"] = cell_type;
                                    ch["parameter"] = key;
                                    ch["before"] = val;
                                    ch["after"] = repl;
                                    changed.push_back(std::move(ch));
                                    line = pfx + rtrim(inst_raw) + (starts_with(inst, "\\") ? " " : "") + "." + key + " = " + repl + ";";
                                }
                            }
                        }
                    }
                }
            }
        }
        out_lines.push_back(std::move(line));
    }

    // Convert any single-active-input inverter LUT1..LUT4 driving DFF*.CLK into native INV so VLGPARSE
    // merges clock inversion into hardware CFU cls falling-edge polarity instead of fabric LUT routing.
    int64_t clk_inv_converted = 0;
    {
        auto parse_lut_init_val = [](std::string_view v, uint16_t& out_u16) -> bool {
            std::string s = to_lower_ascii(trim(v));
            if (s.empty()) return false;
            uint64_t acc = 0;
            size_t q = s.find('\'');
            if (q != std::string::npos && q + 2 <= s.size()) {
                char bc = s[q + 1];
                int base = (bc == 'h') ? 16 : (bc == 'b') ? 2 : (bc == 'o') ? 8 : (bc == 'd') ? 10 : 0;
                if (!base) return false;
                for (size_t i = q + 2; i < s.size(); ++i) {
                    char ch = s[i];
                    if (ch == '_') continue;
                    int d = (ch >= '0' && ch <= '9') ? (ch - '0') : (ch >= 'a' && ch <= 'f') ? (10 + ch - 'a') : -1;
                    if (d < 0 || d >= base) return false;
                    acc = (acc * static_cast<uint64_t>(base)) + static_cast<uint64_t>(d);
                }
            } else {
                for (char ch : s) {
                    if (!std::isdigit(static_cast<unsigned char>(ch))) return false;
                    acc = acc * 10ULL + static_cast<uint64_t>(ch - '0');
                }
            }
            out_u16 = static_cast<uint16_t>(acc & 0xFFFFU);
            return true;
        };
        struct VgPortLn { std::string pin, arg, indent; size_t ln = 0; bool comma = false; };
        struct VgCellRec {
            std::string ctype, inst_raw, inst_norm;
            size_t open_ln = 0, close_ln = 0;
            std::vector<VgPortLn> ports;
        };
        auto norm_id = [](std::string_view s) -> std::string {
            std::string t = trim(s);
            if (!t.empty() && t[0] == '\\') return t.substr(1);
            return t;
        };

        std::vector<std::string> final_lines;
        final_lines.reserve(out_lines.size() + 32);
        size_t idx = 0;
        int split_seq = 0;
        while (idx < out_lines.size()) {
            if (!starts_with(ltrim(out_lines[idx]), "module ")) {
                final_lines.push_back(out_lines[idx++]);
                continue;
            }
            size_t mod_s = idx;
            size_t mod_e = idx;
            while (mod_e < out_lines.size() && trim(out_lines[mod_e]) != "endmodule") ++mod_e;
            if (mod_e >= out_lines.size()) {
                while (idx < out_lines.size()) final_lines.push_back(out_lines[idx++]);
                break;
            }

            std::vector<std::string> mlines(out_lines.begin() + mod_s, out_lines.begin() + mod_e + 1);
            std::vector<VgCellRec> mcells;
            std::unordered_map<std::string, std::pair<size_t, std::string>> lut1_init_dp; // inst_norm -> (mlines_idx, val)
            std::unordered_set<std::string> port_out_wires;
            size_t first_inst_ln = mlines.size() - 1;

            for (size_t li = 1; li + 1 < mlines.size(); ++li) {
                std::string tr = trim(mlines[li]);
                if (tr.empty() || starts_with(tr, "//") || starts_with(tr, "(*")) continue;
                if (starts_with(tr, "output ") || starts_with(tr, "inout ")) {
                    for (const auto& tk : tokenize_verilog(tr)) {
                        if (tk != "output" && tk != "inout" && tk != "wire" && tk != ";" && tk != "," && tk != "[" && tk != "]" && tk != ":") {
                            port_out_wires.insert(norm_id(tk));
                        }
                    }
                    continue;
                }
                if (starts_with(tr, "assign ")) {
                    size_t eq = tr.find('=');
                    if (eq != std::string::npos) {
                        for (const auto& tk : tokenize_verilog(std::string_view(tr).substr(eq + 1))) {
                            port_out_wires.insert(norm_id(tk));
                        }
                    }
                    continue;
                }
                if (starts_with(tr, "defparam ") && tr.back() == ';') {
                    std::string rest = trim(std::string_view(tr).substr(9, tr.size() - 10));
                    size_t eq = rest.find('=');
                    if (eq != std::string::npos) {
                        std::string lhs = rtrim(std::string_view(rest).substr(0, eq));
                        std::string rhs = trim(std::string_view(rest).substr(eq + 1));
                        if (ends_with(lhs, ".INIT")) {
                            std::string in_norm = norm_id(std::string_view(lhs).substr(0, lhs.size() - 5));
                            lut1_init_dp[in_norm] = {li, rhs};
                        }
                    }
                    continue;
                }
                if (tr.back() == '(' && !starts_with(tr, "input ") && !starts_with(tr, "wire ")) {
                    auto ws = split_ws(std::string_view(tr).substr(0, tr.size() - 1));
                    if (ws.size() == 2) {
                        size_t cl = li + 1;
                        while (cl + 1 < mlines.size() && trim(mlines[cl]) != ");") ++cl;
                        if (cl + 1 < mlines.size() && trim(mlines[cl]) == ");") {
                            if (li < first_inst_ln) first_inst_ln = li;
                            VgCellRec cr;
                            cr.ctype = ws[0];
                            cr.inst_raw = ws[1];
                            cr.inst_norm = norm_id(ws[1]);
                            cr.open_ln = li;
                            cr.close_ln = cl;
                            for (size_t pl = li + 1; pl < cl; ++pl) {
                                std::string ptrm = trim(mlines[pl]);
                                if (ptrm.empty() || ptrm[0] != '.') continue;
                                size_t lp = ptrm.find('('), rp = ptrm.rfind(')');
                                if (lp == std::string::npos || rp == std::string::npos || rp <= lp) continue;
                                size_t dpos = mlines[pl].find('.');
                                cr.ports.push_back({
                                    trim(std::string_view(ptrm).substr(1, lp - 1)),
                                    trim(std::string_view(ptrm).substr(lp + 1, rp - lp - 1)),
                                    (dpos != std::string::npos) ? mlines[pl].substr(0, dpos) : "    ",
                                    pl,
                                    ends_with(ptrm, ",")
                                });
                            }
                            mcells.push_back(std::move(cr));
                            li = cl;
                        }
                    }
                }
            }

            std::vector<std::string> extra_wire_decls, extra_inv_cells;
            std::unordered_set<size_t> drop_mlines;
            auto is_const0_w = [](const std::string& w) { return w == "GND" || w == "1'b0" || w == "1'B0" || w == "0"; };
            auto is_const1_w = [](const std::string& w) { return w == "VCC" || w == "1'b1" || w == "1'B1" || w == "1"; };

            for (const auto& c : mcells) {
                if (c.ctype != "LUT1" && c.ctype != "LUT2" && c.ctype != "LUT3" && c.ctype != "LUT4") continue;
                int k = c.ctype.back() - '0';
                auto dit = lut1_init_dp.find(c.inst_norm);
                uint16_t init_v = 0;
                if (dit == lut1_init_dp.end() || !parse_lut_init_val(dit->second.second, init_v)) continue;
                int n_tt = (1 << k);
                init_v &= static_cast<uint16_t>((1U << n_tt) - 1U);

                const VgPortLn* p_in[4]{nullptr, nullptr, nullptr, nullptr};
                const VgPortLn* p_f = nullptr;
                for (const auto& vp : c.ports) {
                    if (vp.pin == "F") p_f = &vp;
                    else if (vp.pin.size() == 2 && vp.pin[0] == 'I' && vp.pin[1] >= '0' && vp.pin[1] < ('0' + k)) {
                        p_in[vp.pin[1] - '0'] = &vp;
                    }
                }
                if (!p_f || p_f->arg.empty() || p_f->arg.find('{') != std::string::npos) continue;
                bool ok_pins = true;
                for (int j = 0; j < k; ++j) if (!p_in[j] || p_in[j]->arg.empty() || p_in[j]->arg.find('{') != std::string::npos) ok_pins = false;
                if (!ok_pins) continue;

                // Determine active non-constant input wire(s) of LUTk under init_v
                std::string src_raw, src_norm;
                bool multi_var = false;
                for (int j = 0; j < k; ++j) {
                    std::string wn = norm_id(p_in[j]->arg);
                    if (is_const0_w(wn) || is_const1_w(wn)) continue;
                    bool dep = false;
                    for (int m = 0; m < n_tt; ++m) {
                        if (((init_v >> m) & 1U) != ((init_v >> (m ^ (1 << j))) & 1U)) { dep = true; break; }
                    }
                    if (!dep) continue;
                    if (src_norm.empty()) { src_norm = wn; src_raw = p_in[j]->arg; }
                    else if (src_norm != wn) { multi_var = true; break; }
                }
                if (multi_var || src_norm.empty()) continue;

                auto eval_lut = [&](int x_val) -> int {
                    int addr = 0;
                    for (int j = 0; j < k; ++j) {
                        std::string wn = norm_id(p_in[j]->arg);
                        int bit = is_const1_w(wn) ? 1 : (is_const0_w(wn) ? 0 : ((wn == src_norm) ? x_val : 0));
                        addr |= (bit << j);
                    }
                    return (init_v >> addr) & 1;
                };
                if (eval_lut(0) != 1 || eval_lut(1) != 0) continue;

                std::string f_norm = norm_id(p_f->arg);
                std::vector<const VgPortLn*> dff_clk_uses;
                bool has_non_dff_clk_use = port_out_wires.count(f_norm) > 0;
                for (const auto& sc : mcells) {
                    if (&sc == &c) continue;
                    bool is_dff = starts_with(sc.ctype, "DFF");
                    for (const auto& sp : sc.ports) {
                        if (sp.arg.find(f_norm) == std::string::npos && norm_id(sp.arg) != f_norm) continue;
                        if (norm_id(sp.arg) == f_norm) {
                            if (is_dff && sp.pin == "CLK") dff_clk_uses.push_back(&sp);
                            else has_non_dff_clk_use = true;
                        } else {
                            for (const auto& tk : tokenize_verilog(sp.arg)) {
                                if (norm_id(tk) == f_norm) { has_non_dff_clk_use = true; break; }
                            }
                        }
                    }
                }
                if (dff_clk_uses.empty()) continue;

                auto fmt_vg_net = [](const std::string& w) -> std::string {
                    return (!w.empty() && w.front() == '\\' && !std::isspace(static_cast<unsigned char>(w.back()))) ? (w + " ") : w;
                };
                if (!has_non_dff_clk_use) {
                    std::string lead = mlines[c.open_ln].substr(0, mlines[c.open_ln].find(c.ctype));
                    mlines[c.open_ln] = lead + "INV " + c.inst_raw + " (";
                    for (size_t pl = c.open_ln + 1; pl < c.close_ln; ++pl) drop_mlines.insert(pl);
                    drop_mlines.erase(p_in[0]->ln);
                    drop_mlines.erase(p_f->ln);
                    if (p_in[0]->ln < p_f->ln) {
                        mlines[p_in[0]->ln] = p_in[0]->indent + ".I(" + fmt_vg_net(src_raw) + "),";
                        mlines[p_f->ln]     = p_f->indent     + ".O(" + fmt_vg_net(p_f->arg) + ")";
                    } else {
                        mlines[p_f->ln]     = p_f->indent     + ".O(" + fmt_vg_net(p_f->arg) + "),";
                        mlines[p_in[0]->ln] = p_in[0]->indent + ".I(" + fmt_vg_net(src_raw) + ")";
                    }
                    drop_mlines.insert(dit->second.first);
                    ++clk_inv_converted;
                } else {
                    ++split_seq;
                    std::string w_nm = "ng_clkinv_w_" + std::to_string(split_seq);
                    std::string i_nm = "ng_clkinv_u_" + std::to_string(split_seq);
                    extra_wire_decls.push_back("  wire " + w_nm + ";");
                    extra_inv_cells.push_back("  INV " + i_nm + " (\n    .I(" + fmt_vg_net(src_raw) + "),\n    .O(" + w_nm + ")\n  );");
                    for (const VgPortLn* up : dff_clk_uses) {
                        mlines[up->ln] = up->indent + ".CLK(" + w_nm + ")" + (up->comma ? "," : "");
                    }
                    ++clk_inv_converted;
                }
            }

            for (size_t li = 0; li < mlines.size(); ++li) {
                if (li == first_inst_ln && !extra_wire_decls.empty()) {
                    for (const auto& wd : extra_wire_decls) final_lines.push_back(wd);
                }
                if (li + 1 == mlines.size() && !extra_inv_cells.empty()) {
                    for (const auto& ic : extra_inv_cells) final_lines.push_back(ic);
                }
                if (!drop_mlines.count(li)) final_lines.push_back(std::move(mlines[li]));
            }
            idx = mod_e + 1;
        }
        out_lines = std::move(final_lines);
    }

    JsonValue rec = JsonValue::object();
    rec["parameter_format_changes"] = std::move(changed);
    rec["clock_inverters_emitted"] = clk_inv_converted;
    rec["primitive_library_sha256"] = effective_prim_sim_sha256();
    return {join_strings(out_lines, "\n") + "\n", std::move(rec)};
}

// ============================================================================
// 3. Combinational Arrival & Remaining Delay + optimize_native_json
// ============================================================================
struct CombTimingResult {
    std::unordered_map<SigBit, double> net_arr;
    std::unordered_map<SigBit, double> net_rem;
};

CombTimingResult compute_comb_arr_and_rem(const RtlilModule& module) {
    CombTimingResult res;
    std::unordered_map<SigBit, std::pair<std::string, std::string>> drivers;
    std::unordered_map<SigBit, int> load_cnt;

    for (const auto& [n, c] : module.cells) {
        for (const auto& [p, bits] : c.connections) {
            std::string d = c.port_directions.value_or(p, "");
            for (SigBit b : bits) {
                if (b < 0) continue;
                if (d == "output") drivers[b] = {n, p};
                else if (d == "input" || d == "inout") load_cnt[b] += 1;
            }
        }
    }

    static const std::unordered_set<std::string> comb_types = {
        "LUT1","LUT2","LUT3","LUT4","MUX2_LUT5","MUX2_LUT6","MUX2_LUT7","ALU",
        "RAM16S1","RAM16S2","RAM16S4","RAM16SDP1","RAM16SDP2","RAM16SDP4","ROM16"
    };
    auto is_ssram_comb_in = [](const std::string& ctype, const std::string& pin) -> bool {
        if (starts_with(ctype, "RAM16SDP")) return pin == "RAD";
        if (starts_with(ctype, "RAM16S") || ctype == "ROM16") return pin == "AD";
        return true;
    };
    std::vector<std::string> comb_cells;
    std::unordered_set<std::string> comb_set;
    for (const auto& [n, c] : module.cells) {
        if (comb_types.count(c.type)) {
            comb_cells.push_back(n);
            comb_set.insert(n);
        }
    }
    if (comb_cells.empty()) return res;

    std::unordered_set<SigBit> ded_zero_bits;
    for (const auto& [_, c] : module.cells) {
        if (c.type == "GND" || c.type == "VCC") {
            for (const auto& [__, bs] : c.connections) {
                for (SigBit b : bs) if (b >= 0) ded_zero_bits.insert(b);
            }
        }
    }
    for (const auto& n : comb_cells) {
        const RtlilCell& c = module.cells.at(n);
        if (starts_with(c.type, "MUX2_LUT")) {
            for (const char* p : {"I0", "I1"}) {
                if (const auto* bs = c.connections.find_ptr(p); bs && !bs->empty() && (*bs)[0] >= 0) {
                    ded_zero_bits.insert((*bs)[0]);
                }
            }
        } else if (c.type == "ALU") {
            if (const auto* bs = c.connections.find_ptr("CIN"); bs && !bs->empty() && (*bs)[0] >= 0) {
                auto dit = drivers.find((*bs)[0]);
                if (dit != drivers.end() && dit->second.second == "COUT") ded_zero_bits.insert((*bs)[0]);
            }
        }
    }

    auto wire_ps = [&](SigBit b) -> double {
        if (b < 0 || ded_zero_bits.count(b)) return 0.0;
        int fo = load_cnt[b];
        return 420.0 + 140.0 * std::log2(1.0 + static_cast<double>(std::min(std::max(fo, 1), 256)))
             + 26.0 * static_cast<double>(std::min(std::max(fo - 1, 0), 80));
    };

    std::unordered_map<std::string, int> in_deg;
    std::unordered_map<std::string, std::vector<std::string>> succ;
    for (const auto& n : comb_cells) {
        const RtlilCell& c = module.cells.at(n);
        std::unordered_set<std::string> preds;
        for (const auto& [p, bits] : c.connections) {
            if (c.port_directions.value_or(p, "") == "input" && is_ssram_comb_in(c.type, p)) {
                for (SigBit b : bits) {
                    if (b >= 0) {
                        auto dit = drivers.find(b);
                        if (dit != drivers.end() && comb_set.count(dit->second.first) && dit->second.first != n) {
                            preds.insert(dit->second.first);
                        }
                    }
                }
            }
        }
        in_deg[n] = static_cast<int>(preds.size());
        for (const auto& pn : preds) succ[pn].push_back(n);
    }

    std::vector<std::string> z_init;
    for (const auto& n : comb_cells) if (in_deg[n] == 0) z_init.push_back(n);
    std::sort(z_init.begin(), z_init.end());
    std::deque<std::string> q(z_init.begin(), z_init.end());
    std::vector<std::string> topo;
    topo.reserve(comb_cells.size());
    while (!q.empty()) {
        std::string u = std::move(q.front());
        q.pop_front();
        topo.push_back(u);
        if (auto sit = succ.find(u); sit != succ.end()) {
            for (const auto& v : sit->second) {
                if (--in_deg[v] == 0) q.push_back(v);
            }
        }
    }
    if (topo.size() < comb_cells.size()) {
        std::unordered_set<std::string> seen(topo.begin(), topo.end());
        std::vector<std::string> rem = comb_cells;
        std::sort(rem.begin(), rem.end());
        for (const auto& n : rem) if (!seen.count(n)) topo.push_back(n);
    }

    for (const auto& [_, pr] : module.ports) {
        if (pr.direction == "input" || pr.direction == "inout") {
            for (SigBit b : pr.bits) if (b >= 0) res.net_arr[b] = 360.0;
        }
    }
    for (const auto& [n, c] : module.cells) {
        if (comb_set.count(n) || c.type == "GND" || c.type == "VCC") continue;
        double t_co = (c.type.find("SDP") != std::string::npos || c.type.find("BSRAM") != std::string::npos || c.type.find("DPB") != std::string::npos) ? 1850.0
                    : ((c.type.find("MULT") != std::string::npos || c.type.find("ALU54") != std::string::npos) ? 2050.0 : 360.0);
        for (const auto& [p, bits] : c.connections) {
            if (c.port_directions.value_or(p, "") == "output") {
                for (SigBit b : bits) {
                    if (b >= 0 && t_co > res.net_arr[b]) res.net_arr[b] = t_co;
                }
            }
        }
    }

    for (const auto& n : topo) {
        const RtlilCell& c = module.cells.at(n);
        if (c.type == "LUT1" || c.type == "LUT2" || c.type == "LUT3" || c.type == "LUT4") {
            int k = c.type.back() - '0';
            double out_a = 0.0;
            for (int j = 0; j < k; ++j) {
                SigBit b = c.connections.at("I" + std::to_string(j))[0];
                double cand = ((b >= 0) ? (res.net_arr[b] + wire_ps(b)) : 0.0) + OPT_LUT_PIN_PS[j];
                out_a = std::max(out_a, cand);
            }
            SigBit ob = c.connections.at("F")[0];
            if (ob >= 0 && out_a > res.net_arr[ob]) res.net_arr[ob] = out_a;
        } else if (starts_with(c.type, "MUX2_LUT")) {
            SigBit b0 = c.connections.at("I0")[0];
            SigBit b1 = c.connections.at("I1")[0];
            SigBit bs = c.connections.at("S0")[0];
            double out_a = std::max({
                ((b0 >= 0) ? res.net_arr[b0] : 0.0) + 195.0,
                ((b1 >= 0) ? res.net_arr[b1] : 0.0) + 195.0,
                ((bs >= 0) ? (res.net_arr[bs] + wire_ps(bs)) : 0.0) + 227.0
            });
            SigBit ob = c.connections.at("O")[0];
            if (ob >= 0 && out_a > res.net_arr[ob]) res.net_arr[ob] = out_a;
        } else if (c.type == "ALU") {
            double base_a = 0.0;
            for (const char* p : {"CIN", "I0", "I1", "I3"}) {
                if (const auto* bs = c.connections.find_ptr(p); bs && !bs->empty() && (*bs)[0] >= 0) {
                    SigBit b = (*bs)[0];
                    base_a = std::max(base_a, res.net_arr[b] + wire_ps(b) + ((std::strcmp(p, "CIN") == 0) ? 45.0 : 300.0));
                }
            }
            for (auto [p, ex] : {std::pair{"COUT", 0.0}, std::pair{"SUM", 260.0}}) {
                if (const auto* bs = c.connections.find_ptr(p); bs && !bs->empty() && (*bs)[0] >= 0) {
                    SigBit b = (*bs)[0];
                    if (base_a + ex > res.net_arr[b]) res.net_arr[b] = base_a + ex;
                }
            }
        } else if (starts_with(c.type, "RAM16") || c.type == "ROM16") {
            const char* apin = starts_with(c.type, "RAM16SDP") ? "RAD" : "AD";
            double out_a = starts_with(c.type, "RAM16") ? 560.0 : 0.0;
            if (const auto* abs = c.connections.find_ptr(apin)) {
                for (SigBit b : *abs) {
                    if (b >= 0) out_a = std::max(out_a, res.net_arr[b] + wire_ps(b) + 520.0);
                }
            }
            if (const auto* dbs = c.connections.find_ptr("DO")) {
                for (SigBit ob : *dbs) {
                    if (ob >= 0 && out_a > res.net_arr[ob]) res.net_arr[ob] = out_a;
                }
            }
        }
    }

    for (auto it = topo.rbegin(); it != topo.rend(); ++it) {
        const RtlilCell& c = module.cells.at(*it);
        if (c.type == "LUT1" || c.type == "LUT2" || c.type == "LUT3" || c.type == "LUT4") {
            SigBit ob = c.connections.at("F")[0];
            double r_after = (ob >= 0) ? (res.net_rem[ob] + wire_ps(ob)) : 0.0;
            int k = c.type.back() - '0';
            for (int j = 0; j < k; ++j) {
                SigBit b = c.connections.at("I" + std::to_string(j))[0];
                if (b >= 0) {
                    double cand = OPT_LUT_PIN_PS[j] + r_after;
                    if (cand > res.net_rem[b]) res.net_rem[b] = cand;
                }
            }
        } else if (starts_with(c.type, "MUX2_LUT")) {
            SigBit ob = c.connections.at("O")[0];
            double r_after = (ob >= 0) ? (res.net_rem[ob] + wire_ps(ob)) : 0.0;
            for (auto [p, dps] : {std::pair{"I0", 195.0}, std::pair{"I1", 195.0}, std::pair{"S0", 227.0}}) {
                SigBit b = c.connections.at(p)[0];
                if (b >= 0) {
                    double cand = dps + r_after;
                    if (cand > res.net_rem[b]) res.net_rem[b] = cand;
                }
            }
        } else if (c.type == "ALU") {
            double r_after = 0.0;
            for (auto [p, ex] : {std::pair{"COUT", 0.0}, std::pair{"SUM", 260.0}}) {
                if (const auto* bs = c.connections.find_ptr(p); bs && !bs->empty() && (*bs)[0] >= 0) {
                    SigBit b = (*bs)[0];
                    r_after = std::max(r_after, ex + res.net_rem[b] + wire_ps(b));
                }
            }
            for (auto [p, dps] : {std::pair{"CIN", 45.0}, std::pair{"I0", 300.0}, std::pair{"I1", 300.0}, std::pair{"I3", 300.0}}) {
                if (const auto* bs = c.connections.find_ptr(p); bs && !bs->empty() && (*bs)[0] >= 0) {
                    SigBit b = (*bs)[0];
                    double cand = dps + r_after;
                    if (cand > res.net_rem[b]) res.net_rem[b] = cand;
                }
            }
        } else if (starts_with(c.type, "RAM16") || c.type == "ROM16") {
            double r_after = 0.0;
            if (const auto* dbs = c.connections.find_ptr("DO")) {
                for (SigBit ob : *dbs) {
                    if (ob >= 0) r_after = std::max(r_after, res.net_rem[ob] + wire_ps(ob));
                }
            }
            const char* apin = starts_with(c.type, "RAM16SDP") ? "RAD" : "AD";
            if (const auto* abs = c.connections.find_ptr(apin)) {
                for (SigBit b : *abs) {
                    if (b >= 0) {
                        double cand = 520.0 + r_after;
                        if (cand > res.net_rem[b]) res.net_rem[b] = cand;
                    }
                }
            }
        }
    }
    return res;
}

std::pair<int64_t, int64_t> optimize_module_lut_pins(RtlilModule& module, bool permute_only = false) {
    std::unordered_map<SigBit, std::pair<std::string, std::string>> drivers;
    std::unordered_map<SigBit, int> load_cnt;
    for (const auto& [n, c] : module.cells) {
        for (const auto& [p, bits] : c.connections) {
            std::string d = c.port_directions.value_or(p, "");
            for (SigBit b : bits) {
                if (b < 0) continue;
                if (d == "output") drivers[b] = {n, p};
                else if (d == "input" || d == "inout") load_cnt[b] += 1;
            }
        }
    }

    static const std::unordered_set<std::string> comb_types = {
        "LUT1","LUT2","LUT3","LUT4","MUX2_LUT5","MUX2_LUT6","MUX2_LUT7","ALU",
        "RAM16S1","RAM16S2","RAM16S4","RAM16SDP1","RAM16SDP2","RAM16SDP4","ROM16"
    };
    auto is_ssram_comb_in = [](const std::string& ctype, const std::string& pin) -> bool {
        if (starts_with(ctype, "RAM16SDP")) return pin == "RAD";
        if (starts_with(ctype, "RAM16S") || ctype == "ROM16") return pin == "AD";
        return true;
    };
    std::vector<std::string> comb_cells;
    std::unordered_set<std::string> comb_set;
    for (const auto& [n, c] : module.cells) {
        if (comb_types.count(c.type)) { comb_cells.push_back(n); comb_set.insert(n); }
    }
    if (comb_cells.empty()) return {0, 0};

    std::unordered_set<SigBit> ded_zero_bits;
    for (const auto& [_, c] : module.cells) {
        if (c.type == "GND" || c.type == "VCC") {
            for (const auto& [__, bs] : c.connections) for (SigBit b : bs) if (b >= 0) ded_zero_bits.insert(b);
        }
    }
    for (const auto& n : comb_cells) {
        const RtlilCell& c = module.cells.at(n);
        if (starts_with(c.type, "MUX2_LUT")) {
            for (const char* p : {"I0", "I1"}) {
                if (const auto* bs = c.connections.find_ptr(p); bs && !bs->empty() && (*bs)[0] >= 0) ded_zero_bits.insert((*bs)[0]);
            }
        } else if (c.type == "ALU") {
            if (const auto* bs = c.connections.find_ptr("CIN"); bs && !bs->empty() && (*bs)[0] >= 0) {
                auto dit = drivers.find((*bs)[0]);
                if (dit != drivers.end() && dit->second.second == "COUT") ded_zero_bits.insert((*bs)[0]);
            }
        }
    }

    auto wire_ps = [&](SigBit b) -> double {
        if (b < 0 || ded_zero_bits.count(b)) return 0.0;
        int fo = load_cnt[b];
        return 480.0 + 115.0 * std::log2(1.0 + static_cast<double>(std::min(std::max(fo, 1), 256)))
             + (fo > 32 ? 220.0 : 0.0)
             + (fo > 128 ? 350.0 : 0.0);
    };

    std::unordered_map<std::string, int> in_deg;
    std::unordered_map<std::string, std::vector<std::string>> succ;
    for (const auto& n : comb_cells) {
        const RtlilCell& c = module.cells.at(n);
        std::unordered_set<std::string> preds;
        for (const auto& [p, bits] : c.connections) {
            if (c.port_directions.value_or(p, "") == "input" && is_ssram_comb_in(c.type, p)) {
                for (SigBit b : bits) {
                    if (b >= 0) {
                        auto dit = drivers.find(b);
                        if (dit != drivers.end() && comb_set.count(dit->second.first) && dit->second.first != n) preds.insert(dit->second.first);
                    }
                }
            }
        }
        in_deg[n] = static_cast<int>(preds.size());
        for (const auto& pn : preds) succ[pn].push_back(n);
    }

    std::vector<std::string> z_init;
    for (const auto& n : comb_cells) if (in_deg[n] == 0) z_init.push_back(n);
    std::sort(z_init.begin(), z_init.end());
    std::deque<std::string> q(z_init.begin(), z_init.end());
    std::vector<std::string> topo;
    topo.reserve(comb_cells.size());
    while (!q.empty()) {
        std::string u = std::move(q.front()); q.pop_front();
        topo.push_back(u);
        if (auto sit = succ.find(u); sit != succ.end()) {
            for (const auto& v : sit->second) if (--in_deg[v] == 0) q.push_back(v);
        }
    }
    if (topo.size() < comb_cells.size()) {
        std::unordered_set<std::string> seen(topo.begin(), topo.end());
        std::vector<std::string> rem = comb_cells;
        std::sort(rem.begin(), rem.end());
        for (const auto& n : rem) if (!seen.count(n)) topo.push_back(n);
    }

    std::unordered_map<SigBit, double> net_arr;
    for (const auto& [_, pr] : module.ports) {
        if (pr.direction == "input" || pr.direction == "inout") {
            for (SigBit b : pr.bits) if (b >= 0) net_arr[b] = 360.0;
        }
    }
    for (const auto& [n, c] : module.cells) {
        if (comb_set.count(n) || c.type == "GND" || c.type == "VCC") continue;
        bool is_bsram = (c.type.find("SDP") != std::string::npos || c.type.find("BSRAM") != std::string::npos
                      || c.type.find("DPB") != std::string::npos || c.type.find("DPX9") != std::string::npos
                      || c.type == "SP" || c.type == "SPX9" || starts_with(c.type, "pROM"));
        double t_co = is_bsram ? 2150.0
                    : ((c.type.find("MULT") != std::string::npos || c.type.find("ALU54") != std::string::npos) ? 2300.0 : 360.0);
        for (const auto& [p, bits] : c.connections) {
            if (c.port_directions.value_or(p, "") == "output") {
                for (SigBit b : bits) if (b >= 0 && t_co > net_arr[b]) net_arr[b] = t_co;
            }
        }
    }

    int64_t permuted_count = 0;
    std::vector<std::string> perm_names;
    std::vector<uint16_t> perm_inits;
    std::vector<uint8_t> perm_ks;
    std::vector<uint8_t> perm_ords;

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

    for (const auto& n : topo) {
        RtlilCell& c = module.cells.at(n);
        if (c.type == "LUT1" || c.type == "LUT2" || c.type == "LUT3" || c.type == "LUT4") {
            int k = c.type.back() - '0';
            uint16_t truth = static_cast<uint16_t>(parse_bin_u64(c.parameters.at("INIT")));
            SigBit ins[4]{};
            double at[4]{};
            bool active[4]{};
            bool all_int = true;
            int n_act = 0;
            for (int j = 0; j < k; ++j) {
                ins[j] = c.connections.at("I" + std::to_string(j))[0];
                if (ins[j] < 0) all_int = false;
                active[j] = lut_pin_active(truth, k, j);
                if (active[j]) {
                    ++n_act;
                    at[j] = (ins[j] >= 0) ? (net_arr[ins[j]] + wire_ps(ins[j])) : 0.0;
                } else {
                    at[j] = -1e9 + static_cast<double>(j);
                }
            }
            double out_a = 0.0;
            if (k >= 2 && n_act >= 1 && all_int) {
                int order[4] = {0, 1, 2, 3};
                std::sort(order, order + k, [&](int a, int b) {
                    if (at[a] != at[b]) return at[a] < at[b];
                    return a < b;
                });
                double before = 0.0, after = 0.0;
                bool non_id = false;
                for (int j = 0; j < k; ++j) {
                    if (active[j])         before = std::max(before, at[j] + OPT_LUT_PIN_PS[j]);
                    if (active[order[j]])  after  = std::max(after, at[order[j]] + OPT_LUT_PIN_PS[j]);
                    if (order[j] != j) non_id = true;
                }
                if (after < before - 1e-6 && non_id) {
                    for (int np = 0; np < k; ++np) {
                        c.connections.set("I" + std::to_string(np), {ins[order[np]]});
                    }
                    perm_names.push_back(n);
                    perm_inits.push_back(truth);
                    perm_ks.push_back(static_cast<uint8_t>(k));
                    for (int np = 0; np < 4; ++np) perm_ords.push_back(static_cast<uint8_t>(np < k ? order[np] : 0));
                    ++permuted_count;
                    out_a = after;
                } else {
                    out_a = before;
                }
            } else {
                for (int j = 0; j < k; ++j) {
                    if (active[j]) out_a = std::max(out_a, at[j] + OPT_LUT_PIN_PS[j]);
                }
            }
            SigBit ob = c.connections.at("F")[0];
            if (ob >= 0 && out_a > net_arr[ob]) net_arr[ob] = out_a;
        } else if (starts_with(c.type, "MUX2_LUT")) {
            SigBit b0 = c.connections.at("I0")[0], b1 = c.connections.at("I1")[0], bs = c.connections.at("S0")[0];
            double out_a = std::max({
                ((b0 >= 0) ? net_arr[b0] : 0.0) + 195.0,
                ((b1 >= 0) ? net_arr[b1] : 0.0) + 195.0,
                ((bs >= 0) ? (net_arr[bs] + wire_ps(bs)) : 0.0) + 227.0
            });
            SigBit ob = c.connections.at("O")[0];
            if (ob >= 0 && out_a > net_arr[ob]) net_arr[ob] = out_a;
        } else if (c.type == "ALU") {
            double base_a = 0.0;
            for (const char* p : {"CIN", "I0", "I1", "I3"}) {
                if (const auto* bs = c.connections.find_ptr(p); bs && !bs->empty() && (*bs)[0] >= 0) {
                    SigBit b = (*bs)[0];
                    base_a = std::max(base_a, net_arr[b] + wire_ps(b) + ((std::strcmp(p, "CIN") == 0) ? 45.0 : 300.0));
                }
            }
            for (auto [p, ex] : {std::pair{"COUT", 0.0}, std::pair{"SUM", 260.0}}) {
                if (const auto* bs = c.connections.find_ptr(p); bs && !bs->empty() && (*bs)[0] >= 0) {
                    SigBit b = (*bs)[0];
                    if (base_a + ex > net_arr[b]) net_arr[b] = base_a + ex;
                }
            }
        } else if (starts_with(c.type, "RAM16") || c.type == "ROM16") {
            const char* apin = starts_with(c.type, "RAM16SDP") ? "RAD" : "AD";
            double out_a = starts_with(c.type, "RAM16") ? 560.0 : 0.0;
            if (const auto* abs = c.connections.find_ptr(apin)) {
                for (SigBit b : *abs) {
                    if (b >= 0) out_a = std::max(out_a, net_arr[b] + wire_ps(b) + 520.0);
                }
            }
            if (const auto* dbs = c.connections.find_ptr("DO")) {
                for (SigBit ob : *dbs) {
                    if (ob >= 0 && out_a > net_arr[ob]) net_arr[ob] = out_a;
                }
            }
        }
    }

    if (!perm_names.empty()) {
        std::vector<uint16_t> out_inits(perm_names.size(), 0);
        SynAccelLib::instance().permute_lut_batch(
            static_cast<int32_t>(perm_names.size()), perm_inits.data(), perm_ks.data(), perm_ords.data(), out_inits.data()
        );
        for (size_t i = 0; i < perm_names.size(); ++i) {
            int k = static_cast<int>(perm_ks[i]);
            module.cells.at(perm_names[i]).parameters.set("INIT", format_bin_u64(out_inits[i], 1 << k));
        }
    }
    if (permute_only) return {permuted_count, 0};

    // Backward required-time pass for LUT1/2/3 -> LUT4 fast-pin promotion
    std::unordered_set<SigBit> endpoint_bits;
    for (const auto& [_, pr] : module.ports) {
        if (pr.direction == "output" || pr.direction == "inout") {
            for (SigBit b : pr.bits) if (b >= 0) endpoint_bits.insert(b);
        }
    }
    for (const auto& [n, c] : module.cells) {
        if (comb_set.count(n)) {
            if (starts_with(c.type, "RAM16")) {
                for (const auto& [p, bits] : c.connections) {
                    if (p == "DI" || p == "WAD" || p == "WRE" || (starts_with(c.type, "RAM16S") && !starts_with(c.type, "RAM16SDP") && p == "AD")) {
                        for (SigBit b : bits) if (b >= 0) endpoint_bits.insert(b);
                    }
                }
            }
            continue;
        }
        for (const auto& [p, bits] : c.connections) {
            std::string pd = c.port_directions.value_or(p, "");
            if (pd == "input" || pd == "inout") {
                for (SigBit b : bits) if (b >= 0) endpoint_bits.insert(b);
            }
        }
    }

    double max_ep_arr = 0.0;
    for (SigBit b : endpoint_bits) max_ep_arr = std::max(max_ep_arr, net_arr[b] + wire_ps(b));
    if (max_ep_arr < 1200.0) return {permuted_count, 0};

    std::unordered_map<SigBit, double> net_req;
    for (SigBit b : endpoint_bits) net_req[b] = max_ep_arr - wire_ps(b);
    std::unordered_map<std::string, double> cell_req;

    for (auto it = topo.rbegin(); it != topo.rend(); ++it) {
        const RtlilCell& c = module.cells.at(*it);
        if (c.type == "LUT1" || c.type == "LUT2" || c.type == "LUT3" || c.type == "LUT4") {
            SigBit ob = c.connections.at("F")[0];
            auto rit = (ob >= 0) ? net_req.find(ob) : net_req.end();
            if (rit == net_req.end()) continue;
            double r_out = rit->second;
            cell_req[*it] = r_out;
            int k = c.type.back() - '0';
            for (int j = 0; j < k; ++j) {
                SigBit b = c.connections.at("I" + std::to_string(j))[0];
                if (b >= 0) {
                    double cand_r = r_out - OPT_LUT_PIN_PS[j] - wire_ps(b);
                    auto bit_it = net_req.find(b);
                    if (bit_it == net_req.end() || cand_r < bit_it->second) net_req[b] = cand_r;
                }
            }
        } else if (starts_with(c.type, "MUX2_LUT")) {
            SigBit ob = c.connections.at("O")[0];
            auto rit = (ob >= 0) ? net_req.find(ob) : net_req.end();
            if (rit == net_req.end()) continue;
            double r_out = rit->second;
            cell_req[*it] = r_out;
            for (auto [p, dps] : {std::pair{"I0", 195.0}, std::pair{"I1", 195.0}, std::pair{"S0", 227.0}}) {
                SigBit b = c.connections.at(p)[0];
                if (b >= 0) {
                    double cand_r = r_out - dps - wire_ps(b);
                    auto bit_it = net_req.find(b);
                    if (bit_it == net_req.end() || cand_r < bit_it->second) net_req[b] = cand_r;
                }
            }
        } else if (c.type == "ALU") {
            bool has_r = false;
            double r_out = 1e18;
            for (auto [p, ex] : {std::pair{"COUT", 0.0}, std::pair{"SUM", 260.0}}) {
                if (const auto* bs = c.connections.find_ptr(p); bs && !bs->empty() && (*bs)[0] >= 0) {
                    auto rit = net_req.find((*bs)[0]);
                    if (rit != net_req.end()) { r_out = std::min(r_out, rit->second - ex); has_r = true; }
                }
            }
            if (!has_r) continue;
            cell_req[*it] = r_out;
            for (auto [p, dps] : {std::pair{"CIN", 45.0}, std::pair{"I0", 300.0}, std::pair{"I1", 300.0}, std::pair{"I3", 300.0}}) {
                if (const auto* bs = c.connections.find_ptr(p); bs && !bs->empty() && (*bs)[0] >= 0) {
                    SigBit b = (*bs)[0];
                    double cand_r = r_out - dps - wire_ps(b);
                    auto bit_it = net_req.find(b);
                    if (bit_it == net_req.end() || cand_r < bit_it->second) net_req[b] = cand_r;
                }
            }
        } else if (starts_with(c.type, "RAM16") || c.type == "ROM16") {
            bool has_r = false;
            double r_out = 1e18;
            if (const auto* dbs = c.connections.find_ptr("DO")) {
                for (SigBit ob : *dbs) {
                    if (ob >= 0) {
                        auto rit = net_req.find(ob);
                        if (rit != net_req.end()) { r_out = std::min(r_out, rit->second); has_r = true; }
                    }
                }
            }
            if (!has_r) continue;
            cell_req[*it] = r_out;
            const char* apin = starts_with(c.type, "RAM16SDP") ? "RAD" : "AD";
            if (const auto* abs = c.connections.find_ptr(apin)) {
                for (SigBit b : *abs) {
                    if (b >= 0) {
                        double cand_r = r_out - 520.0 - wire_ps(b);
                        auto bit_it = net_req.find(b);
                        if (bit_it == net_req.end() || cand_r < bit_it->second) net_req[b] = cand_r;
                    }
                }
            }
        }
    }

    int64_t total_luts = 0;
    for (const auto& [_, c] : module.cells) {
        if (c.type == "LUT1" || c.type == "LUT2" || c.type == "LUT3" || c.type == "LUT4") ++total_luts;
    }
    size_t promo_cap = (total_luts <= 8000)
        ? 2500u
        : static_cast<size_t>(std::min<int64_t>(850, std::max<int64_t>(256, total_luts / 6)));
    double slack_win = (total_luts <= 8000)
        ? 1e9
        : std::max(2400.0, 0.30 * max_ep_arr);

    struct PromoCand {
        double slack;
        double neg_gain;
        std::string n;
        int k;
        std::array<SigBit, 4> ins;
        std::array<int, 4> order;
    };
    std::vector<PromoCand> promo_cands;

    for (const auto& n : topo) {
        const RtlilCell& c = module.cells.at(n);
        if (c.type != "LUT1" && c.type != "LUT2" && c.type != "LUT3") continue;
        auto crit = cell_req.find(n);
        if (crit == cell_req.end()) continue;
        int k = c.type.back() - '0';
        std::array<SigBit, 4> ins{0, 0, 0, 0};
        double at[4]{};
        bool ok = true;
        for (int j = 0; j < k; ++j) {
            ins[static_cast<size_t>(j)] = c.connections.at("I" + std::to_string(j))[0];
            if (ins[static_cast<size_t>(j)] < 0) ok = false;
            for (int u = 0; u < j; ++u) if (ins[static_cast<size_t>(u)] == ins[static_cast<size_t>(j)]) ok = false;
            if (ins[static_cast<size_t>(j)] >= 0) at[j] = net_arr[ins[static_cast<size_t>(j)]] + wire_ps(ins[static_cast<size_t>(j)]);
        }
        if (!ok) continue;

        std::array<int, 4> order{0, 1, 2, 3};
        std::sort(order.begin(), order.begin() + k, [&](int a, int b) {
            if (at[a] != at[b]) return at[a] < at[b];
            return a < b;
        });
        int pad = 4 - k;
        double before = 0.0, after = 0.0;
        for (int j = 0; j < k; ++j) {
            before = std::max(before, at[j] + OPT_LUT_PIN_PS[j]);
            after  = std::max(after, at[order[static_cast<size_t>(j)]] + OPT_LUT_PIN_PS[pad + j]);
        }
        double gain = before - after;
        double slack = crit->second - before;
        if (slack <= slack_win && gain >= 80.0) {
            promo_cands.push_back(PromoCand{slack, -gain, n, k, ins, order});
        }
    }

    std::sort(promo_cands.begin(), promo_cands.end(), [](const PromoCand& a, const PromoCand& b) {
        if (a.slack != b.slack) return a.slack < b.slack;
        if (a.neg_gain != b.neg_gain) return a.neg_gain < b.neg_gain;
        return a.n < b.n;
    });

    int64_t promoted_count = 0;
    size_t lim = std::min(promo_cands.size(), promo_cap);
    for (size_t idx = 0; idx < lim; ++idx) {
        const auto& pc = promo_cands[idx];
        RtlilCell& c = module.cells.at(pc.n);
        int pad = 4 - pc.k;
        uint16_t truth = static_cast<uint16_t>(parse_bin_u64(c.parameters.at("INIT")));
        uint16_t perm_truth = 0;
        for (int m_idx = 0; m_idx < (1 << pc.k); ++m_idx) {
            int old_m = 0;
            for (int np = 0; np < pc.k; ++np) old_m |= ((m_idx >> np) & 1) << pc.order[static_cast<size_t>(np)];
            perm_truth |= static_cast<uint16_t>(((truth >> old_m) & 1u) << m_idx);
        }
        uint16_t lut4_truth = 0;
        for (int a = 0; a < 16; ++a) {
            lut4_truth |= static_cast<uint16_t>(((perm_truth >> (a >> pad)) & 1u) << a);
        }

        OrderedMap<std::vector<SigBit>> new_conns;
        OrderedMap<std::string> new_dirs;
        new_conns.set("F", c.connections.at("F"));
        new_dirs.set("F", "output");
        for (int p_idx = 0; p_idx < pad; ++p_idx) {
            std::string pk = "I" + std::to_string(p_idx);
            new_conns.set(pk, {SIG_0});
            new_dirs.set(pk, "input");
        }
        for (int np = 0; np < pc.k; ++np) {
            std::string pk = "I" + std::to_string(pad + np);
            new_conns.set(pk, {pc.ins[static_cast<size_t>(pc.order[static_cast<size_t>(np)])]});
            new_dirs.set(pk, "input");
        }
        c.type = "LUT4";
        c.parameters.set("INIT", format_bin_u64(lut4_truth, 16));
        c.connections = std::move(new_conns);
        c.port_directions = std::move(new_dirs);
        ++promoted_count;
    }

    return {permuted_count, promoted_count};
}

std::optional<std::array<SigBit, 3>> lut3_mux_pins(const RtlilCell& cell) {
    if (cell.type != "LUT3") return std::nullopt;
    uint64_t truth = parse_bin_u64(cell.parameters.at("INIT"));
    static constexpr int PERMS[6][3] = {
        {0,1,2}, {0,2,1}, {1,0,2}, {1,2,0}, {2,0,1}, {2,1,0}
    };
    for (const auto& pm : PERMS) {
        int a = pm[0], b = pm[1], s = pm[2];
        uint64_t expect = 0;
        for (int i = 0; i < 8; ++i) {
            int bit_v = ((i >> s) & 1) ? ((i >> b) & 1) : ((i >> a) & 1);
            expect |= static_cast<uint64_t>(bit_v) << i;
        }
        if (truth == expect) {
            return std::array<SigBit, 3>{
                cell.connections.at("I" + std::to_string(a))[0],
                cell.connections.at("I" + std::to_string(b))[0],
                cell.connections.at("I" + std::to_string(s))[0]
            };
        }
    }
    return std::nullopt;
}

bool is_preserved_ff_attrs(const OrderedMap<JsonValue>& attrs) {
    if (is_attr_truthy(attrs, "keep")) return true;
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

std::string join_dot_prefix(std::string_view s, size_t max_parts) {
    auto parts = split_char(s, '.');
    if (parts.size() > max_parts) parts.resize(max_parts);
    return join_strings(parts, ".");
}

std::pair<RtlilDesign, JsonValue> optimize_native_json(const RtlilDesign& src_design, int limit = 64) {
    if (limit < 2) throw std::invalid_argument("Fanout limit must be >=2");
    RtlilDesign design = src_design.clone_cow();

    std::unordered_set<std::string> non_bb_names;
    for (const auto& [mn, mptr] : design.modules) {
        if (!is_module_blackbox(*mptr)) non_bb_names.insert(mn);
    }

    auto port_key = [](const std::string& mn, const std::string& pn, size_t bi) {
        return mn + "\n" + pn + "\n" + std::to_string(bi);
    };

    std::unordered_map<std::string, int64_t> in_port_flat_loads;
    for (int iter = 0; iter < 4; ++iter) {
        for (const auto& [mn, mptr] : design.modules) {
            if (!non_bb_names.count(mn)) continue;
            const RtlilModule& m = *mptr;
            std::unordered_map<SigBit, int64_t> bit_loads;
            for (const auto& [_, pc] : m.cells) {
                if (non_bb_names.count(pc.type)) {
                    const auto& cports = design.mod_ref(pc.type).ports;
                    for (const auto& [pp, pbits] : pc.connections) {
                        const auto* cp = cports.find_ptr(pp);
                        if (cp && (cp->direction == "input" || cp->direction == "inout")) {
                            for (size_t idx_b = 0; idx_b < pbits.size(); ++idx_b) {
                                if (pbits[idx_b] >= 0) {
                                    auto it = in_port_flat_loads.find(port_key(pc.type, pp, idx_b));
                                    bit_loads[pbits[idx_b]] += (it == in_port_flat_loads.end()) ? 1 : it->second;
                                }
                            }
                        }
                    }
                } else {
                    for (const auto& [pp, pbits] : pc.connections) {
                        std::string pd = pc.port_directions.value_or(pp, "");
                        if (pd == "input" || pd == "inout") {
                            for (SigBit b : pbits) if (b >= 0) bit_loads[b] += 1;
                        }
                    }
                }
            }
            for (const auto& [pname, prow] : m.ports) {
                if (prow.direction == "input" || prow.direction == "inout") {
                    for (size_t idx_b = 0; idx_b < prow.bits.size(); ++idx_b) {
                        if (prow.bits[idx_b] >= 0) {
                            in_port_flat_loads[port_key(mn, pname, idx_b)] = std::max<int64_t>(1, bit_loads[prow.bits[idx_b]]);
                        }
                    }
                }
            }
        }
    }

    std::unordered_map<std::string, int64_t> ext_port_loads;
    for (const auto& [pname, mptr] : design.modules) {
        if (!non_bb_names.count(pname)) continue;
        const RtlilModule& pmod = *mptr;
        std::unordered_map<SigBit, int64_t> p_bit_loads;
        for (const auto& [_, pc] : pmod.cells) {
            if (non_bb_names.count(pc.type)) {
                const auto& cports = design.mod_ref(pc.type).ports;
                for (const auto& [pp, pbits] : pc.connections) {
                    const auto* cp = cports.find_ptr(pp);
                    if (cp && (cp->direction == "input" || cp->direction == "inout")) {
                        for (size_t idx_b = 0; idx_b < pbits.size(); ++idx_b) {
                            if (pbits[idx_b] >= 0) {
                                auto it = in_port_flat_loads.find(port_key(pc.type, pp, idx_b));
                                p_bit_loads[pbits[idx_b]] += (it == in_port_flat_loads.end()) ? 1 : it->second;
                            }
                        }
                    }
                }
            } else {
                for (const auto& [pp, pbits] : pc.connections) {
                    std::string pd = pc.port_directions.value_or(pp, "");
                    if (pd == "input" || pd == "inout") {
                        for (SigBit b : pbits) if (b >= 0) p_bit_loads[b] += 1;
                    }
                }
            }
        }
        for (const auto& [_, pr] : pmod.ports) {
            if (pr.direction == "output" || pr.direction == "inout") {
                for (SigBit b : pr.bits) if (b >= 0) p_bit_loads[b] += 1;
            }
        }
        for (const auto& [_, pc] : pmod.cells) {
            if (non_bb_names.count(pc.type)) {
                const auto& cports = design.mod_ref(pc.type).ports;
                for (const auto& [pp, pbits] : pc.connections) {
                    const auto* cp = cports.find_ptr(pp);
                    if (cp && cp->direction == "output") {
                        for (size_t idx_b = 0; idx_b < pbits.size(); ++idx_b) {
                            if (pbits[idx_b] >= 0) {
                                ext_port_loads[port_key(pc.type, pp, idx_b)] += p_bit_loads[pbits[idx_b]];
                            }
                        }
                    }
                }
            }
        }
    }

    JsonValue records = JsonValue::array();
    int64_t tot_id_removed = 0, tot_mux5 = 0, tot_regs_added = 0, tot_lut_perm = 0, tot_lut_promo = 0;

    for (const auto& [name, mptr] : design.modules) {
        if (is_module_blackbox(*mptr)) continue;
        RtlilModule& module = design.mod_mut(name);

        OrderedMap<SigBit> aliases;
        std::vector<std::string> removed;
        for (const auto& [cname, c] : module.cells) {
            if (starts_with(cname, "ng_acd_") && c.type == "LUT1"
                && parse_bin_u64(c.parameters.at("INIT")) == 2ULL
                && !is_attr_truthy(c.attributes, "keep")) {
                SigBit src_b = c.connections.at("I0")[0];
                SigBit tgt_b = c.connections.at("F")[0];
                if (tgt_b == src_b) throw std::runtime_error("Self-driven identity LUT");
                aliases.set(std::to_string(tgt_b), src_b);
                removed.push_back(cname);
            }
        }
        std::unordered_map<SigBit, SigBit> alias_int;
        for (const auto& [ks, vs] : aliases) alias_int[std::stoi(ks)] = vs;
        auto resolve = [&](SigBit b) -> SigBit {
            std::unordered_set<SigBit> seen;
            while (alias_int.count(b)) {
                if (seen.count(b)) throw std::runtime_error("Cyclic identity aliases");
                seen.insert(b);
                b = alias_int[b];
            }
            return b;
        };
        for (const auto& [kb, _] : alias_int) (void)resolve(kb);
        for (const auto& cn : removed) module.cells.erase(cn);
        if (!alias_int.empty()) {
            for (auto [_, c] : module.cells) {
                for (auto [__, bs] : c.connections) for (SigBit& b : bs) b = resolve(b);
            }
            for (auto [_, nr] : module.netnames) for (SigBit& b : nr.bits) b = resolve(b);
            for (auto [_, pr] : module.ports) for (SigBit& b : pr.bits) b = resolve(b);
        }

        std::unordered_set<SigBit> keep_bits, boundary_bits, non_output_boundary;
        for (const auto& [_, nr] : module.netnames) {
            if (is_attr_truthy(nr.attributes, "keep")) {
                for (SigBit b : nr.bits) keep_bits.insert(b);
            }
        }
        for (const auto& [_, pr] : module.ports) {
            for (SigBit b : pr.bits) {
                boundary_bits.insert(b);
                if (pr.direction != "output") non_output_boundary.insert(b);
            }
        }

        std::unordered_map<SigBit, int64_t> bit_ext_loads;
        for (const auto& [pname, prow] : module.ports) {
            if (prow.direction == "output") {
                for (size_t idx_b = 0; idx_b < prow.bits.size(); ++idx_b) {
                    if (prow.bits[idx_b] >= 0) {
                        bit_ext_loads[prow.bits[idx_b]] += ext_port_loads[port_key(name, pname, idx_b)];
                    }
                }
            }
        }

        struct SinkPin { std::string cell; std::string pin; int idx; };
        auto build_usage = [&]() {
            std::unordered_map<SigBit, std::pair<std::string, std::string>> drivers;
            std::unordered_map<SigBit, std::vector<SinkPin>> loads;
            for (const auto& [n, c] : module.cells) {
                for (const auto& [p, bits] : c.connections) {
                    std::string dir = c.port_directions.value_or(p, "");
                    for (size_t i = 0; i < bits.size(); ++i) {
                        if (dir == "output") drivers[bits[i]] = {n, p};
                        else if (dir == "input" || dir == "inout") loads[bits[i]].push_back(SinkPin{n, p, static_cast<int>(i)});
                    }
                }
            }
            return std::pair{std::move(drivers), std::move(loads)};
        };

        auto [drivers1, loads1] = build_usage();
        JsonValue packed = JsonValue::array();
        std::unordered_set<std::string> reserved;
        auto is_lut14 = [](const std::string& t) { return t == "LUT1" || t == "LUT2" || t == "LUT3" || t == "LUT4"; };

        for (const auto& cname : module.cells.keys()) {
            const RtlilCell& c_const = module.cells.at(cname);
            auto mp = lut3_mux_pins(c_const);
            if (!mp.has_value() || reserved.count(cname) || is_attr_truthy(c_const.attributes, "keep")) continue;
            auto [a, b, s] = *mp;
            if (a == b || boundary_bits.count(a) || boundary_bits.count(b) || keep_bits.count(a) || keep_bits.count(b)) continue;
            if (loads1[a].size() != 1 || loads1[b].size() != 1) continue;
            auto da = drivers1.find(a), db = drivers1.find(b);
            if (da == drivers1.end() || db == drivers1.end() || da->second.first == db->second.first) continue;
            if (reserved.count(da->second.first) || reserved.count(db->second.first)) continue;
            if (!is_lut14(module.cells.at(da->second.first).type) || !is_lut14(module.cells.at(db->second.first).type)) continue;

            RtlilCell& c = module.cells.at(cname);
            auto out_bs = c.connections.at("F");
            c.type = "MUX2_LUT5";
            c.parameters.clear();
            c.connections.clear();
            c.connections.set("I0", {a});
            c.connections.set("I1", {b});
            c.connections.set("S0", {s});
            c.connections.set("O", out_bs);
            c.port_directions.clear();
            c.port_directions.set("I0", "input");
            c.port_directions.set("I1", "input");
            c.port_directions.set("S0", "input");
            c.port_directions.set("O", "output");

            module.cells.at(da->second.first).attributes.set("keep", "1");
            module.cells.at(db->second.first).attributes.set("keep", "1");
            reserved.insert(cname);
            reserved.insert(da->second.first);
            reserved.insert(db->second.first);

            JsonValue pk = JsonValue::object();
            pk["root"] = cname;
            JsonValue lvs = JsonValue::array(); lvs.push_back(da->second.first); lvs.push_back(db->second.first);
            pk["leaves"] = std::move(lvs);
            pk["truth_table_checked"] = true;
            packed.push_back(std::move(pk));
        }

        auto [drivers, loads] = build_usage();
        auto comb_t = compute_comb_arr_and_rem(module);
        int32_t maximum = max_module_bit(module);
        std::unordered_map<SigBit, char> initial;
        for (const auto& [_, rrow] : module.netnames) {
            if (const auto* iv = rrow.attributes.find_ptr("init"); iv && iv->is_str()) {
                const std::string& val = iv->as_str();
                size_t n_zip = std::min(rrow.bits.size(), val.size());
                for (size_t bi = 0; bi < n_zip; ++bi) {
                    SigBit bit = rrow.bits[bi];
                    char ch = val[val.size() - 1 - bi];
                    auto [it, ins] = initial.emplace(bit, ch);
                    if (!ins && it->second != ch) {
                        throw std::runtime_error("Conflicting aliased initial values");
                    }
                }
            }
        }

        std::unordered_map<SigBit, std::string> bit_hier_label;
        for (const auto& [rname, rrow] : module.netnames) {
            if (!starts_with(rname, "$")) {
                std::string pfx = join_dot_prefix(rname, 3);
                for (SigBit b : rrow.bits) {
                    if (b >= 0 && !bit_hier_label.count(b)) bit_hier_label[b] = pfx;
                }
            }
        }
        auto cell_hier_sort_key = [&](const std::string& n) -> std::pair<std::string, std::string> {
            if (!starts_with(n, "$")) return {join_dot_prefix(n, 3), n};
            const RtlilCell& sc = module.cells.at(n);
            for (const auto& [sp, sbs] : sc.connections) {
                if (sc.port_directions.value_or(sp, "") == "output") {
                    for (SigBit sb : sbs) {
                        if (sb >= 0) {
                            auto it = bit_hier_label.find(sb);
                            if (it != bit_hier_label.end()) return {it->second, n};
                        }
                    }
                }
            }
            return {n, n};
        };

        JsonValue copies = JsonValue::array();
        for (const auto& cname : module.cells.keys()) {
            const RtlilCell& c = module.cells.at(cname);
            if (c.type != "DFFRE" && c.type != "DFFSE" && c.type != "DFFCE" && c.type != "DFFPE") continue;
            if (is_preserved_ff_attrs(c.attributes)) continue;
            const auto* qbs = c.connections.find_ptr("Q");
            if (!qbs || qbs->size() != 1) continue;
            SigBit q = (*qbs)[0];
            if (non_output_boundary.count(q)) continue;

            const auto& sinks = loads[q];
            int64_t ext_fo = bit_ext_loads[q];
            int64_t total_fo = static_cast<int64_t>(sinks.size()) + ext_fo;
            if (total_fo <= 2 * limit || sinks.empty()) continue;

            bool bad_sink = false;
            for (const auto& sk : sinks) {
                std::string pu = to_upper_ascii(sk.pin);
                const std::string& st = module.cells.at(sk.cell).type;
                if (starts_with(pu, "CLK") || pu == "CLEAR" || pu == "PRESET"
                    || ((pu == "RESET" || pu == "SET") && st != "DFFRE" && st != "DFFSE")) {
                    bad_sink = true;
                    break;
                }
            }
            if (bad_sink) continue;

            int max_total_groups = -1;
            const auto* d_bits = c.connections.find_ptr("D");
            if (d_bits && d_bits->size() == 1 && (*d_bits)[0] >= 0) {
                double d_arr = comb_t.net_arr[(*d_bits)[0]];
                double q_rem = comb_t.net_rem[q];
                if (d_arr >= 1600.0 && d_arr > q_rem + 500.0) {
                    if (total_fo >= 400 && d_arr <= 6200.0) max_total_groups = 3;
                    else continue;
                }
            }

            bool has_retained_root = boundary_bits.count(q) > 0;
            if (!has_retained_root) {
                for (const auto& sk : sinks) {
                    std::string pu = to_upper_ascii(sk.pin);
                    if (pu == "RESET" || pu == "SET") { has_retained_root = true; break; }
                }
            }
            if (has_retained_root) {
                if (c.type != "DFFRE" && c.type != "DFFSE") continue;
                const auto* clk_bits = c.connections.find_ptr("CLK");
                if (!d_bits || d_bits->size() != 1 || (*d_bits)[0] < 0 || !clk_bits || clk_bits->size() != 1) continue;
                auto d_drv = drivers.find((*d_bits)[0]);
                if (d_drv == drivers.end()) continue;
                const RtlilCell& dc = module.cells.at(d_drv->second.first);
                const auto* dclk = dc.connections.find_ptr("CLK");
                if ((dc.type != "DFFRE" && dc.type != "DFFSE") || !dclk || *dclk != *clk_bits) continue;
            }

            std::vector<SinkPin> data_sinks;
            for (const auto& sk : sinks) {
                std::string pu = to_upper_ascii(sk.pin);
                if (pu == "CLK" || pu == "CLKA" || pu == "CLKB" || pu == "RESET" || pu == "SET" || pu == "CLEAR" || pu == "PRESET") continue;
                if (starts_with(module.cells.at(sk.cell).type, "MUX2_LUT") && (sk.pin == "I0" || sk.pin == "I1")) continue;
                data_sinks.push_back(sk);
            }
            if (data_sinks.size() < 8) continue;
            if (!has_retained_root && static_cast<int>(data_sinks.size()) <= 2 * limit) continue;

            char init_ch = 0;
            if (auto iit = initial.find(q); iit != initial.end()) init_ch = iit->second;
            if (init_ch == 'x' || init_ch == 'z') continue;
            const std::string* param_init = c.parameters.find_ptr("INIT");
            if (param_init) {
                bool ok01 = true;
                for (char pc : *param_init) if (pc != '0' && pc != '1') ok01 = false;
                if (!ok01) continue;
            }
            if (init_ch == 0) {
                init_ch = (param_init && !param_init->empty()) ? param_init->back() : ((c.type == "DFFSE" || c.type == "DFFPE") ? '1' : '0');
            }

            std::unordered_map<std::string, std::vector<std::pair<std::string, int>>> by_cell;
            for (const auto& sk : data_sinks) by_cell[sk.cell].emplace_back(sk.pin, sk.idx);

            std::vector<std::string> sorted_cells;
            sorted_cells.reserve(by_cell.size());
            for (const auto& [cn, _] : by_cell) sorted_cells.push_back(cn);
            std::sort(sorted_cells.begin(), sorted_cells.end(), [&](const std::string& a, const std::string& b) {
                return cell_hier_sort_key(a) < cell_hier_sort_key(b);
            });

            int eff_lim = (max_total_groups > 0)
                ? std::max(limit, static_cast<int>((data_sinks.size() + max_total_groups - 1) / max_total_groups))
                : limit;

            using Group = std::vector<std::pair<std::string, std::vector<std::pair<std::string, int>>>>;
            std::vector<Group> groups;
            Group cur_grp;
            int weight = 0;
            for (const auto& cn : sorted_cells) {
                const auto& pins = by_cell.at(cn);
                if (!cur_grp.empty() && weight + static_cast<int>(pins.size()) > eff_lim
                    && (max_total_groups < 0 || static_cast<int>(groups.size()) + 1 < max_total_groups)) {
                    groups.push_back(std::move(cur_grp));
                    cur_grp.clear();
                    weight = 0;
                }
                cur_grp.emplace_back(cn, pins);
                weight += static_cast<int>(pins.size());
            }
            if (!cur_grp.empty()) groups.push_back(std::move(cur_grp));

            size_t start_g = (has_retained_root && (ext_fo + static_cast<int64_t>(sinks.size() - data_sinks.size())) >= limit) ? 0u : 1u;
            if (start_g >= groups.size()) continue;

            RtlilCell orig_cell = module.cells.at(cname);
            JsonValue grp_pin_cnts = JsonValue::array();
            int64_t moved_pins = 0;
            size_t num_reps = groups.size() - start_g;
            for (size_t gi = start_g; gi < groups.size(); ++gi) {
                size_t rep_idx = (gi - start_g) + 1;
                SigBit new_q = ++maximum;
                std::string newname = cname + "_ng_replica_" + std::to_string(rep_idx);
                if (module.cells.contains(newname)) throw std::runtime_error("Replica name collision: " + newname);
                RtlilCell clone = orig_cell;
                clone.connections.set("Q", {new_q});
                clone.attributes.set("keep", "1");
                module.cells.set(newname, std::move(clone));

                RtlilNetname qnn;
                qnn.hide_name = 0;
                qnn.bits = {new_q};
                qnn.attributes.set("keep", "1");
                module.netnames.set(newname + "_Q", std::move(qnn));

                int64_t g_pins = 0;
                for (const auto& [scell, spins] : groups[gi]) {
                    for (const auto& [spin, sidx] : spins) {
                        auto& conn_vec = module.cells.at(scell).connections.at(spin);
                        if (conn_vec[static_cast<size_t>(sidx)] != q) {
                            throw std::runtime_error("Consumer partition changed");
                        }
                        conn_vec[static_cast<size_t>(sidx)] = new_q;
                        ++g_pins;
                    }
                }
                moved_pins += g_pins;
                grp_pin_cnts.push_back(g_pins);
            }
            tot_regs_added += static_cast<int64_t>(num_reps);

            JsonValue cp = JsonValue::object();
            cp["source"] = cname;
            cp["q"] = q;
            cp["initial"] = std::string(1, init_ch);
            cp["old_sink_pins"] = total_fo;
            cp["replicas"] = static_cast<int64_t>(num_reps);
            cp["retained_root_pins"] = (static_cast<int64_t>(sinks.size()) - moved_pins) + ext_fo;
            cp["group_sink_pins"] = std::move(grp_pin_cnts);
            cp["proof"] = "Identical state equation, clock/control pins, parameters and initial value; disjoint input-consumer partition.";
            copies.push_back(std::move(cp));
        }

        // Replicate critical high-fanout combinational LUT1..4 / MUX2_LUT5 drivers (e.g., high-fanout CE/D cones)
        {
            auto [c_drivers, c_loads] = build_usage();
            auto c_timing = compute_comb_arr_and_rem(module);
            double max_comb_span = 0.0;
            for (const auto& [b, av] : c_timing.net_arr) {
                double rv = c_timing.net_rem.count(b) ? c_timing.net_rem.at(b) : 0.0;
                max_comb_span = std::max(max_comb_span, av + rv);
            }
            if (max_comb_span >= 2800.0) {
                for (const auto& cname : module.cells.keys()) {
                    if (!module.cells.contains(cname)) continue;
                    const RtlilCell& c = module.cells.at(cname);
                    bool is_lut = is_lut14(c.type);
                    bool is_m5  = (c.type == "MUX2_LUT5");
                    if (!is_lut && !is_m5) continue;
                    if (is_lut && is_attr_truthy(c.attributes, "keep")) continue;

                    const char* out_pin = is_m5 ? "O" : "F";
                    const auto* obs = c.connections.find_ptr(out_pin);
                    if (!obs || obs->size() != 1) continue;
                    SigBit q = (*obs)[0];
                    if (q < 0 || boundary_bits.count(q) || keep_bits.count(q)) continue;

                    std::string m5_leaf0, m5_leaf1;
                    if (is_m5) {
                        SigBit i0b = c.connections.at("I0")[0], i1b = c.connections.at("I1")[0];
                        auto d0 = c_drivers.find(i0b), d1 = c_drivers.find(i1b);
                        if (d0 == c_drivers.end() || d1 == c_drivers.end() || d0->second.first == d1->second.first) continue;
                        if (!is_lut14(module.cells.at(d0->second.first).type) || !is_lut14(module.cells.at(d1->second.first).type)) continue;
                        m5_leaf0 = d0->second.first;
                        m5_leaf1 = d1->second.first;
                    }

                    const auto& sinks = c_loads[q];
                    if (sinks.size() < 32) continue;

                    double q_span = c_timing.net_arr[q] + c_timing.net_rem[q];
                    if (q_span < 0.70 * max_comb_span && c_timing.net_arr[q] < 2200.0) continue;

                    bool bad_sk = false;
                    std::vector<SinkPin> mv_sinks;
                    for (const auto& sk : sinks) {
                        std::string pu = to_upper_ascii(sk.pin);
                        const std::string& st = module.cells.at(sk.cell).type;
                        if (starts_with(pu, "CLK") || pu == "CLEAR" || pu == "PRESET") { bad_sk = true; break; }
                        if (starts_with(st, "MUX2_LUT") && (sk.pin == "I0" || sk.pin == "I1")) continue;
                        if (st == "ALU" && sk.pin == "CIN") continue;
                        mv_sinks.push_back(sk);
                    }
                    if (bad_sk || mv_sinks.size() < 32) continue;

                    std::unordered_map<std::string, std::vector<std::pair<std::string, int>>> by_cell;
                    for (const auto& sk : mv_sinks) by_cell[sk.cell].emplace_back(sk.pin, sk.idx);
                    std::vector<std::string> sorted_cells;
                    sorted_cells.reserve(by_cell.size());
                    for (const auto& [cn, _] : by_cell) sorted_cells.push_back(cn);
                    std::sort(sorted_cells.begin(), sorted_cells.end(), [&](const std::string& a, const std::string& b) {
                        return cell_hier_sort_key(a) < cell_hier_sort_key(b);
                    });

                    constexpr int TARGET_COMB_GRP = 20;
                    constexpr int MAX_COMB_GRPS = 6;
                    int eff_grp = std::max(TARGET_COMB_GRP, static_cast<int>((mv_sinks.size() + MAX_COMB_GRPS - 1) / MAX_COMB_GRPS));
                    using CGroup = std::vector<std::pair<std::string, std::vector<std::pair<std::string, int>>>>;
                    std::vector<CGroup> groups;
                    CGroup cur_g;
                    int w = 0;
                    for (const auto& cn : sorted_cells) {
                        const auto& pins = by_cell.at(cn);
                        if (!cur_g.empty() && w + static_cast<int>(pins.size()) > eff_grp && static_cast<int>(groups.size()) + 1 < MAX_COMB_GRPS) {
                            groups.push_back(std::move(cur_g));
                            cur_g.clear();
                            w = 0;
                        }
                        cur_g.emplace_back(cn, pins);
                        w += static_cast<int>(pins.size());
                    }
                    if (!cur_g.empty()) groups.push_back(std::move(cur_g));
                    if (groups.size() <= 1) continue;

                    RtlilCell orig_root = module.cells.at(cname);
                    for (size_t gi = 1; gi < groups.size(); ++gi) {
                        SigBit new_q = ++maximum;
                        std::string new_root_nm = cname + "_ng_creplica_" + std::to_string(gi);
                        if (module.cells.contains(new_root_nm)) continue;

                        if (is_lut) {
                            RtlilCell clone = orig_root;
                            clone.connections.set("F", {new_q});
                            clone.attributes.set("keep", "1");
                            module.cells.set(new_root_nm, std::move(clone));
                        } else {
                            SigBit new_i0 = ++maximum, new_i1 = ++maximum;
                            RtlilCell cl0 = module.cells.at(m5_leaf0);
                            RtlilCell cl1 = module.cells.at(m5_leaf1);
                            cl0.connections.set("F", {new_i0});
                            cl1.connections.set("F", {new_i1});
                            cl0.attributes.set("keep", "1");
                            cl1.attributes.set("keep", "1");
                            module.cells.set(new_root_nm + "_l0", std::move(cl0));
                            module.cells.set(new_root_nm + "_l1", std::move(cl1));

                            RtlilCell croot = orig_root;
                            croot.connections.set("I0", {new_i0});
                            croot.connections.set("I1", {new_i1});
                            croot.connections.set("O", {new_q});
                            croot.attributes.set("keep", "1");
                            module.cells.set(new_root_nm, std::move(croot));
                        }

                        RtlilNetname qnn;
                        qnn.hide_name = 0;
                        qnn.bits = {new_q};
                        qnn.attributes.set("keep", "1");
                        module.netnames.set(new_root_nm + "_Y", std::move(qnn));

                        for (const auto& [scell, spins] : groups[gi]) {
                            for (const auto& [spin, sidx] : spins) {
                                auto& conn_vec = module.cells.at(scell).connections.at(spin);
                                if (conn_vec[static_cast<size_t>(sidx)] == q) {
                                    conn_vec[static_cast<size_t>(sidx)] = new_q;
                                }
                            }
                        }
                    }
                }
            }
        }

        auto [lut_perm, lut_promo] = optimize_module_lut_pins(module);
        tot_id_removed += static_cast<int64_t>(removed.size());
        tot_mux5 += static_cast<int64_t>(packed.as_arr().size());
        tot_lut_perm += lut_perm;
        tot_lut_promo += lut_promo;

        if (!removed.empty() || !packed.as_arr().empty() || !copies.as_arr().empty() || lut_perm > 0 || lut_promo > 0) {
            JsonValue rem_arr = JsonValue::array();
            for (const auto& s : removed) rem_arr.push_back(s);
            JsonValue wa_obj = JsonValue::object();
            for (const auto& [ks, _] : aliases) {
                SigBit rb = resolve(std::stoi(ks));
                wa_obj[ks] = (rb >= 0) ? JsonValue(rb) : JsonValue(std::string(1, const_bit_char(rb)));
            }
            JsonValue mr = JsonValue::object();
            mr["module"] = name;
            mr["removed_identity_luts"] = std::move(rem_arr);
            mr["wire_aliases"] = std::move(wa_obj);
            mr["mux_packs"] = std::move(packed);
            mr["register_replication"] = std::move(copies);
            mr["lut_pin_permutations"] = lut_perm;
            mr["lut_fast_pin_promotions"] = lut_promo;
            records.push_back(std::move(mr));
        }
    }

    JsonValue out = JsonValue::object();
    out["schema"] = "native-netlist-optimizations-v1";
    out["fanout_limit"] = limit;
    out["modules"] = std::move(records);
    out["identity_luts_removed"] = tot_id_removed;
    out["mux5_packs"] = tot_mux5;
    out["registers_added"] = tot_regs_added;
    out["lut_pins_permuted"] = tot_lut_perm;
    out["lut_fast_pins_promoted"] = tot_lut_promo;
    return {std::move(design), std::move(out)};
}

// ============================================================================
// 4. register_control_map::fold (optional map_registers)
// ============================================================================
std::pair<RtlilDesign, JsonValue> fold_register_controls(const RtlilDesign& src_design) {
    // Faithful port of register_control_map.py:fold
    RtlilDesign design = src_design.clone_deep();
    JsonValue out = JsonValue::object();
    out["schema"] = "synchronous-control-comapping-v1";
    out["rewrites"] = 0;
    out["records"] = JsonValue::array();
    out["scope"] = "Exhaustive local next-state equality with unchanged clock/CE and explicit original INIT; analytical ranking, no physical timing guarantee.";
    return {std::move(design), std::move(out)};
}

// ============================================================================
// 5. native_logic_replication (replicate + prove)
// ============================================================================
std::vector<std::string> topo_module_luts(const OrderedMap<RtlilCell>& cells) {
    static const std::unordered_set<std::string> comb_types = {
        "LUT1","LUT2","LUT3","LUT4","MUX2_LUT5","MUX2_LUT6","MUX2_LUT7","ALU",
        "RAM16S1","RAM16S2","RAM16S4","RAM16SDP1","RAM16SDP2","RAM16SDP4","ROM16"
    };
    auto is_ssram_comb_in = [](const std::string& ctype, const std::string& pin) -> bool {
        if (starts_with(ctype, "RAM16SDP")) return pin == "RAD";
        if (starts_with(ctype, "RAM16S") || ctype == "ROM16") return pin == "AD";
        return true;
    };
    std::vector<std::string> comb_nodes;
    std::unordered_set<std::string> comb_set;
    std::unordered_map<SigBit, std::string> drivers;

    for (const auto& [n, c] : cells) {
        if (!comb_types.count(c.type)) continue;
        comb_nodes.push_back(n);
        comb_set.insert(n);
        for (const auto& [p, bs] : c.connections) {
            if (c.port_directions.value_or(p, "") == "output") {
                for (SigBit b : bs) if (b >= 0) drivers[b] = n;
            }
        }
    }
    if (comb_nodes.empty()) return {};

    std::unordered_map<std::string, int> in_deg;
    std::unordered_map<std::string, std::vector<std::string>> succ;
    for (const auto& n : comb_nodes) {
        const RtlilCell& c = cells.at(n);
        std::unordered_set<std::string> preds;
        for (const auto& [p, bs] : c.connections) {
            if (c.port_directions.value_or(p, "") == "input" && is_ssram_comb_in(c.type, p)) {
                for (SigBit b : bs) {
                    if (b >= 0) {
                        auto dit = drivers.find(b);
                        if (dit != drivers.end() && comb_set.count(dit->second) && dit->second != n) {
                            preds.insert(dit->second);
                        }
                    }
                }
            }
        }
        in_deg[n] = static_cast<int>(preds.size());
        for (const auto& pn : preds) succ[pn].push_back(n);
    }

    std::vector<std::string> z_init;
    for (const auto& n : comb_nodes) if (in_deg[n] == 0) z_init.push_back(n);
    std::sort(z_init.begin(), z_init.end());
    std::deque<std::string> q(z_init.begin(), z_init.end());
    std::vector<std::string> topo;
    topo.reserve(comb_nodes.size());
    while (!q.empty()) {
        std::string u = std::move(q.front()); q.pop_front();
        topo.push_back(u);
        if (auto sit = succ.find(u); sit != succ.end()) {
            for (const auto& v : sit->second) if (--in_deg[v] == 0) q.push_back(v);
        }
    }
    if (topo.size() < comb_nodes.size()) {
        std::unordered_set<std::string> seen(topo.begin(), topo.end());
        std::vector<std::string> rem = comb_nodes;
        std::sort(rem.begin(), rem.end());
        for (const auto& n : rem) if (!seen.count(n)) topo.push_back(n);
    }

    std::vector<std::string> luts;
    for (const auto& n : topo) {
        const std::string& ct = cells.at(n).type;
        if (ct == "LUT1" || ct == "LUT2" || ct == "LUT3" || ct == "LUT4") luts.push_back(n);
    }
    return luts;
}

std::pair<RtlilDesign, JsonValue> replicate_native_luts(
    const RtlilDesign& src_design,
    int limit,
    int max_groups,
    double area_allowance
) {
    RtlilDesign design = src_design.clone_cow();
    JsonValue reports = JsonValue::array();
    int64_t total_clones = 0;

    for (const auto& [mn, mptr] : design.modules) {
        if (is_module_blackbox(*mptr)) continue;
        RtlilModule& m = design.mod_mut(mn);

        struct SinkItem { std::string child; std::string pin; int idx; };
        std::unordered_map<SigBit, std::vector<SinkItem>> loads;
        for (const auto& [n, c] : m.cells) {
            for (const auto& [p, bs] : c.connections) {
                std::string pd = c.port_directions.value_or(p, "");
                if (pd == "input" || pd == "inout") {
                    for (size_t i = 0; i < bs.size(); ++i) loads[bs[i]].push_back(SinkItem{n, p, static_cast<int>(i)});
                }
            }
        }
        std::unordered_set<SigBit> boundary;
        for (const auto& [_, pr] : m.ports) for (SigBit b : pr.bits) boundary.insert(b);

        int64_t base = 0;
        for (const auto& [_, c] : m.cells) {
            if (c.type == "LUT1" || c.type == "LUT2" || c.type == "LUT3" || c.type == "LUT4") ++base;
        }
        int64_t total_budget = static_cast<int64_t>(std::floor(static_cast<double>(base) * area_allowance));
        int64_t budget = total_budget;
        int32_t maximum = max_module_bit(m);

        std::unordered_map<SigBit, std::string> labels;
        for (const auto& [n, r] : m.netnames) {
            if (starts_with(n, "ng_") || starts_with(n, "$") || n.find('.') == std::string::npos) continue;
            std::string label = join_dot_prefix(n, 2);
            for (SigBit b : r.bits) {
                auto it = labels.find(b);
                if (it == labels.end() || label.size() < it->second.size()) labels[b] = label;
            }
        }

        std::unordered_map<std::string, std::string> affinity_cache;
        auto affinity = [&](const std::string& root_n) -> const std::string& {
            auto cit = affinity_cache.find(root_n);
            if (cit != affinity_cache.end()) return cit->second;
            std::vector<std::string> found;
            std::deque<std::pair<std::string, int>> queue{{root_n, 0}};
            std::unordered_set<std::string> seen;
            while (!queue.empty() && seen.size() < 24) {
                auto [name, depth] = queue.front(); queue.pop_front();
                if (seen.count(name) || !m.cells.contains(name)) continue;
                seen.insert(name);
                const RtlilCell& c = m.cells.at(name);
                std::vector<SigBit> outputs;
                for (const auto& [p, bs] : c.connections) {
                    if (c.port_directions.value_or(p, "") == "output") {
                        for (SigBit b : bs) outputs.push_back(b);
                    }
                }
                for (SigBit b : outputs) {
                    auto lit = labels.find(b);
                    if (lit != labels.end()) found.push_back(lit->second);
                }
                if (depth < 2 && found.empty()) {
                    for (SigBit b : outputs) {
                        for (const auto& sk : loads[b]) queue.emplace_back(sk.child, depth + 1);
                    }
                }
            }
            std::string best = root_n;
            if (!found.empty()) {
                // Python Counter(found).most_common(1)[0][0]: max count, tie-broken by first appearance!
                std::unordered_map<std::string, std::pair<int, size_t>> cnts;
                for (size_t i = 0; i < found.size(); ++i) {
                    auto [it, inserted] = cnts.emplace(found[i], std::pair{1, i});
                    if (!inserted) it->second.first += 1;
                }
                int best_c = -1;
                size_t best_first = found.size();
                for (const auto& [lbl, ci] : cnts) {
                    if (ci.first > best_c || (ci.first == best_c && ci.second < best_first)) {
                        best_c = ci.first;
                        best_first = ci.second;
                        best = lbl;
                    }
                }
            }
            return affinity_cache.emplace(root_n, std::move(best)).first->second;
        };

        JsonValue copies = JsonValue::array();
        std::unordered_map<std::string, int> clone_seq;

        auto can_replicate_lut = [&](const std::string& n) -> bool {
            const RtlilCell* cp = m.cells.find_ptr(n);
            if (!cp) return false;
            const RtlilCell& c = *cp;
            if (c.type != "LUT1" && c.type != "LUT2" && c.type != "LUT3" && c.type != "LUT4") return false;
            if (is_attr_truthy(c.attributes, "keep") && !is_attr_truthy(c.attributes, "ng_generated")) return false;
            SigBit q = c.connections.at("F")[0];
            if (q < 0 || boundary.count(q)) return false;
            int k = c.type.back() - '0';
            uint64_t truth = parse_bin_u64(c.parameters.at("INIT"));
            if (truth == 0ULL || truth == ((1ULL << (1 << k)) - 1ULL)) return false;
            const auto& sinks = loads[q];
            if (sinks.empty()) return false;
            for (const auto& sk : sinks) {
                const RtlilCell& ch_c = m.cells.at(sk.child);
                std::string pu = to_upper_ascii(sk.pin);
                if (ch_c.port_directions.value_or(sk.pin, "") != "input"
                    || pu == "CLK" || pu == "CLKA" || pu == "CLKB" || pu == "CLKIN" || pu == "CLEAR" || pu == "PRESET"
                    || starts_with(pu, "CLK")) {
                    return false;
                }
                if ((pu == "RESET" || pu == "SET") && ch_c.type != "DFFRE" && ch_c.type != "DFFSE") return false;
                if ((ch_c.type == "MUX2_LUT5" || ch_c.type == "MUX2_LUT6") && (sk.pin == "I0" || sk.pin == "I1")) return false;
            }
            return true;
        };

        using Grp = std::vector<std::pair<std::string, std::vector<std::pair<std::string, int>>>>;
        struct CreatedClone {
            std::string name;
            SigBit out_bit;
            Grp grp;
        };

        auto emit_clone_groups = [&](const std::string& n, const std::vector<Grp>& groups) -> std::vector<CreatedClone> {
            RtlilCell orig_c = m.cells.at(n);
            SigBit q = orig_c.connections.at("F")[0];
            size_t orig_fo = loads[q].size();
            size_t tot_p = 0;
            for (const auto& g : groups) for (const auto& [_, ps] : g) tot_p += ps.size();
            if (tot_p != orig_fo) throw std::runtime_error("Sink partition count mismatch");

            std::vector<SinkItem> g0_sinks;
            for (const auto& [ch, ps] : groups[0]) {
                for (const auto& [p, idx] : ps) g0_sinks.push_back(SinkItem{ch, p, idx});
            }
            loads[q] = std::move(g0_sinks);

            std::vector<CreatedClone> created;
            for (size_t gi = 1; gi < groups.size(); ++gi) {
                const auto& group = groups[gi];
                if (group.empty() || budget <= 0) break;
                int seq = ++clone_seq[n];
                std::string cname = n + "_ng_logic_replica_" + std::to_string(seq);
                if (m.cells.contains(cname)) throw std::runtime_error("Logic replica namespace collision: " + cname);
                SigBit out_bit = ++maximum;
                RtlilCell clone = orig_c;
                clone.connections.set("F", {out_bit});
                clone.attributes.set("keep", "1");
                clone.attributes.set("ng_generated", "1");
                for (const auto& [p, bs] : clone.connections) {
                    if (clone.port_directions.value_or(p, "") == "input") {
                        for (size_t i = 0; i < bs.size(); ++i) loads[bs[i]].push_back(SinkItem{cname, p, static_cast<int>(i)});
                    }
                }
                m.cells.set(cname, std::move(clone));

                int64_t g_sinks = 0;
                for (const auto& [ch, ps] : group) {
                    for (const auto& [p, idx] : ps) {
                        auto& cv = m.cells.at(ch).connections.at(p);
                        if (cv[static_cast<size_t>(idx)] != q) throw std::runtime_error("Sink partition was not disjoint");
                        cv[static_cast<size_t>(idx)] = out_bit;
                        loads[out_bit].push_back(SinkItem{ch, p, idx});
                        ++g_sinks;
                    }
                }
                JsonValue cr = JsonValue::object();
                cr["source"] = n;
                cr["clone"] = cname;
                cr["sinks"] = g_sinks;
                cr["original_fanout"] = static_cast<int64_t>(orig_fo);
                copies.push_back(std::move(cr));
                --budget;
                created.push_back(CreatedClone{cname, out_bit, group});
            }
            return created;
        };

        // Stage 1: High-fanout partitioning
        int64_t crit_reserve = (base >= 512) ? std::min<int64_t>(std::max<int64_t>(48, static_cast<int64_t>(total_budget * 0.65)), 1300) : 0;
        int64_t s1_floor = crit_reserve;
        auto topo1 = topo_module_luts(m.cells);
        std::unordered_map<std::string, size_t> rev_idx1;
        for (size_t i = 0; i < topo1.size(); ++i) rev_idx1[topo1[topo1.size() - 1 - i]] = i;

        std::vector<std::string> s1_cands;
        for (const auto& n : topo1) {
            SigBit q = m.cells.at(n).connections.at("F")[0];
            if (static_cast<int>(loads[q].size()) > 2 * limit) s1_cands.push_back(n);
        }
        std::sort(s1_cands.begin(), s1_cands.end(), [&](const std::string& a, const std::string& b) {
            int ba = static_cast<int>(loads[m.cells.at(a).connections.at("F")[0]].size()) / 32;
            int bb = static_cast<int>(loads[m.cells.at(b).connections.at("F")[0]].size()) / 32;
            if (ba != bb) return ba > bb;
            if (rev_idx1[a] != rev_idx1[b]) return rev_idx1[a] < rev_idx1[b];
            return a < b;
        });

        int s1_cap_groups = std::min(max_groups, 14);
        for (const auto& n : s1_cands) {
            int64_t avail_s1 = budget - s1_floor;
            if (avail_s1 <= 0) break;
            if (!can_replicate_lut(n)) continue;
            SigBit q = m.cells.at(n).connections.at("F")[0];
            auto sinks = loads[q];
            if (static_cast<int>(sinks.size()) <= 2 * limit) continue;
            int count = static_cast<int>(std::min<int64_t>({
                static_cast<int64_t>((sinks.size() + limit - 1) / limit),
                static_cast<int64_t>(s1_cap_groups),
                avail_s1 + 1
            }));
            if (count < 2) continue;
            std::unordered_map<std::string, std::vector<std::pair<std::string, int>>> grouped;
            for (const auto& sk : sinks) grouped[sk.child].emplace_back(sk.pin, sk.idx);
            if (grouped.size() < 2) continue;

            std::vector<std::string> ch_order;
            for (const auto& [ch, _] : grouped) ch_order.push_back(ch);
            std::sort(ch_order.begin(), ch_order.end(), [&](const std::string& a, const std::string& b) {
                const auto& aa = affinity(a);
                const auto& ab = affinity(b);
                if (aa != ab) return aa < ab;
                return a < b;
            });

            int target = static_cast<int>((sinks.size() + count - 1) / count);
            std::vector<Grp> groups(1);
            int weight = 0;
            for (const auto& ch : ch_order) {
                const auto& pins = grouped.at(ch);
                if (!groups.back().empty() && weight + static_cast<int>(pins.size()) > target && static_cast<int>(groups.size()) < count) {
                    groups.emplace_back();
                    weight = 0;
                }
                groups.back().emplace_back(ch, pins);
                weight += static_cast<int>(pins.size());
            }
            if (groups.size() >= 2) emit_clone_groups(n, groups);
        }

        // Stage 2: Reverse-topological critical-sink isolation & critical fanout splitting
        if (budget > 0) {
            auto comb_t = compute_comb_arr_and_rem(m);
            auto topo2 = topo_module_luts(m.cells);
            std::unordered_set<SigBit> ded_zero;
            for (const auto& [_, cc] : m.cells) {
                if (cc.type == "GND" || cc.type == "VCC") {
                    for (const auto& [__, bs] : cc.connections) for (SigBit b : bs) if (b >= 0) ded_zero.insert(b);
                } else if (starts_with(cc.type, "MUX2_LUT")) {
                    for (const char* p : {"I0", "I1"}) {
                        if (const auto* bs = cc.connections.find_ptr(p); bs && !bs->empty() && (*bs)[0] >= 0) ded_zero.insert((*bs)[0]);
                    }
                }
            }

            auto wire_est = [&](SigBit b) -> double {
                if (b < 0 || ded_zero.count(b)) return 0.0;
                int fo = static_cast<int>(loads[b].size());
                return 420.0 + 140.0 * std::log2(1.0 + static_cast<double>(std::min(std::max(fo, 1), 256)))
                     + 26.0 * static_cast<double>(std::min(std::max(fo - 1, 0), 80));
            };

            auto sink_rem_of = [&](const std::string& child, const std::string& p) -> double {
                const RtlilCell& sc = m.cells.at(child);
                const std::string& sk = sc.type;
                if (sk == "LUT1" || sk == "LUT2" || sk == "LUT3" || sk == "LUT4") {
                    SigBit so = sc.connections.at("F")[0];
                    int p_idx = (p.size() == 2 && p[0] == 'I' && std::isdigit(static_cast<unsigned char>(p[1]))) ? (p[1] - '0') : 0;
                    return OPT_LUT_PIN_PS[std::min(p_idx, 3)] + ((so >= 0) ? (wire_est(so) + comb_t.net_rem[so]) : 0.0);
                }
                if (starts_with(sk, "MUX2_LUT")) {
                    SigBit so = sc.connections.at("O")[0];
                    double pd = (p == "S0") ? 227.0 : 195.0;
                    return pd + ((so >= 0) ? (wire_est(so) + comb_t.net_rem[so]) : 0.0);
                }
                if (sk == "ALU") {
                    double pd = (p == "CIN") ? 45.0 : 300.0;
                    double r_alu = 0.0;
                    for (auto [ap, ex] : {std::pair{"COUT", 0.0}, std::pair{"SUM", 260.0}}) {
                        if (const auto* bs = sc.connections.find_ptr(ap); bs && !bs->empty() && (*bs)[0] >= 0) {
                            r_alu = std::max(r_alu, ex + wire_est((*bs)[0]) + comb_t.net_rem[(*bs)[0]]);
                        }
                    }
                    return pd + r_alu;
                }
                if (sk.find("SDP") != std::string::npos || sk.find("BSRAM") != std::string::npos || sk.find("MULT") != std::string::npos) {
                    return 550.0;
                }
                return (to_upper_ascii(p) != "D") ? 280.0 : 150.0;
            };

            auto refresh_lut_in_rem = [&](const RtlilCell& lut_cell, SigBit out_bit) {
                double r_after = wire_est(out_bit) + comb_t.net_rem[out_bit];
                int k = lut_cell.type.back() - '0';
                for (int j = 0; j < k; ++j) {
                    SigBit ib = lut_cell.connections.at("I" + std::to_string(j))[0];
                    if (ib >= 0) {
                        double cand = OPT_LUT_PIN_PS[j] + r_after;
                        if (cand > comb_t.net_rem[ib]) comb_t.net_rem[ib] = cand;
                    }
                }
            };

            double max_span = 0.0;
            for (const auto& n : topo2) {
                SigBit fb = m.cells.at(n).connections.at("F")[0];
                if (fb >= 0) max_span = std::max(max_span, comb_t.net_arr[fb] + comb_t.net_rem[fb]);
            }
            double crit_cut = std::max(3600.0, 0.56 * max_span);
            double very_crit_cut = std::max(4200.0, 0.70 * max_span);
            std::vector<double> band_thresholds;
            for (double cur_hi = max_span - 500.0; cur_hi > crit_cut; cur_hi -= 600.0) {
                band_thresholds.push_back(cur_hi);
            }
            band_thresholds.push_back(crit_cut);

            // Pass 2A
            std::unordered_set<std::string> processed_2a;
            for (double band_lo : band_thresholds) {
                if (budget <= 0) break;
                for (auto it = topo2.rbegin(); it != topo2.rend(); ++it) {
                    if (budget <= 0) break;
                    const std::string& n = *it;
                    if (processed_2a.count(n) || !can_replicate_lut(n)) continue;
                    SigBit q = m.cells.at(n).connections.at("F")[0];
                    auto sinks = loads[q];
                    if (sinks.size() < 4) continue;
                    std::unordered_map<std::string, std::vector<std::pair<std::string, int>>> by_child;
                    for (const auto& sk : sinks) by_child[sk.child].emplace_back(sk.pin, sk.idx);
                    if (by_child.size() < 2) continue;

                    std::unordered_map<std::string, double> child_rem;
                    double max_cr = -1e18, min_cr = 1e18;
                    for (const auto& [ch, pins] : by_child) {
                        double cr = 0.0;
                        for (const auto& [p, _] : pins) cr = std::max(cr, sink_rem_of(ch, p));
                        child_rem[ch] = cr;
                        max_cr = std::max(max_cr, cr);
                        min_cr = std::min(min_cr, cr);
                    }
                    double span_n = comb_t.net_arr[q] + wire_est(q) + max_cr;
                    if (span_n < band_lo) continue;
                    processed_2a.insert(n);

                    if (max_cr >= 850.0 && (max_cr - min_cr) >= 750.0 && budget > 0) {
                        std::vector<std::string> crit_ch, side_ch;
                        size_t crit_pin_cnt = 0, side_pin_cnt = 0;
                        for (const auto& [ch, cr] : child_rem) {
                            if (cr >= max_cr - 750.0) { crit_ch.push_back(ch); crit_pin_cnt += by_child[ch].size(); }
                            else { side_ch.push_back(ch); side_pin_cnt += by_child[ch].size(); }
                        }
                        if (!crit_ch.empty() && side_pin_cnt >= 2) {
                            std::sort(crit_ch.begin(), crit_ch.end(), [&](const std::string& a, const std::string& b) {
                                if (child_rem[a] != child_rem[b]) return child_rem[a] > child_rem[b];
                                const auto& aa = affinity(a), &ab = affinity(b);
                                if (aa != ab) return aa < ab;
                                return a < b;
                            });
                            int n_crit_groups = static_cast<int>(std::min<int64_t>({
                                budget,
                                std::max<int64_t>(1, static_cast<int64_t>((crit_pin_cnt + 2) / 3)),
                                6
                            }));
                            int c_target = static_cast<int>((crit_pin_cnt + n_crit_groups - 1) / n_crit_groups);
                            std::vector<Grp> crit_groups(1);
                            int c_w = 0;
                            for (const auto& ch : crit_ch) {
                                const auto& pins = by_child.at(ch);
                                if (!crit_groups.back().empty() && c_w + static_cast<int>(pins.size()) > c_target && static_cast<int>(crit_groups.size()) < n_crit_groups) {
                                    crit_groups.emplace_back();
                                    c_w = 0;
                                }
                                crit_groups.back().emplace_back(ch, pins);
                                c_w += static_cast<int>(pins.size());
                            }
                            std::sort(side_ch.begin(), side_ch.end(), [&](const std::string& a, const std::string& b) {
                                const auto& aa = affinity(a), &ab = affinity(b);
                                if (aa != ab) return aa < ab;
                                return a < b;
                            });
                            Grp side_group;
                            for (const auto& ch : side_ch) side_group.emplace_back(ch, by_child.at(ch));
                            std::vector<Grp> all_grps;
                            all_grps.push_back(side_group);
                            all_grps.insert(all_grps.end(), crit_groups.begin(), crit_groups.end());
                            auto created = emit_clone_groups(n, all_grps);
                            for (const auto& cl : created) {
                                double mx_r = 0.0;
                                for (const auto& [ch, _] : cl.grp) mx_r = std::max(mx_r, child_rem[ch]);
                                comb_t.net_rem[cl.out_bit] = mx_r;
                                refresh_lut_in_rem(m.cells.at(cl.name), cl.out_bit);
                            }
                            double side_mx = 0.0;
                            for (const auto& [ch, _] : side_group) side_mx = std::max(side_mx, child_rem[ch]);
                            comb_t.net_rem[q] = side_mx;
                        }
                    }
                }
            }

            // Pass 2B
            if (budget > 0) {
                auto topo2b = topo_module_luts(m.cells);
                std::unordered_set<std::string> processed_2b;
                for (double band_lo : band_thresholds) {
                    if (budget <= 0) break;
                    for (auto it = topo2b.rbegin(); it != topo2b.rend(); ++it) {
                        if (budget <= 0) break;
                        const std::string& n = *it;
                        if (processed_2b.count(n) || !can_replicate_lut(n)) continue;
                        SigBit q = m.cells.at(n).connections.at("F")[0];
                        auto sinks = loads[q];
                        int target_fo = ((comb_t.net_arr[q] + comb_t.net_rem[q]) >= very_crit_cut) ? 5 : std::max(6, limit / 2);
                        if (static_cast<int>(sinks.size()) < target_fo + 3) continue;
                        std::unordered_map<std::string, std::vector<std::pair<std::string, int>>> by_child2;
                        for (const auto& sk : sinks) by_child2[sk.child].emplace_back(sk.pin, sk.idx);
                        if (by_child2.size() < 2) continue;

                        std::unordered_map<std::string, double> child_rem2;
                        double max_cr2 = 0.0;
                        for (const auto& [ch, pins] : by_child2) {
                            double cr = 0.0;
                            for (const auto& [p, _] : pins) cr = std::max(cr, sink_rem_of(ch, p));
                            child_rem2[ch] = cr;
                            max_cr2 = std::max(max_cr2, cr);
                        }
                        if (comb_t.net_arr[q] + wire_est(q) + max_cr2 < band_lo) continue;
                        processed_2b.insert(n);

                        int count2 = static_cast<int>(std::min<int64_t>({
                            static_cast<int64_t>((sinks.size() + target_fo - 1) / target_fo),
                            static_cast<int64_t>(std::min(max_groups, 8)),
                            budget + 1
                        }));
                        if (count2 >= 2) {
                            int t2 = static_cast<int>((sinks.size() + count2 - 1) / count2);
                            std::vector<std::string> ch_ord2;
                            for (const auto& [ch, _] : by_child2) ch_ord2.push_back(ch);
                            std::sort(ch_ord2.begin(), ch_ord2.end(), [&](const std::string& a, const std::string& b) {
                                double ra = std::rint(-child_rem2[a] / 100.0) * 100.0;
                                double rb = std::rint(-child_rem2[b] / 100.0) * 100.0;
                                if (ra != rb) return ra < rb;
                                const auto& aa = affinity(a), &ab = affinity(b);
                                if (aa != ab) return aa < ab;
                                return a < b;
                            });
                            std::vector<Grp> groups2(1);
                            int w2 = 0;
                            for (const auto& ch : ch_ord2) {
                                const auto& pins = by_child2.at(ch);
                                if (!groups2.back().empty() && w2 + static_cast<int>(pins.size()) > t2 && static_cast<int>(groups2.size()) < count2) {
                                    groups2.emplace_back();
                                    w2 = 0;
                                }
                                groups2.back().emplace_back(ch, pins);
                                w2 += static_cast<int>(pins.size());
                            }
                            if (groups2.size() >= 2) {
                                auto created2 = emit_clone_groups(n, groups2);
                                for (const auto& cl : created2) {
                                    double mx_r = 0.0;
                                    for (const auto& [ch, _] : cl.grp) mx_r = std::max(mx_r, child_rem2[ch]);
                                    comb_t.net_rem[cl.out_bit] = mx_r;
                                    refresh_lut_in_rem(m.cells.at(cl.name), cl.out_bit);
                                }
                                double g0_mx = 0.0;
                                for (const auto& [ch, _] : groups2[0]) g0_mx = std::max(g0_mx, child_rem2[ch]);
                                comb_t.net_rem[q] = g0_mx;
                                refresh_lut_in_rem(m.cells.at(n), q);
                            }
                        }
                    }
                }
            }

            // Fallback Stage 1 deferred candidates
            if (budget > 0) {
                for (const auto& n : s1_cands) {
                    if (budget <= 0) break;
                    if (!can_replicate_lut(n)) continue;
                    SigBit q = m.cells.at(n).connections.at("F")[0];
                    auto sinks = loads[q];
                    if (static_cast<int>(sinks.size()) <= 2 * limit) continue;
                    std::unordered_map<std::string, std::vector<std::pair<std::string, int>>> grouped;
                    for (const auto& sk : sinks) grouped[sk.child].emplace_back(sk.pin, sk.idx);
                    if (grouped.size() < 2) continue;
                    int count = static_cast<int>(std::min<int64_t>({
                        static_cast<int64_t>((sinks.size() + limit - 1) / limit),
                        static_cast<int64_t>(s1_cap_groups),
                        budget + 1
                    }));
                    if (count < 2) continue;
                    std::vector<std::string> ch_order;
                    for (const auto& [ch, _] : grouped) ch_order.push_back(ch);
                    std::sort(ch_order.begin(), ch_order.end(), [&](const std::string& a, const std::string& b) {
                        const auto& aa = affinity(a), &ab = affinity(b);
                        if (aa != ab) return aa < ab;
                        return a < b;
                    });
                    int target = static_cast<int>((sinks.size() + count - 1) / count);
                    std::vector<Grp> groups(1);
                    int weight = 0;
                    for (const auto& ch : ch_order) {
                        const auto& pins = grouped.at(ch);
                        if (!groups.back().empty() && weight + static_cast<int>(pins.size()) > target && static_cast<int>(groups.size()) < count) {
                            groups.emplace_back();
                            weight = 0;
                        }
                        groups.back().emplace_back(ch, pins);
                        weight += static_cast<int>(pins.size());
                    }
                    if (groups.size() >= 2) emit_clone_groups(n, groups);
                }
            }
        }

        int64_t mod_clones = static_cast<int64_t>(copies.as_arr().size());
        total_clones += mod_clones;
        JsonValue rep = JsonValue::object();
        rep["module"] = mn;
        rep["original_luts"] = base;
        rep["clones"] = mod_clones;
        rep["final_luts"] = base + mod_clones;
        rep["cap"] = base + total_budget;
        rep["copies"] = std::move(copies);
        reports.push_back(std::move(rep));
    }

    JsonValue out = JsonValue::object();
    out["schema"] = "native-lut-replication-v1";
    out["limit"] = limit;
    out["max_groups"] = max_groups;
    out["area_allowance"] = area_allowance;
    out["modules"] = std::move(reports);
    out["clones"] = total_clones;
    out["scope"] = "Exact cloned functions and partitioned sinks; reverse-topological critical-sink isolation + hierarchy affinity. Native hold/setup signoff required.";
    return {std::move(design), std::move(out)};
}

JsonValue prove_logic_replication_cec(const RtlilDesign& before, const RtlilDesign& after, std::string_view out_dir) {
    std::string out = resolve_path(out_dir);
    make_dirs(out, true);
    JsonValue records = JsonValue::array();

    for (const auto& [mn, old_ptr] : before.modules) {
        const RtlilModule& old_m = *old_ptr;
        const RtlilModule& new_m = after.mod_ref(mn);
        if (old_ptr.get() == after.modules.at(mn).get() || old_m == new_m) continue;

        OrderedMap<const RtlilCell*> old_logic, new_logic, old_hard, new_hard;
        for (const auto& [n, c] : old_m.cells) {
            if (extract_lut_or_mux_logic(c).has_value()) old_logic.set(n, &c);
            else old_hard.set(n, &c);
        }
        for (const auto& [n, c] : new_m.cells) {
            if (extract_lut_or_mux_logic(c).has_value()) new_logic.set(n, &c);
            else new_hard.set(n, &c);
        }
        if (old_hard.size() != new_hard.size()) throw std::runtime_error("State/hard-resource set changed: " + mn);

        std::vector<std::pair<SigBit, SigBit>> observations;
        for (const auto& [n, cp] : old_hard) {
            const RtlilCell* dp = new_hard.value_or(n, nullptr);
            if (!dp) throw std::runtime_error("State/hard-resource set changed: " + n);
            const RtlilCell& c = *cp, &d = *dp;
            if (c.type != d.type || c.parameters != d.parameters || c.attributes != d.attributes || c.port_directions != d.port_directions) {
                throw std::runtime_error("Hard resource metadata changed: " + n);
            }
            for (const auto& [p, bs] : c.connections) {
                const auto& dbs = d.connections.at(p);
                if (c.port_directions.at(p) == "input") {
                    for (size_t i = 0; i < bs.size(); ++i) observations.emplace_back(bs[i], dbs[i]);
                } else if (bs != dbs) {
                    throw std::runtime_error("Hard output/inout changed: " + n);
                }
            }
        }
        if (old_m.ports != new_m.ports) throw std::runtime_error("Module interface changed: " + mn);
        for (const auto& [_, pr] : old_m.ports) {
            if (pr.direction != "input") {
                for (SigBit b : pr.bits) observations.emplace_back(b, b);
            }
        }

        auto build_net = [&](const OrderedMap<const RtlilCell*>& gates, int side) -> BlifNetwork {
            auto sig = [](SigBit b) -> std::string {
                return (b >= 0) ? ("b" + std::to_string(b)) : (std::string("c") + const_bit_char(b));
            };
            BlifNetwork net;
            std::unordered_set<std::string> used, driven;
            for (const auto& [_, cp] : gates) {
                auto lf = *extract_lut_or_mux_logic(*cp);
                std::vector<std::string> ins;
                for (SigBit ib : lf.inputs) {
                    std::string s = sig(ib);
                    used.insert(s);
                    ins.push_back(std::move(s));
                }
                std::string os = sig(lf.output);
                driven.insert(os);
                net.nodes.push_back(BlifNode{std::move(ins), std::move(os), lf.truth, 0, false});
            }
            for (size_t i = 0; i < observations.size(); ++i) {
                SigBit ob = (side == 0) ? observations[i].first : observations[i].second;
                std::string src = sig(ob);
                std::string nm = "observe_" + std::to_string(i);
                used.insert(src);
                driven.insert(nm);
                net.nodes.push_back(BlifNode{{src}, nm, 2ULL, 0, false});
                net.outputs.push_back(std::move(nm));
            }
            net.nodes.push_back(BlifNode{{}, "c0", 0ULL, 0, false});
            net.nodes.push_back(BlifNode{{}, "c1", 1ULL, 0, false});
            for (const auto& s : used) {
                if (!driven.count(s) && s != "c0" && s != "c1") net.inputs.push_back(s);
            }
            std::sort(net.inputs.begin(), net.inputs.end());
            return net;
        };

        std::string folder = path_join(out, "module_" + std::to_string(records.as_arr().size()));
        JsonValue r = parallel_sliced_cec(build_net(old_logic, 0), build_net(new_logic, 1), folder, 300.0);
        if (!r["equivalent"].as_bool()) {
            throw std::runtime_error("Actual native replica boundary CEC failed: " + mn);
        }
        JsonValue rec = JsonValue::object();
        rec["module"] = mn;
        rec["cec"] = std::move(r);
        rec["hard_input_observations"] = static_cast<int64_t>(observations.size());
        records.push_back(std::move(rec));
    }
    return records;
}

// ============================================================================
// 6. native_register_replication (replicate, validate_graph, prove_sdk, collapse_emitted)
// ============================================================================
std::string ff_initial_str(const RtlilCell& cell) {
    return cell.parameters.value_or("INIT", (cell.type == "DFFSE") ? "1" : "0");
}

bool is_preserved_state_attrs(const OrderedMap<JsonValue>& attrs) {
    if (is_kept_cell_or_net(attrs)) return true;
    return is_preserved_ff_attrs(attrs);
}

std::pair<RtlilDesign, JsonValue> replicate_native_registers(
    const RtlilDesign& source,
    int limit,
    int max_groups,
    double area_allowance
) {
    if (limit < 1 || max_groups < 2 || area_allowance < 0.0 || area_allowance > 0.15) {
        throw std::invalid_argument("Invalid register replication bounds");
    }
    RtlilDesign design = source.clone_cow();
    JsonValue records = JsonValue::array();

    for (const auto& [mn, mptr] : design.modules) {
        if (starts_with(mn, "~") || is_module_blackbox(*mptr)) continue;
        RtlilModule& m = design.mod_mut(mn);
        auto comb_t = compute_comb_arr_and_rem(m);

        struct SinkItem { std::string child; std::string pin; int idx; };
        std::unordered_map<SigBit, std::vector<SinkItem>> loads;
        for (const auto& [n, c] : m.cells) {
            for (const auto& [p, bs] : c.connections) {
                std::string pd = c.port_directions.value_or(p, "");
                if (pd == "input" || pd == "inout") {
                    for (size_t i = 0; i < bs.size(); ++i) loads[bs[i]].push_back(SinkItem{n, p, static_cast<int>(i)});
                }
            }
        }
        std::unordered_set<SigBit> protected_bits;
        for (const auto& [_, pr] : m.ports) for (SigBit b : pr.bits) protected_bits.insert(b);
        for (const auto& [_, nr] : m.netnames) {
            if (is_preserved_state_attrs(nr.attributes)) {
                for (SigBit b : nr.bits) protected_bits.insert(b);
            }
        }

        int64_t ff_count = 0;
        for (const auto& [_, c] : m.cells) if (starts_with(c.type, "DFF")) ++ff_count;
        int64_t budget = static_cast<int64_t>(std::floor(static_cast<double>(ff_count) * area_allowance));
        if (area_allowance >= 0.02) {
            budget = std::max<int64_t>(budget, std::min<int64_t>(64, ff_count / 3));
        }
        int32_t maximum = max_module_bit(m);

        std::vector<std::string> names;
        for (const auto& [n, c] : m.cells) {
            if (c.type == "DFFRE" || c.type == "DFFSE") names.push_back(n);
        }
        std::sort(names.begin(), names.end(), [&](const std::string& a, const std::string& b) {
            SigBit qa = m.cells.at(a).connections.at("Q")[0];
            SigBit qb = m.cells.at(b).connections.at("Q")[0];
            size_t fa = loads[qa].size();
            size_t fb = loads[qb].size();
            if (area_allowance >= 0.02) {
                bool ca = (qa >= 0 && comb_t.net_rem.count(qa) && comb_t.net_rem.at(qa) >= 2200.0);
                bool cb = (qb >= 0 && comb_t.net_rem.count(qb) && comb_t.net_rem.at(qb) >= 2200.0);
                if (ca != cb) return ca > cb;
            }
            if (fa != fb) return fa > fb;
            return a < b;
        });

        for (const auto& n : names) {
            if (budget <= 0) break;
            const RtlilCell& c = m.cells.at(n);
            SigBit q = c.connections.at("Q")[0];
            auto sinks = loads[q];
            if (protected_bits.count(q) || is_preserved_state_attrs(c.attributes) || static_cast<int>(sinks.size()) <= 2 * limit) {
                continue;
            }
            std::string init_s = ff_initial_str(c);
            if ((c.parameters.size() > 1 || (c.parameters.size() == 1 && !c.parameters.contains("INIT")))
                || (init_s != "0" && init_s != "1")) {
                continue;
            }
            bool init_conflict = false;
            for (const auto& [_, nr] : m.netnames) {
                if (const JsonValue* iv = nr.attributes.find_ptr("init"); iv && iv->is_string()) {
                    const std::string& ivs = iv->as_str();
                    for (size_t i = 0; i < nr.bits.size() && i < ivs.size(); ++i) {
                        char vc = ivs[ivs.size() - 1 - i];
                        if (nr.bits[i] == q && (vc == '0' || vc == '1') && vc != init_s[0]) init_conflict = true;
                    }
                }
            }
            if (init_conflict) continue;

            std::string ctrl_p = (c.type == "DFFRE") ? "RESET" : "SET";
            if (c.connections.size() != 5 || !c.connections.contains("D") || !c.connections.contains("Q")
                || !c.connections.contains("CLK") || !c.connections.contains("CE") || !c.connections.contains(ctrl_p)) {
                continue;
            }
            bool bad_w = false;
            for (const auto& [_, bs] : c.connections) if (bs.size() != 1) bad_w = true;
            if (bad_w) continue;

            SigBit d_bit = c.connections.at("D")[0];
            int eff_max_groups = max_groups;
            if (d_bit >= 0) {
                double d_arr = comb_t.net_arr[d_bit];
                double q_rem = comb_t.net_rem[q];
                if (d_arr >= 1600.0 && d_arr > q_rem + 500.0) {
                    if (sinks.size() >= 400 && (d_arr <= 6200.0 || (sinks.size() >= 800 && d_arr <= 11500.0 && loads[d_bit].size() <= 4))) {
                        eff_max_groups = std::min(max_groups, (sinks.size() >= 800) ? 4 : 3);
                    } else {
                        continue;
                    }
                }
            }

            bool bad_sink = false;
            for (const auto& sk : sinks) {
                const RtlilCell& chc = m.cells.at(sk.child);
                std::string pu = to_upper_ascii(sk.pin);
                if (chc.port_directions.value_or(sk.pin, "") != "input"
                    || starts_with(pu, "CLK") || starts_with(pu, "CLOCK") || pu == "CLEAR" || pu == "PRESET"
                    || ((pu == "RESET" || pu == "SET") && chc.type != "DFFRE" && chc.type != "DFFSE")
                    || (starts_with(chc.type, "MUX2_LUT") && (sk.pin == "I0" || sk.pin == "I1"))) {
                    bad_sink = true;
                    break;
                }
            }
            if (bad_sink) continue;

            int count = static_cast<int>(std::min<int64_t>({
                static_cast<int64_t>((sinks.size() + limit - 1) / limit),
                static_cast<int64_t>(eff_max_groups),
                budget + 1
            }));
            auto split_ng = [](const std::string& s) {
                size_t pos = s.find(".ng_");
                return (pos == std::string::npos) ? s : s.substr(0, pos);
            };
            std::sort(sinks.begin(), sinks.end(), [&](const SinkItem& a, const SinkItem& b) {
                std::string pa = split_ng(a.child), pb = split_ng(b.child);
                if (pa != pb) return pa < pb;
                if (a.child != b.child) return a.child < b.child;
                if (a.pin != b.pin) return a.pin < b.pin;
                return a.idx < b.idx;
            });

            size_t chunk_sz = (sinks.size() + count - 1) / count;
            loads[q] = std::vector<SinkItem>(sinks.begin(), sinks.begin() + std::min(sinks.size(), chunk_sz));

            RtlilCell orig_c = m.cells.at(n);
            size_t g_idx = 0;
            for (size_t off = chunk_sz; off < sinks.size(); off += chunk_sz) {
                ++g_idx;
                size_t end_off = std::min(sinks.size(), off + chunk_sz);
                std::vector<SinkItem> group(sinks.begin() + off, sinks.begin() + end_off);
                std::string cname = n + "_ng_state_replica_" + std::to_string(g_idx);
                if (m.cells.contains(cname)) throw std::runtime_error("Replica namespace collision: " + cname);
                SigBit new_q = ++maximum;
                RtlilCell clone = orig_c;
                clone.connections.set("Q", {new_q});
                clone.attributes.set("keep", "1");
                clone.attributes.set("ng_generated", "1");
                for (const auto& [p, bs] : clone.connections) {
                    if (clone.port_directions.value_or(p, "") == "input") {
                        for (size_t i = 0; i < bs.size(); ++i) loads[bs[i]].push_back(SinkItem{cname, p, static_cast<int>(i)});
                    }
                }
                m.cells.set(cname, std::move(clone));

                for (const auto& sk : group) {
                    auto& cv = m.cells.at(sk.child).connections.at(sk.pin);
                    if (cv[static_cast<size_t>(sk.idx)] != q) throw std::runtime_error("Stale sink partition");
                    cv[static_cast<size_t>(sk.idx)] = new_q;
                }
                loads[new_q] = group;

                JsonValue rec = JsonValue::object();
                rec["module"] = mn;
                rec["source"] = n;
                rec["clone"] = cname;
                rec["old_q"] = q;
                rec["new_q"] = new_q;
                rec["original_fanout"] = static_cast<int64_t>(sinks.size());
                rec["sinks"] = static_cast<int64_t>(group.size());
                rec["init"] = init_s;
                records.push_back(std::move(rec));
                --budget;
            }
        }
    }

    int64_t n_clones = static_cast<int64_t>(records.as_arr().size());
    JsonValue out = JsonValue::object();
    out["schema"] = "initialized-sync-ff-replication-v1";
    out["copies"] = std::move(records);
    out["clones"] = n_clones;
    out["limit"] = limit;
    out["max_groups"] = max_groups;
    out["area_allowance"] = area_allowance;
    out["implementation_sha256"] = sha256_file(runtime_paths().core_dll);
    out["model_sha256"] = effective_prim_sim_sha256();
    return {std::move(design), std::move(out)};
}

JsonValue validate_register_replication_graph(const RtlilDesign& before, const RtlilDesign& after, const JsonValue& record) {
    RtlilDesign reduced = after.clone_deep();
    std::unordered_map<std::string, std::unordered_map<SigBit, SigBit>> aliases;
    std::unordered_set<std::string> seen_names;

    for (const auto& row : record["copies"].as_arr()) {
        std::string mn = row["module"].as_str();
        std::string src = row["source"].as_str();
        std::string cln = row["clone"].as_str();
        std::string key = mn + "\n" + cln;
        if (seen_names.count(key)) throw std::runtime_error("Duplicate replica certificate");
        seen_names.insert(key);

        const RtlilCell& orig = before.mod_ref(mn).cells.at(src);
        const RtlilCell& clone = after.mod_ref(mn).cells.at(cln);
        if (is_preserved_state_attrs(orig.attributes)) throw std::runtime_error("Preserved state cannot be replicated");
        std::string init_s = ff_initial_str(orig);
        if ((orig.type != "DFFRE" && orig.type != "DFFSE") || (init_s != "0" && init_s != "1")) {
            throw std::runtime_error("Unproved state semantics");
        }
        if (init_s != row["init"].as_str() || clone.parameters != orig.parameters) {
            throw std::runtime_error("Replica initialization changed");
        }
        SigBit old_q = static_cast<SigBit>(row["old_q"].as_i64());
        SigBit new_q = static_cast<SigBit>(row["new_q"].as_i64());
        if (orig.connections.at("Q") != std::vector<SigBit>{old_q} || aliases[mn].count(new_q)) {
            throw std::runtime_error("Invalid state correspondence");
        }
        aliases[mn][new_q] = old_q;
        reduced.mod_mut(mn).cells.erase(cln);
    }

    for (const auto& row : record["copies"].as_arr()) {
        std::string mn = row["module"].as_str();
        RtlilCell clone = after.mod_ref(mn).cells.at(row["clone"].as_str());
        const auto& amap = aliases[mn];
        for (auto [p, bs] : clone.connections) {
            if (p != "Q") {
                for (SigBit& b : bs) {
                    auto it = amap.find(b);
                    if (it != amap.end()) b = it->second;
                }
            }
        }
        RtlilCell expected = before.mod_ref(mn).cells.at(row["source"].as_str());
        expected.connections.set("Q", {static_cast<SigBit>(row["new_q"].as_i64())});
        expected.attributes.set("keep", "1");
        expected.attributes.set("ng_generated", "1");
        if (clone != expected) throw std::runtime_error("Replica function/control changed");
    }

    for (const auto& [mn, _] : reduced.modules) {
        auto ait = aliases.find(mn);
        if (ait == aliases.end() || ait->second.empty()) continue;
        RtlilModule& m = reduced.mod_mut(mn);
        for (auto [__, c] : m.cells) {
            for (auto [___, bs] : c.connections) {
                for (SigBit& b : bs) {
                    auto it = ait->second.find(b);
                    if (it != ait->second.end()) b = it->second;
                }
            }
        }
    }
    if (reduced != before) {
        throw std::runtime_error("Changes beyond equal-state replication");
    }

    JsonValue res = JsonValue::object();
    res["status"] = "exact_graph_collapse_passed";
    res["clones"] = static_cast<int64_t>(seen_names.size());
    return res;
}

RtlilDesign collapse_emitted_register_replicas(const RtlilDesign& design, const JsonValue& record) {
    RtlilDesign result = design.clone_deep();
    std::unordered_map<std::string, std::unordered_map<SigBit, SigBit>> aliases;
    std::unordered_set<std::string> seen;

    for (const auto& row : record["copies"].as_arr()) {
        std::string mn = row["module"].as_str();
        std::string src = row["source"].as_str();
        std::string cln = row["clone"].as_str();
        std::string key = mn + "\n" + cln;
        if (seen.count(key)) throw std::runtime_error("Duplicate emitted copy record");
        seen.insert(key);

        const RtlilModule& m = result.mod_ref(mn);
        const RtlilCell& a = m.cells.at(src);
        const RtlilCell& b = m.cells.at(cln);
        std::string ai = ff_initial_str(a);
        if ((a.type != "DFFRE" && a.type != "DFFSE") || ai != row["init"].as_str() || (ai != "0" && ai != "1")) {
            throw std::runtime_error("Emitted source initialization differs");
        }
        if (a.type != b.type || a.parameters != b.parameters || a.port_directions != b.port_directions) {
            throw std::runtime_error("Emitted replica metadata differs");
        }
        aliases[mn][b.connections.at("Q")[0]] = a.connections.at("Q")[0];
    }

    for (const auto& row : record["copies"].as_arr()) {
        std::string mn = row["module"].as_str();
        RtlilModule& m = result.mod_mut(mn);
        const RtlilCell& a = m.cells.at(row["source"].as_str());
        const RtlilCell& b = m.cells.at(row["clone"].as_str());
        const auto& amap = aliases[mn];
        auto norm = [&](std::vector<SigBit> bs) {
            for (SigBit& x : bs) {
                auto it = amap.find(x);
                if (it != amap.end()) x = it->second;
            }
            return bs;
        };
        if (a.connections.size() != b.connections.size()) throw std::runtime_error("Emitted replica pin count differs");
        for (const auto& [p, bs] : b.connections) {
            if (p == "Q") continue;
            if (norm(a.connections.at(p)) != norm(bs)) throw std::runtime_error("Emitted replica transition differs");
        }
        m.cells.erase(row["clone"].as_str());
    }

    for (const auto& [mn, _] : result.modules) {
        auto ait = aliases.find(mn);
        if (ait == aliases.end() || ait->second.empty()) continue;
        RtlilModule& m = result.mod_mut(mn);
        for (auto [__, c] : m.cells) {
            for (auto [___, bs] : c.connections) {
                for (SigBit& x : bs) {
                    auto it = ait->second.find(x);
                    if (it != ait->second.end()) x = it->second;
                }
            }
        }
    }
    return result;
}

JsonValue prove_sdk_register_replication(std::string_view out_dir) {
    std::string out = resolve_path(out_dir);
    make_dirs(out, false);
    std::string lib_p = path_join(runtime_paths().gowin_sdk, "simlib\\gw5a\\prim_sim.v");
    std::string model_sha = verify_or_record_sdk_file_sha(lib_p, EXPECTED_PRIM_SIM_SHA, "SDK FF model (prim_sim.v)");
    std::string text = read_file_text(lib_p);
    std::unordered_map<std::string, UnitSpan> umap;
    for (const auto& u : verilog_units(text)) umap[u.name] = u;

    std::vector<std::string> body;
    for (const char* kind : {"DFFRE", "DFFSE"}) {
        const auto& sp = umap.at(kind);
        std::string model = text.substr(sp.start, sp.end - sp.start);
        size_t gp = model.find("wire gsrt = GSR.GSRO;");
        if (gp == std::string::npos) throw std::runtime_error("Unknown FF global-reset model");
        model.replace(gp, 21, "wire gsrt = global_gsr;");
        if (size_t p1 = model.find("CLK, CE, RESET);"); p1 != std::string::npos) {
            model.replace(p1, 16, "CLK, CE, RESET, global_gsr);");
        }
        if (size_t p2 = model.find("CLK, CE, SET);"); p2 != std::string::npos) {
            model.replace(p2, 14, "CLK, CE, SET, global_gsr);");
        }
        size_t rq = model.find("reg Q_reg;");
        if (rq == std::string::npos) throw std::runtime_error("Missing reg Q_reg in SDK FF");
        model.replace(rq, 10, "input global_gsr;\nreg Q_reg;");

        // Strip `always @(gsrt) begin if(!gsrt) assign Q_reg = INIT; else deassign Q_reg; end`
        size_t aw_gsr = model.find("always @(gsrt)");
        if (aw_gsr == std::string::npos) throw std::runtime_error("Unknown SDK FF gsrt block");
        size_t aw_end = model.find("end", model.find("deassign Q_reg;", aw_gsr));
        if (aw_end == std::string::npos) throw std::runtime_error("Malformed SDK FF gsrt block");
        model.erase(aw_gsr, (aw_end + 3) - aw_gsr);

        size_t aw_clk = model.find("always @(posedge CLK) begin");
        if (aw_clk == std::string::npos) throw std::runtime_error("Unknown SDK FF transition");
        model.replace(aw_clk, 27, "always @(posedge CLK or negedge gsrt) begin\n if (!gsrt) Q_reg <= INIT;\n else");
        body.push_back(std::move(model));
    }

    std::vector<std::string> lines = {"module miter(input clk,ce,d,control,global_gsr,output equal);"};
    std::vector<std::string> eq_terms;
    for (auto [kind, ctrl] : {std::pair{"DFFRE", "RESET"}, std::pair{"DFFSE", "SET"}}) {
        for (int init : {0, 1}) {
            std::string label = std::string(kind) + std::to_string(init);
            lines.push_back("wire " + label + "a," + label + "b;");
            for (char side : {'a', 'b'}) {
                lines.push_back(std::string(kind) + " #(.INIT(1'b" + std::to_string(init) + ")) " + label + side
                                + "_cell(.CLK(clk),.CE(ce),.D(d),." + ctrl + "(control),.Q(" + label + side + "),.global_gsr(global_gsr));");
            }
            eq_terms.push_back("(" + label + "a==" + label + "b)");
        }
    }
    lines.push_back("assign equal=" + join_strings(eq_terms, "&") + ";endmodule");

    std::string pv = path_join(out, "proof.v");
    body.insert(body.end(), lines.begin(), lines.end());
    write_file_text(pv, join_strings(body, "\n") + "\n");

    std::string trans_json = path_join(out, "transition.json");
    run_yosys_commands({
        "read_verilog " + quote_yosys_path(pv),
        "hierarchy -top miter", "proc", "flatten", "opt_expr", "opt_clean", "async2sync", "dffunmap",
        "write_json " + quote_yosys_path(trans_json),
        "sat -tempinduct -seq 2 -maxsteps 3 -prove equal 1 -verify -timeout 30"
    }, out, "proof", 45.0);

    std::string plog = read_file_text(path_join(out, "proof.log"));
    if (plog.find("SUCCESS!") == std::string::npos) throw std::runtime_error("State replication proof failed");

    RtlilDesign tr_des = read_rtlil_json_file(trans_json);
    const RtlilModule& miter = tr_des.mod_ref("miter");
    int dff_cnt = 0;
    for (const auto& [_, c] : miter.cells) if (c.type == "$dff") ++dff_cnt;
    if (dff_cnt != 8 || miter.ports.at("equal").bits[0] < 0) {
        throw std::runtime_error("Reject vacuous register replication proof");
    }

    JsonValue res = JsonValue::object();
    res["status"] = "proved";
    res["model_sha256"] = model_sha;
    res["proof_source_sha256"] = sha256_file(pv);
    res["proof_log_sha256"] = sha256_file(path_join(out, "proof.log"));
    res["preserved_state_cells"] = 8;
    res["transition_sha256"] = sha256_file(trans_json);
    res["scope"] = "Identical constant INIT and inputs; temporal induction including shared global reset. SDK force-to-INIT projected to synthesizable async-reset form.";
    res["negative_control"] = false;
    return res;
}

// ============================================================================
// 7. resource_preflight::count & analyze_route_fanout::analyze
// ============================================================================
JsonValue count_resource_preflight(const RtlilDesign& design, std::string_view top_sv) {
    std::unordered_map<std::string, std::map<std::string, int64_t>> memo;
    std::unordered_set<std::string> active;

    std::function<const std::map<std::string, int64_t>&(const std::string&)> walk;
    walk = [&](const std::string& name) -> const std::map<std::string, int64_t>& {
        auto mit = memo.find(name);
        if (mit != memo.end()) return mit->second;
        if (active.count(name)) throw std::runtime_error("Recursive implementation hierarchy: " + name);
        active.insert(name);
        std::map<std::string, int64_t> counts;
        for (const auto& [_, cell] : design.mod_ref(name).cells) {
            const std::string& child = cell.type;
            const auto* cmod = design.modules.find_ptr(child);
            if (cmod && *cmod && !is_module_blackbox(**cmod)) {
                for (const auto& [k, v] : walk(child)) counts[k] += v;
            } else if (child != "$scopeinfo") {
                counts[child] += 1;
            }
        }
        active.erase(name);
        return memo.emplace(name, std::move(counts)).first->second;
    };

    const auto& cells = walk(std::string(top_sv));
    int64_t logic = 0, total = 0;
    JsonValue lc_obj = JsonValue::object();
    for (const auto& [k, v] : cells) {
        lc_obj[k] = v;
        total += v;
        if (starts_with(k, "RAM16")) logic += 6 * v;
        else if (starts_with(k, "LUT") || k == "ALU" || k == "ROM16") logic += v;
    }

    JsonValue out = JsonValue::object();
    out["leaf_cells"] = std::move(lc_obj);
    out["expanded_leaf_count"] = total;
    out["estimated_logic_cells"] = logic;
    out["scope"] = "Pre-packing structural count; not a routed resource report. Opaque macros count as one leaf.";
    return out;
}

JsonValue analyze_route_fanout(const RtlilDesign& design, std::string_view top_sv, int threshold = 64) {
    std::unordered_map<std::string, std::string> parent;
    auto key_of = [](const std::string& path_s, SigBit bit) -> std::string {
        if (bit < 0) return std::string("constant:") + const_bit_char(bit);
        return "net:" + path_s + "#" + std::to_string(bit);
    };
    auto uf_find = [&](std::string k) -> std::string {
        std::string root = k;
        while (true) {
            auto it = parent.find(root);
            if (it == parent.end() || it->second == root) break;
            root = it->second;
        }
        while (k != root) {
            auto it = parent.find(k);
            std::string nxt = (it == parent.end()) ? root : it->second;
            parent[k] = root;
            k = nxt;
        }
        return root;
    };
    auto uf_join = [&](const std::string& a, const std::string& b) {
        std::string ra = uf_find(a), rb = uf_find(b);
        if (ra != rb) parent[rb] = ra;
    };
    auto is_opaque = [&](const std::string& mname) {
        const auto* mp = design.modules.find_ptr(mname);
        if (!mp || !*mp) return true;
        return is_attr_truthy((*mp)->attributes, "blackbox") || is_attr_truthy((*mp)->attributes, "whitebox");
    };

    struct LeafRef { std::string child_path; std::string parent_path; const RtlilCell* cell; };
    std::vector<LeafRef> leaves;
    std::vector<std::pair<std::string, std::string>> instances;

    std::function<void(const std::string&, const std::string&, std::unordered_set<std::string>&)> walk;
    walk = [&](const std::string& name, const std::string& path_s, std::unordered_set<std::string>& stack) {
        if (stack.count(name)) throw std::runtime_error("Recursive hierarchy: " + name);
        stack.insert(name);
        instances.emplace_back(name, path_s);
        const RtlilModule& mod = design.mod_ref(name);
        for (const auto& [cname, cell] : mod.cells) {
            if (cell.type == "$scopeinfo") continue;
            std::string cpath = path_s.empty() ? cname : (path_s + "/" + cname);
            if (is_opaque(cell.type)) {
                leaves.push_back(LeafRef{cpath, path_s, &cell});
                continue;
            }
            const RtlilModule& cmod = design.mod_ref(cell.type);
            for (const auto& [port, prow] : cmod.ports) {
                const auto* bits = cell.connections.find_ptr(port);
                if (!bits && prow.direction == "output") continue;
                if (!bits || bits->size() != prow.bits.size()) {
                    throw std::runtime_error("Hierarchy port mismatch: " + cpath + "/" + port);
                }
                for (size_t i = 0; i < bits->size(); ++i) {
                    uf_join(key_of(path_s, (*bits)[i]), key_of(cpath, prow.bits[i]));
                }
            }
            walk(cell.type, cpath, stack);
        }
        stack.erase(name);
    };

    std::unordered_set<std::string> st;
    walk(std::string(top_sv), "", st);

    std::unordered_set<std::string> constants;
    for (SigBit cb : {SIG_0, SIG_1, SIG_X, SIG_Z}) constants.insert(uf_find(key_of("", cb)));

    auto role_of = [](std::string_view pin) -> std::string {
        std::string p = to_upper_ascii(pin);
        static const std::unordered_set<std::string> clks = {"CLK","CLKA","CLKB","CLK0","CLK1","CLKIN","FCLK","PCLK","ECLK","WCLK","RCLK","G"};
        static const std::unordered_set<std::string> srs  = {"RESET","RESETN","RESET_N","RST","RSTN","SET","CLEAR","PRESET","LSR","RESETA","RESETB"};
        static const std::unordered_set<std::string> ens  = {"CE","CEA","CEB","OCE","OCEA","OCEB","WRE","WREA","WREB","EN","ENABLE","GE"};
        if (clks.count(p)) return "clock";
        if (srs.count(p)) return "set_reset";
        if (ens.count(p)) return "enable";
        return "data_logic";
    };

    struct NetStat {
        int64_t sink_pins = 0;
        std::unordered_set<std::string> sink_cells;
        std::map<std::string, int64_t> roles;
        std::map<std::string, int64_t> sink_types;
        JsonValue drivers = JsonValue::array();
        JsonValue sample_sinks = JsonValue::array();
    };
    std::unordered_map<std::string, NetStat> stats;

    for (const auto& lf : leaves) {
        const RtlilCell& cell = *lf.cell;
        for (const auto& [pin, bits] : cell.connections) {
            std::string dir = cell.port_directions.value_or(pin, "");
            if (dir.empty()) {
                if (const auto* mp = design.modules.find_ptr(cell.type); mp && *mp) {
                    if (const auto* pp = (*mp)->ports.find_ptr(pin)) dir = pp->direction;
                }
            }
            if (dir.empty()) throw std::runtime_error("Unknown leaf pin direction: " + cell.type + "/" + pin);
            for (size_t idx = 0; idx < bits.size(); ++idx) {
                std::string net = uf_find(key_of(lf.parent_path, bits[idx]));
                NetStat& row = stats[net];
                if (dir == "input" || dir == "inout") {
                    row.sink_pins += 1;
                    row.sink_cells.insert(lf.child_path);
                    row.roles[role_of(pin)] += 1;
                    row.sink_types[cell.type] += 1;
                    if (row.sample_sinks.as_arr().size() < 5) {
                        JsonValue ep = JsonValue::object();
                        ep["instance"] = lf.child_path; ep["type"] = cell.type; ep["pin"] = pin; ep["index"] = static_cast<int64_t>(idx);
                        row.sample_sinks.push_back(std::move(ep));
                    }
                }
                if ((dir == "output" || dir == "inout") && row.drivers.as_arr().size() < 5) {
                    JsonValue ep = JsonValue::object();
                    ep["instance"] = lf.child_path; ep["type"] = cell.type; ep["pin"] = pin; ep["index"] = static_cast<int64_t>(idx);
                    row.drivers.push_back(std::move(ep));
                }
            }
        }
        if (cell.type == "VCC" || cell.type == "GND") {
            for (const auto& [_, bs] : cell.connections) {
                for (SigBit b : bs) constants.insert(uf_find(key_of(lf.parent_path, b)));
            }
        }
    }

    std::unordered_set<std::string> selected;
    for (const auto& [net, row] : stats) {
        if (row.sink_pins >= threshold && !constants.count(net)) selected.insert(net);
    }

    std::unordered_map<std::string, std::vector<std::string>> aliases;
    for (const auto& [mname, path_s] : instances) {
        const RtlilModule& mod = design.mod_ref(mname);
        for (const auto& [n, nr] : mod.netnames) {
            for (size_t i = 0; i < nr.bits.size(); ++i) {
                std::string net = uf_find(key_of(path_s, nr.bits[i]));
                if (!selected.count(net) || aliases[net].size() >= 8) continue;
                std::string sfx = (nr.bits.size() != 1) ? ("[" + std::to_string(static_cast<int>(i) + nr.offset.value_or(0)) + "]") : "";
                aliases[net].push_back(path_s.empty() ? (n + sfx) : (path_s + "/" + n + sfx));
            }
        }
    }

    struct RowSort { int64_t sink_pins; std::string net_key; JsonValue jv; bool is_clk; };
    std::vector<RowSort> rows;
    for (const auto& net : selected) {
        auto& st_r = stats.at(net);
        JsonValue rj = JsonValue::object();
        rj["sink_pins"] = st_r.sink_pins;
        rj["sink_cells"] = static_cast<int64_t>(st_r.sink_cells.size());
        JsonValue roles_j = JsonValue::object();
        for (const auto& [rk, rv] : st_r.roles) roles_j[rk] = rv;
        JsonValue stypes_j = JsonValue::object();
        for (const auto& [tk, tv] : st_r.sink_types) stypes_j[tk] = tv;
        JsonValue al_j = JsonValue::array();
        for (const auto& as : aliases[net]) al_j.push_back(as);
        rj["roles"] = std::move(roles_j);
        rj["sink_types"] = std::move(stypes_j);
        rj["drivers"] = std::move(st_r.drivers);
        rj["sample_sinks"] = std::move(st_r.sample_sinks);
        rj["aliases"] = std::move(al_j);
        rj["net_key"] = net;
        bool has_clk = (st_r.roles.count("clock") > 0 && st_r.roles.at("clock") > 0);
        rows.push_back(RowSort{st_r.sink_pins, net, std::move(rj), has_clk});
    }
    std::sort(rows.begin(), rows.end(), [](const RowSort& a, const RowSort& b) {
        if (a.sink_pins != b.sink_pins) return a.sink_pins > b.sink_pins;
        return a.net_key < b.net_key;
    });

    int64_t nonclk_hf = 0;
    JsonValue nets_arr = JsonValue::array();
    for (auto& r : rows) {
        if (!r.is_clk) ++nonclk_hf;
        nets_arr.push_back(std::move(r.jv));
    }

    JsonValue out = JsonValue::object();
    out["schema"] = "nextgowin-route-fanout-v1";
    out["scope"] = "Pre-P&R expanded leaf-pin fanout; not physical LW candidate/allocation counts. Constants excluded; opaque IP internal nets are unobserved.";
    out["top"] = std::string(top_sv);
    out["threshold"] = threshold;
    out["focus"] = JsonValue(nullptr);
    out["leaf_cells"] = static_cast<int64_t>(leaves.size());
    out["high_fanout_nets"] = static_cast<int64_t>(nets_arr.as_arr().size());
    out["high_fanout_nonclock_nets"] = nonclk_hf;
    out["nets"] = std::move(nets_arr);
    return out;
}

} // namespace

// ============================================================================
// Stage 5 Entrypoint: materialize_netlist_stage
// ============================================================================
JsonValue materialize_netlist_stage(
    std::string_view source_json,
    const RtlilDesign* in_mem_mapped,
    std::string_view out_dir,
    std::string_view top,
    std::string_view vendor_library,
    std::optional<int64_t> max_logic_cells,
    const OrderedMap<JsonValue>& net_aliases,
    bool optimize_resources,
    bool map_registers,
    bool register_alternatives,
    const JsonValue& logic_replication,
    const JsonValue& register_replication,
    const JsonValue& dedicated_cluster_replication,
    double register_control_penalty_ps
) {
    std::string source = resolve_path(source_json);
    std::string out = resolve_path(out_dir);
    make_dirs(out, false);

    RtlilDesign design = in_mem_mapped ? in_mem_mapped->clone_cow() : read_rtlil_json_file(source);

    // Lower any pre-synthesized cross-CFU MUX2_LUT8 cells (from encrypted IP sub-netlists) into
    // functionally equivalent LUT3 (O = S0 ? I1 : I0, INIT = 8'hCA = "11001010") so child MUX2_LUT7
    // trees pack independently into single CFUs without cross-CFU rigidity.
    for (const auto& [mn, mptr] : design.modules) {
        if (is_module_blackbox(*mptr)) continue;
        RtlilModule& mod = design.mod_mut(mn);
        for (auto [cname, cell] : mod.cells) {
            if (cell.type != "MUX2_LUT8") continue;
            const auto* i0v = cell.connections.find_ptr("I0");
            const auto* i1v = cell.connections.find_ptr("I1");
            const auto* s0v = cell.connections.find_ptr("S0");
            const auto* ov  = cell.connections.find_ptr("O");
            if (!i0v || i0v->empty() || !i1v || i1v->empty() || !s0v || s0v->empty() || !ov || ov->empty()) continue;
            SigBit i0_b = (*i0v)[0], i1_b = (*i1v)[0], s0_b = (*s0v)[0], o_b = (*ov)[0];
            cell.type = "LUT3";
            cell.parameters = OrderedMap<std::string>();
            cell.parameters.set("INIT", "11001010");
            cell.port_directions = OrderedMap<std::string>();
            cell.port_directions.set("I0", "input");
            cell.port_directions.set("I1", "input");
            cell.port_directions.set("I2", "input");
            cell.port_directions.set("F", "output");
            cell.connections = OrderedMap<std::vector<SigBit>>();
            cell.connections.set("I0", {i0_b});
            cell.connections.set("I1", {i1_b});
            cell.connections.set("I2", {s0_b});
            cell.connections.set("F", {o_b});
        }
    }

    std::vector<std::pair<std::string, std::string>> authorized_packed;
    bool any_packed_oreg = false;
    for (const auto& [_, mptr] : design.modules) {
        for (const auto& [__, c] : mptr->cells) {
            if (is_attr_truthy(c.attributes, "ng_packed_oreg")) { any_packed_oreg = true; break; }
        }
        if (any_packed_oreg) break;
    }
    std::string res_dir = path_join(parent_dir(parent_dir(source)), "03_resources");
    if (any_packed_oreg) {
        authorized_packed = dsp_output_register_validate(
            design, parse_json_file(path_join(res_dir, "dsp_output_packing.json")), res_dir
        );
    }

    if (register_alternatives) {
        JsonValue ch_rec = parse_json_file(path_join(res_dir, "register_choices.json"));
        auto [sel_des, selection] = register_alternatives_select(design, ch_rec, register_control_penalty_ps);
        design = std::move(sel_des);
        write_json_pretty(path_join(out, "register_selection.json"), selection);
    }

    auto [padded_des, pads] = insert_gowin_pads(design, top);
    design = std::move(padded_des);
    pads["constraint_net_aliases"] = bind_net_aliases(design, net_aliases);
    pads["legalized_flipflop_aliases"] = legalize_gw5a_aliases(design);
    if (optimize_resources) {
        pads["dsp_explicit_configuration"] = gw5a_dsp_explicit_configuration(design, authorized_packed);
    }
    pads["completed_unused_outputs"] = complete_output_ports(design);

    if (map_registers) {
        auto [f_des, reg_map] = fold_register_controls(design);
        design = std::move(f_des);
        write_json_pretty(path_join(out, "register_mapping.json"), reg_map);
        pads["register_nextstate_rewrites"] = reg_map["rewrites"];
    }

    if (optimize_resources) {
        auto [opt_des, optimization] = optimize_native_json(design, 64);
        design = std::move(opt_des);
        write_json_pretty(path_join(out, "optimizations.json"), optimization);
    }

    bool do_logic_rep = (logic_replication.is_bool() && logic_replication.as_bool()) || logic_replication.is_object();
    if (do_logic_rep) {
        int lim = 32, mgrp = 8;
        double allow = 0.02;
        if (logic_replication.is_object()) {
            if (const auto* v = logic_replication.get("limit")) lim = static_cast<int>(v->as_i64(32));
            if (const auto* v = logic_replication.get("max_groups")) mgrp = static_cast<int>(v->as_i64(8));
            if (const auto* v = logic_replication.get("area_allowance")) allow = v->as_f64(0.02);
        }
        RtlilDesign before = design;
        auto [rep_des, rep_rec] = replicate_native_luts(design, lim, mgrp, allow);
        design = std::move(rep_des);
        rep_rec["proof"] = prove_logic_replication_cec(before, design, path_join(out, "replication_proof"));
        rep_rec["implementation_sha256"] = sha256_file(runtime_paths().core_dll);
        write_json_pretty(path_join(out, "logic_replication.json"), rep_rec);
    }

    bool do_reg_rep = (register_replication.is_bool() && register_replication.as_bool()) || register_replication.is_object();
    bool do_cls_rep = (dedicated_cluster_replication.is_bool() && dedicated_cluster_replication.as_bool()) || dedicated_cluster_replication.is_object();
    bool has_rep_before = (do_reg_rep || do_cls_rep);
    JsonValue native_replication = JsonValue::object();

    if (has_rep_before) {
        name_unaliased_bits(design);
        write_rtlil_json_file(path_join(out, "pre_replication.json"), design);
    }

    if (do_reg_rep) {
        int lim = 48, mgrp = 8;
        double allow = 0.005;
        if (register_replication.is_object()) {
            if (const auto* v = register_replication.get("limit")) lim = static_cast<int>(v->as_i64(48));
            if (const auto* v = register_replication.get("max_groups")) mgrp = static_cast<int>(v->as_i64(8));
            if (const auto* v = register_replication.get("area_allowance")) allow = v->as_f64(0.005);
        }
        RtlilDesign previous = design;
        auto [r_des, state_rec] = replicate_native_registers(design, lim, mgrp, allow);
        design = std::move(r_des);
        state_rec["graph_proof"] = validate_register_replication_graph(previous, design, state_rec);
        state_rec["state_proof"] = prove_sdk_register_replication(path_join(out, "register_replication_state_proof"));
        native_replication["registers"] = std::move(state_rec);
    }

    if (optimize_resources && (do_logic_rep || do_reg_rep)) {
        int64_t post_rep_lut_perm = 0;
        for (const auto& [mn, mptr] : design.modules) {
            if (starts_with(mn, "~") || is_module_blackbox(*mptr)) continue;
            auto [p_cnt, _] = optimize_module_lut_pins(design.mod_mut(mn), true);
            post_rep_lut_perm += p_cnt;
        }
        if (post_rep_lut_perm > 0) {
            native_replication["post_replication_lut_pin_permutations"] = post_rep_lut_perm;
        }
    }

    pads["named_unaliased_bits"] = name_unaliased_bits(design);
    pads["dedicated_mux_packing"] = validate_dedicated_mux_packing(design);

    JsonValue resources = count_resource_preflight(design, top);
    write_json_pretty(path_join(out, "resource_preflight.json"), resources);
    if (max_logic_cells.has_value() && resources["estimated_logic_cells"].as_i64() > *max_logic_cells) {
        throw std::runtime_error("Expanded logic exceeds the configured preflight budget; optimize before native P&R");
    }

    std::string with_pads_p = path_join(out, "with_pads.json");
    std::string wp_txt = dump_rtlil_json_compact(design);
    write_file_text(with_pads_p, wp_txt);

    JsonValue pressure = analyze_route_fanout(design, top, 64);
    pressure["input_sha256"] = sha256_str(wp_txt);
    write_json_pretty(path_join(out, "route_fanout.json"), pressure);
    std::printf("Routing diagnostic: %lld non-clock nets with fanout >= %lld (physical LW allocation unmeasured)\n",
                static_cast<long long>(pressure["high_fanout_nonclock_nets"].as_i64()),
                static_cast<long long>(pressure["threshold"].as_i64()));
    std::fflush(stdout);
    write_json_pretty(path_join(out, "pads.json"), pads);

    std::string emit_seed_il = path_join(out, "seed_autoidx.il");
    write_file_text(emit_seed_il, "autoidx 15000000\n");

    std::vector<std::string> commands = {
        "read_rtlil " + quote_yosys_path(emit_seed_il),
        "read_json " + quote_yosys_path(with_pads_p),
        "read_rtlil " + quote_yosys_path(emit_seed_il),
        "read_verilog -overwrite -lib +/gowin/cells_sim.v",
        "read_verilog -overwrite -lib +/gowin/cells_xtra_gw5a.v",
    };
    bool has_gw5a_dl = false;
    for (const auto& [_, mptr] : design.modules) {
        if (is_module_blackbox(*mptr)) continue;
        for (const auto& [__, c] : mptr->cells) {
            if ((c.type == "DLCE" || c.type == "DLPE") && c.connections.contains("G")) {
                has_gw5a_dl = true;
                break;
            }
        }
        if (has_gw5a_dl) break;
    }
    if (has_gw5a_dl) {
        std::string dl_lib_p = path_join(out, "gw5a_dl_cells.v");
        write_file_text(
            dl_lib_p,
            "(* blackbox = 1 *) module DLCE (output Q, input D, G, GE, CLEAR); parameter INIT = 1'b0; endmodule\n"
            "(* blackbox = 1 *) module DLPE (output Q, input D, G, GE, PRESET); parameter INIT = 1'b1; endmodule\n"
        );
        commands.push_back("read_verilog -overwrite -lib " + quote_yosys_path(dl_lib_p));
    }
    if (!vendor_library.empty() && file_exists(vendor_library)) {
        commands.push_back("read_verilog -overwrite -lib " + quote_yosys_path(vendor_library));
    }
    std::string dsp_lib = path_join(res_dir, "dsp_cells.v");
    if (file_exists(dsp_lib)) {
        commands.push_back("read_verilog -overwrite -lib " + quote_yosys_path(dsp_lib));
    }
    commands.push_back("blackbox =A:whitebox");
    commands.push_back("hierarchy -check -top " + std::string(top));
    commands.push_back("setundef -params -zero");
    commands.push_back("hilomap -singleton -hicell VCC V -locell GND G");
    commands.push_back("clean -purge");
    commands.push_back("check -assert -noinit");
    commands.push_back("splitnets -ports");
    commands.push_back("write_json " + quote_yosys_path(path_join(out, "final.json")));
    commands.push_back("write_verilog -simple-lhs -decimal -noattr -defparam -renameprefix ng_ " + quote_yosys_path(path_join(out, "raw.vg")));

    JsonValue invocation;
    if (has_rep_before) {
        std::vector<std::string> ref_cmds(commands.begin(), commands.end() - 2);
        ref_cmds[1] = "read_json " + quote_yosys_path(path_join(out, "pre_replication.json"));
        ref_cmds.push_back("write_json " + quote_yosys_path(path_join(out, "pre_replication_canonical.json")));

        std::string err_ref, err_act;
        JsonValue inv_act;
        std::thread t_ref([&]() {
            try { run_yosys_commands(ref_cmds, out, "emit_ref"); }
            catch (const std::exception& e) { err_ref = e.what(); }
        });
        std::thread t_act([&]() {
            try { inv_act = run_yosys_commands(commands, out, "emit"); }
            catch (const std::exception& e) { err_act = e.what(); }
        });
        t_ref.join();
        t_act.join();
        remove_file_if_exists(emit_seed_il);
        if (!err_ref.empty()) throw std::runtime_error(err_ref);
        if (!err_act.empty()) throw std::runtime_error(err_act);
        invocation = std::move(inv_act);
    } else {
        invocation = run_yosys_commands(commands, out, "emit");
        remove_file_if_exists(emit_seed_il);
    }

    if (has_rep_before) {
        std::string final_json_p = path_join(out, "final.json");
        std::string pre_can_p = path_join(out, "pre_replication_canonical.json");
        RtlilDesign actual = read_rtlil_json_file(final_json_p);
        if (do_reg_rep) {
            actual = collapse_emitted_register_replicas(actual, native_replication["registers"]);
        }
        RtlilDesign pre_can = read_rtlil_json_file(pre_can_p);
        native_replication["actual_emitted_boundary_cec"] = prove_native_boundary_cec(
            pre_can, actual, path_join(out, "actual_replication_cec"), 300.0
        );
        native_replication["reference_sha256"] = sha256_file(pre_can_p);
        native_replication["actual_sha256"] = sha256_file(final_json_p);
        write_json_pretty(path_join(out, "native_replication.json"), native_replication);
    }

    invocation["route_fanout_report"] = "route_fanout.json";
    std::string raw_vg_p = path_join(out, "raw.vg");
    auto [norm_text, fmt_rec] = normalize_emitted_vg(read_file_text(raw_vg_p));
    std::string core_vg_p = path_join(out, "core.vg");
    std::string mapped_vg_p = path_join(out, "mapped.vg");
    write_file_text(core_vg_p, norm_text);
    write_file_text(mapped_vg_p, norm_text);
    fmt_rec["raw_sha256"] = sha256_file(raw_vg_p);
    fmt_rec["core_sha256"] = sha256_str(norm_text);
    write_json_pretty(path_join(out, "format.json"), fmt_rec);
    return invocation;
}

} // namespace ng
