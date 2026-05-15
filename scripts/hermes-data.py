#!/usr/bin/env python3
"""
hermes-data.py — Enhanced Data Fetcher for Hermes Qualitative Analysis

Fetches protocol fundamental data: TVL (DefiLlama), developer activity (GitHub API),
treasury estimates, fee revenue. Uses CoinGecko free API for price/volume data,
DefiLlama for TVL. Parses GitHub API for commit counts and stars.

Usage:
    python3 scripts/hermes-data.py --coin bitcoin
    python3 scripts/hermes-data.py --coin ethereum --json
    python3 scripts/hermes-data.py --list-coins
    python3 scripts/hermes-data.py --coin solana --stale-check

Dependencies: python3 standard lib (urllib, json)
"""

import argparse
import functools
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# ── Retry Decorator ────────────────────────────────────────────────────────────

def retry(max_attempts=3, delay=2):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        raise
                    print(f'  [retry] Attempt {attempt+1} failed: {e}. Retrying in {delay}s...', file=sys.stderr)
                    time.sleep(delay)
            return None
        return wrapper
    return decorator

# ── Cache Decorator ────────────────────────────────────────────────────────────

CACHE_DIR = os.path.expanduser('~/.cache/telos-agents')
os.makedirs(CACHE_DIR, exist_ok=True)

def cached(ttl_seconds=300):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = f'{func.__name__}_{hash(str(args) + str(sorted(kwargs.items())))}'
            cache_path = os.path.join(CACHE_DIR, f'{cache_key}.json')
            if os.path.exists(cache_path):
                age = time.time() - os.path.getmtime(cache_path)
                if age < ttl_seconds:
                    with open(cache_path) as f:
                        return json.load(f)
            result = func(*args, **kwargs)
            if result is not None:
                with open(cache_path, 'w') as f:
                    json.dump(result, f)
            return result
        return wrapper
    return decorator

# ── Configuration ──────────────────────────────────────────────────────────────

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
DEFILLAMA_BASE = "https://api.llama.fi"
GITHUB_API_BASE = "https://api.github.com"

COIN_IDS = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana",
    "cardano": "cardano", "ada": "cardano",
    "ripple": "ripple", "xrp": "ripple",
    "polkadot": "polkadot", "dot": "polkadot",
    "avalanche": "avalanche-2", "avax": "avalanche-2",
    "chainlink": "chainlink", "link": "chainlink",
    "polygon": "matic-network", "matic": "matic-network",
    "arbitrum": "arbitrum", "arb": "arbitrum",
    "optimism": "optimism", "op": "optimism",
    "sui": "sui", "aptos": "aptos", "apt": "aptos",
    "near": "near",
    "injective": "injective-protocol", "inj": "injective-protocol",
    "render": "render-token", "rndr": "render-token",
    "aave": "aave", "uniswap": "uniswap", "uni": "uniswap",
    "maker": "makerdao", "mkr": "makerdao",
    "litecoin": "litecoin", "ltc": "litecoin",
    "dogecoin": "dogecoin", "doge": "dogecoin",
}

# Known GitHub repos for major projects (owner/repo format)
GITHUB_REPOS = {
    "bitcoin": "bitcoin/bitcoin",
    "ethereum": "ethereum/go-ethereum",
    "solana": "solana-labs/solana",
    "cardano": "IntersectMBO/cardano-node",
    "polkadot": "paritytech/polkadot",
    "avalanche": "ava-labs/avalanchego",
    "chainlink": "smartcontractkit/chainlink",
    "polygon": "maticnetwork/bor",
    "arbitrum": "OffchainLabs/arbitrum",
    "optimism": "ethereum-optimism/optimism",
    "sui": "MystenLabs/sui",
    "aptos": "aptos-labs/aptos-core",
    "near": "nearprotocol/nearcore",
    "injective": "InjectiveLabs/injective-chain",
    "aave": "aave/aave-v3-core",
    "uniswap": "Uniswap/v3-core",
    "maker": "makerdao/dss",
}

# DefiLlama protocol slugs
PROTOCOL_SLUGS = {
    "lido": "lido", "uniswap": "uniswap", "aave": "aave",
    "makerdao": "makerdao", "maker": "makerdao",
    "eigenlayer": "eigenlayer", "ethena": "ethena",
    "pendle": "pendle", "jupiter": "jupiter",
    "raydium": "raydium", "aerodrome": "aerodrome-finance",
}

# ── Data Freshness Tracking ────────────────────────────────────────────────────

DATA_FRESHNESS = {}

def _record_freshness(source):
    DATA_FRESHNESS[source] = time.time()

def fresh_since(source):
    if source in DATA_FRESHNESS:
        return f"{(time.time() - DATA_FRESHNESS[source]) / 60:.0f}"
    return "never"

def _stale_warning(source, max_age_minutes=60):
    if source in DATA_FRESHNESS:
        age_min = (time.time() - DATA_FRESHNESS[source]) / 60
        if age_min > max_age_minutes:
            print(f"  [stale] WARNING: {source} data is {age_min:.0f}m old (max {max_age_minutes}m)", file=sys.stderr)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _fetch(url, timeout=15):
    """Fetch JSON from a URL. Returns dict or None on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Hermes/3.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"_error": f"URL Error: {e.reason}"}
    except json.JSONDecodeError as e:
        return {"_error": f"JSON parse error: {e}"}
    except Exception as e:
        return {"_error": str(e)}


def _fmt(val, suffix=""):
    """Format a number with commas, optional suffix."""
    if val is None:
        return "N/A"
    try:
        v = float(val)
        if v >= 1_000_000_000:
            return f"${v / 1_000_000_000:,.2f}B{suffix}"
        if v >= 1_000_000:
            return f"${v / 1_000_000:,.2f}M{suffix}"
        if v >= 1_000:
            return f"${v:,.0f}{suffix}"
        if v < 0.0001:
            return f"{v:.8f}"
        if v < 1:
            return f"{v:.6f}"
        return f"{v:,.4f}"
    except (ValueError, TypeError):
        return str(val)


def _pct(val):
    """Format a percentage."""
    if val is None:
        return "N/A"
    try:
        v = float(val)
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.2f}%"
    except (ValueError, TypeError):
        return str(val)

# ── Data Fetchers ──────────────────────────────────────────────────────────────

@cached(ttl_seconds=180)
@retry(max_attempts=3, delay=2)
def coin_price_data(coin_id):
    """Fetch current price, volume, market cap from CoinGecko."""
    try:
        url = f"{COINGECKO_BASE}/simple/price?ids={coin_id}&vs_currencies=usd&include_24hr_vol=true&include_24hr_change=true&include_market_cap=true"
        data = _fetch(url)
        if not data or coin_id not in data:
            return {"_error": f"No price data for {coin_id}"}
        d = data[coin_id]
        result = {
            "price": d.get("usd"),
            "volume_24h": d.get("usd_24h_vol"),
            "change_24h": d.get("usd_24h_change"),
            "market_cap": d.get("usd_market_cap"),
            "_fetched_at": time.time(),
        }
        _record_freshness(f"price_{coin_id}")
        return result
    except Exception as e:
        return {"_error": f"Price fetch failed: {e}"}


@cached(ttl_seconds=300)
@retry(max_attempts=3, delay=2)
def coin_detail_data(coin_id):
    """Fetch detailed project data from CoinGecko including developer stats."""
    try:
        url = f"{COINGECKO_BASE}/coins/{coin_id}?localization=false&tickers=false&community_data=true&developer_data=true"
        data = _fetch(url)
        if not data or "market_data" not in data:
            return {"_error": f"No detail data for {coin_id}"}
        md = data.get("market_data", {})
        dd = data.get("developer_data", {})
        cd = data.get("community_data", {})
        result = {
            "name": data.get("name"),
            "symbol": data.get("symbol", "").upper(),
            "price": md.get("current_price", {}).get("usd"),
            "market_cap": md.get("market_cap", {}).get("usd"),
            "volume_24h": md.get("total_volume", {}).get("usd"),
            "fdv": md.get("fully_diluted_valuation", {}).get("usd"),
            "circulating_supply": md.get("circulating_supply"),
            "total_supply": md.get("total_supply"),
            "price_change_24h": md.get("price_change_percentage_24h"),
            "price_change_7d": md.get("price_change_percentage_7d"),
            "price_change_30d": md.get("price_change_percentage_30d"),
            "ath": md.get("ath", {}).get("usd"),
            "atl": md.get("atl", {}).get("usd"),
            "developer_commits_4w": dd.get("commit_count_4_weeks"),
            "developer_stars": dd.get("star_count"),
            "developer_forks": dd.get("fork_count"),
            "developer_contributors_30d": dd.get("developer_contributors_4_weeks"),
            "twitter_followers": cd.get("twitter_followers"),
            "reddit_subscribers": cd.get("reddit_subscribers"),
            "telegram_users": cd.get("telegram_channel_user_count"),
            "_fetched_at": time.time(),
        }
        _record_freshness(f"detail_{coin_id}")
        return result
    except Exception as e:
        return {"_error": f"Detail fetch failed: {e}"}


@cached(ttl_seconds=600)
@retry(max_attempts=2, delay=3)
def defillama_tvl(protocol_slug):
    """Fetch TVL data from DefiLlama for a protocol."""
    try:
        data = _fetch(f"{DEFILLAMA_BASE}/protocol/{protocol_slug}")
        if not data or "tvl" not in data:
            return None
        tvl_history = data.get("tvl", [])
        current_tvl = tvl_history[-1].get("totalLiquidityUSD", 0) if tvl_history else 0
        result = {
            "name": data.get("name"),
            "symbol": data.get("symbol"),
            "current_tvl": current_tvl,
            "change_1d": data.get("change_1d"),
            "change_7d": data.get("change_7d"),
            "change_1m": data.get("change_1m"),
            "chain": data.get("chain"),
            "_fetched_at": time.time(),
        }
        _record_freshness(f"tvl_{protocol_slug}")
        return result
    except Exception as e:
        return {"_error": f"DeFiLlama TVL fetch failed: {e}"}


@cached(ttl_seconds=600)
@retry(max_attempts=2, delay=3)
def defillama_protocols_list():
    """Fetch top protocols list from DefiLlama for TVL context."""
    try:
        data = _fetch(f"{DEFILLAMA_BASE}/protocols")
        if not data or not isinstance(data, list):
            return None
        top = sorted(data, key=lambda x: x.get("tvl", 0), reverse=True)[:15]
        result = []
        for p in top:
            result.append({
                "name": p.get("name"),
                "symbol": p.get("symbol"),
                "tvl": p.get("tvl"),
                "chain": p.get("chain"),
                "change_1d": p.get("change_1d"),
                "change_7d": p.get("change_7d"),
            })
        _record_freshness("defillama_top")
        return result
    except Exception as e:
        return {"_error": f"DeFiLlama list fetch failed: {e}"}


@cached(ttl_seconds=1800)
@retry(max_attempts=2, delay=3)
def github_repo_data(repo_path):
    """Fetch GitHub repository stats (stars, forks, commits, last push)."""
    try:
        data = _fetch(f"{GITHUB_API_BASE}/repos/{repo_path}")
        if not data or "_error" in data:
            return None
        # Fetch commit count in last 30 days
        since = datetime.now(timezone.utc).timestamp() - 30 * 86400
        since_iso = datetime.fromtimestamp(since, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        commits_url = f"{GITHUB_API_BASE}/repos/{repo_path}/commits?since={since_iso}&per_page=100"
        commits_data = _fetch(commits_url)
        commit_count_30d = len(commits_data) if isinstance(commits_data, list) else 0

        result = {
            "full_name": data.get("full_name"),
            "stars": data.get("stargazers_count"),
            "forks": data.get("forks_count"),
            "open_issues": data.get("open_issues_count"),
            "watchers": data.get("subscribers_count"),
            "description": data.get("description"),
            "language": data.get("language"),
            "topics": data.get("topics", []),
            "last_push": data.get("pushed_at"),
            "last_updated": data.get("updated_at"),
            "created_at": data.get("created_at"),
            "archived": data.get("archived", False),
            "commits_30d": commit_count_30d,
            "_fetched_at": time.time(),
        }
        _record_freshness(f"github_{repo_path.replace('/', '_')}")
        return result
    except Exception as e:
        return {"_error": f"GitHub fetch failed: {e}"}


@cached(ttl_seconds=600)
@retry(max_attempts=2, delay=3)
def defillama_fees(slug):
    """Fetch fee/revenue data from DefiLlama Fees (if available)."""
    try:
        data = _fetch(f"{DEFILLAMA_BASE}/overview/fees/{slug}?dataType=dailyFees")
        if not data or "_error" in data:
            return None
        fees = data.get("totalDataChart", [])
        if fees and len(fees) > 0:
            latest_fee = fees[-1][1] if len(fees[-1]) > 1 else 0
            prev_fee = fees[-2][1] if len(fees) > 1 and len(fees[-2]) > 1 else 0
            fee_change_pct = ((latest_fee - prev_fee) / prev_fee * 100) if prev_fee else 0
        else:
            latest_fee = 0
            fee_change_pct = 0
        return {
            "slug": slug,
            "latest_daily_fee": latest_fee,
            "fee_change_pct_1d": fee_change_pct,
            "data_points": len(fees),
            "_fetched_at": time.time(),
        }
    except Exception:
        return None


@cached(ttl_seconds=180)
@retry(max_attempts=3, delay=2)
def fear_greed_index():
    """Fetch Fear & Greed Index for sentiment context."""
    try:
        data = _fetch("https://api.alternative.me/fng/?limit=7")
        if not data or "data" not in data:
            return None
        today = data["data"][0]
        week_ago = data["data"][-1] if len(data["data"]) > 1 else None
        result = {
            "value": today.get("value"),
            "classification": today.get("value_classification"),
            "timestamp": today.get("timestamp"),
            "trend": (
                f"{week_ago.get('value')} -> {today.get('value')} ({today.get('value_classification')})"
                if week_ago else None
            ),
            "_fetched_at": time.time(),
        }
        _record_freshness("fear_greed")
        return result
    except Exception as e:
        return {"_error": f"Fear & Greed fetch failed: {e}"}

# ── Report Builder ─────────────────────────────────────────────────────────────

def build_report(coin, stale_check=False, json_mode=False):
    """Build a comprehensive fundamentals data report."""
    coin_id = COIN_IDS.get(coin.lower(), coin.lower())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    report = {
        "timestamp": timestamp,
        "coin": coin,
        "coin_id": coin_id,
        "version": "3.0",
        "agent": "hermes-qualitative",
    }

    # 1. Price data
    report["price_data"] = coin_price_data(coin_id)
    _stale_warning(f"price_{coin_id}", 30)

    # 2. Detail / fundamentals
    report["fundamentals"] = coin_detail_data(coin_id)
    _stale_warning(f"detail_{coin_id}", 60)

    # 3. GitHub developer activity
    repo_path = GITHUB_REPOS.get(coin.lower())
    if repo_path:
        report["github"] = github_repo_data(repo_path)
        _stale_warning(f"github_{repo_path.replace('/', '_')}", 120)
    else:
        report["github"] = None

    # 4. TVL from DefiLlama (if known protocol)
    slug = PROTOCOL_SLUGS.get(coin.lower())
    if slug:
        report["tvl"] = defillama_tvl(slug)
        _stale_warning(f"tvl_{slug}", 120)
    else:
        report["tvl"] = None

    # 5. Fee revenue
    if slug:
        report["fees"] = defillama_fees(slug)
    else:
        report["fees"] = None

    # 6. Top DeFi protocols for context
    report["top_protocols"] = defillama_protocols_list()

    # 7. Sentiment
    report["fear_greed"] = fear_greed_index()

    # 8. Data freshness summary
    report["data_freshness"] = {}
    for src in sorted(DATA_FRESHNESS.keys()):
        report["data_freshness"][src] = f"{fresh_since(src)}m"

    # Stale data detection
    if stale_check:
        stale_sections = []
        for src, age_min_str in report["data_freshness"].items():
            try:
                age_min = float(age_min_str.replace("m", ""))
                if age_min > 60:
                    stale_sections.append(src)
            except ValueError:
                pass
        report["stale_sections"] = stale_sections
        if stale_sections:
            print(f"  [stale] Stale data detected: {', '.join(stale_sections)}", file=sys.stderr)

    if json_mode:
        return json.dumps(report, indent=2)

    return _format_human_report(report)


def _format_human_report(r):
    """Format the report for human reading."""
    lines = []
    lines.append(f"Hermes Data Report — {r['coin'].upper()} (v3.0)")
    lines.append(f"Generated: {r['timestamp']}")
    lines.append("")

    # Price
    pd = r.get("price_data", {}) or {}
    lines.append("── Price Data ──")
    if "_error" not in pd:
        lines.append(f"  Price          : {_fmt(pd.get('price'))}")
        lines.append(f"  24h Volume     : {_fmt(pd.get('volume_24h'))}")
        lines.append(f"  24h Change     : {_pct(pd.get('change_24h'))}")
        lines.append(f"  Market Cap     : {_fmt(pd.get('market_cap'))}")
    else:
        lines.append(f"  {pd.get('_error')}")
    lines.append("")

    # Fundamentals
    fd = r.get("fundamentals", {}) or {}
    lines.append("── Fundamentals ──")
    if "_error" not in fd:
        lines.append(f"  Name              : {fd.get('name', 'N/A')}")
        lines.append(f"  Symbol            : {fd.get('symbol', 'N/A')}")
        lines.append(f"  FDV               : {_fmt(fd.get('fdv'))}")
        lines.append(f"  Circ Supply       : {_fmt(fd.get('circulating_supply'), ' tokens')}")
        lines.append(f"  7d Change         : {_pct(fd.get('price_change_7d'))}")
        lines.append(f"  30d Change        : {_pct(fd.get('price_change_30d'))}")
        lines.append(f"  ATH               : {_fmt(fd.get('ath'))}")
        lines.append(f"  ATL               : {_fmt(fd.get('atl'))}")
        lines.append(f"  Twitter Followers : {_fmt(fd.get('twitter_followers'), '')}")
    else:
        lines.append(f"  {fd.get('_error')}")
    lines.append("")

    # GitHub
    gh = r.get("github", {}) or {}
    lines.append("── Developer Activity (GitHub) ──")
    if gh and "_error" not in gh:
        lines.append(f"  Repo            : {gh.get('full_name', 'N/A')}")
        lines.append(f"  Stars           : {gh.get('stars', 'N/A')}")
        lines.append(f"  Forks           : {gh.get('forks', 'N/A')}")
        lines.append(f"  Language        : {gh.get('language', 'N/A')}")
        lines.append(f"  Last Push       : {str(gh.get('last_push', 'N/A'))[:10]}")
        lines.append(f"  Commits (30d)   : {gh.get('commits_30d', 'N/A')}")
        lines.append(f"  Archived        : {gh.get('archived', False)}")
    elif gh and "_error" in gh:
        lines.append(f"  {gh.get('_error')}")
    else:
        lines.append("  (no GitHub mapping available)")
    lines.append("")

    # TVL
    tv = r.get("tvl", {}) or {}
    if tv and "_error" not in tv:
        lines.append("── TVL (DefiLlama) ──")
        lines.append(f"  Protocol    : {tv.get('name', 'N/A')}")
        lines.append(f"  TVL         : {_fmt(tv.get('current_tvl'))}")
        lines.append(f"  1d Change   : {_pct(tv.get('change_1d'))}")
        lines.append(f"  7d Change   : {_pct(tv.get('change_7d'))}")
        lines.append(f"  1m Change   : {_pct(tv.get('change_1m'))}")
        lines.append("")

    # Fees
    fees = r.get("fees", {}) or {}
    if fees and "_error" not in fees:
        lines.append("── Fee Revenue ──")
        lines.append(f"  Daily Fee      : {_fmt(fees.get('latest_daily_fee'))}")
        lines.append(f"  Fee 1d Change  : {_pct(fees.get('fee_change_pct_1d'))}")
        lines.append("")

    # Sentiment
    fg = r.get("fear_greed", {}) or {}
    lines.append("── Sentiment ──")
    if "_error" not in fg:
        lines.append(f"  Fear & Greed : {fg.get('value')} — {fg.get('classification')}")
        if fg.get("trend"):
            lines.append(f"  7-day trend  : {fg['trend']}")
    else:
        lines.append(f"  {fg.get('_error')}")
    lines.append("")

    # Top protocols
    top = r.get("top_protocols", [])
    if top and isinstance(top, list) and "_error" not in top[0] if top else True:
        lines.append("── Top DeFi Protocols (TVL Context) ──")
        for p in top[:8]:
            lines.append(f"  {p.get('name', '?'):20s} TVL: {_fmt(p.get('tvl')):>12s}  7d: {_pct(p.get('change_7d'))}")
        lines.append("")

    # Freshness
    lines.append("── Data Freshness ──")
    for src, age in sorted(r.get("data_freshness", {}).items()):
        lines.append(f"  {src:30s}: {age}")

    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────────

def list_coins():
    """Print all known coin mappings."""
    print("Known Coin ID Mappings:")
    for name, cid in sorted(COIN_IDS.items()):
        print(f"  {name:15s} -> {cid}")
    print()
    print("Known GitHub Repos:")
    for name, repo in sorted(GITHUB_REPOS.items()):
        print(f"  {name:15s} -> {repo}")
    print()
    print("Known DefiLlama Protocol Slugs:")
    for name, slug in sorted(PROTOCOL_SLUGS.items()):
        print(f"  {name:15s} -> {slug}")


def main():
    parser = argparse.ArgumentParser(
        description="Hermes — Qualitative Data Fetcher v3.0",
    )
    parser.add_argument("--coin", "-c", default="bitcoin", help="Coin name/ID (default: bitcoin)")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    parser.add_argument("--stale-check", action="store_true", help="Check data freshness and report stale sections")
    parser.add_argument("--list-coins", action="store_true", help="List known coin ID mappings and repos")
    args = parser.parse_args()

    if args.list_coins:
        list_coins()
        return

    report = build_report(coin=args.coin, stale_check=args.stale_check, json_mode=args.json)
    print(report)


if __name__ == "__main__":
    main()
