#!/usr/bin/env bash
# Ink metric for the A/B fits: render each fit's windings through the public
# 3D ink prediction of Paris 4 and count the ink pixels an nnU-Net model finds
# (summary.total_fg_pixels in ink_metric/metrics.json), as the team's
# render_ink.py + get_ink_metrics.py chain does.
#
# Called by run_ab_test.sh with WORK and SEEDS set. For every seed and arm it
# links the windings that all fits exported into
# out/ab_<arm>_s<seed>/meshes/common_ab_<arm>_s<seed>/ and scores that folder.
#
# Frame: the fitted meshes are in the dataset frame (9.6 um), which is level 2
# of the 2.4 um ink zarr on S3. --scale-segmentation 4 maps them to its level 0,
# and --group-idx 3 --scale 0.25 then gives the same render geometry as the
# team's runner on a zarr whose level 0 is the mesh frame (76.8 um per pixel).
# The settings are identical for A and B; whether they match the leaderboard's
# exact render resolution is not stated in the code (see README.md).
set -euo pipefail

WORK=${WORK:?}; SEEDS=${SEEDS:?}
SPIRAL=$WORK/villa/spiral-fitting
INK_URL=${INK_URL:-s3://vesuvius-challenge-open-data/PHercParis4/representations/predictions/ink-3d/20260411134726-ink3d-20260428123845-v3-78k-fullsup.zarr/}
INK_SEG_SCALE=${INK_SEG_SCALE:-4}; INK_GROUP=${INK_GROUP:-3}; INK_SCALE=${INK_SCALE:-0.25}
export PATH="$HOME/.local/bin:$PATH"

# 1. vc_render_tifxyz and vc_tifxyz_trim from the VC3D AppImage, without docker
VCBIN=$WORK/vcbin
if [ ! -x "$VCBIN/squashfs-root/AppRun" ]; then
  mkdir -p "$VCBIN"
  # The rolling 'latest' release (rebuilt from villa main); what was used is recorded.
  rel=$(curl -fsSL --retry 5 --retry-delay 30 --retry-all-errors https://api.github.com/repos/ScrollPrize/villa/releases/tags/latest)
  url=$(printf '%s' "$rel" | python3 -c \
    'import json, sys; print(next(a["browser_download_url"] for a in json.load(sys.stdin)["assets"] if a["name"].endswith(".AppImage")))')
  curl -fL --retry 5 --retry-delay 30 -o "$VCBIN/VC3D.AppImage" "$url"
  { echo "$url"
    printf '%s' "$rel" | python3 -c 'import json, sys; r = json.load(sys.stdin); print(r.get("target_commitish"), r.get("published_at"))'
    sha256sum "$VCBIN/VC3D.AppImage"; } > "$WORK/vc3d_appimage.txt"
  chmod +x "$VCBIN/VC3D.AppImage"
  (cd "$VCBIN" && ./VC3D.AppImage --appimage-extract >/dev/null)
fi
for t in vc_render_tifxyz vc_tifxyz_trim; do ln -sfn "$VCBIN/squashfs-root/AppRun" "$VCBIN/$t"; done
# A tool that loads prints its usage and exits 0 or 1; anything else is a load
# failure, usually missing host graphics libraries (OpenCV links libGLX).
rc=0; "$VCBIN/vc_tifxyz_trim" >/dev/null 2>&1 || rc=$?
[ "$rc" -le 1 ] || {
  echo "vc_tifxyz_trim does not start (exit $rc); install the graphics libraries listed in README.md and run again"
  exit 1; }

# Streamed ink chunks go to the big disk, not the home folder
export VC3D_CONFIG_DIR=$WORK/vc3d
mkdir -p "$VC3D_CONFIG_DIR" "$WORK/vc3d_cache" "$WORK/ink3d_marker.zarr"
printf '[viewer]\nremote_cache_dir=%s\n' "$WORK/vc3d_cache" > "$VC3D_CONFIG_DIR/VC3D.ini"

# 2. Scorer dependencies (nnunetv2, huggingface_hub) are not in the project
#    environment. Add them to it, keeping its CUDA 12.8 PyTorch; if that fails,
#    use a Python 3.12 venv with the same PyTorch build.
# Everything below calls the project's Python directly: a 'uv run' would sync
# the project environment and remove the packages added here.
cd "$SPIRAL"
PY=$SPIRAL/.venv/bin/python
CHECK='import torch, huggingface_hub
from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor
assert torch.cuda.is_available(), "torch cannot see the GPU"'
if uv pip install --python "$PY" nnunetv2 huggingface_hub && "$PY" -c "$CHECK"; then
  SCORER=("$PY")
else
  echo "nnunetv2 does not work on the project's Python; using a Python 3.12 venv"
  [ -x "$WORK/inkenv/bin/python" ] || uv venv -p 3.12 "$WORK/inkenv"
  uv pip install --python "$WORK/inkenv/bin/python" --index-url https://download.pytorch.org/whl/cu128 torch
  uv pip install --python "$WORK/inkenv/bin/python" nnunetv2 huggingface_hub numpy scipy pillow
  "$WORK/inkenv/bin/python" -c "$CHECK"
  SCORER=("$WORK/inkenv/bin/python")
fi
MODEL_ROOT=$WORK/ink-coverage-32um
"${SCORER[@]}" -c 'import sys
from huggingface_hub import snapshot_download
snapshot_download("scrollprize/ink-coverage-32um", local_dir=sys.argv[1])' "$MODEL_ROOT"
plans=$(find "$MODEL_ROOT" -name plans.json -print -quit)
[ -n "$plans" ] && [ -f "$(dirname "$plans")/dataset.json" ] \
  || { echo "no nnU-Net model folder (plans.json + dataset.json) under $MODEL_ROOT"; exit 1; }
MODEL=$(dirname "$plans")

# 3. Render and score every fit with identical settings, on the windings that
#    every fit exported: the exported range follows each fitted transform and
#    B's extra strips, and total_fg_pixels is a sum over windings. A fit is
#    scored again whenever the settings or the common windings change.
n_runs=$(( 2 * $(wc -w <<< "$SEEDS") ))
common=$(for s in $SEEDS; do for v in A B; do
           { ls "$WORK/out/ab_${v}_s$s/meshes/fitted_ab_${v}_s$s" 2>/dev/null || true; } \
             | sed -n "s/^w\([0-9]\{3\}\)_spliced_ab_${v}_s$s\$/\1/p"
         done; done | sort | uniq -c | awk -v n="$n_runs" '$1 == n {print $2}')
[ -n "$common" ] || { echo "the fits share no exported winding (or a fit has no meshes)"; exit 1; }
wlist=$(paste -sd, <<< "$common")
echo "scoring the windings every fit exported: $wlist"
key="url=$INK_URL seg=$INK_SEG_SCALE group=$INK_GROUP scale=$INK_SCALE windings=$wlist"
for s in $SEEDS; do
  for v in A B; do
    run=ab_${v}_s$s
    m=$WORK/out/$run/meshes/fitted_$run
    c=$WORK/out/$run/meshes/common_$run
    if [ -f "$c/ink_metric/metrics.json" ] && [ "$(cat "$c/ink_settings.txt" 2>/dev/null)" = "$key" ]; then
      echo "scored: $c"; continue
    fi
    rm -rf "$c"; mkdir -p "$c"
    for w in $common; do ln -s "$m/w${w}_spliced_$run" "$c/"; done
    echo "--- render $v seed $s ($(date -Is))"
    "$PY" render_ink.py "$c" \
      --volume "$WORK/ink3d_marker.zarr" --remote-url "$INK_URL" \
      --vc-render-bin "$VCBIN/vc_render_tifxyz" \
      --scale-segmentation "$INK_SEG_SCALE" --group-idx "$INK_GROUP" --scale "$INK_SCALE" \
      > "$WORK/out/$run/ink.log" 2>&1 \
      || { tail -30 "$WORK/out/$run/ink.log"; exit 1; }
    echo "--- score $v seed $s ($(date -Is))"
    "${SCORER[@]}" get_ink_metrics.py "$c/ink" --model "$MODEL" \
      > "$WORK/out/$run/coverage.log" 2>&1 \
      || { tail -30 "$WORK/out/$run/coverage.log"; exit 1; }
    printf '%s\n' "$key" > "$c/ink_settings.txt"
  done
done
