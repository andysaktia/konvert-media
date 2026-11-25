"""Pembungkus tipis ffmpeg/ffprobe: lokasi binary, probe, daftar encoder, penyusun perintah."""
import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOWIN = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: tanpa jendela console di Windows


@lru_cache(None)
def binary(name):
    """Urutan: env KONVERT_FFMPEG/KONVERT_FFPROBE, folder ffmpeg/bin di proyek, lalu PATH."""
    env = os.environ.get("KONVERT_" + name.upper())
    if env:
        return env
    exe = name + (".exe" if os.name == "nt" else "")
    for d in (ROOT / "ffmpeg" / "bin", ROOT / "ffmpeg"):
        if (d / exe).is_file():
            return str(d / exe)
    return shutil.which(name)


def require():
    missing = [n for n in ("ffmpeg", "ffprobe") if not binary(n)]
    if missing:
        raise RuntimeError(
            "Tidak menemukan: " + ", ".join(missing) + ".\n"
            "Windows: jalankan  winget install Gyan.FFmpeg  lalu buka terminal baru,\n"
            "atau taruh ffmpeg.exe dan ffprobe.exe di folder ffmpeg\\bin di proyek ini.")


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, creationflags=NOWIN)


@lru_cache(None)
def version():
    out = _run([binary("ffmpeg"), "-hide_banner", "-version"]).stdout
    return out.splitlines()[0] if out else "ffmpeg"


@lru_cache(None)
def encoders():
    """Nama semua encoder yang ada di build ffmpeg ini."""
    out = _run([binary("ffmpeg"), "-hide_banner", "-encoders"]).stdout
    return set(re.findall(r"^\s[VAS][A-Z.]{5}\s+(\S+)", out, re.M))


def _fps(s):
    try:
        n, d = s.get("avg_frame_rate", "0/1").split("/")
        return float(n) / float(d) if float(d) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path):
    """Ringkasan file: kind (video/audio/image), durasi, dimensi, codec. ValueError jika bukan media."""
    r = _run([binary("ffprobe"), "-v", "error", "-print_format", "json",
              "-show_format", "-show_streams", str(path)])
    if r.returncode != 0:
        # stderr ffprobe memuat path file di server; jangan diteruskan ke UI
        raise ValueError("bukan video, audio, atau gambar yang dikenali ffmpeg")
    d = json.loads(r.stdout or "{}")
    streams = d.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = d.get("format", {})
    fname = fmt.get("format_name", "")
    try:
        duration = float(fmt.get("duration") or 0)
    except ValueError:
        duration = 0.0

    if video and (fname == "image2" or fname.endswith("_pipe")):
        kind = "image"
    elif video:
        kind = "video"
    elif audio:
        kind = "audio"
    else:
        raise ValueError("File tidak berisi stream audio/video/gambar")

    info = {"kind": kind, "duration": duration, "width": 0, "height": 0,
            "vcodec": video.get("codec_name", "") if video else "",
            "acodec": audio.get("codec_name", "") if audio else "",
            "has_audio": audio is not None}
    if video:
        info["width"], info["height"] = video.get("width", 0), video.get("height", 0)
    if kind in ("video", "image"):
        txt = f"{info['width']}×{info['height']}, {video.get('codec_name', '?')}"
        if kind == "video" and _fps(video) >= 1:
            txt += f", {_fps(video):.0f} fps"
    else:
        txt = audio.get("codec_name", "?")
        if audio.get("sample_rate"):
            txt += f", {int(audio['sample_rate']) / 1000:g} kHz"
        if audio.get("channels"):
            txt += f", {audio['channels']} ch"
    info["summary"] = txt
    return info


def build_command(src, fmt, params, ctx, target):
    """Perintah ffmpeg lengkap (list argumen, tanpa shell). `target`: file hasil, atau folder untuk format dir."""
    head = [binary("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-progress", "pipe:1", "-nostats", "-i", str(src)]
    return head + fmt["build"](params, ctx, target)


def display_command(src_name, fmt, params, ctx, target):
    """Versi ringkas untuk ditampilkan dan disalin pengguna."""
    return ["ffmpeg", "-i", src_name] + fmt["build"](params, ctx, target)