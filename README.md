# Crypto & AI News Scanner — Public Dashboard

A mobile-friendly, read-only showcase for an automated crypto and AI news scanner. The dashboard presents scored news summaries, source links, categories, and a curated list of trading links.

## Public architecture

This repository contains only static HTML and sanitized JSON. It has:

- no application server;
- no admin or write endpoints;
- no scanner credentials;
- no Telegram configuration;
- no logs, pending queue, deduplication state, or delivery history;
- no network path back to the private scanner runtime.

The private scanner exports data through explicit field allowlists. GitHub Pages serves the resulting static files over HTTPS.

## Public data files

- `data/news.json` — approved article metadata and summaries
- `data/links.json` — curated trading links (manually maintained)
- `data/categories.json` — dashboard categories
- `data/stats.json` — public counts and update timestamp

## Dashboard tabs

- 🏠 Home — latest AI market report (data/`market-report.json`, generated at 09:00 / 15:30 / 22:00, refresh button via ntfy relay with 5-min cooldown) + the report's numbers table (Instrument | Wert | 24h)
- 🪙 Crypto / 🤖 AI — scored news with German summaries (WAS / WIRKUNG / KONSEQUENZ), filters and original-source links. Default score floor per stream: Crypto ≥ 8, AI ≥ 7 (adjustable per tab)
- 🎓 Lernen — explanations for scoring, the market instruments, app usage and live views (data in `data/learn.json`)
- 📺 Live — external pages embedded live (Coinglass liquidation heatmap, liquidations, funding rates, order book; TradingView BTC chart). The CSP `frame-src` allowlist is limited to `www.coinglass.com` and `s.tradingview.com`.
- 🔗 Links — curated trading pages, opened directly in a new tab

## Disclaimer

Summaries and scores are AI-assisted and may contain errors. Original publishers are linked on every article. This project does not provide financial advice.
