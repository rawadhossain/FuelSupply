@echo off
cd /d "%~dp0"
set LOG=intelligence\artifacts\benchmark_log.txt
docker compose up -d > %LOG% 2>&1
for /L %%i in (1,1,60) do (
  curl -s http://localhost:8000/v1/health >nul 2>&1 && goto ready
  timeout /t 2 >nul
)
echo SIMULATOR DID NOT START >> %LOG%
goto end
:ready
set PY=python
where python >nul 2>&1 || set PY=py
%PY% -m pip install -q -r intelligence\requirements.txt >> %LOG% 2>&1
%PY% -m intelligence.benchmark --ticks 288 >> %LOG% 2>&1
%PY% -m intelligence.benchmark --ticks 288 --crisis >> %LOG% 2>&1
:end
echo FINISHED >> %LOG%
type %LOG%
pause
