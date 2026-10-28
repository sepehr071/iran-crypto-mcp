"""One adapter per exchange: public endpoints -> a common shape in the exchange's own price unit.

quote is "IRT" (Toman market; Nobitex, Ramzinex and OMPFinex list it in Rial) or "USDT".
tickers(quote)  -> {COIN: {last, bid, ask, high, low, change_pct, volume_quote, ...}}
book(coin, quote)   -> (bids, asks) as [[price, qty], ...]
trades(coin, quote) -> [{time (ISO UTC), price, qty, side}], newest first
candles(coin, quote, resolution, start, end) -> [[epoch_s, o, h, l, c, v], ...]
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from .http import ApiError, fetch

NOBITEX = "https://apiv2.nobitex.ir"
WALLEX = "https://api.wallex.ir/v1"
BITPIN = "https://api.bitpin.ir/api/v1"
RAMZ = "https://api.ramzinex.ir/exchange/api/v1.0/exchange"
RAMZ_PUBLIC = "https://publicapi.ramzinex.ir/exchange/api/v1.0/exchange"
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
WALLEX_V2 = "https://api.wallex.ir/v2"
BITPIN_V1 = "https://api.bitpin.ir/v1"
BITPIN_V4 = "https://api.bitpin.ir/v4"
TABDEAL_R = "https://api-web.tabdeal.org/r"
ABAN_OTC = "https://api.abantether.ir/otc_reporting/tradingview"
OKEX_API = "https://azapi.ok-ex.io/sno/oapi"
KIFPOOL = "https://kifpool.me/api/graphql"
# Kifpool speaks Apollo's automatic persisted queries: the query text in queries/ hashes to sha256Hash (Apollo's
# print() with __typename added). The server forgets hashes; a frontend redeploy may change them.
QUERIES = Path(__file__).parent / "queries"
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

# the site's persisted GraphQL query for its fee tables (same hash caveat as above)
KIFPOOL_FEES_BODY = {
    "operationName": "AppFees",
    "variables": {"page": 1, "search": None},
    "extensions": {
        "persistedQuery": {
            "version": 1,
            "sha256Hash": "01d1670502f68663af1a563d184aa8f58c06099f7ea5d32a8619e1fa1e7b019e",
        }
    },
}


async def _kifpool(body: dict) -> dict:
    """POST a persisted query; when the server has forgotten the hash, resend it with the query text, as Apollo does."""
    d = await fetch(KIFPOOL, json=body)
    if any((e.get("extensions") or {}).get("code") == "PERSISTED_QUERY_NOT_FOUND" for e in d.get("errors") or []):
        query = (QUERIES / f"kifpool_{body['operationName']}.graphql").read_text(encoding="utf-8")
        d = await fetch(KIFPOOL, json={**body, "query": query})
    return d


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


def _trade(time: str, price: Any, qty: Any, side: str | None) -> dict:
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


def _fees(maker: Any, taker: Any, note: str, coins: dict[str, list[dict]] | None = None) -> dict:
    """The fees() contract. Several rows of one network (retired, tagged or renamed variants) merge into the
    open one, then the cheapest."""
    merged = {}
    for coin, rows in (coins or {}).items():
        best: dict[str, tuple] = {}
        for r in rows:
            key = (not r["withdraw"], r["withdraw_fee"] is None, r["withdraw_fee"] or 0)
            if r["network"] not in best or key < best[r["network"]][0]:
                best[r["network"]] = (key, r)
        merged[coin] = [r for _, r in best.values()]
    return {"trade": {"maker_pct": _f(maker), "taker_pct": _f(taker), "note": note}, "coins": merged}


def _hardcoded(page: str) -> str:
    return f"from the site's fee page ({page}) as of 2026-10-28, not an API; may be stale"


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
    d = (await _kifpool(KIFPOOL_BODY)).get("data")
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


# ---- Fees (public fee and network endpoints) and the candles added with them ----

# Chain codes and display names -> the common network names; anything else is just upper-cased
_NET = {
    "TRX": "TRC20",
    "TRON": "TRC20",
    "ETH": "ERC20",
    "ETHEREUM": "ERC20",
    "BSC": "BEP20",
    "BNB": "BEP20",
    "BEP20(BSC)": "BEP20",
    "MATIC": "POLYGON",
    "ARB": "ARBITRUM",
    "SOLANA": "SOL",
    "ARBITRUM ONE": "ARBITRUM",
    "BITCOIN": "BTC",
    "BTC LEGACY": "BTC",
    "BTC SEGWIT": "BTC",
    "DOGECOIN": "DOGE",
    "RIPPLE": "XRP",
}


def _net(name: str) -> str:
    n = str(name).strip().upper()
    return _NET.get(n, n)


async def _nobitex_fees() -> dict:
    d = await fetch(f"{NOBITEX}/v2/options")  # ~330 KB; percent strings, index 0 = level 0
    tf = d["nobitex"]["tradingFees"]
    coins = {
        c["coin"].upper(): [
            {
                "network": _net(key),  # networkList is keyed by chain code
                "withdraw_fee": _f(n.get("withdrawFee")),
                "min_withdraw": _f(n.get("withdrawMin")),
                "withdraw": bool(n.get("withdrawEnable") and n.get("withdrawCreationEnabled", True)),
                "deposit": bool(n.get("depositEnable")),
            }
            for key, n in (c.get("networkList") or {}).items()
        ]
        for c in d["coins"]
    }
    return _fees(
        tf["makerFees"][0],
        tf["takerFees"][0],
        f"Level 0, Rial (IRT) markets. USDT markets: maker {tf['makerFeesTether'][0]}%, "
        f"taker {tf['takerFeesTether'][0]}%. Fees fall with 30-day volume.",
        {k: v for k, v in coins.items() if v},
    )


async def _wallex_fees() -> dict:
    sf, cur = await asyncio.gather(fetch(f"{WALLEX_V2}/structured-fee"), fetch(f"{WALLEX}/currencies"))
    tmn, usdt = sf["result"]["TMN"]["levels"][0], sf["result"]["USDT"]["levels"][0]  # fractions: 0.0025 = 0.25%
    coins = {}
    for sym, c in cur["result"].items():
        coins[sym.upper()] = [
            {
                "network": _net(n["name"]),
                "withdraw_fee": _f(n.get("transaction_fee")),
                "min_withdraw": _f(n.get("min_withdrawal_value")),
                # the coin-level switch counts too
                "withdraw": n.get("withdrawal_availability") == c.get("withdrawal_availability") == "ENABLE",
                "deposit": n.get("deposit_availability") == c.get("deposit_availability") == "ENABLE",
            }
            for n in c.get("network") or []  # TMN (fiat) has none
        ]
    return _fees(
        round(tmn["maker_fee"] * 100, 6),
        round(tmn["taker_fee"] * 100, 6),
        f"Level 1, Toman markets. USDT markets: maker {round(usdt['maker_fee'] * 100, 6)}%, "
        f"taker {round(usdt['taker_fee'] * 100, 6)}%. Fees fall with 30-day volume.",
        {k: v for k, v in coins.items() if v},
    )


_BITPIN_RES = {"15": "15", "60": "60", "240": "240", "D": "1D"}


async def _bitpin_fees() -> dict:
    url = f"{BITPIN_V1}/mkt/network_settings/"
    comm, first = await asyncio.gather(
        fetch(f"{BITPIN_V4}/mkt/commissions/"),
        fetch(url, {"page_size": 100}),  # 100 is the cap: ~808 rows, 9 pages
    )
    rows = list(first["results"])
    for p in await asyncio.gather(
        *(fetch(url, {"page_size": 100, "page": i}) for i in range(2, -(-first["count"] // 100) + 1))
    ):
        rows += p["results"]
    btc = next(c for c in comm if c["symbol"] == "BTC_IRT")  # fractions; every market is 0.3/0.35 but DAI_USDT
    coins: dict[str, list] = {}
    for r in rows:
        n = r["network"]
        coins.setdefault(r["currency"]["code"].upper(), []).append(
            {
                "network": _net(n["code"]),
                # one row is a percent fee: no coin-unit amount then
                "withdraw_fee": _f(r["withdraw_commission"]) if r["withdraw_commission_type"] == "value" else None,
                "min_withdraw": _f(r["min_withdraw"]),
                "withdraw": bool(
                    n["can_withdraw"] and not n["withdraw_under_maintenance"] and not r["withdraw_under_maintenance"]
                ),
                "deposit": bool(
                    n["can_deposit"] and not n["deposit_under_maintenance"] and not r["deposit_under_maintenance"]
                ),
            }
        )
    return _fees(
        round(btc["maker"] * 100, 6),
        round(btc["taker"] * 100, 6),
        "Base level 1, same for Toman and USDT markets; the top level pays 0.08/0.135%. A few markets are zero-fee.",
        coins,
    )


async def _bitpin_candles(coin: str, quote: str, resolution: str, start: int, end: int):
    # Toman like the tickers; unknown symbol is HTTP 200 [], high/low come as strings
    rows = await fetch(
        f"{BITPIN_V1}/mkt/tv/get_bars/",
        {"symbol": f"{coin}_{quote}", "res": _BITPIN_RES[resolution], "from": start, "to": end},
    )
    # `ts` is the bar open; `time` is ms and +1 day on 1D bars
    return [[int(r["ts"]), _f(r["open"]), _f(r["high"]), _f(r["low"]), _f(r["close"]), _f(r["volume"])] for r in rows]


async def _ramz_fees() -> dict:
    nets, cur = await asyncio.gather(fetch(f"{RAMZ}/networks"), fetch(f"{RAMZ}/currencies"))
    sym = {c["id"]: c["symbol"].split()[0].upper() for c in cur["data"]}  # same coin key as _ramz_tickers
    coins: dict[str, list] = {}
    for n in nets["data"]:
        if n["currency_id"] not in sym:
            continue
        live = n.get("network_type_status", True)  # false = retired network (BEP2, ETHEREUM)
        coins.setdefault(sym[n["currency_id"]], []).append(
            {
                "network": _net(n["name"]),
                "withdraw_fee": _f(n["withdraw_fee"]),
                "min_withdraw": _f(n["withdraw_min"]),
                "withdraw": bool(live and n["withdraw"] and not n["withdraw_temporarily_suspended"]),
                "deposit": bool(live and n["deposit"] and not n["deposit_temporarily_suspended"]),
            }
        )
    return _fees(
        0.2,
        0.25,
        "Level 1 (monthly volume under 300M Toman), Rial (IRT) markets; USDT markets: maker 0.095%, taker 0.1%. "
        f"Fees fall with 30-day volume. Trade fees {_hardcoded('ramzinex.ir/info/commissions')}.",
        coins,
    )


_RAMZ_RES = {"15": "15", "60": "60", "240": "240", "D": "1D"}


async def _ramz_candles(coin: str, quote: str, resolution: str, start: int, end: int):
    # Rial like the tickers; symbol is the lowercase pair (btcirr, btcusdt); unknown pair is HTTP 400
    sym = coin.lower() + ("irr" if quote == "IRT" else quote.lower())
    return await _udf(f"{RAMZ_PUBLIC}/chart/tv/v2.0/history", sym, _RAMZ_RES[resolution], start, end)


_TABDEAL_RES = {"15": "15", "60": "60", "240": "240", "D": "1D"}  # plain "D" is HTTP 400


def _tabdeal_net(t: dict) -> str:
    """'Ethereum (ERC20)' -> ERC20, 'BSC (Bep20)' -> BEP20; plain names ('BTC') come from the chain id."""
    m = re.search(r"\(([^)]+)\)", t.get("network_type_name") or "")
    return _net(m.group(1) if m else t["network_type"])


async def _tabdeal_fees() -> dict:
    fees, levels, static = await asyncio.gather(
        fetch(f"{TABDEAL_R}/treasury/currency_transaction_fees/"),  # ~150 KB
        fetch(f"{TABDEAL_R}/get-commission-levels-v2/"),
        fetch(f"{TABDEAL_R}/treasury/currencies/static/"),  # ~1 MB: only for the per-coin deposit/withdraw switches
    )
    lv = levels["commission_levels"][0]  # fractions: 0.0035 = 0.35%
    coins = {}
    for c in fees["currency_transaction_fees"]:
        sym = c["symbol"].upper()
        st = static.get(sym) or {}
        coins[sym] = [
            {
                "network": _tabdeal_net(t),
                "withdraw_fee": _f(t["transaction_fee"]),
                "min_withdraw": _f(t["min_withdraw"]),
                "withdraw": bool(st.get("can_perm_withdraw", True)),  # coin-level switch, not per network
                "deposit": bool(st.get("can_perm_deposit", True)),
            }
            for t in c["transaction_fees"]
            if t.get("visible")
        ]
    return _fees(
        round(float(lv["maker_commission_percent"]) * 100, 6),
        round(float(lv["taker_commission_percent"]) * 100, 6),
        "Level 1 (the lowest of 12+ volume tiers; higher volume pays less)",
        {k: v for k, v in coins.items() if v},
    )


async def _tabdeal_candles(coin: str, quote: str, resolution: str, start: int, end: int):
    # Toman for IRT. No row cap and from/to are loose (a 100 s window returned a bar outside it): always bounded, filtered here
    d = await fetch(
        f"{TABDEAL_R}/plots/history/",  # the trailing slash is required (301 without it)
        {"symbol": f"{coin}_{quote}", "resolution": _TABDEAL_RES[resolution], "from": start, "to": end},
    )
    return [
        [r["time"], _f(r["open"]), _f(r["high"]), _f(r["low"]), _f(r["close"]), _f(r["volume"])]
        for r in d["data"]
        if start <= r["time"] <= end
    ]


async def _omp_fees() -> dict:
    coins = {
        c["id"].upper(): [
            {
                "network": _net(n["network"]),
                "withdraw_fee": _f(n.get("withdraw_fee")),
                "min_withdraw": _f(n.get("minimum_withdraw_amount")),
                "withdraw": bool(n.get("withdraw_enabled")),
                "deposit": bool(n.get("deposit_enabled")),
            }
            for n in c.get("networks") or []
        ]
        for c in (await fetch(f"{OMP}/v2/currencies"))["data"]  # ~115 KB; fiat IRR has no networks
    }
    return _fees(
        0.35,
        0.35,
        "Level 1 (blue, up to 20M Toman 30-day volume; silver 0.30%, gold 0.25%, platinum 0.20%), one rate for "
        f"maker and taker. Trade fee {_hardcoded('ompfinex.com/commission')}.",
        {k: v for k, v in coins.items() if v},
    )


# the UDF feed has no 15, 240 or plain D: (source resolution, source bars per bar)
_OMP_RES = {"15": ("5", 3), "60": ("60", 1), "240": ("60", 4), "D": ("1D", 1)}


async def _omp_candles(coin: str, quote: str, resolution: str, start: int, end: int):
    # Toman while /v2/market is Rial (x10 for IRT, like Nobitex); `v` is QUOTE volume, so base volume is v / close (approximate)
    res, n = _OMP_RES[resolution]
    rows = await _udf(f"{OMP}/v2/udf/real/history", coin + ("IRT" if quote == "IRT" else "USDT"), res, start, end)
    k = 10 if quote == "IRT" else 1
    buckets: dict[int, list] = {}
    for t, o, h, low, c, v in rows:
        if not start <= t <= end or None in (o, h, low, c):  # `from` is loose
            continue
        key = t - t % (86400 if res == "1D" else n * int(res) * 60)
        b = buckets.setdefault(key, [key, o, h, low, c, 0.0])
        b[2], b[3], b[4] = max(b[2], h), min(b[3], low), c
        b[5] += v / c if v is not None else 0.0
    return [[b[0], *(x * k for x in b[1:5]), b[5]] for b in buckets.values()]


_EXIR_RES = {"15": "15", "60": "60", "240": "240", "D": "1D"}


async def _exir_fees() -> dict:
    const, tiers = await asyncio.gather(fetch(f"{EXIR}/constants"), fetch(f"{EXIR}/tiers"))  # 376 KB + 167 KB
    coins = {}
    for sym, c in const["coins"].items():
        if not c.get("active"):
            continue
        over = c.get("network_overrides") or {}
        per_net = c.get("withdrawal_fees") or {}
        if per_net:  # `total` = value + markup
            nets = {n: (_f(v.get("total")), v.get("active", True)) for n, v in per_net.items()}
        else:  # older coins: one flat fee and a comma-separated network list
            nets = {(c.get("network") or sym).split(",")[0]: (_f(c.get("withdrawal_fee")), True)}
        coins[sym.upper()] = [
            {
                "network": _net(n),
                "withdraw_fee": fee,
                "min_withdraw": _f(c.get("min")),  # coin-level, the same for every network
                "withdraw": bool(over.get(n, {}).get("allow_withdrawal", c.get("allow_withdrawal")) and active),
                "deposit": bool(over.get(n, {}).get("allow_deposit", c.get("allow_deposit"))),
            }
            for n, (fee, active) in nets.items()
        ]
    f = tiers["1"]["fees"]  # percents, per pair
    return _fees(
        f["maker"].get("btc-irt"),
        f["taker"].get("btc-irt"),
        f"Tier 1, Toman markets; USDT markets pay {f['maker'].get('btc-usdt')}% maker / {f['taker'].get('btc-usdt')}% "
        "taker; fees are per pair, other pairs may differ.",
        coins,
    )


async def _exir_candles(coin: str, quote: str, resolution: str, start: int, end: int):
    # Toman for IRT, base volume. Bars are sparse and unsorted, repeat, and `from` is only a hint (a 14-day window
    # returned bars back to 2023): always bounded; dedupe by time and filter here.
    d = await fetch(
        f"{EXIR}/chart",
        {"symbol": f"{coin.lower()}-{quote.lower()}", "resolution": _EXIR_RES[resolution], "from": start, "to": end},
    )
    bars = {}
    for r in d:
        t = int(datetime.fromisoformat(r["time"].replace("Z", "+00:00")).timestamp())
        if start <= t <= end:
            bars[t] = [t, _f(r["open"]), _f(r["high"]), _f(r["low"]), _f(r["close"]), _f(r["volume"])]
    return [bars[t] for t in sorted(bars)]


_BIT24_RES = {"15": "15m", "60": "1h", "240": "4h", "D": "1D"}  # anything else is HTTP 422


async def _bit24_fees() -> dict:
    url = f"{BIT24_OTC}/fee-table"  # per_page is capped at 50 (422 above)
    first = (await fetch(url, {"per_page": 50, "page": 1}))["data"]
    pages = [first["list"]] + [
        p["data"]["list"]
        for p in await asyncio.gather(
            *(fetch(url, {"per_page": 50, "page": n}) for n in range(2, first["pagination"]["lastPage"] + 1))
        )
    ]
    coins = {
        row["coin"]["symbol"].upper(): [
            {
                "network": _net(n["name"]),
                "withdraw_fee": _f(n.get("withdraw_fee")),
                "min_withdraw": _f(n.get("min_withdraw")),
                "withdraw": bool(n.get("withdraw_enable")) and not n.get("is_disabled"),
                "deposit": bool(n.get("deposit_enable")) and not n.get("is_disabled"),
            }
            for n in row.get("networks") or []
        ]
        for page in pages
        for row in page
    }
    return _fees(
        0.2,
        0.2,
        "Level VIP 0 (30-day volume under 100M Toman); instant buy/sell has no fee, only a spread. "
        f"Trade fee {_hardcoded('bit24.cash/fee')}.",
        {k: v for k, v in coins.items() if v},
    )


async def _bit24_candles(coin: str, quote: str, resolution: str, start: int, end: int):
    # pro.bit24.cash's own market (not the chart widget's third-party feed): Toman for IRT, base volume.
    # Newest first, ~2000 bars at most; a market that does not exist (USDT/IRT) answers 200 without `results`.
    d = (
        await fetch(
            f"{BIT24_PRO}/markets/ohclv",
            {
                "from_ts": start,
                "to_ts": end,
                "base_coin_symbol": coin,
                "quote_coin_symbol": quote,
                "resolution": _BIT24_RES[resolution],
            },
        )
    )["data"]
    rows = [r for r in d.get("results") or [] if start <= r["t"] <= end]
    return [[r["t"], _f(r["o"]), _f(r["h"]), _f(r["l"]), _f(r["c"]), _f(r["v"])] for r in reversed(rows)]


async def _bit24_book(coin: str, quote: str):
    d = (await fetch(f"{BIT24_PRO}/markets/{coin}-{quote}/order-books"))["data"]  # unknown market: 404
    lv = lambda rows: [[float(r["price"]), float(r["amount"])] for r in rows]  # noqa: E731
    return lv(d["buy_orders"]), lv(d["sell_orders"])


async def _bit24_trades(coin: str, quote: str):
    d = (await fetch(f"{BIT24_PRO}/markets/{coin}-{quote}/trades", {"limit": 50}))["data"]["results"]
    return [_trade(_iso(t["created_at"]), t["price"], t["amount"], None) for t in d]  # no taker side published


async def _aban_fees() -> dict:
    return _fees(
        0.3,
        0.3,
        "Toman market, level 1 (30-day volume up to 100M Toman; falls to 0.2% above 20B); the instant buy/sell spread "
        f"is in the prices; withdraw fees and networks are not public. Trade fee {_hardcoded('abantether.ir/info/fees')}.",
    )


_ABAN_RES = {"60": "60", "240": "240", "D": "1D"}  # no 15m


async def _aban_candles(coin: str, quote: str, resolution: str, start: int, end: int):
    # Aban's own chart feed (not Bitycle), Toman. Only USDT/IRT has data; other symbols answer s=no_data
    if (coin, quote) != ("USDT", "IRT"):
        raise ApiError("abantether publishes candles for USDT/IRT only. Use wallex or nobitex for other coins.", 404)
    if resolution not in _ABAN_RES:
        raise ApiError("abantether has no 15m candles. Use 1h, 4h or 1d.", 400)
    return await _udf(f"{ABAN_OTC}/history", "USDT/IRT", _ABAN_RES[resolution], start, end)


async def _kifpool_fees() -> dict:
    async def page(n: int) -> dict:
        body = {**KIFPOOL_FEES_BODY, "variables": {"page": n, "search": None}}
        d = (await _kifpool(body)).get("data")
        if not d:  # a redeployed frontend rejects the old query with errors and no data
            raise ApiError("kifpool changed its fees feed; this exchange needs a server update.", 502)
        return d["appFees"]

    first = await page(1)  # 10 rows a page, ~8 pages
    pages = [first, *await asyncio.gather(*(page(n) for n in range(2, first["crypto"]["pagination"]["lastPage"] + 1)))]
    pro = next(r for r in first["trading"]["rows"] if r["key"] == "pro")
    easy = next(r for r in first["trading"]["rows"] if r["key"] == "easy")
    coins: dict[str, list] = {}
    for p in pages:
        for r in p["crypto"]["rows"]:
            coins.setdefault(r["symbol"].upper(), []).append(
                {
                    "network": _net(r["network"]),
                    "withdraw_fee": _f(r["withdrawFee"].get("value")),
                    "min_withdraw": _f(r["withdrawMin"].get("value")),
                    "withdraw": r["withdrawStatus"]["value"] == "active",
                    "deposit": r["depositStatus"]["value"] == "active",
                }
            )
    return _fees(
        pro["buy"]["values"][0],
        pro["sell"]["values"][0],
        f"Pro market (USDT quote) flat; easy buy/sell {easy['buy']['min']}-{easy['buy']['max']}% built into the price.",
        coins,
    )


async def _okex_fees() -> dict:
    lv = next(x for x in (await fetch(f"{OKEX_API}/levels"))["levels"] if x["name"] == "VIP0")  # default level
    return _fees(
        lv["maker_fee"],
        lv["taker_fee"],
        "Spot market, VIP0; the quick-swap (OTC) fee is built into the buy/sell spread; "
        "withdraw fees and networks are not public (login only).",
    )


async def _tetherland_fees() -> dict:
    coins: dict[str, list] = {}
    for c in (await fetch(f"{TETHERLAND}/currencies-list"))["data"]:
        meta = json.loads(c.get("meta") or "{}")  # a JSON string
        coins.setdefault(str(c["symbol"]).upper(), []).append(
            {
                "network": _net(c["network"]),
                "withdraw_fee": _f(c.get("fee")),
                "min_withdraw": _f(c.get("minimum_amount")),
                "withdraw": c.get("status") == "ENABLE" and meta.get("withdrawal_status") == "ENABLE",
                "deposit": meta.get("deposit_status") == "ENABLE",
            }
        )
    return _fees(
        0.45,
        0.45,
        "Bronze level, Tetherland market; the USDT/IRT easy swap is 1.7% built into the price, crypto/crypto is free. "
        f"Trade fee {_hardcoded('tetherland.com/fee')}.",
        coins,
    )


class Exchange(NamedTuple):
    id: str
    name: str
    site: str
    rial: bool  # IRT prices come back in Rial; divide by 10 for Toman
    tickers: Callable[[str], Awaitable[dict]]
    book: Callable[[str, str], Awaitable[tuple]] | None = None  # None: instant-swap broker, no order book
    trades: Callable[[str, str], Awaitable[list]] | None = None
    candles: Callable[..., Awaitable[list]] | None = None
    # fees() -> {"trade": {maker_pct, taker_pct, note}, "coins": {COIN: [{network, withdraw_fee, min_withdraw,
    # withdraw, deposit}]}}; percent values, withdraw amounts in coin units
    fees: Callable[[], Awaitable[dict]] | None = None


EXCHANGES = {
    e.id: e
    for e in (
        Exchange(
            "nobitex",
            "Nobitex",
            "nobitex.ir",
            True,
            _nobitex_tickers,
            _nobitex_book,
            _nobitex_trades,
            _nobitex_candles,
            fees=_nobitex_fees,
        ),
        Exchange(
            "wallex",
            "Wallex",
            "wallex.ir",
            False,
            _wallex_tickers,
            _wallex_book,
            _wallex_trades,
            _wallex_candles,
            fees=_wallex_fees,
        ),
        Exchange(
            "bitpin",
            "Bitpin",
            "bitpin.ir",
            False,
            _bitpin_tickers,
            _bitpin_book,
            _bitpin_trades,
            candles=_bitpin_candles,
            fees=_bitpin_fees,
        ),
        Exchange(
            "ramzinex",
            "Ramzinex",
            "ramzinex.ir",
            True,
            _ramz_tickers,
            _ramz_book,
            _ramz_trades,
            candles=_ramz_candles,
            fees=_ramz_fees,
        ),
        Exchange(
            "tabdeal",
            "Tabdeal",
            "tabdeal.org",
            False,
            _tabdeal_tickers,
            _tabdeal_book,
            _tabdeal_trades,
            candles=_tabdeal_candles,
            fees=_tabdeal_fees,
        ),
        Exchange(
            "ompfinex", "OMPFinex", "ompfinex.com", True, _omp_tickers, _omp_book, candles=_omp_candles, fees=_omp_fees
        ),
        Exchange(
            "exir",
            "Exir",
            "exir.io",
            False,
            _exir_tickers,
            _exir_book,
            _exir_trades,
            candles=_exir_candles,
            fees=_exir_fees,
        ),
        Exchange(
            "abantether", "Aban Tether", "abantether.ir", False, _aban_tickers, candles=_aban_candles, fees=_aban_fees
        ),
        Exchange("kifpool", "Kifpool", "kifpool.me", False, _kifpool_tickers, fees=_kifpool_fees),
        Exchange("okex", "OK-EX", "ok-ex.io", False, _okex_tickers, fees=_okex_fees),
        Exchange("tetherland", "Tetherland", "tetherland.com", False, _tetherland_tickers, fees=_tetherland_fees),
        Exchange(
            "bit24",
            "Bit24",
            "bit24.cash",
            False,
            _bit24_tickers,
            _bit24_book,
            _bit24_trades,
            _bit24_candles,
            _bit24_fees,
        ),
    )
}
