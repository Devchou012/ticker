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
    print("位階／籌碼／類股／警示 ok")


if __name__ == "__main__":
    demo()
