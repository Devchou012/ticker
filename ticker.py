"""終端機行情面板：頂部大盤固定，下方個股每 AUTO_SEC 秒自動翻頁；←/→ 或數字鍵手動切頁，q 離開。資料源 Yahoo Finance（免費，部分市場有延遲）。"""
import io
import re
import json
import msvcrt
import os
import socket
import ssl
import sys
import threading
import unicodedata
import urllib.request
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yfinance as yf
from rich import box
from rich.console import Console, Group
from rich.table import Table
from rich.cells import cell_len
from rich.text import Text

ANSI = re.compile(r"\x1b\[[0-9;]*m")

INDICES = [("^TWII", "加權"), ("^GSPC", "S&P"), ("^IXIC", "Nasdaq"), ("^DJI", "道瓊"),
           ("^SOX", "費半"), ("^N225", "日經"), ("^KS11", "KOSPI"),
           ("TXF", "台指期"), ("EXF", "電子期")]
# Yahoo 沒有台灣期貨，這兩個改抓期交所行情（近月合約）
FUTURES = {"TXF", "EXF"}
TAIFEX_URL = "https://mis.taifex.com.tw/futures/api/getQuoteList"
BASE_PAGES = {
    # 0050 成分股，依權重排序（MoneyDJ 2026/08/31 持股）；每季調整後要手動更新
    "台股": [("0050.TW", "元大台灣50"), ("2330.TW", "台積電"), ("2454.TW", "聯發科"), ("2308.TW", "台達電"), ("2317.TW", "鴻海"),
             ("3711.TW", "日月光投控"), ("2383.TW", "台光電"), ("2303.TW", "聯電"), ("3037.TW", "欣興"), ("2881.TW", "富邦金"),
             ("1303.TW", "南亞"), ("2891.TW", "中信金"), ("3017.TW", "奇鋐"), ("2882.TW", "國泰金"), ("2345.TW", "智邦"),
             ("2887.TW", "台新新光金"), ("2382.TW", "廣達"), ("2327.TW", "國巨"), ("2059.TW", "川湖"), ("2885.TW", "元大金"),
             ("6669.TW", "緯穎"), ("2360.TW", "致茂"), ("3008.TW", "大立光"), ("2357.TW", "華碩"), ("2884.TW", "玉山金"),
             ("2408.TW", "南亞科"), ("2301.TW", "光寶科"), ("2886.TW", "兆豐金"), ("2344.TW", "華邦電"), ("2890.TW", "永豐金"),
             ("3231.TW", "緯創"), ("2883.TW", "凱基金"), ("2412.TW", "中華電"), ("3653.TW", "健策"), ("3443.TW", "創意"),
             ("2368.TW", "金像電"), ("2892.TW", "第一金"), ("2880.TW", "華南金"), ("3665.TW", "貿聯-KY"), ("4958.TW", "臻鼎-KY"),
             ("1216.TW", "統一"), ("7769.TW", "鴻勁"), ("3661.TW", "世芯-KY"), ("2395.TW", "研華"), ("2449.TW", "京元電子"),
             ("5880.TW", "合庫金"), ("8046.TW", "南電"), ("2603.TW", "長榮"), ("4904.TW", "遠傳"), ("3045.TW", "台灣大"),
             ("6505.TW", "台塑化")],
    "美股": [("NVDA", "NVIDIA"), ("AAPL", "Apple"), ("TSLA", "Tesla"), ("MSFT", "Microsoft"), ("AVGO", "Broadcom"),
             ("SNDK", "SanDisk"), ("MU", "Micron"), ("SPCX", "SpaceX"), ("INTC", "Intel"), ("AMD", "AMD"),
             ("MRVL", "Marvell"), ("PLTR", "Palantir"),
             ("GOOGL", "Google"), ("AMZN", "Amazon"), ("AMAT", "應用材料"), ("ASML", "ASML"), ("COHR", "Coherent"),
             ("MRNA", "Moderna"), ("SOXX", "SOXX 半導體ETF"),
             ("000660.KS", "SK海力士")],  # 海力士沒有美國掛牌，放韓股報價（韓元、韓股時段）
    "日韓": [("7203.T", "Toyota"),
             ("8035.T", "東京威力"), ("285A.T", "鎧俠"), ("8031.T", "三井物產"), ("8058.T", "三菱商事"),
             ("5706.T", "三井金屬"), ("8801.T", "三井不動產"), ("7011.T", "三菱重工"), ("8306.T", "三菱日聯"),
("005930.KS", "三星"), ("000660.KS", "SK海力士")],
    "總經": [("^TNX", "美10年債殖利率"), ("^IRX", "美13週國庫券"), ("^VIX", "VIX"), ("DX-Y.NYB", "美元指數"),
             ("TWD=X", "美元/台幣"), ("JPY=X", "美元/日圓"), ("KRW=X", "美元/韓元"),
             ("GC=F", "黃金"), ("SI=F", "白銀"), ("HG=F", "銅"), ("ALI=F", "鋁"),
             # 鈾、鎢 Yahoo 沒有能用的現貨報價（UX=F 沒昨收），改用代理：Sprott 實體鈾信託、Almonty（非中國最大鎢礦）
             ("SRUUF", "鈾(SPUT)"), ("ALM", "鎢(ALM)"),
             ("CL=F", "WTI 原油"), ("BTC-USD", "比特幣"),
             ("FED", "美國利率"), ("BOJ", "日本利率"), ("CBC", "台灣利率")],
}
# 個人名單放 portfolio.json（不進程式碼）：庫存填股數與成本，觀察只填代號與名稱。
# 面板執行中改檔會自動重載，watch.py 就是靠這個即時加股票。
PORTFOLIO = Path(__file__).with_name("portfolio.json")


def load_portfolio():
    my = json.loads(PORTFOLIO.read_text(encoding="utf-8")) if PORTFOLIO.exists() else {}
    hold = {h["symbol"]: (h["shares"], h["cost"]) for h in my.get("庫存", [])}
    pages = {**({"庫存": [(h["symbol"], h["name"]) for h in my["庫存"]]} if my.get("庫存") else {}),
             **({"觀察": [(w["symbol"], w["name"]) for w in my["觀察"]]} if my.get("觀察") else {}),
             **BASE_PAGES}
    return hold, pages


HOLD, PAGES = load_portfolio()
REFRESH_SEC, IDLE_SEC = 15, 60  # 盤中／盤後輪詢間隔；ponytail: Yahoo 輪詢，要逐筆就換富果/Shioaji WebSocket
UP, DOWN = "bold #ff3b3b", "bold #00e676"  # 台灣習慣紅漲綠跌，美式就對調
FLAT, NAME, SYMBOL, HEADER, TAB = "#b0b0b0", "bold #ffffff", "#8a8a8a", "bold #4dd0ff", "bold #000000 on #4dd0ff"
RULE = "#3a3a3a"  # 表頭下方細線顏色
RULE_BAR = "#4a4a4a"  # 今日區間條的線
VOL_HOT_STYLE, VOL_LOW_STYLE = "bold #000000 on #ffd54f", "#555555"  # 爆量用反白黃底，跟紅綠、翻牌字都分得開
FLIPPING = "bold #ffd54f"  # 翻牌中的字用琥珀色，像機場看板
# 翻牌：列延遲 ROW_DELAY、字延遲 CHAR_DELAY、字輪每格 STEP_SEC、最多翻 MAX_STEPS 格
ROW_DELAY, CHAR_DELAY, STEP_SEC, MAX_STEPS = 0.08, 0.02, 0.035, 18
FLIP_SEC = 2.0            # 須 ≥ 列數×ROW_DELAY + 字數×CHAR_DELAY + MAX_STEPS×STEP_SEC
ALNUM = " ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,+-%^=/&…"  # 字輪順序
CJK = "台積電鴻海聯發科美股日韓總經指數黃金原油"  # 全形字轉這池，寬度才不會跳
FIXED_LINES = 5           # 大盤 2 行 + 頁籤 + 表頭 + 細線，剩下的高度放個股
AUTO_SEC = 8              # 自動翻頁間隔；手動切頁會重新計時
TAB_GAP = " " * 4          # 頁籤之間的空白
INDEX_SWAP_SEC = 4        # 大盤第二行在幅度 / 漲跌點數之間輪流切換的秒數

quotes = {}  # symbol -> (price, prev_close)
last_update = 0.0


def fetch_taifex(prefix):
    """期交所近月合約：日盤 08:45-13:45 抓一般盤，其餘時間抓夜盤。"""
    now = time.localtime()
    hm = now.tm_hour * 100 + now.tm_min
    day = now.tm_wday < 5 and 845 <= hm <= 1345
    body = json.dumps({"MarketType": "0" if day else "1", "SymbolType": "F", "KindID": "1", "CID": "",
                       "ExpireMonth": "", "RowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"}).encode()
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT  # 期交所憑證缺 Python 3.13 嚴格模式要的欄位，憑證鏈仍照常驗證
    req = urllib.request.Request(TAIFEX_URL, data=body,
                                 headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"})
    rows = json.load(urllib.request.urlopen(req, timeout=10, context=ctx))["RtData"]["QuoteList"]
    # 清單依到期月排序；-S/-P 是現貨，第一筆 -F（日盤）或 -M（夜盤）就是近月
    near = next(r for r in rows if r["SymbolID"].startswith(prefix) and r["SymbolID"][-2:] in ("-F", "-M"))
    price = float(near["CLastPrice"] or 0)
    if price:  # 還沒成交就保留舊值
        quotes[prefix] = (price, float(near["CRefPrice"]))


TWSE_MIS = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch="


def fetch_twse(syms):
    """台股即時：證交所 MIS 約 5 秒一筆，蓋掉 Yahoo 延遲約 20 分鐘的價格、昨收、高低、成交量。
    量比用的 10 日均量仍沿用 Yahoo（歷史值，延遲無妨）。"""
    chans = {}
    for sym in syms:
        if sym == "^TWII":
            chans["tse_t00"] = sym
        elif sym.endswith(".TW"):
            chans[f"tse_{sym[:-3]}"] = sym
        elif sym.endswith(".TWO"):
            chans[f"otc_{sym[:-4]}"] = sym
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT  # 同期交所：憑證缺 3.13 嚴格模式要的欄位
    keys = list(chans)
    for i in range(0, len(keys), 50):  # 一次查 50 檔
        url = TWSE_MIS + "|".join(f"{k}.tw" for k in keys[i:i + 50])  # 代號大小寫要照原樣（00631L）
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        for r in json.load(urllib.request.urlopen(req, timeout=10, context=ctx)).get("msgArray", []):
            sym = chans.get(f"{r.get('ex')}_{r.get('c')}")
            if not sym:
                continue
            try:
                # z 是最近成交價；快照剛好落在兩筆成交之間時是 "-"，改用最佳買價
                price = float(r["z"]) if r.get("z", "-") != "-" else float(r["b"].split("_")[0])
                prev = float(r["y"])
            except (KeyError, ValueError, AttributeError):
                continue
            old = quotes.get(sym, ())
            avg = old[3] if len(old) > 3 else None
            vol = float(r["v"]) * 1000 if r.get("v") else None  # MIS 成交量單位是張
            quotes[sym] = (price, prev, vol, avg, float(r["l"]), float(r["h"]))
            mark_live(sym, price)


def mark_live(sym, price):
    """盤中把證交所即時價記到現在這個時段格，走勢圖最右邊就不會落後。"""
    start, length = SESSIONS.get(suffix_of(sym), (None, None))
    now = time.localtime()
    mins = now.tm_hour * 60 + now.tm_min - (start or 0)
    if start is None or now.tm_wday >= 5 or not 0 <= mins < length:
        return
    cell = min(int(mins / length * RANGE_W * 2), RANGE_W * 2 - 1)
    today = time.strftime("%Y%m%d")
    day, marks = live.get(sym, (today, {}))
    if day != today:
        marks = {}
    marks[cell] = price
    live[sym] = (today, marks)
    cells = intraday.get(sym)
    if cells and all(c is None for c in cells[cell + 1:]):  # 只接在已有資料的後面，不改 Yahoo 那段
        cells[cell] = price


# 央行利率不是盤中報價：美國抓 FRED 的聯邦基金目標區間上限，日本、台灣抓 Trading Economics
# 漲跌欄是跟上一次利率決議比。一小時抓一次就夠。
RATE_SEC = 3600
RATES = {"FED": "united-states", "BOJ": "japan", "CBC": "taiwan"}  # 代號 -> Trading Economics 國家
rate_at = {}


def fetch_rate(sym):
    if time.time() - rate_at.get(sym, 0) < RATE_SEC:
        return
    if sym == "FED":
        url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFEDTARU"
        rows = urllib.request.urlopen(url, timeout=20).read().decode().split()[1:]
        vals = [float(r.split(",")[1]) for r in rows if r.split(",")[1] not in ("", ".")]
        prev = next((v for v in reversed(vals) if v != vals[-1]), vals[-1])  # 上一次調整前的利率
        quotes[sym] = (vals[-1], prev)
    else:
        country = RATES[sym]
        req = urllib.request.Request(f"https://tradingeconomics.com/{country}/interest-rate",
                                     headers={"User-Agent": "Mozilla/5.0"})
        text = re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", urllib.request.urlopen(req, timeout=20).read().decode()))
        # 頁面相關指標表的一列：「Aug 2026 Interest Rate 1.25 1.00 percent」＝ 本期、上期
        last, prev = re.search(r"\d{4} Interest Rate ([\d.]+) ([\d.]+) percent", text).groups()
        quotes[sym] = (float(last), float(prev))
    rate_at[sym] = time.time()


def fetch(sym):
    if sym in RATES:
        try:
            fetch_rate(sym)
        except Exception:
            pass  # 網站改版或連不上就保留舊值
        return
    if sym in FUTURES:
        try:
            fetch_taifex(sym)
        except Exception:
            pass
        return
    q = quotes.get(sym, ())
    if (sym == "^TWII" or sym.endswith((".TW", ".TWO"))) and len(q) > 3 and q[3]:
        return  # 台股即時價由 fetch_twse 更新；Yahoo 只需抓一次 10 日均量，省下請求免得被限流
    try:
        fi = yf.Ticker(sym).fast_info
        quotes[sym] = (fi["lastPrice"], fi["previousClose"], fi["lastVolume"], fi["tenDayAverageVolume"],
                       fi["dayLow"], fi["dayHigh"])
    except Exception:
        pass  # 保留舊值，下輪再試


# 各市場現貨交易時段（台灣時間，開盤分鐘數, 時長分鐘數）；總經、期貨沒有量比
# ponytail: 不管日股午休、美股夏令時間與假日，量比在這些時段會略偏
SESSIONS = {".TW": (540, 270), ".TWO": (540, 270), ".T": (480, 390), ".KS": (480, 390), "US": (1290, 390)}
VOL_HOT, VOL_LOW = 2.0, 0.5   # 量比 ≥ VOL_HOT 爆量標亮，≤ VOL_LOW 量縮變暗
VOL_WARMUP = 15               # 開盤後前幾分鐘量比失真，先不標亮


def suffix_of(sym):
    """.TW / .TWO / .T / .KS，美股回 "US"，其餘（總經、匯率、期貨）回 None。"""
    return "." + sym.split(".")[-1] if "." in sym else ("US" if sym.isalpha() else None)


def volume_ratio(sym, vol, avg):
    """今日量 ÷（10 日均量 × 已開盤比例）。回傳 (文字, 樣式)；沒有量的標的回傳空字串。"""
    suffix = suffix_of(sym)
    if suffix not in SESSIONS or not vol or not avg:
        return "", ""
    start, length = SESSIONS[suffix]
    now = time.localtime()
    since = (now.tm_hour * 60 + now.tm_min - start) % 1440
    trading = since < length and now.tm_wday < 5
    ratio = vol / (avg * (max(since, 1) / length if trading else 1))  # 盤外 lastVolume 是整日量，不用換算
    if ratio >= VOL_HOT and not (trading and since < VOL_WARMUP):
        style = VOL_HOT_STYLE
    else:
        style = VOL_LOW_STYLE if ratio <= VOL_LOW else SYMBOL
    return f"{ratio:.1f}x", style


def market_hours():
    """台灣時間平日 08:00-14:30（台日韓港陸）或 21:00-05:00（美股）算盤中。"""
    # ponytail: 固定時段不管夏令時間跟假日，要精準再查各交易所行事曆
    now = time.localtime()
    hm = now.tm_hour * 100 + now.tm_min
    asia = now.tm_wday < 5 and 800 <= hm <= 1430
    us = (now.tm_wday < 5 and hm >= 2100) or (0 < now.tm_wday < 6 and hm <= 500)
    return asia or us


def poller():
    global last_update
    with ThreadPoolExecutor(8) as pool:
        while True:
            if time.time() - intraday_at >= INTRADAY_SEC:
                try:
                    fetch_intraday()
                except Exception:
                    pass  # 分時抓不到不影響即時報價
            syms = [s for rows in [INDICES, *PAGES.values()] for s, _ in rows]
            list(pool.map(fetch, syms))
            try:
                fetch_twse(syms)
            except Exception:
                pass  # 證交所連不上就先用 Yahoo 的延遲價
            last_update = time.time()
            time.sleep(REFRESH_SEC if market_hours() else IDLE_SEC)


SPARK_ROWS = 2  # 走勢圖幾行高，每檔個股也跟著佔這麼多行
RANGE_W = 16  # 走勢欄寬度（字數）：區間條與分時走勢共用，換顯示不會改版面
SPARK_SWAP_SEC = 5   # 區間條 / 分時走勢輪流切換的秒數
INTRADAY_SEC = 60    # 分時資料重抓間隔
intraday = {}        # symbol -> 收盤價（交易時段切成 RANGE_W*2 個時間點，未到的是 None）
intraday_at = 0.0
live = {}            # symbol -> (日期, {時段格: 證交所即時價})，補 Yahoo 延遲的那一段


def fetch_intraday():
    """當日分時走勢（江波圖）：5 分 K 收盤價，依時間落到固定的時段格子裡。"""
    global intraday_at
    syms = sorted({s for rows in PAGES.values() for s, _ in rows if s not in FUTURES and s not in RATES})
    data = yf.download(syms, period="1d", interval="5m", group_by="ticker",
                       threads=True, progress=False, auto_adjust=False)
    for sym in syms:
        try:
            close = data[sym]["Close"].dropna()
            if close.empty:
                continue
            length = SESSIONS.get(suffix_of(sym), (0, 1440))[1]  # 期貨、匯率、加密幣近乎 24 小時交易
            open_at = close.index[0]
            cells = [None] * (RANGE_W * 2)  # 一格兩個時間點
            for stamp, value in close.items():
                mins = (stamp - open_at).total_seconds() / 60
                cells[min(int(mins / length * RANGE_W * 2), RANGE_W * 2 - 1)] = float(value)
            # Yahoo 台股晚約 20 分鐘：最後幾格改用面板自己記下的證交所即時價接上
            day, marks = live.get(sym, (None, {}))
            if day == time.strftime("%Y%m%d"):
                last = max((i for i, c in enumerate(cells) if c is not None), default=-1)
                for i, price in marks.items():
                    if i > last:
                        cells[i] = price
            intraday[sym] = cells
        except (KeyError, TypeError, ValueError):
            pass  # 抓不到就沒有走勢圖，其他欄照常
    intraday_at = time.time()


def spark(sym, price, prev):
    """當日走勢：一格畫兩個時間點（點字左右兩欄），高度看當天區間，紅綠看昨收上下。"""
    cells = intraday.get(sym)
    if not cells or not prev:
        return "", []
    seen = [c for c in cells if c is not None]
    if not seen:
        return "", []
    cells = cells[:]
    cells[max(i for i, c in enumerate(cells) if c is not None)] = price  # 最新那格用現價
    low, high = min(seen + [price, prev]), max(seen + [price, prev])  # 昨收一定在圖內，當填色的基準線
    span = (high - low) or 1
    top = SPARK_ROWS * 4 - 1
    rows = [None if v is None else top - round((v - low) / span * top) for v in cells]  # 0 最上
    base = top - round((prev - low) / span * top)
    # 點字一個字有 4 列 2 欄，左欄畫前半段、右欄畫後半段；疊 SPARK_ROWS 行字，高度就是 4 倍
    bits = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))
    lines, styles, last_row = [""] * SPARK_ROWS, [], None
    for i in range(0, len(cells), 2):
        pair = cells[i:i + 2]
        if all(v is None for v in pair):  # 還沒走到的時段用點標出時間軸（畫在昨收那行）
            for k in range(SPARK_ROWS):
                lines[k] += "·" if k == base // 4 else "⠀"  # 空白點字，不會被置中對齊吃掉
            styles.append(RULE_BAR)
            continue
        dots = [0] * SPARK_ROWS
        for col in range(2):
            row = rows[i + col]
            if row is None:
                continue
            # 從線（含跟前一點的落差）填到昨收：漲的往下填、跌的往上填
            start = row if last_row is None else last_row
            for r in range(min(start, row, base), max(start, row, base) + 1):
                dots[r // 4] |= bits[col][r % 4]
            last_row = row
        for k in range(SPARK_ROWS):
            lines[k] += chr(0x2800 + dots[k])
        last = [v for v in pair if v is not None][-1]
        styles.append(UP if last > prev else DOWN if last < prev else FLAT)
    return "\n".join(lines), styles


def range_bar(price, low, high):
    """現價在今日低點到高點之間的位置，● 越靠右越接近今日高點。"""
    if not low or not high or high <= low:
        return ""
    pos = round((min(max(price, low), high) - low) / (high - low) * (RANGE_W - 1))
    return "─" * pos + "●" + "─" * (RANGE_W - 1 - pos)


def rows_of(items, hold=False):
    """hold=True 時漲跌欄改成今日損益（漲跌×股數），幅度欄改成總報酬（對成本）。"""
    out = []
    for sym, name in items:
        q = quotes.get(sym)
        # 列格式：(名稱, 代號, 區間條, 量比, 價格, 漲跌, 幅度, 漲跌色, 量比色, 日線圖)
        if not q or not q[1]:
            out.append((name, sym, "", "", "…", "", "", None, "", ("", [])))
            continue
        price, prev, *extra = q
        chg = price - prev
        vr, vr_style = volume_ratio(sym, *extra[:2]) if extra else ("", "")
        rb = range_bar(price, *extra[2:]) if extra else ""
        sp = spark(sym, price, prev)
        if hold:
            shares, cost = HOLD[sym]
            ret = (price - cost) / cost
            color = UP if ret > 0 else DOWN if ret < 0 else FLAT
            out.append((name, sym, rb, vr, f"{price:,.2f}", f"{chg * shares:+,.0f}", f"{ret:+.2%}", color, vr_style, sp))
            continue
        color = UP if chg > 0 else DOWN if chg < 0 else FLAT
        out.append((name, sym, rb, vr, f"{price:,.2f}", f"{chg:+,.2f}", f"{chg / prev:+.2%}", color, vr_style, sp))
    return out


COL_W = 90  # 一欄個股表至少要這麼寬，視窗夠寬就並排多欄，一頁塞更多檔


def views_for(height, width=COL_W):
    """依窗格高度、寬度把每個市場切成數個子頁：高度決定一欄幾檔，寬度決定並排幾欄。"""
    rows = max(1, (height - FIXED_LINES) // SPARK_ROWS)
    per = rows * max(1, width // COL_W)
    out = []
    for market, items in PAGES.items():
        chunks = [items[i:i + per] for i in range(0, len(items), per)]
        for n, chunk in enumerate(chunks, 1):
            label = f"{market} {n}/{len(chunks)}" if len(chunks) > 1 else market
            out.append((market, chunk, label, rows))
    return out


def render(view, old_rows=None, t=1.0):
    market, items, label, per_col = view
    # 頁籤拉開、左右留白，滑鼠比較好點
    tabs = TAB_GAP.join(f"[{TAB}]   {label}   [/]" if p == market else f"[{SYMBOL}]   {p}   [/]" for p in PAGES)
    age = int(time.time() - last_update) if last_update else "-"
    hold = market == "庫存"
    show_spark = int(time.time() / SPARK_SWAP_SEC) % 2  # 區間條與日線圖輪流，欄寬一樣所以版面不動
    new_rows = rows_of(items, hold)
    n = max(len(new_rows), len(old_rows or []))
    # 第 i 檔放在第 i // per_col 欄；翻牌時新舊頁的同一格位置對得上
    tables = [stock_table(hold, show_spark, new_rows, old_rows, t, range(c, min(c + per_col, n)))
              for c in range(0, n, per_col)] or [stock_table(hold, show_spark, [], None, t, range(0))]
    grid = Table.grid(expand=True, padding=(0, 2))
    grid.title = f"{tabs}{TAB_GAP}[{SYMBOL}]· {age}s[/]"
    for _ in tables:
        grid.add_column(ratio=1)
    grid.add_row(*tables)
    return Group(index_bar(), grid)


def stock_table(hold, show_spark, new_rows, old_rows, t, span):
    """一欄個股表，放 span 範圍內的列。"""
    table = Table(box=box.SIMPLE_HEAD, border_style=RULE, show_edge=False, expand=True, header_style=HEADER)
    # ratio 讓欄寬只看視窗寬度、不看內容，翻牌時才不會伸縮
    for col, just, ratio in (("名稱", "left", 3), ("代號", "left", 2),
                             ("當日走勢" if show_spark else "今日區間", "center", 2), ("量比", "right", 1), ("價格", "right", 3),
                             ("今日損益" if hold else "漲跌", "right", 2), ("總報酬" if hold else "幅度", "right", 2)):
        table.add_column(Text(col, justify=just), justify=just, ratio=ratio, no_wrap=True)  # 標題跟內容同邊對齊
    table.columns[1].min_width = 11
    for i, w in ((2, RANGE_W), (3, 5)):  # 區間條、量比固定寬度，ratio 欄會無視 min_width 把它們壓成「…」
        table.columns[i].ratio, table.columns[i].width = None, w
    for i in span:
        blank = ("", "", "", "", "", "", "", None, "", ("", []))
        row = new_rows[i] if i < len(new_rows) else blank
        if show_spark and row[9][0]:
            bar = Text(row[9][0])
            for j, style in enumerate(row[9][1]):  # 每一格自己上色：比前一天漲紅、跌綠
                for k in range(SPARK_ROWS):
                    at = k * (len(row[9][1]) + 1) + j
                    bar.stylize(style, at, at + 1)
        else:
            bar = Text(row[2] + "\n" * (SPARK_ROWS - 1), RULE_BAR)  # 補空行，兩種顯示列高一樣
            bar.highlight_words(["●"], row[7] or FLAT)
        if old_rows is None:
            table.add_row(Text(row[0], NAME), Text(row[1], SYMBOL), bar, Text(row[3], row[8]),
                          *(Text(c, row[7] or "") for c in row[4:7]))
            continue
        old = old_rows[i] if i < len(old_rows) else blank
        flips = [Text(solari(o, c, t, i * ROW_DELAY), FLIPPING) for o, c in zip(old[:7], row[:7])]
        flips[2] = bar  # 區間條是線條符號，不進字輪，直接換新
        table.add_row(*flips)
    return table


def solari(old, new, t, delay):
    """機場航班看板：每個字是一個字輪，從舊字一格一格翻到新字。
    各字要翻的格數不同，所以停下的時間自然錯開。"""
    out = []
    for j in range(max(len(old), len(new))):
        o = old[j] if j < len(old) else " "
        c = new[j] if j < len(new) else " "
        drum = CJK if unicodedata.east_asian_width(c) in "WF" else ALNUM
        oi = drum.find(o) if o in drum else 0
        ci = drum.find(c)
        steps = (ci - oi) % len(drum) if ci >= 0 else len(drum)
        if steps > MAX_STEPS:  # 距離太遠就從目標前 MAX_STEPS 格開始，避免翻太久
            oi, steps = (ci if ci >= 0 else 0) - MAX_STEPS, MAX_STEPS
        # 同列的字輪起跑時間加一點固定抖動，才不會整排同步
        k = int((t - delay - j * CHAR_DELAY - (j * 37 % 5) * 0.015) / STEP_SEC)
        if o == c or k < 0:
            out.append(o)
        elif k >= steps:
            out.append(c)
        else:
            out.append(drum[(oi + k) % len(drum)])
    return "".join(out)


def index_bar():
    bar = Table.grid(expand=True, padding=(0, 1))
    cells = []
    show_chg = int(time.time() / INDEX_SWAP_SEC) % 2
    for name, _, _, _, price, chg, pct, color, *_ in rows_of(INDICES):
        bar.add_column(justify="center")
        if len(price) > 8:
            price = price.rsplit(".", 1)[0]  # 大盤點數去小數，窄窗格才放得下 9 個
        if len(chg) > 7:
            chg = chg.rsplit(".", 1)[0]
        cells.append(f"[{NAME}]{name}[/] [#e0e0e0]{price}[/]\n[{color or SYMBOL}]{(chg if show_chg else pct) or '…'}[/]")
    bar.add_row(*cells)
    return bar


def reload_if_changed(mtime):
    """portfolio.json 有變就重讀；新代號馬上抓一次，不用等下一輪輪詢。"""
    global HOLD, PAGES
    try:
        now = PORTFOLIO.stat().st_mtime if PORTFOLIO.exists() else 0
        if now == mtime:
            return mtime
        HOLD, PAGES = load_portfolio()
    except (OSError, ValueError, KeyError):
        return mtime  # 檔案寫到一半或格式錯：保留舊名單，下圈再試
    new = [s for rows in PAGES.values() for s, _ in rows if s not in quotes]
    threading.Thread(target=lambda: [fetch(x) for x in new], daemon=True).start()
    return now


def enable_mouse():
    """開 VT 輸入跟滑鼠回報：點擊會變成 ESC[<鍵;欄;列M 從鍵盤輸入進來。關掉快速編輯，點擊才不會被拿去選字。"""
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.GetStdHandle(-10)
    mode = ctypes.c_uint32()
    if k32.GetConsoleMode(h, ctypes.byref(mode)):
        k32.SetConsoleMode(h, (mode.value | 0x0200 | 0x0080) & ~0x0040)  # +VT 輸入 +延伸旗標 -快速編輯
    sys.stdout.write("\x1b[?1000h\x1b[?1006h")


def read_key():
    """回傳 "q"、數字、"left"/"right"，或滑鼠點擊 ("click", 欄, 列)（從 1 起算）。"""
    if not msvcrt.kbhit():
        return None
    k = msvcrt.getwch()
    if k in ("\x00", "\xe0"):
        return {"K": "left", "M": "right"}.get(msvcrt.getwch())
    if k != "\x1b":
        return k
    seq = ""
    while msvcrt.kbhit() and not (seq[-1:].isalpha() or seq[-1:] == "~"):
        seq += msvcrt.getwch()
    if seq in ("[C", "[D"):  # VT 輸入模式下的方向鍵
        return "right" if seq == "[C" else "left"
    if seq.startswith("[<") and seq.endswith("M"):  # 只看按下，放開是小寫 m
        b, x, y = map(int, seq[2:-1].split(";"))
        if b in (64, 65):  # 滾輪：上一頁／下一頁
            return "left" if b == 64 else "right"
        if b == 0:
            return ("click", x, y)
    return None


def clicked_market(x, y):
    """點到頁籤列的哪個市場。頁籤位置從上一幀畫面反查，版面怎麼改都對得上。"""
    if not 0 < y <= len(screen):
        return None
    line = screen[y - 1]
    if not all(p in line for p in PAGES):  # 不是頁籤那一列
        return None
    # 點在頁籤列上就算離最近的頁籤：頁籤之間沒有死角，點偏一點也會落到最近那個
    centers = {p: cell_len(line[:line.index(p)]) + 1 + cell_len(p) / 2 for p in PAGES}
    first, last = min(centers.values()), max(centers.values())
    reach = (last - first) / max(1, len(PAGES) - 1)  # 最左、最右的頁籤外側也給一個頁籤間距
    if not first - reach <= x <= last + reach:
        return None
    return min(centers, key=lambda p: abs(centers[p] - x))


screen = []  # 上一幀每一行的純文字，給 clicked_market 反查頁籤位置


def draw(console, renderable):
    """游標回左上角，整頁剛好畫滿窗格高度，最後一行不換行，所以畫面不會捲動。
    不用 rich Live：alt screen 從 hook／重開窗格時偶爾整片空白；原地模式滿高時每次重畫都會往下捲。"""
    width, height = console.size
    buf = Console(file=io.StringIO(), width=width, height=height, force_terminal=True,
                  color_system="truecolor", legacy_windows=False, no_color=False)
    buf.print(renderable, crop=True)
    lines = buf.file.getvalue().split("\n")[:height]
    screen[:] = [ANSI.sub("", line) for line in lines]
    console.file.write("\x1b[H" + "\r\n".join(line + "\x1b[0m\x1b[K" for line in lines) + "\x1b[J")
    console.file.flush()


def main():
    # 從 Claude Code hook 啟動時環境帶著固定的 COLUMNS/LINES，rich 會照它畫、不看窗格真實大小
    os.environ.pop("COLUMNS", None)
    os.environ.pop("LINES", None)
    if "--once" not in sys.argv:
        lock = socket.socket()
        try:  # 佔一個本機 port 當單一實例鎖，程式結束（含被砍）自動釋放
            lock.bind(("127.0.0.1", 47653))
        except OSError:
            return  # 已經有面板在跑：exit 0，wt 會自動關掉這個窗格
    threading.Thread(target=poller, daemon=True).start()
    idx = 0
    if "--once" in sys.argv:  # 自我檢查：抓一輪、印出所有頁
        while not last_update:
            time.sleep(0.2)
        for v in views_for(100):
            Console().print(render(v))
        return
    # 從 hook 或窗格重開時，不加這三個參數會整片空白、沒顏色；確切偵測路徑未查明，別拿掉
    console = Console(force_terminal=True, color_system="truecolor", legacy_windows=False, no_color=False)
    shown = time.time()
    mtime = reload_if_changed(None)
    code_at = Path(__file__).stat().st_mtime
    enable_mouse()
    pending = None  # 翻牌動畫中收到的按鍵／點擊，動畫中斷後馬上處理
    console.file.write("\x1b[2J\x1b[?25l")  # 清畫面、藏游標
    try:
        while True:
            mtime = reload_if_changed(mtime)
            if Path(__file__).stat().st_mtime != code_at:
                sys.exit(3)  # 程式碼改了：外層會用新版重跑，同一個窗格
            names = views_for(console.height, console.width)  # 每圈重算，拖拉窗格高度會自動重新分頁
            idx %= len(names)
            key, pending = pending or read_key(), None
            new = (idx + 1) % len(names) if time.time() - shown >= AUTO_SEC else idx
            if key == "q":
                return
            if isinstance(key, tuple) and (market := clicked_market(*key[1:])):
                own = [i for i, v in enumerate(names) if v[0] == market]
                new = own[(own.index(idx) + 1) % len(own)] if idx in own else own[0]  # 再點同一個頁籤就翻它的下一子頁
            elif key == "right":
                new = (idx + 1) % len(names)
            elif key == "left":
                new = (idx - 1) % len(names)
            elif isinstance(key, str) and key.isdigit() and 1 <= int(key) <= len(PAGES):
                market = list(PAGES)[int(key) - 1]
                new = next(i for i, v in enumerate(names) if v[0] == market)
            if new != idx:
                old_rows, idx = rows_of(names[idx][1], names[idx][0] == "庫存"), new
                t0 = time.time()
                while (t := time.time() - t0) < FLIP_SEC:
                    draw(console, render(names[idx], old_rows, t))
                    time.sleep(1 / 30)
                    if pending := read_key():  # 翻牌中又點了別頁：不等動畫跑完，直接換
                        break
                shown = time.time()
            if not pending:
                draw(console, render(names[idx]))
                time.sleep(0.05)
    finally:
        console.file.write("\x1b[?25h\x1b[?1000l\x1b[?1006l")  # q 離開時把游標、滑鼠還回來


if __name__ == "__main__":
    if "--child" in sys.argv or "--once" in sys.argv:
        main()
    else:
        # 外層只管重跑：程式碼改了（exit 3）或子程序被砍都在原窗格重開，不會留下死掉的窗格；q 正常結束才收
        import subprocess
        while subprocess.call([sys.executable, __file__, "--child", *sys.argv[1:]]) != 0:
            time.sleep(0.5)
