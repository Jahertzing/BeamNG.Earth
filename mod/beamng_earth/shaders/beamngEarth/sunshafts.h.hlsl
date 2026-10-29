// BeamNG.Earth sun shafts (lua/ge/extensions/beamngEarth/fidelity.lua): shared by the three passes,
// sunshaftsMask / sunshaftsBlur / sunshaftsApply.cs.hlsl. Each pass declares its own textures, then includes this.
//
//   bneSunDir   xyz: the direction to the sun (world, unit), w: the effect's strength from the sun's height (0 = off)
//   bneShaft    x: intensity, y: decay per tap, z: the blur's length (share of the way to the sun), w: falloff power
//   bneShaft2   x: sky radiance cap (the sun's disc compressed to it), y: taps, z: debug (1 = show mask and sun), w: unused
// A pass starts with `#define PFX_COMPUTE 1`, includes shaders/common/postFx/postFx.h.hlsl, declares its textures, then
// includes this.
cbuffer perDraw : register(b0)
{
    uniform float4 bneSunDir;
    uniform float4 bneShaft;
    uniform float4 bneShaft2;

    POSTFX_UNIFORMS
};

#include "shaders/common/postFx/postFx.hlsl"
#include "shaders/common/postFx/postFxCompute.h.hlsl"

static const float3 BNE_LUMA = float3(0.2126f, 0.7152f, 0.0722f);

// The sun's place on screen (viewport uv, top left 0 0) from the dispatch's frustum rays: the eye ray of a viewport
// point is eyeRayTopLeft + eyeRayRight * u + eyeRayDown * v, a plane in front of the eye; the sun's ray meets it at t.
// False when the sun is behind the camera.
bool bneSunUv(out float2 sunUv)
{
    sunUv = float2(0.5f, 0.5f);
    const float3 s = bneSunDir.xyz;
    const float3 n = cross(eyeRayRight, eyeRayDown);
    const float denom = dot(s, n);
    if (abs(denom) < 1e-6f) {
        return false;
    }
    const float t = dot(eyeRayTopLeft, n) / denom;
    if (t <= 0.0f) {
        return false;
    }
    const float3 p = s * t - eyeRayTopLeft;
    sunUv = float2(dot(p, eyeRayRight) / max(dot(eyeRayRight, eyeRayRight), 1e-12f),
                   dot(p, eyeRayDown) / max(dot(eyeRayDown, eyeRayDown), 1e-12f));
    return true;
}

// Interleaved gradient noise (Jimenez 2014): a per-pixel offset for the blur's first tap, so the taps' steps don't
// show as rings.
float bneNoise(float2 pixel)
{
    return frac(52.9829189f * frac(dot(pixel, float2(0.06711056f, 0.00583715f))));
}
