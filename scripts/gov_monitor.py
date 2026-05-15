#!/usr/bin/env python3
"""
gov_monitor.py — Governance Monitor for Hermes Qualitative Analysis

Fetches proposal data from Snapshot API (https://hub.snapshot.org/graphql).
Queries for active proposals by space. Outputs active_proposals count,
top proposals with titles and scores, voting_participation trend,
and governance_risk_score (0-10).

Usage:
    python3 scripts/gov_monitor.py --space uniswap
    python3 scripts/gov_monitor.py --space aave.eth --json
    python3 scripts/gov_monitor.py --list-spaces

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

SNAPSHOT_API = "https://hub.snapshot.org/graphql"

# Known Snapshot spaces / governance locations
KNOWN_SPACES = {
    "uniswap": "uniswap",
    "aave": "aave.eth",
    "aave.eth": "aave.eth",
    "maker": "makerdao.eth",
    "makerdao": "makerdao.eth",
    "compound": "compound-community.eth",
    "compound-community.eth": "compound-community.eth",
    "curve": "curve.eth",
    "curve.eth": "curve.eth",
    "lido": "lido-snapshot.eth",
    "lido-snapshot.eth": "lido-snapshot.eth",
    "balancer": "balancer.eth",
    "balancer.eth": "balancer.eth",
    "sushi": "sushigov.eth",
    "sushigov.eth": "sushigov.eth",
    "gitcoin": "gitcoin.eth",
    "gitcoin.eth": "gitcoin.eth",
    "ens": "ens.eth",
    "ens.eth": "ens.eth",
    "arbitrum": "arbitrumfoundation.eth",
    "arbitrumfoundation.eth": "arbitrumfoundation.eth",
    "optimism": "optimism.eth",
    "optimism.eth": "optimism.eth",
    "polygon": "polygon.eth",
    "polygon.eth": "polygon.eth",
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def _graphql_query(query, timeout=30):
    """Execute a GraphQL query against the Snapshot API."""
    try:
        req_data = json.dumps({"query": query}).encode("utf-8")
        req = urllib.request.Request(
            SNAPSHOT_API,
            data=req_data,
            headers={
                "User-Agent": "Hermes/3.0",
                "Content-Type": "application/json",
            }
        )
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
    """Format a number."""
    if val is None:
        return "N/A"
    try:
        v = float(val)
        if v >= 1_000_000:
            return f"{v / 1_000_000:,.2f}M{suffix}"
        if v >= 1_000:
            return f"{v:,.0f}{suffix}"
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
        return f"{sign}{v:.1f}%"
    except (ValueError, TypeError):
        return str(val)

# ── Data Fetchers ──────────────────────────────────────────────────────────────

@cached(ttl_seconds=120)
@retry(max_attempts=3, delay=2)
def fetch_active_proposals(space):
    """Fetch active proposals for a given Snapshot space."""
    query = """
    {
      proposals(
        first: 50,
        where: {
          space_in: ["%s"],
          state: "active"
        },
        orderBy: "created",
        orderDirection: desc
      ) {
        id
        title
        body
        state
        author
        created
        start
        end
        votes
        quorum
        scores
        scores_total
        strategies {
          name
        }
      }
    }
    """ % space

    result = _graphql_query(query)
    if result is None or "_error" in result:
        return result

    try:
        proposals = result.get("data", {}).get("proposals", [])
        return proposals
    except Exception as e:
        return {"_error": f"Failed to parse proposals: {e}"}


@cached(ttl_seconds=300)
@retry(max_attempts=3, delay=2)
def fetch_recent_proposals(space, limit=100):
    """Fetch recent proposals (last 100) for voting trend analysis."""
    query = """
    {
      proposals(
        first: %d,
        where: {
          space_in: ["%s"]
        },
        orderBy: "created",
        orderDirection: desc
      ) {
        id
        title
        state
        author
        created
        start
        end
        votes
        quorum
        scores
        scores_total
      }
    }
    """ % (limit, space)

    result = _graphql_query(query)
    if result is None or "_error" in result:
        return result

    try:
        proposals = result.get("data", {}).get("proposals", [])
        return proposals
    except Exception as e:
        return {"_error": f"Failed to parse proposals: {e}"}


@cached(ttl_seconds=600)
@retry(max_attempts=3, delay=2)
def fetch_space_info(space):
    """Fetch space metadata (name, followers, voting power)."""
    query = """
    {
      space(id: "%s") {
        id
        name
        about
        members
        followersCount
        proposalsCount
        votesCount
        network
        strategies {
          name
          network
        }
        admins
        moderators
        private
      }
    }
    """ % space

    result = _graphql_query(query)
    if result is None or "_error" in result:
        return result

    try:
        return result.get("data", {}).get("space", {})
    except Exception as e:
        return {"_error": f"Failed to parse space info: {e}"}

# ── Analysis Functions ─────────────────────────────────────────────────────────

def compute_voting_trend(proposals):
    """Compute voting participation trend from recent proposals."""
    if not proposals or not isinstance(proposals, list):
        return {"trend": "unknown", "avg_votes": 0, "samples": 0}

    # Only consider closed proposals with votes data
    closed = [p for p in proposals if p.get("state") == "closed" and p.get("votes") is not None]
    if len(closed) < 3:
        return {"trend": "insufficient_data", "avg_votes": 0, "samples": len(closed)}

    # Compare recent half vs older half
    mid = len(closed) // 2
    recent_half = closed[:mid]
    older_half = closed[mid:]

    recent_avg = sum(p["votes"] for p in recent_half if p["votes"]) / len(recent_half)
    older_avg = sum(p["votes"] for p in older_half if p["votes"]) / len(older_half)

    if older_avg > 0:
        change_pct = ((recent_avg - older_avg) / older_avg) * 100
    else:
        change_pct = 0

    if change_pct > 10:
        trend = "increasing"
    elif change_pct < -10:
        trend = "decreasing"
    else:
        trend = "stable"

    return {
        "trend": trend,
        "avg_votes_recent": round(recent_avg, 1),
        "avg_votes_older": round(older_avg, 1),
        "change_pct": round(change_pct, 1),
        "samples": len(closed),
    }


def compute_governance_risk(active_count, space_info, voting_trend):
    """Compute a governance risk score from 0 (safe) to 10 (critical risk)."""
    risk = 2.0  # baseline moderate

    # More active proposals = more governance activity (generally good, less risk)
    if active_count > 20:
        risk -= 0.5
    elif active_count > 10:
        risk -= 0.3
    elif active_count == 0:
        risk += 1.5  # no active governance is a risk

    # Voting participation trend
    trend = voting_trend.get("trend", "unknown")
    if trend == "decreasing":
        risk += 1.5
    elif trend == "increasing":
        risk -= 0.5

    # Low average votes suggests apathy
    avg_votes = voting_trend.get("avg_votes_recent", 0)
    if avg_votes < 5:
        risk += 1.0
    elif avg_votes < 50:
        risk += 0.5
    elif avg_votes > 500:
        risk -= 0.5

    # Private spaces are more risky
    if space_info and space_info.get("private"):
        risk += 2.0

    # Few members / followers
    if space_info:
        followers = space_info.get("followersCount", 0)
        if followers and followers < 100:
            risk += 1.0

    # Few proposals total = new or inactive DAO
    if space_info:
        total_proposals = space_info.get("proposalsCount", 0)
        if total_proposals and total_proposals < 10:
            risk += 1.0

    # Clamp to 0-10
    risk = max(0.0, min(10.0, risk))
    return round(risk, 1)


# ── Report Builder ─────────────────────────────────────────────────────────────

def build_report(space, json_mode=False):
    """Build a comprehensive governance monitoring report."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    report = {
        "timestamp": timestamp,
        "space": space,
        "agent": "hermes-qualitative",
        "version": "3.0",
    }

    # 1. Space info
    try:
        report["space_info"] = fetch_space_info(space)
    except Exception as e:
        report["space_info"] = {"_error": str(e)}

    # 2. Active proposals
    try:
        active = fetch_active_proposals(space)
        if active and "_error" not in active:
            report["active_proposals"] = len(active)
            # Top proposals by score
            active_sorted = sorted(active, key=lambda p: p.get("scores_total", 0) or 0, reverse=True)
            top_proposals = []
            for p in active_sorted[:5]:
                top_proposals.append({
                    "id": p.get("id"),
                    "title": p.get("title"),
                    "author": p.get("author"),
                    "created": datetime.fromtimestamp(p.get("created", 0), tz=timezone.utc).strftime("%Y-%m-%d") if p.get("created") else None,
                    "end": datetime.fromtimestamp(p.get("end", 0), tz=timezone.utc).strftime("%Y-%m-%d") if p.get("end") else None,
                    "votes": p.get("votes"),
                    "quorum": p.get("quorum"),
                    "scores_total": p.get("scores_total"),
                    "state": p.get("state"),
                })
            report["top_proposals"] = top_proposals
        else:
            report["active_proposals"] = 0
            report["top_proposals"] = []
            if active and "_error" in active:
                report["proposals_error"] = active["_error"]
    except Exception as e:
        report["active_proposals"] = 0
        report["top_proposals"] = []
        report["proposals_error"] = str(e)

    # 3. Voting participation trend
    try:
        recent = fetch_recent_proposals(space)
        if recent and "_error" not in recent:
            report["voting_trend"] = compute_voting_trend(recent)
        else:
            report["voting_trend"] = {"trend": "unknown", "error": str(recent.get("_error", "fetch failed")) if recent else "fetch failed"}
    except Exception as e:
        report["voting_trend"] = {"trend": "unknown", "error": str(e)}

    # 4. Governance risk score
    report["governance_risk_score"] = compute_governance_risk(
        report.get("active_proposals", 0),
        report.get("space_info"),
        report.get("voting_trend", {}),
    )

    # 5. Risk level label
    risk = report["governance_risk_score"]
    if risk >= 7.0:
        report["risk_level"] = "CRITICAL"
    elif risk >= 5.0:
        report["risk_level"] = "HIGH"
    elif risk >= 3.0:
        report["risk_level"] = "MEDIUM"
    else:
        report["risk_level"] = "LOW"

    if json_mode:
        return json.dumps(report, indent=2)

    return _format_human_report(report)


def _format_human_report(r):
    """Format the report for human reading."""
    lines = []
    lines.append(f"Hermes Governance Monitor — {r['space']}")
    lines.append(f"Generated: {r['timestamp']}")
    lines.append("")

    # Space info
    si = r.get("space_info", {}) or {}
    lines.append("── Space Info ──")
    if "_error" not in si:
        lines.append(f"  Name             : {si.get('name', si.get('id', 'N/A'))}")
        lines.append(f"  Network          : {si.get('network', 'N/A')}")
        lines.append(f"  Members          : {si.get('members', 'N/A')}")
        lines.append(f"  Followers        : {_fmt(si.get('followersCount'))}")
        lines.append(f"  Total Proposals  : {si.get('proposalsCount', 'N/A')}")
        lines.append(f"  Private          : {si.get('private', False)}")
    else:
        lines.append(f"  {si.get('_error')}")
    lines.append("")

    # Active proposals
    lines.append(f"── Active Proposals: {r.get('active_proposals', 0)} ──")
    top = r.get("top_proposals", [])
    if top:
        for i, p in enumerate(top, 1):
            lines.append(f"  {i}. {p.get('title', 'Untitled')}")
            lines.append(f"     Author : {str(p.get('author', '?'))[:42]}")
            lines.append(f"     Ends   : {p.get('end', '?')}  |  Votes: {p.get('votes', 0)}  |  Quorum: {p.get('quorum', 0)}")
            lines.append(f"     Score  : {_fmt(p.get('scores_total'))}")
            lines.append("")
    else:
        lines.append("  (no active proposals)")
        lines.append("")

    # Voting trend
    vt = r.get("voting_trend", {})
    lines.append("── Voting Participation Trend ──")
    if "_error" not in vt:
        lines.append(f"  Trend        : {vt.get('trend', 'unknown')}")
        lines.append(f"  Recent Avg   : {vt.get('avg_votes_recent', 'N/A')} votes")
        lines.append(f"  Older Avg    : {vt.get('avg_votes_older', 'N/A')} votes")
        lines.append(f"  Change       : {_pct(vt.get('change_pct'))}")
        lines.append(f"  Samples      : {vt.get('samples', 0)} proposals")
    else:
        lines.append(f"  {vt.get('error', 'unknown')}")
    lines.append("")

    # Risk score
    risk = r.get("governance_risk_score", 5.0)
    level = r.get("risk_level", "MEDIUM")
    lines.append(f"── Governance Risk Score: {risk}/10 ({level}) ──")

    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────────

def list_spaces():
    """Print all known Snapshot governance spaces."""
    print("Known Snapshot Governance Spaces:")
    for name, space in sorted(KNOWN_SPACES.items(), key=lambda x: x[1]):
        print(f"  {name:20s} -> {space}")
    print()
    print("Use --space <name> or --space <snapshot.eth> to query")
    print("Example: --space uniswap  or  --space aave.eth")


def main():
    parser = argparse.ArgumentParser(
        description="Hermes — Governance Monitor v3.0",
    )
    parser.add_argument("--space", "-s", default="uniswap", help="Snapshot space name (default: uniswap)")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    parser.add_argument("--list-spaces", action="store_true", help="List known Snapshot governance spaces")
    args = parser.parse_args()

    if args.list_spaces:
        list_spaces()
        return

    space = KNOWN_SPACES.get(args.space.lower(), args.space)
    report = build_report(space=space, json_mode=args.json)
    print(report)


if __name__ == "__main__":
    main()
