"""Build NextGowin's clean, Python-free GitHub release & source archives (Tier 1 x86-64 + SSE2/SSE3 only).

Emits into `--out-dir` (default `dist/github_release`):
  1. `nextgowin-env.zip`       — Self-contained Windows x64 execution environment (Tier 1 baseline, 0 Python)
  2. `nextgowin-resources.zip` — Compact GW5AST-138C calibration models & synthesis cell libraries
  3. `nextgowin-source.zip`    — Clean GitHub source archive (reproducible build, 0 historical/temp/private artifacts)
  4. `README.md`, `BUILD.md`, `THIRD_PARTY_NOTICES.txt`, and `SHA256SUMS.txt`

Note: Python is used strictly as a build-time packaging tool; the released runtime packages
neither contain nor require Python (`python*.dll`, `python*.exe`, `.pyd`, `.py`, `.pyc`).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]

# Optional workspace budget check when running inside the main development workspace
if (ROOT / "scripts/workspace_budget.py").is_file():
    sys.path.insert(0, str(ROOT / "scripts"))
    from workspace_budget import reserve as _ws_reserve
else:
    def _ws_reserve(_root: Path, _bytes: int) -> None:
        pass

# Inbox Windows OS DLLs guaranteed present on all Windows 10/11 x64 installations
INBOX_WINDOWS_OS_DLLS = {
    "KERNEL32.DLL",
    "USER32.DLL",
    "GDI32.DLL",
    "ADVAPI32.DLL",
    "SHELL32.DLL",
    "SHLWAPI.DLL",
    "OLE32.DLL",
    "OLEAUT32.DLL",
    "WS2_32.DLL",
    "IPHLPAPI.DLL",
    "NETAPI32.DLL",
    "USERENV.DLL",
    "CRYPT32.DLL",
    "BCRYPT.DLL",
    "RPCRT4.DLL",
    "VERSION.DLL",
    "WINMM.DLL",
    "MSVCRT.DLL",
    "IMM32.DLL",
}

def build_forbidden_private_substrings() -> list[bytes]:
    items: list[bytes] = []
    cur_user = os.environ.get("USERNAME", "").strip().lower()
    if len(cur_user) >= 3:
        items.append(cur_user.encode("utf-8"))
    for part in ROOT.parts:
        pl = part.lower()
        if pl not in ("c:\\", "d:\\", "users", "documents", "nextgowin", "repos", "src", "github", "work") and len(pl) >= 5:
            items.append(pl.encode("utf-8"))
    return items

FORBIDDEN_GOWIN_FILENAMES = {
    "gowin.dll",
    "gwsyn.dll",
    "gw_sh.exe",
    "gw_ide.exe",
    "prim_sim.v",
    "gw5ast-138c.dat",
    "gw5ast-138c.fse",
    "gw5ast-138c.tm",
}

RESOURCE_REL_FILES = [
    "results/timing/gowin138_model.json",
    "results/timing/gowin138_model_ff_checked.json",
    "results/native_route_nodes_003/manifest.json",
    "results/native_route_runtime_019/manifest.json",
    "results/native_route_runtime_019/native_register_ports.json",
    "results/native_ff_ports_001/manifest.json",
    "results/native_ff_ports_001/ff_attributes.json",
    "configs/gw5a_arch_cost_v2.json",
    "configs/fpgafly_general_coopt.json",
    "configs/fpgafly_native.json",
    "configs/cpu_gao_native.json",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pe_imports(path: Path) -> list[str]:
    data = path.read_bytes()
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    magic = struct.unpack_from("<H", data, pe_off + 24)[0]
    num_sec = struct.unpack_from("<H", data, pe_off + 6)[0]
    opt_sz = struct.unpack_from("<H", data, pe_off + 20)[0]
    imp_rva_off = pe_off + 24 + (120 if magic == 0x20B else 104)
    imp_rva, imp_sz = struct.unpack_from("<II", data, imp_rva_off)
    if imp_rva == 0 or imp_sz == 0:
        return []
    sec_off = pe_off + 24 + opt_sz
    secs = []
    for i in range(num_sec):
        s = sec_off + i * 40
        vsz, va, rsz, ptr = struct.unpack_from("<IIII", data, s + 8)
        secs.append((va, max(vsz, rsz), ptr))

    def rva2off(rva: int) -> int:
        for va, sz, ptr in secs:
            if va <= rva < va + sz:
                return ptr + (rva - va)
        raise RuntimeError(f"Unmapped RVA {rva:#x} in {path}")

    pos = rva2off(imp_rva)
    dlls = []
    while True:
        ilt, _, _, name_rva, iat = struct.unpack_from("<IIIII", data, pos)
        if ilt == 0 and name_rva == 0 and iat == 0:
            break
        noff = rva2off(name_rva)
        end = data.index(b"\x00", noff)
        dlls.append(data[noff:end].decode("ascii", errors="replace"))
        pos += 20
    return dlls


def strip_pe_dwarf_debug(src: Path, dst: Path) -> tuple[int, int]:
    """Strip trailing COFF/DWARF `/...` debug sections and COFF symbol table from a MinGW PE64 DLL."""
    raw = bytearray(src.read_bytes())
    orig_len = len(raw)
    pe_off = struct.unpack_from("<I", raw, 0x3C)[0]
    num_sec = struct.unpack_from("<H", raw, pe_off + 6)[0]
    opt_sz = struct.unpack_from("<H", raw, pe_off + 20)[0]
    sec_align = struct.unpack_from("<I", raw, pe_off + 24 + 32)[0]
    sec_off = pe_off + 24 + opt_sz

    keep_secs = 0
    max_file_end = 0
    max_virt_end = 0
    for i in range(num_sec):
        s = sec_off + i * 40
        name = bytes(raw[s : s + 8]).rstrip(b"\x00")
        vsz, va, rsz, ptr, _, _, _, _, ch = struct.unpack_from("<IIIIIIHHI", raw, s + 8)
        is_dwarf = name.startswith(b"/") and ((ch & 0x02000000) != 0)
        if is_dwarf:
            break
        keep_secs += 1
        if ptr > 0 and rsz > 0:
            max_file_end = max(max_file_end, ptr + rsz)
        end_va = va + max(vsz, rsz)
        aligned_va = ((end_va + sec_align - 1) // sec_align) * sec_align
        max_virt_end = max(max_virt_end, aligned_va)

    if keep_secs < num_sec and max_file_end > 0:
        for i in range(keep_secs, num_sec):
            s = sec_off + i * 40
            raw[s : s + 40] = b"\x00" * 40
        struct.pack_into("<H", raw, pe_off + 6, keep_secs)
        struct.pack_into("<II", raw, pe_off + 12, 0, 0)
        struct.pack_into("<I", raw, pe_off + 24 + 56, max_virt_end)
        struct.pack_into("<I", raw, pe_off + 24 + 64, 0)
        raw = raw[:max_file_end]

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(bytes(raw))
    return orig_len, len(raw)


def copy_yosys_share_minimal(suite_root: Path, dst_share_yosys: Path) -> list[Path]:
    src_y = suite_root / "share/yosys"
    copied = []
    for p in sorted(src_y.iterdir()):
        if p.is_file() and p.suffix == ".v":
            tgt = dst_share_yosys / p.name
            tgt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, tgt)
            copied.append(tgt)
        elif p.is_dir() and p.name in ("gowin", "choices", "sdc"):
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() != ".py":
                    rel = f.relative_to(src_y)
                    tgt = dst_share_yosys / rel
                    tgt.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, tgt)
                    copied.append(tgt)
    return copied


def verify_zero_python_artifacts(pkg_dir: Path) -> None:
    forbidden = []
    for f in sorted(pkg_dir.rglob("*")):
        if not f.is_file():
            continue
        nl = f.name.lower()
        ext = f.suffix.lower()
        if ext in (".py", ".pyc", ".pyo", ".pyd") or nl.startswith("python"):
            forbidden.append(f.relative_to(pkg_dir).as_posix())
    if forbidden:
        raise RuntimeError(f"Python artifacts found inside {pkg_dir.name}: {forbidden}")


def audit_zip_cleanliness(zip_path: Path, forbid_python: bool) -> None:
    forbidden_subs = build_forbidden_private_substrings()
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            bn = Path(info.filename).name.lower()
            ext = Path(info.filename).suffix.lower()
            if bn in FORBIDDEN_GOWIN_FILENAMES:
                raise RuntimeError(f"Proprietary Gowin file detected inside {zip_path.name}: {info.filename}")
            if forbid_python and (ext in (".py", ".pyc", ".pyo", ".pyd") or bn.startswith("python")):
                raise RuntimeError(f"Python file detected inside {zip_path.name}: {info.filename}")
            # Skip matching literal check patterns inside build_standalone_packages.py itself for c:\users\
            is_self_packager = info.filename.endswith("scripts/build_standalone_packages.py")
            low_data = zf.read(info.filename).lower()
            for needle in forbidden_subs:
                if needle in low_data:
                    raise RuntimeError(
                        f"Private path/identifier {needle!r} detected inside {zip_path.name} -> {info.filename}"
                    )
            if not is_self_packager:
                for pfx in (b"c:\\users\\", b"c:/users/"):
                    idx = 0
                    while True:
                        idx = low_data.find(pfx, idx)
                        if idx < 0:
                            break
                        tail = low_data[idx + len(pfx) : idx + len(pfx) + 8]
                        if not tail.startswith(b"public"):
                            raise RuntimeError(
                                f"Private user path {low_data[idx:idx+32]!r} detected inside {zip_path.name} -> {info.filename}"
                            )
                        idx += len(pfx)


def write_deterministic_zip(src_dir: Path, zip_path: Path, top_prefix: str) -> None:
    if zip_path.exists():
        zip_path.unlink()
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for f in sorted(src_dir.rglob("*")):
            if f.is_file():
                arcname = f"{top_prefix}/{f.relative_to(src_dir).as_posix()}"
                zf.write(f, arcname)


def build_resource_package(stage_root: Path, out_dir: Path, suite_root: Path) -> dict:
    res_dir = stage_root / "nextgowin-resources"
    res_dir.mkdir(parents=True)

    for rel in RESOURCE_REL_FILES:
        src = ROOT / rel
        dst = res_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.txt", res_dir / "THIRD_PARTY_NOTICES.txt")
    copy_yosys_share_minimal(suite_root, res_dir / "share/yosys")
    verify_zero_python_artifacts(res_dir)

    files_inv = {}
    for f in sorted(res_dir.rglob("*")):
        if f.is_file():
            rel_posix = f.relative_to(res_dir).as_posix()
            files_inv[rel_posix] = {
                "bytes": f.stat().st_size,
                "sha256": sha256_file(f),
            }

    manifest = {
        "schema": "nextgowin-resource-package-v3",
        "target_device": "GW5AST-138C-FCPBGA676A-1",
        "target_part": "GW5AST-LV138FPG676AC1/I0",
        "supported_gowin_version": "Gowin V1.9.12.04 x64",
        "python_files_present": False,
        "validated_gowin_sdk_sha256": {
            "bin/gwsyn.dll": "254eac1ed05c7d1168889473dcc85203032fa4cd641c83bd5bbe4ea61a55b6b0",
            "bin/gowin.dll": "bc9472a5a22cf4a94e39e07439f261f3354c668f92ef9abc735e6678bea53b7b",
            "simlib/gw5a/prim_sim.v": "7109c2568188627465d218bc099cf72f12e90807c21d4d862d2335b499043544",
            "share/device/GW5AST-138C/GW5AST-138C.dat": "19e3ec885f359ec571c09ed5d3face1b0c10971b9e0f569fdf703911993c9d33",
            "share/device/GW5AST-138C/GW5AST-138C.fse": "19f859e61df5b8da361330d42d1250bd31d03bcfa2912c3ccfbf983cc4329030",
            "share/device/GW5AST-138C/GW5AST-138C.tm": "a3558f35685ebc58eefce834e82059ce651b9e0f569fdf703911993c9d33"[:0] + "a3558f35685ebc58eefce834e82059ce651b9e2a6cbca2d6668094153ca0707f",
        },
        "file_count": len(files_inv),
        "total_uncompressed_bytes": sum(m["bytes"] for m in files_inv.values()),
        "files": files_inv,
    }
    (res_dir / "resource_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    zip_path = out_dir / "nextgowin-resources.zip"
    write_deterministic_zip(res_dir, zip_path, "nextgowin-resources")
    audit_zip_cleanliness(zip_path, forbid_python=True)

    return {
        "zip": str(zip_path),
        "uncompressed_bytes": sum(f.stat().st_size for f in res_dir.rglob("*") if f.is_file()),
        "zip_bytes": zip_path.stat().st_size,
        "zip_sha256": sha256_file(zip_path),
        "file_count": len(files_inv) + 1,
        "python_files_present": False,
    }


def build_env_package(stage_root: Path, out_dir: Path, bins_dir: Path, suite_root: Path) -> dict:
    env_dir = stage_root / "nextgowin-env"
    env_dir.mkdir(parents=True)

    # 1. Native Tier 1 launcher + gowin-path.txt + docs
    shutil.copy2(bins_dir / "nextgowin.exe", env_dir / "nextgowin.exe")
    (env_dir / "gowin-path.txt").write_text("C:\\Gowin\\Gowin_V1.9.12.04_x64\n", encoding="utf-8")
    shutil.copy2(ROOT / "README.md", env_dir / "README.md")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.txt", env_dir / "THIRD_PARTY_NOTICES.txt")

    # 2. Tier 1 native DLLs + 64-bit abc.exe inside bin/
    bin_dir = env_dir / "bin"
    bin_dir.mkdir(parents=True)
    for fn in ("nextgowin_core.dll", "ownpr_bridge.dll", "syn_accel.dll", "abc.exe"):
        src = bins_dir / "bin" / fn
        assert src.is_file(), f"Missing compiled Tier 1 binary: {src}"
        shutil.copy2(src, bin_dir / fn)

    # 3. Yosys + yosys-abc + 8 DWARF-stripped MinGW runtime DLLs inside bin/
    shutil.copy2(suite_root / "bin/yosys.exe", bin_dir / "yosys.exe")
    shutil.copy2(suite_root / "bin/yosys-abc.exe", bin_dir / "yosys-abc.exe")
    mingw_dlls = [
        "libgcc_s_seh-1.dll",
        "libwinpthread-1.dll",
        "libstdc++-6.dll",
        "libffi-8.dll",
        "libreadline8.dll",
        "tcl86.dll",
        "zlib1.dll",
        "libtermcap-0.dll",
    ]
    strip_stats = {}
    for dn in mingw_dlls:
        orig_sz, new_sz = strip_pe_dwarf_debug(suite_root / "lib" / dn, bin_dir / dn)
        if new_sz < orig_sz:
            strip_stats[dn] = {"before_bytes": orig_sz, "after_bytes": new_sz}

    copy_yosys_share_minimal(suite_root, env_dir / "share/yosys")
    verify_zero_python_artifacts(env_dir)

    # 4. Audit 100% Transitive PE64 DLL Import Closure across ALL .exe/.dll files
    pe_files = sorted(
        f for f in env_dir.rglob("*") if f.is_file() and f.suffix.lower() in (".exe", ".dll", ".pyd")
    )
    bundled_dll_names = {f.name.upper() for f in pe_files if f.suffix.lower() == ".dll"}
    external_os_imports: set[str] = set()
    unaccounted_imports: dict[str, list[str]] = {}
    binary_inventory = {}

    for pf in pe_files:
        imps = pe_imports(pf)
        bad = []
        for imp in imps:
            iu = imp.upper()
            if iu in bundled_dll_names:
                continue
            if iu in INBOX_WINDOWS_OS_DLLS:
                external_os_imports.add(iu)
            else:
                bad.append(imp)
        if bad:
            unaccounted_imports[pf.relative_to(env_dir).as_posix()] = bad
        binary_inventory[pf.relative_to(env_dir).as_posix()] = {
            "bytes": pf.stat().st_size,
            "sha256": sha256_file(pf),
            "imports": imps,
        }

    if unaccounted_imports:
        raise RuntimeError(f"Standalone DLL closure FAILED — non-inbox external DLL imports found: {unaccounted_imports}")

    env_manifest = {
        "schema": "nextgowin-execution-env-v3-tier1-sse3",
        "launcher": "nextgowin.exe",
        "gowin_config_file": "gowin-path.txt",
        "supported_gowin_version": "Gowin V1.9.12.04 x64",
        "execution_engine": "native_cpp20_tier1_sse3",
        "target_isa": "x86-64 + SSE2 + SSE3 (All x86 CPUs released after 2007)",
        "higher_tier_binaries_present": False,
        "runtime_tier_selection_present": False,
        "python_runtime_present": False,
        "python_files_count": 0,
        "pyd_modules_count": 0,
        "stripped_debug_sections": strip_stats,
        "pe_binary_count": len(pe_files),
        "external_inbox_os_dlls_only": sorted(external_os_imports),
        "non_os_external_dll_imports": 0,
        "binaries": binary_inventory,
    }
    (env_dir / "env_manifest.json").write_text(json.dumps(env_manifest, indent=2) + "\n", encoding="utf-8")

    zip_path = out_dir / "nextgowin-env.zip"
    write_deterministic_zip(env_dir, zip_path, "nextgowin-env")
    audit_zip_cleanliness(zip_path, forbid_python=True)

    return {
        "zip": str(zip_path),
        "uncompressed_bytes": sum(f.stat().st_size for f in env_dir.rglob("*") if f.is_file()),
        "zip_bytes": zip_path.stat().st_size,
        "zip_sha256": sha256_file(zip_path),
        "pe_binary_count": len(pe_files),
        "python_runtime_present": False,
        "external_os_dlls": sorted(external_os_imports),
    }


def build_source_archive(stage_root: Path, out_dir: Path, suite_root: Path) -> dict:
    src_dir = stage_root / "nextgowin-source"
    src_dir.mkdir(parents=True)

    # 1. Top-level documentation & config files
    for fn in ("README.md", "BUILD.md", "THIRD_PARTY_NOTICES.txt", ".gitignore"):
        shutil.copy2(ROOT / fn, src_dir / fn)

    # 2. Verbatim third-party license texts
    lic_dir = src_dir / "licenses"
    lic_dir.mkdir(parents=True)
    yosys_lic = suite_root / "license/LICENSE.yosys"
    if yosys_lic.is_file():
        shutil.copy2(yosys_lic, lic_dir / "LICENSE.yosys")
    abc_lic = ROOT / "third_party/abc/copyright.txt"
    if abc_lic.is_file():
        shutil.copy2(abc_lic, lic_dir / "LICENSE.abc")

    # 3. Clean Tier 1 C/C++20 source files (excluding legacy unused ownpr_native_bridge.cpp / mu_cache.hpp if unneeded)
    src_rels = [
        "src/cpu_dispatch.hpp",
        "src/nextgowin_cli.cpp",
        "src/ownpr_bridge_tiered.cpp",
        "src/syn_accel_tiered.cpp",
        "src/win32_pthread_shim.c",
        "src/native/ng_common.hpp",
        "src/native/ng_common.cpp",
        "src/native/ng_json.hpp",
        "src/native/ng_json.cpp",
        "src/native/ng_pipeline.hpp",
        "src/native/ng_frontend_bridge.cpp",
        "src/native/ng_mapper_cec.cpp",
        "src/native/ng_emitter_opt.cpp",
        "src/native/ng_ownpr_driver.cpp",
        "src/native/ng_flow_main.cpp",
        "scripts/build_native_binaries.py",
        "scripts/build_standalone_packages.py",
        "third_party/abc_sources.txt",
        "third_party/abc/copyright.txt",
        *RESOURCE_REL_FILES,
    ]
    for rel in src_rels:
        s = ROOT / rel
        d = src_dir / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)

    # 4. Example GW5AST-138C .gprj project
    ex_root = ROOT / "examples/blinky_gw5ast138"
    if ex_root.is_dir():
        for f in sorted(ex_root.rglob("*")):
            if f.is_file():
                rel = f.relative_to(ROOT)
                d = src_dir / rel
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, d)

    # 5. Clean third_party/abc/src tree (only source/header/license files; zero .obj/.lib/.pdb/.exe)
    abc_src_root = ROOT / "third_party/abc/src"
    allowed_abc_exts = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".inc", ".txt", ""}
    for f in sorted(abc_src_root.rglob("*")):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        nl = f.name.lower()
        if ext in allowed_abc_exts or "license" in nl or "copyright" in nl:
            if ext in (".obj", ".o", ".lib", ".a", ".pdb", ".exe", ".dll", ".pyc"):
                continue
            rel = f.relative_to(ROOT)
            d = src_dir / rel
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, d)

    zip_path = out_dir / "nextgowin-source.zip"
    write_deterministic_zip(src_dir, zip_path, "nextgowin-source")
    audit_zip_cleanliness(zip_path, forbid_python=False)

    file_count = sum(1 for f in src_dir.rglob("*") if f.is_file())
    uncomp_bytes = sum(f.stat().st_size for f in src_dir.rglob("*") if f.is_file())
    return {
        "zip": str(zip_path),
        "file_count": file_count,
        "uncompressed_bytes": uncomp_bytes,
        "zip_bytes": zip_path.stat().st_size,
        "zip_sha256": sha256_file(zip_path),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build NextGowin GitHub release & source archives.")
    ap.add_argument(
        "--bins-dir",
        type=Path,
        default=Path(os.environ.get("NEXTGOWIN_BINS_DIR", str(ROOT / "build/tier1_bins"))),
    )
    ap.add_argument(
        "--oss-cad-suite",
        type=Path,
        default=ROOT / "build/dependencies/oss-cad-suite",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "dist/github_release",
    )
    args = ap.parse_args()

    _ws_reserve(ROOT, 250_000_000)

    bins_dir = args.bins_dir.resolve()
    suite_root = args.oss_cad_suite.resolve()
    out_dir = args.out_dir.resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ng_pkg_stage_") as tmp_s:
        stage_root = Path(tmp_s)
        res_info = build_resource_package(stage_root, out_dir, suite_root)
        print("[PKG 1] nextgowin-resources.zip:", json.dumps(res_info, indent=2), flush=True)

        env_info = build_env_package(stage_root, out_dir, bins_dir, suite_root)
        print("[PKG 2] nextgowin-env.zip:", json.dumps(env_info, indent=2), flush=True)

        src_info = build_source_archive(stage_root, out_dir, suite_root)
        print("[PKG 3] nextgowin-source.zip:", json.dumps(src_info, indent=2), flush=True)

    # Copy top-level documentation beside the archives in out_dir and write SHA256SUMS.txt
    for doc_fn in ("README.md", "BUILD.md", "THIRD_PARTY_NOTICES.txt"):
        shutil.copy2(ROOT / doc_fn, out_dir / doc_fn)

    checksum_targets = [
        "nextgowin-env.zip",
        "nextgowin-resources.zip",
        "nextgowin-source.zip",
        "README.md",
        "BUILD.md",
        "THIRD_PARTY_NOTICES.txt",
    ]
    sum_lines = []
    for fn in checksum_targets:
        p = out_dir / fn
        sum_lines.append(f"{sha256_file(p)}  {fn}")
    (out_dir / "SHA256SUMS.txt").write_text("\n".join(sum_lines) + "\n", encoding="utf-8")
    print("[OK] Wrote SHA256SUMS.txt:\n" + "\n".join(sum_lines), flush=True)


if __name__ == "__main__":
    main()
