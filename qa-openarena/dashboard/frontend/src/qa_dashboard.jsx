import React, { useState, useEffect, useMemo, useCallback } from "react";

/*
  OpenArena QA Monitor — 대시보드 (파이썬 API 연결, 3열 구조)

  좌: 규칙별 집계 + 이상탐지 자리(준비 중)
  중앙: 버그 사건 목록 (항목 클릭 가능)
  우: 상세 보기 패널 (시작 순간 상태 + 가장 심했던 순간)

  데이터 소스는 FastAPI 서버(dashboard/server/app.py)의 /api/events 다.
  서버가 프레임 단위 탐지를 (봇, 규칙) 단위 사건으로 묶어 보내므로, 이 화면이 다루는 단위는
  "탐지"가 아니라 "사건"이다. 집계 전 원시 탐지는 서버의 /api/detections 에 그대로 남아 있다.

  실시간이다. 서버가 텔레메트리 파일 끝을 따라가며 새 줄만 읽고, 이 화면은 1초 간격으로
  현재 상태를 받아온다. 게임을 켜 둔 채로 화면이 스스로 갱신된다.
*/

// == 데이터 소스 ==
// 서버 주소. uvicorn 기본값(127.0.0.1:8000)을 가리킨다. 포트를 바꿔 띄웠다면 여기만 고치면 된다.
const API_BASE = "http://127.0.0.1:8000";

// 정상 동작 중 서버에 새 상태를 물어보는 간격(밀리초).
// 서버가 파일 끝의 새 줄만 읽게 되면서 요청 한 번의 비용이 '파일 전체'에서 '새로 생긴 줄'로
// 줄었기 때문에, 이 주기가 감당 가능해졌다. 정적 시절에는 요청마다 전체 재검출이라 넣지 않았다.
const POLL_MS = 1000;

// 서버에 연결하지 못했을 때 다시 시도하는 간격(밀리초). 정상 주기보다 느슨하게 잡아,
// 서버가 꺼져 있는 동안 불필요한 요청을 촘촘히 보내지 않는다.
const RETRY_MS = 3000;

// 목록에 실제로 그릴 최대 행 수.
// 서버도 같은 수만큼만 실어 보내므로(limit) 실제로는 서버가 자른 것을 그대로 그리는 셈이다.
// 사건 자체는 서버 메모리와 세션 파일에 전부 남아 있고, 여기서 자르는 것은 화면에 그리는 양뿐이다.
const MAX_ROWS = 300;

/*
  서버가 보낸 사건 하나를 화면이 쓰는 형태로 변환한다.

  서버 응답은 화면 전용 포맷이 아니라 API 계약이므로, 화면 표기 편의로 서버 쪽 이름을 줄이지
  않고 여기서 흡수한다. 나중에 이상탐지 오라클이 같은 엔드포인트에 섞여 들어와도 이 함수
  한 곳만 손보면 된다.
*/
function normalizeEvent(raw) {
  return {
    id: raw.event_id,          // 목록 렌더링 키로 쓰는 사건 번호
    entity: raw.entity_id,     // 어느 봇인지
    rule: raw.rule,            // 위반한 규칙 이름
    sev: raw.severity,         // "HIGH" / "MEDIUM"
    start: raw.start_time,     // 사건이 시작된 게임 시간(초)
    end: raw.end_time,         // 사건이 마지막으로 관측된 게임 시간(초)
    duration: raw.duration,    // 지속 시간(초)
    hits: raw.hits,            // 이 사건을 이루는 프레임 단위 탐지 수
    msg: raw.message,          // 가장 최근 탐지의 설명
    first: raw.first_sample || {},   // 사건이 시작된 순간의 상태값
    peakValue: raw.peak_value,       // 규칙별 극값(가장 심했던 수치)
    peakLabel: raw.peak_label,       // 그 극값의 이름(예: "최저 z")
    peak: raw.peak_sample || {},     // 극값이 관측된 순간의 상태값
    ongoing: raw.ongoing,            // 스트림 끝까지 이어지던 사건인지
  };
}

const C = {
  bg: "#0D1512", panel: "#121D17", panelHi: "#17241C", row: "#141F19",
  line: "#26362C", text: "#D2DED6", dim: "#6C7C72", faint: "#3F4E45",
  cyan: "#54C68A", high: "#E5595A", med: "#DFA13F", ok: "#54C68A", anomaly: "#B48AE8",
};
const MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
const SANS = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";

// 좌측 집계 패널에 고정으로 보여줄 규칙 목록.
// 사건이 0건인 규칙도 자리를 지켜야 "그 규칙은 안 걸렸다"는 정보가 되므로 목록을 고정한다.
const RULES = [
  { id: "fell_through_floor", sev: "HIGH" },
  { id: "out_of_bounds", sev: "HIGH" },
  { id: "health_out_of_range", sev: "HIGH" },
  { id: "impossible_velocity", sev: "MEDIUM" },
  { id: "stuck", sev: "MEDIUM" },
];
const RULE_KR = {
  fell_through_floor: "바닥 관통", out_of_bounds: "경계 이탈", health_out_of_range: "체력 초과",
  impossible_velocity: "과속", stuck: "끼임",
};
// 모르는 규칙 이름이 와도 화면이 비지 않도록, 한글 이름이 없으면 규칙 id 를 그대로 쓴다.
const ruleLabel = (id) => RULE_KR[id] || id;

const pad = (n) => String(n).padStart(2, "0");
// 게임 시간(초)을 mm:ss 로 표기한다. 값이 없을 때는 자리만 지킨다.
const clock = (s) => (typeof s === "number" ? `${pad(Math.floor(s / 60))}:${pad(Math.floor(s % 60))}` : "--:--");
// 숫자를 소수 첫째 자리까지만 표기한다. 값이 없으면 대시로 표기한다.
const num = (v) => (typeof v === "number" ? v.toFixed(1) : "—");
// 지속 시간을 짧게 표기한다. 한 프레임짜리 사건은 0초가 되므로 그 경우를 따로 적는다.
const dur = (s) => (typeof s !== "number" ? "—" : s < 0.05 ? "순간" : `${s.toFixed(1)}초`);
// 바이트 수를 사람이 읽는 단위로 바꾼다. 텔레메트리 사본 크기 표시에 쓴다.
const bytes = (n) => {
  if (!n) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

export default function QADashboard() {
  const [events, setEvents] = useState([]);        // 서버에서 받아 변환한 사건 전체 목록(최신순)
  const [rawCount, setRawCount] = useState(0);     // 집계 전 원시 탐지 수
  const [gapSeconds, setGapSeconds] = useState(null); // 서버가 사건을 묶은 시간 기준
  const [status, setStatus] = useState("loading"); // "loading" | "ok" | "error"
  const [errorMsg, setErrorMsg] = useState("");    // 실패했을 때 화면에 보여줄 이유
  const [reconnecting, setReconnecting] = useState(false); // 연결 자체가 안 돼 자동 재시도 중인지
  const [source, setSource] = useState("");        // 서버가 읽은 텔레메트리 파일 경로
  const [lastTime, setLastTime] = useState(null);  // 텔레메트리가 기록된 마지막 게임 시간
  const [samples, setSamples] = useState(0);       // 서버가 지금까지 처리한 텔레메트리 줄 수
  const [skipped, setSkipped] = useState(0);       // 파싱 실패로 건너뛴 줄 수
  const [sessionId, setSessionId] = useState(null); // 현재 세션 식별자(파일 이름과 같다)
  const [archived, setArchived] = useState(0);     // 세션 폴더에 복사해 둔 텔레메트리 바이트
  const [anomaly, setAnomaly] = useState(null);    // 이상탐지 상태와 결과 목록
  const [watchdog, setWatchdog] = useState(null);  // 워치독(성능·크래시) 상태
  const [fetchedAt, setFetchedAt] = useState(null); // 마지막으로 받아온 시각(브라우저 기준)
  /*
    상세 패널의 선택은 사건 객체가 아니라 사건 번호로 들고 있다.
    1초마다 목록을 새로 받으므로, 객체를 붙들고 있으면 갱신될 때마다 참조가 끊겨 패널이 닫힌다.
    번호로 들고 매번 현재 목록에서 찾으면, 진행 중인 사건의 지속 시간과 프레임 수가 패널에서도
    실시간으로 늘어난다.
  */
  /*
    상세 패널의 선택이다. { kind: "event" | "anomaly", id } 형태로 들고 있다.
    종류를 함께 두는 이유는 사건과 이상 항목이 서로 다른 목록에서 오고 번호도 각자 매기기
    때문이다. 번호만으로는 어느 쪽인지 알 수 없다.
  */
  const [selectedId, setSelectedId] = useState(null);
  const [paused, setPaused] = useState(false);  // 탭이 가려졌거나 사용자가 멈춘 상태인지
  const [notice, setNotice] = useState("");     // 내보내기·새 세션 결과를 잠깐 보여주는 문구

  /*
    서버에서 사건 목록을 받아온다.
    useCallback 으로 감싼 이유는 최초 마운트 시(useEffect)와 다시 불러오기 버튼에서
    같은 함수를 재사용하기 위해서다.
    signal 은 컴포넌트가 사라졌을 때 진행 중인 요청을 취소하는 데 쓴다.
  */
  const load = useCallback(async (signal) => {
    setStatus("loading");
    setErrorMsg("");
    try {
      const res = await fetch(`${API_BASE}/api/events`, { signal });
      if (!res.ok) {
        // 서버는 떠 있지만 응답 코드가 정상이 아닌 경우(예: 500)다. 원인을 구분해서 알려준다.
        throw new Error(`서버가 ${res.status} 를 반환했다`);
      }
      const data = await res.json();
      // 서버 응답은 시작 시간 순(오래된 것부터)이다. 목록은 최신이 위로 오는 편이 읽기 쉬우므로 뒤집는다.
      const list = (data.events || []).map(normalizeEvent).reverse();
      setEvents(list);
      setRawCount(data.raw_count || 0);
      setGapSeconds(typeof data.gap_seconds === "number" ? data.gap_seconds : null);
      setLastTime(typeof data.last_time === "number" ? data.last_time : null);
      setSamples(data.samples || 0);
      setSkipped(data.skipped_lines || 0);
      setSessionId(data.session_id || null);
      setArchived(data.telemetry_bytes || 0);
      setAnomaly(data.anomaly || null);
      setWatchdog(data.watchdog || null);
      setSource(data.source || "");
      setFetchedAt(new Date());
      setStatus("ok");
      setReconnecting(false); // 연결됐으므로 자동 재시도를 멈춘다.
      // 선택은 건드리지 않는다. 사건 번호로 들고 있으므로 목록이 갱신돼도 그대로 유지된다.
    } catch (err) {
      if (err.name === "AbortError") return; // 화면을 떠나며 취소된 요청은 오류가 아니다.
      setStatus("error");
      /*
        실패를 두 가지로 나눈다. 대응이 다르기 때문이다.
        - 서버에 연결 자체가 안 됨: fetch 가 TypeError 로 실패한다. 서버가 아직 안 떠 있다는 뜻이므로
          사람이 할 일은 없고 기다리면 된다. 자동 재시도 대상이다.
        - 서버가 오류 응답을 줌: 서버는 살아 있는데 안에서 뭔가 잘못됐다는 뜻이다. 자동으로 계속
          찔러 봐야 상황이 바뀌지 않고, 오히려 문제를 감춘다. 사람이 서버 로그를 봐야 하므로
          자동 재시도하지 않고 수동 재시도로 남긴다.
      */
      const unreachable = err instanceof TypeError;
      setReconnecting(unreachable);
      setErrorMsg(
        unreachable
          ? "서버에 연결하지 못했다. uvicorn 이 떠 있는지 확인한다."
          : err.message
      );
    }
  }, []);

  // 화면이 처음 뜰 때 한 번 받아온다.
  useEffect(() => {
    const ac = new AbortController();
    load(ac.signal);
    return () => ac.abort(); // 컴포넌트가 사라지면 진행 중인 요청을 취소한다.
  }, [load]);

  /*
    주기적으로 서버에 새 상태를 물어본다.

    setInterval 이 아니라 매번 setTimeout 을 새로 거는 방식을 쓴다. setInterval 은 응답이
    늦어져도 다음 요청을 그대로 쏘기 때문에, 서버가 밀리면 요청이 겹쳐 쌓인다. 이 방식은
    한 번의 요청이 끝난 뒤에야 다음 타이머를 걸므로 겹치지 않는다.

    간격은 상태에 따라 다르다. 정상이면 POLL_MS, 연결 실패면 RETRY_MS 로 느슨하게 잡아
    서버가 꺼져 있는 동안 요청을 촘촘히 보내지 않는다.

    status 가 loading 인 동안에는 타이머를 걸지 않는다. 요청이 끝나면 status 가 바뀌면서
    이 효과가 다시 돌아 다음 타이머를 건다.
  */
  useEffect(() => {
    if (status === "loading") return;                 // 요청 중이면 다음 타이머를 걸지 않는다.
    if (status === "error" && !reconnecting) return;  // 서버 오류는 수동 재시도로 남긴다.
    if (paused) return;                               // 사용자가 멈춰 두었으면 요청하지 않는다.
    const wait = status === "error" ? RETRY_MS : POLL_MS;
    const t = setTimeout(() => load(), wait);
    return () => clearTimeout(t);  // 상태가 바뀌거나 화면을 떠나면 예약된 요청을 취소한다.
  }, [status, reconnecting, paused, load]);

  /*
    브라우저 탭이 가려져 있는 동안에는 폴링을 멈춘다.
    화면이 안 보이는데 매초 요청을 보내면 서버와 브라우저 양쪽에서 쓸데없이 일한다.
    저사양 환경에서는 이 정도 절약도 의미가 있다.
  */
  useEffect(() => {
    const onVisibility = () => setPaused(document.hidden);
    document.addEventListener("visibilitychange", onVisibility);
    onVisibility();  // 처음 뜰 때의 상태도 반영한다.
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  /*
    규칙별 사건 수와 헤더 표시값을 전체 목록에서 계산한다.
    화면에 그리는 행은 MAX_ROWS 로 잘리지만, 이 숫자들은 자르기 전 전체를 대상으로 한다.
    useMemo 를 쓴 이유는 렌더마다 다시 세지 않게 하기 위해서다.
  */
  const stats = useMemo(() => {
    const counts = {};      // 규칙 id → 사건 수
    const bots = new Set(); // 등장한 봇(엔티티) 번호 모음
    for (const e of events) {
      counts[e.rule] = (counts[e.rule] || 0) + 1;
      bots.add(e.entity);
    }
    return { counts, botCount: bots.size, total: events.length };
  }, [events]);

  // 집계 막대의 길이를 정하는 기준값. 사건이 가장 많은 규칙을 100%로 잡는다.
  const maxCount = Math.max(1, ...Object.values(stats.counts));
  // 실제로 그릴 행만 잘라 낸다.
  const visible = events.slice(0, MAX_ROWS);
  // 선택된 항목을 현재 목록에서 매번 다시 찾는다. 진행 중인 사건이면 값이 계속 갱신된다.
  const selected = selectedId && selectedId.kind === "event"
    ? events.find((e) => e.id === selectedId.id) || null
    : null;
  const selectedAnomaly = selectedId && selectedId.kind === "anomaly" && anomaly
    ? (anomaly.items || []).find((a) => a.id === selectedId.id) || null
    : null;

  /*
    내보내기와 새 세션은 서버 상태를 바꾸는 동작이라 POST 로 보낸다.
    끝나면 곧바로 목록을 다시 받아와 화면과 서버 상태를 맞춘다.
  */
  const command = useCallback(async (path, label) => {
    try {
      const res = await fetch(`${API_BASE}${path}`, { method: "POST" });
      if (!res.ok) throw new Error(`서버가 ${res.status} 를 반환했다`);
      const data = await res.json();
      // 내보내기는 파일 경로를, 새 세션은 이전 세션의 경로를 돌려준다.
      // 내보내기는 방금 마감한 세션의 경로를, 새 세션은 previous 안에 이전 세션 경로를 돌려준다.
      const done = data.events_file ? data : (data.previous || {});
      const parts = [];
      if (done.events_file) parts.push(done.events_file);
      // 텔레메트리 사본이 남았으면 함께 알린다. 원본은 맵 전환 때 지워지므로 이쪽이 원본 역할을 한다.
      if (done.telemetry_bytes) parts.push(`텔레메트리 사본 ${bytes(done.telemetry_bytes)}`);
      setNotice(parts.length ? `${label} 완료 · ${parts.join(" · ")}` : `${label} 완료`);
      load();  // 서버 상태가 바뀌었으므로 즉시 다시 받아온다.
    } catch (err) {
      setNotice(`${label} 실패 · ${err.message}`);
    }
  }, [load]);

  // 안내 문구는 잠시 뒤 스스로 사라지게 한다. 남아 있으면 오래된 정보가 화면에 붙어 있게 된다.
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(""), 6000);
    return () => clearTimeout(t);
  }, [notice]);

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: SANS, padding: 18 }}>
      <style>{`
        @keyframes qaPulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.82)}}
        .qa-row{cursor:pointer}
        .qa-row:hover{background:${C.panelHi}!important}
        .qa-log::-webkit-scrollbar,.qa-detail::-webkit-scrollbar{width:8px}
        .qa-log::-webkit-scrollbar-thumb,.qa-detail::-webkit-scrollbar-thumb{background:${C.line};border-radius:4px}
        @media (prefers-reduced-motion:reduce){.qa-pulse{animation:none!important}}
      `}</style>

      <div style={{ maxWidth: 1280, margin: "0 auto" }}>
        {/* 헤더 */}
        <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", borderBottom: `1px solid ${C.line}`, paddingBottom: 14, marginBottom: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 13 }}>
            <Crosshair />
            <div>
              <div style={{ fontFamily: MONO, fontSize: 15, letterSpacing: 2, fontWeight: 600 }}>OPENARENA<span style={{ color: C.cyan }}> · </span>QA MONITOR</div>
              <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: 1, marginTop: 2 }}>봇 텔레메트리 탐지 · 하드 인바리언트 오라클</div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
            {/* 사람이 검토할 실제 단위. 프레임 중복이 묶인 뒤의 수다. */}
            <Meta label="사건" value={String(stats.total)} />
            {/* 집계 전 원시 탐지 수. 얼마나 묶였는지가 보여야 집계가 감추는 게 없다는 것이 드러난다. */}
            <Meta label="원시 탐지" value={rawCount.toLocaleString()} />
            {/* 텔레메트리에 등장한 봇 수. 하드코딩이 아니라 실제 데이터에서 센 값이다. */}
            <Meta label="봇" value={String(stats.botCount)} />
            {/* 텔레메트리의 마지막 게임 시간. 실시간이면 이 값이 계속 올라간다. */}
            <Meta label="게임 시간" value={clock(lastTime)} />
            <LiveLamp status={status} reconnecting={reconnecting} paused={paused} />
            <div style={{ display: "flex", gap: 6 }}>
              {/* 진행 중인 사건까지 파일에 확정 기록한다. 닫힌 사건은 이미 자동으로 기록돼 있다. */}
              <SmallButton onClick={() => command("/api/export", "내보내기")}>내보내기</SmallButton>
              {/* 새 매치를 시작할 때 쓴다. 현재 세션을 마감하고 텔레메트리를 처음부터 다시 읽는다. */}
              <SmallButton onClick={() => command("/api/reset", "새 세션")}>새 세션</SmallButton>
            </div>
          </div>
        </header>

        {/* 워치독: 게임 프로세스 상태. 봇이 아니라 게임 자체를 보는 층이라 별도 줄에 둔다. */}
        <WatchdogStrip data={watchdog} />

        {/* 내보내기·새 세션 결과 안내. 몇 초 뒤 스스로 사라진다. */}
        {notice && (
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.cyan, background: C.panel, border: `1px solid ${C.line}`, borderRadius: 6, padding: "7px 11px", marginBottom: 12 }}>
            {notice}
          </div>
        )}

        {/* 3열 본문 */}
        <section style={{ display: "grid", gridTemplateColumns: "230px 1fr 320px", gap: 12, alignItems: "start" }}>

          {/* 좌: 규칙별 집계 + 이상탐지 자리 */}
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <Panel title="규칙별 사건" tag="BY RULE">
              <div style={{ display: "flex", flexDirection: "column", gap: 10, paddingTop: 2 }}>
                {RULES.map((r) => {
                  const n = stats.counts[r.id] || 0;
                  const col = r.sev === "HIGH" ? C.high : C.med;
                  return (
                    <div key={r.id}>
                      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                        <span style={{ fontSize: 11, color: C.text }}>{ruleLabel(r.id)}</span>
                        <span style={{ fontFamily: MONO, fontSize: 11, color: n ? C.text : C.faint }}>{n}</span>
                      </div>
                      <div style={{ height: 6, background: "#0C1114", borderRadius: 3, overflow: "hidden" }}>
                        <div style={{ width: `${(n / maxCount) * 100}%`, height: "100%", background: col, transition: "width .4s ease", opacity: 0.85 }} />
                      </div>
                    </div>
                  );
                })}
              </div>
              <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.faint, marginTop: 10, lineHeight: 1.5, borderTop: `1px solid ${C.line}`, paddingTop: 8 }}>
                같은 봇의 같은 규칙이 {gapSeconds === null ? "일정" : `${gapSeconds}초`} 이내로 이어지면 한 사건으로 묶는다.
              </div>
            </Panel>

            {/* 이상탐지 결과 */}
            <Panel title="이상 구간" tag="ANOMALY">
              <AnomalyPanel
                data={anomaly}
                samples={samples}
                selectedId={selectedId}
                onSelect={(id) => setSelectedId({ kind: "anomaly", id })}
              />
            </Panel>
          </div>

          {/* 중앙: 사건 목록 */}
          <Panel title="버그 사건" tag="EVENTS">
            <div className="qa-log" style={{ height: 560, overflowY: "auto", margin: "-2px -4px 0", paddingRight: 4 }}>
              {/* 상태별 안내. 비어 있는 이유를 화면에서 구분할 수 있어야 한다. */}
              {/* 최초 연결 때만 보여준다. 폴링마다 뜨면 1초에 한 번씩 화면이 깜빡인다. */}
              {status === "loading" && events.length === 0 && (
                <div style={{ fontFamily: MONO, fontSize: 12, color: C.faint, padding: "24px 6px" }}>서버에서 사건 목록을 받아오는 중…</div>
              )}
              {status === "error" && (
                <div style={{ padding: "20px 6px" }}>
                  <div style={{ fontFamily: MONO, fontSize: 12, color: C.high, marginBottom: 6 }}>연결 실패</div>
                  <div style={{ fontSize: 12, color: C.dim, lineHeight: 1.6 }}>
                    {errorMsg}<br />
                    <span style={{ fontFamily: MONO, fontSize: 11, color: C.faint }}>
                      {reconnecting
                        ? `dashboard/server 에서 uvicorn app:app --reload 로 서버를 띄우면 자동으로 붙는다. ${RETRY_MS / 1000}초마다 재시도 중이다.`
                        : "서버는 응답했지만 처리 중 오류가 났다. 서버 콘솔의 오류 내용을 확인한 뒤 재시도한다."}
                    </span>
                  </div>
                </div>
              )}
              {status === "ok" && events.length === 0 && (
                <div style={{ fontFamily: MONO, fontSize: 12, color: C.faint, padding: "24px 6px", lineHeight: 1.6 }}>
                  아직 잡힌 사건이 없다.<br />
                  {samples > 0
                    ? `텔레메트리 ${samples.toLocaleString()}줄을 봤고 규칙 위반은 없었다. 감시는 계속된다.`
                    : "텔레메트리가 아직 들어오지 않았다. 게임을 실행하고 봇 매치를 시작한다."}
                </div>
              )}

              {visible.map((e) => {
                const col = e.sev === "HIGH" ? C.high : C.med;
                const on = selected && selected.id === e.id;
                return (
                  <div key={e.id} className="qa-row" onClick={() => setSelectedId({ kind: "event", id: e.id })} style={{
                    display: "flex", gap: 10, alignItems: "flex-start", padding: "9px 10px 9px 8px",
                    borderBottom: `1px solid ${C.line}`, borderLeft: `2px solid ${col}`,
                    background: on ? C.panelHi : C.row, marginBottom: 4, borderRadius: "0 5px 5px 0",
                    outline: on ? `1px solid ${C.cyan}` : "none",
                  }}>
                    <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim, minWidth: 42, paddingTop: 1 }}>{clock(e.start)}</span>
                    <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.text, background: C.panelHi, border: `1px solid ${C.line}`, borderRadius: 4, padding: "1px 6px", whiteSpace: "nowrap" }}>BOT {e.entity}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        <span style={{ fontFamily: MONO, fontSize: 12, color: col, fontWeight: 600 }}>{e.rule}</span>
                        <span style={{ fontFamily: MONO, fontSize: 9.5, color: col, opacity: 0.75 }}>{e.sev}</span>
                        <span style={{ fontSize: 10, color: C.faint }}>{ruleLabel(e.rule)}</span>
                        {/* 지속 시간과 프레임 수. 집계가 없으면 볼 수 없던 정보다. */}
                        <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginLeft: "auto" }}>{dur(e.duration)} · {e.hits}프레임</span>
                        {e.ongoing && (
                          <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.cyan, border: `1px solid ${C.cyan}`, borderRadius: 3, padding: "0 4px" }}>진행 중</span>
                        )}
                      </div>
                      <div style={{ fontSize: 12, color: C.dim, marginTop: 3, lineHeight: 1.4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.msg}</div>
                    </div>
                  </div>
                );
              })}

              {/* 잘라 낸 경우에만, 몇 건 중 몇 건을 그렸는지 밝힌다. */}
              {events.length > MAX_ROWS && (
                <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.faint, textAlign: "center", padding: "12px 6px" }}>
                  총 {events.length}건 중 최근 {MAX_ROWS}건만 표시한다
                </div>
              )}
            </div>
          </Panel>

          {/* 우: 상세 보기 */}
          <Panel title="상세 보기" tag="INSPECT">
            <div className="qa-detail" style={{ height: 560, overflowY: "auto" }}>
              {!selected && !selectedAnomaly ? (
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 8, textAlign: "center" }}>
                  <Crosshair size={26} dim />
                  <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim }}>항목을 선택하세요</div>
                  <div style={{ fontSize: 10.5, color: C.faint, maxWidth: 200, lineHeight: 1.5 }}>버그 사건이나 이상 구간을 클릭하면 상세가 여기 표시된다</div>
                </div>
              ) : selectedAnomaly ? (
                <AnomalyDetail a={selectedAnomaly} threshold={anomaly && anomaly.threshold} onClose={() => setSelectedId(null)} />
              ) : selected ? (
                <Detail e={selected} onClose={() => setSelectedId(null)} />
              ) : null}
            </div>
          </Panel>
        </section>

        <footer style={{ fontFamily: MONO, fontSize: 10.5, color: C.faint, textAlign: "center", marginTop: 14, letterSpacing: 1, lineHeight: 1.7 }}>
          실시간 감시 · {POLL_MS / 1000}초 주기 · localhost 전용
          {sessionId && <> · 세션 {sessionId}</>}
          {samples > 0 && <> · 텔레메트리 {samples.toLocaleString()}줄 처리</>}
          {/* 건너뛴 줄이 있을 때만 표시한다. 0이면 굳이 알릴 것이 없다. */}
          {skipped > 0 && <> · 깨진 줄 {skipped}개 건너뜀</>}
          {/* 사본 크기. 게임은 맵 전환 때 원본을 지우므로, 재분석의 근거는 이쪽에 남는다. */}
          {archived > 0 && <> · 원본 사본 {bytes(archived)}</>}
          {source && <><br />{source}</>}
          {fetchedAt && <> · 갱신 {fetchedAt.toLocaleTimeString()}</>}
        </footer>
      </div>
    </div>
  );
}

function Detail({ e, onClose }) {
  const col = e.sev === "HIGH" ? C.high : C.med;
  // 사건 자체를 설명하는 값들. 시작·끝·지속·프레임 수는 집계가 만들어 준 정보다.
  const ident = [
    ["봇", `BOT ${e.entity}`],
    ["규칙", e.rule],
    ["심각도", e.sev],
    ["구간", `${clock(e.start)} ~ ${clock(e.end)}`],
    ["지속", dur(e.duration)],
    ["프레임 수", `${e.hits}`],
    ["상태", e.ongoing ? "진행 중(스트림 끝까지 이어짐)" : "종료"],
  ];
  // 사건이 시작된 순간의 상태. 무엇이 잘못되기 시작했는지가 진단의 출발점이므로 주 증거로 둔다.
  const first = [
    ["위치 X", num(e.first.x)], ["위치 Y", num(e.first.y)], ["위치 Z", num(e.first.z)],
    ["속도 X", num(e.first.vx)], ["속도 Y", num(e.first.vy)], ["속도 Z", num(e.first.vz)],
    ["속력", num(e.first.speed)],
    ["체력", `${e.first.health} / ${e.first.max_health}`],
    ["이동 입력", e.first.move_input ? "있음" : "없음"],
  ];
  // 가장 심했던 순간. 규칙마다 무엇이 '심한' 것인지가 달라 서버가 규칙별 극값을 골라 보낸다.
  const peak = [
    ["시각", clock(e.peak.time)],
    ["위치 Z", num(e.peak.z)],
    ["속력", num(e.peak.speed)],
    ["체력", `${e.peak.health} / ${e.peak.max_health}`],
  ];
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
        <div>
          <div style={{ fontFamily: MONO, fontSize: 13, color: col, fontWeight: 600 }}>{e.rule}</div>
          <div style={{ fontSize: 10.5, color: C.faint, marginTop: 2 }}>{ruleLabel(e.rule)} · {e.sev}</div>
        </div>
        <button onClick={onClose} style={{ background: "none", border: `1px solid ${C.line}`, color: C.dim, borderRadius: 4, fontFamily: MONO, fontSize: 11, padding: "2px 7px", cursor: "pointer" }}>✕</button>
      </div>

      <div style={{ background: C.row, border: `1px solid ${C.line}`, borderLeft: `2px solid ${col}`, borderRadius: 5, padding: "9px 11px", fontSize: 12, color: C.text, lineHeight: 1.5, marginBottom: 14 }}>{e.msg}</div>

      <Section label="사건">
        {ident.map(([k, v]) => <KV key={k} kk={k} vv={v} />)}
      </Section>

      <Section label="시작 순간의 상태 (텔레메트리)">
        {first.map(([k, v]) => <KV key={k} kk={k} vv={v} mono />)}
      </Section>

      {/* 극값이 있는 규칙에서만 이 절을 보여준다. 향후 추가될 규칙이 극값 정의를 갖지 않을 수 있다. */}
      {e.peakValue !== null && e.peakValue !== undefined && (
        <Section label={`가장 심했던 순간 · ${e.peakLabel || "극값"} ${e.peakValue}`}>
          {peak.map(([k, v]) => <KV key={k} kk={k} vv={v} mono />)}
        </Section>
      )}

      <div style={{ marginTop: 12, fontFamily: MONO, fontSize: 10, color: C.faint, lineHeight: 1.5, borderTop: `1px solid ${C.line}`, paddingTop: 10 }}>
        프레임 단위 탐지 {e.hits}건을 하나의 사건으로 묶은 것이다. 원시 탐지는 /api/detections 에 그대로 남아 있다.
      </div>
    </div>
  );
}


/*
  이상탐지 패널이다.

  하드 인바리언트 사건과 목록을 섞지 않고 따로 둔다. 하드 인바리언트는 "규칙을 어겼다"는
  확정이고, 이상탐지는 "여기를 검사하라"는 단서다. 한 목록에 섞으면 단서가 확정처럼 보인다.

  상태를 네 가지로 나눠 보여 준다. 비어 있는 이유를 화면만 보고 알 수 있어야 하기 때문이다.
  - 모델 없음: 아직 학습하지 않았다. 하드 인바리언트는 그대로 동작한다.
  - 창 채우는 중: 창이 60초라 세션 시작 직후에는 결과가 없는 것이 정상이다.
  - 이상 없음: 채점은 하고 있는데 벗어난 구간이 없다.
  - 목록: 이상 구간들
*/

/*
  워치독(③) 표시줄이다.

  오라클 ①·②와 관측 대상이 다르다. 앞의 둘은 봇의 상태를 보지만 이쪽은 게임 프로세스 자체를
  본다. 그래서 사건 목록이나 이상 구간 패널에 섞지 않고 헤더 아래 한 줄로 따로 둔다.

  틱은 서버 틱이지 클라이언트 렌더 FPS 가 아니다. 화면이 몇 프레임으로 그려지는지는
  텔레메트리에 없다. 라벨에 "서버 틱"이라 적어 오해를 막는다.
*/
function WatchdogStrip({ data }) {
  if (!data) return null;

  const active = (data.alerts || []).filter((a) => a.ongoing);
  const has = active.length > 0;
  // "진행 없음"은 진짜 행과 메뉴·점수판 구간을 구분하지 못한다. 셋 다 텔레메트리가 멈추고
  // 프로세스는 살아 있는 상태로 보인다. 그래서 "행"이라 단정하지 않고 사실만 적는다.
  const kindLabel = { low_tick: "성능 저하", no_progress: "진행 없음", process_gone: "프로세스 종료" };
  // 상태를 함께 보여 준다. 게임을 켜지 않았거나 밀린 기록을 읽는 중인 것은 이상이 아니다.
  const stateLabel = { idle: "게임 대기 중", catching_up: "기록 따라잡는 중", live: "감시 중" };

  // 틱이 목표 대비 얼마나 되는지로 색을 정한다. 경보 기준선 아래면 붉게 표시한다.
  const ratio = data.tick_ratio;
  const tickColor = ratio === null || ratio === undefined ? C.dim
    : ratio < data.tick_ratio_alert ? C.high
    : ratio < 0.95 ? C.med : C.cyan;

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap",
      background: C.panel, border: `1px solid ${active.some((a) => a.kind !== "no_progress") ? C.high : has ? C.med : C.line}`,
      borderRadius: 8, padding: "8px 13px", marginBottom: 12,
    }}>
      <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: 2 }}>WATCHDOG</span>
      <Stat label="상태" color={data.state === "live" ? C.cyan : C.dim}
        value={stateLabel[data.state] || data.state} />

      <Stat label="서버 틱" color={tickColor}
        value={data.tick_rate === null || data.tick_rate === undefined
          ? "—" : `${data.tick_rate} / ${data.target_tick}`} />

      {/* psutil 이 없거나 게임을 못 찾으면 이 값들이 비어 있다. 그 사실을 숨기지 않는다. */}
      <Stat label="CPU" value={data.cpu_percent === null || data.cpu_percent === undefined ? "—" : `${data.cpu_percent}%`} />
      <Stat label="메모리" value={data.memory_mb === null || data.memory_mb === undefined ? "—" : `${data.memory_mb} MB`} />
      <Stat label="프로세스"
        color={data.process_alive === true ? C.cyan : data.process_alive === false ? C.high : C.dim}
        value={data.process_alive === true ? "실행 중" : data.process_alive === false ? "없음" : "확인 불가"} />

      <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}>
        {has ? (
          active.map((a) => {
            // 진행 없음은 메뉴나 점수판일 수도 있으므로 경고색으로 둔다. 확정이 아니다.
            const col = a.kind === "no_progress" ? C.med : C.high;
            return (
              <span key={a.kind} style={{
                fontFamily: MONO, fontSize: 11, color: col,
                border: `1px solid ${col}`, borderRadius: 4, padding: "2px 8px",
              }}>
                {kindLabel[a.kind] || a.kind} {a.duration}초
              </span>
            );
          })
        ) : (
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
            {data.state !== "live"
              ? "텔레메트리를 기다리는 중 (판정 안 함)"
              : data.alert_count ? `누적 경보 ${data.alert_count}건` : "이상 없음"}
          </span>
        )}
        {!data.process_monitor && (
          // 프로세스 감시가 꺼진 이유를 화면에 남긴다. 값이 비어 있는 것과 감시가 없는 것은 다르다.
          <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.faint }}>
            프로세스 감시 꺼짐
          </span>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, color }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
      <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>{label}</span>
      <span style={{ fontFamily: MONO, fontSize: 12, color: color || C.text }}>{value}</span>
    </div>
  );
}

function AnomalyPanel({ data, samples, selectedId, onSelect }) {
  if (!data) {
    return <PanelNote>서버 응답을 기다리는 중이다.</PanelNote>;
  }
  if (!data.enabled) {
    return (
      <div>
        <PanelNote>
          모델이 없어 이상탐지가 꺼져 있다.<br />
          하드 인바리언트는 그대로 동작한다.
        </PanelNote>
        <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.faint, marginTop: 8, lineHeight: 1.5 }}>
          학습:<br />python tools/train_anomaly.py &lt;정상 세션&gt;
        </div>
      </div>
    );
  }

  const items = data.items || [];
  const scored = data.windows_scored || 0;

  return (
    <div>
      {/* 채점 현황. 이상이 0건일 때 '고장난 것'과 '정상인 것'을 구분하려면 이 숫자가 필요하다. */}
      <div style={{ display: "flex", justifyContent: "space-between", fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 9 }}>
        <span>채점한 창 {scored.toLocaleString()}</span>
        <span style={{ color: data.count ? C.anomaly : C.dim }}>이상 {data.count || 0}</span>
      </div>

      {scored === 0 ? (
        <PanelNote>
          {samples > 0
            ? `첫 창을 채우는 중이다. 창이 ${data.window_seconds}초라 그만큼 쌓여야 첫 결과가 나온다.`
            : "텔레메트리가 아직 들어오지 않았다."}
        </PanelNote>
      ) : items.length === 0 ? (
        <PanelNote>정상 패턴에서 벗어난 구간이 아직 없다.</PanelNote>
      ) : (
        <div style={{ maxHeight: 210, overflowY: "auto", margin: "0 -2px" }} className="qa-log">
          {items.slice(0, 40).map((a) => {
            const on = selectedId && selectedId.kind === "anomaly" && selectedId.id === a.id;
            return (
              <div key={a.id} className="qa-row" onClick={() => onSelect(a.id)} style={{
                borderLeft: `2px solid ${C.anomaly}`, background: on ? C.panelHi : C.row,
                borderRadius: "0 4px 4px 0", padding: "5px 7px", marginBottom: 3,
                outline: on ? `1px solid ${C.anomaly}` : "none",
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontFamily: MONO, fontSize: 10 }}>
                  <span style={{ color: C.text }}>BOT {a.entity_id}</span>
                  <span style={{ color: C.dim }}>{clock(a.start_time)}</span>
                </div>
                {/* 원인 특징을 함께 보여 준다. 점수만으로는 무엇을 검사할지 알 수 없다. */}
                <div style={{ fontSize: 10, color: C.dim, marginTop: 2 }}>
                  {a.top_feature_label} <span style={{ color: C.anomaly }}>{a.top_feature_z > 0 ? "+" : ""}{a.top_feature_z}σ</span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.faint, marginTop: 9, lineHeight: 1.5, borderTop: `1px solid ${C.line}`, paddingTop: 8 }}>
        결론이 아니라 단서다. {data.window_seconds}초 창, 학습 데이터 {data.percentile}% 기준선.
      </div>
    </div>
  );
}

function PanelNote({ children }) {
  return (
    <div style={{ border: `1px dashed ${C.line}`, borderRadius: 6, padding: "14px 11px", textAlign: "center", fontSize: 10.5, color: C.faint, lineHeight: 1.6 }}>
      {children}
    </div>
  );
}

/* 이상 구간 하나의 상세다. 어떤 특징이 얼마나 벗어났는지를 전부 보여 준다. */
function AnomalyDetail({ a, threshold, onClose }) {
  const entries = Object.keys(a.contributions || {});
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
        <div>
          <div style={{ fontFamily: MONO, fontSize: 13, color: C.anomaly, fontWeight: 600 }}>이상 구간</div>
          <div style={{ fontSize: 10.5, color: C.faint, marginTop: 2 }}>BOT {a.entity_id} · {clock(a.start_time)} ~ {clock(a.end_time)}</div>
        </div>
        <button onClick={onClose} style={{ background: "none", border: `1px solid ${C.line}`, color: C.dim, borderRadius: 4, fontFamily: MONO, fontSize: 11, padding: "2px 7px", cursor: "pointer" }}>✕</button>
      </div>

      <div style={{ background: C.row, border: `1px solid ${C.line}`, borderLeft: `2px solid ${C.anomaly}`, borderRadius: 5, padding: "9px 11px", fontSize: 12, color: C.text, lineHeight: 1.5, marginBottom: 14 }}>
        {a.top_feature_label}이(가) 정상 범위에서 {Math.abs(a.top_feature_z)}표준편차 벗어났다.
      </div>

      <Section label="특징별 벗어난 정도">
        {entries.map((k) => {
          const z = a.contributions[k];
          const v = a.values[k];
          const strong = Math.abs(z) >= 2;
          return (
            <div key={k} style={{ padding: "5px 0", borderBottom: "1px solid #1B252C" }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ fontSize: 11.5, color: strong ? C.text : C.dim }}>{ANOMALY_LABEL[k] || k}</span>
                <span style={{ fontFamily: MONO, fontSize: 11.5, color: strong ? C.anomaly : C.dim }}>
                  {v} ({z > 0 ? "+" : ""}{z}σ)
                </span>
              </div>
              {/* 벗어난 정도를 막대로 보여 준다. 가운데가 정상 평균이다. */}
              <div style={{ height: 4, background: "#0C1114", borderRadius: 2, marginTop: 4, position: "relative" }}>
                <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, background: C.line }} />
                <div style={{
                  position: "absolute", top: 0, bottom: 0, borderRadius: 2, background: C.anomaly,
                  opacity: strong ? 0.9 : 0.4,
                  left: z < 0 ? `${Math.max(0, 50 - Math.min(Math.abs(z), 5) * 10)}%` : "50%",
                  width: `${Math.min(Math.abs(z), 5) * 10}%`,
                }} />
              </div>
            </div>
          );
        })}
      </Section>

      <Section label="판정">
        <KV kk="이상 점수" vv={String(a.score)} mono />
        {threshold !== null && threshold !== undefined && <KV kk="임계값" vv={String(Math.round(threshold * 10000) / 10000)} mono />}
        <KV kk="구간 길이" vv={`${Math.round(a.end_time - a.start_time)}초`} mono />
      </Section>

      <div style={{ marginTop: 12, fontFamily: MONO, fontSize: 10, color: C.faint, lineHeight: 1.5, borderTop: `1px solid ${C.line}`, paddingTop: 10 }}>
        이 구간이 버그라는 뜻이 아니라, 정상 플레이에서 벗어났으니 확인하라는 뜻이다. 판단은 사람이 한다.
      </div>
    </div>
  );
}

// 이상탐지 특징의 한글 이름이다. 서버의 qa/anomaly.py 와 같은 키를 쓴다.
const ANOMALY_LABEL = {
  attack_ratio: "발사 비율",
  health_lost_rate: "체력 손실률",
  health_ratio: "체력 유지율",
};

function Section({ label, children }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: 1.5, marginBottom: 7 }}>{label}</div>
      <div>{children}</div>
    </div>
  );
}
function KV({ kk, vv, mono }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid #1B252C" }}>
      <span style={{ fontSize: 11.5, color: C.dim }}>{kk}</span>
      <span style={{ fontFamily: mono ? MONO : SANS, fontSize: 11.5, color: C.text }}>{vv}</span>
    </div>
  );
}

function Panel({ title, tag, children }) {
  return (
    <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, padding: 14 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: 13, fontWeight: 600, letterSpacing: 0.5 }}>{title}</h2>
        <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: 2 }}>{tag}</span>
      </div>
      {children}
    </div>
  );
}
function Meta({ label, value }) {
  return (
    <div style={{ textAlign: "right" }}>
      <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: 1 }}>{label}</div>
      <div style={{ fontFamily: MONO, fontSize: 14, color: C.text, marginTop: 1 }}>{value}</div>
    </div>
  );
}

/*
  연결·감시 상태 표시등이다.

  정적 시절에는 LIVE 표시를 뺐다. 멈춰 있는 데이터 위에서 램프가 깜빡이면 실제보다
  실시간처럼 보이기 때문이다. 지금은 서버가 텔레메트리를 실제로 따라가고 화면도 1초마다
  갱신되므로, LIVE 표시가 사실과 일치한다.

  상태는 네 가지로 구분한다.
  - LIVE   : 정상 감시 중
  - 대기   : 탭이 가려져 폴링을 멈춘 상태(화면이 고장난 게 아님을 알린다)
  - 재연결 : 서버에 닿지 않아 자동 재시도 중
  - 오류   : 서버는 응답하지만 처리에 실패한 상태(사람이 봐야 한다)
*/
function LiveLamp({ status, reconnecting, paused }) {
  const failed = status === "error";
  let col = C.cyan;
  let label = "LIVE";
  let pulse = true;
  if (failed) {
    col = C.high;
    label = reconnecting ? "재연결" : "오류";
    pulse = reconnecting;  // 재시도 중일 때만 깜빡여 '기다리는 중'임을 나타낸다.
  } else if (paused) {
    col = C.dim;
    label = "대기";
    pulse = false;
  }
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      border: `1px solid ${col}`, borderRadius: 6, padding: "6px 11px",
      fontFamily: MONO, fontSize: 12, letterSpacing: 1, color: C.text,
    }}>
      <span className="qa-pulse" style={{
        width: 8, height: 8, borderRadius: "50%", background: col,
        animation: pulse ? "qaPulse 1.3s ease-in-out infinite" : "none",
      }} />
      {label}
    </div>
  );
}

/* 헤더의 보조 동작 버튼이다(내보내기·새 세션). */
function SmallButton({ onClick, children }) {
  return (
    <button onClick={onClick} style={{
      background: C.panelHi, color: C.text, border: `1px solid ${C.line}`, borderRadius: 6,
      padding: "6px 10px", fontFamily: MONO, fontSize: 11, letterSpacing: 0.5, cursor: "pointer",
    }}>
      {children}
    </button>
  );
}

function Crosshair({ size = 22, dim }) {
  const col = dim ? C.faint : C.cyan;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" style={{ display: "block" }}>
      <circle cx="12" cy="12" r="9" stroke={col} strokeWidth="1.4" opacity="0.9" />
      <circle cx="12" cy="12" r="2" fill={col} />
      <path d="M12 1v5M12 18v5M1 12h5M18 12h5" stroke={col} strokeWidth="1.4" />
    </svg>
  );
}
