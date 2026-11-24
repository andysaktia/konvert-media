# Konvert Studio

Konverter media lokal berbasis **ffmpeg**, mirip CloudConvert tapi berjalan di komputermu sendiri.
Video, audio, dan gambar. File tidak pernah keluar dari mesinmu.

Tanpa dependensi Python: hanya **Python 3.9+** dan **ffmpeg**.

## Menjalankan di Windows

1. Pasang Python 3.9+ dari python.org (centang *Add python.exe to PATH*).
2. Pasang ffmpeg: buka terminal, jalankan `winget install Gyan.FFmpeg`, lalu buka terminal baru.
   Alternatif: taruh `ffmpeg.exe` dan `ffprobe.exe` di folder `ffmpeg\bin` di dalam proyek ini.
3. Klik dua kali **`run.bat`**. Browser terbuka di `http://127.0.0.1:8765`.

Atau dari terminal: `python -m konvert`

| Opsi | Fungsi |
|---|---|
| `--port 8800` | Ganti port (default 8765) |
| `--jobs 4` | Jumlah konversi paralel (default 2) |
| `--data D:\konvert` | Lokasi folder kerja (default `.\data`) |
| `--no-browser` | Jangan buka browser otomatis |

Variabel lingkungan `KONVERT_FFMPEG` / `KONVERT_FFPROBE` bisa menunjuk ke binary tertentu.

## Cara pakai

Seret file ke halaman (boleh banyak sekaligus), pilih format di panel kanan, klik **Konversi**.
Progress tampil sebagai pita film. Selesai, klik **Unduh**.
Satu set file bisa dikonversi ke beberapa format berturut-turut.

Format yang muncul mengikuti build ffmpeg-mu. Preset yang encodernya tidak ada otomatis disembunyikan.

Tahap 1 mencakup: MP4 (H.264/H.265), WebM (VP9), preset YouTube dan WhatsApp, GIF, remux MKV/MP4,
audio (MP3, M4A, Opus, OGG, FLAC, WAV; juga ekstrak audio dari video), gambar (WebP, JPG, PNG),
batas tinggi video, antrean paralel, dan pembatalan.

## Catatan

- File yang diunggah dan hasilnya disimpan sementara di `<data>\work` dan **dihapus tiap kali aplikasi dijalankan**.
  Unduh hasilnya sebelum menutup aplikasi.
- Server hanya mendengarkan `127.0.0.1` dan menolak permintaan dengan Host/Origin non-lokal.
- Unggahan disalin ke folder kerja, jadi file besar memakai ruang disk dua kali sementara.
- Bila konversi gagal, buka "Pesan dari ffmpeg" pada barisnya untuk melihat penyebabnya.

## Menambah format

Semua preset ada di `konvert/presets.py`, satu baris `_p(...)` per format.

## Tes

```
python -m pip install pytest
python -m pytest
```

Tes menjalankan ffmpeg sungguhan untuk setiap preset, pembatalan, progress, dan keamanan dasar.

## Struktur

```
konvert/
  ffmpeg.py    lokasi binary, probe, daftar encoder, penyusun perintah
  presets.py   katalog format
  jobs.py      antrean, progress, pembatalan, event real-time
  server.py    server HTTP + API (stdlib)
  static/      index.html, app.js, style.css
tests/
run.bat
```
