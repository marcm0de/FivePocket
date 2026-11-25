#include "private_display_sequence.h"
#include <cassert>
#include <cstdio>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>
using recomp::private_trace::DisplaySequence;
using recomp::private_trace::DisplayCallbacksV4;

int main(){
    constexpr unsigned testedFrames=512;
    for(unsigned rejected: {0U,1U,255U,256U,511U,testedFrames}){
        DisplaySequence state;
        assert(!state.finish(true) && state.next()==0);
        for(unsigned i=0;i<testedFrames;++i){
            assert(!state.begin(i+1) && !state.begin(0xFFFFFFFFU));
            if(i)assert(!state.begin(i-1));
            assert(state.begin(i));
            assert(!state.begin(i) && !state.begin(i+1));
            assert(state.finish(i!=rejected)==(i!=rejected));
            assert(!state.finish(true));
            if(i==rejected){assert(!state.begin(i+1));break;}
        }
        assert(!state.begin(DisplaySequence::Limit));
        if(rejected==testedFrames){assert(state.begin(testedFrames));assert(state.finish(true));}
    }
    std::vector<std::uint8_t> pixels(1280U*720U*4U,255);
    struct Context {unsigned calls=0,reject=999;DisplayCallbacksV4* bridge=nullptr;};
    auto callback=[](void* user,std::uint32_t sequence,const std::uint8_t* rgba,std::uint32_t bytes,
            std::uint32_t width,std::uint32_t height)noexcept{
        auto& context=*static_cast<Context*>(user);
        assert(sequence==context.calls++ && bytes==1280U*720U*4U && width==1280 && height==720 && rgba[0]==255);
        assert(context.bridge->consume(rgba,bytes,width,height)==0); // In-flight reentry.
        return sequence==context.reject?0:1;
    };
    for(unsigned reject: {0U,1U,127U,255U,256U,511U,999U}){
        Context context;context.reject=reject;
        RecompPrivateTraceCallbacksV4 source{sizeof(source),4,0,0,&context,callback};
        DisplayCallbacksV4 bridge(source);context.bridge=&bridge;
        source.orderedRgba=nullptr;source.user=nullptr; // Bridge owns the header.
        for(unsigned i=0;i<514;++i){
            assert(!bridge.consume(nullptr,pixels.size(),1280,720));
            assert(!bridge.consume(pixels.data(),pixels.size()-1,1280,720));
            assert(!bridge.consume(pixels.data(),pixels.size(),1279,720));
            assert(!bridge.consume(pixels.data(),pixels.size(),1280,719));
            assert(bridge.consume(pixels.data(),pixels.size(),1280,720)==(i<reject));
        }
        assert(context.calls==(reject<514?reject+1:514));
    }
    for(int result: {-1,2,127}){
        const auto child=fork();assert(child>=0);
        if(!child){
            const RecompPrivateTraceCallbacksV4 callbackHeader{sizeof(callbackHeader),4,0,0,&result,
                [](void* value,std::uint32_t,const std::uint8_t*,std::uint32_t,std::uint32_t,std::uint32_t)noexcept{
                    return *static_cast<int*>(value);}};
            DisplayCallbacksV4 bridge(callbackHeader);
            bridge.consume(pixels.data(),pixels.size(),1280,720);_exit(99);
        }
        int status=0;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==4);
    }
    std::puts("PRIVATE_DISPLAY_SEQUENCE_PASS:514 ordered updates, rejection across old 256 cap, replay/gap/reentry/reserved sequence, owned header, 3 malformed-return fail-stops; CPU only");
}
