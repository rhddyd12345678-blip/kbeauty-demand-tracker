/* 쉬운 화면(질문 중심). 데이터는 site.json의 story. 상세 화면은 app.js("자세히 보기"). */
"use strict";

const STORY_PAGES = [
  ["home", "홈"], ["exports", "수출"], ["consumers", "해외 소비자"], ["chain", "밸류체인"],
  ["stocks", "주가"], ["booster", "스킨부스터"], ["news", "뉴스"], ["help", "도움말"],
];

// ── 용어 툴팁 ────────────────────────────────────────────────────
const Gloss = {
  terms: [], used: new Set(), pop: null,
  init(list) {
    this.terms = [];
    for (const g of list) for (const t of [g.term, ...(g.alias || [])]) this.terms.push({ t, g });
    this.terms.sort((a, b) => b.t.length - a.t.length); // 긴 표기부터 (예: '후행 PER'이 'PER'보다 먼저)
    if (!this.pop) {
      this.pop = document.createElement("div");
      this.pop.className = "term-pop";
      this.pop.setAttribute("role", "tooltip");
      this.pop.id = "term-pop";
      this.pop.hidden = true;
      document.body.appendChild(this.pop);
      document.addEventListener("click", (e) => { if (!e.target.closest(".term")) this.hide(); });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") this.hide(); });
      window.addEventListener("scroll", () => this.hide(), { passive: true });
    }
  },
  reset() { this.used = new Set(); this.hide(); },
  /** el 안의 글자에서 용어를 찾아 페이지마다 처음 한 번만 밑줄 버튼으로 바꾼다. */
  annotate(el) {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.parentElement.closest(".term, button, a, script, style, .no-gloss") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      let text = node.nodeValue;
      let hit = null;
      for (const x of this.terms) {
        if (this.used.has(x.g.term)) continue;
        const re = /^[A-Za-z0-9]/.test(x.t) ? new RegExp(`(^|[^A-Za-z0-9])(${x.t.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")})(?![A-Za-z0-9])`) : null;
        const i = re ? (() => { const m = re.exec(text); return m ? m.index + m[1].length : -1; })() : text.indexOf(x.t);
        if (i >= 0 && (!hit || i < hit.i)) hit = { i, x };
      }
      if (!hit) continue;
      this.used.add(hit.x.g.term);
      const before = document.createTextNode(text.slice(0, hit.i));
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "term";
      btn.textContent = hit.x.t;
      btn.dataset.term = hit.x.g.term;
      btn.setAttribute("aria-describedby", "term-pop");
      const after = document.createTextNode(text.slice(hit.i + hit.x.t.length));
      node.replaceWith(before, btn, after);
      const g = hit.x.g;
      btn.addEventListener("mouseenter", () => this.show(btn, g));
      btn.addEventListener("mouseleave", () => this.hide());
      btn.addEventListener("focus", () => this.show(btn, g));
      btn.addEventListener("blur", () => this.hide());
      btn.addEventListener("click", (e) => { e.stopPropagation(); this.pop.hidden ? this.show(btn, g) : this.hide(); });
      this.annotate(el); // 같은 노드의 나머지 부분에서 다른 용어 찾기
      return;
    }
  },
  show(btn, g) {
    this.pop.innerHTML = `<b>${esc(g.term)}</b><span>${esc(g.desc)}</span>${g.ex ? `<i>${esc(g.ex)}</i>` : ""}`;
    this.pop.hidden = false;
    const r = btn.getBoundingClientRect(), p = this.pop.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    let left = Math.min(Math.max(8, r.left + r.width / 2 - p.width / 2), vw - p.width - 8);
    let top = r.bottom + 8;
    if (top + p.height > window.innerHeight - 8) top = r.top - p.height - 8; // 아래가 모자라면 위로
    this.pop.style.left = `${left + window.scrollX}px`;
    this.pop.style.top = `${top + window.scrollY}px`;
  },
  hide() { if (this.pop) this.pop.hidden = true; },
};

// ── 공통 조각 ────────────────────────────────────────────────────
function statusPill(s) {
  return `<span class="pill tone-${esc(s.tone)}"><span aria-hidden="true">${esc(s.arrow)}</span> ${esc(s.text)}</span>`;
}

function fmtVal(v, unit) {
  if (v == null) return "–";
  if (unit === "usd") return Math.abs(v) >= 1e8 ? `${(v / 1e8).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억 달러` : `${Math.round(v / 1e4).toLocaleString("ko-KR")}만 달러`;
  if (unit === "%") return `${v.toFixed(1)}%`;
  if (unit === "배") return `${v.toFixed(2)}배`;
  return `${v.toLocaleString("ko-KR")}${unit || ""}`;
}

function sparkline(el, spark, tone) {
  if (!spark?.data?.length || typeof echarts === "undefined") { el.remove(); return; }
  const data = spark.data.map((p) => [toTime(p[0]), p[1]]);
  const last = data[data.length - 1];
  const col = { good: css("--tone-good"), weak: css("--tone-weak"), bad: css("--tone-bad") }[tone] || css("--ink-2");
  const c = echarts.init(el, null, { renderer: "svg" });
  state.charts.set(el.dataset.id, c);
  c.setOption({
    animation: false, grid: { left: 2, right: 8, top: 6, bottom: 4 },
    xAxis: { type: "time", show: false }, yAxis: { type: "value", show: false, scale: true },
    series: [{ type: "line", data, showSymbol: false, lineStyle: { width: 2, color: col },
               areaStyle: { color: col, opacity: 0.08 },
               markPoint: { symbol: "circle", symbolSize: 7, itemStyle: { color: col }, label: { show: false }, data: [{ coord: last }] } }],
  });
  el.setAttribute("aria-label", `${spark.label || "추세"}: 최근 값 ${fmtVal(last[1], spark.unit)}`);
}

// ── 홈 ───────────────────────────────────────────────────────────
function renderHome(root, st) {
  const h = st.home;
  root.innerHTML = `
    <section class="hero gl"><p class="eyebrow">오늘의 K뷰티 한눈에</p><p class="headline">${esc(h.summary)}</p>
      <p class="hint">점선 밑줄이 있는 용어에 마우스를 올리거나 누르면 뜻이 나와요.</p></section>
    <div class="qgrid">${h.cards.map((c) => `
      <article class="qcard" id="c-${esc(c.id)}">
        <h2 class="q gl">${esc(c.question)}</h2>
        <p class="a gl">${esc(c.answer)}</p>
        <div class="qrow">${statusPill(c.status)}<div class="spark" data-id="${esc(c.id)}" role="img"></div></div>
        ${c.sub ? `<p class="sub gl">${esc(c.sub)}</p>` : ""}
        ${c.rows ? `<details class="more"><summary>더 알아보기</summary><div class="tscroll"><table class="gl"><thead><tr>${c.rows.columns.map((x) => `<th>${esc(x)}</th>`).join("")}</tr></thead>
          <tbody>${c.rows.data.map((r) => `<tr>${r.map((v) => `<td${typeof v === "number" ? ' class="num"' : ""}>${esc(typeof v === "number" ? v.toLocaleString("ko-KR") : v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details>` : ""}
        <a class="go" href="#${esc(c.page)}" data-page="${esc(c.page)}">자세히 →</a>
      </article>`).join("")}
    </div>
    <section class="changes gl"><h2>이번 달에 달라진 것</h2><ul>${h.changes.map((x) =>
      `<li><a href="#${esc(x.page)}" data-page="${esc(x.page)}">${esc(x.text)}</a></li>`).join("")}</ul>
      <p class="hint">지난 기간(1년 전 같은 시기) 대비 변화를 판정 기준으로 나눈 값이 큰 순서로 골랐어요.</p></section>`;
  root.querySelectorAll(".spark").forEach((el) => {
    const c = h.cards.find((x) => x.id === el.dataset.id);
    sparkline(el, c.spark, c.status.tone);
  });
  root.querySelectorAll("[data-page]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); showStory(a.dataset.page); }));
  root.querySelectorAll(".gl").forEach((el) => Gloss.annotate(el));
}

// ── 질문 페이지 ───────────────────────────────────────────────────
function unitFmt(v, unit, axis) {
  if (v == null || Number.isNaN(v)) return "–";
  if (unit === "usd") {
    const e = v / 1e8;
    return axis ? `${e.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억` : `${e.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억 달러`;
  }
  if (unit === "usd_kg") return axis ? `${v.toFixed(0)}달러` : `${v.toFixed(1)}달러/kg`;
  if (unit === "명") return axis ? `${(v / 1e4).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}만` : `${(v / 1e4).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}만 명`;
  if (unit === "%") return `${v.toFixed(1)}%`;
  return v.toLocaleString("ko-KR", { maximumFractionDigits: 1 });
}
const monthLabel = (t) => { const d = new Date(t); return `${d.getFullYear()}년 ${d.getMonth() + 1}월`; };

function avg3(data) {
  return data.map((p, i) => (i < 2 ? null : [p[0], (data[i][1] + data[i - 1][1] + data[i - 2][1]) / 3])).filter(Boolean);
}

function storyChartOption(ch, view) {
  const cutoff = view.range ? Date.now() - view.range * 365.25 * 864e5 : -Infinity;
  const gray = css("--story-gray"), ink2 = css("--ink-2"), muted = css("--muted"), grid = css("--grid"), axis = css("--axis"), surface = css("--surface");
  const shown = ch.series.filter((s) => s.role !== "extra" || view.extra);
  const series = [];
  for (const s of shown) {
    const raw = s.data.map((p) => [toTime(p[0]), p[1]]).filter((p) => p[0] >= cutoff);
    const line = s.smooth ? avg3(s.data.map((p) => [toTime(p[0]), p[1]])).filter((p) => p[0] >= cutoff) : raw;
    const main = s.role === "main";
    const col = main ? css(s.color) : (s.role === "extra" ? css(s.color) : gray);
    if (s.smooth && main) {  // 원래 값은 옅게 뒤에
      series.push({ name: `${s.name} (월)`, type: "line", data: raw, showSymbol: false, silent: true, z: 1,
                    lineStyle: { width: 1, color: col, opacity: 0.25 }, tooltip: { show: false } });
    }
    series.push({
      name: s.name, type: "line", data: line, z: main ? 3 : 2,
      // 스냅샷(불규칙한 날짜)은 점을 찍어, 점 사이는 관측이 없다는 걸 보이게 (연결선은 점선)
      showSymbol: ch.freq === "S", symbol: "circle", symbolSize: main ? 7 : 5,
      lineStyle: { width: main ? 3 : 1.6, color: col, opacity: main ? 1 : 0.9, type: ch.freq === "S" ? [4, 4] : "solid" }, itemStyle: { color: col },
      endLabel: { show: true, formatter: () => s.name, color: main ? css("--ink") : ink2, fontSize: 12, fontWeight: main ? 700 : 500, distance: 6 },
      labelLayout: { moveOverlap: "shiftY" }, emphasis: { disabled: true },
    });
  }
  if (ch.events?.length && series.length) {
    series[series.length - 1].markLine = { symbol: "none", silent: true, lineStyle: { color: muted, width: 1, type: "solid" },
      label: { formatter: (p) => p.name, color: ink2, fontSize: 11, position: "insideEndTop" },
      data: ch.events.map((e) => ({ name: e.label, xAxis: toTime(e.x) })) };
  }
  const byName = Object.fromEntries(ch.series.map((s) => [s.name, s]));
  return {
    animation: false,
    grid: { left: 4, right: 96, top: 16, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "axis", confine: true, backgroundColor: surface, borderColor: axis, textStyle: { color: css("--ink"), fontSize: 13 },
      formatter: (ps) => {
        const t = ps[0]?.value[0];
        const lines = ps.filter((p) => byName[p.seriesName]).map((p) => {
          const s = byName[p.seriesName];
          const rawPt = s.data.find((d) => toTime(d[0]) === t);
          const v = s.smooth ? rawPt?.[1] : p.value[1];
          const prev = s.data.find((d) => { const x = new Date(t); x.setFullYear(x.getFullYear() - 1); return toTime(d[0]) === x.getTime(); });
          const yoy = s.smooth && prev && prev[1] ? ` (YoY ${(v / prev[1] - 1) * 100 >= 0 ? "+" : ""}${((v / prev[1] - 1) * 100).toFixed(0)}%)` : "";
          const extra = s.smooth ? ` · 3개월 평균 ${unitFmt(p.value[1], ch.unit)}` : "";
          return `${esc(s.name)}: <b>${unitFmt(v, ch.unit)}</b>${yoy}${extra}`;
        });
        return `<b>${monthLabel(t)}</b><br>${lines.join("<br>")}`;
      },
      axisPointer: { type: "line", lineStyle: { color: axis } },
    },
    xAxis: { type: "time", axisLine: { lineStyle: { color: axis } }, splitLine: { show: false },
             minInterval: view.range === 1 ? 30 * 864e5 : 365 * 864e5,
             axisLabel: { color: muted, fontSize: 11, hideOverlap: true,
                          formatter: view.range === 1 ? (v) => `${new Date(v).getMonth() + 1}월` : "{yyyy}" } },
    yAxis: { type: "value", scale: ch.unit !== "%", axisLabel: { color: muted, fontSize: 11, formatter: (v) => unitFmt(v, ch.unit, true) },
             splitLine: { lineStyle: { color: grid } } },
    series,
  };
}

function csvOf(card) {
  const ch = card.chart;
  const dates = [...new Set(ch.series.flatMap((s) => s.data.map((p) => p[0])))].sort();
  const maps = ch.series.map((s) => new Map(s.data));
  const lines = [["기준일", ...ch.series.map((s) => s.name)], ...dates.map((d) => [d, ...maps.map((m) => m.get(d) ?? "")])];
  lines.push([], ["출처", card.more?.출처 || ""], ["갱신", card.more?.갱신 || ""]);
  const blob = new Blob(["\ufeff" + lines.map((r) => r.map((c) => (/[",\n]/.test(String(c)) ? `"${String(c).replace(/"/g, '""')}"` : c)).join(",")).join("\n")],
                        { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${card.id}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function tableHtml(t) {
  const cell = (v) => (v && typeof v === "object" && v.level ? `<td>${statusPill(v)}</td>`
    : `<td${typeof v === "number" ? ' class="num"' : ""}>${esc(typeof v === "number" ? v.toLocaleString("ko-KR") : v)}</td>`);
  return `<div class="tscroll"><table class="gl stable"><thead><tr>${t.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
    <tbody>${t.rows.map((r) => `<tr>${r.map(cell).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function chainHtml(boxes) {
  return `<div class="chain story-chain">
    <div class="flow goods"><span>물건 흐름</span><i></i><b>▶</b></div>
    <div class="chain-row">${boxes.map((b, i) => `${i ? '<div class="chain-arrow" aria-hidden="true">→</div>' : ""}
      <div class="stage-box sbox"><span class="stage-name">${i + 1}. ${esc(b.name)}</span>
        ${b.metrics.map((m) => `<div class="sm"><span class="lbl">${esc(m.label)}</span><b>${esc(m.value)}</b>
          <span class="sub">${esc(m.sub || "")}</span>${statusPill(m)}</div>`).join("")}
        <span class="stage-members">${esc((b.members || []).join(", ") || "–")}</span></div>`).join("")}</div>
    <div class="flow demand"><b>◀</b><i></i><span>주문 신호</span></div></div>`;
}

function renderStoryCard(card) {
  const el = document.createElement("article");
  el.className = "qcard scard" + (card.wide ? " wide" : "");
  el.id = `c-${card.id}`;
  const hasExtra = card.chart?.series.some((s) => s.role === "extra");
  el.innerHTML = `
    <h2 class="q gl">${esc(card.question)}</h2>
    <p class="a gl">${esc(card.answer)}</p>
    <div class="qrow">${statusPill(card.status)}</div>
    ${card.sub ? `<p class="sub gl">${esc(card.sub)}</p>` : ""}
    ${card.chain ? chainHtml(card.chain) : ""}
    ${card.table && card.table_place === "body" ? tableHtml(card.table) : ""}
    ${card.chart ? `<div class="ctl story-ctl">
        <span class="seg" role="group" aria-label="기간">${[[1, "1년"], [3, "3년"], [0, "전체"]].map(([n, l]) => `<button data-range="${n}" aria-pressed="${n === 3}">${l}</button>`).join("")}</span>
        ${hasExtra ? `<button class="extra-btn" aria-pressed="false">다른 항목 보기</button>` : ""}
      </div><div class="splot" role="img" aria-label="${esc(card.question)} 그래프"></div>` : ""}
    ${card.why ? `<p class="why gl"><b>왜 중요할까요?</b> ${esc(card.why)}</p>` : ""}
    ${card.more ? `<details class="more"><summary>더 알아보기</summary><dl class="gl">${Object.entries(card.more).map(([k, v]) =>
      `<dt>${esc(k)}</dt><dd>${esc(v || "–")}</dd>`).join("")}</dl>${card.table && card.table_place !== "body" ? tableHtml(card.table) : ""}
      ${card.chart ? `<button class="dl csv-btn">CSV 내려받기</button>` : ""}</details>` : ""}`;
  if (card.chart) {
    const view = { range: 3, extra: false };
    const plot = $(".splot", el);
    const draw = () => {
      let c = state.charts.get(card.id);
      if (!c) { c = echarts.init(plot, null, { renderer: "canvas" }); state.charts.set(card.id, c); }
      c.setOption(storyChartOption(card.chart, view), true);
    };
    el._draw = draw;
    el.querySelectorAll("[data-range]").forEach((b) => b.addEventListener("click", () => {
      view.range = +b.dataset.range;
      el.querySelectorAll("[data-range]").forEach((x) => x.setAttribute("aria-pressed", x === b));
      draw();
    }));
    const xb = $(".extra-btn", el);
    if (xb) xb.addEventListener("click", () => { view.extra = !view.extra; xb.setAttribute("aria-pressed", view.extra); draw(); });
    const cb = $(".csv-btn", el);
    if (cb) cb.addEventListener("click", () => csvOf(card));
  }
  return el;
}

function renderPage(root, page) {
  root.innerHTML = `<header class="phead"><h1 class="gl">${esc(page.question)}</h1>${page.label ? `<span class="plabel">${esc(page.label)}</span>` : ""}</header>
    <div class="qgrid"></div>`;
  const grid = $(".qgrid", root);
  page.cards.forEach((c) => grid.appendChild(renderStoryCard(c)));
  grid.querySelectorAll(".scard").forEach((el) => el._draw && el._draw());
  root.querySelectorAll(".gl").forEach((el) => Gloss.annotate(el));
}

function renderHelp(root, h) {
  root.innerHTML = `<header class="phead"><h1>도움말</h1></header>
    <section class="qcard wide help-sec"><h2 class="q">이 사이트가 알 수 없는 것</h2>
      <ul class="unknowns">${h.unknowns.map((x) => `<li><b>${esc(x.q)}</b><span>${esc(x.a)}</span></li>`).join("")}</ul></section>
    <section class="qcard wide help-sec"><h2 class="q">수동으로 입력하는 항목</h2>${tableHtml(h.manual)}
      <p class="hint">파일에 행을 추가하고 커밋하면 다음 빌드에 반영돼요. 자세한 방법은 README.</p></section>
    <section class="qcard wide help-sec"><h2 class="q">용어 사전</h2><div class="no-gloss">${tableHtml(h.glossary)}</div>
      <p class="hint">용어는 config/glossary.csv 한 곳에서 관리해요. 여기 있는 용어는 모든 페이지에서 처음 나올 때 점선 밑줄로 표시돼요.</p></section>`;
}

function renderPlaceholder(root, key) {
  const label = STORY_PAGES.find(([k]) => k === key)?.[1] || key;
  root.innerHTML = `<div class="empty story-empty"><strong>${esc(label)}</strong> 페이지는 다음 단계에서 만들어요.
    지금은 오른쪽 위 <b>자세히 보기</b>를 켜면 이전 화면에서 볼 수 있어요.</div>`;
}

// ── 모드·탭 전환 ──────────────────────────────────────────────────
function renderStoryNav() {
  $("#tabs").innerHTML = `<div class="tab-group story">${STORY_PAGES.map(([k, l]) =>
    `<button role="tab" data-story="${k}" aria-selected="false">${esc(l)}</button>`).join("")}</div>`;
  document.querySelectorAll("#tabs [data-story]").forEach((b) => b.addEventListener("click", () => showStory(b.dataset.story)));
}

function showStory(key) {
  state.tab = key;
  state.storyTab = key;
  document.querySelectorAll("#tabs [data-story]").forEach((b) => b.setAttribute("aria-selected", b.dataset.story === key));
  document.body.dataset.area = "story";
  const main = $("#main");
  state.charts.forEach((c) => c.dispose());
  state.charts.clear();
  Gloss.reset();
  main.innerHTML = "";
  const st = state.data.story;
  if (key === "home") renderHome(main, st);
  else if (st.pages?.[key]) renderPage(main, st.pages[key]);
  else if (key === "help") renderHelp(main, st.help);
  else if (key === "news") { renderNews(main); main.querySelectorAll(".news-list").forEach((el) => el.classList.add("no-gloss")); }
  else renderPlaceholder(main, key);
  try { history.replaceState(null, "", `#${key}`); localStorage.setItem("storyTab", key); } catch (e) { /* 저장소 없음 */ }
  window.scrollTo(0, 0);
}

function setMode(detail) {
  state.mode = detail ? "detail" : "story";
  const sw = $("#mode");
  sw.setAttribute("aria-checked", String(detail));
  try { localStorage.setItem("mode", state.mode); } catch (e) { /* 저장소 없음 */ }
  if (detail) { renderDetailNav(); show(state.detailTab || "overview"); }
  else { renderStoryNav(); showStory(state.storyTab || "home"); }
}
