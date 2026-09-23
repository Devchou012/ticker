"""位階、籌碼、類股強弱條、警示的自我檢查。不連網，純算邏輯。"""
import importlib.util, sys, time

spec = importlib.util.spec_from_file_location("t", "ticker.py")
m = importlib.util.module_from_spec(spec); sys.modules["t"] = m
spec.loader.exec_module(m)


def demo():
    # 位階：乖離率與 52 週位置
    m.daily.clear()
    m.daily["AAA"] = (100.0, 95.0, 50.0, 150.0)  # ma20, ma60, 52週低, 52週高
    assert m.bias("AAA", 100.0) == ("+0.0%", m.SYMBOL), "貼著均線是中性"
    assert m.bias("AAA", 110.0)[1] == m.DEV_HOT, "乖離 +10% 要標追高"
    assert m.bias("AAA", 90.0)[1] == m.DEV_COLD, "乖離 -10% 要標回檔"
    assert m.bias("AAA", 105.0)[1] == m.SYMBOL, "+5% 還在中間帶"
    assert m.bias("NOPE", 10.0) == ("", ""), "沒日線資料就留白"
    assert m.pos52("AAA", 100.0)[0] == "50%", "高低點正中間是 50%"
    assert m.pos52("AAA", 150.0) == ("100%", m.UP), "創高是 100%"
    assert m.pos52("AAA", 50.0) == ("0%", m.DOWN), "貼底是 0%"
    assert m.pos52("AAA", 999.0)[0] == "100%", "超出一年高點要夾在 100%"

    # 籌碼：外資連續買賣超天數
    m.chips.clear()
    m.chips.update({"20260918": {"2330": 5.0}, "20260919": {},  # 放假日不算中斷
                    "20260922": {"2330": 3.0}, "20260923": {"2330": 1.0, "2317": -2.0}})
    assert m.chip_streak("2330.TW") == ("+3日", m.UP), "連三天買超"
    assert m.chip_streak("2317.TW") == ("-1日", m.DOWN), "只有最近一天賣超"
    assert m.chip_streak("9999.TW") == ("", ""), "沒進出的留白"
    assert m.chip_streak("3081.TWO") == ("", ""), "T86 只有上市，上櫃留白"
    assert m.chip_streak("NVDA") == ("", ""), "美股沒有這個欄位"

    # 類股強弱條：中間平盤，滿格 HEAT_FULL
    half = m.RANGE_W // 2
    assert m.heat_bar(0) == " " * m.RANGE_W, "平盤是空條"
    assert m.heat_bar(m.HEAT_FULL) == " " * half + "█" * half, "漲滿格往右"
    assert m.heat_bar(-m.HEAT_FULL) == "█" * half + " " * half, "跌滿格往左"
    assert m.heat_bar(9.9) == " " * half + "█" * half, "超過滿格要夾住"
    assert all(len(m.heat_bar(p)) == m.RANGE_W for p in (0, 0.005, -0.03)), "條長要固定，版面才不會抖"

    # 警示：每天每條只響一次，過期自動消失
    m.quotes.clear(); m.alert_msgs.clear(); m.alert_fired.clear(); m.PAGES = {}
    m.bell = False
    m.quotes["AAA"] = (100.0, 95.0)
    m.ALERTS = [{"symbol": "AAA", "name": "測試", "above": 99}]
    m.check_alerts()
    assert len(m.alert_msgs) == 1 and m.bell, "突破要報一次並響鈴"
    m.bell = False
    m.check_alerts()
    assert len(m.alert_msgs) == 1 and not m.bell, "同一天同一條不重複報"
    m.alert_msgs[:] = [(time.time() - m.ALERT_SEC - 1, "舊訊息")]
    m.check_alerts()
    assert not m.alert_msgs, "超過 ALERT_SEC 要自己消失"

    # K 線：根數隨縮放變、圖的寬高固定、顏色看站在哪條均線上
    m.bars.clear()
    m.bars["AAA"] = [[10 + i * 0.1, 11 + i * 0.1, 9 + i * 0.1, 10.5 + i * 0.1] for i in range(90)]
    m.zoom = 0
    g, st, n = m.kline("AAA", 40, 10)
    assert n == 20, f"每根 2 格、40 格寬應該畫 20 根，畫了 {n}"
    assert len(g) == 10 and all(len(r) == 40 for r in g), "圖的行數與每行寬度要固定"
    m.zoom = 1
    assert m.kline("AAA", 40, 10)[2] == 40, "×1 時 40 格畫 40 根"
    m.zoom = 2
    assert m.kline("AAA", 40, 10)[2] == 13, "×3 時 40 格畫 13 根"
    m.zoom = 0
    assert m.kline("NOPE", 40, 10) == ([], [], 0), "沒資料回空的"
    assert m.kline("AAA", 40, 2) == ([], [], 0), "行數太少畫不了"
    used = {s for row in st for s in row if s}
    assert used == {m.BAR_UP}, f"一路上漲的資料每根都該站上均線，拿到 {used}"
    m.bars["BBB"] = [[20 - i * 0.1, 21 - i * 0.1, 19 - i * 0.1, 20.5 - i * 0.1] for i in range(90)]
    used = {s for row in m.kline("BBB", 40, 10)[1] for s in row if s}
    assert used == {m.BAR_DOWN}, f"一路下跌的每根都該跌破月線，拿到 {used}"

    # 選取：換頁時自動落在這一頁的第一檔，上下鍵繞一圈
    items = [("AAA", "甲"), ("BBB", "乙"), ("CCC", "丙")]
    m.sel_sym = "ZZZ"
    m.sel_items(items)
    assert m.sel_sym == "AAA", "不在這一頁就選第一檔"
    m.move_sel(items, 1)
    assert m.sel_sym == "BBB"
    m.move_sel(items, -1)
    m.move_sel(items, -1)
    assert m.sel_sym == "CCC", "往上越界要繞到最後一檔"
    m.sel_items([])
    assert m.sel_sym is None, "空頁面沒有東西可選"

    # 點列：從畫面文字反查代號，兩欄並排時取水平位置最近的那個
    m.screen[:] = ["", " 甲  AAA   ...   乙  BBB "]
    assert m.clicked_row(3, 2, items) == "AAA"
    assert m.clicked_row(24, 2, items) == "BBB"
    assert m.clicked_row(3, 99, items) is None, "點到畫面外不算"
    assert m.clicked_row(3, 1, items) is None, "那一行沒有代號"
    assert m.clicked_row(24, 2, items, limit=10) is None, "點在表格右界外（K 線圖上）不改選股"

    print("位階／籌碼／類股／警示／K線／選取 ok")


if __name__ == "__main__":
    demo()
