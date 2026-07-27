"""게임 설치 폴더에서 맵 이름과 봇 이름을 읽어 오는 모듈이다.

왜 필요한가:
시나리오가 `map oa_rpg3dm2` 나 `addbot Ayumi ...` 를 만들어 내는데, 이름이 하나라도 틀리면
게임은 조용히 실패한다. 맵이 없으면 콘솔에 한 줄 찍고 이전 화면에 머물고, 봇 이름이 없으면
`Error: Bot 'X' not defined` 만 남기고 그 봇을 건너뛴다. 둘 다 **게임 창을 보고 있지 않으면
알 수 없는** 실패다. 자동 실행에서는 그사이 파이썬 쪽이 멀쩡히 돌고 있으므로 더 위험하다.

그래서 cfg 를 만들기 **전에** 실제 설치본을 뒤져 이름을 검증한다. 게임을 켜지 않고도 확인할 수
있는 이유는, 게임 데이터가 담긴 `.pk3` 파일이 확장자만 바꾼 zip 이기 때문이다. 표준 라이브러리
`zipfile` 로 목록을 읽을 수 있고, 중앙 디렉터리만 읽으므로 100MB 짜리 pk3 여도 빠르다.

무엇을 어디서 찾는가:
- 맵: 검색 경로 어디든 `maps/<이름>.bsp` 가 있으면 그 맵은 로드 가능하다. 이 규칙은 Quake3
  계열에서 바뀐 적이 없어 배포판을 타지 않는다.
- 봇: 게임 모듈이 봇 목록 파일(보통 `scripts/bots.txt`)과 검색 경로의 `*.bot` 파일을 읽어
  목록을 만든다. 그런데 **봇 목록 파일은 위치와 표기가 배포판마다 다르다.** 그래서 경로를
  고정하지 않고 `bots.txt` 로 끝나는 파일을 전부 찾으며, 이름이 따옴표에 싸여 있든 아니든
  읽는다. 게임 쪽 파서가 두 표기를 모두 받아들이므로 실제로 두 형태가 다 존재한다.

검색 경로를 그대로 흉내 내지는 않는다:
게임은 같은 이름의 파일이 여러 pk3 에 있으면 우선순위가 높은 하나만 읽는다. 이 모듈은
반대로 **찾은 것을 전부 합친다.** 우선순위 규칙(mod 폴더 우선, pk3 이름 순서 등)을 잘못
흉내 내면 실제로는 쓸 수 있는 이름을 없다고 판정해 사용자를 막게 되는데, 그 실패가 반대 방향
실패(없는 이름을 있다고 판정)보다 나쁘다. 없는 이름을 통과시키면 게임이 콘솔에 이유를 찍고
넘어가지만, 있는 이름을 막으면 사용자는 도구를 우회할 방법이 없다. 그래서 합집합을 쓰고,
이 목록은 '후보'이지 '확정'이 아님을 호출하는 쪽이 알고 쓰도록 한다.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

# 맵 파일이 놓이는 폴더와 확장자다.
MAP_DIR = "maps/"
MAP_EXT = ".bsp"

# 봇 목록 파일을 알아보는 방법이다. 경로를 고정하지 않는 것이 핵심이다.
#
# 왜 고정하지 않는가: Quake3 게임 모듈은 기본적으로 `scripts/bots.txt` 를 읽지만 그 경로는
# `g_botsFile` cvar 로 바꿀 수 있고, 배포판이 다른 폴더에 두기도 한다. 경로를 두어 개만
# 적어 두면 그 밖에 둔 배포판에서 조사가 통째로 실패하고, 그러면 이름 검증이 조용히 꺼진다.
# 파일 이름으로 알아보면 어디에 있든 걸린다.
BOTS_FILE_SUFFIX = "bots.txt"

# 개별 봇을 담는 파일의 확장자다. 게임도 검색 경로의 `*.bot` 파일을 함께 읽는다.
BOT_FILE_EXT = ".bot"

# 봇 목록 한 항목에서 이름을 뽑는 정규식이다. 두 가지를 함께 처리한다.
#
# (1) 줄 앞에서부터 맞춘다. 같은 항목 안에 `funname "^1S^3arge"` 라는 줄이 있는데, 줄 중간까지
#     허용하면 `funname` 안의 `name` 에 걸려 색 코드가 붙은 표시용 이름을 봇 이름으로 잘못
#     읽는다. 실제 `addbot` 이 받는 것은 `name` 쪽이다.
# (2) 값이 따옴표에 싸여 있을 수도, 없을 수도 있다. 게임 쪽 파서가 두 표기를 모두 받아들여
#     실제로 두 형태가 다 존재한다. 따옴표만 읽으면 따옴표 없는 배포판에서 한 마리도 못 찾는다.
BOT_NAME_RE = re.compile(
    r'^[ \t]*name[ \t]+(?:"([^"\r\n]+)"|([^\s"{}/]+))',
    re.MULTILINE,
)

# 게임의 기본 데이터 폴더 이름이다. OpenArena 는 baseoa, ioquake3/Q3 계열은 baseq3 를 쓴다.
# 둘 다 훑어 두면 이 도구가 어느 쪽에서도 동작한다.
BASE_GAME_DIRS = ("baseoa", "baseq3")


def search_dirs(game_root: str | None, fs_game: str = "qa",
                home_path: str | None = None) -> list[Path]:
    """게임이 데이터를 찾는 폴더들을 우선순위와 무관하게 모아 반환한다.

    game_root 는 실행 파일이 있는 폴더(설치 경로)이고, home_path 는 게임이 쓰기용으로 쓰는
    폴더(윈도우에서는 보통 `%APPDATA%\\OpenArena`)다. 텔레메트리와 우리가 만드는 cfg 가
    home_path 쪽에 생기므로, 맵이나 봇을 거기에 따로 넣어 둔 경우까지 보려면 둘 다 훑어야 한다.
    """
    roots: list[Path] = []
    seen: set[str] = set()
    for base in (game_root, home_path):
        if not base:                      # 경로를 안 알려 줬으면 그쪽은 건너뛴다.
            continue
        root = Path(base)
        if not root.is_dir():             # 존재하지 않는 경로는 조용히 무시한다.
            continue
        for name in (*BASE_GAME_DIRS, fs_game):
            candidate = root / name
            # 같은 폴더가 두 번 들어가면 같은 파일을 두 번 읽는다(fs_game 이 baseoa 인 경우 등).
            key = str(candidate).lower()
            if candidate.is_dir() and key not in seen:
                seen.add(key)
                roots.append(candidate)
    return roots


def _iter_entries(directory: Path):
    """한 데이터 폴더 안의 모든 파일을 (표시용 위치, 게임 내부 경로, 읽기 함수)로 내보낸다.

    두 가지를 함께 다룬다.
    - 폴더에 그대로 풀려 있는 파일(loose). `sv_pure 0` 일 때 로드된다. 이 프로젝트가 QVM 을
      pk3 로 묶지 않고 두는 방식이 바로 이것이다.
    - `.pk3` 안에 들어 있는 파일. pk3 는 zip 이므로 목록만 읽어 온다.

    읽기를 함수로 넘기는 이유는 **필요한 파일만 실제로 읽기 위해서**다. 목록에는 수천 개가
    들어 있고 그중 내용을 봐야 하는 것은 봇 목록 파일 몇 개뿐이다.
    """
    try:
        entries = list(directory.rglob("*"))   # 하위 폴더까지 재귀적으로 훑는다.
    except OSError:
        entries = []
    for path in entries:
        if not path.is_file():
            continue
        # 게임 내부 경로 표기에 맞춰 폴더 구분자를 슬래시로 바꾸고 소문자로 통일한다.
        rel = path.relative_to(directory).as_posix().lower()
        yield str(path), rel, (lambda p=path: p.read_bytes())

    # 그다음 pk3 안을 본다. 대소문자가 섞인 경우가 있어 두 가지를 모두 찾는다.
    for pak in sorted(set(list(directory.glob("*.pk3")) + list(directory.glob("*.PK3")))):
        try:
            with zipfile.ZipFile(pak) as zf:
                names = zf.namelist()
        except (zipfile.BadZipFile, OSError):
            # 깨진 pk3 하나 때문에 전체 조사가 멈추면 안 된다. 그 파일만 건너뛴다.
            continue
        for name in names:
            if name.endswith("/"):        # zip 안의 폴더 항목은 파일이 아니다.
                continue
            rel = name.replace("\\", "/").lower()

            def _read(p=pak, n=name):
                with zipfile.ZipFile(p) as zf:
                    return zf.read(n)

            yield f"{pak.name}:{name}", rel, _read


def parse_bot_names(text: str) -> list[str]:
    """봇 목록 파일의 내용에서 이름들을 뽑는다.

    별도 함수로 둔 이유는 이 부분이 배포판마다 가장 많이 어긋나는 지점이라, 실제 설치본 없이도
    표기 형태를 바꿔 가며 확인할 수 있어야 하기 때문이다.
    """
    names: list[str] = []
    for quoted, bare in BOT_NAME_RE.findall(text):
        value = (quoted or bare).strip()
        # 파싱 사고로 들어온 긴 문자열을 걸러 낸다. 봇 이름은 짧은 식별자다.
        if value and len(value) <= 36:
            names.append(value)
    return names


def scan(game_root: str | None, fs_game: str = "qa", home_path: str | None = None) -> dict:
    """설치본을 한 번만 훑어 맵·봇·진단 정보를 한꺼번에 모은다.

    맵과 봇을 따로 조회하면 같은 pk3 의 목록을 두 번 읽게 된다. 조회가 여럿이어도 훑기는
    한 번이면 된다.

    진단 정보를 함께 담는 이유: 이름 조사가 실패했을 때 "왜 실패했는가"를 알 수 있어야 한다.
    봇 목록 파일을 못 찾은 것인지, 찾았는데 이름을 못 읽은 것인지에 따라 고칠 곳이 다르다.
    """
    result = {
        "search_dirs": [],   # 실제로 훑은 폴더
        "maps": set(),       # 로드 가능한 맵 이름
        "bots": set(),       # addbot 에 쓸 수 있는 봇 이름
        "bot_sources": [],   # (파일 위치, 뽑은 이름 수). 파일은 찾았는지 구분하는 데 쓴다
        "bot_like": [],      # 이름에 'bot' 이 들어간 파일들. 목록을 못 찾았을 때의 단서
        "pak_count": 0,      # 연 pk3 수
        "file_count": 0,     # 훑은 파일 수
    }
    for directory in search_dirs(game_root, fs_game, home_path):
        result["search_dirs"].append(str(directory))
        result["pak_count"] += len(set(list(directory.glob("*.pk3")) + list(directory.glob("*.PK3"))))
        for where, rel, read in _iter_entries(directory):
            result["file_count"] += 1

            # ── 맵 ──
            # maps/ 바로 아래의 .bsp 만 맵이다. 하위 폴더에 있는 것은 게임의 map 명령이 보지 않는다.
            if rel.startswith(MAP_DIR) and rel.endswith(MAP_EXT):
                stem = rel[len(MAP_DIR):-len(MAP_EXT)]
                if stem and "/" not in stem:
                    result["maps"].add(stem)
                continue

            # ── 봇 ──
            if rel.endswith(BOTS_FILE_SUFFIX) or rel.endswith(BOT_FILE_EXT):
                try:
                    # 봇 목록 파일에는 라틴 계열 문자가 섞여 있을 수 있다. 이름만 뽑으면 되므로
                    # 깨진 바이트는 버린다.
                    text = read().decode("utf-8", errors="replace")
                except (OSError, zipfile.BadZipFile):
                    continue              # 읽기에 실패한 파일 하나는 건너뛴다.
                found = parse_bot_names(text)
                result["bot_sources"].append((where, len(found)))
                result["bots"].update(found)
            elif "bot" in rel and len(result["bot_like"]) < 40:
                # 봇 목록 파일을 하나도 못 찾았을 때 "그럼 봇 관련 파일이 어디에 있는가"를
                # 보여 주기 위한 단서다. 목록이 길어지므로 앞쪽 일부만 들고 있는다.
                result["bot_like"].append(where)
    return result


def available_maps(game_root: str | None, fs_game: str = "qa",
                   home_path: str | None = None) -> set[str]:
    """로드 가능한 맵 이름의 집합을 반환한다. 조사에 실패하면 빈 집합이 나온다."""
    return scan(game_root, fs_game, home_path)["maps"]


def available_bots(game_root: str | None, fs_game: str = "qa",
                   home_path: str | None = None) -> set[str]:
    """`addbot` 에 쓸 수 있는 봇 이름의 집합을 반환한다. 조사에 실패하면 빈 집합이 나온다."""
    return scan(game_root, fs_game, home_path)["bots"]


def describe(game_root: str | None, fs_game: str = "qa",
             home_path: str | None = None) -> dict:
    """조사 결과를 진단에 쓰기 좋은 형태로 반환한다."""
    r = scan(game_root, fs_game, home_path)
    return {
        "search_dirs": r["search_dirs"],
        "pak_count": r["pak_count"],
        "file_count": r["file_count"],
        "maps": sorted(r["maps"]),
        "bots": sorted(r["bots"]),
        "bot_sources": r["bot_sources"],
        "bot_like": r["bot_like"],
    }
