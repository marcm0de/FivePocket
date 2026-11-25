#include "private_trace_api.h"
#include <dlfcn.h>
#include <atomic>
#include <cassert>
#include <cstdio>
#include <thread>

int main(int argc,char** argv){
    assert(argc==2 && argv[1][0]=='/');
    void* library=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);assert(library);
    const auto v1=reinterpret_cast<decltype(&recomp_run_private_trace_v1)>(dlsym(library,"recomp_run_private_trace_v1"));
    const auto v2=reinterpret_cast<decltype(&recomp_run_private_trace_v2)>(dlsym(library,"recomp_run_private_trace_v2"));
    const auto v3=reinterpret_cast<decltype(&recomp_run_private_trace_v3)>(dlsym(library,"recomp_run_private_trace_v3"));
    assert(v1 && v2 && v3 && !dlsym(library,"main") && !dlsym(library,"_xstart"));
    std::atomic<int> callbacks{0};
    auto callback=[](void* user,const std::uint8_t*,std::uint32_t,std::uint32_t,std::uint32_t)noexcept{
        ++*static_cast<std::atomic<int>*>(user);return 1;};
    const RecompPrivateTraceCallbacksV3 valid{sizeof(RecompPrivateTraceCallbacksV3),3,0,0,&callbacks,callback};
    const RecompPrivateTraceCallbacksV2 stopped{sizeof(RecompPrivateTraceCallbacksV2),2,0,0,&callbacks,callback};
    char name[]="/private-v3-fixture";char* arguments[]{name,nullptr};
    assert(v3(1,arguments,nullptr)==2);
    auto altered=valid;altered.orderedRgba=nullptr;assert(v3(1,arguments,&altered)==2);
    for(unsigned field=0;field<4;++field)for(unsigned bit=0;bit<32;++bit){
        altered=valid;
        switch(field){
            case 0:altered.structureBytes^=1U<<bit;break;
            case 1:altered.version^=1U<<bit;break;
            case 2:altered.reserved0^=1U<<bit;break;
            case 3:altered.reserved1^=1U<<bit;break;
        }
        assert(v3(1,arguments,&altered)==2);
    }
    assert(v3(0,nullptr,&valid)==2 && v3(-1,arguments,&valid)==2 && v3(1025,arguments,&valid)==2);
    int result[3]{-1,-1,-1};
    std::thread one([&]{result[0]=v1(1,arguments);});
    std::thread two([&]{result[1]=v2(1,arguments,&stopped);});
    std::thread three([&]{result[2]=v3(1,arguments,&valid);});
    one.join();two.join();three.join();
    unsigned won=0,lost=0;for(int code:result){won+=code==2;lost+=code==3;}
    assert(won==1 && lost==2 && callbacks==0);
    assert(v1(1,arguments)==3 && v2(1,arguments,&stopped)==3 && v3(1,arguments,&valid)==3);
    assert(dlclose(library)==0);
    std::puts("PRIVATE_TRACE_V3_ABI_PASS:128 config bit controls, shared v1/v2/v3 once gate; no guest/frame callback execution");
}
