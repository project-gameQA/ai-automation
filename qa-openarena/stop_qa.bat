@echo off
chcp 65001 > nul
setlocal

REM ===========================================================================
REM  run_qa.bat 가 띄운 창들을 종료한다.
REM
REM  창 제목으로 대상을 찾는다. node.exe 나 python.exe 를 이름으로 죽이면 이 프로젝트와
REM  무관한 다른 작업까지 함께 죽이게 되므로, 제목 필터를 쓴다.
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

taskkill /FI "WINDOWTITLE eq QA Frontend*" /T /F > nul 2>nul
if errorlevel 1 goto :front_none
echo   프론트엔드를 종료했다.
goto :front_done
:front_none
echo   실행 중인 프론트엔드 창이 없다.
:front_done

echo.
echo   창이 남아 있으면 직접 닫는다. 게임은 이 스크립트로 종료하지 않는다.
echo.
timeout /t 3 /nobreak > nul
exit /b 0
