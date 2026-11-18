#pragma once
#pragma fp_contract(off)

#include "ng_json.hpp"

#include <array>
#include <optional>
#include <string>
#include <string_view>
#include <tuple>
#include <utility>
#include <vector>

namespace ng {

// Pinned Gowin V1.9.12.04_x64 SHA-256 constants (GW5AST-138C & GW5AST-138B)
inline constexpr const char* EXPECTED_GWSYN_DLL_SHA   = "254eac1ed05c7d1168889473dcc85203032fa4cd641c83bd5bbe4ea61a55b6b0";
inline constexpr const char* EXPECTED_GOWIN_DLL_SHA   = "bc9472a5a22cf4a94e39e07439f261f3354c668f92ef9abc735e6678bea53b7b";
inline constexpr const char* EXPECTED_PRIM_SIM_SHA    = "7109c2568188627465d218bc099cf72f12e90807c21d4d862d2335b499043544";
inline constexpr const char* EXPECTED_DAT_SHA         = "19e3ec885f359ec571c09ed5d3face1b0c10971b9e0f569fdf703911993c9d33";
inline constexpr const char* EXPECTED_FSE_SHA         = "19f859e61df5b8da361330d42d1250bd31d03bcfa2912c3ccfbf983cc4329030";
inline constexpr const char* EXPECTED_TM_SHA          = "a3558f35685ebc58eefce834e82059ce651b9e2a6cbca2d6668094153ca0707f";
inline constexpr const char* EXPECTED_FS_HDR_SHA      = "18209683e46c9ba4a9d92c0045e647c5342f138798d990ccac9effe7189f152e";
inline constexpr const char* EXPECTED_138B_DAT_SHA    = "481f2d3a5e373bac0779948b6b2f8eec43a0a2dd62b058a2cce4d7cb4e275586";
inline constexpr const char* EXPECTED_138B_FSE_SHA    = "495da9737f17f5545978cc4b68899826ff0806073b1e26251502614ce21a74ae";
inline constexpr const char* EXPECTED_138B_TM_SHA     = "fffb1704a2d262cf4d7d1db21bb7be0419d573242a0bd606132858db5a18a44e";
inline constexpr const char* EXPECTED_138B_FS_HDR_SHA = "326ffe3d142d4edbadd4173a89bc337c7129a1da9003d4ff3aea5756c8859c54";

inline constexpr const char* PINNED_DEVICE = "GW5AST-138C-FCPBGA676A-1";
inline constexpr const char* PINNED_PART   = "GW5AST-LV138FPG676AC1/I0";
inline constexpr const char* SUPPORTED_GOWIN_VERSION = "Gowin V1.9.12.04 x64";

struct TargetDeviceSpec {
    std::string code;          // e.g. "gw5ast138c-003" or "gw5ast138b-002"
    std::string series;        // e.g. "GW5AST"
    std::string device_name;   // e.g. "GW5AST-138C" or "GW5AST-138B"
    std::string package_name;  // e.g. "FCPBGA676A"
    std::string speed_num;     // e.g. "1" or "2"
    std::string speed_grade;   // e.g. "C1/I0", "ES", "C2/I1"
    std::string part_number;   // e.g. "GW5AST-LV138FPG676AC1/I0"
    std::string dev_p;         // e.g. "GW5AST-138C-FCPBGA676A-1"
    bool has_ssram = true;     // true when device_info.csv reports distributed SSRAM (GW5AST-138C)
};

TargetDeviceSpec resolve_target_device(
    std::string_view dev_code,
    std::string_view dev_name,
    std::string_view part_num
);

inline bool allow_unsupported_gowin_sdk() noexcept {
    return runtime_paths().allow_unsupported_gowin;
}

inline std::string verify_or_record_sdk_file_sha(
    std::string_view abs_path,
    std::string_view expected_sha,
    const char* label
) {
    std::string actual = sha256_file(abs_path);
    if (actual != expected_sha && !allow_unsupported_gowin_sdk()) {
        throw std::runtime_error(
            std::string(label) + " SHA-256 differs from validated " + SUPPORTED_GOWIN_VERSION
        );
    }
    return actual;
}

inline std::string effective_prim_sim_sha256() {
    std::string lib_path = path_join(runtime_paths().gowin_sdk, "simlib\\gw5a\\prim_sim.v");
    return verify_or_record_sdk_file_sha(lib_path, EXPECTED_PRIM_SIM_SHA, "simlib/gw5a/prim_sim.v");
}

// ============================================================================
// Stage 1-3: Frontend, Verific Bridge & Resource Inference (ng_frontend_bridge.cpp)
// ============================================================================
int run_internal_frontend_worker(std::string_view script_path);

JsonValue export_gowin_frontend(std::string_view project_path, std::string_view out_dir, double timeout_sec = 600.0);

std::tuple<std::string, std::string, JsonValue> gowin_macros_prepare(
    std::string_view exported_v,
    std::string_view profile_path,
    std::string_view rao_path
);

std::pair<std::string, JsonValue> verific_bridge_convert(std::string_view exported_v);

std::string gw5a_dsp_primitive_library();
const OrderedMap<OrderedMap<std::string>>& gowin_prim_sim_formats();
JsonValue gw5a_dsp_explicit_configuration(
    RtlilDesign& design,
    const std::vector<std::pair<std::string, std::string>>& authorized_packed
);

std::pair<RtlilDesign, JsonValue> lower_carry_only_chains(const RtlilDesign& design, int max_chain_len = 1000000000);

bool is_kept_cell_or_net(const OrderedMap<JsonValue>& attrs);
std::pair<RtlilDesign, JsonValue> dsp_output_register_pack(const RtlilDesign& source, bool allow_inverted_enable);
JsonValue dsp_output_register_prove_template(std::string_view out_dir, bool negative_control = false, int enable_polarity = 1);
std::vector<std::pair<std::string, std::string>> dsp_output_register_validate(
    const RtlilDesign& design,
    const JsonValue& record,
    std::string_view folder
);

std::pair<RtlilDesign, JsonValue> register_alternatives_add(const RtlilDesign& source);
std::pair<RtlilDesign, JsonValue> register_alternatives_select(
    const RtlilDesign& source,
    const JsonValue& record,
    double control_penalty_ps = 0.0
);

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
);

// ============================================================================
// Stage 4: Mapping & Boundary CEC (ng_mapper_cec.cpp)
// ============================================================================
struct ExtractedCut {
    OrderedMap<bool> gates;
    std::vector<std::string> inputs;
    std::vector<std::string> outputs;
    BlifNetwork blif_net;
};

std::optional<ExtractedCut> extract_module_boolean_cut(const RtlilModule& module);
std::string readback_acd_module_blif(const RtlilModule& module, const ExtractedCut& cut);

std::tuple<RtlilDesign, RtlilDesign, std::array<int64_t, 2>> box_scoped_candidate(
    const RtlilDesign& before,
    const RtlilDesign& mapped,
    const std::vector<std::string>& active
);

JsonValue prove_native_boundary_cec(
    const RtlilDesign& before,
    const RtlilDesign& after,
    std::string_view out_dir,
    double timeout_sec = 300.0
);

std::pair<JsonValue, std::optional<RtlilDesign>> map_resource_design(
    std::string_view source_json,
    std::string_view out_dir,
    double period_ps,
    std::string_view strategy,
    std::string_view architecture_recipe,
    bool return_in_memory = true
);

// ============================================================================
// Stage 5: Netlist Emission & Native Optimizations (ng_emitter_opt.cpp)
// ============================================================================
struct CellLogicFunc {
    std::vector<SigBit> inputs;
    SigBit output = SIG_0;
    uint64_t truth = 0;
};
std::optional<CellLogicFunc> extract_lut_or_mux_logic(const RtlilCell& c);

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
);

// ============================================================================
// Stage 6: Custom P&R, STA, Reports & Frame Integrity (ng_ownpr_driver.cpp)
// ============================================================================
int run_internal_pnr_worker(std::string_view out_dir);

JsonValue audit_project_timing(std::string_view pnr_dir, const JsonValue& profile);
JsonValue parse_gowin_tr_summaries(std::string_view tr_text, double hold_uncertainty_ns = 0.0);
JsonValue audit_fs_bitstream_integrity(std::string_view fs_path);

std::pair<JsonValue, JsonValue> run_ownpr_full(
    std::string_view mapped_vg,
    std::string_view final_json,
    std::string_view sdc_path,
    std::string_view cst_path,
    std::string_view device_cfg_path,
    std::string_view physical_profile_path,
    std::string_view out_dir,
    int timeout_sec,
    std::string_view target_dev_p = PINNED_DEVICE,
    std::string_view target_part  = PINNED_PART,
    std::string_view csr_path     = "",
    double global_freq_mhz        = 0.0
);

} // namespace ng
