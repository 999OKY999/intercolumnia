# A/B test in the official spiral fit

Does the spiral fit of PHerc. Paris 4 get better when the intercolumnar gap
lines are added to its same-winding inputs?

- **A**: the published `spiral-input` dataset as is.
- **B**: the same dataset with the gap lines
  (`results/PHercParis4_intercolumnar_same_windings_9.6um.json`) appended to
  `same_windings.json`.

Both arms use one dataset root (only `same_windings.json` is switched), the
same z range, the same number of steps and the same seeds. For every seed the
script also re-evaluates A's finished fit on B's inputs with zero extra steps,
which scores the gap lines against a fit that never saw them.

The scripts were checked against `ScrollPrize/villa` at commit `e0bbb8b`
(6 October 2026), and the fitter is pinned to that commit. The VC3D renderer
used for the ink metric is the current `latest` release; its URL, commit and
sha256 are saved in `vc3d_appimage.txt`. The scripts were tested end to end
against stubs, not yet on a GPU.

## Machine

- Linux (Ubuntu 22.04 or 24.04) with an NVIDIA GPU. 24 GB or more is
  recommended; the GPU memory a run needs is not documented, and the pilot
  run (below) finds out. `nvidia-smi` must show CUDA 12.8 or newer, since the
  fit installs PyTorch built for CUDA 12.8.
- About 250 GB free on the disk that holds `WORK` (package caches go there
  too), and 32 GB of RAM or more.
- Docker is not needed.
- Network access to github.com, api.github.com, astral.sh, pypi.org,
  download.pytorch.org, dl.ash2txt.org, huggingface.co and
  vesuvius-challenge-open-data.s3.us-east-1.amazonaws.com.
- Packages (the second line is for the renderer used by the ink metric):

```bash
sudo apt-get update && sudo apt-get install -y git cmake build-essential rclone curl python3 tmux \
  libglvnd0 libglx0 libegl1 libopengl0 libgl1 libgl1-mesa-dri libglx-mesa0 \
  libx11-6 libx11-xcb1 libxcb1 libice6 libsm6 libfontconfig1 libfreetype6 libharfbuzz0b
```

## Run

```bash
git clone https://github.com/999OKY999/intercolumnia.git
tmux new -s ab
export WORK=/path/on/big/disk/ab
bash intercolumnia/gpu_test/run_ab_test.sh "$PWD/intercolumnia"
```

### Without logging in (cloud-init)

When an SSH login is not possible, create a NEW machine with this cloud-init
user data (most providers have a "cloud-init" or "user data" field), a public
IPv4 and inbound port 80 open. The first line must be exactly `#!/bin/bash`.
Added to an existing machine, it does nothing.

```bash
#!/bin/bash
cd /root
curl -fsSL --retry 10 --retry-all-errors -o ab_boot.sh https://raw.githubusercontent.com/999OKY999/intercolumnia/main/gpu_test/cloud_init.sh
bash ab_boot.sh
```

At first boot `cloud_init.sh` installs the packages, clones this repository
and runs `run_ab_test.sh` as a background service, with `WORK` on the system
disk (or on `/scratch` when the system disk has less than 200 GB free). The
service is stopped after `RUN_MAX_HOURS` at the latest (default `MAX_HOURS` +
12, so 36 h); the results are packed then too. Settings go before `bash` on
the last line, for example `MAX_HOURS=12 bash ab_boot.sh`.

A read-only page at `http://<machine-ip>/` shows the status, the current fit,
the last lines of the log and the pilot's time projection, and refreshes every
minute. It should answer a few minutes after the machine is up; if it does not
load within 15 minutes, nothing is running, so delete the machine. Chrome may
first warn that the connection is not secure (it is plain HTTP): continue to
the site, and keep the download if asked. When the page says FINISHED, or
STOPPED after the test started, download `ab_results.tgz`; a stop before the
test started offers `boot.txt` with the reason. Do not reboot or power off the
machine during the run: that stops the test. The machine keeps running, and
billing, until you delete it.

### Leaving it running

Leave it running with `Ctrl-b` then `d`; come back with `tmux attach -t ab`.
`$WORK/run_all.log` has the overall progress; during a fit it stays quiet and
the fitter's own progress is in `$WORK/out/<run>/fit.log`. Running the same
command again resumes: finished downloads, fits and scores are skipped, and a
fit is redone if its z range, steps, seed or inputs changed.

What it does:

1. Checks the tools, the GPU and the free disk, installs `uv`, clones villa at
   the pinned commit, runs `uv sync` and checks that PyTorch sees the GPU.
2. Downloads the dataset with rclone, as in the tutorial, leaving out
   `unverified_patches/` (the fit never reads them) and `tracks/` (off by
   default). Writes the tutorial's `spiral-scroll.json` if the download has
   none, adding `base_shape_zyx` for the 9.6 µm frame.
3. Builds A's and B's `same_windings.json` and checks both with the official
   loader, because the fitter silently treats an unreadable file as empty.
4. **Pilot**: a 600-step fit of B. It catches setup errors in minutes,
   measures the GPU's speed and projects the total time. Unless `Z_BEGIN` and
   `Z_END` are set, it tries z 8000-14000 (1,860 gap points) and falls back to
   10500-11500 (289 points) if the wide range fails or would take longer than
   `MAX_HOURS`.
5. Fits A and B for each seed (default 30,000 steps, as in `config.py`; with
   fewer steps the distance-transform phase is moved so it keeps its share).
6. Scores every fit with the ink metric (`score_ink.sh`): renders the
   windings that every fit exported through the public 3D ink prediction with
   `render_ink.py` and counts ink pixels with `get_ink_metrics.py`. Scoring
   the same windings in every fit keeps the sum fair, since B's extra inputs
   could widen the exported range. The renderer comes from the VC3D AppImage,
   unpacked, so no docker is needed.
7. Prints the comparison (`compare.py`) and packs every log and metric into
   `$WORK/ab_results.tgz`, also when something fails.

Settings: `WORK`, `Z_BEGIN`/`Z_END` (dataset frame, both or neither),
`STEPS`, `SEEDS` (default `"1 2 3"`), `MAX_HOURS` (default 24; `MAX_HOURS=0`
stops after the pilot with only the time projection, and a later run with a
higher limit resumes from there), `SCORE` (0 to
skip the ink metric), `INK_GROUP` (ink zarr level for the render, default 3).

## Reading the result

The rule is fixed before the run. The primary result is `total_fg_pixels`
over the windings every fit exported, when every fit was scored; otherwise
the satisfied area over the verified patches kept in every fit (the fitter's
theta consistency gate can drop patches during a fit). B counts as better
only if its worst seed beats A's best seed (and, for `total_fg_pixels`, its
ink fraction is not lower). With three seeds per arm a change with no effect
passes this 1 time in 20; with two, 1 time in 6. Everything else in the
summary is a diagnostic:

- the fitter's own patch satisfaction fractions;
- satisfaction of the published point collections and fibers
  (`published_*`; training inputs in both arms, not held out);
- the gap lines scored under A's fit (which never saw them) and under B's;
- the windings each fit exported, and the column width and line pitch the
  ink model sees.

## Known limits

- The render resolution the leaderboard uses relative to the published ink
  zarr is not stated in the code. These scripts render at 76.8 µm per pixel
  for both arms and print the column width and line pitch the ink model
  sees next to its priors (about 850 px and 80-120 px).
- Appending to `same_windings.json` puts the gap lines in the same sampling
  group as the published same-winding collections, so B also samples those
  less often.
- A line with points on two or more verified patches also acts as a
  cross-patch constraint. It keeps a strip-satisfaction entry for its points
  off the patches, cut to the longest run inside the z range (dropped below 2
  points); a line with every point on patches has no strip. Each `fit.log`
  reports the split on its `pcls:` line.
