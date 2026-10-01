/* K뷰티 리레이팅 트래커 — site/data/site.json을 읽어 탭별로 그린다. 의존성: ECharts(CDN). */
"use strict";

const state = { data: null, tab: "overview", charts: new Map(), rendered: new Set() };
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const SLOTS = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8"];

// ── 숫자·날짜 표기 ────────────────────────────────────────────────
function fmtNum(v, unit) {
  if (v == null || Number.isNaN(v)) return "–";
  const a = Math.abs(v);
  const d = a >= 1000 ? 0 : a >= 100 ? 1 : a >= 10 ? 1 : 2;
  return v.toLocaleString("ko-KR", { maximumFractionDigits: d, minimumFractionDigits: 0 }) + (unit ? " " + unit : "");
}
function fmtDate(s, freq) {
  const [y, m, d] = s.split("-");
  if (freq === "Y") return `${y}`;
  if (freq === "Q") return `${y} ${Math.floor((+m - 1) / 3) + 1}Q`;
  if (freq === "M") return `${y}-${m}`;
  return `${y}-${m}-${d}`;
}
const toTime = (s) => new Date(s + "T00:00:00").getTime();

// ── YoY 변환 ─────────────────────────────────────────────────────
function yoyData(data, freq, mode) {
  const out = [];
  const times = data.map((p) => toTime(p[0]));
  const byKey = new Map(data.map((p) => [p[0], p[1]]));
  for (const [d, v] of data) {
    const [y, m, dd] = d.split("-");
    const target = `${+y - 1}-${m}-${dd}`;
    let prev = byKey.get(target);
    if (prev == null && (freq === "D" || freq === "W")) {
      const t = toTime(target);
      let lo = 0, hi = times.length - 1, best = -1;
      while (lo <= hi) { const mid = (lo + hi) >> 1; if (times[mid] <= t) { best = mid; lo = mid + 1; } else hi = mid - 1; }
      if (best >= 0 && t - times[best] <= 7 * 864e5) prev = data[best][1];
    }
    if (prev == null) continue;
    if (mode === "pct") { if (prev > 0) out.push([d, (v / prev - 1) * 100]); }
    else out.push([d, v - prev]);
  }
  return out;
}

// ── 차트 ─────────────────────────────────────────────────────────
function chartOption(item, view) {
  const unit = view.yoy ? (item.yoy === "pct" ? "% (YoY)" : `${item.unit} 차이 (YoY)`) : item.unit;
  const cutoff = view.range ? Date.now() - view.range * 365.25 * 864e5 : -Infinity;
  const ink2 = css("--ink-2"), muted = css("--muted"), grid = css("--grid"), axis = css("--axis"), surface = css("--surface");
  const series = item.series.map((s, i) => {
    let data = view.yoy ? yoyData(s.data, item.freq, item.yoy) : s.data;
    data = data.filter((p) => toTime(p[0]) >= cutoff).map((p) => [toTime(p[0]), p[1]]);
    const color = s.slot === "muted" ? css("--muted") : css(SLOTS[(s.slot ?? i) % 8]);
    const few = data.length <= 30;
    const base = { name: s.name, data, color, emphasis: { focus: "series" } };
    if (item.kind === "bar") return { ...base, type: "bar", barMaxWidth: 14, itemStyle: { borderRadius: [4, 4, 0, 0] } };
    return { ...base, type: "line", legendIcon: "roundRect", showSymbol: few, symbolSize: 8, lineStyle: { width: 2, type: s.dash ? "dashed" : "solid" },
             itemStyle: { borderColor: surface, borderWidth: 2 } };
  });
  if (item.marks?.length && series.length) {
    series[0].markLine = {
      symbol: "none", silent: true, lineStyle: { color: muted, width: 1, type: "solid" },
      label: { color: ink2, fontSize: 11, formatter: (p) => p.name, position: "insideEndTop" },
      data: item.marks.map((m) => ({ name: m.label, xAxis: toTime(m.x) })),
    };
  }
  const xs = series.flatMap((x) => x.data.map((p) => p[0]));
  const tMin = Math.min(...xs), tMax = Math.max(...xs), span = xs.length ? tMax - tMin : 0;
  if (item.areas?.length && series.length) {
    series[0].markArea = { silent: true, itemStyle: { color: css("--g-manual"), opacity: 0.8 },
      data: item.areas.map((a) => [{ xAxis: toTime(a.from) }, { xAxis: toTime(a.to) }]) };
  }
  return {
    animation: false,
    grid: { left: 8, right: 16, top: series.length > 1 ? 36 : 14, bottom: 8, containLabel: true },
    legend: series.length > 1 ? { top: 0, left: 0, icon: "roundRect", itemWidth: 12, itemHeight: 4,
                                  textStyle: { color: ink2, fontSize: 12 }, type: "scroll" } : { show: false },
    tooltip: {
      trigger: "axis", confine: true, backgroundColor: surface, borderColor: axis, textStyle: { color: css("--ink"), fontSize: 12 },
      axisPointer: { type: item.kind === "bar" ? "shadow" : "line", lineStyle: { color: axis } },
      formatter: (ps) => {
        if (!ps.length) return "";
        const d = new Date(ps[0].value[0]);
        const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
        return `<b>${fmtDate(iso, item.freq)}</b><br>` + ps.map((p) =>
          `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${p.color};margin-right:6px"></span>` +
          `${esc(p.seriesName)} <b>${fmtNum(p.value[1], unit)}</b>`).join("<br>");
      },
    },
    xAxis: { type: "time", axisLine: { lineStyle: { color: axis } }, splitLine: { show: false },
             ...(span < 60 * 864e5 ? { min: tMin - 30 * 864e5, max: tMax + 30 * 864e5 } : {}),
             axisLabel: { color: muted, fontSize: 11, hideOverlap: true, formatter: (v) => {
               const d = new Date(v); return span < 400 * 864e5 ? `${d.getMonth() + 1}/${d.getDate()}` : `${d.getFullYear()}`; } } },
    yAxis: { type: "value", scale: item.kind !== "bar",
             axisLabel: { color: muted, fontSize: 11, formatter: (v) => fmtNum(v) },
             splitLine: { lineStyle: { color: grid, width: 1 } } },
    series,
  };
}

function downloadCsv(item) {
  const dates = [...new Set(item.series.flatMap((s) => s.data.map((p) => p[0])))].sort();
  const maps = item.series.map((s) => new Map(s.data));
  const head = ["기준일", ...item.series.map((s) => `${s.name} (${item.unit})`)];
  const lines = [head, ...dates.map((d) => [d, ...maps.map((m) => m.get(d) ?? "")])]
    .map((r) => r.map((c) => (/[",\n]/.test(String(c)) ? `"${String(c).replace(/"/g, '""')}"` : c)).join(","));
  lines.push("", `출처,${item.source}`, `최종 갱신,${item.updated || state.data.generated}`);
  const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${item.id}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function gradeBadge(item) {
  return `<span class="grade g-${item.grade}">${esc(item.gradeLabel)}</span>`;
}

function renderChart(item, el) {
  const view = { range: item.freq === "Y" ? 0 : 5, yoy: false };
  const ranges = [[1, "1년"], [3, "3년"], [5, "5년"], [0, "전체"]];
  el.innerHTML = `
    <h3>${esc(item.title)} ${gradeBadge(item)}</h3>
    <div class="ctl">
      <span class="seg" role="group" aria-label="기간">${ranges.map(([n, l]) =>
        `<button data-range="${n}" aria-pressed="${n === view.range}">${l}</button>`).join("")}</span>
      ${item.yoy ? `<span class="seg" role="group" aria-label="값 전환"><button data-yoy="0" aria-pressed="true">값</button><button data-yoy="1" aria-pressed="false">${item.yoy === "pct" ? "YoY %" : "YoY 차이"}</button></span>` : ""}
      <button class="dl" title="CSV 내려받기">CSV</button>
    </div>
    <p class="unit">단위: <span data-unit>${esc(item.unit)}</span></p>
    <div class="plot" role="img" aria-label="${esc(item.title)}"></div>
    ${item.note ? `<p class="note">${esc(item.note)}</p>` : ""}
    ${item.sample ? `<p class="note"><b>⚠ ${esc(item.sample)}</b></p>` : ""}
    <p class="foot-note"><b>출처</b> ${esc(item.source)} · <b>마지막 시점</b> ${esc(fmtDate(item.lastPoint || "----", item.freq))} · <b>최종 갱신</b> ${esc(item.updated || state.data.generated)}</p>`;
  const plot = $(".plot", el);
  const draw = () => {
    let c = state.charts.get(item.id);
    if (!c) { c = echarts.init(plot, null, { renderer: "canvas" }); state.charts.set(item.id, c); }
    c.setOption(chartOption(item, view), true);
  };
  el.querySelectorAll("[data-range]").forEach((b) => b.addEventListener("click", () => {
    view.range = +b.dataset.range;
    el.querySelectorAll("[data-range]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    draw();
  }));
  el.querySelectorAll("[data-yoy]").forEach((b) => b.addEventListener("click", () => {
    view.yoy = b.dataset.yoy === "1";
    $("[data-unit]", el).textContent = view.yoy ? (item.yoy === "pct" ? "% (전년 대비)" : `${item.unit} 차이 (전년 대비)`) : item.unit;
    el.querySelectorAll("[data-yoy]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    draw();
  }));
  $(".dl", el).addEventListener("click", () => downloadCsv(item));
  el._draw = draw;
  draw();
}

function renderEmpty(item, el) {
  el.innerHTML = `<h3>${esc(item.title)} ${gradeBadge(item)}</h3>
    <div class="empty"><strong>수집 불가</strong> — ${esc(item.empty)}</div>
    ${item.note ? `<p class="note">${esc(item.note)}</p>` : ""}
    ${item.source ? `<p class="foot-note"><b>출처(예정)</b> ${esc(item.source)}</p>` : ""}`;
}

function renderCard(item, el) {
  el.innerHTML = `<h3>${esc(item.title)} ${gradeBadge(item)}</h3>
    <p class="note">${esc(item.body)}</p>
    ${item.memo ? `<p class="memo">${esc(item.memo)}</p>` : ""}
    ${item.source ? `<p class="foot-note"><b>출처</b> ${esc(item.source)}</p>` : ""}`;
}

function cellHtml(v) {
  if (typeof v === "number") return `<td class="num">${fmtNum(v)}</td>`;
  return `<td>${esc(v)}</td>`;
}

function renderTable(item, el) {
  const t = item.table;
  el.classList.add("wide");
  el.innerHTML = `<h3>${esc(item.title)} ${gradeBadge(item)}</h3>` + (t.rows.length
    ? `<div class="tscroll"><table><thead><tr>${t.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
       <tbody>${t.rows.map((r) => `<tr>${r.map(cellHtml).join("")}</tr>`).join("")}</tbody></table></div>`
    : `<div class="empty"><strong>수집 불가</strong> — ${esc(item.empty)}</div>`) +
    (item.note ? `<p class="note">${esc(item.note)}</p>` : "");
}

function renderChain(item, el) {
  el.classList.add("wide");
  const arrow = { 상승: "▲", 하락: "▼", 보합: "▶", "판단 불가": "·" };
  const cls = (m) => {
    if (m.dir !== "상승" && m.dir !== "하락") return "";
    const good = (m.dir === "상승") !== !!m.up_bad;
    return good ? "up" : "down";
  };
  const boxes = item.chain.map((b, i) => `
    ${i ? '<div class="chain-arrow" aria-hidden="true">→</div>' : ""}
    <button class="stage-box" data-stage="${esc(b.id)}" aria-label="${esc(b.name)} 상세 펼치기">
      <span class="stage-name">${i + 1}. ${esc(b.name)}</span>
      <span class="stage-q">${esc(b.quarter || "–")}</span>
      ${b.metrics.map((m) => `<span class="stage-m" title="${esc(m.hint || "")}"><span class="lbl">${esc(m.label)}</span>
        <b>${esc(m.value)}</b> <span class="dir ${cls(m)}">${esc(arrow[m.dir] || "")} ${esc(m.dir || "")}</span></span>`).join("")}
      <span class="stage-members">${esc(b.members.join(", "))}</span>
    </button>`).join("");
  el.innerHTML = `<h3>${esc(item.title)}</h3>
    <div class="chain">
      <div class="flow goods"><span>물건 흐름</span><i></i><b>▶</b></div>
      <div class="chain-row">${boxes}</div>
      <div class="flow demand"><b>◀</b><i></i><span>수요 신호 (주문·판매)</span></div>
    </div>
    <p class="note">${esc(item.note || "")}</p>`;
  el.querySelectorAll("[data-stage]").forEach((b) => b.addEventListener("click", () => {
    if (item.link_tab && state.tab !== item.link_tab) show(item.link_tab);
    openStage(`s-st-${b.dataset.stage}`);
  }));
}

function openStage(id) {
  const d = document.getElementById(id);
  if (!d) return;
  d.open = true;
  d.scrollIntoView({ block: "start", behavior: "smooth" });
}

function renderItem(item) {
  if (item.ref) item = state.data.shared[item.ref];
  const el = document.createElement("section");
  el.className = "item";
  el.id = `i-${item.id}`;
  if (item.wide) el.classList.add("wide");
  if (item.chain) renderChain(item, el);
  else if (item.card) renderCard(item, el);
  else if (item.table) renderTable(item, el);
  else if (item.empty) renderEmpty(item, el);
  else { el._pending = item; }
  return el;
}

function drawPending(scope) {
  // 차트는 화면에 보이는 상태로 붙은 뒤에 그려야 크기가 잡힌다
  scope.querySelectorAll(".item").forEach((el) => {
    if (el._pending && !el.closest("details:not([open])")) { renderChart(el._pending, el); delete el._pending; }
  });
}

function renderSections(root, sections) {
  for (const sec of sections) {
    const g = document.createElement("div");
    g.className = "grid";
    sec.items.forEach((it) => g.appendChild(renderItem(it)));
    if (sec.collapsible) {
      const d = document.createElement("details");
      d.className = "stage-detail";
      d.id = `s-${sec.id}`;
      d.innerHTML = `<summary>${esc(sec.title)} <small>${sec.items.length}개 항목 · 누르면 펼침</small></summary>`;
      d.appendChild(g);
      d.addEventListener("toggle", () => { if (d.open) { drawPending(d); state.charts.forEach((c) => c.resize()); } });
      root.appendChild(d);
      continue;
    }
    const h = document.createElement("h2");
    h.className = "sec";
    h.id = `s-${sec.id}`;
    h.textContent = sec.title;
    root.appendChild(h);
    if (sec.intro) { const p = document.createElement("p"); p.className = "note"; p.textContent = sec.intro; root.appendChild(p); }
    root.appendChild(g);
  }
  drawPending(root);
}

// ── 개요 ─────────────────────────────────────────────────────────
function dirHtml(d) {
  if (!d || !d.dir) return "–";
  const arrow = { 상승: "▲", 하락: "▼", 보합: "▶", "판단 불가": "·" }[d.dir] || "";
  const cls = d.good === true ? "up" : d.good === false ? "down" : "";
  return `<span class="dir ${cls}">${arrow} ${esc(d.dir)}</span>`;
}

function renderOverview(root) {
  const o = state.data.tabs.overview;
  if (!o) { root.innerHTML = `<div class="empty">개요 준비 중</div>`; return; }
  const rows = o.checklist.map((r) => `<tr><td>${esc(r.cond)}</td><td><a href="#" data-goto="${esc(r.tab)}|${esc(r.target)}">${esc(r.metric)}</a></td>
    <td class="num">${esc(r.latest)}</td><td>${dirHtml(r)}</td><td>${esc(r.read)}</td><td><span class="grade g-${r.grade}">${esc(r.gradeLabel)}</span></td></tr>`).join("");
  const areas = (state.data.nav || []).filter((g) => g.purpose);
  const tiles = (o.tiles || []).map((t) => `<a class="tile" href="#" data-goto="${esc(t.tab)}|${esc(t.target)}">
      <span class="lbl">${esc(t.label)}</span><b>${esc(t.value)}</b></a>`).join("");
  root.innerHTML = `<div class="purposes">${areas.map((g) => `<p class="purpose area-${g.area}"><span class="area-chip">${esc(g.label)}${g.new ? ' <i class="new">NEW</i>' : ""}</span> ${esc(g.purpose)}</p>`).join("")}</div>
    <h2 class="sec area-demand-h">전방수요 한눈에 <small>전방수요 모니터</small></h2>
    <div class="kpis">${tiles || '<div class="empty">수집 전</div>'}</div>
    <h2 class="sec area-analysis-h">리레이팅 조건 체크리스트 <small>리레이팅·밸류체인 분석 <i class="new">NEW</i></small></h2>
    <div class="item wide"><div class="tscroll"><table><thead><tr><th>조건</th><th>지표</th><th>최근</th><th>최근 방향</th><th>읽는 법</th><th>신뢰 등급</th></tr></thead>
    <tbody>${rows}</tbody></table></div><p class="foot-note">방향: 최근 값과 1년 전(분기 자료는 4개 분기 전) 비교. ▲▼ 색은 리레이팅에 유리한 쪽이 초록.</p></div>
    <h2 class="sec area-analysis-h">플라이휠 — 밸류체인 5단계 <small>리레이팅·밸류체인 분석 <i class="new">NEW</i></small></h2>
    <div id="ov-chain"></div>
    <h2 class="sec">지표 가이드</h2><div id="ov-guide"></div>
    <h2 class="sec">수집기 상태</h2><div class="item wide">${statusTable()}</div>`;
  const slot = $("#ov-chain", root);
  if (o.chain?.chain) { const el = renderItem(o.chain); slot.replaceWith(el); }
  else slot.innerHTML = `<div class="empty">밸류체인 데이터 준비 중</div>`;
  if (o.guide) $("#ov-guide", root).replaceWith(renderItem(o.guide));
  root.querySelectorAll("[data-goto]").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    const [tab, target] = a.dataset.goto.split("|");
    show(tab, target);
  }));
}

function statusTable() {
  const s = state.data.status || {};
  const names = { fx: "환율", stocks: "주가·시총", news: "뉴스", amazon: "아마존 Top50", wiki: "위키 조회수", import_naver: "네이버 엑셀 가져오기", import_amazon: "아마존 엑셀 가져오기",
                  customs: "관세청 수출", tourism: "방한 외국인", dart: "DART 재무", comtrade: "UN Comtrade 수입시장",
                  index: "KOSPI·KOSDAQ 지수", migrate_legacy: "기존 트래커 이관" };
  const rows = Object.keys(names).map((k) => { const x = s[k];
    return `<tr><td>${names[k]}</td><td>${x ? esc(x.상태) : "미실행"}</td><td>${x ? esc(x.최근성공 || "–") : "–"}</td><td>${x ? esc(x.메모) : ""}</td></tr>`; }).join("");
  return `<div class="tscroll"><table><thead><tr><th>수집기</th><th>상태</th><th>최근 성공</th><th>메모·실패 사유</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

// ── 뉴스 ─────────────────────────────────────────────────────────
function renderNews(root) {
  const n = state.data.tabs.news;
  if (!n || !n.items?.length) { root.innerHTML = `<div class="empty"><strong>수집 불가</strong> — ${esc(n?.empty || "뉴스 수집 전")}</div>`; return; }
  let group = "전체";
  const groups = ["전체", ...n.groups];
  const draw = () => {
    const items = n.items.filter((x) => group === "전체" || x.groups.includes(group)).slice(0, 300);
    $("#news-list", root).innerHTML = items.map((x) => `<li><a href="${esc(x.link)}" target="_blank" rel="noopener">${esc(x.title)}</a>
      ${x.title_ko ? `<span class="ko">${esc(x.title_ko)} <small>(기계 번역)</small></span>` : ""}
      <span class="src">${esc(x.source)} · ${esc(x.date)} · ${esc(x.groups.join(", "))}</span></li>`).join("");
  };
  root.innerHTML = `<div class="chips">${groups.map((g) => `<button data-g="${esc(g)}" aria-pressed="${g === group}">${esc(g)}</button>`).join("")}</div>
    <p class="foot-note">Google News RSS · 키워드는 config/tracker.yml · AI 요약 없음 · 최근 갱신 ${esc(state.data.status?.news?.최근성공 || "–")}</p>
    <ul class="news-list" id="news-list"></ul>`;
  root.querySelectorAll("[data-g]").forEach((b) => b.addEventListener("click", () => {
    group = b.dataset.g;
    root.querySelectorAll("[data-g]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    draw();
  }));
  draw();
}

// ── 데이터 한계 · 부록 ────────────────────────────────────────────
function renderLimits(root) {
  const L = state.data.tabs.limits || { items: [] };
  root.innerHTML = `<h2 class="sec">숫자로 답할 수 없는 것</h2><p class="note">${esc(L.intro || "")}</p>
    <div class="item wide"><div class="tscroll"><table><thead><tr><th>항목</th><th>왜 숫자로 안 되는가</th><th>대신 볼 것 / 확인 방법</th></tr></thead><tbody>
    ${L.items.map((x) => `<tr><td>${esc(x.item)}</td><td>${esc(x.why)}</td><td>${esc(x.alt)}</td></tr>`).join("")}</tbody></table></div></div>`;
}

function renderAppendix(root) {
  const A = state.data.tabs.appendix || { rows: [] };
  root.innerHTML = `<h2 class="sec">발표 질문 17개 ↔ 관련 지표</h2><p class="note">${esc(A.intro || "")}</p>
    <div class="item wide"><div class="tscroll"><table><thead><tr><th>#</th><th>질문</th><th>관련 지표</th><th>답변 가능 수준</th></tr></thead><tbody>
    ${A.rows.map((x) => `<tr><td>${esc(x.no)}</td><td>${esc(x.q)}</td><td>${x.links.map((l) =>
      `<a href="#" data-goto="${esc(l.tab)}|${esc(l.target)}">${esc(l.label)}</a>`).join("<br>")}</td><td>${esc(x.level)}</td></tr>`).join("")}</tbody></table></div></div>`;
  root.querySelectorAll("[data-goto]").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault(); const [tab, target] = a.dataset.goto.split("|"); show(tab, target);
  }));
}

// ── 탭 전환 ──────────────────────────────────────────────────────
function show(tab, target) {
  state.tab = tab;
  state.detailTab = tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === tab));
  const area = (state.data.nav || []).find((g) => g.tabs.some(([k]) => k === tab))?.area || "common";
  document.body.dataset.area = area;
  const main = $("#main");
  state.charts.forEach((c) => c.dispose());
  state.charts.clear();
  main.innerHTML = "";
  const d = state.data.tabs[tab];
  if (tab === "overview") renderOverview(main);
  else if (tab === "news") renderNews(main);
  else if (tab === "limits") renderLimits(main);
  else if (tab === "appendix") renderAppendix(main);
  else if (d?.sections) renderSections(main, d.sections);
  else main.innerHTML = `<div class="empty">준비 중</div>`;
  try { history.replaceState(null, "", `#${tab}`); localStorage.setItem("tab", tab); } catch (e) { /* 저장소 없음 */ }
  if (target) {
    const el = document.getElementById(`i-${target}`);
    if (el) { const d = el.closest("details"); if (d) d.open = true; setTimeout(() => el.scrollIntoView({ block: "start" }), 50); }
  }
  else window.scrollTo(0, 0);
}

function renderDetailNav() {
  $("#tabs").innerHTML = (state.data.nav || []).map((g) => `<div class="tab-group area-${g.area}" role="group" aria-label="${esc(g.label || "공통")}">
      ${g.label ? `<span class="group-label">${esc(g.label)}${g.new ? ' <i class="new">NEW</i>' : ""}</span>` : ""}
      ${g.tabs.map(([k, l]) => `<button role="tab" data-tab="${esc(k)}" data-area="${esc(g.area)}" aria-selected="false">${esc(l)}</button>`).join("")}
    </div>`).join("");
  document.querySelectorAll("#tabs [data-tab]").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
}

async function init() {
  try {
    const r = await fetch("data/site.json", { cache: "no-cache" });
    state.data = await r.json();
  } catch (e) {
    $("#main").innerHTML = `<div class="empty">site.json을 불러오지 못했습니다. 로컬에서는 site 폴더에서 <code>python3 -m http.server</code>로 여세요.</div>`;
    return;
  }
  $("#meta").textContent = `빌드 ${state.data.generated} KST`;
  Gloss.init(state.data.story?.glossary || []);
  let saved = {};
  try { saved = { mode: localStorage.getItem("mode"), story: localStorage.getItem("storyTab"), tab: localStorage.getItem("tab") }; } catch (e) { /* 저장소 없음 */ }
  const hash = location.hash.slice(1);
  const isStory = STORY_PAGES.some(([k]) => k === hash);
  state.storyTab = isStory ? hash : (saved.story || "home");
  state.detailTab = !isStory && state.data.tabs[hash] ? hash : (saved.tab && state.data.tabs[saved.tab] ? saved.tab : "overview");
  const detail = !isStory && state.data.tabs[hash] ? true : saved.mode === "detail";
  $("#mode").addEventListener("click", () => setMode(state.mode !== "detail"));
  setMode(detail);
  // 주소창에서 #페이지를 바꾸거나 링크로 들어온 경우 (replaceState는 이 이벤트를 부르지 않음)
  window.addEventListener("hashchange", () => {
    const h = location.hash.slice(1);
    if (STORY_PAGES.some(([k]) => k === h)) { state.storyTab = h; if (state.mode === "detail") setMode(false); else showStory(h); }
    else if (state.data.tabs[h]) { state.detailTab = h; if (state.mode !== "detail") setMode(true); else show(h); }
  });
  let t;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => state.charts.forEach((c) => c.resize()), 150); });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => (state.mode === "detail" ? show(state.tab) : showStory(state.tab)));
}
document.addEventListener("DOMContentLoaded", init);
