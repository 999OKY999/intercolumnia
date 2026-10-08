"""Save a decimated copy of each segment's 2.4um mesh next to its outputs."""
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import tifffile

BUCKET = 'https://vesuvius-challenge-open-data.s3.us-east-1.amazonaws.com'
STRIDE = 5


def one(seg, mesh, out_root, work):
    dest = os.path.join(out_root, seg, f'{seg}_mesh_s{STRIDE}.npz')
    if os.path.exists(dest):
        return seg, 'exists'
    tmp = os.path.join(work, 'm_' + seg)
    os.makedirs(tmp, exist_ok=True)
    try:
        arrs = {}
        for f in 'xyz':
            p = os.path.join(tmp, f + '.tif')
            subprocess.run(['curl', '-sS', '--retry', '4', '-o', p,
                            f'{BUCKET}/PHercParis4/segments/{seg}/mesh/{mesh}/{f}.tif'], check=True)
            arrs[f] = tifffile.imread(p)[::STRIDE, ::STRIDE].astype(np.float32)
            os.remove(p)
        np.savez_compressed(dest, stride=STRIDE, **arrs)
        return seg, 'ok'
    except Exception as e:
        return seg, repr(e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(out_root, work):
    jobs = []
    for seg in sorted(os.listdir(out_root)):
        done = os.path.join(out_root, seg, 'done.json')
        if os.path.exists(done):
            d = json.load(open(done))
            if d.get('collections'):
                jobs.append((seg, d['mesh']))
    with ThreadPoolExecutor(3) as ex:
        for seg, st in ex.map(lambda j: one(j[0], j[1], out_root, work), jobs):
            print(seg, st, flush=True)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
