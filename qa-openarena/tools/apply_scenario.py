"""테스트 시나리오를 읽어 게임이 실행할 cfg 를 만들어 놓는 도구다.

`run_qa.bat` 이 게임을 띄우기 직전에 이 도구를 부른다. 게임은 시작할 때 `+exec qa_match.cfg`
로 그 파일을 실행하고, 그 안에 맵 로드부터 봇 투입까지가 순서대로 들어 있다.

만들어 내는 파일은 셋이다. 모두 게임의 모드 폴더(`<홈패스>\\qa`)에 놓는다. 게임은 그 폴더를
검색 경로에 두고 있으므로, 거기 있는 cfg 를 이름만으로 실행할 수 있다.

    qa_match.cfg     매치 전체 절차
    qa_bots.cfg      addbot 줄만 모은 파일(봇이 안 들어왔을 때 이것만 다시 실행한다)
    qa_scenario.json 확정된 시나리오. 서버가 읽어 세션 요약에 남긴다

실행 예:
    python tools/apply_scenario.py scenarios/default.toml --telemetry "%APPDATA%\\OpenArena\\qa\\qa_telemetry.jsonl" --game "C:\\game\\openarena-0.8.8\\openarena.exe"
    python tools/apply_scenario.py scenarios/default.toml --out-dir "%APPDATA%\\OpenArena\\qa" --dry-run
    python tools/apply_scenario.py --list --game "C:\\game\\openarena-0.8.8"
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa import gamefiles
from qa import scenario as scen

USAGE = __doc__


def parse_args(argv):
    """인자를 파싱해 딕셔너리로 돌려준다.

    이 저장소의 다른 도구들과 맞추어 argparse 를 쓰지 않고 직접 훑는다. 옵션이 전부
    `--이름 값` 형태라 규칙이 단순하고, 도움말은 모듈 설명을 그대로 쓴다.
    """
    opts = {
        "scenario": None, "out_dir": None, "telemetry": None, "game": None,
        "home": None, "fs_game": "qa", "list": False, "dry_run": False,
    }
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(USAGE)
            sys.exit(0)
        elif arg == "--list":
            opts["list"] = True
        elif arg == "--dry-run":
            opts["dry_run"] = True
        elif arg.startswith("--"):
            key = arg[2:].replace("-", "_")
            if key not in opts:
                print(f"모르는 옵션이다: {arg}\n")
                print(USAGE)
                sys.exit(2)
            i += 1
            if i >= len(argv):
                print(f"{arg} 에 값이 없다.")
                sys.exit(2)
            opts[key] = argv[i]
        elif opts["scenario"] is None:
            opts["scenario"] = arg
        else:
            print(f"인자가 너무 많다: {arg}")
            sys.exit(2)
        i += 1
    return opts


def resolve_paths(opts):
    """옵션에서 출력 폴더·게임 루트·홈패스를 정한다.

    출력 폴더를 텔레메트리 경로에서 유도하는 것이 핵심이다. 게임이 텔레메트리를 쓰는 곳이
    바로 모드 폴더(`<홈패스>\\qa`)이고, 우리가 cfg 를 놓아야 하는 곳도 같은 폴더다. 두 경로를
    따로 설정하게 하면 한쪽만 고쳤을 때 게임이 cfg 를 못 찾는데, 그 실패는 게임 콘솔을 봐야만
    알 수 있다. 이미 맞춰 둔 값 하나에서 나머지를 끌어내면 어긋날 자리가 없다.
    """
    out_dir = opts["out_dir"]
    if not out_dir and opts["telemetry"]:
        out_dir = str(Path(opts["telemetry"]).parent)

    game_root = opts["game"]
    if game_root:
        path = Path(game_root)
        # 실행 파일 경로를 그대로 받았으면 그 폴더를 게임 루트로 본다. run_qa.bat 이 이미
        # openarena.exe 경로를 들고 있으므로 그것을 그대로 넘길 수 있게 한다.
        if path.suffix.lower() == ".exe" or path.is_file():
            path = path.parent
        game_root = str(path)

    home = opts["home"]
    if not home and out_dir:
        # 모드 폴더의 부모가 홈패스다. 홈패스 쪽에도 맵이나 봇을 넣어 두는 경우가 있어 함께 훑는다.
        home = str(Path(out_dir).parent)

    return out_dir, game_root, home


def do_list(game_root, fs_game, home):
    """설치본에서 찾은 맵과 봇 이름을 출력한다.

    조사가 실패했을 때 **왜 실패했는지**까지 보여 주는 것이 이 함수의 절반이다. 봇 목록 파일을
    못 찾은 것인지, 찾았는데 이름을 못 읽은 것인지에 따라 고칠 곳이 다르기 때문이다. 사용자가
    "안 나온다"만 알고 있으면 그다음에 할 수 있는 일이 없다.
    """
    if not game_root:
        print("게임 설치 경로를 --game 으로 알려 준다.")
        return 2
    info = gamefiles.describe(game_root, fs_game, home)

    print("훑은 폴더")
    for d in info["search_dirs"]:
        print(f"  {d}")
    if not info["search_dirs"]:
        print("  (없음) --game 경로 아래에 baseoa 폴더가 있는지 확인한다.")
        return 1
    print(f"  파일 {info['file_count']:,}개, pk3 {info['pak_count']}개")

    print(f"\n맵 {len(info['maps'])}개")
    for name in info["maps"]:
        print(f"  {name}")

    print(f"\n봇 {len(info['bots'])}마리")
    for name in info["bots"]:
        print(f"  {name}")

    # ── 봇 조사 진단 ──
    if info["bot_sources"]:
        print("\n봇 목록을 읽은 파일")
        for where, count in info["bot_sources"]:
            print(f"  {count:4d}마리  {where}")
    if not info["bots"]:
        print("\n봇 이름을 하나도 읽지 못했다. 아래를 확인한다.")
        if not info["bot_sources"]:
            print("  - 봇 목록 파일(이름이 bots.txt 로 끝나는 파일)을 찾지 못했다.")
            if info["bot_like"]:
                print("  - 이름에 bot 이 들어간 파일은 아래에 있다. 목록 파일이 이 중에 있는지 본다.")
                for where in info["bot_like"]:
                    print(f"      {where}")
            else:
                print("  - 봇 관련 파일 자체가 없다. --game 경로가 맞는지 확인한다.")
        else:
            print("  - 목록 파일은 찾았으나 그 안에서 이름을 뽑지 못했다. 표기가 예상과 다르다.")
        print("  - 그동안은 시나리오의 [[roster.bots]] 에 이름을 직접 적으면 된다.")
        print("    이름은 게임의 Multiplayer > Add Bot 메뉴에서도 확인할 수 있다.")
    return 0


def main(argv):
    opts = parse_args(argv)
    out_dir, game_root, home = resolve_paths(opts)

    if opts["list"]:
        return do_list(game_root, opts["fs_game"], home)

    if not opts["scenario"]:
        print(USAGE)
        return 2

    # ── 1. 읽기 ──
    try:
        s = scen.load(opts["scenario"])
    except scen.ScenarioError as exc:
        print(f"[시나리오 오류] {exc}")
        return 1

    # ── 2. 설치본 조사 ──
    # 조사에 실패하면(경로를 모르거나 pk3 를 못 읽음) 빈 목록이 나오고, 그 경우 이름 검사는
    # 건너뛴다. 조사 실패와 '이름이 정말 없음'을 구분하지 않으면 멀쩡한 시나리오를 막게 된다.
    # 맵과 봇을 따로 조회하면 같은 pk3 목록을 두 번 읽으므로 한 번만 훑는다.
    info = gamefiles.scan(game_root, opts["fs_game"], home) if game_root else None
    maps = info["maps"] if info else set()
    bots = info["bots"] if info else set()
    if not game_root:
        print("[알림] --game 을 주지 않아 맵·봇 이름을 검사하지 않는다.")
    elif not maps and not bots:
        print(f"[알림] {game_root} 에서 맵·봇 목록을 읽지 못해 이름을 검사하지 않는다.")
    elif not bots:
        # 맵은 찾았는데 봇만 못 찾은 경우다. pk3 는 제대로 읽고 있으므로 경로 문제가 아니라
        # 봇 목록 파일의 위치나 표기가 예상과 다른 것이다. 원인을 좁혀 알린다.
        읽은파일 = ", ".join(w for w, _ in info["bot_sources"][:3]) or "없음"
        print(f"[알림] 맵은 {len(maps)}개 찾았으나 봇 이름을 하나도 읽지 못했다."
              f" (봇 목록 파일: {읽은파일})")
        print("       --list 로 자세히 보고, 그동안은 [[roster.bots]] 에 이름을 직접 적는다.")

    # ── 3. 봇 목록 확정 ──
    try:
        warnings = scen.resolve(s, bots)
    except scen.ScenarioError as exc:
        print(f"[시나리오 오류] {exc}")
        return 1

    # ── 4. 검증 ──
    errors, more = scen.validate(s, maps, bots)
    warnings += more
    for w in warnings:
        print(f"[경고] {w}")
    if errors:
        for e in errors:
            print(f"[오류] {e}")
        print("\n시나리오를 고친 뒤 다시 실행한다. cfg 를 만들지 않았다.")
        return 1

    # ── 5. cfg 생성 ──
    match_cfg = scen.to_match_cfg(s)
    bots_cfg = scen.to_bots_cfg(s)

    if opts["dry_run"]:
        # 게임 없이 결과만 확인하는 경로다. 시나리오를 손보는 동안 게임을 껐다 켤 필요가 없다.
        print("\n===== qa_match.cfg =====")
        print(match_cfg)
        print("===== qa_bots.cfg =====")
        print(bots_cfg)
        return 0

    if not out_dir:
        print("cfg 를 놓을 폴더를 정하지 못했다. --out-dir 나 --telemetry 를 준다.")
        return 2

    out = Path(out_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
        # 게임이 읽는 파일이므로 줄바꿈을 CRLF 로 강제하지 않는다. Quake3 계열 파서는 둘 다
        # 처리하지만, newline="" 으로 두면 파이썬이 플랫폼에 따라 바꾸는 일도 없어 결과가 같다.
        (out / "qa_match.cfg").write_text(match_cfg, encoding="utf-8", newline="")
        (out / "qa_bots.cfg").write_text(bots_cfg, encoding="utf-8", newline="")
        # 확정된 시나리오를 함께 남긴다. 서버가 이 파일을 읽어 세션 요약에 싣는다.
        (out / "qa_scenario.json").write_text(
            json.dumps(s.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        print(f"cfg 를 쓰지 못했다: {exc}")
        return 1

    # ── 6. 무엇을 만들었는지 보고 ──
    print(f"시나리오: {s.name}")
    print(f"  용도    {scen.PURPOSES.get(s.purpose, s.purpose)}")
    print(f"  맵      {s.map} (gametype {s.gametype})")
    print(f"  봇      {len(s.bots)}마리")
    for bot in s.bots:
        print(f"            {bot.name}  실력 {bot.skill:g}  {bot.team}")
    if s.cvars:
        joined = ", ".join(f"{k}={s.cvars[k]}" for k in sorted(s.cvars))
        print(f"  cvar    {joined}")
    on = [k for k in sorted(scen.INJECTORS) if s.inject.get(k)]
    print(f"  주입    {', '.join(on) if on else '없음'}")
    print(f"  출력    {out}")
    if s.purpose == "probe":
        # 시험지에서는 이상탐지가 반응하는 것이 정상이다. 이 사실을 세션이 끝난 뒤에
        # 떠올리면 늦으므로, 매치를 시작하기 전에 알린다.
        print("\n  주의: 이 조건은 시험지다. 이상탐지가 내는 판정은 게임의 버그가 아니라")
        print("        조건이 학습 때와 다르다는 신호이며, 반응하는 것이 정상이다.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
