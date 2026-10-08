"""Append intercolumnar collections to a dataset's same_windings.json.

Collection and point ids are offset past the largest ids already in the
dataset file so nothing collides. Writes a new file; the original is not
touched.
"""
import argparse
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dataset_same_windings')
    ap.add_argument('intercolumnar')
    ap.add_argument('dest')
    a = ap.parse_args()
    base = json.load(open(a.dataset_same_windings))
    extra = json.load(open(a.intercolumnar))
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
    out = dict(base, collections=cols)
    out.setdefault('vc_pointcollections_json_version', '1')
    json.dump(out, open(a.dest, 'w'))
    print(f'{len(base.get("collections", {}))} existing + {added} intercolumnar collections -> {a.dest}')


if __name__ == '__main__':
    main()
