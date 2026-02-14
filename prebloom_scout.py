import re
import time
import csv
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict

import requests
import praw

# =========================
# 1) YOU EDIT THESE
# =========================
REDDIT_CLIENT_ID = "YOUR_CLIENT_ID"
REDDIT_CLIENT_SECRET = "YOUR_CLIENT_SECRET"
REDDIT_USER_AGENT = "prebloom-scout:v1.1 (by u/YOUR_USERNAME)"

SUBREDDITS = [
    "stocks",
    "stockmarket",
    "wallstreetbets",
    "valueinvesting",
    "pennystocks",
    "Swingtrading",
    "stockstobuytoday",
    "stocksandtrading",
    "wallstreetbetselite",
    "shortsqueeze",
    "stockmarketmovers",
    "smallcapstocks",
    "optionmillionaires",
]

DAYS_BACK = 90
POST_LIMIT_PER_SUB = 1000      # Reddit listing cap-ish per subreddit
SCAN_COMMENTS = True
TOP_LEVEL_COMMENT_LIMIT = 75   # per post

# =========================
# 2) VERIFIED TICKER SOURCES (Nasdaq Trader)
# =========================
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"

# =========================
# 3) EXCLUSIONS / FILTERS
# =========================

# "Usual suspects" list — add/remove freely
EXCLUDE_TICKERS = {
    # mega/household
    "AAPL", "MSFT", "AMZN", "GOOG", "GOOGL", "META", "NVDA", "AMD", "TSLA", "NFLX",
    "ORCL", "INTC", "CSCO", "IBM", "ADBE", "CRM", "QCOM", "AVGO", "TXN",

    # popular finance/market proxies (also removed by ETF flag, but keep anyway)
    "SPY", "QQQ", "DIA", "IWM", "VTI",

    # perma-reddit / legacy memes (edit to taste)
    "GME", "AMC", "BB", "NOK", "PLTR",

    # common “crypto proxy” chatter magnets (edit to taste)
    "COIN", "MARA", "RIOT"
}

# Common false positives / acronyms that look like tickers
STOPWORDS = {
    "A", "I", "DD", "CEO", "CFO", "USA", "US", "GDP", "IPO", "AI", "EV", "FOMO", "YOLO",
    "SEC", "FED", "IMO", "TLDR", "EDIT", "WSB", "NYSE", "NASDAQ",
    "THE", "AND", "OR", "FOR", "WITH", "THIS", "THAT"
}

# ---- Category filters you requested ----
EXCLUDE_ETFS = True
EXCLUDE_BIOTECH = True
EXCLUDE_ADRS = True   # NOTE: implemented as "exclude ALL ADR/ADS/depositary listings"

# Keyword filters (security-name based)
BIOTECH_KEYWORDS = [
    "BIOTECH", "BIO TECH", "BIOSCIENCE", "BIOSCIENCES", "BIOSCI", "BIOPHARMA", "BIO-PHARMA",
    "PHARMA", "PHARMACEUT", "THERAPEUT", "THERAPEUTICS",
    "ONCO", "ONCOLOGY", "GENOM", "GENE", "IMMUNO", "VACCINE",
    "CLINICAL", "DRUG", "MEDICINES"
]
ADR_KEYWORDS = [
    "AMERICAN DEPOSITARY", "DEPOSITARY SHARES", "DEPOSITARY RECEIPT", "ADR", "ADS"
]

# Pre-bloom filters (tune after first run)
MIN_RECENT_MENTIONS = 3          # mentions in last 7 days
MAX_OLD_MENTIONS_31_90 = 25      # keep baseline chatter low
MAX_TOTAL_MENTIONS = 120         # avoid already-saturated tickers
MIN_MOMENTUM_RATIO = 1.8         # (0-7)/(31-90) after smoothing

# Regex for tickers and $TICKER style
TICKER_RE = re.compile(r"\$?[A-Z]{1,6}\b")


def _parse_pipe_file(text: str):
    """Return (header:list[str], rows:list[list[str]]) from a pipe-delimited file."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header = lines[0].split("|")
    rows = []
    for ln in lines[1:]:
        # Footer lines can include "File Creation Time" etc.
        if ln.startswith("File Creation Time"):
            continue
        rows.append(ln.split("|"))
    return header, rows


def load_symbol_metadata():
    """
    Builds:
      - verified: set of symbols
      - meta: dict[symbol] -> {"name": str, "is_etf": bool}
    from Nasdaq Trader:
      - nasdaqlisted.txt (Symbol, Security Name, ETF flag)
      - otherlisted.txt  (ACT Symbol, Security Name, ETF flag)
    """
    meta = {}
    verified = set()

    # nasdaqlisted.txt
    r1 = requests.get(NASDAQ_LISTED_URL, timeout=30)
    r1.raise_for_status()
    h1, rows1 = _parse_pipe_file(r1.text)

    # Common columns in nasdaqlisted: Symbol, Security Name, ETF
    sym_i = h1.index("Symbol")
    name_i = h1.index("Security Name")
    etf_i = h1.index("ETF")

    for parts in rows1:
        if len(parts) <= max(sym_i, name_i, etf_i):
            continue
        sym = parts[sym_i].strip().upper()
        name = parts[name_i].strip()
        is_etf = parts[etf_i].strip().upper() == "Y"
        if sym and sym.isalpha():
            verified.add(sym)
            meta[sym] = {"name": name, "is_etf": is_etf}

    # otherlisted.txt
    r2 = requests.get(OTHER_LISTED_URL, timeout=30)
    r2.raise_for_status()
    h2, rows2 = _parse_pipe_file(r2.text)

    # Columns: ACT Symbol, Security Name, ETF (and more)
    sym2_i = h2.index("ACT Symbol")
    name2_i = h2.index("Security Name")
    etf2_i = h2.index("ETF")

    for parts in rows2:
        if len(parts) <= max(sym2_i, name2_i, etf2_i):
            continue
        sym = parts[sym2_i].strip().upper()
        name = parts[name2_i].strip()
        is_etf = parts[etf2_i].strip().upper() == "Y"
        if sym and sym.isalpha():
            verified.add(sym)
            # prefer Nasdaq-listed record if it exists, but fill gaps
            meta.setdefault(sym, {"name": name, "is_etf": is_etf})

    return verified, meta


def security_name_has_any(name: str, keywords: list[str]) -> bool:
    if not name:
        return False
    up = name.upper()
    return any(k in up for k in keywords)


def passes_category_filters(ticker: str, meta: dict) -> bool:
    info = meta.get(ticker)
    if not info:
        # If we can't find metadata, be conservative: keep it (still verified),
        # because missing meta is rare but possible.
        return True

    name = info.get("name", "") or ""
    is_etf = bool(info.get("is_etf", False))

    if EXCLUDE_ETFS and is_etf:
        return False

    if EXCLUDE_ADRS and security_name_has_any(name, ADR_KEYWORDS):
        return False

    if EXCLUDE_BIOTECH and security_name_has_any(name, BIOTECH_KEYWORDS):
        return False

    return True


def extract_tickers(text: str, verified: set[str], meta: dict) -> list[str]:
    if not text:
        return []
    hits = TICKER_RE.findall(text.upper())
    out = []
    for h in hits:
        t = h.lstrip("$")
        if t in STOPWORDS:
            continue
        if t in EXCLUDE_TICKERS:
            continue
        # Verified only, sane length
        if not (1 <= len(t) <= 5 and t.isalpha() and t in verified):
            continue
        # Category filters (ETF / ADR / biotech)
        if not passes_category_filters(t, meta):
            continue

        out.append(t)
    return out


def bucket_for_age_days(age_days: int) -> str:
    if age_days <= 7:
        return "0_7"
    if age_days <= 30:
        return "8_30"
    return "31_90"


def main():
    print("Loading verified tickers + metadata (Nasdaq Trader lists)...")
    verified, meta = load_symbol_metadata()
    print(f"Verified tickers loaded: {len(verified)}")

    reddit = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
    )

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=DAYS_BACK)
    cutoff_ts = cutoff.timestamp()

    counts = {
        "0_7": Counter(),
        "8_30": Counter(),
        "31_90": Counter()
    }

    per_sub = defaultdict(Counter)
    sample_titles = defaultdict(list)  # ticker -> list of (sub, title)

    for sub in SUBREDDITS:
        print(f"\nScanning r/{sub} (new posts up to ~{POST_LIMIT_PER_SUB})...")
        subreddit = reddit.subreddit(sub)

        scanned_posts = 0
        reached_cutoff = False

        for post in subreddit.new(limit=POST_LIMIT_PER_SUB):
            scanned_posts += 1
            if post.created_utc < cutoff_ts:
                reached_cutoff = True
                break

            post_age_days = (now - datetime.fromtimestamp(post.created_utc, tz=timezone.utc)).days
            b = bucket_for_age_days(post_age_days)

            tks = extract_tickers(post.title or "", verified, meta) + extract_tickers(post.selftext or "", verified, meta)
            if tks:
                for t in tks:
                    counts[b][t] += 1
                    per_sub[t][sub] += 1
                    if len(sample_titles[t]) < 3:
                        sample_titles[t].append((sub, (post.title or "")[:140]))

            if SCAN_COMMENTS:
                try:
                    post.comments.replace_more(limit=0)
                    scanned = 0
                    for c in post.comments:
                        if scanned >= TOP_LEVEL_COMMENT_LIMIT:
                            break
                        scanned += 1
                        created = getattr(c, "created_utc", None)
                        body = getattr(c, "body", "") or ""
                        if not created or not body:
                            continue

                        c_age_days = (now - datetime.fromtimestamp(created, tz=timezone.utc)).days
                        cb = bucket_for_age_days(c_age_days)

                        c_tks = extract_tickers(body, verified, meta)
                        for t in c_tks:
                            counts[cb][t] += 1
                            per_sub[t][sub] += 1
                except Exception:
                    pass

            time.sleep(0.1)

        print(f"Posts scanned: {scanned_posts}")
        print("✅ Reached 90-day cutoff" if reached_cutoff else "⚠️ Did NOT reach 90 days (hit listing limit)")

    # Build ranked candidates
    all_tickers = set(counts["0_7"]) | set(counts["8_30"]) | set(counts["31_90"])

    rows = []
    for t in all_tickers:
        a = counts["0_7"][t]
        b = counts["8_30"][t]
        c = counts["31_90"][t]
        total = a + b + c

        # momentum ratios (smoothed)
        mom_short = (a + 1) / (b + 1)      # 0-7 vs 8-30
        mom_long = (a + 1) / (c + 1)       # 0-7 vs 31-90

        # prebloom filtering
        if a < MIN_RECENT_MENTIONS:
            continue
        if c > MAX_OLD_MENTIONS_31_90:
            continue
        if total > MAX_TOTAL_MENTIONS:
            continue
        if mom_long < MIN_MOMENTUM_RATIO:
            continue

        # Score favors:
        # - recent mentions
        # - strong momentum
        # - low baseline
        score = (a * 2.0) + (mom_long * 3.0) - (c * 0.25)

        name = meta.get(t, {}).get("name", "")
        rows.append((t, name, a, b, c, total, mom_short, mom_long, score))

    rows.sort(key=lambda x: x[8], reverse=True)

    out_csv = "prebloom_candidates.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "ticker", "security_name",
            "mentions_0_7", "mentions_8_30", "mentions_31_90",
            "mentions_total", "mom_0_7_vs_8_30", "mom_0_7_vs_31_90", "score",
            "subreddit_breakdown", "sample_titles"
        ])
        for (t, name, a, b, c, total, m1, m2, score) in rows:
            breakdown = dict(per_sub[t].most_common())
            samples = "; ".join([f"r/{s}: {title}" for s, title in sample_titles[t]])
            w.writerow([t, name, a, b, c, total, f"{m1:.3f}", f"{m2:.3f}", f"{score:.3f}", breakdown, samples])

    print("\n==============================")
    print("PRE-BLOOM CANDIDATES (Top 25)")
    print("==============================")
    if not rows:
        print("No candidates matched your filters. (Totally normal on first run.)")
        print("Try lowering MIN_MOMENTUM_RATIO (e.g., 1.4) or MIN_RECENT_MENTIONS (e.g., 2).")
    else:
        for (t, name, a, b, c, total, m1, m2, score) in rows[:25]:
            print(f"{t:>6}  recent={a:<3} old={c:<3} total={total:<4} momLong={m2:.2f} score={score:.2f}")
            if name:
                print(f"       name={name}")
            subs = dict(per_sub[t].most_common())
            print(f"       subs={subs}")
            for (s, title) in sample_titles[t]:
                print(f"       - r/{s}: {title}")
            print()

    print(f"Saved: {out_csv}")


if __name__ == "__main__":
    main()
