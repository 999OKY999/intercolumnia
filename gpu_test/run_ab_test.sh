#!/usr/bin/env bash
# A/B test of the intercolumnar gap lines in the official spiral fit
# (ScrollPrize/villa spiral-fitting, PHerc. Paris 4).
#
#   A: the published spiral-input dataset as is
#   B: the same dataset with the gap lines appended to same_windings.json
#
# Both arms use one dataset root (only same_windings.json is switched), the
# same z range, the same step count and the same seeds. Every seed runs A and
# B; a seed's A is also re-evaluated on B's inputs with zero extra steps, which
# scores the gap lines against a fit that never saw them.
#
#   bash run_ab_test.sh /path/to/intercolumnia
#
# Needs: Linux, an NVIDIA GPU (24 GB or more recommended) with a driver for
# CUDA 12.8, about 250 GB free under WORK, git, cmake, g++, rclone, curl,
# python3. Network: github.com, api.github.com, astral.sh, pypi.org,
# download.pytorch.org, dl.ash2txt.org, huggingface.co and the public S3
# bucket vesuvius-challenge-open-data. See README.md in this folder.
#
# Settings (environment variables):
#   WORK         work folder on a big disk          (default ~/vesuvius-ab)
#   Z_BEGIN/Z_END  slice range in the dataset frame (9.6 um); when unset the
#                pilot tries 8000-14000, then 10500-11500
#   STEPS        optimizer steps per fit            (default 30000, as config.py)
#   SEEDS        seeds, space separated             (default "1 2 3")
#   MAX_HOURS    stop before the long fits if the pilot projects more (24)
#   SCORE        1 = also compute the ink metric total_fg_pixels (default 1)
#   VILLA_COMMIT villa commit the script was checked against
#
# Everything is logged to $WORK/run_all.log, and on every exit (success or
# failure) the results are packed into $WORK/ab_results.tgz.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(realpath -e "${1:?usage: run_ab_test.sh /path/to/intercolumnia}")
GAPS_JSON=$REPO/results/PHercParis4_intercolumnar_same_windings_9.6um.json
[ -f "$REPO/add_to_dataset.py" ] && [ -f "$GAPS_JSON" ] \
  || { echo "not an intercolumnia folder: $REPO"; exit 1; }
WORK=$(realpath -m "${WORK:-$HOME/vesuvius-ab}")
STEPS=${STEPS:-30000}
SEEDS=${SEEDS:-"1 2 3"}
MAX_HOURS=${MAX_HOURS:-24}
PILOT_STEPS=${PILOT_STEPS:-600}
SCORE=${SCORE:-1}
VILLA_COMMIT=${VILLA_COMMIT:-e0bbb8b40a2db58b1d71864f286eb85717e59e64}
if [ -n "${Z_BEGIN:-}" ] || [ -n "${Z_END:-}" ]; then
  Z_RANGES="${Z_BEGIN:-}-${Z_END:-}"
else
  Z_RANGES=${Z_RANGES:-"8000-14000 10500-11500"}
fi
for r in $Z_RANGES; do
  [[ $r =~ ^[0-9]+-[0-9]+$ ]] && [ "${r%-*}" -lt "${r#*-}" ] \
    || { echo "bad z range '$r': set both Z_BEGIN and Z_END as plain integers, Z_BEGIN < Z_END"; exit 1; }
done
for v in STEPS PILOT_STEPS MAX_HOURS; do
  [[ ${!v} =~ ^[0-9]+$ ]] || { echo "$v must be a plain integer"; exit 1; }
done
Z_BEGIN=; Z_END=
export WANDB_MODE=disabled PYTHONUNBUFFERED=1
mkdir -p "$WORK"
# Package caches on the big disk too (torch with CUDA is several GB)
export UV_CACHE_DIR=$WORK/cache/uv XDG_CACHE_HOME=$WORK/cache

exec > >(trap '' INT TERM HUP; exec tee -a "$WORK/run_all.log") 2>&1   # tee outlives Ctrl-C or a kill
echo "=== start $(date -Is)  STEPS=$STEPS SEEDS='$SEEDS' Z_RANGES='$Z_RANGES' SCORE=$SCORE"
rm -f "$WORK/summary.txt" "$WORK/pilot.txt"     # rebuilt below; never pack a stale one

package_results() {                     # runs on every exit
  local rc=$?
  set +e; trap '' PIPE                  # tee may already be gone
  cd "$WORK" || return
  {
    echo "exit_code=$rc date=$(date -Is)"
    echo "villa=$(git -C villa rev-parse HEAD 2>/dev/null)"
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv 2>/dev/null
    echo "Z_BEGIN=$Z_BEGIN Z_END=$Z_END STEPS=$STEPS SEEDS=$SEEDS"
    sha256sum data/sw/A.json data/sw/B.json 2>/dev/null
    df -h "$WORK" 2>/dev/null
  } > env.txt
  {
    ls -d env.txt run_all.log pilot.txt summary.txt vc3d_appimage.txt data/phercparis4/spiral-scroll.json 2>/dev/null
    find out -type f \( -name fit.log -o -name wall_seconds -o -name params -o -name training_metrics.jsonl \
      -o -name 'satisfaction_metrics_*.json' -o -name 'satisfied_*.json' -o -name non_liftable_patches.txt \
      -o -name ink.log -o -name coverage.log -o -name ink_settings.txt -o -path '*/ink_metric/metrics.*' \) 2>/dev/null
  } > results_files.txt
  tar czf ab_results.tgz -T results_files.txt 2>/dev/null
  echo "=== exit code $rc. Results packed in $WORK/ab_results.tgz ($(du -h ab_results.tgz 2>/dev/null | cut -f1))"
  echo "=== Copy it to your own computer with:  scp <user>@<machine-ip>:$WORK/ab_results.tgz ."
}
trap package_results EXIT
trap 'exit 130' INT; trap 'exit 143' TERM; trap 'exit 129' HUP

# 0. Checks that cost nothing
for t in curl git cmake g++ rclone python3 nvidia-smi; do
  command -v "$t" >/dev/null || { echo "missing: $t (see README.md: apt-get install line)"; exit 1; }
done
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
avail=$(df -BG --output=avail "$WORK" | tail -1 | tr -dc 0-9)
[ "${avail:-0}" -ge 200 ] || echo "WARNING: only ${avail:-?} GB free under $WORK; about 250 GB are recommended"
cd "$WORK"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null || { echo "uv is not on PATH after installing it"; exit 1; }

# 1. Fitter, pinned to the reviewed commit, and a CUDA check before the download
[ -d villa/.git ] || { rm -rf villa; git clone --depth 1 https://github.com/ScrollPrize/villa.git; }
git -C villa fetch --depth 1 origin "$VILLA_COMMIT"
git -C villa checkout --quiet --detach "$VILLA_COMMIT"
(cd villa/spiral-fitting && uv sync)
(cd villa/spiral-fitting && uv run python -c 'import sys, torch
assert torch.cuda.is_available(), "torch cannot see the GPU (driver too old for CUDA 12.8?)"
p = torch.cuda.get_device_properties(0)
print("python", sys.version.split()[0], "torch", torch.__version__, "cuda", torch.version.cuda, p.name, f"{p.total_memory / 2**30:.1f} GiB")')

# 2. Dataset. The fit never reads unverified_patches/, and tracks are off by
#    default (input_use_tracks = False), so both are skipped.
RAW=$WORK/data/phercparis4
if [ ! -f "$WORK/data/.phercparis4_complete" ]; then
  rclone copy :http: "$RAW" \
    --http-url https://dl.ash2txt.org/datasets/spiral_datasets/PHercParis4/ --transfers 32 \
    --exclude "unverified_patches/**" --exclude "tracks/**" --stats 1m --stats-one-line
  touch "$WORK/data/.phercparis4_complete"
fi
du -sh "$RAW"
for f in umbilicus.json same_windings.json; do
  [ -f "$RAW/$f" ] || { echo "missing $RAW/$f: the download is incomplete (run again to resume)"; exit 1; }
done
if [ ! -f "$RAW/spiral-scroll.json" ]; then
  # The tutorial's file for Paris 4 (scrollprize.org/tutorial_spiral), plus the
  # shape of the dataset frame (level 2 of the 2.4 um volume 20260411134726),
  # which fibers that declare coordinate_base_shape_zyx need.
  printf '%s\n' '{"schema_version": 1, "name": "PHercParis4", "voxel_size_um": 9.6, "spiral_outward_sense": "CW", "base_shape_zyx": [18946, 8174, 8174]}' \
    > "$RAW/spiral-scroll.json"
  echo "wrote $RAW/spiral-scroll.json"
fi

# 3. One dataset root for both arms: symlinks to the download, with
#    same_windings.json pointing at A.json or B.json. A single root keeps the
#    input paths (and so the sampling groups) identical, and lets A's checkpoint
#    be re-evaluated on B's inputs.
DS=$WORK/data/ab; SW=$WORK/data/sw
rm -rf "$DS" && mkdir -p "$DS" "$SW"
shopt -s dotglob nullglob
for f in "$RAW"/*; do
  [ "$(basename "$f")" = same_windings.json ] || ln -s "$(realpath "$f")" "$DS"/
done
shopt -u dotglob nullglob
cp "$RAW/same_windings.json" "$SW/A.json"
python3 "$REPO/add_to_dataset.py" "$SW/A.json" "$GAPS_JSON" "$SW/B.json"
use_arm() { ln -sfn "$SW/$1.json" "$DS/same_windings.json"; }

# Frame check on the data, and a load check with the official loader: the
# fitter treats an unreadable point-collection file as empty, silently.
cd "$WORK/villa/spiral-fitting"
uv run python - "$SW/A.json" "$SW/B.json" "$GAPS_JSON" <<'EOF'
import sys
from point_collection import load_point_collection
a, b, g = (load_point_collection(p) for p in sys.argv[1:4])
if a is None or b is None or g is None or len(b) != len(a) + len(g):
    sys.exit("merged same_windings.json does not load as dataset + gap lines")
def zr(pc):
    zs = [p["p"][2] for c in pc.values() for p in c["points"].values()]
    return min(zs), max(zs)
print(f"official loader: {len(a)} + {len(g)} = {len(b)} same-winding collections")
print("z range: dataset %.0f-%.0f, gap lines %.0f-%.0f (frame has 18946 slices)" % (zr(a) + zr(g)))
if max(zr(a)[1], zr(g)[1]) > 18946:
    sys.exit("frame mismatch: points beyond slice 18946 of the 9.6 um frame")
EOF

# 4. Fits
fit() {  # fit <arm> <seed> <run_dir> <steps> [resume_checkpoint]
  local dt=$(( $4 * 25000 / 30000 ))     # keep the default DT-snap phase share
  local ovr
  ovr=$(printf '{"z_begin": %d, "z_end": %d, "optimizer_num_training_steps": %d, "optimizer_random_seed": %d, "loss_start_patch_dt": %d, "loss_start_track_dt": %d}' \
        "$Z_BEGIN" "$Z_END" "$4" "$2" "$dt" "$dt")
  use_arm "$1"; rm -rf "$3"; mkdir -p "$3"
  local t0; t0=$(date +%s)
  echo "--- fit $1 seed $2 z $Z_BEGIN-$Z_END steps $4 -> $3  ($(date -Is))"
  ( if [ -n "${5:-}" ]; then export FIT_SPIRAL_RESUME_PATH="$5"; fi
    FIT_SPIRAL_CONFIG_OVERRIDES="$ovr" FIT_SPIRAL_RUN_DIR="$3" FIT_SPIRAL_RUN_TAG="$(basename "$3")" \
    FIT_SPIRAL_METRICS_HISTORY="$3/training_metrics.jsonl" \
      uv run python fit_spiral.py --dataset "$DS" > "$3/fit.log" 2>&1 ) \
    || { echo "FAILED: $3"; tail -40 "$3/fit.log"; return 1; }
  echo $(( $(date +%s) - t0 )) > "$3/wall_seconds"
  grep -E 'Loaded point collection with|Error loading point collection|^pcls: |failed to load fiber' "$3/fit.log" || true
  [ -f "$3/satisfaction_metrics_fitted.json" ] || { echo "no satisfaction metrics in $3"; return 1; }
  run_params "$1" "$2" "$4" > "$3/params"   # written last: marks a complete fit
}
run_params() { echo "$1 $2 $Z_BEGIN $Z_END $3 $(sha256sum < "$SW/$1.json" | cut -c1-16)"; }
done_with() {  # done_with <run_dir> <arm> <seed> <steps>: finished with exactly these settings
  [ -f "$1/satisfaction_metrics_fitted.json" ] && [ "$(cat "$1/params" 2>/dev/null)" = "$(run_params "$2" "$3" "$4")" ]
}

# 4a. Pilot: a short fit of arm B (it loads every input of A plus the gap lines)
#     catches setup errors in minutes and measures the speed of this GPU.
chosen=
for r in $Z_RANGES; do
  Z_BEGIN=${r%-*}; Z_END=${r#*-}
  p=$WORK/out/pilot_$Z_BEGIN-$Z_END
  if ! done_with "$p" B 1 "$PILOT_STEPS"; then
    fit B 1 "$p" "$PILOT_STEPS" || { echo "pilot failed at z $r"; continue; }
  fi
  if python3 "$HERE/pilot_projection.py" "$p" "$PILOT_STEPS" "$STEPS" "$MAX_HOURS" $SEEDS \
       | tee -a "$WORK/pilot.txt"; then
    chosen=$r; break
  fi
done
[ -n "$chosen" ] || { echo "STOP: no z range passed the pilot (see above and pilot.txt)"; exit 1; }
Z_BEGIN=${chosen%-*}; Z_END=${chosen#*-}
echo "=== using z $Z_BEGIN-$Z_END"

# 4b. A and B for every seed, plus A re-evaluated on B's inputs (zero steps)
for s in $SEEDS; do
  for v in A B; do
    run=$WORK/out/ab_${v}_s$s
    if done_with "$run" "$v" "$s" "$STEPS"; then echo "done: $run"; continue; fi
    if [ "$v" = A ]; then rm -rf "$WORK/out/evalA_onB_s$s"; fi   # a new A needs a new re-evaluation
    fit "$v" "$s" "$run" "$STEPS"
  done
  ev=$WORK/out/evalA_onB_s$s
  done_with "$ev" B "$s" "$STEPS" \
    || fit B "$s" "$ev" "$STEPS" "$WORK/out/ab_A_s$s/checkpoint_fitted.ckpt" \
    || echo "re-evaluation of A on B's inputs failed for seed $s (optional, continuing)"
done
use_arm A

# 5. Official ink metric, in its own process so a failure keeps the fits
if [ "$SCORE" = 1 ]; then
  WORK=$WORK SEEDS=$SEEDS bash "$HERE/score_ink.sh" \
    || echo "SCORING FAILED: the fits and their satisfaction metrics are still valid"
fi

# 6. Compare
python3 "$HERE/compare.py" "$WORK/out" $SEEDS | tee "$WORK/summary.txt"
