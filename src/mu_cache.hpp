// GPU-precomputed column multiplicities for the unchanged ABC ACD search.
// Missing entries execute the original CPU function. No approximate answers.
#pragma once
#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <string>
#include <unordered_map>
#include <vector>
namespace nextgowin_mu {
struct Entry { std::vector<uint8_t> values; };
inline std::string key(const uint64_t* truth,unsigned n,unsigned k) {
  std::string s(reinterpret_cast<const char*>(&n),4);s.append(reinterpret_cast<const char*>(&k),4);
  s.append(reinterpret_cast<const char*>(truth),8*(1u<<(n>6?n-6:0)));return s;
}
struct Cache {
  std::unordered_map<std::string,Entry> entries;
  uint64_t queries=0,hits=0,misses=0,verified=0;
  bool verify=false;
  std::string stats_path;
  Cache() {
    const char* stats=std::getenv("NEXTGOWIN_MU_STATS");if(stats)stats_path=stats;
    const char* check=std::getenv("NEXTGOWIN_MU_VERIFY");verify=check && std::string(check)=="1";
    const char* file=std::getenv("NEXTGOWIN_MU_CACHE");if(!file || !*file)return;
    std::ifstream in(file,std::ios::binary);char magic[8];uint32_t count=0;
    in.read(magic,8);in.read(reinterpret_cast<char*>(&count),4);
    if(!in || std::string(magic,8)!="ACDMU001" || count>1000000)fail("invalid cache header");
    for(unsigned i=0;i<count;i++) {
      uint32_t header[3]={0};in.read(reinterpret_cast<char*>(header),12);
      unsigned n=header[0],k=header[1],words=header[2];
      if(!in || n>11 || n<1 || k<3 || k>6 || words!=(1u<<(n>6?n-6:0)))fail("invalid cache dimensions");
      uint64_t truth[32]={0};in.read(reinterpret_cast<char*>(truth),words*8);
      Entry e;e.values.resize(1u<<n);in.read(reinterpret_cast<char*>(e.values.data()),e.values.size());
      if(!in)fail("truncated cache");
      for(auto value:e.values)if(value!=255 && (value<1 || value>64))fail("invalid multiplicity");
      if(!entries.emplace(key(truth,n,k),std::move(e)).second)fail("duplicate cache truth");
    }
    if(in.peek()!=std::char_traits<char>::eof())fail("trailing cache data");
  }
  static void fail(const char* msg){std::cerr<<"NEXTGOWIN_MU_ERROR "<<msg<<"\n";std::exit(17);}
  ~Cache(){if(stats_path.empty())return;std::ofstream out(stats_path);
    out<<"{\"entries\":"<<entries.size()<<",\"queries\":"<<queries<<",\"hits\":"<<hits
       <<",\"misses\":"<<misses<<",\"verified\":"<<verified<<"}\n";}
};
inline Cache cache;
struct Context;
inline thread_local Context* current=nullptr;
struct Context {
  const Entry* entry=nullptr;Context* previous;
  Context(const uint64_t* truth,unsigned n,unsigned k):previous(current) {
    if(!cache.entries.empty()) {auto it=cache.entries.find(key(truth,n,k));if(it!=cache.entries.end())entry=&it->second;}
    current=this;
  }
  ~Context(){current=previous;}
};
template<class F> inline uint32_t column(unsigned free_count,const uint32_t* perm,const uint32_t* comb,F fallback,bool cap_at_three=false) {
  cache.queries++;
  if(!current || !current->entry){cache.misses++;return fallback();}
  unsigned mask=0;
  for(unsigned i=0;i<free_count;i++) {unsigned bit=perm[comb?comb[i]:i];if(bit>=11)Cache::fail("invalid permutation");mask|=1u<<bit;}
  const auto& values=current->entry->values;
  if(mask>=values.size() || values[mask]==255){cache.misses++;return fallback();}
  uint32_t value=values[mask];if(cap_at_three)value=std::min(value,3u);
  cache.hits++;
  if(cache.verify){if(fallback()!=value)Cache::fail("GPU/CPU multiplicity mismatch in mapper");cache.verified++;}
  return value;
}
}
