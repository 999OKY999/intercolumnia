"""Append intercolumnar collections to a dataset's same_windings.json.

Collection and point ids are offset past the largest ids already in the
dataset file so nothing collides. Writes a new file; the original is not
touched. Both inputs must be valid VC point collections (version "1", every
collection named, every point with a 3-element p), because the fitter treats a
file it cannot load as empty without stopping.
"""
import argparse
import json
import math


def check(doc, path):
    if doc.get('vc_pointcollections_json_version') != '1':
        raise SystemExit(f'{path}: vc_pointcollections_json_version is not the string "1"')
    for cid, col in doc.get('collections', {}).items():
        int(cid)
        if 'name' not in col:
            raise SystemExit(f'{path}: collection {cid} has no name')
        for pid, p in col.get('points', {}).items():
            int(pid)
            if len(p.get('p') or []) != 3:
                raise SystemExit(f'{path}: point {pid} of collection {cid} has no [x, y, z]')


def finite(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, list):
        return [finite(x) for x in v]
    if isinstance(v, dict):
        return {k: finite(x) for k, x in v.items()}
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dataset_same_windings')
    ap.add_argument('intercolumnar')
    ap.add_argument('dest')
    a = ap.parse_args()
    base = json.load(open(a.dataset_same_windings))
    extra = json.load(open(a.intercolumnar))
    check(base, a.dataset_same_windings)
    check(extra, a.intercolumnar)
    cols = dict(base.get('collections', {}))
    next_c = max([int(k) for k in cols] + [0]) + 1
    next_p = max([int(pk) for c in cols.values() for pk in c.get('points', {})] + [0]) + 1
    added = 0
    for _, col in sorted(extra['collections'].items(), key=lambda kv: int(kv[0])):
        pts = {}
        for _, p in sorted(col['points'].items(), key=lambda kv: int(kv[0])):
            pts[str(next_p)] = p
            next_p += 1
        cols[str(next_c)] = dict(col, points=pts)
        next_c += 1
        added += 1
    out = finite(dict(base, collections=cols))
    json.dump(out, open(a.dest, 'w'), allow_nan=False)
    print(f'{len(base.get("collections", {}))} existing + {added} intercolumnar collections -> {a.dest}')


if __name__ == '__main__':
    main()
