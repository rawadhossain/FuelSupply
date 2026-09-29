@echo off
cd /d "%~dp0"
set PY=python
where python >nul 2>&1 || set PY=py
%PY% -m pip install -q -r intelligence\requirements.txt locust psutil
%PY% loadtest\run_loadtest.py
type loadtest\results\summary.md
pause
