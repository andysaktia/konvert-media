"""Katalog preset. Satu preset = stream yang dipetakan + argumen ffmpeg + ekstensi output.

`needs` berisi encoder yang harus ada di build ffmpeg lokal; preset yang
encodernya tidak ada otomatis disembunyikan, jadi daftar selalu sesuai ffmpeg
yang terpasang. Menambah format baru cukup menambah satu baris `_p(...)`.
"""

HEIGHTS = [2160, 1440, 1080, 720, 480, 360]  # pilihan "tinggi maksimum"

_V = ("0:v:0", "0:a?")  # video pertama + semua audio (jika ada)
_H264 = "-c:v libx264 -pix_fmt yuv420p -c:a aac -movflags +faststart"


def _p(id, group, label, desc, ext, kinds, args, maps, needs=(), vf=(), scalable=False):
    return dict(id=id, group=group, label=label, desc=desc, ext=ext, kinds=kinds,
                args=args.split(), maps=maps, needs=tuple(needs), vf=list(vf),
                scalable=scalable)


PRESETS = [
    # ---- Video ----
    _p("mp4-h264", "video", "MP4 · H.264", "Paling kompatibel, cocok di semua perangkat",
       "mp4", ("video",), _H264 + " -preset medium -crf 23 -b:a 160k", _V,
       ("libx264", "aac"), scalable=True),
    _p("mp4-h265", "video", "MP4 · H.265", "Ukuran lebih kecil dari H.264, encode lebih lama",
       "mp4", ("video",),
       "-c:v libx265 -pix_fmt yuv420p -tag:v hvc1 -c:a aac -movflags +faststart "
       "-preset medium -crf 28 -b:a 160k", _V, ("libx265", "aac"), scalable=True),
    _p("webm-vp9", "video", "WebM · VP9", "Format terbuka untuk web",
       "webm", ("video",),
       "-c:v libvpx-vp9 -crf 32 -b:v 0 -row-mt 1 -c:a libopus -b:a 128k", _V,
       ("libvpx-vp9", "libopus"), scalable=True),
    _p("youtube", "video", "Kualitas tinggi (YouTube)", "H.264 CRF 18, encode lambat tapi bersih",
       "mp4", ("video",), _H264 + " -preset slow -crf 18 -b:a 192k", _V,
       ("libx264", "aac"), scalable=True),
    _p("chat", "video", "Ringkas (WhatsApp/chat)", "Maks 720p, ukuran kecil, encode cepat",
       "mp4", ("video",), _H264 + " -preset veryfast -crf 28 -b:a 96k -ac 2", _V,
       ("libx264", "aac"), vf=["scale=-2:'min(720,trunc(ih/2)*2)'"], scalable=True),
    _p("gif", "video", "GIF animasi", "12 fps, lebar maks 480px, palet warna optimal",
       "gif", ("video",), "-loop 0", ("0:v:0",), (),
       vf=["fps=12", "scale='min(480,iw)':-1:flags=lanczos",
           "split[s0][s1];[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5"],
       scalable=True),
    _p("mkv-remux", "video", "MKV (salin, tanpa encode)", "Ganti wadah saja, sangat cepat, kualitas tetap",
       "mkv", ("video",), "-c copy", ("0:v:0", "0:a?", "0:s?")),
    _p("mp4-remux", "video", "MP4 (salin, tanpa encode)", "Ganti wadah saja; gagal jika codec tak cocok dengan MP4",
       "mp4", ("video",), "-c copy -movflags +faststart", _V),

    # ---- Audio (juga bisa dari video = ekstrak audio) ----
    _p("mp3", "audio", "MP3", "VBR ~190 kbps, kompatibel di mana saja",
       "mp3", ("audio", "video"), "-c:a libmp3lame -q:a 2", ("0:a:0",), ("libmp3lame",)),
    _p("m4a", "audio", "M4A · AAC", "AAC 192 kbps",
       "m4a", ("audio", "video"), "-c:a aac -b:a 192k", ("0:a:0",), ("aac",)),
    _p("opus", "audio", "Opus", "Efisien, 128 kbps",
       "opus", ("audio", "video"), "-c:a libopus -b:a 128k", ("0:a:0",), ("libopus",)),
    _p("ogg", "audio", "OGG · Vorbis", "Kualitas q5",
       "ogg", ("audio", "video"), "-c:a libvorbis -q:a 5", ("0:a:0",), ("libvorbis",)),
    _p("flac", "audio", "FLAC", "Lossless, terkompresi",
       "flac", ("audio", "video"), "-c:a flac", ("0:a:0",), ("flac",)),
    _p("wav", "audio", "WAV", "PCM 16-bit, tanpa kompresi",
       "wav", ("audio", "video"), "-c:a pcm_s16le", ("0:a:0",), ("pcm_s16le",)),

    # ---- Gambar ----
    _p("webp", "image", "WebP", "Kualitas 85, kecil",
       "webp", ("image",), "-c:v libwebp -quality 85", ("0:v:0",), ("libwebp",), scalable=True),
    _p("jpg", "image", "JPG", "Kualitas tinggi (q:v 2)",
       "jpg", ("image",), "-c:v mjpeg -q:v 2", ("0:v:0",), ("mjpeg",), scalable=True),
    _p("png", "image", "PNG", "Lossless, mendukung transparansi",
       "png", ("image",), "-c:v png", ("0:v:0",), ("png",), scalable=True),
]

BY_ID = {p["id"]: p for p in PRESETS}


def available(encoders):
    """Preset yang encodernya semua ada di build ffmpeg ini."""
    return [p for p in PRESETS if set(p["needs"]) <= encoders]


def public(p):
    """Bagian preset yang dikirim ke UI."""
    return {k: p[k] for k in ("id", "group", "label", "desc", "ext", "kinds", "scalable")}
