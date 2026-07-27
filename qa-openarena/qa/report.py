"""세션 결과를 사람이 읽는 한 장짜리 리포트로 만든다.

왜 필요한가:
세션이 끝나면 파일 셋이 남는다. 사건 목록(JSONL), 요약(JSON), 텔레메트리 사본(JSONL)이다.
셋 다 기계가 읽는 형태라, 나중에 "그 판에서 무엇이 잡혔더라"를 보려면 JSON 을 직접 열어야 한다.
대시보드는 실시간 화면이라 세션이 끝나면 남는 것이 없다.

무엇으로 만드는가 — 자체 완결 HTML:
- **그림을 인라인 SVG 로 직접 그린다.** 차트 라이브러리도, 이미지 파일도 쓰지 않는다. SVG 는
  텍스트라 파일이 작고(리포트 한 장이 15KB 안팎), 확대해도 깨지지 않으며, 새 의존성이 없다.
- **자바스크립트가 없다.** 파일 하나를 브라우저로 열면 그대로 보인다. 서버도 인터넷도 필요 없다.
- **PDF 가 필요하면 브라우저에서 인쇄로 뽑는다.** PDF 로 직접 만들면 한글 글꼴을 파일 안에
  심어야 해서 용량이 열 배 넘게 늘고 글꼴 파일에 대한 의존이 생긴다. HTML → PDF 는 브라우저가
  해 주지만 그 반대는 성립하지 않으므로, 원본은 HTML 쪽에 두는 것이 맞다.
- **배경은 흰색이다.** 대시보드는 어두운 화면이지만 리포트는 인쇄하거나 문서에 붙일 수 있어야
  한다. 어두운 배경은 인쇄하면 잉크를 먹거나 브라우저가 배경을 지워 대비가 무너진다.

무엇을 그리는가:
1. **타임라인** — 세션 전체를 한 시간축에 놓고, 봇마다 한 줄씩 사건 구간과 이상 창을 겹쳐 그린다.
   ① 하드 인바리언트와 ② 이상탐지를 같은 축에 두는 것이 핵심이다. "규칙은 하나도 안 걸렸는데
   이상탐지만 반응한 구간"이 눈으로 보인다. 그것이 두 층을 나눈 이유이기 때문이다.
   맨 위에 ③ 워치독 경보 구간을 같은 축으로 얹는다.
2. **서버 틱 추이** — 타임라인과 같은 시간축을 공유한다. 축을 맞추는 것이 목적이다. 사건이
   서버가 버벅이던 구간에 있었는지를 눈으로 대조할 수 있어야 한다. 예를 들어 끼임 사건이
   틱이 떨어진 구간과 겹친다면, 그것은 봇이 낀 것이 아니라 서버가 멈춰 위치가 안 변한 것일 수 있다.
3. **이상 점수 곡선** — 임계선을 함께 그려, 판정이 어느 정도 차이로 갈렸는지 보이게 한다.
4. **규칙별 사건 수** — 0건인 규칙도 자리를 지킨다. "그 규칙은 안 걸렸다"도 결과다.

이 모듈은 파일을 모른다. 딕셔너리와 목록을 받아 문자열을 돌려줄 뿐이라, 서버가 세션을 마감할
때도 쓰고 오프라인 도구가 이미 쌓인 세션에서 다시 만들 때도 같은 코드가 쓰인다. 두 경로가
다른 코드를 타면 같은 세션에서 다른 리포트가 나올 수 있다.
"""

from __future__ import annotations

import html
import time

# ── 색 ──────────────────────────────────────────────────────────────────────
# 흰 배경 기준으로 고른다. 대시보드의 어두운 팔레트를 그대로 뒤집지 않고, 흰 바탕에서
# 대비가 충분한 값으로 다시 골랐다. 색만으로 뜻을 전하지 않도록 글자 표시를 함께 둔다.
C = {
    "bg": "#ffffff",       # 종이
    "panel": "#f8faf9",    # 카드·차트 바탕
    "row": "#eef2f0",      # 차트 안의 줄 바탕
    "line": "#dce3df",     # 테두리
    "grid": "#e8ecea",     # 눈금선
    "text": "#16201b",     # 본문
    "dim": "#66756c",      # 보조 글자
    "faint": "#94a09a",    # 축 글자
    "ok": "#1f8a4c",       # 정상
    "high": "#cf3a3c",     # 하드 인바리언트 사건
    "med": "#b3760d",      # 주의·워치독
    "anomaly": "#6f42c1",  # 이상탐지
    "tick": "#1f6f8b",     # 서버 틱 선. 위 셋과 겹치지 않는 색으로 고른다
}

# 규칙 목록을 고정한다. 사건이 0건인 규칙도 자리를 지켜야 "그 규칙은 안 걸렸다"가 정보가 된다.
RULES = [
    ("fell_through_floor", "바닥 관통"),
    ("out_of_bounds", "경계 이탈"),
    ("health_out_of_range", "체력 범위"),
    ("impossible_velocity", "과속"),
    ("stuck", "끼임"),
]

# 워치독 경보 종류의 한글 이름이다. 영문 그대로 두면 리포트에서 갑자기 튄다.
ALERT_LABEL = {
    "low_tick": "성능 저하",
    "no_progress": "진행 없음",
    "process_gone": "프로세스 종료",
}

# 이상탐지의 기준선. 임계값을 학습 데이터의 5백분위로 잡으므로 구조적으로 이 값이 바닥이다.
BASELINE_RATE = 5.0

# 차트 공통 치수. 타임라인과 틱 추이가 X축을 공유해야 하므로 좌우 여백을 같은 값으로 묶는다.
# 값이 어긋나면 두 그림의 같은 시각이 다른 가로 위치에 그려져 대조가 불가능해진다.
W = 980
PAD_L, PAD_R = 52, 14


def _e(v) -> str:
    """HTML 에 넣기 전에 특수문자를 막는다. 맵 이름이나 시나리오 이름이 그대로 들어오기 때문이다."""
    return html.escape(str(v), quote=True)


def _num(v, digits=0) -> str:
    if v is None:
        return "-"
    return f"{v:,.{digits}f}"


def _clock(seconds) -> str:
    """초를 분:초로 바꾼다."""
    if seconds is None:
        return "-"
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def _h2(title, color, tint, note="") -> str:
    """구역 제목을 색이 들어간 탭 모양으로 만든다.

    번호(①②③) 대신 색으로 구분한다. 번호는 규칙에 매기는 것이지 화면에서 눈에 띄라고 붙이는
    것이 아니어서, 리포트를 훑을 때 구역이 바뀐 것을 알아채기 어려웠다. 색은 각 구역이 다루는
    대상과 맞춘다. 사건은 빨강, 이상탐지는 보라, 워치독은 주황이며 이는 그림 안의 색과 같다.
    제목만 보고도 아래 그림에서 무엇을 찾아야 하는지 알 수 있어야 한다.
    """
    extra = f"<small>{_e(note)}</small>" if note else ""
    return (f'<h2 style="color:{color};background:{tint};'
            f'border-top:3px solid {color}">{_e(title)}{extra}</h2>')


def _map(v, lo, hi, a, b) -> float:
    """값 v 를 [lo, hi] 범위에서 [a, b] 범위로 옮긴다."""
    if hi <= lo:
        return a
    return a + (b - a) * (float(v) - lo) / (hi - lo)


def _x(t, dur) -> float:
    """게임 시간을 가로 좌표로 바꾼다. 두 차트가 같은 함수를 쓰므로 축이 어긋날 수 없다."""
    return _map(t, 0, max(dur, 1.0), PAD_L, W - PAD_R)


def _grid(dur, y0, y1, labels=True) -> list:
    """세로 눈금선과 시각 표시를 만든다. 두 차트가 같은 눈금을 갖게 한다."""
    out = []
    step = 60
    while dur / step > 12:
        step *= 2
    t = 0
    while t <= dur:
        x = _x(t, dur)
        out.append(f'<line x1="{x:.1f}" y1="{y0}" x2="{x:.1f}" y2="{y1}" '
                   f'stroke="{C["grid"]}" stroke-width="1"/>')
        if labels:
            out.append(f'<text x="{x:.1f}" y="{y1 + 14}" class="ax" '
                       f'text-anchor="middle">{_clock(t)}</text>')
        t += step
    return out


def _wall_to_game(trail, wall) -> float | None:
    """워치독 경보의 실제 시각을 게임 시간으로 옮긴다.

    경보는 실제 시각(epoch)으로 기록되고 타임라인의 축은 게임 시간이다. 둘을 잇는 다리가
    관측 기록이다. 관측마다 두 시각을 함께 남겨 두었으므로, 가장 가까운 관측을 찾으면 된다.
    관측이 초당 한 번이라 이 방식의 오차는 1초 이내다.
    """
    best, gap = None, None
    for p in trail:
        if p.get("wall") is None or p.get("t") is None:
            continue
        d = abs(p["wall"] - wall)
        if gap is None or d < gap:
            best, gap = p["t"], d
    return best


# ── 그림 ────────────────────────────────────────────────────────────────────

def timeline_svg(events, scores, watchdog, duration, lane_h=16) -> str:
    """세션 전체를 한 시간축에 놓고 봇마다 한 줄씩 그린다. 맨 위에 워치독 줄을 얹는다."""
    bots = sorted({e["entity_id"] for e in events} | {s["entity_id"] for s in scores})
    if not bots:
        return '<p class="empty">그릴 것이 없다. 봇 기록이 없는 세션이다.</p>'

    trail = (watchdog or {}).get("trail", [])
    alerts = (watchdog or {}).get("alerts", [])
    wd_h = lane_h + 6  # 워치독 줄. 봇 줄과 구분되도록 조금 띄운다

    pad_t, pad_b = 16, 26
    dur = max(duration or 0, 1.0)
    height = pad_t + wd_h + lane_h * len(bots) + pad_b
    plot_w = W - PAD_L - PAD_R

    out = [f'<svg viewBox="0 0 {W} {height}" class="chart" role="img">']
    out += _grid(dur, pad_t, height - pad_b)

    # ── ③ 워치독 줄 ──
    y = pad_t
    out.append(f'<rect x="{PAD_L}" y="{y}" width="{plot_w}" height="{lane_h - 3}" '
               f'fill="{C["row"]}"/>')
    out.append(f'<text x="{PAD_L - 6}" y="{y + lane_h - 7}" class="lane" '
               f'text-anchor="end">워치독</text>')
    marked = 0
    for a in alerts:
        g0 = _wall_to_game(trail, a.get("started_at"))
        g1 = _wall_to_game(trail, a.get("ended_at"))
        if g0 is None:
            continue
        if g1 is None or g1 < g0:
            g1 = g0
        x1, x2 = _x(g0, dur), _x(g1, dur)
        label = ALERT_LABEL.get(a.get("kind"), a.get("kind", ""))
        out.append(f'<rect x="{x1:.1f}" y="{y}" width="{max(x2 - x1, 3):.1f}" '
                   f'height="{lane_h - 3}" fill="{C["med"]}" opacity="0.85"><title>'
                   f'{_e(label)} {a.get("duration", 0)}초</title></rect>')
        marked += 1
    if not marked:
        out.append(f'<text x="{PAD_L + 7}" y="{y + lane_h - 7}" class="ax">경보 없음</text>')

    # ── 봇 줄 ──
    for i, bot in enumerate(bots):
        y = pad_t + wd_h + i * lane_h
        out.append(f'<rect x="{PAD_L}" y="{y}" width="{plot_w}" height="{lane_h - 3}" '
                   f'fill="{C["row"]}"/>')
        out.append(f'<text x="{PAD_L - 6}" y="{y + lane_h - 7}" class="lane" '
                   f'text-anchor="end">BOT {bot}</text>')

        # ② 이상 창을 먼저 깔고 ① 사건을 그 위에 얹는다. 사건이 가려지면 안 되기 때문이다.
        for s in scores:
            if s["entity_id"] != bot or not s.get("is_anomaly"):
                continue
            x1, x2 = _x(s["start_time"], dur), _x(s["end_time"], dur)
            out.append(f'<rect x="{x1:.1f}" y="{y}" width="{max(x2 - x1, 1):.1f}" '
                       f'height="{lane_h - 3}" fill="{C["anomaly"]}" opacity="0.22"/>')

        for ev in events:
            if ev["entity_id"] != bot:
                continue
            x1, x2 = _x(ev["start_time"], dur), _x(ev["end_time"], dur)
            # 아주 짧은 사건도 보이도록 최소 폭을 준다. 폭이 0이면 없는 것처럼 보인다.
            out.append(f'<rect x="{x1:.1f}" y="{y + 2}" width="{max(x2 - x1, 2.5):.1f}" '
                       f'height="{lane_h - 7}" fill="{C["high"]}"><title>'
                       f'{_e(ev["rule"])} {ev["start_time"]:.1f}~{ev["end_time"]:.1f}초</title></rect>')

    out.append("</svg>")
    return "".join(out)


def tick_svg(watchdog, duration, height=118) -> str:
    """서버 틱 추이를 타임라인과 같은 시간축에 그린다.

    축을 공유하는 것이 이 그림의 전부다. 틱이 떨어진 구간과 사건 구간이 세로로 맞아떨어지는지를
    눈으로 대조할 수 있어야 한다. 끼임 사건이 틱 저하 구간과 겹친다면 봇이 낀 것이 아니라
    서버가 멈춰 위치가 안 변한 것일 수 있고, 그 둘은 전혀 다른 버그다.
    """
    trail = [p for p in (watchdog or {}).get("trail", []) if p.get("tick") is not None]
    if not trail:
        return ('<p class="empty">서버 틱 기록이 없다. 워치독이 실시간 관측을 확인하기 전이었거나 '
                '기록 이전에 만들어진 세션이다.</p>')

    target = (watchdog or {}).get("target_tick") or 20.0
    ratio = (watchdog or {}).get("tick_ratio_alert") or 0.85
    alert_at = target * ratio

    pad_t, pad_b = 12, 24
    dur = max(duration or 0, 1.0)
    ticks = [p["tick"] for p in trail]
    lo = min(ticks + [alert_at]) * 0.92
    hi = max(ticks + [target]) * 1.06

    def ty(v):
        return _map(v, lo, hi, height - pad_b, pad_t)

    out = [f'<svg viewBox="0 0 {W} {height}" class="chart" role="img">']
    out.append(f'<rect x="{PAD_L}" y="{pad_t}" width="{W - PAD_L - PAD_R}" '
               f'height="{height - pad_t - pad_b}" fill="{C["row"]}"/>')
    out += _grid(dur, pad_t, height - pad_b)

    # 목표선과 경보선. 선 두 개가 있어야 "얼마나 떨어지면 경보인가"가 보인다.
    for value, color, label, dash in ((target, C["dim"], f"목표 {target:g}", "3 3"),
                                      (alert_at, C["med"], f"경보 {alert_at:.1f}", "4 3")):
        yy = ty(value)
        out.append(f'<line x1="{PAD_L}" y1="{yy:.1f}" x2="{W - PAD_R}" y2="{yy:.1f}" '
                   f'stroke="{color}" stroke-width="1" stroke-dasharray="{dash}"/>')
        out.append(f'<text x="{PAD_L - 6}" y="{yy + 3:.1f}" class="ax" '
                   f'text-anchor="end">{_e(label)}</text>')

    pts = " ".join(f"{_x(p['t'], dur):.1f},{ty(p['tick']):.1f}" for p in trail)
    out.append(f'<polyline points="{pts}" fill="none" stroke="{C["tick"]}" '
               f'stroke-width="1.4" stroke-linejoin="round"/>')

    # 경보선 아래로 내려간 지점만 점으로 찍는다. 선만으로는 어디가 문제인지 눈에 안 들어온다.
    for p in trail:
        if p["tick"] < alert_at:
            out.append(f'<circle cx="{_x(p["t"], dur):.1f}" cy="{ty(p["tick"]):.1f}" '
                       f'r="2.2" fill="{C["med"]}"/>')

    out.append("</svg>")
    return "".join(out)


def score_svg(scores, threshold, height=140) -> str:
    """이상 점수를 시간축에 그리고 임계선을 함께 놓는다.

    비율만 보여 주면 "몇 %" 로 끝나지만, 점수와 임계선을 함께 그리면 아슬아슬하게 갈렸는지
    한참 벗어났는지가 보인다. 임계값을 조정할 여지가 있는지 판단하는 근거가 된다.
    """
    if not scores:
        return ('<p class="empty">채점한 창이 없다. 모델을 학습하지 않았거나 '
                '세션이 창 길이(60초)보다 짧다.</p>')

    pad_t, pad_b = 12, 24
    xs = [s["start_time"] for s in scores]
    ys = [s["score"] for s in scores]
    dur = max(xs) or 1.0
    lo, hi = min(ys + [threshold]), max(ys + [threshold])
    pad_v = (hi - lo) * 0.15 or 0.01
    lo, hi = lo - pad_v, hi + pad_v

    out = [f'<svg viewBox="0 0 {W} {height}" class="chart" role="img">']
    out.append(f'<rect x="{PAD_L}" y="{pad_t}" width="{W - PAD_L - PAD_R}" '
               f'height="{height - pad_t - pad_b}" fill="{C["row"]}"/>')
    out += _grid(dur, pad_t, height - pad_b)

    ty = _map(threshold, lo, hi, height - pad_b, pad_t)
    out.append(f'<line x1="{PAD_L}" y1="{ty:.1f}" x2="{W - PAD_R}" y2="{ty:.1f}" '
               f'stroke="{C["med"]}" stroke-width="1" stroke-dasharray="4 3"/>')
    out.append(f'<text x="{PAD_L - 6}" y="{ty + 3:.1f}" class="ax" text-anchor="end">임계</text>')

    for s in scores:
        x = _x(s["start_time"], dur)
        y = _map(s["score"], lo, hi, height - pad_b, pad_t)
        hit = s.get("is_anomaly")
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{2.4 if hit else 1.5}" '
                   f'fill="{C["anomaly"] if hit else C["faint"]}" '
                   f'opacity="{0.95 if hit else 0.75}"/>')
    out.append("</svg>")
    return "".join(out)


def rules_svg(by_rule, bar_h=22) -> str:
    """규칙별 사건 수를 가로 막대로 그린다. 0건인 규칙도 자리를 지킨다."""
    top = max([by_rule.get(k, 0) for k, _ in RULES] + [1])
    pad_l, pad_r = 92, 46
    height = bar_h * len(RULES) + 6
    out = [f'<svg viewBox="0 0 {W} {height}" class="chart" role="img">']
    for i, (key, label) in enumerate(RULES):
        n = by_rule.get(key, 0)
        y = i * bar_h + 3
        w = _map(n, 0, top, 0, W - pad_l - pad_r)
        out.append(f'<text x="{pad_l - 8}" y="{y + bar_h - 8}" class="lane" '
                   f'text-anchor="end">{_e(label)}</text>')
        out.append(f'<rect x="{pad_l}" y="{y}" width="{W - pad_l - pad_r}" '
                   f'height="{bar_h - 6}" fill="{C["row"]}"/>')
        if n:
            out.append(f'<rect x="{pad_l}" y="{y}" width="{max(w, 2):.1f}" '
                       f'height="{bar_h - 6}" fill="{C["high"]}"/>')
        out.append(f'<text x="{pad_l + max(w, 2) + 7:.1f}" y="{y + bar_h - 8}" '
                   f'class="lane" fill="{C["text"] if n else C["faint"]}">{n}</text>')
    out.append("</svg>")
    return "".join(out)


# ── 판정 ────────────────────────────────────────────────────────────────────

def verdict(summary, anomaly_rate) -> tuple:
    """리포트 맨 위에 놓을 한 줄 판정을 정한다.

    결론을 대신 내리는 것이 아니라 **먼저 볼 곳**을 가리키는 것이 목적이다. 그래서 문구가
    "버그다"가 아니라 "확인하라"다. 판정 근거도 함께 돌려주어 색만 보고 넘어가지 않게 한다.
    """
    events = summary.get("events_total", 0)
    alerts = summary.get("watchdog_alerts", 0)
    sc = summary.get("scenario") or {}
    purpose = sc.get("purpose", "hunt")
    injected = [k for k, v in (sc.get("inject") or {}).items() if v]

    # 주입을 가장 먼저 본다. 결함을 심어 놓고 돌린 세션의 사건은 **찾아낸 버그가 아니라 심어
    # 넣은 것**이다. 그것을 "확인 필요"로 띄우면 리포트가 거짓말을 한다. 순서를 뒤로 두면
    # 사건이 있을 때 빨간 배지가 먼저 걸려 주입 사실이 아래 설정 줄에 묻힌다.
    if injected:
        return (C["anomaly"], "주입 검증",
                f"결함을 심어 놓고 돌린 세션이다({', '.join(injected)}). 여기 나온 사건은 "
                f"찾아낸 버그가 아니라 검출기가 그것을 잡는지 확인하려고 만든 것이다. "
                f"실제 QA 결과로 읽으면 안 된다.")

    if events:
        return (C["high"], "확인 필요",
                f"하드 인바리언트 사건 {events}건. 물리적으로 불가능한 상태가 기록됐다.")

    if alerts:
        return (C["med"], "워치독 경보",
                f"규칙 위반은 없으나 워치독 경보가 {alerts}건이다. 게임 자체가 느려졌거나 멈춘 구간이 있다.")

    if purpose == "probe":
        # 시험지에서는 이상탐지가 반응하는 것이 정상이다. 같은 숫자를 다르게 읽어야 한다.
        return (C["ok"], "시험지",
                f"조건을 의도적으로 흔든 세션이다. 이상 판정 {anomaly_rate:.1f}% 는 "
                f"버그 신호가 아니라 오라클이 반응했다는 뜻이다.")

    if anomaly_rate is not None and anomaly_rate >= BASELINE_RATE * 2:
        return (C["med"], "이상 비율 높음",
                f"이상 판정 {anomaly_rate:.1f}% 로 기준선 {BASELINE_RATE:.0f}% 의 "
                f"{anomaly_rate / BASELINE_RATE:.1f}배다. 규칙은 걸리지 않았다.")

    return (C["ok"], "이상 없음",
            "하드 인바리언트 0건, 워치독 경보 0건이고 이상 비율도 기준선 근처다.")


# ── 조립 ────────────────────────────────────────────────────────────────────

STYLE = f"""
*{{box-sizing:border-box}}
body{{margin:0;padding:26px 28px 44px;background:{C['bg']};color:{C['text']};
  font:13px/1.62 system-ui,-apple-system,'Segoe UI','Malgun Gothic',sans-serif}}
.wrap{{max-width:1040px;margin:0 auto}}
h1{{font-size:19px;margin:0 0 3px;letter-spacing:-.2px}}
h2{{font-size:14.5px;margin:30px 0 0;font-weight:700;letter-spacing:-.2px;
  display:inline-block;padding:7px 15px 6px;border-radius:6px 6px 0 0;
  border:1px solid {C['line']};border-bottom:none;position:relative;top:1px}}
h2 small{{font-weight:500;font-size:11px;opacity:.72;margin-left:8px;letter-spacing:0}}
.sub{{color:{C['dim']};font-size:12px;margin:0 0 16px}}
.verdict{{display:flex;align-items:baseline;gap:12px;padding:12px 15px;border-radius:6px;
  background:{C['panel']};border:1px solid {C['line']};border-left:4px solid;margin:0 0 18px}}
.verdict b{{font-size:15px;white-space:nowrap}}
.verdict span{{color:{C['dim']};font-size:12px}}
.cards{{display:flex;gap:9px;margin:0 0 6px;flex-wrap:wrap}}
.card{{flex:1;min-width:132px;background:{C['panel']};border:1px solid {C['line']};
  border-radius:6px;padding:11px 13px}}
.card .k{{color:{C['dim']};font-size:10.5px;text-transform:uppercase;letter-spacing:.07em}}
.card .v{{font-size:22px;font-weight:650;margin-top:3px;
  font-family:ui-monospace,Menlo,Consolas,monospace}}
.card .n{{color:{C['faint']};font-size:10.5px;margin-top:1px}}
.chart{{width:100%;height:auto;display:block;background:{C['panel']};
  border:1px solid {C['line']};border-radius:0 6px 6px 6px;padding:6px 0}}
.stack .chart{{border-radius:0}}
.stack .chart:first-child{{border-radius:6px 6px 0 0}}
.stack .chart:last-child{{border-radius:0 0 6px 6px;border-top:none}}
.ax{{fill:{C['faint']};font-size:9.5px;font-family:ui-monospace,Menlo,Consolas,monospace}}
.lane{{fill:{C['dim']};font-size:10px;font-family:ui-monospace,Menlo,Consolas,monospace}}
table{{width:100%;border-collapse:collapse;font-size:12px;margin-top:8px}}
th,td{{text-align:left;padding:6px 9px;border-bottom:1px solid {C['line']}}}
th{{color:{C['dim']};font-weight:700;font-size:10.5px;text-transform:uppercase;
  letter-spacing:.06em}}
td.n{{font-family:ui-monospace,Menlo,Consolas,monospace}}
.legend{{color:{C['dim']};font-size:11px;margin:7px 0 0;display:flex;gap:15px;flex-wrap:wrap}}
.legend i{{display:inline-block;width:10px;height:10px;border-radius:2px;
  margin-right:5px;vertical-align:-1px}}
.empty{{color:{C['faint']};font-size:12px;padding:14px;background:{C['panel']};
  border:1px solid {C['line']};border-radius:6px;margin:0}}
.warn{{color:{C['text']};font-size:12px;padding:9px 12px;margin:0 0 8px;border-radius:5px;
  background:#f3eefb;border:1px solid #ddd0f0}}
.kv{{columns:2;column-gap:26px;font-size:11.5px;color:{C['dim']}}}
.kv div{{break-inside:avoid;padding:1.5px 0}}
.kv b{{color:{C['text']};font-weight:600}}
footer{{margin-top:30px;padding-top:12px;border-top:1px solid {C['line']};
  color:{C['faint']};font-size:11px}}
@media print{{body{{padding:0}} h2{{margin-top:16px}}
  .chart,.card,.verdict{{break-inside:avoid}}}}
"""


def render(summary: dict, events: list, scores: list = None,
           watchdog: dict = None) -> str:
    """세션 리포트 HTML 을 문자열로 만든다.

    summary 는 세션 요약, events 는 사건 목록, scores 는 이상 채점 결과, watchdog 은
    관측 기록과 경보다. 뒤의 둘은 없어도 되며(모델이 없거나 옛 세션) 그 구역만 빈다.
    """
    scores = scores or []
    watchdog = watchdog or {}
    sid = summary.get("session_id", "?")
    sc = summary.get("scenario") or {}
    duration = summary.get("last_game_time") or 0
    scored = summary.get("anomaly_windows_scored", len(scores))
    n_anom = summary.get("anomaly_count", sum(1 for s in scores if s.get("is_anomaly")))
    rate = (n_anom / scored * 100) if scored else None
    color, title, why = verdict(summary, rate or 0.0)

    started = summary.get("started_at")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(started)) if started else "-"
    real = (summary.get("ended_at", 0) - started) if started else None

    o = ['<!doctype html><html lang="ko"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f'<title>QA 리포트 {_e(sid)}</title><style>{STYLE}</style></head>',
         '<body><div class="wrap">']

    # ── 머리말 ──
    o.append(f'<h1>QA 세션 리포트 · {_e(sid)}</h1>')
    purpose = {"baseline": "기준", "probe": "시험지", "hunt": "탐색"}.get(sc.get("purpose"), "")
    o.append(f'<p class="sub">{_e(sc.get("name", "시나리오 없음"))}'
             f'{" · " + purpose if purpose else ""} · {when} · '
             f'실제 {_clock(real)} / 게임 시간 {_clock(duration)}</p>')
    o.append(f'<div class="verdict" style="border-left-color:{color}">'
             f'<b style="color:{color}">{title}</b><span>{_e(why)}</span></div>')

    # 주입이 켜져 있으면 사건 표 바로 위에서도 다시 알린다. 리포트를 스크롤해 내려가다
    # 표만 보는 사람이 있기 때문이다. 한 번만 적어 두면 그 사람에게는 없는 것과 같다.
    injected = [k for k, v in (sc.get("inject") or {}).items() if v]

    # ── 핵심 숫자 ──
    trail = [p for p in watchdog.get("trail", []) if p.get("tick") is not None]
    tick_min = min((p["tick"] for p in trail), default=None)
    o.append('<div class="cards">')
    o.append(f'<div class="card"><div class="k">사건</div><div class="v">'
             f'{summary.get("events_total", 0)}</div>'
             f'<div class="n">원시 탐지 {_num(summary.get("raw_detections", 0))}건</div></div>')
    o.append(f'<div class="card"><div class="k">이상 판정</div><div class="v">'
             f'{"-" if rate is None else f"{rate:.1f}%"}</div>'
             f'<div class="n">창 {_num(scored)}개 중 {n_anom}개</div></div>')
    o.append(f'<div class="card"><div class="k">워치독</div><div class="v">'
             f'{summary.get("watchdog_alerts", 0)}</div>'
             f'<div class="n">경보 · 최저 틱 {"-" if tick_min is None else f"{tick_min:.1f}"}</div></div>')
    o.append(f'<div class="card"><div class="k">표본</div><div class="v">'
             f'{_num(summary.get("samples_processed", 0))}</div>'
             f'<div class="n">텔레메트리 줄</div></div>')
    o.append('</div>')

    # ── 타임라인 + 서버 틱 (같은 시간축) ──
    o.append(_h2("타임라인", C["text"], "#eef2f0", "무엇이 언제 일어났는가"))
    o.append('<div class="stack">')
    o.append(timeline_svg(events, scores, watchdog, duration))
    o.append(tick_svg(watchdog, duration))
    o.append('</div>')
    o.append(f'<p class="legend">'
             f'<span><i style="background:{C["high"]}"></i>사건</span>'
             f'<span><i style="background:{C["anomaly"]};opacity:.35"></i>이상 판정 창</span>'
             f'<span><i style="background:{C["med"]}"></i>워치독 경보</span>'
             f'<span><i style="background:{C["tick"]}"></i>서버 틱</span></p>'
             f'<p class="legend"><span>세 오라클을 같은 시간축에 놓았다. 사건이 틱이 떨어진 구간과 '
             f'겹친다면 봇의 문제가 아니라 서버가 멈춰 그렇게 보인 것일 수 있다.</span></p>')

    # ── 이상탐지 ──
    o.append(_h2("이상탐지", C["anomaly"], "#f4effb", "정상 플레이에서 벗어난 정도"))
    thr = summary.get("anomaly_threshold")
    if scores and thr is not None:
        o.append(score_svg(scores, thr))
        worst = sorted((s for s in scores if s.get("is_anomaly")), key=lambda s: s["score"])[:6]
        if worst:
            o.append('<table><tr><th>봇</th><th>구간</th><th>점수</th><th>주 원인</th></tr>')
            for s in worst:
                o.append(f'<tr><td class="n">BOT {s["entity_id"]}</td>'
                         f'<td class="n">{_clock(s["start_time"])}~{_clock(s["end_time"])}</td>'
                         f'<td class="n">{s["score"]:.4f}</td>'
                         f'<td>{_e(s.get("top_feature_label", ""))} '
                         f'{s.get("top_feature_z", 0):+.1f}σ</td></tr>')
            o.append('</table>')
    else:
        o.append(score_svg(scores, thr or 0))

    # ── 하드 인바리언트 ──
    o.append(_h2("하드 인바리언트", C["high"], "#fceded", "물리적으로 불가능한 상태"))
    if injected:
        o.append(f'<p class="warn">아래 사건은 <b>{_e(", ".join(injected))}</b> 주입으로 만들어진 '
                 f'것이다. 검출기가 그 결함을 잡는지 확인하는 용도이며 게임의 버그가 아니다.</p>')
    o.append(rules_svg(summary.get("events_by_rule", {})))
    if events:
        o.append('<table><tr><th>봇</th><th>규칙</th><th>구간</th><th>지속</th>'
                 '<th>프레임</th><th>극값</th></tr>')
        for ev in sorted(events, key=lambda e: e["start_time"]):
            peak = "" if ev.get("peak_value") is None else \
                f'{_e(ev.get("peak_label", ""))} {ev["peak_value"]}'
            o.append(f'<tr><td class="n">BOT {ev["entity_id"]}</td><td>{_e(ev["rule"])}</td>'
                     f'<td class="n">{ev["start_time"]:.2f}~{ev["end_time"]:.2f}초</td>'
                     f'<td class="n">{ev["duration"]:.2f}초</td>'
                     f'<td class="n">{ev.get("hits", "")}</td><td class="n">{peak}</td></tr>')
        o.append('</table>')

    # ── 워치독 경보 목록 ──
    alerts = watchdog.get("alerts", [])
    if alerts:
        o.append(_h2("워치독 경보", C["med"], "#fdf3e3", "게임 자체가 느려지거나 멈춤"))
        o.append('<table><tr><th>종류</th><th>시각</th><th>지속</th><th>상세</th></tr>')
        for a in alerts:
            g = _wall_to_game(watchdog.get("trail", []), a.get("started_at"))
            extra = ", ".join(f"{k} {v}" for k, v in a.items()
                              if k not in ("kind", "started_at", "ended_at", "duration", "ongoing"))
            o.append(f'<tr><td>{_e(ALERT_LABEL.get(a.get("kind"), a.get("kind")))}</td>'
                     f'<td class="n">{_clock(g)}</td>'
                     f'<td class="n">{a.get("duration", 0)}초{" (진행 중)" if a.get("ongoing") else ""}</td>'
                     f'<td class="n">{_e(extra)}</td></tr>')
        o.append('</table>')

    # ── 조건과 설정 ──
    o.append(_h2("조건과 판정 설정", C["dim"], "#f1f4f2", "이 결과가 나온 조건"))
    o.append('<div class="kv">')
    m = sc.get("match", {})
    bots = sc.get("roster", {}).get("bots", [])
    if m:
        o.append(f'<div>맵 <b>{_e(m.get("map", "-"))}</b></div>')
        o.append(f'<div>게임 타입 <b>{m.get("gametype", "-")}</b></div>')
        o.append(f'<div>fraglimit / timelimit <b>{m.get("fraglimit")} / {m.get("timelimit")}</b></div>')
    if bots:
        skills = sorted({b["skill"] for b in bots})
        o.append(f'<div>봇 <b>{len(bots)}마리</b> (실력 {", ".join(f"{s:g}" for s in skills)})</div>')
    on = [k for k, v in (sc.get("inject") or {}).items() if v]
    o.append(f'<div>결함 주입 <b>{", ".join(on) if on else "없음"}</b></div>')
    for k, v in (sc.get("cvars") or {}).items():
        o.append(f'<div>{_e(k)} <b>{_e(v)}</b></div>')
    cfg = summary.get("config", {})
    b = cfg.get("bounds", {})
    if b:
        o.append(f'<div>경계 x <b>[{b.get("min_x")}, {b.get("max_x")}]</b></div>')
        o.append(f'<div>경계 y <b>[{b.get("min_y")}, {b.get("max_y")}]</b></div>')
        o.append(f'<div>바닥 / 천장 <b>{b.get("floor_z")} / {b.get("ceiling_z")}</b></div>')
    o.append(f'<div>최대 속력 <b>{cfg.get("max_speed", "-")}</b></div>')
    o.append(f'<div>끼임 판정 <b>{cfg.get("stuck_seconds", "-")}초</b></div>')
    win = cfg.get("activity_window_seconds")
    if win is not None:
        o.append(f'<div>이상탐지 창 <b>{win:g}초</b></div>')
    tgt = watchdog.get("target_tick")
    if tgt:
        o.append(f'<div>목표 서버 틱 <b>{tgt:g}</b></div>')
    o.append('</div>')

    o.append('<footer>이 리포트는 결론이 아니라 사람이 확인할 단서다. '
             '판정에 쓴 경계값과 임계값을 함께 실어, 나중에 같은 텔레메트리로 다시 따져볼 수 있게 했다.'
             '<br>원본 텔레메트리 사본과 사건 목록은 세션 폴더에 그대로 남아 있다.</footer>')
    o.append('</div></body></html>')
    return "".join(o)


# ── 여러 세션 비교 ──────────────────────────────────────────────────────────

def render_compare(summaries: list) -> str:
    """여러 세션을 한 표로 비교하는 리포트를 만든다.

    세션 하나짜리 리포트로는 답할 수 없는 질문이 있다. "학습에 쓴 판 수를 늘리면 오탐률이
    내려가는가" 같은 것이다. 그것은 세션 사이의 차이라서 한 세션 안에서는 보이지 않는다.
    표로 나란히 놓으면 어디서 평평해지는지가 보인다.

    용도(purpose)를 열에 두는 것이 중요하다. 같은 이상 비율이 기준 세션에서는 오탐이고
    시험지에서는 정답이라, 그 구분 없이 숫자만 늘어놓으면 잘못 읽힌다.
    """
    rows = []
    for s in summaries:
        sc = s.get("scenario") or {}
        scored = s.get("anomaly_windows_scored") or 0
        n = s.get("anomaly_count") or 0
        rows.append({
            "id": s.get("session_id", "?"),
            "name": sc.get("name", "-"),
            "purpose": {"baseline": "기준", "probe": "시험지",
                        "hunt": "탐색"}.get(sc.get("purpose"), "-"),
            "map": (sc.get("match") or {}).get("map", "-"),
            "bots": len((sc.get("roster") or {}).get("bots", [])) or "-",
            "skill": ", ".join(f'{b["skill"]:g}' for b in
                               sorted({b["skill"] for b in (sc.get("roster") or {}).get("bots", [])})) or "-",
            "dur": s.get("last_game_time") or 0,
            "events": s.get("events_total", 0),
            "rate": (n / scored * 100) if scored else None,
            "scored": scored,
            "alerts": s.get("watchdog_alerts", 0),
            "injected": bool([k for k, v in (sc.get("inject") or {}).items() if v]),
        })
    rows.sort(key=lambda r: r["id"])

    o = ['<!doctype html><html lang="ko"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f'<title>QA 세션 비교</title><style>{STYLE}</style></head><body><div class="wrap">']
    o.append(f'<h1>QA 세션 비교 · {len(rows)}개</h1>')
    o.append(f'<p class="sub">{time.strftime("%Y-%m-%d %H:%M")} 생성</p>')

    o.append('<table><tr><th>세션</th><th>시나리오</th><th>용도</th><th>맵</th>'
             '<th>봇</th><th>실력</th><th>길이</th><th>사건</th><th>이상 비율</th>'
             '<th>워치독</th></tr>')
    for r in rows:
        # 이상 비율은 용도에 따라 뜻이 다르므로 색으로 미리 갈라 준다.
        if r["rate"] is None:
            cell = "-"
        elif r["purpose"] == "시험지":
            cell = f'<span style="color:{C["anomaly"]}">{r["rate"]:.1f}%</span>'
        elif r["rate"] >= BASELINE_RATE * 2:
            cell = f'<span style="color:{C["med"]}">{r["rate"]:.1f}%</span>'
        else:
            cell = f'{r["rate"]:.1f}%'
        note = ' <span style="color:%s">주입</span>' % C["anomaly"] if r["injected"] else ""
        o.append(f'<tr><td class="n">{_e(r["id"])}</td><td>{_e(r["name"])}{note}</td>'
                 f'<td>{_e(r["purpose"])}</td><td class="n">{_e(r["map"])}</td>'
                 f'<td class="n">{r["bots"]}</td><td class="n">{_e(r["skill"])}</td>'
                 f'<td class="n">{_clock(r["dur"])}</td>'
                 f'<td class="n">{r["events"]}</td><td class="n">{cell}</td>'
                 f'<td class="n">{r["alerts"]}</td></tr>')
    o.append('</table>')

    # 기준 세션만 모아 평균을 낸다. 시험지가 섞이면 평균이 무의미해진다.
    base = [r for r in rows if r["purpose"] == "기준" and r["rate"] is not None]
    if base:
        avg = sum(r["rate"] for r in base) / len(base)
        o.append(f'<p class="legend"><span>기준 세션 {len(base)}개의 이상 비율 평균 '
                 f'<b style="color:{C["text"]}">{avg:.1f}%</b> · 기준선 {BASELINE_RATE:.0f}%. '
                 f'임계값을 학습 데이터의 {BASELINE_RATE:.0f}백분위로 잡으므로 그 값이 바닥이다.</span></p>')

    o.append('<footer>이상 비율은 용도에 따라 뜻이 다르다. 기준 세션에서는 오탐률이고, '
             '시험지에서는 오라클이 반응했다는 뜻이라 높은 것이 정답이다. '
             '주입 표시가 붙은 세션의 사건은 찾아낸 버그가 아니라 심어 넣은 것이다.</footer>')
    o.append('</div></body></html>')
    return "".join(o)
