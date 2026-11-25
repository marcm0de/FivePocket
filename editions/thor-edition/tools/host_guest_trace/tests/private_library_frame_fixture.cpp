#include "private_trace_api.h"
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <vector>

// Original asymmetric pixels: exercise the cross-library borrowed-frame callback
// and actual Android renderer, without a game executable or original assets.
extern "C" int recomp_run_private_trace_v1(int,char**) noexcept {return 2;}
extern "C" int recomp_run_private_trace_v2(int,char**,const RecompPrivateTraceCallbacksV2* callbacks) noexcept {
    if(!callbacks || callbacks->structureBytes!=sizeof(*callbacks) || callbacks->version!=2 || !callbacks->stoppedRgba)return 2;
    try {
        std::vector<std::uint8_t> rgba(1280U*720U*4U);
        for(unsigned y=0;y<720;++y)for(unsigned x=0;x<1280;++x){
            const auto at=(y*1280+x)*4;
            rgba[at]=((x<640)==(y<360))?255:0;
            rgba[at+1]=x>=640?255:0;rgba[at+2]=(x<640 && y>=360)?255:0;rgba[at+3]=255;
        }
        const int accepted=callbacks->stoppedRgba(callbacks->user,rgba.data(),static_cast<std::uint32_t>(rgba.size()),1280,720);
        std::fill(rgba.begin(),rgba.end(),0xCD); // Consumer cannot borrow beyond return.
        std::printf("PRIVATE_FRAME_FIXTURE_V2\naccepted=%d\n",accepted);
        return 0;
    }catch(...){return 2;}
}

// Synthetic ABI/window test only: there are no guest writers in this fixture.
// It cannot attest the real executor's writer-parking or command ordering.
extern "C" int recomp_run_private_trace_v3(int,char**,const RecompPrivateTraceCallbacksV3* callbacks) noexcept {
    if(!callbacks || callbacks->structureBytes!=sizeof(*callbacks) || callbacks->version!=3 ||
        callbacks->reserved0 || callbacks->reserved1 || !callbacks->orderedRgba)return 2;
    try {
        std::vector<std::uint8_t> rgba(1280U*720U*4U);
        for(unsigned y=0;y<720;++y)for(unsigned x=0;x<1280;++x){
            const auto at=(y*1280+x)*4;
            rgba[at]=((x<640)==(y<360))?255:0;
            rgba[at+1]=x>=640?255:0;rgba[at+2]=(x<640 && y>=360)?255:0;rgba[at+3]=255;
        }
        const auto bytes=static_cast<std::uint32_t>(rgba.size());
        for(unsigned i=0;i<3;++i)
            if(callbacks->orderedRgba(callbacks->user,rgba.data(),bytes-(i==0),1280-(i==1),720-(i==2)))return 2;
        const int accepted=callbacks->orderedRgba(callbacks->user,rgba.data(),bytes,1280,720);
        std::fill(rgba.begin(),rgba.end(),0xCD);
        const int duplicate=callbacks->orderedRgba(callbacks->user,rgba.data(),bytes,1280,720);
        if(duplicate)return 2;
        std::printf("PRIVATE_FRAME_FIXTURE_V3\naccepted=%d\ninvalid_shapes_rejected=3\nduplicate_rejected=1\n",accepted);
        return 0;
    }catch(...){return 2;}
}

extern "C" int recomp_run_private_trace_v4(int argc,char** argv,const RecompPrivateTraceCallbacksV4* callbacks) noexcept {
    if(argc<3 || !argv || !argv[2] || !callbacks || callbacks->structureBytes!=sizeof(*callbacks) || callbacks->version!=4 ||
        callbacks->reserved0 || callbacks->reserved1 || !callbacks->orderedRgba)return 2;
    try {
        const unsigned requested=std::strcmp(argv[2],"1")==0?1:2;
        std::vector<std::uint8_t> rgba(1280U*720U*4U);
        const auto bytes=static_cast<std::uint32_t>(rgba.size());
        unsigned accepted=0,invalid=0;
        auto call=[&](unsigned sequence){return callbacks->orderedRgba(callbacks->user,sequence,rgba.data(),bytes,1280,720);};
        for(unsigned sequence=0;sequence<requested;++sequence){
            for(unsigned y=0;y<720;++y)for(unsigned x=0;x<1280;++x){
                const auto at=(y*1280+x)*4;
                rgba[at]=((x<640)==(y<360))?255:0;
                rgba[at+1]=x>=640?255:0;rgba[at+2]=(x<640 && y>=360)?255:0;rgba[at+3]=255;
                if(sequence)for(unsigned c=0;c<3;++c)rgba[at+c]=255-rgba[at+c];
            }
            if(call(sequence+1) || call(256) || call(0xFFFFFFFFU))return 2;
            invalid+=3;
            if(sequence){if(call(sequence-1))return 2;++invalid;}
            for(unsigned i=0;i<3;++i){
                if(callbacks->orderedRgba(callbacks->user,sequence,rgba.data(),bytes-(i==0),1280-(i==1),720-(i==2)))return 2;
                ++invalid;
            }
            const int result=call(sequence);
            if(result!=0 && result!=1)return 2;
            std::fill(rgba.begin(),rgba.end(),0xCD); // No borrowing after return.
            if(call(sequence))return 2; // Already attempted, even if rejected.
            ++invalid;
            if(!result){
                if(call(sequence+1))return 2;
                ++invalid;break;
            }
            ++accepted;
        }
        std::printf("PRIVATE_FRAME_FIXTURE_V4\naccepted=%u\ninvalid_rejected=%u\n",accepted,invalid);
        return 0;
    }catch(...){return 2;}
}
