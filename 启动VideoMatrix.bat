@echo off
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" run_dev.py
if errorlevel 1 pause
