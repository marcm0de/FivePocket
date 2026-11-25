// turnip_probe.cpp (TURNIP PROBE TEST APP 2026-11-22; Fold debug test copy ONLY): libturnip_probe.so, the probe of turnip_probe.h plus
// the statically linked libadrenotools/linkernsbypass. It is a SEPARATE library on purpose: linkernsbypass has a load-time constructor
// (android_linker_ns.cpp resolve_linker_symbols(), walks the linker's code), so linking it into librecomp_private_debug.so would run it
// in every launch. The loader dlopens this library only when it chose Turnip (turnip_choice.h; private_trace_jni.cpp, RECOMP_TURNIP builds).
#include "turnip_probe.h"
extern "C" __attribute__((visibility("default"))) void recomp_turnip_probe_v1(const char* files) {
    if(!files)return;
    (void)turnip_probe::runIfAsked(files);
}
// TURNIP GAME A/B (qol-turnip2): the adrenotools loader handle for the game library's recomp_vk_loader_v1 (nullptr: system driver).
extern "C" __attribute__((visibility("default"))) void* recomp_turnip_open_v1(const char* files) {
    return files ? turnip_probe::runIfAsked(files) : nullptr;
}
