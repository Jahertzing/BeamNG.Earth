#!/usr/bin/env python3
"""Thunder for weatherfx.lua's thunderstorms (the game has none): three claps synthesized from noise, written as Ogg
Vorbis to mod/beamng_earth/art/sound/beamngEarth/thunder_{1,2,3}.ogg.

  1  a near strike: a sharp crack, the boom, a long roll
  2  a mid-distance strike: a softer onset, rolling bursts
  3  a far strike: a low rumble only (the air took the high frequencies)

Each is brown noise shaped in frequency (an FFT filter: no scipy) and in time (bursts that decay, as the sound
arrives from the different parts of the channel), with a fixed seed so the files come out the same every run. Needs
numpy and ffmpeg with libvorbis on the PATH.

  python tools/make_thunder.py            write the three files
  python tools/make_thunder.py --wav DIR  also keep the .wav files in DIR (to listen to them)
"""
import argparse
import os
import shutil
import subprocess
import tempfile
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "mod", "beamng_earth", "art", "sound", "beamngEarth")
RATE = 44100

# name: (seconds, seed, crack, low-pass Hz, bursts, roll seconds, decay seconds, onset seconds)
CLAPS = {
    "thunder_1": (7.0, 11, 0.35, 650.0, 9, 3.5, 2.2, 0.01),
    "thunder_2": (8.0, 23, 0.06, 300.0, 12, 4.5, 2.8, 0.15),
    "thunder_3": (7.5, 37, 0.0, 180.0, 10, 5.0, 3.0, 0.5),
}


def shaped(noise, lo_hz, hi_hz):
    """noise band-limited between lo_hz and hi_hz (smooth edges), by FFT."""
    n = len(noise)
    spec = np.fft.rfft(noise)
    f = np.fft.rfftfreq(n, 1.0 / RATE)
    hp = 1.0 / (1.0 + (lo_hz / np.maximum(f, 1e-3)) ** 4)
    lp = 1.0 / (1.0 + (f / hi_hz) ** 4)
    return np.fft.irfft(spec * hp * lp, n)


def clap(secs, seed, crack, lp_hz, bursts, roll_s, decay_s, onset_s):
    rng = np.random.default_rng(seed)
    n = int(secs * RATE)
    t = np.arange(n) / RATE
    brown = np.cumsum(rng.standard_normal(n))
    brown -= np.convolve(brown, np.ones(4096) / 4096, mode="same")      # no drift
    body = shaped(brown, 25.0, lp_hz)
    body /= np.max(np.abs(body)) + 1e-9
    # the roll: decaying bursts spread over roll_s, the first the strongest
    env = np.zeros(n)
    for i in range(bursts):
        at = onset_s + (roll_s * (i / max(bursts - 1, 1)) ** 1.6 if i else 0.0) + rng.uniform(0, 0.25)
        amp = (0.55 + 0.45 * rng.random()) * (1.0 if i == 0 else 0.85 ** i)
        rise = 0.03 + 0.12 * rng.random()
        tau = 0.3 + 0.9 * rng.random()
        k = t - at
        env += np.where(k >= 0, amp * (1 - np.exp(-k / rise)) * np.exp(-k / tau), 0.0)
    env *= np.exp(-np.maximum(t - onset_s, 0) / (decay_s * 1.8))
    env = np.convolve(env, np.ones(441) / 441, mode="same")
    out = body * env
    if crack > 0:   # the near crack: a short white burst, bright
        c = shaped(rng.standard_normal(n), 200.0, 6000.0)
        c /= np.max(np.abs(c)) + 1e-9
        ce = np.where(t >= onset_s, np.exp(-(t - onset_s) / 0.09), 0.0) * (1 - np.exp(-np.maximum(t - onset_s, 0) / 0.002))
        out += crack * 0.6 * c * ce
    fade = np.minimum(1.0, (secs - t) / 0.8)                           # silent at the end
    out *= np.clip(fade, 0, 1)
    out /= np.max(np.abs(out)) + 1e-9
    return (out * 0.89 * 32767).astype(np.int16)


def write_wav(path, pcm):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wav", help="keep the .wav files in this folder too")
    a = ap.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg (with libvorbis) is needed on the PATH")
    os.makedirs(OUT, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, p in CLAPS.items():
            wav = os.path.join(a.wav or tmp, name + ".wav")
            if a.wav:
                os.makedirs(a.wav, exist_ok=True)
            write_wav(wav, clap(*p))
            ogg = os.path.join(OUT, name + ".ogg")
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", wav, "-c:a", "libvorbis", "-q:a", "3",
                            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact", ogg], check=True)
            print(f"{os.path.relpath(ogg)}: {os.path.getsize(ogg) // 1024} KB")


if __name__ == "__main__":
    main()
