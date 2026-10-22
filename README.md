<!-- mcp-name: io.github.sepehr071/iran-crypto-mcp -->

<div align="center">

<img src="https://raw.githubusercontent.com/sepehr071/iran-crypto-mcp/main/.github/banner.png" alt="iran-crypto-mcp: unofficial MCP server for Iranian crypto exchanges, for Claude, Cursor and Copilot" width="100%">

</div>

# iran-crypto-mcp: Iranian crypto exchanges MCP server for AI agents

An unofficial, read-only MCP server for the public market data of Iranian crypto exchanges: Nobitex, Wallex, Bitpin,
Ramzinex, Tabdeal, OMPFinex and Exir. Compare a coin's price across all of them in Toman, read order books and trades,
get candles and top movers.

**Public market data only.** No API keys, no login, no account data, no orders or trading.

## For AI agents

- **What:** unofficial MCP server for the public endpoints of 7 Iranian crypto exchanges.
- **Install:** `uvx iran-crypto-mcp` or `claude mcp add iran-crypto -- uvx iran-crypto-mcp`. Transport: stdio. Needs [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python 3.10+).
- **Auth:** none. Optional `IRAN_CRYPTO_MCP_PROXY` (HTTP proxy URL, or a comma-separated list); system proxy settings are ignored.
- **Access:** read-only, so no confirmation is needed before calling any tool.
- **Tools:** 8, all prefixed `ic_`. Typical sequence: `ic_compare_price` -> `ic_get_orderbook` -> `ic_candles`.
- **Output:** compact JSON. Every IRT price is in **Toman** (field names say `*_toman`); USDT markets say `*_usdt`.
- **Machine-readable:** [`server.json`](server.json).

## Tools

| Tool | Purpose |
|---|---|
| `ic_list_exchanges` | The supported exchanges, their native price unit and what each can answer |
| `ic_list_markets` | Coins an exchange trades against Toman (IRT) or USDT |
| `ic_compare_price` | One coin's price on every exchange in one call, cheapest first, with the spread (an exchange that fails is reported, not fatal) |
| `ic_get_ticker` | Live price, best bid and ask, 24h range, change and volume of a coin on one exchange |
| `ic_get_orderbook` | Top bids and asks (up to 20 levels) with the spread |
| `ic_recent_trades` | Latest public trades, newest first (not on OMPFinex) |
| `ic_candles` | OHLC candles, 15m / 1h / 4h / 1d (Nobitex and Wallex) |
| `ic_top_movers` | Biggest 24h gainers or losers on one exchange, illiquid coins filtered out |

## Units

| Exchange | Published unit | Returned |
|---|---|---|
| Nobitex, Ramzinex, OMPFinex | Rial | Toman (divided by 10) |
| Wallex, Bitpin, Tabdeal, Exir | Toman | Toman |
| USDT markets (all) | USDT | USDT |

The same BTC price differs 10x between Rial and Toman exchanges; this server normalizes it so exchanges compare directly.

## Notes

- Every exchange row carries `name_fa`, the exchange's own Persian name (نوبیتکس, والکس, بیت‌پین, رمزینکس, تبدیل, او ام پی فینکس, اکسیر), so Persian answers do not depend on the model spelling them.

- Replies stay under about 15 KB: order books cap at 20 levels, trades at 50, candles at 100, movers at 25.
- Bid and ask are missing from the Bitpin, Tabdeal, OMPFinex and Exir tickers (use `ic_get_orderbook`); Tabdeal has no volume.
- Nobitex refuses USDT/IRT candles (its CDN answers HTTP 403, browsers included): use Wallex for those.
- Quotes differ between exchanges for real reasons (fees, liquidity, a stale last trade): check the order book before acting on a spread.
- Data is cached for 15 seconds and concurrency is limited to 2 requests.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run python -m pytest -q          # offline unit tests
uv run python -m pytest -m live -q  # calls the real exchange APIs
uv build
```

Not affiliated with any exchange. Prices are informational, not financial advice. MIT license.
