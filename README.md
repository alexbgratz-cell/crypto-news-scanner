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

- 🪙 Crypto / 🤖 AI — scored news with German summaries (WAS / WIRKUNG / KONSEQUENZ), filters and original-source links
- 📊 Märkte — instrument tiles with sparklines from the scanner snapshot
- 🔗 Links — curated trading pages, opened directly in a new tab
- 📺 Live — external pages embedded live (Coinglass liquidation heatmap, liquidations, funding rates; TradingView BTC chart). The CSP `frame-src` allowlist is limited to `www.coinglass.com` and `s.tradingview.com`.

## Disclaimer

Summaries and scores are AI-assisted and may contain errors. Original publishers are linked on every article. This project does not provide financial advice.
