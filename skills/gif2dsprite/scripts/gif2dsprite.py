#!/usr/bin/env python3
"""Decode animated GIFs and bind their timing to finished sprite frames.

Generation, keying, registration and finishing remain separate steps. No API calls.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from PIL import Image

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import forge_core as fc  # noqa: E402

SCHEMA = "gif2dsprite.source.v1"
MAX_PIXELS = 128_000_000


def extract(args: argparse.Namespace) -> dict:
    source, output = args.gif, args.output_dir
    rows, visual_hashes = [], set()
    with fc.staged_output(output) as stage:
        raw = stage / "frames-raw"
        raw.mkdir()
        with Image.open(source) as gif:
            if gif.format != "GIF":
                raise ValueError("input must be a GIF file, not a renamed still or video")
            size, loop = list(gif.size), gif.info.get("loop")
            count = gif.n_frames
            if count < 2:
                raise ValueError("GIF has only one frame; animated motion is required")
            if size[0] * size[1] * count > MAX_PIXELS:
                raise ValueError("GIF exceeds the 128 million decoded pixel budget")
            for index in range(count):
                # Pillow applies local rectangles, palettes and disposal while seeking.
                gif.seek(index)
                original_duration = gif.info.get("duration")
                duration = original_duration
                if duration is None or duration <= 0:
                    duration = args.default_duration_ms
                    if duration is None:
                        raise ValueError(f"frame {index} has no positive delay; set --default-duration-ms explicitly")
                rgba = gif.convert("RGBA")
                path = raw / f"frame_{index:06d}.png"
                fc.save_png(rgba, path)
                digest = fc.sha256_file(path)
                visual_hashes.add(digest)
                rows.append({"file": f"frames-raw/{path.name}", "sha256": digest,
                             "durationMs": duration, "sourceDurationMs": original_duration,
                             "disposal": getattr(gif, "disposal_method", 0)})
        if len(visual_hashes) < 2:
            raise ValueError("GIF frames are visually identical; animated motion is required")
        warnings = []
        if any(row["durationMs"] != row["sourceDurationMs"] for row in rows):
            warnings.append("Missing or zero delays replaced by the explicit default duration.")
        if loop not in (None, 0):
            warnings.append("Finite GIF repeats need an explicit cycle or oneshot policy at selection.")
        metadata = {"schema": SCHEMA, "source": {"file": source.name, "sha256": fc.sha256_file(source)},
                    "artSource": args.art_source, "size": size, "frameCount": count, "gifLoop": loop,
                    "durationMs": sum(row["durationMs"] for row in rows), "frames": rows,
                    "warnings": warnings, "status": "decoded-needs-visual-review"}
        fc.write_json(stage / "gif-source.json", metadata)
    return {"output": str(output), "metadata": str(output / "gif-source.json"),
            "frames": count, "durationMs": metadata["durationMs"], "warnings": warnings}


def select(args: argparse.Namespace) -> dict:
    metadata = fc.read_json(args.source, strict=True)
    if not isinstance(metadata, dict) or metadata.get("schema") != SCHEMA:
        raise ValueError("--source must be gif-source.json from extract")
    rows = metadata.get("frames")
    if not isinstance(rows, list) or len(rows) < 2 or len(rows) != metadata.get("frameCount"):
        raise ValueError("source frame count does not match its frame records")
    durations = [row.get("durationMs") if isinstance(row, dict) else None for row in rows]
    if any(type(value) is not int or value <= 0 for value in durations):
        raise ValueError("source durations must be positive whole milliseconds")
    # Same ordering as engine_export.list_frames; never include review PNGs here.
    frames = sorted(args.frames_dir.glob("clean_*.png")) or sorted(args.frames_dir.glob("*.png"))
    if len(frames) != len(rows):
        raise ValueError("finished frame count differs from GIF; preserve all frames or use a new retime selection")
    policy = args.loop_policy
    if policy == "source":
        loop = metadata.get("gifLoop")
        if loop not in (None, 0):
            raise ValueError("finite GIF repeats are not an engine loop policy; choose --loop-policy cycle or oneshot")
        policy = "cycle" if loop == 0 else "oneshot"
    size = None
    hashes = []
    for frame in frames:
        with Image.open(frame) as image:
            if image.format != "PNG" or (size is not None and image.size != size):
                raise ValueError("finished frames must be PNGs on one fixed canvas")
            image.load()
            size = image.size
        hashes.append(fc.sha256_file(frame))
    selection = {"schema": "forge-frame-selection/v2",
                 "sourceDirectory": fc.manifest_path(args.frames_dir, args.output_dir),
                 "sourceHashes": hashes, "start": 0, "endExclusive": len(frames),
                 "sourceIndices": list(range(len(frames))), "durations_ms": durations,
                 "loopPolicy": policy, "events": [], "status": "selected-needs-visual-review",
                 "method": "gif2dsprite: original GIF delays bound to final frames"}
    tick_ms = math.gcd(1000, *durations)
    if 1000 // tick_ms <= 60:
        selection.update(tickHz=1000 // tick_ms, ticks=[duration // tick_ms for duration in durations])
    with fc.staged_output(args.output_dir) as stage:
        fc.write_json(stage / "selection.json", selection)
        fc.write_json(stage / "gif-source.json", metadata)
    return {"selection": str(args.output_dir / "selection.json"), "frames": len(frames),
            "durationMs": sum(durations), "loopPolicy": policy, "artSource": metadata.get("artSource")}


def positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    decode = sub.add_parser("extract", help="composite a GIF into full RGBA frames with original delays")
    decode.add_argument("--gif", type=Path, required=True)
    decode.add_argument("--output-dir", type=Path, required=True)
    decode.add_argument("--default-duration-ms", type=positive_int,
                        help="explicit fallback for missing or zero GIF delays; otherwise refuse")
    decode.add_argument("--art-source", choices=("host_image", "api", "existing"), default="existing")
    decode.set_defaults(func=extract)
    timing = sub.add_parser("select", help="bind GIF timing to all final frames, in unchanged order")
    timing.add_argument("--source", type=Path, required=True, help="gif-source.json from extract")
    timing.add_argument("--frames-dir", type=Path, required=True, help="only final animation PNG frames")
    timing.add_argument("--output-dir", type=Path, required=True)
    timing.add_argument("--loop-policy", choices=("source", "cycle", "oneshot"), default="source")
    timing.set_defaults(func=select)
    return parser


def _run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(json.dumps(args.func(args), ensure_ascii=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    return fc.run_cli(_run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
