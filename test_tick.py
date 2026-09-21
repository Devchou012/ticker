"""亮燈邏輯的自我檢查：第一次看到不閃、漲跌方向正確、過了 TICK_SEC 熄燈。"""
import importlib.util, sys, time

spec = importlib.util.spec_from_file_location("t", "ticker.py")
m = importlib.util.module_from_spec(spec); sys.modules["t"] = m
spec.loader.exec_module(m)


def demo():
    m.quotes.clear(); m.seen_px.clear(); m.tick_at.clear()

    m.quotes["AAA"] = (100.0, 99.0)
    m.mark_ticks()
    assert m.tick_style("AAA") is None, "第一次看到不該亮燈"

    m.quotes["AAA"] = (101.0, 99.0)
    m.mark_ticks()
    assert m.tick_style("AAA") == m.TICK_UP, "漲要亮紅底"

    m.quotes["AAA"] = (100.5, 99.0)
    m.mark_ticks()
    assert m.tick_style("AAA") == m.TICK_DOWN, "跌要亮綠底"

    m.mark_ticks()  # 價格沒動
    assert m.tick_style("AAA") == m.TICK_DOWN, "沒動時舊的亮燈應該繼續燒完"

    m.tick_at["AAA"] = (time.time() - m.TICK_SEC - 0.01, 1)
    assert m.tick_style("AAA") is None, "超過 TICK_SEC 要熄燈"

    assert m.tick_style("NOPE") is None, "沒資料的代號不該亮"
    print("tick 亮燈 ok")


if __name__ == "__main__":
    demo()
