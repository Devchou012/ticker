"""買點燈回測：用過去幾年的日線，看亮燈後 5／20／60 個交易日的報酬，跟同一批股票「隨便哪天買」比。

直接呼叫面板的 buy_detail／buy_signal，測的就是面板實際在用的規則，不另外重寫一份。
  - 樣本：上市成交值前 TOP 名（不含 ETF）＋清單裡的台股。只用自己的觀察清單會有事後選股的偏差
  - 進場：亮燈隔天開盤買（燈號用當天收盤算，當天收盤買等於偷看）；出場：第 h 個交易日收盤
  - 價格：還原權息（auto_adjust），除息不會被算成虧損
  - 沒扣手續費與證交稅（來回約 0.585%），看結果時自己扣

用法：python backtest.py            （第一次會下載日線存成 backtest_cache.json，之後直接用）
      python backtest.py --refresh  （重新下載）
"""
import importlib.util
import json
import statistics as st
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("t", HERE / "ticker.py")
m = importlib.util.module_from_spec(spec)
sys.modules["t"] = m
spec.loader.exec_module(m)

CACHE = HERE / "backtest_cache.json"  # 不進 git
RESULT = HERE / "backtest_result.json"  # 給網頁說明頁用
TOP, YEARS, HORIZONS, WINDOW = 200, 5, (5, 20, 60), 145  # WINDOW：buy_detail 最多往回看 140 根（半年線斜率），給 145
FEE = 0.00585  # 台股來回手續費 0.1425%×2 ＋ 證交稅 0.3%，只在報表上列出，不從數字裡扣


def universe():
    """上市成交值前 TOP 名的個股（代號 4 碼、不是 0 開頭的 ETF）＋清單裡的台股。"""
    rows = json.loads(m.urllib.request.urlopen(m.urllib.request.Request(
        "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read())
    top = sorted((r for r in rows if len(r["Code"]) == 4 and not r["Code"].startswith("0")),
                 key=lambda r: -float(r.get("TradeValue") or 0))[:TOP]
    mine = {s for items in m.PAGES.values() for s, _ in items if s and s.endswith((".TW", ".TWO")) and s not in m.SECTOR_SET}
    return sorted({r["Code"] + ".TW" for r in top} | mine)


def download(syms):
    """分批下載還原權息日線，批與批之間停一下，免得 Yahoo 限流。"""
    out = {}
    for k in range(0, len(syms), 25):
        batch = syms[k:k + 25]
        for attempt in range(3):
            try:
                df = m.yf.download(batch, period=f"{YEARS}y", interval="1d", group_by="ticker",
                                   auto_adjust=True, threads=True, progress=False)
                break
            except Exception:
                time.sleep(20 * (attempt + 1))  # 被限流就等久一點再試
        else:
            continue
        for sym in batch:
            try:
                d = df[sym][["Open", "High", "Low", "Close", "Volume"]].dropna()
            except (KeyError, TypeError):
                continue
            if len(d) > WINDOW + max(HORIZONS):
                out[sym] = {"d": [x.strftime("%Y-%m-%d") for x in d.index],
                            "ohlc": [[round(float(x), 4) for x in row[:4]] for row in d.values],
                            "v": [float(row[4]) for row in d.values]}
        print(f"  下載 {min(k + 25, len(syms))}/{len(syms)}，可用 {len(out)} 檔", flush=True)
        time.sleep(3)
    return out


def signals(sym, data):
    """每個交易日跑一次面板的判斷，回傳 [(第幾天, 新橘燈, 紅燈, 舊橘燈)]。只餵最近 WINDOW 根，跟面板看到的一樣長。"""
    out, ohlc, vols = [], data["ohlc"], data["v"]
    for i in range(WINDOW, len(ohlc)):
        m.bars[sym], m.vols[sym] = ohlc[i - WINDOW + 1:i + 1], vols[i - WINDOW + 1:i + 1]
        d = m.buy_detail(sym)
        if not d:
            continue
        zone, flow = m.buy_signal(sym)
        old = d["gap"] is not None and 0.05 <= d["gap"] <= 0.10  # 改規則前：半年線上方 5%～10%
        out.append((i, zone, flow, old))
    return out


def summarize(rets):
    if not rets:
        return None
    return {"n": len(rets), "mean": st.mean(rets), "median": st.median(rets), "win": sum(r > 0 for r in rets) / len(rets)}


def main():
    m.live_bar.clear()
    if CACHE.exists() and "--refresh" not in sys.argv:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
    else:
        syms = universe()
        print(f"下載 {len(syms)} 檔、{YEARS} 年日線…")
        data = download(syms)
        CACHE.write_text(json.dumps(data), encoding="utf-8")
    print(f"回測 {len(data)} 檔，期間 {min(v['d'][0] for v in data.values())} ～ {max(v['d'][-1] for v in data.values())}")

    groups = {k: {h: [] for h in HORIZONS} for k in ("all", "zone", "flow", "both", "old")}
    events = {k: 0 for k in groups}
    t0 = time.time()
    for n, (sym, d) in enumerate(data.items(), 1):
        ohlc = d["ohlc"]
        prev = {k: False for k in groups}
        for i, zone, flow, old in signals(sym, d):
            flags = {"all": True, "zone": zone, "flow": flow, "both": zone and flow, "old": old}
            for k, on in flags.items():
                if on and not prev[k]:
                    events[k] += 1  # 連續亮好幾天只算一次「事件」
                prev[k] = on
                if not on:
                    continue
                for h in HORIZONS:
                    if i + h < len(ohlc):
                        groups[k][h].append(ohlc[i + h][3] / ohlc[i + 1][0] - 1)  # 隔天開盤買、第 h 天收盤賣
        if n % 20 == 0:
            print(f"  {n}/{len(data)}（{time.time() - t0:.0f} 秒）", flush=True)

    names = {"all": "隨便哪天買（基準）", "zone": "橘燈：弱勢提醒（半年線下 1～5%）", "old": "舊橘燈：半年線上 5～10%",
             "flow": "紅燈：買點（F3，含半年線上揚）", "both": "兩顆都亮"}
    result = {"stocks": len(data), "from": min(v["d"][0] for v in data.values()), "to": max(v["d"][-1] for v in data.values()),
              "fee": FEE, "groups": {}}
    print(f"\n{'':24}" + "".join(f"{f'{h} 日後':>28}" for h in HORIZONS))
    print(f"{'':24}" + "".join(f"{'平均':>9}{'勝率':>8}{'超額':>9}" + " " * 2 for _ in HORIZONS) + "   亮燈天數／事件")
    for k in ("all", "zone", "old", "flow", "both"):
        row, res = f"{names[k]:<22}", {}
        for h in HORIZONS:
            s = summarize(groups[k][h])
            base = summarize(groups["all"][h])
            if not s:
                row += f"{'—':>26}  "
                continue
            s["excess"] = s["mean"] - base["mean"]
            res[h] = s
            row += f"{s['mean']:>+9.2%}{s['win']:>8.0%}{s['excess']:>+9.2%}  "
        result["groups"][k] = {"name": names[k], "days": len(groups[k][HORIZONS[0]]), "events": events[k], "h": res}
        print(row + f"   {len(groups[k][HORIZONS[0]])}／{events[k]}")
    print(f"\n超額＝平均報酬減基準。沒扣交易成本（來回約 {FEE:.2%}）。")
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


SPLIT = "2025-09-25"  # 之前是訓練期（找規則），之後是驗證期（看規則在沒見過的資料上還有沒有效）


def features(sym, data, twii):
    """每個交易日：面板的判斷結果，加上改良版要用的幾個條件（半年線斜率、站上半年線、大盤在季線上）。"""
    out, ohlc, vols, dates = [], data["ohlc"], data["v"], data["d"]
    closes = [b[3] for b in ohlc]
    for i in range(WINDOW, len(ohlc)):
        m.bars[sym], m.vols[sym] = ohlc[i - WINDOW + 1:i + 1], vols[i - WINDOW + 1:i + 1]
        d = m.buy_detail(sym)
        if not d or d["gap"] is None:
            continue
        zone, flow = m.buy_signal(sym)
        ma120 = sum(closes[i - 119:i + 1]) / 120
        ma120_prev = sum(closes[i - 139:i - 19]) / 120  # 20 個交易日前的半年線
        out.append({"i": i, "date": dates[i], "gap": d["gap"], "zone": zone, "flow": flow,
                    "ma120_up": ma120 > ma120_prev, "above120": closes[i] > ma120, "market": twii.get(dates[i])})
    return out


VARIANTS = {  # 名稱、條件；條件拿 features() 的一列
    "Z0 半年線下 1～5%（弱勢提醒）": lambda f: f["zone"],
    "Z1 半年線上 5～10%（舊）": lambda f: 0.05 <= f["gap"] <= 0.10,
    "Z2 Z0＋半年線上揚": lambda f: f["zone"] and f["ma120_up"],
    "Z3 半年線 ±3%＋上揚": lambda f: -0.03 <= f["gap"] <= 0.03 and f["ma120_up"],
    "Z4 Z3＋大盤在季線上": lambda f: -0.03 <= f["gap"] <= 0.03 and f["ma120_up"] and f["market"],
    "F0 紅燈（原版）": lambda f: f["flow"],
    "F1 F0＋大盤在季線上": lambda f: f["flow"] and f["market"],
    "F2 F0＋站上半年線": lambda f: f["flow"] and f["above120"],
    "F3 F0＋半年線上揚": lambda f: f["flow"] and f["ma120_up"],
}


def twii_filter():
    """加權指數收盤在季線（60 日均線）之上的日子。"""
    df = m.yf.download("^TWII", period=f"{YEARS}y", interval="1d", auto_adjust=True, progress=False)
    closes = [float(x) for x in df["Close"].values.ravel()]
    dates = [x.strftime("%Y-%m-%d") for x in df.index]
    return {dates[i]: closes[i] > sum(closes[i - 59:i + 1]) / 60 for i in range(59, len(closes))}


def variants():
    m.live_bar.clear()
    data = json.loads(CACHE.read_text(encoding="utf-8"))
    twii = twii_filter()
    rows = []  # (期間, 日期, 變體名 or None=基準, h, 報酬)
    for sym, d in data.items():
        ohlc = d["ohlc"]
        for f in features(sym, d, twii):
            if f["market"] is None:
                continue  # 大盤那天沒資料（例如開盤日不同步），整天略過，各版本才公平
            period = "train" if f["date"] < SPLIT else "test"
            hits = [None] + [k for k, rule in VARIANTS.items() if rule(f)]
            for h in (20, 60):
                i = f["i"]
                if i + h < len(ohlc):
                    r = ohlc[i + h][3] / ohlc[i + 1][0] - 1
                    for k in hits:
                        rows.append((period, f["date"], k, h, r))
    report = {}
    for period in ("train", "test"):
        for h in (20, 60):
            base_by_day = {}
            for p, day, k, hh, r in rows:
                if p == period and hh == h and k is None:
                    base_by_day.setdefault(day, []).append(r)
            base_day = {day: st.mean(v) for day, v in base_by_day.items()}
            base = st.mean(r for v in base_by_day.values() for r in v)
            for name in VARIANTS:
                by_day = {}
                for p, day, k, hh, r in rows:
                    if p == period and hh == h and k == name:
                        by_day.setdefault(day, []).append(r)
                rets = [r for v in by_day.values() for r in v]
                if len(by_day) < 5:
                    report[(name, period, h)] = None
                    continue
                # 日期分組：同一天很多檔一起亮不是獨立事件，先把每天平均、減掉那天的基準，再看整體顯不顯著
                ex = [st.mean(v) - base_day[day] for day, v in by_day.items()]
                tval = st.mean(ex) / (st.stdev(ex) / len(ex) ** 0.5) if len(ex) > 2 and st.stdev(ex) else 0.0
                report[(name, period, h)] = {"n": len(rets), "days": len(by_day), "mean": st.mean(rets),
                                             "excess": st.mean(rets) - base, "win": sum(r > 0 for r in rets) / len(rets), "t": tval}
    print(f"訓練期 ～{SPLIT}，驗證期 {SPLIT}～。超額＝減掉同期基準；t＝日期分組 t 值（>2 才算可信）。未扣成本 {FEE:.2%}。\n")
    for h in (20, 60):
        print(f"── {h} 日後")
        print(f"{'':26}{'訓練期 超額':>12}{'勝率':>7}{'t':>6}{'次數':>8}  │{'驗證期 超額':>12}{'勝率':>7}{'t':>6}{'次數':>7}")
        for name in VARIANTS:
            cells = []
            for period in ("train", "test"):
                s = report[(name, period, h)]
                cells.append(f"{s['excess']:>+12.2%}{s['win']:>7.0%}{s['t']:>6.1f}{s['n']:>8}" if s else f"{'（太少）':>33}")
            print(f"{name:<24}{cells[0]}  │{cells[1]}")
        print()
    RESULT.with_name("backtest_variants.json").write_text(json.dumps(
        {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in report.items()}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    variants() if "--variants" in sys.argv else main()
