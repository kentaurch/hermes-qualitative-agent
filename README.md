# Hermes — Qualitative Analysis Agent

Part of the **Telos Trading Council** — a team of six specialized AI agents for crypto futures trading.

## Overview

Hermes is a specialized analysis agent within the Telos Trading Council, focusing on qualitative analysis. It works alongside five other agents to provide a comprehensive, multi-perspective analysis of the crypto markets.

## Role in the Council

The Telos Trading Council brings together six distinct analytical perspectives to generate high-conviction trading signals:

| Agent | Focus |
|---|---|
| **Prometheus** | Fundamental Analysis — macro, on-chain, valuation |
| **Kairos** | Technical Analysis — charts, indicators, orderflow |
| **Pheme** | Sentiment Analysis — social, news, crowd psychology |
| **Palamedes** | Quantitative Analysis — models, backtesting, risk metrics |
| **Hermes** | Qualitative Analysis — governance, narrative, regulatory |
| **Astraea** | Statistical Analysis — probability, regimes, correlation |

## Contents

- `SKILL.md` — Full Hermes agent skill definition (identity, methodology, guardrails)
- `scripts/` — Executable data pipelines (3 scripts)

### Scripts

| Script | Purpose |
|--------|---------|
| `gov_monitor.py` | Data collection & analysis pipeline |
| `hermes-data.py` | Data collection & analysis pipeline |
| `redflag_scanner.py` | Data collection & analysis pipeline |

## Usage

Run any script directly:

```bash
cd scripts/
python3 gov_monitor.py --json
```

All scripts support the `--json` flag for structured output compatible with the Council Voting Protocol.

## Requirements

Install dependencies:

```bash
pip install -r requirements.txt
```

## Integration

This agent is designed to be loaded by [Hermes Agent](https://hermes-agent.nousresearch.com) as a skill for LLM-driven analysis. The companion scripts provide live data that feeds into the agent's reasoning.

---

*Part of the Telos ecosystem — qualitative analysis intelligence for crypto futures trading.*
