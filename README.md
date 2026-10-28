<!-- mcp-name: io.github.sepehr071/iran-crypto-mcp -->

<div align="center">

<img src="https://raw.githubusercontent.com/sepehr071/iran-crypto-mcp/main/.github/banner.png" alt="iran-crypto-mcp: unofficial MCP server for Iranian crypto exchanges, for Claude, Cursor and Copilot" width="100%">

</div>

<div align="center">

# iran-crypto-mcp: Iranian crypto exchanges MCP server for AI agents

**Compare any coin across twelve Iranian crypto exchanges, in Toman, from Claude, Cursor or Copilot.**<br>
An unofficial, read-only MCP server (model context protocol) for the public market data of [Nobitex](https://nobitex.ir), [Wallex](https://wallex.ir), [Bitpin](https://bitpin.ir), [Ramzinex](https://ramzinex.com),<br>
[Tabdeal](https://tabdeal.org), [OMPFinex](https://ompfinex.com), [Exir](https://exir.io), [Bit24](https://bit24.cash), [Aban Tether](https://abantether.ir),<br>
[Kifpool](https://kifpool.me), [OK-EX](https://ok-ex.io) and [Tetherland](https://tetherland.com): prices, order books, recent trades, candles and top movers.

[![PyPI](https://img.shields.io/pypi/v/iran-crypto-mcp?color=2563eb)](https://pypi.org/project/iran-crypto-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/iran-crypto-mcp)](https://pypi.org/project/iran-crypto-mcp/)
[![CI](https://github.com/sepehr071/iran-crypto-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/sepehr071/iran-crypto-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](https://github.com/sepehr071/iran-crypto-mcp/blob/main/LICENSE)

[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_iran--crypto--mcp-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=iran-crypto&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22iran-crypto-mcp%22%5D%7D)

[For AI agents](#for-ai-agents) · [Why](#why) · [Quick start](#quick-start) · [Exchanges](#supported-exchanges) · [Tools](#tools) · [FAQ](#faq) · [فارسی](#فارسی)

</div>

---

## For AI agents

- **What:** unofficial MCP server for the public market data of 12 Iranian crypto exchanges and brokers (Nobitex, Wallex, Bitpin, Ramzinex, Tabdeal, OMPFinex, Exir, Bit24, Aban Tether, Kifpool, OK-EX, Tetherland).
- **Install:** `uvx iran-crypto-mcp` or `claude mcp add iran-crypto -- uvx iran-crypto-mcp`. Transport: stdio. Needs [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python 3.10+).
- **Auth:** none, no API key or account. Optional `IRAN_CRYPTO_MCP_PROXY` (HTTP proxy URL, or a comma-separated list); system proxy settings are ignored.
- **Access:** read-only. No login, balances, orders or trading, so no confirmation is needed before calling any tool.
- **Tools:** 8, all prefixed `ic_`. Start with `ic_compare_price`. Typical sequence: `ic_compare_price` -> `ic_get_orderbook` -> `ic_candles`.
- **Use when:** comparing a coin's price across Iranian exchanges, checking a spread, reading an order book or recent trades, or listing 24h movers.
- **Do not use when:** the user wants to buy, sell or transfer, needs account data, or wants non-Iranian exchanges.
- **Output:** compact JSON. Every IRT price is in **Toman** (field names say `*_toman`); USDT markets say `*_usdt`.
- **Machine-readable:** [`server.json`](https://github.com/sepehr071/iran-crypto-mcp/blob/main/server.json) and the [iran-mcp catalog](https://github.com/sepehr071/iran-mcp).

## Why

- **Rial vs Toman.** Nobitex, Ramzinex and OMPFinex publish Rial; Wallex, Bitpin, Tabdeal and Exir publish Toman. The same BTC price differs 10x between them. This server converts everything to Toman, so the numbers compare directly.
- **Seven sites to check by hand.** Each exchange has its own app, its own symbols and its own API shape. One call to `ic_compare_price` returns the coin on every exchange, cheapest first, with the spread.
- **Persian names.** Every exchange row carries `name_fa`, the exchange's own Persian name, so answers in Persian do not depend on the model spelling it.

## What it can do

- Compare one coin's price on all twelve exchanges, in Toman or USDT, cheapest first, with the spread
- Get a ticker: last price, 24h range and change, volume, and best bid/ask where the exchange publishes them
- Read order books (up to 20 levels per side) and recent public trades
- Pull OHLC candles (15m, 1h, 4h, 1d) from Nobitex and Wallex
- Rank the biggest 24h gainers and losers on an exchange, with illiquid coins filtered out
- List the coins an exchange trades against Toman or USDT

Example prompts:

- "Compare the BTC price on all Iranian exchanges in Toman. Where is it cheapest?"
- "What is the Tether (USDT) rate in Toman on each exchange right now?"
- "Show the Nobitex ETH order book. Is the spread real?"
- "Top 5 gainers on Wallex in the last 24 hours."
- "Give me 1-hour BTC candles from Wallex for the last two days."
- <span dir="rtl">قیمت بیت&zwnj;کوین را در همه صرافی&zwnj;های ایرانی به تومان مقایسه کن.</span>
- <span dir="rtl">قیمت تتر الان در کدام صرافی ارزان&zwnj;تر است؟</span>
- <span dir="rtl">دفتر سفارش اتریوم در نوبیتکس را نشان بده.</span>

## Supported exchanges

| Exchange | Persian name | Site | Published unit | Ticker | Order book | Trades | Candles |
|---|---|---|---|---|---|---|---|
| Nobitex | نوبیتکس | [nobitex.ir](https://nobitex.ir) | Rial | full | yes | yes | yes (not USDT/IRT) |
| Wallex | والکس | [wallex.ir](https://wallex.ir) | Toman | full | yes | yes | yes |
| Bitpin | بیت&zwnj;پین | [bitpin.ir](https://bitpin.ir) | Toman | no bid/ask | yes | yes | no |
| Ramzinex | رمزینکس | [ramzinex.com](https://ramzinex.com) | Rial | full | yes | yes | no |
| Tabdeal | تبدیل | [tabdeal.org](https://tabdeal.org) | Toman | no bid/ask, no volume | yes | yes | no |
| OMPFinex | او ام پی فینکس | [ompfinex.com](https://ompfinex.com) | Rial | no bid/ask | yes | no | no |
| Exir | اکسیر | [exir.io](https://exir.io) | Toman | no bid/ask | yes | yes | no |
| Bit24 | بیت ۲۴ | [bit24.cash](https://bit24.cash) | Toman | no bid/ask | no | no | no |
| Aban Tether (broker) | آبان تتر | [abantether.ir](https://abantether.ir) | Toman | buy/sell price, no high/low | no | no | no |
| Kifpool (broker) | کیف پول من | [kifpool.me](https://kifpool.me) | Toman | sell price only; no USDT/IRT | no | no | no |
| OK-EX (broker) | اوکی اکسچنج | [ok-ex.io](https://ok-ex.io) | Toman | buy/sell price, no high/low | no | no | no |
| Tetherland (broker) | تترلند | [tetherland.com](https://tetherland.com) | Toman | one price, no high/low | no | no | no |

- All twelve give prices in both quotes: Toman (IRT) and USDT. USDT markets stay in USDT.
- "Full" means last price, bid, ask, 24h high and low, change and volume.
- Bitpin volume comes from a best-effort second call; if it fails, the ticker has no volume.
- Exir publishes base volume only, so quote volume is approximated as volume times last price.
- Use `ic_get_orderbook` for the best bid and ask where the ticker lacks them.
- Nobitex refuses USDT/IRT candles (its CDN answers HTTP 403, browsers included): use Wallex for those.

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). No API key or account.

<details open>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add iran-crypto -- uvx iran-crypto-mcp
```
</details>

<details>
<summary><b>Claude Desktop</b></summary>

Settings → Developer → Edit Config, then add:

```json
{
  "mcpServers": {
    "iran-crypto": { "command": "uvx", "args": ["iran-crypto-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Cursor</b></summary>

Add the Claude Desktop block to `~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project).
</details>

<details>
<summary><b>VS Code (Copilot agent mode)</b></summary>

Click **Install in VS Code** above, or add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "iran-crypto": { "type": "stdio", "command": "uvx", "args": ["iran-crypto-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Codex CLI</b></summary>

```bash
codex mcp add iran-crypto -- uvx iran-crypto-mcp
```

Or in `~/.codex/config.toml`:

```toml
[mcp_servers.iran-crypto]
command = "uvx"
args = ["iran-crypto-mcp"]
```
</details>

<details>
<summary><b>Gemini CLI</b></summary>

Add the Claude Desktop block to `~/.gemini/settings.json`, or run:

```bash
gemini mcp add iran-crypto uvx iran-crypto-mcp
```
</details>

<details>
<summary><b>Windsurf</b></summary>

Add the Claude Desktop block to `~/.codeium/windsurf/mcp_config.json`.
</details>

<details>
<summary><b>Anything else</b></summary>

It is a standard stdio MCP server: run `uvx iran-crypto-mcp`, or `pip install iran-crypto-mcp` and run `iran-crypto-mcp`.
</details>

## Tools

All 8 tools are read-only. Exchange ids: `nobitex`, `wallex`, `bitpin`, `ramzinex`, `tabdeal`, `ompfinex`, `exir`. `quote` is `IRT` (Toman, default) or `USDT`.

| Tool | What it does |
|---|---|
| `ic_list_exchanges` | The supported exchanges, their native price unit and what each can answer |
| `ic_list_markets` | Coins an exchange trades against Toman (IRT) or USDT |
| `ic_compare_price` | One coin's price on every exchange in one call, cheapest first, with the spread (an exchange that fails is reported, not fatal) |
| `ic_get_ticker` | Live price, best bid and ask, 24h range, change and volume of a coin on one exchange |
| `ic_get_orderbook` | Top bids and asks (1 to 20 levels) with the spread |
| `ic_recent_trades` | Latest public trades, newest first (1 to 50; Nobitex, Wallex, Bitpin, Ramzinex, Tabdeal, Exir) |
| `ic_candles` | OHLC candles, 15m / 1h / 4h / 1d, up to 100 (Nobitex and Wallex) |
| `ic_top_movers` | Biggest 24h gainers or losers on one exchange, illiquid coins filtered out (up to 25) |

## How it works

```text
  AI agent  (Claude, Cursor, Copilot, ...)
      │
      │  MCP over stdio
      ▼
  iran-crypto-mcp  (runs on your machine)
      │
      │  HTTPS
      └──────▶  public market-data endpoints of the 12 exchanges
```

- Runs locally and calls the public endpoints the exchanges' own websites use. No hosted server in between, no API key.
- Uses a Chrome-like HTTP client and sends each exchange's own Origin and Referer, like its website does.
- Answers are cached for 15 seconds and at most 2 requests run at once. Failed GET requests are retried.
- Read-only: it never calls account, order or trading endpoints.

## Good to know

- **Units are normalized.** IRT prices are Toman (Rial exchanges are divided by 10); USDT prices are unchanged. The unit is in the field name: `price_toman`, `price_usdt`.
- **`name_fa`** on every exchange row is the exchange's own Persian name.
- **One exchange can fail alone.** `ic_compare_price` reports a failing exchange under `errors` and still compares the rest. HTTP 403 means that exchange's CDN refuses your network. Set `IRAN_CRYPTO_MCP_PROXY` to an Iranian HTTP proxy and try again.
- **A spread is not always real.** Quotes differ for real reasons (fees, liquidity, a stale last trade). Check the order book on both exchanges before acting on it.
- **Reply sizes are capped** to stay under about 15 KB: order books 20 levels, trades 50, candles 100, movers 25.
- **Top movers** skip coins below a 24h volume floor (default 100,000,000 Toman or 10,000 USDT; set `min_volume` to change it). Tabdeal publishes no volume, so its coins are not filtered.
- **Prices are informational.** They are live-ish (15 s cache), not a quote you can trade at.

## FAQ

<details>
<summary><b>Is there an API or MCP server for Iranian crypto exchanges?</b></summary>

`iran-crypto-mcp` is an unofficial MCP server with 8 read-only tools over the public endpoints of Nobitex, Wallex,
Bitpin, Ramzinex, Tabdeal, OMPFinex, Exir, Bit24, Aban Tether, Kifpool, OK-EX and Tetherland. It needs no API key.
</details>

<details>
<summary><b>How do I compare crypto prices in Toman with Claude, Cursor or Copilot?</b></summary>

Add the server (see [Quick start](#quick-start)) and ask, for example, "compare BTC on all Iranian exchanges in Toman".
The agent calls `ic_compare_price`, which returns every exchange's price in Toman, cheapest first, with the spread.
</details>

<details>
<summary><b>Why are some prices 10x different between exchanges?</b></summary>

Some exchanges publish Rial and others Toman (1 Toman = 10 Rial). This server converts everything to Toman. If you still
see a big gap, it is a real difference or a stale quote: check the order book.
</details>

<details>
<summary><b>Can it trade, or see my balance?</b></summary>

No. It has no login or keys and never calls account, order or trading endpoints. It only reads public market data.
</details>

<details>
<summary><b>An exchange shows an error with HTTP 403. What now?</b></summary>

That exchange's CDN refuses requests from your network. The other exchanges still work. Set `IRAN_CRYPTO_MCP_PROXY` to
an HTTP proxy with an Iranian exit and retry. System proxy variables are ignored on purpose.
</details>

<details>
<summary><b>Why does a ticker have no bid/ask or volume?</b></summary>

Bitpin, Tabdeal, OMPFinex, Exir, Bit24 and Tetherland publish no bid/ask in their ticker feeds, and Tabdeal publishes no volume. Call
`ic_get_orderbook` for the best bid and ask. The brokers (Aban Tether, Kifpool, OK-EX, Tetherland) and Bit24 have no public order book.
</details>

<details>
<summary><b>Why are there no candles for most exchanges?</b></summary>

Only Nobitex and Wallex publish candles through public endpoints this server uses. Nobitex refuses USDT/IRT candles; use Wallex for those.
</details>

<details>
<summary><b>Claude Desktop says <code>uvx</code> is not found</b></summary>

Use the full path to `uvx` (`where uvx` on Windows, `which uvx` on macOS/Linux) as `command`.
</details>

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `IRAN_CRYPTO_MCP_PROXY` | unset | Optional HTTP proxy URL for exchange requests; system proxy settings are ignored. A comma-separated list is used one at a time: the next proxy takes over when one is blocked or fails to connect |

<a id="فارسی"></a>

## فارسی

<div dir="rtl">

**iran-crypto-mcp** یک سرور MCP غیررسمی و فقط&zwnj;خواندنی برای داده&zwnj;های عمومی بازار صرافی&zwnj;های ارز دیجیتال ایرانی است: نوبیتکس، والکس، بیت&zwnj;پین، رمزینکس، تبدیل، او ام پی فینکس و اکسیر. به دستیار هوش مصنوعی شما (Claude، Cursor، Copilot و ...) اجازه می&zwnj;دهد قیمت یک ارز را در همه صرافی&zwnj;ها به تومان مقایسه کند.

**چه کارهایی می&zwnj;کند**

- **مقایسه قیمت صرافی&zwnj;ها**: قیمت بیت&zwnj;کوین، تتر و بقیه ارزها در هر هفت صرافی با یک درخواست، از ارزان به گران، همراه با اختلاف قیمت.
- **قیمت تتر و بیت&zwnj;کوین به تومان**: قیمت صرافی&zwnj;هایی که ریال می&zwnj;دهند تقسیم بر ۱۰ می&zwnj;شود، پس همه عددها قابل مقایسه&zwnj;اند.
- **دفتر سفارش** (تا ۲۰ سطح)، **معاملات اخیر** و **کندل&zwnj;ها** (۱۵ دقیقه، ۱ ساعت، ۴ ساعت، روزانه؛ فقط نوبیتکس و والکس).
- **بیشترین رشد و افت ۲۴ ساعته** هر صرافی، بدون ارزهای کم&zwnj;حجم.

**نکته&zwnj;ها**

- فقط داده عمومی بازار؛ بدون ورود به حساب، کلید API، خرید و فروش.
- اگر یک صرافی خطای ۴۰۳ بدهد، یعنی شبکه شما را نمی&zwnj;پذیرد؛ بقیه صرافی&zwnj;ها کار می&zwnj;کنند. متغیر `IRAN_CRYPTO_MCP_PROXY` را روی یک پروکسی HTTP ایرانی بگذارید.
- اختلاف قیمت همیشه فرصت واقعی نیست؛ قبل از هر تصمیم دفتر سفارش را ببینید.
- قیمت&zwnj;ها ۱۵ ثانیه نگه داشته می&zwnj;شوند و توصیه مالی نیستند.

**نصب** (اول [uv](https://docs.astral.sh/uv/getting-started/installation/) را نصب کنید). در Claude Code:

</div>

```bash
claude mcp add iran-crypto -- uvx iran-crypto-mcp
```

<div dir="rtl">

در Claude Desktop، Cursor و بقیه برنامه&zwnj;ها همان تنظیم بخش [Quick start](#quick-start) را بگذارید.

**نمونه پرسش&zwnj;ها**

- «قیمت بیت&zwnj;کوین را در همه صرافی&zwnj;ها به تومان مقایسه کن.»
- «تتر در کدام صرافی ارزان&zwnj;تر است؟»
- «دفتر سفارش اتریوم در نوبیتکس را نشان بده.»
- «بیشترین رشد ۲۴ ساعته در والکس چیست؟»

</div>

## Development

```bash
git clone https://github.com/sepehr071/iran-crypto-mcp && cd iran-crypto-mcp
uv sync
uv run ruff check . && uv run ruff format --check .
uv run python -m pytest -q          # offline unit tests
uv run python -m pytest -m live -q  # calls the real exchange APIs
uv build
```

Tools live in `src/iran_crypto_mcp/markets.py`, the per-exchange adapters in `exchanges.py`. Issues and PRs are welcome.
Releases: bump the version in `pyproject.toml` and `server.json`, then push a `v*` tag.

## Related servers

- [iran-mcp](https://github.com/sepehr071/iran-mcp): catalog of all Iranian-service MCP servers
- [tgju-mcp](https://github.com/sepehr071/tgju-mcp) for dollar, gold, coin and commodity prices from TGJU
- [tsetmc-mcp](https://github.com/sepehr071/tsetmc-mcp) for the Tehran stock exchange
- [milligold-mcp](https://github.com/sepehr071/milligold-mcp) for gold prices
- [iran-news-mcp](https://github.com/sepehr071/iran-news-mcp) for Iranian news

## Disclaimer

Unofficial and not affiliated with or endorsed by any exchange. It reads public market data only: no keys, no account
access, no trading. Prices are informational, not financial advice, and the endpoints can change without notice.

## Author

<a href="https://www.linkedin.com/in/sepehr-radmard"><img src="https://raw.githubusercontent.com/sepehr071/iran-mcp/main/assets/author-banner.png" alt="Sepehr Radmard: open-source MCP servers for the Iranian web. LinkedIn: linkedin.com/in/sepehr-radmard" width="100%"></a>

Built and maintained by **Sepehr Radmard** (سپهر رادمرد). Connect on [LinkedIn](https://www.linkedin.com/in/sepehr-radmard) or follow [@sepehr071 on GitHub](https://github.com/sepehr071) for more MCP servers for Iranian websites, all listed in [iran-mcp](https://github.com/sepehr071/iran-mcp). If this saves you time, a star on the repo helps other people find it.

## License

[MIT](https://github.com/sepehr071/iran-crypto-mcp/blob/main/LICENSE)
