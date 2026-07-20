import React, { useState, useEffect, useRef } from "react";

/*
  OpenArena QA Monitor — 대시보드 (mock 데이터, 3열 구조)

  좌: 규칙별 집계 + 이상탐지 자리(준비 중)
  중앙: 실시간 탐지 로그 (항목 클릭 가능)
  우: 상세 보기 패널 (클릭한 탐지의 그 순간 상태값)

  실제 연결 시 "== 데이터 소스 ==" 블록만 파이썬 API 폴링으로 교체한다.
  각 탐지가 상세값(pos/vel/health)을 함께 들고 있어야 상세 패널이 채워지는데,
  실제로는 파이썬 Bug의 details 필드가 그 역할을 한다. 지금은 mock으로 함께 만든다.
*/

const C = {
  bg: "#0D1512", panel: "#121D17", panelHi: "#17241C", row: "#141F19",
  line: "#26362C", text: "#D2DED6", dim: "#6C7C72", faint: "#3F4E45",
  cyan: "#54C68A", high: "#E5595A", med: "#DFA13F", ok: "#54C68A", anomaly: "#B48AE8",
};
const MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
const SANS = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
const BOTS = ["Sarge", "Grunt", "Major", "Orbb", "Ranger", "Bones", "Keel"];

const RULES = [
  { id: "fell_through_floor", sev: "HIGH", w: 3 },
  { id: "out_of_bounds", sev: "HIGH", w: 2 },
  { id: "health_out_of_range", sev: "HIGH", w: 2 },
  { id: "impossible_velocity", sev: "MEDIUM", w: 3 },
  { id: "stuck", sev: "MEDIUM", w: 2 },
];
const RULE_KR = {
  fell_through_floor: "바닥 관통", out_of_bounds: "경계 이탈", health_out_of_range: "체력 초과",
  impossible_velocity: "과속", stuck: "끼임",
};
const TW = RULES.reduce((s, r) => s + r.w, 0);
function pickRule() { let n = Math.random() * TW; for (const r of RULES) if ((n -= r.w) <= 0) return r; return RULES[0]; }
const pad = (n) => String(n).padStart(2, "0");
const clock = (s) => `${pad(Math.floor(s / 60))}:${pad(Math.floor(s % 60))}`;

// 규칙에 맞는 mock 상태값 + 메시지. 실제로는 파이썬이 이 값들을 details로 실어 보낸다.
function makeDetail(ruleId) {
  const x = +(Math.random() * 2000 - 400).toFixed(1);
  const y = +(Math.random() * 2000).toFixed(1);
  let z = +(Math.random() * 300 + 20).toFixed(1);
  let vx = +(Math.random() * 400 - 200).toFixed(1), vy = +(Math.random() * 400 - 200).toFixed(1), vz = +(Math.random() * 200 - 100).toFixed(1);
  let health = 90 + Math.floor(Math.random() * 11), max = 100, move = 1, msg = "";
  if (ruleId === "fell_through_floor") { z = +(-30 - Math.random() * 900).toFixed(1); vz = +(-400 - Math.random() * 800).toFixed(1); msg = `z=${z} 이(가) 바닥(z=-29.0) 아래로 내려갔다.`; }
  else if (ruleId === "out_of_bounds") { msg = `위치(${x}, ${y}, ${z}) 이(가) 맵 경계를 벗어났다.`; }
  else if (ruleId === "health_out_of_range") { health = 999; msg = `체력 999 이(가) 절대 상한 250 을(를) 초과했다.`; }
  else if (ruleId === "impossible_velocity") { vx = 1000 + Math.random() * 700; const spd = Math.sqrt(vx * vx + vy * vy + vz * vz).toFixed(0); msg = `속력 ${spd} 이(가) 상한 1214 을(를) 초과했다.`; }
  else if (ruleId === "stuck") { vx = 0; vy = 0; vz = 0; const idle = (2 + Math.random() * 0.5).toFixed(1); msg = `이동 입력이 있는데 ${idle}초간 위치가 변하지 않아 끼임으로 판정한다.`; }
  const speed = Math.sqrt(vx * vx + vy * vy + vz * vz);
  return { x, y, z, vx: +(+vx).toFixed(1), vy, vz, speed: +speed.toFixed(1), health, max_health: max, move_input: move, msg };
}

export default function QADashboard() {
  const [session, setSession] = useState(0);
  const [running, setRunning] = useState(true);
  const [dets, setDets] = useState([]);
  const [ruleCounts, setRuleCounts] = useState({});
  const [selected, setSelected] = useState(null);
  const idRef = useRef(0);
  const sessionRef = useRef(0);

  useEffect(() => { sessionRef.current = session; }, [session]);
  useEffect(() => { if (!running) return; const t = setInterval(() => setSession((s) => s + 1), 1000); return () => clearInterval(t); }, [running]);

  // == 데이터 소스 == (mock)
  useEffect(() => {
    if (!running) return;
    let alive = true, to;
    const emit = () => {
      if (!alive) return;
      const r = pickRule();
      const entity = 1 + Math.floor(Math.random() * BOTS.length);
      const d = makeDetail(r.id);
      const det = { id: ++idRef.current, session: sessionRef.current, entity, bot: BOTS[entity - 1], rule: r.id, sev: r.sev, kind: "hard", ...d };
      setDets((arr) => [det, ...arr].slice(0, 200));
      setRuleCounts((rc) => ({ ...rc, [r.id]: (rc[r.id] || 0) + 1 }));
      to = setTimeout(emit, 750 + Math.random() * 1100);
    };
    to = setTimeout(emit, 600);
    return () => { alive = false; clearTimeout(to); };
  }, [running]);

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: SANS, padding: 18 }}>
      <style>{`
        @keyframes qaPulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.82)}}
        @keyframes qaIn{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:translateY(0)}}
        .qa-row{animation:qaIn .28s ease-out both;cursor:pointer}
        .qa-row:hover{background:${C.panelHi}!important}
        .qa-log::-webkit-scrollbar,.qa-detail::-webkit-scrollbar{width:8px}
        .qa-log::-webkit-scrollbar-thumb,.qa-detail::-webkit-scrollbar-thumb{background:${C.line};border-radius:4px}
        @media (prefers-reduced-motion:reduce){.qa-row{animation:none}.qa-pulse{animation:none!important}}
      `}</style>

      <div style={{ maxWidth: 1280, margin: "0 auto" }}>
        {/* 헤더 */}
        <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", borderBottom: `1px solid ${C.line}`, paddingBottom: 14, marginBottom: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 13 }}>
            <Crosshair />
            <div>
              <div style={{ fontFamily: MONO, fontSize: 15, letterSpacing: 2, fontWeight: 600 }}>OPENARENA<span style={{ color: C.cyan }}> · </span>QA MONITOR</div>
              <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: 1, marginTop: 2 }}>봇 텔레메트리 실시간 탐지 · 하드 인바리언트 오라클</div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
            <Meta label="세션" value={clock(session)} />
            <Meta label="맵" value="q3dm17" />
            <Meta label="봇" value={String(BOTS.length)} />
            <Live running={running} onToggle={() => setRunning((v) => !v)} />
          </div>
        </header>

        {/* 3열 본문 */}
        <section style={{ display: "grid", gridTemplateColumns: "230px 1fr 320px", gap: 12, alignItems: "start" }}>

          {/* 좌: 규칙별 집계 + 이상탐지 자리 */}
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <Panel title="규칙별 집계" tag="BY RULE">
              <div style={{ display: "flex", flexDirection: "column", gap: 10, paddingTop: 2 }}>
                {RULES.map((r) => {
                  const n = ruleCounts[r.id] || 0;
                  const max = Math.max(1, ...Object.values(ruleCounts));
                  const col = r.sev === "HIGH" ? C.high : C.med;
                  return (
                    <div key={r.id}>
                      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                        <span style={{ fontSize: 11, color: C.text }}>{RULE_KR[r.id]}</span>
                        <span style={{ fontFamily: MONO, fontSize: 11, color: n ? C.text : C.faint }}>{n}</span>
                      </div>
                      <div style={{ height: 6, background: "#0C1114", borderRadius: 3, overflow: "hidden" }}>
                        <div style={{ width: `${(n / max) * 100}%`, height: "100%", background: col, transition: "width .4s ease", opacity: 0.85 }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </Panel>

            {/* 이상탐지 자리 (준비 중) */}
            <Panel title="이상 구간" tag="ANOMALY">
              <div style={{ border: `1px dashed ${C.line}`, borderRadius: 6, padding: "18px 12px", textAlign: "center" }}>
                <div style={{ width: 10, height: 10, borderRadius: "50%", background: C.anomaly, opacity: 0.6, margin: "0 auto 8px" }} />
                <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>이상탐지 · 준비 중</div>
                <div style={{ fontSize: 10.5, color: C.faint, marginTop: 5, lineHeight: 1.5 }}>
                  규칙은 안 어기지만<br />정상 패턴에서 벗어난 구간이<br />여기에 표시된다
                </div>
              </div>
            </Panel>
          </div>

          {/* 중앙: 탐지 로그 */}
          <Panel title="탐지 로그" tag="LIVE DETECTIONS">
            <div className="qa-log" style={{ height: 560, overflowY: "auto", margin: "-2px -4px 0", paddingRight: 4 }}>
              {dets.length === 0 && <div style={{ fontFamily: MONO, fontSize: 12, color: C.faint, padding: "24px 6px" }}>탐지 대기 중… 봇이 플레이를 시작하면 여기에 쌓인다.</div>}
              {dets.map((d) => {
                const col = d.sev === "HIGH" ? C.high : C.med;
                const on = selected && selected.id === d.id;
                return (
                  <div key={d.id} className="qa-row" onClick={() => setSelected(d)} style={{
                    display: "flex", gap: 10, alignItems: "flex-start", padding: "9px 10px 9px 8px",
                    borderBottom: `1px solid ${C.line}`, borderLeft: `2px solid ${col}`,
                    background: on ? C.panelHi : C.row, marginBottom: 4, borderRadius: "0 5px 5px 0",
                    outline: on ? `1px solid ${C.cyan}` : "none",
                  }}>
                    <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim, minWidth: 42, paddingTop: 1 }}>{clock(d.session)}</span>
                    <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.text, background: C.panelHi, border: `1px solid ${C.line}`, borderRadius: 4, padding: "1px 6px", whiteSpace: "nowrap" }}>BOT {d.entity}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span style={{ fontFamily: MONO, fontSize: 12, color: col, fontWeight: 600 }}>{d.rule}</span>
                        <span style={{ fontFamily: MONO, fontSize: 9.5, color: col, opacity: 0.75 }}>{d.sev}</span>
                        <span style={{ fontSize: 10, color: C.faint }}>{RULE_KR[d.rule]}</span>
                      </div>
                      <div style={{ fontSize: 12, color: C.dim, marginTop: 3, lineHeight: 1.4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.msg}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          </Panel>

          {/* 우: 상세 보기 */}
          <Panel title="상세 보기" tag="INSPECT">
            <div className="qa-detail" style={{ height: 560, overflowY: "auto" }}>
              {!selected ? (
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 8, textAlign: "center" }}>
                  <Crosshair size={26} dim />
                  <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim }}>탐지 항목을 선택하세요</div>
                  <div style={{ fontSize: 10.5, color: C.faint, maxWidth: 200, lineHeight: 1.5 }}>왼쪽 로그에서 항목을 클릭하면 그 순간의 상태값이 여기 표시된다</div>
                </div>
              ) : <Detail d={selected} onClose={() => setSelected(null)} />}
            </div>
          </Panel>
        </section>

        <footer style={{ fontFamily: MONO, fontSize: 10.5, color: C.faint, textAlign: "center", marginTop: 14, letterSpacing: 1 }}>
          mock 데이터 · localhost 전용 · 실제 연결 시 데이터 소스만 파이썬 API로 교체
        </footer>
      </div>
    </div>
  );
}

function Detail({ d, onClose }) {
  const col = d.sev === "HIGH" ? C.high : C.med;
  const rows = [
    ["시각", clock(d.session)], ["봇", `BOT ${d.entity} · ${d.bot}`], ["규칙", d.rule], ["심각도", d.sev],
  ];
  const state = [
    ["위치 X", d.x], ["위치 Y", d.y], ["위치 Z", d.z],
    ["속도 X", d.vx], ["속도 Y", d.vy], ["속도 Z", d.vz],
    ["속력", d.speed], ["체력", `${d.health} / ${d.max_health}`], ["이동 입력", d.move_input ? "있음" : "없음"],
  ];
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
        <div>
          <div style={{ fontFamily: MONO, fontSize: 13, color: col, fontWeight: 600 }}>{d.rule}</div>
          <div style={{ fontSize: 10.5, color: C.faint, marginTop: 2 }}>{RULE_KR[d.rule]} · {d.sev}</div>
        </div>
        <button onClick={onClose} style={{ background: "none", border: `1px solid ${C.line}`, color: C.dim, borderRadius: 4, fontFamily: MONO, fontSize: 11, padding: "2px 7px", cursor: "pointer" }}>✕</button>
      </div>

      <div style={{ background: C.row, border: `1px solid ${C.line}`, borderLeft: `2px solid ${col}`, borderRadius: 5, padding: "9px 11px", fontSize: 12, color: C.text, lineHeight: 1.5, marginBottom: 14 }}>{d.msg}</div>

      <Section label="식별">
        {rows.map(([k, v]) => <KV key={k} kk={k} vv={v} />)}
      </Section>

      <Section label="그 순간의 상태 (텔레메트리)">
        {state.map(([k, v]) => <KV key={k} kk={k} vv={v} mono />)}
      </Section>

      <div style={{ marginTop: 12, fontFamily: MONO, fontSize: 10, color: C.faint, lineHeight: 1.5, borderTop: `1px solid ${C.line}`, paddingTop: 10 }}>
        하드 인바리언트는 결론이 명확하다. 이 상태값이 규칙을 어긴 그 순간의 기록이다.
      </div>
    </div>
  );
}

function Section({ label, children }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: 1.5, textTransform: "uppercase", marginBottom: 7 }}>{label}</div>
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
      <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: 1, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontFamily: MONO, fontSize: 14, color: C.text, marginTop: 1 }}>{value}</div>
    </div>
  );
}
function Live({ running, onToggle }) {
  return (
    <button onClick={onToggle} style={{ display: "flex", alignItems: "center", gap: 8, background: C.panelHi, color: C.text, border: `1px solid ${running ? C.cyan : C.line}`, borderRadius: 6, padding: "7px 12px", fontFamily: MONO, fontSize: 12, letterSpacing: 1, cursor: "pointer" }}>
      <span className="qa-pulse" style={{ width: 8, height: 8, borderRadius: "50%", background: running ? C.cyan : C.faint, animation: running ? "qaPulse 1.3s ease-in-out infinite" : "none" }} />
      {running ? "LIVE" : "PAUSED"}
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
