# IMC Prosperity Tutorial Bot

Python trading algorithm for IMC Prosperity — Tutorial Round (EMERALDS + TOMATOES).

## Strategy

Uses the **Take → Make → Clear** framework consistently applied by top-finishing teams across all Prosperity editions.

### EMERALDS (stable asset)
- Fair value hardcoded at **10,000** (market-maker walls sit at exactly 9992/10008)
- **Take**: lift any ask < 10000 or hit any bid > 10000 (guaranteed profit)
- **Clear**: place 0-EV orders at 10000 when |position| > 50 to free capacity
- **Make**: passive quotes at 9998/10002 (inside the ±8 walls), skewed toward inventory reduction

### TOMATOES (drifting asset)
- Fair value = **Wall Mid** — midpoint of the highest-volume bid/ask levels
  - This is the price IMC's engine uses internally, confirmed from historical data
  - Fast EMA (α = 0.6) smooths 1-tick noise while tracking the drift
- **Take**: lift asks > 1 tick below fair value; hit bids > 1 tick above (buffer avoids noise)
- **Clear**: 0-EV orders at fair value when |position| > 50
- **Make**: passive quotes at fair_value ± 5 (inside the market maker's ±8 walls), position-skewed

### Why Wall Mid instead of simple mid-price?
The simulation's internal PnL uses the market-maker bot's midpoint, not the raw order-book mid.
Using Wall Mid reduces tracking error and directly increases realized PnL — confirmed empirically by top finishers in Prosperity 2 and 3.

### Position Skewing
When inventory builds (e.g., long TOMATOES), quotes are shifted downward: bids become less aggressive and asks more competitive, naturally reducing the position without forcing 0-EV clears.

## File Structure

| File | Purpose |
|---|---|
| `trader.py` | Submitted algorithm — the `Trader` class with `run()` |

`datamodel.py` is injected by the IMC platform at runtime and is not included here.

## Backtesting

Install the community backtester:

```bash
pip install prosperity3bt
```

Run against the tutorial data capsule:

```bash
prosperity3bt trader.py 0
```

## Key Parameters

| Parameter | Value | Notes |
|---|---|---|
| EMERALD_FV | 10000 | Constant — verified from all historical timestamps |
| EMERALD_EDGE | ±2 | Quotes inside market-maker ±8 walls |
| EMERALD_SOFT | 50 | Clearing threshold (62% of limit) |
| TOMATO_ALPHA | 0.6 | EMA speed: fast enough to track drift |
| TOMATO_EDGE | ±5 | Quotes inside market-maker ±8 walls |
| TOMATO_SOFT | 50 | Clearing threshold |
