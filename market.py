"""資料層：報價、日線、籌碼、內外盤的儲存與抓取，交易日曆、資料來源狀態、背景輪詢。
不碰畫面：沒有 rich、沒有顏色。判斷在 signals.py，畫面在 ticker.py；網頁版（ticker-web）也讀這兩層。"""
import json
import os
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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

import yfinance as yf  # noqa: E402  要在換好憑證路徑之後才 import

INDICES = [("^TWII", "加權"), ("^GSPC", "S&P"), ("^IXIC", "Nasdaq"), ("^DJI", "道瓊"),
           ("^SOX", "費半"), ("^N225", "日經"), ("^KS11", "KOSPI"),
           ("TXF", "台指期"), ("EXF", "電子期")]
# Yahoo 沒有台灣期貨，這兩個改抓期交所行情（近月合約）
FUTURES = {"TXF", "EXF"}
INDEX_SYMS = {s for s, _ in INDICES}  # 上面大盤列可以點，點了右邊 K 線改看它
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
# 位階：現價對 20 日均線的乖離率，加 52 週位置。日線一天抓一次，寫成檔案，重開面板不用重抓
DAILY_FILE = Path(__file__).with_name("daily.json")
# 籌碼：外資買賣超連續天數。證交所 T86 收盤後才更新，一小時抓一次就夠
DAILY_BATCH, DAILY_GAP = 25, 2.0   # 日線分批下載的批量與批間隔，一次全丟會被擋
KBARS = 250       # daily.json 每檔存幾根開高低收；約一年。半年線要往前看 120 根，面板最多再畫一百多根
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

quote_at = {}  # symbol -> 最後一次寫進報價的時間，判斷過期用


frame_lock = threading.Lock()  # 組一幀畫面時拿著；整批報價寫入也要拿。一幀裡看到的一定是同一批資料
STAGE = "yahoo"                # Yahoo 那一圈平行抓取的執行緒名稱前綴：它們的寫入先暫存，整圈抓完一次寫入
staged = {}


class Quotes(dict):
    """寫入時順便記時間。報價來源有七八條（Yahoo、MIS、期交所、CNBC、富果…），都經過這裡，不用一個個改。"""
    def __setitem__(self, sym, q):
        if threading.current_thread().name.startswith(STAGE):
            staged[sym] = q  # 8 條執行緒誰先回來誰先寫的話，一頁的報價會在一兩秒內零零落落地跳
            return
        quote_at[sym] = time.time()
        super().__setitem__(sym, q)


def publish(batch):
    """整批報價一次寫進去，跟組畫面互斥：同一頁在同一幀一起跳，不會一半新一半舊。跳價亮燈也一起標。"""
    with frame_lock:
        for sym, q in batch.items():
            quotes[sym] = q
        mark_ticks()


quotes = Quotes()  # symbol -> (price, prev_close)
daily = {}        # symbol -> (ma20, ma60, low52, high52)
daily_date = ""   # 日線統計是哪一天抓的
daily_busy = False  # 背景在抓日線時不要再開一條
miss_at = 0.0       # 上次補抓缺漏日線的時間
MISS_RETRY = 600    # 缺日線的個股多久補抓一次；只抓缺的那幾檔，盤中也不怕被限流
bars = {}         # symbol -> [[開, 高, 低, 收], ...]，最近 KBARS 根，給 K 線面板用
bar_day = {}      # symbol -> bars 最後一根的日期（YYYYMMDD）
vols = {}         # symbol -> [成交量, ...]，跟 bars 一根對一根；另存一份，bars 維持開高低收四欄
live_bar = {}     # symbol -> (交易日, 開, 高, 低, 收)：盤中用即時報價組出今天那根，Yahoo 日線要收盤後才有
flow = {}         # symbol -> [交易日, 外盤量, 內盤量, 上一筆累計量, 上一筆最佳買價, 最佳賣價]：MIS 快照推估的內外盤
flow_exact = {}   # symbol -> (交易日, 外盤量, 內盤量)：富果的真實內外盤，只有 K 線選中那一檔有
chips = {}        # 日期 -> {證券代號: 外資買賣超張數}
chips_at = 0.0
alert_msgs = []   # [(觸發時間, 文字)]，跑馬燈左邊插播
alert_fired = {}  # 規則 -> 觸發日期，同一天只響一次
bell = False      # 有新警示就讓 draw() 響一聲
last_update = 0.0


# ── 資料來源狀態：每個來源最後一次成功／失敗的時間，終端機快捷鍵列與網頁都會顯示 ──
SRC_LIMIT = {"證交所": 30, "期交所": 30, "Yahoo": 120, "富果": 60}  # 該有資料的時段裡，超過幾秒沒成功算「停了」
src_state = {}  # 名稱 -> [最後成功時間, 最後失敗時間, 失敗原因, 最後一次是否成功]


def note_src(name, ok, why=""):
    st = src_state.setdefault(name, [0.0, 0.0, "", True])
    if ok:
        st[0] = time.time()
    else:
        st[1], st[2] = time.time(), str(why)[:80]
    st[3] = ok  # 先後看這個，不比時間：Windows 時間精度約 15ms，同一個時間點分不出先後


def src_status(name, active):
    """ok（正常）／err（上一次失敗）／old（該有資料卻太久沒成功）／idle（還沒抓過）。active：現在應不應該有資料。"""
    ok, err, _, last_ok = src_state.get(name, (0.0, 0.0, "", True))
    if not ok and not err:
        return "idle"
    if not last_ok:
        return "err"
    return "old" if active and time.time() - ok > SRC_LIMIT.get(name, 120) else "ok"


def sources():
    """[(名稱, 狀態, 失敗原因)]。富果沒設金鑰就不列。"""
    tw = session_open("x.TW")
    rows = [("證交所", tw), ("期交所", futures_open()), ("Yahoo", market_hours())] + ([("富果", tw)] if "富果" in src_state else [])
    return [(n, src_status(n, active), src_state.get(n, [0, 0, "", True])[2]) for n, active in rows]


# ── 交易日曆：台股休市日（證交所公布），美股夏令時間 ──
HOLIDAY_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
tw_holidays = set()  # "YYYYMMDD"
holiday_at = 0.0


def holidays_from(rows):
    """證交所的休市日曆（民國年 1150925）→ 西元 YYYYMMDD。名稱有「交易日」的是開始／最後交易日，照常交易；
    「市場無交易，僅辦理結算交割作業」這種沒有盤，算休市。"""
    return {str(int(r["Date"][:3]) + 1911) + r["Date"][3:] for r in rows
            if "交易日" not in r.get("Name", "") and len(r.get("Date", "")) == 7}


def load_holidays():
    global holiday_at
    try:
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        req = urllib.request.Request(HOLIDAY_URL, headers={"User-Agent": "Mozilla/5.0"})
        days = holidays_from(json.load(urllib.request.urlopen(req, timeout=20, context=ctx)))
        if days:
            tw_holidays.clear()
            tw_holidays.update(days)
    except Exception:
        pass  # 抓不到就沿用上次的；第一次就失敗則當成沒有假日（跟以前一樣）
    holiday_at = time.time()


def tw_closed(day=None):
    """台股今天（或指定那天 YYYYMMDD）休市。"""
    return (day or time.strftime("%Y%m%d")) in tw_holidays


def us_open(when=None):
    """美股開盤（紐約 09:30）換成台灣時間是幾點幾分（分鐘數）：夏令時間 21:30（1290）、冬令時間 22:30（1350）。"""
    ny = (when or datetime.now(ZoneInfo("America/New_York"))).astimezone(ZoneInfo("America/New_York"))
    tw = ny.replace(hour=9, minute=30, second=0, microsecond=0).astimezone(ZoneInfo("Asia/Taipei"))
    return tw.hour * 60 + tw.minute


# ── Yahoo 批次查價 ──
YAHOO_QUOTE = "https://query1.finance.yahoo.com/v7/finance/quote"
YAHOO_BATCH = 50


def fetch_yahoo(syms):
    """一次問一批（YAHOO_BATCH 檔一個請求），不再一檔一檔問：一輪從上百個請求變成兩個，比較不會被限流。
    回傳 {代號: 報價}；沒拿到的由呼叫端改問 CNBC，不退回一檔一檔問（那樣只會更容易被限流）。"""
    got, api = {}, yf.data.YfData()
    for i in range(0, len(syms), YAHOO_BATCH):
        try:
            r = api.get_raw_json(YAHOO_QUOTE, params={"symbols": ",".join(syms[i:i + YAHOO_BATCH])})
        except Exception as e:
            note_src("Yahoo", False, type(e).__name__)
            continue
        for x in r.get("quoteResponse", {}).get("result", []):
            price, prev = x.get("regularMarketPrice"), x.get("regularMarketPreviousClose")
            if price is None or not prev:
                continue
            got[x["symbol"]] = (price, prev, x.get("regularMarketVolume"), x.get("averageDailyVolume10Day"),
                                x.get("regularMarketDayLow") or price, x.get("regularMarketDayHigh") or price)
        note_src("Yahoo", True)
    return got


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
    msgs = []
    for i in range(0, len(keys), 50):  # 一次查 50 檔
        url = TWSE_MIS + "|".join(f"{k}.tw" for k in keys[i:i + 50])  # 代號大小寫要照原樣（00631L）
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        msgs += json.load(urllib.request.urlopen(req, timeout=10, context=ctx)).get("msgArray", [])
    with frame_lock:  # 每一批都抓完才一起寫：同一頁的台股在同一幀一起跳，不會前一批先跳、後一批晚一拍
        store_twse(chans, msgs)
        mark_ticks()


def store_twse(chans, msgs):
    """把 MIS 回來的快照寫進報價、今天那根 K 棒與內外盤。呼叫端要拿著 frame_lock。"""
    for r in msgs:
        sym = chans.get(f"{r.get('ex')}_{r.get('c')}")
        if not sym:
            continue
        try:
            # z 是最近成交價；快照剛好落在兩筆成交之間時是 "-"，改用最佳買價
            price = float(r["z"]) if r.get("z", "-") != "-" else float(r["b"].split("_")[0])
            prev = float(r["y"])
        except (KeyError, ValueError, AttributeError):
            continue
        note_flow(sym, r)  # 放在富果判斷前面：選中那檔也要照樣累計，富果斷線時才有推估值可以接手
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
            note_src("期交所", True)
        except Exception as e:
            note_src("期交所", False, type(e).__name__)
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


# 各市場現貨交易時段（台灣時間，開盤分鐘數, 時長分鐘數）；總經、期貨沒有量比。美股開盤時間看夏令時間（window()）
# ponytail: 不管日股午休、美日韓的國定假日（台股看證交所休市日曆），那些日子量比會略偏、列會變暗
SESSIONS = {".TW": (540, 270), ".TWO": (540, 270), ".T": (480, 390), ".KS": (480, 390), "US": (1290, 390)}


def window(suffix):
    """(開盤分鐘數, 時長)：美股依夏令時間算，其他照 SESSIONS。"""
    return (us_open(), 390) if suffix == "US" else SESSIONS[suffix]
VOL_HOT, VOL_LOW = 2.0, 0.5   # 量比 ≥ VOL_HOT 爆量標亮，≤ VOL_LOW 量縮變暗
VOL_WARMUP = 15               # 開盤後前幾分鐘量比失真，先不標亮


def suffix_of(sym):
    """.TW / .TWO / .T / .KS，美股回 "US"，其餘（總經、匯率、期貨）回 None。"""
    return "." + sym.split(".")[-1] if "." in sym else ("US" if sym.isalpha() else None)


def volume_ratio(sym, vol, avg):
    """今日量 ÷（10 日均量 × 已開盤比例）。回傳 (文字, 語意)：volhot 爆量、vollow 量縮、dim 中性；沒有量的標的回傳空字串。"""
    suffix = suffix_of(sym)
    if suffix not in SESSIONS or not vol or not avg:
        return "", ""
    start, length = window(suffix)
    now = time.localtime()
    since = (now.tm_hour * 60 + now.tm_min - start) % 1440
    trading = since < length and now.tm_wday < 5 and not (suffix in (".TW", ".TWO") and tw_closed())
    ratio = vol / (avg * (max(since, 1) / length if trading else 1))  # 盤外 lastVolume 是整日量，不用換算
    if ratio >= VOL_HOT and not (trading and since < VOL_WARMUP):
        style = "volhot"
    else:
        style = "vollow" if ratio <= VOL_LOW else "dim"
    return f"{ratio:.1f}x", style


def session_open(sym):
    """這檔的市場現在有沒有在交易。判斷不了的（總經、匯率、期貨、加密）一律當有，照抓。"""
    suffix = suffix_of(sym)
    if suffix not in SESSIONS:
        return True
    if suffix in (".TW", ".TWO") and tw_closed():
        return False  # 證交所公布的休市日（颱風假臨時停市不在日曆上）
    start, length = window(suffix)
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


def poller():
    global last_update, miss_at
    with ThreadPoolExecutor(8, thread_name_prefix=STAGE) as pool:  # 這些執行緒的寫入會先進 staged
        while True:
            # s 是 None 的是產業分組標題；extra_items 是網頁版各裝置「我的清單」裡的股票
            syms = list(dict.fromkeys(s for rows in [INDICES, *PAGES.values(), extra_items()] for s, _ in rows if s))
            # 收盤的市場價格不會再動，跳過可以少掉夜裡大半的請求，免得被 Yahoo 限流。
            # 還沒抓到過的照抓，不然剛開面板時收盤市場會整片空白
            syms = [s for s in syms if s not in FUTURES and (s in RATES or session_open(s) or s not in quotes)]
            if time.time() - holiday_at > 86400:
                load_holidays()  # 一天更新一次休市日曆
            tw = lambda s: s == "^TWII" or s.endswith((".TW", ".TWO"))
            # 台股即時價由 tw_poller 抓證交所；Yahoo 只補一次 10 日均量（量比要用）
            yahoo = [s for s in syms if s not in RATES and s not in SECTOR_SET
                     and not (tw(s) and len(quotes.get(s, ())) > 3 and quotes[s][3])]
            list(pool.map(fetch, [s for s in syms if s in RATES]))  # 央行利率各有各的網站
            batch = dict(staged)
            staged.clear()
            for s, q in fetch_yahoo(yahoo).items():
                old = quotes.get(s, ())
                batch[s] = (*old[:3], q[3], *old[4:]) if tw(s) and len(old) == 6 else q  # 台股只拿均量，價格留證交所的
            publish(batch)  # 整圈都回來了才一次寫進去，這一圈的報價在同一幀一起跳
            failed = [s for s in yahoo if s not in batch and not tw(s)]  # Yahoo 被限流：整批改問 CNBC，不一檔一檔重試
            if failed:
                try:
                    fetch_cnbc(failed)
                    note_src("CNBC", True)
                except Exception as e:
                    note_src("CNBC", False, type(e).__name__)  # 備援也連不上就維持舊值，下輪再試
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
    """台指期日盤 08:45-13:45、夜盤 15:00-隔天 05:00。台股休市那天日盤、夜盤都沒有（凌晨是前一天夜盤的尾巴，照開）。"""
    now = time.localtime()
    hm = now.tm_hour * 100 + now.tm_min
    if tw_closed() and hm > 500:
        return False
    return (now.tm_wday < 5 and (845 <= hm <= 1345 or hm >= 1500)) or (0 < now.tm_wday < 6 and hm <= 500)


def tw_poller():
    """台股（證交所 MIS）與台指期（期交所）的快迴圈，不跟 Yahoo 那一圈排隊。
    現貨盤中每 TW_SEC 秒抓 MIS；收盤後 MIS 不會再變，每 IDLE_SEC 秒補一次就好。期貨照日夜盤時段。"""
    twse_at = saved_at = 0.0
    while True:
        if session_open("x.TW") or time.time() - twse_at >= IDLE_SEC:
            twse_at = time.time()
            try:
                fetch_twse([s for rows in [INDICES, *PAGES.values(), extra_items()] for s, _ in rows if s])
                note_src("證交所", True)
            except Exception as e:
                note_src("證交所", False, type(e).__name__)  # 連不上就先用 Yahoo 的延遲價
            if time.time() - saved_at >= FLOW_SAVE_SEC:  # 不限盤中：13:30 收盤那筆常在盤後才進來
                saved_at = time.time()
                save_flow()
        for f in FUTURES:
            if futures_open() or f not in quotes:
                fetch(f)
        mark_ticks()
        time.sleep(TW_SEC)


def note_flow(sym, r):
    """用 MIS 前後兩筆快照推估內外盤：這 5 秒多出來的量，成交價在上一筆買賣價中點以上算外盤、以下算內盤
    （碰到賣價就是外盤、碰到買價就是內盤）。漲停沒有賣單一律算外盤，跌停沒有買單一律算內盤。
    ponytail: 5 秒內的多筆成交只能整包歸同一邊，是推估；逐筆精確要訂富果 trades 頻道，免費版只能訂 5 檔"""
    if r.get("z", "-") == "-" or not r.get("v") or not r.get("d"):
        return  # 兩筆成交之間的快照沒有成交價，量留著，等下一筆有價的快照一起分
    z, vol = float(r["z"]), float(r["v"])
    best = lambda key: next((float(p) for p in r.get(key, "").split("_") if p and p != "-" and float(p) > 0), None)
    f = flow.get(sym)
    if not f or f[0] != r["d"]:
        flow[sym] = [r["d"], 0.0, 0.0, vol, best("b"), best("a")]  # 新的一天：從這一筆開始累計，之前的量分不出來
        return
    bid, ask = f[4], f[5]
    if f[3] is not None and vol > f[3]:  # None：剛從存檔讀回來，面板關著那段的量分不出內外盤，丟掉
        outer = ask is None or (bid is not None and z >= (bid + ask) / 2)
        f[1 if outer else 2] += vol - f[3]
    f[3:] = [vol, best("b"), best("a")]


FLOW_FILE = Path(__file__).with_name("flow.json")
FLOW_SAVE_SEC = 30  # 盤中多久存一次內外盤累計；面板重開最多少算這麼久


def save_flow():
    try:
        FLOW_FILE.write_text(json.dumps({"flow": flow, "exact": dict(flow_exact)}), encoding="utf-8")
    except OSError:
        pass


def load_flow():
    """讀回內外盤累計，重開面板不用從零算。上一筆累計量設成 None：關著那段的量分不出內外盤，下一筆只當新起點。
    舊日期的也讀進來，note_flow 看到新的交易日會自己歸零。"""
    try:
        blob = json.loads(FLOW_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for sym, f in blob.get("flow", {}).items():
        f[3] = None
        flow[sym] = f
    flow_exact.update({k: tuple(v) for k, v in blob.get("exact", {}).items()})


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
    vols.update(blob.get("vols", {}))
    # 舊存檔沒記最後一根的日期、成交量，或根數比現在少，當成沒抓過，收盤後重抓一次
    fresh = "bar_day" in blob and "vols" in blob and blob.get("kbars") == KBARS
    daily_date = blob.get("date", "") if fresh else ""


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
    """要抓日線的：清單裡的個股加上大盤列，扣掉期貨、匯率、類股指數，還有產業分組標題（代號是 None）。"""
    return sorted({s for rows in [INDICES, *PAGES.values(), extra_items()] for s, _ in rows
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
                df = data[sym][["Open", "High", "Low", "Close", "Volume"]].dropna()
                vals = [float(v) for v in df["Close"]]
            except (KeyError, TypeError, ValueError):
                continue
            if not vals:
                continue
            if len(vals) >= 20:  # 剛上市的沒有均線可算，但 K 線照畫
                ma60 = sum(vals[-60:]) / 60 if len(vals) >= 60 else None
                daily[sym] = (sum(vals[-20:]) / 20, ma60, min(vals), max(vals))
            # K 線面板要的開高低收；只留最近 KBARS 根，畫得完的部分就夠了
            bars[sym] = [[round(float(x), 4) for x in row[:4]] for row in df.values[-KBARS:]]
            vols[sym] = [float(row[4]) for row in df.values[-KBARS:]]
            bar_day[sym] = df.index[-1].strftime("%Y%m%d")
        time.sleep(DAILY_GAP)
    try:
        DAILY_FILE.write_text(json.dumps({"date": daily_date, "stats": daily, "bars": bars, "bar_day": bar_day, "vols": vols,
                                         "kbars": KBARS},
                                        ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


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
        if style == "volhot" and text and float(text[:-1]) >= VOL_ALERT:
            alert_fired[key] = today
            alert_msgs.append((time.time(), f"{name} 爆量 {text}"))
            bell = True
    alert_msgs[:] = [m for m in alert_msgs if time.time() - m[0] < ALERT_SEC]


FUGLE_FRESH = 10  # 富果多久沒推就讓 MIS 接手（秒）
fugle_at = {}     # symbol -> 富果最後一次推送的時間
watch_sym = None  # K 線面板正在看哪一檔：畫面層每圈寫進來，富果只訂這一檔


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
            note_src("富果", True)
            tot = d.get("total") or {}
            if tot.get("tradeVolumeAtAsk") is not None:  # 富果給的是逐筆算好的真實內外盤，而且從開盤累計
                flow_exact[sym] = (d["date"].replace("-", ""), tot["tradeVolumeAtAsk"], tot.get("tradeVolumeAtBid") or 0)

    ws = websocket.WebSocketApp(WebSocketClient(api_key=key).stock.url, on_open=on_open, on_message=on_message)
    # reconnect：斷線後隔 60 秒再連；官方會擋短時間內大量重連的 IP
    threading.Thread(target=lambda: ws.run_forever(ping_interval=30, reconnect=60), daemon=True).start()
    while True:
        want = watch_sym if watch_sym and watch_sym.endswith((".TW", ".TWO")) else None
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


def volumes(sym):
    """跟 candles() 一根對一根的成交量；今天那根用即時累計量（MIS 已換算成股，跟 Yahoo 同單位）。
    對不齊（舊存檔沒存量）就回空的，量柱留白。"""
    v, lb, data = vols.get(sym) or [], live_bar.get(sym), bars.get(sym) or []
    if len(v) != len(data):
        return []
    if not lb:
        return v
    q = quotes.get(sym) or ()
    today = q[2] if len(q) > 2 and q[2] else 0
    return v[:-1] + [today] if bar_day.get(sym) == lb[0] else v + [today]


def extra_items():
    """網頁版各裝置「我的清單」裡的股票，輪詢與補日線也要抓。網頁版載入時會換掉這個函式；沒裝就是空的。"""
    return []
