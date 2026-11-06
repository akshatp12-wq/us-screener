# US Stock & ETF Screener

A research-grade, evidence-based screener for US stocks and ETFs, built only from free public data, with a
live dashboard on GitHub Pages.

**Dashboard:** https://akshatp12-wq.github.io/us-screener/

Every metric on the dashboard can be tapped. A panel opens with two tabs: **What it says now** reads
the live value in plain English, and **Background & evidence** covers the research behind the metric,
whether it held up after publication, and this tool's own test results. The text lives in
`docs/js/explain.js`.

> **Not investment advice.** This is a research tool. Backtests are hypothetical, ignore taxes, and are
> subject to the data limitations listed below. Past results do not predict future returns.

## What it does

1. **Regime gauge.** Ten market-level dials (credit, yield curve, credit spreads, valuation, sentiment,
   volatility risk premium, bubbles, crash risk, trend) roll up into one label: **Calm**, **Stretched** or
   **Fragile**.
2. **Stock rankings.** The S&P 1500 is scored within each GICS sector on value, momentum, profitability,
   investment, issuance, opportunistic insider trades, short interest, earnings surprise and low volatility.
3. **ETF rankings.** Sector, industry, factor, bond and real-asset ETFs, ranked on momentum and trend with a
   bubble penalty. Where a fund publishes its holdings for free, a look-through stock score is added.
4. **Model portfolios.** One portfolio per strategy mode, each with a scenario engine, historical stress
   tests and a fat-tailed Monte Carlo.
5. **Validation.**
   - Factor-level backtests since 1963 on survivorship-free data, split before and after each signal's
     publication.
   - Out-of-sample tests of the market dials.
   - A stock-level backtest of the actual screener since 2011.
   - A live track record.

## Run it

```bash
pip install -r requirements.txt
export SEC_USER_AGENT="Your Name you@example.com"   # SEC requires a contact in the User-Agent
python pipeline/run.py --job all       # nightly rebuild + weekly backtests (first run ~20-40 min)
python pipeline/run.py --job intraday  # ETFs, sentiment, regime only
python -m pytest -q                    # unit tests + an offline end-to-end run on synthetic data
python -m http.server -d docs 8000     # open http://localhost:8000
```

Outputs go to `docs/data/*.json`, plus `manifest.json`. The manifest records the run time and, for every
source, when it was last fetched, the latest observation date, and any error.

## Automation (GitHub Actions)

| Schedule (UTC) | Job | What runs |
|---|---|---|
| `7,37 13-21 * * 1-5` | intraday | ETF/index prices, sentiment, VRP, bubble watch, regime, ETF ranks. Skips outside NYSE hours. |
| `23 22 * * 1-5` | nightly | Full rebuild: universe, prices, SEC fundamentals, insiders, short interest, scores, portfolios, events |
| `41 9 * * 6` | weekly | Backtests and validation |
| manual | any | **Actions → Pipeline → Run workflow** |

Every run does four things:

- commits `docs/data`
- deploys the dashboard
- caches raw downloads in `actions/cache`, so only changed data is refetched (SEC daily indexes decide
  which companies to refresh)
- opens a GitHub issue if a stage fails

A keep-alive workflow stops GitHub from disabling the schedules after 60 quiet days. Cron timing on GitHub
is best-effort, and the minimum interval is 5 minutes.

**Secrets:** `SEC_USER_AGENT` (required for SEC data). No paid keys are used.

## Editing `config.yaml`

Every weight, threshold, universe setting and assumption lives in `config.yaml`. Commit a change and the next
run picks it up. Some examples:

- **Strategy weights:** `modes.long_term.weights`, `modes.market_neutral.weights` and
  `modes.absolute_return.weights`. Weights are renormalised over whichever signals have data.
- **Dial thresholds:** `macro.credit_gap.red_above`, `macro.yield_curve.red_above` and
  `sentiment.euphoric_z`.
- **Bubble rules:** `bubble.amber_excess`, `bubble.red_excess` and `bubble.stretched_share`.
- **Portfolio rules:** `portfolio.max_position`, `portfolio.sector_cap`, `portfolio.turnover_budget` and
  `universe.min_dollar_volume`.
- **ETF list:** `etfs.list`, including hand-maintained expense ratios.
- **Event dates:** `events.fomc`, `events.cpi` and `events.jobs`, from the Fed and BLS calendars. Extend
  these each year.
- **Scenario assumptions:** `scenarios.base_probs`, `scenarios.shocks` and `scenarios.stress_windows`.
- **Backtest settings:** `backtest.publication`, which sets the McLean–Pontiff split dates, and
  `backtest.factor_costs`.

## Signals, sources and studies

| # | Signal | Source | Rule | Study |
|---|---|---|---|---|
| 1 | Credit-to-GDP gap | BIS via FRED `QUSPAM770A` (% of GDP; the spec's `CRDQUSAPABIS` is a dollar level) | one-sided HP (λ=400,000); green <2, red >10 pp | Borio & Lowe (2002); Drehmann & Juselius (2014) |
| 2 | Red zones | FRED `QUSNAM770A`, `QUSHAM770A`, `CSUSHPINSA`; Shiller | 3y credit growth ≥80th pct AND 3y price growth ≥66.7th pct | Greenwood, Hanson, Shleifer & Sorensen (2022) |
| 3 | Yield-curve recession odds | FRED `T10Y3M` | probit fitted to the published table; red >40% | Estrella & Mishkin (1996, 1998) |
| 4 | Excess bond premium | Fed `ebp_csv.csv` (fallback HY OAS) | amber >0, red >0.5 | Gilchrist & Zakrajšek (2012) |
| 5 | CAPE | Shiller `ie_data.xls` | percentile + 10y expected-return range; never a timing signal | Campbell & Shiller (1988, 1998) |
| 6 | Sentiment composite | VIX, SPHB/SPLV, IPO/SPY; Baker–Wurgler | euphoric >+1 sd, fearful <−1 sd | Baker & Wurgler (2006) |
| 7 | Variance risk premium | VIX, SPY | percentile; high = better odds | Bollerslev, Tauchen & Zhou (2009) |
| 8 | Bubble watch | ETF prices | 2y excess ≥100% amber, ≥150% red | Greenwood, Shleifer & You (2019) |
| 9 | Momentum crash guard | SPY | 24m return <0 and high vol → momentum ×0.25 | Daniel & Moskowitz (2016) |
| 10 | Market trend | SPY, IEF, GLD, DBC | 12m excess return + 10-month SMA | Moskowitz, Ooi & Pedersen (2012) |
| 11 | Value | SEC companyfacts + prices | B/M and E/P, within sector | Fama & French (1992) |
| 12 | Momentum | prices | 12–1 month return | Jegadeesh & Titman (1993) |
| 13 | Profitability | SEC | gross profit / assets | Novy-Marx (2013) |
| 14 | Investment | SEC | asset growth (low is good) | Cooper, Gulen & Schill (2008) |
| 15 | Issuance | SEC + splits | split-adjusted share growth; young-listing penalty | Loughran & Ritter (1995) |
| 16 | Opportunistic insiders | SEC Form 3/4/5 data sets | routine = same month 3 years running; net opportunistic buys | Cohen, Malloy & Pomorski (2012) |
| 17 | Short interest | FINRA (twice monthly) | low positive, extreme negative; squeeze flag | Boehmer, Huszar & Jordan (2010); Asquith, Pathak & Ritter (2005) |
| 18 | Earnings surprise (SUE) | SEC EPS + 8-K item 2.02 dates | seasonal random walk, timed to the earnings press release | Bernard & Thomas (1989) |
| 19 | Low volatility / beta | prices | 1y vol and 2y beta (low is good) | Frazzini & Pedersen (2014) |
| 20–21 | ETF momentum, trend, look-through | prices, SPDR holdings | mode-weighted | Moskowitz & Grinblatt (1999) |
| 22 | Event calendar | Fed, BLS, Yahoo | flag events within 7 days | Savor & Wilson (2013); Lucca & Moench (2015) |

Some popular signals are excluded on purpose because they failed replication in Hou, Xue & Zhang (2020):
the Piotroski F-score, Altman Z, dividend yield, and most liquidity signals.

### Strategy modes

- **Absolute return (default).** Weights: value .2, profitability .2, investment .15, issuance .15,
  insiders .1, low volatility .2.
  - Exposure is volatility-targeted (Moreira & Muir 2017), then multiplied by the regime multiplier.
  - The stock sleeve is halved when the market closes a month below its 10-month average.
  - A trend sleeve takes 30%.
  - A drawdown brake engages at −15%.
- **Long-term.** Weights: value .25, profitability .25, investment .2, issuance .2, insiders .1. Valuation
  sets return expectations; the regime only changes concentration and gross exposure.

A reweighting toward insiders (.2) and away from low volatility (.05) was tested for v2 and not adopted:
it changed nothing before 2011 and lowered the long-term mode's stock-level return since 2011.
- **Market-neutral long/short (research only).** Weights: value .2, momentum .2, profitability .2,
  investment .1, issuance .15, insiders .15, short interest .1. Tracked and backtested, but not offered
  as a model portfolio: it has been flat at the factor level since 2010 and lost money at the stock level.
  - Sizing is beta-neutral, and the book is scaled by sentiment (Stambaugh, Yu & Yuan 2012).
  - The momentum crash guard is on.

The regime label is a reading of risk now, not a forecast: Fragile = credit warning and Stretched;
Stretched = euphoric sentiment, 20%+ of sector ETFs bubble-flagged, or a credit warning on its own. The
yield curve, excess bond premium and variance risk premium are shown for context but do not feed it.

The **buy watch** card fires when the market has fallen 15%+ from its high within 12 months and then
closes a month back above its 10-month average (the one entry rule that worked both in 1960–1999 and
after 2000). A separate capitulation flag marks a 20%+ drawdown with VIX ≥ 35 or fearful sentiment. It is
for speeding up money you were going to invest anyway, not for holding cash.

## How it was validated

- **Factor level, 1963 to now.** Ken French CRSP portfolios stand in for each signal: HML, Mom, RMW, CMA,
  the net-issuance quintile spread, and the variance and beta spreads. They are survivorship-free and
  include delisting returns.
  - Returns are reported gross and net of trading-cost assumptions.
  - Each signal is split pre- and post-publication (McLean & Pontiff 2016).
  - Health is measured on a rolling 36-month window.
- **Strategy modes.** Factor-proxy versions of all three modes are compared with the US market.
- **Market dials.**
  - Yield curve: AUC against NBER recessions, including after 1996.
  - EBP: recession AUC.
  - CAPE: out-of-sample R² against the historical mean.
  - VRP: next-quarter return by quintile.
  - Bubbles: replication of Greenwood–Shleifer–You on 49 industries since 1926.
  - Trend rules, the crash guard, and forward returns by regime label are also tested.
  - Publication lags are applied throughout: 2 quarters for credit, 2 months for house prices, 1 month for
    Baker–Wurgler.
- **Stock level, 2011 to now.** The real screener runs month by month with point-in-time SEC fundamentals,
  using each fact's first `filed` date.
  - The universe is today's S&P 1500, so survivorship bias is measured explicitly: the equal-weighted
    universe is compared with RSP.
- **Live track record.** Each nightly top-pick list is saved and measured against SPY going forward.

## Known limitations

- **Survivorship.** Free price history excludes delisted stocks, so the stock-level backtest is flattered.
  Judge it by the spread over its own universe, not by its return versus SPY. Code for a point-in-time
  S&P 500 membership check exists (`screener/data/membership.py`) but is switched off: Wikipedia's page
  no longer carries the change-log table it was built on.
- **Multi-class companies.** Market cap for companies such as Berkshire and Visa comes from Yahoo in the
  live screen (SEC data reports shares per class). Their history in the backtest still lacks a market cap.
- **XBRL coverage.** Data starts in 2009–2011, and some companies tag concepts inconsistently. Financial
  companies often have no gross profit.
- **Short interest.** Free FINRA files cover exchange-listed stocks only from mid-2021, so short interest is
  used live but not in the long backtest. There is no free borrow-cost data; very high short interest is
  used as the hard-to-borrow proxy.
- **SUE timing.** SUE is dated to the earnings press release (8-K item 2.02) but computed from the EPS in
  the later 10-Q/10-K. The two almost always match; where a company revised the number in between, the
  backtest uses the revised figure a few weeks early. Falls back to the filing date when no release is found.
- **Baker–Wurgler.** The index is built with full-sample PCA and its public file ends years ago. The live
  composite proxy replaces it.
- **Yahoo Finance.** Yahoo is unofficial and sometimes blocks cloud runners; Stooq is the fallback. ETF
  expense ratios are maintained by hand in `config.yaml`.
- **Scenario shocks.** These are calibrated assumptions, not forecasts. The stress-test replay uses today's
  holdings (survivors); the beta estimate is shown next to it.
- **Factor proxies.** The library proxies are not identical to this screener's definitions. RMW is
  operating profitability, not gross profitability, and insiders have no free long history.

## Lead indicators (Leads page)

Five stock-level signals and one sector-ETF signal flag names worth researching. They do not change the strategy scores.

| Indicator | What it flags | Source study | Free history |
|---|---|---|---|
| Opportunistic insider buying | More opportunistic insiders buying than selling over 6 months (routine traders excluded) | Cohen, Malloy & Pomorski (2012) | 2011 on (needs 3 years of history per insider) |
| Fundamental mispricing | Market value far below (or above) the value implied by fundamentals, by sector | Bartram & Grinblatt (2018), scaled by assets | about 2010 on |
| Residual momentum | 12-1 month return net of market, size and value exposure | Blitz, Huij & Martens (2011) | 2007 on; industries 1963 on |
| Earnings reaction | Market-adjusted 3-day return around the latest earnings release | Chan, Jegadeesh & Lakonishok (1996) | 2009 on |
| Abnormal volume | Last week's volume vs the prior 50 days | Gervais, Kaniel & Mingelgrin (2001) | 2004 on |
| Sector ETF residual momentum | Top sector and industry ETFs by residual momentum (separate ETF list) | Blitz, Huij & Martens (2011); Moskowitz & Grinblatt (1999) | 49 industries 1963 on; ETFs 2007 on |

Each flagged name shows its **signal date** (when the current flag started), the closing price that day,
and the return since, next to SPY's. Earnings reactions are dated to the day the 3-day reaction window
closes; insider buying to the filing that turned net buying positive; abnormal volume to the first day of
the spike; mispricing and residual momentum to the first month end of the current run. Every flag is
logged from the day it starts in `docs/data/leads_state.json`, which builds a live record per indicator.

Bearish lists (bottom 10%, and net opportunistic insider selling) get their own status, judged on how far their
names lagged the universe. Every table on every tab sorts by any column: click a header (again to reverse).

The dashboard's ↻ button reloads the latest published data. "Recompute ↗" opens the pipeline on GitHub,
where **Run workflow** recalculates everything (about 5–20 minutes; the page picks it up automatically).

The "flagged by two or more" list counts only indicators whose flagged names beat the universe before costs
over their full backtest. A first version used "two or more insiders buying within 60 days"; it showed no edge
and was replaced by the opportunistic-insider rule above.

The weekly job backtests each one (`docs/data/leads_backtest.json`): each month the flagged names' next-month
return is compared with the liquid universe, split at 2011. Status: **Validated** (t ≥ 3, positive before and
after 2011), **Promising** (t ≥ 2), or **Unproven**. The nightly job writes today's lists to `docs/data/leads.json`.
Settings are under `leads:` in `config.yaml`.

## Version 2 (October 2026)

Each candidate change was tested on its own: added to v1, and toggled in v2. It was adopted only if it
improved the factor-level Sharpe ratio in 1963–2010 (not just since 2011). Full results are in
`docs/data/compare.json` and on the Backtests page. v1 is commit `7f16160`.

| Change | 1963–2010 Sharpe (abs. return) | Since 2011 | Adopted |
|---|---|---|---|
| Halve stocks below the 10-month average | +0.06 | −0.02 to −0.03 | Yes |
| Credit warning alone → Stretched | +0.02 to +0.03 | +0.02 to +0.03 | Yes |
| Reweight toward insiders, away from low volatility | −0.004 to +0.007 | −0.01 | No |
| Earnings surprise timed to the press release | no pre-2011 data | spread −0.4% → +0.4% a year (not significant) | Yes (timing fix) |
| T-bill interest on cash in the stock-level test | accounting | +1.3% a year for abs. return | Yes (fix) |

Absolute return, factor level: 1963–2010 return 10.0% → 10.5% a year, Sharpe 0.50 → 0.58, average
bear-market loss −10.4% → −7.0%. Since 2011: Sharpe 0.91 in both, return 9.0% → 8.4% a year. The trend
switch costs return in fast rebounds such as 2020.

## Repository layout

```
screener/      data/ signals/ regime/ scoring/ portfolio/ scenarios/ backtest/
pipeline/      run.py (entry point), live.py, weekly.py, common.py
docs/          dashboard (index.html, css/, js/) and data/*.json
tests/         unit tests per signal + synthetic-source end-to-end test
.github/       workflows (pipeline, tests, keep-alive) and helper scripts
config.yaml    every weight, threshold and setting
```

## Crash replay and buy timing

The weekly job replays every US bear market since 1960, defined as a month-end drawdown of 15% or more
in the CRSP market. For each one it records:

- what the dials said 12 and 6 months before the peak, and at the trough
- how much warning each dial gave, compared with how often that warning fires in normal months
- how each strategy mode did from peak to trough

It then tests a grid of buy rules: drawdown depth, alone or combined with a trend turn, fearful
sentiment, credit spreads past their peak, or VIX. The rules are chosen on 1960–1999 only and judged on
2000 onward. A pre-registered composite buy score is tested the same way. A cash-reserve simulation asks
whether holding cash to buy dips beats staying invested.

Results are in `docs/data/crashes.json` and on the dashboard's Crashes page. The monthly inputs are in
`docs/data/research_panel.csv`, so every number can be re-derived.
