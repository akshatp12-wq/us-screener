# US Stock & ETF Screener

A research-grade, evidence-based screener for US stocks and ETFs, built only from free public data, with a
live dashboard on GitHub Pages.

**Dashboard:** https://akshatp12-wq.github.io/us-screener/

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
| 1 | Credit-to-GDP gap | BIS via FRED `CRDQUSAPABIS` | one-sided HP (λ=400,000); green <2, red >10 pp | Borio & Lowe (2002); Drehmann & Juselius (2014) |
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
| 18 | Earnings surprise (SUE) | SEC EPS | seasonal random walk, filing-date timed | Bernard & Thomas (1989) |
| 19 | Low volatility / beta | prices | 1y vol and 2y beta (low is good) | Frazzini & Pedersen (2014) |
| 20–21 | ETF momentum, trend, look-through | prices, SPDR holdings | mode-weighted | Moskowitz & Grinblatt (1999) |
| 22 | Event calendar | Fed, BLS, Yahoo | flag events within 7 days | Savor & Wilson (2013); Lucca & Moench (2015) |

Some popular signals are excluded on purpose because they failed replication in Hou, Xue & Zhang (2020):
the Piotroski F-score, Altman Z, dividend yield, and most liquidity signals.

### Strategy modes

- **Long-term.** Weights: value .25, profitability .25, investment .2, issuance .2, insiders .1. Valuation
  sets return expectations; the regime only changes concentration and gross exposure.
- **Market-neutral long/short.** Weights: value .2, momentum .2, profitability .2, investment .1,
  issuance .15, insiders .15, short interest .1.
  - Sizing is beta-neutral, and the book is scaled by sentiment (Stambaugh, Yu & Yuan 2012).
  - The momentum crash guard is on.
  - Borrow and squeeze risk are flagged.
- **Absolute return.** Adds low volatility at .2.
  - Exposure is volatility-targeted (Moreira & Muir 2017), then multiplied by the regime multiplier.
  - A trend sleeve takes 30%.
  - A drawdown brake engages at −15%.

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
  Judge it by the spread over its own universe, not by its return versus SPY.
- **XBRL coverage.** Data starts in 2009–2011, and some companies tag concepts inconsistently. Financial
  companies often have no gross profit.
- **Short interest.** Free FINRA files cover exchange-listed stocks only from mid-2021, so short interest is
  used live but not in the long backtest. There is no free borrow-cost data; very high short interest is
  used as the hard-to-borrow proxy.
- **SUE timing.** SUE is timed to the filing date, which comes days to weeks after the earnings press
  release. It therefore understates the drift.
- **Baker–Wurgler.** The index is built with full-sample PCA and its public file ends years ago. The live
  composite proxy replaces it.
- **Yahoo Finance.** Yahoo is unofficial and sometimes blocks cloud runners; Stooq is the fallback. ETF
  expense ratios are maintained by hand in `config.yaml`.
- **Scenario shocks.** These are calibrated assumptions, not forecasts. The stress-test replay uses today's
  holdings (survivors); the beta estimate is shown next to it.
- **Factor proxies.** The library proxies are not identical to this screener's definitions. RMW is
  operating profitability, not gross profitability, and insiders have no free long history.

## Repository layout

```
screener/      data/ signals/ regime/ scoring/ portfolio/ scenarios/ backtest/
pipeline/      run.py (entry point), live.py, weekly.py, common.py
docs/          dashboard (index.html, css/, js/) and data/*.json
tests/         unit tests per signal + synthetic-source end-to-end test
.github/       workflows (pipeline, tests, keep-alive) and helper scripts
config.yaml    every weight, threshold and setting
```
