#include "guest_vmx_half.h"
#include <cassert>
#include <cstdint>
#include <cstdio>

using namespace recomp::vmx;

int main() {
    // Xenia instruction fixtures, reversed to the PPCVRegister host layout.
    std::uint32_t src[4]{0xbf800000, 0x3f800000, 0xbf000000, 0x3f000000};
    std::uint32_t dst[4]{0xcdcdcdcd, 0xcdcdcdcd, 0xcdcdcdcd, 0xcdcdcdcd};
    packHalf<4, 2, 0>(dst, src);
    assert(dst[0] == 0x3c00bc00 && dst[1] == 0x3800b800);
    assert(dst[2] == 0xcdcdcdcd && dst[3] == 0xcdcdcdcd);
    packHalf<4, 2, 2>(src, src);
    assert(src[3] == 0x3800b800 && src[2] == 0x3c00bc00);
    assert(src[1] == 0x3f800000 && src[0] == 0xbf800000);
    std::uint32_t packed[4]{0x3802b803, 0x3800b801, 0xcdcdcdcd, 0xcdcdcdcd};
    unpackHalf<4>(packed, packed);
    assert(packed[3] == 0x3f000000 && packed[2] == 0xbf002000);
    assert(packed[1] == 0x3f004000 && packed[0] == 0xbf006000);
    std::uint32_t two[4]{0x3800b800, 0, 0, 0};
    unpackHalf<2>(two, two);
    assert(two[3] == 0x3f000000 && two[2] == 0xbf000000);
    assert(two[1] == 0 && two[0] == 0x3f800000);
    packHalf<2, 1, 0>(two, two);
    assert(two[0] == 0x3800b800);
    packHalf<4, 3, 3>(dst, packed);
    assert(dst[0] == 0x3800b801 && dst[1] == 0x3800b800);

    // Truncation (not nearest), finite saturation, gradual half underflow,
    // signed zeros, infinity and preserved/quieted NaN payloads.
    assert(floatToHalfRtz(0x3f803fff) == 0x3c01);
    assert(floatToHalfRtz(0xbf803fff) == 0xbc01);
    assert(floatToHalfRtz(0x47800000) == 0x7bff);
    assert(floatToHalfRtz(0xc7800000) == 0xfbff);
    assert(floatToHalfRtz(0x7f800000) == 0x7c00);
    assert(floatToHalfRtz(0xff800000) == 0xfc00);
    assert(floatToHalfRtz(0x7f800001) == 0x7e00);
    assert(floatToHalfRtz(0x80000000) == 0x8000);
    assert(floatToHalfRtz(0x33800000) == 1);
    assert(floatToHalfRtz(0x337fffff) == 0);
    assert(floatToHalfRtz(0x387fffff) == 0x3ff);
    assert(floatToHalfRtz(0x38800000) == 0x400);
    for (std::uint32_t h = 0; h != 65536; ++h) {
        const auto expected = ((h & 0x7c00) == 0x7c00 && (h & 0x3ff)) ? h | 0x200 : h;
        assert(floatToHalfRtz(halfToFloat(static_cast<std::uint16_t>(h))) == expected);
    }
    std::puts("VMX half pack/unpack: fixtures, aliasing, boundaries and half round-trips PASS");
}
