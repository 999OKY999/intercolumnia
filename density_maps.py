"""Save a coarse ink-density map of each segment's render (for cross checks)."""
import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
BUCKET = 'https://vesuvius-challenge-open-data.s3.us-east-1.amazonaws.com'
STEP, WIN, BIN = 128, 512, 16


def one(seg, render, out_root, work):
    import intercolumnia as ic
    dest = os.path.join(out_root, seg, f'{seg}_density.npz')
    if os.path.exists(dest):
        return seg, 'exists'
    tmp = os.path.join(work, 'd_' + seg + '.jpg')
    try:
        subprocess.run(['curl', '-sS', '--retry', '4', '-o', tmp, f'{BUCKET}/{render}'], check=True)
        a, papyrus, ink = ic.load_render(tmp)
        H, W = ink.shape
        nb = W // BIN
        ink_c = ink[:, :nb * BIN].reshape(H, nb, BIN).sum(2).astype(np.float32)
        pap_c = papyrus[:, :nb * BIN].reshape(H, nb, BIN).sum(2).astype(np.float32)
        ci, cp = np.cumsum(ink_c, 0), np.cumsum(pap_c, 0)
        centres = np.arange(STEP // 2, H, STEP)
        lo = np.clip(centres - WIN // 2, 0, H - 1)
        hi = np.clip(centres + WIN // 2, 0, H - 1)
        den = (ci[hi] - ci[lo]) / np.maximum(cp[hi] - cp[lo], 1)
        cov = (cp[hi] - cp[lo]) / ((hi - lo)[:, None] * BIN)
        np.savez_compressed(dest, density=den.astype(np.float16), coverage=cov.astype(np.float16),
                            step=STEP, bin=BIN, shape=np.array([H, W]))
        return seg, 'ok'
    except Exception as e:
        return seg, repr(e)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main(out_root, work):
    jobs = []
    for seg in sorted(os.listdir(out_root)):
        done = os.path.join(out_root, seg, 'done.json')
        if os.path.exists(done):
            d = json.load(open(done))
            if d.get('collections'):
                jobs.append((seg, d['render']))
    with ProcessPoolExecutor(3) as ex:
        for seg, st in ex.map(one, *zip(*jobs), [out_root] * len(jobs), [work] * len(jobs)):
            print(seg, st, flush=True)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
