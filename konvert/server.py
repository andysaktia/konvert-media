"""Server HTTP lokal (stdlib saja, tanpa dependensi). Hanya mendengarkan 127.0.0.1."""
import argparse
import json
import mimetypes
import queue
import re
import shutil
import sys
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from . import __version__
from . import ffmpeg as ff
from .jobs import Jobs
from .presets import HEIGHTS, available, public

STATIC = Path(__file__).parent / "static"
STATIC_FILES = {"index.html", "app.js", "style.css"}
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}
ROUTES = []


def safe_name(raw):
    """Nama file aman untuk Windows & Linux; ekstensi dipertahankan."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(raw.replace("\\", "/")).name).strip(" .")
    p = Path(name)
    stem, ext = p.stem[:100] or "file", p.suffix[:12]
    if stem.upper() in RESERVED:
        stem = "_" + stem
    return stem + ext


def route(method, pattern):
    rx = re.compile(pattern)

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


class Handler(BaseHTTPRequestHandler):
    server_version = "KonvertStudio/" + __version__

    def log_message(self, *args):  # sunyi
        pass

    # ---- util ----
    def json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def err(self, code, msg):
        self.json({"error": msg}, code)

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1 << 20:
            raise ValueError("Body terlalu besar")
        return json.loads(self.rfile.read(n) or b"{}")

    def local_only(self):
        """Tolak Host/Origin non-lokal (cegah DNS rebinding & situs lain yang menembak localhost)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        origin = self.headers.get("Origin")
        if host in LOCAL_HOSTS and (not origin or urlparse(origin).hostname in LOCAL_HOSTS):
            return True
        self.err(403, "Ditolak")
        return False

    def dispatch(self, method):
        if not self.local_only():
            return
        path = urlparse(self.path).path
        for m, rx, fn in ROUTES:
            g = rx.fullmatch(path) if m == method else None
            if g:
                try:
                    return fn(self, *g.groups())
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception as e:  # noqa: BLE001
                    return self.err(500, f"{type(e).__name__}: {e}")
        if method == "GET":
            return self.static(path)
        self.err(404, "Tidak ditemukan")

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def static(self, path):
        name = "index.html" if path == "/" else path.lstrip("/")
        if name not in STATIC_FILES or not (STATIC / name).is_file():
            return self.err(404, "Tidak ditemukan")
        data = (STATIC / name).read_bytes()
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or "javascript" in ctype else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


# ---------------- API ----------------
@route("GET", r"/api/info")
def info(h):
    h.json({"version": __version__, "ffmpeg": ff.version(), "heights": HEIGHTS,
            "presets": [public(p) for p in available(ff.encoders())]})


@route("POST", r"/api/upload")
def upload(h):
    """Body mentah = isi file; nama asli di header X-Filename (URL-encoded). Tanpa parsing multipart."""
    n = int(h.headers.get("Content-Length") or -1)
    if n < 0:
        return h.err(411, "Content-Length wajib ada")
    name = safe_name(unquote(h.headers.get("X-Filename") or "file"))
    fid = uuid.uuid4().hex
    d = h.server.jobs.work / "in" / fid
    d.mkdir(parents=True)
    path, left = d / name, n
    with open(path, "wb") as f:
        while left:
            chunk = h.rfile.read(min(1 << 20, left))
            if not chunk:
                break
            f.write(chunk)
            left -= len(chunk)
    if left:
        shutil.rmtree(d, ignore_errors=True)
        return h.err(400, "Upload terputus")
    try:
        meta = ff.probe(path)
    except ValueError as e:
        shutil.rmtree(d, ignore_errors=True)
        return h.err(415, f"{name}: {e}")
    h.json(h.server.jobs.add_file(fid, str(path), name, n, meta))


@route("DELETE", r"/api/files/([0-9a-f]{32})")
def delete_file(h, fid):
    try:
        h.server.jobs.remove_file(fid)
    except KeyError:
        return h.err(404, "File tidak ditemukan")
    except RuntimeError as e:
        return h.err(409, str(e))
    h.json({"ok": True})


@route("GET", r"/api/jobs")
def list_jobs(h):
    h.json([j.as_dict() for j in h.server.jobs.jobs.values()])


@route("POST", r"/api/jobs")
def create_jobs(h):
    body = h.body_json()
    made, errors = [], []
    for fid in body.get("files", []):
        try:
            made.append(h.server.jobs.submit(fid, body.get("preset"), body.get("max_height")).as_dict())
        except ValueError as e:
            errors.append({"file_id": fid, "error": str(e)})
    h.json({"jobs": made, "errors": errors})


@route("POST", r"/api/jobs/([0-9a-f]{32})/cancel")
def cancel_job(h, jid):
    try:
        h.json(h.server.jobs.cancel(jid).as_dict())
    except KeyError:
        h.err(404, "Job tidak ditemukan")


@route("DELETE", r"/api/jobs/([0-9a-f]{32})")
def delete_job(h, jid):
    try:
        h.server.jobs.remove(jid)
    except KeyError:
        return h.err(404, "Job tidak ditemukan")
    except RuntimeError as e:
        return h.err(409, str(e))
    h.json({"ok": True})


@route("GET", r"/api/jobs/([0-9a-f]{32})/download")
def download(h, jid):
    job = h.server.jobs.jobs.get(jid)
    if not job or job.status != "done":
        return h.err(404, "Hasil belum tersedia")
    size = job.out_path.stat().st_size
    ascii_name = job.out_path.name.encode("ascii", "replace").decode()
    h.send_response(200)
    h.send_header("Content-Type", "application/octet-stream")
    h.send_header("Content-Length", str(size))
    h.send_header("Content-Disposition",
                  f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(job.out_path.name)}")
    h.end_headers()
    with open(job.out_path, "rb") as f:
        shutil.copyfileobj(f, h.wfile, 1 << 20)


@route("GET", r"/api/events")
def events(h):
    """Server-Sent Events: status semua job saat tersambung, lalu update real-time."""
    jobs = h.server.jobs
    q = jobs.subscribe()
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    try:
        for j in list(jobs.jobs.values()):
            h.wfile.write(f"data: {json.dumps({'type': 'job', 'job': j.as_dict()})}\n\n".encode())
        h.wfile.flush()
        while True:
            try:
                msg = q.get(timeout=15)
                h.wfile.write(f"data: {json.dumps(msg)}\n\n".encode())
            except queue.Empty:
                h.wfile.write(b": ping\n\n")
            h.wfile.flush()
    except OSError:  # klien menutup tab
        pass
    finally:
        jobs.unsubscribe(q)


# ---------------- start ----------------
def make_server(port, work, workers=2):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)  # daemon_threads=True bawaan
    srv.jobs = Jobs(work, workers)
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(prog="konvert", description="Konvert Studio: konverter media lokal berbasis ffmpeg")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--jobs", type=int, default=2, help="jumlah konversi paralel (default 2)")
    ap.add_argument("--data", default="data", help="folder data; file sementara di <data>/work dan dihapus tiap start")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    try:
        ff.require()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    try:
        srv = make_server(a.port, Path(a.data) / "work", a.jobs)
    except OSError as e:
        print(f"Port {a.port} tidak bisa dipakai ({e}). Coba: python -m konvert --port 8800", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{a.port}"
    print(f"Konvert Studio {__version__} · {ff.version()}\nBuka {url}  (Ctrl+C untuk berhenti)")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nBerhenti.")
    finally:
        srv.jobs.close()
        srv.server_close()
    return 0
