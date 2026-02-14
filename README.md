# Prebloom Reddit Ticker Scout

## Overview

Prebloom Reddit Ticker Scout is a read-only analytics tool that scans selected investing-related subreddits to identify publicly traded U.S. companies that may be in the early stages of increased discussion ("pre-bloom" phase).

The purpose of this tool is personal research and idea discovery. It aggregates ticker mention counts and highlights securities that show increasing discussion momentum while maintaining a historically low baseline of chatter.

This project does **not** post, comment, vote, message users, or automate any interaction on Reddit.

---

## What It Does

The script:

1. Authenticates using Reddit’s official API via OAuth (read-only).
2. Retrieves recent public submissions and limited top-level comments from selected subreddits.
3. Extracts potential stock ticker symbols from text.
4. Validates tickers against official exchange symbol directories.
5. Excludes:
   - ETFs
   - Biotechnology / pharmaceutical listings
   - Deposit receipt / ADR listings
   - Large-cap “usual suspect” tickers
6. Aggregates mentions into time buckets:
   - 0–7 days
   - 8–30 days
   - 31–90 days
7. Calculates a simple momentum score to identify tickers with:
   - Low historical baseline
   - Increasing recent discussion
8. Outputs results to a CSV file for personal analysis.

---

## What It Does NOT Do

- Does NOT post to Reddit.
- Does NOT comment on Reddit.
- Does NOT vote.
- Does NOT message users.
- Does NOT scrape private, deleted, or non-public content.
- Does NOT store usernames or personal user data.
- Does NOT republish full Reddit content.

The tool only aggregates mention counts and includes example thread titles/links for context.

---

## Subreddits Analyzed

- r/stocks
- r/stockmarket
- r/wallstreetbets
- r/valueinvesting
- r/pennystocks
- r/Swingtrading
- r/stockstobuytoday
- r/stocksandtrading
- r/wallstreetbetselite
- r/shortsqueeze
- r/stockmarketmovers
- r/smallcapstocks
- r/optionmillionaires

---

## Technical Details

- Language: Python 3.13
- Reddit API Access: PRAW (Python Reddit API Wrapper)
- Exchange validation: Nasdaq Trader symbol directory files
- Output: CSV report for offline research

All requests respect Reddit API rate limits.

---

## Intended Use

This tool is designed for:

- Personal investment research
- Identifying emerging discussion trends
- Organizing cross-subreddit idea discovery

It is not intended for automated trading, market manipulation, or redistribution of Reddit content.

---

## License

This project is for educational and personal research purposes only.
