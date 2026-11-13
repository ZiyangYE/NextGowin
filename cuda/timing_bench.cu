// Exact int64 forward/reverse LUT-DAG timing; no device routing/clock model.
#include <cuda_runtime.h>
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <omp.h>
using Time=int64_t;
constexpr Time INF=Time(1)<<60;
using Clock=std::chrono::steady_clock;
double ms(Clock::time_point start){return std::chrono::duration<double,std::milli>(Clock::now()-start).count();}
double med(std::vector<double> v){std::sort(v.begin(),v.end());return v[v.size()/2];}
void check(cudaError_t code){if(code!=cudaSuccess)throw std::runtime_error(cudaGetErrorString(code));}
struct Mismatch:std::runtime_error{using std::runtime_error::runtime_error;};
template<class T>void load(std::ifstream& file,std::vector<T>& array,size_t count){
  array.resize(count);file.read(reinterpret_cast<char*>(array.data()),count*sizeof(T));
  if(!file)throw std::runtime_error("truncated timing graph");
}
struct Graph {
  uint32_t n,e,levels,batch,inputs;
  std::vector<Time> delay,arr_seed,req_seed,arr_expected,req_expected;
  std::vector<uint32_t> inoff,in,outoff,out,leveloff,order;
  Graph(const char* path){
    std::ifstream file(path,std::ios::binary);char magic[8];uint32_t h[5]={0};
    file.read(magic,8);file.read(reinterpret_cast<char*>(h),20);
    if(!file || std::string(magic,8)!="NGSTA001")throw std::runtime_error("bad graph header");
    n=h[0];e=h[1];levels=h[2];batch=h[3];inputs=h[4];
    if(!n || n>1000000 || e>64000000 || !levels || levels>n || !batch || batch>4096 || inputs>n || size_t(n)*batch>50000000)
      throw std::runtime_error("graph dimensions outside bounds");
    load(file,delay,n);load(file,inoff,n+1);load(file,in,e);load(file,outoff,n+1);load(file,out,e);
    load(file,leveloff,levels+1);load(file,order,n);
    size_t slots=size_t(n)*batch;
    load(file,arr_seed,slots);load(file,req_seed,slots);load(file,arr_expected,slots);load(file,req_expected,slots);
    if(file.peek()!=std::char_traits<char>::eof())throw std::runtime_error("trailing graph data");
    auto offsets=[](const std::vector<uint32_t>& o,uint32_t end){return o.front()==0 && o.back()==end && std::is_sorted(o.begin(),o.end());};
    if(!offsets(inoff,e)||!offsets(outoff,e)||!offsets(leveloff,n))throw std::runtime_error("invalid CSR/level offsets");
    std::vector<int> ranks(n,-1);
    for(unsigned level=0;level<levels;level++)for(unsigned i=leveloff[level];i<leveloff[level+1];i++){
      unsigned node=order[i];if(node>=n || ranks[node]!=-1)throw std::runtime_error("bad level node permutation");ranks[node]=level;
    }
    std::vector<uint64_t> edges_a,edges_b;edges_a.reserve(e);edges_b.reserve(e);
    for(unsigned node=0;node<n;node++){
      if(delay[node]<0 || delay[node]>1000000000)throw std::runtime_error("invalid node delay");
      if(node<inputs && (inoff[node]!=inoff[node+1] || delay[node]!=0))throw std::runtime_error("invalid primary input");
      for(unsigned i=inoff[node];i<inoff[node+1];i++){
        unsigned parent=in[i];if(parent>=n || ranks[parent]>=ranks[node])throw std::runtime_error("invalid/non-DAG fanin");
        edges_a.push_back((uint64_t(parent)<<32)|node);
      }
      for(unsigned i=outoff[node];i<outoff[node+1];i++){
        unsigned sink=out[i];if(sink>=n)throw std::runtime_error("invalid fanout");edges_b.push_back((uint64_t(node)<<32)|sink);
      }
    }
    std::sort(edges_a.begin(),edges_a.end());std::sort(edges_b.begin(),edges_b.end());
    if(edges_a!=edges_b)throw std::runtime_error("fanin/fanout CSR are not transposes");
    for(auto value:arr_seed)if(value<0 || value>=(Time(1)<<50))throw std::runtime_error("invalid arrival seed");
    for(auto value:req_seed)if(value!=INF && (value<=-(Time(1)<<50) || value>=(Time(1)<<50)))throw std::runtime_error("invalid required seed");
  }
  size_t slots()const{return size_t(n)*batch;}
};

void cpu(const Graph& g,std::vector<Time>& arrivals,std::vector<Time>& required,int threads,
         const std::vector<Time>* arr_override=nullptr,const std::vector<Time>* req_override=nullptr){
  const auto& arr_seed=arr_override?*arr_override:g.arr_seed;
  const auto& req_seed=req_override?*req_override:g.req_seed;
  auto config=[&](int b){
    size_t base=size_t(b)*g.n;
    for(unsigned node:g.order){
      Time value=arr_seed[base+node];
      if(g.inoff[node]!=g.inoff[node+1]){
        value=-INF;for(unsigned i=g.inoff[node];i<g.inoff[node+1];i++)value=std::max(value,arrivals[base+g.in[i]]);
        value+=g.delay[node];
      }
      arrivals[base+node]=value;
    }
    for(size_t i=g.n;i-->0;){
      unsigned node=g.order[i];Time value=req_seed[base+node];
      for(unsigned j=g.outoff[node];j<g.outoff[node+1];j++){
        unsigned sink=g.out[j];Time r=required[base+sink];if(r!=INF)value=std::min(value,r-g.delay[sink]);
      }
      required[base+node]=value;
    }
  };
  if(threads==1){for(int b=0;b<int(g.batch);b++)config(b);}
  else{
    #pragma omp parallel for num_threads(threads) schedule(static)
    for(int b=0;b<int(g.batch);b++)config(b);
  }
}
void verify(const Graph& g,const std::vector<Time>& arr,const std::vector<Time>& req,const char* who){
  if(arr!=g.arr_expected || req!=g.req_expected)throw Mismatch(std::string(who)+" arrival/required mismatch against Python oracle");
}
__global__ void forward(const uint32_t* order,unsigned begin,unsigned count,unsigned n,unsigned batch,
                        const uint32_t* offsets,const uint32_t* fanins,const Time* delay,const Time* seed,Time* arr){
  size_t i=size_t(blockIdx.x)*blockDim.x+threadIdx.x;
  if(i>=size_t(count)*batch)return;
  unsigned node=order[begin+i%count];size_t base=(i/count)*n;
  Time value=seed[base+node];
  if(offsets[node]!=offsets[node+1]){
    value=-INF;for(unsigned j=offsets[node];j<offsets[node+1];j++){Time a=arr[base+fanins[j]];if(a>value)value=a;}
    value+=delay[node];
  }
  arr[base+node]=value;
}
__global__ void backward(const uint32_t* order,unsigned begin,unsigned count,unsigned n,unsigned batch,
                         const uint32_t* offsets,const uint32_t* fanouts,const Time* delay,const Time* seed,Time* req){
  size_t i=size_t(blockIdx.x)*blockDim.x+threadIdx.x;
  if(i>=size_t(count)*batch)return;
  unsigned node=order[begin+i%count];size_t base=(i/count)*n;Time value=seed[base+node];
  for(unsigned j=offsets[node];j<offsets[node+1];j++){
    unsigned sink=fanouts[j];Time r=req[base+sink];if(r!=INF){r-=delay[sink];if(r<value)value=r;}
  }
  req[base+node]=value;
}
struct Device {
  std::vector<void*> buffers;cudaStream_t stream=nullptr;cudaGraph_t graph=nullptr;cudaGraphExec_t exec=nullptr;
  cudaEvent_t begin=nullptr,end=nullptr;
  ~Device(){if(exec)cudaGraphExecDestroy(exec);if(graph)cudaGraphDestroy(graph);if(begin)cudaEventDestroy(begin);if(end)cudaEventDestroy(end);
    if(stream)cudaStreamDestroy(stream);for(void* p:buffers)cudaFree(p);}
  template<class T>T* alloc(size_t count){if(!count)count=1;T* pointer;check(cudaMalloc(&pointer,sizeof(T)*count));buffers.push_back(pointer);return pointer;}
  template<class T>T* upload(const std::vector<T>& source){T* p=alloc<T>(source.size());if(source.size())check(cudaMemcpy(p,source.data(),source.size()*sizeof(T),cudaMemcpyHostToDevice));return p;}
};
struct GpuResult {
  bool available=false;std::string reason,name;size_t allocated=0,free_before=0,free_after=0;
  int mutable_checks=0;
  double init=0,setup=0,cold=0;std::vector<double> wall,event,transfer;
};
GpuResult gpu(const Graph& g,std::vector<Time>& arr,std::vector<Time>& req,int repeats){
  GpuResult result;auto cold=Clock::now();Device device;
  try{
    auto start=Clock::now();check(cudaSetDevice(0));check(cudaFree(nullptr));result.init=ms(start);
    cudaDeviceProp prop;check(cudaGetDeviceProperties(&prop,0));result.name=prop.name;
    if(g.levels>4096)throw std::runtime_error("GPU capture layer budget exceeded; CPU fallback");
    size_t total;check(cudaMemGetInfo(&result.free_before,&total));
    result.allocated=(g.delay.size()*8+(g.inoff.size()+g.in.size()+g.outoff.size()+g.out.size()+g.order.size())*4+4*g.slots()*8+(g.e?0:8));
    if(result.allocated>result.free_before*7/10)throw std::runtime_error("snapshot buffers exceed 70 percent of free device memory");
    start=Clock::now();auto delay=device.upload(g.delay);auto inoff=device.upload(g.inoff);auto in=device.upload(g.in);
    auto outoff=device.upload(g.outoff);auto out=device.upload(g.out);auto order=device.upload(g.order);
    auto arr_seed=device.upload(g.arr_seed);auto req_seed=device.upload(g.req_seed);
    auto d_arr=device.alloc<Time>(g.slots());auto d_req=device.alloc<Time>(g.slots());
    check(cudaStreamCreate(&device.stream));check(cudaEventCreate(&device.begin));check(cudaEventCreate(&device.end));
    auto pass=[&](){
      for(unsigned level=0;level<g.levels;level++){
        unsigned count=g.leveloff[level+1]-g.leveloff[level];if(!count)continue;
        unsigned blocks=unsigned((size_t(count)*g.batch+127)/128);
        forward<<<blocks,128,0,device.stream>>>(order,g.leveloff[level],count,g.n,g.batch,inoff,in,delay,arr_seed,d_arr);
      }
      for(unsigned level=g.levels;level-->0;){
        unsigned count=g.leveloff[level+1]-g.leveloff[level];if(!count)continue;
        unsigned blocks=unsigned((size_t(count)*g.batch+127)/128);
        backward<<<blocks,128,0,device.stream>>>(order,g.leveloff[level],count,g.n,g.batch,outoff,out,delay,req_seed,d_req);
      }
      check(cudaGetLastError());
    };
    check(cudaStreamBeginCapture(device.stream,cudaStreamCaptureModeGlobal));pass();check(cudaStreamEndCapture(device.stream,&device.graph));
    check(cudaGraphInstantiate(&device.exec,device.graph,nullptr,nullptr,0));
    check(cudaMemGetInfo(&result.free_after,&total));result.setup=ms(start);
    auto upload_seeds=[&](){
      check(cudaMemcpyAsync(arr_seed,g.arr_seed.data(),g.slots()*8,cudaMemcpyHostToDevice,device.stream));
      check(cudaMemcpyAsync(req_seed,g.req_seed.data(),g.slots()*8,cudaMemcpyHostToDevice,device.stream));
    };
    auto download=[&](){
      check(cudaMemcpyAsync(arr.data(),d_arr,g.slots()*8,cudaMemcpyDeviceToHost,device.stream));
      check(cudaMemcpyAsync(req.data(),d_req,g.slots()*8,cudaMemcpyDeviceToHost,device.stream));
    };
    upload_seeds();check(cudaGraphLaunch(device.exec,device.stream));download();check(cudaStreamSynchronize(device.stream));
    verify(g,arr,req,"cold GPU");result.cold=ms(cold);
    // Reuse the captured graph with changed arrivals and both relaxed/tighter
    // deadlines. This catches stale required times hidden by idempotent reruns.
    std::vector<Time> changed_arr(g.slots()),changed_req(g.slots()),expected_arr(g.slots()),expected_req(g.slots());
    for(Time adjustment:{Time(123),Time(-71)}){
      changed_arr=g.arr_seed;changed_req=g.req_seed;
      for(unsigned b=0;b<g.batch;b++)for(unsigned node=0;node<g.inputs;node++)changed_arr[size_t(b)*g.n+node]+=17;
      for(auto& value:changed_req)if(value!=INF)value+=adjustment;
      cpu(g,expected_arr,expected_req,1,&changed_arr,&changed_req);
      check(cudaMemcpyAsync(arr_seed,changed_arr.data(),g.slots()*8,cudaMemcpyHostToDevice,device.stream));
      check(cudaMemcpyAsync(req_seed,changed_req.data(),g.slots()*8,cudaMemcpyHostToDevice,device.stream));
      check(cudaGraphLaunch(device.exec,device.stream));download();check(cudaStreamSynchronize(device.stream));
      if(arr!=expected_arr || req!=expected_req)throw Mismatch("mutable boundary GPU mismatch");
      result.mutable_checks++;
    }
    upload_seeds();check(cudaGraphLaunch(device.exec,device.stream));download();check(cudaStreamSynchronize(device.stream));verify(g,arr,req,"restored GPU");
    for(int rep=0;rep<repeats;rep++){
      start=Clock::now();check(cudaEventRecord(device.begin,device.stream));check(cudaGraphLaunch(device.exec,device.stream));
      check(cudaEventRecord(device.end,device.stream));check(cudaEventSynchronize(device.end));result.wall.push_back(ms(start));
      float time;check(cudaEventElapsedTime(&time,device.begin,device.end));result.event.push_back(time);
      download();check(cudaStreamSynchronize(device.stream));verify(g,arr,req,"resident GPU");
      start=Clock::now();upload_seeds();check(cudaGraphLaunch(device.exec,device.stream));download();check(cudaStreamSynchronize(device.stream));
      result.transfer.push_back(ms(start));verify(g,arr,req,"transfer GPU");
    }
    result.available=true;
  }catch(const Mismatch&){throw;}catch(const std::exception& error){result.reason=error.what();}
  return result;
}
void array(const std::vector<double>& values){std::printf("[");for(size_t i=0;i<values.size();i++)std::printf("%s%.6f",i?",":"",values[i]);std::printf("]");}
int main(int argc,char** argv){
  if(argc<2){std::fprintf(stderr,"usage: timing_bench graph.bin [--cpu-only] [--threads N]\n");return 2;}
  try{
    bool cpu_only=false;int threads=8;
    for(int i=2;i<argc;i++){std::string a=argv[i];if(a=="--cpu-only")cpu_only=true;else if(a=="--threads"&&i+1<argc)threads=std::stoi(argv[++i]);else return 2;}
    if(threads<1 || threads>64)return 2;
    Graph graph(argv[1]);threads=std::min(threads,int(graph.batch));const int repeats=5;
    std::vector<Time> arr(graph.slots()),req(graph.slots());std::vector<double> serial,parallel;
    cpu(graph,arr,req,1);verify(graph,arr,req,"warm serial CPU");
    cpu(graph,arr,req,threads);verify(graph,arr,req,"warm parallel CPU");
    auto pilot=Clock::now();cpu(graph,arr,req,1);double one=ms(pilot);
    pilot=Clock::now();cpu(graph,arr,req,threads);double multi=ms(pilot);
    int serial_inner=std::min(100000,std::max(1,int(5/std::max(one,.0001))));
    int parallel_inner=std::min(100000,std::max(1,int(5/std::max(multi,.0001))));
    for(int rep=0;rep<repeats;rep++){
      auto start=Clock::now();for(int i=0;i<serial_inner;i++)cpu(graph,arr,req,1);serial.push_back(ms(start)/serial_inner);verify(graph,arr,req,"serial CPU");
      start=Clock::now();for(int i=0;i<parallel_inner;i++)cpu(graph,arr,req,threads);parallel.push_back(ms(start)/parallel_inner);verify(graph,arr,req,"parallel CPU");
    }
    GpuResult device;if(!cpu_only)device=gpu(graph,arr,req,repeats);else device.reason="CPU-only requested";
    std::printf("{\"scope\":\"Fixed LUT DAG arrival/required propagation; not a mapper or full FPGA STA\",\"nodes\":%u,\"edges\":%u,\"levels\":%u,\"snapshots\":%u,\"cpu_threads\":%d,\"cpu_serial_inner_repeats\":%d,\"cpu_parallel_inner_repeats\":%d,\"cpu_correct\":true,\"gpu_correct\":%s,\"device\":\"%s\",\"cpu_serial_ms\":",graph.n,graph.e,graph.levels,graph.batch,threads,serial_inner,parallel_inner,device.available?"true":"false",device.name.c_str());
    array(serial);std::printf(",\"cpu_parallel_ms\":");array(parallel);std::printf(",\"gpu_resident_wall_ms\":");array(device.wall);
    std::printf(",\"gpu_event_ms\":");array(device.event);std::printf(",\"gpu_with_seed_transfer_and_readback_ms\":");array(device.transfer);
    std::printf(",\"gpu_context_init_ms\":%.6f,\"gpu_setup_and_graph_capture_ms\":%.6f,\"cold_gpu_call_ms\":%.6f,\"application_device_buffer_bytes\":%zu,\"device_free_bytes_before\":%zu,\"device_free_bytes_after_setup\":%zu,\"mutable_boundary_checks\":%d,\"cpu_fallback_reason\":\"%s\"}\n",device.init,device.setup,device.cold,device.allocated,device.free_before,device.free_after,device.mutable_checks,device.reason.c_str());
    return 0;
  }catch(const Mismatch& error){std::fprintf(stderr,"TIMING_MISMATCH %s\n",error.what());return 17;}
  catch(const std::exception& error){std::fprintf(stderr,"TIMING_INPUT_ERROR %s\n",error.what());return 3;}
}
