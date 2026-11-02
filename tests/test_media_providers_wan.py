"""QianwenAI Wan HTTP contracts, checked offline against the platform docs on 2026-11-02."""
import base64
import json
from pathlib import Path

import pytest

from test_media_providers_common import (KEYS, assert_contracts, assert_no_secret, configure, http,  # noqa: F401
                                         ledger_lines, media_config, media_mp4, mp4, no_network, png,
                                         project, run_media, run_route, surfaces)

API = "https://maas.qianwenaiapi.com/api/v1"
SUBMIT = API + "/services/aigc/video-generation/video-synthesis"
VIDEO = "https://wan-output.example/clip.mp4?signature=temporary"


@pytest.fixture
def key(project, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", KEYS["DASHSCOPE_API_KEY"])
    (project / "still.png").write_bytes(png((512, 512)))


def done():
    return 200, {"output": {"task_status": "SUCCEEDED", "video_url": VIDEO},
                 "usage": {"output_video_duration": 6, "duration": 6}}


@pytest.mark.parametrize("tier,last", [("draft", False), ("standard", True), ("hero", True)])
def test_generation_request_poll_download_and_ledger(project, http, key, capsys, tier, last):
    model = "wan3.0-video"
    http.on("POST", SUBMIT, (200, {"output": {"task_id": "wan-task", "task_status": "PENDING"}}))
    http.on("GET", API + "/tasks/wan-task", (200, {"output": {"task_status": "PENDING"}}),
            (200, {"output": {"task_status": "RUNNING"}}), done())
    http.on("GET", VIDEO, (200, mp4()))
    code, result, err = run_route(capsys, "video", "--prompt-file", "prompt.txt", "--reference", "still.png",
                                  "--tier", tier, "--provider-option", "wan:seed=7", "--out-dir", "out/wan",
                                  *(["--last-frame", "still.png"] if last else []))
    assert code == 0, err
    assert (result["route"], result["model"], result["lastFrameUsed"]) == ("api:wan", model, last)
    assert result["estimateUsd"] is None
    post, = http.calls("POST")
    assert post.headers["authorization"] == "Bearer " + KEYS["DASHSCOPE_API_KEY"]
    assert post.headers["x-dashscope-async"] == "enable"
    params = {"resolution": "720P", "duration": 6, "ratio": "adaptive", "audio": False,
              "prompt_extend": False, "watermark": False, "seed": 7}
    uri = "data:image/png;base64," + base64.b64encode((project / "still.png").read_bytes()).decode()
    frames = ["first_frame", "last_frame"] if last else ["first_frame"]
    body = post.json()
    assert body == {"model": model, "input": {"prompt": body["input"]["prompt"],
                    "media": [{"type": name, "url": uri} for name in frames]},
                    "parameters": params}
    assert "authorization" not in http.calls("GET")[-1].headers
    job = json.loads(Path(result["job"]).read_text(encoding="utf-8"))
    assert job["apiBase"] == API and job["endpoint"] == SUBMIT
    assert job["consent"]["apiHost"] == "maas.qianwenaiapi.com"
    assert job["requestId"] == "wan-task" and job["durationReported"] == 6
    assert job["options"] == {"model": model, **params, "frames": frames}
    assert media_mp4.scan(Path(result["artifact"]).read_bytes())["tracks"] == ["vide"]
    assert ledger_lines(project)[-1]["jobId"] == "wan-task"
    assert_contracts(job, project)
    assert_no_secret(*surfaces(project, "out/wan", err, json.dumps(result)))
    assert "temporary" not in json.dumps(job)


@pytest.mark.parametrize("status,vendor_code,outcome", [
    (401, "InvalidApiKey", "auth"), (400, "Arrearage", "quota"),
    (429, "Throttling.RateQuota", "rate_limit"), (400, "Throttling.AllocationQuota", "quota"),
    (403, "AccessDenied.Unpurchased", "entitlement"), (404, "ModelNotFound", "entitlement"),
    (400, "DataInspectionFailed", "moderation"), (400, "InvalidParameter.DataInspection", "moderation"),
    (400, "IPInfringementSuspect", "moderation"), (400, "InvalidParameter", "invalid_request"),
    (503, "ServiceUnavailable", "provider_error"),
])
def test_submit_errors_are_classified_without_retries(project, http, key, capsys, status, vendor_code, outcome):
    http.on("POST", SUBMIT, (status, {"code": vendor_code, "message": "request refused", "request_id": "req-wan"}))
    rc, _, err = run_route(capsys, "video", "--route", "wan", "--prompt-file", "prompt.txt", "--reference",
                            "still.png", "--out-dir", "out/error")
    assert rc == 1 and f": {outcome}:" in err
    assert len(http.calls("POST")) == 1
    job = json.loads((project / "out/error/job.json").read_text(encoding="utf-8"))
    assert job["receipt"]["outcomeCode"] == outcome and job["error"]["code"] == vendor_code


@pytest.mark.parametrize("state,code,status,outcome", [("FAILED", "DataInspectionFailed", "failed", "moderation"),
    ("FAILED", "InternalError", "failed", "provider_error"), ("CANCELED", None, "failed", "failed"),
    ("UNKNOWN", None, "expired", "expired")])
def test_terminal_tasks_cannot_be_resubmitted(project, http, key, capsys, state, code, status, outcome):
    http.on("POST", SUBMIT, (200, {"output": {"task_id": "wan-failed"}}))
    http.on("GET", API + "/tasks/wan-failed", (200, {"output": {"task_status": state, "code": code}}))
    rc, _, err = run_route(capsys, "video", "--route", "wan", "--prompt-file", "prompt.txt", "--reference",
                            "still.png", "--out-dir", "out/fail")
    assert rc == 1 and f": {outcome}:" in err
    job = json.loads((project / "out/fail/job.json").read_text(encoding="utf-8"))
    assert job["status"] == status and job["receipt"]["outcomeCode"] == outcome
    rc, _, err = run_media(capsys, "resume", "--job", "out/fail/job.json")
    assert rc == 1 and "terminal" in err and len(http.calls("POST")) == 1


def test_poll_failure_resumes_without_another_post(project, http, key, capsys):
    http.on("POST", SUBMIT, (200, {"output": {"task_id": "wan-resume"}}))
    http.on("GET", API + "/tasks/wan-resume", (503, {"code": "ServiceUnavailable"}), done())
    http.on("GET", VIDEO, (200, mp4(audio=False)))
    rc, _, _ = run_route(capsys, "video", "--route", "wan", "--prompt-file", "prompt.txt", "--reference",
                          "still.png", "--out-dir", "out/resume")
    assert rc == 1
    rc, job, err = run_media(capsys, "resume", "--job", "out/resume/job.json")
    assert rc == 0, err
    assert job["status"] == "done" and len(http.calls("POST")) == 1


@pytest.mark.parametrize("reply,outcome", [({"code": "DataInspectionFailed"}, "moderation"),
    ({"code": KEYS["DASHSCOPE_API_KEY"]}, "failed"), ({"output": {"task_id": "../unsafe"}}, "bad_response")])
def test_bad_submit_responses_never_retry_or_leak_keys(project, http, key, capsys, reply, outcome):
    http.on("POST", SUBMIT, (200, reply))
    rc, result, err = run_route(capsys, "video", "--route", "wan", "--prompt-file", "prompt.txt", "--reference",
                                "still.png", "--out-dir", "out/bad")
    assert rc == 1 and f": {outcome}:" in err and len(http.requests) == 1
    texts = surfaces(project, "out/bad", err, json.dumps(result))
    assert_no_secret(*texts)
    assert all(KEYS["DASHSCOPE_API_KEY"][:60] not in text for text in texts)


@pytest.mark.parametrize("extra", [["--duration", "1"], ["--duration", "31"], ["--keyframe", "still.png@2"],
                                     ["--provider-option", "duration=30"], ["--provider-option", "audio=true"],
                                     ["--provider-option", "ratio=1:1"]])
def test_invalid_input_is_refused_before_sending(project, http, key, capsys, extra):
    rc, _, err = run_media(capsys, "video", "--provider", "wan", "--model", "wan3.0-video", "--prompt-file",
                            "prompt.txt", "--reference", "still.png", "--out-dir", "out/invalid", "--execute", *extra)
    assert rc == 1 and err and not http.requests


def test_prompt_limit_follows_wan30_docs(project, http, key, capsys):
    (project / "prompt.txt").write_text("x" * 20000, encoding="utf-8")
    rc, _, err = run_route(capsys, "video", "--route", "wan", "--prompt-file", "prompt.txt", "--reference",
                            "still.png", "--out-dir", "out/long", "--dry-run")
    assert rc == 0, err
    (project / "prompt.txt").write_text("x" * 20001, encoding="utf-8")
    rc, _, err = run_route(capsys, "video", "--route", "wan", "--prompt-file", "prompt.txt", "--reference",
                            "still.png", "--out-dir", "out/long")
    assert rc == 1 and "20000 characters" in err and not http.requests


def test_wan30_30_seconds_at_480p_reaches_the_api(project, http, key, capsys):
    http.on("POST", SUBMIT, (200, {"output": {"task_id": "wan-long"}}))
    http.on("GET", API + "/tasks/wan-long", (200, {"output": {"task_status": "SUCCEEDED", "video_url": VIDEO},
                                               "usage": {"output_video_duration": 30}}))
    http.on("GET", VIDEO, (200, mp4(audio=False)))
    rc, result, err = run_route(capsys, "video", "--route", "wan", "--prompt-file", "prompt.txt", "--reference",
                                "still.png", "--duration", "30", "--resolution", "480p", "--out-dir", "out/long")
    assert rc == 0, err
    assert result["model"] == "wan3.0-video" and result["durationUsed"] == 30
    assert http.calls("POST")[0].json()["parameters"]["resolution"] == "480P"


def test_wan_config_preference_and_doctor(project, key, monkeypatch, capsys):
    monkeypatch.delenv("DASHSCOPE_API_KEY")
    monkeypatch.setenv("PATH", str(project / "empty-bin"))
    configure(DASHSCOPE_API_KEY=KEYS["DASHSCOPE_API_KEY"], providers={"order": {"video": ["wan"]}})
    assert media_config.key_source("wan") == "config" and media_config.api_key("fal") is None
    rc, result, err = run_route(capsys, "resolve", "--kind", "video")
    assert rc == 0, err
    assert result["route"] == "api:wan" and result["order"][0] == "api:wan" and result["pinsLastFrame"]
    import route_media
    report = route_media.forge_doctor.diagnose(host_tools="none", project_dir=project, run_versions=False)
    assert report["apiKeys"]["wan"] and report["providers"]["wan"]["keyEnv"] == ["DASHSCOPE_API_KEY"]
    assert report["routeOrder"]["video"][0] == "api:wan"
    assert_no_secret(json.dumps(report), json.dumps(result))
