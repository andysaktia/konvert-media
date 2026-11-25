"""Penamaan hasil: template, pembersihan nama, dan penomoran bila nama sudah dipakai."""
import re

RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}
VARS = ("name", "res", "date", "fmt")


def clean_seg(s):
    """Satu segmen path yang aman di Windows & Linux."""
    s = re.sub(r'[<>:"|?*\x00-\x1f]', "_", s).strip(" .")[:120].rstrip(" .")
    return "_" + s if s.split(".")[0].upper() in RESERVED else s


def web_safe(s):
    """Nama yang aman dipakai di URL playlist (tanpa spasi/%/aksen)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("._") or "file"


def render(template, values):
    """Template -> daftar segmen path relatif. ValueError dengan pesan yang bisa dibaca pengguna."""
    def sub(m):
        if m.group(1) not in VARS:
            raise ValueError("Variabel {%s} tidak dikenal. Yang tersedia: %s"
                             % (m.group(1), ", ".join("{%s}" % v for v in VARS)))
        return values.get(m.group(1), "")

    text = re.sub(r"\{(\w*)\}", sub, template.strip())
    if "{" in text or "}" in text:
        raise ValueError("Tanda kurung kurawal pada nama hasil tidak lengkap")
    segs = []
    for raw in re.split(r"[\\/]+", text):
        if raw in ("", "."):
            continue
        if raw == "..":
            raise ValueError("Folder induk (..) tidak boleh dipakai pada nama hasil")
        if clean_seg(raw):
            segs.append(clean_seg(raw))
    if segs:  # {res} kosong meninggalkan "nama_" -> rapikan ujungnya
        segs[-1] = segs[-1].rstrip("_- .")
        if not segs[-1]:
            segs.pop()
    if not segs:
        raise ValueError("Nama hasil kosong")
    return segs


def unique(p, is_dir=False):
    """`a.mp4` -> `a (2).mp4`; folder `a` -> `a (2)`; lewati yang sudah ada."""
    if not p.exists():
        return p
    stem, suf = (p.name, "") if is_dir else (p.stem, p.suffix)
    n = 2
    while (q := p.with_name(f"{stem} ({n}){suf}")).exists():
        n += 1
    return q