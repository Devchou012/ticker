"""跑馬燈的自我檢查：寬度必須剛好、CJK 切半要補空白、捲動要接得回去。"""
import importlib.util, sys, time

spec = importlib.util.spec_from_file_location("t", "ticker.py")
m = importlib.util.module_from_spec(spec); sys.modules["t"] = m
spec.loader.exec_module(m)
from rich.text import Text
from rich.cells import cell_len


def demo():
    # clip_cells：任何起點切出來都要剛好是要求的寬度
    t = Text("台積電ABC鴻海XY")
    for start in range(cell_len(t.plain) + 3):
        for width in (1, 5, 12, 40):
            got = cell_len(m.clip_cells(t, start, width).plain)
            assert got == width, f"start={start} width={width} 切出 {got}"

    # 切在全形字中間要補空白，不能把半個字畫出來
    assert m.clip_cells(Text("台積電"), 1, 5).plain == " 積電", m.clip_cells(Text("台積電"), 1, 5).plain
    assert m.clip_cells(Text("台積電"), 1, 4).plain == " 積 ", m.clip_cells(Text("台積電"), 1, 4).plain

    # marquee：整行寬度要剛好，窄視窗不放固定段
    m.quotes.clear()
    m.quotes["DX-Y.NYB"] = (100.36, 100.31)
    m.quotes["2330.TW"] = (1500.0, 1400.0)
    for width in (60, 119, 120, 200):
        line = m.marquee(width)
        assert cell_len(line.plain) == width, f"寬 {width} 得到 {cell_len(line.plain)}"
    assert "DXY" not in m.marquee(119).plain, "窄視窗不該有固定段"
    assert "DXY" in m.marquee(200).plain, "寬視窗要有固定段"

    # 沒有任何報價時也不能爆，寬度照樣要對
    m.quotes.clear()
    assert cell_len(m.marquee(150).plain) == 150

    # movers：央行利率不進榜，跨頁重複的只算一次
    m.quotes.clear()
    m.quotes["FED"] = (4.0, 3.75)
    m.quotes["000660.KS"] = (100.0, 50.0)
    names = [name for _, name, _, _ in m.movers()]
    assert "美國利率" not in names, "央行利率不該進異動榜"
    assert names.count("SK海力士") == 1, "跨頁重複的代號只該算一次"
    # 盤中的市場優先：台股開盤時美股不該上榜，全收盤時才退回來用
    m.quotes.clear()
    m.quotes["2330.TW"] = (1500.0, 1400.0)   # 台股 +7%
    m.quotes["NVDA"] = (200.0, 100.0)        # 美股 +100%，幅度大得多
    tw_open = m.session_open("2330.TW")
    us_open = m.session_open("NVDA")
    names = [name for _, name, _, _ in m.movers()]
    if tw_open and not us_open:
        assert names == ["台積電"], f"台股盤中不該出現美股，得到 {names}"
    elif us_open and not tw_open:
        assert names == ["NVIDIA"], f"美股盤中不該出現台股，得到 {names}"
    else:
        assert set(names) == {"台積電", "NVIDIA"}, f"兩邊都開或都關時應一起排，得到 {names}"

    m.quotes.clear()
    m.quotes["2330.TW"] = (1500.0, 1400.0)
    assert [n for _, n, _, _ in m.movers()] == ["台積電"], "全收盤也要退回來顯示，不能空"
    print("跑馬燈 ok")


if __name__ == "__main__":
    demo()
