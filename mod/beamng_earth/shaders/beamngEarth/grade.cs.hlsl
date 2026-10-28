// BeamNG.Earth colour grade (lua/ge/extensions/beamngEarth/grade.lua). One compute pass on the tone mapped
// image (PFXAfterBin / AfterPostFX, where smaa.lua's T2x pass runs for the same reason): BeamNG 0.40's
// combine pass (shaders/common/postFx/hdr/combinePass.cs.hlsl main_coconut) no longer applies the stock
// colour-correction ramp or the HSL factors, so the level had no grade at all.
//
// In order, on display-referred colour (sRGB encoded 0..1):
//   1. white balance: per-channel gains in linear light, normalised to keep the luminance;
//   2. saturation (around the luminance) and vibrance (more for the least saturated colours: the aerial
//      photos' washed-out greens and browns), in linear light;
//   3. contrast: a curve through (pivot, pivot) in encoded space that keeps 0 and 1 (no clipping);
//   4. split tone: shadows pulled towards one tint, highlights towards another (encoded space);
//   5. vignette; then the whole result blended with the input by `strength`.
#define PFX_COMPUTE 1

#include "shaders/common/postFx/postFx.h.hlsl"

uniform_sampler2D(backBuffer, 0);

cbuffer perDraw : register(b0)
{
    uniform float4 gradeBalance;     // rgb: white-balance gains (linear), a: strength 0..1
    uniform float4 gradeTone;        // x: contrast (1 = none), y: pivot (encoded), z: saturation, w: vibrance
    uniform float4 gradeShadows;     // rgb: shadow tint (encoded, 0.5 0.5 0.5 = none), a: amount
    uniform float4 gradeHighlights;  // rgb: highlight tint (as above), a: amount
    uniform float4 gradeMisc;        // x: vignette amount, y: vignette radius, z: share of the contrast below the pivot, w: unused

    POSTFX_UNIFORMS
};

#include "shaders/common/postFx/postFx.hlsl"
#include "shaders/common/postFx/postFxCompute.h.hlsl"

static const float3 LUMA = float3(0.2126f, 0.7152f, 0.0722f);

float3 srgbToLinear(float3 c)
{
    c = saturate(c);
    return lerp(c / 12.92f, pow((c + 0.055f) / 1.055f, 2.4f), step(0.04045f, c));
}

float3 linearToSrgb(float3 c)
{
    c = max(c, 0.0f);
    return lerp(c * 12.92f, 1.055f * pow(c, 1.0f / 2.4f) - 0.055f, step(0.0031308f, c));
}

// A contrast curve through (0, 0), (pivot, pivot) and (1, 1): a power on each side of the pivot, so nothing
// clips; above the pivot the slope there is `contrast`, below it `contrastLo` (a gentler toe keeps dusk and
// shadowed streets readable instead of crushing them to black).
float contrastCurve(float x, float pivot, float contrast, float contrastLo)
{
    x = saturate(x);
    if (x < pivot) {
        return pivot * pow(x / max(pivot, 1e-4f), contrastLo);
    }
    const float hi = 1.0f - pivot;
    return 1.0f - hi * pow((1.0f - x) / max(hi, 1e-4f), contrast);
}

float3 grade(float3 encoded, float2 uv)
{
    // 1 + 2: linear light
    float3 lin = srgbToLinear(encoded);
    const float lumBefore = dot(lin, LUMA);
    lin *= gradeBalance.rgb;
    const float lumAfter = dot(lin, LUMA);
    lin *= lumAfter > 1e-6f ? lumBefore / lumAfter : 1.0f;

    const float lum = dot(lin, LUMA);
    const float maxC = max(lin.r, max(lin.g, lin.b));
    const float minC = min(lin.r, min(lin.g, lin.b));
    const float sat = maxC > 1e-5f ? (maxC - minC) / maxC : 0.0f;
    const float s = gradeTone.z * (1.0f + gradeTone.w * (1.0f - sat));
    lin = max(lum + (lin - lum) * s, 0.0f);

    // 3: contrast, per channel (keeps hues closer than a luminance-only curve at these strengths)
    float3 c = linearToSrgb(lin);
    const float lo = lerp(1.0f, gradeTone.x, saturate(gradeMisc.z));
    c = float3(contrastCurve(c.r, gradeTone.y, gradeTone.x, lo), contrastCurve(c.g, gradeTone.y, gradeTone.x, lo),
               contrastCurve(c.b, gradeTone.y, gradeTone.x, lo));

    // 4: split tone (soft light towards the tint, weighted by how dark / bright the pixel is)
    const float y = dot(c, LUMA);
    const float wS = gradeShadows.a * (1.0f - smoothstep(0.0f, 0.55f, y));
    const float wH = gradeHighlights.a * smoothstep(0.45f, 1.0f, y);
    c += (gradeShadows.rgb - 0.5f) * wS + (gradeHighlights.rgb - 0.5f) * wH;

    // 5: vignette (aspect-corrected distance from the centre)
    if (gradeMisc.x > 0.0f) {
        float2 d = uv - 0.5f;
        d.x *= dispatchRect.z / max(dispatchRect.w, 1.0f);
        const float r = length(d) / max(gradeMisc.y, 1e-3f);
        c *= 1.0f - gradeMisc.x * smoothstep(0.0f, 1.0f, saturate(r * r));
    }
    return saturate(c);
}

PFX_NUMTHREADS
void main(uint3 dispatchThreadID : SV_DispatchThreadID)
{
    uint2 storePos;
    if (!postFxComputeThread(dispatchThreadID, storePos))
    {
        return;
    }

    const float2 uvView = postFxComputeUv(dispatchThreadID);
    const float2 uv0 = viewportCoordToRenderTarget(uvView, rtParams0);
    const float4 src = tex2D(backBuffer, uv0);
    const float3 graded = grade(src.rgb, uvView);
    pfxOut[storePos] = float4(lerp(src.rgb, graded, saturate(gradeBalance.a)), src.a);
}
