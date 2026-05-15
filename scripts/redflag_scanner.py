#!/usr/bin/env python3
"""
redflag_scanner.py — Red Flag Scanner for Hermes Qualitative Analysis

Takes a coin/token name. Uses GitHub API to check: commit_frequency (commits in
last 30 days), team_anonymity check, code_freshness (last commit date). Uses
CoinGecko to check: holder_concentration from available data.

Output: per-check scores (0-10), overall_risk_rating (low/medium/high/critical),
evidence for each flag. Handles API errors gracefully.

Usage:
    python3 scripts/redflag_scanner.py --coin bitcoin
    python3 scripts/redflag_scanner.py --coin ethereum --json
    python3 scripts/redflag_scanner.py --list-coins

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
}

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


def _fmt(val):
    if val is None:
        return "N/A"
    try:
        v = float(val)
        if v >= 1_000_000:
            return f"{v / 1_000_000:,.2f}M"
        if v >= 1_000:
            return f"{v:,.0f}"
        return f"{v:,.4f}"
    except (ValueError, TypeError):
        return str(val)

# ── Data Fetchers ──────────────────────────────────────────────────────────────

@cached(ttl_seconds=180)
@retry(max_attempts=3, delay=2)
def coin_detail_data(coin_id):
    """Fetch detailed project data from CoinGecko."""
    try:
        url = f"{COINGECKO_BASE}/coins/{coin_id}?localization=false&tickers=true&community_data=true&developer_data=true"
        data = _fetch(url)
        if not data or "market_data" not in data:
            return {"_error": f"No data for {coin_id}"}
        return data
    except Exception as e:
        return {"_error": str(e)}


@cached(ttl_seconds=1800)
@retry(max_attempts=2, delay=3)
def github_repo_data(repo_path):
    """Fetch GitHub repository stats."""
    try:
        data = _fetch(f"{GITHUB_API_BASE}/repos/{repo_path}")
        if not data or "_error" in data:
            return None
        since = datetime.now(timezone.utc).timestamp() - 30 * 86400
        since_iso = datetime.fromtimestamp(since, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        commits_url = f"{GITHUB_API_BASE}/repos/{repo_path}/commits?since={since_iso}&per_page=100"
        commits_data = _fetch(commits_url)
        commit_count_30d = len(commits_data) if isinstance(commits_data, list) else 0

        # Get contributors
        contributors_url = f"{GITHUB_API_BASE}/repos/{repo_path}/contributors?per_page=10"
        contributors_data = _fetch(contributors_url)
        contributor_count = len(contributors_data) if isinstance(contributors_data, list) else 0

        return {
            "full_name": data.get("full_name"),
            "stars": data.get("stargazers_count"),
            "forks": data.get("forks_count"),
            "open_issues": data.get("open_issues_count"),
            "description": data.get("description"),
            "language": data.get("language"),
            "archived": data.get("archived", False),
            "fork": data.get("fork", False),
            "last_push": data.get("pushed_at"),
            "last_updated": data.get("updated_at"),
            "created_at": data.get("created_at"),
            "commits_30d": commit_count_30d,
            "contributors_count": contributor_count,
            "_fetched_at": time.time(),
        }
    except Exception as e:
        return {"_error": str(e)}

# ── Red Flag Checks ────────────────────────────────────────────────────────────

def check_commit_frequency(repo_data):
    """Score 0-10: how active is development? 0 = very active, 10 = dormant."""
    if repo_data is None or "_error" in (repo_data or {}):
        return {"score": None, "evidence": "GitHub data unavailable", "available": False}

    commits_30d = repo_data.get("commits_30d", 0)
    last_push = repo_data.get("last_push")
    archived = repo_data.get("archived", False)

    evidence_parts = []
    score = 0

    # Archived repo is a critical red flag
    if archived:
        score += 8
        evidence_parts.append("Repository is ARCHIVED")

    # Commits analysis
    if commits_30d == 0:
        score += 6
        evidence_parts.append("Zero commits in last 30 days")
    elif commits_30d < 5:
        score += 4
        evidence_parts.append(f"Very low commit activity ({commits_30d} commits/30d)")
    elif commits_30d < 20:
        score += 2
        evidence_parts.append(f"Moderate commit activity ({commits_30d} commits/30d)")
    else:
        score -= 1
        evidence_parts.append(f"Active development ({commits_30d} commits/30d)")

    # Last push recency
    if last_push:
        try:
            push_dt = datetime.fromisoformat(last_push.replace("Z", "+00:00"))
            days_since_push = (datetime.now(timezone.utc) - push_dt).days
            if days_since_push > 365:
                score += 5
                evidence_parts.append(f"Last push was {days_since_push} days ago (>1 year)")
            elif days_since_push > 90:
                score += 3
                evidence_parts.append(f"Last push was {days_since_push} days ago (>3 months)")
            elif days_since_push > 30:
                score += 1
                evidence_parts.append(f"Last push was {days_since_push} days ago")
            else:
                score -= 1
                evidence_parts.append(f"Recent push ({days_since_push} days ago)")
        except Exception:
            pass

    score = max(0, min(10, score))
    return {
        "score": score,
        "evidence": "; ".join(evidence_parts),
        "commits_30d": commits_30d,
        "archived": archived,
        "available": True,
    }


def check_team_anonymity(detail_data, repo_data):
    """Score 0-10: how anonymous/risky is the team? 0 = transparent, 10 = anonymous."""
    score = 0
    evidence_parts = []

    detail_available = detail_data and "_error" not in (detail_data or {})
    repo_available = repo_data and "_error" not in (repo_data or {})

    if not detail_available and not repo_available:
        return {"score": 5, "evidence": "Insufficient data to assess team transparency", "available": False}

    # Check if the project has identifiable team info on CoinGecko
    if detail_available:
        links = detail_data.get("links", {})
        # Check if there are team/social links
        twitter = links.get("twitter_screen_name")
        github_org = links.get("repositories", [])
        website = links.get("homepage")

        has_identifiable_links = bool(twitter or github_org or website)
        if not has_identifiable_links:
            score += 4
            evidence_parts.append("No identifiable team social links on CoinGecko")

        # Check description for team info
        description = detail_data.get("description", {}).get("en", "")
        if description and ("anonymous" in description.lower() or "pseudo" in description.lower()):
            score += 6
            evidence_parts.append("Project description mentions anonymous/pseudonymous team")

    # GitHub contributors as proxy for team transparency
    if repo_available:
        contributors = repo_data.get("contributors_count", 0)
        if contributors == 0:
            score += 3
            evidence_parts.append("No identifiable contributors on GitHub")
        elif contributors < 3:
            score += 2
            evidence_parts.append(f"Very few contributors ({contributors})")
        elif contributors > 20:
            score -= 1
            evidence_parts.append(f"Large contributor base ({contributors})")

        # Check if repo is a fork (suggests derivatives rather than original work)
        if repo_data.get("fork", False):
            score += 2
            evidence_parts.append("Repository is a fork (not original)")

    score = max(0, min(10, score))
    # Default to mid-range if no clear evidence
    if not evidence_parts:
        score = 5
        evidence_parts.append("No clear team transparency data")

    return {
        "score": score,
        "evidence": "; ".join(evidence_parts),
        "available": True,
    }


def check_code_freshness(repo_data):
    """Score 0-10: how fresh/recent is the code? 0 = fresh, 10 = stale."""
    if repo_data is None or "_error" in (repo_data or {}):
        return {"score": 5, "evidence": "GitHub data unavailable, defaulting to medium risk", "available": False}

    score = 0
    evidence_parts = []

    last_push = repo_data.get("last_push")
    last_updated = repo_data.get("last_updated")
    created_at = repo_data.get("created_at")
    archived = repo_data.get("archived", False)

    if archived:
        score += 9
        evidence_parts.append("Repository is ARCHIVED — no further development")

    # Check last update
    if last_updated:
        try:
            upd_dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
            days_since_update = (datetime.now(timezone.utc) - upd_dt).days
            if days_since_update > 365:
                score += 6
                evidence_parts.append(f"Last updated {days_since_update} days ago (>1 year)")
            elif days_since_update > 180:
                score += 4
                evidence_parts.append(f"Last updated {days_since_update} days ago (>6 months)")
            elif days_since_update > 30:
                score += 1
                evidence_parts.append(f"Last updated {days_since_update} days ago")
            else:
                score -= 1
                evidence_parts.append(f"Recently updated ({days_since_update} days ago)")
        except Exception:
            pass

    # Check project age vs activity — very old project with no recent activity is worse
    if created_at and last_push:
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            push_dt = datetime.fromisoformat(last_push.replace("Z", "+00:00"))
            project_age_days = (datetime.now(timezone.utc) - created_dt).days
            days_since_push = (datetime.now(timezone.utc) - push_dt).days

            if project_age_days > 730 and days_since_push > 180:
                score += 2
                evidence_parts.append(f"Mature project ({project_age_days//365}y old) but no recent activity")
        except Exception:
            pass

    score = max(0, min(10, score))
    return {
        "score": score,
        "evidence": "; ".join(evidence_parts),
        "last_push": last_push,
        "archived": archived,
        "available": True,
    }


def check_holder_concentration(detail_data):
    """Score 0-10: how concentrated is token ownership? 0 = distributed, 10 = highly concentrated."""
    if detail_data is None or "_error" in (detail_data or {}):
        return {"score": 5, "evidence": "Holder data unavailable, defaulting to medium risk", "available": False}

    score = 0
    evidence_parts = []

    md = detail_data.get("market_data", {})

    # Use available concentration proxies
    # Check circulating vs total supply ratio (high ratio of uncirculated = concentration risk)
    circ_supply = md.get("circulating_supply")
    total_supply = md.get("total_supply")
    max_supply = md.get("max_supply")

    if circ_supply and total_supply and total_supply > 0:
        circ_ratio = circ_supply / total_supply
        uncirculated_pct = (1 - circ_ratio) * 100
        if uncirculated_pct > 50:
            score += 6
            evidence_parts.append(f"Only {circ_ratio*100:.0f}% of total supply is circulating ({uncirculated_pct:.0f}% locked/unreleased)")
        elif uncirculated_pct > 25:
            score += 3
            evidence_parts.append(f"Significant supply not circulating ({uncirculated_pct:.0f}% locked/unreleased)")
        else:
            score -= 1
            evidence_parts.append(f"Most supply is circulating ({circ_ratio*100:.0f}%)")
    else:
        score += 2
        evidence_parts.append("Circulating vs total supply data incomplete")

    # Market cap vs fully-diluted valuation ratio
    market_cap = md.get("market_cap", {}).get("usd")
    fdv = md.get("fully_diluted_valuation", {}).get("usd")
    if market_cap and fdv and fdv > 0:
        mc_fdv_ratio = market_cap / fdv
        if mc_fdv_ratio < 0.1:
            score += 5
            evidence_parts.append(f"Very low MC/FDV ratio ({mc_fdv_ratio:.1%}) — massive dilution risk")
        elif mc_fdv_ratio < 0.3:
            score += 2
            evidence_parts.append(f"Low MC/FDV ratio ({mc_fdv_ratio:.1%}) — dilution risk")
        elif mc_fdv_ratio > 0.8:
            score -= 1
            evidence_parts.append(f"Healthy MC/FDV ratio ({mc_fdv_ratio:.1%})")

    # Price volatility as proxy for manipulation risk
    price_change_24h = md.get("price_change_percentage_24h")
    if price_change_24h is not None:
        if abs(price_change_24h) > 30:
            score += 3
            evidence_parts.append(f"Extreme 24h volatility ({price_change_24h:+.1f}%) — potential manipulation")
        elif abs(price_change_24h) > 15:
            score += 1
            evidence_parts.append(f"High 24h volatility ({price_change_24h:+.1f}%)")

    score = max(0, min(10, score))
    if not evidence_parts:
        score = 5
        evidence_parts.append("Insufficient holder concentration data")

    return {
        "score": score,
        "evidence": "; ".join(evidence_parts),
        "circulating_supply": circ_supply,
        "total_supply": total_supply,
        "mc_fdv_ratio": mc_fdv_ratio if market_cap and fdv and fdv > 0 else None,
        "available": True,
    }

# ── Report Builder ─────────────────────────────────────────────────────────────

def build_report(coin, json_mode=False):
    """Build a comprehensive red flag scanning report."""
    coin_id = COIN_IDS.get(coin.lower(), coin.lower())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    report = {
        "timestamp": timestamp,
        "coin": coin,
        "coin_id": coin_id,
        "agent": "hermes-qualitative",
        "version": "3.0",
    }

    # Fetch data
    detail = coin_detail_data(coin_id)
    repo_path = GITHUB_REPOS.get(coin.lower())
    repo = github_repo_data(repo_path) if repo_path else None

    # Run checks
    report["commit_frequency"] = check_commit_frequency(repo)
    report["team_anonymity"] = check_team_anonymity(detail, repo)
    report["code_freshness"] = check_code_freshness(repo)
    report["holder_concentration"] = check_holder_concentration(detail)

    # Compute overall risk
    scores = []
    for check_name in ["commit_frequency", "team_anonymity", "code_freshness", "holder_concentration"]:
        check = report.get(check_name, {})
        score = check.get("score")
        if score is not None:
            scores.append(score)

    if scores:
        overall_score = sum(scores) / len(scores)
    else:
        overall_score = 5.0

    report["overall_risk_score"] = round(overall_score, 1)

    if overall_score >= 7.0:
        report["overall_risk_rating"] = "CRITICAL"
    elif overall_score >= 5.0:
        report["overall_risk_rating"] = "HIGH"
    elif overall_score >= 3.0:
        report["overall_risk_rating"] = "MEDIUM"
    else:
        report["overall_risk_rating"] = "LOW"

    # Collect all evidence
    report["all_evidence"] = []
    for check_name, label in [
        ("commit_frequency", "Commit Frequency"),
        ("team_anonymity", "Team Anonymity"),
        ("code_freshness", "Code Freshness"),
        ("holder_concentration", "Holder Concentration"),
    ]:
        check = report.get(check_name, {})
        if check.get("evidence"):
            report["all_evidence"].append({
                "check": label,
                "score": check.get("score"),
                "evidence": check.get("evidence"),
            })

    if json_mode:
        return json.dumps(report, indent=2)

    return _format_human_report(report)


def _format_human_report(r):
    """Format the report for human reading."""
    lines = []
    lines.append(f"Hermes Red Flag Scanner — {r['coin'].upper()}")
    lines.append(f"Generated: {r['timestamp']}")
    lines.append("")

    checks = [
        ("commit_frequency", "Commit Frequency (GitHub)", "commit_frequency"),
        ("team_anonymity", "Team Anonymity", "team_anonymity"),
        ("code_freshness", "Code Freshness", "code_freshness"),
        ("holder_concentration", "Holder Concentration", "holder_concentration"),
    ]

    for key, label, score_key in checks:
        check = r.get(key, {})
        score = check.get("score")
        score_display = f"{score}/10" if score is not None else "N/A"
        evidence = check.get("evidence", "No evidence available")

        lines.append(f"── {label}: {score_display} ──")
        lines.append(f"  {evidence}")
        lines.append("")

    # Overall
    lines.append("=" * 50)
    lines.append(f"Overall Risk Score : {r['overall_risk_score']}/10")
    lines.append(f"Overall Risk Rating: {r['overall_risk_rating']}")
    lines.append("=" * 50)

    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────────

def list_coins():
    """Print all known coin mappings and GitHub repos."""
    print("Known Coins & GitHub Repos for Red Flag Scanning:")
    for name in sorted(COIN_IDS.keys()):
        cid = COIN_IDS[name]
        repo = GITHUB_REPOS.get(name, "(no repo mapping)")
        print(f"  {name:12s} -> id={cid:25s} repo={repo}")
    print()
    print("Usage: python3 redflag_scanner.py --coin <name>")


def main():
    parser = argparse.ArgumentParser(
        description="Hermes — Red Flag Scanner v3.0",
    )
    parser.add_argument("--coin", "-c", default="bitcoin", help="Coin name/ID (default: bitcoin)")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    parser.add_argument("--list-coins", action="store_true", help="List known coin mappings")
    args = parser.parse_args()

    if args.list_coins:
        list_coins()
        return

    report = build_report(coin=args.coin, json_mode=args.json)
    print(report)


if __name__ == "__main__":
    main()
