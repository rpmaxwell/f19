"""Line of sight from a radar to the plane over the decoded 3D terrain (game units).

  visible(H, step, radar_xy, plane_xy, plane_ft, radar_ft=30) -> bool
  shadow_map(H, step, radar_xy, rng_km, plane_ft, box, res) -> bool grid (True = hidden)

H = logs/theater/<TH>_terrain.npz height_ft (top of terrain, ft; cell = step waypoint units).
"""
import numpy as np

K = 64.0

def _h(H, step, x, y):
    i = np.clip((np.asarray(x) // step).astype(int), 0, H.shape[1] - 1)
    j = np.clip((np.asarray(y) // step).astype(int), 0, H.shape[0] - 1)
    return H[j, i]

def visible(H, step, r, p, plane_ft, radar_ft=30.0):
    d = np.hypot(p[0] - r[0], p[1] - r[1])
    n = max(8, int(d / (step / 2)))
    t = (np.arange(n) + 0.5) / n                     # 0 = plane, 1 = radar
    x = p[0] + t * (r[0] - p[0]); y = p[1] + t * (r[1] - p[1])
    return not np.any(_h(H, step, x, y) > plane_ft + (radar_ft - plane_ft) * t)

def shadow_map(H, step, r, plane_ft, xs, ys, radar_ft=30.0):
    X, Y = np.meshgrid(xs, ys)
    hid = np.zeros(X.shape, bool)
    d = np.hypot(X - r[0], Y - r[1])
    n = int(d.max() / (step / 2)) + 8
    for t in (np.arange(n) + 0.5) / n:
        hid |= _h(H, step, X + t * (r[0] - X), Y + t * (r[1] - Y)) > plane_ft + (radar_ft - plane_ft) * t
    return hid
