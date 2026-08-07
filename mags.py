#!/usr/bin/env python3
"""MAG7+ 面板数据管道（挂载于 stocks 子站 /mags/ 路由）。

成员：每日按市值快照动态选取前 10 大公司（Alphabet 双类股合并计一家，
取市值较大的类作代表），覆盖"标普500 与纳指100 市值前十"的实际并集。

指标：
- 短线：5/20 日相对强度(vs QQQ)、均线排列(20/50/200)、距 20 日高与 52 周高
- 结构：组内 20 日平均两两相关性、当日离散度、Mag 合计权重、Mag等权/QQQ 比值(2年)
- 期权(Cboe 延迟链，官方 IV)：P/C 成交量比与持仓比、ATM IV30、
  IV 溢价(IV30−RV20)、IV 分位(随每日积累渐进，标注积累天数)
- 催化剂：下次财报日期与倒计时(Nasdaq/Zacks，未排期则留空)

期权指标逐日积累于 data/options/，P/C 10日均线与 IV 分位随时间自然成型。
"""
import json
import math
import os
import re
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build  # 复用 http_get / NASDAQ_HEADERS / 路径常量

OPT_DIR = os.path.join(build.DATA, "options")
TOP_N = 10


def _company_key(name):
    return name.split()[0].lower()


def pick_top(constituents, snap):
    rows = sorted(
        ((c, snap[c["sym"]]) for c in constituents if c["sym"] in snap),
        key=lambda cv: -cv[1]["mcap"])
    seen, out = set(), []
    for c, v in rows:
        k = _company_key(c["name"])
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
        if len(out) == TOP_N:
            break
    return out


def merged_closes(sym, snapshots):
    """回填历史 + 后续每日快照 → (dates, closes)。无历史文件返回 None。"""
    path = os.path.join(build.HIST_DIR, sym.replace("/", "_") + ".json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        h = json.load(f)
    dates, closes = list(h["dates"]), list(h["closes"])
    last = dates[-1]
    for sp in snapshots:
        if sp["date"] <= last:
            continue
        for r in sp["rows"]:
            if r[0] == sym:
                dates.append(sp["date"])
                closes.append(r[1])
                break
    return dates, closes


def fetch_qqq():
    """QQQ 十年日线（基准）。"""
    today = date.today()
    frm = today - timedelta(days=3653)
    url = ("https://api.nasdaq.com/api/quote/QQQ/historical?assetclass=etf"
           "&limit=9999&fromdate=%s&todate=%s" % (frm.isoformat(), today.isoformat()))
    obj = json.loads(build.http_get(url, build.NASDAQ_HEADERS))
    rows = ((obj.get("data") or {}).get("tradesTable") or {}).get("rows") or []
    seen = {}
    for r in rows:
        try:
            m, d_, y = r["date"].split("/")
            seen["%s-%02d-%02d" % (y, int(m), int(d_))] = float(
                str(r["close"]).replace("$", "").replace(",", ""))
        except (ValueError, KeyError, AttributeError):
            continue
    if len(seen) < 500:
        raise RuntimeError("QQQ 历史数据异常(%d)" % len(seen))
    ds = sorted(seen)
    return ds, [seen[d] for d in ds]


_OCC = re.compile(r"^[A-Z.]{1,6}(\d{6})([CP])(\d{8})$")


def fetch_opt_metrics(sym):
    """Cboe 延迟期权链 → P/C 比、ATM IV30。官方 iv/delta 字段，无需自行反推。"""
    url = "https://cdn.cboe.com/api/global/delayed_quotes/options/%s.json" % sym
    d = json.loads(build.http_get(url, timeout=50))
    data = d.get("data") or {}
    opts = data.get("options") or []
    if len(opts) < 50:
        raise RuntimeError("%s 期权链过短(%d)" % (sym, len(opts)))
    today = date.today()
    pv = cv = poi = coi = 0.0
    per_exp = {}
    for o in opts:
        m = _OCC.match(o.get("option") or "")
        if not m:
            continue
        yymmdd, typ = m.group(1), m.group(2)
        exp = date(2000 + int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6]))
        vol = o.get("volume") or 0
        oi = o.get("open_interest") or 0
        if typ == "P":
            pv += vol
            poi += oi
        else:
            cv += vol
            coi += oi
        iv, delta = o.get("iv"), o.get("delta")
        if iv and delta is not None and 0.35 <= abs(delta) <= 0.65 and iv > 0.01:
            per_exp.setdefault(exp, []).append(iv)
    # ATM IV 期限点 →（线性时间插值）30 天
    pts = sorted((max(1, (e - today).days), sum(l) / len(l))
                 for e, l in per_exp.items()
                 if len(l) >= 2 and 5 <= (e - today).days <= 120)
    iv30 = None
    if pts:
        lo = [p for p in pts if p[0] <= 30]
        hi = [p for p in pts if p[0] > 30]
        if lo and hi:
            (d1, v1), (d2, v2) = lo[-1], hi[0]
            iv30 = v1 + (v2 - v1) * (30 - d1) / (d2 - d1)
        else:
            iv30 = (lo or hi)[0 if hi else -1][1]
    return {"pcv": round(pv / cv, 3) if cv > 0 else None,
            "pcoi": round(poi / coi, 3) if coi > 0 else None,
            "iv30": round(iv30 * 100, 1) if iv30 else None,
            "opt_vol": int(pv + cv)}


def fetch_earnings(sym):
    """下次财报日期与盘前/盘后；未排期返回 None。"""
    try:
        obj = json.loads(build.http_get(
            "https://api.nasdaq.com/api/analyst/%s/earnings-date" % sym,
            build.NASDAQ_HEADERS, timeout=25))
        txt = ((obj.get("data") or {}).get("reportText")) or ""
        m = re.search(r"on\s+(\d{2}/\d{2}/\d{4})", txt)
        if not m:
            return None
        mm, dd, yy = m.group(1).split("/")
        when = "AMC" if "after market" in txt else ("BMO" if "before market" in txt else "")
        return {"date": "%s-%s-%s" % (yy, mm, dd), "when": when}
    except Exception:
        return None


def _returns(closes, n):
    return [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - n, len(closes))]


def _corr(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da > 0 and db > 0 else None


def save_opt_day(metrics_by_sym):
    os.makedirs(OPT_DIR, exist_ok=True)
    with open(os.path.join(OPT_DIR, date.today().isoformat() + ".json"), "w") as f:
        json.dump(metrics_by_sym, f)


def load_opt_history():
    out = []
    if not os.path.isdir(OPT_DIR):
        return out
    for fn in sorted(os.listdir(OPT_DIR)):
        if fn.endswith(".json"):
            with open(os.path.join(OPT_DIR, fn)) as f:
                out.append((fn[:-5], json.load(f)))
    return out


def run(constituents, snap):
    snapshots = build.load_snapshots()
    top = pick_top(constituents, snap)
    syms = [c["sym"] for c in top]
    print("MAG 成员:", ", ".join(syms))
    qd, qc = fetch_qqq()

    series = {}
    for s in syms:
        mc = merged_closes(s, snapshots)
        if mc is None or len(mc[1]) < 260:
            raise RuntimeError("%s 历史不足，MAG 面板需先完成回填" % s)
        series[s] = mc

    # 期权与财报（逐只，限速）
    opt, earn = {}, {}
    for s in syms:
        time.sleep(1.0)
        try:
            opt[s] = fetch_opt_metrics(s)
        except Exception as e:
            sys.stderr.write("期权 %s 失败: %s\n" % (s, e))
            opt[s] = {}
        time.sleep(0.8)
        earn[s] = fetch_earnings(s)
    save_opt_day({s: opt[s] for s in syms if opt.get(s)})
    opt_hist = load_opt_history()

    today = date.today()
    q_r5 = qc[-1] / qc[-6] - 1
    q_r20 = qc[-1] / qc[-21] - 1
    rows, rets20 = [], {}
    for c in top:
        s = c["sym"]
        ds, cs = series[s]
        px = snap[s]["px"]
        r5 = cs[-1] / cs[-6] - 1
        r20 = cs[-1] / cs[-21] - 1
        rets20[s] = _returns(cs, 20)
        ma = {n: sum(cs[-n:]) / n for n in (20, 50, 200)}
        align = ("bull" if px > ma[20] > ma[50] > ma[200]
                 else "bear" if px < ma[20] < ma[50] < ma[200] else "mixed")
        rv20 = None
        rets = _returns(cs, 21)[1:]
        mu = sum(rets) / len(rets)
        rv20 = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252) * 100
        o = opt.get(s) or {}
        iv30 = o.get("iv30")
        # IV 分位与 P/C 10日均线（基于积累）
        iv_series = [h[s]["iv30"] for _, h in opt_hist if s in h and h[s].get("iv30")]
        ivrank = (round(sum(1 for v in iv_series if v <= iv30) / len(iv_series) * 100)
                  if iv30 and len(iv_series) >= 5 else None)
        pcv_series = [h[s]["pcv"] for _, h in opt_hist if s in h and h[s].get("pcv")][-10:]
        pcv10 = round(sum(pcv_series) / len(pcv_series), 2) if len(pcv_series) >= 3 else None
        e = earn.get(s)
        edays = None
        if e:
            try:
                edays = (date.fromisoformat(e["date"]) - today).days
            except ValueError:
                e = None
        rows.append({
            "s": s, "n": c["name"], "px": px, "chg": snap[s]["chg"],
            "mcap": round(snap[s]["mcap"] / 1e9, 0),
            "rs5": round((r5 - q_r5) * 100, 1), "rs20": round((r20 - q_r20) * 100, 1),
            "ma20": round((px / ma[20] - 1) * 100, 1),
            "ma50": round((px / ma[50] - 1) * 100, 1),
            "ma200": round((px / ma[200] - 1) * 100, 1), "align": align,
            "d20h": round((px / max(cs[-20:] + [px]) - 1) * 100, 1),
            "d52": round((px / max(cs[-252:] + [px]) - 1) * 100, 1),
            "iv30": iv30, "rv20": round(rv20, 1),
            "ivprem": round(iv30 - rv20, 1) if iv30 else None,
            "ivrank": ivrank, "ivdays": len(iv_series),
            "pcv": o.get("pcv"), "pcv10": pcv10, "pcoi": o.get("pcoi"),
            "earn": ({"date": e["date"], "when": e["when"], "days": edays} if e else None),
        })

    # 组内平均两两相关（20日）
    pairs, cors = 0, 0.0
    ss = list(rets20)
    for i in range(len(ss)):
        for j in range(i + 1, len(ss)):
            cr = _corr(rets20[ss[i]], rets20[ss[j]])
            if cr is not None:
                cors += cr
                pairs += 1
    avg_corr = round(cors / pairs, 2) if pairs else None
    chgs = [snap[s]["chg"] for s in syms]
    dispersion = round(max(chgs) - min(chgs), 2)
    total_mcap = sum(v["mcap"] for v in snap.values())
    mag_share = round(sum(snap[s]["mcap"] for s in syms) / total_mcap * 100, 1)
    # Mag等权 / QQQ 比值（近两年，隔日抽样）
    common = set(qd)
    for s in syms:
        common &= set(series[s][0])
    common = sorted(common)[-504:]
    qmap = dict(zip(qd, qc))
    smaps = {s: dict(zip(*series[s])) for s in syms}
    ratio_d, ratio_v = [], []
    if len(common) > 60:
        base_q = qmap[common[0]]
        base_s = {s: smaps[s][common[0]] for s in syms}
        for i, dt in enumerate(common):
            if i % 2 and i != len(common) - 1:
                continue
            ew = sum(smaps[s][dt] / base_s[s] for s in syms) / len(syms)
            ratio_d.append(dt)
            ratio_v.append(round(ew / (qmap[dt] / base_q) * 100, 2))
    nearest = min((r["earn"]["days"], r["s"]) for r in rows
                  if r["earn"] and r["earn"]["days"] is not None and r["earn"]["days"] >= 0) \
        if any(r["earn"] and (r["earn"]["days"] or -1) >= 0 for r in rows) else None
    out = {"asof": today.isoformat(), "updated_at": int(time.time()),
           "regime": {"avg_corr": avg_corr, "dispersion": dispersion,
                      "mag_share": mag_share,
                      "next_earn": ({"sym": nearest[1], "days": nearest[0]} if nearest else None)},
           "ratio": {"dates": ratio_d, "vals": ratio_v},
           "rows": rows}
    os.makedirs(os.path.join(build.PUBLIC, "data"), exist_ok=True)
    with open(os.path.join(build.PUBLIC, "data", "mags.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("MAG 面板: %d 只, 平均相关 %s, 离散度 %s%%, Mag份额 %s%%"
          % (len(rows), avg_corr, dispersion, mag_share))


if __name__ == "__main__":
    cons = build.load_constituents()
    snap = build.fetch_snapshot(cons)
    run(cons, snap)
