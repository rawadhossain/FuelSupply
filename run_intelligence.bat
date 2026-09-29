@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY=python
where python >nul 2>&1 || set PY=py
%PY% -m pip install -q -r intelligence\requirements.txt
echo Intelligence service on http://localhost:8100  (docs: http://localhost:8100/docs)
%PY% -m uvicorn intelligence.service:app --host 0.0.0.0 --port 8100
pause
