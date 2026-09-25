"""終端機行情面板：頂部大盤固定，下方個股每 AUTO_SEC 秒自動翻頁；←/→ 或數字鍵手動切頁，空白鍵暫停翻頁，q 離開。資料源 Yahoo Finance（免費，部分市場有延遲）。"""
import gc
import io
import msvcrt
import os
import re
import socket
import sys
import threading
import time
import unicodedata
from itertools import groupby
from pathlib import Path

import market as mk
from market import *  # noqa: F401,F403
import signals as sg
from signals import *  # noqa: F401,F403
from rich import box
from rich.console import Console, Group
from rich.table import Table
from rich.cells import cell_len
from rich.text import Text

ANSI = re.compile(r"\x1b\[[0-9;]*m")
# 配色取自 TradingView 深色主題：深藍灰底、降飽和的紅與青綠，長時間盯盤不刺眼
BG, FG = "#131722", "#d1d4dc"
PANEL, ZEBRA = "#1a1e2b", "#1e2230"  # 表格、K 線各鋪一層面板底色；表格隔列再亮一階（斑馬紋）代替列間細線
# 底色由程式自己塗：每一格帶 BG，行尾清行前也先切到 BG（清行會用目前底色填滿）。
# 原本用 OSC 11 換窗格預設底色，在使用者的 Windows Terminal 上沒生效（整片還是黑的），改成自己塗
BG_SGR = "\x1b[48;2;{};{};{}m".format(*(int(BG[i:i + 2], 16) for i in (1, 3, 5)))
UP, DOWN = "#ef5350", "#26a69a"  # 台灣習慣紅漲綠跌，美式就對調。不加粗：TradingView 的數字是一般字重
FLAT, NAME, SYMBOL, HEADER, TAB = "#9598a1", "bold #e8eaef", "#787b86", "#787b86", "bold #ffffff on #2962ff"
RULE = "#2a2e39"  # 表頭下方細線顏色
SEL, SEL_MARK = "on #1e2a4a", "bold #2962ff"  # 選中那一列：暗藍底加左邊一條亮藍線，右邊 K 線就是這一檔
RULE_BAR = "#363a45"  # 今日區間條的線
# 今日區間條風格：grad 漸層填色（預設，帶量價對比）/ dash 線加 ● / track 點線 / fill 實心
# / light 細底線 / tick 兩端界線
RANGE_STYLE = "grad"
# grad 的量價配色：爆量用亮色，量縮轉暗，價方向決定紅綠
HOT_OF = {UP: "#ff6f6c", DOWN: "#3fd1c2"}
DIM_OF = {UP: "#6e2a2d", DOWN: "#1c5a55", FLAT: "#4a4e59"}
VOL_HOT_STYLE, VOL_LOW_STYLE = "bold #ffca28", "#50535e"  # 爆量用亮黃字；底色只留給警示與跳價閃燈，其他訊號同時亮才不會一片花
TICK_UP, TICK_DOWN = "bold #ffffff on #ef5350", "bold #ffffff on #26a69a"  # 價格跳動時整格亮一下，顏色跟漲跌一致
TICK_SEC = 0.8  # 亮燈持續秒數；主迴圈 20fps 重繪，這段時間內都看得到
FLIPPING = "bold #b2b5be"  # 翻牌中的字用亮灰，翻的時候看得出在動、又不搶漲跌色的戲
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
MARQUEE_GAP = "    ·    "  # 捲動內容首尾之間的間隔，接回去才看得出斷點
MARQUEE_MIN = 120    # 窄於這個寬度就不放固定段，整條都給捲動
MARQUEE_FIXED = [("DX-Y.NYB", "DXY"), ("BZ=F", "Brent"), ("GC=F", "Gold"),
                 ("^VIX", "VIX"), ("^TNX", "10Y")]
AUTO_SEC = 20             # 自動翻頁間隔；手動切頁會重新計時
TAB_GAP = " " * 4          # 頁籤之間的空白
INDEX_SWAP_SEC = 4        # 大盤第二行在幅度 / 漲跌點數之間輪流切換的秒數
DEV_HOT = "bold #ff9800"   # 追高用橘字，跟爆量的黃字、漲跌的紅綠都分得開
DEV_COLD = "bold #42a5f5"  # 回檔用藍字
ALERT_STYLE = "bold #ffffff on #ef5350"
STALE = "#4a4e59"


def compose(*args, **kwargs):
    """組一幀畫面。拿著 frame_lock，組的途中背景不能寫報價，整幀用的是同一批資料。"""
    with frame_lock:
        return render(*args, **kwargs)
zoom = 0          # ZOOMS 的索引，+ / - 切換 K 棒寬度
DEFAULT_SYM = "^TWII"  # 還沒點任何個股時，右側 K 線看加權指數
sel_sym = DEFAULT_SYM  # 右側 K 線面板顯示哪一檔
paused = False  # 空白鍵暫停自動翻頁，想盯著某一頁看的時候用


def tick_style(sym):
    """還在亮燈時間內就回傳反白樣式，否則 None 交給原本的漲跌色。"""
    tk = tick_at.get(sym)
    if not tk or time.time() - tk[0] >= TICK_SEC:
        return None
    return TICK_UP if tk[1] > 0 else TICK_DOWN


ROW_LINES = 1   # 個股表每列幾行。走勢圖搬走後一列只要一行，同樣高度能多看一倍的檔數
RANGE_W = 16  # 今日區間條與類股強弱條的寬度（字數）
HEAT_FULL = 0.02     # 類股強弱條滿格的漲跌幅；類股指數一天動 2% 已經是很大的輪動


def flow_bar(sym, dim=False):
    """多空比條：左邊外盤 %（紅，主動買），右邊內盤 %（綠，主動賣），中間紅綠色塊照比例切。寬度跟區間條一樣。"""
    ratio = flow_ratio(sym)
    if ratio is None:
        return Text("累計中", STALE)
    left, right = f"{round(ratio * 100)}", f"{100 - round(ratio * 100)}"
    width = RANGE_W - len(left) - len(right) - 2  # 兩邊數字各留一格空白
    red = round(ratio * width)
    up, down = (STALE, STALE) if dim else (UP, DOWN)
    return (Text(left + " ", up).append("█" * red, up.replace("bold ", ""))
            .append("█" * (width - red), down.replace("bold ", "")).append(" " + right, down))


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



def rows_of(items, hold=False):
    """表格每一列。判斷交給 signals.row()，這裡只把語意換成顏色、畫區間條。
    hold=True 時漲跌欄改成今日損益（漲跌×股數），幅度欄改成總報酬（對成本）。"""
    out = []
    for sym, name in items:
        if sym is None:  # 分組標題列：只有名稱，沒有報價（名稱欄是純文字，不吃 markup，靠符號區隔）
            out.append((f"▸ {name}", "", "", "", "", "", "", None, "", "", "", "", "", "", "", "", ""))
            continue
        d = sg.row(sym, name, hold)
        # 列格式：(名稱, 代號, 區間條, 量比, 價格, 漲跌, 幅度, 漲跌色, 量比色,
        #        乖離, 乖離色, 52週位置, 位置色, 外資連續天數, 籌碼色, 型態, 型態色)
        if d.get("empty"):
            out.append((name, sym, "", "", "…", "", "", None, "", "", "", "", "", "", "", "", ""))
            continue
        if d["sector"]:
            rb = heat_bar(d["chg"] / d["prev"])  # 類股沒有個股那種今日區間，這一欄改放強弱條
        else:
            rb = range_bar(d["price"], d["low"], d["high"]) if d["low"] is not None else ""
        # 顯示舊數字比留白危險：過期的列整列壓暗，一眼看出這檔斷線
        color, vr_s, dev_s, pos_s, chip_s, pat_s = (
            [STALE] * 6 if d["stale"] else
            [TONE.get(d[k], "") for k in ("tone", "vr_tone", "bias_tone", "pos_tone", "chip_tone", "pat_tone")])
        out.append((name, sym, rb, d["vr"], d["price_txt"], d["chg_txt"], d["pct_txt"], color, vr_s,
                    d["bias"], dev_s, d["pos"], pos_s, d["chip"], chip_s, d["pat"], pat_s))
    return out


# 欄寬全部寫死，版面不隨內容伸縮；價格三欄置中並靠左邊的籌碼欄，右邊剩下的寬度留給最後那個空欄
NAME_W, SYM_W, PRICE_W, CHG_W, PCT_W = 13, 9, 9, 9, 8  # 名稱含前導空白，實際放得下 12 格
# (標題, 對齊, 寬度, 收合順序)。窗格不夠寬時照收合順序 1、2、3… 藏欄位，0 是永遠留著
# 型態欄放四個中文字，8 格；最後還有一個不在這裡的留白欄，ratio 會把剩下的空間全給它
COLS = ((" 名稱", "left", NAME_W, 0),  # 名稱帶一格前導空白，不然會貼齊窗格邊
        ("代號", "left", SYM_W, 0), ("今日區間", "center", RANGE_W, 5), ("量比", "center", 5, 6),
        ("乖離", "center", 7, 4), ("年區間", "center", 6, 2), ("外資", "center", 5, 3),
        ("價格", "center", PRICE_W, 0), ("漲跌", "center", CHG_W, 0), ("幅度", "center", PCT_W, 0),
        ("型態", "center", 8, 1), ("訊號", "center", 5, 0))


def table_w(keep):
    """這幾欄排出來要多寬：每欄右邊留白 1 格、欄間分隔 1 格（SIMPLE_HEAD 的直線是空白，照樣佔位），再加留白欄的 1 格。"""
    return sum(COLS[i][2] + 2 for i in keep) + 1


COL_W = table_w(range(len(COLS)))  # 分頁、K 線面板用的版面寬度：放滿全部欄位要多寬（125）


def keep_cols(width):
    """窗格窄於 COL_W 時，照收合順序藏欄位，藏到塞得下為止；名稱、代號、價格三欄永遠留著。"""
    keep = list(range(len(COLS)))
    for i in sorted((i for i, c in enumerate(COLS) if c[3]), key=lambda i: COLS[i][3]):
        if table_w(keep) <= width:
            break
        keep.remove(i)
    return keep


# 右側 K 線面板：選中那一檔的日 K。K 棒顏色代表它站在哪條均線上；月線用 · 描在 K 棒之間的空白格，
# 終端機一格只能放一個字元，壓到 K 棒的地方讓 K 棒優先。K 線下面兩行是成交量柱
KPANEL_MIN, KPANEL_MAX = 56, 120  # 面板寬度上下限；窗格不夠寬就整個收起來
VOL_ROWS = 2          # 成交量柱佔幾行；每行 8 階，兩行 16 階
KPANEL_FIXED = 4 + PAT_MAX + VOL_ROWS  # K 線圖以外固定佔幾行：標題、量柱、均線列與上下空行、型態說明
MA_GAP = 5            # 均線列各項之間空幾格
# 均線軌跡：(天數, 標籤, 顏色)。月線紫、季線淺藍、半年線橘，跟紅綠 K 棒都分得開。
# 疊在同一格時先畫的贏，所以短的排前面
MA_LINES = ((20, "月線", "#ab47bc"), (60, "季線", "#2962ff"), (120, "半年", "#ff9800"))
# 判斷層回傳的語意 → 終端機顏色。網頁版有自己的一份
TONE = {"up": UP, "down": DOWN, "flat": FLAT, "hot": DEV_HOT, "cold": DEV_COLD, "volhot": VOL_HOT_STYLE,
        "vollow": VOL_LOW_STYLE, "dim": SYMBOL, "zone": MA_LINES[2][2], "": ""}
VOL_UP, VOL_DOWN = "#6e2a2d", "#1c5a55"  # 量柱比 K 棒暗一階，才不會搶戲
VOL_BLOCKS = " ▁▂▃▄▅▆▇█"
ZOOMS = ((2, 1), (1, 1), (3, 2))  # (每根佔幾格, 棒身幾格)，+ / - 切換
BAR_UP, BAR_MID, BAR_DOWN = UP, "#b23c3a", DOWN  # 站上季線／只站上月線／跌破月線
# 上下半格字元：一格塞兩個價格層級，垂直解析度就是行數的兩倍
CELLS = {0: " ", 1: "╵", 2: "╷", 3: "│", 4: "▀", 5: "▀", 6: "▀", 7: "▀",
         8: "▄", 9: "▄", 10: "▄", 11: "▄", 12: "█", 13: "█", 14: "█", 15: "█"}
BITS = {" ": 0, "╵": 1, "╷": 2, "│": 3, "▀": 4, "▄": 8, "█": 12}


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
    for k, _, color in MA_LINES:  # 均線另外跑，等 K 棒都畫好了才知道哪些格子是空的
        for i in range(n):
            j = len(closes) - n + i
            if j + 1 < k:
                continue  # 前面不滿 k 根，算出來的不是真的均線，不畫
            avg = sum(closes[j - k + 1:j + 1]) / k
            if not lo <= avg <= hi:
                continue  # 均線跑出這張圖的價格範圍就不畫，貼邊畫會誤導
            r = rows - 1 - lvl(avg) // 2
            for x in range(i * step, min(i * step + step, width)):
                if grid[r][x] == " ":
                    grid[r][x], style[r][x] = "·", color
    return ["".join(r) for r in grid], style, n


def vol_rows(sym, width, n):
    """K 線下面的量柱：跟 K 棒同一格起、同樣寬，高度對畫面上這 n 根的最大量。紅 K 用紅柱、黑 K 用綠柱。"""
    step, body = ZOOMS[zoom]
    v, data = volumes(sym)[-n:] if n else [], (candles(sym) or [])[-n:] if n else []
    grid = [[" "] * width for _ in range(VOL_ROWS)]
    style = [[""] * width for _ in range(VOL_ROWS)]
    top = max(v, default=0)
    if not top or len(v) != len(data):
        return ["".join(r) for r in grid], style
    per = len(VOL_BLOCKS) - 1
    for i, (amount, (o, _, _, c)) in enumerate(zip(v, data)):
        h = round(amount / top * per * VOL_ROWS)
        for k in range(VOL_ROWS):  # k=0 是最下面那行
            ch = VOL_BLOCKS[min(max(h - k * per, 0), per)]
            for x in range(i * step, min(i * step + body, width)):
                grid[VOL_ROWS - 1 - k][x], style[VOL_ROWS - 1 - k][x] = ch, VOL_UP if c >= o else VOL_DOWN
    return ["".join(r) for r in grid], style
FLASH_SEC = 0.25               # 閃燈半週期：一秒亮暗各兩次
# 燈用彩色 emoji 圓點：文字的 ● 只是字形上色，看起來空心；emoji 是整顆填滿的顏色。各佔兩格
ZONE_ON, FLOW_ON = "🟠", "🔴"
# 熄燈：跟網頁一樣是面板色系的暗灰點（emoji ⚫ 是近乎全黑的圓，在藍灰底上像破洞）。
# 後面補一格空白，寬度跟 emoji 一樣兩格，亮暗切換時整欄不會左右跳
LAMP_OFF, LAMP_OFF_STYLE = "● ", "#2a2e39"


def buy_light(sym):
    """訊號燈：左邊橘燈＝弱勢提醒（剛跌破半年線，跟 K 線圖上的半年線同色），恆亮不閃；
    右邊紅燈＝買點（拉回流程成立），閃爍。只有買點會閃，警訊不會被看成買點。"""
    t = Text()
    if not sym or sym in SECTOR_SET:
        return t
    zone, flow = sg.buy_signal(sym)
    on = int(time.time() / FLASH_SEC) % 2 == 0
    # 亮燈只放 emoji 圓點本身，不塗底色：終端機一格只能塗方形，試過塗光暈都有方塊感
    t.append(ZONE_ON if zone else LAMP_OFF, None if zone else LAMP_OFF_STYLE)  # 弱勢提醒恆亮，不閃
    t.append(" ")
    t.append(FLOW_ON if flow and on else LAMP_OFF, None if flow and on else LAMP_OFF_STYLE)
    return t


def kline_panel(width, rows):
    """面板整體：標題、K 線、均線數值、型態說明。"""
    q = quotes.get(sel_sym) if sel_sym else None
    if not sel_sym or not q or not q[1] or sel_sym not in bars:
        hint = ("期交所只有即時價，沒有日 K；要看大盤走勢請點「加權」" if sel_sym in FUTURES
                else "還在抓日線資料" if sel_sym else "點左邊任一列看它的日 K")
        return Group(Text("│ ", RULE).append(hint, SYMBOL))
    price, prev = q[0], q[1]
    chg = price - prev
    color = UP if chg > 0 else DOWN if chg < 0 else FLAT
    name = next((n for items in [INDICES, *mk.PAGES.values()] for s, n in items if s == sel_sym), sel_sym)
    lines, styles, n = kline(sel_sym, width - 2, max(3, rows - KPANEL_FIXED))  # -2：分隔線與左留白
    head = Text(" ")
    head.append(f"{name} ", NAME)
    head.append(f"{sel_sym}   ", SYMBOL)
    head.append(f"{price:,.2f}  ", color)
    head.append(f"{chg:+,.2f}  {chg / prev:+.2%}", color)
    head.append(f"   日K {n} 根  ×{ZOOMS[zoom][0]}", SYMBOL)
    out = [head]
    vl, vs = vol_rows(sel_sym, width - 2, n)
    for row, st in zip(lines + vl, styles + vs):
        # 同色相連的格子併成一段再上色。一格一段的話每格都帶一組色碼，K 線面板一幀就四十幾 KB
        # 空格沒有底色、看不出前景色，就沿用前一格的顏色：「█ █ █」這種 K 棒、量柱中間夾空格的，
        # 本來要切成五段、每段重設一次色碼，併起來變一段。換股時要轉的段數跟送出的位元組都少一大半
        t, carry, cells = Text(" "), None, []
        for ch, s in zip(row, st):
            carry = s if ch != " " else carry
            cells.append((ch, carry))
        for s, run in groupby(cells, key=lambda z: z[1]):
            t.append("".join(ch for ch, _ in run), s or None)
        out.append(t)
    data = candles(sel_sym)
    closes = [b[3] for b in data]
    view = data[-n:] or data
    # 均線列：每一項是 (標籤, 數值, 標籤色)。標籤顏色兼圖例：跟圖上同色的點線是同一條
    items = [(label, f"{sum(closes[-k:]) / k:,.0f}", color)
             for k, label, color in ((5, "5日", SYMBOL), *MA_LINES) if len(closes) >= k]
    items += [("高", f"{max(b[1] for b in view):,.0f}", SYMBOL), ("低", f"{min(b[2] for b in view):,.0f}", SYMBOL)]
    for gap in (MA_GAP, 2):  # 寬的間隔放不下就縮成兩格，再放不下就從右邊的高低點開始裁
        ma = Text(no_wrap=True, overflow="crop")
        for i, (label, value, color) in enumerate(items):
            ma.append(" " * gap if i else "").append(f"{label} ", color).append(value, NAME)
        if cell_len(ma.plain) <= width - 1:
            break
    out += [Text(""), ma, Text("")]  # 均線列上下各空一行，跟 K 線、型態說明隔開
    why = buy_reason(sel_sym)  # 燈亮的原因排第一行，擠掉的是最不重要的均線排列
    pats = (([why] if why else []) + patterns(sel_sym))[:PAT_MAX]
    for label, note, st in pats:
        out.append(Text(f"{label} ", TONE.get(st, st)).append(note, SYMBOL))
    out.extend(Text("") for _ in range(PAT_MAX - len(pats)))  # 補空行，K 線高度才不會跟著型態數跳
    body = len(out) - PAT_MAX - 2  # 均線列、型態說明置中；標題跟 K 線照舊靠左
    out[body:] = [Text(" " * max(0, (width - 1 - cell_len(t.plain)) // 2)).append_text(t) for t in out[body:]]
    return Group(*(Text(" ").append_text(t) for t in out))  # 左邊留一格；跟表格的分隔改由面板底色與中間的縫負責


def sel_items(items):
    """確保 sel_sym 落在這一頁裡；不在（換頁、剛開）就回到加權指數，不再自動挑這一頁的第一檔。"""
    global sel_sym
    syms = [s for s, _ in items if s]  # 產業分組標題不能被選
    if sel_sym not in syms and sel_sym not in INDEX_SYMS:  # 點了上面的大盤就留著，換頁也不換掉
        sel_sym = DEFAULT_SYM
    return syms


def move_sel(items, step):
    global sel_sym
    syms = sel_items(items)
    if syms:  # 正在看大盤時按上下鍵，回到這一頁的第一檔
        sel_sym = syms[(syms.index(sel_sym) + step) % len(syms)] if sel_sym in syms else syms[0]


def clicked_index(x, y):
    """點到上面大盤列的哪一個：跟頁籤一樣從上一幀畫面反查名稱位置，取水平位置最近的。"""
    if y not in (1, 2) or not screen:  # 大盤列佔最上面兩行：名稱價格、漲跌
        return None
    line = screen[0]
    hits = [(s, cell_len(line[:line.index(n)]) + cell_len(n) / 2) for s, n in INDICES if n in line]
    return min(hits, key=lambda z: abs(z[1] - x))[0] if hits else None


def clicked_row(x, y, items, limit=None):
    """點到哪一檔：從上一幀的純文字找代號。表格並排好幾欄時先看點在哪一欄，只在那一欄裡找，
    點在一列的右半邊（買點燈、幅度）才不會被隔壁欄比較近的代號搶走。limit 是表格右界，點在右側 K 線圖上不改選股。"""
    if limit and x > limit:
        return None
    if not 0 < y <= len(screen):
        return None
    line = screen[y - 1]
    hits = []
    for s, _ in items:
        # 代號前後不能黏著英數或點，免得 2330.TW 撞到 12330.TW、6488.TW 撞到 6488.TWO
        hit = s and re.search(rf"(?<![\w.]){re.escape(s)}(?![\w.])", line)
        if hit:
            hits.append((s, cell_len(line[:hit.start()]) + cell_len(s) / 2))
    cols, area = table_layout
    col_w = area / max(1, cols)
    same = [h for h in hits if int(h[1] // col_w) == int((x - 1) // col_w)]  # x 從 1 起算，換成從 0
    return min(same or hits, key=lambda z: abs(z[1] - x))[0] if hits else None



def views_for(height, width=COL_W):
    """依窗格高度、寬度把每個市場切成數個子頁：高度決定一欄幾檔，寬度決定並排幾欄。"""
    rows = max(1, (height - FIXED_LINES) // ROW_LINES)
    per = rows * max(1, width // COL_W)
    out = []
    for market, items in mk.PAGES.items():
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
        labels = [label if p == market else p for p in mk.PAGES]
        plain = (" " * gap).join(" " * pad + x + " " * pad for x in labels)
        if cell_len(plain) <= Console().width:
            break
    tabs = (" " * gap).join(
        f"[{TAB}]{' ' * pad}{label}{' ' * pad}[/]" if p == market else f"[{SYMBOL}]{' ' * pad}{p}{' ' * pad}[/]"
        for p in mk.PAGES)
    age = int(time.time() - mk.last_update) if mk.last_update else "-"
    hold = market == "庫存"
    new_rows = rows_of(items, hold)
    n = max(len(new_rows), len(old_rows or []))
    # 第 i 檔放在第 i // per_col 欄；翻牌時新舊頁的同一格位置對得上
    spans = [range(c, min(c + per_col, n)) for c in range(0, n, per_col)] or [range(0)]
    table_layout[:] = [len(spans), Console().width - panel_w]  # 點擊時用來判斷點在哪一欄
    keep = keep_cols((Console().width - panel_w) // len(spans))
    tables = [stock_table(hold, new_rows, old_rows, t, span, keep) for span in spans]
    grid = Table.grid(expand=True, padding=(0, 2))
    grid.title = f"{tabs}{TAB_GAP}[{SYMBOL}]· {age}s{' ⏸' if paused else ''}[/]"
    for _ in tables:
        grid.add_column(ratio=1, style=f"on {PANEL}")  # 表格那一塊的面板底色
    grid.add_row(*tables)
    if not panel_w:
        return Group(index_bar(), grid)
    # 表格、K 線各一塊面板底色，中間留一格頁面底色當縫；那一格從 K 線寬度扣，表格寬度不變
    outer = Table.grid(expand=True)
    outer.add_column()
    outer.add_column(width=1)
    outer.add_column(width=panel_w - 1, style=f"on {PANEL}")
    outer.add_row(grid, "", kline_panel(panel_w - 1, max(4, height - 2)))  # -2：大盤列佔兩行
    return Group(index_bar(), outer)


def stock_table(hold, new_rows, old_rows, t, span, keep=range(len(COLS))):
    """一欄個股表，放 span 範圍內的列，只畫 keep 裡的欄位。"""
    # padding 只留右邊那一格：預設左右各一格，11 欄就吃掉 22 格，欄跟欄之間會散開
    table = Table(box=box.SIMPLE_HEAD, border_style=RULE, show_edge=False, expand=True,
                  header_style=HEADER, padding=(0, 1, 0, 0))
    # 欄寬寫死，版面只看視窗寬度、不看內容，翻牌時才不會伸縮
    titles = {8: "今日損益", 9: "總報酬"} if hold else {}
    tw = {shows_flow(new_rows[i][1]) for i in span if i < len(new_rows) and new_rows[i][1]}
    titles[2] = "多空比" if tw == {True} else "今日區間" if tw == {False} else "多空／區間"  # 這一欄放什麼看這一頁是不是台股
    for i in keep:
        col, just, w, _ = COLS[i]
        table.add_column(Text(titles.get(i, col), justify=just), justify=just, width=w, no_wrap=True)  # 標題跟內容同邊對齊
    table.add_column("", ratio=1, no_wrap=True)  # 最後這欄只是留白，之後要加東西就放這裡
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
        if shows_flow(row[1]):  # 台股盤中這一欄改放內外盤比；收盤後、其他市場照舊畫今日區間
            bar = flow_bar(row[1], row[7] == STALE)
        if old_rows is None:
            dim = row[7] == STALE  # rows_of 已經把過期那列的樣式換成 STALE，名稱也跟著暗
            cells = [Text(" " + row[0], STALE if dim else NAME), Text(row[1], STALE if dim else SYMBOL), bar,
                     Text(row[3], row[8]), Text(row[9], row[10]), Text(row[11], row[12]), Text(row[13], row[14]),
                     Text(row[4], tick_style(row[1]) or row[7] or ""),
                     *(Text(c, row[7] or "") for c in row[5:7]), Text(row[15], row[16])]
        else:
            old = old_rows[i] if i < len(old_rows) else blank
            flips = [Text(solari(o, c, t, i * ROW_DELAY), FLIPPING) for o, c in zip(old[:7], row[:7])]
            flips[2] = bar  # 區間條是線條符號，不進字輪，直接換新
            flips[0] = Text(" " + flips[0].plain, FLIPPING)  # 翻牌中也要留著那一格
            # 位階、籌碼是每天才變一次的數字，不進字輪，跟區間條一樣直接換
            extra = [Text(row[9], row[10]), Text(row[11], row[12]), Text(row[13], row[14])]
            cells = [*flips[:4], *extra, *flips[4:], Text(row[15], row[16])]
        cells.append(buy_light(row[1]))  # 買點燈不進字輪，翻牌中也照樣亮
        picked = row[1] and row[1] == sel_sym
        if picked:  # 名稱前面那格留白換成亮藍線；翻牌中也照樣標，才看得出選到哪一檔
            cells[0] = Text("▌", SEL_MARK).append_text(cells[0][1:])
        # 選中那列用選取色；其他隔列亮一階（斑馬紋），終端機畫不出列間細線，這是不多佔行的替代
        table.add_row(*(cells[k] for k in keep), Text(""), style=SEL if picked else f"on {ZEBRA}" if i % 2 else None)
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
    for name, sym, _, _, price, chg, pct, color, *_ in rows_of(INDICES):
        bar.add_column(justify="center")
        if len(price) > 8:
            price = price.rsplit(".", 1)[0]  # 大盤點數去小數，窄窗格才放得下 9 個
        if len(chg) > 7:
            chg = chg.rsplit(".", 1)[0]
        label = f"[{SEL_MARK} {SEL}]{name}[/]" if sym == sel_sym else f"[{NAME}]{name}[/]"  # 右邊 K 線正在看這個大盤
        cells.append(f"{label} [{FG}]{price}[/]\n[{color or SYMBOL}]{(chg if show_chg else pct) or '…'}[/]")
    bar.add_row(*cells)
    return bar


def reload_if_changed(mtime):
    """portfolio.json 有變就重讀；新代號馬上抓一次，不用等下一輪輪詢。名單放在資料層（mk.PAGES），網頁版讀的也是那一份。"""
    try:
        now = mk.PORTFOLIO.stat().st_mtime if mk.PORTFOLIO.exists() else 0
        if now == mtime:
            return mtime
        mk.HOLD, mk.PAGES, mk.ALERTS = mk.load_portfolio()
    except (OSError, ValueError, KeyError):
        return mtime  # 檔案寫到一半或格式錯：保留舊名單，下圈再試
    new = [s for rows in mk.PAGES.values() for s, _ in rows if s and s not in quotes]
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


SEQ_WAIT = 0.05  # 跳脫序列後半段最多等多久
FLIP_FAST = 3    # 手動換頁時翻牌動畫快幾倍（2 秒 → 約 0.7 秒）


def wait_input(sec):
    """睡 sec 秒，但有按鍵或點擊進來就馬上醒。原本整段睡死，點擊最多要等一整幀（50ms）才開始處理。"""
    end = time.time() + sec
    while time.time() < end and not msvcrt.kbhit():
        time.sleep(0.002)


def read_key():
    """回傳 "q"、數字、"left"/"right"，或滑鼠點擊 ("click", 欄, 列)（從 1 起算）。"""
    if not msvcrt.kbhit():
        return None
    k = msvcrt.getwch()
    if k in ("\x00", "\xe0"):
        return {"K": "left", "M": "right", "H": "up", "P": "down"}.get(msvcrt.getwch())
    if k != "\x1b":
        return k
    # 整串收完才解析：滑鼠序列常常分批到，讀到一半就停的話，剩下的 "52;12M" 下一幀會被當成按鍵
    # （數字鍵＝跳頁），點一下就莫名換頁。最多等 SEQ_WAIT 秒讓後半段到齊
    seq, deadline = "", time.time() + SEQ_WAIT
    while not (seq[-1:].isalpha() or seq[-1:] == "~"):
        if msvcrt.kbhit():
            seq += msvcrt.getwch()
        elif time.time() > deadline:
            break  # 真的只按了 Esc，或序列斷掉：丟掉
        else:
            time.sleep(0.001)
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
    if not all(p in line for p in mk.PAGES):  # 不是頁籤那一列
        return None
    # 點在頁籤列上就算離最近的頁籤：頁籤之間沒有死角，點偏一點也會落到最近那個
    centers = {p: cell_len(line[:line.index(p)]) + 1 + cell_len(p) / 2 for p in mk.PAGES}
    first, last = min(centers.values()), max(centers.values())
    reach = (last - first) / max(1, len(mk.PAGES) - 1)  # 最左、最右的頁籤外側也給一個頁籤間距
    if not first - reach <= x <= last + reach:
        return None
    return min(centers, key=lambda p: abs(centers[p] - x))


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


SRC_COLOR = {"ok": "#66bb6a", "old": "#ffca28", "err": "#ef5350", "idle": "#50535e"}
FOOTER_LINES = 2  # 底部固定兩行：快捷鍵提示列、跑馬燈。表格與 K 線能用的高度要先扣掉
KEY = "bold #d1d4dc on #2a2e39"  # 快捷鍵鍵帽：比底色亮一階的方塊，說明文字用灰字


def fkeys():
    """Bloomberg 那種底部功能鍵列：按鍵反白、說明灰字。空白鍵的說明跟著暫停狀態換。"""
    t = Text(" ")
    for k, label in ((f"1-{len(mk.PAGES)}", "分頁"), ("←→", "換頁"), ("↑↓", "選股"), ("點擊", "看 K 線"),
                     ("空白", "繼續輪動" if paused else "暫停輪動"), ("+−", "K 線縮放"), ("q", "離開")):
        t.append(f" {k} ", KEY).append(f" {label}    ", SYMBOL)
    t.append(" 資料 ", SYMBOL)
    for name, status, _ in sources():  # 綠＝正常、黃＝該有資料卻停了、紅＝上一次失敗、灰＝還沒抓
        t.append("●", SRC_COLOR[status]).append(f"{name}  ", SYMBOL)
    return t


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
drawn = []       # 上一幀每一行實際送出去的內容（含色碼），draw() 比對用
drawn_size = []  # 上一幀的 [寬, 高]；變了就整頁重畫
drawn_at = [0.0]  # 上次整頁重畫的時間
FULL_SEC = 10     # 至少每幾秒整頁重畫一次：畫面被別的東西弄亂（終端機重排、有程式印字進來）最多亂這麼久
table_layout = [1, COL_W]  # [表格並排幾欄, 表格區總寬]，render 每幀更新，clicked_row 用


def paint(line):
    """整頁底色：行首先切到 BG，每次 rich 重設樣式（\x1b[0m）之後再切回來。rich 沒上色的字就落在 BG 上；
    面板、斑馬紋、選取那些自己帶底色的段落照舊。直接改字串，不讓 rich 每一段都多合併一層樣式（那樣每幀多 6ms）。"""
    return BG_SGR + RESET_THEN_TEXT.sub("\x1b[0m" + BG_SGR, line)


RESET_THEN_TEXT = re.compile(r"\x1b\[0m(?!\x1b\[)")  # 重設後緊接著又是色碼的，那段自己會設，不用補 BG


def draw(console, renderable):
    """游標回左上角，整頁畫滿窗格（底部留一行），最後一行不換行，所以畫面不會捲動。
    不用 rich Live：alt screen 從 hook／重開窗格時偶爾整片空白；原地模式滿高時每次重畫都會往下捲。"""
    width, height = console.size
    # 底部兩行固定：快捷鍵列、跑馬燈。跑馬燈那行不寫滿，寫進右下角那一格終端機就捲一行（畫面上下抖動）
    height -= FOOTER_LINES
    buf = Console(file=io.StringIO(), width=width, height=height, force_terminal=True,
                  color_system="truecolor", legacy_windows=False, no_color=False)
    buf.print(renderable, crop=True)
    lines = [paint(line) for line in buf.file.getvalue().split("\n")[:height]]
    lines += [""] * (height - len(lines))  # 補滿：換到比較短的頁面時，下面那幾行舊字也要清掉
    buf = Console(file=io.StringIO(), width=width, force_terminal=True,
                  color_system="truecolor", legacy_windows=False, no_color=False)
    buf.print(fkeys(), end="", crop=True, no_wrap=True)
    lines.append(paint(buf.file.getvalue()))  # 快捷鍵列當成最後一行，一起比對、沒變就不重送
    screen[:] = [ANSI.sub("", line) for line in lines]
    # \x1b[?2026h/l：同步更新，終端機等整幀寫完才換上，不會畫一半就顯示（撕裂）
    bar = Console(file=io.StringIO(), width=width, force_terminal=True,
                  color_system="truecolor", legacy_windows=False, no_color=False)
    bar.print(marquee(width - 1), end="", crop=True, no_wrap=True)  # -1：不碰右下角那一格，碰了畫面會捲
    # 只重寫跟上一幀不一樣的行。整頁每幀重送的話一秒二十幀、每幀幾十 KB，終端機消化不了就會 lag。
    # 視窗大小一變就整頁重畫，順便清掉縮小後殘留的舊字
    full = [width, height] != drawn_size or time.time() - drawn_at[0] >= FULL_SEC
    if full:
        drawn_size[:] = [width, height]
        drawn_at[0] = time.time()
    # 行尾先切到 BG 再清行：清行會用目前的底色填滿，沒切的話右邊留白是終端機的黑
    out = "".join(f"\x1b[{i + 1};1H{line}\x1b[0m{BG_SGR}\x1b[K" for i, line in enumerate(lines)
                  if full or i >= len(drawn) or drawn[i] != line)
    drawn[:] = lines
    console.file.write("\x1b[?2026h" + (BG_SGR + "\x1b[2J" if full else "") + out
                       + f"\x1b[{height + FOOTER_LINES};1H" + paint(bar.file.getvalue()) + "\x1b[0m" + BG_SGR + "\x1b[K\x1b[0m"
                       + "\x1b[?2026l")
    if mk.bell:
        console.file.write(chr(7))  # 終端機響一聲，沒盯著面板也知道有警示
        mk.bell = False
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


# ── 網頁版（另一個 repo：ticker-web）──────────────────────────────────────
# 旁邊有 ticker-web 資料夾就載入它的 web.py，把這個面板（整個模組）交給它：網頁讀的是這裡記憶體裡的同一批資料。
# 沒有就只跑終端機。位置可以用環境變數 TICKER_WEB 指定
WEB_DIR = Path(os.environ.get("TICKER_WEB") or Path(__file__).resolve().parent.parent / "ticker-web")


def start_web():
    if not (WEB_DIR / "web.py").exists():
        return
    sys.path.insert(0, str(WEB_DIR))
    try:
        import web
        web.start(sys.modules[__name__])
    except Exception:
        import traceback
        traceback.print_exc()  # 網頁版壞了不影響面板；錯誤寫進 ticker-errors.log


def code_mtime():
    """面板與網頁版程式最後修改的時間：任一個改了，面板就在原窗格用新版重開。"""
    here = Path(__file__)
    return max(f.stat().st_mtime for f in (here, here.with_name("market.py"), here.with_name("signals.py"), WEB_DIR / "web.py")
               if f.exists())


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
    load_flow()
    # 垃圾回收調整：rich 每幀產生幾萬個暫時物件，預設門檻下每隔幾秒就有一次完整回收剛好落在某一幀，
    # 那幀從 13ms 拖到 40ms 以上（實測偶爾掉一幀）。載入完的日線、設定是長壽物件，freeze 移出回收範圍；
    # 第 0 代門檻調高，少掃幾次。實測 12 秒：最慢一幀 42→18ms、掉幀 1→0
    gc.freeze()
    gc.set_threshold(50000, 50, 100)
    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=tw_poller, daemon=True).start()
    threading.Thread(target=fugle_worker, daemon=True).start()
    if "--once" not in sys.argv:
        start_web()  # 網頁版：http://127.0.0.1:47654
    idx = 0
    if "--once" in sys.argv:  # 自我檢查：抓一輪、印出所有頁
        while not mk.last_update:
            time.sleep(0.2)
        for v in views_for(100):
            Console().print(render(v))
        return
    # 從 hook 或窗格重開時，不加這三個參數會整片空白、沒顏色；確切偵測路徑未查明，別拿掉
    console = Console(force_terminal=True, color_system="truecolor", legacy_windows=False, no_color=False)
    shown = time.time()
    mtime = reload_if_changed(None)
    code_at = code_mtime()
    enable_mouse()
    pending = None  # 翻牌動畫中收到的按鍵／點擊，動畫中斷後馬上處理
    console.file.write(BG_SGR + "\x1b[2J\x1b[?25l")  # 用面板底色清畫面、藏游標
    try:
        while True:
            mtime = reload_if_changed(mtime)
            mk.watch_sym = sel_sym  # 富果只訂 K 線面板正在看的那一檔
            if code_mtime() != code_at:
                sys.exit(3)  # 程式碼改了：外層會用新版重跑，同一個窗格
            # -1：draw() 底部留一行。每圈重算，拖拉窗格高度會自動重新分頁
            # 右側 K 線面板吃掉的寬度要先扣掉，表格才知道自己能用多少
            panel_w = min(KPANEL_MAX, console.width - COL_W - 2)
            if panel_w < KPANEL_MIN:
                panel_w = 0  # 窗格太窄就整個收起來，只留表格
            names = views_for(console.height - FOOTER_LINES, console.width - panel_w)
            idx %= len(names)
            key, pending = pending or read_key(), None
            new = idx
            if key == "q":
                return
            if key == " ":  # 空白鍵定住這一頁，標題出現 ⏸；再按一次恢復自動翻頁
                paused = not paused
                shown = time.time()
            elif isinstance(key, tuple) and (pick := clicked_index(*key[1:])):
                sel_sym = pick  # 點上面的大盤，右邊 K 線改看它；跟點個股一樣停住自動翻頁
                paused = True
            elif isinstance(key, tuple) and (market := clicked_market(*key[1:])):
                own = [i for i, v in enumerate(names) if v[0] == market]
                new = own[(own.index(idx) + 1) % len(own)] if idx in own else own[0]  # 再點同一個頁籤就翻它的下一子頁
            elif isinstance(key, tuple) and (pick := clicked_row(*key[1:], names[idx][1],
                                                              console.width - panel_w)):
                sel_sym = pick  # 點個股那一列就換右邊面板顯示的標的
                paused = True   # 點了就是要盯著看，停住自動翻頁；空白鍵恢復
            elif key in ("up", "down"):
                move_sel(names[idx][1], -1 if key == "up" else 1)
                paused = True  # 正在逐檔看，跟點選一樣停住自動翻頁
            elif key in ("+", "="):  # = 跟 + 同一顆鍵，不用按 shift
                zoom = (zoom + 1) % len(ZOOMS)
            elif key == "-":
                zoom = (zoom - 1) % len(ZOOMS)
            elif key == "right":
                new = (idx + 1) % len(names)
            elif key == "left":
                new = (idx - 1) % len(names)
            elif isinstance(key, str) and key.isdigit() and 1 <= int(key) <= len(mk.PAGES):
                market = list(mk.PAGES)[int(key) - 1]
                new = next(i for i, v in enumerate(names) if v[0] == market)
            if not key and not paused and time.time() - shown >= AUTO_SEC:
                # 自動翻頁放在處理完輸入之後：原本先排好翻頁再處理點擊，剛好到點時點了個股、畫面卻翻走
                new = (idx + 1) % len(names)
            if new != idx:
                old_rows, idx = rows_of(names[idx][1], names[idx][0] == "庫存"), new
                speed = FLIP_FAST if key else 1  # 自己點的換頁動畫加快；自動輪動照原本的節奏慢慢翻
                t0 = time.time()
                while (t := (time.time() - t0) * speed) < FLIP_SEC:
                    draw(console, compose(names[idx], old_rows, t, panel_w, console.height - FOOTER_LINES))
                    wait_input(1 / 30)
                    if pending := read_key():  # 翻牌中又點了別頁：不等動畫跑完，直接換
                        break
                shown = time.time()
            if not pending:
                draw(console, compose(names[idx], panel_w=panel_w, height=console.height - FOOTER_LINES))
                wait_input(FRAME_SEC - time.time() % FRAME_SEC)  # 睡到下一個幀邊界，跑馬燈才勻速；有點擊就提早醒
    finally:
        console.file.write("\x1b[?25h\x1b[?1000l\x1b[?1006l")  # q 離開時把游標、滑鼠還回來
        console.file.write("\x1b[0m\x1b[2J")  # 離開時用終端機原本的底色清掉，不留面板的藍灰


if __name__ == "__main__":
    if "--child" in sys.argv or "--once" in sys.argv:
        main()
    else:
        # 外層只管重跑：程式碼改了（exit 3）或子程序被砍都在原窗格重開，不會留下死掉的窗格；q 正常結束才收
        import subprocess
        while subprocess.call([sys.executable, __file__, "--child", *sys.argv[1:]]) != 0:
            time.sleep(0.5)

