#pragma once
#pragma fp_contract(off)

#include "ng_common.hpp"

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <variant>
#include <vector>

namespace ng {

// ============================================================================
// OrderedMap<V>: Exact Python-dict insertion/overwrite/delete order semantics
// ============================================================================
template <typename V>
class OrderedMap {
public:
    struct Entry {
        std::string key;
        V value;
        bool alive = true;
    };

    template <bool IsConst>
    class Iter {
    public:
        using VecPtr = std::conditional_t<IsConst, const std::vector<Entry>*, std::vector<Entry>*>;
        using ValRef = std::conditional_t<IsConst, const V&, V&>;

        struct Proxy {
            const std::string& first;
            ValRef second;
        };

        Iter(VecPtr vec, size_t idx) noexcept : vec_(vec), idx_(idx) { advance_to_alive(); }

        Proxy operator*() const noexcept {
            auto& e = (*vec_)[idx_];
            return Proxy{e.key, e.value};
        }

        Iter& operator++() noexcept {
            ++idx_;
            advance_to_alive();
            return *this;
        }

        bool operator==(const Iter& o) const noexcept { return idx_ == o.idx_; }
        bool operator!=(const Iter& o) const noexcept { return idx_ != o.idx_; }

    private:
        void advance_to_alive() noexcept {
            while (vec_ && idx_ < vec_->size() && !(*vec_)[idx_].alive) {
                ++idx_;
            }
        }
        VecPtr vec_ = nullptr;
        size_t idx_ = 0;
    };

    OrderedMap() = default;

    size_t size() const noexcept { return live_count_; }
    bool empty() const noexcept { return live_count_ == 0; }

    void reserve(size_t n) {
        entries_.reserve(n);
        index_.reserve(n);
    }

    void clear() {
        entries_.clear();
        index_.clear();
        live_count_ = 0;
    }

    bool contains(const std::string& k) const {
        return index_.find(k) != index_.end();
    }

    V* find_ptr(const std::string& k) {
        auto it = index_.find(k);
        return (it == index_.end()) ? nullptr : &entries_[it->second].value;
    }

    const V* find_ptr(const std::string& k) const {
        auto it = index_.find(k);
        return (it == index_.end()) ? nullptr : &entries_[it->second].value;
    }

    V* find(const std::string& k) { return find_ptr(k); }
    const V* find(const std::string& k) const { return find_ptr(k); }

    V& at(const std::string& k) {
        auto* p = find_ptr(k);
        if (!p) throw std::out_of_range(format_missing_key_error(k));
        return *p;
    }

    const V& at(const std::string& k) const {
        auto* p = find_ptr(k);
        if (!p) throw std::out_of_range(format_missing_key_error(k));
        return *p;
    }

    V value_or(const std::string& k, const V& def_val) const {
        const auto* p = find_ptr(k);
        return p ? *p : def_val;
    }

    V& operator[](const std::string& k) {
        auto it = index_.find(k);
        if (it != index_.end()) {
            return entries_[it->second].value;
        }
        size_t idx = entries_.size();
        entries_.push_back(Entry{k, V{}, true});
        index_.emplace(k, idx);
        ++live_count_;
        return entries_[idx].value;
    }

    void set(const std::string& k, V val) {
        auto it = index_.find(k);
        if (it != index_.end()) {
            entries_[it->second].value = std::move(val);
            return;
        }
        size_t idx = entries_.size();
        entries_.push_back(Entry{k, std::move(val), true});
        index_.emplace(k, idx);
        ++live_count_;
    }

    bool erase(const std::string& k) {
        auto it = index_.find(k);
        if (it == index_.end()) return false;
        entries_[it->second].alive = false;
        index_.erase(it);
        --live_count_;
        if (entries_.size() > 64 && live_count_ * 2 < entries_.size()) {
            compact();
        }
        return true;
    }

    void compact() {
        std::vector<Entry> fresh;
        fresh.reserve(live_count_);
        index_.clear();
        index_.reserve(live_count_);
        for (auto& e : entries_) {
            if (e.alive) {
                size_t idx = fresh.size();
                index_.emplace(e.key, idx);
                fresh.push_back(std::move(e));
            }
        }
        entries_ = std::move(fresh);
    }

    std::vector<std::string> keys() const {
        std::vector<std::string> ks;
        ks.reserve(live_count_);
        for (const auto& e : entries_) {
            if (e.alive) ks.push_back(e.key);
        }
        return ks;
    }

    Iter<false> begin() noexcept { return Iter<false>(&entries_, 0); }
    Iter<false> end() noexcept { return Iter<false>(&entries_, entries_.size()); }
    Iter<true> begin() const noexcept { return Iter<true>(&entries_, 0); }
    Iter<true> end() const noexcept { return Iter<true>(&entries_, entries_.size()); }

    // Order-independent equality matching Python dict `==`
    bool operator==(const OrderedMap& o) const {
        if (live_count_ != o.live_count_) return false;
        for (const auto& e : entries_) {
            if (!e.alive) continue;
            const V* ov = o.find_ptr(e.key);
            if (!ov || !(*ov == e.value)) return false;
        }
        return true;
    }
    bool operator!=(const OrderedMap& o) const { return !(*this == o); }

private:
    std::string format_missing_key_error(const std::string& k) const {
        std::string msg = "OrderedMap::at missing key '" + k + "' (live_keys=" + std::to_string(live_count_);
        if (live_count_ > 0) {
            msg += ", sample_keys=[";
            size_t shown = 0;
            for (const auto& e : entries_) {
                if (!e.alive) continue;
                if (shown > 0) msg += ", ";
                msg += "'" + e.key + "'";
                if (++shown >= 8) break;
            }
            if (live_count_ > shown) msg += ", ...";
            msg += "]";
        }
        msg += ")";
        return msg;
    }

    std::vector<Entry> entries_;
    std::unordered_map<std::string, size_t> index_;
    size_t live_count_ = 0;
};

// ============================================================================
// General JsonValue DOM
// ============================================================================
class JsonValue {
public:
    using Array = std::vector<JsonValue>;
    using Object = OrderedMap<JsonValue>;
    using Storage = std::variant<std::nullptr_t, bool, int64_t, double, std::string, Array, Object>;

    JsonValue() noexcept : data_(nullptr) {}
    JsonValue(std::nullptr_t) noexcept : data_(nullptr) {}
    JsonValue(bool b) noexcept : data_(b) {}
    JsonValue(int v) noexcept : data_(static_cast<int64_t>(v)) {}
    JsonValue(unsigned int v) noexcept : data_(static_cast<int64_t>(v)) {}
    JsonValue(int64_t v) noexcept : data_(v) {}
    JsonValue(uint64_t v) noexcept : data_(static_cast<int64_t>(v)) {}
    JsonValue(double d) noexcept : data_(d) {}
    JsonValue(const char* s) : data_(std::string(s ? s : "")) {}
    JsonValue(std::string_view s) : data_(std::string(s)) {}
    JsonValue(std::string s) noexcept : data_(std::move(s)) {}
    JsonValue(Array a) noexcept : data_(std::move(a)) {}
    JsonValue(Object o) noexcept : data_(std::move(o)) {}

    static JsonValue object() { return JsonValue(Object{}); }
    static JsonValue array() { return JsonValue(Array{}); }

    bool is_null() const noexcept { return std::holds_alternative<std::nullptr_t>(data_); }
    bool is_bool() const noexcept { return std::holds_alternative<bool>(data_); }
    bool is_int() const noexcept { return std::holds_alternative<int64_t>(data_); }
    bool is_double() const noexcept { return std::holds_alternative<double>(data_); }
    bool is_number() const noexcept { return is_int() || is_double(); }
    bool is_string() const noexcept { return std::holds_alternative<std::string>(data_); }
    bool is_str() const noexcept { return is_string(); }
    bool is_array() const noexcept { return std::holds_alternative<Array>(data_); }
    bool is_arr() const noexcept { return is_array(); }
    bool is_object() const noexcept { return std::holds_alternative<Object>(data_); }
    bool is_obj() const noexcept { return is_object(); }

    bool empty() const noexcept {
        if (is_array()) return std::get<Array>(data_).empty();
        if (is_object()) return std::get<Object>(data_).empty();
        if (is_string()) return std::get<std::string>(data_).empty();
        return is_null();
    }
    size_t size() const noexcept {
        if (is_array()) return std::get<Array>(data_).size();
        if (is_object()) return std::get<Object>(data_).size();
        if (is_string()) return std::get<std::string>(data_).size();
        return 0;
    }
    bool contains(const std::string& k) const {
        return is_object() && std::get<Object>(data_).contains(k);
    }

    bool as_bool(bool def_v = false) const noexcept {
        if (auto* p = std::get_if<bool>(&data_)) return *p;
        return def_v;
    }
    int64_t as_i64(int64_t def_v = 0) const noexcept {
        if (auto* p = std::get_if<int64_t>(&data_)) return *p;
        if (auto* d = std::get_if<double>(&data_)) return static_cast<int64_t>(*d);
        return def_v;
    }
    double as_f64(double def_v = 0.0) const noexcept {
        if (auto* d = std::get_if<double>(&data_)) return *d;
        if (auto* p = std::get_if<int64_t>(&data_)) return static_cast<double>(*p);
        return def_v;
    }
    const std::string& as_str() const {
        if (auto* p = std::get_if<std::string>(&data_)) return *p;
        static const std::string empty;
        return empty;
    }
    Array& as_arr() { return std::get<Array>(data_); }
    const Array& as_arr() const { return std::get<Array>(data_); }
    Object& as_obj() { return std::get<Object>(data_); }
    const Object& as_obj() const { return std::get<Object>(data_); }

    JsonValue& operator[](const std::string& k) {
        if (is_null()) data_ = Object{};
        return std::get<Object>(data_)[k];
    }
    JsonValue& operator[](const char* k) {
        return (*this)[std::string(k ? k : "")];
    }
    const JsonValue& operator[](const std::string& k) const {
        static const JsonValue kNull;
        if (const auto* p = get(k)) return *p;
        return kNull;
    }
    const JsonValue& operator[](const char* k) const {
        static const JsonValue kNull;
        if (const auto* p = get(std::string(k ? k : ""))) return *p;
        return kNull;
    }
    JsonValue& operator[](size_t idx) {
        return std::get<Array>(data_).at(idx);
    }
    const JsonValue& operator[](size_t idx) const {
        return std::get<Array>(data_).at(idx);
    }
    const JsonValue* get(const std::string& k) const {
        if (!is_object()) return nullptr;
        return std::get<Object>(data_).find_ptr(k);
    }
    JsonValue* get_mut(const std::string& k) {
        if (!is_object()) return nullptr;
        return std::get<Object>(data_).find_ptr(k);
    }
    void push_back(JsonValue v) {
        if (is_null()) data_ = Array{};
        std::get<Array>(data_).push_back(std::move(v));
    }

    bool operator==(const JsonValue& o) const {
        if (is_number() && o.is_number()) {
            if (is_int() && o.is_int()) return as_i64() == o.as_i64();
            return as_f64() == o.as_f64();
        }
        return data_ == o.data_;
    }
    bool operator!=(const JsonValue& o) const { return !(*this == o); }

    std::string dump_compact() const;
    std::string dump_pretty(int indent = 2) const;

    const Storage& storage() const noexcept { return data_; }

private:
    Storage data_;
};

JsonValue parse_json(std::string_view text);
JsonValue parse_json_file(std::string_view path);
void write_json_pretty(std::string_view path, const JsonValue& v, int indent = 2);
void write_json_compact(std::string_view path, const JsonValue& v);

inline std::string dump_json(const JsonValue& v, int indent = 2, bool /*sort_keys*/ = false) {
    return (indent < 0) ? v.dump_compact() : v.dump_pretty(indent);
}
inline void write_json_file(std::string_view path, const JsonValue& v, int indent = 2) {
    if (indent < 0) write_json_compact(path, v);
    else write_json_pretty(path, v, indent);
}

// ============================================================================
// High-Speed Typed Yosys RTLIL JSON Representation
// ============================================================================
// Signal bit encoding:
//   b >= 0  -> integer wire ID `b`
//   -1      -> '0'
//   -2      -> '1'
//   -3      -> 'x'
//   -4      -> 'z'
using SigBit = int32_t;
constexpr SigBit SIG_0 = -1;
constexpr SigBit SIG_1 = -2;
constexpr SigBit SIG_X = -3;
constexpr SigBit SIG_Z = -4;

inline bool is_int_bit(SigBit b) noexcept { return b >= 0; }
inline char const_bit_char(SigBit b) noexcept {
    switch (b) {
        case SIG_0: return '0';
        case SIG_1: return '1';
        case SIG_X: return 'x';
        case SIG_Z: return 'z';
        default: return '?';
    }
}
inline SigBit char_to_sigbit(char c) {
    switch (c) {
        case '0': return SIG_0;
        case '1': return SIG_1;
        case 'x': case 'X': return SIG_X;
        case 'z': case 'Z': return SIG_Z;
        default: throw std::runtime_error(std::string("Invalid signal constant char: ") + c);
    }
}

struct RtlilPort {
    std::string direction;
    std::vector<SigBit> bits;
    std::optional<int> offset;
    std::optional<int> upto;
    std::optional<int> signed_val;

    bool operator==(const RtlilPort& o) const {
        return direction == o.direction && bits == o.bits && offset == o.offset && upto == o.upto && signed_val == o.signed_val;
    }
    bool operator!=(const RtlilPort& o) const { return !(*this == o); }
};

struct RtlilCell {
    std::optional<int> hide_name;
    std::string type;
    std::optional<std::string> model;
    OrderedMap<std::string> parameters;
    OrderedMap<JsonValue> attributes;
    OrderedMap<std::string> port_directions;
    OrderedMap<std::vector<SigBit>> connections;

    bool operator==(const RtlilCell& o) const {
        return hide_name == o.hide_name && type == o.type && model == o.model
            && parameters == o.parameters && attributes == o.attributes
            && port_directions == o.port_directions && connections == o.connections;
    }
    bool operator!=(const RtlilCell& o) const { return !(*this == o); }
};

struct RtlilNetname {
    int hide_name = 0;
    std::vector<SigBit> bits;
    std::optional<int> offset;
    std::optional<int> upto;
    std::optional<int> signed_val;
    OrderedMap<JsonValue> attributes;

    bool operator==(const RtlilNetname& o) const {
        return hide_name == o.hide_name && bits == o.bits && offset == o.offset
            && upto == o.upto && signed_val == o.signed_val && attributes == o.attributes;
    }
    bool operator!=(const RtlilNetname& o) const { return !(*this == o); }
};

struct RtlilModule {
    OrderedMap<JsonValue> attributes;
    OrderedMap<JsonValue> parameter_default_values;
    OrderedMap<RtlilPort> ports;
    OrderedMap<RtlilCell> cells;
    OrderedMap<JsonValue> memories;
    OrderedMap<RtlilNetname> netnames;

    bool operator==(const RtlilModule& o) const {
        return attributes == o.attributes && parameter_default_values == o.parameter_default_values
            && ports == o.ports && cells == o.cells && memories == o.memories && netnames == o.netnames;
    }
    bool operator!=(const RtlilModule& o) const { return !(*this == o); }
};

struct RtlilDesign {
    std::string creator;
    OrderedMap<std::shared_ptr<RtlilModule>> modules;

    RtlilDesign clone_cow(const std::vector<std::string>* mutable_only = nullptr) const;
    RtlilDesign clone_deep() const;
    RtlilModule& mod_mut(const std::string& name);
    const RtlilModule& mod_ref(const std::string& name) const;

    bool operator==(const RtlilDesign& o) const;
    bool operator!=(const RtlilDesign& o) const { return !(*this == o); }
};

RtlilDesign parse_rtlil_json(std::string_view text);
RtlilDesign read_rtlil_json_file(std::string_view path);
std::string dump_rtlil_json_compact(const RtlilDesign& design);
void write_rtlil_json_file(std::string_view path, const RtlilDesign& design);

int64_t parse_bin_or_int_attr(const OrderedMap<JsonValue>& attrs, const std::string& key, int64_t def_v = 0);
bool is_attr_truthy(const OrderedMap<JsonValue>& attrs, const std::string& key);
bool is_module_blackbox(const RtlilModule& m);
int32_t max_module_bit(const RtlilModule& m);
uint64_t parse_bin_u64(std::string_view bin_str);
std::string format_bin_u64(uint64_t val, int width);

// ============================================================================
// Boolean Gate Truth Tables, BLIF I/O, Functional Choices, ABC & Yosys Runners
// ============================================================================
struct BoolGateDef {
    const char* cell_type;
    const char* pins;
    int arity;
    uint64_t truth;
};

const std::vector<BoolGateDef>& bool_gate_defs();
const BoolGateDef* find_bool_gate(std::string_view cell_type);

struct BlifNode {
    std::vector<std::string> inputs;
    std::string output;
    uint64_t truth = 0;
    int native_mux = 0; // 0 = LUT/const, 5 = MUX2_LUT5, 6 = MUX2_LUT6
    bool keep = false;
};

struct BlifNetwork {
    std::vector<std::string> inputs;
    std::vector<std::string> outputs;
    std::vector<BlifNode> nodes;
    std::vector<std::string> topological;
};

BlifNetwork read_blif_file(std::string_view path, int max_inputs = 4);
void write_blif_fast(std::string_view path, const BlifNetwork& net, bool emit_zero_const_cube = true);
std::string format_blif_text(const BlifNetwork& net, std::string_view model_name = "functional", bool emit_zero_const_cube = true);

// gw5a_functional_map equivalents
std::pair<std::vector<std::string>, uint64_t> reduce_truth_function(const std::vector<std::string>& inputs, uint64_t truth);
std::pair<std::vector<SigBit>, uint64_t> reduce_sigbit_function(const std::vector<SigBit>& inputs, uint64_t truth);
uint64_t cofactor_truth(uint64_t truth, int k, int select_idx, int value);
JsonValue validate_dedicated_mux_packing(const RtlilDesign& design);

// ABC & Yosys runners
std::pair<JsonValue, std::string> invoke_abc(std::string_view commands, std::string_view logfile, double timeout_sec);
JsonValue run_abc_cec(std::string_view original_blif, std::string_view mapped_blif, std::string_view logfile, double timeout_sec);
JsonValue parallel_sliced_cec(const BlifNetwork& net_a, const BlifNetwork& net_b, std::string_view folder, double timeout_sec = 300.0);

std::string quote_yosys_path(std::string_view path);
JsonValue run_yosys_commands(const std::vector<std::string>& commands, std::string_view directory, std::string_view name, double timeout_sec = 1800.0);

} // namespace ng
