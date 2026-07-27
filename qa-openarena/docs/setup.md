# setup.md — 처음부터 세팅하기

이 저장소만 받아서는 시스템이 동작하지 않는다. **게임 쪽 준비가 따로 필요하다.** 이 문서는
아무것도 없는 상태에서 전체를 돌리기까지의 절차를 순서대로 담는다.

역할 분담:
- **이 문서**: 무엇을 설치하고 어떻게 빌드해서 어디에 두는가.
- `docs/gamecode_instrumentation.md`: 게임 C 소스를 **무엇을 왜** 바꿨는가(변경 16건의 목록).
- `docs/injection_map.md`: 주입 지점이 소스의 **어디**인가.
- `docs/scenario.md`: 매치 조건(맵·봇·실력·cvar·주입)을 파일로 적어 자동 실행하는 방법.
- `docs/explain.md`: 파이썬 쪽 데이터 흐름과 모듈 상세.

## 0. 문서 작성 규칙
- 기존 기록은 삭제하지 않는다. 내용이 바뀌면 취소선과 `[제거 YYYY-MM-DD: 사유]`를 남기고,
  새 내용을 아래에 `[추가 YYYY-MM-DD]`로 기록한다.
- 문서체는 평서형(~다)으로 통일한다.

---

## 1. 이 저장소에 없는 것과 그 이유

| 없는 것 | 이유 | 대신 있는 것 |
|---|---|---|
| OpenArena 게임 본체 | 별도 배포물이다 | 아래 3절의 설치 절차 |
| 게임 C 소스(`gamecode`) | 제3자 프로젝트의 큰 코드베이스다. 통째로 넣으면 이 프로젝트에서 실제로 작성한 부분이 묻힌다 | 변경 16건 전체가 `docs/gamecode_instrumentation.md`에 위치·이유와 함께 기록돼 있다 |
| 빌드·배포 배치 파일 | 게임 소스 트리 안에 있고 경로가 환경마다 다르다 | 6절에 같은 일을 하는 템플릿 |
| 텔레메트리·세션 데이터 | 실행하면 생긴다. 크고 환경마다 다르다 | 4절에서 생성 위치를 확인한다 |
| `node_modules`, 학습된 모델 | 생성물이다 | 9절, 10절에서 만든다 |

게임 소스 변경은 **세 파일에 흩어진 작은 편집 16건**이다(`g_main.c`, `g_local.h`, `g_active.c`).
`gamecode_instrumentation.md`를 보고 그대로 재현하면 된다.

---

## 2. 준비물

| | 용도 |
|---|---|
| Windows | 아래 절차는 Windows 기준이다 |
| OpenArena 0.8.8 | 게임 본체. 아키텍처 무관 |
| Git | 게임 소스를 받는다 |
| Python 3.10+ (conda 권장) | 검출기·서버 |
| Node.js | 대시보드 |

**C 컴파일러는 필요 없다.** 게임 소스에 옛 id 컴파일러(`lcc`/`q3asm`)가 동봉돼 있고 그것으로
빌드한다. 최신 gcc 와 2012년 코드가 충돌하는 문제를 피하려고 이 방식을 택했다(상세는
`docs/injection_map.md` 5-B).

---

## 3. 게임 설치

OpenArena 0.8.8 을 설치한다. 이 문서는 아래 경로를 기준으로 쓴다. 다르면 이후 경로를 맞춰 읽는다.

```
C:\game\openarena-0.8.8\
```

설치 후 한 번 실행해 정상 동작을 확인한다.

---

## 4. 텔레메트리가 생길 위치 확인

게임은 설치 폴더가 아니라 **홈패스**에 파일을 쓴다. 계측을 넣기 전에 이 위치를 알아 둬야
나중에 텔레메트리를 못 찾아 헤매지 않는다.

게임 콘솔(`~`)에서 `\condump test.txt` 를 실행하면 그 파일이 생긴 곳이 홈패스다. 보통은
`%APPDATA%\OpenArena` 아래다.

텔레메트리는 모드 폴더 아래에 생긴다.

```
<홈패스>\qa\qa_telemetry.jsonl
```

이 경로를 메모해 둔다. 8절과 10절에서 쓴다.

---

## 5. 게임 소스 받고 계측 적용

```
cd C:\game
git clone https://github.com/OpenArena/gamecode
```

`docs/gamecode_instrumentation.md` 2절의 변경 16건을 적용한다. 파일은 셋뿐이다.

| 파일 | 무엇이 들어가는가 |
|---|---|
| `code/game/g_main.c` | 주입 스위치 cvar 선언·등록, 텔레메트리 파일 열기·닫기 |
| `code/game/g_local.h` | 위 전역들의 `extern` 선언 |
| `code/game/g_active.c` | 텔레메트리 기록, 주입기 4종 |

**주입 순서 규칙에 주의한다.** 상태를 오염시키는 주입(체력·경계·끼임)은 `ClientEndFrame`에서
텔레메트리 기록보다 **위**에 둔다(오염값이 기록되도록). 바닥 관통만 예외로 `ClientThink_real`에
둔다(Pmove 전에 tracemask 를 바꿔야 하므로). 상세는 `gamecode_instrumentation.md`의
"주입기 순서 규칙".

---

## 6. QVM 빌드와 배포

### 6-1. 최초 1회 — 세 모듈 모두 빌드

```
cd C:\game\gamecode\windows_scripts
windows_compile_game.bat
windows_compile_cgame.bat
windows_compile_q3_ui.bat
```

결과물이 `C:\game\gamecode\windows\baseoa\vm\` 에 `qagame.qvm`, `cgame.qvm`, `ui.qvm` 로 생긴다.

세 개를 모드 폴더로 복사한다. 폴더가 없으면 만든다.

```
C:\game\openarena-0.8.8\qa\vm\
```

`.pk3` 로 묶지 않고 파일을 그대로 둔다. 실행 시 `sv_pure 0` 을 주므로 loose 파일이 로드된다.

### 6-2. 이후 반복 — qagame 만 재빌드

계측이나 주입기를 고칠 때는 **`qagame` 만** 다시 빌드해 덮어쓴다. `cgame`·`ui` 는 건드리지
않았으므로 최초 빌드 후 고정이다.

배치 파일로 자동화해 두면 편하다. 아래는 템플릿이며, 경로를 자기 환경에 맞춘다.

```bat
@echo off
REM build_and_deploy.bat — qagame 만 빌드해 모드 폴더에 배포한다.
REM 게임 소스 트리(C:\game\gamecode\windows_scripts\)에 두고 쓴다.

set "GAMECODE=C:\game\gamecode"
set "MOD_VM=C:\game\openarena-0.8.8\qa\vm"

cd /d "%GAMECODE%\windows_scripts"
call windows_compile_game.bat
if errorlevel 1 goto :fail

REM 빌드 결과를 모드 폴더로 덮어쓴다.
copy /Y "%GAMECODE%\windows\baseoa\vm\qagame.qvm" "%MOD_VM%\qagame.qvm"
if errorlevel 1 goto :fail

echo   배포 완료: %MOD_VM%\qagame.qvm
exit /b 0

:fail
echo   실패. 위 출력을 확인한다.
pause
exit /b 1
```

**게임이 실행 중이면 QVM 을 덮어쓸 수 없다.** 게임을 먼저 종료한다.

---

## 7. 게임 실행

```
openarena.exe +set fs_game qa +set sv_pure 0 +exec qa_match.cfg
```

`fs_game qa` 가 모드 폴더 이름이고, `sv_pure 0` 이 loose 파일 로드를 허용한다.
`+exec qa_match.cfg` 가 10절에서 만드는 시나리오 파일을 실행해 **매치까지 자동으로 시작한다**
(맵 로드, 봇 투입, cvar 설정, 관전 전환). `run_qa.bat` 을 쓰면 이 줄을 직접 칠 일은 없다.

시나리오를 쓰지 않고 손으로 매치를 시작할 때는 `+exec` 없이 실행하고, 콘솔(`~`)에서 아래를
설정한다. 시나리오는 결국 이 명령들을 순서대로 적어 둔 것이다.

```
\qa_inject_health 1      결함 주입 켜기 (0 으로 끔)
\fraglimit 0             매치가 중간에 끝나지 않게
\timelimit 0             (끝나면 텔레메트리가 지워지고 점수판 구간이 이상탐지 오탐이 된다)
\bot_minplayers 8        인원이 모자라면 봇으로 채운다
```

시나리오를 쓸 때 콘솔에서 유용한 것은 둘이다.

```
\exec qa_match.cfg       같은 매치를 처음부터 다시 시작한다(게임을 끌 필요가 없다)
\exec qa_bots.cfg        봇이 안 들어왔을 때 봇만 다시 투입한다
```

관전은 `\team spectator` 후 클릭으로 봇을 순환하고, 점프로 자유 시점이 된다.

**주입기는 4종이다**: `qa_inject_health`, `qa_inject_oob`, `qa_inject_fall`, `qa_inject_stuck`.
`oob` 와 `stuck` 은 둘 다 위치를 조작하므로 동시에 켜면 충돌한다.

---

## 8. 파이썬 환경

```
conda create -n ai python=3.11
conda activate ai
cd <이 저장소>
pip install -r dashboard/server/requirements.txt
```

`requirements.txt` 에 들어 있는 것과 용도다.

| 패키지 | 용도 | 없으면 |
|---|---|---|
| fastapi, uvicorn | API 서버 | 서버가 안 뜬다 |
| scikit-learn, joblib, numpy | 이상탐지 | 이상탐지만 꺼진다 |
| psutil | 워치독의 CPU·메모리·프로세스 관측 | 그 항목만 비고 틱 측정은 동작한다 |

---

## 9. 프론트엔드 환경

Vite 프로젝트 뼈대는 저장소에 없다. 한 번 만들고 컴포넌트를 넣는다.

```
cd dashboard
npm create vite@latest frontend -- --template react
cd frontend
npm install
```

저장소의 `dashboard/frontend/src/qa_dashboard.jsx` 를 그 자리에 두고, `src/App.jsx` 에서 부른다.

`index.html` 의 `<title>` 도 저장소의 것으로 맞춘다. Vite 가 만들어 주는 기본 제목 대신
`OpenArena QA Monitor` 를 쓴다. 시연 화면에 그대로 보이기도 하고, 10절의 창 배치가 이 제목으로
브라우저 창을 찾기 때문이다.

```jsx
import QADashboard from "./qa_dashboard";

export default function App() {
  return <QADashboard />;
}
```

**Vite 기본 CSS 가 레이아웃을 깨뜨린다.** `src/index.css` 의 `body { display: flex; place-items: center }`
와 `src/App.css` 의 `#root { max-width: 1280px; padding: 2rem; text-align: center }` 가 3열 그리드와
충돌한다. `main.jsx` 에서 두 CSS import 를 지우거나 `index.css` 를 아래로 비운다.

```css
* { box-sizing: border-box; }
html, body, #root { margin: 0; padding: 0; height: 100%; }
```

---

## 10. 실행 스크립트 설정

저장소 루트의 `run_qa.bat` 위쪽 네 줄을 자기 환경에 맞춘다.

```bat
set "CONDA_ENV=ai"
set "TELEMETRY=%APPDATA%\OpenArena\qa\qa_telemetry.jsonl"
set "OPENARENA_EXE=C:\game\openarena-0.8.8\openarena.exe"
set "SCENARIO=scenarios\default.toml"
```

세션 길이는 `QA_SESSION_MINUTES`(기본 15분)로 정한다. 이 시간마다 서버가 세션을 마감하고
리포트를 낸 뒤 새 세션을 연다. 게임은 계속 돌아간다. 근거는 `docs/report.md` 2-1절에 있다.

`TELEMETRY` 는 4절에서 확인한 경로다. 시나리오 cfg 도 같은 폴더에 만들어지므로 이 값 하나만
맞으면 된다.

`SCENARIO` 는 실행할 매치 조건이다. 비워 두면 게임만 띄우고 매치는 사람이 시작한다.
먼저 설치본에 어떤 맵과 봇이 있는지 확인하고, 시나리오의 `map` 과 봇 이름을 그것에 맞춘다.

```
python tools/apply_scenario.py --list --game "C:\game\openarena-0.8.8"
```

`scenarios/default.toml` 의 맵 이름은 예시값이다. **`qa/config.py` 의 경계값은 특정 맵에서
보정한 것이므로**, 그 보정에 쓴 맵과 같은 이름인지 확인한다. 다르면 경계 이탈이 정상 플레이에도
쏟아진다. 형식과 나머지 항목은 `docs/scenario.md` 를 본다.

실행 파일 이름이 `openarena.exe` 가 아니면 워치독의
프로세스 감시를 위해 환경변수를 추가한다.

```
set QA_PROCESS_HINT=<실행 파일 이름 일부>
```

### 창 배치

시연은 게임 창과 대시보드 창을 함께 화면 녹화하는 형태다. 두 창을 매번 손으로 끌어다 맞추면
녹화본마다 구도가 달라지므로, 실행할 때 자동으로 좌우에 나란히 놓는다.

```bat
set "ARRANGE_WINDOWS=1"
set "GAME_SIDE=left"
```

켜면 게임이 **창 모드**로 뜨고 크기가 화면 절반에 맞춰진다. 전체 화면은 자리를 잡을 수 없기
때문이다. 끄려면 `ARRANGE_WINDOWS=0` 으로 두며, 그러면 게임의 기존 화면 설정을 그대로 쓴다.

배치는 `tools/arrange_windows.ps1` 이 하며 `run_qa.bat` 이 알아서 부른다. 직접 칠 일은 없다.

따로 실행하는 경우는 둘이다. 창을 손으로 옮긴 뒤 원래 자리로 되돌릴 때와, 아래의 대시보드
탐색이 실패했을 때다. 게임과 서버는 그대로 둔 채 배치만 다시 한다.

```
powershell -NoProfile -ExecutionPolicy Bypass -File tools\arrange_windows.ps1 -GameSide right
```

이후 실행과 종료는 이 둘이다.

```
run_qa.bat
stop_qa.bat
```

---

## 11. 이상탐지 모델 학습

이상탐지는 **정상 플레이를 학습해야** 동작한다. 학습 전에는 그 층만 꺼진 채로 나머지가 돈다.

1. 주입기를 모두 끄고 봇 8마리로 10분 이상 플레이한다.
   (`SCENARIO=scenarios\default.toml` 이 그 조건이다. 주입기가 전부 꺼져 있고 봇이 8마리다.)
2. 세션 폴더에 `session_<시각>.telemetry.jsonl` 이 쌓인다.
3. 학습한다.

```
python tools/train_anomaly.py sessions/session_<시각>.telemetry.jsonl
```

4. 학습에 쓰지 않은 다른 정상 세션으로 오탐률을 확인한다.

```
python tools/score_anomaly.py sessions/<다른 정상 세션>
```

모델은 `models/anomaly.joblib` 에 저장된다. 서버는 프로젝트 루트 기준으로 이 경로를 찾는다.

---

## 12. 동작 확인 체크리스트

순서대로 확인한다. 앞이 안 되면 뒤는 안 된다.

| 확인할 것 | 방법 | 정상이면 |
|---|---|---|
| 시나리오가 적용되는가 | `python tools/apply_scenario.py <시나리오> --game <경로> --dry-run` | cfg 두 개가 출력된다 |
| QVM 이 적재됐는가 | 게임 콘솔에 `\qa_inject_health` | cvar 값이 나온다 |
| 매치가 자동 시작되는가 | `run_qa.bat` 실행 후 게임 화면 | 맵이 뜨고 봇이 들어온다 |
| 텔레메트리가 쌓이는가 | 4절 경로의 파일 크기 | 플레이 중 계속 커진다 |
| 검출기가 도는가 | `python run_invariants.py <텔레메트리>` | 정상 플레이면 0건 |
| 서버가 뜨는가 | `http://127.0.0.1:8000/` | JSON 이 나온다 |
| 이상탐지가 붙었는가 | 같은 곳의 `anomaly_enabled` | `true` (학습 후) |
| 프로세스 감시가 되는가 | 같은 곳의 `process_monitor` | `true` |
| 대시보드가 붙었는가 | `http://localhost:5173` | LIVE, 게임 시간이 올라간다 |
| 워치독이 관측 중인가 | 대시보드 WATCHDOG 줄 | `감시 중`, 서버 틱 20 근처 |
| 리포트가 나오는가 | 대시보드에서 **내보내기** | `reports/report_<시각>.html` 이 생긴다 |

---

## 13. 자주 걸리는 문제

실제로 겪은 것들이다.

**매치가 중간에 끝난다** — `fraglimit`/`timelimit` 에 도달하면 관전 카메라가 풀리고 게임이
텔레메트리 파일을 지운다(`FS_WRITE`). 그러면 그 판의 데이터가 사라진다. `\fraglimit 0`
`\timelimit 0` 으로 막는다. 점수판 구간이 이상탐지 오탐이 되는 것도 함께 해결된다.

**봇이 안 들어온다** — 게임 콘솔에 `Error: Bot 'X' not defined` 가 있으면 이름 문제다.
`--list` 로 확인해 시나리오를 고친다. 그런 줄이 없으면 타이밍 문제이므로 `\exec qa_bots.cfg`
로 다시 시도한다. 그것으로 들어오면 시나리오의 `[timing] wait_frames` 를 늘린다.
상세는 `docs/scenario.md` 7절과 10절에 있다.

**시나리오를 고쳤는데 반영되지 않는다** — cfg 는 `run_qa.bat` 이 게임을 띄우기 직전에
만들어진다. 게임이 이미 떠 있으면 옛 cfg 로 도는 중이다. `apply_scenario.py` 를 다시 돌린 뒤
게임 콘솔에서 `\exec qa_match.cfg` 를 친다.

**`g_spSkill` 을 바꿔도 봇 실력이 그대로다** — 봇 실력은 스폰 시점에 읽는 값이라 이미 접속한
봇에게는 반영되지 않는다. `\map_restart` 로 다시 스폰시킨다.

**`conda` 를 못 찾는다** — Anaconda 를 PATH 에 추가하지 않고 설치하면 일반 cmd 에서 안 잡힌다
(Anaconda Prompt 에서만 잡힌다). `run_qa.bat` 이 흔한 설치 위치를 찾아보지만 못 찾으면
`where conda` 로 확인해 `CONDA_CMD` 에 지정한다.

**서버를 `--reload` 로 띄우면 안 된다** — 파일이 바뀔 때마다 재시작되면서 실시간 감시 상태
(읽던 위치, 끼임 이력, 진행 중인 사건)가 통째로 날아간다.

**모델을 찾지 못한다** — `models/anomaly.joblib` 은 프로젝트 루트 기준이다. `train_anomaly.py`
를 루트에서 실행하면 맞는 위치에 생긴다. `/api/` 루트 응답의 `anomaly_error` 에 찾은 경로가 나온다.

**게임을 켜기 전인데 워치독이 조용하다** — 정상이다. 서버가 시작할 때 기존 텔레메트리 파일을
다 읽지만 그것은 지난 매치의 기록이므로, 실시간 관측이 확인되기 전에는 판정하지 않는다.
대시보드에 `게임 대기 중` 으로 표시된다.

**`measure_perf.py` 에서 표본이 안 쌓인다** — 워치독이 `감시 중` 이어야 잰다. 게임을 켜고 봇
매치를 시작한 뒤 실행한다.

**QVM 을 덮어쓸 수 없다** — 게임이 실행 중이면 파일이 잠긴다. 종료하고 다시 배포한다.

**대시보드 레이아웃이 깨진다** — 9절의 Vite 기본 CSS 문제다.

**게임 창이 자리를 잡지 못한다** — 전체 화면이면 옮길 수 없다. `ARRANGE_WINDOWS=1` 이면
창 모드로 뜨는 것이 정상이며, 아니라면 게임 콘솔에서 `\r_fullscreen` 값을 본다.
다중 모니터에서는 주 모니터를 기준으로 나눈다.

**대시보드 창을 찾지 못한다** — 원인은 셋 중 하나다.

1. `OPEN_BROWSER=0` 이라 대시보드를 열지 않았다.
2. `index.html` 의 `<title>` 이 `OpenArena QA Monitor` 가 아니다(9절).
3. 개발 서버가 아직 준비되지 않아 브라우저에 연결 실패 화면이 떠 있다. `npm run dev` 를
   처음 띄우는 경우에 걸린다. 새로 고침한 뒤 위의 배치 스크립트를 다시 실행한다.

**게임 화면이 옆으로 찌그러져 보인다** — 창 크기가 표준 해상도가 아니라 비율 계산이 어긋난
경우다. `run_qa.bat` 이 `r_customaspect` 와 `r_customPixelAspect` 를 함께 넘겨 막고 있지만,
그래도 어긋나면 `ARRANGE_WINDOWS=0` 으로 두고 게임의 기존 해상도를 쓴다.

---

## 14. 변경 이력
- 2026-07-27: 시나리오 기반 매치 자동 실행을 반영했다. 7절(게임 실행)에 `+exec` 를 넣고,
  10절(실행 스크립트 설정)을 네 줄로 늘리고 맵 이름과 경계 보정의 관계를 명시했다.
  12절 체크리스트와 13절 자주 걸리는 문제에 시나리오 항목을 추가했다.
- 2026-07-24: 문서 최초 작성. 게임 설치·소스 빌드·배포와 파이썬·프론트엔드 환경 구성을 한곳에
  모았다. 기존에는 게임 쪽 절차가 `injection_map.md` 5-B 안에 있어 찾기 어려웠고, 그 절차는
  일반 OAX 기준(`fs_game oax`, pk3 묶기)이라 실제 사용 방식(`fs_game qa`, loose 파일,
  qagame 만 재빌드)과 달랐다. 이 문서가 실제 방식을 기준으로 한다.
