"""GIF composition and timing, including a real finish -> package -> verify handoff."""
from __future__ import annotations

import argparse
import json

import numpy as np
from PIL import Image
import pytest

from forge_testutils import assert_valid_contract, load_script, require_ffmpeg, run_cli, script_path

G = load_script("gif2dsprite", "gif2dsprite")
E = load_script("video2dsprite", "engine_export")
SCRIPT = script_path("gif2dsprite", "gif2dsprite")
PALETTE = [0, 0, 0, 220, 40, 40, 40, 180, 60, 40, 100, 220] + [0] * (256 * 3 - 12)


def paletted(box, colour=3):
    image = Image.new("P", (64, 64), 0)
    image.putpalette(PALETTE)
    image.paste(colour, box)
    return image


def write_gif(path, *, frames=None, durations=(40, 120, 70), disposal=2, loop=0, transparent=True):
    if frames is None:
        frames = [paletted((20 + dx, 12, 44 + dx, 52)) for dx in (0, 2, 0)]
    settings = {"save_all": True, "append_images": frames[1:], "disposal": disposal, "optimize": False}
    if durations is not None:
        settings["duration"] = durations
    if loop is not None:
        settings["loop"] = loop
    if transparent:
        settings["transparency"] = 0
    frames[0].save(path, **settings)
    return path


def extract(path, output):
    return G.extract(argparse.Namespace(gif=path, output_dir=output, default_duration_ms=None,
                                       art_source="existing"))


def select(decoded, output, *, frames=None, policy="source"):
    return G.select(argparse.Namespace(source=decoded / "gif-source.json", output_dir=output,
                                      frames_dir=frames or decoded / "frames-raw", loop_policy=policy))


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("disposal", [1, 2, 3])
def test_composites_local_rectangles_and_disposal(tmp_path, disposal):
    frames = [paletted((8, 8, 16, 16), 1), paletted((24, 8, 32, 16), 2),
              paletted((40, 8, 48, 16), 3)]
    source = write_gif(tmp_path / "input.gif", frames=frames, disposal=[1, disposal, 1])
    with Image.open(source) as gif:
        gif.seek(1)
        assert gif.tile[0][1] != (0, 0, 64, 64), "fixture must have a local update rectangle"
    decoded = tmp_path / "decoded"
    extract(source, decoded)
    images = [np.asarray(Image.open(p)) for p in sorted((decoded / "frames-raw").glob("*.png"))]
    assert all(im.shape == (64, 64, 4) for im in images)
    assert tuple(images[0][10, 10]) == (220, 40, 40, 255)
    assert tuple(images[1][10, 10]) == (220, 40, 40, 255)  # old red is retained
    assert tuple(images[1][10, 26]) == (40, 180, 60, 255)
    assert tuple(images[2][10, 42]) == (40, 100, 220, 255)
    if disposal == 1:
        assert images[2][10, 26, 3] == 255  # keep previous frame
    else:
        assert images[2][10, 26, 3] == 0  # background / previous restored
    if disposal == 3:
        assert images[2][10, 10, 3] == 255  # previous red survives restoration
    assert all(np.all(im[im[..., 3] == 0] == 0) for im in images)
    metadata = read(decoded / "gif-source.json")
    assert [row["durationMs"] for row in metadata["frames"]] == [40, 120, 70]
    assert metadata["durationMs"] == 230 and metadata["gifLoop"] == 0


@pytest.mark.parametrize("loop,expected", [(0, "cycle"), (None, "oneshot")])
def test_selection_preserves_variable_timing_and_binds_hashes(tmp_path, loop, expected):
    decoded = tmp_path / "decoded"
    extract(write_gif(tmp_path / "input.gif", loop=loop), decoded)
    selected = tmp_path / "selected"
    select(decoded, selected)
    data = read(selected / "selection.json")
    assert_valid_contract(data, "video", "frame_selection_v2")
    assert data["durations_ms"] == [40, 120, 70] and data["loopPolicy"] == expected
    assert (selected / data["sourceDirectory"]).resolve() == (decoded / "frames-raw").resolve()
    frames = sorted((decoded / "frames-raw").glob("*.png"))
    loaded = E.load_selection(selected / "selection.json", 3, lambda i: G.fc.sha256_file(frames[i]), None)
    assert loaded["timeline"].total_ms == 230
    paletted((12, 12, 30, 50)).convert("RGBA").save(frames[1])
    with pytest.raises(ValueError, match="stale selection"):
        E.load_selection(selected / "selection.json", 3, lambda i: G.fc.sha256_file(frames[i]), None)


def test_finite_repeat_needs_explicit_engine_policy(tmp_path):
    decoded = tmp_path / "decoded"
    extract(write_gif(tmp_path / "input.gif", loop=2), decoded)
    assert read(decoded / "gif-source.json")["gifLoop"] == 2
    with pytest.raises(ValueError, match="finite GIF repeats"):
        select(decoded, tmp_path / "refused")
    assert not (tmp_path / "refused").exists()
    assert select(decoded, tmp_path / "once", policy="oneshot")["loopPolicy"] == "oneshot"


@pytest.mark.parametrize("delays", [None, (40, 0, 70)])
def test_missing_or_zero_delays_need_an_explicit_fallback(tmp_path, delays):
    source = write_gif(tmp_path / "input.gif", durations=delays)
    output = tmp_path / "decoded"
    result = run_cli([SCRIPT, "extract", "--gif", source, "--output-dir", output])
    assert result.returncode == 1 and "--default-duration-ms" in result.stderr
    assert not output.exists() and not list(tmp_path.glob(".*.stage-*"))
    result = run_cli([SCRIPT, "extract", "--gif", source, "--output-dir", output,
                      "--default-duration-ms", "90"])
    assert result.returncode == 0, result.stderr
    metadata = read(output / "gif-source.json")
    assert [row["durationMs"] for row in metadata["frames"]] == ([90, 90, 90] if delays is None else [40, 90, 70])
    assert metadata["warnings"]


@pytest.mark.parametrize("kind", ["png", "still", "corrupt", "static_multiframe"])
def test_bad_or_static_inputs_publish_nothing(tmp_path, kind):
    source = tmp_path / "input.gif"
    if kind == "corrupt":
        source.write_bytes(b"GIF89a broken")
    elif kind == "static_multiframe":
        # A new transparent patch changes encoded indices but leaves displayed art unchanged.
        frames = [paletted((8, 8, 16, 16), 1), paletted((8, 8, 12, 12), 1)]
        write_gif(source, frames=frames, durations=(40, 40), disposal=1)
        with Image.open(source) as gif:
            assert gif.n_frames == 2
    else:
        paletted((8, 8, 16, 16)).save(source, format="PNG" if kind == "png" else "GIF")
    result = run_cli([SCRIPT, "extract", "--gif", source, "--output-dir", tmp_path / "out"])
    assert result.returncode == 1 and "error:" in result.stderr
    assert not (tmp_path / "out").exists() and not list(tmp_path.glob(".*.stage-*"))
    if kind == "static_multiframe":
        assert "visually identical" in result.stderr


def test_existing_output_and_changed_frame_count_are_refused(tmp_path):
    decoded = tmp_path / "decoded"
    source = write_gif(tmp_path / "input.gif")
    extract(source, decoded)
    original = (decoded / "gif-source.json").read_bytes()
    with pytest.raises(FileExistsError):
        extract(source, decoded)
    assert (decoded / "gif-source.json").read_bytes() == original
    (decoded / "frames-raw" / "frame_000001.png").unlink()
    with pytest.raises(ValueError, match="frame count differs"):
        select(decoded, tmp_path / "selection")
    assert not (tmp_path / "selection").exists()


@pytest.mark.parametrize("transparent,formats,delays", [
    (True, "png", (40, 120, 70)),
    (False, "png", (40, 120, 70)),
    pytest.param(True, "png,webm,packed", (40, 80, 40), marks=pytest.mark.ffmpeg),
])
def test_gif_through_finish_package_and_verify(tmp_path, transparent, formats, delays):
    # Synthetic source motion tests processing, not a model's animation quality.
    if formats != "png":
        require_ffmpeg()
    frames = [paletted((20 + dx, 12, 44 + dx, 52)) for dx in (0, 2, 0)]
    if not transparent:
        for im in frames:
            im.putpalette([255, 0, 255] + PALETTE[3:])
    source = write_gif(tmp_path / "source.gif", frames=frames, transparent=transparent, durations=delays)
    decoded = tmp_path / "decoded"
    extract(source, decoded)
    finish_input = decoded / "frames-raw"
    if not transparent:
        finish_input = tmp_path / "clean"
        result = run_cli([script_path("video2dsprite", "video2dsprite"), "clean",
                          "--raw-dir", decoded / "frames-raw", "--output-dir", finish_input, "--key", "magenta"])
        assert result.returncode == 0, result.stderr
    finished = tmp_path / "finished"
    result = run_cli([script_path("video2dsprite", "finish_frames"), "hd", "--frames", finish_input,
                      "--output-dir", finished, "--target-height", "20", "--character", "fixture"])
    assert result.returncode == 0, result.stderr
    selected = tmp_path / "selected"
    select(decoded, selected, frames=finished / "frames")
    geometry = read(finished / "finish.json")
    package = tmp_path / "package"
    result = run_cli([script_path("video2dsprite", "engine_export"), "package",
                      "--clean-dir", finished / "frames", "--selection", selected / "selection.json",
                      "--source-size", ",".join(map(str, geometry["size"])),
                      "--source-anchor", ",".join(map(str, geometry["anchor"])),
                      "--formats", formats, "--art-source", "existing", "--key", "none" if transparent else "magenta",
                      "--crf-packed", "1",  # high quality keeps this tiny fixture's alpha above the verify gate
                      "--name", "walk", "--output-dir", package])
    assert result.returncode == 0, result.stderr
    manifest = read(package / "animation.json")
    assert manifest["durationSeconds"] == sum(delays) / 1000 and manifest["loopPolicy"] == "cycle"
    assert manifest["artSource"] == "existing"
    validation = [script_path("video2dsprite", "validate_animation"), package, "--require-states", "walk"]
    if formats == "png":
        assert manifest["frameCount"] == 3 and manifest["durationsMs"] == list(delays)
    else:
        assert manifest["sourceIndices"] == [0, 1, 1, 2] and manifest["durationsMs"] == [40] * 4
        result = run_cli([script_path("video2dsprite", "engine_export"), "verify", "--package", package])
        assert result.returncode == 0, result.stderr
        validation.append("--require-verify")
    result = run_cli(validation)
    assert result.returncode == 0, result.stderr
