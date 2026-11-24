"""Antrean konversi: file upload, job, progress, pembatalan, dan siaran event ke UI (SSE)."""
import queue
import shutil
import subprocess
import threading
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import ffmpeg as ff
from .presets import BY_ID, HEIGHTS, available

# Kunci keluaran `-progress`; baris lain dari ffmpeg (loglevel error) dianggap pesan galat.
PROGRESS_KEYS = {"frame", "fps", "bitrate", "total_size", "out_time_us", "out_time_ms",
                 "out_time", "dup_frames", "drop_frames", "speed", "progress"}
ACTIVE = ("queued", "running")


class Job:
    def __init__(self, file, preset, max_height):
        self.id = uuid.uuid4().hex
        self.file, self.preset, self.max_height = file, preset, max_height
        self.status, self.percent, self.speed, self.error = "queued", 0.0, "", ""
        self.out_path, self.out_size = None, 0
        self.cancelled, self.proc = False, None

    def as_dict(self):
        return {"id": self.id, "file_id": self.file["id"], "name": self.file["name"],
                "preset": self.preset["id"], "label": self.preset["label"],
                "ext": self.preset["ext"], "max_height": self.max_height,
                "status": self.status, "percent": round(self.percent, 1),
                "speed": self.speed, "error": self.error,
                "out_name": self.out_path.name if self.out_path else "",
                "out_size": self.out_size}


class Jobs:
    def __init__(self, work, workers=2):
        self.work = Path(work)
        shutil.rmtree(self.work, ignore_errors=True)  # hanya subfolder kerja milik kita
        (self.work / "in").mkdir(parents=True)
        (self.work / "out").mkdir()
        self.pool = ThreadPoolExecutor(max(1, workers), thread_name_prefix="konvert")
        self.files, self.jobs = {}, {}
        self._subs, self._lock = [], threading.Lock()

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
    def submit(self, fid, preset_id, max_height=None):
        f = self.files.get(fid)
        if not f:
            raise ValueError("File tidak ditemukan")
        preset = BY_ID.get(preset_id)
        if not preset or preset not in available(ff.encoders()):
            raise ValueError("Preset tidak tersedia di ffmpeg ini")
        if f["kind"] not in preset["kinds"]:
            raise ValueError(f"{f['kind']} tidak cocok untuk preset {preset['label']}")
        if max_height not in (None, *HEIGHTS):
            raise ValueError("Tinggi maksimum tidak valid")
        job = Job(f, preset, max_height)
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
        job = self.jobs[jid]
        if job.status in ACTIVE:
            raise RuntimeError("Batalkan dulu job yang sedang berjalan")
        shutil.rmtree(self.work / "out" / job.id, ignore_errors=True)
        del self.jobs[jid]
        self._emit({"type": "removed", "id": jid})

    def close(self):
        for j in list(self.jobs.values()):
            self.cancel(j.id)
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _run(self, job):
        if job.cancelled:
            return
        out_dir = self.work / "out" / job.id
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(job.file["name"]).stem
        job.out_path = out_dir / f"{stem}.{job.preset['ext']}"
        job.status = "running"
        self._emit_job(job)

        duration = job.file["duration"]
        tail, last = deque(maxlen=12), -1
        try:
            cmd = ff.build_command(job.file["path"], job.out_path, job.preset, job.max_height)
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
            elif rc == 0 and job.out_path.is_file():
                job.status, job.percent = "done", 100.0
                job.out_size = job.out_path.stat().st_size
            else:
                job.status = "error"
                job.error = "\n".join(tail) or f"ffmpeg berhenti dengan kode {rc}"
        except Exception as e:  # noqa: BLE001 - laporkan apa pun ke UI
            job.status, job.error = "error", f"{type(e).__name__}: {e}"
        if job.status != "done":
            shutil.rmtree(out_dir, ignore_errors=True)
            job.out_path = None
        job.speed = ""
        self._emit_job(job)
