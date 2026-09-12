@echo off
REM ============================================================
REM  start.bat — boot the Medical Intelligence dev server
REM  Prepends FFmpeg to PATH (required by openai-whisper for
REM  audio decoding) and starts uvicorn on port 8000.
REM  Usage: double-click start.bat, or run from a terminal.
REM ============================================================

REM FFmpeg is installed under the user's WinGet packages. Add it to
REM PATH at the front so it wins over any system ffmpeg.
set "FFMPEG_BIN=%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin"
if exist "%FFMPEG_BIN%\ffmpeg.exe" (
    set "PATH=%FFMPEG_BIN%;%PATH%"
    echo Using FFmpeg at: %FFMPEG_BIN%
) else (
    echo WARNING: FFmpeg not found at %FFMPEG_BIN%.
    echo Transcription will fail until FFmpeg is installed.
)

REM Switch to the project directory (script location) so uvicorn can
REM find main.py regardless of where the user invoked start.bat from.
cd /d "%~dp0"

REM Allow the user to override host/port via env if needed.
set "HOST=127.0.0.1"
set "PORT=8000"

echo Starting FastAPI on http://%HOST%:%PORT% ...
python -m uvicorn main:app --host %HOST% --port %PORT%