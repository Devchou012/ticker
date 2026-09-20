"""session_open 的檢查：美股跨午夜那段最容易寫錯。python test_session_open.py 跑。"""
import time
from unittest import mock

import ticker

CASES = [  # (週幾, 時, 代號, 預期, 說明)
    (0, 10, "2330.TW", True, "週一台股盤中"),
    (0, 16, "2330.TW", False, "週一台股收盤後"),
    (5, 10, "2330.TW", False, "週六台股休市"),
    (0, 22, "AAPL", True, "週一晚美股盤中"),
    (1, 3, "AAPL", True, "週二凌晨＝週一美股盤中"),
    (5, 3, "AAPL", True, "週六凌晨＝週五美股盤中"),
    (6, 3, "AAPL", False, "週日凌晨，美股沒開"),
    (0, 12, "AAPL", False, "週一中午，美股沒開"),
    (5, 10, "GC=F", True, "期貨判斷不了，當作有開"),
    (5, 10, "BTC-USD", True, "加密貨幣一直開"),
]


def at(wday, hh, fn, *args):
    """把時鐘固定在 2026-01-05（週一）起算的某個平日時刻再呼叫 fn。"""
    real = time.localtime
    when = time.mktime((2026, 1, 5 + wday, hh, 0, 0, wday, 5 + wday, real().tm_isdst))
    with mock.patch.object(ticker.time, "time", lambda: when), \
         mock.patch.object(ticker.time, "localtime", lambda t=None: real(when if t is None else t)):
        return fn(*args)


def main():
    bad = [(w, h, s, e, why) for w, h, s, e, why in CASES if at(w, h, ticker.session_open, s) != e]
    for w, h, s, e, why in bad:
        print(f"FAIL {s} 週{w} {h:02d}:00 應該是 {e}（{why}）")
    assert not bad, f"{len(bad)}/{len(CASES)} 個時段判斷錯誤"
    print(f"{len(CASES)} 個時段全部正確")


if __name__ == "__main__":
    main()
