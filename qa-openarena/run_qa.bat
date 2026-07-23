@echo off
chcp 65001 > nul
setlocal

REM 위의 chcp 는 콘솔 코드 페이지를 UTF-8로 바꾼다. 이 파일이 UTF-8로 저장돼 있어서,
REM 코드 페이지를 먼저 바꾸지 않으면 이 아래의 한글이 깨진 채로 읽힌다.
REM cmd 는 배치 파일을 한 줄씩 읽으며 실행하므로, chcp 위쪽은 반드시 ASCII 여야 한다.

REM ===========================================================================
REM  OpenArena QA Monitor 실행 스크립트
REM
REM  파이썬 가상환경 활성화, API 서버(uvicorn), 프론트엔드(vite)를 한 번에 띄운다.
REM  서버와 프론트엔드는 둘 다 계속 떠 있는 프로세스라 한 창에서 순서대로 실행하면
REM  앞의 것이 뒤의 것을 막는다. 그래서 start 로 각각 별도 창에 띄운다.
REM
REM  종료는 stop_qa.bat 를 쓰거나, 열린 창을 각각 닫으면 된다.
REM ===========================================================================

REM ── 여기만 자기 환경에 맞게 고치면 된다 ────────────────────────────────────

REM 파이썬 가상환경(conda) 이름
set "CONDA_ENV=ai"

REM 게임이 실제로 기록하는 텔레메트리 파일 경로.
REM OpenArena 홈패스(condump 가 저장되던 곳) 아래의 qa 폴더에 생긴다.
REM 경로가 다르면 여기를 고친다. 서버는 이 파일을 실시간으로 따라간다.
set "TELEMETRY=%APPDATA%\OpenArena\qa\qa_telemetry.jsonl"

REM API 서버 포트. 바꾸면 qa_dashboard.jsx 의 API_BASE 도 같이 고쳐야 한다.
set "SERVER_PORT=8000"

REM 다 뜬 뒤 브라우저를 자동으로 열지 여부(1이면 연다)
set "OPEN_BROWSER=1"

REM 게임도 함께 실행하려면 openarena.exe 의 전체 경로를 적는다. 비워 두면 실행하지 않는다.
REM 예: set "OPENARENA_EXE=C:\game\openarena-0.8.8\openarena.exe"
set "OPENARENA_EXE=C:\game\openarena-0.8.8\openarena.exe"

REM ── 여기부터는 고칠 일이 없다 ──────────────────────────────────────────────

REM 이 배치 파일이 있는 폴더(=프로젝트 루트)로 이동한다.
REM %~dp0 은 배치 파일의 경로이며 항상 역슬래시로 끝난다. /d 는 드라이브까지 바꾼다.
REM 이렇게 해 두면 어디서 더블클릭하든 아래의 상대 경로가 그대로 맞는다.
cd /d "%~dp0"

echo.
echo   OpenArena QA Monitor
echo   ====================
echo.

REM ── 1. 프로젝트 구조 확인 ─────────────────────────────────────────────────
REM 잘못된 위치에 배치 파일을 두면 엉뚱한 오류가 나므로 먼저 확인한다.
if not exist "dashboard\server\app.py" goto :no_server
if not exist "dashboard\frontend\package.json" goto :no_frontend

REM ── 2. conda 찾기 ─────────────────────────────────────────────────────────
REM conda activate 는 실행 파일이 아니라 배치 파일이다. 그래서 call 없이 부르면
REM 제어가 돌아오지 않고 이 스크립트가 거기서 끝나 버린다. 항상 call 을 붙인다.
REM 또한 Anaconda 를 PATH 에 추가하지 않고 설치하면 일반 cmd 에서는 conda 가 잡히지
REM 않는다(Anaconda Prompt 에서만 잡힌다). 그 경우를 대비해 흔한 설치 위치를 찾아본다.
set "CONDA_CMD=conda"
where conda > nul 2>nul
if not errorlevel 1 goto :conda_ready

if exist "%USERPROFILE%\anaconda3\condabin\conda.bat" goto :conda_anaconda
if exist "%USERPROFILE%\miniconda3\condabin\conda.bat" goto :conda_miniconda
if exist "%LOCALAPPDATA%\anaconda3\condabin\conda.bat" goto :conda_local
if exist "C:\ProgramData\anaconda3\condabin\conda.bat" goto :conda_programdata
goto :no_conda

:conda_anaconda
set "CONDA_CMD=%USERPROFILE%\anaconda3\condabin\conda.bat"
goto :conda_ready
:conda_miniconda
set "CONDA_CMD=%USERPROFILE%\miniconda3\condabin\conda.bat"
goto :conda_ready
:conda_local
set "CONDA_CMD=%LOCALAPPDATA%\anaconda3\condabin\conda.bat"
goto :conda_ready
:conda_programdata
set "CONDA_CMD=C:\ProgramData\anaconda3\condabin\conda.bat"
goto :conda_ready

:conda_ready
echo   [1/4] conda: %CONDA_CMD%  (환경: %CONDA_ENV%)

REM ── 3. 텔레메트리 경로 확인 ───────────────────────────────────────────────
REM 파일이 없어도 서버는 정상 동작한다(빈 결과를 돌려주고 파일이 생기면 따라간다).
REM 다만 경로 오타가 가장 흔한 실수라 미리 알려 준다. 게임을 아직 안 켰으면 없는 것이 정상이다.
if exist "%TELEMETRY%" goto :telemetry_ok
echo   [2/4] 텔레메트리: 아직 없음
echo         %TELEMETRY%
echo         게임을 아직 안 켰다면 정상이다. 경로 자체가 틀렸다면 이 파일 위쪽의
echo         TELEMETRY 값을 고친다.
goto :telemetry_done
:telemetry_ok
echo   [2/4] 텔레메트리: %TELEMETRY%
:telemetry_done

REM 서버가 읽을 경로를 환경변수로 넘긴다.
REM start 로 띄우는 자식 창은 이 환경을 그대로 물려받으므로, 여기서 한 번만 설정하면 된다.
REM (자식 명령줄 안에서 set 을 하면 경로에 공백이 있을 때 따옴표가 겹쳐 지저분해진다.)
set "QA_TELEMETRY=%TELEMETRY%"

REM ── 4. 프론트엔드 의존성 확인 ─────────────────────────────────────────────
REM node_modules 가 없으면 npm run dev 가 바로 실패한다. 최초 1회는 설치가 필요하다.
if exist "dashboard\frontend\node_modules" goto :npm_ok
echo   [3/4] node_modules 가 없다. npm install 을 먼저 실행한다(최초 1회, 인터넷 필요).
pushd "dashboard\frontend"
call npm install
if errorlevel 1 goto :npm_failed
popd
goto :npm_done
:npm_ok
echo   [3/4] node_modules: 확인됨
:npm_done

REM ── 5. 실행 ───────────────────────────────────────────────────────────────
REM start "제목" cmd /k "명령" 은 새 창을 열고 명령을 실행한 뒤 창을 남긴다(/k).
REM 창을 남기는 이유는 서버 로그와 오류를 그대로 볼 수 있어야 하기 때문이다.
REM 제목을 붙여 두면 stop_qa.bat 가 그 제목으로 창을 찾아 종료할 수 있다.
REM 자식 창은 현재 폴더(프로젝트 루트)를 물려받으므로 상대 경로로 이동하면 된다.

echo   [4/4] 서버와 프론트엔드를 새 창에서 실행한다.
echo.

REM API 서버. --reload 는 일부러 쓰지 않는다. 파일이 바뀔 때마다 서버가 재시작되면서
REM 실시간 감시 상태(읽던 위치, 끼임 이력, 진행 중인 사건)가 통째로 날아가기 때문이다.
start "QA Server" cmd /k "call %CONDA_CMD% activate %CONDA_ENV% && cd dashboard\server && uvicorn app:app --port %SERVER_PORT%"

REM 프론트엔드. vite 는 conda 와 무관하므로 가상환경을 활성화하지 않는다.
start "QA Frontend" cmd /k "cd dashboard\frontend && npm run dev"

REM 게임 실행 경로가 지정돼 있으면 함께 띄운다.
if "%OPENARENA_EXE%"=="" goto :skip_game
if not exist "%OPENARENA_EXE%" goto :bad_game_path
start "" "%OPENARENA_EXE%" +set fs_game qa +set sv_pure 0
goto :skip_game
:bad_game_path
echo   경고: OPENARENA_EXE 경로에 파일이 없다. 게임은 실행하지 않는다.
echo         %OPENARENA_EXE%
:skip_game

REM vite 가 포트를 열기까지 몇 초 걸린다. 그 전에 브라우저를 열면 연결 실패 화면이 뜬다.
if not "%OPEN_BROWSER%"=="1" goto :done
echo   브라우저를 여는 중이다. 잠시 기다린다.
timeout /t 8 /nobreak > nul
start "" http://localhost:5173
goto :done

REM ── 오류 처리 ─────────────────────────────────────────────────────────────
:no_server
echo   오류: dashboard\server\app.py 를 찾을 수 없다.
echo         이 배치 파일은 프로젝트 루트(qa-openarena 폴더) 안에 있어야 한다.
echo         현재 위치: %CD%
goto :halt

:no_frontend
echo   오류: dashboard\frontend\package.json 을 찾을 수 없다.
echo         프론트엔드 프로젝트가 생성되지 않았다. 아래를 먼저 실행한다.
echo             cd dashboard
echo             npm create vite@latest frontend -- --template react
goto :halt

:no_conda
echo   오류: conda 를 찾을 수 없다.
echo         PATH 에도 없고 흔한 설치 위치에서도 못 찾았다.
echo         Anaconda Prompt 에서 다음을 실행해 전체 경로를 확인한 뒤,
echo         이 파일 위쪽의 CONDA_CMD 로 지정하거나 PATH 에 추가한다.
echo             where conda
goto :halt

:npm_failed
popd
echo   오류: npm install 에 실패했다. 인터넷 연결과 Node.js 설치를 확인한다.
goto :halt

:halt
echo.
pause
exit /b 1

:done
echo.
echo   서버:       http://127.0.0.1:%SERVER_PORT%/
echo   대시보드:   http://localhost:5173
echo.
echo   종료하려면 stop_qa.bat 를 실행하거나 열린 창을 닫는다.
echo.
timeout /t 3 /nobreak > nul
exit /b 0
