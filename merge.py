"""Merge per-segment outputs into one same-winding point-collection file.

Points are written in voxels of the 2.4 um volume 20260411134726. The
spiral-input dataset is fitted in the 4x downsampled frame (9.6 um, the
`voxel_size_um` in its spiral-scroll.json); pass --scale 0.25 for that frame.
"""
import argparse
import glob
import json
import math
import os

# Metadata fields holding scan coordinates or lengths in voxels; scaled with
# the points so every voxel quantity in a file shares one frame.
VOXEL_KEYS = ('line_start_xyz', 'radius_vox')


def clean(v, scale=1.0):
    """NaN -> None (strict JSON), optionally scaling every number."""
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return round(v * scale, 3) if scale != 1.0 else v
    if isinstance(v, list):
        return [clean(x, scale) for x in v]
    if isinstance(v, dict):
        return {k: clean(x, scale) for k, x in v.items()}
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out_root')
    ap.add_argument('dest')
    ap.add_argument('--scale', type=float, default=1.0,
                    help='multiply coordinates (0.25 for the 9.6 um frame)')
    a = ap.parse_args()
    merged, cid, pid = {}, 1, 1
    for path in sorted(glob.glob(os.path.join(a.out_root, '*', '*_intercolumnar_same_windings.json'))):
        pc = json.load(open(path))
        for _, col in sorted(pc['collections'].items(), key=lambda kv: int(kv[0])):
            pts = {}
            for _, p in sorted(col['points'].items(), key=lambda kv: int(kv[0])):
                q = dict(p, p=[round(c * a.scale, 3) for c in p['p']])
                pts[str(pid)] = q
                pid += 1
            meta = {k: clean(v, a.scale if k in VOXEL_KEYS else 1.0)
                    for k, v in col['metadata'].items()}
            meta['coordinate_scale_from_2p4um'] = a.scale
            merged[str(cid)] = dict(col, points=pts, metadata=meta)
            cid += 1
    json.dump({'vc_pointcollections_json_version': '1', 'collections': merged},
              open(a.dest, 'w'), allow_nan=False)
    print(f'{cid - 1} collections, {pid - 1} points -> {a.dest}')


if __name__ == '__main__':
    main()
