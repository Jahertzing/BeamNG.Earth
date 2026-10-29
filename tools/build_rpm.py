"""build_rpm.py: the raised pavement markers' night shapes (mod/.../art/shapes/beamngEarth/bne_rpm_white.dae and
bne_rpm_amber.dae; server roads.rpm_rows, trees.lua's rpm_white / rpm_amber groups, night only: nightlights.lua turns
them on after dusk). A real marker is a 10 cm lens 2 cm high: past ~50 m it is under a pixel, yet at night it is a
bright point in the headlights far down the road (its retroreflector), so the shape is build_lamp_glow's ball in the
engine's camera detail levels: RPM_R m near the camera (half of it under the road), larger farther, each kept while it
spans MIN_PX pixels; their materials bne_rpm_white / bne_rpm_amber (night.materials.json) are lit by nightlights.lua.

    python3 tools/build_rpm.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_lamp_glow as lod  # noqa: E402

RPM_R = (0.05, 0.1, 0.2, 0.4)      # m: the balls' radii, nearest first
MIN_PX = 2.0


def main():
    sizes = lod.detail_sizes(RPM_R, MIN_PX, None)
    lod.make("bne_rpm_white.dae", "bne_rpm_white", "0.85 0.85 0.85 1", RPM_R, sizes)
    lod.make("bne_rpm_amber.dae", "bne_rpm_amber", "0.85 0.6 0.1 1", RPM_R, sizes)


if __name__ == "__main__":
    main()
