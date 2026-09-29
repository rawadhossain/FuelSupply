@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
chcp 65001 >nul
set LOG=intelligence\artifacts\genai_log.txt
set PY=python
where python >nul 2>&1 || set PY=py
echo === update .env (names only, no secrets) === > %LOG%
%PY% tools\update_env.py >> %LOG% 2>&1
echo === install === >> %LOG%
%PY% -m pip install -q -r intelligence\requirements.txt >> %LOG% 2>&1
echo === tests === >> %LOG%
%PY% -m pytest intelligence\tests -q >> %LOG% 2>&1
echo === live OpenAI demo === >> %LOG%
%PY% -m intelligence.genai_demo >> %LOG% 2>&1
echo FINISHED >> %LOG%
type %LOG%
pause
