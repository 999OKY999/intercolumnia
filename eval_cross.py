"""Cross-trace confirmation of gap constraints, without ground truth.

For every gap point found on segment A, look for another segment B whose
mesh passes through the same spot on the same sheet (within half a sheet
spacing along B's surface normal). Map the spot into B's render through B's
mesh and ask whether B's render shows a gap there too:

  confirmed  B's mesh is on the same sheet and B sees a gap at that spot
  b_blank    B's mesh is on the same sheet and B's render is blank papyrus
             there, but B's own run did not keep a gap at that spot
  conflict   B's mesh is on the same sheet and B's render shows text there
             (one of the two traces carries the wrong content: a sheet
             switch in A or B, or a false gap in A)
  b_edge     the spot falls at the edge of B's render
  other      B passes nearby only on other wraps, or not at all

The confirmed share is a precision estimate for the constraints, and the
conflicts are concrete places for a person to check in the CT.
"""
import argparse
import glob
import json
import os

import numpy as np
from scipy.spatial import cKDTree

SPACING = 89.4      # sheet spacing in voxels, from wrap length vs winding
TO_GRID = 0.4       # ds8 render pixel -> full mesh grid index (8 * 0.05)


def load_segment(out_root, seg):
    m = np.load(os.path.join(out_root, seg, f'{seg}_mesh_s5.npz'))
    s = int(m['stride'])
    P = np.stack([m['x'], m['y'], m['z']], -1).astype(np.float64)
    valid = (m['x'] != -1) & (m['y'] != -1) & (m['z'] != -1)
    gu = np.gradient(P, axis=0)
    gv = np.gradient(P, axis=1)
    N = np.cross(gu, gv)
    N /= np.maximum(np.linalg.norm(N, axis=-1, keepdims=True), 1e-9)
    ok = valid.copy()
    ok[1:] &= valid[:-1]
    ok[:-1] &= valid[1:]
    ok[:, 1:] &= valid[:, :-1]
    ok[:, :-1] &= valid[:, 1:]
    ii, jj = np.nonzero(ok)
    gaps = np.load(os.path.join(out_root, seg, f'{seg}_gaps.npy'), allow_pickle=True)
    pc = json.load(open(os.path.join(out_root, seg, f'{seg}_intercolumnar_same_windings.json')))
    pts = []
    for col in pc['collections'].values():
        for k, p in col['points'].items():
            pts.append((col['name'], p['p']))
    lo, hi = P[ok].min(0), P[ok].max(0)
    den = None
    dp = os.path.join(out_root, seg, f'{seg}_density.npz')
    if os.path.exists(dp):
        dz = np.load(dp)
        d = dz['density'].astype(np.float32)
        cv = dz['coverage'].astype(np.float32)
        text = float(np.percentile(d[cv > 0.9], 75)) if (cv > 0.9).any() else np.nan
        den = dict(d=d, cov=cv, step=int(dz['step']), bin=int(dz['bin']), text=text)
    return dict(seg=seg, P=P[ok], N=N[ok], row=ii * s / TO_GRID, col=jj * s / TO_GRID,
                tree=cKDTree(P[ok]), gaps=gaps, pts=pts, lo=lo, hi=hi, den=den)


def classify(p, B, radius=160.0, max_tangent=80.0):
    idx = B['tree'].query_ball_point(p, radius)
    if not idx:
        return 'absent', None
    idx = np.array(idx)
    d = p - B['P'][idx]
    dn = np.einsum('ij,ij->i', d, B['N'][idx])
    dt = np.linalg.norm(d - dn[:, None] * B['N'][idx], axis=1)
    near = dt < max_tangent
    if not near.any():
        return 'absent', None
    k = idx[near][np.argmin(np.abs(dn[near]))]
    best = float(np.min(np.abs(dn[near])))
    if best > 0.5 * SPACING:
        return 'other_wrap', best
    row, col = B['row'][k], B['col'][k]
    for g in B['gaps']:
        tr = g['trace']
        rows = np.array([r['row'] for r in tr])
        w = int(np.argmin(np.abs(rows - row)))
        if abs(rows[w] - row) > 200:
            continue
        if abs(tr[w]['x'] - col) <= g['halfwidth']:
            return ('confirmed' if g['ok'][w] else 'confirmed_weak'), best
    # No traced gap in B there: does B's render show text, or blank papyrus?
    dn_ = B['den']
    if dn_ is not None:
        r = int(np.clip(round((row - dn_['step'] // 2) / dn_['step']), 0, dn_['d'].shape[0] - 1))
        c0 = int(col // dn_['bin'])
        sl = slice(max(0, c0 - 6), c0 + 7)          # +-100 render px around the spot
        if dn_['cov'][r, sl].min() < 0.9:
            return 'b_edge', best
        if dn_['d'][r, sl].mean() < 0.4 * dn_['text']:
            return 'b_blank', best
        return 'conflict', best
    return 'conflict_unchecked', best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out_root')
    a = ap.parse_args()
    segs = sorted(os.path.basename(os.path.dirname(p))
                  for p in glob.glob(os.path.join(a.out_root, '*', '*_mesh_s5.npz')))
    S = {s: load_segment(a.out_root, s) for s in segs}
    counts = {}
    per_pair = []
    conflicts = []
    for sa in segs:
        A = S[sa]
        for sb in segs:
            if sb == sa:
                continue
            B = S[sb]
            if np.any(A['hi'] < B['lo'] - 200) or np.any(B['hi'] < A['lo'] - 200):
                continue
            c = {}
            for name, p in A['pts']:
                st, dn = classify(np.array(p, float), B)
                c[st] = c.get(st, 0) + 1
                counts[st] = counts.get(st, 0) + 1
                if st == 'conflict':
                    conflicts.append(dict(a=sa, b=sb, collection=name, p=p, normal_offset=dn))
            if c.get('confirmed', 0) + c.get('confirmed_weak', 0) + c.get('conflict', 0):
                per_pair.append(dict(a=sa, b=sb, **c))
    conf = counts.get('confirmed', 0) + counts.get('confirmed_weak', 0)
    report = dict(counts=counts,
                  confirmed_share=conf / max(1, conf + counts.get('conflict', 0)),
                  blank_or_confirmed_share=(conf + counts.get('b_blank', 0)) / max(1, conf + counts.get('b_blank', 0) + counts.get('conflict', 0)),
                  pairs=per_pair, conflicts=conflicts)
    json.dump(report, open(os.path.join(a.out_root, 'cross_report.json'), 'w'), indent=1)
    print(counts, 'confirmed share', round(report['confirmed_share'], 3),
          'blank-or-confirmed', round(report['blank_or_confirmed_share'], 3))
    for p in sorted(per_pair, key=lambda q: -(q.get('confirmed', 0) + q.get('conflict', 0)))[:25]:
        print(p)


if __name__ == '__main__':
    main()
