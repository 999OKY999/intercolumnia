"""Run intercolumnia over every PHerc. Paris 4 segment with a 2.4um ink render.

Downloads each segment's ds8 ink render and its 2.4um tifxyz mesh from the
public bucket, runs the gap pipeline, keeps the outputs and a thumbnail, and
deletes the downloads before moving on.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BUCKET = 'https://vesuvius-challenge-open-data.s3.us-east-1.amazonaws.com'
VOLUME = '20260411134726'


def fetch(key, dest):
    subprocess.run(['curl', '-sS', '--retry', '4', '-o', dest, f'{BUCKET}/{key}'], check=True)


def pick_render(keys):
    good = [k for k in keys if VOLUME in k and '2.4um' in k and k.endswith('-ds8.jpg')]
    # Prefer the canonical recipe render when several exist.
    good.sort(key=lambda k: ('autoresearch' not in k, len(k)))
    return good[0] if good else None


def one(row, work, out_root, umbilicus):
    import intercolumnia as ic
    from overlay import overlay
    seg = row['seg']
    out = os.path.join(out_root, seg)
    if os.path.exists(os.path.join(out, 'done.json')):
        return json.load(open(os.path.join(out, 'done.json')))
    render = pick_render(row['ink'])
    mesh = next((m for m in row['meshes'] if m.endswith(f'{VOLUME}-2.4um.tifxyz')), None)
    if not render or not mesh:
        return {'segment': seg, 'skipped': 'no 2.4um render or mesh'}
    tmp = os.path.join(work, seg)
    os.makedirs(os.path.join(tmp, 'mesh'), exist_ok=True)
    try:
        fetch(render, os.path.join(tmp, 'render.jpg'))
        base = f'PHercParis4/segments/{seg}/mesh/{mesh}'
        for f in ('x.tif', 'y.tif', 'z.tif', 'meta.json'):
            fetch(f'{base}/{f}', os.path.join(tmp, 'mesh', f))
        args = argparse.Namespace(render=os.path.join(tmp, 'render.jpg'),
                                  mesh=os.path.join(tmp, 'mesh'), umbilicus=umbilicus,
                                  out=out, segment=seg, render_downsample=8.0, band=128)
        gaps, pc = ic.process(args)
        overlay(args.render, os.path.join(out, f'{seg}_gaps.npy'),
                os.path.join(out, f'{seg}_overlay.jpg'), 0.05)
        summary = {
            'segment': seg, 'render': render, 'mesh': mesh,
            'gaps': len(gaps),
            'collections': len(pc['collections']),
            'points': sum(len(c['points']) for c in pc['collections'].values()),
            'pitch_px': gaps[0]['pitch_px'] if gaps else None,
            'gap_spacing_px': gaps[0]['spacing_px'] if gaps else None,
            'per_gap': [{'x0': g['x0'], 'halfwidth': g['halfwidth'],
                         'windows_ok': int(g['ok'].sum()), 'steps': int(g['steps'].sum()),
                         'wrap_len_px': g['wrap_len_px'], 'switch_offset_px': g['switch_offset_px'],
                         'switch_offset_spacing_px': g['switch_offset_spacing_px'],
                         'noise_px': g['centre_noise_px'],
                         'checkable': g['switch_checkable']} for g in gaps],
        }
        json.dump(summary, open(os.path.join(out, 'done.json'), 'w'), indent=1, default=float)
        return summary
    except Exception as e:
        return {'segment': seg, 'error': repr(e), 'trace': traceback.format_exc()[-1500:]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('segments_json')
    ap.add_argument('umbilicus')
    ap.add_argument('out')
    ap.add_argument('--work', required=True)
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--only', nargs='*')
    a = ap.parse_args()
    rows = json.load(open(a.segments_json))
    if a.only:
        rows = [r for r in rows if r['seg'] in a.only]
    os.makedirs(a.out, exist_ok=True)
    results = []
    with ProcessPoolExecutor(a.workers) as ex:
        futs = [ex.submit(one, r, a.work, a.out, a.umbilicus) for r in rows]
        for f in futs:
            r = f.result()
            results.append(r)
            print(json.dumps({k: r.get(k) for k in ('segment', 'gaps', 'collections', 'points', 'error', 'skipped')}), flush=True)
    json.dump(results, open(os.path.join(a.out, 'summary.json'), 'w'), indent=1, default=float)


if __name__ == '__main__':
    main()
