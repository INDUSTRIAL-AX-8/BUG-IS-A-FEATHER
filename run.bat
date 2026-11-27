@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 불량 원인 추적 - 서버
rem 사용법: run.bat          (포트 8000)
rem        run.bat 8080     (다른 포트)

if exist "backend\.venv\Scripts\python.exe" goto :installed
echo [오류] 아직 설치되지 않았습니다. 먼저 set_up.bat 을 실행하세요.
pause
exit /b 1
:installed
if not exist "frontend\dist\index.html" echo [경고] 화면 빌드 결과 frontend\dist 가 없습니다. set_up.bat 을 다시 실행하세요.

set "PYTHONUTF8=1"
set "PORT=%~1"
if "%PORT%"=="" set "PORT=8000"
set "BIF_SERVER_PORT=%PORT%"

echo ============================================================
echo   불량 원인 추적 서버: http://localhost:%PORT%
echo   테스트 계정: admin / admin1234 [관리자]
echo               operator / operator1234 [실무자]
echo               viewer / viewer1234 [조회자]
echo   끄려면 이 창에서 Ctrl+C
echo ============================================================

rem 서버가 뜰 시간을 두고 기본 브라우저를 연다
start "" /min cmd /c "timeout /t 4 /nobreak >nul & start http://localhost:%PORT%/"

cd backend
".venv\Scripts\python.exe" -m app.cli serve
pause
