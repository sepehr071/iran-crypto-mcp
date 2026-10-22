"""Iranian crypto exchange tools: price comparison, tickers, order books, trades, candles, movers.

All IRT prices are returned in Toman (Rial-quoted exchanges are divided by 10); USDT prices are unchanged.
Field names carry the unit: `price_toman` / `price_usdt`.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .exchanges import EXCHANGES, NAMES_FA, Exchange
from .http import ApiError
from .registry import tool

ExchangeId = Literal["nobitex", "wallex", "bitpin", "ramzinex", "tabdeal", "ompfinex", "exir"]
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
                "orderbook": True,
                "trades": e.trades is not None,
                "candles": e.candles is not None,
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

    Bid and ask are missing for Bitpin, Tabdeal, OMPFinex and Exir; volume is missing for Tabdeal. Use
    ic_get_orderbook for the best bid and ask, ic_candles (Nobitex, Wallex) for price history.
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
    """
    ex, c, u = EXCHANGES[exchange], _coin(coin), _unit(quote)
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

    Not available on OMPFinex. `side` is the taker side when the exchange reports it.
    """
    ex, c, u = EXCHANGES[exchange], _coin(coin), _unit(quote)
    if ex.trades is None:
        raise ToolError(
            f"{ex.name} has no public trades endpoint here. Use nobitex, wallex, bitpin, ramzinex, tabdeal or exir."
        )
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
        Literal["nobitex", "wallex"], Field(description="'nobitex' or 'wallex' (the only exchanges with candles)")
    ],
    coin: COIN,
    quote: QUOTE = "IRT",
    interval: Annotated[Literal["15m", "1h", "4h", "1d"], Field(description="Candle size")] = "1h",
    limit: Annotated[int, Field(ge=1, le=100, description="Most recent candles to return (1-100)")] = 48,
) -> dict:
    """Get recent OHLC candles of one coin from Nobitex or Wallex, oldest first.

    Rows are [time, open, high, low, close, volume in coin]; prices are Toman (IRT) or USDT.
    Nobitex refuses the USDT/IRT candles (use Wallex for those).
    """
    ex, c, u = EXCHANGES[exchange], _coin(coin), _unit(quote)
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
        (coin, _view(ex, quote, r))
        for coin, r in (await ex.tickers(quote)).items()
        if r["change_pct"] is not None and r["last"] is not None
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
