#include "native_completed_scene_target.h"
#include <atomic>
#include <cassert>
#include <cstdio>
#include <functional>
#include <optional>

namespace {
unsigned destroyed=0;
struct Target {unsigned value=17;~Target(){++destroyed;}};
struct Engine {
    unsigned calls=0;bool succeed=true;
    bool executeRuntimeMaintenancePoints(int,int,const Target& target,const std::function<bool()>& publish) {
        ++calls;assert(target.value==17);return succeed && publish();
    }
};
struct Registry {
    unsigned calls=0,publications=0,device=0,word=0;bool accept=true;
    template<class Gate,class Execute> bool submitNativeRuntimeMaintenancePoints(unsigned d,unsigned w,const Gate& gate,const Execute& execute) {
        ++calls;device=d;word=w;bool done=false;
        const bool entered=gate([&]{if(accept)done=execute(1,2,[&]{++publications;return true;});});
        return entered && done;
    }
};
struct LiveNativeScene {enum class Phase {FirstSubmit,FinalRetired};unsigned device=0x1004cd00;Phase phase=Phase::FirstSubmit;};
std::optional<LiveNativeScene> liveNativeScene;
std::unique_ptr<Target> pendingNativeSceneTarget;
recomp::gpu::NativeCompletedSceneTarget<Target,Engine> completedNativeSceneTarget;
Engine* inlineGraphics=nullptr;
Registry* inlineRegistry=nullptr;
void* activeEndQueue=nullptr;void* activeSceneQueue=nullptr;
std::atomic<unsigned> tileRecordingDevice{0};
std::function<bool(const std::function<void()>&)> originalRecordingGate;
void rendererLog(const char*,...) {}
#include "../renderer_runtime_maintenance.inc"
}
int main() {
    Engine engine,foreign;Registry registry;
    inlineGraphics=&engine;inlineRegistry=&registry;
    originalRecordingGate=[](const auto& f){f();return true;};
    assert(!submitRendererRuntimeMaintenance(100) && !registry.calls);
    liveNativeScene.emplace();pendingNativeSceneTarget=std::make_unique<Target>();
    assert(!handoffRendererCompletedSceneTarget());
    liveNativeScene->phase=LiveNativeScene::Phase::FinalRetired;
    activeEndQueue=&registry;assert(!handoffRendererCompletedSceneTarget());activeEndQueue=nullptr;
    activeSceneQueue=&registry;assert(!handoffRendererCompletedSceneTarget());activeSceneQueue=nullptr;
    auto* original=pendingNativeSceneTarget.get();assert(handoffRendererCompletedSceneTarget());
    assert(!pendingNativeSceneTarget && completedNativeSceneTarget.target(&engine)==original && !destroyed);
    assert(!handoffRendererCompletedSceneTarget());
    tileRecordingDevice=0x1004cd00;
    assert(!submitRendererRuntimeMaintenance(100) && !registry.calls); // Other recording state not released yet.
    tileRecordingDevice=0;
    assert(!liveNativeScene && !pendingNativeSceneTarget && !destroyed);
    assert(submitRendererRuntimeMaintenance(100));
    assert(registry.device==0x1004cd00 && registry.word==100 && registry.publications==1 && engine.calls==1);
    assert(submitRendererRuntimeMaintenance(103) && registry.publications==2);
    unsigned rejected=0;
    const auto reject=[&]{const auto n=registry.calls;assert(!submitRendererRuntimeMaintenance(106));assert(registry.calls==n);++rejected;};
    activeEndQueue=&registry;reject();activeEndQueue=nullptr;
    activeSceneQueue=&registry;reject();activeSceneQueue=nullptr;
    tileRecordingDevice=0x1004cd00;reject();tileRecordingDevice=0;
    liveNativeScene.emplace();reject();liveNativeScene.reset();
    pendingNativeSceneTarget=std::make_unique<Target>();reject();
    assert(!completedNativeSceneTarget.retain(0x1004cd00,&engine,pendingNativeSceneTarget));
    pendingNativeSceneTarget.reset();assert(destroyed==1);
    inlineGraphics=&foreign;reject();inlineGraphics=nullptr;reject();inlineGraphics=&engine;
    inlineRegistry=nullptr;reject();inlineRegistry=&registry;
    // A moved-from std::function need not be empty (libc++ keeps this lambda).
    auto gate=originalRecordingGate;originalRecordingGate={};reject();originalRecordingGate=std::move(gate);
    registry.accept=false;assert(!submitRendererRuntimeMaintenance(106) && engine.calls==2);registry.accept=true;
    engine.succeed=false;assert(!submitRendererRuntimeMaintenance(106) && registry.publications==2);engine.succeed=true;
    completedNativeSceneTarget.reset();assert(destroyed==2 && !completedNativeSceneTarget.device());reject();
    assert(!completedNativeSceneTarget.target(&engine));
    pendingNativeSceneTarget=std::make_unique<Target>();
    assert(!completedNativeSceneTarget.retain(0,&engine,pendingNativeSceneTarget));
    assert(!completedNativeSceneTarget.retain(1,nullptr,pendingNativeSceneTarget));
    assert(pendingNativeSceneTarget && !completedNativeSceneTarget.device());
    liveNativeScene.emplace();liveNativeScene->phase=LiveNativeScene::Phase::FinalRetired;
    assert(handoffRendererCompletedSceneTarget());
    assert(submitRendererRuntimeMaintenance(109));completedNativeSceneTarget.reset();assert(destroyed==3);
    std::printf("RENDERER_RUNTIME_LIFECYCLE_PASS production_dispatch=1 recording_destroyed=1 target_retained=1 stale_owner_rejected=1 new_generation=1 admission_negatives=%u GPU=0\n",rejected);
}
