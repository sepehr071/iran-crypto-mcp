"""Iranian crypto exchange tools: price comparison, tickers, order books, trades, candles, movers.

All IRT prices are returned in Toman (Rial-quoted exchanges are divided by 10); USDT prices are unchanged.
Field names carry the unit: `price_toman` / `price_usdt`.
"""

from __future__ import annotations

import asyncio
import re
import time
from statistics import median
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .exchanges import EXCHANGES, NAMES_FA, Exchange
from .http import ApiError
from .registry import tool

ExchangeId = Literal[
    "nobitex",
    "wallex",
    "bitpin",
    "ramzinex",
    "tabdeal",
    "ompfinex",
    "exir",
    "abantether",
    "kifpool",
    "okex",
    "tetherland",
    "bit24",
    "balinex",
]
Quote = Literal["IRT", "USDT"]
EX = Annotated[ExchangeId, Field(description="Exchange id from ic_list_exchanges, e.g. 'nobitex'")]
COIN = Annotated[str, Field(description="Coin symbol, e.g. 'BTC', 'ETH', 'USDT' (Tether in Toman), 'TON'")]
QUOTE = Annotated[Quote, Field(description="Quote currency: 'IRT' = Toman market (default), 'USDT' = Tether market")]

RESOLUTIONS = {"15m": "15", "1h": "60", "4h": "240", "1d": "D"}
SECONDS = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def _coin(coin: str) -> str:
    c = coin.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_]{1,15}", c):
        raise ToolError(f"'{coin}' is not a coin symbol. Use a ticker like BTC, ETH or USDT.")
    return c


def _unit(quote: str) -> str:
    return "toman" if quote == "IRT" else "usdt"


def _r(v: float | None) -> float | int | None:
    """Round for display: 2 decimals above 1000, else 8 significant digits; whole numbers become int."""
    if v is None:
        return None
    v = round(v, 2) if abs(v) >= 1000 else float(f"{v:.8g}")
    return int(v) if v == int(v) else v


def _pc(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def _px(ex: Exchange, quote: str, v: float | None) -> float | int | None:
    """A price in the reply unit: Toman for IRT (Rial exchanges / 10), USDT for USDT."""
    return _r(v / 10 if v is not None and ex.rial and quote == "IRT" else v)


async def _ticker(ex: Exchange, coin: str, quote: str) -> dict:
    row = (await ex.tickers(quote)).get(coin)
    if not row:
        raise ApiError(f"{ex.name} has no {coin}/{quote} market. Use ic_list_markets to see the listed coins.", 404)
    return row


def _view(ex: Exchange, quote: str, row: dict) -> dict:
    """Common reply fields of a ticker row, with the unit in the names."""
    u = _unit(quote)
    last, bid, ask = row["last"], row["bid"], row["ask"]
    basis = "last"
    if last is None and bid is not None and ask is not None:
        last, basis = (bid + ask) / 2, "mid"
    return {
        "exchange": ex.id,
        "name_fa": NAMES_FA[ex.id],
        f"price_{u}": _px(ex, quote, last),
        "price_basis": basis if last is not None else None,
        f"bid_{u}": _px(ex, quote, bid),
        f"ask_{u}": _px(ex, quote, ask),
        f"high_24h_{u}": _px(ex, quote, row["high"]),
        f"low_24h_{u}": _px(ex, quote, row["low"]),
        "change_24h_pct": _pc(row["change_pct"]),
        f"volume_24h_{u}": _px(ex, quote, row["volume_quote"]),
    }


@tool("List Exchanges")
async def ic_list_exchanges() -> dict:
    """List the supported Iranian crypto exchanges, their native price unit and what each can answer.

    Start here. Every price this server returns is already converted to Toman (IRT markets) or is in USDT.
    """
    return {
        "note": "IRT prices are Toman. Nobitex, Ramzinex and OMPFinex publish Rial; this server divides by 10.",
        "exchanges": [
            {
                "id": e.id,
                "name": e.name,
                "name_fa": NAMES_FA[e.id],
                "site": e.site,
                "source_unit": "rial" if e.rial else "toman",
                "quotes": ["IRT", "USDT"],
                "orderbook": e.book is not None,
                "trades": e.trades is not None,
                "candles": e.candles is not None,
                "fees": e.fees is not None,
            }
            for e in EXCHANGES.values()
        ],
    }


@tool("List Markets")
async def ic_list_markets(exchange: EX, quote: QUOTE = "IRT") -> dict:
    """List the coins an exchange trades against Toman (IRT) or USDT.

    Use it to check a coin symbol before calling the other tools.
    """
    ex = EXCHANGES[exchange]
    coins = sorted(await ex.tickers(quote))
    return {"exchange": ex.id, "quote": quote, "count": len(coins), "coins": coins}


@tool("Compare Coin Price")
async def ic_compare_price(coin: COIN, quote: QUOTE = "IRT") -> dict:
    """Compare one coin's price on every supported exchange in one call, cheapest first, with the spread.

    Prices are normalized to Toman (IRT) or USDT. Use coin='USDT' with quote='IRT' for the Tether rate in Toman.
    An exchange that is down, blocks the caller's network or does not list the coin appears under `errors`; the rest
    are still compared. Always tell the user which exchanges are missing and why.
    To check a spread is real, call ic_get_orderbook on the cheapest and the priciest exchange next.
    """
    c, u = _coin(coin), _unit(quote)
    exs = list(EXCHANGES.values())
    results = await asyncio.gather(*(_ticker(e, c, quote) for e in exs), return_exceptions=True)
    prices, errors = [], {}
    for e, r in zip(exs, results, strict=True):
        if isinstance(r, ApiError):
            errors[e.id] = str(r)
        elif isinstance(r, BaseException):
            raise r
        elif (v := _view(e, quote, r))[f"price_{u}"] is None:
            errors[e.id] = f"{e.name} has no price for {c}/{quote} right now."
        else:
            prices.append(v)
    prices.sort(key=lambda p: p[f"price_{u}"])
    out: dict[str, Any] = {"coin": c, "quote": quote, "unit": u, "prices": prices, "errors": errors}
    if len(prices) > 1:
        lo, hi = prices[0], prices[-1]
        out |= {
            "cheapest": lo["exchange"],
            "priciest": hi["exchange"],
            "spread_pct": _pc((hi[f"price_{u}"] / lo[f"price_{u}"] - 1) * 100),
        }
    return out


@tool("Get Ticker")
async def ic_get_ticker(exchange: EX, coin: COIN, quote: QUOTE = "IRT") -> dict:
    """Get the live price, best bid and ask, 24h range, change and volume of one coin on one exchange.

    Bid and ask are missing for Bitpin, Tabdeal, OMPFinex, Exir and Balinex; volume is missing for Tabdeal. Use
    ic_get_orderbook for the best bid and ask (not for Balinex, which has no order book here), ic_candles for
    price history.
    A coin the exchange does not list is an error: check the symbol with ic_list_markets.
    """
    ex, c, u = EXCHANGES[exchange], _coin(coin), _unit(quote)
    v = _view(ex, quote, await _ticker(ex, c, quote))
    if v[f"price_{u}"] is None:
        raise ToolError(f"{ex.name} has no price for {c}/{quote} right now (listed but not trading).")
    return {"coin": c, "quote": quote, "unit": u, **v}


@tool("Get Order Book")
async def ic_get_orderbook(
    exchange: EX,
    coin: COIN,
    quote: QUOTE = "IRT",
    depth: Annotated[int, Field(ge=1, le=20, description="Levels per side (1-20)")] = 10,
) -> dict:
    """Get the top bids and asks of one coin's order book on one exchange, with the spread.

    Levels are [price, quantity in coin], best first. Prices are Toman (IRT) or USDT.
    Not on the instant-swap brokers (Aban Tether, Kifpool, OK-EX, Tetherland) nor on Balinex.
    """
    ex, c, u = EXCHANGES[exchange], _coin(coin), _unit(quote)
    if ex.book is None:
        have = ", ".join(e.id for e in EXCHANGES.values() if e.book)
        raise ToolError(f"{ex.name} has no order book here (ic_list_exchanges shows orderbook=false). Use {have}.")
    bids, asks = await ex.book(c, quote)
    bids.sort(key=lambda x: -x[0])
    asks.sort(key=lambda x: x[0])
    out: dict[str, Any] = {
        "exchange": ex.id,
        "coin": c,
        "quote": quote,
        "unit": u,
        "level_format": f"[price_{u}, quantity_{c}]",
        "bids": [[_px(ex, quote, p), _r(q)] for p, q in bids[:depth]],
        "asks": [[_px(ex, quote, p), _r(q)] for p, q in asks[:depth]],
    }
    if bids and asks:
        out["spread_pct"] = _pc((asks[0][0] / bids[0][0] - 1) * 100)
    return out


@tool("Recent Trades")
async def ic_recent_trades(
    exchange: EX,
    coin: COIN,
    quote: QUOTE = "IRT",
    limit: Annotated[int, Field(ge=1, le=50, description="Trades to return (1-50)")] = 20,
) -> dict:
    """Get the most recent public trades of one coin on one exchange, newest first.

    Order-book exchanges except OMPFinex. `side` is the taker side when the exchange reports it.
    """
    ex, c, u = EXCHANGES[exchange], _coin(coin), _unit(quote)
    if ex.trades is None:
        have = ", ".join(e.id for e in EXCHANGES.values() if e.trades)
        raise ToolError(f"{ex.name} has no public trades endpoint here. Use {have}.")
    rows = sorted(await ex.trades(c, quote), key=lambda t: t["time"], reverse=True)[:limit]
    return {
        "exchange": ex.id,
        "coin": c,
        "quote": quote,
        "unit": u,
        "trades": [
            {"time": t["time"], f"price_{u}": _px(ex, quote, t["price"]), f"qty_{c}": _r(t["qty"]), "side": t["side"]}
            for t in rows
        ],
    }


@tool("Price Candles")
async def ic_candles(
    exchange: Annotated[
        ExchangeId, Field(description="Exchange id; ic_list_exchanges shows which have candles (candles: true)")
    ],
    coin: COIN,
    quote: QUOTE = "IRT",
    interval: Annotated[Literal["15m", "1h", "4h", "1d"], Field(description="Candle size")] = "1h",
    limit: Annotated[int, Field(ge=1, le=100, description="Most recent candles to return (1-100)")] = 48,
) -> dict:
    """Get recent OHLC candles of one coin on one exchange, oldest first.

    Rows are [time, open, high, low, close, volume in coin]; prices are Toman (IRT) or USDT.
    Nobitex and Balinex have no USDT/IRT candles (use Wallex for those).
    """
    ex, c, u = EXCHANGES[exchange], _coin(coin), _unit(quote)
    if ex.candles is None:
        have = ", ".join(e.id for e in EXCHANGES.values() if e.candles)
        raise ToolError(f"{ex.name} publishes no candles here. Use {have}.")
    end = int(time.time())
    rows = await ex.candles(c, quote, RESOLUTIONS[interval], end - (limit + 2) * SECONDS[interval], end)
    return {
        "exchange": ex.id,
        "coin": c,
        "quote": quote,
        "unit": u,
        "interval": interval,
        "columns": ["time", f"open_{u}", f"high_{u}", f"low_{u}", f"close_{u}", f"volume_{c}"],
        "candles": [
            [time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)), *(_px(ex, quote, x) for x in (o, h, lo, cl)), _r(v)]
            for t, o, h, lo, cl, v in rows[-limit:]
        ],
    }


@tool("Top Movers")
async def ic_top_movers(
    exchange: EX,
    quote: QUOTE = "IRT",
    direction: Annotated[Literal["gainers", "losers"], Field(description="'gainers' or 'losers' over 24h")] = "gainers",
    limit: Annotated[int, Field(ge=1, le=25, description="Coins to return (1-25)")] = 10,
    min_volume: Annotated[
        float | None,
        Field(
            description="Skip coins with less 24h volume than this, in Toman (IRT) or USDT. Default 100,000,000 Toman or 10,000 USDT. Coins with unknown volume are kept."
        ),
    ] = None,
) -> dict:
    """List the biggest 24h gainers or losers on one exchange, with illiquid coins filtered out.

    Tabdeal publishes no volume, so its coins are not filtered by volume. Pass a mover's `coin` to
    ic_get_ticker or ic_compare_price for detail.
    """
    ex, u = EXCHANGES[exchange], _unit(quote)
    floor = min_volume if min_volume is not None else (100_000_000 if quote == "IRT" else 10_000)
    rows = [
        (coin, v)
        for coin, r in (await ex.tickers(quote)).items()
        if r["change_pct"] is not None and (v := _view(ex, quote, r))[f"price_{u}"] is not None
    ]
    if not rows:
        raise ToolError(f"{ex.name} publishes no 24h change, so movers cannot be ranked. Use another exchange.")
    rows = [(c, v) for c, v in rows if v[f"volume_24h_{u}"] is None or v[f"volume_24h_{u}"] >= floor]
    rows.sort(key=lambda x: x[1]["change_24h_pct"], reverse=direction == "gainers")
    return {
        "exchange": ex.id,
        "quote": quote,
        "unit": u,
        "direction": direction,
        "movers": [
            {
                "coin": c,
                f"price_{u}": v[f"price_{u}"],
                "change_24h_pct": v["change_24h_pct"],
                f"volume_24h_{u}": v[f"volume_24h_{u}"],
            }
            for c, v in rows[:limit]
        ],
    }


async def _all_tickers(quote: str) -> tuple[dict[str, dict], dict[str, str]]:
    """Every exchange's ticker list for one quote (one cached call each); failures land in errors."""
    exs = list(EXCHANGES.values())
    results = await asyncio.gather(*(e.tickers(quote) for e in exs), return_exceptions=True)
    lists, errors = {}, {}
    for e, r in zip(exs, results, strict=True):
        if isinstance(r, ApiError):
            errors[e.id] = str(r)
        elif isinstance(r, BaseException):
            raise r
        else:
            lists[e.id] = r
    return lists, errors


def _walk(levels: list[list[float]], amount: float, side: str) -> tuple[float, float]:
    """Fill an order against book levels (best first). Returns (received, amount left unfilled).

    buy: amount is quote currency to spend, received is coin. sell: amount is coin, received is quote currency.
    """
    got, left = 0.0, amount
    for p, q in levels:
        if left <= 0:
            break
        if side == "buy":
            take = min(q, left / p)
            left -= take * p
            got += take
        else:
            take = min(q, left)
            left -= take
            got += take * p
    return got, max(left, 0.0)


async def _deal(ex: Exchange, c: str, quote: str, side: str, amount: float) -> dict:
    """One exchange's fill: walks the order book when there is one, else uses the broker's price."""
    u = _unit(quote)
    if ex.book is not None:
        bids, asks = await ex.book(c, quote)
        levels = sorted(asks, key=lambda x: x[0]) if side == "buy" else sorted(bids, key=lambda x: -x[0])
        scale = 10 if ex.rial and quote == "IRT" else 1  # book in Rial: compare in Toman
        levels = [[p / scale, q] for p, q in levels if p > 0 and q > 0]
        if not levels:
            raise ApiError(f"{ex.name} has an empty {c}/{quote} order book.", 404)
        source = "orderbook"
    else:
        row = await _ticker(ex, c, quote)
        v = _view(ex, quote, row)
        price = v[f"ask_{u}"] if side == "buy" else v[f"bid_{u}"]
        source = "broker_price"
        if price is None and v[f"bid_{u}"] is None and v[f"ask_{u}"] is None and v[f"price_{u}"] is not None:
            price, source = v[f"price_{u}"], "listed_price"  # one price only (Tetherland): indicative
        if price is None:
            what = "buy-back" if side == "sell" else "sell"
            raise ApiError(f"{ex.name} publishes no {what} price for {c}/{quote}.", 404)
        levels = [[float(price), float("inf")]]
    got, left = _walk(levels, amount, side)
    done = amount - left
    if got <= 0 or done <= 0:
        raise ApiError(f"{ex.name} could not fill any of the order.", 404)
    avg = done / got if side == "buy" else got / done
    best = levels[0][0]
    return {
        "exchange": ex.id,
        "name_fa": NAMES_FA[ex.id],
        "source": source,
        "receive": _r(got),
        f"avg_price_{u}": _r(avg),
        f"best_price_{u}": _r(best),
        "slippage_pct": _pc(abs(avg / best - 1) * 100),
        "filled_pct": _pc(done / amount * 100),
    }


@tool("Best Deal For An Amount")
async def ic_best_deal(
    coin: COIN,
    side: Annotated[Literal["buy", "sell"], Field(description="'buy' the coin or 'sell' it")],
    amount: Annotated[
        float,
        Field(
            gt=0,
            description="buy: how much Toman (or USDT) to spend, e.g. 50000000. sell: how many coins to sell, e.g. 0.2",
        ),
    ],
    quote: QUOTE = "IRT",
) -> dict:
    """Rank every exchange by what a buy or sell of a given size actually gets, with order-book slippage.

    Answers "where do I get the most USDT for 50M Toman?" or "where should I sell 0.2 BTC?". Order-book exchanges are
    walked level by level (filled_pct < 100 means the visible book is too thin); brokers fill at their own buy/sell
    price; `source: listed_price` (Tetherland, Balinex) is a single indicative price, ranked last. `receive` is coins for
    a buy and Toman/USDT for a sell. Trading and withdrawal fees are not deducted:
    check ic_fees before acting. Prices move every second; this is a snapshot, not a quote.
    """
    c, u = _coin(coin), _unit(quote)
    exs = list(EXCHANGES.values())
    results = await asyncio.gather(*(_deal(e, c, quote, side, amount) for e in exs), return_exceptions=True)
    deals, errors = [], {}
    for e, r in zip(exs, results, strict=True):
        if isinstance(r, ApiError):
            errors[e.id] = str(r)
        elif isinstance(r, BaseException):
            raise r
        else:
            deals.append(r)
    # one listed price (no separate buy/sell price) is indicative, so it ranks after real quotes
    deals.sort(key=lambda d: (d["source"] != "listed_price", d["filled_pct"], d["receive"]), reverse=True)
    return {
        "coin": c,
        "side": side,
        "amount": amount,
        "amount_unit": u if side == "buy" else c.lower(),
        "receive_unit": c.lower() if side == "buy" else u,
        "best": deals[0]["exchange"] if deals else None,
        "deals": deals,
        "errors": errors,
    }


def _leg(v: dict, u: str, side: str) -> tuple[float | None, str]:
    """The price a trader can act on: ask to buy, bid to sell; the last price when the exchange publishes neither."""
    p = v[f"ask_{u}"] if side == "buy" else v[f"bid_{u}"]
    return (p, "ask" if side == "buy" else "bid") if p is not None else (v[f"price_{u}"], "last")


@tool("Arbitrage Scanner")
async def ic_arbitrage(
    quote: QUOTE = "IRT",
    min_gap_pct: Annotated[float, Field(ge=0, le=15, description="Smallest gap to report, in percent")] = 1.0,
    min_exchanges: Annotated[
        int,
        Field(ge=2, le=13, description="Only coins listed on at least this many exchanges (filters obscure tokens)"),
    ] = 3,
    limit: Annotated[int, Field(ge=1, le=25, description="Opportunities to return (1-25)")] = 10,
    executable_only: Annotated[
        bool,
        Field(
            description="Only real bid/ask prices (default). False also uses exchanges that publish just a last price"
        ),
    ] = True,
) -> dict:
    """Scan every coin on every exchange for the biggest price gaps: buy where it is cheapest, sell where it is dearest.

    Buy legs use the ask and sell legs the bid; `basis: last` marks an exchange that publishes only a last price, so
    the gap may not be executable. Gaps over 15% are dropped: they are almost always stale, illiquid markets or two
    different tokens sharing one symbol.
    Fees, withdrawal time and network costs are not included: confirm with ic_get_orderbook and ic_fees.
    """
    u = _unit(quote)
    lists, errors = await _all_tickers(quote)
    legs: dict[str, list[tuple[str, float | None, str, float | None, str]]] = {}
    for ex_id, rows in lists.items():
        ex = EXCHANGES[ex_id]
        for coin, row in rows.items():
            v = _view(ex, quote, row)
            buy, bb = _leg(v, u, "buy")
            sell, sb = _leg(v, u, "sell")
            legs.setdefault(coin, []).append((ex_id, buy, bb, sell, sb))
    found = []
    for coin, rows in legs.items():
        if executable_only:
            rows = [r for r in rows if r[2] != "last" and r[4] != "last"]
        buys = [r for r in rows if r[1]]
        sells = [r for r in rows if r[3]]
        if len(rows) < min_exchanges or not buys or not sells:
            continue
        b = min(buys, key=lambda r: r[1])
        s = max(sells, key=lambda r: r[3])
        if b[0] == s[0]:
            continue
        gap = (s[3] / b[1] - 1) * 100
        if min_gap_pct <= gap <= 15:
            found.append(
                {
                    "coin": coin,
                    "gap_pct": _pc(gap),
                    "buy_on": b[0],
                    f"buy_price_{u}": b[1],
                    "buy_basis": b[2],
                    "sell_on": s[0],
                    f"sell_price_{u}": s[3],
                    "sell_basis": s[4],
                    "exchanges": len(rows),
                }
            )
    found.sort(key=lambda f: f["gap_pct"], reverse=True)
    return {"quote": quote, "unit": u, "count": len(found), "opportunities": found[:limit], "errors": errors}


@tool("Market Overview")
async def ic_market_overview(
    coins: Annotated[
        list[str] | None,
        Field(
            max_length=10,
            description="Coins to summarize (max 10). Default: USDT, BTC, ETH, BNB, TRX, SOL, XRP, DOGE",
        ),
    ] = None,
) -> dict:
    """Get the key Iranian crypto rates in one call: the Tether rate in Toman and major coins across all exchanges.

    For each coin: the median Toman price over the exchanges that list it, the cheapest and dearest exchange, the
    median 24h change and how many exchanges answered. Start here for "what is the dollar/Tether rate" or "how is
    the market today". Use ic_compare_price for every exchange's price of one coin.
    """
    wanted = [_coin(c) for c in coins] if coins else ["USDT", "BTC", "ETH", "BNB", "TRX", "SOL", "XRP", "DOGE"]
    lists, errors = await _all_tickers("IRT")
    out = []
    for c in wanted:
        rows = []
        for ex_id, tickers in lists.items():
            if c in tickers:
                v = _view(EXCHANGES[ex_id], "IRT", tickers[c])
                if v["price_toman"] is not None:
                    rows.append((ex_id, v["price_toman"], v["change_24h_pct"]))
        if not rows:
            out.append({"coin": c, "exchanges": 0})
            continue
        rows.sort(key=lambda r: r[1])
        changes = [r[2] for r in rows if r[2] is not None]
        out.append(
            {
                "coin": c,
                "median_toman": _r(median(r[1] for r in rows)),
                "cheapest": {"exchange": rows[0][0], "price_toman": rows[0][1]},
                "dearest": {"exchange": rows[-1][0], "price_toman": rows[-1][1]},
                "change_24h_pct_median": _pc(median(changes)) if changes else None,
                "exchanges": len(rows),
            }
        )
    return {"unit": "toman", "coins": out, "errors": errors}


@tool("Fees And Networks")
async def ic_fees(
    coin: Annotated[
        str | None,
        Field(description="Coin to compare withdrawals for, e.g. 'USDT'. Omit for trading fees only"),
    ] = None,
    network: Annotated[
        str | None, Field(description="Only this network, e.g. 'TRC20', 'BEP20', 'ERC20', 'TON' (needs coin)")
    ] = None,
) -> dict:
    """Compare trading fees and, for one coin, each exchange's withdrawal networks, fees and minimums.

    Answers "which exchange has the cheapest USDT withdrawal on TRC20?" or "what is the trading fee on Wallex?".
    Trading fees are the entry level (level 1) for Toman markets, in percent; volume tiers lower them. Withdrawal fees
    and minimums are in coin units. `withdraw: false` means that network is closed right now. `cheapest` names the
    lowest open withdrawal fee per network. Exchanges that publish no fee data are listed under `missing`.
    """
    c = _coin(coin) if coin else None
    net = network.upper().replace("-", "") if network else None
    exs = [e for e in EXCHANGES.values() if e.fees]
    results = await asyncio.gather(*(e.fees() for e in exs), return_exceptions=True)
    trade, rows, errors = {}, [], {}
    for e, r in zip(exs, results, strict=True):
        if isinstance(r, ApiError):
            errors[e.id] = str(r)
            continue
        if isinstance(r, BaseException):
            raise r
        trade[e.id] = r["trade"]
        if c:
            for n in r["coins"].get(c, []):
                if not net or n["network"] == net:
                    rows.append({"exchange": e.id, **n})
    out: dict[str, Any] = {"trade_fees_pct": trade}
    if c:
        cheapest: dict[str, dict] = {}
        for n in rows:
            fee = n.get("withdraw_fee")
            if n.get("withdraw") and fee is not None:
                best = cheapest.get(n["network"])
                if best is None or fee < best["withdraw_fee"]:
                    cheapest[n["network"]] = {"exchange": n["exchange"], "withdraw_fee": fee}
        rows.sort(key=lambda n: (n["network"], n.get("withdraw_fee") is None, n.get("withdraw_fee") or 0))
        out |= {"coin": c, "fee_unit": c.lower(), "networks": rows, "cheapest": cheapest}
    out["missing"] = sorted(e.id for e in EXCHANGES.values() if not e.fees)
    out["errors"] = errors
    return out
