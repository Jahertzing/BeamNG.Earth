// Sun shafts, pass 3 of 3 (full resolution, HDR before tone mapping): the blurred light added over the scene, faded
// as the sun leaves the screen. Debug (bneShaft2.z): the mask instead of the scene and a ring on the sun's place.
#define PFX_COMPUTE 1

#include "shaders/common/postFx/postFx.h.hlsl"

uniform_sampler2D(backBuffer, 0);
uniform_sampler2D(shaftTex, 1);

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
    const float4 src = tex2D(backBuffer, viewportCoordToRenderTarget(uv, rtParams0));
    float2 sunUv;
    const bool inFront = bneSunUv(sunUv);
    if (bneSunDir.w <= 0.0f || !inFront) {
        pfxOut[storePos] = src;
        return;
    }
    // full strength while the sun is on screen, gone half a screen past its edge
    const float2 off = max(abs(sunUv - 0.5f) - 0.5f, 0.0f);
    const float onScreen = 1.0f - smoothstep(0.0f, 0.5f, max(off.x, off.y));
    const float3 shafts = tex2D(shaftTex, viewportCoordToRenderTarget(uv, rtParams1)).rgb;
    float3 c = src.rgb + shafts * (bneShaft.x * bneSunDir.w * onScreen);
    if (bneShaft2.z > 0.5f) {
        c = shafts;
        float2 d = (uv - sunUv) * float2(dispatchRect.z / max(dispatchRect.w, 1.0f), 1.0f);
        const float r = length(d);
        if (abs(r - 0.03f) < 0.003f) {
            c = float3(8.0f, 0.0f, 0.0f);
        }
    }
    pfxOut[storePos] = float4(c, src.a);
}
