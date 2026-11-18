"""Build-time compiler driver for all Python-free NextGowin C/C++20 native binaries.

Compiles Tier 1 baseline ONLY (x86-64 + SSE2/SSE3, all x86 CPUs released after 2007):
  - `nextgowin.exe`        (`/O2 /MT /d2Qvec-sse2only` native launcher, `gowin-path.txt` & `.gprj` drag-and-drop)
  - `bin/nextgowin_core.dll` (6-stage C++20 synthesis, mapping, CEC, custom P&R, STA & frame-CRC engine)
  - `bin/syn_accel.dll`    (SSE2/SSE3 LUT4 permute, comment stripper, BLIF writer, 21,872-frame CRC-16)
  - `bin/ownpr_bridge.dll` (SSE2/SSE3 SimdFlatMap64, HotNode placement & PathFinder timing-driven router)
  - `bin/abc.exe`          (64-bit baseline ABC with SWAR popcnt & Win32 SRWLOCK shim)

All binaries are compiled with `/d1trimfile` and linked with `/DEBUG:NONE /BREPRO` so zero local build
machine paths or timestamps are embedded in the released PE64 binaries.
Python is used strictly as a build-time tool; none of the compiled outputs depend on Python.
"""
import argparse
from collections import defaultdict
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]

NATIVE_CORE_SRCS = [
    ROOT / "src/native/ng_common.cpp",
    ROOT / "src/native/ng_json.cpp",
    ROOT / "src/native/ng_frontend_bridge.cpp",
    ROOT / "src/native/ng_mapper_cec.cpp",
    ROOT / "src/native/ng_emitter_opt.cpp",
    ROOT / "src/native/ng_ownpr_driver.cpp",
    ROOT / "src/native/ng_flow_main.cpp",
]

ALLOWED_CLI_OS_DLLS = {"KERNEL32.DLL", "USER32.DLL"}
ALLOWED_ABC_OS_DLLS = {
    "KERNEL32.DLL", "USER32.DLL", "ADVAPI32.DLL", "SHELL32.DLL", "SHLWAPI.DLL", "WS2_32.DLL"
}


def find_msvc_and_sdk() -> tuple[Path, Path, str]:
    vs_roots = [
        Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC"),
        Path(r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC"),
        Path(r"C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Tools\MSVC"),
        Path(r"C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Tools\MSVC"),
    ]
    msvc = None
    for r in vs_roots:
        if r.is_dir():
            vers = sorted(d for d in r.iterdir() if d.is_dir())
            if vers:
                msvc = vers[-1]
                break
    if msvc is None:
        raise RuntimeError("MSVC x64 toolchain not found under Visual Studio 2022")
    kits = Path(r"C:\Program Files (x86)\Windows Kits\10")
    sdk_ver = sorted(d.name for d in (kits / "Include").iterdir() if d.is_dir() and d.name.startswith("10."))[-1]
    return msvc, kits, sdk_ver


def make_msvc_env(msvc: Path, kits: Path, sdk_ver: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{msvc / 'bin/Hostx64/x64'};{kits / 'bin' / sdk_ver / 'x64'};" + env.get("PATH", "")
    env["INCLUDE"] = ";".join([
        str(ROOT / "src/native"),
        str(msvc / "include"),
        str(kits / "Include" / sdk_ver / "ucrt"),
        str(kits / "Include" / sdk_ver / "shared"),
        str(kits / "Include" / sdk_ver / "um"),
    ])
    env["LIB"] = ";".join([
        str(msvc / "lib/x64"),
        str(kits / "Lib" / sdk_ver / "ucrt/x64"),
        str(kits / "Lib" / sdk_ver / "um/x64"),
    ])
    return env


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
        raise RuntimeError(f"Unmapped RVA {rva:#x}")

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


def load_or_generate_abc_source_list() -> list[Path]:
    abc_src = ROOT / "third_party/abc/src"
    manifest_txt = ROOT / "third_party/abc_sources.txt"
    vcx_path = ROOT / "build/abc/abclib.vcxproj"

    if not manifest_txt.is_file() and vcx_path.is_file():
        vcx = vcx_path.read_text(encoding="utf-8")
        raw = re.findall(r'<ClCompile Include="([^"]+)"', vcx)
        rels, seen_r = [], set()
        for r in raw:
            idx = r.lower().find("third_party\\abc\\src\\")
            if idx < 0:
                idx = r.lower().find("third_party/abc/src/")
            rel = r[idx + len("third_party\\abc\\src\\"):].replace("\\", "/")
            if rel not in seen_r:
                seen_r.add(rel)
                rels.append(rel)
        if "base/main/main.c" not in seen_r:
            rels.append("base/main/main.c")
        manifest_txt.write_text("\n".join(rels) + "\n", encoding="utf-8")

    if not manifest_txt.is_file():
        raise RuntimeError(f"Missing ABC source list manifest: {manifest_txt}")

    srcs: list[Path] = []
    seen: set[Path] = set()
    for ln in manifest_txt.read_text(encoding="utf-8").splitlines():
        rel = ln.strip()
        if not rel or rel.startswith("#"):
            continue
        p = (abc_src / rel).resolve()
        if p not in seen:
            seen.add(p)
            srcs.append(p)
    shim_p = (ROOT / "src/win32_pthread_shim.c").resolve()
    if shim_p not in seen:
        srcs.append(shim_p)
    return srcs


def build_single_dll(cl_exe: str, env: dict[str, str], src: Path, out_dll: Path, obj_dir: Path, trim_flag: str) -> None:
    obj_dir.mkdir(parents=True, exist_ok=True)
    out_dll.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    p = subprocess.run([
        cl_exe, "/nologo", "/O2", "/MT", "/EHsc", "/std:c++20", "/fp:precise", "/utf-8",
        "/d2Qvec-sse2only", "/DNG_TIER=10", trim_flag,
        str(src), f"/Fo{obj_dir / (out_dll.stem + '.obj')}",
        "/LD", f"/Fe{out_dll}",
        "/link", "/INCREMENTAL:NO", "/OPT:REF", "/OPT:ICF", "/DEBUG:NONE", "/BREPRO",
        f"/IMPLIB:{obj_dir / (out_dll.stem + '.lib')}",
    ], env=env, cwd=str(obj_dir), capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Failed compiling {src.name}:\n{p.stdout}\n{p.stderr}")
    imps = pe_imports(out_dll)
    assert [x.upper() for x in imps] == ["KERNEL32.DLL"], f"Unexpected imports in {out_dll}: {imps}"
    print(f"[OK] bin/{out_dll.name} ({out_dll.stat().st_size:,} B, {time.perf_counter()-t0:.2f}s)", flush=True)


def build_abc_tier1(cl_exe: str, link_exe: str, env: dict[str, str], out_exe: Path, work_dir: Path, trim_flag: str, clean: bool) -> None:
    abc_src = ROOT / "third_party/abc/src"
    srcs = load_or_generate_abc_source_list()

    def part_stems(ps: list[Path]) -> list[list[Path]]:
        by_s: dict[str, list[Path]] = defaultdict(list)
        for x in ps:
            by_s[x.stem.lower()].append(x)
        mx = max((len(v) for v in by_s.values()), default=0)
        return [[v[i] for v in by_s.values() if i < len(v)] for i in range(mx)]

    c_batches = part_stems([p for p in srcs if p.suffix.lower() == ".c"])
    cpp_batches = part_stems([p for p in srcs if p.suffix.lower() in (".cpp", ".cc", ".cxx")])
    tier1_hdr = work_dir / "tier1_no_popcnt.h"
    tier1_hdr.write_text(
        "#pragma once\n#include <intrin.h>\n"
        "static __inline unsigned int ng_swar_popcnt32(unsigned int x) {\n"
        "    x = x - ((x >> 1) & 0x55555555U);\n"
        "    x = (x & 0x33333333U) + ((x >> 2) & 0x33333333U);\n"
        "    return (((x + (x >> 4)) & 0x0F0F0F0FU) * 0x01010101U) >> 24;\n"
        "}\n"
        "#define __popcnt16(x) ((unsigned short)ng_swar_popcnt32((unsigned short)(x)))\n"
        "#define __popcnt(x) ng_swar_popcnt32((unsigned int)(x))\n"
        "#ifdef NG_ESLIM_NO_POPCNT64\n"
        "static __inline unsigned __int64 ng_swar_popcnt64(unsigned __int64 x) {\n"
        "    return (unsigned __int64)ng_swar_popcnt32((unsigned int)x) +\n"
        "           (unsigned __int64)ng_swar_popcnt32((unsigned int)(x >> 32));\n"
        "}\n"
        "#define __popcnt64(x) ng_swar_popcnt64((unsigned __int64)(x))\n"
        "#endif\n",
        encoding="utf-8",
    )

    t0 = time.perf_counter()
    vobj = work_dir / "abc_objs_tier1_clean"
    if clean and vobj.exists():
        shutil.rmtree(vobj)
    out_exe.parent.mkdir(parents=True, exist_ok=True)
    c_flags = [
        "/nologo", "/c", "/O2", "/MT", "/MP", "/Gy", "/fp:precise", "/utf-8", "/d2Qvec-sse2only",
        trim_flag, f"/FI{tier1_hdr}",
        "/wd4146", "/wd4334", "/wd4996", "/wd4703", "/wd4244", "/wd4267", "/wd4018", "/wd4101", "/wd4311", "/wd4312", "/wd4302", "/wd4805",
        f"/I{abc_src}", "/DWIN32", "/DWINDOWS", "/DWIN32_NO_DLL", "/DPTW32_STATIC_LIB",
        "/DHAVE_STRUCT_TIMESPEC", "/DABC_USE_STDINT_H", "/DABC_USE_CUDD", "/DNDEBUG", "/D_CONSOLE",
        "/D_CRT_SECURE_NO_DEPRECATE", "/D_CRT_NONSTDC_NO_WARNINGS", "/D_WINSOCKAPI_", "/DNG_TIER=10",
    ]
    all_objs: list[Path] = []
    for kind, batches, ex in [("c", c_batches, []), ("cpp", cpp_batches, ["/EHsc", "/std:c++17", "/Zc:strictStrings-"])]:
        for bi, batch in enumerate(batches):
            bdir = vobj / f"{kind}_{bi}"
            bdir.mkdir(parents=True, exist_ok=True)
            todo = [p for p in batch if not (bdir / f"{p.stem}.obj").is_file()]
            todo_std = [p for p in todo if "eslim" not in p.as_posix().lower()]
            todo_esl = [p for p in todo if "eslim" in p.as_posix().lower()]
            for sub_idx, (sub_todo, sub_ex) in enumerate([(todo_std, []), (todo_esl, ["/DNG_ESLIM_NO_POPCNT64"])]):
                if sub_todo:
                    rsp = vobj / f"compile_{kind}_{bi}_{sub_idx}.rsp"
                    rsp.write_text("\n".join([*c_flags, *ex, *sub_ex, f"/Fo{bdir}\\"] + [f'"{p}"' for p in sub_todo]) + "\n", encoding="utf-8")
                    cp = subprocess.run([cl_exe, f"@{rsp}"], env=env, cwd=str(bdir), capture_output=True, text=True)
                    if cp.returncode != 0:
                        raise RuntimeError(f"ABC compile failed:\n{cp.stdout[-3000:]}\n{cp.stderr[-3000:]}")
            all_objs.extend(bdir / f"{s.stem}.obj" for s in batch)
    lrsp = vobj / "link.rsp"
    lrsp.write_text("\n".join([
        "/NOLOGO", "/INCREMENTAL:NO", "/OPT:REF", "/OPT:ICF", "/DEBUG:NONE", "/BREPRO",
        "/SUBSYSTEM:CONSOLE", "/STACK:16777216",
        f'/OUT:"{out_exe}"', "kernel32.lib", "user32.lib", "advapi32.lib", "shell32.lib", "shlwapi.lib", "ws2_32.lib",
        *[f'"{o}"' for o in all_objs],
    ]) + "\n", encoding="utf-8")
    lp = subprocess.run([link_exe, f"@{lrsp}"], env=env, cwd=str(vobj), capture_output=True, text=True)
    if lp.returncode != 0:
        raise RuntimeError(f"ABC link failed:\n{lp.stdout[-3000:]}\n{lp.stderr[-3000:]}")
    bad = [d for d in pe_imports(out_exe) if d.upper() not in ALLOWED_ABC_OS_DLLS]
    assert not bad, f"Disallowed imports in {out_exe}: {bad}"
    print(f"[OK] bin/abc.exe ({out_exe.stat().st_size:,} B, {time.perf_counter()-t0:.2f}s)", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Compile NextGowin Tier 1 (x86-64 + SSE2/SSE3) native binaries.")
    ap.add_argument("--out-dir", type=Path, default=Path(os.environ.get("NEXTGOWIN_BINS_DIR", str(ROOT / "build/tier1_bins"))))
    ap.add_argument("--work-dir", type=Path, default=Path(tempfile.gettempdir()) / "nextgowin_tier1_objs")
    ap.add_argument("--skip-abc", action="store_true")
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args()

    msvc, kits, sdk_ver = find_msvc_and_sdk()
    cl_exe = str(msvc / "bin/Hostx64/x64/cl.exe")
    link_exe = str(msvc / "bin/Hostx64/x64/link.exe")
    env = make_msvc_env(msvc, kits, sdk_ver)

    out_base = args.out_dir.resolve()
    work_dir = args.work_dir.resolve()
    if args.clean and out_base.exists():
        shutil.rmtree(out_base)
    out_base.mkdir(parents=True, exist_ok=True)
    bin_dir = out_base / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    trim_flag = f"/d1trimfile:{str(ROOT.resolve())}\\"

    # 1. Native SSE2/SSE3 launcher nextgowin.exe
    cli_obj = work_dir / "cli"
    cli_obj.mkdir(parents=True, exist_ok=True)
    cli_out = out_base / "nextgowin.exe"
    p_cli = subprocess.run([
        cl_exe, "/nologo", "/O2", "/MT", "/EHsc", "/std:c++20", "/utf-8", "/d2Qvec-sse2only", trim_flag,
        str(ROOT / "src/nextgowin_cli.cpp"), f"/Fo{cli_obj / 'nextgowin_cli.obj'}", f"/Fe{cli_out}",
        "/link", "/INCREMENTAL:NO", "/OPT:REF", "/OPT:ICF", "/DEBUG:NONE", "/BREPRO", "kernel32.lib", "user32.lib",
    ], env=env, cwd=str(cli_obj), capture_output=True, text=True)
    if p_cli.returncode != 0:
        raise RuntimeError(f"Failed compiling nextgowin.exe:\n{p_cli.stdout}\n{p_cli.stderr}")
    cli_imps = {x.upper() for x in pe_imports(cli_out)}
    assert cli_imps.issubset(ALLOWED_CLI_OS_DLLS), f"Unexpected imports in nextgowin.exe: {cli_imps}"
    print(f"[OK] nextgowin.exe ({cli_out.stat().st_size:,} B, imports={sorted(cli_imps)})", flush=True)

    # 2. Tier 1 nextgowin_core.dll, syn_accel.dll, ownpr_bridge.dll
    cobj = work_dir / "core_tier1"
    if args.clean and cobj.exists():
        shutil.rmtree(cobj)
    cobj.mkdir(parents=True, exist_ok=True)
    hdr_mtime = max(h.stat().st_mtime for h in (ROOT / "src/native").glob("*.hpp"))
    todo_srcs = []
    objs = []
    for s in NATIVE_CORE_SRCS:
        ob = cobj / f"{s.stem}.obj"
        objs.append(str(ob))
        if args.clean or not ob.is_file() or ob.stat().st_mtime < max(s.stat().st_mtime, hdr_mtime):
            todo_srcs.append(str(s))
    if todo_srcs:
        t0 = time.perf_counter()
        pc = subprocess.run([
            cl_exe, "/nologo", "/MP", "/c", "/O2", "/MT", "/EHsc", "/std:c++20", "/fp:precise",
            "/bigobj", "/utf-8", "/d2Qvec-sse2only", "/DNG_TIER=10", trim_flag,
            *todo_srcs, f"/Fo{cobj}\\",
        ], env=env, cwd=str(cobj), capture_output=True, text=True)
        if pc.returncode != 0:
            raise RuntimeError(f"Failed compiling nextgowin_core:\n{pc.stdout}\n{pc.stderr}")
        print(f"[OK] compiled {len(todo_srcs)} core translation units in {time.perf_counter()-t0:.2f}s", flush=True)
    core_dll = bin_dir / "nextgowin_core.dll"
    pl = subprocess.run([
        link_exe, "/NOLOGO", "/DLL", "/INCREMENTAL:NO", "/OPT:REF", "/OPT:ICF", "/DEBUG:NONE", "/BREPRO",
        f"/OUT:{core_dll}", f"/IMPLIB:{cobj / 'nextgowin_core.lib'}", *objs,
    ], env=env, cwd=str(cobj), capture_output=True, text=True)
    if pl.returncode != 0:
        raise RuntimeError(f"Failed linking nextgowin_core:\n{pl.stdout}\n{pl.stderr}")
    assert [x.upper() for x in pe_imports(core_dll)] == ["KERNEL32.DLL"]
    print(f"[OK] bin/nextgowin_core.dll ({core_dll.stat().st_size:,} B)", flush=True)

    build_single_dll(cl_exe, env, ROOT / "src/syn_accel_tiered.cpp", bin_dir / "syn_accel.dll", work_dir / "syn_tier1", trim_flag)
    build_single_dll(cl_exe, env, ROOT / "src/ownpr_bridge_tiered.cpp", bin_dir / "ownpr_bridge.dll", work_dir / "brg_tier1", trim_flag)

    # 3. Tier 1 64-bit abc.exe
    if not args.skip_abc:
        build_abc_tier1(cl_exe, link_exe, env, bin_dir / "abc.exe", work_dir, trim_flag, args.clean)

    # 4. Disassembly ISA audit across compiled application .obj files (zero AVX/AVX2/AVX-512/GFNI/POPCNT/BMI2)
    dumpbin_exe = str(msvc / "bin/Hostx64/x64/dumpbin.exe")
    bad_isa_re = re.compile(r"\b(ymm\d+|zmm\d+|vmov[a-z0-9]+|vpadd[a-z0-9]+|vpxor[a-z0-9]*|vpand[a-z0-9]*|vgf2p8[a-z0-9]+|popcnt|lzcnt|bzhi|pdep|pext)\b")
    audit_objs = [
        cli_obj / "nextgowin_cli.obj",
        work_dir / "syn_tier1/syn_accel_tiered.obj",
        work_dir / "brg_tier1/ownpr_bridge_tiered.obj",
        *[Path(o) for o in objs],
    ]
    for ob_p in audit_objs:
        if not ob_p.is_file():
            continue
        dis = subprocess.run([dumpbin_exe, "/disasm:nobytes", str(ob_p)], env=env, capture_output=True, text=True)
        if dis.returncode != 0:
            raise RuntimeError(f"dumpbin ISA audit failed on {ob_p}")
        hits = sorted(set(m.group(1) for m in bad_isa_re.finditer(dis.stdout)))
        if hits:
            raise RuntimeError(f"Tier-1 ISA violation in {ob_p.name}: {hits}")
    print(f"[OK] Tier-1 ISA audit passed across {len(audit_objs)} application .obj units (zero AVX/AVX2/AVX-512/GFNI/POPCNT/BMI2)", flush=True)


if __name__ == "__main__":
    main()
