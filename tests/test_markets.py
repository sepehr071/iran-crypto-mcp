import json

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
    "/api/v2/manager/coins": "abantether_coins.json",
    "/api/graphql": "kifpool_headercoins.json",
    "/api/v1/asset/otc/tickers": "okex_tickers.json",
    "/api/v1/asset/otc/tickers/usd": "okex_tickers_usd.json",
    "/api/v5/currencies": "tetherland_currencies.json",
    "/api/v3/markets": "bit24_markets.json",
    "/api/v3/mega-menu-coins": "bit24_otc_quotes.json",
    "/api/v3/markets/BTC-IRT/order-books": "bit24_orderbook.json",
    "/api/v3/markets/BTC-IRT/trades": "bit24_trades.json",
    "/landing/v3/markets/0/500": "balinex_markets.json",
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
        "ic_best_deal",
        "ic_arbitrage",
        "ic_market_overview",
        "ic_fees",
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
    assert prices == sorted(prices) and r["spread_pct"] < 5  # fixtures were captured minutes apart; brokers add spread
    assert r["cheapest"] == r["prices"][0]["exchange"] and r["priciest"] == r["prices"][-1]["exchange"]


async def test_compare_price_tolerates_one_exchange_down(client, up):
    up["/v1/markets"] = lambda r: httpx.Response(503)
    r = (await client.call_tool("ic_compare_price", {"coin": "USDT"})).structured_content
    assert "wallex" in r["errors"] and len(r["prices"]) + len(r["errors"]) == len(exchanges.EXCHANGES)
    assert 100_000 < r["prices"][0]["price_toman"] < 1_000_000


async def test_compare_price_unlisted_coin_is_reported_per_exchange(client, up):
    r = (await client.call_tool("ic_compare_price", {"coin": "NOPE"})).structured_content
    assert r["prices"] == [] and len(r["errors"]) == len(exchanges.EXCHANGES)


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


@pytest.mark.parametrize("exchange", sorted(e.id for e in exchanges.EXCHANGES.values() if e.book))
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
    assert by_host["publicapi.ramzinex.ir"]["origin"] == "https://ramzinex.ir"
    assert by_host["api.ramzinex.ir"]["referer"] == "https://ramzinex.ir/"
    assert by_host["api.ramzinex.ir"]["sec-fetch-site"] == "same-site"
    assert by_host["api.ramzinex.ir"]["accept"] == "*/*" and by_host["api.ramzinex.ir"]["pragma"] == "no-cache"


async def test_each_site_gets_its_own_frontend_headers(client, up):
    await client.call_tool("ic_compare_price", {"coin": "BTC"})
    sent = {c.url.host: c.headers for c in up.calls}
    assert sent["api.wallex.ir"]["platform"] == "web" and sent["api.wallex.ir"]["accept"] == http.AXIOS
    assert "referer" not in sent["azapi.ok-ex.io"] and sent["azapi.ok-ex.io"]["origin"] == "https://ok-ex.io"
    assert sent["kifpool.me"]["sec-fetch-site"] == "same-origin" and "origin" in sent["kifpool.me"]  # POST
    assert sent["pro.bit24.cash"]["origin"] == "https://bit24.cash"
    assert sent["api.ompfinex.com"]["origin"] == "https://my.ompfinex.com"
    assert sent["apiv2.nobitex.ir"]["accept-language"] == http.EN
    assert (
        sent["api.balinex.com"]["origin"] == "https://www.balinex.com"
        and sent["api.balinex.com"]["accept"] == http.AXIOS
    )


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


async def test_compare_price_reports_a_blocked_exchange(client, up):
    up["/v2/ticker/all"] = lambda r: httpx.Response(403, text="<html>blocked</html>")
    r = (await client.call_tool("ic_compare_price", {"coin": "BTC"})).structured_content
    assert "403" in r["errors"]["exir"] and "other exchanges" in r["errors"]["exir"].lower()
    assert "exir" not in {p["exchange"] for p in r["prices"]} and len(r["prices"]) == len(exchanges.EXCHANGES) - 1


@pytest.mark.parametrize("exchange", ["abantether", "kifpool", "okex", "tetherland"])
async def test_broker_ticker_and_no_orderbook(client, up, exchange):
    t = (await client.call_tool("ic_get_ticker", {"exchange": exchange, "coin": "BTC"})).structured_content
    assert 1e10 < t["price_toman"] < 1e11  # Toman, not Rial
    u = (
        await client.call_tool("ic_get_ticker", {"exchange": exchange, "coin": "BTC", "quote": "USDT"})
    ).structured_content
    assert 10_000 < u["price_usdt"] < 1_000_000
    book = await client.call_tool("ic_get_orderbook", {"exchange": exchange, "coin": "BTC"})
    assert book.is_error and "no order book" in book.content[0].text


async def test_okex_orders_inverted_bid_ask_and_uses_mid(client, up):
    r = (await client.call_tool("ic_get_ticker", {"exchange": "okex", "coin": "BTC"})).structured_content
    assert r["bid_toman"] <= r["ask_toman"] and r["price_basis"] == "mid"


async def test_kifpool_posts_its_persisted_query(client, up):
    await client.call_tool("ic_get_ticker", {"exchange": "kifpool", "coin": "BTC"})
    req = next(c for c in up.calls if c.url.path == "/api/graphql")
    assert req.method == "POST" and b"headerCoins" in req.content


async def test_kifpool_changed_feed_is_a_clear_error(client, up):
    up["/api/graphql"] = {"errors": [{"message": "PersistedQueryNotFound"}]}
    result = await client.call_tool("ic_get_ticker", {"exchange": "kifpool", "coin": "BTC"})
    assert result.is_error and "changed its price feed" in result.content[0].text


async def test_bit24_usdt_rate_comes_from_the_instant_feed(client, up):
    r = (await client.call_tool("ic_get_ticker", {"exchange": "bit24", "coin": "USDT"})).structured_content
    assert 100_000 < r["price_toman"] < 1_000_000


def test_walk_fills_across_levels():
    from iran_crypto_mcp.markets import _walk

    asks = [[100.0, 1.0], [110.0, 2.0]]
    assert _walk(asks, 320.0, "buy") == (3.0, 0.0)  # 100 + 2 x 110
    assert _walk(asks, 1000.0, "buy") == (3.0, 680.0)  # book too thin: 680 left unspent
    bids = [[100.0, 1.0], [90.0, 1.0]]
    assert _walk(bids, 1.5, "sell") == (145.0, 0.0)


async def test_best_deal_buy_ranks_by_coins_received(client, up):
    r = (
        await client.call_tool("ic_best_deal", {"coin": "BTC", "side": "buy", "amount": 100_000_000})
    ).structured_content
    deals = r["deals"]
    assert r["receive_unit"] == "btc" and deals and r["best"] == deals[0]["exchange"]
    real = [d for d in deals if d["source"] != "listed_price"]
    assert [d["receive"] for d in real] == sorted((d["receive"] for d in real), reverse=True)
    assert deals[-1]["source"] == "listed_price"  # indicative single prices rank last
    nob = next(d for d in deals if d["exchange"] == "nobitex")
    assert 1e10 < nob["avg_price_toman"] < 1e11 and nob["avg_price_toman"] >= nob["best_price_toman"]  # Toman


async def test_best_deal_sell_kifpool_has_no_buyback_price(client, up):
    r = (await client.call_tool("ic_best_deal", {"coin": "BTC", "side": "sell", "amount": 0.01})).structured_content
    assert "buy-back" in r["errors"]["kifpool"] and r["receive_unit"] == "toman"
    assert all(d["receive"] > 1e6 for d in r["deals"])


async def test_arbitrage_reports_executable_gaps(client, up):
    r = (await client.call_tool("ic_arbitrage", {"min_gap_pct": 0, "min_exchanges": 2})).structured_content
    for o in r["opportunities"]:
        assert o["buy_on"] != o["sell_on"] and 0 <= o["gap_pct"] <= 15
        assert o["buy_basis"] == "ask" and o["sell_basis"] == "bid"


async def test_market_overview_median_and_range(client, up):
    r = (await client.call_tool("ic_market_overview", {"coins": ["usdt", "btc"]})).structured_content
    usdt, btc = r["coins"]
    assert usdt["coin"] == "USDT" and 100_000 < usdt["median_toman"] < 1_000_000
    assert btc["cheapest"]["price_toman"] <= btc["median_toman"] <= btc["dearest"]["price_toman"]
    assert btc["exchanges"] == len(exchanges.EXCHANGES)


async def test_bit24_trades_have_no_side(client, up):
    r = (
        await client.call_tool("ic_recent_trades", {"exchange": "bit24", "coin": "BTC", "limit": 3})
    ).structured_content
    t = r["trades"]
    assert len(t) == 3 and "side" not in t[0] and 1e10 < t[0]["price_toman"] < 1e11


async def test_list_exchanges_reports_what_balinex_offers(client):
    ex = {e["id"]: e for e in (await client.call_tool("ic_list_exchanges", {})).structured_content["exchanges"]}
    b = ex["balinex"]
    assert (b["source_unit"], b["orderbook"], b["trades"], b["candles"], b["fees"]) == (
        "toman",
        False,
        False,
        True,
        False,
    )


async def test_balinex_ticker_is_toman_with_stats_but_no_bid_ask(client, up):
    r = (await client.call_tool("ic_get_ticker", {"exchange": "balinex", "coin": "btc"})).structured_content
    row = next(m for m in fixture("balinex_markets.json")["result"]["items"] if m["_id"] == "BTCIRT")
    assert r["price_toman"] == int(row["price"]) > 1e10  # already Toman: not divided by 10
    assert r["bid_toman"] is None and r["ask_toman"] is None and r["price_basis"] == "last"
    assert r["high_24h_toman"] == int(row["highPrice24h"]) and r["low_24h_toman"] == int(row["lowPrice24h"])
    assert r["volume_24h_toman"] == int(row["volume"]) and r["change_24h_pct"] == float(row["percentChange24h"])


async def test_balinex_usdt_irt_has_a_price_but_no_stats(client, up):
    r = (await client.call_tool("ic_get_ticker", {"exchange": "balinex", "coin": "USDT"})).structured_content
    assert 100_000 < r["price_toman"] < 1_000_000
    assert r["high_24h_toman"] is None and r["volume_24h_toman"] is None and r["change_24h_pct"] is None


async def test_balinex_usdt_market_keeps_usdt(client, up):
    r = (
        await client.call_tool("ic_get_ticker", {"exchange": "balinex", "coin": "BTC", "quote": "USDT"})
    ).structured_content
    assert r["unit"] == "usdt" and 1000 < r["price_usdt"] < 1_000_000 and "price_toman" not in r


async def test_balinex_lists_the_coins_of_each_quote(client, up):
    irt = (await client.call_tool("ic_list_markets", {"exchange": "balinex"})).structured_content
    usdt = (await client.call_tool("ic_list_markets", {"exchange": "balinex", "quote": "USDT"})).structured_content
    assert irt["coins"] == ["BABYDOGE", "BTC", "ETH", "USDT"] and usdt["coins"] == ["BABYDOGE", "BTC", "ETH"]


async def test_balinex_posts_the_landing_market_list(client, up):
    await client.call_tool("ic_get_ticker", {"exchange": "balinex", "coin": "BTC"})
    req = next(c for c in up.calls if c.url.host == "api.balinex.com")
    assert req.method == "POST" and req.url.path == "/landing/v3/markets/0/500"
    assert json.loads(req.content)["sortMarkets"] == "MOST_VOLUME"
    assert req.headers["origin"] == "https://www.balinex.com" and req.headers["referer"] == "https://www.balinex.com/"


async def test_balinex_follows_pages_until_total_is_reached(client, up):
    doc = fixture("balinex_markets.json")
    items = doc["result"]["items"]
    for page, chunk in enumerate((items[:3], items[3:])):  # 3 IRT markets, then the other IRT market and the USDT ones
        up[f"/landing/v3/markets/{page}/500"] = {**doc, "result": {"items": chunk, "total": len(items)}}
    r = (await client.call_tool("ic_list_markets", {"exchange": "balinex", "quote": "USDT"})).structured_content
    assert r["coins"] == ["BABYDOGE", "BTC", "ETH"]
    assert [c.url.path for c in up.calls if c.url.host == "api.balinex.com"] == [
        "/landing/v3/markets/0/500",
        "/landing/v3/markets/1/500",
    ]


async def test_balinex_changed_list_is_a_clear_error(client, up):
    up["/landing/v3/markets/0/500"] = {"status": 500, "message": {"en": "boom"}}
    result = await client.call_tool("ic_get_ticker", {"exchange": "balinex", "coin": "BTC"})
    assert result.is_error and "changed its market list" in result.content[0].text


@pytest.mark.parametrize(
    "body",
    [[], "oops", {}, {"result": None}, {"result": {"items": None, "total": 0}}, {"result": {"items": {"a": 1}}}],
)
async def test_balinex_malformed_list_is_reported_not_fatal(client, up, body):
    up["/landing/v3/markets/0/500"] = body
    r = (await client.call_tool("ic_compare_price", {"coin": "BTC"})).structured_content
    assert "changed its market list" in r["errors"]["balinex"]
    assert len(r["prices"]) == len(exchanges.EXCHANGES) - 1  # every other exchange still answered


@pytest.mark.parametrize("total", ["missing", None, "abc", "nan", -1])
async def test_balinex_unusable_total_means_a_single_page(client, up, total):
    doc = fixture("balinex_markets.json")
    if total == "missing":
        del doc["result"]["total"]
    else:
        doc["result"]["total"] = total
    up["/landing/v3/markets/0/500"] = doc
    r = (await client.call_tool("ic_compare_price", {"coin": "BTC"})).structured_content
    assert "balinex" in {p["exchange"] for p in r["prices"]} and "balinex" not in r["errors"]
    assert sum(c.url.host == "api.balinex.com" for c in up.calls) == 1


async def test_balinex_skips_malformed_rows_and_unusable_prices(client, up):
    doc = fixture("balinex_markets.json")
    good = doc["result"]["items"]

    def row(base, **kw):
        return {**good[0], "_id": base + "IRT", "baseCurrency": base, **kw}

    bad = [
        None,
        "x",
        {"_id": "NOPEIRT", "quoteCurrency": "IRT", "price": "5"},  # no baseCurrency
        {"baseCurrency": "NOPE", "quoteCurrency": "IRT", "price": "5"},  # no _id
        row("AAA", price="NaN"),
        row("BBB", price="Infinity"),
        row("CCC", price="-5"),
        row("DDD", price="0"),
        row("EEE", price=None),
        row("FFF", enabled=False),
    ]
    doc["result"]["items"] = bad + good
    doc["result"]["total"] = len(bad + good)
    up["/landing/v3/markets/0/500"] = doc
    r = (await client.call_tool("ic_list_markets", {"exchange": "balinex"})).structured_content
    assert r["coins"] == ["BABYDOGE", "BTC", "ETH", "USDT"]


async def test_balinex_non_finite_stats_are_treated_as_missing(client, up):
    doc = fixture("balinex_markets.json")
    btc = doc["result"]["items"][0]
    assert btc["_id"] == "BTCIRT"
    btc |= {"percentChange24h": "NaN", "volume": "Infinity", "highPrice24h": "-Infinity", "lowPrice24h": "nan"}
    up["/landing/v3/markets/0/500"] = doc
    t = (await client.call_tool("ic_get_ticker", {"exchange": "balinex", "coin": "BTC"})).structured_content
    assert t["price_toman"] > 1e10 and t["change_24h_pct"] is None and t["volume_24h_toman"] is None
    assert t["high_24h_toman"] is None and t["low_24h_toman"] is None
    for tool, args in (
        ("ic_compare_price", {"coin": "BTC"}),
        ("ic_market_overview", {"coins": ["BTC"]}),
        ("ic_best_deal", {"coin": "BTC", "side": "buy", "amount": 100_000_000}),
        ("ic_arbitrage", {"min_gap_pct": 0, "min_exchanges": 2}),
    ):
        assert not (await client.call_tool(tool, args)).is_error, tool


async def test_balinex_has_no_orderbook_or_trades_here(client, up):
    book = await client.call_tool("ic_get_orderbook", {"exchange": "balinex", "coin": "BTC"})
    assert book.is_error and "no order book" in book.content[0].text and "instant-swap" not in book.content[0].text
    assert (await client.call_tool("ic_recent_trades", {"exchange": "balinex", "coin": "BTC"})).is_error


async def test_best_deal_ranks_balinex_as_an_indicative_listed_price(client, up):
    r = (await client.call_tool("ic_best_deal", {"coin": "BTC", "side": "sell", "amount": 0.01})).structured_content
    bal = next(d for d in r["deals"] if d["exchange"] == "balinex")
    assert bal["source"] == "listed_price" and bal["filled_pct"] == 100 and bal["receive"] > 1e6


async def test_arbitrage_never_trades_on_balinex_last_prices_by_default(client, up):
    # Balinex publishes no bid/ask, so its legs are `last` and only count when executable_only is off
    r = (await client.call_tool("ic_arbitrage", {"min_gap_pct": 0, "min_exchanges": 2})).structured_content
    assert all("balinex" not in (o["buy_on"], o["sell_on"]) for o in r["opportunities"])


@pytest.mark.parametrize("body", [exchanges.KIFPOOL_BODY, exchanges.KIFPOOL_FEES_BODY])
def test_kifpool_query_text_matches_its_persisted_hash(body):
    import hashlib

    text = (exchanges.QUERIES / f"kifpool_{body['operationName']}.graphql").read_text(encoding="utf-8")
    assert hashlib.sha256(text.encode()).hexdigest() == body["extensions"]["persistedQuery"]["sha256Hash"]


async def test_kifpool_forgotten_hash_is_resent_with_the_query(client, up):
    data = fixture("kifpool_headercoins.json")
    up["/api/graphql"] = lambda r: httpx.Response(
        200,
        json=data
        if b'"query"' in r.content
        else {"errors": [{"message": "PersistedQueryNotFound", "extensions": {"code": "PERSISTED_QUERY_NOT_FOUND"}}]},
    )
    r = (await client.call_tool("ic_get_ticker", {"exchange": "kifpool", "coin": "BTC"})).structured_content
    assert r["price_toman"] > 1e10
    sent = [c for c in up.calls if c.url.path == "/api/graphql"]
    assert len(sent) == 2 and b"query headerCoins" in sent[1].content
