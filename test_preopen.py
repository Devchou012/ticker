"""盤前預估的自我檢查，不連網：期交所 CSV 解析、夜盤對開盤的配對、滾動係數、各時段的狀態。"""
import time

import market as mk
import signals as sg

CSV = """交易日期,契約,到期月份(週別),開盤價,最高價,最低價,收盤價,漲跌價,漲跌%,成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,是否因訊息面暫停交易,交易時段,價差對單式委託成交量
2026/08/03,TX,202608  ,43186,43836,42989,43230,-497,-1.14%,69550,43219,109589,43231,43247,49470,39442,,一般,,
2026/08/03,TX,202609  ,43368,43966,43260,43388,-500,-1.14%,485,43363,6108,43373,43391,49651,24962,,一般,,
2026/08/04,TX,202608  ,43003,43165,42104,43152,-67,-0.16%,51504,-,-,43138,43152,49470,39442,,盤後,,
2026/08/04,TX,202609  ,43175,43295,42258,43280,-83,-0.19%,311,-,-,43288,43308,49651,24962,,盤後,,
2026/08/04,TX,202608/202609    ,143,150,141,148,-,-,11,-,-,150,156,273,121,,盤後,243,
2026/08/04,TX,202703  ,-,-,-,-,-,-,0,-,-,44490,44586,51411,41461,,盤後,,
"""


def demo():
    rows = mk.parse_taifex(CSV)
    assert len(rows) == 5, "價差單要去掉，沒成交的列保留但收盤是 None"
    assert rows[0] == ("20260803", "202608", "一般", 43186.0, 43230.0, 43219.0, 69550)
    assert rows[-1][4] is None

    ix = {"20260803": (43000.0, 43386.41), "20260804": (43092.49, 43360.66)}
    pairs = mk.preopen_pairs(rows, ix)
    assert len(pairs) == 1, "8/3 前面沒有交易日，只有 8/4 一組"
    d, x, y = pairs[0]
    assert d == "20260804"
    assert abs(x - (43152 / 43219 - 1)) < 1e-12, "夜盤用成交量最大的近月合約，比同一合約前一天的日盤結算"
    assert abs(y - (43092.49 / 43386.41 - 1)) < 1e-12
    day2 = mk.parse_taifex(CSV + "2026/08/04,TX,202608  ,43300,43400,43000,43350,120,0.28%,50000,43340,1,1,1,1,1,,一般,,\n")
    [(d, x, _)] = mk.preopen_pairs(day2, ix, "open")
    assert d == "20260804" and abs(x - (43300 / 43219 - 1)) < 1e-12, "08:45 那組用日盤開盤比前一天結算"

    # 開盤永遠是夜盤的一半：係數要算出 0.5，誤差為 0，方向全對
    fake = [(f"d{i}", (-1) ** i * 0.01 * (1 + i % 3), (-1) ** i * 0.005 * (1 + i % 3)) for i in range(60)]
    fit = mk.fit_preopen(fake, days=40)
    assert abs(fit["beta"] - 0.5) < 1e-12 and fit["band"] < 1e-12 and fit["hit"] == 1
    assert fit["n"] == 20 and len(fit["recent"]) == 10, "前 40 天只拿來算第一個係數"
    assert mk.fit_preopen(fake[:30], days=40) == {}, "資料不夠就不給係數"

    # 狀態：有係數、有夜盤就給預估；ADR 溢價＝ADR 換成台幣 ÷（台股 × 換股比例）− 1
    mk.preopen_fit.clear()
    one = dict(band=0.004, mae=0.002, hit=0.9, n=81, days=40, recent=[])
    mk.preopen_fit.update(night=dict(one, beta=0.5), open=dict(one, beta=0.8), date="20260927")
    mk.night["TXF"] = (48481.25, 48125.0, time.time())
    mk.quotes.update({"TSM": (300.0, 290.0), "2330.TW": (1800.0, 1790.0), "TWD=X": (32.0, 32.0),
                      "^TWII": (48000.0, 47500.0)})
    p = sg.preopen()
    assert p["base"] == (47500.0 if p["phase"] == "open" else 48000.0), "開盤後用昨收，開盤前用最新收盤"
    assert p["phase"] in ("live", "final", "open")
    if not (p["phase"] == "open" or 845 <= int(time.strftime("%H%M")) < 900):  # 平常時段：用夜盤那組
        assert p["stage"] == "night" and abs(p["move"] - (48481.25 / 48125 - 1)) < 1e-12
        assert abs(p["est"] - 0.5 * p["move"]) < 1e-12
    tsm = next(a for a in p["adrs"] if a["sym"] == "TSM")
    assert abs(tsm["prem"] - (300 * 32 / (1800 * 5) - 1)) < 1e-12 and abs(tsm["pct"] - (300 / 290 - 1)) < 1e-12
    assert next(a for a in p["adrs"] if a["sym"] == "UMC")["prem"] is None, "沒報價就是 None，不要猜"
    # 假時鐘：交易日 08:50 有今天的日盤價 → 改用日盤那組並記下；09:10 開盤後拿記下的預估對照，不看還在動的日盤價
    real_time, real_closed = sg.time, mk.tw_closed

    class Clock:
        def __init__(self, hm):
            self.t = time.struct_time((2026, 9, 29, hm // 100, hm % 100, 0, 1, 272, 0))

        def localtime(self, *a):
            return self.t

        def strftime(self, fmt, *a):
            return time.strftime(fmt, self.t)

        def time(self):
            return time.time()
    try:
        mk.tw_closed = lambda *a: False
        mk.day_quote["TXF"] = (48606.0, 48125.0, "20260929")
        sg.last_open_est.clear()
        sg.time = Clock(850)
        p = sg.preopen()
        assert p["stage"] == "open" and abs(p["est"] - 0.8 * (48606 / 48125 - 1)) < 1e-12, "08:45 後用日盤"
        mk.day_quote["TXF"] = (49000.0, 48125.0, "20260929")  # 開盤後日盤還在動
        mk.live_bar["^TWII"] = ("20260929", 48200.0, 48300.0, 48100.0, 48250.0)
        sg.time = Clock(910)
        q = sg.preopen()
        assert q["phase"] == "open" and q["stage"] == "open" and q["est"] == p["est"], "對照用 09:00 前最後的預估"
        assert abs(q["actual"] - (48200 / 47500 - 1)) < 1e-12
        mk.day_quote["TXF"] = (48606.0, 48125.0, "20260928")
        sg.last_open_est.clear()
        sg.time = Clock(850)
        assert sg.preopen()["stage"] == "night", "日盤價是昨天的就不能用"
    finally:
        sg.time, mk.tw_closed = real_time, real_closed
    print("ok")


if __name__ == "__main__":
    demo()
