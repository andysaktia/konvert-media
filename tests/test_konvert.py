"""Uji: bagian murni (nama, parameter, perintah) + end-to-end dengan ffmpeg sungguhan."""
import http.client
import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from konvert import ffmpeg as ff
from konvert import paths
from konvert import server as server_mod
from konvert.presets import BY_ID, available, load_profiles, parse_extra, resolve
from konvert.server import make_server, safe_name

AVAILABLE = available(ff.encoders())


def ffmpeg_make(args, out):
    subprocess.run([ff.binary("ffmpeg"), "-v", "error", "-y", *args, str(out)], check=True)
    return out


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    lav = lambda spec: ["-f", "lavfi", "-i", spec]  # noqa: E731
    return {
        # H.264 + AAC genap 854x480: sumber ideal untuk HLS salin
        "video": ffmpeg_make([*lav("testsrc2=d=3:s=854x480:r=25"), *lav("sine=d=3"), "-shortest", "-c:v", "libx264",
                              "-preset", "ultrafast", "-g", "25", "-c:a", "aac"], d / "clip contoh.mp4"),
        # dimensi ganjil + codec mpeg4: menguji pembulatan genap dan peringatan HLS
        "odd": ffmpeg_make([*lav("testsrc2=d=2:s=641x481:r=15"), *lav("sine=d=2"), "-shortest", "-c:v", "mpeg4",
                            "-c:a", "aac"], d / "ganjil.mp4"),
        "small": ffmpeg_make([*lav("testsrc2=d=2:s=320x240:r=15"), *lav("sine=d=2"), "-shortest", "-c:v", "libx264",
                              "-preset", "ultrafast", "-c:a", "aac"], d / "kecil.mp4"),
        "silent": ffmpeg_make([*lav("testsrc2=d=2:s=320x240:r=15"), "-c:v", "mpeg4"], d / "bisu.mp4"),
        "audio": ffmpeg_make([*lav("sine=d=2"), "-c:a", "pcm_s16le"], d / "nada.wav"),
        "image": ffmpeg_make([*lav("testsrc2=s=801x401"), "-frames:v", "1"], d / "gambar.png"),
    }


@pytest.fixture
def server(tmp_path):
    srv = make_server(0, tmp_path / "work", tmp_path / "hasil", workers=2, profiles_path=tmp_path / "profiles.json")
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
    status, f = jcall(srv, "POST", "/api/upload", Path(path).read_bytes(),
                      {"X-Filename": path.name.replace(" ", "%20")})
    assert status == 200, f
    return f


def wait_job(srv, jid, want=("done", "error", "cancelled"), timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        _, jobs = jcall(srv, "GET", "/api/jobs")
        j = next(j for j in jobs if j["id"] == jid)
        if j["status"] in want:
            return j
        time.sleep(0.1)
    raise TimeoutError(j)


def convert(srv, f, fmt, params=None, timeout=60):
    """Kirim satu job dan tunggu selesai; kembalikan dict job (dan pastikan tidak ada galat submit)."""
    _, res = jcall(srv, "POST", "/api/jobs", {"files": [f["id"]], "format": fmt, "params": params or {}})
    assert not res["errors"], res["errors"]
    return wait_job(srv, res["jobs"][0]["id"], timeout=timeout)


def out(srv, job):
    return srv.jobs.out_dir / job["rel"]


# ---------- murni ----------
def test_safe_name():
    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name("C:\\Users\\a\\video: 1?.mp4") == "video_ 1_.mp4"
    assert safe_name("CON.mp4") == "_CON.mp4"
    assert safe_name("...") == "file"
    assert safe_name("a" * 300 + ".mkv").endswith(".mkv") and len(safe_name("a" * 300 + ".mkv")) <= 112


def test_render_template():
    v = {"name": "clip", "res": "480p", "date": "20261125", "fmt": "mp4-h264"}
    assert paths.render("{name}_{res}", v) == ["clip_480p"]
    assert paths.render("{name}_{res}", {**v, "res": ""}) == ["clip"]  # {res} kosong dirapikan
    assert paths.render("m3u8\\ts/{name}", v) == ["m3u8", "ts", "clip"]
    assert paths.render("/abs/C:/x", v) == ["abs", "C_", "x"]  # tidak bisa keluar ke path absolut
    assert paths.render("CON/{name}", v) == ["_CON", "clip"]
    for bad in ("../x", "a/../b", "{nope}", "{name", "{res}x{"):
        with pytest.raises(ValueError):
            paths.render(bad, {**v, "res": ""})
    with pytest.raises(ValueError):
        paths.render("{res}", {**v, "res": ""})  # hasil kosong


def test_web_safe_and_unique(tmp_path):
    assert paths.web_safe("Kelas 1 (fisika) 100%.v2") == "Kelas_1_fisika_100_.v2"
    assert paths.web_safe("???") == "file"
    (tmp_path / "a.mp4").write_text("x")
    assert paths.unique(tmp_path / "a.mp4").name == "a (2).mp4"
    (tmp_path / "a (2).mp4").write_text("x")
    assert paths.unique(tmp_path / "a.mp4").name == "a (3).mp4"
    (tmp_path / "d.v2").mkdir()
    assert paths.unique(tmp_path / "d.v2", True).name == "d.v2 (2)"  # folder: titik bukan ekstensi


def test_resolve_params():
    fmt = BY_ID["mp4-h264"]
    p = resolve(fmt, {"height": "480"})
    assert p["height"] == "480" and p["quality"] == "balanced" and p["abr"] == "128" and p["out"] == "{name}_{res}"
    for bad in ({"height": "999"}, {"quality": "x"}, {"abr": "1"}):
        with pytest.raises(ValueError):
            resolve(fmt, bad)
    with pytest.raises(ValueError):
        resolve(BY_ID["hls-abr"], {"renditions": []})
    assert resolve(BY_ID["hls-abr"], {})["renditions"] == ["720", "480", "360"]


def test_parse_extra():
    assert parse_extra("-r 30 -af loudnorm") == ["-r", "30", "-af", "loudnorm"]
    assert parse_extra("-an -sn") == ["-an", "-sn"]
    assert parse_extra("-ss -5") == ["-ss", "-5"]  # nilai negatif bukan opsi
    for bad in ("-i other.mp4", "out2.mp4", "-r 30 out2.mp4", "-vf movie=/etc/passwd", "-y", '-metadata "x',
                "-attach secret.txt"):
        with pytest.raises(ValueError):
            parse_extra(bad)


def test_build_commands():
    ctx = {"width": 1280, "height": 720, "has_audio": True, "stem": "clip", "name": "clip.mp4"}
    fmt = BY_ID["mp4-h264"]
    cmd = fmt["build"](resolve(fmt, {"height": "480", "abr": "128"}), ctx, "o.mp4")
    assert cmd[cmd.index("-vf") + 1] == "scale=-2:'min(480,trunc(ih/2)*2)'"
    assert ["-crf", "23"] == cmd[cmd.index("-crf"):cmd.index("-crf") + 2]
    assert ["-b:a", "128k"] == cmd[cmd.index("-b:a"):cmd.index("-b:a") + 2] and "+faststart" in cmd
    assert cmd[-1] == "o.mp4"
    up = fmt["build"](resolve(fmt, {"height": "480", "upscale": True}), ctx, "o.mp4")
    assert up[up.index("-vf") + 1] == "scale=-2:480"  # sama dengan script tim: paksa ke tinggi itu
    odd = fmt["build"](resolve(fmt, {}), {**ctx, "width": 641, "height": 481}, "o.mp4")
    assert odd[odd.index("-vf") + 1] == "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    assert "-vf" not in fmt["build"](resolve(fmt, {}), ctx, "o.mp4")  # ukuran asli genap: tanpa filter
    assert "-vf" not in BY_ID["mkv-remux"]["build"](resolve(BY_ID["mkv-remux"], {}), ctx, "o.mkv")
    img = BY_ID["webp"]["build"](resolve(BY_ID["webp"], {"height": "480"}), {**ctx, "height": 720}, "o.webp")
    assert img[img.index("-vf") + 1] == "scale=-1:'min(480,ih)'"


def test_shipped_profiles_are_valid():
    profiles, warns = load_profiles(ff.ROOT / "profiles.json")
    assert not warns and {"mp4-480p", "mp4-360p", "hls-m3u8"} <= {p["id"] for p in profiles}
    assert next(p for p in profiles if p["id"] == "hls-m3u8")["params"]["out"] == "m3u8/ts/{name}"


def test_out_dir_inside_work_rejected(tmp_path):
    with pytest.raises(ValueError):
        make_server(0, tmp_path / "work", tmp_path / "work" / "hasil", profiles_path=tmp_path / "p.json")


# ---------- end-to-end: setiap format ----------
@pytest.mark.parametrize("fmt", AVAILABLE, ids=lambda f: f["id"])
def test_every_format_converts(server, media, fmt):
    src = media["video" if fmt["kinds"][0] == "video" else fmt["kinds"][0]]
    job = convert(server, upload(server, src), fmt["id"])
    assert job["status"] == "done", job["error"]
    dest = out(server, job)
    if fmt["kind"] == "dir":
        assert dest.is_dir() and job["files"] >= 2  # minimal playlist + satu segmen
    else:
        assert dest.is_file() and dest.suffix == "." + fmt["ext"] and job["size"] == dest.stat().st_size > 0
        status, data = call(server, "GET", f"/api/jobs/{job['id']}/download")
        assert status == 200 and len(data) == job["size"]
    assert not any((server.jobs.work / "stage").iterdir())  # folder sementara bersih


@pytest.mark.parametrize("fid", ["mp4-h264", "mp4-h265", "webm-vp9"])
@pytest.mark.parametrize("height", ["", "360"])
def test_odd_source_gives_even_output(server, media, fid, height):
    job = convert(server, upload(server, media["odd"]), fid, {"height": height})
    assert job["status"] == "done", job["error"]
    info = ff.probe(out(server, job))
    assert info["width"] % 2 == 0 and info["height"] % 2 == 0
    if height:
        assert info["height"] == 360


# ---------- tiga script tim ----------
def test_team_mp4_480_and_360(server, media):
    f = upload(server, media["video"])  # 854x480
    j360 = convert(server, f, "mp4-h264", {"height": "360", "abr": "128"})
    assert j360["rel"] == "clip contoh_360p.mp4"
    i = ff.probe(out(server, j360))
    assert (i["width"], i["height"]) == (640, 360) and i["vcodec"] == "h264" and i["acodec"] == "aac"
    j480 = convert(server, f, "mp4-h264", {"height": "480", "abr": "128"})
    assert (j480["rel"], ff.probe(out(server, j480))["height"]) == ("clip contoh_480p.mp4", 480)


def test_upscale_is_opt_in(server, media):
    f = upload(server, media["small"])  # 320x240
    keep = convert(server, f, "mp4-h264", {"height": "480"})
    assert ff.probe(out(server, keep))["height"] == 240  # default: tidak diperbesar
    big = convert(server, f, "mp4-h264", {"height": "480", "upscale": True})
    assert ff.probe(out(server, big))["height"] == 480  # seperti script tim
    _, pv = jcall(server, "POST", "/api/preview", {"files": [f["id"]], "format": "mp4-h264", "params": {"height": "480"}})
    assert "tidak diperbesar" in pv["warnings"][0]


def test_team_hls_copy_layout(server, media, tmp_path):
    lav = lambda spec: ["-f", "lavfi", "-i", spec]  # noqa: E731
    src = ffmpeg_make([*lav("testsrc2=d=14:s=854x480:r=25"), *lav("sine=d=14"), "-shortest", "-c:v", "libx264",
                       "-preset", "ultrafast", "-g", "25", "-c:a", "aac"], tmp_path / "clip contoh.mp4")
    job = convert(server, upload(server, src), "hls-copy")  # bawaan: segmen 6 detik
    assert job["status"] == "done", job["error"]
    root = server.jobs.out_dir / "m3u8" / "ts" / "clip_contoh"  # {name} dibuat aman untuk URL
    assert job["rel"] == "m3u8/ts/clip_contoh"
    playlist = (root / "v0" / "clip_contoh.m3u8").read_text()
    segs = sorted(p.name for p in (root / "v0").glob("*.ts"))
    assert segs == ["clip_contoh__00000.ts", "clip_contoh__00001.ts", "clip_contoh__00002.ts"]  # 6 + 6 + 2 detik
    assert all(s in playlist for s in segs) and "#EXT-X-ENDLIST" in playlist and "TARGETDURATION:6" in playlist
    assert call(server, "GET", f"/api/jobs/{job['id']}/download")[0] == 404  # folder: dibuka dari folder hasil


def test_hls_copy_warns_for_non_h264(server, media):
    f = upload(server, media["odd"])  # mpeg4
    _, pv = jcall(server, "POST", "/api/preview", {"files": [f["id"]], "format": "hls-copy", "params": {}})
    assert "mpeg4" in pv["warnings"][0] and "HLS adaptif" in pv["warnings"][0]
    _, ok = jcall(server, "POST", "/api/preview", {"files": [upload(server, media["video"])["id"]],
                                                    "format": "hls-copy", "params": {}})
    assert ok["warnings"] == []


def test_hls_adaptive_ladder(server, media):
    f = upload(server, media["video"])  # 480p: rung 720 harus dilewati
    job = convert(server, f, "hls-abr", {"renditions": ["720", "480", "360"], "seg": "2"})
    assert job["status"] == "done", job["error"]
    root = out(server, job)
    master = (root / "master.m3u8").read_text()
    assert master.count("#EXT-X-STREAM-INF") == 2 and "RESOLUTION=854x480" in master and "RESOLUTION=640x360" in master
    assert all(int(l.split("BANDWIDTH=")[1].split(",")[0]) > 0 for l in master.splitlines() if "BANDWIDTH=" in l)
    assert sorted(p.name for p in root.iterdir() if p.is_dir()) == ["v0", "v1"]
    assert "v0/clip_contoh.m3u8" in master and (root / "v1" / "clip_contoh__00000.ts").is_file()


# ---------- penamaan & folder hasil ----------
def test_numbering_and_replace(server, media):
    f = upload(server, media["audio"])
    a = convert(server, f, "mp3")
    b = convert(server, f, "mp3")
    assert (a["rel"], b["rel"]) == ("nada.mp3", "nada (2).mp3")
    c = convert(server, f, "mp3", {"exists": "replace"})
    assert c["rel"] == "nada.mp3" and sorted(p.name for p in server.jobs.out_dir.iterdir()) == ["nada (2).mp3", "nada.mp3"]
    d = convert(server, upload(server, media["video"]), "hls-copy")
    e = convert(server, upload(server, media["video"]), "hls-copy")
    assert (d["rel"], e["rel"]) == ("m3u8/ts/clip_contoh", "m3u8/ts/clip_contoh (2)")  # folder diberi nomor


def test_folder_template(server, media):
    job = convert(server, upload(server, media["video"]), "mp4-h264", {"height": "360", "out": "proyek\\{date}/{name}_{res}.mp4"})
    assert job["rel"] == f"proyek/{time.strftime('%Y%m%d')}/clip contoh_360p.mp4"  # ".mp4" ganda tidak terjadi
    assert out(server, job).is_file()


def test_bad_template_and_extra_reported_per_file(server, media):
    f = upload(server, media["audio"])
    for params in ({"out": "../keluar"}, {"out": "{x}"}, {"out": "{res}"}, {"extra": "-i lain.wav"}, {"extra": "keluaran2.mp3"}):
        _, res = jcall(server, "POST", "/api/jobs", {"files": [f["id"]], "format": "mp3", "params": params})
        assert res["jobs"] == [] and res["errors"], params
    assert not any(server.jobs.out_dir.glob("**/*")) if server.jobs.out_dir.exists() else True


def test_extra_args_take_effect(server, media):
    job = convert(server, upload(server, media["video"]), "mp4-h264", {"extra": "-r 5"})
    assert job["status"] == "done" and ", 5 fps" in ff.probe(out(server, job))["summary"]


def test_remove_from_list_keeps_file(server, media):
    job = convert(server, upload(server, media["audio"]), "wav")
    assert jcall(server, "DELETE", f"/api/jobs/{job['id']}")[0] == 200
    assert out(server, job).is_file()  # hasil tetap ada di folder


def test_reveal(server, media, monkeypatch):
    seen = []
    monkeypatch.setattr(server_mod, "reveal", seen.append)
    job = convert(server, upload(server, media["audio"]), "flac")
    assert jcall(server, "POST", f"/api/jobs/{job['id']}/reveal")[0] == 200 and seen == [out(server, job)]
    assert jcall(server, "POST", "/api/open-out")[0] == 200 and seen[-1] == server.jobs.out_dir
    out(server, job).unlink()
    assert jcall(server, "POST", f"/api/jobs/{job['id']}/reveal")[0] == 404


# ---------- aturan format & keamanan ----------
def test_kind_and_audio_rules(server, media):
    wav, silent = upload(server, media["audio"]), upload(server, media["silent"])
    _, res = jcall(server, "POST", "/api/jobs", {"files": [wav["id"]], "format": "mp4-h264"})
    assert "tidak cocok" in res["errors"][0]["error"]
    _, res = jcall(server, "POST", "/api/jobs", {"files": [silent["id"]], "format": "mp3"})
    assert "tidak punya audio" in res["errors"][0]["error"]
    _, res = jcall(server, "POST", "/api/jobs", {"files": [wav["id"]], "format": "nope"})
    assert res["errors"]
    _, res = jcall(server, "POST", "/api/jobs", {"files": [wav["id"]], "format": "mp3", "params": {"abr": "7"}})
    assert res["errors"]


def test_audio_extracted_from_video(server, media):
    job = convert(server, upload(server, media["video"]), "mp3", {"abr": "128"})
    assert job["status"] == "done" and ff.probe(out(server, job))["kind"] == "audio"


def test_upload_non_media_rejected(server):
    status, body = jcall(server, "POST", "/api/upload", b"bukan media", {"X-Filename": "x.mp4"})
    assert status == 415 and "x.mp4" in body["error"] and str(server.jobs.work) not in body["error"]
    assert not any((server.jobs.work / "in").iterdir())  # tak ada sisa


def test_rejects_foreign_host_and_origin(server):
    assert call(server, "GET", "/api/info", headers={"Host": "evil.example"})[0] == 403
    assert call(server, "GET", "/api/info", headers={"Origin": "http://evil.example"})[0] == 403
    assert call(server, "GET", "/api/info")[0] == 200


def test_static_whitelist(server):
    assert call(server, "GET", "/")[0] == 200
    assert call(server, "GET", "/../konvert/server.py")[0] == 404
    assert call(server, "GET", "/server.py")[0] == 404


def test_info_lists_only_available_formats(server):
    _, info = jcall(server, "GET", "/api/info")
    assert {f["id"] for f in info["formats"]} == {f["id"] for f in AVAILABLE}
    assert [g["id"] for g in info["groups"]] == ["video", "audio", "image", "stream"]
    assert info["out_dir"] == str(server.jobs.out_dir)
    assert all("build" not in f for f in info["formats"])  # fungsi tidak ikut dikirim


def test_preview_command_and_errors(server, media):
    f = upload(server, media["video"])
    _, pv = jcall(server, "POST", "/api/preview", {"files": [f["id"]], "format": "mp4-h264", "params": {"height": "480"}})
    assert pv["command"][:3] == ["ffmpeg", "-i", "clip contoh.mp4"] and "-vf" in pv["command"]
    assert pv["display"] == "clip contoh_480p.mp4" and pv["command"][-1].endswith("hasil/clip contoh_480p.mp4")
    _, pv = jcall(server, "POST", "/api/preview", {"files": [], "format": "hls-copy", "params": {}})
    assert pv["display"] == "m3u8/ts/contoh/v0/contoh.m3u8"  # tanpa file: memakai contoh
    assert jcall(server, "POST", "/api/preview", {"format": "mp3", "params": {"out": "../x"}})[0] == 400
    assert jcall(server, "POST", "/api/preview", {"format": "mp3", "params": {"extra": "-i x"}})[0] == 400


def test_profiles_crud(server, tmp_path):
    assert jcall(server, "GET", "/api/info")[1]["profiles"] == []
    _, r = jcall(server, "POST", "/api/profiles", {"label": "Kursus 480p", "format": "mp4-h264",
                                                     "params": {"height": "480", "out": "kursus/{name}"}})
    assert r["profiles"][0]["id"] == "kursus-480p" and r["profiles"][0]["params"]["out"] == "kursus/{name}"
    saved = json.loads((tmp_path / "profiles.json").read_text())
    assert saved[0]["format"] == "mp4-h264" and saved[0]["params"]["height"] == "480"
    jcall(server, "POST", "/api/profiles", {"label": "Kursus 480p", "format": "mp4-h264", "params": {"height": "360"}})
    assert len(server.profiles) == 1 and server.profiles[0]["params"]["height"] == "360"  # label sama: diperbarui
    assert jcall(server, "POST", "/api/profiles", {"label": "x", "format": "nope"})[0] == 400
    assert jcall(server, "POST", "/api/profiles", {"label": "!!!", "format": "mp3"})[0] == 400
    assert jcall(server, "POST", "/api/profiles", {"label": "ok", "format": "mp3", "params": {"abr": "1"}})[0] == 400
    assert jcall(server, "DELETE", "/api/profiles/kursus-480p")[1]["profiles"] == []
    assert jcall(server, "DELETE", "/api/profiles/kursus-480p")[0] == 404


# ---------- pembatalan & progress ----------
def test_cancel_running_job(server, tmp_path):
    long = ffmpeg_make(["-f", "lavfi", "-i", "testsrc2=d=120:s=1280x720:r=30", "-c:v", "mpeg4", "-q:v", "5"],
                       tmp_path / "long.mp4")
    f = upload(server, long)
    _, res = jcall(server, "POST", "/api/jobs", {"files": [f["id"]], "format": "mp4-h264",
                                                 "params": {"quality": "best", "speed": "slow"}})
    jid = res["jobs"][0]["id"]
    wait_job(server, jid, want=("running",))
    time.sleep(0.5)
    jcall(server, "POST", f"/api/jobs/{jid}/cancel")
    assert wait_job(server, jid)["status"] == "cancelled"
    assert not any(server.jobs.out_dir.glob("**/*")) and not any((server.jobs.work / "stage").iterdir())
    assert jcall(server, "DELETE", f"/api/jobs/{jid}")[0] == 200


def test_failed_job_leaves_nothing(server, media):
    # encoder yang tidak ada -> ffmpeg gagal; pesan galatnya harus sampai ke UI
    job = convert(server, upload(server, media["video"]), "mp4-h264", {"extra": "-c:v encoder_palsu"})
    assert job["status"] == "error" and "encoder_palsu" in job["error"]
    assert not any(server.jobs.out_dir.glob("**/*")) and not any((server.jobs.work / "stage").iterdir())


def test_progress_reported(server, tmp_path):
    src = ffmpeg_make(["-f", "lavfi", "-i", "testsrc2=d=20:s=640x360:r=30", "-c:v", "mpeg4", "-q:v", "5"], tmp_path / "p.mp4")
    f = upload(server, src)
    q = server.jobs.subscribe()
    jcall(server, "POST", "/api/jobs", {"files": [f["id"]], "format": "mp4-h264"})
    seen, end = set(), time.time() + 60
    while time.time() < end:
        msg = q.get(timeout=30)
        if msg["type"] == "job":
            seen.add(int(msg["job"]["percent"]))
            if msg["job"]["status"] == "done":
                break
    assert 100 in seen and len(seen) > 2  # progress bertahap, bukan hanya 0 -> 100


def test_delete_file(server, media):
    f = upload(server, media["audio"])
    assert jcall(server, "DELETE", f"/api/files/{f['id']}")[0] == 200
    assert jcall(server, "DELETE", f"/api/files/{f['id']}")[0] == 404