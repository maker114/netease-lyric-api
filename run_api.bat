@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m netease_lyric serve --port 23517
pause
