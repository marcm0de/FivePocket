// Tests for the precise-reservation overrides in guest_reservation_helpers.cpp.
// Needs the generated corpus's ppc_config.h/ppc_context.h (private; pass their directory):
//   G=local-analysis/android-private-trace-window/api26/validated-ppc
//   clang++ -std=c++20 -O2 -pthread -DRECOMP_GUEST_MEMORY_ATOMIC=1 -DSIMDE_ENABLE_NATIVE_ALIASES
//     -include tools/host_guest_trace/trace_prelude.h -I"$G" -Iandroid/app/src/main/cpp
//     -Itools/host_guest_trace -Itools/XenonRecomp/thirdparty/simde
//     tools/host_guest_trace/guest_reservation_helpers.cpp android/app/src/main/cpp/guest_execution_guard.cpp
//     tools/host_guest_trace/tests/guest_reservation_helpers_test.cpp
// The helpers compile to nothing unless a memory model or RECOMP_GUEST_PRECISE_RESERVATION=1
// is set, so build both files with the same defines. The positive control is XenonRecomp's
// value-only ldarx/stdcx. translation of the same two routines.
#include "trace_prelude.h"

#include "ppc_config.h"
#include "ppc_context.h"

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <random>
#include <set>
#include <string>
#include <thread>
#include <vector>

#include "guest_execution_guard.h"

PPC_EXTERN_FUNC(sub_836DE760);
PPC_EXTERN_FUNC(sub_836DE778);

// WP1's checkpoint contract (guest_parallel_wake.h), which the executor normally implements.
// This test links no executor, so it supplies the cooperative behaviour: attention is always
// set, and the cold path runs the per-entry guard checkpoint with the label bounded to 128 bytes.
extern "C" {
std::uint32_t recomp_guest_checkpoint_attention = 1;
void recomp_guest_execution_checkpoint_slow(std::int32_t* countdown, const char* label, std::size_t length) {
    if (countdown) *countdown = 0;
    recomp::bootstrap::GuestExecutionGuard::checkpoint(std::string_view(label, std::min<std::size_t>(length, 128)));
}
}

namespace {

alignas(4096) std::uint8_t arena[1 << 20];
int failures = 0;

void check(bool ok, const char* name, const std::string& detail = {}) {
    std::printf("%s %s%s%s\n", ok ? "PASS" : "FAIL", name, detail.empty() ? "" : " ", detail.c_str());
    if (!ok) ++failures;
}

using Helper = void (*)(PPCContext&, std::uint8_t*);
struct HelperPair {
    Helper loadReserved;
    Helper storeConditional;
    const char* name;
};

// XenonRecomp's translation of the two routines: the reservation is only the loaded value.
// The load is atomic here only so the stress test stays free of C++ data races; the
// generated form is a plain dereference with the same value semantics.
void generatedLoadReserved(PPCContext& __restrict ctx, std::uint8_t* base) {
    ctx.r5.u64 = ctx.msr;
    PPC_STORE_U64(ctx.r4.u32, ctx.r5.u64);
    ctx.msr = (ctx.r13.u32 & 0x8020) | (ctx.msr & ~0x8020);
    ctx.reserved.u64 = __atomic_load_n(reinterpret_cast<std::uint64_t*>(base + ctx.r3.u32), __ATOMIC_RELAXED);
    ctx.r3.u64 = __builtin_bswap64(ctx.reserved.u64);
}

void generatedStoreConditional(PPCContext& __restrict ctx, std::uint8_t* base) {
    ctx.cr0.lt = 0;
    ctx.cr0.gt = 0;
    ctx.cr0.eq = __sync_bool_compare_and_swap(reinterpret_cast<uint64_t*>(base + ctx.r3.u32), ctx.reserved.s64,
            __builtin_bswap64(ctx.r4.s64));
    ctx.cr0.so = ctx.xer.so;
    ctx.msr = (ctx.r5.u32 & 0x8020) | (ctx.msr & ~0x8020);
    ctx.r3.s64 = 1;
    if (ctx.cr0.eq) return;
    ctx.r3.s64 = 0;
}

const HelperPair precise{sub_836DE760, sub_836DE778, "precise"};
const HelperPair generated{generatedLoadReserved, generatedStoreConditional, "generated"};

// Guest layout. Node next pointers are at +0, as the pop's "lwz r8,0(r31)" reads them.
constexpr std::uint32_t Header = 0x100; // big-endian u64 {head:32 | depth:32}
constexpr std::uint32_t msrSlot(std::uint32_t thread) { return 0x1000 + thread * 0x80; }
constexpr std::uint32_t dummySlot(std::uint32_t thread) { return 0x1040 + thread * 0x80; }
constexpr std::uint32_t node(std::uint32_t index) { return 0x10000 + index * 0x40; }

std::uint64_t loadReserved(const HelperPair& pair, PPCContext& ctx, std::uint32_t address, std::uint32_t thread) {
    ctx.r3.u64 = address;
    ctx.r4.u64 = msrSlot(thread);
    pair.loadReserved(ctx, arena);
    return ctx.r3.u64;
}

bool storeConditional(const HelperPair& pair, PPCContext& ctx, std::uint32_t address, std::uint64_t value, std::uint32_t thread) {
    std::uint8_t* const base = arena;
    ctx.r3.u64 = address;
    ctx.r4.u64 = value;
    ctx.r5.u64 = PPC_LOAD_U64(msrSlot(thread)); // "ld r5,88(r1)" in the callers
    pair.storeConditional(ctx, arena);
    return (ctx.r3.u64 & 0xFF) != 0;
}

// Models the pop caller sub_834710D8: LL, empty check, head->next load, SC {next, depth-1}.
std::uint32_t pop(const HelperPair& pair, PPCContext& ctx, std::uint32_t thread,
        const std::function<void()>& betweenLoadAndStore = {}, unsigned* retries = nullptr) {
    std::uint8_t* const base = arena;
    for (;;) {
        const std::uint64_t value = loadReserved(pair, ctx, Header, thread);
        if (value == 0) { // empty: drop the reservation with a stdcx. to a stack slot
            (void)storeConditional(pair, ctx, dummySlot(thread), 0, thread);
            return 0;
        }
        const std::uint32_t head = static_cast<std::uint32_t>(value >> 32);
        const std::uint32_t depth = static_cast<std::uint32_t>(value);
        const std::uint32_t next = PPC_LOAD_U32(head);
        if (betweenLoadAndStore) betweenLoadAndStore();
        if (storeConditional(pair, ctx, Header, (std::uint64_t{next} << 32) | std::uint32_t(depth - 1), thread)) return head;
        if (retries) ++*retries;
    }
}

// Models the push caller sub_83471180 for one node: LL, node->next = head, SC {node, depth+1}.
void push(const HelperPair& pair, PPCContext& ctx, std::uint32_t thread, std::uint32_t pushed) {
    std::uint8_t* const base = arena;
    for (;;) {
        const std::uint64_t value = loadReserved(pair, ctx, Header, thread);
        PPC_STORE_U32(pushed, static_cast<std::uint32_t>(value >> 32));
        if (storeConditional(pair, ctx, Header, (std::uint64_t{pushed} << 32) | std::uint32_t(std::uint32_t(value) + 1), thread))
            return;
    }
}

void setStack(const std::vector<std::uint32_t>& nodes) {
    std::uint8_t* const base = arena;
    for (std::size_t i = 0; i < nodes.size(); ++i) PPC_STORE_U32(nodes[i], i + 1 < nodes.size() ? nodes[i + 1] : 0);
    PPC_STORE_U64(Header, nodes.empty() ? 0 : (std::uint64_t{nodes.front()} << 32) | nodes.size());
}

// Walks at most limit nodes. The positive control can corrupt the list into a cycle.
std::vector<std::uint32_t> stackContents(std::size_t limit, std::uint32_t& depth) {
    std::uint8_t* const base = arena;
    const std::uint64_t header = PPC_LOAD_U64(Header);
    depth = static_cast<std::uint32_t>(header);
    std::vector<std::uint32_t> nodes;
    for (std::uint32_t at = static_cast<std::uint32_t>(header >> 32); at != 0 && nodes.size() < limit; at = PPC_LOAD_U32(at))
        nodes.push_back(at);
    return nodes;
}

void callingConvention(const HelperPair& pair) {
    std::uint8_t* const base = arena;
    const std::string name = std::string("calling_convention_") + pair.name;
    PPCContext ctx{};
    PPC_STORE_U64(Header, 0x1122334455667788ULL);
    ctx.msr = 0x9030;         // EE (0x8000) and 0x20 set, other bits must survive
    ctx.r13.u64 = 0x40000000; // PCR-like value with bits 0x8020 clear, so mtmsrd r13,1 masks them
    ctx.xer.so = 1;
    const std::uint64_t loaded = loadReserved(pair, ctx, Header, 0);
    bool ok = loaded == 0x1122334455667788ULL && ctx.r5.u64 == 0x9030 && PPC_LOAD_U64(msrSlot(0)) == 0x9030 &&
            ctx.msr == 0x1010 && ctx.reserved.u64 == __builtin_bswap64(0x1122334455667788ULL);
    const bool stored = storeConditional(pair, ctx, Header, 0xCAFEF00DDEADBEEFULL, 0);
    ok = ok && stored && ctx.r3.u64 == 1 && ctx.cr0.lt == 0 && ctx.cr0.gt == 0 && ctx.cr0.eq == 1 && ctx.cr0.so == 1 &&
            ctx.msr == 0x9030 && PPC_LOAD_U64(Header) == 0xCAFEF00DDEADBEEFULL;
    check(ok, name.c_str(), "r3/r5/msr/save-slot/reserved/cr0 as generated");
}

void reservationSemantics() {
    std::uint8_t* const base = arena;
    PPCContext ctx{};
    PPC_STORE_U64(Header, 0x10);
    check(!storeConditional(precise, ctx, Header, 0x20, 0) && PPC_LOAD_U64(Header) == 0x10 && ctx.r3.u64 == 0 &&
            ctx.cr0.eq == 0, "sc_without_reservation_fails");

    (void)loadReserved(precise, ctx, Header, 0);
    const bool first = storeConditional(precise, ctx, Header, 0x20, 0);
    const bool second = storeConditional(precise, ctx, Header, 0x30, 0);
    check(first && !second && PPC_LOAD_U64(Header) == 0x20, "sc_consumes_reservation");

    (void)loadReserved(precise, ctx, Header, 0);
    const bool other = storeConditional(precise, ctx, Header + 0x800, 0x40, 0);
    const bool after = storeConditional(precise, ctx, Header, 0x40, 0);
    check(!other && !after && PPC_LOAD_U64(Header) == 0x20, "sc_other_address_fails_and_consumes");

    PPC_STORE_U64(Header + 0x800, 0x77);
    (void)loadReserved(precise, ctx, Header, 0);
    (void)loadReserved(precise, ctx, Header + 0x800, 0);
    const bool moved = storeConditional(precise, ctx, Header, 0x50, 0);
    check(!moved && PPC_LOAD_U64(Header) == 0x20, "second_ll_replaces_first");

    (void)loadReserved(precise, ctx, Header, 0);
    PPC_STORE_U64(Header, 0x21); // a plain store that changes the value
    check(!storeConditional(precise, ctx, Header, 0x60, 0) && PPC_LOAD_U64(Header) == 0x21, "plain_store_changed_value_fails");
    (void)loadReserved(precise, ctx, Header, 0);
    PPC_STORE_U64(Header, 0x21); // same value: plain stores publish no generation (documented limit)
    check(storeConditional(precise, ctx, Header, 0x61, 0) && PPC_LOAD_U64(Header) == 0x61, "plain_store_same_value_not_detected");

    // Another thread's successful store-conditional in the same 128-byte granule (different
    // word) kills the reservation, as on Xenon. One in another granule does not.
    for (const std::uint32_t otherWord : {Header + 8, Header + 0x100}) {
        PPC_STORE_U64(Header, 0x70);
        PPC_STORE_U64(otherWord, 0x1);
        (void)loadReserved(precise, ctx, Header, 0);
        std::thread([otherWord] {
            PPCContext peer{};
            (void)loadReserved(precise, peer, otherWord, 1);
            (void)storeConditional(precise, peer, otherWord, 0x2, 1);
        }).join();
        const bool stored = storeConditional(precise, ctx, Header, 0x71, 0);
        const bool sameGranule = otherWord == Header + 8;
        check(stored == !sameGranule, sameGranule ? "peer_sc_same_granule_kills_reservation" : "peer_sc_other_granule_keeps_reservation");
    }
}

void checkpointLabel() {
    std::vector<std::string> labels;
    recomp::bootstrap::GuestExecutionGuard guard;
    const auto result = guard.run(16, [] {
        PPCContext ctx{};
        (void)loadReserved(precise, ctx, Header, 0);
        (void)storeConditional(precise, ctx, Header, 0, 0);
    }, [&](std::string_view label) { labels.emplace_back(label); });
    check(result.completed() && labels == std::vector<std::string>{"__imp__sub_836DE760", "__imp__sub_836DE778"},
            "checkpoint_labels_match_generated", labels.size() == 2 ? labels[0] + "," + labels[1] : "");
}

// Deterministic ABA. T1 pops: reads {A,3} and next = B, then stops before its SC. T2 pops A,
// pops B, pushes X, pushes A. The header is {A,3} again, but A->next is now X and B belongs to T2.
void abaScenario(const HelperPair& pair) {
    const std::uint32_t a = node(0), b = node(1), c = node(2), x = node(3);
    setStack({a, b, c});
    std::atomic<int> phase{0};
    std::uint32_t t1Popped = 0, t2HeldB = 0;
    unsigned t1Retries = 0;
    std::thread t1([&] {
        PPCContext ctx{};
        bool first = true;
        t1Popped = pop(pair, ctx, 1, [&] {
            if (!first) return;
            first = false;
            phase.store(1, std::memory_order_release);
            while (phase.load(std::memory_order_acquire) != 2) {
            }
        }, &t1Retries);
    });
    std::thread t2([&] {
        while (phase.load(std::memory_order_acquire) != 1) {
        }
        PPCContext ctx{};
        const std::uint32_t poppedA = pop(pair, ctx, 2);
        t2HeldB = pop(pair, ctx, 2);
        push(pair, ctx, 2, x);
        push(pair, ctx, 2, poppedA);
        phase.store(2, std::memory_order_release);
    });
    t1.join();
    t2.join();
    std::uint32_t depth = 0;
    const auto contents = stackContents(8, depth);
    const bool correct = t1Popped == a && t2HeldB == b && contents == std::vector<std::uint32_t>{x, c} && depth == 2;
    const bool corrupted = std::find(contents.begin(), contents.end(), b) != contents.end() ||
            std::find(contents.begin(), contents.end(), x) == contents.end();
    char detail[160];
    std::snprintf(detail, sizeof detail, "t1_retries=%u list=%zu nodes depth=%u contains_B(owned_by_T2)=%d contains_X=%d",
            t1Retries, contents.size(), depth, int(std::find(contents.begin(), contents.end(), b) != contents.end()),
            int(std::find(contents.begin(), contents.end(), x) != contents.end()));
    if (&pair == &precise) {
        check(correct && t1Retries == 1, "aba_deterministic_precise", detail);
    } else {
        // Positive control: must reproduce the corruption, or the scenario proves nothing.
        check(corrupted && t1Retries == 0, "aba_deterministic_generated_control_corrupts", detail);
    }
}

void stress(const HelperPair& pair, bool hard) {
    constexpr std::uint32_t poolSize = 64;
    const unsigned threads = 8, operations = 200000;
    std::vector<std::uint32_t> pool;
    for (std::uint32_t i = 0; i < poolSize; ++i) pool.push_back(node(i));
    setStack(pool);
    std::vector<std::vector<std::uint32_t>> held(threads);
    std::vector<std::thread> workers;
    for (unsigned t = 0; t < threads; ++t) {
        workers.emplace_back([&, t] {
            PPCContext ctx{};
            std::mt19937 random(1000 + t);
            auto& mine = held[t];
            for (unsigned i = 0; i < operations; ++i) {
                if (mine.empty() || (random() & 1)) {
                    if (const std::uint32_t popped = pop(pair, ctx, t + 1)) mine.push_back(popped);
                } else {
                    push(pair, ctx, t + 1, mine.back());
                    mine.pop_back();
                }
            }
            while (!mine.empty()) {
                push(pair, ctx, t + 1, mine.back());
                mine.pop_back();
            }
        });
    }
    for (auto& worker : workers) worker.join();
    std::uint32_t depth = 0;
    const auto contents = stackContents(4 * poolSize, depth);
    const std::set<std::uint32_t> unique(contents.begin(), contents.end());
    const bool conserved = contents.size() == poolSize && unique.size() == poolSize && depth == poolSize &&
            std::all_of(contents.begin(), contents.end(), [&](std::uint32_t n) { return std::find(pool.begin(), pool.end(), n) != pool.end(); });
    char detail[128];
    std::snprintf(detail, sizeof detail, "threads=%u ops=%u list=%zu unique=%zu depth=%u", threads, operations,
            contents.size(), unique.size(), depth);
    const std::string name = std::string("stack_stress_") + pair.name;
    if (hard) {
        check(conserved, name.c_str(), detail);
    } else {
        std::printf("INFO %s %s %s\n", name.c_str(), conserved ? "conserved" : "CORRUPTED", detail);
    }
}

void counter() {
    std::uint8_t* const base = arena;
    constexpr std::uint32_t Counter = 0x300; // a different granule from Header
    PPC_STORE_U64(Counter, 0);
    const unsigned threads = 8, iterations = 100000;
    std::vector<std::thread> workers;
    for (unsigned t = 0; t < threads; ++t)
        workers.emplace_back([t] {
            PPCContext ctx{};
            for (unsigned i = 0; i < iterations; ++i)
                while (!storeConditional(precise, ctx, Counter, loadReserved(precise, ctx, Counter, t + 1) + 1, t + 1)) {
                }
        });
    for (auto& worker : workers) worker.join();
    const auto value = PPC_LOAD_U64(Counter);
    check(value == std::uint64_t{threads} * iterations, "llsc_counter_precise",
            "value=" + std::to_string(value) + " expected=" + std::to_string(std::uint64_t{threads} * iterations));
}

} // namespace

int main() {
    // As an updated guard maintains the split special ranges with nothing bound, so guest
    // accesses take the inline fast path (the header default sends them all to the cold path).
    recomp::bootstrap::guestScalarDataSlotLo = recomp::bootstrap::guestScalarDataSlotHi = 0;
    recomp::bootstrap::guestScalarUptimeLo = recomp::bootstrap::guestScalarUptimeHi = 0;
    callingConvention(precise);
    callingConvention(generated);
    reservationSemantics();
    checkpointLabel();
    abaScenario(precise);
    abaScenario(generated);
    counter();
    stress(precise, true);
    stress(generated, false);
    std::printf(failures ? "RESULT FAIL %d\n" : "RESULT PASS\n", failures);
    return failures ? 1 : 0;
}
