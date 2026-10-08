"""Show two traces and their gap points on a CT cross-section.

Reads one z-plane of the public OME-Zarr volume with HTTP range requests
(chunks are uncompressed, so a plane inside a chunk is one contiguous byte
range), intersects both tifxyz meshes with that plane and draws them, with
the gap points of each trace, over the CT.
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np
import tifffile
from PIL import Image, ImageDraw

BUCKET = 'https://vesuvius-challenge-open-data.s3.us-east-1.amazonaws.com'
VOL = 'PHercParis4/volumes/20260411134726-2.400um-0.2m-78keV-masked.zarr'


def ct_plane(z, y0, y1, x0, x1, level=0, chunk=128):
    s = 2 ** level
    z, y0, y1, x0, x1 = z // s, y0 // s, y1 // s, x0 // s, x1 // s
    out = np.zeros((y1 - y0, x1 - x0), np.uint8)
    cz, dz = divmod(z, chunk)
    for cy in range(y0 // chunk, (y1 - 1) // chunk + 1):
        for cx in range(x0 // chunk, (x1 - 1) // chunk + 1):
            start = dz * chunk * chunk
            url = f'{BUCKET}/{VOL}/{level}/{cz}/{cy}/{cx}'
            r = subprocess.run(['curl', '-sS', '-r', f'{start}-{start + chunk * chunk - 1}', url],
                               capture_output=True)
            if len(r.stdout) != chunk * chunk:
                continue
            tile = np.frombuffer(r.stdout, np.uint8).reshape(chunk, chunk)
            ty0, tx0 = cy * chunk, cx * chunk
            ys, ye = max(y0, ty0), min(y1, ty0 + chunk)
            xs, xe = max(x0, tx0), min(x1, tx0 + chunk)
            out[ys - y0:ye - y0, xs - x0:xe - x0] = tile[ys - ty0:ye - ty0, xs - tx0:xe - tx0]
    return out


def mesh_section(folder, z, tol=12):
    if folder.endswith('.npz'):
        m = np.load(folder)
        x, y, zz = m['x'], m['y'], m['z']
        tol = max(tol, 12 * int(m['stride']))
    else:
        x = tifffile.imread(os.path.join(folder, 'x.tif'))
        y = tifffile.imread(os.path.join(folder, 'y.tif'))
        zz = tifffile.imread(os.path.join(folder, 'z.tif'))
    m = (zz != -1) & (np.abs(zz - z) < tol)
    return x[m], y[m]


def gap_points(seg_json):
    pc = json.load(open(seg_json))
    out = []
    for col in pc['collections'].values():
        for p in col['points'].values():
            out.append((col['name'], p['p']))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--z', type=int, required=True)
    ap.add_argument('--x', type=int, required=True)
    ap.add_argument('--y', type=int, required=True)
    ap.add_argument('--half', type=int, default=600)
    ap.add_argument('--mesh', nargs=2, required=True)
    ap.add_argument('--gaps', nargs=2, required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    y0, y1, x0, x1 = a.y - a.half, a.y + a.half, a.x - a.half, a.x + a.half
    img = ct_plane(a.z, y0, y1, x0, x1)
    scale = 1.0
    im = Image.fromarray(img).convert('RGB')
    d = ImageDraw.Draw(im)
    colours = [(60, 220, 90), (80, 170, 255)]
    for (mesh, gj), col in zip(zip(a.mesh, a.gaps), colours):
        mx, my = mesh_section(mesh, a.z)
        sel = (mx > x0) & (mx < x1) & (my > y0) & (my < y1)
        off = 0 if col == colours[0] else 1
        for px, py in zip(mx[sel], my[sel]):
            cx, cy = (px - x0) * scale, (py - y0) * scale
            d.ellipse([cx - 2 + off, cy - 2 + off, cx + 2 + off, cy + 2 + off], fill=col)
        for name, (px, py, pz) in gap_points(gj):
            if abs(pz - a.z) < 200 and x0 < px < x1 and y0 < py < y1:
                r = 9
                d.ellipse([px - x0 - r, py - y0 - r, px - x0 + r, py - y0 + r], outline=col, width=3)
    im.save(a.out)
    print('saved', a.out)


if __name__ == '__main__':
    main()
