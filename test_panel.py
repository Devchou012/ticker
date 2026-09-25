"""欄位收合、過期變暗、底色收斂的自我檢查。不連網。"""
import importlib.util, re, sys, time

spec = importlib.util.spec_from_file_location("t", "ticker.py")
m = importlib.util.module_from_spec(spec); sys.modules["t"] = m
spec.loader.exec_module(m)


def demo():
    # 欄位收合：夠寬全留，變窄照順序藏，名稱/代號/價格三欄永遠在
    full = m.table_w(range(len(m.COLS)))
    assert m.keep_cols(full) == list(range(len(m.COLS))), "剛好夠寬不該藏任何欄"
    assert 10 not in m.keep_cols(full - 1), "差一格就先藏型態"
    # 每一種收合結果用最長的內容實際畫一次，寬度剛好 table_w 時不能出現截字的 …
    from rich.console import Console
    rows = [("十二個字寬的", "6488.TWO", "████████████▓░░░", "10.5x", "12,345.00", "+1,345.00", "+12.23%",
             m.UP, "", "+23.5%", "", "100%", "", "+12日", "", "突破整理", "")]
    for w in range(full, 50, -1):
        keep = m.keep_cols(w)
        c = Console(width=m.table_w(keep), record=True, file=open(m.os.devnull, "w"))
        c.print(m.stock_table(False, rows, None, 1.0, range(1), keep))
        assert "…" not in c.export_text(), f"寬 {w} 收成 {keep} 還是被截字"
    # 選中那一列：名稱前面換成亮藍線、整列上底色；沒選中的維持原樣
    m.sel_sym = "6488.TWO"
    table = m.stock_table(False, rows, None, 1.0, range(1))
    assert table.rows[0].style == m.SEL, "選中那列要上底色"
    c = Console(width=200, record=True, file=open(m.os.devnull, "w"))
    c.print(table)
    assert "▌十二個字寬的" in c.export_text(), "選中那列名稱前面要有亮藍線"
    m.sel_sym = "NOPE"
    assert m.stock_table(False, rows, None, 1.0, range(1)).rows[0].style is None, "沒選中不上底色"
    narrow = m.keep_cols(80)
    assert m.table_w(narrow) <= 80, "藏完要塞得下"
    assert {0, 1, 7, 8, 9, 11} <= set(narrow), "名稱、代號、價格、漲跌、幅度、買點不能藏"
    assert 3 in narrow and 5 not in narrow, "量比最後才藏，年區間很早就藏"
    assert m.keep_cols(10) == [0, 1, 7, 8, 9, 11], "再窄也只剩必留欄（含買點燈）"

    # 過期：盤中超過 STALE_SEC 沒新報價就壓暗；收盤市場不算
    m.quotes.clear()
    m.quotes["2330.TW"] = (1000.0, 990.0)
    m.session_open = lambda sym: True
    assert not m.stale("2330.TW"), "剛寫入不算過期"
    m.quote_at["2330.TW"] = time.time() - m.STALE_SEC - 1
    assert m.stale("2330.TW"), "盤中太久沒更新要算過期"
    row = m.rows_of([("2330.TW", "台積電")])[0]
    assert row[7] == m.STALE and row[4] == "1,000.00", "過期列保留數字、換成暗色"
    assert not m.stale("FED"), "利率一小時抓一次，不算過期"
    m.session_open = lambda sym: False
    assert not m.stale("2330.TW"), "收盤市場不會更新，不算過期"

    # 月線軌跡：只補在空白格，不能蓋掉 K 棒
    m.bars.clear(); m.vols.clear(); m.live_bar.clear(); m.bar_day.clear(); m.zoom = 0
    # 先漲後跌的一年：半年線、季線、月線會分開，三條都要畫得出來
    closes = [10 + i * 0.1 for i in range(200)] + [30 - i * 0.2 for i in range(50)]
    m.bars["AAA"] = [[c, c + 0.5, c - 0.5, c] for c in closes]
    g, st, n = m.kline("AAA", 60, 20)
    ma_colors = {c for *_, c in m.MA_LINES}
    dots = {st[r][x] for r, row in enumerate(g) for x, ch in enumerate(row) if ch == "·"}
    assert dots == ma_colors, f"月線、季線、半年線都要畫，拿到 {dots}"
    assert all(ch in m.BITS or ch == "·" for row in g for ch in row), "圖上只能有 K 棒字元和均線點"
    assert all(st[r][x] not in ma_colors for r, row in enumerate(g) for x, ch in enumerate(row) if ch != "·"),         "K 棒的格子不該被均線染色"
    m.bars["NEW"] = m.bars["AAA"][:100]
    got = {s for row in m.kline("NEW", 60, 20)[1] for s in row}
    assert m.MA_LINES[2][2] not in got, "不滿 120 根不該畫半年線"
    m.bars["AAA"] = [[10 + i * 0.1, 11 + i * 0.1, 9 + i * 0.1, 10.5 + i * 0.1] for i in range(90)]
    g, st, n = m.kline("AAA", 40, 10)

    # 量柱：最大量那根兩行全滿，一半的量只到下面那行頂
    m.vols["AAA"] = [100.0] * 88 + [50.0, 200.0]
    vl, vs = m.vol_rows("AAA", 40, n)
    assert len(vl) == m.VOL_ROWS and all(len(r) == 40 for r in vl), "量柱行數與寬度固定"
    last = (n - 1) * m.ZOOMS[0][0]
    assert vl[0][last] == "█" and vl[1][last] == "█", "最大量兩行全滿"
    assert vl[0][last - 2] == " " and vl[1][last - 2] == "▄", "四分之一量只到下面那行一半"
    assert vs[1][last] == m.VOL_UP, "收紅的量柱是紅的"
    # 今天那根：盤中即時量接在後面
    m.live_bar["AAA"] = ("20260926", 19.0, 20.0, 18.0, 18.5)
    m.bar_day["AAA"] = "20260925"
    m.quotes["AAA"] = (18.5, 19.4, 400.0, None, 18.0, 20.0)
    assert m.volumes("AAA")[-1] == 400.0 and len(m.volumes("AAA")) == 91, "今天的量要接上"
    assert m.vol_rows("AAA", 40, 20)[1][1][38] == m.VOL_DOWN, "今天收黑，量柱是綠的"
    m.vols["AAA"] = [1.0] * 10
    assert m.volumes("AAA") == [], "量跟 K 棒對不齊就不畫"
    m.live_bar.clear()

    # draw 只重寫有變的行：第二幀只剩跑馬燈那一行，跳價時多送那一檔所在的行，改窗格大小整頁重畫
    import io
    from rich.text import Text

    class Con:
        size = (60, 6)
        file = io.StringIO()
    frame = lambda *rows: m.Group(*(Text(r) for r in rows))
    sent = lambda: Con.file.tell()
    m.draw(Con, frame("a", "b", "c")); first = sent()
    m.draw(Con, frame("a", "b", "c")); same = sent() - first
    m.draw(Con, frame("a", "X", "c"))
    assert "\x1b[2J" in Con.file.getvalue()[:first], "第一幀整頁重畫"
    assert "b" not in Con.file.getvalue()[first:first + same].replace("\x1b[", ""), "沒變的行不重送"
    seg = Con.file.getvalue()[first + same:]
    assert re.search(r"\x1b\[2;1H(\x1b\[[0-9;]*m)*X", seg), "變的那行要送、而且送到第 2 行"
    assert "a" not in Con.file.getvalue()[first + same:].split("\x1b[6;1H")[0].replace("\x1b[", ""), "只送變的那行"
    Con.size = (50, 6)
    n = sent(); m.draw(Con, frame("a", "X", "c"))
    assert "\x1b[2J" in Con.file.getvalue()[n:], "窗格大小變了整頁重畫"

    # 買點燈：多頭拉回月線、收長下影線、量縮 → 閃；少任何一個條件就不閃
    m.live_bar.clear()

    def setup(last_low_gap=0.001, quiet=True, hammer=True, slope=0.2):
        closes = [100 + i * slope for i in range(130)]
        m.bars["BUY"] = [[c, c + 0.3, c - 0.3, c] for c in closes]
        m20 = sum(closes[-20:]) / 20
        low = m20 * (1 + last_low_gap)
        o, c = (m20 + 1.0, m20 + 1.2) if hammer else (m20 + 3, m20 + 1.2)
        m.bars["BUY"][-1] = [o, c + 0.05, low, c]
        m.vols["BUY"] = [100.0] * 127 + ([40.0] * 3 if quiet else [300.0] * 3)
    setup()
    assert m.buy_signal("BUY")[1], "條件全部成立要閃"
    setup(quiet=False)
    assert not m.buy_signal("BUY")[1], "沒量縮不閃"
    setup(hammer=False)
    assert not m.buy_signal("BUY")[1], "沒止跌 K 棒不閃"
    setup(last_low_gap=0.05)
    assert not m.buy_signal("BUY")[1], "沒拉回到均線附近不閃"
    setup(slope=-0.2)
    assert not m.buy_signal("BUY")[1], "季線下彎不閃"
    # 半年線區間：現價在半年線上方 5%～10% 才亮橘燈
    for ratio, want in ((1.07, True), (1.03, False), (1.12, False)):
        m.bars["Z"] = [[100.0] * 4 for _ in range(129)] + [[100.0 * ratio] * 4]  # 只拉開最後一根，半年線幾乎不動
        assert m.buy_signal("Z")[0] == want, f"現價約是半年線的 {ratio} 倍，區間燈應該 {want}"
    assert m.cell_len(m.buy_light("BUY").plain) == 5 and m.buy_light(None).plain == "", "兩顆燈、空列不畫"
    # 閃燈：亮的那半拍兩顆都亮，暗的那半拍都熄成灰
    real_signal, real_time = m.buy_signal, m.time.time
    m.buy_signal = lambda sym: (True, True)
    styles = lambda: m.buy_light("X").plain.split(" ")
    m.time.time = lambda: m.FLASH_SEC * 10.5   # 偶數拍：亮
    assert styles() == [m.ZONE_ON, m.FLOW_ON], "亮拍橘燈、紅燈都亮"
    m.time.time = lambda: m.FLASH_SEC * 11.5   # 奇數拍：熄
    assert styles() == [m.LAMP_OFF, m.LAMP_OFF], "暗拍兩顆都熄"
    m.buy_signal, m.time.time = real_signal, real_time
    assert m.FLASH_SEC <= 0.25, "閃燈要比原本的 0.5 秒快"

    # 點大盤：從畫面反查點到哪一個，選了之後換頁不會被換掉，上下鍵回到個股
    m.screen[:] = ["   加權 21,000     S&P 6,500     台指期 21,050   ", "   +1.0%   ", "觀察  ETF"]
    assert m.clicked_index(5, 1) == "^TWII" and m.clicked_index(19, 2) == "^GSPC", "點名稱或下面的漲跌都算"
    assert m.clicked_index(40, 1) == "TXF", "點台指期"
    assert m.clicked_index(5, 3) is None, "第三行是頁籤，不算大盤"
    items = [("AAA", "甲"), ("BBB", "乙")]
    m.sel_sym = "^TWII"
    m.sel_items(items)
    assert m.sel_sym == "^TWII", "選了大盤，換頁不能被換回第一檔"
    m.move_sel(items, 1)
    assert m.sel_sym == "AAA", "看大盤時按上下鍵回到這一頁第一檔"
    assert "^TWII" in m.daily_syms() and "TXF" not in m.daily_syms(), "大盤要抓日線，期貨不抓"
    m.sel_sym = "TXF"
    m.quotes["TXF"] = (21050.0, 21000.0)
    c = Console(width=80, record=True, file=open(m.os.devnull, "w"))
    c.print(m.kline_panel(80, 20))
    assert "沒有日 K" in c.export_text(), "期貨要說明為什麼沒有 K 線"

    # 內外盤：成交價碰賣價算外盤、碰買價算內盤，夾中間看靠哪邊；沒成交價的快照量留到下一筆
    m.flow.clear(); m.flow_exact.clear()
    snap = lambda z, v, d="20260925", b="99.0000_98.0000_", a="100.0000_101.0000_": \
        {"z": z, "v": str(v), "d": d, "b": b, "a": a}
    m.note_flow("F.TW", snap("99.5", 100))            # 第一筆只當起點
    assert m.flow_ratio("F.TW") is None and m.flow_bar("F.TW").plain == "累計中", "還沒量就顯示累計中"
    m.note_flow("F.TW", snap("100.0", 130))           # +30 碰賣價 → 外盤
    m.note_flow("F.TW", snap("99.0", 140))            # +10 碰買價 → 內盤
    m.note_flow("F.TW", snap("-", 150))               # 沒成交價，這 10 張先不分
    m.note_flow("F.TW", snap("99.6", 160))            # 連同上一筆共 +20，靠近賣價 → 外盤
    assert m.flow["F.TW"][1:3] == [50.0, 10.0], f"外 50 內 10，拿到 {m.flow['F.TW'][1:3]}"
    m.note_flow("F.TW", snap("105.0", 170, a="-"))    # 上一筆有賣價，照中點分 → 外盤
    m.note_flow("F.TW", snap("105.0", 180, a="-"))    # 上一筆沒賣價（漲停鎖住）→ 外盤
    assert m.flow["F.TW"][1] == 70.0, "漲停沒賣單算外盤"
    m.note_flow("F.TW", snap("99.0", 10, d="20260926"))
    assert m.flow_ratio("F.TW") is None, "換日重算"
    m.flow["F.TW"][1:3] = [62.0, 38.0]
    bar = m.flow_bar("F.TW")
    assert bar.plain.startswith("62 ") and bar.plain.endswith(" 38") and m.cell_len(bar.plain) == m.RANGE_W, \
        f"外 62 內 38、寬度跟區間條一樣，拿到 {bar.plain!r}"
    assert m.cell_len(m.flow_bar("F.TW").plain) == m.RANGE_W
    m.flow["F.TW"][1:3] = [100.0, 0.0]
    assert m.cell_len(m.flow_bar("F.TW").plain) == m.RANGE_W, "100% 三位數也塞得下"
    m.flow_exact["F.TW"] = ("20260926", 30, 70)
    assert abs(m.flow_ratio("F.TW") - 0.3) < 1e-9, "富果有今天的真實值就用它"
    m.flow_exact["F.TW"] = ("20260925", 30, 70)
    assert m.flow_ratio("F.TW") == 1.0, "富果的是昨天的，不用"
    assert m.is_tw_stock("2330.TW") and m.is_tw_stock("6488.TWO"), "上市上櫃都算"
    assert not m.is_tw_stock("NVDA") and not m.is_tw_stock("t01.TW") and not m.is_tw_stock(None), "美股、類股不算"
    # 表頭跟著這一頁是不是台股換
    head = lambda syms: m.stock_table(False, [(n, n, "", "", "", "", "", None, "", "", "", "", "", "", "", "", "")
                                               for n in syms], None, 1.0, range(len(syms))).columns[2].header.plain
    m.session_open = lambda sym: True
    assert head(["2330.TW"]) == "多空比" and head(["NVDA"]) == "今日區間" and head(["2330.TW", "NVDA"]) == "多空／區間"
    # 收盤、國定假日切回今日區間
    m.session_open = lambda sym: False
    assert head(["2330.TW"]) == "今日區間", "收盤後切回今日區間"
    m.session_open = lambda sym: True
    m.flow["2330.TW"] = ["19990101", 1.0, 1.0, 10.0, None, None]
    assert not m.shows_flow("2330.TW"), "MIS 的交易日不是今天＝國定假日沒開盤"
    m.flow["2330.TW"][0] = m.time.strftime("%Y%m%d")
    assert m.shows_flow("2330.TW")

    # 存檔：讀回來接著算，關著那段的量不算進任何一邊
    m.FLOW_FILE = m.Path(__import__("tempfile").gettempdir()) / "ticker_flow_test.json"
    today = m.time.strftime("%Y%m%d")
    m.flow.clear(); m.flow_exact.clear()
    m.flow["S.TW"] = [today, 60.0, 40.0, 1000.0, 99.0, 100.0]
    m.flow_exact["S.TW"] = (today, 7, 3)
    m.save_flow()
    m.flow.clear(); m.flow_exact.clear()
    m.load_flow()
    assert m.flow["S.TW"][1:3] == [60.0, 40.0] and m.flow_exact["S.TW"] == (today, 7, 3), "讀回原本的累計"
    m.note_flow("S.TW", snap("100.0", 5000, d=today))   # 關著那段多了 4000 張：丟掉，只當新起點
    assert m.flow["S.TW"][1:4] == [60.0, 40.0, 5000.0], "重開後第一筆不算量"
    m.note_flow("S.TW", snap("100.0", 5010, d=today))
    assert m.flow["S.TW"][1] == 70.0, "之後照常累計"
    m.FLOW_FILE.unlink()

    # 閃燈原因：燈亮才有，寫出成立的是哪幾項
    setup()
    why = m.buy_reason("BUY")
    assert why and why[0] == "買點" and "回測月線" in why[1] and "量縮" in why[1] and "長下影" in why[1], why
    setup(quiet=False)
    assert "量縮" not in (m.buy_reason("BUY") or ("", "", ""))[1], "紅燈沒亮不寫流程原因"
    m.bars["FLAT"] = [[100.0] * 4 for _ in range(130)]
    assert m.buy_reason("FLAT") is None, "兩顆燈都沒亮就沒有說明"
    m.bars["Z"] = [[100.0] * 4 for _ in range(129)] + [[107.0] * 4]
    assert "半年線上" in m.buy_reason("Z")[1], "橘燈寫出離半年線幾 %"

    # 滑鼠序列分兩批到：要等後半段到齊，不能把 "52;12M" 當成按鍵
    class Keys:
        def __init__(self, *batches):
            self.batches, self.buf, self.t0 = list(batches), [], m.time.time()
        def kbhit(self):
            if not self.buf and self.batches and m.time.time() - self.t0 >= self.batches[0][0]:
                self.buf += list(self.batches.pop(0)[1])
            return bool(self.buf)
        def getwch(self):
            return self.buf.pop(0)
    real_msvcrt = m.msvcrt
    m.msvcrt = Keys((0, "\x1b[<0;"), (0.01, "52;12M"))
    assert m.read_key() == ("click", 52, 12), "分批到的點擊要拼回完整一筆"
    assert not m.msvcrt.kbhit(), "後半段不能留到下一幀變成按鍵"
    m.msvcrt = Keys((0, "\x1b"))
    assert m.read_key() is None, "只有 Esc、後面沒東西要放棄，不能卡住"
    # 等下一幀時有點擊進來要馬上醒，不能睡滿
    m.msvcrt = Keys((0.005, "x"))
    t0 = m.time.time(); m.wait_input(0.5)
    assert m.time.time() - t0 < 0.05, "有輸入要提早醒"
    m.msvcrt = Keys()
    t0 = m.time.time(); m.wait_input(0.03)
    assert m.time.time() - t0 >= 0.03, "沒輸入就睡滿"
    m.msvcrt = real_msvcrt

    # 多欄表格：點在左欄右半邊，不能被右欄比較近的代號搶走；代號不能撞到更長的代號
    m.table_layout[:] = [2, 200]
    m.screen[:] = [" 台積電  2330.TW   1000.00  ██ ██" + " " * 67 + " 力旺  3529.TWO   500.00"]
    items = [("2330.TW", "台積電"), ("3529.TWO", "力旺")]
    assert m.clicked_row(95, 1, items) == "2330.TW", "左欄最右邊還是左欄那檔"
    assert m.clicked_row(105, 1, items) == "3529.TWO", "過了中線就是右欄"
    m.table_layout[:] = [1, 200]
    m.screen[:] = [" 甲  12330.TW    乙  6488.TWO"]
    assert m.clicked_row(10, 1, [("2330.TW", "x"), ("12330.TW", "甲")]) == "12330.TW", "2330.TW 不能撞到 12330.TW"
    assert m.clicked_row(25, 1, [("6488.TW", "x"), ("6488.TWO", "乙")]) == "6488.TWO", "6488.TW 不能撞到 6488.TWO"

    # 換到比較短的頁面：下面多出來的舊行要清掉，不能留著上一頁的字
    Con.size = (60, 8)
    m.draw(Con, frame("a", "b", "c", "d")); n = sent()
    m.draw(Con, frame("a", "b"))
    assert f"\x1b[3;1H{m.BG_SGR}\x1b[0m{m.BG_SGR}\x1b[K" in Con.file.getvalue()[n:], "第 3 行的舊字要用底色清掉"
    # 整頁底色：行首先切 BG，rich 每次重設之後切回 BG；沒上色的字落在 BG 上
    assert m.paint("a\x1b[1mb\x1b[0mc") == f"{m.BG_SGR}a\x1b[1mb\x1b[0m{m.BG_SGR}c"
    assert m.paint("\x1b[1mb\x1b[0m\x1b[2mc\x1b[0m") == f"{m.BG_SGR}\x1b[1mb\x1b[0m\x1b[2mc\x1b[0m{m.BG_SGR}", \
        "重設後緊接著色碼就不補 BG，省位元組"
    # 快捷鍵列固定在倒數第二行，空白鍵的說明跟著暫停狀態換
    m.paused = False
    assert "暫停輪動" in m.fkeys().plain and f"1-{len(m.PAGES)}" in m.fkeys().plain
    m.paused = True
    assert "繼續輪動" in m.fkeys().plain
    m.paused = False
    assert len(m.screen) == 8 - 1 and m.screen[-1].strip().startswith(f"1-{len(m.PAGES)}"), \
        "8 行高的窗格：內容 6 行 + 快捷鍵列（倒數第二行），最後一行是跑馬燈"
    Con.size = (50, 6)
    m.draw(Con, frame("a", "X", "c"))  # 還原成下面那段用的窗格大小

    # 定時整頁重畫：畫面被弄亂最多亂 FULL_SEC 秒
    Con.size = (50, 6)
    n = sent(); m.draw(Con, frame("a", "X", "c"))
    assert "\x1b[2J" not in Con.file.getvalue()[n:], "剛重畫過不用再整頁"
    m.drawn_at[0] -= m.FULL_SEC
    n = sent(); m.draw(Con, frame("a", "X", "c"))
    assert "\x1b[2J" in Con.file.getvalue()[n:], "超過 FULL_SEC 要整頁重畫"

    # 同步：Yahoo 平行抓取先暫存，整圈 publish 才看得到；組畫面時寫入要等
    import threading
    m.quotes.clear(); m.staged.clear()
    worker = threading.Thread(target=lambda: m.quotes.__setitem__("Y1", (10.0, 9.0)), name=m.STAGE + "_0")
    worker.start(); worker.join()
    assert "Y1" not in m.quotes and m.staged == {"Y1": (10.0, 9.0)}, "Yahoo 執行緒的寫入先進暫存"
    m.quotes["MAIN"] = (1.0, 1.0)
    assert "MAIN" in m.quotes, "其他執行緒照常直接寫"
    m.frame_lock.acquire()  # 假裝正在組一幀
    pub = threading.Thread(target=m.publish, args=({"Y1": (10.0, 9.0), "Y2": (20.0, 19.0)},))
    pub.start(); pub.join(0.05)
    assert pub.is_alive() and "Y1" not in m.quotes, "組畫面途中不能寫進去"
    m.frame_lock.release(); pub.join()
    assert m.quotes["Y1"] == (10.0, 9.0) and m.quotes["Y2"] == (20.0, 19.0), "組完一幀後整批一起寫入"
    m.staged.clear()

    # 底色只留給警示與跳價閃燈
    for st in (m.DEV_HOT, m.DEV_COLD, m.VOL_HOT_STYLE):
        assert " on " not in st, f"{st} 不該帶底色"
    assert " on " in m.ALERT_STYLE, "警示保留底色"
    print("panel ok")


if __name__ == "__main__":
    demo()
