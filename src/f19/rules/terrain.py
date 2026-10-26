"""Terrain lookahead on the planner's decoded terrain (moved from pilot/runner.py by WP-7d)."""
import math

import numpy as np

from f19.core.units import K


def terrain_ahead_probe(heights, step):
    """nav.terrain_ahead (the height_game branch) on decoded terrain heights (ft, [y, x] cells of `step` units):
    probe(p, hdg, alt, max_km=6.0, half_width_km=0.5, margin_ft=100) -> (km, top ft) of the first terrain straight
    ahead within margin_ft of alt, in a corridor +-half_width_km wide, or None."""
    H = heights

    def probe(p, hdg, alt, max_km=6.0, half_width_km=0.5, margin_ft=100):
        hr = math.radians(hdg); sx, cy = math.sin(hr), -math.cos(hr)
        for d in np.arange(0.25, max_km + 0.01, 0.25):
            tops = []
            for w in (-half_width_km, 0.0, half_width_km):
                gx, gy = p[0] + (sx * d - cy * w) * K, p[1] + (cy * d + sx * w) * K
                i, j = int(gx // step), int(gy // step)
                if 0 <= j < H.shape[0] and 0 <= i < H.shape[1]: tops.append(int(H[j, i]))
            if tops and max(tops) > 0 and max(tops) >= alt - margin_ft: return float(d), max(tops)
        return None
    return probe
