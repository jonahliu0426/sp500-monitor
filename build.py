#!/usr/bin/env python3
"""标普500成分股面板 — 数据管道（Python 标准库，零第三方依赖）。

每次运行：
1. 成分股名单：Wikipedia（缓存 7 天，含名称/GICS 板块）
2. 全市场快照：Nasdaq 批量筛选器（一次调用 ~7000 行，过滤出成分股）
3. 快照累积：data/snapshots/YYYY-MM-DD.json（近90天每日 + 更早仅保留周五）
4. 慢速回填：每次补 25 只成分股的十年日线（按市值从大到小），
   3-4 周补齐全部 500 只，解锁 52 周位置与均线宽度类指标
5. 聚合计算 → public/data/stocks.json（集中度/宽度/板块统计/个股表格）

教训沿用主站：Wikipedia/curl 默认 UA；api.nasdaq.com 需浏览器 UA + Origin/Referer。
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
DATA = os.path.join(ROOT, "data")
SNAP_DIR = os.path.join(DATA, "snapshots")
HIST_DIR = os.path.join(DATA, "history")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
NASDAQ_HEADERS = ["-A", UA, "-H", "Accept: application/json",
                  "-H", "Origin: https://www.nasdaq.com",
                  "-H", "Referer: https://www.nasdaq.com/"]
BACKFILL_PER_RUN = 25


def http_get(url, extra_args=None, timeout=40):
    cmd = ["curl", "-sS", "-f", "--compressed", "--max-time", str(timeout),
           "--config", "-"] + (extra_args or [])
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout + 15,
                           input=('url = "%s"\n' % url).encode())
    except subprocess.TimeoutExpired:
        raise RuntimeError("请求超时: " + url.split("?")[0])
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError("抓取失败(%d): %s" % (p.returncode, err[-1] if err else url))
    return p.stdout.decode("utf-8", errors="replace")


class _WikiTable(HTMLParser):
    """提取第一张 wikitable 的每行单元格文本。"""
    def __init__(self):
        super().__init__()
        self.in_table = self.done = False
        self.depth = 0
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "table":
            cls = dict(attrs).get("class", "")
            if not self.in_table and "wikitable" in cls:
                self.in_table = True
            elif self.in_table:
                self.depth += 1
        elif self.in_table and self.depth == 0:
            if tag == "tr":
                self.row = []
            elif tag in ("td", "th"):
                self.cell = []

    def handle_endtag(self, tag):
        if self.done or not self.in_table:
            return
        if tag == "table":
            if self.depth:
                self.depth -= 1
            else:
                self.in_table = False
                self.done = True
        elif self.depth == 0:
            if tag in ("td", "th") and self.cell is not None and self.row is not None:
                self.row.append("".join(self.cell).strip())
                self.cell = None
            elif tag == "tr" and self.row:
                self.rows.append(self.row)
                self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def load_constituents():
    """成分股 [{sym, name, sector}]，Wikipedia 缓存 7 天。sym 用 Nasdaq 格式（/）。"""
    path = os.path.join(DATA, "constituents.json")
    if os.path.exists(path):
        with open(path) as f:
            cached = json.load(f)
        if time.time() - cached.get("fetched_at", 0) < 7 * 86400:
            return cached["rows"]
    try:
        html = http_get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        p = _WikiTable()
        p.feed(html)
        rows = []
        for r in p.rows[1:]:  # 跳过表头
            if len(r) < 3 or not r[0]:
                continue
            sym = r[0].strip().upper().replace(".", "/")
            if not re.fullmatch(r"[A-Z/]{1,6}", sym):
                continue
            rows.append({"sym": sym, "name": r[1].strip(), "sector": r[2].strip()})
        if not 495 <= len(rows) <= 510:
            raise RuntimeError("成分股行数异常: %d" % len(rows))
        for must in ("AAPL", "MSFT", "NVDA", "BRK/B"):
            if not any(x["sym"] == must for x in rows):
                raise RuntimeError("成分股缺少 %s，解析可能出错" % must)
        with open(path, "w") as f:
            json.dump({"fetched_at": int(time.time()), "rows": rows}, f, ensure_ascii=False)
        print("成分股名单: %d 只（Wikipedia 刷新）" % len(rows))
        return rows
    except Exception as e:
        if os.path.exists(path):
            print("⚠ 名单刷新失败，用缓存: %s" % e)
            with open(path) as f:
                return json.load(f)["rows"]
        raise


def fetch_snapshot(constituents):
    """全市场筛选器一次调用 → 成分股当日快照 {sym: {px, chg, mcap}}。"""
    obj = json.loads(http_get(
        "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&download=true",
        NASDAQ_HEADERS))
    rows = (obj.get("data") or {}).get("rows") or []
    if len(rows) < 3000:
        raise RuntimeError("筛选器行数异常: %d" % len(rows))
    want = {c["sym"] for c in constituents}
    snap = {}
    for r in rows:
        s = (r.get("symbol") or "").strip().upper()
        if s not in want:
            continue
        try:
            px = float((r.get("lastsale") or "").replace("$", "").replace(",", ""))
            mc = float(r.get("marketCap") or 0)
            ch = (r.get("pctchange") or "").replace("%", "").strip()
            chg = float(ch) if ch and ch not in ("--", "N/A") else 0.0
            if px <= 0:
                continue
            snap[s] = {"px": px, "chg": round(chg, 3), "mcap": mc}
        except ValueError:
            continue
    if len(snap) < 480:
        raise RuntimeError("快照匹配到的成分股过少: %d" % len(snap))
    print("快照: 匹配 %d/%d 只成分股" % (len(snap), len(want)))
    return snap


def save_snapshot(snap):
    """落盘当日快照并清理：近90天保留每日，更早仅保留周五。"""
    today = date.today().isoformat()
    with open(os.path.join(SNAP_DIR, today + ".json"), "w") as f:
        json.dump({"date": today,
                   "rows": [[s, v["px"], v["chg"], v["mcap"]] for s, v in sorted(snap.items())]},
                  f)
    cutoff = date.today() - timedelta(days=90)
    for fn in os.listdir(SNAP_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            d = date.fromisoformat(fn[:-5])
        except ValueError:
            continue
        if d < cutoff and d.weekday() != 4:
            os.remove(os.path.join(SNAP_DIR, fn))


def load_snapshots():
    out = []
    for fn in sorted(os.listdir(SNAP_DIR)):
        if fn.endswith(".json"):
            with open(os.path.join(SNAP_DIR, fn)) as f:
                out.append(json.load(f))
    return out


def backfill_batch(constituents, snap):
    """每次运行回填 25 只成分股的十年日线（市值从大到小），带状态持久化。"""
    state_path = os.path.join(DATA, "backfill.json")
    state = {"done": [], "failed": {}}
    if os.path.exists(state_path):
        with open(state_path) as f:
            state = json.load(f)
    done = set(state["done"])
    order = sorted(constituents, key=lambda c: -(snap.get(c["sym"], {}).get("mcap") or 0))
    pending = [c["sym"] for c in order if c["sym"] not in done
               and state["failed"].get(c["sym"], 0) < 3]
    batch = pending[:BACKFILL_PER_RUN]
    if not batch:
        print("回填: 已全部完成（%d 只）" % len(done))
        return len(done)
    today = date.today()
    frm = today - timedelta(days=3653)
    for sym in batch:
        # 带斜杠的代码（BRK/B 等）：先试 %2F，404 再试点号形式
        variants = [sym.replace("/", "%2F")] + ([sym.replace("/", ".")] if "/" in sym else [])
        try:
            rows = None
            for i, url_sym in enumerate(variants):
                url = ("https://api.nasdaq.com/api/quote/%s/historical?assetclass=stocks"
                       "&limit=9999&fromdate=%s&todate=%s"
                       % (url_sym, frm.isoformat(), today.isoformat()))
                time.sleep(1.5)
                try:
                    obj = json.loads(http_get(url, NASDAQ_HEADERS))
                    rows = ((obj.get("data") or {}).get("tradesTable") or {}).get("rows") or []
                    if rows:
                        break
                except RuntimeError:
                    if i == len(variants) - 1:
                        raise
            rows = rows or []
            seen = {}
            for r in rows:
                try:
                    raw = str(r.get("close") or "").replace("$", "").replace(",", "")
                    m, d_, y = r["date"].split("/")
                    seen["%s-%02d-%02d" % (y, int(m), int(d_))] = float(raw)
                except (ValueError, KeyError, AttributeError):
                    continue
            if len(seen) < 30:
                raise RuntimeError("数据过少(%d)" % len(seen))
            dates = sorted(seen)
            with open(os.path.join(HIST_DIR, sym.replace("/", "_") + ".json"), "w") as f:
                json.dump({"dates": dates, "closes": [seen[d] for d in dates]}, f)
            state["done"].append(sym)
        except Exception as e:
            state["failed"][sym] = state["failed"].get(sym, 0) + 1
            sys.stderr.write("回填 %s 失败: %s\n" % (sym, e))
    with open(state_path, "w") as f:
        json.dump(state, f)
    print("回填: 本轮 %d 只，累计 %d/%d" % (len(batch), len(state["done"]), len(constituents)))
    return len(state["done"])


def hist_52w_dist(sym, snap):
    """基于回填历史+后续快照的 52 周高点距离%；无历史时返回 None。"""
    path = os.path.join(HIST_DIR, sym.replace("/", "_") + ".json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        h = json.load(f)
    closes = h["closes"][-252:]
    px = snap[sym]["px"]
    hi = max(closes + [px])
    return round((px / hi - 1) * 100, 1)


def build_site_data(constituents, snap, backfilled):
    snaps = load_snapshots()
    total_mcap = sum(v["mcap"] for v in snap.values())
    # Top10 权重与当日贡献集中度
    by_mcap = sorted(snap.items(), key=lambda kv: -kv[1]["mcap"])
    top10 = by_mcap[:10]
    top10_w = sum(v["mcap"] for _, v in top10) / total_mcap * 100
    contrib_all = sum(v["mcap"] * v["chg"] for v in snap.values())
    contrib_t10 = sum(v["mcap"] * v["chg"] for _, v in top10)
    contrib_share = (contrib_t10 / contrib_all * 100) if abs(contrib_all) > 1e-9 else None
    adv = sum(1 for v in snap.values() if v["chg"] > 0)
    adv_pct = adv / len(snap) * 100
    # 板块统计
    sec_map = {}
    for c in constituents:
        if c["sym"] in snap:
            sec_map.setdefault(c["sector"], []).append(snap[c["sym"]])
    sectors = []
    for sec, vs in sec_map.items():
        chgs = sorted(x["chg"] for x in vs)
        sectors.append({"sector": sec, "n": len(vs),
                        "adv_pct": round(sum(1 for x in vs if x["chg"] > 0) / len(vs) * 100, 0),
                        "median_chg": round(chgs[len(chgs) // 2], 2),
                        "weight": round(sum(x["mcap"] for x in vs) / total_mcap * 100, 1)})
    sectors.sort(key=lambda s: -s["weight"])
    # 历史序列（来自快照累积）：Top10权重、上涨占比、累积净上涨（A/D线雏形）
    series = {"dates": [], "top10w": [], "advpct": [], "adline": []}
    ad_cum = 0
    for sp in snaps:
        rows = sp["rows"]
        tm = sum(r[3] for r in rows)
        if tm <= 0:
            continue
        t10 = sum(x[3] for x in sorted(rows, key=lambda r: -r[3])[:10]) / tm * 100
        a = sum(1 for r in rows if r[2] > 0)
        dcl = sum(1 for r in rows if r[2] < 0)
        ad_cum += a - dcl
        series["dates"].append(sp["date"])
        series["top10w"].append(round(t10, 2))
        series["advpct"].append(round(a / len(rows) * 100, 1))
        series["adline"].append(ad_cum)
    # 个股表格
    name_sec = {c["sym"]: c for c in constituents}
    table = []
    for s, v in by_mcap:
        c = name_sec[s]
        table.append({"s": s, "n": c["name"], "sec": c["sector"], "px": v["px"],
                      "chg": v["chg"], "mcap": round(v["mcap"] / 1e9, 1),
                      "w": round(v["mcap"] / total_mcap * 100, 2),
                      "d52": hist_52w_dist(s, snap)})
    out = {"asof": date.today().isoformat(),
           "updated_at": int(time.time()),
           "agg": {"n": len(snap), "adv_pct": round(adv_pct, 1),
                   "top10_w": round(top10_w, 1),
                   "contrib_share": round(contrib_share, 0) if contrib_share is not None else None,
                   "total_mcap_t": round(total_mcap / 1e12, 2),
                   "backfilled": backfilled, "total": len(constituents)},
           "sectors": sectors, "series": series, "rows": table}
    os.makedirs(os.path.join(PUBLIC, "data"), exist_ok=True)
    with open(os.path.join(PUBLIC, "data", "stocks.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("站点数据: %d 行, 上涨占比 %.0f%%, Top10 权重 %.1f%%, 快照天数 %d"
          % (len(table), adv_pct, top10_w, len(series["dates"])))


def main():
    for d in (SNAP_DIR, HIST_DIR):
        os.makedirs(d, exist_ok=True)
    cons = load_constituents()
    snap = fetch_snapshot(cons)
    save_snapshot(snap)
    backfilled = backfill_batch(cons, snap)
    build_site_data(cons, snap, backfilled)
    # MAG 面板：独立容错——期权/财报源故障不应阻断成分股面板的更新，
    # 失败时保留仓库中上一版 mags.json（页面自带更新时间，陈旧可见）
    try:
        import mags
        mags.run(cons, snap)
    except Exception as e:
        sys.stderr.write("⚠ MAG 面板生成失败（保留上一版数据）: %s\n" % e)


if __name__ == "__main__":
    main()
