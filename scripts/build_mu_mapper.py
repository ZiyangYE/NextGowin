"""Compile generated ABC ACD hooks against a GPU-produced exact lookup table."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    source=ROOT/'third_party/abc/src/map/if/acd'
    out=ROOT/'build/abc_mu';out.mkdir(parents=True,exist_ok=True)
    original=(source/'ac_decomposition.hpp').read_text()
    header=original.replace('best_cost = fn( tt );',
        'best_cost = nextgowin_mu::column(free_set_size, permutations.data(), nullptr, [&](){ return fn(tt); });')
    header=header.replace('uint32_t cost = fn( tt );',
        'uint32_t cost = nextgowin_mu::column(free_set_size, permutations.data(), pComb, [&](){ return fn(tt); });')
    header=header.replace('uint32_t cost = column_multiplicity2( tt, free_set_size );',
        'uint32_t cost = nextgowin_mu::column(free_set_size, permutations.data(), pComb, [&](){ return column_multiplicity2(tt,free_set_size); }, true);')
    if header.count('nextgowin_mu::column')!=3:
        raise RuntimeError('Upstream hook locations changed')
    (out/'ac_decomposition_mu.hpp').write_text(header)
    wrapper=(source/'ac_wrapper.cpp').read_text()
    wrapper=wrapper.replace('#include "ac_decomposition.hpp"',
        '#include "mu_cache.hpp"\n#include "ac_decomposition_mu.hpp"')
    for name in ['acd_evaluate','acd_decompose']:
        begin=wrapper.index('int '+name+'(');pos=wrapper.index('{',begin)+1
        wrapper=wrapper[:pos]+'\n  nextgowin_mu::Context context(pTruth,nVars,lutSize);\n'+wrapper[pos:]
    cpp=out/'mu_wrapper.cpp';cpp.write_text(wrapper)
    project=(ROOT/'build/abc/abcexe.vcxproj').read_text(encoding='utf-8-sig')
    project=project.replace('Include="abclib.vcxproj"',f'Include="{ROOT / "build/abc/abclib.vcxproj"}"')
    project=project.replace('<AdditionalIncludeDirectories>',f'<AdditionalIncludeDirectories>{source};{ROOT/"src"};')
    project=project.replace('<ClCompile Include=',f'<ClCompile Include="{cpp}" />\n    <ClCompile Include=',1)
    project_file=out/'abc_mu.vcxproj';project_file.write_text(project)
    msbuild='C:/Program Files/Microsoft Visual Studio/2022/Community/MSBuild/Current/Bin/MSBuild.exe'
    with (out/'build.log').open('w') as log:
        cp=subprocess.run([msbuild,str(project_file),'/m:2','/nologo','/v:minimal',
            '/p:Configuration=Release','/p:Platform=Win32'],stdout=log,stderr=subprocess.STDOUT)
    if cp.returncode:
        raise RuntimeError('MU mapper build failed; see build log')
    (out/'_TEST/pthreadVC2.dll').write_bytes((ROOT/'third_party/abc/lib/x86/pthreadVC2.dll').read_bytes())
    files=[source/'ac_decomposition.hpp',source/'ac_wrapper.cpp',ROOT/'src/mu_cache.hpp',cpp,out/'ac_decomposition_mu.hpp']
    (out/'source_manifest.json').write_text(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},indent=2)+'\n')
    print(out/'_TEST/abc.exe')


if __name__=='__main__':
    main()
