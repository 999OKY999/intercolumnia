"""Compare the A/B fits written by run_ab_test.sh.

Usage: compare.py OUT_DIR SEED [SEED ...]

Reads, for every seed and arm, OUT_DIR/ab_<arm>_s<seed>/:
  satisfaction_metrics_fitted.json   patch satisfaction summary
  satisfied_fitted.json              per-patch and per-strip satisfaction
  meshes/common_ab_<arm>_s<seed>/ink_metric/metrics.json   (if scored)
and OUT_DIR/evalA_onB_s<seed>/ (A's fit re-evaluated on B's inputs).

The decision rule is fixed before the run:
  PRIMARY = total_fg_pixels over the windings every fit exported, when every
  run was scored; otherwise the satisfied area over the verified patches kept
  in every fit (the theta consistency gate can drop patches during a fit, so
  run-level denominators can differ). B wins only if min over seeds of B >
  max over seeds of A (for total_fg_pixels, also mean overall_fg_fraction of
  B >= that of A). With n seeds per arm a change with no effect passes this
  1 time in C(2n, n): 1 in 20 for 3 seeds, 1 in 6 for 2.
Every other number is a diagnostic.
"""
import json
import math
import os
import re
import sys

PATCH = ['satisfied_area_fraction', 'satisfied_patches_fraction',
         'satisfied_patches_area_weighted_fraction', 'boundary_satisfied_patches_fraction']


def ours(entry):
    # Our collections are named intercolumnar_<segment>_gap<k>_<rows>
    return str(entry.get('name') or '').startswith('intercolumnar_')


def load(out, run):
    d = os.path.join(out, run)
    f = os.path.join(d, 'satisfaction_metrics_fitted.json')
    if not os.path.isfile(f):
        return None
    summ = json.load(open(f))['summary']
    sat = json.load(open(os.path.join(d, 'satisfied_fitted.json')))
    pcls = sat.get('pcls', [])
    r = {k: summ.get(k) for k in PATCH}
    r['patch_area'] = {p['id']: (p['satisfied_area'], p['total_area']) for p in sat.get('patches', [])}
    r['den_patches'] = (summ.get('total_patches'), round(summ.get('total_area') or 0, 1))
    for tag, sel in (('published', [p for p in pcls if not ours(p)]), ('gaps', [p for p in pcls if ours(p)])):
        sp = sum(p['satisfied_points'] for p in sel)
        tp = sum(p['total_points'] for p in sel)
        full = sum(p['total_points'] > 0 and p['satisfied_points'] == p['total_points'] for p in sel)
        r[f'{tag}_points_fraction'] = sp / tp if tp else None
        r[f'{tag}_strips_full_fraction'] = full / len(sel) if sel else None
        r[f'den_{tag}'] = (len(sel), tp)
    mesh = os.path.join(d, 'meshes', f'fitted_{run}')
    windings = sorted({int(m.group(1)) for n in (os.listdir(mesh) if os.path.isdir(mesh) else [])
                       for m in [re.match(r'w(\d{3})_spliced', n)] if m})
    r['windings'] = (windings[0], windings[-1], len(windings)) if windings else None
    ink = os.path.join(d, 'meshes', f'common_{run}', 'ink_metric', 'metrics.json')
    if os.path.isfile(ink):
        m = json.load(open(ink))
        s = m['summary']
        r['total_fg_pixels'] = s.get('total_fg_pixels')
        r['overall_fg_fraction'] = s.get('overall_fg_fraction')
        st = (m.get('strips') or [{}])[0]
        r['ink_layout'] = (st.get('col_median_width_px'), st.get('line_median_pitch_px'))
    return r


def fmt(x):
    if x is None:
        return '   n/a'
    if isinstance(x, int):
        return f'{x:,}'
    return f'{x:.4f}'


def verdict(a, b):
    if any(x is None for x in a + b):
        return 'incomplete'
    if min(b) > max(a):
        return 'B better'
    if max(b) < min(a):
        return 'A better'
    return 'no clear difference'


def main():
    out, seeds = sys.argv[1], sys.argv[2:]
    runs = {(v, s): load(out, f'ab_{v}_s{s}') for v in 'AB' for s in seeds}
    evals = {s: load(out, f'evalA_onB_s{s}') for s in seeds}
    missing = [f'ab_{v}_s{s}' for (v, s), r in runs.items() if r is None]
    if missing:
        print('missing runs:', ', '.join(missing))

    def col(v, k):
        return [runs[(v, s)].get(k) if runs[(v, s)] else None for s in seeds]

    # Primary result first
    done = [r for r in runs.values() if r]
    common = set.intersection(*(set(r['patch_area']) for r in done)) if done else set()
    for r in done:
        ta = sum(r['patch_area'][i][1] for i in common)
        r['common_patch_area_fraction'] = sum(r['patch_area'][i][0] for i in common) / ta if ta else None
    scored = len(done) == len(runs) and all(r.get('total_fg_pixels') is not None for r in done)
    chance = 1 / math.comb(2 * len(seeds), len(seeds))
    print(f'Decision rule: B better only if its worst seed beats A\'s best seed; '
          f'with {len(seeds)} seed(s) per arm a change with no effect passes 1 time in {round(1 / chance)}.')
    if scored:
        a, b = col('A', 'total_fg_pixels'), col('B', 'total_fg_pixels')
        v = verdict(a, b)
        fa, fb = col('A', 'overall_fg_fraction'), col('B', 'overall_fg_fraction')
        if v == 'B better' and sum(fb) / len(fb) < sum(fa) / len(fa):
            v = 'no clear difference (B has more ink pixels but a lower ink fraction)'
        print(f'PRIMARY total_fg_pixels over the windings every fit exported: {v}')
    else:
        a, b = col('A', 'common_patch_area_fraction'), col('B', 'common_patch_area_fraction')
        print(f'PRIMARY satisfied area over the {len(common)} verified patches kept in every fit '
              f'(not every run was scored): {verdict(a, b)}')
    print(f'  A per seed: {" ".join(map(fmt, a))}')
    print(f'  B per seed: {" ".join(map(fmt, b))}')

    # Inputs actually reached the fit
    print('\nGap-line strips in each fit (A must be 0, B above 0):')
    for (v, s), r in runs.items():
        if r:
            print(f'  {v} seed {s}: {r["den_gaps"][0]} strips, {r["den_gaps"][1]} points')
    if any(runs[('B', s)] and runs[('B', s)]['den_gaps'][0] == 0 for s in seeds):
        print('  WARNING: a B fit has no gap-line strip: every gap line was fully on verified patches, '
              'outside the z range, or not loaded (see "pcls:" and "Loaded point collection" in its fit.log)')

    # Same denominators in A and B
    for s in seeds:
        ra, rb = runs[('A', s)], runs[('B', s)]
        if ra and rb:
            for k in ('den_patches', 'den_published'):
                if ra[k] != rb[k]:
                    print(f'note seed {s}: {k} differs (A {ra[k]}, B {rb[k]}); run-level fractions are not '
                          f'directly comparable')

    print('\n(published_* = the published point collections and fibers: training inputs in both arms, not held out)')
    print(f'{"metric":40s} {"A per seed":>30s} {"B per seed":>30s}  verdict')
    keys = ['total_fg_pixels', 'overall_fg_fraction', 'common_patch_area_fraction'] + PATCH + [
        'published_points_fraction', 'published_strips_full_fraction']
    for k in keys:
        a, b = col('A', k), col('B', k)
        if all(x is None for x in a + b):
            continue
        print(f'{k:40s} {" ".join(map(fmt, a)):>30s} {" ".join(map(fmt, b)):>30s}  {verdict(a, b)}')

    print('\nGap lines scored against a fit that never saw them (A re-evaluated on B\'s inputs),')
    print('next to B\'s own fit (which trained on them):')
    for s in seeds:
        e, rb = evals[s], runs[('B', s)]
        print(f'  seed {s}: A fit {fmt(e and e["gaps_points_fraction"])} of points, '
              f'{fmt(e and e["gaps_strips_full_fraction"])} of strips fully satisfied;  '
              f'B fit {fmt(rb and rb["gaps_points_fraction"])} / {fmt(rb and rb["gaps_strips_full_fraction"])}')

    print('\nExported windings (first, last, count) and ink layout (column width px, line pitch px):')
    for (v, s), r in runs.items():
        if r:
            print(f'  {v} seed {s}: {r["windings"]}  {r.get("ink_layout")}')
    print('  The ink model expects columns about 850 px wide and lines 80-120 px apart. Columns far')
    print('  narrower mean the render is too coarse: run again with INK_GROUP=2 (2x finer) or 1 (4x finer).')


if __name__ == '__main__':
    main()
