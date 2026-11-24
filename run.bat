@echo off
rem Konvert Studio: klik dua kali untuk menjalankan. Argumen diteruskan, mis. run.bat --port 8800
cd /d "%~dp0"

where ffmpeg >nul 2>nul
if not errorlevel 1 goto run
if exist "ffmpeg\bin\ffmpeg.exe" goto run
echo [!] ffmpeg tidak ditemukan.
echo     Pasang dengan: winget install Gyan.FFmpeg   lalu buka terminal baru
echo     atau taruh ffmpeg.exe dan ffprobe.exe di folder ffmpeg\bin
pause
exit /b 1

:run
where py >nul 2>nul
if not errorlevel 1 (py -3 -m konvert %*) else (python -m konvert %*)
pause
