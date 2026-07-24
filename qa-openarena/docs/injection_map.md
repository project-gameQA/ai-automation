# injection_map.md — 결함 주입기 · 텔레메트리 훅 주입 지점 맵

## 0. 문서 작성 규칙
- 이 문서는 cvar 토글 결함 주입기와 텔레메트리 계측 훅을 심을 소스 코드 위치를 기록한다.
- 기존 기록은 삭제하지 않는다. 내용이 바뀌면 취소선과 `[제거 YYYY-MM-DD: 사유]`를 남기고, 새 내용을 아래에 `[추가 YYYY-MM-DD]`로 기록한다.
- 문서체는 평서형(~다)으로 통일한다.

## 0-1. 기준 소스와 주의
- 아래 행 번호는 ioquake3 upstream(github.com/ioquake/ioq3) 소스에서 직접 확인한 값이다.
- OpenArena 게임 소스는 이 코드의 포크다. 함수·구조체·상수 이름은 동일하지만 버전에 따라 행 번호가 다를 수 있으므로, OA 소스에서 같은 심볼로 grep해 재확인한다.

---

## 1. 게임 모듈 구조
- 게임 로직은 엔진과 분리된 별도 모듈이다. `code/game/` 아래에 `g_main.c`, `g_active.c`, `bg_pmove.c`, 봇 AI(`ai_*.c`) 등이 모여 있다.
- 이 모듈만 게임 공유 라이브러리(`BUILD_GAME_SO`)나 QVM(`BUILD_GAME_QVM`)으로 빌드한다. 엔진은 건드리지 않는다.
- 결함 주입기와 텔레메트리 훅은 모두 이 게임 모듈 안에 들어간다.

---

## 2. 주입 지점 (검증된 위치)

| 용도 | 파일 | 심볼 | 행(upstream) |
| --- | --- | --- | --- |
| cvar 등록 테이블 | code/game/g_main.c | `gameCvarTable[]` | 99 |
| cvar 등록 함수 | code/game/g_main.c | `G_RegisterCvars()` | 338 |
| 프레임 루프(엔티티 순회) | code/game/g_main.c | `G_RunFrame()` | 1785 |
| 봇/클라이언트 상태 갱신 | code/game/g_active.c | `ClientThink_real()` | 754 |
| 상태 구조체 | code/qcommon/q_shared.h | `playerState_t` | 1138 |
| └ 위치 필드 | code/qcommon/q_shared.h | `vec3_t origin` | 1145 |
| └ 속도 필드 | code/qcommon/q_shared.h | `vec3_t velocity` | 1146 |
| └ 상태 배열 | code/qcommon/q_shared.h | `int stats[MAX_STATS]` | 1191 |
| 체력 인덱스 | code/game/bg_public.h | `STAT_HEALTH` | 209 |

- 봇의 상태는 `ent->client->ps`(playerState_t)로 접근한다. 체력은 `ent->client->ps.stats[STAT_HEALTH]`다.
- 기존 cvar(`g_gravity`, `g_speed` 등)가 `gameCvarTable[]`에 등록된 방식을 그대로 흉내 내어 `qa_inject_*`를 추가한다.

---

## 3. 버그 유형별 주입 방법
`ClientThink_real()`(또는 `G_RunFrame`의 클라이언트 순회) 안에서, 대상 cvar가 켜져 있으면 아래처럼 상태를 오염시킨다.

- fell_through_floor: `ps.origin[2]`를 맵 바닥 훨씬 아래로 강제한다. 더 현실적으로는 접지 트레이스(ground trace)를 잠깐 건너뛰게 한다(품이 더 든다).
- out_of_bounds: `ps.origin`의 x/y를 맵 경계 밖으로 강제 이동한다.
- health_out_of_range: `ps.stats[STAT_HEALTH]`를 최대치 위로 설정한다.
- impossible_velocity: `ps.velocity`를 물리 상한 이상으로 설정한다.
- stuck: 봇의 이동 명령(usercmd)을 일정 시간 0으로 만들어 제자리에 묶는다.

각 주입은 `qa_inject_<유형>` cvar로 개별 토글한다. 발표 때 정해진 시점에 켜서 재현하고, 켠 사실을 알고 있으므로 정답이 붙은 평가 데이터로 활용한다.

---

## 4. 텔레메트리 훅 위치
- 계측 훅도 `ClientThink_real()`에 둔다. 매 틱 각 봇의 `ps`(위치·속도·체력 등)를 읽어 `StateSample` 형태로 밖으로 내보낸다.
- 즉 결함 주입기와 텔레메트리 훅은 같은 함수 층에 공존한다.

---

## 5. 빌드·적용 요약

### ~~5-A. (구) 네이티브 DLL 방식 (Windows / MinGW)~~ `[제거 2026-07-20: QVM 모드 방식으로 대체]`
1. ~~MSYS2를 설치하고 mingw-w64 툴체인(gcc, make)을 준비한다.~~
2. ~~OpenArena 게임 소스(`code/game`과 Makefile을 포함하는 배포본)를 받는다.~~
3. ~~게임 모듈만 빌드한다: `make BUILD_GAME_SO=1`(변수명은 소스의 Makefile에서 확인한다).~~
4. ~~산출물 `qagame<arch>.dll`을 OpenArena 설치 폴더의 `baseoa/`(또는 `+set fs_game`로 지정한 모드 폴더)에 넣는다.~~
5. ~~콘솔에서 `vm_game 0`으로 두어 pk3의 기본 QVM 대신 내가 빌드한 네이티브 DLL을 로드한다.~~

~~주의: OpenArena 0.8.8은 오래된 코드(2012)라 최신 MinGW로 빌드할 때 소소한 수정이 필요할 수 있다.~~

폐기 사유: (1) MSYS2가 32비트 툴체인을 사실상 정리해, 32비트인 스톡 OA 0.8.8 엔진에 맞출 네이티브 DLL 빌드 경로가 막혔다. (2) 네이티브 DLL은 엔진과 아키텍처를 맞춰야 하고, 64비트 엔진을 별도로 확보/빌드해야 한다. (3) 최신 gcc로 2012년 코드를 빌드하면 추가 수정이 필요하다.

### 5-B. (현) QVM 모드 방식 `[추가 2026-07-20]`
기준 소스: `OpenArena/gamecode`(github.com/OpenArena/gamecode). OA 게임 코드가 QVM 모드(OpenArenaExpanded, OAX) 빌드용으로 구성돼 있고, Windows용 컴파일 도구와 배치 스크립트가 동봉돼 있다.

이 방식이 나은 이유는 다음과 같다.
- QVM은 아키텍처 독립 바이트코드라 엔진 32/64비트와 무관하게 로드된다. 스톡 OA 0.8.8을 그대로 쓴다. 64비트 엔진을 따로 구할 필요가 없다.
- 빌드에 gcc가 아니라 동봉된 옛 id 컴파일러(lcc/q3asm)를 쓰므로, 최신 gcc와 2012년 코드의 충돌 문제가 없다. 배치 스크립트는 cmd에서 실행되므로 이 경로만 놓고 보면 MSYS2/gcc는 필수가 아니다.

절차:
1. OpenArena 0.8.8을 설치한다(게임 데이터+엔진, 아키텍처 무관).
2. `git clone https://github.com/OpenArena/gamecode` 로 게임 소스를 받는다.
3. `windows_scripts\windows_compile_game.bat`를 실행한다. 동봉된 `lcc.exe`/`q3cpp.exe`/`q3rcc.exe`/`q3asm.exe`가 게임 C 소스를 `qagame.qvm`으로 컴파일해 `windows\baseoa\vm\`에 넣는다. (필요 시 `windows_compile_cgame.bat`, `windows_compile_q3_ui.bat`도 실행한다.)
4. ~~생성된 `vm\*.qvm`을 `oax.pk3`로 묶는다(동봉된 `zip.exe` 또는 `git-bash-compile.bash` 사용).~~ `[제거 2026-07-24: 실제로는 pk3로 묶지 않았다]`
5. ~~`oax.pk3`(또는 loose `vm\qagame.qvm`)를 OA 설치 폴더의 `oax` 모드 폴더에 넣고, `+set fs_game oax +set sv_pure 0`으로 실행한다. baseoa에 직접 넣을 경우 pk3 로드 순서 때문에 이름을 뒤로 정렬되게(예: `zzz-oax.pk3`) 지어 기본 pak을 덮어쓰게 한다.~~ `[제거 2026-07-24: 모드 폴더 이름과 배포 방식이 실제와 다르다]`

`[추가 2026-07-24]` **실제로 채택한 방식**은 다음과 같다. 위 4~5번은 일반 OAX 배포 절차를 그대로 옮긴 것이라 실제 사용 방식과 어긋난 채 남아 있었다.

4. 생성된 `vm\*.qvm`을 **pk3로 묶지 않고** 모드 폴더에 그대로 둔다.

       C:\game\openarena-0.8.8\qa\vm\qagame.qvm
                                    \cgame.qvm
                                    \ui.qvm

5. `+set fs_game qa +set sv_pure 0`으로 실행한다. 모드 폴더 이름이 `oax`가 아니라 `qa`이며, `sv_pure 0`이 loose 파일 로드를 허용하므로 pk3로 묶을 필요가 없다.

**최초 1회만 세 모듈을 모두 빌드하고, 이후에는 `qagame`만 재빌드해 덮어쓴다.** 계측과 주입기는 전부 qagame에만 들어가므로 `cgame`·`ui`는 건드릴 일이 없다. 이 반복 작업은 배치 파일로 자동화했다.

전체 세팅 절차(게임 설치부터 파이썬·프론트엔드 환경까지)는 **`docs/setup.md`**에 따로 정리했다. 이 문서는 주입 지점 지도이므로, 처음 세팅하는 경우 그쪽을 본다.

결함 주입·계측 시: 위 2절의 주입 지점(`g_active.c`의 `ClientThink_real`, `g_main.c`의 cvar 테이블)을 수정하고 3~5절을 다시 수행한다. QVM에서는 파일 입출력을 직접 할 수 없으므로, 텔레메트리 출력은 엔진 syscall(`trap_FS_FOpenFileByMode`/`trap_FS_Write`)이나 콘솔/로그를 통한다.

주의: 위 2절 행 번호는 ioquake3 upstream 기준이다. OA `gamecode`의 `g_active.c`/`g_main.c`에서 같은 심볼로 재확인한다.

---

## 6. 변경 이력
- 2026-07-20: 문서 최초 작성. ioquake3 upstream에서 확인한 주입 지점 맵과 Windows 빌드 요약을 기록했다.
- 2026-07-20: 빌드·적용 방식을 네이티브 DLL(MinGW)에서 QVM 모드(OA gamecode, OAX)로 변경했다. 구 방식은 5-A에 취소선으로 보존하고, 신 방식을 5-B에 추가했다.
- 2026-07-24: 5-B의 4~5번이 일반 OAX 배포 절차(`fs_game oax`, pk3 묶기) 그대로였고 실제 사용 방식(`fs_game qa`, loose 파일, qagame만 재빌드)과 어긋나 있어 정정했다. 전체 세팅 절차는 `docs/setup.md`로 분리했다.
