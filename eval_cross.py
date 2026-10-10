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

Each point gets one verdict over all the segments that cover it on its
sheet: confirmed (some segment sees a gap there in a window its run kept,
none shows text), confirmed_weak (same, but only in windows those runs
rejected), mixed (a gap in one segment, text in another), conflict (text,
no gap), b_blank (only blank papyrus). The same tally is repeated with only
the segments of another family, since a second version of the same trace is
not an independent check: segments with the same winding range in their name
(`-w046-052`), or one named after the other (`..._20231210121321_v8`,
`..._5753_-1`), are one family.

The conflicts are concrete places for a person to check in the CT.
"""
import argparse
import glob
import json
import os
import re

import numpy as np
from scipy.spatial import cKDTree

SPACING = 89.4      # sheet spacing in voxels, from wrap length vs winding
TO_GRID = 0.4       # ds8 render pixel -> full mesh grid index (8 * 0.05)


def family(seg):
    """Segments with the same key are versions of one trace."""
    m = re.search(r'-w(\d+)-(\d+)', seg)
    if m:
        return f'w{int(m.group(1))}-{int(m.group(2))}'
    ids = re.findall(r'\d{14}', seg)
    m = re.search(r'-(\d{4})_', seg)       # 20260602204401-5753_-7 -> ...185753
    if len(ids) == 1 and m:
        return '*' + m.group(1)
    return '*' + (ids[-1] if ids else seg)[-4:]


def point_verdict(sts):
    sts = set(sts)
    gap = sts & {'confirmed', 'confirmed_weak'}
    if gap and 'conflict' in sts:
        return 'mixed'
    if 'confirmed' in sts:
        return 'confirmed'
    if gap:
        return 'confirmed_weak'
    if 'conflict' in sts:
        return 'conflict'
    if 'b_blank' in sts:
        return 'b_blank'
    return None


def tally(per_point, keep=lambda sa, sb: True):
    out = {}
    for (sa, _, _), seen in per_point.items():
        v = point_verdict(st for sb, st in seen if keep(sa, sb))
        if v:
            out[v] = out.get(v, 0) + 1
    return dict(per_point=out, points_checked=sum(out.values()))


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
    per_point = {}
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
                per_point.setdefault((sa, name, tuple(p)), []).append((sb, st))
                if st == 'conflict':
                    conflicts.append(dict(a=sa, b=sb, collection=name, p=p, normal_offset=dn))
            if c.get('confirmed', 0) + c.get('confirmed_weak', 0) + c.get('conflict', 0):
                per_pair.append(dict(a=sa, b=sb, **c))
    conf = counts.get('confirmed', 0) + counts.get('confirmed_weak', 0)
    every = tally(per_point)
    other = tally(per_point, lambda sa, sb: family(sa) != family(sb))
    report = dict(points_total=sum(len(S[s_]['pts']) for s_ in segs),
                  all_segments=every, other_family=other,
                  pair_counts=counts,
                  pair_confirmed_share=conf / max(1, conf + counts.get('conflict', 0)),
                  conflict_points=len({(c['a'], c['collection'], tuple(c['p'])) for c in conflicts}),
                  conflict_normal_offsets=np.percentile([c['normal_offset'] for c in conflicts], [10, 50, 90]).tolist() if conflicts else None,
                  pairs=per_pair, conflicts=conflicts)
    json.dump(report, open(os.path.join(a.out_root, 'cross_report.json'), 'w'), indent=1)
    for k in ('all_segments', 'other_family'):
        print(k, report[k]['points_checked'], 'of', report['points_total'], 'points checked:', report[k]['per_point'])
    print('pair level', counts, 'confirmed share', round(report['pair_confirmed_share'], 3))
    for p in sorted(per_pair, key=lambda q: -(q.get('confirmed', 0) + q.get('conflict', 0)))[:25]:
        print(p)


if __name__ == '__main__':
    main()
