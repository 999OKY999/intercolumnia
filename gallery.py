"""Build the verification gallery page from a batch output folder.

Writes index.html plus img/<segment>.jpg into the site folder: headline
figures, the wrap-length chart (with the windings where a one-turn sheet
switch hides inside the column layout), the cross-trace check, and one
overlay per segment so every constraint can be checked by eye.
"""
import argparse
import html
import json
import os
import re
import shutil

import numpy as np

YES = '<span class="ok">yes</span>'
NO = '<span class="no">no</span>'


def esc(s):
    return html.escape(str(s))


def min_shift(g):
    """The smaller of the two predicted switch shifts (pitch, own spacing)."""
    v = [abs(x) for x in (g['switch_offset_px'], g.get('switch_offset_spacing_px'))
         if x is not None and x == x]
    return min(v) if v else float('nan')


def chart_svg(W, C, slope, intercept, pitch):
    w, h = 640, 300
    ml, mr, mt, mb = 56, 16, 14, 40
    xmax, ymax = 140, 80000
    sx = lambda v: ml + v / xmax * (w - ml - mr)
    sy = lambda v: h - mb - v / ymax * (h - mt - mb)
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-labelledby="chart-t chart-d" class="chart">',
             '<title id="chart-t">Length of one turn against winding number</title>',
             '<desc id="chart-d">Each dot is a traced segment; the line is a straight fit. '
             'Dashed lines mark where one turn equals one or two column pitches.</desc>']
    for yv in range(0, ymax + 1, 20000):
        parts.append(f'<line x1="{ml}" x2="{w - mr}" y1="{sy(yv):.1f}" y2="{sy(yv):.1f}" class="grid"/>')
        parts.append(f'<text x="{ml - 8}" y="{sy(yv) + 4:.1f}" text-anchor="end" class="tick">{yv // 1000}k</text>')
    for xv in range(0, xmax + 1, 20):
        parts.append(f'<text x="{sx(xv):.1f}" y="{h - mb + 18}" text-anchor="middle" class="tick">{xv}</text>')
    for k in (1, 2):
        wk = (k * pitch - intercept) / slope
        parts.append(f'<line x1="{sx(wk):.1f}" x2="{sx(wk):.1f}" y1="{mt}" y2="{h - mb}" class="alias"/>')
        parts.append(f'<text x="{sx(wk) + 5:.1f}" y="{mt + 12}" class="alias-t">turn = {k}× pitch, w≈{wk:.0f}</text>')
        parts.append(f'<line x1="{ml}" x2="{w - mr}" y1="{sy(k * pitch):.1f}" y2="{sy(k * pitch):.1f}" class="pitch"/>')
    parts.append(f'<line x1="{sx(0):.1f}" y1="{sy(intercept):.1f}" x2="{sx(xmax):.1f}" y2="{sy(intercept + slope * xmax):.1f}" class="fit"/>')
    for x, y in zip(W, C):
        parts.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3.2" class="dot"/>')
    parts.append(f'<text x="{(ml + w - mr) / 2:.0f}" y="{h - 6}" text-anchor="middle" class="axis">winding number (from segment name)</text>')
    parts.append(f'<text x="14" y="{(mt + h - mb) / 2:.0f}" text-anchor="middle" class="axis" transform="rotate(-90 14 {(mt + h - mb) / 2:.0f})">one turn (voxels)</text>')
    parts.append('</svg>')
    return ''.join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out_root')
    ap.add_argument('site')
    ap.add_argument('--ct', help='CT check image from ct_check.py, shown on the page')
    a = ap.parse_args()
    os.makedirs(os.path.join(a.site, 'img'), exist_ok=True)
    summary = json.load(open(os.path.join(a.out_root, 'summary.json')))
    rows = [d for d in summary if d.get('collections')]
    for d in rows:
        seg = d['segment']
        shutil.copy(os.path.join(a.out_root, seg, f'{seg}_overlay.jpg'), os.path.join(a.site, 'img', f'{seg}.jpg'))
    if a.ct:
        shutil.copy(a.ct, os.path.join(a.site, 'img', 'ct_check.png'))

    # Wrap length against winding number, and the column pitch.
    W, C, spacings = [], [], []
    for d in summary:
        g = d.get('per_gap') or []
        xs = sorted(q['x0'] for q in g)
        spacings += list(np.diff(xs))
        m = re.search(r'-w(\d+)-(\d+)', d['segment'])
        wl = [q['wrap_len_px'] for q in g if q['wrap_len_px'] == q['wrap_len_px']]
        if m and wl:
            W.append((int(m.group(1)) + int(m.group(2))) / 2)
            C.append(float(np.median(wl)) * 8)
    W, C = np.array(W), np.array(C)
    slope, intercept = np.polyfit(W, C, 1)
    sp = np.array(spacings)
    pitch = float(np.median(sp[(sp > 3000) & (sp < 5000)])) * 8
    spacing_vox = slope / (2 * np.pi)

    # Gap spacings of about one turn instead of a whole number of pitches.
    turn_like, turn_w = 0, []
    for d in summary:
        g = d.get('per_gap') or []
        xs = sorted(q['x0'] for q in g)
        wl = [q['wrap_len_px'] for q in g if q['wrap_len_px'] == q['wrap_len_px']]
        m = re.search(r'-w(\d+)-(\d+)', d['segment'])
        if not wl:
            continue
        for q in np.diff(xs):
            if q > 5000 and abs(q / np.median(wl) - 1) < 0.05:
                turn_like += 1
                if m:
                    turn_w += [int(m.group(1)), int(m.group(2))]
    big = int((sp > 5000).sum())

    cross = json.load(open(os.path.join(a.out_root, 'cross_report.json')))
    total = cross['points_total']

    def shares(t):
        pp, n = t['per_point'], max(1, t['points_checked'])
        return dict(n=t['points_checked'], kept=pp.get('confirmed', 0), weak=pp.get('confirmed_weak', 0),
                    mixed=pp.get('mixed', 0), conflict=pp.get('conflict', 0), blank=pp.get('b_blank', 0),
                    pct=lambda v: f'{100 * v / n:.0f}%')
    every, other = shares(cross['all_segments']), shares(cross['other_family'])
    n_col = sum(d['collections'] for d in rows)
    n_pts = sum(d['points'] for d in rows)
    n_render = sum(1 for d in summary if not d.get('skipped'))

    figures = [
        (f'{n_col}', 'same-winding lines'),
        (f'{n_pts:,}', 'points in the volume'),
        (f'{len(rows)} / {n_render}', 'segments with an ink render that gave lines'),
        (every['pct'](every['kept'] + every['weak']),
         f'of {every["n"]:,} cross-checked points fall in a gap of another trace and on text in none'),
        (f'{spacing_vox * 2.4:.0f} µm', 'sheet spacing from the data'),
    ]
    fig_html = ''.join(f'<div class="fig"><span class="v">{esc(v)}</span><span class="k">{esc(k)}</span></div>'
                       for v, k in figures)

    cards = []
    for d in rows:
        seg = d['segment']
        gl = ''.join(
            f"<tr><td>{i}</td><td>{g['windows_ok']}</td><td>{g['steps']}</td>"
            f"<td>{g['wrap_len_px'] * 8:,.0f}</td><td>{min_shift(g) * 8:,.0f}</td>"
            f"<td>{YES if g['checkable'] else NO}</td></tr>"
            for i, g in enumerate(d['per_gap']) if g['wrap_len_px'] == g['wrap_len_px'])
        cards.append(f"""
<article class="seg" id="s{esc(seg)}">
  <header><h3>{esc(seg)}</h3><p>{d['collections']} line{'' if d['collections'] == 1 else 's'} · {d['points']} points</p></header>
  <div class="scroll"><img loading="lazy" src="img/{esc(seg)}.jpg" alt="Ink render of segment {esc(seg)} with its traced gaps"></div>
  <div class="scroll"><table><thead><tr><th>gap</th><th>kept windows</th><th>step flags</th><th>one turn (vox)</th><th>switch would move gap (vox)</th><th>switch visible</th></tr></thead><tbody>{gl}</tbody></table></div>
</article>""")

    conflicts = ''.join(
        f"<tr><td>{esc(c['a'])}</td><td>{esc(c['b'])}</td><td>{', '.join(f'{v:.0f}' for v in c['p'])}</td><td>{c['normal_offset']:.0f}</td></tr>"
        for c in cross['conflicts'][:40])

    ct_html = ''
    if os.path.exists(os.path.join(a.site, 'img', 'ct_check.png')):
        ct_html = ('  <figure class="ct">\n'
                   '    <img src="img/ct_check.png" alt="CT cross-section with two traces drawn over the papyrus sheets" width="1000" height="1000">\n'
                   '    <figcaption>One plane of the 2.4 µm scan with two traces of windings 28 to 37 drawn over it, green and blue '
                   '(<code>ct_check.py</code>). They follow the same sheets on every wrap that crosses this window. '
                   'Rings mark gap points of each trace near this plane.</figcaption>\n'
                   '  </figure>\n')

    page = f"""<title>Intercolumnar Gaps of Paris 4</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Sans+Condensed:wght@500;600&display=swap">
<style>
/* Layout: a single reading column for the method and figures, then full-width segment strips that scroll sideways inside themselves. */
:root {{
  --bg: #f6f7f8; --fg: #1b2026; --muted: #5d6874; --line: #d9dee3; --panel: #ffffff;
  --accent: #2f6b8a; --ok: #2e8b57; --bad: #c0392b; --amber: #c88a12; --flag: #b03ab0;
  --display: "IBM Plex Sans Condensed", "Arial Narrow", sans-serif;
  --body: "IBM Plex Sans", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #12161a; --fg: #e3e8ec; --muted: #94a0ab; --line: #2a323a; --panel: #192026;
  --accent: #7fb3d0; --ok: #5cc48a; --bad: #e0675a; --amber: #e2ac3f; --flag: #d77ad7; color-scheme: dark }} }}
:root[data-theme="dark"] {{
  --bg: #12161a; --fg: #e3e8ec; --muted: #94a0ab; --line: #2a323a; --panel: #192026;
  --accent: #7fb3d0; --ok: #5cc48a; --bad: #e0675a; --amber: #e2ac3f; --flag: #d77ad7; color-scheme: dark }}
body {{ background: var(--bg); color: var(--fg); font: 15px/1.6 var(--body); }}
.wrap {{ max-width: 1120px; margin: 0 auto; padding-inline: 16px; padding-block: 28px 56px; }}
.col {{ max-width: 68ch; }}
h1 {{ font: 600 clamp(26px, 4vw, 38px)/1.1 var(--display); letter-spacing: -0.01em; margin: 0 0 10px; text-wrap: balance; }}
h2 {{ font: 600 21px/1.2 var(--display); margin: 40px 0 8px; text-wrap: balance; }}
h3 {{ font: 500 14px/1.3 var(--mono); margin: 0; }}
p {{ margin: 0 0 12px; }}
.eyebrow {{ font: 500 12px/1 var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--accent); margin-bottom: 12px; }}
.lead {{ color: var(--muted); font-size: 16px; }}
.figs {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 1px; background: var(--line); border: 1px solid var(--line); margin: 24px 0 8px; }}
.fig {{ background: var(--panel); padding: 14px 16px; display: flex; flex-direction: column; gap: 4px; min-width: 0; }}
.fig .v {{ font: 600 26px/1 var(--display); font-variant-numeric: tabular-nums; }}
.fig .k {{ font-size: 13px; color: var(--muted); }}
.chart {{ width: 100%; max-width: 680px; height: auto; display: block; margin: 8px 0; }}
.chart .grid {{ stroke: var(--line); stroke-width: 1; }}
.chart .tick, .chart .axis {{ fill: var(--muted); font: 11px var(--mono); }}
.chart .fit {{ stroke: var(--accent); stroke-width: 2; fill: none; }}
.chart .dot {{ fill: var(--fg); }}
.chart .alias {{ stroke: var(--flag); stroke-dasharray: 4 4; stroke-width: 1.5; }}
.chart .alias-t {{ fill: var(--flag); font: 11px var(--mono); }}
.chart .pitch {{ stroke: var(--flag); stroke-opacity: .35; stroke-width: 1; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 13px; color: var(--muted); margin: 8px 0 16px; }}
.legend i {{ display: inline-block; width: 18px; height: 3px; vertical-align: middle; margin-right: 6px; }}
.legend i.c {{ width: 9px; height: 9px; border-radius: 50%; background: transparent !important; border: 2px solid; }}
.segs {{ display: grid; gap: 18px; }}
.seg {{ border-top: 1px solid var(--line); padding-top: 12px; min-width: 0; display: grid; gap: 8px; }}
.seg header {{ display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 12px; }}
.seg header p {{ margin: 0; color: var(--muted); font-size: 13px; }}
.scroll {{ overflow-x: auto; max-width: 100%; }}
.scroll img {{ display: block; max-width: none; height: 220px; width: auto; border-radius: 3px; background: #000; }}
table {{ border-collapse: collapse; font: 12px/1.4 var(--mono); font-variant-numeric: tabular-nums; }}
th {{ font-weight: 500; color: var(--muted); text-align: right; padding: 3px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; }}
td {{ text-align: right; padding: 3px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; }}
.ok {{ color: var(--ok); }} .no {{ color: var(--bad); }}
.ct {{ margin: 16px 0 8px; max-width: 560px; }}
.ct img {{ width: 100%; height: auto; display: block; border-radius: 3px; }}
.ct figcaption {{ font-size: 13px; color: var(--muted); margin-top: 6px; }}
code {{ font: 13px var(--mono); }}
a {{ color: var(--accent); }}
a:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
</style>
<div class="wrap">
  <div class="col">
    <div class="eyebrow">Vesuvius Challenge · PHerc. Paris 4 · winding constraints</div>
    <h1>Intercolumnar gaps of Paris 4 as same-winding constraints</h1>
    <p class="lead">The blank strip between two columns of text runs down a single wrap of the papyrus. This tool finds those strips on the ink renders of traced segments, follows each one down the column, maps it into the scan through the segment's mesh, and writes it as a same-winding point collection that the spiral fit reads as it is.</p>
  </div>
  <div class="figs">{fig_html}</div>

  <div class="col">
    <h2>How far apart the columns are, and when a sheet switch can hide</h2>
    <p>The median spacing of neighbouring gaps (counting spacings between 3,000 and 5,000 render pixels, {int(((sp > 3000) & (sp < 5000)).sum())} of {len(sp)}) puts the column pitch at {pitch:,.0f} voxels along the sheet ({pitch * 2.4e-3:.1f} mm). The length of one turn grows by {slope:,.0f} voxels per winding, from the winding numbers in {len(W)} segment names, which puts the sheet spacing at {spacing_vox:.1f} voxels ({spacing_vox * 2.4:.0f} µm). If a trace slips one wrap, the gap below the slip appears shifted sideways by the length of one turn modulo the column pitch. Where one turn is close to a whole number of pitches (dashed lines) that shift is too small to see, so the gaps cannot catch a switch there. The tool works the shift out twice, with this pitch and with the segment's own gap spacing, and uses the smaller one; each line records whether a switch would have been visible.</p>
    <p>{big} gap spacings are larger than 5,000 pixels; {turn_like} of them match the length of one turn within 5%{f" (segments of windings {min(turn_w)} to {max(turn_w)})" if turn_w else ""}. A strip that repeats every turn may be a crack or fold running through the wraps rather than a gap between columns, and a slip would not move it; this is not yet explained, and lines on such segments are marked as unable to show a switch.</p>
  </div>
  {chart_svg(W, C, slope, intercept, pitch)}

  <div class="col">
    <h2>Checked against a second trace</h2>
    <p>Many windings of Paris 4 were traced more than once. For every gap point the check looks for other segments whose mesh passes through the same spot on the same sheet (within half a sheet spacing along the surface normal), maps the spot into each one's render, and asks what is there. Every point gets one verdict over all of them. Segments with the same winding range in their name, or named after an older trace, are versions of one trace, so the tally is repeated with only the segments of another family.</p>
  </div>
  <div class="scroll"><table><thead><tr><th></th><th>all other segments</th><th>other families only</th></tr></thead><tbody>
    <tr><td>points with a second trace on the same sheet</td><td>{every['n']:,} of {total:,}</td><td>{other['n']:,} of {total:,}</td></tr>
    <tr><td>in a gap that trace kept, on text in none</td><td>{every['kept']:,} ({every['pct'](every['kept'])})</td><td>{other['kept']:,} ({other['pct'](other['kept'])})</td></tr>
    <tr><td>in a gap that trace found but rejected, on text in none</td><td>{every['weak']:,} ({every['pct'](every['weak'])})</td><td>{other['weak']:,} ({other['pct'](other['weak'])})</td></tr>
    <tr><td>only on blank papyrus that no run kept as a gap</td><td>{every['blank']:,} ({every['pct'](every['blank'])})</td><td>{other['blank']:,} ({other['pct'](other['blank'])})</td></tr>
    <tr><td>in a gap in one trace and on text in another</td><td class="no">{every['mixed']:,} ({every['pct'](every['mixed'])})</td><td class="no">{other['mixed']:,} ({other['pct'](other['mixed'])})</td></tr>
    <tr><td>on text, in no gap</td><td class="no">{every['conflict']:,} ({every['pct'](every['conflict'])})</td><td class="no">{other['conflict']:,} ({other['pct'](other['conflict'])})</td></tr>
  </tbody></table></div>
  <div class="col">
    <p>A point on text in another trace means one of the two traces carries the wrong content there: a sheet switch in either trace, or a false gap. The {len(cross['conflicts']):,} such checks (on {cross['conflict_points']:,} points) are in <code>results/cross_report.json</code> with their coordinates, so each can be opened in the scan; the first {min(40, len(cross['conflicts']))} are listed here.</p>
  </div>
  <div class="scroll"><table><thead><tr><th>gap from</th><th>second trace</th><th>x, y, z (voxels)</th><th>offset across sheet</th></tr></thead><tbody>{conflicts}</tbody></table></div>
{ct_html}
  <h2>Every segment</h2>
  <div class="legend">
    <span><i style="background:var(--ok)"></i>kept window</span>
    <span><i style="background:var(--bad)"></i>rejected window</span>
    <span><i class="c" style="border-color:var(--amber)"></i>line starts of the next column</span>
    <span><i class="c" style="border-color:var(--flag)"></i>possible sheet switch</span>
  </div>
  <div class="segs">{''.join(cards)}</div>
</div>"""
    open(os.path.join(a.site, 'index.html'), 'w').write(page)
    print(f'{len(rows)} segments; pitch {pitch:.0f} vox; spacing {spacing_vox:.1f} vox; '
          f'blind windings {[(k * pitch - intercept) / slope for k in (1, 2)]}; '
          f'turn-like spacings {turn_like} of {big}; checked {every["n"]}, other family {other["n"]}')


if __name__ == '__main__':
    main()
