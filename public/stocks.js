/* 标普500 成分股面板 — 前端逻辑（加载 stocks.json，渲染聚合卡/板块格/可排序表格） */
"use strict";

let LANG = "zh";
try { LANG = localStorage.getItem("sp5-lang") === "en" ? "en" : "zh"; } catch (e) {}
let THEME = "dark";
try { THEME = localStorage.getItem("sp5-theme") === "light" ? "light" : "dark"; } catch (e) {}
const L = (zh, en) => (LANG === "zh" ? zh : en);

const SEC_ZH = {
  "Information Technology": "信息技术", "Financials": "金融", "Health Care": "医疗保健",
  "Consumer Discretionary": "可选消费", "Communication Services": "通信服务",
  "Industrials": "工业", "Consumer Staples": "必需消费", "Energy": "能源",
  "Utilities": "公用事业", "Materials": "材料", "Real Estate": "房地产",
};
const secName = (s) => (LANG === "zh" ? (SEC_ZH[s] || s) : s);

let DATA = null;
let sortKey = "w", sortDesc = true, query = "", secSel = "";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const fmtChg = (v) => (v > 0 ? "+" : "") + v.toFixed(2) + "%";
const chgCls = (v) => (v > 0.001 ? "pos" : v < -0.001 ? "neg" : "");

function spark(values, color) {
  const vs = values.filter((v) => v != null);
  if (vs.length < 2) return "";
  const min = Math.min(...vs), max = Math.max(...vs), span = max - min || 1;
  const pts = vs.map((v, i) =>
    (i / (vs.length - 1) * 100).toFixed(1) + "," + (3 + (1 - (v - min) / span) * 28).toFixed(1));
  return `<svg class="spark" viewBox="0 0 100 34" preserveAspectRatio="none">` +
    `<polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
}

function renderStatic() {
  document.documentElement.lang = LANG === "zh" ? "zh-CN" : "en";
  document.title = L("标普500 成分股面板 — 集中度、宽度与全成分行情 | Weathertop",
    "S&P 500 Constituents — Concentration, Breadth & Quotes | Weathertop");
  $("#t-title").textContent = L("标普500 成分股面板", "S&P 500 Constituents");
  $("#t-sub").textContent = L("集中度 · 内部宽度 · 全成分行情", "Concentration · internal breadth · all constituents");
  $("#t-home").textContent = L("← 主面板 weathertop.app", "← Main dashboard weathertop.app");
  $("#q").placeholder = L("搜索代码或公司名…", "Search ticker or name…");
  $("#t-hint").textContent = L("点击表头排序 · 数据为最近收盘（EOD）", "Click headers to sort · latest close (EOD)");
  $("#theme-toggle").textContent = THEME === "dark" ? L("☀️ 浅色", "☀️ Light") : L("🌙 深色", "🌙 Dark");
  $("#lang-toggle").textContent = LANG === "zh" ? "EN · English" : "中 · 中文";
}

function renderAgg() {
  const a = DATA.agg, s = DATA.series;
  const cs = getComputedStyle(document.documentElement);
  const c1 = cs.getPropertyValue("--s1").trim(), c3 = cs.getPropertyValue("--s3").trim();
  const cards = [
    { l: L("上涨家数占比（当日）", "Advancers (today)"), v: a.adv_pct.toFixed(0) + "%",
      sub: L(`${a.n} 只成分股`, `${a.n} constituents`), sp: spark(s.advpct, c3) },
    { l: L("前十大市值权重", "Top-10 weight"), v: a.top10_w.toFixed(1) + "%",
      sub: L("集中度（历史高位区）", "Concentration"), sp: spark(s.top10w, c1) },
    { l: L("当日涨跌贡献 · 前十大占比", "Top-10 share of today's move"),
      v: a.contrib_share == null ? "—" : a.contrib_share.toFixed(0) + "%",
      sub: L("指数当日变动中头部贡献", "of index move from top 10"), sp: "" },
    { l: L("成分股总市值", "Total market cap"), v: "$" + a.total_mcap_t.toFixed(1) + "T",
      sub: L(`52周位置回填进度 ${a.backfilled}/${a.total}`, `52-wk backfill ${a.backfilled}/${a.total}`), sp: "" },
  ];
  $("#agg").innerHTML = cards.map((c) =>
    `<div class="agg"><div class="a-label">${c.l}</div><div class="a-value">${c.v}</div>` +
    `<div class="a-sub">${c.sub}</div>${c.sp}</div>`).join("");
}

function renderSectors() {
  $("#sectors").innerHTML = DATA.sectors.map((s) =>
    `<div class="sec-cell"><div class="s-name">${esc(secName(s.sector))}</div>` +
    `<div class="s-line"><span class="${chgCls(s.median_chg)}">${fmtChg(s.median_chg)}</span>` +
    ` · ${L("上涨", "adv")} ${s.adv_pct.toFixed(0)}% · ${L("权重", "wt")} ${s.weight}%</div></div>`).join("");
}

const COLS = () => [
  { k: "s", t: L("代码", "Ticker") }, { k: "n", t: L("公司", "Company") },
  { k: "sec", t: L("板块", "Sector") }, { k: "px", t: L("价格", "Price") },
  { k: "chg", t: L("当日", "1D %") }, { k: "mcap", t: L("市值($B)", "Mkt cap ($B)") },
  { k: "w", t: L("权重", "Weight") }, { k: "d52", t: L("距52周高", "vs 52-wk hi") },
];

function renderTable() {
  const cols = COLS();
  $("#tbl thead").innerHTML = "<tr>" + cols.map((c) =>
    `<th data-k="${c.k}">${c.t} <span class="arrow">${sortKey === c.k ? (sortDesc ? "▼" : "▲") : ""}</span></th>`).join("") + "</tr>";
  let rows = DATA.rows;
  if (secSel) rows = rows.filter((r) => r.sec === secSel);
  if (query) {
    const q = query.toUpperCase();
    rows = rows.filter((r) => r.s.includes(q) || r.n.toUpperCase().includes(q));
  }
  rows = [...rows].sort((a, b) => {
    let x = a[sortKey], y = b[sortKey];
    if (x == null) return 1;
    if (y == null) return -1;
    if (typeof x === "string") { x = x.toUpperCase(); y = String(y).toUpperCase(); return sortDesc ? (y < x ? -1 : 1) : (x < y ? -1 : 1); }
    return sortDesc ? y - x : x - y;
  });
  $("#tbl tbody").innerHTML = rows.map((r) =>
    `<tr><td class="sym">${r.s}</td><td class="name">${esc(r.n)}</td><td>${esc(secName(r.sec))}</td>` +
    `<td>$${r.px.toFixed(2)}</td><td class="${chgCls(r.chg)}">${fmtChg(r.chg)}</td>` +
    `<td>${r.mcap.toLocaleString("en-US")}</td><td>${r.w.toFixed(2)}%</td>` +
    `<td class="${r.d52 != null && r.d52 <= -20 ? "neg" : ""}">${r.d52 == null ? "…" : r.d52.toFixed(1) + "%"}</td></tr>`).join("");
  $("#tbl thead").querySelectorAll("th").forEach((th) => {
    th.onclick = () => {
      const k = th.dataset.k;
      if (sortKey === k) sortDesc = !sortDesc; else { sortKey = k; sortDesc = k !== "s" && k !== "n" && k !== "sec"; }
      renderTable();
    };
  });
}

function renderAll() {
  renderStatic();
  const d = new Date(DATA.updated_at * 1000);
  $("#t-asof").textContent = L("数据截至 ", "As of ") + DATA.asof +
    L("（更新于 ", " (updated ") + d.toLocaleString(LANG === "zh" ? "zh-CN" : "en-US") + "）";
  renderAgg();
  renderSectors();
  const sel = $("#secfilter");
  sel.innerHTML = `<option value="">${L("全部板块", "All sectors")}</option>` +
    DATA.sectors.map((s) => `<option value="${esc(s.sector)}">${esc(secName(s.sector))}</option>`).join("");
  sel.value = secSel;
  renderTable();
  $("#t-foot").innerHTML = L(
    "数据：Nasdaq 批量行情（未复权收盘）、Wikipedia 成分股名单；权重为市值近似（非官方流通市值口径）。「距52周高」随每日回填逐步补齐。仅供研究参考，不构成投资建议。 · <a href='https://weathertop.app/'>返回主面板</a>",
    "Data: Nasdaq bulk quotes (unadjusted closes), Wikipedia constituent list; weights are market-cap approximations (not official float-adjusted). 52-wk column fills in as daily backfill completes. Research only — not investment advice. · <a href='https://weathertop.app/'>Main dashboard</a>");
}

function boot() {
  document.documentElement.dataset.theme = THEME === "light" ? "light" : "";
  $("#theme-toggle").onclick = () => {
    THEME = THEME === "dark" ? "light" : "dark";
    try { localStorage.setItem("sp5-theme", THEME); } catch (e) {}
    document.documentElement.dataset.theme = THEME === "light" ? "light" : "";
    renderAll();
  };
  $("#lang-toggle").onclick = () => {
    LANG = LANG === "zh" ? "en" : "zh";
    try { localStorage.setItem("sp5-lang", LANG); } catch (e) {}
    renderAll();
  };
  $("#q").addEventListener("input", (e) => { query = e.target.value.trim(); renderTable(); });
  $("#secfilter").addEventListener("change", (e) => { secSel = e.target.value; renderTable(); });
  fetch("data/stocks.json", { cache: "no-cache" })
    .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then((j) => { DATA = j; renderAll(); })
    .catch((e) => {
      $("#agg").innerHTML = `<div class="agg"><div class="a-value" style="font-size:15px">` +
        L("数据加载失败：", "Failed to load data: ") + esc(e.message) + "</div></div>";
    });
}
boot();
