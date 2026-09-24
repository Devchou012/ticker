"""今天那根 K 棒：Yahoo 還沒有今天就接上，已經有就換掉，沒有即時資料就原樣。"""
import ticker as t

t.bars["X.TW"] = [[1, 2, 0.5, 1.5], [1.5, 2.5, 1, 2]]
t.bar_day["X.TW"] = "20260923"
t.live_bar["X.TW"] = ("20260924", 2, 3, 1.8, 2.9)
assert t.candles("X.TW") == [[1, 2, 0.5, 1.5], [1.5, 2.5, 1, 2], [2, 3, 1.8, 2.9]]

t.bar_day["X.TW"] = "20260924"
assert t.candles("X.TW") == [[1, 2, 0.5, 1.5], [2, 3, 1.8, 2.9]]

del t.live_bar["X.TW"]
assert t.candles("X.TW") == t.bars["X.TW"]
print("ok")
