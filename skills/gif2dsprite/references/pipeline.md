# GIF workflow commands

Use these paths from the project root. `<gif-scripts>` is this skill's `scripts`
folder; `<video-scripts>` is the sibling `video2dsprite/scripts`. Replace placeholders
with actual paths and give every step a fresh output directory.

## Generate from one master

```text
python "<video-scripts>/video2dsprite.py" key-plan --master art/master.png
python "<video-scripts>/prepare_i2v_input.py" prepare --master art/master.png --action walk --output-dir work/walk-job
```

Read `prompt.txt` and `registration_job.json`. Pass `input.png` to the GIF-capable
tool as the reference. Keep the preparation geometry, key colour and action clauses,
but replace video-container/duration wording with the actual GIF request. For example:

> Create an actual animated GIF of this exact character walking in place, facing
> right, for one complete repeating gait cycle. Keep the camera and character scale
> fixed, match the reference canvas and keep the whole character and weapon visible.
> Alternate planted feet with clear leg movement; preserve identity and clothing.
> Use the flat background colour specified in the preparation prompt, with no gradient,
> ground shadow, text or extra objects. Return an animated GIF file, not a still sheet.

For idle request subtle breathing and a returning pose; attacks/hurt play once with
a clear anticipation, impact and recovery. Only request a duration/frame count the
tool supports; measure the returned GIF instead of assuming it obeyed the prompt.
Save it as `work/walk-source.gif` and keep the generation receipt beside it. Supplied
GIFs skip preparation/generation and retain their original canvas unless a real job
for that source is available.

## Decode, key, register, finish

```text
python "<gif-scripts>/gif2dsprite.py" extract --gif work/walk-source.gif --output-dir work/walk-decoded --art-source host_image
```

Outputs: `frames-raw/frame_000000.png` onward and `gif-source.json` with original
source hash, logical canvas, frame hashes, delays, disposal values and loop extension.
Sequential Pillow seeking composites updates; do not crop to a GIF's local frame
rectangle. The helper caps total decoded pixels at 128 million, as the packager does.
If a delay is missing/zero, choose a fallback based on the intended animation and
rerun into a fresh directory with `--default-duration-ms N`.

Inspect decoded frames. For **native transparency**, use `frames-raw` directly as
the next step's frame directory. For an **opaque key background**, run:

```text
python "<video-scripts>/video2dsprite.py" clean --raw-dir work/walk-decoded/frames-raw --output-dir work/walk-clean --key magenta --reference art/master.png
```

Replace `magenta` with the actual background. The cleaned PNGs are directly in
`work/walk-clean`. Do not key a transparent GIF again: that may remove a matching
costume colour. Inspect matte QA and the subject edges.

When the generated GIF used the prepared input:

```text
python "<video-scripts>/register_clip.py" apply --job work/walk-job/registration_job.json --frames work/walk-clean --output-dir work/walk-reg
python "<video-scripts>/finish_frames.py" hd --frames work/walk-reg --output-dir work/walk-finished --target-height 96 --role hero --character hero
```

For native-alpha input replace `work/walk-clean` by `work/walk-decoded/frames-raw`.
For supplied GIFs without a preparation job, skip `apply` and finish the decoded or
cleaned frame directory directly. Choose a target height no larger than the source
body. Use `--scale-ref <first-action-finish>` for later actions of the same character.
For pixel/hibit use the [existing finish commands](../../video2dsprite/references/finishing.md).

## Preserve timing and export

```text
python "<gif-scripts>/gif2dsprite.py" select --source work/walk-decoded/gif-source.json --frames-dir work/walk-finished/frames --loop-policy cycle --output-dir work/walk-timing
python "<video-scripts>/engine_export.py" package --clean-dir work/walk-finished/frames --selection work/walk-timing/selection.json --source-size W,H --source-anchor X,Y --key magenta --art-source host_image --name walk --formats png --output-dir game/hero/walk
python "<video-scripts>/validate_animation.py" game/hero --require-states walk
```

Read `W,H` from `finish.json` `size`, and `X,Y` from `anchor`; do not pass the old
registration geometry after finishing. Native-alpha art uses `--key none`; keyed
art uses its actual key. Carry the source route through `--art-source` (`existing`
for supplied GIFs). A deliberate one-shot uses `--loop-policy oneshot`; omit that
flag to use the GIF's loop extension. Finite repeats require an explicit decision.
Keep `gif-source.json` and the original GIF alongside the work artifacts for provenance.

PNG packaging preserves authored per-frame delays, except the existing exporter's
60 fps cap: check `fpsCapped`/`inputFps` for unusually fast GIFs and disclose any
reduction. Review `animation.json` `durationsMs`, frame count and total duration.
PNG-only packages use the package QA and validator; `engine_export.py verify` requires
an encoded video transport and must not be called for PNG-only output.
To ship video transports, use a new package directory and `--formats png,webm,packed`
only when the exporter can represent the delays on its tick grid. The GIF timing
binder records exact integer ticks when all delays fit a grid at <=60 Hz. If the
exporter refuses the timing, deliver PNGs or author a retimed selection; never
conceal the timing change. After encoding, run `engine_export.py verify --package
<package>`, then `validate_animation.py <package> --require-verify`.

For Godot SpriteFrames/Sprite3D or Aseprite, first create a clips manifest following
[frames-and-clips.md](../../generate2dsprite/references/frames-and-clips.md): list the
finished PNG paths in playback order, use the finished anchor, and copy the selection's
`durations_ms` to the clip's `duration_ms` (and cycle/oneshot to its loop policy).
Run `generate2dsprite/scripts/build_animation_clips.py --manifest <clips.json>
--output-dir <new>`, then pass its `animation-clips.json` to
`generate2dsprite/scripts/export_engine.py --clips <built>/animation-clips.json
--target all --output-dir <new>`. The video packager's `animation.json` is a different
contract and cannot be passed directly to that exporter. See the existing
[engine export commands](../../generate2dsprite/references/engine-export.md).
Review PNGs and play the original/final animation before accepting an action.

GIF decoding follows the [Pillow GIF documentation](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#gif).
