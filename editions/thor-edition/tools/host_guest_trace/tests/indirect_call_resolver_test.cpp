#include "guest_execution_guard.h"
#include "indirect_call_resolver.h"
#include "ppc_context.h"

#include <cstddef>
#include <cstdint>
#include <string>

namespace {

void first(PPCContext& context, std::uint8_t*) {
    context.marker = 1;
}

void second(PPCContext& context, std::uint8_t*) {
    context.marker = 2;
}

bool expect(bool condition) {
    return condition;
}

} // namespace

PPCFuncMapping PPCFuncMappings[] = {
        {0x1000, first},
        {0x1004, second},
        {0, nullptr},
};

int main() {
    std::size_t count = 0;
    if (!expect(recomp::bootstrap::initializeGuestIndirectCallResolver(2, count)
            && count == 2)) {
        return 1;
    }

    PPCContext context{};
    context.lr = 0xABCDEF0000001010ULL; context.r1.u32 = 0x6000;
    context.r3.u32 = 0x7000; context.r5.u32 = 0x8000; context.r26.u32 = 0x9000;
    if (recomp::bootstrap::stoppedGuestIndirectCall()) return 13;
    if (!recomp::bootstrap::hasExactGuestFunction(0x1000)
            || !recomp::bootstrap::hasExactGuestFunction(0x1004)
            || recomp::bootstrap::hasExactGuestFunction(0)
            || recomp::bootstrap::hasExactGuestFunction(0x1001)
            || recomp::bootstrap::hasExactGuestFunction(0x1008)) {
        return 11;
    }
    std::uint8_t base[1]{};
    recomp::bootstrap::GuestExecutionGuard guard;
    const auto resolved = guard.run(1, [&] {
        recomp_host_guest_call_indirect(0x1004, context, base);
    });
    if (!expect(resolved.completed() && context.marker == 2)) {
        return 2;
    }
    if (recomp::bootstrap::stoppedGuestIndirectCall()) return 14;

    const auto missing = guard.run(1, [&] {
        recomp_host_guest_call_indirect(0x1008, context, base);
    });
    if (!expect(missing.reason
                    == recomp::bootstrap::GuestExecutionStopReason::InvalidIndirectTarget
            && missing.detail == "Generated guest indirect target is not mapped"
            && missing.detail.find("1008") == std::string::npos
            && missing.detail.find("0x") == std::string::npos)) {
        return 3;
    }

    const auto failed = recomp::bootstrap::stoppedGuestIndirectCall();
    if (!failed || failed->target != 0x1008 || failed->linkRegister != 0x1010 || failed->stackPointer != 0x6000) return 15;
    if (failed->registers[0] != 0x7000 || failed->registers[2] != 0x8000 || failed->registers[6] != 0x9000) return 19;
    context.r3.u32 = 0;
    (void)guard.run(1,[&] { recomp_host_guest_call_indirect(0,context,base); });
    if (recomp::bootstrap::stoppedGuestIndirectCall()->target != 0x1008) return 16;
    if (recomp::bootstrap::stoppedGuestIndirectCall()->registers[0] != 0x7000) return 20;
    recomp::bootstrap::resetGuestIndirectCallResolver();
    if (recomp::bootstrap::stoppedGuestIndirectCall()) return 17;
    if (recomp::bootstrap::hasExactGuestFunction(0x1000)) { return 12; }
    const auto uninitialized = guard.run(1, [&] {
        recomp_host_guest_call_indirect(0x1000, context, base);
    });
    if (!expect(uninitialized.reason
            == recomp::bootstrap::GuestExecutionStopReason::InvalidIndirectTarget)) {
        return 4;
    }

    PPCFuncMappings[1].guest = 0x1000;
    if (!expect(!recomp::bootstrap::initializeGuestIndirectCallResolver(2, count))) {
        return 5;
    }
    if (recomp::bootstrap::stoppedGuestIndirectCall()) return 18;
    PPCFuncMappings[1].guest = 0x1002;
    if (!expect(!recomp::bootstrap::initializeGuestIndirectCallResolver(2, count))) {
        return 6;
    }
    PPCFuncMappings[1].guest = 0x1100;
    if (!expect(!recomp::bootstrap::initializeGuestIndirectCallResolver(2, count))) {
        return 7;
    }
    PPCFuncMappings[1] = {0x1004, nullptr};
    if (!expect(!recomp::bootstrap::initializeGuestIndirectCallResolver(2, count))) {
        return 8;
    }
    PPCFuncMappings[1] = {0x1004, second};
    PPCFuncMappings[2] = {4, nullptr};
    if (!expect(!recomp::bootstrap::initializeGuestIndirectCallResolver(2, count))) {
        return 9;
    }
    if (!expect(!recomp::bootstrap::initializeGuestIndirectCallResolver(
                recomp::bootstrap::MaximumGuestFunctionMappings + 1, count))) {
        return 10;
    }
    return 0;
}
