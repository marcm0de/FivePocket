#pragma once
// ppc_fused_fix.h (audit fix COR-05; applied to generated chunks by fused_fix.py, never by hand).
//
// The PowerPC multiply-add instructions round ONCE: the product is not rounded before the addend is added (AltiVec PEM
// vmaddfp / vnmsubfp, PowerPC ISA fmadd family; VMX128 vmaddfp128 / vmaddcfp128 / vnmsubfp128 are the same operations with
// other register fields). The generated text spells them as a multiply and then an add, and the chunks are compiled with
// -ffp-model=strict (no contraction), so the library rounds twice: FMUL then FADD. For a = 3f800001, c = 3f7ffffe,
// b = bf800000 the split form gives 0 and the fused form a8800000 (-2^-46).
//
// Comparator: IEEE 754 fused multiply-add as published, computed exactly by the test's rational oracle, and the Xenia
// test expectations (local checkout c9c8e483). NOT an Xbox 360 recording: none exists here. What a helper below does for
// NaN operands, and for denormals, is AArch64's behaviour and is reported by the test, not claimed as the console's.
//
// Each helper takes the operands in the instruction's own order (A, C, B): result = A*C + B (or the named variation).
// PPC_FUSED_FIX_OFF (compile-time): every helper is again the exact expression the generator wrote, so one flag gives the
// old code for an A/B build.
//
// Vector forms: the NEON intrinsic vfmaq_f32. Under the chunks' -ffp-model=strict it is a constrained fused operation, kept
// in order with every FPCR write exactly as the generated FMUL / FADD are, and the NDK compiler emits one FMLA for it
// (lane notes: ndk/helpers-ndk.txt). An inline-asm FMLA tied to memory was tried first: it is as exact, but the compiler
// then has to write the pending ctx registers back before each one (9 more stores per loop of the kernel).
// Scalar forms: inline asm, on purpose. LLVM selects FNMADD for fneg(fma(a, c, b)) - also when the fneg is a LATER guest
// instruction - and AArch64's FNMADD computes (-a*c) + (-b), whose exact zero has the other sign (-(1*1 + -1) gives +0
// where the PowerPC result is -0; reproduced with clang on this Mac). An asm result cannot be folded that way. The unused
// memory input ties each asm to the points that may write memory - every `msr fpcr` asm of ppc_context.h and every call -
// as vmx_native_arith.h does, so it is never moved across a change of FPCR.
#include "ppc_context.h"

#if (defined(__aarch64__) || defined(_M_ARM64)) && !defined(PPC_FUSED_FIX_OFF)
#include <arm_neon.h>

inline char ppc_fused_fpcr_token;   // never read or written: its address only orders the asm (see above)
#define PPC_FUSED_INLINE static inline __attribute__((always_inline))
#define PPC_FUSED_TOKEN "m"(ppc_fused_fpcr_token)

// vmaddfp / vmaddfp128 / vmaddcfp128: A*C + B, one rounding (FMLA)
PPC_FUSED_INLINE simde__m128 ppc_fused_vmaddfp(simde__m128 a, simde__m128 c, simde__m128 b)
{
    return vfmaq_f32(b, a, c);
}

// vnmsubfp / vnmsubfp128: -(A*C - B), one rounding. Not FMLS (B - A*C): that differs in the sign of an exact zero.
// The two sign flips are bit operations, as the generated xor was.
PPC_FUSED_INLINE simde__m128 ppc_fused_vnmsubfp(simde__m128 a, simde__m128 c, simde__m128 b)
{
    const uint32x4_t sign = vdupq_n_u32(0x80000000u);
    const float32x4_t t = vfmaq_f32(vreinterpretq_f32_u32(veorq_u32(vreinterpretq_u32_f32(b), sign)), a, c);
    return vreinterpretq_f32_u32(veorq_u32(vreinterpretq_u32_f32(t), sign));
}

// fmadd: A*C + B                              (AArch64 FMADD  d, n, m, a = n*m + a)
PPC_FUSED_INLINE double ppc_fused_fmadd(double a, double c, double b)
{
    double r;
    __asm__("fmadd %d0, %d1, %d2, %d3" : "=w"(r) : "w"(a), "w"(c), "w"(b), PPC_FUSED_TOKEN);
    return r;
}
// fmsub: A*C - B                              (AArch64 FNMSUB d, n, m, a = n*m - a)
PPC_FUSED_INLINE double ppc_fused_fmsub(double a, double c, double b)
{
    double r;
    __asm__("fnmsub %d0, %d1, %d2, %d3" : "=w"(r) : "w"(a), "w"(c), "w"(b), PPC_FUSED_TOKEN);
    return r;
}
// fnmadd: -(A*C + B), fnmsub: -(A*C - B): the fused value, then a sign flip (as the generated `-( ... )` was)
PPC_FUSED_INLINE double ppc_fused_fnmadd(double a, double c, double b)
{
    double r;
    __asm__("fmadd %d0, %d1, %d2, %d3\n\tfneg %d0, %d0" : "=&w"(r) : "w"(a), "w"(c), "w"(b), PPC_FUSED_TOKEN);
    return r;
}
PPC_FUSED_INLINE double ppc_fused_fnmsub(double a, double c, double b)
{
    double r;
    __asm__("fnmsub %d0, %d1, %d2, %d3\n\tfneg %d0, %d0" : "=&w"(r) : "w"(a), "w"(c), "w"(b), PPC_FUSED_TOKEN);
    return r;
}

// The single forms (fmadds, fmsubs, fnmadds, fnmsubs) round the exact value once, to single. The generated text computes
// in double and narrows. When all three operands hold single values - what single-precision guest code produces - the
// double product is exact, so the old text is already fused in double; what it still does is round twice (to 53 bits,
// then to 24), wrong by one unit only when the 53-bit value lands on a single-precision tie. The exact form: when the
// operands narrow without loss, one single-precision fused operation; otherwise (a double operand, a NaN) the fused
// double operation, narrowed - which keeps the double rounding for that rare case and is said so in the lane notes.
#define PPC_FUSED_SINGLE(NAME, INSN, TAIL, WIDE)                                                                          \
    PPC_FUSED_INLINE double NAME(double a, double c, double b)                                                            \
    {                                                                                                                     \
        const float fa = float(a), fc = float(c), fb = float(b);                                                          \
        if (double(fa) == a && double(fc) == c && double(fb) == b) [[likely]]                                             \
        {                                                                                                                 \
            float r;                                                                                                      \
            __asm__(INSN " %s0, %s1, %s2, %s3" TAIL : "=&w"(r) : "w"(fa), "w"(fc), "w"(fb), PPC_FUSED_TOKEN);             \
            return double(r);                                                                                             \
        }                                                                                                                 \
        return double(float(WIDE(a, c, b)));                                                                              \
    }
PPC_FUSED_SINGLE(ppc_fused_fmadds, "fmadd", "", ppc_fused_fmadd)
PPC_FUSED_SINGLE(ppc_fused_fmsubs, "fnmsub", "", ppc_fused_fmsub)
PPC_FUSED_SINGLE(ppc_fused_fnmadds, "fmadd", "\n\tfneg %s0, %s0", ppc_fused_fnmadd)
PPC_FUSED_SINGLE(ppc_fused_fnmsubs, "fnmsub", "\n\tfneg %s0, %s0", ppc_fused_fnmsub)
#undef PPC_FUSED_SINGLE
#undef PPC_FUSED_TOKEN
#undef PPC_FUSED_INLINE

#else
// PPC_FUSED_FIX_OFF, or a host that is not AArch64: the expressions exactly as the generator wrote them.
#define ppc_fused_vmaddfp(a, c, b) simde_mm_add_ps(simde_mm_mul_ps((a), (c)), (b))
#define ppc_fused_vnmsubfp(a, c, b) \
    simde_mm_xor_ps(simde_mm_sub_ps(simde_mm_mul_ps((a), (c)), (b)), simde_mm_castsi128_ps(simde_mm_set1_epi32(int(0x80000000))))
#define ppc_fused_fmadd(a, c, b) ((a) * (c) + (b))
#define ppc_fused_fmsub(a, c, b) ((a) * (c) - (b))
#define ppc_fused_fnmadd(a, c, b) (-((a) * (c) + (b)))
#define ppc_fused_fnmsub(a, c, b) (-((a) * (c) - (b)))
#define ppc_fused_fmadds(a, c, b) double(float((a) * (c) + (b)))
#define ppc_fused_fmsubs(a, c, b) double(float((a) * (c) - (b)))
#define ppc_fused_fnmadds(a, c, b) double(float(-((a) * (c) + (b))))
#define ppc_fused_fnmsubs(a, c, b) double(float(-((a) * (c) - (b))))
#endif
