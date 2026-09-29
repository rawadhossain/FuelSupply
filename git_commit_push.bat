@echo off
cd /d "%~dp0"
set LOG=.git_push_log.txt
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD') do set BR=%%b
echo Branch: %BR% > %LOG%
if /i "%BR%"=="main" goto protected
if /i "%BR%"=="master" goto protected
if /i "%BR%"=="HEAD" goto protected
git add -A >> %LOG% 2>&1
git status --short >> %LOG% 2>&1
git commit -F commit_msg.txt >> %LOG% 2>&1
git push -u origin %BR% >> %LOG% 2>&1
echo PUSH_EXIT=%ERRORLEVEL% >> %LOG%
git log --oneline -3 >> %LOG% 2>&1
goto end
:protected
echo REFUSED: on protected branch %BR%, nothing committed >> %LOG%
:end
echo FINISHED >> %LOG%
type %LOG%
pause
