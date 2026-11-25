"""Antrean konversi: upload, job, progress, pembatalan, penerbitan hasil ke folder, dan event ke UI (SSE).

ffmpeg selalu menulis ke folder sementara (work/stage/<job>). Hanya setelah sukses hasilnya
dipindah ke folder hasil, jadi folder hasil tidak pernah berisi file setengah jadi.
"""
import queue
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import ffmpeg as ff
from . import paths
from .presets import BY_ID, accepts, available, resolve, summary

# Kunci keluaran `-progress`; baris lain dari ffmpeg (loglevel error) dianggap pesan galat.
PROGRESS_KEYS = {"frame", "fps", "bitrate", "total_size", "out_time_us", "out_time_ms",
                 "out_time", "dup_frames", "drop_frames", "speed", "progress"}
ACTIVE = ("queued", "running")


def plan_output(fmt, params, name):
    """Template nama hasil -> (segmen path relatif, nama file yang dipakai untuk {name})."""
    stem = Path(name).stem
    shown = paths.web_safe(stem) if fmt.get("websafe") else stem
    res = f"{params['height']}p" if params.get("height") else ""
    segs = paths.render(params["out"], {"name": shown, "res": res, "date": time.strftime("%Y%m%d"),
                                        "fmt": fmt["id"]})
    if fmt["kind"] == "file":
        ext = "." + fmt["ext"]
        if segs[-1].lower().endswith(ext) and len(segs[-1]) > len(ext):
            segs[-1] = segs[-1][:-len(ext)]
        segs[-1] += ext
    return segs, shown


class Job:
    def __init__(self, file, fmt, params, segs, stem):
        self.id = uuid.uuid4().hex
        self.file, self.fmt, self.params, self.segs, self.stem = file, fmt, params, segs, stem
        self.status, self.percent, self.speed, self.error = "queued", 0.0, "", ""
        self.rel = "/".join(segs)  # jadi path final (bisa bernomor) setelah selesai
        self.dest, self.size, self.nfiles = None, 0, 0
        self.cancelled, self.proc = False, None

    def as_dict(self):
        return {"id": self.id, "file_id": self.file["id"], "name": self.file["name"],
                "format": self.fmt["id"], "label": self.fmt["label"], "kind": self.fmt["kind"],
                "summary": summary(self.fmt, self.params), "status": self.status,
                "percent": round(self.percent, 1), "speed": self.speed, "error": self.error,
                "rel": self.rel, "size": self.size, "files": self.nfiles,
                "out_name": self.dest.name if self.dest else ""}


class Jobs:
    def __init__(self, work, out_dir, workers=2):
        self.work, self.out_dir = Path(work), Path(out_dir).resolve()
        w = self.work.resolve()
        if w == self.out_dir or w in self.out_dir.parents:
            raise ValueError("Folder hasil tidak boleh berada di dalam folder kerja (isinya dihapus tiap start)")
        shutil.rmtree(self.work, ignore_errors=True)  # hanya subfolder kerja milik kita
        (self.work / "in").mkdir(parents=True)
        (self.work / "stage").mkdir()
        self.pool = ThreadPoolExecutor(max(1, workers), thread_name_prefix="konvert")
        self.files, self.jobs = {}, {}
        self._subs, self._lock, self._pub = [], threading.Lock(), threading.Lock()

    # ---- event (SSE) ----
    def subscribe(self):
        q = queue.Queue()
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def _emit(self, msg):
        with self._lock:
            for q in self._subs:
                q.put(msg)

    def _emit_job(self, job):
        self._emit({"type": "job", "job": job.as_dict()})

    # ---- file ----
    def add_file(self, fid, path, name, size, info):
        f = {"id": fid, "path": path, "name": name, "size": size, **info}
        self.files[fid] = f
        return {k: v for k, v in f.items() if k != "path"}

    def remove_file(self, fid):
        f = self.files.get(fid)
        if not f:
            raise KeyError(fid)
        if any(j.file["id"] == fid and j.status in ACTIVE for j in self.jobs.values()):
            raise RuntimeError("File sedang dipakai konversi yang berjalan")
        shutil.rmtree(Path(f["path"]).parent, ignore_errors=True)
        del self.files[fid]

    # ---- job ----
    def format_for(self, fmt_id):
        fmt = BY_ID.get(fmt_id)
        if not fmt or fmt not in available(ff.encoders()):
            raise ValueError("Format tidak tersedia di ffmpeg ini")
        return fmt

    def submit(self, fid, fmt_id, raw_params):
        f = self.files.get(fid)
        if not f:
            raise ValueError("File tidak ditemukan")
        fmt = self.format_for(fmt_id)
        if not accepts(fmt, f):
            why = "tidak punya audio" if f["kind"] in fmt["kinds"] else f"tidak cocok untuk {fmt['label']}"
            raise ValueError(f"{f['name']} {why}")
        params = resolve(fmt, raw_params)
        segs, stem = plan_output(fmt, params, f["name"])
        job = Job(f, fmt, params, segs, stem)
        self.jobs[job.id] = job
        self._emit_job(job)
        self.pool.submit(self._run, job)
        return job

    def cancel(self, jid):
        job = self.jobs[jid]
        if job.status not in ACTIVE:
            return job
        job.cancelled = True
        if job.proc and job.proc.poll() is None:
            job.proc.terminate()
        elif job.status == "queued":
            job.status = "cancelled"
            self._emit_job(job)
        return job

    def remove(self, jid):
        """Hanya menghapus dari daftar; file hasil di folder hasil tidak disentuh."""
        if self.jobs[jid].status in ACTIVE:
            raise RuntimeError("Batalkan dulu job yang sedang berjalan")
        del self.jobs[jid]
        self._emit({"type": "removed", "id": jid})

    def close(self):
        for j in list(self.jobs.values()):
            self.cancel(j.id)
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _run(self, job):
        if job.cancelled:
            return
        f, fmt = job.file, job.fmt
        stage = self.work / "stage" / job.id
        job.status = "running"
        self._emit_job(job)
        duration, tail, last = f["duration"], deque(maxlen=12), -1
        try:
            ctx = {**f, "stem": job.stem}
            target = stage / job.segs[-1]
            if fmt["kind"] == "dir":
                for d in [".", *fmt["dirs"](job.params, ctx)]:
                    (target / d).mkdir(parents=True, exist_ok=True)
            else:
                stage.mkdir(parents=True)
            cmd = ff.build_command(f["path"], fmt, job.params, ctx, target)
            job.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                        errors="replace", creationflags=ff.NOWIN)
            if job.cancelled:  # pembatalan masuk sebelum proc sempat dibuat
                job.proc.terminate()
            for line in job.proc.stdout:
                line = line.strip()
                key, _, val = line.partition("=")
                if key == "out_time_us" and val.isdigit() and duration > 0:
                    job.percent = min(99.0, int(val) / 1e6 / duration * 100)
                elif key == "speed":
                    job.speed = val
                elif key not in PROGRESS_KEYS and line:
                    tail.append(line)
                if int(job.percent) != last:
                    last = int(job.percent)
                    self._emit_job(job)
            rc = job.proc.wait()
            if job.cancelled:
                job.status = "cancelled"
            elif rc == 0:
                self._publish(job, target, ctx)
            else:
                job.status, job.error = "error", "\n".join(tail) or f"ffmpeg berhenti dengan kode {rc}"
        except Exception as e:  # noqa: BLE001 - laporkan apa pun ke UI
            job.status, job.error = "error", f"{type(e).__name__}: {e}"
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        job.speed = ""
        self._emit_job(job)

    def _publish(self, job, target, ctx):
        """Pindahkan hasil dari folder sementara ke folder hasil (di bawah kunci, agar nomor tidak bentrok)."""
        fmt = job.fmt
        is_dir = fmt["kind"] == "dir"
        main = target / fmt["main"](job.params, ctx) if is_dir else target
        if not main.is_file():
            raise RuntimeError("ffmpeg selesai tetapi tidak menghasilkan file")
        with self._pub:
            dest = self.out_dir.joinpath(*job.segs)
            dest.resolve().relative_to(self.out_dir)  # ValueError bila keluar dari folder hasil
            if dest.exists():
                if job.params["exists"] == "replace":
                    shutil.rmtree(dest) if is_dir else dest.unlink()
                else:
                    dest = paths.unique(dest, is_dir)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(dest))
        job.dest, job.rel = dest, dest.relative_to(self.out_dir).as_posix()
        files = [p for p in dest.rglob("*") if p.is_file()] if is_dir else [dest]
        job.nfiles, job.size = len(files), sum(p.stat().st_size for p in files)
        job.status, job.percent = "done", 100.0