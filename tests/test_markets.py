import httpx
import pytest
from conftest import fixture

from iran_crypto_mcp import exchanges, http

pytestmark = pytest.mark.anyio

ROUTES = {
    "/market/stats": "nobitex_stats.json",
    "/v3/orderbook/BTCIRT": "nobitex_orderbook.json",
    "/v2/trades/BTCIRT": "nobitex_trades.json",
    "/market/udf/history": "nobitex_udf.json",
    "/v1/markets": "wallex_markets.json",
    "/v1/depth": "wallex_depth.json",
    "/v1/trades": "wallex_trades.json",
    "/v1/udf/history": "wallex_udf.json",
    "/api/v1/mkt/tickers/": "bitpin_tickers.json",
    "/api/v1/mth/orderbook/BTC_IRT/": "bitpin_orderbook.json",
    "/api/v1/mth/matches/BTC_IRT/": "bitpin_matches.json",
    "/exchange/api/v1.0/exchange/pairs": "ramzinex_pairs.json",
    "/exchange/api/v1.0/exchange/orderbooks/2/buys_sells": "ramzinex_book.json",
    "/exchange/api/v1.0/exchange/orderbooks/2/trades": "ramzinex_trades.json",
    "/r/api/v1/depth": "tabdeal_depth.json",
    "/r/api/v1/trades": "tabdeal_trades.json",
    "/r/plots/currencies/dynamic-info/": "tabdeal_dynamic_info.json",
    "/v5/mkt/markets/": "bitpin_markets_v5.json",
    "/v2/market": "omp_market.json",
    "/v1/market/1/depth": "omp_depth.json",
    "/v2/ticker/all": "exir_ticker_all.json",
    "/v2/orderbooks": "exir_orderbook.json",
    "/v2/trades": "exir_trades.json",
}


@pytest.fixture
def up(api):
    for path, name in ROUTES.items():
        api[path] = fixture(name)
    depth = fixture("tabdeal_depth.json")  # only BTCIRT exists; other symbols are unknown, like upstream
    api["/r/api/v1/depth"] = lambda r: (
        httpx.Response(200, json=depth)
        if r.url.params["symbol"] == "BTCIRT"
        else httpx.Response(404, json={"code": 1206})
    )
    return api


async def test_all_tools_are_read_only(client):
    tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == {
        "ic_list_exchanges",
        "ic_list_markets",
        "ic_compare_price",
        "ic_get_ticker",
        "ic_get_orderbook",
        "ic_recent_trades",
        "ic_candles",
        "ic_top_movers",
    }
    for t in tools:
        assert t.annotations.read_only_hint is True, t.name
        assert t.description and t.title, t.name


async def test_list_exchanges(client):
    ex = (await client.call_tool("ic_list_exchanges", {})).structured_content["exchanges"]
    assert {e["id"] for e in ex} == set(exchanges.EXCHANGES)
    assert {e["id"]: e["source_unit"] for e in ex}["nobitex"] == "rial"


async def test_compare_price_normalizes_rial_to_toman(client, up):
    r = (await client.call_tool("ic_compare_price", {"coin": "btc"})).structured_content
    assert r["unit"] == "toman" and not r["errors"]
    by = {p["exchange"]: p for p in r["prices"]}
    assert set(by) == set(exchanges.EXCHANGES)
    # the raw fixtures are 10x apart (Rial vs Toman); normalized prices agree within 2%
    assert by["nobitex"]["price_toman"] == int(fixture("nobitex_stats.json")["stats"]["btc-rls"]["latest"]) / 10
    assert by["tabdeal"]["price_basis"] == "last" and by["tabdeal"]["change_24h_pct"] is not None
    prices = [p["price_toman"] for p in r["prices"]]
    assert prices == sorted(prices) and r["spread_pct"] < 2
    assert r["cheapest"] == r["prices"][0]["exchange"] and r["priciest"] == r["prices"][-1]["exchange"]


async def test_compare_price_tolerates_one_exchange_down(client, up):
    up["/v1/markets"] = lambda r: httpx.Response(503)
    r = (await client.call_tool("ic_compare_price", {"coin": "USDT"})).structured_content
    assert set(r["errors"]) == {"wallex"} and len(r["prices"]) == 6
    assert 100_000 < r["prices"][0]["price_toman"] < 1_000_000


async def test_compare_price_unlisted_coin_is_reported_per_exchange(client, up):
    r = (await client.call_tool("ic_compare_price", {"coin": "NOPE"})).structured_content
    assert r["prices"] == [] and len(r["errors"]) == 7


async def test_invalid_coin_rejected(client, up):
    result = await client.call_tool("ic_get_ticker", {"exchange": "nobitex", "coin": "../x"})
    assert result.is_error


async def test_underscore_coin_symbol_is_accepted(client, up):
    # Nobitex lists coins like 1K_SHIB; they must pass validation (an unknown one is a market error, not a symbol error)
    result = await client.call_tool("ic_get_ticker", {"exchange": "nobitex", "coin": "1K_NOPE"})
    assert result.is_error and "not a coin symbol" not in result.content[0].text


async def test_ticker_without_price_is_an_error(client, up):
    # Tabdeal lists a market whose feed row has no price yet
    up["/r/plots/currencies/dynamic-info/"] = {"currencies": {"BTC": {"IRT": {"price": None}}}}
    result = await client.call_tool("ic_get_ticker", {"exchange": "tabdeal", "coin": "BTC"})
    assert result.is_error and "no price" in result.content[0].text


async def test_ticker_usdt_market_keeps_usdt(client, up):
    r = (
        await client.call_tool("ic_get_ticker", {"exchange": "nobitex", "coin": "BTC", "quote": "USDT"})
    ).structured_content
    assert r["unit"] == "usdt" and 1000 < r["price_usdt"] < 1_000_000 and "price_toman" not in r


async def test_ticker_bitpin_has_volume_but_no_bid_ask(client, up):
    r = (await client.call_tool("ic_get_ticker", {"exchange": "bitpin", "coin": "BTC"})).structured_content
    assert r["price_toman"] > 1e9 and r["bid_toman"] is None
    assert r["volume_24h_toman"] == int(fixture("bitpin_markets_v5.json")["results"][0]["order_book_info"]["value"])


async def test_ticker_bitpin_survives_v5_list_down(client, up):
    up["/v5/mkt/markets/"] = lambda r: httpx.Response(503)
    r = (await client.call_tool("ic_get_ticker", {"exchange": "bitpin", "coin": "BTC"})).structured_content
    assert r["price_toman"] > 1e9 and r.get("volume_24h_toman") is None


@pytest.mark.parametrize("exchange", sorted(exchanges.EXCHANGES))
async def test_orderbook_every_exchange(client, up, exchange):
    r = (
        await client.call_tool("ic_get_orderbook", {"exchange": exchange, "coin": "BTC", "depth": 5})
    ).structured_content
    bids, asks = r["bids"], r["asks"]
    assert 0 < len(bids) <= 5 and 0 < len(asks) <= 5
    assert bids == sorted(bids, reverse=True) and asks == sorted(asks)
    assert 1e9 < bids[0][0] < 1e11  # Toman, not Rial


@pytest.mark.parametrize("exchange", ["nobitex", "wallex", "bitpin", "ramzinex", "tabdeal", "exir"])
async def test_recent_trades(client, up, exchange):
    r = (
        await client.call_tool("ic_recent_trades", {"exchange": exchange, "coin": "BTC", "limit": 3})
    ).structured_content
    t = r["trades"]
    assert len(t) == 3 and t[0]["time"] >= t[1]["time"] >= t[2]["time"]
    assert t[0]["side"] in ("buy", "sell") and 1e9 < t[0]["price_toman"] < 1e11


async def test_recent_trades_unsupported(client, up):
    assert (await client.call_tool("ic_recent_trades", {"exchange": "ompfinex", "coin": "BTC"})).is_error


@pytest.mark.parametrize("exchange", ["nobitex", "wallex"])
async def test_candles_are_toman(client, up, exchange):
    r = (await client.call_tool("ic_candles", {"exchange": exchange, "coin": "BTC", "limit": 4})).structured_content
    assert len(r["candles"]) == 4 and r["columns"][1] == "open_toman"
    assert 1e9 < r["candles"][0][1] < 1e11
    assert up.calls[-1].url.params["resolution"] == "60"


async def test_top_movers_sorted_and_filtered(client, up):
    r = (
        await client.call_tool("ic_top_movers", {"exchange": "nobitex", "direction": "losers", "min_volume": 0})
    ).structured_content
    ch = [m["change_24h_pct"] for m in r["movers"]]
    assert ch == sorted(ch)


async def test_top_movers_tabdeal_uses_feed_change(client, up):
    r = (await client.call_tool("ic_top_movers", {"exchange": "tabdeal", "limit": 3})).structured_content
    assert r["movers"] and all(m.get("volume_24h_toman") is None for m in r["movers"])


async def test_nobitex_blocked_candle_market_names_the_block(client, up):
    up["/market/udf/history"] = lambda r: httpx.Response(403, text="<html>Access Denied</html>")
    result = await client.call_tool("ic_candles", {"exchange": "nobitex", "coin": "USDT"})
    assert result.is_error and "blocks candle requests" in result.content[0].text and "wallex" in result.content[0].text


async def test_wallex_candles_unknown_coin_is_an_error(client, up):
    up["/v1/udf/history"] = {"errmsg": "Invalid symbol!", "s": "error"}
    result = await client.call_tool("ic_candles", {"exchange": "wallex", "coin": "ZZZZ"})
    assert result.is_error and "ic_list_markets" in result.content[0].text


async def test_tabdeal_trades_unknown_coin_fails_fast(client, up):
    result = await client.call_tool("ic_recent_trades", {"exchange": "tabdeal", "coin": "NOPE"})
    assert result.is_error and "ic_list_markets" in result.content[0].text
    assert not any(c.url.path == "/r/api/v1/trades" for c in up.calls)


async def test_list_markets(client, up):
    r = (await client.call_tool("ic_list_markets", {"exchange": "tabdeal"})).structured_content
    assert "BTC" in r["coins"] and r["count"] == len(r["coins"])


async def test_requests_carry_the_exchange_site_as_origin(client, up):
    await client.call_tool("ic_get_orderbook", {"exchange": "ramzinex", "coin": "BTC"})
    by_host = {c.url.host: c.headers for c in up.calls}
    assert by_host["publicapi.ramzinex.com"]["origin"] == "https://ramzinex.com"
    assert by_host["api.ramzinex.com"]["referer"] == "https://ramzinex.com/"
    assert by_host["api.ramzinex.com"]["sec-fetch-site"] == "same-site"


async def test_unknown_host_refused():
    with pytest.raises(http.ApiError):
        await http.fetch("https://example.com/x")


async def test_upstream_400_names_the_market(client, up):
    up["/v3/orderbook/BTCIRT"] = lambda r: httpx.Response(400, json={"status": "failed"})
    result = await client.call_tool("ic_get_orderbook", {"exchange": "nobitex", "coin": "BTC"})
    assert result.is_error and "ic_list_markets" in result.content[0].text


async def test_persian_names_come_with_the_data(client, up):
    ex = (await client.call_tool("ic_list_exchanges", {})).structured_content["exchanges"]
    names = {e["id"]: e["name_fa"] for e in ex}
    assert set(names) == set(exchanges.EXCHANGES) and names["bitpin"] == "بیت‌پین" and names["wallex"] == "والکس"
    rows = (await client.call_tool("ic_compare_price", {"coin": "BTC"})).structured_content["prices"]
    assert {p["exchange"]: p["name_fa"] for p in rows} == names
