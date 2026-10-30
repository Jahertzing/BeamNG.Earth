// BeamNG.Earth weather on the ground (lua/ge/extensions/beamngEarth/weatherfx.lua): one full-resolution pass over the
// lit scene (HDR, before the fog and the translucent objects: the rain drops, the markings and the glass stay as they
// are), from the prepass's normals, roughness and depth.
//
//   wet     rough surfaces darken (water fills the pores), flat ones mirror the street through a short screen-space
//           ray march (a rough, jittered reflection the upscaler's temporal pass smooths; Fresnel: strong at grazing
//           angles, as a wet road in front of a car), puddles in the dips of a world-space noise mirror sharper, rings
//           in them while it rains. Glossy surfaces (car paint, metal, glass) are left alone.
//   snow    up-facing rough surfaces turn white in patches that close as it piles up; the snow is lit like the
//           surface it covers (its brightness from the scene's own, so shadows, night and street lights still hold).
//   flash   lightning: the sky and, less, the ground brighten for a moment.
//
//   bneWx0  x: wetness 0..1, y: puddles 0..1, z: snow cover 0..1, w: time (s)
//   bneWx1  xyz: the camera (world, for the noise), w: rain now 0..1 (the rings)
//   bneWx2  x: flash, y: reflection strength, z: reflections out to (m), w: debug (1: masks, wet r / snow g / puddle b)
//   bneWx3  x: snow gain (snow brightness / scene brightness), y: wet darkening (multiplier at full wet), z: sky flash
//           gain, w: ground flash gain
#define PFX_COMPUTE 1

#include "shaders/common/postFx/postFx.h.hlsl"

uniform_sampler2D(backBuffer, 0);
uniform_sampler2D(prepassTex, 1);
uniform_sampler2D(prepassDepthTex, 2);

cbuffer perDraw : register(b0)
{
    uniform float4 bneWx0;
    uniform float4 bneWx1;
    uniform float4 bneWx2;
    uniform float4 bneWx3;

    POSTFX_UNIFORMS
};

#include "shaders/common/postFx/postFx.hlsl"
#include "shaders/common/postFx/postFxCompute.h.hlsl"

static const float3 BNE_LUMA = float3(0.2126f, 0.7152f, 0.0722f);
static const float3 BNE_SNOW_TINT = float3(0.93f, 0.96f, 1.0f);
static const int BNE_STEPS = 20;

float bneHash(float2 p)
{
    p = frac(p * float2(0.1031f, 0.1030f));
    p += dot(p, p.yx + 33.33f);
    return frac((p.x + p.y) * p.x);
}

float bneNoise(float2 p)
{
    const float2 i = floor(p);
    const float2 f = frac(p);
    const float2 u = f * f * (3.0f - 2.0f * f);
    const float a = bneHash(i);
    const float b = bneHash(i + float2(1.0f, 0.0f));
    const float c = bneHash(i + float2(0.0f, 1.0f));
    const float d = bneHash(i + float2(1.0f, 1.0f));
    return lerp(lerp(a, b, u.x), lerp(c, d, u.x), u.y);
}

// 0..1, three octaves
float bneFbm(float2 p)
{
    float s = 0.0f;
    float w = 0.5f;
    [unroll] for (int i = 0; i < 3; ++i) {
        s += w * bneNoise(p);
        p = p * 2.03f + float2(17.1f, 9.7f);
        w *= 0.5f;
    }
    return s / 0.875f;
}

// Interleaved gradient noise (Jimenez 2014), moving with time: the reflection's jitter.
float bneIgn(float2 pixel, float t)
{
    pixel += frac(t * 7.31f) * float2(47.0f, 17.0f);
    return frac(52.9829189f * frac(dot(pixel, float2(0.06711056f, 0.00583715f))));
}

// The scene at viewport uv: world normal, eye-linear depth (0..1 of the far plane; 1 = sky).
float4 bneScene(float2 uv)
{
    return decodeGBuffer(TEX_SAMPLER(prepassDepthTex), TEX_SAMPLER(prepassTex), viewportCoordToRenderTarget(uv, rtParams1),
                         projParams);
}

// A point p relative to the eye -> viewport uv and its eye-linear depth (the eye ray of uv is
// eyeRayTopLeft + eyeRayRight * u + eyeRayDown * v, reaching the far plane; p = ray * depth). False behind the eye.
bool bneProject(float3 p, out float2 uv, out float depth)
{
    uv = 0.0f;
    depth = 0.0f;
    const float3 n = cross(eyeRayRight, eyeRayDown);
    const float pn = dot(p, n);
    const float tn = dot(eyeRayTopLeft, n);
    if (abs(pn) < 1e-9f || pn * tn <= 0.0f) {
        return false;
    }
    const float s = tn / pn;
    const float3 q = p * s - eyeRayTopLeft;
    uv = float2(dot(q, eyeRayRight) / max(dot(eyeRayRight, eyeRayRight), 1e-12f),
                dot(q, eyeRayDown) / max(dot(eyeRayDown, eyeRayDown), 1e-12f));
    depth = 1.0f / s;
    return true;
}

// Rain rings in a puddle at world xy p (time t, rain r): the slope a few expanding ripples give the water.
float2 bneRipples(float2 p, float t, float r)
{
    float2 slope = 0.0f;
    [unroll] for (int k = 0; k < 2; ++k) {
        const float2 g = p * (k == 0 ? 2.1f : 3.3f) + k * 7.7f;
        const float2 cell = floor(g);
        const float2 f = frac(g) - 0.5f;
        const float h = bneHash(cell + k * 3.1f);
        const float phase = frac(t * (0.9f + h * 0.6f) + h);
        if (h < r) {
            const float2 c = (float2(bneHash(cell + 1.7f), bneHash(cell + 4.3f)) - 0.5f) * 0.5f;
            const float2 d = f - c;
            const float l = length(d);
            const float ring = phase * 0.5f;
            const float w = saturate(1.0f - abs(l - ring) / 0.06f) * (1.0f - phase);
            slope += (l > 1e-4f ? d / l : 0.0f) * w * sin((l - ring) * 80.0f);
        }
    }
    return slope * 0.35f;
}

// The scene along the reflected ray from p (relative to the eye) in direction r: rgb and how much was found (a), the
// sky where the ray leaves above everything, nothing where it leaves the screen.
float4 bneReflect(float3 p, float3 r, float dist, float jitter)
{
    const float far = max(projParams.y, 1.0f);
    float t = max(0.3f, dist * 0.02f) * (0.75f + 0.5f * jitter);
    [loop] for (int i = 0; i < BNE_STEPS; ++i) {
        const float3 q = p + r * t;
        float2 uv;
        float dq;
        if (!bneProject(q, uv, dq)) {
            break;
        }
        if (any(uv < 0.0f) || any(uv > 1.0f)) {
            break;
        }
        const float ds = bneScene(uv).w;
        if (ds >= 0.99999f) {
            // the sky behind this point: if the ray climbs on into it, that's what the surface shows
            if (r.z > 0.02f && i >= 2) {
                const float3 sky = tex2D(backBuffer, viewportCoordToRenderTarget(uv, rtParams0)).rgb;
                return float4(sky, 1.0f);
            }
        } else if (dq > ds) {
            // behind a surface: a hit if it is thin enough there (else the ray passed behind an object)
            const float behind = (dq - ds) * far;
            if (behind < max(0.4f, t * 0.35f)) {
                const float2 e = min(uv, 1.0f - uv);
                const float edge = saturate(min(e.x, e.y) * 12.0f);
                const float3 hit = tex2D(backBuffer, viewportCoordToRenderTarget(uv, rtParams0)).rgb;
                return float4(hit, edge);
            }
            return 0.0f;
        }
        t *= 1.3f;
    }
    return 0.0f;
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
    const float4 src = tex2D(backBuffer, viewportCoordToRenderTarget(uv, rtParams0));
    const float4 g = bneScene(uv);
    const float flash = max(bneWx2.x, 0.0f);
    if (g.w >= 0.99999f) {
        pfxOut[storePos] = float4(src.rgb * (1.0f + flash * bneWx3.z), src.a);
        return;
    }
    const float3 n = normalize(g.xyz);
    const float3 ray = eyeRayTopLeft + eyeRayRight * uv.x + eyeRayDown * uv.y;
    const float3 rel = ray * g.w;
    const float dist = max(length(rel), 1e-3f);
    const float3 v = rel / dist;
    const float3 pw = bneWx1.xyz + rel;
    const float rough = prepassTexTex.SampleLevel(prepassTex_sampler_, viewportCoordToRenderTarget(uv, rtParams1), 0).z;
    const float porous = smoothstep(0.30f, 0.55f, rough);   // car paint, glass and metal stay as they are
    const float up = saturate(n.z);
    const float flat = smoothstep(0.55f, 0.95f, up);
    float3 c = src.rgb * (1.0f + flash * bneWx3.w);

    // snow: patches on up-facing rough ground that close as the cover grows
    float cover = 0.0f;
    const float snow = saturate(bneWx0.z);
    if (snow > 0.001f) {
        const float coarse = bneFbm(pw.xy * 0.25f);
        const float fine = bneNoise(pw.xy * 3.1f);
        const float field = up * 0.55f + coarse * 0.35f + fine * 0.10f;
        const float th = 1.05f - snow * 1.15f;
        cover = smoothstep(th - 0.06f, th + 0.06f, field) * smoothstep(0.30f, 0.62f, up) * porous;
        const float lum = dot(c, BNE_LUMA);
        const float sparkle = step(0.985f, bneHash(floor(pw.xy * 40.0f))) * saturate(1.0f - dist / 25.0f);
        const float3 snowC = BNE_SNOW_TINT * lum * bneWx3.x * (1.0f + sparkle * 1.5f);
        c = lerp(c, snowC, cover);
    }

    // wet: darker pores, reflections on the flat parts, puddles in the dips
    const float wet = saturate(bneWx0.x) * porous * (1.0f - cover);
    float puddle = 0.0f;
    float shown = 0.0f;
    if (wet > 0.001f) {
        c *= lerp(1.0f, bneWx3.y, wet * lerp(0.35f, 1.0f, flat));
        const float dip = bneFbm(pw.xy * 0.11f + 3.7f);
        puddle = smoothstep(0.78f, 0.86f, dip + saturate(bneWx0.y) * 0.32f) * saturate(bneWx0.y * 3.0f)
               * smoothstep(0.96f, 0.995f, up) * wet;
        const float fade = saturate(1.0f - dist / max(bneWx2.z, 1.0f));
        if (flat > 0.01f && fade > 0.0f && bneWx2.y > 0.0f) {
            const float jitter = bneIgn(float2(storePos), bneWx0.w);
            float3 nr = normalize(lerp(n, float3(0.0f, 0.0f, 1.0f), puddle));
            if (puddle > 0.01f && bneWx1.w > 0.0f) {
                const float2 s = bneRipples(pw.xy, bneWx0.w, saturate(bneWx1.w)) * puddle;
                nr = normalize(nr + float3(s, 0.0f));
            }
            // a wet road is a rough mirror: the normal jittered (less in a puddle)
            const float spread = 0.10f * (1.0f - puddle);
            const float a = jitter * 6.2831853f;
            const float2 j = float2(cos(a), sin(a)) * spread * sqrt(bneIgn(float2(storePos.yx), bneWx0.w + 0.5f));
            nr = normalize(nr + float3(j, 0.0f));
            const float3 r = reflect(v, nr);
            if (r.z > 0.0f) {
                const float4 refl = bneReflect(rel + nr * (0.02f + dist * 0.002f), r, dist, jitter);
                const float cosv = saturate(dot(-v, nr));
                const float fres = 0.02f + 0.98f * pow(1.0f - cosv, 5.0f);
                const float k = saturate(fres * bneWx2.y * wet * flat * lerp(0.7f, 1.0f, puddle) * fade) * refl.a;
                c = lerp(c, refl.rgb, k);
                shown = k;
            }
        }
    }

    if (bneWx2.w > 0.5f) {
        c = float3(wet, cover, puddle) + shown * 0.25f;
    }
    pfxOut[storePos] = float4(c, src.a);
}
