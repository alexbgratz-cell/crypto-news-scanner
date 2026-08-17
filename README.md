# Crypto & AI News Scanner — Public Dashboard

A mobile-friendly, read-only showcase for an automated crypto and AI news scanner. The dashboard presents scored news summaries, source links, categories, instruments, and selected market indicators.

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
- `data/snapshot.json` — approved market values
- `data/history.json` — bounded market history
- `data/categories.json` — dashboard categories
- `data/stats.json` — public counts and update timestamp

## Disclaimer

Summaries and scores are AI-assisted and may contain errors. Original publishers are linked on every article. This project does not provide financial advice.
