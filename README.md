# intercolumnia

Same-winding constraints for the spiral fit, read from the blank strips between
columns of text.

The blank strip between two columns of a Herculaneum papyrus runs down a single
wrap of the sheet. `intercolumnia` finds those strips on the ink renders of
traced segments, follows each one down the column, maps it into the scan through
the segment's tifxyz mesh, checks it, and writes it as a VC point collection
that `spiral-fitting/fit_spiral.py` reads as a same-winding input with no
changes.

This is the "Intercolumnar gaps" idea from the team's list of winding
constraints not yet tried (`scrollprize.org/docs/39_winding_annotations.md`).

## Results on PHerc. Paris 4

Run on all 81 traced segments of PHerc. Paris 4 that have a 2.4 µm ink render
(volume `20260411134726`), 8 October 2026:

- **199 same-winding lines, 3,243 points**, from 52 segments (the other 29 are
  narrower than a column or show no clear gap).
- The merged files load with the official `point_collection.load_point_collection`
  as 199 same-winding collections.
- **Cross-trace check**: 2,778 of the 3,243 points have a second, independent
  trace passing through the same spot on the same sheet. In that second trace's
  render, 2,217 (80%) fall inside a traced gap, 385 (14%) fall on blank papyrus
  that its run did not keep as a gap, and 176 (6%) fall on text. The conflicts
  gather in a few segments; one re-flattened copy of an older trace
  (`20260604223808-20231210121321_v8`) confirms no gap from any other segment,
  which suggests its render does not line up with its mesh (inferred).
- **Layout of the scroll, measured from the data**: column pitch 32,192 voxels
  (77.3 mm); the length of one turn grows by 561 voxels per winding, a sheet
  spacing of 89.4 voxels (214 µm), with the fit passing through the origin.
- **Where gaps cannot catch a switch**: one turn equals one column pitch near
  winding 57 and two near winding 115. A trace that slips one wrap there still
  sees a gap in almost the same place.

A gallery with every segment's overlay and these numbers is built by
`gallery.py`.

## What it does

1. **Find the gaps.** On the ds8 ink render, ink density per column of pixels
   (inside the papyrus mask) is smoothed and its deep, wide minima are taken as
   candidate gaps.
2. **Follow each gap down the column.** Rows are read in overlapping windows a
   few text lines tall (512 px, every 128 px). A dynamic-programming path keeps
   to the middle of the blank strip while allowing it to lean and bend. In each
   window the tool also locates the line starts of the next column, the
   sharpest edge on the page.
3. **Keep only clear windows.** A window is kept when text lies on both sides
   (at least half the gap's typical flank density), the strip holds well under
   half the ink of its flanks, the papyrus is intact, and the strip is at least
   80 px wide.
4. **Map into the scan.** Each kept point is mapped through the segment's mesh
   (render px × 8 × mesh scale = grid index, bilinear interpolation of x, y, z).
   Mesh grid steps are 20 voxels, so render pixels are voxels on the surface.
5. **Check in 3D and against the layout.**
   - *Mesh tears*: consecutive points must not sit much further apart in the
     volume than in the render.
   - *Sheet switches*: if the trace slips to another wrap, the text below the
     slip comes from one turn further along the sheet, and the gap appears
     shifted sideways by (length of one turn) mod (column pitch). The tool
     measures the length of one turn from the mesh and the umbilicus, predicts
     that shift, and flags windows where the line starts of the next column
     jump by it.
   - *When a switch can hide*: where one turn is close to a whole number of
     column pitches the predicted shift is smaller than the gap's own jitter,
     and the gaps cannot catch a switch. Each collection records whether a
     switch would have been visible (`one_turn_switch_checkable`).
6. **Write point collections.** One collection per continuous run of kept
   windows (at least six windows, about 6,000 voxels of column height).

## Output

`<segment>_intercolumnar_same_windings.json` follows
`vc_pointcollections_json_version` 1. Points carry `p` as `[x, y, z]` in
full-resolution voxels of the 2.4 µm volume `20260411134726` and no `wind_a`,
so `point_collection.py` loads them as same-winding collections. Each
collection's `metadata` holds the evidence a reviewer needs: render
coordinates of every point, surface normals, the 3D line starts of the next
column, gap width, column pitch, the predicted switch shift, and whether a
switch would have been visible.

`results/PHercParis4_intercolumnar_same_windings_2.4um.json` merges every
segment in voxels of the 2.4 µm volume. `results/..._9.6um.json` is the same
file in the 4× downsampled frame that the published `spiral-input` dataset is
fitted in (its `spiral-scroll.json` gives `voxel_size_um` 9.6, and its default
fit range of z 4,000 to 17,000 matches our points, which span 3,554 to 17,727
in that frame). `add_to_dataset.py` appends the collections to a dataset's
`same_windings.json` with fresh ids.

## Checks you can run without ground truth

- `eval_cross.py`: for each gap point, finds another segment whose mesh passes
  through the same spot on the same sheet and asks whether that segment's
  render shows a gap there too.
- `ct_check.py`: draws two traces and their gap points over one CT plane read
  straight from the public OME-Zarr (HTTP range requests, no download).
- `gallery.py`: one page with every segment's overlay and numbers.

## Usage

```
pip install numpy scipy pillow tifffile imagecodecs
python intercolumnia.py RENDER_ds8.jpg MESH.tifxyz umbilicus.json OUT --segment NAME
python batch.py segments.json umbilicus.json OUT --work /tmp/work     # every Paris 4 segment
python merge.py OUT PHercParis4_intercolumnar_same_windings_2.4um.json
python merge.py OUT PHercParis4_intercolumnar_same_windings_9.6um.json --scale 0.25
python save_meshes.py OUT /tmp/work && python density_maps.py OUT /tmp/work
python eval_cross.py OUT
python gallery.py OUT site
```

For the spiral fit, append the 9.6 µm file to the dataset's `same_windings.json`
with `add_to_dataset.py`; `gpu_test/run_ab_test.sh` does this and runs a fit
with and without the gap lines on the same slice range.

## Limits

- Needs an ink render with visible columns; segments narrower than a column
  give nothing.
- The gap centre is uncertain along the sheet by a few hundred voxels (the
  strips are about 7,000 voxels wide); this does not affect a same-winding
  claim but matters if the points are used for anything else.
- Around the windings where one turn equals a whole number of column pitches
  (about 57 and 115 on Paris 4) the gaps cannot reveal a one-turn switch.
- Not yet run through `fit_spiral.py`; the effect on `total_fg_pixels` is
  untested. The 9.6 µm frame of the spiral dataset is inferred from the
  tutorial, not checked against the dataset files.

## License

MIT
