#pragma once

#include <cstddef>
#include <cstdint>

struct PPCContext {
    std::uint32_t marker{0};
    std::uint64_t lr{0};
    struct { std::uint32_t u32{0}; } r1, r3, r4, r5, r6, r7, r11, r26, r31;
};

using PPCFunc = void(PPCContext& context, std::uint8_t* base);

struct PPCFuncMapping {
    std::size_t guest;
    PPCFunc* host;
};

extern PPCFuncMapping PPCFuncMappings[];
