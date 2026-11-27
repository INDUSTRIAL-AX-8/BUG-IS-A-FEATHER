@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title 불량 원인 추적 - 설치
rem 사용법: set_up.bat          (대화형)
rem        set_up.bat 1~4      (데모 데이터 번호를 미리 지정, 창 멈춤 없음)

echo ============================================================
echo   불량 원인 추적 시스템 - 설치 [처음 한 번만]
echo ============================================================
echo.

rem ---------- 1. Python 확인 [3.11 이상]
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto :py_ok
python -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto :py_ok
echo [오류] Python 3.11 이상이 필요합니다. python.org 에서 설치 후 다시 실행하세요.
echo        설치 화면에서 "Add python.exe to PATH" 를 체크하세요.
goto :fail
:py_ok
echo [1/5] Python 확인: %PY%

rem ---------- 2. 가상환경 + 패키지
set "VPY=backend\.venv\Scripts\python.exe"
if exist "%VPY%" goto :venv_ok
echo [2/5] 가상환경 만드는 중...
%PY% -m venv backend\.venv
if errorlevel 1 goto :fail
:venv_ok
set "WHEELS="
if exist "offline_bundle\wheels" set "WHEELS=offline_bundle\wheels"
if exist "wheels" set "WHEELS=wheels"
if defined WHEELS goto :pip_offline
echo [2/5] 패키지 설치 중 - 인터넷 사용, 몇 분 걸릴 수 있습니다
"%VPY%" -m pip install -q -r backend\requirements.lock
if errorlevel 1 goto :fail
goto :pip_done
:pip_offline
echo [2/5] 패키지 설치 중 - 폐쇄망 묶음 %WHEELS% 사용
"%VPY%" -m pip install -q --no-index --find-links "%WHEELS%" -r backend\requirements.lock
if errorlevel 1 goto :fail
:pip_done

rem ---------- 3. 설정 파일
if exist "config\config.toml" goto :cfg_ok
copy /y "config\config.example.toml" "config\config.toml" >nul
echo [3/5] 설정 파일 생성: config\config.toml - 운영 전 secret_key 와 관리자 비밀번호를 바꾸세요
goto :cfg_done
:cfg_ok
echo [3/5] 설정 파일 있음: config\config.toml
:cfg_done

rem ---------- 4. 화면 빌드
if exist "frontend\dist\index.html" goto :web_ok
where npm >nul 2>nul
if errorlevel 1 goto :no_npm
echo [4/5] 화면 빌드 중...
pushd frontend
call npm ci --no-audit --no-fund
if errorlevel 1 goto :fail_pop
call npm run build
if errorlevel 1 goto :fail_pop
popd
goto :web_done
:no_npm
echo [4/5] [경고] Node.js 가 없어 화면을 빌드하지 못했습니다.
echo        인터넷 PC 에서 빌드한 frontend\dist 폴더를 복사해 넣거나 Node.js 를 설치한 뒤 다시 실행하세요.
goto :web_done
:web_ok
echo [4/5] 화면 빌드 결과 있음
:web_done

rem ---------- 5. DB 준비 + 데모 데이터
set "PYTHONUTF8=1"
pushd backend
".venv\Scripts\python.exe" -m app.cli init-db
if errorlevel 1 goto :fail_pop
popd

set "DEMO=%~1"
if not "%DEMO%"=="" goto :demo
echo.
echo [5/5] 데모 데이터를 고르세요. 기존 데이터는 지워집니다.
echo    1. 사출기 3호 공정 기록 [권장]
echo    2. 합성 시나리오 [원자재 LOT, 설비 이상을 심어 둔 데이터]
echo    3. KAMP 다이캐스팅 실데이터 [원본 CSV 폴더 경로 필요]
echo    4. 넣지 않음 [기존 데이터 유지]
choice /c 1234 /n /m "번호 입력: "
set "DEMO=%errorlevel%"
:demo
pushd backend
if "%DEMO%"=="1" goto :demo1
if "%DEMO%"=="2" goto :demo2
if "%DEMO%"=="3" goto :demo3
goto :demo_done
:demo1
".venv\Scripts\python.exe" -m app.cli seed-process-log --reset --file ..\samples\injection_m3\hourly_2026-03-04_06.csv --mapping ..\samples\injection_m3\mapping.json
if errorlevel 1 goto :fail_pop
goto :demo_done
:demo2
".venv\Scripts\python.exe" -m app.cli seed-sample --reset
if errorlevel 1 goto :fail_pop
goto :demo_done
:demo3
set "KAMP=%~2"
if "%KAMP%"=="" set /p "KAMP=KAMP data\raw 폴더 경로: "
".venv\Scripts\python.exe" -m app.cli seed-kamp --reset --raw-dir "%KAMP%"
if errorlevel 1 goto :fail_pop
:demo_done
popd

echo.
echo ============================================================
echo   설치 완료. run.bat 을 실행하면 서버가 켜지고 브라우저가 열립니다.
echo   테스트 계정: admin / admin1234 [관리자]
echo               operator / operator1234 [실무자]
echo               viewer / viewer1234 [조회자]
echo ============================================================
if "%~1"=="" pause
exit /b 0

:fail_pop
popd
:fail
echo.
echo [실패] 위 메시지를 확인하세요.
if "%~1"=="" pause
exit /b 1
