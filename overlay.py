import sys, numpy as np
from PIL import Image, ImageDraw
Image.MAX_IMAGE_PIXELS = None


def overlay(render, gaps_npy, out_png, scale=0.1, x_range=None):
    im = Image.open(render).convert('L')
    W, H = im.size
    if x_range:
        im = im.crop((x_range[0], 0, x_range[1], H))
        ox = x_range[0]
    else:
        ox = 0
    im = im.resize((int(im.size[0] * scale), int(H * scale))).convert('RGB')
    d = ImageDraw.Draw(im)
    g = np.load(gaps_npy, allow_pickle=True)
    for gg in g:
        tr = gg['trace']
        hw = gg['halfwidth']
        for k in range(len(tr) - 1):
            x0, y0 = (tr[k]['x'] - ox) * scale, tr[k]['row'] * scale
            x1, y1 = (tr[k + 1]['x'] - ox) * scale, tr[k + 1]['row'] * scale
            col = (60, 220, 90) if gg['ok'][k] and gg['ok'][k + 1] else (230, 60, 60)
            d.line([(x0, y0), (x1, y1)], fill=col, width=max(1, int(3 * scale / 0.1)))
            r = max(1, int(4 * scale / 0.1))
            ex = (tr[k]['edge_right'] - ox) * scale
            c2 = (255, 0, 255) if gg.get('steps') is not None and gg['steps'][k] else (255, 190, 40)
            d.ellipse([ex - r, y0 - r, ex + r, y0 + r], outline=c2)
    im.save(out_png)


if __name__ == '__main__':
    xr = tuple(map(int, sys.argv[4].split(','))) if len(sys.argv) > 4 else None
    overlay(sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[5]) if len(sys.argv) > 5 else 0.1, xr)
