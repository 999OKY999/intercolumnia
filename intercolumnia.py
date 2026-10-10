"""Intercolumnar gaps as same-winding constraints.

Detects the blank vertical gaps between columns of text on an ink-detection
render of a traced segment, follows each gap down the column height, maps it
back to the scroll volume through the segment's tifxyz mesh, checks it in 3D
against the umbilicus, and writes VC point collections (same-winding role).
"""
import argparse
import json
import os
import time

import numpy as np
import tifffile
from PIL import Image
from scipy.ndimage import gaussian_filter1d, uniform_filter
from scipy.signal import find_peaks

Image.MAX_IMAGE_PIXELS = None

# Column pitch of PHerc. Paris 4 in voxels of the 2.4 um volume: the median
# spacing of neighbouring gaps over all segments (gallery.py measures it).
COLUMN_PITCH_VOX = 32192.0


# ---------------------------------------------------------------- 2D: render

def load_render(path):
    a = np.asarray(Image.open(path).convert('L'), dtype=np.float32) / 255.0
    papyrus = uniform_filter((a > 0.02).astype(np.float32), size=31) > 0.3
    ink = (a > 0.6) & papyrus
    return a, papyrus, ink


def column_density(ink, papyrus, rows=slice(None)):
    n_ink = ink[rows].sum(0).astype(np.float32)
    n_pap = papyrus[rows].sum(0).astype(np.float32)
    return n_ink / np.maximum(n_pap, 1.0), n_pap


def find_gaps(ink, papyrus, min_spacing=2000, sigma=40, prominence=0.04,
              max_density=0.06):
    """Global gap candidates: deep, wide minima of ink density along x."""
    den, _ = column_density(ink, papyrus)
    s = gaussian_filter1d(den, sigma)
    peaks, props = find_peaks(-s, distance=min_spacing, prominence=prominence)
    keep = s[peaks] < max_density
    return peaks[keep], props['prominences'][keep], s


def gap_halfwidth(s, x0, reach=1500):
    """Half-width of the low-density valley around x0 in a smoothed profile."""
    W = s.size
    left = s[max(0, x0 - reach):x0]
    right = s[x0:min(W, x0 + reach)]
    level = 0.5 * (np.percentile(left, 75) + np.percentile(right, 75))
    half = 0.5 * (s[x0] + level)
    l = x0
    while l > max(0, x0 - reach) and s[l] < half:
        l -= 1
    r = x0
    while r < min(W - 1, x0 + reach) and s[r] < half:
        r += 1
    return max(60, (r - l) // 2), int((l + r) // 2)


def edge(above, j, direction, reach, min_run):
    """First index from j in `direction` that starts a run of min_run Trues."""
    n = above.size
    k = j
    run = 0
    while 0 <= k < n and abs(k - j) <= reach:
        run = run + 1 if above[k] else 0
        if run >= min_run:
            return k - direction * (min_run - 1)
        k += direction
    return max(0, min(n - 1, k))


def trace_gap(ink, papyrus, x0, hw, step=128, window=512, smooth=5e-5,
              max_step=40, flank=400, min_run=30):
    """Follow one gap down the render with dynamic programming.

    Rows are read in overlapping windows (several text lines tall, so a window
    never falls only between two lines). Each window gives an ink-density
    profile around x0; the path minimises density plus a quadratic penalty on
    sideways steps. Text density is read just outside the gap on each side.
    """
    H, W = ink.shape
    reach = hw + flank + max_step * 4
    lo, hi = max(0, x0 - reach), min(W, x0 + reach)
    xs = np.arange(lo, hi)
    centres = np.arange(step // 2, H, step)
    profiles, bowls, coverage = [], [], []
    for c in centres:
        rows = slice(max(0, c - window // 2), min(H, c + window // 2))
        den, n_pap = column_density(ink[:, lo:hi], papyrus[:, lo:hi], rows)
        profiles.append(gaussian_filter1d(den, 15.0))
        # Wide blur turns the flat bottom of the gap into a bowl whose lowest
        # point is the middle of the gap, not wherever a tie happens to fall.
        bowls.append(gaussian_filter1d(den, 0.8 * hw))
        coverage.append(n_pap / max(1, rows.stop - rows.start))
    profiles = np.array(profiles, np.float32)
    bowls = np.array(bowls, np.float32)
    coverage = np.array(coverage, np.float32)
    cost = bowls.copy()
    # Keep the path inside the valley found globally.
    outside = np.abs(xs - x0) > hw
    cost[:, outside] += 1.0

    n = len(centres)
    acc = cost.copy()
    back = np.zeros(cost.shape, np.int32)
    steps = np.arange(-max_step, max_step + 1)
    pen = (smooth * steps.astype(np.float32) ** 2).astype(np.float32)
    idx = np.arange(xs.size)
    for b in range(1, n):
        prev = acc[b - 1]
        best = np.full(xs.size, np.inf, np.float32)
        arg = np.zeros(xs.size, np.int32)
        for k, d in enumerate(steps):
            shifted = np.full(xs.size, np.inf, np.float32)
            if d >= 0:
                shifted[d:] = prev[:xs.size - d] + pen[k]
            else:
                shifted[:d] = prev[-d:] + pen[k]
            better = shifted < best
            best[better] = shifted[better]
            arg[better] = (idx - d)[better]
        acc[b] = cost[b] + best
        back[b] = arg
    path = np.zeros(n, np.int32)
    path[-1] = int(np.argmin(acc[-1]))
    for b in range(n - 1, 0, -1):
        path[b - 1] = back[b, path[b]]

    out = []
    for b in range(n):
        j = path[b]
        prof = profiles[b]
        left = prof[max(0, j - hw - flank):max(1, j - hw // 2)]
        right = prof[min(xs.size - 1, j + hw // 2):j + hw + flank]
        lmax = float(left.max()) if left.size else 0.0
        rmax = float(right.max()) if right.size else 0.0
        floor = float(prof[max(0, j - hw // 2):j + hw // 2].min())
        # Text edges: first place, walking out from the gap, where the
        # profile rises above half-way to the text and stays there for
        # min_run pixels (stray specks in the gap do not count).
        el = edge(prof >= 0.5 * (floor + lmax), j, -1, hw + flank, min_run)
        er = edge(prof >= 0.5 * (floor + rmax), j, +1, hw + flank, min_run)
        out.append(dict(
            row=int(centres[b]),
            x=int(xs[j]),
            edge_left=int(xs[el]),
            edge_right=int(xs[er]),
            gap=float(prof[j]),
            left=lmax,
            right=rmax,
            coverage=float(min(coverage[b, max(0, el - flank):er + flank].min(), 1.0)),
        ))
    return out


def classify_bands(trace, min_flank=0.06, rel_flank=0.5, max_ratio=0.4,
                   min_cov=0.9, min_width=80):
    """A window is usable when text lies on both sides and the gap is clear.

    Thresholds adapt to the gap: ink renders are fainter in some columns, so
    text must reach half this gap's typical flank density, and the gap must
    hold well under half the ink of its flanks.
    """
    flanks = np.array([min(r['left'], r['right']) for r in trace])
    typical = float(np.median(flanks[flanks > 0])) if (flanks > 0).any() else 0.0
    for r, f in zip(trace, flanks):
        r['contrast'] = float(f - r['gap'])
        r['ok2d'] = bool(f >= max(min_flank, rel_flank * typical)
                         and r['gap'] <= max_ratio * f
                         and r['coverage'] >= min_cov
                         and r['edge_right'] - r['edge_left'] >= min_width)
    return trace


def bridge(mask, max_hole=1):
    """Fill isolated dropouts of at most max_hole windows inside good runs."""
    m = mask.copy()
    rs = runs(mask)
    for (a0, b0), (a1, b1) in zip(rs, rs[1:]):
        if a1 - b0 <= max_hole:
            m[b0:a1] = True
    return m


def find_steps(xc, ok, min_step):
    """Windows where the gap centre jumps sideways by at least min_step.

    The gap may drift smoothly (columns lean, flattening bends); a sheet
    switch moves it at once. Compare the median centre just before and just
    after each window boundary, after removing the local drift.
    """
    n = len(xc)
    flags = np.zeros(n, bool)
    idx = np.where(ok)[0]
    if idx.size < 6:
        return flags
    x = np.asarray(xc, float)
    for t in range(1, len(idx)):
        before = idx[max(0, t - 3):t]
        after = idx[t:t + 3]
        if before.size < 2 or after.size < 2:
            continue
        drift = np.polyfit(idx, x[idx], 2) if idx.size >= 8 else np.polyfit(idx, x[idx], 1)
        res = x - np.polyval(drift, np.arange(n))
        jump = np.median(res[after]) - np.median(res[before])
        if abs(jump) >= min_step:
            flags[idx[t - 1]] = flags[idx[t]] = True
    return flags


# ---------------------------------------------------------- 3D: tifxyz mesh

class Mesh:
    def __init__(self, folder):
        self.x = tifffile.imread(os.path.join(folder, 'x.tif'))
        self.y = tifffile.imread(os.path.join(folder, 'y.tif'))
        self.z = tifffile.imread(os.path.join(folder, 'z.tif'))
        self.meta = json.load(open(os.path.join(folder, 'meta.json')))
        self.valid = (self.x != -1) & (self.y != -1) & (self.z != -1)

    def sample(self, gi, gj):
        """Bilinear xyz at fractional grid (row gi, col gj); NaN if invalid."""
        H, W = self.x.shape
        i0, j0 = int(np.floor(gi)), int(np.floor(gj))
        if i0 < 0 or j0 < 0 or i0 + 1 >= H or j0 + 1 >= W:
            return None
        if not self.valid[i0:i0 + 2, j0:j0 + 2].all():
            return None
        fi, fj = gi - i0, gj - j0
        w = np.array([(1 - fi) * (1 - fj), (1 - fi) * fj, fi * (1 - fj), fi * fj])
        out = []
        for arr in (self.x, self.y, self.z):
            q = arr[i0:i0 + 2, j0:j0 + 2].astype(np.float64).ravel()
            out.append(float(w @ q))
        return np.array(out)


    def normal(self, gi, gj, h=2.0):
        """Unit surface normal from central differences on the grid."""
        pu, pd = self.sample(gi - h, gj), self.sample(gi + h, gj)
        pl, pr = self.sample(gi, gj - h), self.sample(gi, gj + h)
        if any(v is None for v in (pu, pd, pl, pr)):
            return None
        n = np.cross(pd - pu, pr - pl)
        nn = np.linalg.norm(n)
        return n / nn if nn > 0 else None


class Umbilicus:
    def __init__(self, path):
        cp = json.load(open(path))['control_points']
        a = np.array(sorted((p['z'], p['x'], p['y']) for p in cp), float)
        self.z, self.x, self.y = a[:, 0], a[:, 1], a[:, 2]

    def polar(self, xyz):
        ux = np.interp(xyz[:, 2], self.z, self.x)
        uy = np.interp(xyz[:, 2], self.z, self.y)
        dx, dy = xyz[:, 0] - ux, xyz[:, 1] - uy
        return np.hypot(dx, dy), np.arctan2(dy, dx)


def mesh_stretch(points, render_xy_vox, max_stretch=1.5):
    """Flag samples where the mesh stretches far more than the render.

    Render pixels are voxels on the surface, so two samples d apart in the
    render should sit about d apart in the volume. A much longer hop means
    the mesh tears or jumps between them.
    """
    n = len(points)
    flags = np.zeros(n, bool)
    for k in range(1, n):
        d3 = np.linalg.norm(points[k] - points[k - 1])
        d2 = np.linalg.norm(render_xy_vox[k] - render_xy_vox[k - 1])
        if not np.isfinite(d3) or d3 > max_stretch * d2:
            flags[k] = flags[k - 1] = True
    return flags


def wrap_length(mesh, umb, gi, gj):
    """Surface length of one full turn at grid point (gi, gj), in voxels.

    Walks the mesh row through the point, unwraps the angle about the
    umbilicus and returns the distance to where it has turned by 2*pi, on
    whichever side the row reaches that far (mean if both).
    """
    i = int(round(gi))
    j = int(round(gj))
    row = mesh.valid[i]
    if not row[j]:
        return np.nan
    l = j
    while l > 0 and row[l - 1]:
        l -= 1
    r = j
    while r < row.size - 1 and row[r + 1]:
        r += 1
    xyz = np.stack([mesh.x[i, l:r + 1], mesh.y[i, l:r + 1], mesh.z[i, l:r + 1]], -1).astype(np.float64)
    _, th = umb.polar(xyz)
    th = np.unwrap(th)
    d = np.abs(th - th[j - l])
    out = []
    right = np.where(d[j - l:] >= 2 * np.pi)[0]
    if right.size:
        out.append(right[0])
    left = np.where(d[:j - l + 1][::-1] >= 2 * np.pi)[0]
    if left.size:
        out.append(left[0])
    if not out:
        return np.nan
    return float(np.mean(out)) / mesh.meta['scale'][0]


def switch_offset(wrap_len_px, pitch_px):
    """Where a one-turn sheet switch would move the gap, in render pixels.

    Content one turn away is one wrap length further along the sheet. Seen
    through the column layout (period = pitch) the gap would appear shifted
    by the wrap length modulo the pitch. If that shift is too small to tell
    from the window-to-window jitter of the gap, a switch could hide there.
    """
    f = wrap_len_px / pitch_px
    return float(((f + 0.5) % 1.0 - 0.5) * pitch_px)


# ------------------------------------------------------------------ output

def runs(mask):
    """Maximal runs of True as (start, stop) index pairs."""
    out, start = [], None
    for i, m in enumerate(list(mask) + [False]):
        if m and start is None:
            start = i
        elif not m and start is not None:
            out.append((start, i))
            start = None
    return out


def to_point_collections(gaps, segment, min_points=6, t0=None):
    t0 = int(time.time() * 1000) if t0 is None else t0
    cols, cid, pid = {}, 1, 1
    for g in gaps:
        for a, b in runs(g['ok']):
            if b - a < min_points:
                continue
            pts = {}
            for k in range(a, b):
                x, y, z = g['xyz'][k]
                pts[str(pid)] = {'p': [round(x, 2), round(y, 2), round(z, 2)],
                                 'creation_time': t0}
                pid += 1
            cols[str(cid)] = {
                'name': f"intercolumnar_{segment}_gap{g['index']}_{a}-{b}",
                'metadata': {
                    'source': 'intercolumnia',
                    'segment': segment,
                    'gap_index': g['index'],
                    'render_x_ds8': [g['trace'][k]['x'] for k in range(a, b)],
                    'surface_normal': [[round(float(c), 4) for c in g['normal'][k]] for k in range(a, b)],
                    'line_start_xyz': [[round(float(c), 2) for c in g['edge_xyz'][k]] for k in range(a, b)],
                    'render_row_ds8': [g['trace'][k]['row'] for k in range(a, b)],
                    'mean_contrast': float(np.mean([g['trace'][k]['contrast'] for k in range(a, b)])),
                    'radius_vox': [float(g['r'][a]), float(g['r'][b - 1])],
                    'gap_halfwidth_px_ds8': g['halfwidth'],
                    'column_pitch_px_ds8': g['pitch_px'],
                    'gap_spacing_px_ds8': g['spacing_px'],
                    'one_turn_switch_shift_px_ds8': g['switch_offset_px'],
                    'one_turn_switch_shift_by_spacing_px_ds8': g['switch_offset_spacing_px'],
                    'one_turn_switch_checkable': g['switch_checkable'],
                    'gap_centre_noise_px_ds8': g['centre_noise_px'],
                    'wrap_length_px_ds8': g['wrap_len_px'],
                },
                'color': [1.0, 0.75, 0.1],
                'points': pts,
            }
            cid += 1
    return {'vc_pointcollections_json_version': '1', 'collections': cols}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('render', help='ds8 ink render (jpg/png)')
    ap.add_argument('mesh', help='tifxyz folder the render was made on')
    ap.add_argument('umbilicus', help='umbilicus json (control_points)')
    ap.add_argument('out', help='output folder')
    ap.add_argument('--segment', default='segment')
    ap.add_argument('--render-downsample', type=float, default=8.0)
    ap.add_argument('--band', type=int, default=128)
    ap.add_argument('--column-pitch-vox', type=float, default=COLUMN_PITCH_VOX,
                    help='column pitch of the scroll in voxels (0: use only the '
                         "segment's own gap spacing)")
    process(ap.parse_args())


def process(args):
    """Run one segment; returns the gap records and the point collections."""
    os.makedirs(args.out, exist_ok=True)
    a, papyrus, ink = load_render(args.render)
    mesh = Mesh(args.mesh)
    umb = Umbilicus(args.umbilicus)
    # render pixel (ds) -> mesh grid index: ds / (1/scale)
    to_grid = args.render_downsample * mesh.meta['scale'][0]
    expect_h = a.shape[0] * to_grid
    expect_w = a.shape[1] * to_grid
    assert abs(expect_h - mesh.x.shape[0]) <= 2 and abs(expect_w - mesh.x.shape[1]) <= 2, \
        (a.shape, mesh.x.shape, to_grid)

    peaks, prom, smoothed = find_gaps(ink, papyrus)
    gaps = []
    for gi, (x0, p) in enumerate(zip(peaks, prom)):
        hw, xc = gap_halfwidth(smoothed, int(x0))
        trace = classify_bands(trace_gap(ink, papyrus, xc, hw, step=args.band))
        xyz, have, nrm, edge_xyz = [], [], [], []
        nan3 = np.full(3, np.nan)
        for r in trace:
            v = mesh.sample(r['row'] * to_grid, r['x'] * to_grid)
            nv = mesh.normal(r['row'] * to_grid, r['x'] * to_grid) if v is not None else None
            ev = mesh.sample(r['row'] * to_grid, r['edge_right'] * to_grid)
            have.append(v is not None and nv is not None)
            xyz.append(v if v is not None else nan3)
            nrm.append(nv if nv is not None else nan3)
            edge_xyz.append(ev if ev is not None else nan3)
        xyz = np.array(xyz)
        nrm = np.array(nrm)
        edge_xyz = np.array(edge_xyz)
        have = np.array(have)
        ok2d = np.array([r['ok2d'] for r in trace]) & have
        rr = np.full(len(trace), np.nan)
        th = np.full(len(trace), np.nan)
        idx = np.where(have)[0]
        if idx.size:
            rr[idx], th[idx] = umb.polar(xyz[idx])
        flags = np.zeros(len(trace), bool)
        for s0, e0 in runs(ok2d):
            sub = np.arange(s0, e0)
            rxy = np.array([[trace[k]['x'], trace[k]['row']] for k in sub], float) * args.render_downsample
            flags[sub] = mesh_stretch(xyz[sub], rxy)
        ok = ok2d & ~flags
        # Detectability of a one-turn switch along this gap.
        wl = np.full(len(trace), np.nan)
        for k in np.where(ok)[0]:
            wl[k] = wrap_length(mesh, umb, trace[k]['row'] * to_grid, trace[k]['x'] * to_grid)
        gaps.append(dict(index=gi, x0=int(x0), halfwidth=int(hw), prominence=float(p), trace=trace,
                         xyz=xyz, normal=nrm, edge_xyz=edge_xyz,
                         r=rr, theta=th, ok2d=ok2d, flag3d=flags, ok=ok,
                         wrap_len_vox=wl))
    xs0 = np.array([g['trace'][len(g['trace']) // 2]['x'] for g in gaps])
    spacing = float(np.median(np.diff(np.sort(xs0)))) if len(xs0) > 1 else np.nan
    cp = getattr(args, 'column_pitch_vox', COLUMN_PITCH_VOX)
    pitch = cp / args.render_downsample if cp else np.nan
    for g in gaps:
        wl = g['wrap_len_vox']
        wl_px = np.nanmedian(wl) / args.render_downsample if np.isfinite(wl).any() else np.nan
        # A one-turn switch moves the gap by the wrap length modulo the column
        # pitch. The scroll-wide pitch and this segment's own gap spacing can
        # disagree (a missed gap doubles the spacing; a strip that repeats
        # every turn makes it one turn), so both are used and the smaller
        # shift decides whether a switch would be visible at all. It also
        # sets the step threshold, but only when the segment has its own
        # spacing; with a single gap any clear jump cuts the line.
        off = switch_offset(wl_px, pitch) if np.isfinite(wl_px) and np.isfinite(pitch) else np.nan
        off_sp = switch_offset(wl_px, spacing) if np.isfinite(wl_px) and np.isfinite(spacing) else np.nan
        offs = [abs(o) for o in (off, off_sp) if np.isfinite(o)]
        off_min = min(offs) if offs else np.nan
        off_step = off_min if np.isfinite(off_sp) else np.nan
        # Line starts of the right-hand column are the sharpest, most regular
        # edge (line ends are ragged), so jumps are measured on them.
        xc = np.array([r['edge_right'] for r in g['trace']], float)
        idx = np.where(g['ok'])[0]
        # Window-to-window jitter of the gap centre sets the smallest step we
        # can see; a one-turn switch moves the gap by `off`.
        noise = float(1.4826 * np.median(np.abs(np.diff(xc[idx]) - np.median(np.diff(xc[idx]))))) if idx.size > 3 else np.nan
        min_step = max(4.0 * noise, 60.0) if np.isfinite(noise) else np.inf
        steps = find_steps(xc, g['ok'], max(min_step, 0.6 * off_step) if np.isfinite(off_step) else min_step)
        g['steps'] = steps
        g['ok'] = bridge(g['ok'] & ~steps)
        g['pitch_px'] = pitch
        g['spacing_px'] = spacing
        g['wrap_len_px'] = float(wl_px)
        g['switch_offset_px'] = off
        g['switch_offset_spacing_px'] = off_sp
        g['centre_noise_px'] = noise
        # find_steps needs six clean windows; with fewer no jump was looked for.
        g['switch_checkable'] = bool(idx.size >= 6 and np.isfinite(off_min) and 0.6 * off_min >= min_step)
        ok, rr = g['ok'], g['r']
        print(f"gap {g['index']}: x0={g['x0']} hw={g['halfwidth']} prom={g['prominence']:.3f} "
              f"windows={len(g['trace'])} 2d_ok={g['ok2d'].sum()} torn={g['flag3d'].sum()} "
              f"steps={steps.sum()} kept={ok.sum()} wrap={wl_px:.0f}px pitch={pitch:.0f}px "
              f"spacing={spacing:.0f}px switch_shift={off:.0f}/{off_sp:.0f}px noise={noise:.0f}px "
              f"checkable={g['switch_checkable']}")

    pc = to_point_collections(gaps, args.segment)
    with open(os.path.join(args.out, f'{args.segment}_intercolumnar_same_windings.json'), 'w') as f:
        json.dump(pc, f)
    np.save(os.path.join(args.out, f'{args.segment}_gaps.npy'),
            np.array([{k: v for k, v in g.items()} for g in gaps], dtype=object),
            allow_pickle=True)
    n = sum(len(c['points']) for c in pc['collections'].values())
    print(f"wrote {len(pc['collections'])} collections, {n} points")
    return gaps, pc


if __name__ == '__main__':
    main()
