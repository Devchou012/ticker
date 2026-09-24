"""分組標題列與 CNBC 備援的自我檢查：標題列不進輪詢、代號對照正確、備援真的抓得到價。"""
import importlib.util, sys

spec = importlib.util.spec_from_file_location("t", "ticker.py")
m = importlib.util.module_from_spec(spec); sys.modules["t"] = m
spec.loader.exec_module(m)


def demo():
    rows = m.rows_of([(None, "衛星"), ("AAA", "測試")])
    assert rows[0][0] == "▸ 衛星" and rows[0][1] == "", "分組標題列只有名稱"
    assert rows[1][4] == "…", "沒報價的股票顯示等待中"

    assert m.cnbc_sym("^TNX") == "US10Y"
    assert m.cnbc_sym("7203.T") == "7203.JP"
    assert m.cnbc_sym("AAPL") == "AAPL"
    for sym in ("2330.TW", "3105.TWO", "005930.KS"):  # CNBC 對不上或報價不可信，讓 Yahoo/證交所處理
        assert m.cnbc_sym(sym) is None, sym

    m.quotes.clear()
    m.fetch_cnbc(["AAPL", "^N225", "2330.TW"])
    for sym in ("AAPL", "^N225"):
        price, prev, _vol, _avg, low, high = m.quotes[sym]
        assert price > 0 and prev > 0 and low <= price <= high, f"{sym} 價格不合理"
    assert "2330.TW" not in m.quotes, "台股不該走 CNBC"
    print("ok")


if __name__ == "__main__":
    demo()
