---
name: gif2dsprite
description: Generate animated GIF actions from an approved character still using an available GIF-capable model, or process a supplied animated GIF into game-ready 2D sprites. Preserve GIF frame composition, transparency and per-frame timing, then reuse the Forge keying, registration, finishing and engine export tools. Use for GIF-to-sprite workflows and GIF-generated character animation.
---

# GIF to 2D Sprite

Use one approved master for identity, one animated GIF per action for motion, and the
existing video2dsprite tools for keying, registration, finishing and packaging. Decode
the GIF directly to PNGs: an intermediate MP4 loses transparency and variable delays.

Keep this folder beside `video2dsprite`, `generate2dsprite` and `generate2dmedia` when
installing. Python dependencies are the repository's `requirements.txt`. GIF decoding
and PNG packaging do not need FFmpeg; optional WebM/packed MP4 exports do.

## Choose the source

- **Generate:** inspect the current host tools for an actual animated-GIF output route.
  Use that tool's documented arguments, passing the approved master or prepared input
  as a reference. A still-image tool is not evidence of GIF capability. Do not invent
  `gif` parameters or call `route_media.py video` for a GIF request. That router has no
  GIF generation command.
- **Supplied GIF:** use it directly. If there is no master or recorded preparation
  transform, preserve the source canvas; do not invent a registration job.
- **No GIF-capable route:** prepare the action prompt and reference input, report the
  missing route and request a GIF or access to a compatible generator. Do not silently
  replace model motion with video, a static sheet, code animation or repeated stills.

Keep the original GIF and prompt, reference, tool/model name and any returned generation
receipt in the action's work folder. Use `--art-source host_image` for a host model,
`api` for an API, or `existing` for supplied art, and carry it through packaging.

## Workflow

`<skill-dir>` below is this folder; `<video-scripts>` is
`<skill-dir>/../video2dsprite/scripts`. Run from the user's project, writing artifacts
there. Each command gets a new output directory. The decoder and timing binder print
one JSON summary; failures exit 1 without publishing partial outputs.

1. **Plan the actions.** Reuse the approved master from the user or
   [generate2dsprite](../generate2dsprite/SKILL.md). Follow the user's action list; if
   unspecified, start with idle and walk. Decide facing, fixed canvas, game size,
   loop versus one-shot and finish (HD by default; pixel/hibit when requested).
2. **Prepare and generate each GIF.** Use `prepare_i2v_input.py prepare` when generating
   from a master so the existing registration transform is recorded. Adapt its motion
   prompt to request an actual GIF with fixed camera, constant scale, entire subject
   inside the canvas, no text and one action. Use a transparent background if the route
   really supports it, otherwise a flat key selected by `video2dsprite.py key-plan`.
   See [references/pipeline.md](references/pipeline.md) for commands and a prompt.
3. **Decode and inspect.** Run `gif2dsprite.py extract`. It checks the file format,
   composites local GIF updates/disposal into full RGBA frames, records each original
   delay, and refuses single-frame or visually static GIFs. Inspect the source animation
   and representative decoded frames for identity, clipping, camera movement and motion.
   A successful decode does not prove a useful walk or a seamless loop.
4. **Key and register.** Native alpha: use decoded frames directly, keeping transparent
   pixels. Opaque flat background: run `video2dsprite.py clean` and inspect its matte
   report and edges over light/dark backgrounds. Register with `register_clip.py apply`
   only when the GIF came from that job's input. Preserve frame count and order.
5. **Finish.** Reuse [finishing.md](../video2dsprite/references/finishing.md). Every action
   shares the master's scale, anchor and palette; use the first finish as `--scale-ref`
   for subsequent actions. Do not normalize each pose independently.
6. **Bind timing after the last frame edit.** Run `gif2dsprite.py select --source
   <decoded>/gif-source.json --frames-dir <finished>/frames --output-dir <timing>`.
   This writes the existing `forge-frame-selection/v2` contract with final frame hashes
   and original delays. Default policy follows GIF metadata: infinite loop -> cycle;
   no loop extension -> oneshot. Finite repeats require an explicit policy because the
   engine contract does not encode a repeat count. Set `--loop-policy cycle|oneshot`
   when the requested action differs from the source policy.
7. **Package and verify.** Pass that selection to `engine_export.py package`; start with
   `--formats png` to retain uneven delays exactly. Take source size and anchor from
   `finish.json`; native-alpha input uses `--key none`, keyed input uses its actual key.
   Run `validate_animation.py` for PNG-only output. For encoded transports, also run
   `engine_export.py verify`, then `validate_animation.py --require-verify`. Optional
   Godot/Aseprite exports use the existing engine exporter (see the pipeline reference).
8. **Review and deliver.** Inspect the finished animation, feet/contact, silhouette,
   weapons and first/last transition. Report source route, actions, frame counts,
   duration, loop policy, finish, validation and output paths. Distinguish numeric
   checks from visual acceptance; never mark motion approved just because files exist.

## Timing and quality decisions

- Never replace GIF delays by `1000 / frameCount` or an average FPS. GIF stores delays
  per frame, and zero/missing delays are ambiguous. `extract` refuses them unless
  `--default-duration-ms N` is explicitly chosen and records that substitution.
- The timing binder expects one final frame per decoded frame in the same order. It
  checks count and canvas and hashes the final files, but cannot infer a reordered
  animation. Do not remove, insert or reorder frames before rebinding original delays.
- A deliberate cut, loop search or retime needs a **new** selection on the final
  frames, using [animation-review.md](../video2dsprite/references/animation-review.md)
  or `gait_loop.py`/`retime.py`. Their FPS input assumes uniform timing: first author an
  explicit timing grid for variable-delay GIFs. Keep that change distinct from the
  preserved source timing. Do not merely change `fps` in JSON.
- WebM/packed MP4 require a representable constant-rate tick grid at <=60 fps. Let the
  existing exporter expand holds when exact; otherwise retain PNG-only output or
  deliberately retime and disclose the timing change. GIF palette edges and binary
  transparency cannot recover the original model's soft alpha.
- Regenerate an unusable take with a targeted fix. Do not hide cropped limbs, static
  walks or identity changes with padding, pose scaling or fabricated frames. Stop after
  three unsuccessful takes per action and report the remaining issue.
