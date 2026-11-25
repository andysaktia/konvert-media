"""Katalog format keluaran beserta parameternya.

Satu `_f(...)` = satu format. UI dibangun otomatis dari daftar `params`, dan
`build` menyusun argumen ffmpeg dari nilai parameter. Menambah format baru =
menambah satu entri di FORMATS (dan satu fungsi build kecil bila perlu).
Format yang encodernya tidak ada di ffmpeg lokal otomatis disembunyikan.
"""
import json
import os
import re
import shlex
import tempfile
from pathlib import Path

GROUPS = [("video", "Video"), ("audio", "Audio"), ("image", "Gambar"), ("stream", "Streaming")]


# ---------------- spesifikasi parameter ----------------
def _sel(id, label, options, default, help="", adv=False):
    return {"id": id, "type": "select", "label": label, "help": help, "adv": adv, "default": default,
            "options": [{"v": v, "l": l} for v, l in options]}


def _chk(id, label, default=False, help="", adv=False):
    return {"id": id, "type": "check", "label": label, "help": help, "adv": adv, "default": default}


def _multi(id, label, options, default, help=""):
    return {"id": id, "type": "multi", "label": label, "help": help, "adv": False, "default": list(default),
            "options": [{"v": v, "l": l} for v, l in options]}


def _txt(id, label, default="", help="", adv=False, placeholder=""):
    return {"id": id, "type": "text", "label": label, "help": help, "adv": adv, "default": default,
            "placeholder": placeholder}


HEIGHTS = [("", "Ukuran asli"), ("2160", "2160p (4K)"), ("1440", "1440p"), ("1080", "1080p (Full HD)"),
           ("720", "720p (HD)"), ("480", "480p"), ("360", "360p"), ("240", "240p")]
QUALITY = [("small", "Kecil"), ("balanced", "Seimbang"), ("high", "Tinggi"), ("best", "Terbaik")]


def _height():
    return _sel("height", "Resolusi", HEIGHTS, "", "Rasio gambar tetap terjaga.")


def _quality():
    return _sel("quality", "Kualitas", QUALITY, "balanced", "Makin tinggi, makin besar file dan makin lama.")


def _abr(default="128", opts=("64", "96", "128", "160", "192", "256", "320")):
    return _sel("abr", "Bitrate audio", [(o, f"{o} kbps") for o in opts], default)


def _seg():
    return _sel("seg", "Panjang segmen", [(s, f"{s} detik") for s in ("2", "4", "6", "10")], "6",
                "Potongan video untuk streaming.")


def _speed():
    return _sel("speed", "Kecepatan encode",
                [("fast", "Cepat (file lebih besar)"), ("medium", "Seimbang"), ("slow", "Lambat (file lebih kecil)")],
                "medium", adv=True)


def _upscale():
    return _chk("upscale", "Perbesar video yang lebih kecil", False,
                "Bila mati, video yang sudah lebih kecil dari resolusi pilihan dibiarkan.", adv=True)


def _tail(default_out, folder=False, show_out=False):
    return [
        _txt("out", "Folder hasil" if folder else "Nama hasil", default_out,
             "Variabel: {name} nama file, {res} resolusi, {date} tanggal. Boleh memakai folder, "
             "mis. proyek/{name}.", adv=not show_out),
        _sel("exists", "Jika sudah ada", [("rename", "Beri nomor baru"), ("replace", "Timpa yang lama")],
             "rename", adv=True),
        _txt("extra", "Argumen ffmpeg tambahan", "",
             "Opsional, ditaruh sebelum nama file hasil. Tulis path dengan garis miring /.",
             adv=True, placeholder="-r 30 -af loudnorm"),
    ]


# ---------------- argumen tambahan (dibatasi) ----------------
BLOCKED = {"-i", "-y", "-n", "-filter_script", "-filter_complex_script", "-attach", "-dump_attachment",
           "-protocol_whitelist"}


def parse_extra(text):
    """Pecah argumen tambahan. Hanya opsi keluaran: tanpa input baru dan tanpa nama file tambahan."""
    if not text:
        return []
    try:
        toks = shlex.split(text)
    except ValueError:
        raise ValueError("Argumen tambahan: tanda kutip tidak berpasangan")
    prev_flag = False
    for t in toks:
        if t.lower() in BLOCKED or "movie=" in t.lower():
            raise ValueError(f"Argumen tambahan: {t} tidak diizinkan")
        flag = len(t) > 1 and t[0] == "-" and not t[1].isdigit()
        if not flag and not prev_flag:
            raise ValueError("Argumen tambahan harus berupa opsi ffmpeg yang diawali tanda minus. "
                             "Nama file hasil diatur otomatis.")
        prev_flag = flag
    return toks


def _extra(p):
    return parse_extra(p.get("extra", ""))


# ---------------- penyusun argumen ----------------
CRF = {"libx264": {"small": 28, "balanced": 23, "high": 20, "best": 17},
       "libx265": {"small": 32, "balanced": 28, "high": 24, "best": 21},
       "libvpx-vp9": {"small": 38, "balanced": 32, "high": 28, "best": 24}}
CPU = {"fast": "4", "medium": "2", "slow": "1"}  # -cpu-used untuk VP9
KBPS = {2160: 14000, 1440: 8000, 1080: 5000, 720: 3000, 480: 1400, 360: 800, 240: 400}


def _scale(p, ctx, even):
    """Filter skala untuk parameter `height`; kosong bila ukuran asli (kecuali dimensi ganjil)."""
    h = int(p.get("height") or 0)
    if not h:
        if even and (ctx["width"] % 2 or ctx["height"] % 2):  # yuv420p wajib genap
            return ["scale=trunc(iw/2)*2:trunc(ih/2)*2"]
        return []
    if p.get("upscale"):
        return [f"scale=-2:{h}" if even else f"scale=-1:{h}"]
    return [f"scale=-2:'min({h},trunc(ih/2)*2)'" if even else f"scale=-1:'min({h},ih)'"]


def _video(codec):
    def build(p, ctx, target):
        crf = str(CRF[codec][p["quality"]])
        a = ["-map", "0:v:0", "-map", "0:a?"]
        vf = _scale(p, ctx, True)
        if vf:
            a += ["-vf", ",".join(vf)]
        if codec == "libvpx-vp9":
            a += ["-c:v", codec, "-crf", crf, "-b:v", "0", "-row-mt", "1", "-cpu-used", CPU[p["speed"]],
                  "-c:a", "libopus"]
        else:
            a += ["-c:v", codec, "-pix_fmt", "yuv420p", "-preset", p["speed"], "-crf", crf, "-c:a", "aac"]
            if codec == "libx265":
                a += ["-tag:v", "hvc1"]
            a += ["-movflags", "+faststart"]
        return a + ["-b:a", p["abr"] + "k"] + _extra(p) + [str(target)]
    return build


def _gif(p, ctx, target):
    vf = (f"fps={p['fps']},scale='min({p['width']},iw)':-1:flags=lanczos,split[s0][s1];"
          "[s0]palettegen=stats_mode=diff[pal];[s1][pal]paletteuse=dither=bayer:bayer_scale=5")
    return ["-map", "0:v:0", "-vf", vf, "-loop", "0"] + _extra(p) + [str(target)]


def _remux(maps, flags=()):
    def build(p, ctx, target):
        a = [x for m in maps for x in ("-map", m)]
        return a + ["-c", "copy", *flags] + _extra(p) + [str(target)]
    return build


def _audio(codec, qargs):
    def build(p, ctx, target):
        return ["-map", "0:a:0", "-c:a", codec, *qargs(p)] + _extra(p) + [str(target)]
    return build


def _image(codec, qargs):
    def build(p, ctx, target):
        a = ["-map", "0:v:0"]
        vf = _scale(p, ctx, False)
        if vf:
            a += ["-vf", ",".join(vf)]
        return a + ["-c:v", codec, *qargs(p)] + _extra(p) + [str(target)]
    return build


def _rungs(p, ctx):
    """Tinggi tiap rendition HLS: yang dipilih dan tidak melebihi sumber; minimal satu."""
    src = (ctx["height"] or 0) // 2 * 2
    hs = sorted({int(x) for x in p["renditions"]}, reverse=True)
    return [h for h in hs if h <= src] or [src or 360]


def _kbps(h):
    return next((v for k, v in sorted(KBPS.items(), reverse=True) if k <= h), 400)


def _hls_copy(p, ctx, target):
    r, n = Path(target).as_posix(), ctx["stem"]
    return (["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-f", "hls", "-hls_time", p["seg"],
             "-hls_list_size", "0", "-hls_segment_filename", f"{r}/v0/{n}__%05d.ts"]
            + _extra(p) + [f"{r}/v0/{n}.m3u8"])


def _hls_abr(p, ctx, target):
    r, n, seg = Path(target).as_posix(), ctx["stem"], p["seg"]
    hs, audio = _rungs(p, ctx), ctx["has_audio"]
    k = len(hs)
    fc = (f"[0:v:0]split={k}" + "".join(f"[s{i}]" for i in range(k)) + ";"
          + ";".join(f"[s{i}]scale=-2:{h}[v{i}]" for i, h in enumerate(hs)))
    a = ["-filter_complex", fc]
    for i in range(k):
        a += ["-map", f"[v{i}]"] + (["-map", "0:a:0"] if audio else [])
    a += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", p["speed"], "-crf", str(CRF["libx264"][p["quality"]]),
          "-sc_threshold", "0", "-force_key_frames", f"expr:gte(t,n_forced*{seg})"]
    for i, h in enumerate(hs):  # batas bitrate => playlist utama punya BANDWIDTH yang benar
        a += [f"-maxrate:v:{i}", f"{_kbps(h)}k", f"-bufsize:v:{i}", f"{2 * _kbps(h)}k"]
    if audio:
        a += ["-c:a", "aac", "-b:a", p["abr"] + "k", "-ac", "2"]
    vsm = " ".join(f"v:{i},a:{i}" if audio else f"v:{i}" for i in range(k))
    return (a + ["-f", "hls", "-hls_time", seg, "-hls_list_size", "0", "-master_pl_name", "master.m3u8",
                 "-var_stream_map", vsm, "-hls_segment_filename", f"{r}/v%v/{n}__%05d.ts"]
            + _extra(p) + [f"{r}/v%v/{n}.m3u8"])


# ---------------- peringatan (ditampilkan di UI, tidak memblokir) ----------------
def _warn_scale(p, ctx):
    h = int(p.get("height") or 0)
    if h and ctx["height"] and h > ctx["height"] and not p.get("upscale"):
        return [f"{ctx['name']}: videonya {ctx['height']}p, lebih kecil dari {h}p, jadi ukurannya tidak diperbesar."]
    return []


def _warn_hls_copy(p, ctx):
    if ctx["vcodec"] != "h264" or (ctx["has_audio"] and ctx["acodec"] != "aac"):
        return [f"{ctx['name']}: isinya {ctx['vcodec']} + {ctx['acodec'] or 'tanpa audio'}. HLS salin paling aman "
                "untuk H.264 + AAC. Untuk codec lain pakai HLS adaptif (encode ulang)."]
    return []


def _warn_hls_abr(p, ctx):
    if ctx["height"] and min(int(x) for x in p["renditions"]) > ctx["height"]:
        return [f"{ctx['name']}: videonya {ctx['height']}p, hanya dibuat satu resolusi sebesar sumbernya."]
    return []


# ---------------- daftar format ----------------
def _f(id, group, label, desc, ext, kinds, params, build, needs=(), kind="file", **kw):
    return dict(id=id, group=group, label=label, desc=desc, ext=ext, kinds=tuple(kinds), params=params,
                build=build, needs=tuple(needs), kind=kind, **kw)


def _vparams():
    return [_height(), _quality(), _abr("128"), _speed(), _upscale(), *_tail("{name}_{res}")]


def _aparams(abr_default, opts):
    return [_abr(abr_default, opts), *_tail("{name}")]


_HLS_OUT = "m3u8/ts/{name}"

FORMATS = [
    # ---- Video ----
    _f("mp4-h264", "video", "MP4 (H.264)", "Paling kompatibel: jalan di semua perangkat dan browser.",
       "mp4", ("video",), _vparams(), _video("libx264"), ("libx264", "aac"), warn=_warn_scale),
    _f("mp4-h265", "video", "MP4 (H.265)",
       "File sekitar 40% lebih kecil dari H.264, tetapi perangkat lama mungkin tidak bisa memutarnya.",
       "mp4", ("video",), _vparams(), _video("libx265"), ("libx265", "aac"), warn=_warn_scale),
    _f("webm-vp9", "video", "WebM (VP9)", "Format terbuka untuk web. Encode lebih lama.",
       "webm", ("video",), _vparams(), _video("libvpx-vp9"), ("libvpx-vp9", "libopus"), warn=_warn_scale),
    _f("gif", "video", "GIF animasi", "Palet warna dioptimalkan. Sebaiknya untuk klip pendek.",
       "gif", ("video",),
       [_sel("fps", "Frame per detik", [(x, x) for x in ("8", "10", "12", "15", "20")], "12"),
        _sel("width", "Lebar maksimum", [(x, f"{x} px") for x in ("320", "480", "640", "800")], "480"),
        *_tail("{name}")], _gif),
    _f("mkv-remux", "video", "MKV (salin)", "Hanya ganti wadah, tanpa encode ulang. Sangat cepat dan kualitas tetap.",
       "mkv", ("video",), _tail("{name}"), _remux(("0:v:0", "0:a?", "0:s?"))),
    _f("mp4-remux", "video", "MP4 (salin)",
       "Hanya ganti wadah, tanpa encode ulang. Gagal bila codec sumber tidak cocok dengan MP4.",
       "mp4", ("video",), _tail("{name}"), _remux(("0:v:0", "0:a?"), ("-movflags", "+faststart"))),

    # ---- Audio (dari file audio, atau ekstrak dari video) ----
    _f("mp3", "audio", "MP3", "Kompatibel di mana saja.", "mp3", ("audio", "video"),
       _aparams("192", ("128", "192", "256", "320")), _audio("libmp3lame", lambda p: ["-b:a", p["abr"] + "k"]),
       ("libmp3lame",)),
    _f("m4a", "audio", "M4A (AAC)", "Kualitas bagus di ukuran kecil.", "m4a", ("audio", "video"),
       _aparams("192", ("128", "192", "256", "320")), _audio("aac", lambda p: ["-b:a", p["abr"] + "k"]), ("aac",)),
    _f("opus", "audio", "Opus", "Paling efisien untuk suara dan musik.", "opus", ("audio", "video"),
       _aparams("128", ("64", "96", "128", "160", "192", "256")),
       _audio("libopus", lambda p: ["-b:a", p["abr"] + "k"]), ("libopus",)),
    _f("ogg", "audio", "OGG (Vorbis)", "Format terbuka.", "ogg", ("audio", "video"),
       [_sel("q", "Kualitas", [("3", "Kecil"), ("5", "Seimbang"), ("7", "Tinggi"), ("9", "Terbaik")], "5"),
        *_tail("{name}")], _audio("libvorbis", lambda p: ["-q:a", p["q"]]), ("libvorbis",)),
    _f("flac", "audio", "FLAC", "Lossless: kualitas asli, ukuran lebih kecil dari WAV.", "flac", ("audio", "video"),
       _tail("{name}"), _audio("flac", lambda p: []), ("flac",)),
    _f("wav", "audio", "WAV", "Tanpa kompresi (PCM 16-bit).", "wav", ("audio", "video"),
       _tail("{name}"), _audio("pcm_s16le", lambda p: []), ("pcm_s16le",)),

    # ---- Gambar ----
    _f("webp", "image", "WebP", "Kecil dan mendukung transparansi.", "webp", ("image",),
       [_height(), _quality(), *_tail("{name}_{res}")],
       _image("libwebp", lambda p: ["-quality", {"small": "70", "balanced": "85", "high": "92", "best": "98"}[p["quality"]]]),
       ("libwebp",)),
    _f("jpg", "image", "JPG", "Paling umum, tanpa transparansi.", "jpg", ("image",),
       [_height(), _quality(), *_tail("{name}_{res}")],
       _image("mjpeg", lambda p: ["-q:v", {"small": "6", "balanced": "3", "high": "2", "best": "1"}[p["quality"]]]),
       ("mjpeg",)),
    _f("png", "image", "PNG", "Lossless dan mendukung transparansi.", "png", ("image",),
       [_height(), *_tail("{name}_{res}")], _image("png", lambda p: []), ("png",)),

    # ---- Streaming ----
    _f("hls-copy", "stream", "HLS (salin)",
       "Potong jadi playlist .m3u8 dan segmen .ts tanpa encode ulang. Cepat, tetapi sumber harus H.264 + AAC.",
       "m3u8", ("video",), [_seg(), *_tail(_HLS_OUT, folder=True, show_out=True)], _hls_copy,
       kind="dir", websafe=True, dirs=lambda p, ctx: ["v0"], main=lambda p, ctx: f"v0/{ctx['stem']}.m3u8",
       warn=_warn_hls_copy),
    _f("hls-abr", "stream", "HLS adaptif",
       "Beberapa resolusi sekaligus dengan playlist utama master.m3u8, jadi pemutar memilih sendiri sesuai koneksi.",
       "m3u8", ("video",),
       [_multi("renditions", "Resolusi yang dibuat", [(x, f"{x}p") for x in ("1080", "720", "480", "360", "240")],
               ("720", "480", "360"), "Resolusi lebih besar dari sumber dilewati. v0 selalu yang tertinggi."),
        _seg(), _quality(), _abr("128"), _speed(), *_tail(_HLS_OUT, folder=True, show_out=True)],
       _hls_abr, ("libx264", "aac"), kind="dir", websafe=True,
       dirs=lambda p, ctx: [f"v{i}" for i in range(len(_rungs(p, ctx)))], main=lambda p, ctx: "master.m3u8",
       warn=_warn_hls_abr),
]
BY_ID = {f["id"]: f for f in FORMATS}


def available(encoders):
    return [f for f in FORMATS if set(f["needs"]) <= encoders]


def public(f):
    return {k: f[k] for k in ("id", "group", "label", "desc", "kind", "ext", "kinds", "params")}


def accepts(fmt, f):
    """Apakah file `f` (hasil probe) cocok dengan format ini."""
    return f["kind"] in fmt["kinds"] and (fmt["group"] != "audio" or f["has_audio"])


def resolve(fmt, raw):
    """Validasi nilai parameter dari UI; lengkapi dengan nilai bawaan. ValueError bila tidak valid."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for s in fmt["params"]:
        v = raw.get(s["id"], s["default"])
        if s["type"] == "select":
            v = str(v)
            if v not in {o["v"] for o in s["options"]}:
                raise ValueError(f"{s['label']}: pilihan tidak valid")
        elif s["type"] == "check":
            v = bool(v)
        elif s["type"] == "multi":
            ok = {o["v"] for o in s["options"]}
            v = [str(x) for x in v] if isinstance(v, list) else []
            if not v or not set(v) <= ok:
                raise ValueError(f"{s['label']}: pilih minimal satu")
        else:
            v = str(v).strip()
            if len(v) > 300:
                raise ValueError(f"{s['label']}: terlalu panjang")
            v = v or s["default"]
        out[s["id"]] = v
    parse_extra(out.get("extra", ""))
    return out


def summary(fmt, p):
    """Ringkasan pengaturan utama untuk daftar hasil, mis. '480p, Seimbang, 128 kbps'."""
    out = []
    for s in fmt["params"]:
        if s["adv"]:
            continue
        v = p.get(s["id"])
        if s["type"] == "select" and v:
            out.append(next(o["l"] for o in s["options"] if o["v"] == v))
        elif s["type"] == "multi":
            out.append("/".join(f"{x}p" for x in sorted(v, key=int, reverse=True)))
    return ", ".join(out)


# ---------------- preset tersimpan (profiles.json, bisa dibagikan ke tim) ----------------
def slug(label):
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40]


def load_profiles(path):
    """-> (daftar preset valid, daftar peringatan untuk entri yang dilewati)."""
    path, out, warns = Path(path), [], []
    if not path.is_file():
        return out, warns
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
    except (OSError, ValueError, AssertionError):
        return out, [f"{path.name} tidak bisa dibaca (harus berupa daftar JSON)"]
    for e in data:
        try:
            fmt = BY_ID[e["format"]]
            params = resolve(fmt, e.get("params"))
            out.append({"id": e.get("id") or slug(e["label"]), "label": str(e["label"]), "format": fmt["id"],
                        "params": params})
        except (KeyError, TypeError, ValueError) as ex:
            warns.append(f"Preset {e.get('label', '?') if isinstance(e, dict) else '?'} dilewati: {ex}")
    return out, warns


def save_profiles(path, profiles):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(profiles, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)