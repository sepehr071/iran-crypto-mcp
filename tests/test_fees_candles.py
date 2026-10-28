import time
from types import SimpleNamespace

import pytest
from conftest import fixture

from iran_crypto_mcp import exchanges, markets
from iran_crypto_mcp.http import ApiError

pytestmark = pytest.mark.anyio

R = "/exchange/api/v1.0/exchange"
FEE_ROUTES = {
    "/v2/options": "nobitex_fees_options.json",
    "/v2/structured-fee": "wallex_fees_structured.json",
    "/v1/currencies": "wallex_fees_currencies.json",
    "/v4/mkt/commissions/": "bitpin_fees_commissions.json",
    "/v1/mkt/network_settings/": "bitpin_fees_network_settings.json",
    f"{R}/networks": "ramzinex_fees_networks.json",
    f"{R}/currencies": "ramzinex_fees_currencies.json",
    "/r/treasury/currency_transaction_fees/": "tabdeal_fees.json",
    "/r/get-commission-levels-v2/": "tabdeal_fees_levels.json",
    "/r/treasury/currencies/static/": "tabdeal_fees_static.json",
    "/v2/currencies": "ompfinex_fees.json",
    "/v2/constants": "exir_fees.json",
    "/v2/tiers": "exir_fees_tiers.json",
    "/api/v3/fee-table": "bit24_fees.json",
    "/api/graphql": "kifpool_fees.json",
    "/sno/oapi/levels": "okex_fees.json",
    "/api/v5/currencies-list": "tetherland_fees.json",
}
# exchange -> (route path, fixture); the bar times in each fixture decide the clock the test runs at
CANDLE_ROUTES = {
    "bitpin": ("/v1/mkt/tv/get_bars/", "bitpin_candles.json"),
    "ramzinex": (f"{R}/chart/tv/v2.0/history", "ramzinex_candles.json"),
    "tabdeal": ("/r/plots/history/", "tabdeal_candles.json"),
    "ompfinex": ("/v2/udf/real/history", "ompfinex_candles.json"),
    "exir": ("/v2/chart", "exir_candles.json"),
    "bit24": ("/api/v3/markets/ohclv", "bit24_candles.json"),
}
# fixture time of the newest bar (+1 s) per exchange
NOW = {
    "bitpin": 1793172601,
    "ramzinex": 1793172602,
    "tabdeal": 1793118601,
    "ompfinex": 1793170801,
    "exir": 1792029601,  # 2026-10-15T02:00:01Z
    "bit24": 1793170801,
    "abantether": 1793175864,
}
HARD_CODED = ["ramzinex", "ompfinex", "bit24", "abantether"]


@pytest.fixture
def fees_api(api):
    for path, name in FEE_ROUTES.items():
        api[path] = fixture(name)
    return api


def at(monkeypatch, now: int):
    """ic_candles asks for the last N bars up to now: pin now to the fixture's clock."""
    monkeypatch.setattr(markets, "time", SimpleNamespace(time=lambda: now, strftime=time.strftime, gmtime=time.gmtime))


async def test_fees_usdt_rows_from_every_exchange_with_cheapest(client, fees_api):
    r = (await client.call_tool("ic_fees", {"coin": "USDT"})).structured_content
    assert r["errors"] == {} and r["missing"] == []
    have = {n["exchange"] for n in r["networks"]}
    assert have >= {"nobitex", "wallex", "bitpin", "ramzinex", "tabdeal", "ompfinex", "exir", "bit24", "tetherland"}
    assert all({"network", "withdraw_fee", "min_withdraw", "withdraw", "deposit"} <= set(n) for n in r["networks"])
    assert r["cheapest"], r
    for net, best in r["cheapest"].items():
        open_fees = [n["withdraw_fee"] for n in r["networks"] if n["network"] == net and n["withdraw"]]
        assert best["withdraw_fee"] == min(f for f in open_fees if f is not None)
    # one row per exchange and network
    keys = [(n["exchange"], n["network"]) for n in r["networks"]]
    assert len(keys) == len(set(keys))


async def test_fees_nobitex_usdt_normalises_networks(client, fees_api):
    r = (await client.call_tool("ic_fees", {"coin": "USDT", "network": "BEP20"})).structured_content
    nobitex = next(n for n in r["networks"] if n["exchange"] == "nobitex")
    assert nobitex["withdraw_fee"] == 0.7 and nobitex["withdraw"] is True
    assert {n["network"] for n in r["networks"]} == {"BEP20"}


async def test_fees_without_coin_gives_trade_fees_for_all_12(client, fees_api):
    r = (await client.call_tool("ic_fees", {})).structured_content
    assert set(r["trade_fees_pct"]) == set(exchanges.EXCHANGES) and len(r["trade_fees_pct"]) == 12
    for t in r["trade_fees_pct"].values():
        assert 0 < t["maker_pct"] < 2 and 0 < t["taker_pct"] < 2  # percent, not a fraction
    assert r["trade_fees_pct"]["wallex"]["taker_pct"] == 0.3 and r["trade_fees_pct"]["tabdeal"]["maker_pct"] == 0.33


@pytest.mark.parametrize("exchange", HARD_CODED)
async def test_hard_coded_trade_fees_are_dated(client, fees_api, exchange):
    r = (await client.call_tool("ic_fees", {})).structured_content
    assert "as of 2026-10-28" in r["trade_fees_pct"][exchange]["note"]


async def test_fees_ramzinex_merges_duplicate_networks(fees_api):
    rows = (await exchanges.EXCHANGES["ramzinex"].fees())["coins"]["USDT"]
    names = [n["network"] for n in rows]
    assert len(names) == len(set(names)) and {"TRC20", "BEP20", "TON", "ARBITRUM"} <= set(names)


async def test_fees_exir_network_override_closes_withdrawal(fees_api):
    usdt = {n["network"]: n for n in (await exchanges.EXCHANGES["exir"].fees())["coins"]["USDT"]}
    assert usdt["TRC20"]["withdraw_fee"] == 2.0 and usdt["TRC20"]["withdraw"] is False
    assert usdt["BEP20"]["withdraw"] is True


@pytest.mark.parametrize("exchange", sorted(CANDLE_ROUTES))
async def test_candles_toman_oldest_first(client, api, monkeypatch, exchange):
    path, name = CANDLE_ROUTES[exchange]
    api[path] = fixture(name)
    at(monkeypatch, NOW[exchange])
    r = (await client.call_tool("ic_candles", {"exchange": exchange, "coin": "BTC", "limit": 100})).structured_content
    rows = r["candles"]
    assert rows and r["columns"][1] == "open_toman"
    assert [x[0] for x in rows] == sorted({x[0] for x in rows})  # oldest first, no repeats
    for _t, o, h, low, c, v in rows:
        assert 1e10 < o < 1e11 and 1e10 < c < 1e11 and low <= h and v >= 0, rows[0]


async def test_candles_ramzinex_rial_is_divided_by_ten(client, api, monkeypatch):
    path, name = CANDLE_ROUTES["ramzinex"]
    raw = fixture(name)
    api[path] = raw
    at(monkeypatch, NOW["ramzinex"])
    raw_rows = await exchanges.EXCHANGES["ramzinex"].candles("BTC", "IRT", "60", 0, 2**40)
    assert raw_rows[0][1] == float(raw["o"][0]) > 1e11  # the adapter stays in Rial
    r = (await client.call_tool("ic_candles", {"exchange": "ramzinex", "coin": "BTC", "limit": 100})).structured_content
    assert r["candles"][0][1] == pytest.approx(float(raw["o"][0]) / 10, rel=1e-6)
    assert api.calls[-1].url.params["symbol"] == "btcirr"


@pytest.mark.parametrize("exchange", ["tabdeal", "exir"])
async def test_candles_big_feeds_send_bounded_range_and_filter(api, exchange):
    path, name = CANDLE_ROUTES[exchange]
    api[path] = fixture(name)
    start, end = NOW[exchange] - 3 * 3600, NOW[exchange]
    rows = await exchanges.EXCHANGES[exchange].candles("BTC", "IRT", "60", start, end)
    q = api.calls[-1].url.params
    assert (int(q["from"]), int(q["to"])) == (start, end)
    assert rows and all(start <= x[0] <= end for x in rows)


async def test_candles_exir_dedupes_and_drops_stray_old_bar(api):
    api["/v2/chart"] = fixture("exir_candles.json")
    rows = await exchanges.EXCHANGES["exir"].candles("BTC", "IRT", "60", 1_790_000_000, 2**31)
    assert len(fixture("exir_candles.json")) == 8 and len(rows) == 4
    assert [x[0] for x in rows] == sorted({x[0] for x in rows}) and rows[0][0] > 1_790_000_000


async def test_candles_ompfinex_four_hour_aggregates_hourly_bars(api):
    api["/v2/udf/real/history"] = fixture("ompfinex_candles.json")
    hourly = await exchanges.EXCHANGES["ompfinex"].candles("BTC", "IRT", "60", 0, 2**40)
    four = await exchanges.EXCHANGES["ompfinex"].candles("BTC", "IRT", "240", 0, 2**40)
    assert api.calls[-1].url.params["resolution"] == "60" and 2 <= len(four) <= 3 and four[0][0] % 14400 == 0
    assert sum(x[5] for x in four) == pytest.approx(sum(x[5] for x in hourly))
    assert hourly[0][1] > 1e11  # x10: Toman feed, Rial adapter


async def test_candles_abantether_usdt_irt_only(client, api, monkeypatch):
    api["/otc_reporting/tradingview/history"] = fixture("abantether_candles.json")
    at(monkeypatch, NOW["abantether"])
    r = (
        await client.call_tool("ic_candles", {"exchange": "abantether", "coin": "USDT", "interval": "1h", "limit": 5})
    ).structured_content
    assert len(r["candles"]) == 5 and 10_000 < r["candles"][0][1] < 1_000_000
    assert api.calls[-1].url.params["symbol"] == "USDT/IRT"
    for args in ({"coin": "BTC"}, {"coin": "USDT", "interval": "15m"}):
        result = await client.call_tool("ic_candles", {"exchange": "abantether", **args})
        assert result.is_error


async def test_candles_abantether_raises_api_error_for_other_symbols():
    with pytest.raises(ApiError):
        await exchanges.EXCHANGES["abantether"].candles("ETH", "IRT", "60", 0, 1)


async def test_candles_bit24_empty_market_is_empty(api):
    api["/api/v3/markets/ohclv"] = {"data": {"message": ""}}
    assert await exchanges.EXCHANGES["bit24"].candles("USDT", "IRT", "60", 0, 2**40) == []
