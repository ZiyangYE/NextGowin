#include "ng_json.hpp"

#include <charconv>
#include <deque>
#include <mutex>
#include <thread>
#include <unordered_set>

namespace ng {

namespace {

void append_escaped_json_str(std::string& out, std::string_view s) {
    out.push_back('"');
    for (unsigned char c : s) {
        switch (c) {
            case '"':  out.append("\\\""); break;
            case '\\': out.append("\\\\"); break;
            case '\b': out.append("\\b"); break;
            case '\f': out.append("\\f"); break;
            case '\n': out.append("\\n"); break;
            case '\r': out.append("\\r"); break;
            case '\t': out.append("\\t"); break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", static_cast<unsigned int>(c));
                    out.append(buf);
                } else {
                    out.push_back(static_cast<char>(c));
                }
                break;
        }
    }
    out.push_back('"');
}

void dump_json_impl(std::string& out, const JsonValue& v, int indent, int depth) {
    if (v.is_null()) {
        out.append("null");
    } else if (v.is_bool()) {
        out.append(v.as_bool() ? "true" : "false");
    } else if (v.is_int()) {
        char buf[32];
        auto [p, ec] = std::to_chars(buf, buf + sizeof(buf), v.as_i64());
        out.append(buf, p);
    } else if (v.is_double()) {
        double d = v.as_f64();
        if (!std::isfinite(d)) {
            out.append("null");
        } else {
            char buf[64];
            int n = std::snprintf(buf, sizeof(buf), "%.16g", d);
            std::string_view sv(buf, static_cast<size_t>(std::max(0, n)));
            out.append(sv);
            if (sv.find('.') == std::string_view::npos && sv.find('e') == std::string_view::npos && sv.find('E') == std::string_view::npos) {
                out.append(".0");
            }
        }
    } else if (v.is_string()) {
        append_escaped_json_str(out, v.as_str());
    } else if (v.is_array()) {
        const auto& arr = v.as_arr();
        if (arr.empty()) {
            out.append("[]");
            return;
        }
        out.push_back('[');
        if (indent > 0) {
            for (size_t i = 0; i < arr.size(); ++i) {
                if (i > 0) out.push_back(',');
                out.push_back('\n');
                out.append(static_cast<size_t>((depth + 1) * indent), ' ');
                dump_json_impl(out, arr[i], indent, depth + 1);
            }
            out.push_back('\n');
            out.append(static_cast<size_t>(depth * indent), ' ');
        } else {
            for (size_t i = 0; i < arr.size(); ++i) {
                if (i > 0) out.push_back(',');
                dump_json_impl(out, arr[i], 0, 0);
            }
        }
        out.push_back(']');
    } else if (v.is_object()) {
        const auto& obj = v.as_obj();
        if (obj.empty()) {
            out.append("{}");
            return;
        }
        out.push_back('{');
        size_t idx = 0;
        if (indent > 0) {
            for (const auto& [k, val] : obj) {
                if (idx++ > 0) out.push_back(',');
                out.push_back('\n');
                out.append(static_cast<size_t>((depth + 1) * indent), ' ');
                append_escaped_json_str(out, k);
                out.append(": ");
                dump_json_impl(out, val, indent, depth + 1);
            }
            out.push_back('\n');
            out.append(static_cast<size_t>(depth * indent), ' ');
        } else {
            for (const auto& [k, val] : obj) {
                if (idx++ > 0) out.push_back(',');
                append_escaped_json_str(out, k);
                out.push_back(':');
                dump_json_impl(out, val, 0, 0);
            }
        }
        out.push_back('}');
    }
}

struct JsonScanner {
    const char* p = nullptr;
    const char* end = nullptr;

    void skip_ws() noexcept {
        while (p < end) {
            char c = *p;
            if (c == ' ' || c == '\n' || c == '\r' || c == '\t') ++p;
            else break;
        }
    }

    char peek() {
        skip_ws();
        if (p >= end) throw std::runtime_error("Unexpected EOF in JSON");
        return *p;
    }

    void expect(char expected) {
        char c = peek();
        if (c != expected) {
            throw std::runtime_error(std::string("Expected '") + expected + "' in JSON, got '" + c + "'");
        }
        ++p;
    }

    static void append_utf8_cp(std::string& dst, uint32_t cp) {
        if (cp <= 0x7Fu) {
            dst.push_back(static_cast<char>(cp));
        } else if (cp <= 0x7FFu) {
            dst.push_back(static_cast<char>(0xC0u | (cp >> 6)));
            dst.push_back(static_cast<char>(0x80u | (cp & 0x3Fu)));
        } else if (cp <= 0xFFFFu) {
            dst.push_back(static_cast<char>(0xE0u | (cp >> 12)));
            dst.push_back(static_cast<char>(0x80u | ((cp >> 6) & 0x3Fu)));
            dst.push_back(static_cast<char>(0x80u | (cp & 0x3Fu)));
        } else {
            dst.push_back(static_cast<char>(0xF0u | (cp >> 18)));
            dst.push_back(static_cast<char>(0x80u | ((cp >> 12) & 0x3Fu)));
            dst.push_back(static_cast<char>(0x80u | ((cp >> 6) & 0x3Fu)));
            dst.push_back(static_cast<char>(0x80u | (cp & 0x3Fu)));
        }
    }

    uint32_t parse_hex4() {
        if (p + 4 > end) throw std::runtime_error("Truncated \\u escape in JSON");
        uint32_t v = 0;
        for (int i = 0; i < 4; ++i) {
            char c = *p++;
            v <<= 4;
            if (c >= '0' && c <= '9') v |= static_cast<uint32_t>(c - '0');
            else if (c >= 'a' && c <= 'f') v |= static_cast<uint32_t>(10 + c - 'a');
            else if (c >= 'A' && c <= 'F') v |= static_cast<uint32_t>(10 + c - 'A');
            else throw std::runtime_error("Invalid hex digit in \\u escape");
        }
        return v;
    }

    std::string parse_string() {
        expect('"');
        // Fast path: scan until '"' or '\\'
        const char* start = p;
        while (p < end && *p != '"' && *p != '\\') ++p;
        if (p < end && *p == '"') {
            std::string s(start, p);
            ++p;
            return s;
        }
        std::string s(start, p);
        while (p < end) {
            char c = *p++;
            if (c == '"') return s;
            if (c == '\\') {
                if (p >= end) throw std::runtime_error("Truncated escape in JSON string");
                char e = *p++;
                switch (e) {
                    case '"':  s.push_back('"'); break;
                    case '\\': s.push_back('\\'); break;
                    case '/':  s.push_back('/'); break;
                    case 'b':  s.push_back('\b'); break;
                    case 'f':  s.push_back('\f'); break;
                    case 'n':  s.push_back('\n'); break;
                    case 'r':  s.push_back('\r'); break;
                    case 't':  s.push_back('\t'); break;
                    case 'u': {
                        uint32_t cp = parse_hex4();
                        if (cp >= 0xD800u && cp <= 0xDBFFu && p + 2 <= end && p[0] == '\\' && p[1] == 'u') {
                            p += 2;
                            uint32_t lo = parse_hex4();
                            if (lo >= 0xDC00u && lo <= 0xDFFFu) {
                                cp = 0x10000u + (((cp - 0xD800u) << 10) | (lo - 0xDC00u));
                            }
                        }
                        append_utf8_cp(s, cp);
                        break;
                    }
                    default: s.push_back(e); break;
                }
            } else {
                s.push_back(c);
            }
        }
        throw std::runtime_error("Unterminated JSON string");
    }

    int64_t parse_int64() {
        skip_ws();
        int64_t val = 0;
        auto [nxt, ec] = std::from_chars(p, end, val);
        if (ec != std::errc{}) throw std::runtime_error("Invalid integer in JSON");
        p = nxt;
        return val;
    }

    JsonValue parse_value() {
        char c = peek();
        if (c == '"') return JsonValue(parse_string());
        if (c == '{') {
            ++p;
            JsonValue::Object obj;
            if (peek() == '}') {
                ++p;
                return JsonValue(std::move(obj));
            }
            for (;;) {
                std::string k = parse_string();
                expect(':');
                obj.set(k, parse_value());
                char sep = peek();
                if (sep == ',') {
                    ++p;
                } else if (sep == '}') {
                    ++p;
                    break;
                } else {
                    throw std::runtime_error("Expected ',' or '}' in JSON object");
                }
            }
            return JsonValue(std::move(obj));
        }
        if (c == '[') {
            ++p;
            JsonValue::Array arr;
            if (peek() == ']') {
                ++p;
                return JsonValue(std::move(arr));
            }
            for (;;) {
                arr.push_back(parse_value());
                char sep = peek();
                if (sep == ',') {
                    ++p;
                } else if (sep == ']') {
                    ++p;
                    break;
                } else {
                    throw std::runtime_error("Expected ',' or ']' in JSON array");
                }
            }
            return JsonValue(std::move(arr));
        }
        if (c == 't') {
            if (end - p >= 4 && std::memcmp(p, "true", 4) == 0) { p += 4; return JsonValue(true); }
            throw std::runtime_error("Invalid token in JSON");
        }
        if (c == 'f') {
            if (end - p >= 5 && std::memcmp(p, "false", 5) == 0) { p += 5; return JsonValue(false); }
            throw std::runtime_error("Invalid token in JSON");
        }
        if (c == 'n') {
            if (end - p >= 4 && std::memcmp(p, "null", 4) == 0) { p += 4; return JsonValue(nullptr); }
            throw std::runtime_error("Invalid token in JSON");
        }
        // Number
        const char* num_start = p;
        if (*p == '-') ++p;
        while (p < end && (*p >= '0' && *p <= '9')) ++p;
        bool is_flt = false;
        if (p < end && *p == '.') {
            is_flt = true;
            ++p;
            while (p < end && (*p >= '0' && *p <= '9')) ++p;
        }
        if (p < end && (*p == 'e' || *p == 'E')) {
            is_flt = true;
            ++p;
            if (p < end && (*p == '+' || *p == '-')) ++p;
            while (p < end && (*p >= '0' && *p <= '9')) ++p;
        }
        if (!is_flt) {
            int64_t iv = 0;
            auto [nxt, ec] = std::from_chars(num_start, p, iv);
            if (ec == std::errc{} && nxt == p) {
                return JsonValue(iv);
            }
        }
        std::string tmp(num_start, p);
        double dv = std::strtod(tmp.c_str(), nullptr);
        return JsonValue(dv);
    }

    std::vector<SigBit> parse_sigbit_array() {
        expect('[');
        std::vector<SigBit> bits;
        if (peek() == ']') {
            ++p;
            return bits;
        }
        for (;;) {
            char c = peek();
            if (c == '"') {
                ++p;
                if (p + 1 >= end || p[1] != '"') {
                    // Fallback general string
                    --p;
                    std::string sv = parse_string();
                    if (sv.size() != 1) throw std::runtime_error("Unexpected multi-char sigbit string: " + sv);
                    bits.push_back(char_to_sigbit(sv[0]));
                } else {
                    bits.push_back(char_to_sigbit(p[0]));
                    p += 2;
                }
            } else {
                int32_t b = 0;
                auto [nxt, ec] = std::from_chars(p, end, b);
                if (ec != std::errc{}) throw std::runtime_error("Invalid integer sigbit");
                p = nxt;
                bits.push_back(b);
            }
            char sep = peek();
            if (sep == ',') {
                ++p;
            } else if (sep == ']') {
                ++p;
                break;
            } else {
                throw std::runtime_error("Expected ',' or ']' in sigbit array");
            }
        }
        return bits;
    }

    OrderedMap<std::string> parse_str_map() {
        expect('{');
        OrderedMap<std::string> m;
        if (peek() == '}') { ++p; return m; }
        for (;;) {
            std::string k = parse_string();
            expect(':');
            if (peek() == '"') {
                m.set(k, parse_string());
            } else {
                JsonValue jv = parse_value();
                m.set(k, jv.is_int() ? std::to_string(jv.as_i64()) : jv.dump_compact());
            }
            char sep = peek();
            if (sep == ',') ++p;
            else if (sep == '}') { ++p; break; }
            else throw std::runtime_error("Expected ',' or '}' in string map");
        }
        return m;
    }

    OrderedMap<JsonValue> parse_json_obj() {
        JsonValue v = parse_value();
        if (!v.is_object()) throw std::runtime_error("Expected JSON object");
        return std::move(v.as_obj());
    }

    RtlilPort parse_port() {
        expect('{');
        RtlilPort port;
        if (peek() == '}') { ++p; return port; }
        for (;;) {
            std::string k = parse_string();
            expect(':');
            if (k == "direction") port.direction = parse_string();
            else if (k == "bits") port.bits = parse_sigbit_array();
            else if (k == "offset") port.offset = static_cast<int>(parse_int64());
            else if (k == "upto") port.upto = static_cast<int>(parse_int64());
            else if (k == "signed") port.signed_val = static_cast<int>(parse_int64());
            else (void)parse_value();
            char sep = peek();
            if (sep == ',') ++p;
            else if (sep == '}') { ++p; break; }
            else throw std::runtime_error("Expected ',' or '}' in port");
        }
        return port;
    }

    RtlilCell parse_cell() {
        expect('{');
        RtlilCell cell;
        if (peek() == '}') { ++p; return cell; }
        for (;;) {
            std::string k = parse_string();
            expect(':');
            if (k == "hide_name") cell.hide_name = static_cast<int>(parse_int64());
            else if (k == "type") cell.type = parse_string();
            else if (k == "model") cell.model = parse_string();
            else if (k == "parameters") cell.parameters = parse_str_map();
            else if (k == "attributes") cell.attributes = parse_json_obj();
            else if (k == "port_directions") cell.port_directions = parse_str_map();
            else if (k == "connections") {
                expect('{');
                if (peek() == '}') {
                    ++p;
                } else {
                    for (;;) {
                        std::string pk = parse_string();
                        expect(':');
                        cell.connections.set(pk, parse_sigbit_array());
                        char csep = peek();
                        if (csep == ',') ++p;
                        else if (csep == '}') { ++p; break; }
                        else throw std::runtime_error("Expected ',' or '}' in connections");
                    }
                }
            } else {
                (void)parse_value();
            }
            char sep = peek();
            if (sep == ',') ++p;
            else if (sep == '}') { ++p; break; }
            else throw std::runtime_error("Expected ',' or '}' in cell");
        }
        return cell;
    }

    RtlilNetname parse_netname() {
        expect('{');
        RtlilNetname nn;
        if (peek() == '}') { ++p; return nn; }
        for (;;) {
            std::string k = parse_string();
            expect(':');
            if (k == "hide_name") nn.hide_name = static_cast<int>(parse_int64());
            else if (k == "bits") nn.bits = parse_sigbit_array();
            else if (k == "offset") nn.offset = static_cast<int>(parse_int64());
            else if (k == "upto") nn.upto = static_cast<int>(parse_int64());
            else if (k == "signed") nn.signed_val = static_cast<int>(parse_int64());
            else if (k == "attributes") nn.attributes = parse_json_obj();
            else (void)parse_value();
            char sep = peek();
            if (sep == ',') ++p;
            else if (sep == '}') { ++p; break; }
            else throw std::runtime_error("Expected ',' or '}' in netname");
        }
        return nn;
    }

    std::shared_ptr<RtlilModule> parse_module() {
        auto mod = std::make_shared<RtlilModule>();
        expect('{');
        if (peek() == '}') { ++p; return mod; }
        for (;;) {
            std::string k = parse_string();
            expect(':');
            if (k == "attributes") {
                mod->attributes = parse_json_obj();
            } else if (k == "parameter_default_values") {
                mod->parameter_default_values = parse_json_obj();
            } else if (k == "ports") {
                expect('{');
                if (peek() == '}') {
                    ++p;
                } else {
                    for (;;) {
                        std::string pk = parse_string();
                        expect(':');
                        mod->ports.set(pk, parse_port());
                        char s2 = peek();
                        if (s2 == ',') ++p;
                        else if (s2 == '}') { ++p; break; }
                        else throw std::runtime_error("Expected ',' or '}' in ports");
                    }
                }
            } else if (k == "cells") {
                expect('{');
                if (peek() == '}') {
                    ++p;
                } else {
                    for (;;) {
                        std::string ck = parse_string();
                        expect(':');
                        mod->cells.set(ck, parse_cell());
                        char s2 = peek();
                        if (s2 == ',') ++p;
                        else if (s2 == '}') { ++p; break; }
                        else throw std::runtime_error("Expected ',' or '}' in cells");
                    }
                }
            } else if (k == "memories") {
                mod->memories = parse_json_obj();
            } else if (k == "netnames") {
                expect('{');
                if (peek() == '}') {
                    ++p;
                } else {
                    for (;;) {
                        std::string nk = parse_string();
                        expect(':');
                        mod->netnames.set(nk, parse_netname());
                        char s2 = peek();
                        if (s2 == ',') ++p;
                        else if (s2 == '}') { ++p; break; }
                        else throw std::runtime_error("Expected ',' or '}' in netnames");
                    }
                }
            } else {
                (void)parse_value();
            }
            char sep = peek();
            if (sep == ',') ++p;
            else if (sep == '}') { ++p; break; }
            else throw std::runtime_error("Expected ',' or '}' in module");
        }
        return mod;
    }
};

} // namespace

std::string JsonValue::dump_compact() const {
    std::string out;
    out.reserve(256);
    dump_json_impl(out, *this, 0, 0);
    return out;
}

std::string JsonValue::dump_pretty(int indent) const {
    std::string out;
    out.reserve(512);
    dump_json_impl(out, *this, indent, 0);
    return out;
}

JsonValue parse_json(std::string_view text) {
    JsonScanner sc{text.data(), text.data() + text.size()};
    try {
        if (text.size() >= 3 && static_cast<uint8_t>(text[0]) == 0xEF && static_cast<uint8_t>(text[1]) == 0xBB && static_cast<uint8_t>(text[2]) == 0xBF) {
            sc.p += 3;
        }
        JsonValue v = sc.parse_value();
        sc.skip_ws();
        if (sc.p != sc.end) {
            throw std::runtime_error("Trailing data after JSON root value");
        }
        return v;
    } catch (const std::exception& ex) {
        size_t off = (sc.p && sc.p >= text.data() && sc.p <= text.data() + text.size())
            ? static_cast<size_t>(sc.p - text.data()) : 0;
        size_t line = 1, col = 1;
        for (size_t i = 0; i < off; ++i) {
            if (text[i] == '\n') { ++line; col = 1; }
            else if (text[i] != '\r') ++col;
        }
        size_t sn_beg = (off > 24) ? (off - 24) : 0;
        size_t sn_end = std::min(text.size(), off + 32);
        std::string snippet;
        for (size_t i = sn_beg; i < sn_end; ++i) {
            char c = text[i];
            if (c == '\n' || c == '\r' || c == '\t') snippet.push_back(' ');
            else snippet.push_back(c);
        }
        throw std::runtime_error(
            std::string(ex.what()) + " at line " + std::to_string(line) + ", column " + std::to_string(col)
            + " (near \"" + snippet + "\")"
        );
    }
}

JsonValue parse_json_file(std::string_view path) {
    auto bytes = read_file_bytes(path);
    try {
        return parse_json(std::string_view(reinterpret_cast<const char*>(bytes.data()), bytes.size()));
    } catch (const std::exception& ex) {
        throw std::runtime_error("JSON parse error in '" + std::string(path) + "': " + ex.what());
    }
}

void write_json_pretty(std::string_view path, const JsonValue& v, int indent) {
    std::string s = v.dump_pretty(indent);
    s.push_back('\n');
    write_file_text(path, s);
}

void write_json_compact(std::string_view path, const JsonValue& v) {
    std::string s = v.dump_compact();
    s.push_back('\n');
    write_file_text(path, s);
}

// ============================================================================
// RtlilDesign Streaming Parser & Serializer
// ============================================================================
RtlilDesign RtlilDesign::clone_cow(const std::vector<std::string>* mutable_only) const {
    RtlilDesign d;
    d.creator = creator;
    std::unordered_set<std::string> mut_set;
    if (mutable_only) {
        for (const auto& s : *mutable_only) mut_set.insert(s);
    }
    for (const auto& [mn, mptr] : modules) {
        bool share = false;
        if (mutable_only && !mut_set.count(mn)) {
            share = true;
        } else if (is_module_blackbox(*mptr)) {
            share = true;
        }
        if (share) {
            d.modules.set(mn, mptr);
        } else {
            d.modules.set(mn, std::make_shared<RtlilModule>(*mptr));
        }
    }
    return d;
}

RtlilDesign RtlilDesign::clone_deep() const {
    RtlilDesign d;
    d.creator = creator;
    for (const auto& [mn, mptr] : modules) {
        d.modules.set(mn, std::make_shared<RtlilModule>(*mptr));
    }
    return d;
}

RtlilModule& RtlilDesign::mod_mut(const std::string& name) {
    auto* p = modules.find_ptr(name);
    if (!p || !*p) throw std::runtime_error("Missing module: " + name);
    if (p->use_count() > 1) {
        *p = std::make_shared<RtlilModule>(**p);
    }
    return **p;
}

const RtlilModule& RtlilDesign::mod_ref(const std::string& name) const {
    const auto* p = modules.find_ptr(name);
    if (!p || !*p) throw std::runtime_error("Missing module: " + name);
    return **p;
}

bool RtlilDesign::operator==(const RtlilDesign& o) const {
    if (modules.size() != o.modules.size()) return false;
    for (const auto& [mn, mptr] : modules) {
        const auto* optr = o.modules.find_ptr(mn);
        if (!optr || !*optr) return false;
        if (mptr.get() != optr->get() && !(*mptr == **optr)) return false;
    }
    return true;
}

RtlilDesign parse_rtlil_json(std::string_view text) {
    JsonScanner sc{text.data(), text.data() + text.size()};
    RtlilDesign d;
    sc.expect('{');
    if (sc.peek() == '}') { ++sc.p; return d; }
    for (;;) {
        std::string k = sc.parse_string();
        sc.expect(':');
        if (k == "creator") {
            d.creator = sc.parse_string();
        } else if (k == "modules") {
            sc.expect('{');
            if (sc.peek() == '}') {
                ++sc.p;
            } else {
                for (;;) {
                    std::string mn = sc.parse_string();
                    sc.expect(':');
                    d.modules.set(mn, sc.parse_module());
                    char s2 = sc.peek();
                    if (s2 == ',') ++sc.p;
                    else if (s2 == '}') { ++sc.p; break; }
                    else throw std::runtime_error("Expected ',' or '}' in top-level modules");
                }
            }
        } else {
            (void)sc.parse_value();
        }
        char sep = sc.peek();
        if (sep == ',') ++sc.p;
        else if (sep == '}') { ++sc.p; break; }
        else throw std::runtime_error("Expected ',' or '}' in RTLIL design");
    }
    return d;
}

RtlilDesign read_rtlil_json_file(std::string_view path) {
    auto bytes = read_file_bytes(path);
    try {
        return parse_rtlil_json(std::string_view(reinterpret_cast<const char*>(bytes.data()), bytes.size()));
    } catch (const std::exception& ex) {
        throw std::runtime_error("RTLIL JSON parse error in '" + std::string(path) + "': " + ex.what());
    }
}

namespace {

inline void append_int(std::string& out, int32_t v) {
    char buf[16];
    auto [p, ec] = std::to_chars(buf, buf + sizeof(buf), v);
    out.append(buf, p);
}

void append_sigbits(std::string& out, const std::vector<SigBit>& bits) {
    out.push_back('[');
    for (size_t i = 0; i < bits.size(); ++i) {
        if (i > 0) out.push_back(',');
        SigBit b = bits[i];
        if (b >= 0) {
            append_int(out, b);
        } else {
            out.push_back('"');
            out.push_back(const_bit_char(b));
            out.push_back('"');
        }
    }
    out.push_back(']');
}

void append_str_map(std::string& out, const OrderedMap<std::string>& m) {
    out.push_back('{');
    size_t idx = 0;
    for (const auto& [k, v] : m) {
        if (idx++ > 0) out.push_back(',');
        append_escaped_json_str(out, k);
        out.push_back(':');
        append_escaped_json_str(out, v);
    }
    out.push_back('}');
}

void append_json_map(std::string& out, const OrderedMap<JsonValue>& m) {
    out.push_back('{');
    size_t idx = 0;
    for (const auto& [k, v] : m) {
        if (idx++ > 0) out.push_back(',');
        append_escaped_json_str(out, k);
        out.push_back(':');
        dump_json_impl(out, v, 0, 0);
    }
    out.push_back('}');
}

} // namespace

std::string dump_rtlil_json_compact(const RtlilDesign& design) {
    std::string out;
    out.reserve(1 << 20);
    out.push_back('{');
    bool need_comma = false;
    if (!design.creator.empty()) {
        out.append("\"creator\":");
        append_escaped_json_str(out, design.creator);
        need_comma = true;
    }
    if (need_comma) out.push_back(',');
    out.append("\"modules\":{");
    size_t m_idx = 0;
    for (const auto& [mn, mptr] : design.modules) {
        if (m_idx++ > 0) out.push_back(',');
        append_escaped_json_str(out, mn);
        out.append(":{");
        const RtlilModule& m = *mptr;
        bool f_comma = false;
        auto comma = [&]() {
            if (f_comma) out.push_back(',');
            f_comma = true;
        };
        if (!m.attributes.empty()) {
            comma();
            out.append("\"attributes\":");
            append_json_map(out, m.attributes);
        }
        if (!m.parameter_default_values.empty()) {
            comma();
            out.append("\"parameter_default_values\":");
            append_json_map(out, m.parameter_default_values);
        }
        comma();
        out.append("\"ports\":{");
        size_t p_idx = 0;
        for (const auto& [pn, pr] : m.ports) {
            if (p_idx++ > 0) out.push_back(',');
            append_escaped_json_str(out, pn);
            out.append(":{\"direction\":");
            append_escaped_json_str(out, pr.direction);
            if (pr.offset.has_value()) { out.append(",\"offset\":"); append_int(out, *pr.offset); }
            if (pr.upto.has_value()) { out.append(",\"upto\":"); append_int(out, *pr.upto); }
            if (pr.signed_val.has_value()) { out.append(",\"signed\":"); append_int(out, *pr.signed_val); }
            out.append(",\"bits\":");
            append_sigbits(out, pr.bits);
            out.push_back('}');
        }
        out.push_back('}');

        comma();
        out.append("\"cells\":{");
        size_t c_idx = 0;
        for (const auto& [cn, cell] : m.cells) {
            if (c_idx++ > 0) out.push_back(',');
            append_escaped_json_str(out, cn);
            out.push_back(':');
            out.push_back('{');
            bool cf = false;
            auto ccomma = [&]() { if (cf) out.push_back(','); cf = true; };
            if (cell.hide_name.has_value()) {
                ccomma();
                out.append("\"hide_name\":");
                append_int(out, *cell.hide_name);
            }
            ccomma();
            out.append("\"type\":");
            append_escaped_json_str(out, cell.type);
            if (cell.model.has_value()) {
                ccomma();
                out.append("\"model\":");
                append_escaped_json_str(out, *cell.model);
            }
            ccomma();
            out.append("\"parameters\":");
            append_str_map(out, cell.parameters);
            ccomma();
            out.append("\"attributes\":");
            append_json_map(out, cell.attributes);
            if (!cell.port_directions.empty()) {
                ccomma();
                out.append("\"port_directions\":");
                append_str_map(out, cell.port_directions);
            }
            ccomma();
            out.append("\"connections\":{");
            size_t k_idx = 0;
            for (const auto& [pk, pbits] : cell.connections) {
                if (k_idx++ > 0) out.push_back(',');
                append_escaped_json_str(out, pk);
                out.push_back(':');
                append_sigbits(out, pbits);
            }
            out.append("}}");
        }
        out.push_back('}');

        if (!m.memories.empty()) {
            comma();
            out.append("\"memories\":");
            append_json_map(out, m.memories);
        }

        comma();
        out.append("\"netnames\":{");
        size_t n_idx = 0;
        for (const auto& [nn, nr] : m.netnames) {
            if (n_idx++ > 0) out.push_back(',');
            append_escaped_json_str(out, nn);
            out.append(":{\"hide_name\":");
            append_int(out, nr.hide_name);
            out.append(",\"bits\":");
            append_sigbits(out, nr.bits);
            if (nr.offset.has_value()) { out.append(",\"offset\":"); append_int(out, *nr.offset); }
            if (nr.upto.has_value()) { out.append(",\"upto\":"); append_int(out, *nr.upto); }
            if (nr.signed_val.has_value()) { out.append(",\"signed\":"); append_int(out, *nr.signed_val); }
            out.append(",\"attributes\":");
            append_json_map(out, nr.attributes);
            out.push_back('}');
        }
        out.append("}}");
    }
    out.append("}}\n");
    return out;
}

void write_rtlil_json_file(std::string_view path, const RtlilDesign& design) {
    std::string txt = dump_rtlil_json_compact(design);
    write_file_text(path, txt);
}

int64_t parse_bin_or_int_attr(const OrderedMap<JsonValue>& attrs, const std::string& key, int64_t def_v) {
    const JsonValue* v = attrs.find_ptr(key);
    if (!v || v->is_null()) return def_v;
    if (v->is_int()) return v->as_i64();
    if (v->is_bool()) return v->as_bool() ? 1 : 0;
    if (v->is_string()) {
        const std::string& s = v->as_str();
        if (s.empty()) return def_v;
        int64_t acc = 0;
        for (char c : s) {
            if (c == '0' || c == '1') {
                acc = (acc << 1) | (c - '0');
            } else {
                return std::strtoll(s.c_str(), nullptr, 0);
            }
        }
        return acc;
    }
    return def_v;
}

bool is_attr_truthy(const OrderedMap<JsonValue>& attrs, const std::string& key) {
    return parse_bin_or_int_attr(attrs, key, 0) != 0;
}

bool is_module_blackbox(const RtlilModule& m) {
    return is_attr_truthy(m.attributes, "blackbox");
}

int32_t max_module_bit(const RtlilModule& m) {
    int32_t mx = 1;
    for (const auto& [_, p] : m.ports) {
        for (SigBit b : p.bits) if (b > mx) mx = b;
    }
    for (const auto& [_, c] : m.cells) {
        for (const auto& [__, bs] : c.connections) {
            for (SigBit b : bs) if (b > mx) mx = b;
        }
    }
    for (const auto& [_, n] : m.netnames) {
        for (SigBit b : n.bits) if (b > mx) mx = b;
    }
    return mx;
}

uint64_t parse_bin_u64(std::string_view bin_str) {
    uint64_t v = 0;
    for (char c : bin_str) {
        if (c == '0' || c == '1') {
            v = (v << 1) | static_cast<uint64_t>(c - '0');
        } else if (!std::isspace(static_cast<unsigned char>(c))) {
            throw std::runtime_error("Invalid binary string: " + std::string(bin_str));
        }
    }
    return v;
}

std::string format_bin_u64(uint64_t val, int width) {
    if (width <= 0) return "";
    std::string s(static_cast<size_t>(width), '0');
    for (int i = 0; i < width; ++i) {
        char bit_c = (i < 64) ? static_cast<char>('0' + ((val >> i) & 1u)) : '0';
        s[static_cast<size_t>(width - 1 - i)] = bit_c;
    }
    return s;
}

// ============================================================================
// Boolean Gate Definitions (Exact match to map_resource_json.py FUNCTIONS)
// ============================================================================
const std::vector<BoolGateDef>& bool_gate_defs() {
    static const std::vector<BoolGateDef> defs = {
        {"$_NOT_",    "A",   1, 0x1ULL},
        {"$_BUF_",    "A",   1, 0x2ULL},
        {"$_AND_",    "AB",  2, 0x8ULL},
        {"$_NAND_",   "AB",  2, 0x7ULL},
        {"$_OR_",     "AB",  2, 0xEULL},
        {"$_NOR_",    "AB",  2, 0x1ULL},
        {"$_XOR_",    "AB",  2, 0x6ULL},
        {"$_XNOR_",   "AB",  2, 0x9ULL},
        {"$_ANDNOT_", "AB",  2, 0x2ULL},
        {"$_ORNOT_",  "AB",  2, 0xBULL},
        {"$_MUX_",    "ABS", 3, 0xCAULL},
        {"$_NMUX_",   "ABS", 3, 0x35ULL},
    };
    return defs;
}

const BoolGateDef* find_bool_gate(std::string_view cell_type) {
    if (cell_type.size() < 5 || cell_type[0] != '$') return nullptr;
    for (const auto& g : bool_gate_defs()) {
        if (cell_type == g.cell_type) return &g;
    }
    return nullptr;
}

// ============================================================================
// BLIF Reader & Accelerated Writer
// ============================================================================
namespace {

uint64_t compute_cube_truth(const std::vector<std::string>& inputs, const std::vector<std::vector<std::string>>& cover, int max_inputs) {
    int n = static_cast<int>(inputs.size());
    if (n > max_inputs || n > 6) {
        throw std::runtime_error("BLIF node exceeds supported inputs");
    }
    if (cover.empty()) return 0ULL;
    int polarity = -1;
    for (const auto& row : cover) {
        if (row.empty() || (row.back() != "0" && row.back() != "1")) {
            throw std::runtime_error("Invalid BLIF cover polarity");
        }
        int p = (row.back() == "1") ? 1 : 0;
        if (polarity < 0) polarity = p;
        else if (polarity != p) throw std::runtime_error("Mixed BLIF cover polarity");
    }
    uint64_t full_mask = (n == 6) ? ~0ULL : ((1ULL << (1 << n)) - 1ULL);
    uint64_t result = polarity ? 0ULL : full_mask;
    for (const auto& row : cover) {
        std::string pattern;
        if (n == 0) {
            if (row.size() != 1) throw std::runtime_error("Invalid constant cover");
        } else {
            if (row.size() != 2 || static_cast<int>(row[0].size()) != n) {
                throw std::runtime_error("Invalid cube width");
            }
            for (char c : row[0]) {
                if (c != '0' && c != '1' && c != '-') throw std::runtime_error("Invalid cube character");
            }
            pattern = row[0];
        }
        int total_a = 1 << n;
        for (int a = 0; a < total_a; ++a) {
            bool match = true;
            for (int i = 0; i < n; ++i) {
                if (pattern[static_cast<size_t>(i)] != '-' && (pattern[static_cast<size_t>(i)] - '0') != ((a >> i) & 1)) {
                    match = false;
                    break;
                }
            }
            if (match) {
                if (polarity) result |= (1ULL << a);
                else result &= ~(1ULL << a);
            }
        }
    }
    return result;
}

} // namespace

BlifNetwork read_blif_file(std::string_view path, int max_inputs) {
    std::string raw_text = read_file_text(path);
    // Join backslash line continuations `\\\s*\n`
    std::string text;
    text.reserve(raw_text.size());
    for (size_t i = 0; i < raw_text.size(); ++i) {
        if (raw_text[i] == '\\') {
            size_t j = i + 1;
            while (j < raw_text.size() && (raw_text[j] == ' ' || raw_text[j] == '\t' || raw_text[j] == '\r')) ++j;
            if (j < raw_text.size() && raw_text[j] == '\n') {
                text.push_back(' ');
                i = j;
                continue;
            }
        }
        text.push_back(raw_text[i]);
    }

    BlifNetwork net;
    struct RawNode {
        std::vector<std::string> inputs;
        std::string output;
        std::vector<std::vector<std::string>> cover;
    };
    std::vector<RawNode> raw_nodes;
    int cur_node = -1;

    for (const auto& raw_line : split_lines(text)) {
        size_t hash_p = raw_line.find('#');
        std::string line = trim(hash_p == std::string::npos ? std::string_view(raw_line) : std::string_view(raw_line).substr(0, hash_p));
        if (line.empty()) continue;
        auto words = split_ws(line);
        if (words.empty()) continue;
        if (words[0] == ".inputs") {
            net.inputs.insert(net.inputs.end(), words.begin() + 1, words.end());
            cur_node = -1;
        } else if (words[0] == ".outputs") {
            net.outputs.insert(net.outputs.end(), words.begin() + 1, words.end());
            cur_node = -1;
        } else if (words[0] == ".names") {
            if (words.size() < 2) throw std::runtime_error("Empty .names in BLIF");
            RawNode rn;
            rn.inputs.assign(words.begin() + 1, words.end() - 1);
            rn.output = words.back();
            cur_node = static_cast<int>(raw_nodes.size());
            raw_nodes.push_back(std::move(rn));
        } else if (words[0] == ".model" || words[0] == ".end") {
            cur_node = -1;
        } else if (words[0][0] == '.') {
            throw std::runtime_error("Unsupported BLIF directive: " + words[0]);
        } else {
            if (cur_node < 0) throw std::runtime_error("Cover without .names in BLIF");
            raw_nodes[static_cast<size_t>(cur_node)].cover.push_back(std::move(words));
        }
    }

    std::unordered_set<std::string> drivers(net.inputs.begin(), net.inputs.end());
    std::unordered_set<std::string> out_set(net.outputs.begin(), net.outputs.end());
    if (drivers.size() != net.inputs.size() || out_set.size() != net.outputs.size()) {
        throw std::runtime_error("Duplicate BLIF ports");
    }

    net.nodes.reserve(raw_nodes.size());
    std::unordered_map<std::string, size_t> node_by_out;
    for (const auto& rn : raw_nodes) {
        if (drivers.count(rn.output)) throw std::runtime_error("Multiple net drivers in BLIF: " + rn.output);
        drivers.insert(rn.output);
        BlifNode bn;
        bn.inputs = rn.inputs;
        bn.output = rn.output;
        bn.truth = compute_cube_truth(rn.inputs, rn.cover, max_inputs);
        node_by_out[bn.output] = net.nodes.size();
        net.nodes.push_back(std::move(bn));
    }

    for (const auto& o : net.outputs) {
        if (!drivers.count(o)) throw std::runtime_error("Undriven BLIF output: " + o);
    }
    for (const auto& nd : net.nodes) {
        for (const auto& inp : nd.inputs) {
            if (!drivers.count(inp)) throw std::runtime_error("Undriven BLIF signal: " + inp);
        }
    }

    std::vector<int> pending(net.nodes.size(), 0);
    std::vector<std::vector<size_t>> fanouts(net.nodes.size());
    std::deque<size_t> ready;
    for (size_t i = 0; i < net.nodes.size(); ++i) {
        std::unordered_set<size_t> deps;
        for (const auto& inp : net.nodes[i].inputs) {
            auto it = node_by_out.find(inp);
            if (it != node_by_out.end()) deps.insert(it->second);
        }
        pending[i] = static_cast<int>(deps.size());
        for (size_t p : deps) fanouts[p].push_back(i);
        if (pending[i] == 0) ready.push_back(i);
    }

    while (!ready.empty()) {
        size_t u = ready.front();
        ready.pop_front();
        net.topological.push_back(net.nodes[u].output);
        for (size_t v : fanouts[u]) {
            if (--pending[v] == 0) ready.push_back(v);
        }
    }
    if (net.topological.size() != net.nodes.size()) {
        throw std::runtime_error("Combinational cycle in BLIF");
    }
    return net;
}

std::string format_blif_text(const BlifNetwork& net, std::string_view model_name, bool emit_zero_const_cube) {
    std::string out;
    out.reserve(1024 + net.nodes.size() * 48);
    out.append(".model ").append(model_name).push_back('\n');
    out.append(".inputs");
    for (const auto& s : net.inputs) { out.push_back(' '); out.append(s); }
    out.push_back('\n');
    out.append(".outputs");
    for (const auto& s : net.outputs) { out.push_back(' '); out.append(s); }
    out.push_back('\n');
    for (const auto& nd : net.nodes) {
        int k = static_cast<int>(nd.inputs.size());
        out.append(".names");
        for (const auto& s : nd.inputs) { out.push_back(' '); out.append(s); }
        out.push_back(' ');
        out.append(nd.output);
        out.push_back('\n');
        if (k > 0 && nd.truth == 0ULL && emit_zero_const_cube) {
            out.append(static_cast<size_t>(k), '-');
            out.append(" 0\n");
            continue;
        }
        int rows = 1 << k;
        for (int a = 0; a < rows; ++a) {
            if ((nd.truth >> a) & 1ULL) {
                if (k > 0) {
                    for (int i = 0; i < k; ++i) out.push_back(static_cast<char>('0' + ((a >> i) & 1)));
                    out.append(" 1\n");
                } else {
                    out.append("1\n");
                }
            }
        }
    }
    out.append(".end\n");
    return out;
}

void write_blif_fast(std::string_view path, const BlifNetwork& net, bool emit_zero_const_cube) {
    for (const auto& nd : net.nodes) {
        if (nd.inputs.size() > 6) {
            write_file_text(path, format_blif_text(net, "functional", emit_zero_const_cube));
            return;
        }
    }
    make_dirs(parent_dir(path), true);
    std::unordered_map<std::string, int32_t> name_to_id;
    name_to_id.reserve(net.inputs.size() + net.outputs.size() + net.nodes.size() + 8);
    std::string names_blob;
    names_blob.reserve((net.inputs.size() + net.nodes.size()) * 16);
    std::vector<int32_t> offsets{0};

    auto intern = [&](const std::string& s) -> int32_t {
        auto it = name_to_id.find(s);
        if (it != name_to_id.end()) return it->second;
        int32_t id = static_cast<int32_t>(name_to_id.size());
        name_to_id.emplace(s, id);
        names_blob.append(s);
        offsets.push_back(static_cast<int32_t>(names_blob.size()));
        return id;
    };

    std::vector<int32_t> in_ids, out_ids, node_in_offsets{0}, node_in_ids, node_out_ids;
    std::vector<uint64_t> node_truths;
    in_ids.reserve(net.inputs.size());
    out_ids.reserve(net.outputs.size());
    node_in_offsets.reserve(net.nodes.size() + 1);
    node_out_ids.reserve(net.nodes.size());
    node_truths.reserve(net.nodes.size());

    for (const auto& s : net.inputs) in_ids.push_back(intern(s));
    for (const auto& s : net.outputs) out_ids.push_back(intern(s));
    for (const auto& nd : net.nodes) {
        for (const auto& inp : nd.inputs) node_in_ids.push_back(intern(inp));
        node_in_offsets.push_back(static_cast<int32_t>(node_in_ids.size()));
        node_out_ids.push_back(intern(nd.output));
        node_truths.push_back(nd.truth);
    }

    int32_t dummy_i = 0;
    uint64_t dummy_u = 0;
    std::string resolved = resolve_path(path);
    int32_t rc = SynAccelLib::instance().write_blif_file(
        resolved.c_str(),
        static_cast<int32_t>(name_to_id.size()),
        names_blob.data(),
        offsets.data(),
        static_cast<int32_t>(in_ids.size()),
        in_ids.empty() ? &dummy_i : in_ids.data(),
        static_cast<int32_t>(out_ids.size()),
        out_ids.empty() ? &dummy_i : out_ids.data(),
        static_cast<int32_t>(net.nodes.size()),
        node_in_offsets.data(),
        node_in_ids.empty() ? &dummy_i : node_in_ids.data(),
        node_out_ids.empty() ? &dummy_i : node_out_ids.data(),
        node_truths.empty() ? &dummy_u : node_truths.data(),
        emit_zero_const_cube ? 1 : 0
    );
    if (rc != 0) {
        throw std::runtime_error("accel_write_blif_file failed for " + resolved);
    }
}

// ============================================================================
// gw5a_functional_map helpers
// ============================================================================
namespace {

std::vector<int> support_indices(uint64_t truth, int k) {
    std::vector<int> used;
    int rows = 1 << k;
    for (int i = 0; i < k; ++i) {
        int bit = 1 << i;
        for (int a = 0; a < rows; ++a) {
            if (((truth >> a) ^ (truth >> (a ^ bit))) & 1ULL) {
                used.push_back(i);
                break;
            }
        }
    }
    return used;
}

uint64_t project_truth_to_support(uint64_t truth, const std::vector<int>& used) {
    uint64_t reduced = 0;
    int rrows = 1 << static_cast<int>(used.size());
    for (int a = 0; a < rrows; ++a) {
        int orig_a = 0;
        for (size_t i = 0; i < used.size(); ++i) {
            orig_a |= ((a >> i) & 1) << used[i];
        }
        reduced |= ((truth >> orig_a) & 1ULL) << a;
    }
    return reduced;
}

} // namespace

std::pair<std::vector<std::string>, uint64_t> reduce_truth_function(const std::vector<std::string>& inputs, uint64_t truth) {
    auto used = support_indices(truth, static_cast<int>(inputs.size()));
    std::vector<std::string> rins;
    rins.reserve(used.size());
    for (int idx : used) rins.push_back(inputs[static_cast<size_t>(idx)]);
    return {std::move(rins), project_truth_to_support(truth, used)};
}

std::pair<std::vector<SigBit>, uint64_t> reduce_sigbit_function(const std::vector<SigBit>& inputs, uint64_t truth) {
    auto used = support_indices(truth, static_cast<int>(inputs.size()));
    std::vector<SigBit> rins;
    rins.reserve(used.size());
    for (int idx : used) rins.push_back(inputs[static_cast<size_t>(idx)]);
    return {std::move(rins), project_truth_to_support(truth, used)};
}

uint64_t cofactor_truth(uint64_t truth, int k, int select_idx, int value) {
    uint64_t res = 0;
    int sub_rows = 1 << (k - 1);
    int lo_mask = (1 << select_idx) - 1;
    for (int a = 0; a < sub_rows; ++a) {
        int full_a = (a & lo_mask) | ((a >> select_idx) << (select_idx + 1)) | (value << select_idx);
        res |= ((truth >> full_a) & 1ULL) << a;
    }
    return res;
}

JsonValue validate_dedicated_mux_packing(const RtlilDesign& design) {
    int64_t mux5 = 0, mux6 = 0;
    for (const auto& [mn, mptr] : design.modules) {
        const RtlilModule& m = *mptr;
        if (is_module_blackbox(m)) continue;
        std::unordered_map<SigBit, std::string> drivers;
        std::unordered_map<SigBit, int> loads;
        for (const auto& [cname, c] : m.cells) {
            for (const auto& [p, bs] : c.connections) {
                std::string pdir = c.port_directions.value_or(p, "");
                for (SigBit b : bs) {
                    if (pdir == "output") drivers[b] = cname;
                    else if (pdir == "input" || pdir == "inout") loads[b] += 1;
                }
            }
        }
        for (const auto& [cname, c] : m.cells) {
            if (c.type != "MUX2_LUT5" && c.type != "MUX2_LUT6") continue;
            bool is5 = (c.type == "MUX2_LUT5");
            SigBit b0 = c.connections.at("I0")[0];
            SigBit b1 = c.connections.at("I1")[0];
            if (b0 == b1) throw std::runtime_error("Illegal or shared dedicated mux leaf: " + cname);
            for (SigBit b : {b0, b1}) {
                auto dit = drivers.find(b);
                if (dit == drivers.end() || loads[b] != 1) {
                    throw std::runtime_error("Illegal or shared dedicated mux leaf: " + cname);
                }
                const std::string& dt = m.cells.at(dit->second).type;
                bool ok = is5 ? (dt == "LUT1" || dt == "LUT2" || dt == "LUT3" || dt == "LUT4") : (dt == "MUX2_LUT5");
                if (!ok) throw std::runtime_error("Illegal dedicated mux child type on " + cname);
            }
            if (is5) ++mux5; else ++mux6;
        }
    }
    JsonValue res = JsonValue::object();
    res["mux5_checked"] = mux5;
    res["mux6_checked"] = mux6;
    return res;
}

// ============================================================================
// ABC & Yosys Invocation
// ============================================================================
std::pair<JsonValue, std::string> invoke_abc(std::string_view commands, std::string_view logfile, double timeout_sec) {
    double eff_timeout = clipped_timeout(timeout_sec);
    std::string cwd = parent_dir(resolve_path(logfile));
    make_dirs(cwd, true);
    ProcResult pr = run_process_to_log(
        {runtime_paths().abc_exe, "-c", std::string(commands)},
        cwd,
        logfile,
        {},
        eff_timeout
    );
    std::string state = "error";
    if (pr.timed_out) {
        state = "timeout";
    } else if (pr.returncode == 0) {
        bool has_done = false;
        for (const auto& ln : split_lines(pr.output)) {
            if (trim(ln) == "NEXTGOWIN_DONE") { has_done = true; break; }
        }
        const char* bad_markers[] = {
            "LMS manager is not running", "unknown command", "Error:", "Error in", "Assertion failed"
        };
        bool has_err = false;
        for (const char* bm : bad_markers) {
            if (pr.output.find(bm) != std::string::npos) { has_err = true; break; }
        }
        if (has_done && !has_err) state = "ok";
    }
    JsonValue rec = JsonValue::object();
    rec["status"] = state;
    rec["returncode"] = pr.timed_out ? JsonValue(nullptr) : JsonValue(pr.returncode);
    rec["wall_seconds"] = pr.wall_seconds;
    rec["command"] = std::string(commands);
    rec["log"] = std::string(logfile);
    return {std::move(rec), std::move(pr.output)};
}

JsonValue run_abc_cec(std::string_view original_blif, std::string_view mapped_blif, std::string_view logfile, double timeout_sec) {
    std::string ref_sha = sha256_file(original_blif);
    std::string cand_sha = sha256_file(mapped_blif);
    std::string cmd = "cec -n \"" + posix_path(resolve_path(original_blif)) + "\" \"" + posix_path(resolve_path(mapped_blif)) + "\"; echo NEXTGOWIN_DONE";
    auto [res, text] = invoke_abc(cmd, logfile, timeout_sec);
    bool unchanged = (sha256_file(original_blif) == ref_sha) && (sha256_file(mapped_blif) == cand_sha);
    bool eq = (res["status"].as_str() == "ok") && (text.find("Networks are equivalent") != std::string::npos) && unchanged;
    res["reference_sha256"] = ref_sha;
    res["candidate_sha256"] = cand_sha;
    res["inputs_unchanged"] = unchanged;
    res["equivalent"] = eq;
    if (!eq && res["status"].as_str() == "ok") {
        res["status"] = "unproven";
    }
    return res;
}

namespace {

BlifNetwork extract_cone_slice(const BlifNetwork& net, const std::vector<std::string>& slice_outs, const std::unordered_map<std::string, size_t>& drv_map) {
    std::vector<uint8_t> keep(net.nodes.size(), 0);
    std::deque<size_t> q;
    for (const auto& out_nm : slice_outs) {
        auto it = drv_map.find(out_nm);
        if (it != drv_map.end() && !keep[it->second]) {
            keep[it->second] = 1;
            q.push_back(it->second);
        }
    }
    for (const char* cn : {"c0", "c1"}) {
        auto it = drv_map.find(cn);
        if (it != drv_map.end()) keep[it->second] = 1;
    }
    while (!q.empty()) {
        size_t cur = q.front();
        q.pop_front();
        for (const auto& inp : net.nodes[cur].inputs) {
            auto it = drv_map.find(inp);
            if (it != drv_map.end() && !keep[it->second]) {
                keep[it->second] = 1;
                q.push_back(it->second);
            }
        }
    }
    BlifNetwork sub;
    sub.inputs = net.inputs;
    sub.outputs = slice_outs;
    for (size_t i = 0; i < net.nodes.size(); ++i) {
        if (keep[i]) sub.nodes.push_back(net.nodes[i]);
    }
    return sub;
}

} // namespace

JsonValue parallel_sliced_cec(const BlifNetwork& net_a, const BlifNetwork& net_b, std::string_view folder, double timeout_sec) {
    make_dirs(folder, true);
    std::string a_path = path_join(folder, "before.blif");
    std::string b_path = path_join(folder, "after.blif");
    write_blif_fast(a_path, net_a, true);
    write_blif_fast(b_path, net_b, true);

    if (net_a.outputs != net_b.outputs || net_a.inputs != net_b.inputs) {
        throw std::runtime_error("Mismatched CEC primary I/O order between before and after networks");
    }

    if (net_a.outputs.size() <= 2500) {
        return run_abc_cec(a_path, b_path, path_join(folder, "cec.log"), timeout_sec);
    }

    std::string ref_hash = sha256_file(a_path);
    std::string cand_hash = sha256_file(b_path);
    double t0 = perf_counter();

    unsigned int hw = hardware_threads();
    size_t num_slices = std::min<size_t>(8, std::max<size_t>(2, hw / 2));
    size_t slice_sz = (net_a.outputs.size() + num_slices - 1) / num_slices;
    std::vector<std::vector<std::string>> slices;
    for (size_t i = 0; i < net_a.outputs.size(); i += slice_sz) {
        size_t end_i = std::min(net_a.outputs.size(), i + slice_sz);
        slices.emplace_back(net_a.outputs.begin() + i, net_a.outputs.begin() + end_i);
    }

    std::unordered_map<std::string, size_t> drv_a, drv_b;
    drv_a.reserve(net_a.nodes.size());
    drv_b.reserve(net_b.nodes.size());
    for (size_t i = 0; i < net_a.nodes.size(); ++i) drv_a[net_a.nodes[i].output] = i;
    for (size_t i = 0; i < net_b.nodes.size(); ++i) drv_b[net_b.nodes[i].output] = i;

    struct SliceOut {
        JsonValue res;
        std::string log_txt;
        std::string err_msg;
    };
    std::vector<SliceOut> results(slices.size());
    double eff_slice_timeout = clipped_timeout(timeout_sec);

    std::vector<std::thread> workers;
    workers.reserve(slices.size());
    for (size_t si = 0; si < slices.size(); ++si) {
        workers.emplace_back([&, si]() {
            std::string sa = path_join(folder, "_slice_" + std::to_string(si) + "_before.blif");
            std::string sb = path_join(folder, "_slice_" + std::to_string(si) + "_after.blif");
            std::string slog = path_join(folder, "_slice_" + std::to_string(si) + "_cec.log");
            try {
                BlifNetwork sub_a = extract_cone_slice(net_a, slices[si], drv_a);
                BlifNetwork sub_b = extract_cone_slice(net_b, slices[si], drv_b);
                write_blif_fast(sa, sub_a, true);
                write_blif_fast(sb, sub_b, true);

                std::string cmd = "cec -n \"" + posix_path(resolve_path(sa)) + "\" \"" + posix_path(resolve_path(sb)) + "\"; echo NEXTGOWIN_DONE";
                ProcResult pr = run_process_to_log({runtime_paths().abc_exe, "-c", cmd}, resolve_path(folder), slog, {}, eff_slice_timeout);
                bool eq = (!pr.timed_out && pr.returncode == 0 && pr.output.find("Networks are equivalent") != std::string::npos);
                JsonValue r = JsonValue::object();
                r["status"] = eq ? "ok" : (pr.timed_out ? "timeout" : "unproven");
                r["equivalent"] = eq;
                results[si].res = std::move(r);
                results[si].log_txt = std::move(pr.output);
            } catch (const std::exception& ex) {
                results[si].err_msg = ex.what();
            }
            remove_file_if_exists(sa);
            remove_file_if_exists(sb);
            remove_file_if_exists(slog);
        });
    }
    for (auto& th : workers) th.join();

    std::string combined_log;
    bool all_ok = true;
    bool all_eq = true;
    for (size_t si = 0; si < slices.size(); ++si) {
        if (!results[si].err_msg.empty()) {
            throw std::runtime_error("parallel_sliced_cec worker error: " + results[si].err_msg);
        }
        combined_log += "=== Slice " + std::to_string(si + 1) + "/" + std::to_string(slices.size()) + " (" + std::to_string(slices[si].size()) + " outputs) ===\n";
        combined_log += results[si].log_txt + "\n";
        if (results[si].res["status"].as_str() != "ok") all_ok = false;
        if (!results[si].res["equivalent"].as_bool()) all_eq = false;
    }
    std::string log_path = path_join(folder, "cec.log");
    write_file_text(log_path, combined_log);

    bool unchanged = (sha256_file(a_path) == ref_hash) && (sha256_file(b_path) == cand_hash);
    bool final_eq = all_ok && all_eq && unchanged;
    JsonValue out = JsonValue::object();
    out["status"] = final_eq ? "ok" : (all_ok ? "unproven" : "error");
    out["returncode"] = all_ok ? 0 : 1;
    out["wall_seconds"] = perf_counter() - t0;
    out["command"] = "parallel_sliced_cec(slices=" + std::to_string(slices.size()) + ", outputs=" + std::to_string(net_a.outputs.size()) + ")";
    out["log"] = log_path;
    out["reference_sha256"] = ref_hash;
    out["candidate_sha256"] = cand_hash;
    out["inputs_unchanged"] = unchanged;
    out["equivalent"] = final_eq;
    out["parallel_slices"] = static_cast<int64_t>(slices.size());
    return out;
}

std::string quote_yosys_path(std::string_view path) {
    std::string v = posix_path(resolve_path(path));
    for (char c : v) {
        if (c == '\r' || c == '\n' || c == '"') {
            throw std::runtime_error("Unsupported Yosys path: " + v);
        }
    }
    return "\"" + v + "\"";
}

JsonValue run_yosys_commands(const std::vector<std::string>& commands, std::string_view directory, std::string_view name, double timeout_sec) {
    double eff_timeout = clipped_timeout(timeout_sec);
    std::string dir = resolve_path(directory);
    make_dirs(dir, true);
    std::string script = path_join(dir, std::string(name) + ".ys");
    write_file_text(script, join_strings(commands, "\n") + "\n");

    std::string yosys_bin_dir = parent_dir(runtime_paths().yosys_exe);
    std::string cur_path = get_env("PATH");
    std::string new_path = yosys_bin_dir + ";" + cur_path;

    std::string ylog = path_join(dir, std::string(name) + ".log");
    std::string clog = path_join(dir, std::string(name) + ".console.log");

    ProcResult pr = run_process_to_log(
        {runtime_paths().yosys_exe, "-Q", "-T", "-l", ylog, "-s", script},
        dir,
        clog,
        {{"PATH", new_path}, {"OMP_NUM_THREADS", "1"}},
        eff_timeout
    );
    if (pr.timed_out) {
        throw TimeoutError(
            "Yosys step '" + std::string(name) + "' timed out after " + std::to_string(static_cast<int>(eff_timeout))
            + "s (script: " + script + ", console log: " + clog + ")"
        );
    }
    JsonValue res = JsonValue::object();
    res["returncode"] = pr.returncode;
    res["wall_seconds"] = pr.wall_seconds;
    res["binary_sha256"] = sha256_file(runtime_paths().yosys_exe);
    res["script_sha256"] = sha256_file(script);
    res["log"] = std::string(name) + ".log";
    write_json_pretty(path_join(dir, std::string(name) + ".invocation.json"), res);
    if (pr.returncode != 0) {
        std::string raw_out = !pr.output.empty() ? pr.output : (file_exists(clog) ? read_file_text(clog) : "");
        if (raw_out.empty() && file_exists(ylog)) raw_out = read_file_text(ylog);
        auto lines = split_lines(raw_out);
        std::string last_pass;
        std::vector<std::string> recent_warns;
        std::vector<std::string> err_lines;
        std::vector<std::string> tail_nonempty;
        for (const auto& ln : lines) {
            std::string tr = trim(ln);
            if (tr.empty()) continue;
            tail_nonempty.push_back(tr);
            if (tail_nonempty.size() > 16) tail_nonempty.erase(tail_nonempty.begin());
            if (tr.find("Executing ") != std::string::npos && tr.find(" pass") != std::string::npos) {
                last_pass = tr;
                recent_warns.clear();
            } else if (starts_with(tr, "Warning:")) {
                recent_warns.push_back(tr);
                if (recent_warns.size() > 12) recent_warns.erase(recent_warns.begin());
            } else if (starts_with(tr, "ERROR:") || starts_with(tr, "Syntax error") || tr.find("ERROR:") == 0) {
                err_lines.push_back(tr);
            }
        }
        std::string detail = "Yosys step '" + std::string(name) + "' failed with exit code " + std::to_string(pr.returncode)
                           + " (script: " + script + ", console log: " + clog + ")";
        if (!last_pass.empty()) detail += "\n  Active pass: " + last_pass;
        for (const auto& w : recent_warns) detail += "\n  " + w;
        if (!err_lines.empty()) {
            for (const auto& e : err_lines) detail += "\n  " + e;
        } else if (!tail_nonempty.empty()) {
            detail += "\n  Log tail:";
            size_t start_t = (tail_nonempty.size() > 8) ? (tail_nonempty.size() - 8) : 0;
            for (size_t i = start_t; i < tail_nonempty.size(); ++i) detail += "\n    " + tail_nonempty[i];
        }
        throw std::runtime_error(detail);
    }
    return res;
}

} // namespace ng
