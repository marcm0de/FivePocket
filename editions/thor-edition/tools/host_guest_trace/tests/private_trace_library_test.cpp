#include <dlfcn.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <thread>

int main(int argc,char** argv){
    // Asset-free ABI test. No valid game arguments or entry point are supplied.
    // The library itself is private generated source and must stay local.
    assert(argc==2 && argv[1][0]=='/');
    void* library=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);
    if(!library){std::fprintf(stderr,"private library load failed: %s\n",dlerror());return 1;}
    using Entry=int (*)(int,char**) noexcept;
    const auto entry=reinterpret_cast<Entry>(dlsym(library,"recomp_run_private_trace_v1"));
    assert(entry);
    assert(!dlsym(library,"main") && !dlsym(library,"_xstart"));
    char name[]="/private-abi-fixture";char* valid[]{name,nullptr};
    char longArgument[4098];std::memset(longArgument,'x',sizeof(longArgument));longArgument[4097]=0;
    char* invalid[]{longArgument,nullptr};char* nullString[]{nullptr};
    assert(entry(0,nullptr)==2 && entry(-1,valid)==2 && entry(1025,valid)==2);
    assert(entry(1,nullptr)==2 && entry(1,nullString)==2 && entry(1,invalid)==2);
    // Exactly one simultaneous syntactically valid call may attempt setup.
    // Both lack mandatory input arguments: the winner returns2; the other3.
    int a=-1,b=-1;
    std::thread one([&]{a=entry(1,valid);}),two([&]{b=entry(1,valid);});one.join();two.join();
    assert((a==2 && b==3) || (a==3 && b==2));
    assert(entry(1,valid)==3);
    assert(dlclose(library)==0);
    std::puts("PRIVATE_TRACE_LIBRARY: load, bounded argv, one-shot concurrent gate and hidden entry symbols PASS; no guest execution");
}
