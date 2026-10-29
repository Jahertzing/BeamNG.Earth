// Sun shafts, pass 2 of 3 (run twice, quarter resolution): a radial blur towards the sun's place on screen. The
// first run takes long steps with a decay (the shafts), the second short ones without (fills the gaps between the
// first one's taps). Each tap's weight decays by bneShaft.y; the sum is normalised by the weights, so the intensity
// is the apply pass's alone.
#define PFX_COMPUTE 1

#include "shaders/common/postFx/postFx.h.hlsl"

uniform_sampler2D(shaftSrc, 0);

#include "shaders/beamngEarth/sunshafts.h.hlsl"

PFX_NUMTHREADS
void main(uint3 dispatchThreadID : SV_DispatchThreadID)
{
    uint2 storePos;
    if (!postFxComputeThread(dispatchThreadID, storePos))
    {
        return;
    }
    const float2 uv = postFxComputeUv(dispatchThreadID);
    float2 sunUv;
    if (bneSunDir.w <= 0.0f || !bneSunUv(sunUv)) {
        pfxOut[storePos] = 0.0f;
        return;
    }
    const int taps = clamp((int)bneShaft2.y, 4, 64);
    const float2 step = (sunUv - uv) * bneShaft.z / (float)taps;
    float2 p = uv + step * bneNoise(float2(storePos));
    float w = 1.0f;
    float wSum = 0.0f;
    float3 sum = 0.0f;
    [loop] for (int i = 0; i < taps; ++i) {
        sum += tex2D(shaftSrc, viewportCoordToRenderTarget(saturate(p), rtParams0)).rgb * w;
        wSum += w;
        w *= bneShaft.y;
        p += step;
    }
    pfxOut[storePos] = float4(sum / max(wSum, 1e-6f), 1.0f);
}
