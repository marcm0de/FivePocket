// turnip_choice.h - which Vulkan driver the game process uses (lead 2026-11-24: Turnip's default-on path, debug test copy first).
//
// Turnip (Mesa's open Adreno driver, packaged in the app) is the default on the Adreno 730 only: the Snapdragon 8 Gen 1 (SM8450)
// and 8+ Gen 1 (SM8475), board platform "taro". Every other GPU stays on the system (Qualcomm) driver, as before.
//   files/private-trace/diag-turnip-off  forces the system driver (wins over everything);
//   files/private-trace/diag-turnip      forces Turnip on any GPU (testing; the switch the Turnip test copies used).
// Pure: no I/O. The loader (private_trace_jni.cpp) reads the two switches and the two system properties and logs the answer.
#pragma once
#include <cstring>

namespace turnip_choice {

struct Choice {
    bool turnip;
    const char* reason;   // off-switch | on-switch | default-a730 | other-gpu
};

// "SM8450", "SM8475" (also with a letter suffix, e.g. "SM8475P"); not "SM84750".
inline bool adreno730Soc(const char* soc) {
    if (!soc) return false;
    static const char* const kSocs[] = {"SM8450", "SM8475"};
    for (const char* known : kSocs) {
        const std::size_t n = std::strlen(known);
        if (std::strncmp(soc, known, n) == 0 && !(soc[n] >= '0' && soc[n] <= '9')) return true;
    }
    return false;
}

// soc: ro.soc.model (Android 12+); platform: ro.board.platform, the fallback when soc is empty.
inline bool adreno730(const char* soc, const char* platform) {
    if (soc && *soc) return adreno730Soc(soc);
    return platform && std::strcmp(platform, "taro") == 0;
}

inline Choice decide(bool offSwitch, bool onSwitch, const char* soc, const char* platform) {
    if (offSwitch) return {false, "off-switch"};
    if (onSwitch) return {true, "on-switch"};
    if (adreno730(soc, platform)) return {true, "default-a730"};
    return {false, "other-gpu"};
}

}  // namespace turnip_choice
