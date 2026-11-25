# Konvert Studio

Konverter media lokal berbasis **ffmpeg**, mirip CloudConvert tapi berjalan di komputermu sendiri.
Video, audio, gambar, dan streaming HLS. File tidak pernah keluar dari mesinmu.

Tanpa dependensi Python: hanya **Python 3.9+** dan **ffmpeg**.

## Menjalankan di Windows

1. Pasang Python 3.9+ dari python.org (centang *Add python.exe to PATH*).
2. Pasang ffmpeg: buka terminal, jalankan `winget install Gyan.FFmpeg`, lalu buka terminal baru.
   Alternatif: taruh `ffmpeg.exe` dan `ffprobe.exe` di folder `ffmpeg\bin` di dalam proyek ini.
3. Klik dua kali **`run.bat`**. Browser terbuka di `http://127.0.0.1:8765`.

Atau dari terminal: `python -m konvert`

| Opsi | Fungsi |
|---|---|
| `--out D:\hasil` | Folder tujuan hasil (default `.\hasil`) |
| `--port 8800` | Ganti port (default 8765) |
| `--jobs 4` | Jumlah konversi paralel (default 2) |
| `--profiles D:\tim\profiles.json` | File preset (default `profiles.json` di folder proyek) |
| `--data D:\konvert` | Folder kerja sementara (default `.\data`) |
| `--no-browser` | Jangan buka browser otomatis |

Variabel lingkungan `KONVERT_FFMPEG` / `KONVERT_FFPROBE` bisa menunjuk ke binary tertentu.

## Cara pakai

1. Seret file ke halaman (boleh banyak sekaligus).
2. Pilih **preset cepat**, atau pilih jenis (Video, Audio, Gambar, Streaming) lalu format, lalu atur opsinya.
3. Klik **Konversi**. Hasil langsung disimpan di folder hasil. **Unduh** dan **Buka folder** ada di barisnya.

Bagian **Perintah ffmpeg** menampilkan perintah persis yang akan dijalankan, siap disalin.
Bagian **Lanjutan** berisi nama/folder hasil, penanganan file yang sudah ada, dan argumen ffmpeg tambahan.

### Nama dan folder hasil

Isi dengan template. Variabel: `{name}` nama file, `{res}` resolusi (mis. `480p`, kosong bila ukuran asli),
`{date}` tanggal (`20261125`), `{fmt}` id format. Boleh memakai folder, mis. `kursus/{date}/{name}_{res}`.
Bila nama sudah dipakai, hasil diberi nomor (`video (2).mp4`), atau ditimpa bila dipilih "Timpa yang lama".
ffmpeg menulis ke folder sementara dulu, jadi folder hasil tidak pernah berisi file setengah jadi.

### Preset cepat dan preset tim

Preset disimpan di `profiles.json`. Klik **Simpan pengaturan ini** untuk menambah, tanda × untuk menghapus.
File ini bisa dibagikan ke tim (taruh di git, atau arahkan dengan `--profiles` ke folder bersama).
Bawaannya: MP4 480p, MP4 360p, HLS m3u8, WhatsApp, YouTube, MP3 192.

## Format

| Jenis | Format | Opsi utama |
|---|---|---|
| Video | MP4 (H.264), MP4 (H.265), WebM (VP9) | resolusi, kualitas, bitrate audio; lanjutan: kecepatan encode, perbesar |
| Video | GIF, MKV (salin), MP4 (salin) | GIF: fps dan lebar. Salin: tanpa encode ulang |
| Audio | MP3, M4A, Opus, OGG, FLAC, WAV | bitrate/kualitas; bisa juga ekstrak audio dari video |
| Gambar | WebP, JPG, PNG | resolusi, kualitas |
| Streaming | HLS (salin) | panjang segmen |
| Streaming | HLS adaptif | resolusi yang dibuat (1080 sampai 240), segmen, kualitas, bitrate audio |

Format yang encodernya tidak ada di ffmpeg-mu otomatis disembunyikan.

### HLS

- **HLS (salin)** setara script tim: `-c copy -f hls -hls_time 6 -hls_list_size 0`. Hasil bawaan di
  `m3u8/ts/{name}/v0/{name}.m3u8` dengan segmen `{name}__00000.ts`. Sumber sebaiknya H.264 + AAC
  (aplikasi memberi peringatan bila bukan). Panjang segmen mengikuti keyframe sumber.
- **HLS adaptif** membuat beberapa resolusi sekaligus (`v0` = tertinggi) plus `master.m3u8`.
  Keyframe diselaraskan di semua resolusi, dan `BANDWIDTH` terisi, jadi pemutar bisa berpindah kualitas.
  Resolusi yang lebih besar dari sumber dilewati.
- `{name}` pada HLS otomatis dibuat aman untuk URL (spasi, `%`, dan aksen menjadi `_`).

## Argumen ffmpeg tambahan

Kolom opsional di Lanjutan, ditambahkan sebelum nama file hasil, mis. `-r 30 -af loudnorm`.
Hanya opsi keluaran yang diizinkan: `-i`, `-y`, `-attach`, filter `movie=`, dan nama file tambahan ditolak.
Tulis path dengan garis miring `/`.

## Catatan

- File yang diunggah disalin ke `<data>\work`, yang **dihapus tiap kali aplikasi dijalankan**.
  Hasil di folder hasil tidak ikut dihapus, begitu juga saat baris dihapus dari daftar.
- Server hanya mendengarkan `127.0.0.1` dan menolak permintaan dengan Host/Origin non-lokal.
- Unggahan disalin ke folder kerja, jadi file besar memakai ruang disk dua kali sementara.
- Bila konversi gagal, buka "Pesan dari ffmpeg" pada barisnya untuk melihat penyebabnya.

## Menambah format

Semua format ada di `konvert/presets.py`: satu entri `_f(...)` berisi daftar parameter dan fungsi
`build` kecil yang menyusun argumen ffmpeg. UI membangun kontrolnya otomatis dari daftar parameter itu.

## Tes

```
python -m pip install pytest
python -m pytest
```

Tes menjalankan ffmpeg sungguhan untuk setiap format, skenario script tim (MP4 480p/360p, HLS),
penomoran hasil, template folder, argumen tambahan, preset, pembatalan, progress, dan keamanan dasar.

## Struktur

```
konvert/
  ffmpeg.py    lokasi binary, probe, daftar encoder
  presets.py   katalog format + parameter + preset tersimpan
  paths.py     template nama hasil, pembersihan nama, penomoran
  jobs.py      antrean, progress, pembatalan, penerbitan hasil, event real-time
  server.py    server HTTP + API (stdlib)
  static/      index.html, app.js, style.css
profiles.json  preset cepat (bisa dibagikan ke tim)
tests/
run.bat
```