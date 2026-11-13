// Real-cut column multiplicity prototype. Does not replace the full ABC mapper.
#include <cuda_runtime.h>
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <vector>
#define CHECK(x) do { auto e=(x); if(e!=cudaSuccess){std::fprintf(stderr,"%s: %s\n",#x,cudaGetErrorString(e));std::exit(1);}}while(0)
struct Cut { uint32_t n,k; uint64_t truth[32]; };
struct Pattern { uint32_t nf,nb,free_count,k,fs[64],bs[64]; };
struct Task { uint32_t cut,pattern; };
static_assert(sizeof(Cut)==264,"Cut ABI");
static_assert(sizeof(Pattern)==528,"Pattern ABI");
__global__ void multiplicity(const Cut* cuts,const Pattern* patterns,const Task* tasks,uint32_t* result,int count) {
  int i=blockIdx.x,b=threadIdx.x;
  if(i>=count) return;
  __shared__ uint64_t columns[64]; __shared__ unsigned first[64];
  const Task t=tasks[i];const Pattern& p=patterns[t.pattern];const Cut& c=cuts[t.cut];
  uint64_t col=0;
  if(b<p.nb) for(unsigned f=0;f<p.nf;f++) { unsigned bit=p.bs[b]|p.fs[f]; col|=((c.truth[bit>>6]>>(bit&63))&1ull)<<f; }
  columns[b]=col;__syncthreads();
  unsigned unique=b<p.nb;
  if(unique) for(int j=0;j<b;j++) if(columns[j]==col){unique=0;break;}
  first[b]=unique;__syncthreads();
  for(int stride=32;stride;stride>>=1) {if(b<stride)first[b]+=first[b+stride];__syncthreads();}
  if(b==0) result[i]=first[0];
}
using Clock=std::chrono::steady_clock;
double elapsed(Clock::time_point start){return std::chrono::duration<double,std::milli>(Clock::now()-start).count();}
double median(std::vector<double> v){if(v.empty())return 0;std::sort(v.begin(),v.end());return v[v.size()/2];}
template<class T> void load(std::ifstream& in,std::vector<T>& out,size_t n) {
  if(n>10000000) throw "input size limit";out.resize(n);in.read(reinterpret_cast<char*>(out.data()),sizeof(T)*n);if(!in)throw "truncated input";
}
int main(int argc,char** argv) {
  if(argc<2 || argc>4){std::fprintf(stderr,"usage: acd_partition_bench partitions.bin [mu_cache.bin [--produce]]\n");return 2;}
  bool produce=argc==4 && std::string(argv[3])=="--produce";
  if(argc==4 && !produce)return 2;
  std::ifstream in(argv[1],std::ios::binary);char magic[8];uint32_t sizes[3];
  in.read(magic,8);in.read(reinterpret_cast<char*>(sizes),12);
  if(!in||std::string(magic,8)!="ACDPART1"||!sizes[2])return 3;
  std::vector<Cut> cuts;std::vector<Pattern> patterns;std::vector<Task> tasks;
  try{load(in,cuts,sizes[0]);load(in,patterns,sizes[1]);load(in,tasks,sizes[2]);}catch(...){return 4;}
  for(auto t:tasks)if(t.cut>=cuts.size()||t.pattern>=patterns.size())return 5;
  for(auto p:patterns)if(!p.nf||p.nf>64||!p.nb||p.nb>64)return 6;
  size_t n=tasks.size();std::vector<uint32_t> cpu(n),gpu(n);
  auto cold=Clock::now();CHECK(cudaSetDevice(0));CHECK(cudaFree(nullptr));double init=elapsed(cold);
  Cut* dc;Pattern* dp;Task* dt;uint32_t* dr;
  CHECK(cudaMalloc(&dc,cuts.size()*sizeof(Cut)));CHECK(cudaMalloc(&dp,patterns.size()*sizeof(Pattern)));
  CHECK(cudaMalloc(&dt,tasks.size()*sizeof(Task)));CHECK(cudaMalloc(&dr,n*4));
  auto upload=[&](){CHECK(cudaMemcpy(dc,cuts.data(),cuts.size()*sizeof(Cut),cudaMemcpyHostToDevice));
    CHECK(cudaMemcpy(dp,patterns.data(),patterns.size()*sizeof(Pattern),cudaMemcpyHostToDevice));
    CHECK(cudaMemcpy(dt,tasks.data(),tasks.size()*sizeof(Task),cudaMemcpyHostToDevice));};
  upload();auto first=Clock::now();multiplicity<<<int(n),64>>>(dc,dp,dt,dr,int(n));CHECK(cudaGetLastError());CHECK(cudaDeviceSynchronize());double first_ms=elapsed(first);
  std::vector<double> ctimes,gtimes,transfers;
  if(produce){CHECK(cudaMemcpy(gpu.data(),dr,n*4,cudaMemcpyDeviceToHost));gtimes.push_back(first_ms);}
  for(int rep=0;rep<(produce?0:5);rep++) {
    auto start=Clock::now();
    for(size_t i=0;i<n;i++) {
      auto t=tasks[i];const auto& c=cuts[t.cut];const auto& p=patterns[t.pattern];uint64_t columns[64];
      for(unsigned b=0;b<p.nb;b++) {uint64_t col=0;for(unsigned f=0;f<p.nf;f++){unsigned bit=p.bs[b]|p.fs[f];col|=((c.truth[bit>>6]>>(bit&63))&1ull)<<f;}columns[b]=col;}
      std::sort(columns,columns+p.nb);cpu[i]=unsigned(std::unique(columns,columns+p.nb)-columns);
    }
    ctimes.push_back(elapsed(start));start=Clock::now();
    multiplicity<<<int(n),64>>>(dc,dp,dt,dr,int(n));CHECK(cudaGetLastError());CHECK(cudaDeviceSynchronize());gtimes.push_back(elapsed(start));
    CHECK(cudaMemcpy(gpu.data(),dr,n*4,cudaMemcpyDeviceToHost));
    if(cpu!=gpu){std::fprintf(stderr,"resident CPU/GPU mismatch\n");return 7;}
    start=Clock::now();upload();multiplicity<<<int(n),64>>>(dc,dp,dt,dr,int(n));CHECK(cudaGetLastError());
    CHECK(cudaMemcpy(gpu.data(),dr,n*4,cudaMemcpyDeviceToHost));transfers.push_back(elapsed(start));
    if(cpu!=gpu){std::fprintf(stderr,"transfer CPU/GPU mismatch\n");return 8;}
  }
  unsigned feasible=0;for(size_t i=0;i<n;i++){const auto&p=patterns[tasks[i].pattern];feasible+=gpu[i]<=(1u<<(p.k-p.free_count));}
  if(argc>=3) {
    std::vector<std::vector<uint8_t>> tables(cuts.size());
    for(size_t i=0;i<cuts.size();i++) {
      if(cuts[i].n>11 || cuts[i].k<3 || cuts[i].k>6)return 9;
      tables[i].assign(1u<<cuts[i].n,255);
    }
    for(size_t i=0;i<n;i++) {const auto&p=patterns[tasks[i].pattern];unsigned mask=p.fs[p.nf-1];
      if(mask>=tables[tasks[i].cut].size() || gpu[i]>64)return 10;
      tables[tasks[i].cut][mask]=static_cast<uint8_t>(gpu[i]);}
    std::ofstream cache(argv[2],std::ios::binary);cache.write("ACDMU001",8);uint32_t count=uint32_t(cuts.size());
    cache.write(reinterpret_cast<const char*>(&count),4);
    for(size_t i=0;i<cuts.size();i++) {uint32_t words=1u<<(cuts[i].n>6?cuts[i].n-6:0);
      uint32_t header[3]={cuts[i].n,cuts[i].k,words};cache.write(reinterpret_cast<char*>(header),12);
      cache.write(reinterpret_cast<const char*>(cuts[i].truth),words*8);
      cache.write(reinterpret_cast<const char*>(tables[i].data()),tables[i].size());}
    if(!cache)return 11;
  }
  std::printf("{\"scope\":\"Real cut partition multiplicity; not full ACD/mapper\",\"tasks\":%zu,\"cuts\":%zu,\"cpu_validated\":%s,\"single_launch_producer\":%s,\"feasible_partitions\":%u,\"context_init_ms\":%.6f,\"cpu_median_ms\":%.6f,\"resident_gpu_median_ms\":%.6f,\"transfer_gpu_median_ms\":%.6f}\n",n,cuts.size(),produce?"false":"true",produce?"true":"false",feasible,init,median(ctimes),median(gtimes),median(transfers));
  CHECK(cudaFree(dc));CHECK(cudaFree(dp));CHECK(cudaFree(dt));CHECK(cudaFree(dr));return 0;
}
