@echo off
cd /d "%~dp0"
set LOG=dataset\run_log.txt
echo === %DATE% %TIME% === > %LOG%
where docker >> %LOG% 2>&1 || (echo DOCKER NOT FOUND >> %LOG% & goto end)
docker compose up -d >> %LOG% 2>&1
echo Waiting for simulator... >> %LOG%
for /L %%i in (1,1,60) do (
  curl -s http://localhost:8000/v1/health >nul 2>&1 && goto ready
  timeout /t 2 >nul
)
echo SIMULATOR DID NOT START >> %LOG%
goto end
:ready
set PY=python
where python >nul 2>&1 || set PY=py
%PY% dataset\export_dataset.py --ticks 2000 --reset >> %LOG% 2>&1
:end
echo FINISHED >> %LOG%
type %LOG%
pause
