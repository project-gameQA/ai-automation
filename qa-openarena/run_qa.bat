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

REM 한 세션의 길이(분)다. 이 시간이 지나면 서버가 스스로 세션을 마감하고 리포트를 낸 뒤
REM 새 세션을 연다. 게임은 이 일을 모르고 매치는 계속 돌아간다.
REM
REM 0으로 두면 자르지 않는다. 그 경우 밤새 켜 두면 세션 하나가 무한히 이어져 텔레메트리
REM 한 파일이 시간당 약 127MB 씩 커지고, 리포트는 사람이 멈출 때까지 한 장도 나오지 않는다.
REM 근거와 적정 범위(10~20분)는 docs/report.md 를 본다.
set "QA_SESSION_MINUTES=15"

REM 다 뜬 뒤 브라우저를 자동으로 열지 여부(1이면 연다)
set "OPEN_BROWSER=1"

REM 게임 창과 대시보드 창을 화면 좌우로 나란히 놓을지 여부다(1이면 놓는다).
REM 시연 녹화에서 두 창을 함께 담으려면 매번 손으로 맞춰야 하는데, 그러면 녹화본마다
REM 구도가 달라진다. 켜 두면 작업 표시줄을 뺀 영역을 재서 정확히 반씩 나눈다.
REM 켜면 게임이 창 모드로 뜬다. 전체 화면은 자리를 잡을 수 없기 때문이다.
set "ARRANGE_WINDOWS=1"

REM 게임을 어느 쪽에 놓을지 정한다(left 또는 right).
set "GAME_SIDE=left"

REM 게임도 함께 실행하려면 openarena.exe 의 전체 경로를 적는다. 비워 두면 실행하지 않는다.
REM 예: set "OPENARENA_EXE=C:\game\openarena-0.8.8\openarena.exe"
set "OPENARENA_EXE=C:\game\openarena-0.8.8\openarena.exe"

REM 실행할 테스트 시나리오 파일이다. 맵, 봇 수, 봇별 실력, 실험용 cvar, 결함 주입 상태가
REM 여기에 적혀 있고 게임이 그대로 매치를 시작한다. 파일 형식은 docs/scenario.md 를 본다.
REM 비워 두면 시나리오를 쓰지 않고 예전처럼 게임만 띄운다(매치는 사람이 직접 시작한다).
set "SCENARIO=scenarios\default.toml"

REM 명령줄로 시나리오를 넘기면 위 값 대신 그것을 쓴다.
REM     run_qa.bat scenarios\skill_low.toml
REM
REM 시나리오마다 이 파일을 복사해 값만 바꾸는 방법도 있지만 그러지 않는다. 복사본에는
REM 나머지 200여 줄이 함께 복제되고, conda 경로나 포트를 고칠 때 한쪽만 고치면 두 실행이
REM 조용히 달라진다. 자주 쓰는 조합은 아래 두 줄짜리 파일을 따로 만들어 쓴다.
REM     @echo off
REM     call "%%~dp0run_qa.bat" scenarios\skill_low.toml
REM
REM 상대 경로는 이 배치 파일이 있는 폴더(프로젝트 루트) 기준이다.
if not "%~1"=="" set "SCENARIO=%~1"

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
echo   [1/5] conda: %CONDA_CMD%  (환경: %CONDA_ENV%)

REM ── 3. 텔레메트리 경로 확인 ───────────────────────────────────────────────
REM 파일이 없어도 서버는 정상 동작한다(빈 결과를 돌려주고 파일이 생기면 따라간다).
REM 다만 경로 오타가 가장 흔한 실수라 미리 알려 준다. 게임을 아직 안 켰으면 없는 것이 정상이다.
if exist "%TELEMETRY%" goto :telemetry_ok
echo   [2/5] 텔레메트리: 아직 없음
echo         %TELEMETRY%
echo         게임을 아직 안 켰다면 정상이다. 경로 자체가 틀렸다면 이 파일 위쪽의
echo         TELEMETRY 값을 고친다.
goto :telemetry_done
:telemetry_ok
echo   [2/5] 텔레메트리: %TELEMETRY%
:telemetry_done

REM 텔레메트리가 놓이는 폴더가 곧 게임의 모드 폴더다. 시나리오 cfg 도 같은 곳에 놓아야
REM 게임이 이름만으로 실행할 수 있다. 두 경로를 따로 설정하게 하면 한쪽만 고쳤을 때 게임이
REM cfg 를 못 찾는데, 그 실패는 게임 콘솔을 봐야만 알 수 있다. 이미 맞춰 둔 값에서 끌어낸다.
REM %%~dpI 는 경로에서 드라이브와 폴더 부분만 뽑으며 항상 역슬래시로 끝난다.
for %%I in ("%TELEMETRY%") do set "MOD_DIR=%%~dpI"

REM 서버가 읽을 경로를 환경변수로 넘긴다.
REM start 로 띄우는 자식 창은 이 환경을 그대로 물려받으므로, 여기서 한 번만 설정하면 된다.
REM (자식 명령줄 안에서 set 을 하면 경로에 공백이 있을 때 따옴표가 겹쳐 지저분해진다.)
set "QA_TELEMETRY=%TELEMETRY%"

REM ── 4. 프론트엔드 의존성 확인 ─────────────────────────────────────────────
REM node_modules 가 없으면 npm run dev 가 바로 실패한다. 최초 1회는 설치가 필요하다.
if exist "dashboard\frontend\node_modules" goto :npm_ok
echo   [3/5] node_modules 가 없다. npm install 을 먼저 실행한다(최초 1회, 인터넷 필요).
pushd "dashboard\frontend"
call npm install
if errorlevel 1 goto :npm_failed
popd
goto :npm_done
:npm_ok
echo   [3/5] node_modules: 확인됨
:npm_done

REM ── 5. 시나리오 적용 ──────────────────────────────────────────────────────
REM 시나리오를 게임이 읽을 cfg 로 바꿔 모드 폴더에 놓는다. 게임은 시작할 때 그 파일을
REM 실행하고, 그 안에 맵 로드부터 봇 투입까지가 순서대로 들어 있다.
REM
REM 실패하면 여기서 멈춘다. cfg 가 없으면 게임은 메인 메뉴에서 대기하는데, 그것을 모른 채
REM 대시보드만 보고 있으면 "게임은 켰는데 아무 데이터도 안 들어온다"로 보인다. 조용히
REM 다른 조건으로 도는 것보다 여기서 멈추고 이유를 보여 주는 편이 낫다.
if "%SCENARIO%"=="" goto :scenario_skip
if not exist "%SCENARIO%" goto :no_scenario

REM 시나리오 도구는 표준 라이브러리만 쓰지만 python 자체가 PATH 에 없을 수 있어, 서버와
REM 같은 가상환경을 활성화한 뒤 실행한다. 여기서 활성화한 환경은 아래에서 start 로 띄우는
REM 자식 창에도 그대로 물려진다.
call %CONDA_CMD% activate %CONDA_ENV%
if errorlevel 1 goto :scenario_failed

echo   [4/5] 시나리오 적용: %SCENARIO%
python tools\apply_scenario.py "%SCENARIO%" --telemetry "%TELEMETRY%" --game "%OPENARENA_EXE%"
if errorlevel 1 goto :scenario_failed

REM 확정된 시나리오를 서버에 알려 준다. 서버는 이것을 세션 요약에 함께 남겨, 나중에 세션
REM 파일만 보고 "이 결과가 어떤 매치 조건에서 나왔는가"를 알 수 있게 한다.
set "QA_SCENARIO=%MOD_DIR%qa_scenario.json"

REM 게임에 넘길 인자다. +exec 가 위에서 만든 cfg 를 실행한다.
set "GAME_ARGS=+set fs_game qa +set sv_pure 0 +exec qa_match.cfg"
goto :video_args

:scenario_skip
echo   [4/5] 시나리오: 사용하지 않는다. 게임만 띄우고 매치는 사람이 시작한다.
set "GAME_ARGS=+set fs_game qa +set sv_pure 0"

:video_args
REM 창 배치를 켰으면 게임을 창 모드로, 화면 절반 크기로 띄운다.
REM
REM 크기를 여기서 정하는 이유가 있다. Quake3 계열은 창 크기가 바뀌어도 렌더링 해상도를
REM 따라 바꾸지 않는다. GL 컨텍스트가 시작할 때 정해진 크기로 고정되므로, 뒤에서 창만
REM 늘리면 화면이 늘어나거나 잘린다. 그래서 배치 스크립트에 먼저 크기를 물어보고 그
REM 값으로 게임을 띄운 뒤, 나중에는 자리만 옮긴다.
if not "%ARRANGE_WINDOWS%"=="1" goto :scenario_done
if not exist "tools\arrange_windows.ps1" goto :scenario_done

REM -Report 는 창을 건드리지 않고 "가로 세로" 두 숫자만 출력한다.
REM -ExecutionPolicy Bypass 를 주는 이유는 기본 정책이 스크립트 실행을 막는 환경이
REM 흔하기 때문이다. 이 한 번의 실행에만 적용되며 시스템 설정을 바꾸지 않는다.
REM 스크립트가 "이름=값" 형태로 한 줄에 하나씩 내보내므로 그대로 set 에 넘긴다.
REM 한 줄에 변수 여러 개를 설정하는 문법은 for 반복 안에서 동작이 미묘해 값이 빌 수 있다.
set "GAME_W="
for /f "delims=" %%A in ('powershell -NoProfile -ExecutionPolicy Bypass -File "tools\arrange_windows.ps1" -Report -Split 0.5') do set "%%A"

REM 크기를 못 구했으면 창 모드로만 띄우고 해상도는 게임의 기존 설정을 따른다.
if not defined GAME_W goto :video_fallback
set "GAME_ARGS=%GAME_ARGS% +set r_fullscreen 0 +set r_mode -1"
set "GAME_ARGS=%GAME_ARGS% +set r_customwidth %GAME_W% +set r_customheight %GAME_H%"

REM 화면 비율을 명시한다. 표준 해상도가 아닌 크기에서 비율을 스스로 계산하지 못하는 갈래가
REM 있어 그대로 두면 화면이 옆으로 찌그러진다. 두 이름을 모두 넣는 이유는 어느 이름을 쓰는지가
REM 엔진 갈래마다 달라서다. 쓰지 않는 쪽은 그냥 무시된다.
set "GAME_ARGS=%GAME_ARGS% +set r_customaspect %GAME_ASPECT% +set r_customPixelAspect 1"
echo         게임 창 크기: %GAME_W%x%GAME_H% (화면 절반)
goto :scenario_done
:video_fallback
set "GAME_ARGS=%GAME_ARGS% +set r_fullscreen 0"
echo         화면 크기를 재지 못해 창 모드로만 띄운다.
:scenario_done

REM ── 6. 실행 ───────────────────────────────────────────────────────────────
REM start "제목" cmd /k "명령" 은 새 창을 열고 명령을 실행한 뒤 창을 남긴다(/k).
REM 창을 남기는 이유는 서버 로그와 오류를 그대로 볼 수 있어야 하기 때문이다.
REM 제목을 붙여 두면 stop_qa.bat 가 그 제목으로 창을 찾아 종료할 수 있다.
REM 자식 창은 현재 폴더(프로젝트 루트)를 물려받으므로 상대 경로로 이동하면 된다.

echo   [5/5] 서버와 프론트엔드를 새 창에서 실행한다.
echo.

REM API 서버. --reload 는 일부러 쓰지 않는다. 파일이 바뀔 때마다 서버가 재시작되면서
REM 실시간 감시 상태(읽던 위치, 끼임 이력, 진행 중인 사건)가 통째로 날아가기 때문이다.
start "QA Server" cmd /k "call %CONDA_CMD% activate %CONDA_ENV% && cd dashboard\server && uvicorn app:app --port %SERVER_PORT%"

REM 프론트엔드. vite 는 conda 와 무관하므로 가상환경을 활성화하지 않는다.
start "QA Frontend" cmd /k "cd dashboard\frontend && npm run dev"

REM 게임 실행 경로가 지정돼 있으면 함께 띄운다.
if "%OPENARENA_EXE%"=="" goto :skip_game
if not exist "%OPENARENA_EXE%" goto :bad_game_path
start "" "%OPENARENA_EXE%" %GAME_ARGS%
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

REM 브라우저가 페이지를 그리고 창 제목이 정해지기까지 잠깐 걸린다. 배치 스크립트가
REM 자체 대기를 갖고 있으므로 여기서 더 기다리지는 않는다.
if not "%ARRANGE_WINDOWS%"=="1" goto :done
if not exist "tools\arrange_windows.ps1" goto :done
echo   창을 좌우로 배치한다.
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\arrange_windows.ps1" -GameSide %GAME_SIDE% -Split 0.5
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

:no_scenario
echo   오류: 시나리오 파일을 찾을 수 없다.
echo         %SCENARIO%
echo         이 파일 위쪽의 SCENARIO 값을 고친다. 비워 두면 시나리오 없이 실행한다.
echo         예시 시나리오는 scenarios 폴더에 있다.
goto :halt

:scenario_failed
echo   오류: 시나리오를 적용하지 못했다. 위 메시지를 확인한다.
echo         설치본에 어떤 맵과 봇이 있는지는 다음으로 확인한다.
echo             python tools\apply_scenario.py --list --game "%OPENARENA_EXE%"
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
