// Sun shafts, pass 1 of 3 (quarter resolution): the light that can cast a shaft. Only sky pixels count (the scene in
// front of the sky is the occluder: buildings, trees, the hills), each weighted by how close it is to the sun's
// direction (bneShaft.w: a power of the cosine), its radiance capped so the sun's disc doesn't swamp the sky around it.
#define PFX_COMPUTE 1

#include "shaders/common/postFx/postFx.h.hlsl"

uniform_sampler2D(backBuffer, 0);
uniform_sampler2D(prepassTex, 1);
uniform_sampler2D(prepassDepthTex, 2);

#include "shaders/beamngEarth/sunshafts.h.hlsl"

float3 bneSkyTap(float2 uvView, float2 offset)
{
    const float2 uv = uvView + offset;
    const float depth = decodeGBuffer(TEX_SAMPLER(prepassDepthTex), TEX_SAMPLER(prepassTex),
                                      viewportCoordToRenderTarget(uv, rtParams2), projParams).w;
    if (depth < 0.99999f) {
        return 0.0f;
    }
    const float3 c = max(tex2D(backBuffer, viewportCoordToRenderTarget(uv, rtParams0)).rgb, 0.0f);
    const float lum = dot(c, BNE_LUMA);
    const float cap = max(bneShaft2.x, 1e-4f);
    return lum > cap ? c * (cap / lum) : c;
}

PFX_NUMTHREADS
void main(uint3 dispatchThreadID : SV_DispatchThreadID)
{
    uint2 storePos;
    if (!postFxComputeThread(dispatchThreadID, storePos))
    {
        return;
    }
    const float2 uv = postFxComputeUv(dispatchThreadID);
    if (bneSunDir.w <= 0.0f) {
        pfxOut[storePos] = 0.0f;
        return;
    }
    // four taps inside this quarter-resolution texel (it covers 4 x 4 full-resolution pixels)
    const float2 q = 0.25f / max(dispatchRect.zw, 1.0f);
    float3 c = bneSkyTap(uv, float2(-q.x, -q.y)) + bneSkyTap(uv, float2(q.x, -q.y)) + bneSkyTap(uv, float2(-q.x, q.y))
             + bneSkyTap(uv, float2(q.x, q.y));
    c *= 0.25f;
    const float3 ray = normalize(eyeRayTopLeft + eyeRayRight * uv.x + eyeRayDown * uv.y);
    const float w = pow(saturate(dot(ray, bneSunDir.xyz)), max(bneShaft.w, 1.0f));
    pfxOut[storePos] = float4(c * w, 1.0f);
}
