"""Add or remove stocks on the watchlist in portfolio.json; a running panel picks up the change within a second.

Usage:
  python watch.py 興富發 2881 NVDA   add (Taiwan stock name or code, or a US ticker)
  python watch.py -g 半導體 2303      add into a group (default group: 台股／美股／日韓, same as the web page)
  python watch.py -d 興富發           remove (a group left empty is removed too)
  python watch.py                     list

The watchlist is either a flat list or {"group": [...]} (groups may nest); both work.
"""
import json
import ssl
import sys
import urllib.request
from pathlib import Path

import yfinance as yf

PORTFOLIO = Path(__file__).with_name("portfolio.json")
MARKET_OF = {".TW": "台股", ".TWO": "台股", ".T": "日韓", ".KS": "日韓", ".KQ": "日韓"}  # same default groups as the web page
SOURCES = [  # (url, code field, name field, Yahoo suffix)
    ("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", "Code", "Name", ".TW"),
    ("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
     "SecuritiesCompanyCode", "CompanyName", ".TWO"),
]


def tw_listing():
    """Return {code: (name, symbol)} for all TWSE and TPEx stocks."""
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT  # TWSE cert lacks a field Python 3.13 strict mode requires; the chain is still verified
    out = {}
    for url, code, name, suffix in SOURCES:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        for row in json.load(urllib.request.urlopen(req, timeout=20, context=ctx)):
            out.setdefault(row[code], (row[name].rstrip("*"), row[code] + suffix))
    return out


def resolve(query, listing):
    """Return (symbol, name) for a code, a Chinese name, or a US ticker; None if not found."""
    if query in listing:
        name, sym = listing[query]
        return sym, name
    hits = [v for v in listing.values() if v[0] == query] or [v for v in listing.values() if query in v[0]]
    if len(hits) == 1:
        return hits[0][1], hits[0][0]
    if len(hits) > 1:
        print(f"「{query}」matches several: " + ", ".join(f"{n}({s})" for n, s in hits[:8]) + ". Use the code instead.")
        return None
    if query.isascii():
        info = yf.Ticker(query.upper()).info
        if info.get("shortName"):
            return query.upper(), info["shortName"]
    print(f"Not found: {query}")
    return None


def lists(watch, group=""):
    """Yield (group, list) for every list in the watchlist, flat or grouped."""
    if isinstance(watch, list):
        yield group, watch
    else:
        for g, v in watch.items():
            yield from lists(v, g)


def prune(watch):
    """Drop groups left empty; the panel would show an empty group header otherwise."""
    if isinstance(watch, dict):
        for g in [g for g, v in watch.items() if not prune(v)]:
            del watch[g]
    return watch


def main():
    args = sys.argv[1:]
    data = json.loads(PORTFOLIO.read_text(encoding="utf-8")) if PORTFOLIO.exists() else {}
    watch = data.setdefault("觀察", {})
    if not args:
        for g, items in lists(watch):
            if g:
                print(f"▸ {g}")
            for w in items:
                print(f"{'  ' if g else ''}{w['symbol']:<12}{w['name']}")
        return
    remove = args[0] == "-d"
    group = args[1] if args[0] == "-g" and len(args) > 1 else None
    queries = args[1:] if remove else args[2:] if group else args
    listing = tw_listing()
    for q in queries:
        if remove:
            gone = 0
            for _, items in lists(watch):
                before = len(items)
                items[:] = [w for w in items if q not in (w["symbol"], w["name"], w["symbol"].split(".")[0])]
                gone += before - len(items)
            print(f"Removed {q}" if gone else f"Not on the list: {q}")
            continue
        hit = resolve(q, listing)
        if not hit:
            continue
        where = next((g for g, items in lists(watch) for w in items if w["symbol"] == hit[0]), None)
        if where is not None:
            print(f"Already on the list: {hit[1]} {hit[0]}" + (f" ({where})" if where else ""))
            continue
        g = group or MARKET_OF.get("." + hit[0].split(".")[-1], "美股")  # NVDA → ".NVDA" isn't a suffix → 美股
        (watch.setdefault(g, []) if isinstance(watch, dict) else watch).append({"symbol": hit[0], "name": hit[1]})
        print(f"Added {hit[1]} {hit[0]}" + (f" ({g})" if isinstance(watch, dict) else ""))
    prune(watch)
    tmp = PORTFOLIO.with_suffix(".tmp")  # write to a temp file, then swap it in, so the panel never reads a half-written file
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PORTFOLIO)

if __name__ == "__main__":
    main()
