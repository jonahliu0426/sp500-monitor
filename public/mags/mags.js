/* MAG 巨头面板 — 前端逻辑 */
"use strict";

let LANG = "zh";
try { LANG = localStorage.getItem("sp5-lang") === "en" ? "en" : "zh"; } catch (e) {}
let THEME = "dark";
try { THEME = localStorage.getItem("sp5-theme") === "light" ? "light" : "dark"; } catch (e) {}
const L = (zh, en) => (LANG === "zh" ? zh : en);
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const pctS = (v, d = 1) => (v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(d) + "%");
const cls = (v) => (v == null ? "" : v > 0.001 ? "pos" : v < -0.001 ? "neg" : "");
let DATA = null, sortKey = "mcap", sortDesc = true;

function line(dates, vals, color) {
  if (!vals || vals.length < 2) return "";
  const min = Math.min(...vals), max = Math.max(...vals), span = max - min || 1;
  const pts = vals.map((v, i) =>
    (i / (vals.length - 1) * 1000).toFixed(1) + "," + (6 + (1 - (v - min) / span) * 104).toFixed(1));
  const lastY = (6 + (1 - (vals[vals.length - 1] - min) / span) * 104).toFixed(1);
  return `<svg viewBox="0 0 1000 120" preserveAspectRatio="none">` +
    `<polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"/>` +
    `<circle cx="1000" cy="${lastY}" r="3.5" fill="${color}"/></svg>`;
}

function corrLabel(c) {
  if (c == null) return ["—", ""];
  if (c >= 0.6) return [L("高相关 · 指数驱动行情", "High corr · index-driven tape"), "warn"];
  if (c <= 0.35) return [L("低相关 · 个股分化行情", "Low corr · stock-picker's tape"), "good"];
  return [L("中等相关", "Moderate correlation"), ""];
}

function renderAgg() {
  const r = DATA.regime;
  const cs = getComputedStyle(document.documentElement);
  const [clabel] = corrLabel(r.avg_corr);
  const ne = r.next_earn;
  const cards = [
    { l: L("组内平均相关性（20日）", "Avg pairwise corr (20d)"),
      v: r.avg_corr == null ? "—" : r.avg_corr.toFixed(2), sub: clabel },
    { l: L("当日组内离散度", "Today's dispersion"), v: r.dispersion.toFixed(1) + " pp",
      sub: L("最强与最弱的涨跌差", "best minus worst") },
    { l: L("MAG 合计权重（占标普）", "MAG share of S&P"), v: r.mag_share.toFixed(1) + "%",
      sub: L("集中度背景", "concentration context") },
    { l: L("最近的财报", "Next earnings"),
      v: ne ? ne.sym + " · " + (ne.days === 0 ? L("今天", "today") : "T-" + ne.days) : "—",
      sub: L("盘前/盘后见下表", "see table for timing") },
  ];
  $("#agg").innerHTML = cards.map((c) =>
    `<div class="agg"><div class="a-label">${c.l}</div><div class="a-value">${c.v}</div>` +
    `<div class="a-sub">${c.sub}</div></div>`).join("");
  const c1 = cs.getPropertyValue("--s1").trim();
  $("#ratio").innerHTML = line(DATA.ratio.dates, DATA.ratio.vals, c1);
}

const COLS = () => [
  { k: "s", t: L("代码", "Ticker") },
  { k: "px", t: L("价格", "Price") },
  { k: "chg", t: L("1日", "1D") },
  { k: "rs5", t: L("RS5日", "RS 5d"), tip: L("5日收益减QQQ", "5d return minus QQQ") },
  { k: "rs20", t: L("RS20日", "RS 20d") },
  { k: "align", t: L("均线排列", "MA stack") },
  { k: "ma50", t: L("距50日线", "vs 50DMA") },
  { k: "d20h", t: L("距20日高", "vs 20d high") },
  { k: "d52", t: L("距52周高", "vs 52w high") },
  { k: "iv30", t: "IV30" },
  { k: "ivprem", t: L("IV溢价", "IV prem"), tip: L("IV30 − 20日实现波动率", "IV30 − realized vol 20d") },
  { k: "ivrank", t: L("IV分位", "IV rank") },
  { k: "pcv", t: "P/C" },
  { k: "pcv10", t: L("P/C 10日", "P/C 10d") },
  { k: "pcoi", t: L("P/C持仓", "P/C OI") },
  { k: "earn", t: L("财报", "Earnings") },
];

function alignTxt(a) {
  return a === "bull" ? L("多头", "Bull") : a === "bear" ? L("空头", "Bear") : L("混合", "Mixed");
}

function renderTable() {
  const cols = COLS();
  $("#tbl thead").innerHTML = "<tr>" + cols.map((c) =>
    `<th data-k="${c.k}" ${c.tip ? `title="${c.tip}"` : ""}>${c.t} <span class="arrow">${sortKey === c.k ? (sortDesc ? "▼" : "▲") : ""}</span></th>`).join("") + "</tr>";
  const rows = [...DATA.rows].sort((a, b) => {
    let x = a[sortKey], y = b[sortKey];
    if (sortKey === "earn") { x = a.earn ? a.earn.days : 9e9; y = b.earn ? b.earn.days : 9e9; }
    if (x == null) return 1;
    if (y == null) return -1;
    if (typeof x === "string") return sortDesc ? String(y).localeCompare(x) : String(x).localeCompare(y);
    return sortDesc ? y - x : x - y;
  });
  $("#tbl tbody").innerHTML = rows.map((r) => {
    const e = r.earn;
    const etxt = e
      ? (e.days != null && e.days >= 0
          ? `<span class="${e.days <= 7 ? "earn-soon" : ""}">${e.date.slice(5)}${e.when ? " " + e.when : ""}（T-${e.days}）</span>`
          : e.date.slice(5))
      : L("未排期", "TBD");
    return `<tr><td class="sym" title="${esc(r.n)}">${r.s}</td>` +
      `<td>$${r.px.toFixed(2)}</td>` +
      `<td class="${cls(r.chg)}">${pctS(r.chg, 2)}</td>` +
      `<td class="${cls(r.rs5)}">${pctS(r.rs5)}</td>` +
      `<td class="${cls(r.rs20)}">${pctS(r.rs20)}</td>` +
      `<td class="align-${r.align}">${alignTxt(r.align)}</td>` +
      `<td class="${cls(r.ma50)}">${pctS(r.ma50)}</td>` +
      `<td>${pctS(r.d20h)}</td><td>${pctS(r.d52)}</td>` +
      `<td>${r.iv30 == null ? "—" : r.iv30.toFixed(1) + "%"}</td>` +
      `<td class="${r.ivprem != null && r.ivprem > 8 ? "neg" : ""}">${r.ivprem == null ? "—" : (r.ivprem > 0 ? "+" : "") + r.ivprem.toFixed(1) + "pp"}</td>` +
      `<td>${r.ivrank == null ? L("积累中(" + r.ivdays + "天)", "building (" + r.ivdays + "d)") : r.ivrank + "%"}</td>` +
      `<td>${r.pcv == null ? "—" : r.pcv.toFixed(2)}</td>` +
      `<td>${r.pcv10 == null ? "—" : r.pcv10.toFixed(2)}</td>` +
      `<td>${r.pcoi == null ? "—" : r.pcoi.toFixed(2)}</td>` +
      `<td>${etxt}</td></tr>`;
  }).join("");
  $("#tbl thead").querySelectorAll("th").forEach((th) => {
    th.onclick = () => {
      const k = th.dataset.k;
      if (sortKey === k) sortDesc = !sortDesc;
      else { sortKey = k; sortDesc = k !== "s" && k !== "earn"; }
      renderTable();
    };
  });
}

function renderAll() {
  document.documentElement.lang = LANG === "zh" ? "zh-CN" : "en";
  document.title = L("MAG 巨头面板 — 相对强度、期权 IV 与 P/C、财报日历 | Weathertop",
    "MAG Mega-caps — RS, options IV & P/C, earnings calendar | Weathertop");
  $("#t-title").textContent = L("MAG 巨头面板", "MAG Mega-caps");
  $("#t-sub").textContent = L("市值前十大公司 · 短线视角", "Top-10 by market cap · swing view");
  $("#t-back").textContent = L("← 成分股面板", "← All constituents");
  $("#r-title").textContent = L("MAG 等权指数 / QQQ（近两年，期初=100）", "MAG equal-weight / QQQ (2 years, start=100)");
  $("#theme-toggle").textContent = THEME === "dark" ? L("☀️ 浅色", "☀️ Light") : L("🌙 深色", "🌙 Dark");
  $("#lang-toggle").textContent = LANG === "zh" ? "EN · English" : "中 · 中文";
  const d = new Date(DATA.updated_at * 1000);
  $("#t-asof").textContent = L("数据截至 ", "As of ") + DATA.asof +
    L("（更新于 ", " (updated ") + d.toLocaleString(LANG === "zh" ? "zh-CN" : "en-US") + "）";
  renderAgg();
  renderTable();
  $("#t-foot").innerHTML = L(
    "成员为每日按市值动态选取的前十大公司（Alphabet 双类股合并计一家）。期权数据来自 Cboe 延迟报价（官方 IV），P/C 为全链成交量口径；IV 分位随每日积累渐进成型，积累不足时显示天数。全部为 EOD 收盘后数据——本面板是「每晚的作战准备」，不提供盘中信号。仅供研究参考，不构成投资建议。 · <a href='/'>成分股面板</a> · <a href='https://weathertop.app/'>主面板</a>",
    "Membership = top-10 companies by market cap, selected daily (Alphabet share classes merged). Options data from Cboe delayed quotes (official IV); P/C uses full-chain volume; IV rank builds up day by day and shows accumulation count until ready. Everything is end-of-day — this panel is evening prep, not intraday signals. Research only, not investment advice. · <a href='/'>All constituents</a> · <a href='https://weathertop.app/'>Main dashboard</a>");
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
  fetch("/data/mags.json", { cache: "no-cache" })
    .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then((j) => { DATA = j; renderAll(); })
    .catch((e) => {
      $("#agg").innerHTML = `<div class="agg"><div class="a-value" style="font-size:15px">` +
        L("数据加载失败：", "Failed to load: ") + esc(e.message) + "</div></div>";
    });
}
boot();
