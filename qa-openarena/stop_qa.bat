@echo off
chcp 65001 > nul
setlocal

REM ===========================================================================
REM  run_qa.bat 가 띄운 창들을 종료한다.
REM
REM  대상을 찾는 방법이 둘로 나뉜다.
REM  - API 서버: 창 제목("QA Server")으로 찾는다. uvicorn 은 제목을 바꾸지 않는다.
REM  - 프론트엔드: 포트(5173)로 찾는다. npm 이 창 제목을 덮어써 제목으로는 찾을 수 없다.
REM  어느 쪽이든 node.exe 나 python.exe 를 이름으로 죽이지는 않는다. 이 프로젝트와 무관한
REM  다른 작업까지 함께 죽이게 되기 때문이다.
REM  /T 는 자식 프로세스까지 함께 종료한다는 뜻이다. cmd 창만 닫으면 그 안에서 돌던
REM  uvicorn 이나 node 가 살아남아 포트를 계속 잡고 있는 일이 생긴다.
REM ===========================================================================

echo.
echo   OpenArena QA Monitor 종료
echo.

REM 창 제목 앞부분만 맞으면 되도록 * 를 붙인다. cmd 가 실행 중인 명령을 제목에
REM 덧붙이는 경우가 있어, 정확히 일치시키면 못 찾을 수 있다.
taskkill /FI "WINDOWTITLE eq QA Server*" /T /F > nul 2>nul
if errorlevel 1 goto :server_none
echo   API 서버를 종료했다.
goto :server_done
:server_none
echo   실행 중인 API 서버 창이 없다.
:server_done

REM 프론트엔드는 제목으로 찾을 수 없다. start 로 붙인 "QA Frontend" 제목을 npm 이
REM 실행되면서 자기 스크립트 이름으로 덮어쓰기 때문이다. 그래서 taskkill 의 제목 필터가
REM 대상을 못 찾고 조용히 넘어간다(서버는 uvicorn 이 제목을 안 바꿔 제목 방식이 통한다).
REM
REM 대신 vite 가 듣고 있는 포트로 프로세스를 찾는다. 포트를 듣는 것은 실제로 서버 역할을
REM 하는 node 프로세스이므로, 이름이나 제목과 달리 확실하게 특정된다.
set "VITE_PORT=5173"
set "VITE_PID="

REM netstat -ano 의 마지막 열이 PID 다. LISTENING 상태만 골라 서버 쪽 프로세스를 잡는다.
REM (접속해 들어온 클라이언트 쪽 연결까지 잡으면 엉뚱한 프로세스를 죽일 수 있다.)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%VITE_PORT%" ^| findstr "LISTENING"') do set "VITE_PID=%%p"

if not defined VITE_PID goto :front_none

REM node 만 죽이면 그것을 띄운 cmd 창이 빈 채로 남는다. 창까지 정리하려면 부모를 죽여야 한다.
REM 프로세스 구조가 cmd.exe -> npm -> node.exe 이므로, node 의 부모를 /T 로 죽이면
REM 창과 그 안의 프로세스가 함께 정리된다.
set "VITE_PARENT="
for /f "skip=1 tokens=1" %%a in ('wmic process where "processid=%VITE_PID%" get parentprocessid 2^>nul') do (
    if not defined VITE_PARENT set "VITE_PARENT=%%a"
)

REM wmic 은 최신 Windows 에서 기본 제공되지 않을 수 있다. 없으면 node 만 종료한다.
REM 이 경우 서버는 확실히 멈추지만 빈 cmd 창이 남을 수 있으며, 직접 닫으면 된다.
if defined VITE_PARENT taskkill /PID %VITE_PARENT% /T /F > nul 2>nul
taskkill /PID %VITE_PID% /T /F > nul 2>nul
echo   프론트엔드를 종료했다. (포트 %VITE_PORT%, PID %VITE_PID%)
goto :front_done

:front_none
echo   실행 중인 프론트엔드가 없다. (포트 %VITE_PORT% 를 듣는 프로세스 없음)
:front_done

echo.
echo   창이 남아 있으면 직접 닫는다. 게임은 이 스크립트로 종료하지 않는다.
echo.
timeout /t 3 /nobreak > nul
exit /b 0
