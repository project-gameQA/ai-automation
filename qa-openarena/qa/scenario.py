"""테스트 시나리오를 읽어 게임이 실행할 설정 파일(cfg)로 바꾸는 모듈이다.

무엇을 푸는가:
`run_qa.bat` 은 게임을 띄우기만 했다. 게임은 메인 메뉴에서 멈추고, 사람이 맵을 고르고 봇을
넣어야 매치가 시작됐다. 그러면 매 실행이 조금씩 달라지고, 나중에 세션 파일만 보고 "이 결과는
어떤 조건에서 나왔는가"를 재구성할 수 없다. 검출 설정값을 `qa/config.py` 한곳에 모은 것과
같은 이유로, **매치 조건도 파일 하나에 적어 두고 그것을 실행**해야 한다.

왜 게임 cfg 인가:
게임을 밖에서 조종하는 방법은 두 가지다. 하나는 시작할 때 명령을 넘기는 것(cfg), 다른 하나는
실행 중에 명령을 보내는 것(rcon)이다. 여기서는 앞의 것만 쓴다. 시작 조건을 정하는 데는 그것으로
충분하고, 네트워크 채널을 추가하지 않아 실패 지점이 늘지 않는다. 매치 도중에 시각을 정해
결함을 주입하는 일은 cfg 로 할 수 없으므로 rcon 이 필요하고, 그 확장은 `docs/scenario.md` 에
설계만 적어 둔다.

생성물은 두 파일이다:
- `qa_match.cfg` : cvar 설정 → `map` → 대기 → 봇 투입 → 관전 전환까지의 전체 절차
- `qa_bots.cfg`  : `addbot` 줄만 모은 파일

봇을 따로 뺀 이유는 복구 경로 때문이다. 봇 투입은 이 절차에서 유일하게 타이밍에 의존하는
부분이라(아래 '대기' 설명 참조) 드물게 실패할 수 있는데, 그때 콘솔에서 `\\exec qa_bots.cfg`
한 줄로 다시 시도할 수 있어야 한다. 파일이 하나로 합쳐져 있으면 맵부터 다시 로드된다.

`qa_match.cfg` 는 스스로 완결되게 만든다. 즉 게임을 켜 둔 채 콘솔에서 `\\exec qa_match.cfg`
를 치면 같은 매치가 처음부터 다시 시작된다. 시나리오를 바꿔 가며 반복 시험할 때 게임을
껐다 켤 필요가 없다.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field

# ── 게임 쪽 상수 ───────────────────────────────────────────────────────────

# 게임 타입 이름과 번호의 대응이다. 번호는 게임 소스에 박혀 있고 배포판마다 뒤쪽이 다르므로,
# 여러 배포판에서 값이 같다고 확인된 앞쪽 다섯 개만 이름으로 받는다. 그 밖의 모드는 시나리오에
# 숫자를 직접 적게 한다. 모르는 이름을 조용히 0(FFA)으로 떨어뜨리면 엉뚱한 모드로 시험하게 된다.
GAMETYPES = {
    "ffa": 0,          # 개인전
    "tournament": 1,   # 1대1 토너먼트
    "single": 2,       # 싱글 플레이
    "team": 3,         # 팀 데스매치
    "ctf": 4,          # 깃발 뺏기
}

# 결함 주입 cvar 4종이다. 이름은 `docs/gamecode_instrumentation.md` 2.2~2.16 에서 정한 것이다.
INJECTORS = {
    "health": "qa_inject_health",   # 체력을 999로 고정
    "oob": "qa_inject_oob",         # x 좌표를 맵 밖으로 강제
    "fall": "qa_inject_fall",       # 콜리전 제거로 바닥 관통
    "stuck": "qa_inject_stuck",     # 위치를 한 점에 고정(이동 입력은 유지)
}

# 시나리오의 용도다. 이상탐지(②)의 출력을 어떻게 읽어야 하는지가 여기서 갈린다.
#
# 이 구분이 필요한 이유가 있다. ② 오라클은 "정상 플레이"를 학습하고 거기서 벗어난 정도를
# 점수로 낸다. 그 "정상"은 **학습에 쓴 조건**을 뜻하지 게임의 절대적 정상 상태가 아니다.
# 봇 실력을 낮춘 조건에서 ②가 100% 이상을 잡는다면 그것은 게임에 버그가 100건 있다는 뜻이
# 아니라 조건이 학습 때와 다르다는 뜻이며, 실제로 그 성질을 이용해 ②를 검증했다.
# 그런데 세션 요약에는 이상 건수만 남으므로, 나중에 파일만 보면 둘을 구분할 수 없다.
PURPOSES = {
    # 이상탐지의 '정상'을 정의하는 조건. 학습과 대조군에 쓴다. 결함 주입이 있으면 안 된다.
    "baseline": "기준 (학습·대조군)",
    # 조건을 의도적으로 흔든 시험지. 오라클이 반응하는지 보는 것이 목적이다.
    # 여기서 나온 이상 판정은 버그 신호가 아니라 시험 통과 신호로 읽는다.
    "probe": "시험지 (오라클 검증)",
    # 실제로 버그를 찾는 운용 조건. 이상 판정이 조사 대상이다.
    "hunt": "탐색 (실제 버그 찾기)",
}

# 봇을 넣을 팀 이름이다. 팀 게임이 아니면 게임이 알아서 free 로 취급한다.
TEAMS = ("free", "red", "blue")

# cvar 이름으로 허용할 형태다. 이 이름은 따옴표 없이 cfg 에 그대로 나가므로 공백이나 특수문자가
# 섞이면 명령줄이 깨진다.
CVAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 키 이름으로 허용할 형태다. F5, KP_HOME, MOUSE3 같은 이름이 여기에 들어온다.
KEY_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

# 시나리오 파일 최상단에 허용하는 절 이름이다. 오타를 잡기 위해 목록을 고정한다.
SECTIONS = {"match", "roster", "cvars", "inject", "keys", "player", "timing"}


class ScenarioError(Exception):
    """시나리오를 읽거나 검증하는 중에 생긴, 실행을 막아야 하는 오류다."""


# ── 데이터 구조 ────────────────────────────────────────────────────────────

@dataclass
class Bot:
    """봇 한 마리의 투입 조건이다."""

    name: str            # `addbot` 에 넘길 봇 이름. 게임의 봇 목록에 있어야 한다.
    skill: float = 3.0   # 실력. 1이 가장 약하고 5가 가장 세다.
    team: str = "free"   # 팀. 팀 게임이 아니면 무시된다.

    def to_dict(self) -> dict:
        return {"name": self.name, "skill": self.skill, "team": self.team}


@dataclass
class Scenario:
    """시나리오 파일 하나를 그대로 담은 구조다.

    필드를 평평하게 늘어놓는다. 절 단위로 중첩 객체를 만들면 값 하나를 읽는 데 두 단계를
    거쳐야 하고, 이 구조가 하는 일은 결국 cfg 한 장을 찍어내는 것뿐이라 중첩이 값을 하지 못한다.
    """

    # 사람이 알아보기 위한 이름과 메모다. 세션 요약에 그대로 실린다.
    name: str = "이름 없는 시나리오"
    notes: str = ""

    # 이 조건의 용도다. baseline / probe / hunt 중 하나이며 기본은 hunt 다.
    # 기본값을 hunt 로 둔 이유: 용도를 적지 않은 시나리오는 대개 "그냥 돌려 보며 버그를 찾는"
    # 조건이고, 그 경우 ②의 이상 판정은 조사 대상이라는 평범한 해석이 맞다. baseline 을
    # 기본값으로 두면 주입이 켜진 시나리오가 오류로 막히고, probe 를 기본값으로 두면
    # 진짜 버그 신호를 "시험 통과"로 잘못 읽게 된다.
    purpose: str = "hunt"

    # [match] 매치 조건
    map: str = ""                 # 필수. 로드할 맵 이름
    gametype: int = 0             # 게임 타입 번호
    maxclients: int = 16          # 서버가 받을 최대 인원(봇 포함). 봇 수보다 커야 한다
    fraglimit: int = 0            # 0이면 점수로 매치가 끝나지 않는다
    timelimit: int = 0            # 0이면 시간으로 매치가 끝나지 않는다
    warmup: int = 0               # g_doWarmup. 준비 시간 동안 봇이 얼어 있어 기본은 끈다

    # [roster] 봇 구성
    bots: list = field(default_factory=list)  # Bot 목록. resolve() 가 채운다
    count: int = 0                # 봇 수. bots 를 직접 적지 않았을 때 이 수만큼 자동으로 채운다
    skill: float = 3.0            # 자동으로 채울 때 쓸 기본 실력
    team: str = "free"            # 자동으로 채울 때 쓸 기본 팀
    keep_filled: bool = False     # 봇이 빠지면 서버가 자동으로 다시 채울지(bot_minplayers)
    spawn_stagger_ms: int = 250   # 봇을 한 마리씩 띄울 간격(밀리초)

    # [cvars] 실험용 cvar. g_gravity, g_knockback, g_spSkill 등 무엇이든 들어올 수 있다
    cvars: dict = field(default_factory=dict)

    # [inject] 매치 시작 시점의 결함 주입기 상태
    inject: dict = field(default_factory=dict)

    # [keys] 시연 중에 손으로 켜고 끌 키 바인딩
    keys: dict = field(default_factory=dict)

    # [player] 사람 쪽 설정
    spectator: bool = True        # 사람은 관전만 한다. 주입기가 사람에게도 걸리므로 기본값이다

    # [timing] 대기 프레임 수
    wait_frames: int = 150        # map 이후 봇을 넣기까지 기다릴 프레임 수
    spectator_wait_frames: int = 60  # 봇 투입 이후 관전으로 돌리기까지 기다릴 프레임 수

    # 원본 파일 경로. 기록용이며 cfg 에는 주석으로만 들어간다
    source: str = ""

    def to_dict(self) -> dict:
        """세션 요약에 남길 형태로 반환한다.

        `qa/config.py` 의 `as_dict()` 와 같은 목적이다. 판정 설정만 남기고 매치 조건을 안 남기면
        "이 결과가 어떤 조건에서 나왔는가"의 절반이 비어 있게 된다.
        """
        return {
            "name": self.name,
            "notes": self.notes,
            "purpose": self.purpose,
            "source": self.source,
            "match": {
                "map": self.map,
                "gametype": self.gametype,
                "maxclients": self.maxclients,
                "fraglimit": self.fraglimit,
                "timelimit": self.timelimit,
                "warmup": self.warmup,
            },
            "roster": {
                "bots": [b.to_dict() for b in self.bots],
                "keep_filled": self.keep_filled,
                "spawn_stagger_ms": self.spawn_stagger_ms,
            },
            "cvars": dict(self.cvars),
            "inject": {k: bool(v) for k, v in self.inject.items()},
            "keys": dict(self.keys),
            "spectator": self.spectator,
        }


# ── 읽기 ───────────────────────────────────────────────────────────────────

def _load_toml(path: str) -> dict:
    """TOML 파일을 딕셔너리로 읽는다.

    TOML 을 고른 이유는 주석을 달 수 있고 표준 라이브러리로 읽히기 때문이다. 시나리오는
    사람이 손으로 쓰고 왜 그 값인지 옆에 적어 두는 파일이라 주석이 없으면 곤란한데, JSON 에는
    주석이 없다. `tomllib` 은 파이썬 3.11부터 표준이므로 새 의존성도 생기지 않는다.
    """
    try:
        import tomllib                     # 파이썬 3.11+ 표준 라이브러리다.
    except ModuleNotFoundError:
        try:
            import tomli as tomllib        # 3.10 이하에서는 같은 API 의 외부 패키지를 쓴다.
        except ModuleNotFoundError as exc:
            raise ScenarioError(
                "TOML 을 읽을 수 없다. 파이썬 3.11 이상을 쓰거나 `pip install tomli` 를 한다."
            ) from exc
    try:
        with open(path, "rb") as f:        # tomllib 은 바이트 모드만 받는다(인코딩을 스스로 판단한다).
            return tomllib.load(f)
    except FileNotFoundError as exc:
        raise ScenarioError(f"시나리오 파일이 없다: {path}") from exc
    except Exception as exc:               # TOML 문법 오류는 메시지에 줄 번호가 들어 있다.
        raise ScenarioError(f"시나리오 파일을 읽지 못했다: {path}\n  {exc}") from exc


def _as_gametype(value) -> int:
    """게임 타입을 이름이나 숫자로 받아 번호로 바꾼다."""
    if isinstance(value, bool):            # bool 은 int 의 하위형이라 먼저 걸러야 한다.
        raise ScenarioError("gametype 에 true/false 를 쓸 수 없다.")
    if isinstance(value, int):
        return value                       # 표에 없는 모드를 숫자로 지정하는 경로다.
    key = str(value).strip().lower()
    if key not in GAMETYPES:
        known = ", ".join(sorted(GAMETYPES))
        raise ScenarioError(
            f"모르는 gametype 이다: {value!r}\n"
            f"  이름으로 쓸 수 있는 값: {known}\n"
            f"  그 밖의 모드는 게임의 g_gametype 번호를 숫자로 적는다."
        )
    return GAMETYPES[key]


def load(path: str) -> Scenario:
    """시나리오 파일을 읽어 Scenario 로 만든다. 봇 목록은 아직 확정되지 않는다.

    이름을 실제 설치본과 대조하는 일은 `resolve()` 와 `validate()` 가 맡는다. 읽기와 검증을
    나눈 이유는, 게임 설치 경로를 모르는 상황에서도 시나리오 문법만은 확인할 수 있어야 하기
    때문이다.
    """
    data = _load_toml(path)

    unknown = set(k for k in data if isinstance(data[k], dict)) - SECTIONS
    if unknown:
        # 절 이름 오타는 조용히 무시되면 아주 찾기 어렵다. `[roseter]` 라고 쓰면 봇이 하나도
        # 안 들어간 채로 매치가 시작되고, 원인은 게임 쪽 어디에도 남지 않는다.
        raise ScenarioError(
            f"모르는 절이 있다: {', '.join(sorted(unknown))}\n"
            f"  쓸 수 있는 절: {', '.join(sorted(SECTIONS))}"
        )

    match = data.get("match", {})
    roster = data.get("roster", {})
    player = data.get("player", {})
    timing = data.get("timing", {})

    s = Scenario()
    s.source = path
    s.name = str(data.get("name", s.name))
    s.notes = str(data.get("notes", ""))
    s.purpose = str(data.get("purpose", s.purpose)).strip().lower()

    s.map = str(match.get("map", "")).strip()
    s.gametype = _as_gametype(match.get("gametype", 0))
    s.maxclients = int(match.get("maxclients", s.maxclients))
    s.fraglimit = int(match.get("fraglimit", s.fraglimit))
    s.timelimit = int(match.get("timelimit", s.timelimit))
    s.warmup = int(match.get("warmup", s.warmup))

    s.count = int(roster.get("count", 0))
    s.skill = float(roster.get("skill", s.skill))
    s.team = str(roster.get("team", s.team)).lower()
    s.keep_filled = bool(roster.get("keep_filled", False))
    s.spawn_stagger_ms = int(roster.get("spawn_stagger_ms", s.spawn_stagger_ms))

    # 봇을 하나씩 적은 경우다. 적지 않은 값은 roster 의 기본값을 물려받는다.
    for entry in roster.get("bots", []):
        if "name" not in entry:
            raise ScenarioError("[[roster.bots]] 항목에 name 이 없다.")
        s.bots.append(Bot(
            name=str(entry["name"]).strip(),
            skill=float(entry.get("skill", s.skill)),
            team=str(entry.get("team", s.team)).lower(),
        ))

    s.cvars = dict(data.get("cvars", {}))
    s.inject = {k: bool(v) for k, v in data.get("inject", {}).items()}
    s.keys = {str(k): str(v) for k, v in data.get("keys", {}).items()}

    s.spectator = bool(player.get("spectator", s.spectator))

    s.wait_frames = int(timing.get("wait_frames", s.wait_frames))
    s.spectator_wait_frames = int(timing.get("spectator_wait_frames", s.spectator_wait_frames))
    return s


# ── 봇 목록 확정 ───────────────────────────────────────────────────────────

def resolve(scenario: Scenario, bot_pool=None) -> list:
    """`count` 만큼 봇을 채워 넣고, 그 과정에서 생긴 경고를 반환한다.

    봇 이름을 이 모듈에 적어 두지 않고 **설치본에서 읽어 온 목록(bot_pool)에서 고르는** 것이
    핵심이다. 배포판과 모드에 따라 들어 있는 봇이 다르므로, 코드에 이름을 박아 두면 다른
    환경에서 통째로 어긋난다. 목록을 못 읽었으면 자동 채우기를 포기하고 사용자가 이름을 직접
    적도록 안내한다. 없는 이름을 지어내 넣으면 게임이 조용히 그 봇을 건너뛴다.
    """
    warnings: list[str] = []
    need = scenario.count - len(scenario.bots)
    if need <= 0:
        return warnings

    if not bot_pool:
        raise ScenarioError(
            f"[roster] count = {scenario.count} 인데 게임에서 봇 이름 목록을 읽지 못했다.\n"
            f"  지금 바로 넘어가려면 [[roster.bots]] 에 이름을 직접 적고 count 를 지운다.\n"
            f"  이름은 게임의 Multiplayer > Add Bot 메뉴에서 확인할 수 있다.\n"
            f"\n"
            f"      [[roster.bots]]\n"
            f"      name = \"Ayumi\"\n"
            f"      skill = 3\n"
            f"\n"
            f"  조사가 왜 실패했는지는 아래로 본다. 봇 목록 파일을 못 찾은 것인지,\n"
            f"  찾았는데 이름 표기가 달라 못 읽은 것인지가 나온다.\n"
            f"      python tools/apply_scenario.py --list --game <게임 설치 폴더>"
        )

    used = {b.name.lower() for b in scenario.bots}
    # 정렬해서 고른다. 무작위로 고르면 같은 시나리오 파일이 실행할 때마다 다른 봇 조합을 내는데,
    # 그러면 두 세션의 차이가 시나리오 때문인지 봇 조합 때문인지 구분할 수 없다.
    pool = [n for n in sorted(bot_pool) if n.lower() not in used]
    if len(pool) < need:
        warnings.append(
            f"봇을 {scenario.count}마리 요청했으나 설치본에 쓸 수 있는 이름이 "
            f"{len(pool) + len(scenario.bots)}개뿐이라 그만큼만 넣는다."
        )
    for name in pool[:need]:
        scenario.bots.append(Bot(name=name, skill=scenario.skill, team=scenario.team))
    return warnings


# ── 검증 ───────────────────────────────────────────────────────────────────

def validate(scenario: Scenario, maps=None, bots=None) -> tuple[list, list]:
    """시나리오를 검사해 (오류, 경고) 두 목록을 반환한다.

    오류는 실행을 막는 것이고, 경고는 실행은 되지만 결과를 왜곡할 수 있는 것이다. 둘을 나누는
    기준은 "게임이 이 시나리오를 그대로 수행할 수 있는가"다. 예를 들어 `timelimit` 이 0이 아니면
    게임은 아무 문제 없이 돌지만 매치가 끝나면서 텔레메트리가 지워지므로 경고에 해당한다.

    maps 와 bots 는 설치본에서 읽어 온 이름 목록이다. **비어 있으면 이름 검사를 건너뛴다.**
    조사 자체가 실패한 것(경로를 모르거나 pk3 를 못 읽음)과 이름이 정말 없는 것을 구분해야
    하는데, 목록이 비었다면 앞의 경우로 보는 것이 안전하다. 실제로 있는 이름을 없다고 막으면
    사용자는 도구를 우회할 방법이 없다.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # ── 맵 ──
    if not scenario.map:
        errors.append("[match] map 이 비어 있다. 로드할 맵 이름을 적는다.")
    elif maps and scenario.map not in maps:
        # 오타를 잡아 주기 위해 비슷한 이름을 몇 개 보여 준다.
        similar = [m for m in sorted(maps) if scenario.map.lower() in m.lower()][:8]
        hint = f"\n  비슷한 이름: {', '.join(similar)}" if similar else ""
        errors.append(
            f"맵 '{scenario.map}' 을 설치본에서 찾지 못했다.{hint}\n"
            f"  전체 목록은 --list 로 본다."
        )

    # ── 인원 ──
    if scenario.maxclients <= len(scenario.bots):
        # 사람 클라이언트가 한 자리를 쓰므로 봇 수보다 최소 하나는 커야 한다.
        errors.append(
            f"[match] maxclients = {scenario.maxclients} 인데 봇이 {len(scenario.bots)}마리다. "
            f"사람 자리까지 있어야 하므로 봇 수보다 크게 잡는다."
        )
    if not scenario.bots:
        warnings.append("봇이 한 마리도 없다. 텔레메트리는 봇만 기록하므로 아무 데이터도 쌓이지 않는다.")

    # ── 봇 ──
    for bot in scenario.bots:
        if not 1.0 <= bot.skill <= 5.0:
            errors.append(f"봇 '{bot.name}' 의 skill = {bot.skill} 이 범위를 벗어났다(1~5).")
        if bot.team not in TEAMS:
            errors.append(f"봇 '{bot.name}' 의 team = '{bot.team}' 은 쓸 수 없다({', '.join(TEAMS)}).")
        if bots and bot.name not in bots:
            similar = [b for b in sorted(bots) if bot.name.lower() in b.lower()][:8]
            hint = f"\n  비슷한 이름: {', '.join(similar)}" if similar else ""
            errors.append(f"봇 '{bot.name}' 을 설치본에서 찾지 못했다.{hint}")
    if scenario.gametype in (3, 4) and all(b.team == "free" for b in scenario.bots) and scenario.bots:
        warnings.append("팀 게임인데 봇을 모두 free 로 넣었다. 게임이 팀을 임의로 배정한다.")

    # ── 매치가 끝나면 안 되는 이유 ──
    # docs/setup.md 13절에 적힌 실제 사고다. 값이 0이 아니면 매치가 끝나면서 게임이 텔레메트리를
    # FS_WRITE 로 새로 열어 그 판의 기록이 사라지고, 점수판 구간이 이상탐지 오탐이 된다.
    if scenario.fraglimit != 0:
        warnings.append(
            f"[match] fraglimit = {scenario.fraglimit}. 매치가 끝나면 텔레메트리가 지워지고 "
            f"점수판 구간이 이상탐지 오탐이 된다. 0을 권한다."
        )
    if scenario.timelimit != 0:
        warnings.append(
            f"[match] timelimit = {scenario.timelimit}. fraglimit 과 같은 이유로 0을 권한다."
        )
    if scenario.warmup:
        warnings.append(
            f"[match] warmup = {scenario.warmup}. 준비 시간 동안 봇이 움직이지 않아 그 구간이 "
            f"이상탐지에 비정상으로 보인다."
        )

    # ── 용도 ──
    if scenario.purpose not in PURPOSES:
        errors.append(
            f"모르는 purpose 다: {scenario.purpose!r}\n"
            f"  쓸 수 있는 값: {', '.join(f'{k}({v})' for k, v in PURPOSES.items())}"
        )
    elif scenario.purpose == "baseline":
        # 기준 조건은 이상탐지가 '정상'으로 배울 대상이다. 여기에 결함이 섞이면 모델이
        # 그 결함을 정상으로 배우고, 이후 진짜 정상 플레이를 이상으로 판정하게 된다.
        on = [k for k in sorted(INJECTORS) if scenario.inject.get(k)]
        if on:
            errors.append(
                f"purpose = baseline 인데 결함 주입이 켜져 있다: {', '.join(on)}\n"
                f"  기준 조건은 이상탐지가 '정상'으로 배울 대상이므로 주입이 섞이면 안 된다.\n"
                f"  시험지로 쓰려면 purpose 를 probe 로 바꾼다."
            )
        if scenario.cvars:
            # 오류가 아니라 경고인 이유: 기준 조건을 무엇으로 잡을지는 사용자의 선택이다.
            # 다만 학습과 채점에서 같은 값을 써야 한다는 점을 놓치기 쉽다.
            warnings.append(
                f"purpose = baseline 인데 [cvars] 가 있다. 여기에 적은 값이 곧 '정상'의 정의가 "
                f"되므로, 학습과 채점에 같은 값을 써야 한다."
            )

    # ── 주입기 ──
    for key in scenario.inject:
        if key not in INJECTORS:
            errors.append(
                f"[inject] 에 모르는 항목이 있다: {key}\n"
                f"  쓸 수 있는 항목: {', '.join(sorted(INJECTORS))}"
            )
    if scenario.inject.get("oob") and scenario.inject.get("stuck"):
        # gamecode_instrumentation.md 4절에 기록된 알려진 충돌이다. 둘 다 위치를 조작한다.
        errors.append("[inject] oob 와 stuck 은 둘 다 위치를 조작하므로 동시에 켤 수 없다.")
    if scenario.inject.get("fall"):
        warnings.append(
            "[inject] fall 이 켜져 있다. 봇이 맵 아래로 무한히 떨어져 사건이 닫히지 않고 "
            "탐지 프레임 수가 계속 올라간다."
        )
    if any(scenario.inject.values()) and not scenario.spectator:
        # 주입기는 아직 봇 한정이 아니다(gamecode_instrumentation.md 4절).
        warnings.append(
            "주입기가 켜져 있는데 [player] spectator = false 다. 주입기는 사람에게도 걸린다."
        )

    # ── cvar ──
    for name in scenario.cvars:
        if not CVAR_NAME_RE.match(str(name)):
            errors.append(f"[cvars] 이름으로 쓸 수 없는 값이다: {name!r}")
        if str(name) in INJECTORS.values():
            warnings.append(
                f"[cvars] {name} 은 [inject] 로 적는 편이 낫다. 둘 다 적으면 [inject] 가 나중에 적용된다."
            )
    for name, command in scenario.keys.items():
        if not KEY_NAME_RE.match(name):
            errors.append(f"[keys] 키 이름으로 쓸 수 없는 값이다: {name!r}")
        if '"' in command:
            errors.append(f"[keys] {name} 의 명령에 큰따옴표가 들어 있어 cfg 가 깨진다.")

    if scenario.wait_frames < 1:
        errors.append("[timing] wait_frames 는 1 이상이어야 한다. 맵 로드 직후에는 봇을 넣을 수 없다.")

    return errors, warnings


# ── cfg 생성 ───────────────────────────────────────────────────────────────

def _quote(value) -> str:
    """cvar 값을 cfg 에 넣을 형태로 바꾼다.

    항상 큰따옴표로 감싼다. 값에 공백이 들어가면 감싸지 않은 쪽이 깨지고, 감싸서 깨지는 경우는
    없기 때문이다. bool 은 게임에 그런 타입이 없으므로 1과 0으로 바꾼다.
    """
    if isinstance(value, bool):
        value = 1 if value else 0
    text = str(value)
    if '"' in text or "\n" in text:
        raise ScenarioError(f"cvar 값에 큰따옴표나 줄바꿈을 쓸 수 없다: {value!r}")
    return f'"{text}"'


def _wait_block(frames: int) -> list:
    """지정한 프레임 수만큼 기다리는 줄들을 만든다.

    `wait 150` 이라고 한 줄로 쓰지 않고 `wait` 를 그 수만큼 늘어놓는다. 인자를 받는 `wait` 는
    엔진 갈래에 따라 있기도 없고, 없는 쪽에서는 인자를 무시하고 한 프레임만 기다린다. 그러면
    실패가 조용하다. 봇이 안 들어오는데 cfg 는 멀쩡해 보인다. `wait` 한 줄이 한 프레임이라는
    동작은 어느 갈래에나 있으므로, 줄 수로 세면 어디서든 같게 동작한다.

    한 줄에 여러 개를 세미콜론으로 이어 붙인다. 게임은 세미콜론도 명령 구분자로 보므로 동작은
    같고, 150줄짜리 wait 덩어리가 되어 사람이 파일을 읽기 어려워지는 것만 막는다.
    """
    per_line = 25
    lines = []
    remaining = frames
    while remaining > 0:
        chunk = min(per_line, remaining)
        lines.append(";".join(["wait"] * chunk))
        remaining -= chunk
    return lines


def to_bots_cfg(scenario: Scenario) -> str:
    """`addbot` 줄만 담은 cfg 를 문자열로 만든다."""
    out = [
        "// qa_bots.cfg — 이 파일은 tools/apply_scenario.py 가 생성한다. 직접 고치면 다음 실행에서 덮어써진다.",
        f"// 시나리오: {scenario.name}",
        "//",
        "// addbot <이름> <실력 1~5> <팀> <지연 ms>",
        "// 마지막의 지연은 봇이 실제로 스폰하기까지의 시간이다. 한 프레임에 여러 마리가 함께",
        "// 스폰하면 서버가 순간적으로 밀리므로 한 마리씩 시차를 둔다.",
        "//",
        "// 봇이 들어오지 않으면 게임 콘솔에서 이 파일만 다시 실행한다: \\exec qa_bots.cfg",
        "",
    ]
    if not scenario.bots:
        out.append("// 이 시나리오에는 봇이 없다.")
        return "\n".join(out) + "\n"

    for index, bot in enumerate(scenario.bots):
        # 첫 봇에도 최소한의 지연을 준다. 0을 주면 게임이 대기열을 거치지 않고 그 자리에서
        # 스폰시키는데, 맵이 막 올라온 직후라 다른 초기화와 겹칠 여지가 있다.
        delay = scenario.spawn_stagger_ms * (index + 1)
        # 실력은 소수점 한 자리까지만 적는다. 게임은 실수를 받지만 1~5 사이의 정수로 쓰는 값이라
        # 자릿수를 늘려 봐야 읽기만 나빠진다.
        out.append(f"addbot {bot.name} {bot.skill:g} {bot.team} {delay}")
    return "\n".join(out) + "\n"


def _heading(step: list, title: str) -> str:
    """cfg 의 구역 제목 줄을 만든다.

    번호를 상수로 박지 않고 세어 붙인다. 시나리오에 따라 있는 구역과 없는 구역이 갈리는데
    (cvar 를 안 적었거나 관전을 끈 경우), 번호를 박아 두면 생성된 파일에서 번호가 건너뛴다.
    사람이 그 파일을 읽으며 "3번은 어디 갔나" 를 생각하게 만들 이유가 없다.
    """
    step[0] += 1
    # 제목 뒤를 선으로 채워 폭을 맞춘다. 고정폭 글꼴로 볼 때 구역이 눈에 들어온다.
    # 글자 수가 아니라 화면 폭으로 세는 이유는 한글과 괘선 문자가 두 칸을 차지하기 때문이다.
    # 글자 수로 세면 한글이 많은 제목일수록 선이 길어져 줄 끝이 들쭉날쭉해진다.
    line = f"// ── {step[0]}. {title} "
    width = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in line)
    return line + "─" * max(2, (78 - width) // 2)


def to_match_cfg(scenario: Scenario, bots_cfg_name: str = "qa_bots.cfg") -> str:
    """매치 전체 절차를 담은 cfg 를 문자열로 만든다."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    step = [0]  # 구역 번호 카운터. 리스트에 담아 _heading 이 값을 늘릴 수 있게 한다.
    out = [
        "// qa_match.cfg — 이 파일은 tools/apply_scenario.py 가 생성한다. 직접 고치면 다음 실행에서 덮어써진다.",
        f"// 시나리오: {scenario.name}",
        f"// 용도: {PURPOSES.get(scenario.purpose, scenario.purpose)}",
        f"// 원본: {scenario.source}",
        f"// 생성: {stamp}",
    ]
    if scenario.notes:
        # 메모는 여러 줄일 수 있으므로 줄마다 주석 표시를 붙인다.
        out.append("//")
        for line in scenario.notes.strip().splitlines():
            out.append(f"// {line}")
    out += [
        "//",
        "// 게임을 켜 둔 채 콘솔에서 \\exec qa_match.cfg 를 치면 이 매치가 처음부터 다시 시작된다.",
        "",
        _heading(step, "맵을 로드하기 전에 정해져야 하는 값"),
        "// 아래 셋은 latched cvar 라 맵이 로드되는 순간의 값으로 고정된다. map 보다 뒤에 두면",
        "// 이번 매치에는 반영되지 않는다.",
        f"set g_gametype {_quote(scenario.gametype)}",
        f"set sv_maxclients {_quote(scenario.maxclients)}",
        'set sv_pure "0"',
        'set bot_enable "1"',
    ]

    # 봇 자동 보충은 기본으로 끈다. 켜 두면 시나리오에 없는 봇이 임의로 들어와 인원과 실력이
    # 시나리오와 달라진다. 자동 보충이 쓰는 실력은 g_spSkill 하나뿐이라 봇별 실력도 무너진다.
    if scenario.keep_filled:
        out += [
            "",
            "// 봇이 빠져나가면 서버가 다시 채운다. 채워 넣는 봇의 실력은 g_spSkill 하나로 정해지므로",
            "// 봇별로 다른 실력을 준 시나리오에서는 보충된 봇만 실력이 달라진다.",
            f"set bot_minplayers {_quote(len(scenario.bots))}",
        ]
    else:
        out += ["", "// 시나리오에 적힌 봇만 쓴다. 임의 보충을 끈다.", 'set bot_minplayers "0"']

    out += [
        "",
        _heading(step, "매치 규칙"),
        "// fraglimit 과 timelimit 이 0이 아니면 매치가 끝나면서 게임이 텔레메트리 파일을 새로 열어",
        "// (FS_WRITE) 그 판의 기록이 사라진다. 점수판 구간이 이상탐지 오탐이 되는 문제도 함께 생긴다.",
        f"set fraglimit {_quote(scenario.fraglimit)}",
        f"set timelimit {_quote(scenario.timelimit)}",
        f"set g_doWarmup {_quote(scenario.warmup)}",
    ]

    if scenario.cvars:
        out += ["", _heading(step, "시나리오 cvar")]
        for name in sorted(scenario.cvars):
            out.append(f"set {name} {_quote(scenario.cvars[name])}")

    # 주입기는 시나리오에 적지 않은 것도 명시적으로 꺼 둔다. 앞 실행에서 켜 둔 값이 남아 있으면
    # 이번 매치가 조용히 오염되고, 세션 요약만 봐서는 그 사실을 알 수 없다.
    out += ["", _heading(step, "결함 주입기"),
            "// 시나리오에 적지 않은 주입기도 명시적으로 끈다. 앞 실행의 값이 남아 있으면 안 된다."]
    for key in sorted(INJECTORS):
        value = 1 if scenario.inject.get(key) else 0
        out.append(f"set {INJECTORS[key]} {_quote(value)}")

    out += [
        "",
        _heading(step, "맵 로드"),
        "// 먼저 이미 들어와 있는 봇을 모두 내보낸다.",
        "//",
        "// map 명령은 봇을 쫓아내지 않는다. 접속해 있던 클라이언트를 새 서버에 다시 붙이는데,",
        "// 봇도 클라이언트라 그대로 살아남는다. 그래서 이 파일을 두 번째로 실행하면 아래의",
        "// addbot 이 기존 봇 위에 얹혀 인원이 두 배가 된다. 자리가 찰 때까지 늘어난 뒤에는",
        "// 조용히 멈추므로 화면에도 오류가 남지 않는다.",
        "//",
        "// 인원이 달라지면 교전 빈도가 달라지고 이상탐지가 배운 '정상'의 분포가 통째로 어긋난다.",
        "// 실제로 이 문제로 오탐률이 일곱 배가 된 적이 있다(README '검증 결과' 참조).",
        "kick allbots",
        "",
        "// 이 줄에서 서버가 뜨고, 게임이 텔레메트리 파일을 새로 만든다.",
        f"map {scenario.map}",
        "",
        _heading(step, "서버가 자리를 잡을 때까지 기다린다"),
        "// 맵 로드가 끝나도 사람 클라이언트가 서버에 접속을 마치기까지 몇 프레임이 더 필요하다.",
        "// 그 전에 봇을 넣으면 자리 배정이 겹칠 수 있다. wait 한 줄이 한 프레임이다.",
    ]
    out += _wait_block(scenario.wait_frames)

    out += [
        "",
        _heading(step, "봇 투입"),
        f"exec {bots_cfg_name}",
    ]

    if scenario.spectator:
        out += [
            "",
            _heading(step, "사람은 관전으로 돌린다"),
            "// 텔레메트리는 봇만 기록하지만 결함 주입기는 아직 사람에게도 걸린다. 관전으로 빠져",
            "// 있어야 주입이 시험 대상(봇)에게만 보이는 상태가 된다.",
        ]
        out += _wait_block(scenario.spectator_wait_frames)
        out.append("team spectator")

    if scenario.keys:
        out += [
            "",
            _heading(step, "시연용 키 바인딩"),
            "// 매치 도중에 조건을 바꾸려면 사람이 눌러야 한다. cfg 만으로는 '몇 초 뒤에 켜기'를",
            "// 할 수 없기 때문이다(docs/scenario.md 의 '자동 주입' 참조).",
        ]
        for key in sorted(scenario.keys):
            out.append(f'bind {key} "{scenario.keys[key]}"')

    # 게임 콘솔에 어떤 시나리오가 걸렸는지 남긴다. 화면만 보고는 default 로 돌고 있는지
    # skill_low 로 돌고 있는지 알 수 없기 때문이다. 이름에 큰따옴표가 있으면 이 줄이 깨지므로
    # 지우고 넣는다(시나리오 이름은 표시용이라 지워도 잃는 것이 없다).
    label = scenario.name.replace('"', "")
    out += [
        "",
        f'echo "QA 시나리오: {label} / 맵 {scenario.map} / 봇 {len(scenario.bots)}마리"',
    ]
    return "\n".join(out) + "\n"
