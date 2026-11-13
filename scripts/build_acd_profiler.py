"""Build an instrumented ABC executable while reusing the verified static lib.

Only a generated copy of upstream ac_wrapper.cpp is changed. The ordinary
ABC executable and upstream checkout remain unchanged.
"""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

TRACE=r'''
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <string>
namespace nextgowin_trace {
struct Stats {
  std::ofstream corpus;
  std::string path;
  uint64_t calls=0, saved=0, feasible=0, nanoseconds=0;
  Stats() { const char* p=std::getenv("NEXTGOWIN_ACD_CORPUS");
    if(p && *p) { path=p; corpus.open(path,std::ios::binary); corpus.write("ACDCUT01",8); } }
  ~Stats() { if(path.empty()) return; std::ofstream out(path+".json");
    out << "{\"calls\":" << calls << ",\"saved\":" << saved
        << ",\"feasible\":" << feasible << ",\"evaluation_nanoseconds\":" << nanoseconds << "}\n"; }
};
static Stats stats;
struct Scope {
  unsigned n,k,input,flag,*profile,*cost;
  int* status;
  uint64_t truth[32];
  std::chrono::steady_clock::time_point start;
  Scope(word* tt,unsigned vars,int lut,unsigned* p,unsigned* c,int f,int* s):
    n(vars),k(lut),input(*p),flag(f),profile(p),cost(c),status(s) {
    unsigned words=1u << (n>6?n-6:0);
    for(unsigned i=0;i<words && i<32;i++) truth[i]=tt[i];
    start=std::chrono::steady_clock::now();
  }
  ~Scope() {
    auto elapsed=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-start).count();
    stats.calls++; stats.feasible+=(*status>=0); stats.nanoseconds+=elapsed;
    if(!stats.corpus || stats.saved>=20000 || n>11) return;
    uint32_t header[7]={n,k,input,flag,*profile,*status>=0?*cost:0u,static_cast<uint32_t>(*status)};
    stats.corpus.write(reinterpret_cast<char*>(header),sizeof(header));
    stats.corpus.write(reinterpret_cast<char*>(truth),8*(1u << (n>6?n-6:0)));
    stats.saved++;
  }
};
}
'''


def main():
    source=ROOT/'third_party/abc/src/map/if/acd/ac_wrapper.cpp'
    out=ROOT/'build/abc_profile';out.mkdir(parents=True,exist_ok=True)
    original=source.read_text()
    text=original.replace('ABC_NAMESPACE_IMPL_START',TRACE+'\nABC_NAMESPACE_IMPL_START',1)
    begin=text.index('int acd_evaluate(');end=text.index('\nint acd_decompose(',begin)
    function=text[begin:end]
    pos=function.index('{')+1
    function=function[:pos]+'\n  int nextgowin_status=-999;\n  nextgowin_trace::Scope trace(pTruth,nVars,lutSize,pdelay,cost,try_no_late_arrival,&nextgowin_status);\n'+function[pos:]
    function=function.replace('int val = acd.run( pTruth, *pdelay );','int val = nextgowin_status = acd.run( pTruth, *pdelay );',1)
    text=text[:begin]+function+text[end:]
    cpp=out/'profile_wrapper.cpp';cpp.write_text(text)
    project=(ROOT/'build/abc/abcexe.vcxproj').read_text(encoding='utf-8-sig')
    project=project.replace('Include="abclib.vcxproj"',f'Include="{ROOT / "build/abc/abclib.vcxproj"}"')
    include=ROOT/'third_party/abc/src/map/if/acd'
    project=project.replace('<AdditionalIncludeDirectories>',f'<AdditionalIncludeDirectories>{include};')
    project=project.replace('<ClCompile Include=',f'<ClCompile Include="{cpp}" />\n    <ClCompile Include=',1)
    (out/'abc_profile.vcxproj').write_text(project)
    msbuild='C:/Program Files/Microsoft Visual Studio/2022/Community/MSBuild/Current/Bin/MSBuild.exe'
    with (out/'build.log').open('w') as log:
        cp=subprocess.run([msbuild,str(out/'abc_profile.vcxproj'),'/m:2','/nologo','/v:minimal',
            '/p:Configuration=Release','/p:Platform=Win32'],stdout=log,stderr=subprocess.STDOUT)
    if cp.returncode:
        raise RuntimeError('Profiler build failed; see build/abc_profile/build.log')
    target=out/'_TEST';target.mkdir(exist_ok=True)
    (target/'pthreadVC2.dll').write_bytes((ROOT/'third_party/abc/lib/x86/pthreadVC2.dll').read_bytes())
    (out/'source_manifest.json').write_text(json.dumps(dict(upstream_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        generated_source_sha256=hashlib.sha256(cpp.read_bytes()).hexdigest(),scope='ACD timing and bounded real-cut capture; not performance baseline'),indent=2)+'\n')
    print(target/'abc.exe')


if __name__=='__main__':
    main()
