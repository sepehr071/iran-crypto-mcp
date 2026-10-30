import pytest

from iran_crypto_mcp.exchanges import EXCHANGES

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_live_compare_btc(client):
    r = (await client.call_tool("ic_compare_price", {"coin": "BTC"})).structured_content
    assert len(r["prices"]) >= 5, r["errors"]
    assert r["spread_pct"] < 10  # a Rial/Toman mix-up would show 900%


async def test_live_compare_usdt_rate(client):
    r = (await client.call_tool("ic_compare_price", {"coin": "USDT"})).structured_content
    assert len(r["prices"]) >= 5, r["errors"]
    assert 10_000 < r["prices"][0]["price_toman"] < 10_000_000 and r["spread_pct"] < 10


@pytest.mark.parametrize("exchange", sorted(e.id for e in EXCHANGES.values() if e.book))
async def test_live_orderbook(client, exchange):
    r = (
        await client.call_tool("ic_get_orderbook", {"exchange": exchange, "coin": "BTC", "depth": 3})
    ).structured_content
    assert r["bids"] and r["asks"] and r["bids"][0][0] <= r["asks"][0][0] * 1.01


@pytest.mark.parametrize("exchange", sorted(EXCHANGES))
async def test_live_ticker(client, exchange):
    r = (await client.call_tool("ic_get_ticker", {"exchange": exchange, "coin": "ETH"})).structured_content
    assert r["price_toman"] > 0


@pytest.mark.parametrize("exchange", sorted(EXCHANGES))
async def test_live_ticker_usdt_rate(client, exchange):
    quote = "USDT" if exchange == "kifpool" else "IRT"  # Kifpool lists no USDT/IRT price
    coin = "BTC" if quote == "USDT" else "USDT"
    r = (
        await client.call_tool("ic_get_ticker", {"exchange": exchange, "coin": coin, "quote": quote})
    ).structured_content
    price = r[f"price_{quote.lower() if quote == 'USDT' else 'toman'}"]
    assert 10_000 < price < 10_000_000  # USDT in Toman, or BTC in USDT


@pytest.mark.parametrize("exchange", ["nobitex", "wallex", "bitpin", "ramzinex", "tabdeal", "exir", "bit24"])
async def test_live_trades(client, exchange):
    r = (
        await client.call_tool("ic_recent_trades", {"exchange": exchange, "coin": "BTC", "limit": 5})
    ).structured_content
    assert r["trades"]


@pytest.mark.parametrize("exchange", ["nobitex", "wallex"])
async def test_live_candles(client, exchange):
    r = (await client.call_tool("ic_candles", {"exchange": exchange, "coin": "BTC", "limit": 5})).structured_content
    assert r["candles"]


async def test_live_candles_match_ticker_scale(client):
    c = (await client.call_tool("ic_candles", {"exchange": "nobitex", "coin": "BTC", "limit": 1})).structured_content
    t = (await client.call_tool("ic_get_ticker", {"exchange": "nobitex", "coin": "BTC"})).structured_content
    assert 0.8 < c["candles"][-1][4] / t["price_toman"] < 1.25


async def test_live_top_movers(client):
    r = (await client.call_tool("ic_top_movers", {"exchange": "wallex", "limit": 5})).structured_content
    assert r["movers"]


async def test_live_bitpin_has_volume(client):
    r = (await client.call_tool("ic_get_ticker", {"exchange": "bitpin", "coin": "BTC"})).structured_content
    assert r["volume_24h_toman"] > 1e9


async def test_live_tabdeal_ticker_has_change(client):
    r = (await client.call_tool("ic_get_ticker", {"exchange": "tabdeal", "coin": "BTC"})).structured_content
    assert r["price_basis"] == "last" and r["change_24h_pct"] is not None and r["high_24h_toman"] > r["low_24h_toman"]


async def test_live_tabdeal_unknown_trades_fail_fast(client):
    result = await client.call_tool("ic_recent_trades", {"exchange": "tabdeal", "coin": "NOPEZZ"})
    assert result.is_error and "ic_list_markets" in result.content[0].text


async def test_live_wallex_candles_unknown_coin_is_error(client):
    assert (await client.call_tool("ic_candles", {"exchange": "wallex", "coin": "NOPEZZ"})).is_error


async def test_live_nobitex_usdt_candles_blocked_message(client):
    # Nobitex's CDN refuses USDTIRT candles; if it ever stops, candles come back and this test should go
    r = await client.call_tool("ic_candles", {"exchange": "nobitex", "coin": "USDT", "limit": 3})
    assert r.is_error is False or "blocks candle requests" in r.content[0].text


async def test_live_list_markets(client):
    r = (await client.call_tool("ic_list_markets", {"exchange": "exir"})).structured_content
    assert "BTC" in r["coins"]


async def test_live_unknown_coin_is_error(client):
    assert (await client.call_tool("ic_get_orderbook", {"exchange": "nobitex", "coin": "NOPEZZ"})).is_error


@pytest.mark.parametrize("exchange", sorted(e.id for e in EXCHANGES.values() if e.candles))
async def test_live_candles_sane_toman(client, exchange):
    coin, lo, hi = ("USDT", 10_000, 10_000_000) if exchange == "abantether" else ("BTC", 1e10, 1e11)
    r = (await client.call_tool("ic_candles", {"exchange": exchange, "coin": coin, "limit": 5})).structured_content
    rows = r["candles"]
    assert rows and [x[0] for x in rows] == sorted(x[0] for x in rows)
    assert lo < rows[-1][4] < hi  # a Rial/Toman mix-up is 10x off


async def test_live_balinex_mixed_case_market_has_candles(client):
    # the chart feed wants the market id as listed (BabyDogeIRT), not BABYDOGEIRT
    r = (
        await client.call_tool("ic_candles", {"exchange": "balinex", "coin": "BABYDOGE", "limit": 3})
    ).structured_content
    assert r["candles"]


async def test_live_fees_usdt(client):
    r = (await client.call_tool("ic_fees", {"coin": "USDT"})).structured_content
    assert len({n["exchange"] for n in r["networks"]}) >= 6, r["errors"]
    assert r["cheapest"] and all(0 < t["taker_pct"] < 2 for t in r["trade_fees_pct"].values())
