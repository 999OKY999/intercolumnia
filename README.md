# intercolumnia

Same-winding constraints for the spiral fit, read from the blank strips between
columns of text.

The blank strip between two columns of a Herculaneum papyrus runs down a single
wrap of the sheet. `intercolumnia` finds those strips on the ink renders of
traced segments, follows each one down the column, maps it into the scan through
the segment's tifxyz mesh, checks it, and writes it as a VC point collection
that `spiral-fitting/fit_spiral.py` reads as a same-winding input with no
changes.

This is the "Intercolumnar gaps" idea listed under "Things we have not tried"
on the team's [In-depth: Winding Constraints](https://scrollprize.org/open_problems/winding_annotations)
page: "Like ink, they could provide same-winding evidence that is easy to
identify and verify."

## Results on PHerc. Paris 4

Run on the 80 traced segments of PHerc. Paris 4 that have a 2.4 µm ink render
(volume `20260411134726`; an 81st listed segment, `20260623161921-w104-106`,
has none), 10 October 2026. The per-segment summary and the cross-check are in
`results/`.

- **182 same-winding lines, 2,830 points**, from 52 segments. The other 28
  are narrower than a column or show no clear gap, except two whose clear
  stretches the switch check cut below six windows; none of the eight
  segments of windings 69 to 84 gives a line. Many windings were traced more
  than once, so one physical gap often appears in two or three lines.
- The merged files load with the official `point_collection.load_point_collection`
  as 182 collections whose points have no `wind_a`, which the fitter reads as
  same-winding.
- **Switch check**: 143 lines (2,389 points) lie where a one-turn switch would
  show as a jump; the other 39 lines (441 points) are marked
  `one_turn_switch_checkable: false`.
- **Cross-trace check** (`eval_cross.py`): 2,441 of the 2,830 points can be
  checked against at least one other segment with lines whose mesh passes
  within half a sheet spacing of them along the normal. Each point gets one
  verdict over all of them:

  | | all other segments | other families only |
  |---|---|---|
  | points checked | 2,441 | 1,477 |
  | in a gap that trace kept, on text in none | 1,374 (56%) | 644 (44%) |
  | in a gap that trace found but rejected, on text in none | 291 (12%) | 213 (14%) |
  | only on blank papyrus that no run kept as a gap | 221 (9%) | 175 (12%) |
  | in a gap in one trace and on text in another | 329 (13%) | 189 (13%) |
  | on text, in no gap | 226 (9%) | 256 (17%) |

  A family is one trace and its later versions: segments with the same
  winding range in their name, or one named after the other. Another version
  of the same trace is not an independent check; 1,907 of the checked points
  have one among their second traces, and for 964 it is the only one, hence
  the second column. All renders come from the same ink-detection run, so the
  check tests whether traces agree, not the ink detection itself.

  A point on text in another trace means the two traces do not show the same
  papyrus there: a trace crossing between sheets, a blank strip that is not a
  gap, or a render that does not line up with its mesh. Text hits grow with
  the distance between the two meshes along the normal, from 6% of checks
  within 10 voxels to 51% at 40 to 45 voxels (the limit), so many mark two
  meshes that are not on the same surface there. The 728 such checks, on 555
  points, are listed with coordinates in `results/cross_report.json`. Half of
  those points come from 6 of 37 segments, and 130 of them fall on text in one
  later version of an older trace (`20260604223808-20231210121321_v8`). That
  trace confirms none of the gaps of the four segments that cross it on the
  same sheet, although its own line falls in gaps of one of them. Its render
  may not line up with its mesh everywhere (inferred, not yet checked by
  hand).
- **Layout of the scroll, measured from the data** (`gallery.py`): column
  pitch 32,192 voxels (77.3 mm), the median of the 73 of 128 neighbouring-gap
  spacings that fall between 3,000 and 5,000 render pixels. The length of one
  turn grows by 561 voxels per winding (straight fit over the winding numbers
  in 46 segment names, intercept 105 voxels), a sheet spacing of 89.4 voxels
  (214 µm).
- **Where gaps cannot catch a switch**: one turn equals one column pitch near
  winding 57 and two pitches near winding 114. A trace that slips one wrap
  there still sees a gap in almost the same place.
- **Not yet explained**: 18 of the 44 gap spacings larger than 5,000 pixels
  match the length of one turn within 5%, on segments of windings 89 to 115.
  A strip that repeats every turn may be a crack or fold through the wraps
  rather than a gap between columns, and a slip would not move it. Where a
  segment's own gap spacing is close to one turn, its lines are marked as
  unable to show a switch.

`gallery.py` builds a page with these numbers and every segment's overlay;
open `gallery/index.html` in a browser after cloning, or look at the images in
`gallery/img/` directly. `gallery/img/ct_check.png` shows two versions of the
trace of windings 28 to 37 and their gap points on one plane of the CT scan.

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
   80 px wide. After the checks below, a single dropped window inside a run is
   bridged (57 of the 2,830 Paris 4 points).
4. **Map into the scan.** Each kept point is mapped through the segment's mesh
   (render px × 8 × mesh scale = grid index, bilinear interpolation of x, y, z).
   Mesh grid steps are 20 voxels, so render pixels are voxels on the surface.
5. **Check in 3D and against the layout.**
   - *Mesh tears*: consecutive points must not sit much further apart in the
     volume than in the render.
   - *Sheet switches*: if the trace slips to another wrap, the text below the
     slip comes from one turn further along the sheet, and the gap appears
     shifted sideways by (length of one turn) mod (column pitch). The tool
     measures the length of one turn from the mesh and the umbilicus and
     works the shift out twice: with the scroll's column pitch
     (`--column-pitch-vox`, default 32,192 voxels for Paris 4) and with the
     segment's own gap spacing, which differs from it when a gap is missed
     or a strip repeats every turn. The smaller of the two shifts is used. A
     window where the line starts of the next column jump sideways by at
     least 60% of it (and by at least 60 px and four times the
     window-to-window jitter) is dropped, which cuts the line there, so no
     line spans a detected jump. On a segment with a single gap, which has no
     spacing of its own, any jump above the jitter threshold cuts the line.
   - *When a switch can hide*: where one turn is close to a whole number of
     column pitches, the shift is too small to tell from the jitter and the
     gaps cannot catch a switch. Each collection records whether a switch
     would have been visible (`one_turn_switch_checkable`); it is false too
     when fewer than six clean windows were left to look for a jump in.
6. **Write point collections.** One collection per continuous run of kept
   windows (at least six windows, about 6,000 voxels of column height).

## Output

`<segment>_intercolumnar_same_windings.json` follows
`vc_pointcollections_json_version` 1. Points carry `p` as `[x, y, z]` in
full-resolution voxels of the 2.4 µm volume `20260411134726` and no `wind_a`,
so `point_collection.py` loads them as same-winding collections. Each
collection's `metadata` holds the evidence a reviewer needs: for every point
its render coordinates, surface normal and the 3D line start of the next
column; for the line the gap's half-width, the column pitch and the segment's
gap spacing, the length of one turn, both predicted switch shifts, the
window-to-window jitter, and whether a switch would have been visible.

`results/PHercParis4_intercolumnar_same_windings_2.4um.json` merges every
segment in voxels of the 2.4 µm volume. `results/..._9.6um.json` is the same
file in the 4× downsampled frame that the published `spiral-input` dataset is
fitted in: level 2 of the volume (9.6 µm, the `voxel_size_um` of the
tutorial's `spiral-scroll.json`, shape 18,946 × 8,174 × 8,174 as in the
fitter's own tests). The fitter's default range of z 4,000 to 17,000 matches
our points, which span 3,554 to 17,727 in that frame. Voxel quantities in the
metadata (`line_start_xyz`, `radius_vox`) are scaled with the points.
`add_to_dataset.py` writes a copy of a dataset's `same_windings.json` with
the collections appended under fresh ids; the original file is not touched.

## Checks you can run without ground truth

- `eval_cross.py`: for each gap point, finds the other segments with lines
  (the ones `save_meshes.py` and `density_maps.py` prepare) whose mesh passes
  within half a sheet spacing of it along the normal, and asks whether their
  renders show a gap, blank papyrus or text there; one verdict per point, over
  all segments and over other families only, plus the share of text hits per
  distance band along the normal.
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
python ct_check.py --z 56973 --x 18549 --y 15904 --half 500 \
    --mesh MESH_A.tifxyz MESH_B.tifxyz --gaps A.json B.json --out ct_check.png
python gallery.py OUT site --ct ct_check.png
python add_to_dataset.py DATASET/same_windings.json \
    PHercParis4_intercolumnar_same_windings_9.6um.json same_windings_with_gaps.json
```

The Paris 4 run used the segment list in `data/paris4_segments.json` and the
umbilicus `PHercParis4/representations/umbilicus/20260411134726-umbilicus-20260524235033.json`
from the public bucket. The generator needs only a CPU and the packages above,
no model or training data.

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
  (about 57 and 114 on Paris 4) the gaps cannot reveal a one-turn switch.
- The column pitch is one number for the whole scroll; real columns vary in
  width, so the windings where a switch hides are approximate.
- The lines come from segments that are already traced. How many of them lie
  on surfaces the spiral dataset already holds as verified patches is not yet
  known (each `fit.log` of the A/B test reports that split).
- Not yet run through `fit_spiral.py`; the effect on `total_fg_pixels` is
  untested. The spiral dataset's frame is level 2 (9.6 µm) of the 2.4 µm
  volume, as the fitter's own tests and the volume's shape show; the dataset
  files themselves could not be checked from where this was written.

## License

MIT
