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
import urllib.parse
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
    holdings = my.get("庫存", [])
    if isinstance(holdings, dict):
        holdings = [x for items in holdings.values() for x in items]
    hold = {h["symbol"]: (h["shares"], h["cost"]) for h in holdings}
    # 分頁名隨 portfolio.json，照檔案裡的順序排；只有「庫存」那頁額外顯示股數與損益。
    # 一頁底下可再用 {"產業": [...]} 分組，同產業排在一起，分組名當成一列標題。
    def entries(v):
        if isinstance(v, dict):
            return [x for group, items in v.items() for x in [(None, group)] + entries(items)]
        return [(x["symbol"], x["name"]) for x in v]

    pages = {**{k: entries(v) for k, v in my.items() if v and k != "警示"}, **BASE_PAGES}
    return hold, pages, my.get("警示", [])


SECTORS = []          # BASE_PAGES["類股"] 就是這個 list，抓到之後原地依漲幅排序
BASE_PAGES["類股"] = SECTORS

HOLD, PAGES, ALERTS = load_portfolio()
REFRESH_SEC, IDLE_SEC = 15, 60  # 國際報價（Yahoo）盤中／盤後輪詢間隔
# 台股與台指期另開一條快迴圈，不跟 Yahoo 那一圈排隊。MIS 本身約 5 秒才更新一次快照，再快也拿不到新價，
# 太密還會被證交所暫時封 IP。ponytail: 5 秒快照是免費來源的極限，要逐筆就換富果/Shioaji WebSocket（要券商帳號）
TW_SEC = 5
UP, DOWN = "bold #ff3b3b", "bold #00e676"  # 台灣習慣紅漲綠跌，美式就對調
FLAT, NAME, SYMBOL, HEADER, TAB = "#b0b0b0", "bold #ffffff", "#8a8a8a", "bold #4dd0ff", "bold #000000 on #4dd0ff"
RULE = "#3a3a3a"  # 表頭下方細線顏色
RULE_BAR = "#4a4a4a"  # 今日區間條的線
# 今日區間條風格：grad 漸層填色（預設，帶量價對比）/ dash 線加 ● / track 點線 / fill 實心
# / light 細底線 / tick 兩端界線
RANGE_STYLE = "grad"
# grad 的量價配色：爆量用亮色，量縮轉暗，價方向決定紅綠
HOT_OF = {"bold #ff3b3b": "#ff1744", "bold #00e676": "#00ff88"}
DIM_OF = {"bold #ff3b3b": "#7a2020", "bold #00e676": "#1a6640", "#b0b0b0": "#4a4a4a"}
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
KBARS = 90        # daily.json 每檔存幾根開高低收；90 根約四個月，K 線面板畫得完
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
zoom = 0          # ZOOMS 的索引，+ / - 切換 K 棒寬度
sel_sym = None    # 右側 K 線面板顯示哪一檔
daily = {}        # symbol -> (ma20, ma60, low52, high52)
daily_date = ""   # 日線統計是哪一天抓的
daily_busy = False  # 背景在抓日線時不要再開一條
miss_at = 0.0       # 上次補抓缺漏日線的時間
MISS_RETRY = 600    # 缺日線的個股多久補抓一次；只抓缺的那幾檔，盤中也不怕被限流
bars = {}         # symbol -> [[開, 高, 低, 收], ...]，最近 KBARS 根，給 K 線面板用
bar_day = {}      # symbol -> bars 最後一根的日期（YYYYMMDD）
live_bar = {}     # symbol -> (交易日, 開, 高, 低, 收)：盤中用即時報價組出今天那根，Yahoo 日線要收盤後才有
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
            if time.time() - fugle_at.get(sym, 0) < FUGLE_FRESH:
                continue  # 富果逐筆比 MIS 快照新，別用舊的蓋回去
            old = quotes.get(sym, ())
            avg = old[3] if len(old) > 3 else None
            vol = float(r["v"]) * 1000 if r.get("v") else None  # MIS 成交量單位是張
            quotes[sym] = (price, prev, vol, avg, float(r["l"]), float(r["h"]))
            if r.get("o", "-") != "-" and r.get("d"):  # 開盤前還沒有開盤價，今天那根先不畫
                live_bar[sym] = (r["d"], float(r["o"]), float(r["h"]), float(r["l"]), price)


CNBC_URL = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols={}"
            "&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json&events=1")
# Yahoo 限流時的備援來源。CNBC 自己一套代號，對得上的才補，對不上的（韓股、鋁、上櫃）就等 Yahoo 回來
CNBC_SYM = {"^N225": ".N225", "^HSI": ".HSI", "^GDAXI": ".GDAXI", "^VIX": ".VIX", "^DJT": ".DJT",
            "^SKEW": ".SKEWX", "^IRX": "US3M", "^TNX": "US10Y", "^TYX": "US30Y", "DX-Y.NYB": ".DXY",
            "^GSPC": ".SPX", "^IXIC": ".IXIC", "^DJI": ".DJI", "^SOX": ".SOX",
            "EURUSD=X": "EUR=", "CNY=X": "CNY=", "TWD=X": "TWD=", "JPY=X": "JPY=", "KRW=X": "KRW=",
            "GC=F": "@GC.1", "SI=F": "@SI.1", "HG=F": "@HG.1", "CL=F": "@CL.1", "BZ=F": "@LCO.1",
            "NG=F": "@NG.1", "BTC-USD": "BTC.CM="}


def cnbc_sym(sym):
    if sym in CNBC_SYM:
        return CNBC_SYM[sym]
    if sym.endswith(".T"):
        return f"{sym[:-2]}.JP"
    # 台股不走 CNBC：它給的昨收等於最後價，會變成永遠 0%；同一輪的 fetch_twse 才是正解
    return sym if sym.replace(".", "").isalnum() and not sym.endswith((".TW", ".TWO", ".KS")) else None


def num(text):
    return float(str(text).replace(",", "").rstrip("%"))


def fetch_cnbc(syms):
    """Yahoo 被限流時的備援：CNBC 一次吃一整批代號，欄位剛好對得上（價、昨收、量、高低）。
    日線均量它沒有，量比就先空著，Yahoo 回來那輪會補上。"""
    chans = {c: s for s in syms if (c := cnbc_sym(s))}
    if not chans:
        return
    keys = list(chans)
    for i in range(0, len(keys), 40):
        url = CNBC_URL.format(urllib.parse.quote("|".join(keys[i:i + 40]), safe="|"))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        rows = json.load(urllib.request.urlopen(req, timeout=15))["FormattedQuoteResult"]["FormattedQuote"]
        for r in rows if isinstance(rows, list) else [rows]:
            sym = chans.get(r.get("symbol"))
            if not sym or r.get("last") is None or r.get("previous_day_closing") is None:
                continue
            try:
                price, prev = num(r["last"]), num(r["previous_day_closing"])
                low = num(r["low"]) if r.get("low") else 0
                high = num(r["high"]) if r.get("high") else 0
                if low <= 0 or high <= 0:  # 殖利率這類沒有日內高低，區間條就收成一點
                    low = high = price
            except ValueError:
                continue
            quotes[sym] = (price, prev, None, None, min(low, price), max(high, price))


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
    global last_update, miss_at
    with ThreadPoolExecutor(8) as pool:
        while True:
            syms = [s for rows in [INDICES, *PAGES.values()] for s, _ in rows if s]  # s 是 None 的是產業分組標題
            # 收盤的市場價格不會再動，跳過可以少掉夜裡大半的請求，免得被 Yahoo 限流。
            # 還沒抓到過的照抓，不然剛開面板時收盤市場會整片空白
            syms = [s for s in syms if s not in FUTURES and (s in RATES or session_open(s) or s not in quotes)]
            list(pool.map(fetch, syms))  # 台股、台指期交給 tw_poller
            missing = [s for s in syms if s not in quotes]  # 多半是 Yahoo 限流，整批空手而回
            if missing:
                try:
                    fetch_cnbc(missing)
                except Exception:
                    pass  # 備援也連不上就維持空白，下輪再試
            try:
                fetch_sectors()
            except Exception:
                pass  # 類股指數抓不到就先空著
            # 位階是日線統計，盤中不急；盤中抓會跟即時報價搶 Yahoo 的額度（YFRateLimitError），
            # 所以只在盤後補，除非手上完全沒資料（第一次開面板）
            if daily_busy:
                pass
            elif daily_date != time.strftime("%Y%m%d") and (not market_hours() or not daily):
                threading.Thread(target=daily_worker, daemon=True).start()
            elif time.time() - miss_at >= MISS_RETRY and (miss := [s for s in daily_syms() if s not in bars]):
                # 新加進清單、或昨天那批被擋掉的，不要等到盤後：只補這幾檔
                miss_at = time.time()
                threading.Thread(target=daily_worker, args=(miss,), daemon=True).start()
            if time.time() - chips_at >= CHIP_SEC:
                try:
                    fetch_chips()
                except Exception:
                    pass
            mark_ticks()
            check_alerts()
            last_update = time.time()
            time.sleep(REFRESH_SEC if market_hours() else IDLE_SEC)


def futures_open():
    """台指期日盤 08:45-13:45、夜盤 15:00-隔天 05:00。"""
    # ponytail: 不管國定假日，放假那幾天會多抓幾次期交所，無害
    now = time.localtime()
    hm = now.tm_hour * 100 + now.tm_min
    return (now.tm_wday < 5 and (845 <= hm <= 1345 or hm >= 1500)) or (0 < now.tm_wday < 6 and hm <= 500)


def tw_poller():
    """台股（證交所 MIS）與台指期（期交所）的快迴圈，不跟 Yahoo 那一圈排隊。
    現貨盤中每 TW_SEC 秒抓 MIS；收盤後 MIS 不會再變，每 IDLE_SEC 秒補一次就好。期貨照日夜盤時段。"""
    twse_at = 0.0
    while True:
        if session_open("x.TW") or time.time() - twse_at >= IDLE_SEC:
            twse_at = time.time()
            try:
                fetch_twse([s for rows in [INDICES, *PAGES.values()] for s, _ in rows if s])
            except Exception:
                pass  # 證交所連不上就先用 Yahoo 的延遲價
        for f in FUTURES:
            if futures_open() or f not in quotes:
                fetch(f)
        mark_ticks()
        time.sleep(TW_SEC)


ROW_LINES = 1   # 個股表每列幾行。走勢圖搬走後一列只要一行，同樣高度能多看一倍的檔數
RANGE_W = 16  # 今日區間條與類股強弱條的寬度（字數）
HEAT_FULL = 0.02     # 類股強弱條滿格的漲跌幅；類股指數一天動 2% 已經是很大的輪動


def heat_bar(pct):
    """類股強弱條：中間是平盤，往右紅、往左綠，滿格是 HEAT_FULL。翻到類股頁一眼看出今天誰在領漲。"""
    half = RANGE_W // 2
    n = min(round(abs(pct) / HEAT_FULL * half), half)
    return " " * half + "█" * n + " " * (half - n) if pct >= 0 else " " * (half - n) + "█" * n + " " * half


def range_bar(price, low, high):
    """現價在今日低點到高點之間的位置，越靠右越接近今日高點。風格看 RANGE_STYLE。"""
    if not low or not high or high <= low:
        return ""
    pos = round((min(max(price, low), high) - low) / (high - low) * (RANGE_W - 1))
    if RANGE_STYLE == "grad":   # 填到現價，游標是 ▓，剩下 ░；顏色由 range_style() 補上量價對比
        return "".join("█" if i < pos else "▓" if i == pos else "░" for i in range(RANGE_W))
    if RANGE_STYLE == "track":
        return "·" * pos + "◆" + "·" * (RANGE_W - 1 - pos)
    if RANGE_STYLE == "fill":
        return "█" * pos + "▌" + " " * (RANGE_W - 1 - pos)
    if RANGE_STYLE == "light":
        return "▁" * pos + "▄" + "▁" * (RANGE_W - 1 - pos)
    if RANGE_STYLE == "tick":
        return "".join("│" if i in (0, RANGE_W - 1) else "●" if i == pos else "┄" for i in range(RANGE_W))
    return "─" * pos + "●" + "─" * (RANGE_W - 1 - pos)  # dash


def range_style(color, vr_style):
    """量價對比：色相看漲跌，明暗看量比。爆量加粗變亮、量縮轉暗，量價背離一眼看得出來。"""
    color = color or FLAT
    if vr_style == VOL_HOT_STYLE:
        return f"bold {HOT_OF.get(color, color)}"
    if vr_style == VOL_LOW_STYLE:
        return DIM_OF.get(color, RULE_BAR)
    return color


def load_daily():
    """開面板時先讀上次存的日線統計：同一天抓過就不再抓，網路不通也還有位階可看。"""
    global daily_date
    try:
        blob = json.loads(DAILY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    daily.update({k: tuple(v) for k, v in blob.get("stats", {}).items()})
    bars.update(blob.get("bars", {}))
    bar_day.update(blob.get("bar_day", {}))
    # 舊存檔沒記最後一根的日期，分不出 Yahoo 有沒有今天那根，當成沒抓過，收盤後重抓一次
    daily_date = blob.get("date", "") if "bar_day" in blob else ""


def daily_worker(syms=None):
    """位階的日線統計慢又容易被擋，丟背景跑，不要卡住即時報價那一圈。"""
    global daily_busy
    daily_busy = True
    try:
        fetch_daily(syms)
    except Exception:
        pass  # 抓不到就沿用上次存的
    finally:
        daily_busy = False


def daily_syms():
    """要抓日線的個股：清單裡扣掉期貨、匯率、類股指數，還有產業分組標題（代號是 None）。"""
    return sorted({s for rows in PAGES.values() for s, _ in rows
                   if s and s not in FUTURES and s not in RATES and s not in SECTOR_SET})


def fetch_daily(syms=None):
    """位階用的日線統計：20／60 日均線與 52 週高低。收盤後的值到隔天都不會變，一天抓一次。
    syms 有給就只補那幾檔，不動 daily_date。"""
    global daily_date
    if syms is None:
        daily_date = time.strftime("%Y%m%d")  # 先記日期：中途失敗也不要每圈重試，等明天
        syms = daily_syms()
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
                df = data[sym][["Open", "High", "Low", "Close"]].dropna()
                vals = [float(v) for v in df["Close"]]
            except (KeyError, TypeError, ValueError):
                continue
            if not vals:
                continue
            if len(vals) >= 20:  # 剛上市的沒有均線可算，但 K 線照畫
                ma60 = sum(vals[-60:]) / 60 if len(vals) >= 60 else None
                daily[sym] = (sum(vals[-20:]) / 20, ma60, min(vals), max(vals))
            # K 線面板要的開高低收；只留最近 KBARS 根，畫得完的部分就夠了
            bars[sym] = [[round(float(x), 4) for x in row] for row in df.values[-KBARS:]]
            bar_day[sym] = df.index[-1].strftime("%Y%m%d")
        time.sleep(DAILY_GAP)
    try:
        DAILY_FILE.write_text(json.dumps({"date": daily_date, "stats": daily, "bars": bars, "bar_day": bar_day},
                                        ensure_ascii=False), encoding="utf-8")
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
        if sym is None:  # 分組標題列：只有名稱，沒有報價（名稱欄是純文字，不吃 markup，靠符號區隔）
            out.append((f"▸ {name}", "", "", "", "", "", "", None, "", "", "", "", "", "", "", "", ""))
            continue
        q = quotes.get(sym)
        # 列格式：(名稱, 代號, 區間條, 量比, 價格, 漲跌, 幅度, 漲跌色, 量比色,
        #        乖離, 乖離色, 52週位置, 位置色, 外資連續天數, 籌碼色, 型態, 型態色)
        if not q or not q[1]:
            out.append((name, sym, "", "", "…", "", "", None, "", "", "", "", "", "", "", "", ""))
            continue
        price, prev, *extra = q
        chg = price - prev
        vr, vr_style = volume_ratio(sym, *extra[:2]) if extra else ("", "")
        rb = range_bar(price, *extra[2:]) if extra else ""
        if sym in SECTOR_SET:
            rb = heat_bar(chg / prev)  # 類股沒有個股那種今日區間，這一欄改放強弱條
        dev, dev_style = bias(sym, price)
        pos, pos_style = pos52(sym, price)
        chip, chip_style = chip_streak(sym)
        pat, pat_style = next(((p[0], p[2]) for p in patterns(sym)), ("", ""))  # 表格只放最重要那一條，說明看右邊面板
        if hold:
            shares, cost = HOLD[sym]
            ret = (price - cost) / cost
            color = UP if ret > 0 else DOWN if ret < 0 else FLAT
            out.append((name, sym, rb, vr, f"{price:,.2f}", f"{chg * shares:+,.0f}", f"{ret:+.2%}", color, vr_style, dev, dev_style, pos, pos_style, chip, chip_style, pat, pat_style))
            continue
        color = UP if chg > 0 else DOWN if chg < 0 else FLAT
        out.append((name, sym, rb, vr, f"{price:,.2f}", f"{chg:+,.2f}", f"{chg / prev:+.2%}", color, vr_style, dev, dev_style, pos, pos_style, chip, chip_style, pat, pat_style))
    return out


# 欄寬全部寫死，版面不隨內容伸縮；價格三欄置中並靠左邊的籌碼欄，右邊剩下的寬度留給最後那個空欄
NAME_W, SYM_W, PRICE_W, CHG_W, PCT_W = 13, 9, 9, 9, 8  # 名稱含前導空白，實際放得下 12 格
COL_W = 112  # 一欄個股表至少要這麼寬（欄寬 95 + 欄間留白 12 + 外層 grid 留白 4 + 留白欄 1），不夠就會截字


# 右側 K 線面板：選中那一檔的日 K。K 棒顏色代表它站在哪條均線上，均線本身不畫線——
# 終端機一格只能放一個字元，畫上去就會把 K 棒吃掉，數值改寫在圖下面那一行
KPANEL_MIN, KPANEL_MAX = 56, 120  # 面板寬度上下限；窗格不夠寬就整個收起來
PAT_MAX = 2           # 型態說明最多列幾條，多了 K 線就被擠扁
KPANEL_FIXED = 2 + PAT_MAX  # K 線圖以外固定佔幾行：標題、均線、型態說明
ZOOMS = ((2, 1), (1, 1), (3, 2))  # (每根佔幾格, 棒身幾格)，+ / - 切換
BAR_UP, BAR_MID, BAR_DOWN = "bold #ff3b3b", "#c62828", "bold #00e676"  # 站上季線／只站上月線／跌破月線
# 上下半格字元：一格塞兩個價格層級，垂直解析度就是行數的兩倍
CELLS = {0: " ", 1: "╵", 2: "╷", 3: "│", 4: "▀", 5: "▀", 6: "▀", 7: "▀",
         8: "▄", 9: "▄", 10: "▄", 11: "▄", 12: "█", 13: "█", 14: "█", 15: "█"}
BITS = {" ": 0, "╵": 1, "╷": 2, "│": 3, "▀": 4, "▄": 8, "█": 12}


FUGLE_FRESH = 10  # 富果多久沒推就讓 MIS 接手（秒）
fugle_at = {}     # symbol -> 富果最後一次推送的時間


def fugle_worker():
    """選中的台股改用富果 WebSocket 逐筆更新：aggregates 頻道每筆成交推一次今天的開高低收。
    沒設 FUGLE_API_KEY 就不跑，今天那根照樣用 MIS 每 5 秒組。
    ponytail: 免費方案只能訂 5 檔，所以只訂 K 線面板選中的那一檔；升級付費方案才值得整份清單都訂。"""
    key = os.environ.get("FUGLE_API_KEY")
    if not key:  # 面板的上層視窗比 key 早開就不會帶這個變數，直接去讀使用者環境變數
        try:
            import winreg
            key = winreg.QueryValueEx(winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment"), "FUGLE_API_KEY")[0]
        except OSError:
            return
    import websocket  # 富果 SDK 帶進來的 websocket-client；SDK 自己的連線執行緒不是 daemon，會卡住面板結束，所以不用它連
    from fugle_marketdata import WebSocketClient
    state = {"ready": False, "sym": None, "ids": {}}  # ids: 富果代號 -> 訂閱 id，退訂要用 id

    def on_open(ws):
        state.update(ready=False, sym=None, ids={})  # 斷線重連後訂閱都沒了，重新來
        ws.send(json.dumps({"event": "auth", "data": {"apikey": key}}))

    def on_message(ws, raw):
        m = json.loads(raw)
        d, ev = m.get("data") or {}, m.get("event")
        if ev == "authenticated":
            state["ready"] = True
        elif ev == "subscribed":
            state["ids"][d.get("symbol")] = d.get("id")
        elif ev in ("data", "snapshot") and m.get("channel") == "aggregates":
            sym = state["sym"]
            if not sym or d.get("symbol") != sym.split(".")[0] or not d.get("openPrice"):
                return  # 退訂前的殘留訊息，或還沒開盤
            last = d.get("lastPrice") or d.get("closePrice")
            live_bar[sym] = (d["date"].replace("-", ""), d["openPrice"], d["highPrice"], d["lowPrice"], last)
            q = quotes.get(sym)
            if q:
                quotes[sym] = (last, q[1], q[2], q[3] if len(q) > 3 else None, d["lowPrice"], d["highPrice"])
            fugle_at[sym] = time.time()

    ws = websocket.WebSocketApp(WebSocketClient(api_key=key).stock.url, on_open=on_open, on_message=on_message)
    # reconnect：斷線後隔 60 秒再連；官方會擋短時間內大量重連的 IP
    threading.Thread(target=lambda: ws.run_forever(ping_interval=30, reconnect=60), daemon=True).start()
    while True:
        want = sel_sym if sel_sym and sel_sym.endswith((".TW", ".TWO")) else None
        if state["ready"] and want != state["sym"]:
            try:
                for code, sid in list(state["ids"].items()):
                    ws.send(json.dumps({"event": "unsubscribe", "data": {"id": sid}}))
                    state["ids"].pop(code, None)
                state["sym"] = want
                if want:
                    ws.send(json.dumps({"event": "subscribe",
                                        "data": {"channel": "aggregates", "symbol": want.split(".")[0]}}))
            except Exception:
                pass  # 連線剛好斷掉；重連後 on_open 會重設，下一圈再訂
        time.sleep(0.5)


def candles(sym):
    """日 K 加上即時組出的今天那根：Yahoo 已經有今天就換掉，還沒有就接在後面。"""
    data, lb = bars.get(sym), live_bar.get(sym)
    if not data or not lb:
        return data
    day, *ohlc = lb
    return data[:-1] + [ohlc] if bar_day.get(sym) == day else data + [ohlc]


def kline(sym, width, rows):
    """K 線本體，回傳 (每列文字, 每列每格的樣式, 畫了幾根)。棒身佔 body 格、每根隔 step 格。"""
    data = candles(sym)
    step, body = ZOOMS[zoom]
    if not data or rows < 3 or width < step:
        return [], [], 0
    n = min(len(data), width // step)
    view = data[-n:]
    lo, hi = min(b[2] for b in view), max(b[1] for b in view)
    span = (hi - lo) or 1
    sub = rows * 2
    lvl = lambda p: min(sub - 1, max(0, int((p - lo) / span * (sub - 1))))
    grid = [[" "] * width for _ in range(rows)]
    style = [[""] * width for _ in range(rows)]
    closes = [b[3] for b in data]
    for i, (o, h, l, c) in enumerate(view):
        j = len(closes) - n + i  # 均線要用這根當下的值，不是最新的
        ma20 = sum(closes[max(0, j - 19):j + 1]) / min(20, j + 1)
        ma60 = sum(closes[max(0, j - 59):j + 1]) / min(60, j + 1)
        st = BAR_UP if c >= ma60 and c >= ma20 else BAR_MID if c >= ma20 else BAR_DOWN
        x0 = i * step
        bt, bb = lvl(max(o, c)), lvl(min(o, c))
        for k in range(lvl(l), lvl(h) + 1):
            r, half = rows - 1 - k // 2, k % 2
            solid = bb <= k <= bt  # 實體畫滿棒寬，影線只畫中間那一格
            xs = range(x0, min(x0 + body, width)) if solid else [min(x0 + body // 2, width - 1)]
            for x in xs:
                grid[r][x] = CELLS[BITS.get(grid[r][x], 0) | ((4 if half else 8) if solid else (1 if half else 2))]
                style[r][x] = st
    return ["".join(r) for r in grid], style, n


def patterns(sym):
    """用已收盤的日 K 判型態，回傳 [(名稱, 白話說明, 顏色)]，少見的先列：K 棒、突破／回測，最後才是均線排列。"""
    data = bars.get(sym) or []
    if len(data) < 21:
        return []
    closes = [b[3] for b in data]
    ma = lambda k, end=None: sum(closes[:end][-k:]) / k
    trend, level, candle = [], [], []
    if len(closes) >= 60:
        m5, m20, m60 = ma(5), ma(20), ma(60)
        if m5 > m20 > m60:
            trend.append(("多頭排列", "短中長均線由上往下排，趨勢偏多", UP))
        elif m5 < m20 < m60:
            trend.append(("空頭排列", "短中長均線由下往上排，趨勢偏空", DOWN))
    o, h, l, c = data[-1]
    po, _, _, pc = data[-2]
    prior = data[-21:-1]
    m20, m20_prev = ma(20), ma(20, -1)
    if c > max(b[1] for b in prior):
        level.append(("突破月高", "收盤站上近 20 日最高價，有機會續攻", UP))
    elif c < min(b[2] for b in prior):
        level.append(("跌破月低", "收盤跌破近 20 日最低價，留意續弱", DOWN))
    elif l <= m20 * 1.01 and c > m20 and m20 >= m20_prev:
        level.append(("回測月線", "碰到上揚的 20 日線又收回，月線有撐", UP))
    body, rng = abs(c - o), (h - l) or 1
    if pc < po and c > o and o <= pc and c >= po:
        candle.append(("多頭吞噬", "紅 K 整根包住前一根黑 K，轉強訊號", UP))
    elif pc > po and c < o and o >= pc and c <= po:
        candle.append(("空頭吞噬", "黑 K 整根包住前一根紅 K，轉弱訊號", DOWN))
    elif body <= rng * 0.1:
        candle.append(("十字線", "開收幾乎同價，多空拉鋸，留意變盤", FLAT))
    elif min(o, c) - l >= body * 2 and h - max(o, c) <= body:
        candle.append(("長下影線", "盤中殺低被買回，下方有承接", UP))
    elif h - max(o, c) >= body * 2 and min(o, c) - l <= body:
        candle.append(("長上影線", "盤中衝高被賣回，上方有賣壓", DOWN))
    return (candle + level + trend)[:PAT_MAX]


def kline_panel(width, rows):
    """面板整體：標題、K 線、均線數值、型態說明。"""
    q = quotes.get(sel_sym) if sel_sym else None
    if not sel_sym or not q or not q[1] or sel_sym not in bars:
        hint = "還在抓日線資料" if sel_sym else "點左邊任一列看它的日 K"
        return Group(Text("│ ", RULE).append(hint, SYMBOL))
    price, prev = q[0], q[1]
    chg = price - prev
    color = UP if chg > 0 else DOWN if chg < 0 else FLAT
    name = next((n for items in PAGES.values() for s, n in items if s == sel_sym), sel_sym)
    lines, styles, n = kline(sel_sym, width - 2, max(3, rows - KPANEL_FIXED))  # -2：分隔線與左留白
    head = Text(" ")
    head.append(f"{name} ", NAME)
    head.append(f"{sel_sym}   ", SYMBOL)
    head.append(f"{price:,.2f}  ", color)
    head.append(f"{chg:+,.2f}  {chg / prev:+.2%}", color)
    head.append(f"   日K {n} 根  ×{ZOOMS[zoom][0]}", SYMBOL)
    out = [head]
    for row, st in zip(lines, styles):
        t = Text(" " + row)
        for x, s in enumerate(st):
            if s:
                t.stylize(s, x + 1, x + 2)
        out.append(t)
    data = candles(sel_sym)
    closes = [b[3] for b in data]
    view = data[-n:] or data
    ma = Text(" ")
    for label, k in (("MA5", 5), ("MA20", 20), ("MA60", 60)):
        if len(closes) >= k:
            ma.append(f"{label} ", SYMBOL)
            ma.append(f"{sum(closes[-k:]) / k:,.0f}   ", NAME)
    ma.append("高 ", SYMBOL)
    ma.append(f"{max(b[1] for b in view):,.0f}   ", NAME)
    ma.append("低 ", SYMBOL)
    ma.append(f"{min(b[2] for b in view):,.0f}", NAME)
    out.append(ma)
    pats = patterns(sel_sym)
    for label, note, st in pats:
        t = Text(" ")
        t.append(f"{label} ", st)
        t.append(note, SYMBOL)
        out.append(t)
    out.extend(Text("") for _ in range(PAT_MAX - len(pats)))  # 補空行，K 線高度才不會跟著型態數跳
    return Group(*(Text("│", RULE).append_text(t) for t in out))  # 每一行前面補一條分隔線


def sel_items(items):
    """確保 sel_sym 落在這一頁裡；換頁或第一次進來就選第一檔。"""
    global sel_sym
    syms = [s for s, _ in items if s]  # 產業分組標題不能被選
    if sel_sym not in syms:
        sel_sym = syms[0] if syms else None
    return syms


def move_sel(items, step):
    global sel_sym
    syms = sel_items(items)
    if syms:
        sel_sym = syms[(syms.index(sel_sym) + step) % len(syms)]


def clicked_row(x, y, items, limit=None):
    """點到哪一檔：從上一幀的純文字找代號，取水平位置最接近 x 的那一個。
    limit 是表格右界，點在右側 K 線圖上不該改選股。"""
    if limit and x > limit:
        return None
    if not 0 < y <= len(screen):
        return None
    line = screen[y - 1]
    hits = [(s, cell_len(line[:line.index(s)]), cell_len(s)) for s, _ in items if s and s in line]
    return min(hits, key=lambda z: abs(z[1] + z[2] / 2 - x))[0] if hits else None



def views_for(height, width=COL_W):
    """依窗格高度、寬度把每個市場切成數個子頁：高度決定一欄幾檔，寬度決定並排幾欄。"""
    rows = max(1, (height - FIXED_LINES) // ROW_LINES)
    per = rows * max(1, width // COL_W)
    out = []
    for market, items in PAGES.items():
        chunks = [items[i:i + per] for i in range(0, len(items), per)] or [[]]
        for n, chunk in enumerate(chunks, 1):
            label = f"{market} {n}/{len(chunks)}" if len(chunks) > 1 else market
            out.append((market, chunk, label, rows))
    return out


def render(view, old_rows=None, t=1.0, panel_w=0, height=0):
    market, items, label, per_col = view
    sel_items(items)  # 換頁後 sel_sym 可能不在這一頁了
    # 頁籤拉開、左右留白，滑鼠比較好點；分類一多會超寬被截掉，就一路縮到塞得下
    for pad, gap in ((3, len(TAB_GAP)), (2, 2), (1, 1), (0, 1)):
        labels = [label if p == market else p for p in PAGES]
        plain = (" " * gap).join(" " * pad + x + " " * pad for x in labels)
        if cell_len(plain) <= Console().width:
            break
    tabs = (" " * gap).join(
        f"[{TAB}]{' ' * pad}{label}{' ' * pad}[/]" if p == market else f"[{SYMBOL}]{' ' * pad}{p}{' ' * pad}[/]"
        for p in PAGES)
    age = int(time.time() - last_update) if last_update else "-"
    hold = market == "庫存"
    new_rows = rows_of(items, hold)
    n = max(len(new_rows), len(old_rows or []))
    # 第 i 檔放在第 i // per_col 欄；翻牌時新舊頁的同一格位置對得上
    tables = [stock_table(hold, new_rows, old_rows, t, range(c, min(c + per_col, n)))
              for c in range(0, n, per_col)] or [stock_table(hold, [], None, t, range(0))]
    grid = Table.grid(expand=True, padding=(0, 2))
    grid.title = f"{tabs}{TAB_GAP}[{SYMBOL}]· {age}s{' ⏸' if paused else ''}[/]"
    for _ in tables:
        grid.add_column(ratio=1)
    grid.add_row(*tables)
    if not panel_w:
        return Group(index_bar(), grid)
    outer = Table.grid(expand=True)
    outer.add_column()
    outer.add_column(width=panel_w)
    outer.add_row(grid, kline_panel(panel_w, max(4, height - 2)))  # -2：大盤列佔兩行
    return Group(index_bar(), outer)


def stock_table(hold, new_rows, old_rows, t, span):
    """一欄個股表，放 span 範圍內的列。"""
    # padding 只留右邊那一格：預設左右各一格，11 欄就吃掉 22 格，欄跟欄之間會散開
    table = Table(box=box.SIMPLE_HEAD, border_style=RULE, show_edge=False, expand=True,
                  header_style=HEADER, padding=(0, 1, 0, 0))
    # ratio 讓欄寬只看視窗寬度、不看內容，翻牌時才不會伸縮
    for col, just, ratio in ((" 名稱", "left", 3), ("代號", "left", 2),  # 名稱帶一格前導空白，不然會貼齊窗格邊
                             ("今日區間", "center", 2), ("量比", "center", 1),
                             ("乖離", "center", 1), ("年區間", "center", 1), ("外資", "center", 1),
                             ("價格", "center", 3),
                             ("今日損益" if hold else "漲跌", "center", 2),
                             ("總報酬" if hold else "幅度", "center", 2),
                             ("型態", "left", 1),
                             ("", "left", 1)):  # 最後這欄只是留白，之後要加東西就放這裡
        table.add_column(Text(col, justify=just), justify=just, ratio=ratio, no_wrap=True)  # 標題跟內容同邊對齊
    # 最後那欄不設寬度，ratio 會把剩下的空間全給它；型態欄放四個中文字，8 格
    for i, w in ((0, NAME_W), (1, SYM_W), (2, RANGE_W), (3, 5), (4, 7), (5, 6), (6, 5),
                 (7, PRICE_W), (8, CHG_W), (9, PCT_W), (10, 8)):
        table.columns[i].ratio, table.columns[i].width = None, w
    for i in span:
        blank = ("", "", "", "", "", "", "", None, "", "", "", "", "", "", "", "", "")
        row = new_rows[i] if i < len(new_rows) else blank
        if row[1] in SECTOR_SET:
            line = row[7] or RULE_BAR  # 類股強弱條整條都是色塊，不壓暗
        else:
            line = RULE_BAR
        bar = Text(row[2], line)
        if RANGE_STYLE == "grad" and row[2] and row[1] not in SECTOR_SET:
            end = row[2].find("▓")
            bar.stylize(range_style(row[7], row[8]), 0, (end if end >= 0 else len(row[2])) + 1)
        else:
            bar.highlight_words(["●"], row[7] or FLAT)
        if old_rows is None:
            table.add_row(Text(" " + row[0], NAME), Text(row[1], SYMBOL), bar, Text(row[3], row[8]),
                          Text(row[9], row[10]), Text(row[11], row[12]), Text(row[13], row[14]),
                          Text(row[4], tick_style(row[1]) or row[7] or ""),
                          *(Text(c, row[7] or "") for c in row[5:7]), Text(row[15], row[16]), Text(""))
            continue
        old = old_rows[i] if i < len(old_rows) else blank
        flips = [Text(solari(o, c, t, i * ROW_DELAY), FLIPPING) for o, c in zip(old[:7], row[:7])]
        flips[2] = bar  # 區間條是線條符號，不進字輪，直接換新
        flips[0] = Text(" " + flips[0].plain, FLIPPING)  # 翻牌中也要留著那一格
        # 位階、籌碼是每天才變一次的數字，不進字輪，跟區間條一樣直接換
        extra = [Text(row[9], row[10]), Text(row[11], row[12]), Text(row[13], row[14])]
        table.add_row(*flips[:4], *extra, *flips[4:], Text(row[15], row[16]), Text(""))
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
    new = [s for rows in PAGES.values() for s, _ in rows if s and s not in quotes]
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
        return {"K": "left", "M": "right", "H": "up", "P": "down"}.get(msvcrt.getwch())
    if k != "\x1b":
        return k
    seq = ""
    while msvcrt.kbhit() and not (seq[-1:].isalpha() or seq[-1:] == "~"):
        seq += msvcrt.getwch()
    if seq in ("[A", "[B", "[C", "[D"):  # VT 輸入模式下的方向鍵
        return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}[seq]
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
    global paused, zoom, sel_sym
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
    threading.Thread(target=tw_poller, daemon=True).start()
    threading.Thread(target=fugle_worker, daemon=True).start()
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
            # 右側 K 線面板吃掉的寬度要先扣掉，表格才知道自己能用多少
            panel_w = min(KPANEL_MAX, console.width - COL_W - 2)
            if panel_w < KPANEL_MIN:
                panel_w = 0  # 窗格太窄就整個收起來，只留表格
            names = views_for(console.height - 1, console.width - panel_w)
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
            elif isinstance(key, tuple) and (pick := clicked_row(*key[1:], names[idx][1],
                                                              console.width - panel_w)):
                sel_sym = pick  # 點個股那一列就換右邊面板顯示的標的
            elif key in ("up", "down"):
                move_sel(names[idx][1], -1 if key == "up" else 1)
                shown = time.time()  # 正在逐檔看，自動翻頁重新計時
            elif key in ("+", "="):  # = 跟 + 同一顆鍵，不用按 shift
                zoom = (zoom + 1) % len(ZOOMS)
            elif key == "-":
                zoom = (zoom - 1) % len(ZOOMS)
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
                    draw(console, render(names[idx], old_rows, t, panel_w, console.height - 1))
                    time.sleep(1 / 30)
                    if pending := read_key():  # 翻牌中又點了別頁：不等動畫跑完，直接換
                        break
                shown = time.time()
            if not pending:
                draw(console, render(names[idx], panel_w=panel_w, height=console.height - 1))
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
