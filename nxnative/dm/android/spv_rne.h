// Maxwell's conversion of a fragment shader's float output to an 8-bit UNORM target, emulated where the GPU rounds a tie the other way. A value exactly
// halfway between two codes goes to the even code on the Switch (as on NVIDIA's and Apple's GPUs, and as Direct3D specifies); Qualcomm's Adreno stores the
// odd one, and Vulkan leaves the choice to the GPU. Hedgehog Engine 2's G-buffer writes a material flag byte into the alpha of an R8G8B8A8_UNORM target as
// (flags + 0.5) / 255, flag bits below the halfway point on purpose, and its deferred lighting decodes it as uint(alpha * 255): read back with the tie rounded
// up, every pixel's bit 0 is set, the lighting skips the light-probe (light field) ambient, and the game's world draws black (Sonic Frontiers, Sonic Forces,
// Sonic X Shadow Generations). The same rounding decides the passes of other engines that lean on it.
// The rewrite: before every OpStore of a float colour output at one of the mask's locations the value becomes roundEven(v * 255) / 255. Every value that is
// no tie converts to the code it did before (the stored code is exactly c then, whatever the GPU's rule), and a tie converts to the even code Maxwell picks:
// the shader, not the GPU, decides the tie. A masked output that is no float scalar, vec2, vec3 or vec4 is no normalized colour target and is left alone (the
// others are still rounded); one that something reads, copies or hands on rather than stores leaves the whole module as it is (SPV_RNE_KEPT), as does a module
// that is no fragment shader.
// mask: the Locations (under 32) of the outputs to round, which the caller takes from the pass's attachment formats. Self-contained (no libc), so that
// tests/test_spv_rne.py builds it alone. Backport: mymes1/droidplay-core e93661d0;
// corrected here to use OpExtInst GLSL.std.450 RoundEven, not opcode 46.
#pragma once
#include <stddef.h>
#include <stdint.h>

enum { SPV_RNE_DONE = 1, SPV_RNE_KEPT = 2 };   // rewritten; a masked output it does not take: the module as it is
#define SPV_RNE_SITE 16u   // words a store adds: multiply, GLSL.std.450 RoundEven, multiply
#define SPV_RNE_DECL 8u    // the two float constants the rewrite reads: 255 and 1 / 255
#define SPV_RNE_F255 0x437f0000u   // 255.0f
#define SPV_RNE_INV255 0x3b808081u   // 1 / 255, as float

// w[n]: the module. scratch: 3 × bound words (w[3]): the Locations decorated, the pointers' pointees, and the masked outputs' value types. o[cap]: the
// result. → its word count, or 0 when the module is left as it is (*need 0; *what SPV_RNE_KEPT for a masked output the rewrite does not take), or o is NULL
// or smaller than *need (call again with that much)
static size_t spv_rne(const uint32_t *w, size_t n, uint32_t mask, uint32_t *scratch, uint32_t *o, size_t cap, size_t *need, int *what) {
    *need = 0; *what = 0;
    if (n < 5 || w[0] != 0x07230203 || !mask) return 0;
    uint32_t bound = w[3], *loc = scratch, *ptr = scratch + bound, *ovt = scratch + 2 * bound;   // loc: a Location decoration's id; ptr: a pointer type's pointee; ovt: a colour output's value type
    for (uint32_t i = 0; i < 3 * bound; i++) scratch[i] = 0;
    uint32_t ext = 0;
    uint32_t ft = 0, fv[8] = {0}, nfv = 0;
    int frag = 0, fn = 0, bad = 0, sites = 0, memory_model = 0;
#define SRN_ID(x) ((x) < bound)
    for (size_t i = 5; i < n; ) {
        uint32_t len = w[i] >> 16, op = w[i] & 0xffff; const uint32_t *x = w + i;
        if (!len || i + len > n) return 0;
        if (!fn && op == 11 && len == 6 && x[2] == 0x4c534c47 && x[3] == 0x6474732e && x[4] == 0x3035342e && x[5] == 0) ext = x[1];
        if (!fn && op == 14) memory_model = 1;
        if (op == 54) fn = 1;   // OpFunction: the globals are behind us
        else if (!fn && op == 15 && len >= 3 && x[1] == 4) frag = 1;   // OpEntryPoint Fragment
        else if (!fn && op == 22 && len == 3 && x[2] == 32 && SRN_ID(x[1])) ft = x[1];   // OpTypeFloat 32
        else if (!fn && op == 23 && len == 4 && ft && x[2] == ft && x[3] >= 2 && x[3] <= 4 && SRN_ID(x[1]) && nfv < 8) fv[nfv++] = x[1];   // OpTypeVector of that float
        else if (!fn && op == 32 && len == 4 && SRN_ID(x[1]) && SRN_ID(x[3])) ptr[x[1]] = x[3];   // OpTypePointer: its pointee
        else if (!fn && op == 71 && len == 4 && x[2] == 30 && SRN_ID(x[1]) && x[3] < 32 && (mask >> x[3] & 1)) loc[x[1]] = 1;   // OpDecorate id Location n
        else if (!fn && op == 59 && len >= 4 && x[3] == 3 && SRN_ID(x[2]) && loc[x[2]]) {   // OpVariable Output at a masked location
            uint32_t t = SRN_ID(x[1]) ? ptr[x[1]] : 0;
            int vec = 0; for (uint32_t q = 0; q < nfv; q++) vec |= t == fv[q];
            if (t == ft || vec) ovt[x[2]] = t;   // (an integer or a composite is no normalized colour target: left alone)
        }
        else if (fn && op == 62 && len >= 3 && SRN_ID(x[1]) && ovt[x[1]]) sites++;   // OpStore to one
        else if (fn && op == 61 && len >= 4 && SRN_ID(x[3]) && ovt[x[3]]) bad = 1;   // OpLoad of one: a read, not a write the rewrite can round
        else if (fn && (op == 63 || op == 64) && len >= 3 && ((SRN_ID(x[1]) && ovt[x[1]]) || (SRN_ID(x[2]) && ovt[x[2]]))) bad = 1;   // OpCopyMemory, OpCopyMemorySized
        else if (fn && op == 228 && len >= 2 && SRN_ID(x[1]) && ovt[x[1]]) bad = 1;   // OpAtomicStore
        else if (fn && (op == 65 || op == 66 || op == 67 || op == 83) && len >= 4 && SRN_ID(x[3]) && ovt[x[3]]) bad = 1;   // access chain / pointer copy: cannot safely round component stores
        else if (fn && op == 57) { for (uint32_t q = 4; q < len; q++) if (SRN_ID(x[q]) && ovt[x[q]]) bad = 1; }   // OpFunctionCall: a pointer handed on
        i += len;
    }
    if (!frag || !ft || !memory_model) return 0;
    if (bad) { *what = SPV_RNE_KEPT; return 0; }
    if (!sites) return 0;   // nothing writes a masked output: the module as it is
    *what = SPV_RNE_DONE;
    *need = n + SPV_RNE_DECL + (ext ? 0 : 6) + (size_t)SPV_RNE_SITE * (size_t)sites;
    if (!o || cap < *need) return 0;
    uint32_t nb = bound, c255 = nb++, cinv = nb++, placed = 0, add_ext = !ext;
    if (add_ext) ext = nb++;
    size_t k = 0; int inf = 0;   // pass 1 counts the stores inside functions: so does this one, so that the buffer it sized holds them
    for (size_t i = 0; i < 5; i++) o[k++] = w[i];
    o[3] = nb + 3 * (uint32_t)sites;   // the two constants and three results a store
    for (size_t i = 5; i < n; ) {
        uint32_t len = w[i] >> 16, op = w[i] & 0xffff; const uint32_t *x = w + i;
        if (op == 14 && add_ext) {   // extended instruction imports precede OpMemoryModel
            o[k++] = (6u << 16) | 11; o[k++] = ext;
            o[k++] = 0x4c534c47; o[k++] = 0x6474732e; o[k++] = 0x3035342e; o[k++] = 0;
            add_ext = 0;
        }
        if (op == 54 && !placed) {   // before the first function, behind the globals
            o[k++] = (4u << 16) | 43; o[k++] = ft; o[k++] = c255; o[k++] = SPV_RNE_F255;   // OpConstant float 255
            o[k++] = (4u << 16) | 43; o[k++] = ft; o[k++] = cinv; o[k++] = SPV_RNE_INV255;
            placed = 1;
        }
        if (op == 54) inf = 1;
        if (inf && op == 62 && len >= 3 && SRN_ID(x[1]) && ovt[x[1]]) {   // the value through roundEven(v * 255) / 255 before it is stored
            uint32_t vt = ovt[x[1]], t1 = nb++, t2 = nb++, t3 = nb++, mul = vt == ft ? 133u : 142u;   // OpFMul, or OpVectorTimesScalar
            o[k++] = (5u << 16) | mul; o[k++] = vt; o[k++] = t1; o[k++] = x[2]; o[k++] = c255;
            o[k++] = (6u << 16) | 12; o[k++] = vt; o[k++] = t2; o[k++] = ext; o[k++] = 2; o[k++] = t1;   // OpExtInst GLSL.std.450 RoundEven (2), NOT core opcode 46 (OpConstantNull)
            o[k++] = (5u << 16) | mul; o[k++] = vt; o[k++] = t3; o[k++] = t2; o[k++] = cinv;
            o[k++] = (len << 16) | 62; o[k++] = x[1]; o[k++] = t3;   // OpStore of the rounded value, its memory access operands as they were
            for (uint32_t q = 3; q < len; q++) o[k++] = x[q];
        } else {
            for (uint32_t q = 0; q < len; q++) o[k++] = w[i + q];
        }
        i += len;
    }
    return k;
}
#undef SRN_ID
