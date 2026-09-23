"""終端機行情面板：頂部大盤固定，下方個股每 AUTO_SEC 秒自動翻頁；←/→ 或數字鍵手動切頁，空白鍵暫停翻頁，q 離開。資料源 Yahoo Finance（免費，部分市場有延遲）。"""
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


def ascii_ca_bundle():
    """libcurl 開不了路徑含中文字的憑證檔（curl: (77)），yfinance 走的 curl_cffi 就整批抓不到，
    面板只剩 TWSE 那條路有數字。複製一份到純 ASCII 路徑再用。要在 import yfinance 之前跑。"""
    import certifi
    import shutil
    src = certifi.where()
    if src.isascii():
        return
    dst = Path("C:/Users/Public/ticker-cacert.pem")
    if not str(dst).isascii():
        return
    if not dst.exists() or dst.stat().st_mtime < Path(src).stat().st_mtime:
        shutil.copyfile(src, dst)
    os.environ["CURL_CA_BUNDLE"] = os.environ["SSL_CERT_FILE"] = str(dst)
    certifi.where = lambda: str(dst)  # yfinance 直接把 where() 當 verify= 傳進去，環境變數擋不住


ascii_ca_bundle()

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
    # 0050 成分股（MoneyDJ 2026/08/31 持股）；每季調整後要手動更新。
    # 依證交所產業別分組，組內仍照權重排序：分頁是按窗格高度切的，同產業排在一起，翻到的每一頁就大致是同一類
    "台股": [("0050.TW", "元大台灣50"),
             # 半導體
             ("2330.TW", "台積電"), ("2454.TW", "聯發科"), ("3711.TW", "日月光投控"), ("2303.TW", "聯電"),
             ("2408.TW", "南亞科"), ("2344.TW", "華邦電"), ("3443.TW", "創意"), ("3661.TW", "世芯-KY"),
             ("2449.TW", "京元電子"),
             # 電子零組件
             ("2308.TW", "台達電"), ("2383.TW", "台光電"), ("3037.TW", "欣興"), ("3017.TW", "奇鋐"),
             ("2327.TW", "國巨"), ("2059.TW", "川湖"), ("2301.TW", "光寶科"), ("3653.TW", "健策"),
             ("2368.TW", "金像電"), ("3665.TW", "貿聯-KY"), ("4958.TW", "臻鼎-KY"), ("8046.TW", "南電"),
             # 電腦及週邊
             ("2382.TW", "廣達"), ("6669.TW", "緯穎"), ("2357.TW", "華碩"), ("3231.TW", "緯創"), ("2395.TW", "研華"),
             # 其他電子（鴻海證交所歸在這類，不是電腦及週邊）
             ("2317.TW", "鴻海"), ("2360.TW", "致茂"), ("7769.TW", "鴻勁"),
             # 通信網路
             ("2345.TW", "智邦"), ("2412.TW", "中華電"), ("4904.TW", "遠傳"), ("3045.TW", "台灣大"),
             # 光電
             ("3008.TW", "大立光"),
             # 金融保險
             ("2881.TW", "富邦金"), ("2891.TW", "中信金"), ("2882.TW", "國泰金"), ("2887.TW", "台新新光金"),
             ("2885.TW", "元大金"), ("2884.TW", "玉山金"), ("2886.TW", "兆豐金"), ("2890.TW", "永豐金"),
             ("2883.TW", "凱基金"), ("2892.TW", "第一金"), ("2880.TW", "華南金"), ("5880.TW", "合庫金"),
             # 塑膠石化、食品、航運
             ("1303.TW", "南亞"), ("6505.TW", "台塑化"), ("1216.TW", "統一"), ("2603.TW", "長榮")],
    "美股": [# 晶片設計與記憶體
             ("NVDA", "NVIDIA"), ("AVGO", "Broadcom"), ("AMD", "AMD"), ("INTC", "Intel"), ("MRVL", "Marvell"),
             ("MU", "Micron"), ("SNDK", "SanDisk"),
             ("000660.KS", "SK海力士"),  # 海力士沒有美國掛牌，放韓股報價（韓元、韓股時段）
             # 半導體設備與材料
             ("AMAT", "應用材料"), ("ASML", "ASML"), ("ENTG", "Entegris"),
             # 光通訊與光學元件
             ("COHR", "Coherent"), ("LITE", "Lumentum"), ("GLW", "Corning"),
             # 大型科技與軟體
             ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("GOOGL", "Google"), ("AMZN", "Amazon"),
             ("META", "Meta"), ("PLTR", "Palantir"),
             # 電動車、太空與伺服器
             ("TSLA", "Tesla"), ("SPCX", "SpaceX"), ("DELL", "Dell"),
             # 稀土與關鍵礦產
             ("MP", "MP Materials"), ("REMX", "稀土ETF"),
             # 航太軍工
             ("LMT", "Lockheed"), ("RTX", "RTX"), ("HWM", "Howmet"),
             # 能源
             ("XOM", "Exxon"), ("SLB", "SLB"), ("XLE", "能源ETF"), ("BE", "Bloom Energy"),
             # 生技與 ETF
             ("MRNA", "Moderna"), ("SOXX", "半導體ETF")],
    "日韓": [# 半導體設備與材料
             ("8035.T", "東京威力"), ("6146.T", "Disco"), ("6920.T", "Lasertec"), ("6857.T", "愛德萬"),
             ("285A.T", "鎧俠"),
             # 稀土與關鍵材料
             ("5713.T", "住友金屬礦山"), ("6762.T", "TDK"), ("5706.T", "三井金屬"),
             # 重工與軍工
             ("7011.T", "三菱重工"), ("7012.T", "川崎重工"), ("7013.T", "IHI"),
             ("5631.T", "日本製鋼所"), ("6208.T", "石川製作所"),
             # AI 與自動化
             ("9984.T", "軟銀集團"), ("6954.T", "發那科"),
             # 商社、金融與其他
             ("8031.T", "三井物產"), ("8058.T", "三菱商事"), ("8306.T", "三菱日聯"),
             ("8801.T", "三井不動產"), ("7203.T", "Toyota"),
             # 韓股
             ("005930.KS", "三星"), ("000660.KS", "SK海力士")],
    "總經": [# 政策利率與公債殖利率曲線
             ("FED", "美國利率"), ("BOJ", "日本利率"), ("CBC", "台灣利率"),
             ("^IRX", "美13週國庫券"), ("^TNX", "美10年債殖利率"), ("^TYX", "美30年債"),
             # 高收益債是信用風險的煤礦金絲雀，股市回檔前常先鬆動
             ("HYG", "高收益債"),
             # 匯率
             ("DX-Y.NYB", "美元指數"), ("EURUSD=X", "歐元"), ("CNY=X", "人民幣"),
             ("TWD=X", "美元/台幣"), ("JPY=X", "美元/日圓"), ("KRW=X", "美元/韓元"),
             # 國際股市：亞洲、中港、歐洲三個時區接力，跟大盤列的美台股湊成一輪
             ("^N225", "日經"), ("^HSI", "恒生"), ("^GDAXI", "德DAX"),
             # 貴金屬
             # PAXG 是代幣化黃金，24/7 不休市；期貨週末、美國假日停牌時看它，價差約 1.5%（持有成本＋幣圈溢價）
             ("GC=F", "黃金"), ("PAXG-USD", "黃金24h"), ("SI=F", "白銀"), ("XAGX-USD", "白銀24h"),
             # 白銀這格只能當參考：XAGX 價位貼著 SI=F（比值 0.997）、週末也在跳，但 24h 量只有個位數美元、
             # 市值 0，等於沒人交易的報價。代幣銀沒有 PAXG 等級的標的（KAG 直接脫鉤，相關 -0.03）
             # 工業金屬與關鍵礦產
             # 銅的代理追蹤最緊（比值 1.012、日報酬相關 0.95），一樣是零市值的薄報價。鋁沒有能用的代幣
             ("HG=F", "銅"), ("XCU-USD", "銅24h"), ("ALI=F", "鋁"),
             # 鈾、鎢 Yahoo 沒有能用的現貨報價（UX=F 沒昨收），改用代理：Sprott 實體鈾信託、Almonty（非中國最大鎢礦）
             ("SRUUF", "鈾(SPUT)"), ("ALM", "鎢(ALM)"),
             # 能源：布蘭特是國際基準、比 WTI 更吃地緣風險；天然氣看歐洲能源與 AI 資料中心電力
             ("CL=F", "WTI 原油"), ("BZ=F", "布蘭特原油"), ("NG=F", "天然氣"),
             # 景氣與運價：BDRY 是波羅的海乾散貨指數的可交易版（Yahoo 沒有 ^BDI）
             # ^DJT 是道氏理論的確認訊號，運輸股不跟上，大盤漲勢就存疑
             ("BDRY", "乾散貨運價"), ("^DJT", "道瓊運輸"),
             # 風險情緒：^SKEW 是尾部風險定價，>145 代表大戶在買保險，比 VIX 更前瞻
             ("^VIX", "VIX"), ("^SKEW", "尾部風險"), ("BTC-USD", "比特幣")],
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
    return hold, pages, my.get("警示", [])


SECTORS = []          # BASE_PAGES["類股"] 就是這個 list，抓到之後原地依漲幅排序
BASE_PAGES["類股"] = SECTORS

HOLD, PAGES, ALERTS = load_portfolio()
REFRESH_SEC, IDLE_SEC = 15, 60  # 盤中／盤後輪詢間隔；ponytail: Yahoo 輪詢，要逐筆就換富果/Shioaji WebSocket
UP, DOWN = "bold #ff3b3b", "bold #00e676"  # 台灣習慣紅漲綠跌，美式就對調
FLAT, NAME, SYMBOL, HEADER, TAB = "#b0b0b0", "bold #ffffff", "#8a8a8a", "bold #4dd0ff", "bold #000000 on #4dd0ff"
RULE = "#3a3a3a"  # 表頭下方細線顏色
RULE_BAR = "#4a4a4a"  # 今日區間條的線
VOL_HOT_STYLE, VOL_LOW_STYLE = "bold #000000 on #ffd54f", "#555555"  # 爆量用反白黃底，跟紅綠、翻牌字都分得開
TICK_UP, TICK_DOWN = "bold #000000 on #ff3b3b", "bold #000000 on #00e676"  # 價格跳動時整格亮一下，顏色跟漲跌一致
TICK_SEC = 0.8  # 亮燈持續秒數；主迴圈 20fps 重繪，這段時間內都看得到
FLIPPING = "bold #d0d0d0"  # 翻牌中的字用亮灰，翻的時候看得出在動、又不搶漲跌色的戲
# 翻牌：列延遲 ROW_DELAY、字延遲 CHAR_DELAY、字輪每格 STEP_SEC、最多翻 MAX_STEPS 格
ROW_DELAY, CHAR_DELAY, STEP_SEC, MAX_STEPS = 0.05, 0.02, 0.035, 18
FLIP_SEC = 2.0            # 須 ≥ 列數×ROW_DELAY + 字數×CHAR_DELAY + MAX_STEPS×STEP_SEC
ALNUM = " ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,+-%^=/&…"  # 字輪順序
CJK = "台積電鴻海聯發科美股日韓總經指數黃金原油"  # 全形字轉這池，寬度才不會跳
FIXED_LINES = 5           # 大盤 2 行 + 頁籤 + 表頭 + 細線，剩下的高度放個股
# 底部跑馬燈：左邊固定幾個總經數字，右邊捲動全市場異動。用 draw() 留的那一行，寬度不能寫滿
FRAME_SEC = 0.05     # 主迴圈一幀多久；睡到下一個邊界，幀落點才不會飄
MARQUEE_FRAMES = 2   # 每幾幀捲一格。終端最小只能移一整格，所以速度只有 1/FRAME_SEC 除以
                     # 整數這幾檔（20、10、6.7…）。中間值會變成一下動一下不動，那才是頓的來源，
                     # 跟快慢無關；幀率不受這個值影響
MARQUEE_TOP = 10     # 異動排行取前幾名
MARQUEE_GAP = "    ·    "  # 捲動內容首尾之間的間隔，接回去才看得出斷點
MARQUEE_MIN = 120    # 窄於這個寬度就不放固定段，整條都給捲動
MARQUEE_FIXED = [("DX-Y.NYB", "DXY"), ("BZ=F", "Brent"), ("GC=F", "Gold"),
                 ("^VIX", "VIX"), ("^TNX", "10Y")]
AUTO_SEC = 20             # 自動翻頁間隔；手動切頁會重新計時
TAB_GAP = " " * 4          # 頁籤之間的空白
INDEX_SWAP_SEC = 4        # 大盤第二行在幅度 / 漲跌點數之間輪流切換的秒數
# 位階：現價對 20 日均線的乖離率，加 52 週位置。日線一天抓一次，寫成檔案，重開面板不用重抓
DAILY_FILE = Path(__file__).with_name("daily.json")
DEV_HIGH, DEV_LOW = 0.08, -0.08       # 乖離率超過 ±8% 就標追高／回檔
DEV_HOT = "bold #000000 on #ff8f00"   # 追高用橘底，跟爆量的黃底、漲跌的紅綠都分得開
DEV_COLD = "bold #000000 on #26c6da"  # 回檔用青底
POS_HOT, POS_COLD = 90, 10            # 52 週位置的高低帶，超過就上色
# 籌碼：外資買賣超連續天數。證交所 T86 收盤後才更新，一小時抓一次就夠
DAILY_BATCH, DAILY_GAP = 25, 2.0   # 日線分批下載的批量與批間隔，一次全丟會被擋
CHIP_FILE = Path(__file__).with_name("chips.json")
# ponytail: chips.json 存全市場（約 230KB），不是只存名單內的。觀察清單隨時會加新股，
# 只存名單內的話新加的那檔就沒有歷史可以算連續天數。嫌大再改成按名單裁切
CHIP_DAYS = 12        # 往回看幾個交易日算連續天數
CHIP_SEC = 3600
# openapi.twse.com.tw 的 /v1/fund/T86 會回 HTML，只有 rwd 這條是 JSON
T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86?date={}&selectType=ALLBUT0999&response=json"
# 類股輪動：證交所 MIS 的類股指數，跟個股走同一條連線，盤中即時。名稱用回傳的 n，不寫死
SECTOR_CODES = [f"t{n:02d}" for n in range(1, 32)]
SECTOR_SET = {f"{c}.TW" for c in SECTOR_CODES}  # 這些只走 MIS，不進 yfinance，也沒有分時圖
# 警示：到價與爆量。規則放 portfolio.json 的「警示」，每天每條只響一次
ALERT_SEC = 300       # 訊息在跑馬燈上留多久
VOL_ALERT = 3.0       # 量比超過這個倍數就自動報一次
ALERT_STYLE = "bold #000000 on #ff3b3b"

quotes = {}  # symbol -> (price, prev_close)
daily = {}        # symbol -> (ma20, ma60, low52, high52)
daily_date = ""   # 日線統計是哪一天抓的
daily_busy = False  # 背景在抓日線時不要再開一條
chips = {}        # 日期 -> {證券代號: 外資買賣超張數}
chips_at = 0.0
alert_msgs = []   # [(觸發時間, 文字)]，跑馬燈左邊插播
alert_fired = {}  # 規則 -> 觸發日期，同一天只響一次
bell = False      # 有新警示就讓 draw() 響一聲
last_update = 0.0
paused = False  # 空白鍵暫停自動翻頁，想盯著某一頁看的時候用


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
    if sym in SECTOR_SET:
        return  # 類股指數只有 MIS 有，交給 fetch_twse
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


def session_open(sym):
    """這檔的市場現在有沒有在交易。判斷不了的（總經、匯率、期貨、加密）一律當有，照抓。"""
    suffix = suffix_of(sym)
    if suffix not in SESSIONS:
        return True
    start, length = SESSIONS[suffix]
    now = time.localtime()
    since = (now.tm_hour * 60 + now.tm_min - start) % 1440
    opened = time.localtime(time.time() - since * 60)  # 美股跨午夜，要看開盤那天是不是平日
    return since < length and opened.tm_wday < 5


def market_hours():
    """台灣時間平日 08:00-14:30（台日韓港陸）或 21:00-05:00（美股）算盤中。"""
    # ponytail: 固定時段不管夏令時間跟假日，要精準再查各交易所行事曆
    now = time.localtime()
    hm = now.tm_hour * 100 + now.tm_min
    asia = now.tm_wday < 5 and 800 <= hm <= 1430
    us = (now.tm_wday < 5 and hm >= 2100) or (0 < now.tm_wday < 6 and hm <= 500)
    return asia or us


tick_at = {}   # symbol -> (跳動時間, +1 漲 / -1 跌)
seen_px = {}   # symbol -> 上一輪看到的價格；第一次看到不算跳動，免得開面板時整片閃


def mark_ticks():
    """poller 抓完一輪後比對價格，有動的記時間戳，stock_table 據此亮燈。
    放在這裡而不是各個 fetch 裡：Yahoo、證交所、盤中即時價三條來源都會經過這一點。"""
    now = time.time()
    for sym, q in list(quotes.items()):
        price, old = q[0], seen_px.get(sym)
        if old is not None and price != old:
            tick_at[sym] = (now, 1 if price > old else -1)
        seen_px[sym] = price


def tick_style(sym):
    """還在亮燈時間內就回傳反白樣式，否則 None 交給原本的漲跌色。"""
    tk = tick_at.get(sym)
    if not tk or time.time() - tk[0] >= TICK_SEC:
        return None
    return TICK_UP if tk[1] > 0 else TICK_DOWN


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
            # 收盤的市場價格不會再動，跳過可以少掉夜裡大半的請求，免得被 Yahoo 限流。
            # 還沒抓到過的照抓，不然剛開面板時收盤市場會整片空白
            syms = [s for s in syms if s in FUTURES or s in RATES or session_open(s) or s not in quotes]
            list(pool.map(fetch, syms))
            try:
                fetch_twse(syms)
            except Exception:
                pass  # 證交所連不上就先用 Yahoo 的延遲價
            try:
                fetch_sectors()
            except Exception:
                pass  # 類股指數抓不到就先空著
            # 位階是日線統計，盤中不急；盤中抓會跟即時報價搶 Yahoo 的額度（YFRateLimitError），
            # 所以只在盤後補，除非手上完全沒資料（第一次開面板）
            if daily_date != time.strftime("%Y%m%d") and not daily_busy and (not market_hours() or not daily):
                threading.Thread(target=daily_worker, daemon=True).start()
            if time.time() - chips_at >= CHIP_SEC:
                try:
                    fetch_chips()
                except Exception:
                    pass
            mark_ticks()
            check_alerts()
            last_update = time.time()
            time.sleep(REFRESH_SEC if market_hours() else IDLE_SEC)


SPARK_ROWS = 2  # 走勢圖幾行高，每檔個股也跟著佔這麼多行
RANGE_W = 16  # 走勢欄寬度（字數）：區間條與分時走勢共用，換顯示不會改版面
SPARK_SWAP_SEC = 5   # 區間條 / 分時走勢輪流切換的秒數
HEAT_FULL = 0.02     # 類股強弱條滿格的漲跌幅；類股指數一天動 2% 已經是很大的輪動
INTRADAY_SEC = 60    # 分時資料重抓間隔
intraday = {}        # symbol -> 收盤價（交易時段切成 RANGE_W*2 個時間點，未到的是 None）
intraday_at = 0.0
live = {}            # symbol -> (日期, {時段格: 證交所即時價})，補 Yahoo 延遲的那一段


def fetch_intraday():
    """當日分時走勢（江波圖）：5 分 K 收盤價，依時間落到固定的時段格子裡。"""
    global intraday_at
    syms = sorted({s for rows in PAGES.values() for s, _ in rows if s not in FUTURES and s not in RATES and s not in SECTOR_SET})
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


def heat_bar(pct):
    """類股強弱條：中間是平盤，往右紅、往左綠，滿格是 HEAT_FULL。翻到類股頁一眼看出今天誰在領漲。"""
    half = RANGE_W // 2
    n = min(round(abs(pct) / HEAT_FULL * half), half)
    return " " * half + "█" * n + " " * (half - n) if pct >= 0 else " " * (half - n) + "█" * n + " " * half


def range_bar(price, low, high):
    """現價在今日低點到高點之間的位置，● 越靠右越接近今日高點。"""
    if not low or not high or high <= low:
        return ""
    pos = round((min(max(price, low), high) - low) / (high - low) * (RANGE_W - 1))
    return "─" * pos + "●" + "─" * (RANGE_W - 1 - pos)


def load_daily():
    """開面板時先讀上次存的日線統計：同一天抓過就不再抓，網路不通也還有位階可看。"""
    global daily_date
    try:
        blob = json.loads(DAILY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    daily.update({k: tuple(v) for k, v in blob.get("stats", {}).items()})
    daily_date = blob.get("date", "")


def daily_worker():
    """位階的日線統計慢又容易被擋，丟背景跑，不要卡住即時報價那一圈。"""
    global daily_busy
    daily_busy = True
    try:
        fetch_daily()
    except Exception:
        pass  # 抓不到就沿用上次存的
    finally:
        daily_busy = False


def fetch_daily():
    """位階用的日線統計：20／60 日均線與 52 週高低。收盤後的值到隔天都不會變，一天抓一次。"""
    global daily_date
    daily_date = time.strftime("%Y%m%d")  # 先記日期：中途失敗也不要每圈重試，等明天
    syms = sorted({s for rows in PAGES.values() for s, _ in rows
                   if s not in FUTURES and s not in RATES and s not in SECTOR_SET})
    # 一次丟一百多檔會被 Yahoo 擋（YFRateLimitError），連當輪的即時報價都一起掛掉。分批慢慢拿
    for k in range(0, len(syms), DAILY_BATCH):
        batch = syms[k:k + DAILY_BATCH]
        try:
            data = yf.download(batch, period="1y", interval="1d", group_by="ticker",
                               threads=True, progress=False, auto_adjust=False)
        except Exception:
            continue  # 這批沒拿到就算了，明天再抓
        for sym in batch:
            try:
                vals = [float(v) for v in data[sym]["Close"].dropna()]
            except (KeyError, TypeError, ValueError):
                continue
            if len(vals) < 20:
                continue  # 剛上市的沒有均線可算
            ma60 = sum(vals[-60:]) / 60 if len(vals) >= 60 else None
            daily[sym] = (sum(vals[-20:]) / 20, ma60, min(vals), max(vals))
        time.sleep(DAILY_GAP)
    try:
        DAILY_FILE.write_text(json.dumps({"date": daily_date, "stats": daily}, ensure_ascii=False),
                              encoding="utf-8")
    except OSError:
        pass



def bias(sym, price):
    """乖離率：現價離 20 日均線多遠。追高、回檔用底色標出來，中間帶維持灰字。"""
    d = daily.get(sym)
    if not d or not d[0]:
        return "", ""
    dev = (price - d[0]) / d[0]
    return f"{dev:+.1%}", DEV_HOT if dev >= DEV_HIGH else DEV_COLD if dev <= DEV_LOW else SYMBOL


def pos52(sym, price):
    """52 週位置：0% 貼著一年低點，100% 創一年新高。"""
    d = daily.get(sym)
    if not d or d[3] is None or d[3] <= d[2]:
        return "", ""
    pct = min(max(round((price - d[2]) / (d[3] - d[2]) * 100), 0), 100)
    return f"{pct}%", UP if pct >= POS_HOT else DOWN if pct <= POS_COLD else SYMBOL


def load_chips():
    try:
        chips.update(json.loads(CHIP_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass


def fetch_chips():
    """外資買賣超：證交所 T86 一天一份，只補 chips.json 還沒有的交易日。
    收盤前當天還沒有資料，所以當天不寫空值，等下一輪再問。"""
    global chips_at
    chips_at = time.time()
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT  # 同 MIS：憑證缺 3.13 嚴格模式要的欄位
    today, days, back = time.strftime("%Y%m%d"), [], 0
    while len(days) < CHIP_DAYS and back < 30:
        t = time.localtime(time.time() - back * 86400)
        back += 1
        if t.tm_wday < 5:
            days.append(time.strftime("%Y%m%d", t))
    for day in days:
        if day in chips:
            continue
        try:
            req = urllib.request.Request(T86_URL.format(day), headers={"User-Agent": "Mozilla/5.0"})
            blob = json.load(urllib.request.urlopen(req, timeout=20, context=ctx))
        except Exception:
            continue  # 連不上就留著，下一輪再補
        net = {}
        if blob.get("stat") == "OK":
            for r in blob.get("data", []):
                try:  # 欄 4 外陸資、欄 7 外資自營商，兩者相加才是市場講的外資買賣超。股數換成張
                    net[r[0].strip()] = (int(r[4].replace(",", "")) + int(r[7].replace(",", ""))) / 1000
                except (IndexError, ValueError, AttributeError):
                    pass
        if net or day != today:
            chips[day] = net  # 放假日存空的，下次就跳過；當天空的不存，收盤後才有
    for day in list(chips):
        if day not in days:
            del chips[day]  # 只留最近這幾個交易日，檔案不會無限長
    try:
        CHIP_FILE.write_text(json.dumps(chips, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def chip_streak(sym):
    """外資連續買超（正）或連續賣超（負）天數。T86 只有上市，上櫃與其他市場回空字串。"""
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
    return (f"{sign * run:+d}日", UP if sign > 0 else DOWN) if run else ("", "")


def pct_of(sym):
    q = quotes.get(sym)
    return (q[0] - q[1]) / q[1] if q and q[1] else 0.0


def fetch_sectors():
    """類股指數：MIS 的 tse_t01..t31，跟個股同一個介面，盤中即時。名稱用回傳的 n，不寫死。
    每輪依今日漲幅重排，翻到這一頁最上面就是今天的主流類股。"""
    if not SECTORS:
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        url = TWSE_MIS + "|".join(f"tse_{c}.tw" for c in SECTOR_CODES)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        found = []
        for r in json.load(urllib.request.urlopen(req, timeout=15, context=ctx)).get("msgArray", []):
            name = (r.get("n") or "").removesuffix("指數").removesuffix("類")
            if name and r.get("c"):
                sym = f"{r['c']}.TW"
                found.append((sym, name))
                try:  # 盤前 z 是「-」，等 fetch_twse 下一輪用最佳買價補
                    quotes[sym] = (float(r["z"]), float(r["y"]))
                except (KeyError, ValueError):
                    pass
        SECTORS[:] = found  # 整批換掉，不用 append：render 那邊隨時可能在讀這個 list
    SECTORS[:] = sorted(SECTORS, key=lambda s: -pct_of(s[0]))


def check_alerts():
    """到價與爆量。每條規則每天只響一次，訊息插在跑馬燈最左邊，順便響一聲。"""
    global bell
    today = time.strftime("%Y%m%d")
    for rule in ALERTS:
        q = quotes.get(rule.get("symbol"))
        if not q or not q[1]:
            continue
        for way, label in (("above", "突破"), ("below", "跌破")):
            level = rule.get(way)
            key = f"{rule.get('symbol')}|{way}|{level}"
            if level is None or alert_fired.get(key) == today:
                continue
            if q[0] >= level if way == "above" else q[0] <= level:
                alert_fired[key] = today
                alert_msgs.append((time.time(), f"{rule.get('name') or rule['symbol']} {label} {level:,.2f}"))
                bell = True
    for sym, name in [(s, n) for rows in PAGES.values() for s, n in rows]:
        q, key = quotes.get(sym), f"{sym}|vol"
        if not q or len(q) < 4 or alert_fired.get(key) == today:
            continue
        text, style = volume_ratio(sym, q[2], q[3])
        if style == VOL_HOT_STYLE and text and float(text[:-1]) >= VOL_ALERT:
            alert_fired[key] = today
            alert_msgs.append((time.time(), f"{name} 爆量 {text}"))
            bell = True
    alert_msgs[:] = [m for m in alert_msgs if time.time() - m[0] < ALERT_SEC]



def rows_of(items, hold=False):
    """hold=True 時漲跌欄改成今日損益（漲跌×股數），幅度欄改成總報酬（對成本）。"""
    out = []
    for sym, name in items:
        q = quotes.get(sym)
        # 列格式：(名稱, 代號, 區間條, 量比, 價格, 漲跌, 幅度, 漲跌色, 量比色, 日線圖,
        #        乖離, 乖離色, 52週位置, 位置色, 外資連續天數, 籌碼色)
        if not q or not q[1]:
            out.append((name, sym, "", "", "…", "", "", None, "", ("", []), "", "", "", "", "", ""))
            continue
        price, prev, *extra = q
        chg = price - prev
        vr, vr_style = volume_ratio(sym, *extra[:2]) if extra else ("", "")
        rb = range_bar(price, *extra[2:]) if extra else ""
        if sym in SECTOR_SET:
            rb = heat_bar(chg / prev)  # 類股沒有個股那種今日區間，這一欄改放強弱條
        sp = spark(sym, price, prev)
        dev, dev_style = bias(sym, price)
        pos, pos_style = pos52(sym, price)
        chip, chip_style = chip_streak(sym)
        if hold:
            shares, cost = HOLD[sym]
            ret = (price - cost) / cost
            color = UP if ret > 0 else DOWN if ret < 0 else FLAT
            out.append((name, sym, rb, vr, f"{price:,.2f}", f"{chg * shares:+,.0f}", f"{ret:+.2%}", color, vr_style, sp, dev, dev_style, pos, pos_style, chip, chip_style))
            continue
        color = UP if chg > 0 else DOWN if chg < 0 else FLAT
        out.append((name, sym, rb, vr, f"{price:,.2f}", f"{chg:+,.2f}", f"{chg / prev:+.2%}", color, vr_style, sp, dev, dev_style, pos, pos_style, chip, chip_style))
    return out


# 欄寬全部寫死，版面不隨內容伸縮；價格三欄置中並靠左邊的籌碼欄，右邊剩下的寬度留給最後那個空欄
NAME_W, SYM_W, PRICE_W, CHG_W, PCT_W = 13, 9, 9, 9, 8  # 名稱含前導空白，實際放得下 12 格
COL_W = 100  # 一欄個股表至少要這麼寬（固定欄寬合計 87 + 每欄右側 1 格留白），視窗夠寬就並排多欄


def views_for(height, width=COL_W):
    """依窗格高度、寬度把每個市場切成數個子頁：高度決定一欄幾檔，寬度決定並排幾欄。"""
    rows = max(1, (height - FIXED_LINES) // SPARK_ROWS)
    per = rows * max(1, width // COL_W)
    out = []
    for market, items in PAGES.items():
        chunks = [items[i:i + per] for i in range(0, len(items), per)] or [[]]
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
    grid.title = f"{tabs}{TAB_GAP}[{SYMBOL}]· {age}s{' ⏸' if paused else ''}[/]"
    for _ in tables:
        grid.add_column(ratio=1)
    grid.add_row(*tables)
    return Group(index_bar(), grid)


def stock_table(hold, show_spark, new_rows, old_rows, t, span):
    """一欄個股表，放 span 範圍內的列。"""
    # padding 只留右邊那一格：預設左右各一格，11 欄就吃掉 22 格，欄跟欄之間會散開
    table = Table(box=box.SIMPLE_HEAD, border_style=RULE, show_edge=False, expand=True,
                  header_style=HEADER, padding=(0, 1, 0, 0))
    # ratio 讓欄寬只看視窗寬度、不看內容，翻牌時才不會伸縮
    for col, just, ratio in ((" 名稱", "left", 3), ("代號", "left", 2),  # 名稱帶一格前導空白，不然會貼齊窗格邊
                             ("當日走勢" if show_spark else "今日區間", "center", 2), ("量比", "center", 1),
                             ("乖離", "center", 1), ("年區間", "center", 1), ("外資", "center", 1),
                             ("價格", "center", 3),
                             ("今日損益" if hold else "漲跌", "center", 2),
                             ("總報酬" if hold else "幅度", "center", 2),
                             ("", "left", 1)):  # 最後這欄只是留白，之後要加東西就放這裡
        table.add_column(Text(col, justify=just), justify=just, ratio=ratio, no_wrap=True)  # 標題跟內容同邊對齊
    # 最後那欄不設寬度，ratio 會把剩下的空間全給它
    for i, w in ((0, NAME_W), (1, SYM_W), (2, RANGE_W), (3, 5), (4, 7), (5, 6), (6, 5),
                 (7, PRICE_W), (8, CHG_W), (9, PCT_W)):
        table.columns[i].ratio, table.columns[i].width = None, w
    for i in span:
        blank = ("", "", "", "", "", "", "", None, "", ("", []), "", "", "", "", "", "")
        row = new_rows[i] if i < len(new_rows) else blank
        if show_spark and row[9][0]:
            bar = Text(row[9][0])
            for j, style in enumerate(row[9][1]):  # 每一格自己上色：比前一天漲紅、跌綠
                for k in range(SPARK_ROWS):
                    at = k * (len(row[9][1]) + 1) + j
                    bar.stylize(style, at, at + 1)
        else:
            if row[1] in SECTOR_SET:
                line = row[7] or RULE_BAR  # 類股強弱條整條都是色塊，不壓暗
            else:
                line = f"dim {row[7]}".replace("bold ", "") if row[7] else RULE_BAR  # 線用漲跌同色的暗版，● 才是亮的
            bar = Text(row[2] + "\n" * (SPARK_ROWS - 1), line)  # 補空行，兩種顯示列高一樣
            bar.highlight_words(["●"], f"not dim {row[7]}" if row[7] else FLAT)
        if old_rows is None:
            table.add_row(Text(" " + row[0], NAME), Text(row[1], SYMBOL), bar, Text(row[3], row[8]),
                          Text(row[10], row[11]), Text(row[12], row[13]), Text(row[14], row[15]),
                          Text(row[4], tick_style(row[1]) or row[7] or ""),
                          *(Text(c, row[7] or "") for c in row[5:7]), Text(""))
            continue
        old = old_rows[i] if i < len(old_rows) else blank
        flips = [Text(solari(o, c, t, i * ROW_DELAY), FLIPPING) for o, c in zip(old[:7], row[:7])]
        flips[2] = bar  # 區間條是線條符號，不進字輪，直接換新
        flips[0] = Text(" " + flips[0].plain, FLIPPING)  # 翻牌中也要留著那一格
        # 位階、籌碼是每天才變一次的數字，不進字輪，跟區間條一樣直接換
        extra = [Text(row[10], row[11]), Text(row[12], row[13]), Text(row[14], row[15])]
        table.add_row(*flips[:4], *extra, *flips[4:], Text(""))
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
    global HOLD, PAGES, ALERTS
    try:
        now = PORTFOLIO.stat().st_mtime if PORTFOLIO.exists() else 0
        if now == mtime:
            return mtime
        HOLD, PAGES, ALERTS = load_portfolio()
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


def movers(n=MARQUEE_TOP):
    """全部頁面的標的裡，依漲跌幅絕對值取前 n 名。跨頁重複的代號只算一次。
    盤中的市場優先：白天是台股、日韓，晚上是美股。收盤的市場價格整天不動，
    掛在榜上只是佔位子；全部收盤時（半夜、週末）才退回來用它們，免得整條空白。
    央行利率排除：它的漲跌是跟上次決議比，一放進來就永遠佔著榜首。"""
    seen, live, rest = set(), [], []
    for items in PAGES.values():
        for sym, name in items:
            q = quotes.get(sym)
            if sym in seen or sym in RATES or sym in SECTOR_SET or not q or not q[1]:
                continue
            seen.add(sym)
            price, prev, *extra = q
            vr = volume_ratio(sym, *extra[:2])[1] if extra else ""
            row = (abs(price - prev) / prev, name, (price - prev) / prev, vr == VOL_HOT_STYLE)
            (live if session_open(sym) else rest).append(row)
    live.sort(reverse=True)
    rest.sort(reverse=True)
    return (live or rest)[:n]


def marquee_body():
    """捲動段：異動排行。漲紅跌綠，爆量的補一個閃電。"""
    t = Text()
    for _, name, pct, hot in movers():
        t.append("▲ " if pct > 0 else "▼ ", UP if pct > 0 else DOWN)
        t.append(name, NAME)
        t.append(f" {pct:+.2%}", UP if pct > 0 else DOWN)
        if hot:
            t.append(" ⚡", VOL_HOT_STYLE)
        t.append("   ")
    return t


def alert_text():
    """警示插在跑馬燈最左邊，窄窗格沒有固定段時也照樣顯示。只留最近兩則，蓋掉整條就沒得看了。"""
    t = Text()
    for _, msg in alert_msgs[-2:]:
        t.append(f" 警示 {msg} ", ALERT_STYLE)
        t.append("  ")
    return t


def marquee_head():
    """固定段：幾個看大環境的數字，不捲動。"""
    t = Text()
    for sym, label in MARQUEE_FIXED:
        q = quotes.get(sym)
        if not q or not q[1]:
            continue
        price, prev = q[0], q[1]
        pct = (price - prev) / prev
        t.append(f"{label} ", SYMBOL)
        t.append(f"{price:,.2f} ", NAME)
        t.append(f"{pct:+.2%}", UP if pct > 0 else DOWN if pct < 0 else FLAT)
        t.append("  ")
    return t


def clip_cells(t, start, width):
    """Text 依「顯示格」切片：全形字算兩格。切點落在全形字中間就補一個空白，
    不然捲動時整條會左右抖一格。尾巴補空白填滿，殘影才不會留在後面。"""
    w = [2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in t.plain]
    pos = i = 0
    while i < len(w) and pos + w[i] <= start:
        pos += w[i]
        i += 1
    out = Text()
    if pos < start and i < len(w):  # start 卡在全形字中間
        out.append(" " * (pos + w[i] - start))
        pos, i = pos + w[i], i + 1
    used, j = cell_len(out.plain), i
    while j < len(w) and used + w[j] <= width:
        used += w[j]
        j += 1
    out.append_text(t[i:j])
    out.append(" " * max(0, width - used))
    return out


def marquee(width):
    """組一整行：固定段 + 捲動段，總寬剛好 width。"""
    head = alert_text()
    if width >= MARQUEE_MIN:
        head.append_text(marquee_head())
    if head.plain:
        head.append("│ ", RULE)
    rest = width - cell_len(head.plain)
    body = marquee_body()
    if not body.plain:
        return head.append(" " * max(0, rest))
    loop = body.copy()
    loop.append(MARQUEE_GAP, RULE)
    span = cell_len(loop.plain)
    scroll = loop.copy()
    while cell_len(scroll.plain) < rest + span:  # 接夠長，切到尾巴時後面還有內容接上
        scroll.append_text(loop)
    head.append_text(clip_cells(scroll, int(time.time() / FRAME_SEC) // MARQUEE_FRAMES % span, rest))
    return head


screen = []  # 上一幀每一行的純文字，給 clicked_market 反查頁籤位置


def draw(console, renderable):
    """游標回左上角，整頁畫滿窗格（底部留一行），最後一行不換行，所以畫面不會捲動。
    不用 rich Live：alt screen 從 hook／重開窗格時偶爾整片空白；原地模式滿高時每次重畫都會往下捲。"""
    global bell
    width, height = console.size
    height -= 1  # 底部留一行：每行都剛好滿寬，寫進右下角那一格終端機就捲一行（畫面上下抖動）
    buf = Console(file=io.StringIO(), width=width, height=height, force_terminal=True,
                  color_system="truecolor", legacy_windows=False, no_color=False)
    buf.print(renderable, crop=True)
    lines = buf.file.getvalue().split("\n")[:height]
    screen[:] = [ANSI.sub("", line) for line in lines]
    # \x1b[?2026h/l：同步更新，終端機等整幀寫完才換上，不會畫一半就顯示（撕裂）
    bar = Console(file=io.StringIO(), width=width, force_terminal=True,
                  color_system="truecolor", legacy_windows=False, no_color=False)
    bar.print(marquee(width - 1), end="", crop=True, no_wrap=True)  # -1：不碰右下角那一格，碰了畫面會捲
    console.file.write("\x1b[?2026h\x1b[H"
                       + "\r\n".join(line + "\x1b[0m\x1b[K" for line in lines)
                       + "\x1b[J\r\n" + bar.file.getvalue() + "\x1b[0m\x1b[K"
                       + "\x1b[?2026l")
    if bell:
        console.file.write(chr(7))  # 終端機響一聲，沒盯著面板也知道有警示
        bell = False
    console.file.flush()


def log_stderr():
    """yfinance 一輪輪詢往 stderr 印幾百行（SSL、delisted…）。那些字落進窗格，
    每印一行畫面就捲一行，看起來就是面板一直上下抖。整個 fd 2 導進 log，
    不管哪個函式庫在印都擋得住，要查錯就看這個檔。"""
    path = Path(__file__).with_name("ticker-errors.log")
    mode = "w" if path.exists() and path.stat().st_size > 1_000_000 else "a"  # 太大就從頭寫，不然會無限長
    f = open(path, mode, buffering=1, encoding="utf-8", errors="replace")
    os.dup2(f.fileno(), 2)
    sys.stderr = f


def main():
    global paused
    # 從 Claude Code hook 啟動時環境帶著固定的 COLUMNS/LINES，rich 會照它畫、不看窗格真實大小
    os.environ.pop("COLUMNS", None)
    os.environ.pop("LINES", None)
    if "--once" not in sys.argv:
        log_stderr()
        lock = socket.socket()
        try:  # 佔一個本機 port 當單一實例鎖，程式結束（含被砍）自動釋放
            lock.bind(("127.0.0.1", 47653))
        except OSError:
            return  # 已經有面板在跑：exit 0，wt 會自動關掉這個窗格
    load_daily()
    load_chips()
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
            # -1：draw() 底部留一行。每圈重算，拖拉窗格高度會自動重新分頁
            names = views_for(console.height - 1, console.width)
            idx %= len(names)
            key, pending = pending or read_key(), None
            new = (idx + 1) % len(names) if not paused and time.time() - shown >= AUTO_SEC else idx
            if key == "q":
                return
            if key == " ":  # 空白鍵定住這一頁，標題出現 ⏸；再按一次恢復自動翻頁
                paused = not paused
                shown = time.time()
            elif isinstance(key, tuple) and (market := clicked_market(*key[1:])):
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
                time.sleep(FRAME_SEC - time.time() % FRAME_SEC)  # 睡到下一個幀邊界，跑馬燈才勻速
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
