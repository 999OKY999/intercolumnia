"""Project the run time of the A/B test from the pilot fit.

Usage: pilot_projection.py PILOT_DIR PILOT_STEPS STEPS MAX_HOURS SEED [SEED ...]

The fitter prints lines like
  PROGRESS optimizing — 600/600 iterations (100.0%) — 9.8 it/s — elapsed 1m 01s
when its output is not a terminal (spiral_progress.py). The steady rate is
taken between PROGRESS lines after the first 20% of the steps, so start-up
compilation does not count against the long fits; the rest of the pilot's
wall time is counted as per-fit overhead.
Exits with status 1 when the projection is longer than MAX_HOURS.
"""
import re
import sys


def main():
    pilot, pilot_steps, steps, max_hours = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
    seeds = sys.argv[5:]
    log = open(f'{pilot}/fit.log', encoding='utf-8', errors='replace').read()
    wall = float(open(f'{pilot}/wall_seconds').read())

    def sec(t):
        return sum(int(n) * {'h': 3600, 'm': 60, 's': 1}[u] for n, u in re.findall(r'(\d+)([hms])', t))
    pts = [(int(n.replace(',', '')), sec(t)) for n, t in re.findall(
        r'PROGRESS optimizing\D+([\d,]+)/[\d,]+ iterations.*?elapsed (\d+h \d+m|(?:\d+m )?\d+s)', log, re.I)]
    late = [p for p in pts if p[0] >= 0.2 * pilot_steps]
    if len(late) >= 2 and late[-1][1] > late[0][1]:
        rate = (late[-1][0] - late[0][0]) / (late[-1][1] - late[0][1])
    elif pts and pts[-1][0] > 0 and pts[-1][1] > 0:
        rate = pts[-1][0] / pts[-1][1]
    else:
        rate = pilot_steps / max(wall, 1.0)
    rate = max(rate, 1e-3)
    overhead = max(0.0, wall - pilot_steps / rate)
    per_fit = overhead + steps / rate
    # A and B per seed, plus one zero-step re-evaluation per seed
    hours = (2 * len(seeds) * per_fit + len(seeds) * overhead) / 3600
    print(f'pilot {pilot}: {pilot_steps} steps, {wall:.0f} s wall, {rate:.2f} it/s, overhead {overhead:.0f} s')
    print(f'projection: {2 * len(seeds)} fits of {steps} steps x {per_fit / 3600:.2f} h '
          f'+ {len(seeds)} re-evaluations = {hours:.1f} h (ink scoring not included)')
    if hours > max_hours:
        print(f'too long: {hours:.1f} h > MAX_HOURS={max_hours:g}. Narrow the z range, '
              f'lower STEPS, use fewer SEEDS, or raise MAX_HOURS.')
        sys.exit(1)


if __name__ == '__main__':
    main()
