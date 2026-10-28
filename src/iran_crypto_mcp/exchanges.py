"""One adapter per exchange: public endpoints -> a common shape in the exchange's own price unit.

quote is "IRT" (Toman market; Nobitex, Ramzinex and OMPFinex list it in Rial) or "USDT".
tickers(quote)  -> {COIN: {last, bid, ask, high, low, change_pct, volume_quote, ...}}
book(coin, quote)   -> (bids, asks) as [[price, qty], ...]
trades(coin, quote) -> [{time (ISO UTC), price, qty, side}], newest first
candles(coin, quote, resolution, start, end) -> [[epoch_s, o, h, l, c, v], ...]
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, NamedTuple

from .http import ApiError, fetch

NOBITEX = "https://apiv2.nobitex.ir"
WALLEX = "https://api.wallex.ir/v1"
BITPIN = "https://api.bitpin.ir/api/v1"
RAMZ = "https://api.ramzinex.com/exchange/api/v1.0/exchange"
RAMZ_PUBLIC = "https://publicapi.ramzinex.com/exchange/api/v1.0/exchange"
TABDEAL = "https://api.tabdeal.org/r/api/v1"
TABDEAL_WEB = "https://api-web.tabdeal.org/r/plots"
BITPIN_V5 = "https://api.bitpin.ir/v5/mkt"
OMP = "https://api.ompfinex.com"
EXIR = "https://api.exir.io/v2"
ABAN = "https://api.abantether.ir/api/v2/manager"
OKEX = "https://azapi.ok-ex.io/api/v1/asset/otc"
TETHERLAND = "https://service.tetherland.com/api/v5"
BIT24_PRO = "https://pro.bit24.cash/api/v3"
BIT24_OTC = "https://api.bit24.cash/api/v3"
KIFPOOL = "https://kifpool.me/api/graphql"
# the site's persisted GraphQL query (free-form queries are refused); a frontend redeploy may change the hash
KIFPOOL_BODY = {
    "operationName": "headerCoins",
    "variables": {"search": "", "offset": 0, "limit": 2000},
    "extensions": {
        "persistedQuery": {
            "version": 1,
            "sha256Hash": "bb149e99c1d2da2ca162085286128da78a9bd3936198e47e28028e20961f6f3c",
        }
    },
}


# each exchange's own Persian name, as its website spells it (agents transliterate them wrongly)
NAMES_FA = {
    "nobitex": "نوبیتکس",
    "wallex": "والکس",
    "bitpin": "بیت\u200cپین",
    "ramzinex": "رمزینکس",
    "tabdeal": "تبدیل",
    "ompfinex": "او ام پی فینکس",
    "exir": "اکسیر",
    "abantether": "آبان تتر",
    "kifpool": "کیف پول من",
    "okex": "اوکی اکسچنج",
    "tetherland": "تترلند",
    "bit24": "بیت ۲۴",
}


def _f(v: Any) -> float | None:
    """Number from a number or numeric string; None for null, '-' or junk."""
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _iso(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _levels(rows: list) -> list[list[float]]:
    return [[float(r[0]), float(r[1])] for r in rows]


def _trade(time: str, price: Any, qty: Any, side: str) -> dict:
    return {"time": time, "price": float(price), "qty": float(qty), "side": side}


def _row(last=None, bid=None, ask=None, high=None, low=None, change_pct=None, volume_quote=None, **extra) -> dict:
    return {
        "last": _f(last),
        "bid": _f(bid),
        "ask": _f(ask),
        "high": _f(high),
        "low": _f(low),
        "change_pct": _f(change_pct),
        "volume_quote": _f(volume_quote),
        **extra,
    }


def _pct(now: Any, before: Any) -> float | None:
    a, b = _f(now), _f(before)
    return (a / b - 1) * 100 if a is not None and b else None


# ---- Nobitex (Rial) ----


def _nobitex_sym(coin: str, quote: str) -> str:
    return coin + quote


async def _nobitex_tickers(quote: str) -> dict:
    q = "rls" if quote == "IRT" else "usdt"
    out = {}
    for key, v in (await fetch(f"{NOBITEX}/market/stats"))["stats"].items():
        base, _, qq = key.partition("-")
        if qq == q:
            out[base.upper()] = _row(
                v.get("latest"),
                v.get("bestBuy"),
                v.get("bestSell"),
                v.get("dayHigh"),
                v.get("dayLow"),
                v.get("dayChange"),
                v.get("volumeDst"),
            )
    return out


async def _nobitex_book(coin: str, quote: str):
    d = await fetch(f"{NOBITEX}/v3/orderbook/{_nobitex_sym(coin, quote)}")
    return _levels(d["bids"]), _levels(d["asks"])


async def _nobitex_trades(coin: str, quote: str):
    d = await fetch(f"{NOBITEX}/v2/trades/{_nobitex_sym(coin, quote)}")
    return [_trade(_iso(t["time"] / 1000), t["price"], t["volume"], t["type"]) for t in d["trades"]]


async def _udf(url: str, symbol: str, resolution: str, start: int, end: int):
    d = await fetch(url, {"symbol": symbol, "resolution": resolution, "from": start, "to": end})
    if d.get("s") == "error":  # Wallex answers HTTP 200 with s=error for an unknown symbol
        raise ApiError(f"No {symbol} market ({d.get('errmsg')}). Use ic_list_markets to see the listed coins.", 404)
    if d.get("s") != "ok":
        return []
    v = d.get("v") or [None] * len(d["t"])
    return [
        [t, _f(o), _f(h), _f(low), _f(c), _f(vol)]
        for t, o, h, low, c, vol in zip(d["t"], d["o"], d["h"], d["l"], d["c"], v, strict=False)
    ]


async def _nobitex_candles(coin: str, quote: str, resolution: str, start: int, end: int):
    try:
        rows = await _udf(f"{NOBITEX}/market/udf/history", _nobitex_sym(coin, quote), resolution, start, end)
    except ApiError as e:
        if e.status != 403:
            raise
        # Nobitex's CDN refuses this one symbol (USDTIRT) for every client, browsers included
        raise ApiError(
            f"Nobitex blocks candle requests for {coin}/{quote} (HTTP 403 from its CDN, in browsers too). "
            "Use wallex for these candles.",
            403,
        ) from e
    if quote != "IRT":
        return rows
    # the UDF feed is in Toman while the rest of Nobitex is Rial: scale to Rial so callers treat it like the other endpoints
    return [[t, *(x * 10 if x is not None else None for x in (o, h, low, c)), v] for t, o, h, low, c, v in rows]


# ---- Wallex (Toman) ----


def _wallex_sym(coin: str, quote: str) -> str:
    return coin + ("TMN" if quote == "IRT" else "USDT")


async def _wallex_tickers(quote: str) -> dict:
    q = "TMN" if quote == "IRT" else "USDT"
    out = {}
    for m in (await fetch(f"{WALLEX}/markets"))["result"]["symbols"].values():
        if m.get("quoteAsset") == q:
            s = m.get("stats") or {}
            out[m["baseAsset"].upper()] = _row(
                s.get("lastPrice"),
                s.get("bidPrice"),
                s.get("askPrice"),
                s.get("24h_highPrice"),
                s.get("24h_lowPrice"),
                s.get("24h_ch"),
                s.get("24h_quoteVolume"),
            )
    return out


async def _wallex_book(coin: str, quote: str):
    d = (await fetch(f"{WALLEX}/depth", {"symbol": _wallex_sym(coin, quote)}))["result"]
    return [[float(x["price"]), float(x["quantity"])] for x in d["bid"]], [
        [float(x["price"]), float(x["quantity"])] for x in d["ask"]
    ]


async def _wallex_trades(coin: str, quote: str):
    d = (await fetch(f"{WALLEX}/trades", {"symbol": _wallex_sym(coin, quote)}))["result"]["latestTrades"]
    return [_trade(t["timestamp"], t["price"], t["quantity"], "buy" if t["isBuyOrder"] else "sell") for t in d]


async def _wallex_candles(coin: str, quote: str, resolution: str, start: int, end: int):
    return await _udf(f"{WALLEX}/udf/history", _wallex_sym(coin, quote), resolution, start, end)


# ---- Bitpin (Toman) ----


async def _bitpin_volumes() -> dict[str, Any]:
    """24h traded value per market code from the site's v5 list (the plain tickers feed has no volume)."""
    try:
        d = await fetch(f"{BITPIN_V5}/markets/", {"limit": 2000})  # 2000 returns every market (~1.7 MB)
    except ApiError:
        return {}  # volume is a bonus: keep the ticker if the undocumented v5 list is down
    return {m["code"]: (m.get("order_book_info") or {}).get("value") for m in d.get("results", [])}


async def _bitpin_tickers(quote: str) -> dict:
    rows, volumes = await asyncio.gather(fetch(f"{BITPIN}/mkt/tickers/"), _bitpin_volumes())
    out = {}
    for t in rows:
        base, _, qq = t["symbol"].partition("_")
        if qq == quote:
            out[base.upper()] = _row(
                t["price"],
                high=t.get("high"),
                low=t.get("low"),
                change_pct=t.get("daily_change_price"),
                volume_quote=volumes.get(t["symbol"]),
            )
    return out


async def _bitpin_book(coin: str, quote: str):
    d = await fetch(f"{BITPIN}/mth/orderbook/{coin}_{quote}/")
    return _levels(d["bids"]), _levels(d["asks"])


async def _bitpin_trades(coin: str, quote: str):
    d = await fetch(f"{BITPIN}/mth/matches/{coin}_{quote}/")
    return [_trade(_iso(t["time"]), t["price"], t["base_amount"], t["side"]) for t in d]


# ---- Ramzinex (Rial; pair ids come from the pairs list) ----


async def _ramz_tickers(quote: str) -> dict:
    q = "irr" if quote == "IRT" else "usdt"
    out = {}
    for p in (await fetch(f"{RAMZ}/pairs"))["data"]:
        if p["quote_currency_symbol"]["en"] == q and not p.get("is_delist"):
            f = (p.get("financial") or {}).get("last24h") or {}
            out[p["base_currency_symbol"]["en"].split()[0].upper()] = _row(
                f.get("close"),
                p.get("buy"),
                p.get("sell"),
                f.get("highest"),
                f.get("lowest"),
                f.get("change_percent"),
                f.get("quote_volume"),
                pair_id=p["pair_id"],
            )
    return out


async def _ramz_id(coin: str, quote: str) -> int:
    row = (await _ramz_tickers(quote)).get(coin)
    if not row:
        raise ApiError(f"ramzinex has no {coin}/{quote} market. Use ic_list_markets to see the listed coins.", 404)
    return row["pair_id"]


async def _ramz_book(coin: str, quote: str):
    d = (await fetch(f"{RAMZ_PUBLIC}/orderbooks/{await _ramz_id(coin, quote)}/buys_sells"))["data"]
    return _levels([r[:2] for r in d["buys"]]), _levels([r[:2] for r in d["sells"]])


async def _ramz_trades(coin: str, quote: str):
    d = (await fetch(f"{RAMZ_PUBLIC}/orderbooks/{await _ramz_id(coin, quote)}/trades"))["data"]
    return [_trade(_iso(t[4]), t[0], t[1], t[3]) for t in d]


# ---- Tabdeal (Toman; ticker feed is the website's own: last, 24h range and change, no bid/ask or volume) ----


async def _tabdeal_tickers(quote: str) -> dict:
    d = await fetch(f"{TABDEAL_WEB}/currencies/dynamic-info/")
    return {
        coin.upper(): _row(
            m.get("price"), high=m.get("high_24"), low=m.get("low_24"), change_pct=m.get("change_percent_24")
        )
        for coin, quotes in d["currencies"].items()
        if (m := quotes.get(quote))
    }


async def _tabdeal_book(coin: str, quote: str):
    d = await fetch(f"{TABDEAL}/depth", {"symbol": coin + quote})
    return _levels(d["bids"]), _levels(d["asks"])


async def _tabdeal_trades(coin: str, quote: str):
    if coin not in await _tabdeal_tickers(quote):  # an unknown symbol answers HTTP 500, which fetch retries for seconds
        raise ApiError(f"tabdeal has no {coin}/{quote} market. Use ic_list_markets to see the listed coins.", 404)
    d = await fetch(f"{TABDEAL}/trades", {"symbol": coin + quote})
    return [_trade(_iso(t["time"] / 1000), t["price"], t["qty"], "sell" if t["isBuyerMaker"] else "buy") for t in d]


# ---- OMPFinex (Rial; market ids come from the market list) ----


async def _omp_tickers(quote: str) -> dict:
    q = "IRR" if quote == "IRT" else "USDT"
    out = {}
    for m in (await fetch(f"{OMP}/v2/market"))["data"]:
        if m["quoteCurrencyId"] == q and m["status"] == "LISTED":
            out[m["baseCurrencyId"].upper()] = _row(
                m["price"],
                high=m.get("maxPrice24h"),
                low=m.get("minPrice24h"),
                change_pct=_pct(m["price"], m.get("price24h")),
                volume_quote=m.get("volume24h"),
                market_id=m["id"],
            )
    return out


async def _omp_book(coin: str, quote: str):
    row = (await _omp_tickers(quote)).get(coin)
    if not row:
        raise ApiError(f"ompfinex has no {coin}/{quote} market. Use ic_list_markets to see the listed coins.", 404)
    d = (await fetch(f"{OMP}/v1/market/{row['market_id']}/depth"))["data"]
    return _levels(d["bids"]), _levels(d["asks"])


# ---- Exir (Toman; ticker feed has OHLCV only) ----


async def _exir_tickers(quote: str) -> dict:
    out = {}
    for key, t in (await fetch(f"{EXIR}/ticker/all")).items():
        base, _, qq = key.partition("-")
        if qq == quote.lower():
            last = _f(t.get("last"))
            vol = _f(t.get("volume"))
            out[base.upper()] = _row(
                last,
                high=t.get("high"),
                low=t.get("low"),
                change_pct=_pct(last, t.get("open")),
                volume_quote=vol * last if vol and last else None,
            )
    return out


async def _exir_book(coin: str, quote: str):
    sym = f"{coin.lower()}-{quote.lower()}"
    d = (await fetch(f"{EXIR}/orderbooks", {"symbol": sym})).get(sym)
    if not d:  # Exir answers an unknown symbol with every book instead of an error
        raise ApiError(f"exir has no {coin}/{quote} market. Use ic_list_markets to see the listed coins.", 404)
    return _levels(d["bids"]), _levels(d["asks"])


async def _exir_trades(coin: str, quote: str):
    sym = f"{coin.lower()}-{quote.lower()}"
    d = (await fetch(f"{EXIR}/trades", {"symbol": sym})).get(sym) or []
    return [_trade(t["timestamp"][:19] + "Z", t["price"], t["size"], t["side"]) for t in d]


# ---- Aban Tether (Toman; instant-swap broker: tickers only) ----


async def _aban_tickers(quote: str) -> dict:
    out = {}
    for c in (await fetch(f"{ABAN}/coins"))["data"]:
        if c.get("coin_type") != "COIN" or not c.get("is_active"):
            continue  # FUND rows (AST_*BOX) are Aban's own investment boxes, not coins
        if quote == "IRT":
            buy, sell = _f(c.get("price_buy")), _f(c.get("price_sell"))
            if buy:  # bid = what Aban pays (price_sell), ask = what it charges (price_buy)
                out[c["symbol"].upper()] = _row(
                    (buy + sell) / 2 if sell else buy, sell, buy, change_pct=c.get("percent_change_24h")
                )
        elif _f(c.get("tether_price")):  # one USDT price, no spread; volume24h is already USDT
            out[c["symbol"].upper()] = _row(
                c["tether_price"], change_pct=c.get("percent_change_24h"), volume_quote=c.get("volume24h")
            )
    return out


# ---- Kifpool (Toman; instant-swap broker: tickers only, sell price only) ----


async def _kifpool_tickers(quote: str) -> dict:
    d = (await fetch(KIFPOOL, json=KIFPOOL_BODY)).get("data")
    if not d:  # a redeployed frontend answers the old persisted-query hash with errors and no data
        raise ApiError("kifpool changed its price feed; this exchange needs a server update.", 502)
    out = {}
    for c in d["coinList"]["coins"]:
        if quote == "IRT":
            if c.get("priceSellIRT") is not None:  # the Toman price Kifpool sells at; null for USDT itself
                out[c["symbol"].upper()] = _row(
                    c["priceSellIRT"], ask=c["priceSellIRT"], change_pct=c.get("priceChangePercent")
                )
        elif c.get("price") is not None:
            out[c["symbol"].upper()] = _row(c["price"], change_pct=c.get("priceChangePercent"))
    return out


# ---- OK-EX (Toman; OTC broker: tickers only) ----


async def _okex_tickers(quote: str) -> dict:
    out = {}
    for t in await fetch(f"{OKEX}/tickers" if quote == "IRT" else f"{OKEX}/tickers/usd"):
        a, b = _f(t.get("buyAmt")), _f(t.get("sellAmt"))
        if a is None or b is None or not (t.get("allowBuy") or t.get("allowSell")):
            continue  # disabled coins keep a stale price
        if quote == "USDT" and t.get("asset") == "USDT":
            continue
        # buyAmt/sellAmt are inverted on ~40% of rows, so order them by value; no last price: the tool shows the mid
        out[t["asset"].upper()] = _row(None, min(a, b), max(a, b), change_pct=t.get("priceChange"))
    return out


# ---- Tetherland (Toman; instant-swap broker: one price per coin) ----


async def _tetherland_tickers(quote: str) -> dict:
    out = {}
    for c in (await fetch(f"{TETHERLAND}/currencies"))["data"]:
        coin = str(c.get("symbol") or "").upper()
        price = _f(str(c.get("toman_amount") if quote == "IRT" else c.get("price")).replace(",", ""))  # "80,909.4"
        if coin and price and not (quote == "USDT" and coin == "USDT"):
            out[coin] = _row(price, change_pct=c.get("changes_24h"))
    return out


# ---- Bit24 (Toman; public ticker list only: book and trades need a login) ----


async def _bit24_tickers(quote: str) -> dict:
    url = f"{BIT24_PRO}/markets"
    first = (await fetch(url, {"per_page": 100, "page": 1}))["data"]  # per_page is capped at 100
    pages = [first] + [
        m["data"]
        for m in await asyncio.gather(
            *(fetch(url, {"per_page": 100, "page": p}) for p in range(2, first["last_page"] + 1))
        )
    ]
    out = {}
    for m in (x for page in pages for x in page["results"]):
        if m["quote_coin"]["symbol"] == quote:
            s = m.get("market_24h_information") or {}
            out[m["base_coin"]["symbol"].upper()] = _row(
                s.get("last_price") or m.get("each_price"),
                high=s.get("max_price"),
                low=s.get("min_price"),
                change_pct=s.get("change_percent"),
                volume_quote=s.get("quote_volume"),
            )
    if quote == "IRT" and "USDT" not in out:  # pro has no USDT/IRT market; the instant-buy feed prices it
        try:
            for c in (await fetch(f"{BIT24_OTC}/mega-menu-coins"))["data"]["coins"]:
                if c["symbol"] == "USDT":
                    out["USDT"] = _row(c["price_irt"], change_pct=c.get("change"))
        except ApiError:
            pass
    return out


class Exchange(NamedTuple):
    id: str
    name: str
    site: str
    rial: bool  # IRT prices come back in Rial; divide by 10 for Toman
    tickers: Callable[[str], Awaitable[dict]]
    book: Callable[[str, str], Awaitable[tuple]] | None = None  # None: instant-swap broker, no order book
    trades: Callable[[str, str], Awaitable[list]] | None = None
    candles: Callable[..., Awaitable[list]] | None = None


EXCHANGES = {
    e.id: e
    for e in (
        Exchange(
            "nobitex", "Nobitex", "nobitex.ir", True, _nobitex_tickers, _nobitex_book, _nobitex_trades, _nobitex_candles
        ),
        Exchange(
            "wallex", "Wallex", "wallex.ir", False, _wallex_tickers, _wallex_book, _wallex_trades, _wallex_candles
        ),
        Exchange("bitpin", "Bitpin", "bitpin.ir", False, _bitpin_tickers, _bitpin_book, _bitpin_trades),
        Exchange("ramzinex", "Ramzinex", "ramzinex.com", True, _ramz_tickers, _ramz_book, _ramz_trades),
        Exchange("tabdeal", "Tabdeal", "tabdeal.org", False, _tabdeal_tickers, _tabdeal_book, _tabdeal_trades),
        Exchange("ompfinex", "OMPFinex", "ompfinex.com", True, _omp_tickers, _omp_book),
        Exchange("exir", "Exir", "exir.io", False, _exir_tickers, _exir_book, _exir_trades),
        Exchange("abantether", "Aban Tether", "abantether.ir", False, _aban_tickers),
        Exchange("kifpool", "Kifpool", "kifpool.me", False, _kifpool_tickers),
        Exchange("okex", "OK-EX", "ok-ex.io", False, _okex_tickers),
        Exchange("tetherland", "Tetherland", "tetherland.com", False, _tetherland_tickers),
        Exchange("bit24", "Bit24", "bit24.cash", False, _bit24_tickers),
    )
}
