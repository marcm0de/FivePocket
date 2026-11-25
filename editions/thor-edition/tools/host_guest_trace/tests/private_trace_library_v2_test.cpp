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
    assert(v1 && v2 && !dlsym(library,"main") && !dlsym(library,"_xstart"));
    std::atomic<int> callbacks{0};
    const RecompPrivateTraceCallbacksV2 valid{sizeof(RecompPrivateTraceCallbacksV2),2,0,0,&callbacks,
        [](void* user,const std::uint8_t*,std::uint32_t,std::uint32_t,std::uint32_t)noexcept{
            ++*static_cast<std::atomic<int>*>(user);return 1;}};
    char name[]="/private-v2-fixture";char* arguments[]{name,nullptr};
    assert(v2(1,arguments,nullptr)==2);
    auto altered=valid;altered.stoppedRgba=nullptr;assert(v2(1,arguments,&altered)==2);
    for(unsigned field=0;field<4;++field)for(unsigned bit=0;bit<32;++bit){
        altered=valid;
        switch(field){
            case 0:altered.structureBytes^=1U<<bit;break;
            case 1:altered.version^=1U<<bit;break;
            case 2:altered.reserved0^=1U<<bit;break;
            case 3:altered.reserved1^=1U<<bit;break;
        }
        assert(v2(1,arguments,&altered)==2);
    }
    assert(v2(0,nullptr,&valid)==2 && v2(-1,arguments,&valid)==2 && v2(1025,arguments,&valid)==2);
    // Both interfaces share ONE admission gate. Missing game inputs/configuration
    // return2 for the winner; a racing other-version entry must return3.
    int first=-1,second=-1;
    std::thread one([&]{first=v1(1,arguments);});
    std::thread two([&]{second=v2(1,arguments,&valid);});one.join();two.join();
    assert((first==2 && second==3) || (first==3 && second==2));
    assert(v1(1,arguments)==3 && v2(1,arguments,&valid)==3 && callbacks==0);
    assert(dlclose(library)==0);
    std::puts("PRIVATE_TRACE_V2_ABI_PASS:128 config bit controls, shared v1/v2 once gate; no guest/frame callback execution");
}
