"""watch.py 的自我檢查：分組名單、扁平名單的列出、加入、移除。不連網（上市櫃清單換成假的）。"""
import contextlib, io, json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import watch

LISTING = {"2330": ("台積電", "2330.TW"), "2303": ("聯電", "2303.TW"), "6488": ("環球晶", "6488.TWO")}


def run(*args):
    sys.argv = ["watch.py", *args]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        watch.main()
    return out.getvalue()


def load():
    return json.loads(watch.PORTFOLIO.read_text(encoding="utf-8"))["觀察"]


def demo():
    watch.tw_listing = lambda: LISTING
    watch.PORTFOLIO = Path(tempfile.gettempdir()) / "ticker_watch_test.json"
    grouped = {"庫存": [{"symbol": "2330.TW", "name": "台積電", "shares": 1, "cost": 1}],
               "觀察": {"半導體": [{"symbol": "2330.TW", "name": "台積電"}], "衛星": [{"symbol": "3491.TWO", "name": "昇達科"}]}}
    watch.PORTFOLIO.write_text(json.dumps(grouped, ensure_ascii=False), encoding="utf-8")
    try:
        # 分組名單：列出、依市場預設分組、-g 指定分組、重複不加
        assert "▸ 半導體" in run() and "2330.TW" in run()
        assert "Added 環球晶 6488.TWO (台股)" in run("6488")
        assert "Added 聯電 2303.TW (半導體)" in run("-g", "半導體", "聯電")
        assert "Already on the list: 台積電 2330.TW (半導體)" in run("2330")
        assert load() == {"半導體": [{"symbol": "2330.TW", "name": "台積電"}, {"symbol": "2303.TW", "name": "聯電"}],
                          "衛星": [{"symbol": "3491.TWO", "name": "昇達科"}],
                          "台股": [{"symbol": "6488.TWO", "name": "環球晶"}]}
        # 移除：代號、名稱都行；分組空了一起拿掉；其他分頁不動
        assert "Removed 3491" in run("-d", "3491") and "Not on the list: 9999" in run("-d", "9999")
        assert "Removed 環球晶" in run("-d", "環球晶")
        assert list(load()) == ["半導體"], "空分組要刪掉"
        assert json.loads(watch.PORTFOLIO.read_text(encoding="utf-8"))["庫存"] == grouped["庫存"]
        # 舊的扁平名單照樣能用
        watch.PORTFOLIO.write_text(json.dumps({"觀察": [{"symbol": "2330.TW", "name": "台積電"}]}, ensure_ascii=False),
                                   encoding="utf-8")
        assert "Added 聯電 2303.TW\n" in run("2303") and [w["symbol"] for w in load()] == ["2330.TW", "2303.TW"]
        assert "Removed 2330" in run("-d", "2330") and load() == [{"symbol": "2303.TW", "name": "聯電"}]
        assert run().strip() == "2303.TW     聯電"
    finally:
        watch.PORTFOLIO.unlink(missing_ok=True)
    print("watch ok")


if __name__ == "__main__":
    demo()
