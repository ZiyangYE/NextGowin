// Candidate scoring feasibility benchmark, not a complete technology mapper.
#include <cuda_runtime.h>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <vector>
#define CHECK(expr) do { cudaError_t e=(expr); if(e!=cudaSuccess) { \
  std::fprintf(stderr,"%s: %s\n",#expr,cudaGetErrorString(e)); std::exit(1); }} while(0)
__global__ void score(const int* a,const int* p,const int* req,int* delay,int* feasible,int n) {
  int i=blockIdx.x*blockDim.x+threadIdx.x;
  if(i<n) { int v=a[4*i]+p[4*i];
    for(int j=1;j<4;j++) v=max(v,a[4*i+j]+p[4*i+j]);
    delay[i]=v; feasible[i]=(v<=req[i]); }
}
using Clock=std::chrono::steady_clock;
double ms(Clock::time_point t) { return std::chrono::duration<double,std::milli>(Clock::now()-t).count(); }
double median(std::vector<double> v) { std::sort(v.begin(),v.end()); return v[v.size()/2]; }
int main(int argc,char** argv) {
  if(argc!=1 && argc!=5) { std::fprintf(stderr,"usage: cut_bench [A_ps B_ps C_ps D_ps]\n"); return 3; }
  int pin[4]={0,0,0,0};
  if(argc==5) for(int j=0;j<4;j++) { pin[j]=std::atoi(argv[j+1]); if(pin[j]<=0) return 3; }
  auto cold=Clock::now(); CHECK(cudaSetDevice(0)); CHECK(cudaFree(nullptr));
  cudaDeviceProp prop; CHECK(cudaGetDeviceProperties(&prop,0));
  double init=ms(cold);
  std::printf("{\"scope\":\"Synthetic four-pin scoring only; not full mapping\",\"gpu\":\"%s\",\"context_init_ms\":%.6f,\"pin_delays_ps\":[%d,%d,%d,%d],\"random_pin_delays\":%s,\"runs\":[",prop.name,init,pin[0],pin[1],pin[2],pin[3],argc==5?"false":"true");
  bool first=true;
  for(int n: {1024,65536,1048576}) {
    size_t sz=n*sizeof(int);
    std::vector<int>a(n*4),p(n*4),req(n),cd(n),cf(n),gd(n),gf(n);
    unsigned state=20261101;
    auto next=[&](){state=1664525u*state+1013904223u; return state;};
    for(int i=0;i<4*n;i++) { a[i]=next()%10000; p[i]=argc==5?pin[i%4]:200+next()%600; }
    for(int i=0;i<n;i++) req[i]=1000+next()%10000;
    int *da,*dp,*dr,*dd,*df;
    auto alloc=Clock::now();
    CHECK(cudaMalloc(&da,sz*4)); CHECK(cudaMalloc(&dp,sz*4)); CHECK(cudaMalloc(&dr,sz));
    CHECK(cudaMalloc(&dd,sz)); CHECK(cudaMalloc(&df,sz)); double alloc_ms=ms(alloc);
    auto upload=[&](){ CHECK(cudaMemcpy(da,a.data(),sz*4,cudaMemcpyHostToDevice));
      CHECK(cudaMemcpy(dp,p.data(),sz*4,cudaMemcpyHostToDevice)); CHECK(cudaMemcpy(dr,req.data(),sz,cudaMemcpyHostToDevice)); };
    upload(); score<<<(n+255)/256,256>>>(da,dp,dr,dd,df,n); CHECK(cudaGetLastError()); CHECK(cudaDeviceSynchronize());
    std::vector<double> cpu,gpu,transfer;
    for(int rep=0;rep<5;rep++) {
      auto start=Clock::now();
      for(int i=0;i<n;i++) { int v=a[4*i]+p[4*i];
        for(int j=1;j<4;j++) v=std::max(v,a[4*i+j]+p[4*i+j]);
        cd[i]=v; cf[i]=(v<=req[i]); }
      cpu.push_back(ms(start));
      start=Clock::now(); score<<<(n+255)/256,256>>>(da,dp,dr,dd,df,n); CHECK(cudaGetLastError()); CHECK(cudaDeviceSynchronize()); gpu.push_back(ms(start));
      start=Clock::now(); upload(); score<<<(n+255)/256,256>>>(da,dp,dr,dd,df,n); CHECK(cudaGetLastError());
      CHECK(cudaMemcpy(gd.data(),dd,sz,cudaMemcpyDeviceToHost)); CHECK(cudaMemcpy(gf.data(),df,sz,cudaMemcpyDeviceToHost)); transfer.push_back(ms(start));
      if(gd!=cd || gf!=cf) { std::fprintf(stderr,"CPU/GPU mismatch\n"); return 2; }
    }
    std::printf("%s{\"candidates\":%d,\"correct\":true,\"device_bytes\":%zu,\"allocation_ms\":%.6f,\"cpu_median_ms\":%.6f,\"resident_median_ms\":%.6f,\"transfer_median_ms\":%.6f}",first?"":",",n,sz*11,alloc_ms,median(cpu),median(gpu),median(transfer));
    first=false; CHECK(cudaFree(da)); CHECK(cudaFree(dp)); CHECK(cudaFree(dr)); CHECK(cudaFree(dd)); CHECK(cudaFree(df));
  }
  std::puts("]}"); return 0;
}
