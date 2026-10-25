"""Decode F-19's 3D world (.3DG grid, .3DT tile objects, .3D3 shapes) into world positions.

Same engine as F-15 Strike Eagle II; the loader/grid/model code is decompiled in
analysis/f15se2-re/src (eg3dload.c, eg3dgrid.c, eg3dproj.c, eg3drast.c).

World layout (position units: 2048 per km; waypoint units = pos >> 5):
  LOD 4   8x8 grid, cell 0x40000 (128 km); grid col/row 2..5 cover the map. Built into
          EGAME.EXE per theater (TOP_GRIDS below, from the unpacked exe at 0x1f546).
  LOD 3  16x16 grid, cell 0x10000 (32 km)   .3DG buf1[col + row*16]
  LOD 2  64x64,       cell 0x4000  (8 km)   .3DG buf2[(col&3) + (row&3)*4 + lod3tile*16]
  LOD 1 256x256,      cell 0x1000  (2 km)   .3DG buf3[...lod2tile*16]
  LOD 0 1024x1024,    cell 0x800   (1 km)   .3DG buf4[...lod1tile*16]
col = X tile, row = Y tile (Y grows southward, like waypoint Y).
A tile id at LOD L indexes .3DT list [L][id]: objects (x, y, z, shape) as offsets from the
tile centre in that LOD's units (4096 per tile). World pos = ((tile<<12) + 0x800 + x) << SHIFT[L].
Shapes (.3D3) are vertex lists in the same LOD-local units, z = height.

  python -m f19.gamedata.terrain [NC|CE|LB|PG]   (or analysis/terrain_3d.py)
      -> logs/theater/<TH>_terrain.npz, <TH>_mountains.json, <TH>_terrain.png
"""
import json
import struct
import sys

from f19.core.settings import settings as S
from f19.core.theater import Theater
from f19.core.units import POS_PER_KM, FT_PER_M
from f19.gamedata.wld import grid as territory_grid

SHIFT = {0: -1, 1: 0, 2: 2, 3: 4, 4: 6}          # LOD-local units -> position units
FT_PER_POS = 1000 / POS_PER_KM * FT_PER_M         # ~1.6 ft per position unit
THEATER_INDEX = {t.value: t.index for t in Theater}   # LB 0, PG 1, NC 2, CE 3

# LOD-4 8x8 grids from the unpacked EGAME.EXE (64 bytes per theater, index above).
TOP_GRIDS = [bytes.fromhex(h) for h in (
    "1010101010101010" "1010101010101010" "1010000102031010" "1010040506071010"
    "10100809" "0a0b1111" "11110c0d0e0f1111" "1111111111101010" "1110101111101010",
    "1010101010111111" "1010101010101111" "1010000102031111" "1010040506071010"
    "10100809" "0a0b1010" "10100c0d0e0f1010" "1010101010101010" "1010101010101010",
    "1111101010101010" "1011101010101010" "1010000102031010" "1010040506071010"
    "11100809" "0a0b1111" "11110c0d0e0f1111" "1111111111111111" "1111111111111111",
    "1010101010101010" "1010101010101010" "1010000102031010" "1010040506071010"
    "11110809" "0a0b1010" "11110c0d0e0f1010" "1011111011111110" "1010101010111010",
)]


def load_3dg(th):
    d = (S.game_dir / f"{th}.3DG").read_bytes()
    assert d[:2] == b"22" and len(d) == 1810, "bad .3DG"
    return [d[18:18 + 256], d[274:274 + 512], d[786:786 + 512], d[1298:1298 + 512]]


def load_3dt(th):
    """-> lists[lod][tile] = [(x, y, z, shape), ...]"""
    d = (S.game_dir / f"{th}.3DT").read_bytes()
    assert d[:2] == b"11", "bad .3DT"
    sizes = struct.unpack_from("<5H", d, 2)
    p = 12
    counts = []
    for n in sizes:
        counts.append(struct.unpack_from(f"<{n}H", d, p)); p += 2 * n
    lists = []
    for cat in counts:
        tiles = []
        for n in cat:
            tiles.append([struct.unpack_from("<hhhH", d, p + 8 * i) for i in range(n)]); p += 8 * n
        lists.append(tiles)
    assert p == len(d)
    return lists


def load_3d3(th):
    """-> {shape: (verts [(x, y, z)], faces [(nx, ny, nz, thr)])}, most detailed LOD of each shape."""
    d = (S.game_dir / f"{th}.3D3").read_bytes()
    sig, n = struct.unpack_from("<HH", d, 0)
    assert sig == 0x3333, "bad .3D3"
    offs = struct.unpack_from(f"<{n}H", d, 4)
    p = 4 + 2 * n
    size, = struct.unpack_from("<H", d, p); p += 2
    data = d[p:p + size]; p += size
    shared = None
    k = d[p]; p += 1
    if k:
        bx, by, bz = d[p:p + k], d[p + k:p + 2 * k], d[p + 2 * k:p + 3 * k]; p += 3 * k
        tabs = []
        for _ in range(3):
            m = d[p]; p += 1
            tabs.append(struct.unpack_from(f"<{m}h", d, p)); p += 2 * m
        shared = [(tabs[0][bx[i]], tabs[1][by[i]], tabs[2][bz[i]]) for i in range(k)]
    shapes = {}
    for s, o in enumerate(offs):
        try:
            shapes[s] = _model_vertices(data, o, shared)
        except (IndexError, struct.error):
            shapes[s] = ([], [])
    return shapes


def _model_vertices(data, o, shared):
    p = o + 1                                   # render-mode byte
    while data[p] & 0x80:                       # LOD chain: take the nearest (most detailed)
        p += 3
    if data[p] & 0x60 == 0x60:                  # spinning part: one extra byte
        p += 1
    op = data[p]
    if op & 0x3f == 0x3f:                       # single point
        return [(0, 0, 0)], []
    if op & 0x3f == 0x3e:                       # run of shared points
        n = data[p + 2]
        return [shared[r] for r in data[p + 3:p + 3 + n]], []
    nf = op & 0x1f
    mask = 4 if nf > 16 else 2
    faces = [struct.unpack_from("<hhhh", data, p + 1 + 8 * i) for i in range(nf)]
    p += 1 + 8 * nf
    al = data[p]; p += 1
    out = []
    for _ in range(al & 0x7f):
        p += mask
        if al & 0x80:
            out.append(shared[data[p]]); p += 1
        else:
            out.append(struct.unpack_from("<hhh", data, p)); p += 6
    return out, faces


def grid_tile(g3dg, top, lod, col, row):
    dim = {4: 8, 3: 16, 2: 64, 1: 256, 0: 1024}[lod]
    if lod == 4:
        col += 2; row += 2
    if not (0 <= col < dim and 0 <= row < dim):
        return 0
    if lod == 4:
        return top[col + row * 8]
    if lod == 3:
        return g3dg[0][col + row * 16]
    parent = grid_tile(g3dg, top, lod + 1, col >> 2, row >> 2)
    return g3dg[3 - lod][(col & 3) + (row & 3) * 4 + parent * 16]


def scale_to_lod(lod, v):
    """scaleCoordToLod: position/altitude units -> LOD-local units."""
    return {4: (v + 0x20) >> 6, 3: (v + 8) >> 4, 2: (v + 2) >> 2, 1: v, 0: v << 1}[lod]


class World:
    """All placed 3D objects of a theater, with the game's own inside-a-solid test."""

    def __init__(self, th, flip_y=True, z_scale=1.0):
        self.th, self.flip_y, self.z_scale = th, flip_y, z_scale
        g, t, self.shapes = load_3dg(th), load_3dt(th), load_3d3(th)
        top = TOP_GRIDS[THEATER_INDEX[th]]
        self.objs = []                                   # (lod, col, row, x, y, z, shape)
        for lod in (4, 3, 2, 1, 0):
            dim = {4: 8, 3: 16, 2: 64, 1: 256, 0: 1024}[lod]
            lo = -2 if lod == 4 else 0
            for row in range(lo, dim + lo):
                for col in range(lo, dim + lo):
                    tile = grid_tile(g, top, lod, col, row)
                    if tile >= len(t[lod]):
                        continue
                    for (x, y, z, shape) in t[lod][tile]:
                        self.objs.append((lod, col, row, x, y, z, shape & 0x7f))

    def to3d(self, wx, wy, alt):
        """waypoint units (map Y grows south) + altitude -> 3D world units."""
        y3 = ((0x8000 - wy) << 5) if self.flip_y else (wy << 5)
        return wx << 5, y3, int(alt * self.z_scale)

    def solids_at(self, wx, wy, alt, min_faces=4):
        """Objects whose every face plane has the point on its inner side (= a crash)."""
        X, Y, Z = self.to3d(wx, wy, alt)
        hits = []
        for (lod, col, row, x, y, z, shape) in self.objs:
            verts, faces = self.shapes.get(shape, ([], []))
            if len(faces) < min_faces:
                continue
            dx = scale_to_lod(lod, X) - (col << 12) - 0x800 - x
            dy = scale_to_lod(lod, Y) - (row << 12) - 0x800 - y
            if abs(dx) > 4096 or abs(dy) > 4096:
                continue
            dz = scale_to_lod(lod, Z) - z
            if all(nx * dx + ny * dy + nz * dz < thr for nx, ny, nz, thr in faces):
                hits.append((lod, col, row, x, y, z, shape))
        return hits

    def solid_objects(self, min_faces=4):
        """Placed objects the game treats as solid (>= 4 faces), with peak height. Skips cloud slabs
        (see is_backdrop)."""
        for o in self.objs:
            lod, col, row, x, y, z, shape = o
            verts, faces = self.shapes.get(shape, ([], []))
            if len(faces) >= min_faces and verts and not self.is_backdrop(o, verts):
                yield o, verts, faces

    BACKDROP_KM = 64.0

    def is_backdrop(self, o, verts):
        """Cloud layers: LOD-4 slabs wider than BACKDROP_KM (96 x 80 km boxes topping out at 8192 ft: two in
        the Gulf, one in Libya; none in CE/NC) over cells the game's own terrain grid marks flat or sea,
        not mountain. The game draws large rectangular clouds in some theaters (the user, 2026-10-24).
        The height field fills every solid from the ground up to its top, so these walled off whole
        stretches of open water (Bahrain unreachable from the north): they are left out. Not checked:
        whether the game's inside test counts flying into one as a crash (only matters near 8000 ft)."""
        lod, col, row, x, y, z, shape = o
        xs = [x + v[0] for v in verts]; ys = [y + v[1] for v in verts]
        (ax, ay), (bx, by) = self.lod_to_wp(lod, col, row, min(xs), min(ys)), self.lod_to_wp(lod, col, row, max(xs), max(ys))
        if max(abs(bx - ax), abs(by - ay)) / 64 <= self.BACKDROP_KM:
            return False
        g = territory_grid(self.th)
        cells = [(int(j), int(i)) for j in range(int(min(ay, by)) >> 11, (int(max(ay, by)) >> 11) + 1)
                 for i in range(int(min(ax, bx)) >> 11, (int(max(ax, bx)) >> 11) + 1) if 0 <= i < 16 and 0 <= j < 16]
        mountain = sum((g[j, i] & 0xC) == 0 for j, i in cells)
        return bool(cells) and mountain < len(cells) / 2

    def lod_to_wp(self, lod, col, row, lx, ly):
        """LOD-local point in tile (col, row) -> waypoint units (float)."""
        s = 2.0 ** SHIFT[lod]
        X3 = ((col << 12) + 0x800 + lx) * s
        Y3 = ((row << 12) + 0x800 + ly) * s
        wy = (0x8000 - Y3 / 32) if self.flip_y else Y3 / 32
        return X3 / 32, wy

    def heightfield(self, step=16, min_faces=4):
        """Top-of-terrain height (alt units) on a grid of `step` waypoint units -> (H, step).
        H[j, i] covers waypoint (i*step, j*step). Height = lowest altitude at which the
        game's inside test stops firing, i.e. min over upward faces of the face plane."""
        import numpy as np
        n = 0x8000 // step
        H = np.zeros((n, n), dtype=np.float32)
        for (lod, col, row, x, y, z, shape), verts, faces in self.solid_objects(min_faces):
            s = 2.0 ** SHIFT[lod]
            xs = [x + v[0] for v in verts]; ys = [y + v[1] for v in verts]
            (ax, ay), (bx, by) = self.lod_to_wp(lod, col, row, min(xs), min(ys)), self.lod_to_wp(lod, col, row, max(xs), max(ys))
            i0, i1 = int(min(ax, bx) // step), int(max(ax, bx) // step) + 1
            j0, j1 = int(min(ay, by) // step), int(max(ay, by) // step) + 1
            i0, j0, i1, j1 = max(i0, 0), max(j0, 0), min(i1, n - 1), min(j1, n - 1)
            if i1 < i0 or j1 < j0:
                continue
            I, J = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
            X3 = I * step * 32.0
            Y3 = ((0x8000 - J * step) if self.flip_y else J * step) * 32.0
            dx = X3 / s - (col << 12) - 0x800 - x
            dy = Y3 / s - (row << 12) - 0x800 - y
            top = np.full(dx.shape, np.inf)
            ok = np.ones(dx.shape, bool)
            for nx, ny, nz, thr in faces:
                if nz > 0:
                    top = np.minimum(top, (thr - nx * dx - ny * dy) / nz)
                elif nz == 0:
                    ok &= nx * dx + ny * dy < thr
                else:                                   # downward face: a floor; ignore (we fly above)
                    pass
            top = np.minimum(top, max(v[2] for v in verts))       # never above the peak vertex
            top = np.where(ok & np.isfinite(top), top + z, 0.0)   # object-local -> tile z
            top = np.clip(top * s / self.z_scale, 0, None)
            H[j0:j1 + 1, i0:i1 + 1] = np.maximum(H[j0:j1 + 1, i0:i1 + 1], top)
        return H


def crash_check(th, W=None):
    """Known crash sites (logs/theater/<TH>_obstacles.json) -> solids containing each."""
    W = W or World(th)
    f = S.logs / "theater" / f"{th}_obstacles.json"
    out = []
    for o in (json.load(open(f))["obstacles"] if f.exists() else []):
        alt = o.get("alt_ft", 0)
        out.append((o, W.solids_at(o["x"], o["y"], alt) if alt else None))
    return out


def write_outputs(th):
    import numpy as np
    from PIL import Image, ImageDraw
    W = World(th)
    H = W.heightfield(step=16)
    out = S.logs / "theater"; out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / f"{th}_terrain.npz", height_ft=H.astype(np.uint16), step_wp=16)
    mts = []
    for (lod, col, row, x, y, z, shape), verts, faces in W.solid_objects():
        peak = (max(v[2] for v in verts) + z) << SHIFT[lod]
        if peak < 200:                                   # buildings / tree blocks: below cruise
            continue
        cx, cy = W.lod_to_wp(lod, col, row, x, y)
        r = max(max(abs(v[0]), abs(v[1])) for v in verts) * 2.0 ** SHIFT[lod] / POS_PER_KM
        if not (-64 * r <= cx <= 0x8000 + 64 * r and -64 * r <= cy <= 0x8000 + 64 * r):
            continue
        mts.append(dict(x=round(cx), y=round(cy), peak_ft=peak, radius_km=round(r, 2), lod=lod, shape=shape))
    mts.sort(key=lambda m: -m["peak_ft"])
    json.dump({"note": "Solid 3D terrain decoded from .3DG/.3DT/.3D3 (analysis/terrain_3d.py). "
                       "x/y waypoint units (64/km, y grows south); peak_ft above the flat world "
                       "(same units as the altimeter); radius_km = half-width of the footprint. "
                       f"Full top-of-terrain grid: {th}_terrain.npz (height_ft[j, i] at waypoint (i, j) * step_wp).",
               "mountains": mts}, open(out / f"{th}_mountains.json", "w"), indent=0)
    # preview: grey = height (log), red rings = crash sites, white dots = named places
    n = H.shape[0]; small = H.reshape(n // 2, 2, n // 2, 2).max(axis=(1, 3))
    v = np.log1p(small) / np.log1p(max(H.max(), 1))
    rgb = np.stack([40 + 215 * v, 70 + 150 * v, 40 + 120 * v], -1)
    rgb[small == 0] = (25, 45, 70)
    img = Image.fromarray(rgb.astype(np.uint8)); d = ImageDraw.Draw(img)
    S = img.size[0] / 0x8000
    place = S.logs / "theater" / f"{th}.json"
    if place.exists():
        for r in json.load(open(place))["records"]:
            if r["name"] and r["kind"] == 0 and 0 < r["x"] < 0x8000 and 0 < r["y"] < 0x8000:
                px, py = r["x"] * S, r["y"] * S
                d.ellipse((px - 2, py - 2, px + 2, py + 2), fill=(255, 255, 255)); d.text((px + 4, py - 6), r["name"], fill=(255, 255, 255))
    for o, hit in crash_check(th, W):
        px, py = o["x"] * S, o["y"] * S
        col = (255, 60, 60) if hit else (255, 200, 0)
        d.ellipse((px - 8, py - 8, px + 8, py + 8), outline=col, width=2)
    img.save(out / f"{th}_terrain.png")
    return H, mts


def main(argv):
    for th in (argv or ["NC", "CE", "LB", "PG"]):
        H, mts = write_outputs(th)
        print(f"{th}: {len(mts)} solids >= 200 ft, highest {H.max():.0f} ft, "
              f"{(H > 300).mean() * 100:.1f}% of the map above 300 ft")
        for o, hit in crash_check(th):
            if hit is not None:
                print(f"   crash ({o['x']}, {o['y']}) at {o['alt_ft']} ft: "
                      + (f"inside LOD-{hit[0][0]} shape {hit[0][6]}" if hit else "NOT inside any solid"))


if __name__ == "__main__":
    main(sys.argv[1:])
