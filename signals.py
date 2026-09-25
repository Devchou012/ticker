"""判斷層：乖離、年區間、外資、過期、多空比、買點、弱勢、型態、異動排行，以及一列的完整判斷 row()。
回傳的是語意，不是顏色，終端機（ticker.py）與網頁版各自配色：
  up／down／flat 漲跌方向、hot 追高、cold 回檔、volhot 爆量、vollow 量縮、dim 中性、zone 弱勢提醒
量比在資料層（market.volume_ratio），因為要看各市場的交易時段。"""
import time

import market as mk
from market import *  # noqa: F401,F403  常數、報價、日線這些容器都從資料層拿

MARQUEE_TOP = 10                  # 異動排行取前幾名
DEV_HIGH, DEV_LOW = 0.08, -0.08   # 乖離率超過 ±8% 就標追高／回檔
POS_HOT, POS_COLD = 90, 10        # 52 週位置的高低帶，超過就上色
STALE_SEC = 90  # 盤中超過這麼久沒拿到新報價就整列變暗；Yahoo 一輪 15 秒，等於連掉六輪
PAT_MAX = 2     # 型態說明最多列幾條，多了 K 線就被擠扁
# 弱勢提醒（橘燈）：現價是半年線的 95%～99%，也就是剛跌破半年線 1%～5%。
# 原本是使用者自己的買點，回測（backtest.py，2021-09～2026-09、211 檔）這個位置買進 60 天後平均落後大盤 1.3%～8.7%，
# 兩段期間都顯著（t 值 -8 以下），所以改成警訊：恆亮不閃，跟閃爍的紅燈買點分開
ZONE_LO, ZONE_HI = 0.95, 0.99
NEAR_MA = 0.02  # 低點離月線／季線 2% 內算「拉回到均線附近」


def tone(x):
    """正負 → up／down／flat。"""
    return "up" if x > 0 else "down" if x < 0 else "flat"


def stale(sym):
    """盤中超過 STALE_SEC 沒拿到新報價。收盤的市場本來就不會更新，利率一小時抓一次，都不算。"""
    # ponytail: 開盤那一刻上一輪還沒跑到，美股最多會暗 15 秒；要消掉就看開盤後經過的時間
    return (suffix_of(sym) in SESSIONS and sym not in RATES and mk.session_open(sym)
            and time.time() - quote_at.get(sym, 0) > STALE_SEC)


def is_tw_stock(sym):
    """台股個股（含 ETF）：MIS 有五檔，可以算內外盤。類股指數不算。"""
    return bool(sym) and sym.endswith((".TW", ".TWO")) and sym not in SECTOR_SET


def shows_flow(sym):
    """台股盤中才放多空比；休盤、收盤切回今日區間條。國定假日看 MIS 回的交易日：不是今天就是沒開盤。"""
    f = flow.get(sym)
    return is_tw_stock(sym) and mk.session_open(sym) and (not f or f[0] == time.strftime("%Y%m%d"))


def flow_ratio(sym):
    """外盤佔內外盤的比例。富果有今天的真實值就用它，沒有就用 MIS 推估。還沒累積到量回 None。"""
    f, ex = flow.get(sym), flow_exact.get(sym)
    if ex and ex[1] + ex[2] and (not f or ex[0] >= f[0]):
        return ex[1] / (ex[1] + ex[2])
    if f and f[1] + f[2]:
        return f[1] / (f[1] + f[2])
    return None


def bias(sym, price):
    """乖離率：現價離 20 日均線多遠。追高 hot、回檔 cold，中間帶 dim。"""
    d = daily.get(sym)
    if not d or not d[0]:
        return "", ""
    dev = (price - d[0]) / d[0]
    return f"{dev:+.1%}", "hot" if dev >= DEV_HIGH else "cold" if dev <= DEV_LOW else "dim"


def pos52(sym, price):
    """52 週位置：0% 貼著一年低點，100% 創一年新高。靠近高點 up、靠近低點 down。"""
    d = daily.get(sym)
    if not d or d[3] is None or d[3] <= d[2]:
        return "", ""
    pct = min(max(round((price - d[2]) / (d[3] - d[2]) * 100), 0), 100)
    return f"{pct}%", "up" if pct >= POS_HOT else "down" if pct <= POS_COLD else "dim"


def chip_streak(sym):
    """外資連續買超（正，up）或連續賣超（負，down）天數。T86 只有上市，上櫃與其他市場回空字串。"""
    if not sym.endswith(".TW") or sym in SECTOR_SET:
        return "", ""
    code, sign, run = sym[:-3], 0, 0
    for day in sorted(chips, reverse=True):
        net = chips[day]
        if not net:
            continue  # 放假日沒有資料，不算中斷
        v = net.get(code)
        if not v:
            break
        way = 1 if v > 0 else -1
        if sign and way != sign:
            break
        sign = way
        run += 1
    return (f"{sign * run:+d}日", tone(sign)) if run else ("", "")


def buy_detail(sym):
    """買點的每個條件。用含今天即時那根的 K 線，盤中就會亮；量縮只看已收盤的量。資料不夠回 None。
    紅燈（買點）：半年線上揚 → 季線上揚 → 拉回月線或季線附近又收回 → 量縮 → 止跌 K 棒（長下影、多頭吞噬）或站回月線。
    「半年線上揚」是回測後加的（backtest.py 的 F3）：前四年、最近一年 60 天後都比原版好，但 t 值 1.3～1.5，證據不強。
    held、trigger 放的是成立的那一項名稱，K 線面板直接拿來當說明。"""
    data = candles(sym) or []
    if len(data) < 65:
        return None
    closes = [b[3] for b in data]
    ma = lambda k, back=0: sum(closes[len(closes) - back - k:len(closes) - back]) / k
    gap = closes[-1] / ma(120) - 1 if len(closes) >= 120 else None  # 現價離半年線幾 %（負的是在下方）
    o, h, l, c = data[-1]
    po, pc = data[-2][0], data[-2][3]
    m20, m60 = ma(20), ma(60)
    v = vols.get(sym) or []
    body = abs(c - o)
    return {
        "zone": gap is not None and ZONE_LO - 1 <= gap <= ZONE_HI - 1,
        "gap": gap,
        "rising": m60 > ma(60, 5),  # 季線比一週前高
        "ma120_up": len(closes) >= 140 and ma(120) > ma(120, 20),  # 半年線比 20 個交易日前高；資料不夠就不亮
        "held": next((n for n, m in (("月線", m20), ("季線", m60))
                      if l <= m * (1 + NEAR_MA) and c >= m), ""),  # 碰到均線附近、收盤站得住
        "quiet": len(v) >= 20 and sum(v[-3:]) / 3 < sum(v[-20:]) / 20,  # 最近三天的量低於月均量
        "trigger": ("長下影" if min(o, c) - l >= body * 2 and h - max(o, c) <= body
                    else "多頭吞噬" if pc < po and c > o and o <= pc and c >= po
                    else "站回月線" if pc < ma(20, 1) and c >= m20  # 昨天在月線下，今天收盤站回
                    else ""),
    }


def buy_signal(sym):
    """回傳 (弱勢提醒：剛跌破半年線, 買點：拉回流程成立)。"""
    d = buy_detail(sym)
    if not d:
        return False, False
    return d["zone"], bool(d["ma120_up"] and d["rising"] and d["held"] and d["quiet"] and d["trigger"])


def buy_reason(sym):
    """K 線面板那一行：燈為什麼亮，(標籤, 說明, 語意 up／zone)。沒亮回 None。"""
    d = buy_detail(sym)
    zone, flow_ok = buy_signal(sym)
    weak = f"跌破半年線 {abs(d['gap']):.1%}，回測顯示之後常落後大盤" if zone else ""
    if flow_ok:
        note = f"半年線上揚・季線上揚・回測{d['held']}・量縮・{d['trigger']}"
        return "買點", note + (f"｜{weak}" if weak else ""), "up"
    return ("弱勢", weak, "zone") if zone else None


def patterns(sym):
    """用已收盤的日 K 判型態，回傳 [(名稱, 白話說明, 語意 up／down／flat)]，少見的先列：K 棒、突破／回測，最後才是均線排列。"""
    data = bars.get(sym) or []
    if len(data) < 21:
        return []
    closes = [b[3] for b in data]
    ma = lambda k, end=None: sum(closes[:end][-k:]) / k
    trend, level, candle = [], [], []
    if len(closes) >= 60:
        m5, m20, m60 = ma(5), ma(20), ma(60)
        if m5 > m20 > m60:
            trend.append(("多頭排列", "短中長均線由上往下排，趨勢偏多", "up"))
        elif m5 < m20 < m60:
            trend.append(("空頭排列", "短中長均線由下往上排，趨勢偏空", "down"))
    o, h, l, c = data[-1]
    po, _, _, pc = data[-2]
    prior = data[-21:-1]
    m20, m20_prev = ma(20), ma(20, -1)
    if c > max(b[1] for b in prior):
        level.append(("突破月高", "收盤站上近 20 日最高價，有機會續攻", "up"))
    elif c < min(b[2] for b in prior):
        level.append(("跌破月低", "收盤跌破近 20 日最低價，留意續弱", "down"))
    elif l <= m20 * 1.01 and c > m20 and m20 >= m20_prev:
        level.append(("回測月線", "碰到上揚的 20 日線又收回，月線有撐", "up"))
    body, rng = abs(c - o), (h - l) or 1
    if pc < po and c > o and o <= pc and c >= po:
        candle.append(("多頭吞噬", "紅 K 整根包住前一根黑 K，轉強訊號", "up"))
    elif pc > po and c < o and o >= pc and c <= po:
        candle.append(("空頭吞噬", "黑 K 整根包住前一根紅 K，轉弱訊號", "down"))
    elif body <= rng * 0.1:
        candle.append(("十字線", "開收幾乎同價，多空拉鋸，留意變盤", "flat"))
    elif min(o, c) - l >= body * 2 and h - max(o, c) <= body:
        candle.append(("長下影線", "盤中殺低被買回，下方有承接", "up"))
    elif h - max(o, c) >= body * 2 and min(o, c) - l <= body:
        candle.append(("長上影線", "盤中衝高被賣回，上方有賣壓", "down"))
    return (candle + level + trend)[:PAT_MAX]


def movers(n=MARQUEE_TOP):
    """全部頁面的標的裡，依漲跌幅絕對值取前 n 名：[(|幅度|, 名稱, 幅度, 是否爆量)]。跨頁重複的代號只算一次。
    盤中的市場優先：白天是台股、日韓，晚上是美股。收盤的市場價格整天不動，
    掛在榜上只是佔位子；全部收盤時（半夜、週末）才退回來用它們，免得整條空白。
    央行利率排除：它的漲跌是跟上次決議比，一放進來就永遠佔著榜首。"""
    seen, live, rest = set(), [], []
    for items in mk.PAGES.values():
        for sym, name in items:
            q = quotes.get(sym)
            if sym in seen or sym in RATES or sym in SECTOR_SET or not q or not q[1]:
                continue
            seen.add(sym)
            price, prev, *extra = q
            vr = volume_ratio(sym, *extra[:2])[1] if extra else ""
            row_ = (abs(price - prev) / prev, name, (price - prev) / prev, vr == "volhot")
            (live if mk.session_open(sym) else rest).append(row_)
    live.sort(reverse=True)
    rest.sort(reverse=True)
    return (live or rest)[:n]


def row(sym, name, hold=False):
    """一列的完整判斷，終端機表格與網頁表格共用。沒有報價回 {"sym", "name", "empty": True}。
    hold=True（庫存頁）時漲跌改成今日損益（漲跌×股數），幅度改成總報酬（對成本），方向也照總報酬。"""
    q = quotes.get(sym)
    if not q or not q[1]:
        return {"sym": sym, "name": name, "empty": True}
    price, prev, *extra = q
    chg = price - prev
    vr, vr_tone = volume_ratio(sym, *extra[:2]) if extra else ("", "")
    dev, dev_tone = bias(sym, price)
    pos, pos_tone = pos52(sym, price)
    chip, chip_tone = chip_streak(sym)
    pat, pat_tone = next(((p[0], p[2]) for p in patterns(sym)), ("", ""))  # 表格只放最重要那一條，說明看右邊面板
    if hold:
        shares, cost = mk.HOLD[sym]
        ret = (price - cost) / cost
        chg_txt, pct_txt, direction = f"{chg * shares:+,.0f}", f"{ret:+.2%}", tone(ret)
    else:
        chg_txt, pct_txt, direction = f"{chg:+,.2f}", f"{chg / prev:+.2%}", tone(chg)
    low, high = (extra[2], extra[3]) if len(extra) >= 4 else (None, None)
    return {"sym": sym, "name": name, "price": price, "prev": prev, "chg": chg,
            "price_txt": f"{price:,.2f}", "chg_txt": chg_txt, "pct_txt": pct_txt, "tone": direction,
            "vr": vr, "vr_tone": vr_tone, "bias": dev, "bias_tone": dev_tone, "pos": pos, "pos_tone": pos_tone,
            "chip": chip, "chip_tone": chip_tone, "pat": pat, "pat_tone": pat_tone,
            "low": low, "high": high, "sector": sym in SECTOR_SET, "stale": stale(sym)}
