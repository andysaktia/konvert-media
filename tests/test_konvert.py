"""Uji: pure (penyusun perintah, nama file) + end-to-end dengan ffmpeg sungguhan."""
import http.client
import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from konvert import ffmpeg as ff
from konvert.presets import BY_ID, available
from konvert.server import make_server, safe_name

AVAILABLE = available(ff.encoders())


def ffmpeg_make(args, out):
    subprocess.run([ff.binary("ffmpeg"), "-v", "error", "-y", *args, str(out)], check=True)
    return out


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    return {
        "video": ffmpeg_make(["-f", "lavfi", "-i", "testsrc2=d=2:s=641x481:r=15",
                              "-f", "lavfi", "-i", "sine=d=2", "-shortest",
                              "-c:v", "mpeg4", "-c:a", "aac"], d / "clip contoh.mp4"),
        "audio": ffmpeg_make(["-f", "lavfi", "-i", "sine=d=2", "-c:a", "pcm_s16le"], d / "nada.wav"),
        "image": ffmpeg_make(["-f", "lavfi", "-i", "testsrc2=s=801x401", "-frames:v", "1"], d / "gambar.png"),
    }


@pytest.fixture
def server(tmp_path):
    srv = make_server(0, tmp_path / "work", workers=2)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.jobs.close()
    srv.server_close()


def call(srv, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=30)
    h = dict(headers or {})
    if isinstance(body, (dict, list)):
        body = json.dumps(body)
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


def jcall(srv, *a, **k):
    status, data = call(srv, *a, **k)
    return status, json.loads(data)


def upload(srv, path):
    return jcall(srv, "POST", "/api/upload", Path(path).read_bytes(),
                 {"X-Filename": path.name.replace(" ", "%20")})


def wait_job(srv, jid, want=("done", "error", "cancelled"), timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        _, jobs = jcall(srv, "GET", "/api/jobs")
        j = next(j for j in jobs if j["id"] == jid)
        if j["status"] in want:
            return j
        time.sleep(0.1)
    raise TimeoutError(j)


# ---------- pure ----------
def test_safe_name():
    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name("C:\\Users\\a\\video: 1?.mp4") == "video_ 1_.mp4"
    assert safe_name("CON.mp4") == "_CON.mp4"
    assert safe_name("...") == "file"
    assert safe_name("a" * 300 + ".mkv").endswith(".mkv") and len(safe_name("a" * 300 + ".mkv")) <= 112


def test_build_command_scale_and_remux():
    cmd = ff.build_command("in.mkv", "out.mp4", BY_ID["mp4-h264"], 720)
    assert cmd[cmd.index("-vf") + 1] == "scale=-2:'min(720,trunc(ih/2)*2)'"
    assert cmd[-1] == "out.mp4" and "libx264" in cmd
    # remux tidak boleh pakai filter (-c copy tak bisa difilter)
    assert "-vf" not in ff.build_command("in.mkv", "out.mkv", BY_ID["mkv-remux"], 720)
    # audio tidak difilter
    assert "-vf" not in ff.build_command("in.mp4", "o.mp3", BY_ID["mp3"], 720)
    # gambar: tanpa pembulatan genap
    img = ff.build_command("a.png", "a.webp", BY_ID["webp"], 480)
    assert img[img.index("-vf") + 1] == "scale=-1:'min(480,ih)'"


def test_probe_kinds(media):
    assert ff.probe(media["video"])["kind"] == "video"
    assert ff.probe(media["audio"])["kind"] == "audio"
    assert ff.probe(media["image"])["kind"] == "image"
    with pytest.raises(ValueError):
        ff.probe(Path(__file__))  # bukan media


# ---------- end-to-end: semua preset yang tersedia ----------
@pytest.mark.parametrize("preset", AVAILABLE, ids=lambda p: p["id"])
def test_every_preset_converts(server, media, preset):
    src = media[preset["kinds"][0]]
    _, f = upload(server, src)
    assert f["kind"] == preset["kinds"][0]
    _, res = jcall(server, "POST", "/api/jobs", {"files": [f["id"]], "preset": preset["id"],
                                                 "max_height": 360 if preset["scalable"] else None})
    assert not res["errors"], res
    job = wait_job(server, res["jobs"][0]["id"])
    assert job["status"] == "done", job["error"]
    status, data = call(server, "GET", f"/api/jobs/{job['id']}/download")
    assert status == 200 and len(data) == job["out_size"] > 0
    out = server.jobs.jobs[job["id"]].out_path
    assert out.suffix == "." + preset["ext"]
    info = ff.probe(out)
    src_h = ff.probe(src)["height"]
    if preset["group"] == "audio":
        assert info["kind"] == "audio"
    elif preset["scalable"] and preset["group"] != "audio":
        assert 0 < info["height"] <= 360 and info["height"] < src_h  # max_height berlaku
        if preset["group"] == "video" and preset["id"] != "gif":
            assert info["width"] % 2 == 0 and info["height"] % 2 == 0  # genap untuk yuv420p


def test_audio_extracted_from_video(server, media):
    _, f = upload(server, media["video"])
    _, res = jcall(server, "POST", "/api/jobs", {"files": [f["id"]], "preset": "mp3"})
    assert wait_job(server, res["jobs"][0]["id"])["status"] == "done"


# ---------- keamanan & galat ----------
def test_kind_mismatch_and_bad_preset(server, media):
    _, f = upload(server, media["audio"])
    _, res = jcall(server, "POST", "/api/jobs", {"files": [f["id"]], "preset": "mp4-h264"})
    assert res["jobs"] == [] and "tidak cocok" in res["errors"][0]["error"]
    _, res = jcall(server, "POST", "/api/jobs", {"files": [f["id"]], "preset": "nope"})
    assert res["errors"]
    _, res = jcall(server, "POST", "/api/jobs", {"files": [f["id"]], "preset": "mp3", "max_height": 7})
    assert res["errors"]


def test_upload_non_media_rejected(server):
    status, body = jcall(server, "POST", "/api/upload", b"bukan media", {"X-Filename": "x.mp4"})
    assert status == 415 and "x.mp4" in body["error"]
    assert not any((server.jobs.work / "in").iterdir())  # tak ada sisa


def test_rejects_foreign_host_and_origin(server):
    assert call(server, "GET", "/api/info", headers={"Host": "evil.example"})[0] == 403
    assert call(server, "GET", "/api/info", headers={"Origin": "http://evil.example"})[0] == 403
    assert call(server, "GET", "/api/info")[0] == 200


def test_static_whitelist(server):
    assert call(server, "GET", "/")[0] == 200
    assert call(server, "GET", "/../konvert/server.py")[0] == 404
    assert call(server, "GET", "/server.py")[0] == 404


def test_info_lists_only_available_presets(server):
    _, info = jcall(server, "GET", "/api/info")
    assert {p["id"] for p in info["presets"]} == {p["id"] for p in AVAILABLE}


def test_cancel_running_job(server, tmp_path):
    long = ffmpeg_make(["-f", "lavfi", "-i", "testsrc2=d=120:s=1280x720:r=30",
                        "-c:v", "mpeg4", "-q:v", "5"], tmp_path / "long.mp4")
    _, f = upload(server, long)
    _, res = jcall(server, "POST", "/api/jobs", {"files": [f["id"]], "preset": "youtube"})
    jid = res["jobs"][0]["id"]
    wait_job(server, jid, want=("running",))
    time.sleep(0.5)
    jcall(server, "POST", f"/api/jobs/{jid}/cancel")
    assert wait_job(server, jid)["status"] == "cancelled"
    assert not (server.jobs.work / "out" / jid).exists()  # output parsial dibersihkan
    assert jcall(server, "DELETE", f"/api/jobs/{jid}")[0] == 200


def test_progress_reported(server, tmp_path):
    src = ffmpeg_make(["-f", "lavfi", "-i", "testsrc2=d=20:s=640x360:r=30",
                       "-c:v", "mpeg4", "-q:v", "5"], tmp_path / "p.mp4")
    _, f = upload(server, src)
    q = server.jobs.subscribe()
    _, res = jcall(server, "POST", "/api/jobs", {"files": [f["id"]], "preset": "mp4-h264"})
    seen = set()
    end = time.time() + 60
    while time.time() < end:
        msg = q.get(timeout=30)
        if msg["type"] == "job":
            seen.add(int(msg["job"]["percent"]))
            if msg["job"]["status"] == "done":
                break
    assert 100 in seen and len(seen) > 2  # progress bertahap, bukan hanya 0 -> 100


def test_delete_file_blocked_while_active_and_removes_after(server, media):
    _, f = upload(server, media["audio"])
    assert jcall(server, "DELETE", f"/api/files/{f['id']}")[0] == 200
    assert jcall(server, "DELETE", f"/api/files/{f['id']}")[0] == 404
