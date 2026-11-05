/* Plain-English explanations for every metric on the dashboard.
   Each entry: title, short (what it measures), now(ctx) (reading of the live value), use (how to act on it),
   origin + held (research history and whether it held up), ours(ctx) (this tool's own tests), refs.
   Loaded before app.js, which calls window.ExplainContent(H) with formatting helpers and live data. */
(function () {
  "use strict";
  window.ExplainContent = function (H) {
    const { pct, spct, num, esc } = H;
    const D = () => H.data() || {};
    const P = (s) => s ? `<p>${s}</p>` : "";
    const dial = (k) => ((D().regime || {}).dials || {})[k] || {};
    const val = () => D().validation || {};
    const fa = (name) => (((D().crashes || {}).replay || {}).false_alarms || {})[name];
    const base = () => ((((D().crashes || {}).replay || {}).false_alarms) || {})._base_rate;
    const faLine = (name, label) => {
      const x = fa(name);
      return x ? `In the crash replay since 1960, months when ${label} were followed by a 15%+ bear market within two years ${pct(x.followed_by_bear_within_24m, 0)} of the time, against ${pct(base(), 0)} for an average month (flag on in ${pct(x.share_on, 0)} of months).` : "";
    };
    const pctWord = (p) => !H.isNum(p) ? "–" : p >= 0.9 ? "very high" : p >= 0.7 ? "high" : p >= 0.3 ? "middling" : p >= 0.1 ? "low" : "very low";
    const statusWord = (s) => ({ green: "green (no warning)", amber: "amber (caution)", red: "red (warning)" }[s] || "unavailable");
    const mode = () => H.mode();
    const MODE_NAMES = { long_term: "Long-term", market_neutral: "Market-neutral", absolute_return: "Absolute return" };
    const stock = (t) => ((D().stocks || {}).rows || []).find((r) => r.t === t);
    const etf = (t) => (((D().etfs || {}).modes || {})[mode()] || []).find((r) => r.t === t);
    const bub = (t) => ((D().bubble || {}).rows || []).find((r) => r.ticker === t);
    const weightOf = (k) => { const w = (((D().stocks || {}).modes || {})[mode()] || {}).weights || {}; return w[k]; };
    const factorSig = (k) => (((D().backtest || {}).factor || {}).signals || {})[k];
    const stockSig = (k) => ((D().stock_backtest || {}).signals || {})[k];
    const mp = (k) => {
      const s = factorSig(k); if (!s || !s.mclean_pontiff || !s.mclean_pontiff.pre) return "";
      const m = s.mclean_pontiff;
      return `Factor level (Ken French CRSP data, survivorship-free): the long/short spread earned ${spct(m.pre.ann_mean)} a year before publication (${esc(String(m.publication).slice(0, 4))}) and ${spct(m.post.ann_mean)} after (t = ${num(m.post_t_nw, 1)}). Recent 36-month health: ${esc(((s.health || {}).status === "green" ? "healthy" : (s.health || {}).status === "amber" ? "fading" : (s.health || {}).status === "red" ? "decayed" : "n/a"))}.`;
    };
    const sl = (k) => {
      const s = stockSig(k); if (!s || !s.top30_minus_bottom30) return "";
      return `Stock level since 2011 (this screener on today's S&P 1500, so survivorship-biased): top 30% minus bottom 30% within sector earned ${spct(s.top30_minus_bottom30.ann_mean)} a year (t = ${num(s.t_nw, 1)}${Math.abs(s.t_nw) < 2 ? ", not statistically significant" : ""}).`;
    };
    const SIG_PHRASE = {
      value: ["cheaper", "than", "on book-to-market and earnings yield"],
      momentum: ["had a stronger 12-month return (skipping the latest month)", "than", ""],
      profitability: ["more profitable relative to assets", "than", ""],
      investment: ["grew its assets more slowly", "than", ""],
      issuance: ["issued fewer new shares (or bought back more)", "than", ""],
      insiders: ["had more net opportunistic insider buying", "than", ""],
      short_interest: ["had lower short interest", "than", ""],
      sue: ["had a bigger positive earnings surprise", "than", ""],
      lowvol: ["was less volatile (volatility and beta)", "than", ""],
    };
    const RAW = {
      value: (x) => `Book/market ${num(x.bm)}, earnings yield ${pct(x.ep)}.`,
      momentum: (x) => `12–1 month return ${spct(x.mom)}.`,
      profitability: (x) => `Gross profit/assets ${pct(x.gpa)}, operating income/assets ${pct(x.opa)}.`,
      investment: (x) => `Asset growth over the last year ${spct(x.ag)}.`,
      issuance: (x) => `Share count change over the last year ${spct(x.iss)}.`,
      insiders: (x) => `Net opportunistic insider trades (last 6 months): ${H.isNum(x.ins) ? x.ins : "–"}.`,
      short_interest: (x) => `Short interest ${pct(x.si)} of shares, ${num(x.dtc, 1)} days to cover.`,
      sue: (x) => `Standardized unexpected earnings ${num(x.sue)} (surprise vs the same quarter last year, in standard deviations).`,
      lowvol: (x) => `Volatility ${pct(x.vol, 0)} a year, beta ${num(x.beta)}.`,
    };
    function stockNow(k, ctx, generic) {
      const r = ctx && stock(ctx);
      const w = weightOf(k);
      const wtxt = H.isNum(w) ? `In ${MODE_NAMES[mode()]} mode it carries ${pct(w, 0)} of the score.` : `It is not part of the ${MODE_NAMES[mode()]} score; it is shown for information.`;
      if (!r) {
        const cov = ((D().stocks || {}).coverage || {})[k];
        return P(generic) + P(`Each stock is ranked only against others in its GICS sector, from 0% to 100% (100% = most attractive on this signal). ${wtxt}${H.isNum(cov) ? ` Data is available for ${pct(cov, 0)} of the universe.` : ""}`) +
          P("Click a stock's row on the Stocks page, then a signal in its breakdown, to see that stock's reading.");
      }
      const v = r.f[k], ph = SIG_PHRASE[k];
      if (k === "insiders" && H.isNum(v) && (r.raw || {}).ins === 0) {
        return P(`<b>${esc(r.t)}: no net opportunistic insider buying or selling in the last six months.</b>`) +
          P(`Most companies are in the same position, and tied stocks share a middle rank, so its ${pct(v, 0)} percentile is neutral rather than a sign of buying.`) + P(wtxt);
      }
      if (!H.isNum(v)) return P(`${esc(r.t)} has no ${esc(k.replace("_", " "))} score: the underlying data is missing.`) + P(RAW[k](r.raw || {}));
      return P(`<b>${esc(r.t)}: ${pct(v, 0)}.</b> It ${ph[0]} ${ph[1]} ${pct(v, 0)} of ${esc(r.s)} stocks${ph[2] ? " " + ph[2] : ""}.`) +
        P(RAW[k](r.raw || {})) + P(wtxt) +
        P(v >= 0.8 ? "That is a strong reading on this signal." : v <= 0.2 ? "That is a weak reading on this signal." : "That is an unremarkable reading on this signal.");
    }

    const M = {};
    // =================================================================== regime page
    M.regime = {
      title: "Market regime (Calm / Stretched / Fragile)",
      short: "A one-word summary of how risky the market backdrop is right now, built from the credit dials, sentiment and the bubble watch.",
      now() {
        const r = (D().regime || {}).regime || {};
        const by = ((val().regime || {}).by_label || {})[r.label];
        return P(`<b>${esc(r.label || "–")}.</b> ${esc((r.reasons || []).join("; "))}.`) +
          P(`Rules: <b>Stretched</b> if sentiment is euphoric, 20% or more of sector ETFs are bubble-flagged, or a credit dial is red on its own. <b>Fragile</b> if a credit dial is red <i>and</i> the market is stretched. Otherwise <b>Calm</b>.`) +
          P(`The label sets the absolute-return mode's risk multiplier (now ${num(r.multiplier, 2)}×) and the long-term mode's number of holdings and cash level.`) +
          (by ? P(`Historically, months labelled ${esc(r.label)} were followed by an average 12-month market return of ${spct(by.fwd12_mean)} and a ${pct(by.prob_dd20, 0)} chance of a 20% drawdown.`) : "");
      },
      use: "Treat it as a reading of risk now, not a forecast of when a fall starts. Use it to size risk, not to jump in and out of the market.",
      origin: P("This label is specific to this tool. It combines three research-backed ingredients: credit booms (BIS credit gap; Greenwood, Hanson, Shleifer & Sørensen's red zones), investor sentiment (Baker & Wurgler), and industry bubbles (Greenwood, Shleifer & You)."),
      held: P("Each ingredient has published support, but the combination was designed here, not taken from a paper. It rests on few episodes: there have been only about a dozen US bear markets since 1960, and Fragile months cluster in a handful of them."),
      ours() {
        const by = (val().regime || {}).by_label || {};
        const rows = ["Calm", "Stretched", "Fragile"].filter((k) => by[k]).map((k) => `${k}: ${by[k].months} months, next-12-month return ${spct(by[k].fwd12_mean)}, chance of a 20% drawdown ${pct(by[k].prob_dd20, 0)}`);
        return (rows.length ? P(`Forward returns by label since 1960: ${rows.join("; ")}.`) : "") +
          P("In the crash replay the label mostly turned to Stretched or Fragile at or after market peaks rather than months ahead, which is why it is framed as risk now.") + P(faLine("Regime not Calm", "the label was not Calm"));
      },
      refs: ["Greenwood, Hanson, Shleifer & Sørensen (2022), Predictable Financial Crises, Journal of Finance", "Baker & Wurgler (2006), Investor Sentiment and the Cross-Section of Stock Returns, Journal of Finance", "Greenwood, Shleifer & You (2019), Bubbles for Fama, Journal of Financial Economics"],
    };
    M.credit_gap = {
      title: "Credit-to-GDP gap",
      short: "How far total private-sector borrowing (as a share of the economy) is above or below its long-run trend.",
      now() {
        const d = dial("credit_gap");
        return P(`<b>${num(d.value, 1)} percentage points of GDP</b>, ${statusWord(d.status)}, as of ${esc(d.as_of || "–")} (the data arrive about two quarters late).`) +
          P(H.isNum(d.value) && d.value < 0 ? "Negative means US households and businesses have borrowed <i>less</i> than the long-run trend would imply. Credit is not booming, so this dial sees no credit-driven crisis risk." :
            H.isNum(d.value) && d.value > 10 ? "Above 10 points is the Basel III danger zone: borrowing has run well ahead of the economy, the pattern seen before past banking crises." :
              "Between 2 and 10 points is a build-up worth watching; below 2 is normal.") +
          P(`This reading sits at the ${pct(d.percentile, 0)} percentile of US history.`);
      },
      use: "A red reading is a slow-moving warning about financial-system risk over the next few years. It says nothing about the next few months.",
      origin: P("Borio and Lowe at the Bank for International Settlements (2002) showed that rapid credit growth, measured as a gap from trend, preceded banking crises. Drehmann, Borio and Tsatsaronis (2011) found it the best single early-warning indicator across countries. Basel III adopted it as the starting point for the countercyclical capital buffer, with the same 2 and 10 point thresholds used here."),
      held: P("It remains a standard central-bank indicator. Its main weakness is the statistical trend (a one-sided Hodrick-Prescott filter): the trend is unreliable at the end of the sample, so real-time readings get revised (Edge & Meisenzahl 2011), and Hamilton (2018) argues the filter itself creates spurious patterns. In the US the gap has been deeply negative since the post-2008 deleveraging, so it has had little to say about recent markets."),
      ours() {
        const c = (val().credit || {}).credit_gap_red;
        return c ? P(`Red in ${c.months_flagged} months since 1960 (${esc((c.flag_periods || []).map((p) => p.join(" to ")).join(", "))}). Three-year market return when red: ${pct(c.fwd3y_ann_when_on)} a year vs ${pct(c.fwd3y_ann_when_off)} otherwise; chance of a 30% drawdown within three years ${pct(c.prob_dd30_3y_on, 0)} vs ${pct(c.prob_dd30_3y_off, 0)}. That is essentially one episode (2007–08), so read it cautiously.`) : "";
      },
      refs: ["Borio & Lowe (2002), Asset Prices, Financial and Monetary Stability, BIS Working Paper 114", "Drehmann, Borio & Tsatsaronis (2011), Anchoring Countercyclical Capital Buffers, International Journal of Central Banking", "Basel Committee (2010), Guidance for National Authorities Operating the Countercyclical Capital Buffer", "Edge & Meisenzahl (2011), The Unreliability of Credit-to-GDP Ratio Gaps in Real Time, IJCB", "Hamilton (2018), Why You Should Never Use the Hodrick-Prescott Filter, Review of Economics and Statistics"],
    };
    const redZone = (key, who, price) => ({
      title: `Red zone: ${who}`,
      short: `Flags the combination that has preceded financial crises: ${who} borrowing growing fast relative to the economy <i>and</i> ${price} rising sharply, both over the same three years.`,
      now() {
        const d = dial(key);
        const credHot = H.isNum(d.credit_pct) && d.credit_pct >= 0.8, priceHot = H.isNum(d.price_pct) && d.price_pct >= 0.667;
        return P(`<b>${cap(statusWord(d.status))}.</b> Over the last three years, ${who} credit changed by ${num(d.value, 1)} points of GDP (${pct(d.credit_pct, 0)} percentile of history) while ${price} rose ${spct(d.price_growth_3y, 0)} (${pct(d.price_pct, 0)} percentile).`) +
          P(credHot && priceHot ? "Both halves are hot. This is the pattern that preceded past crises." :
            priceHot ? `${cap(price)} have run up, but borrowing has not. A price rally financed without a credit boom has historically been much less dangerous, so the dial stays green.` :
              credHot ? `Borrowing is growing fast, but ${price} have not boomed. One half of the red zone is met.` :
                `Neither borrowing nor ${price} are booming by historical standards.`) +
          P(`Red requires credit growth in the top 20% of history and price growth in the top third.`);
      },
      use: "Red is a multi-year warning of financial-crisis risk, which tends to come with deep, slow bear markets. It is not a short-term timing signal.",
      origin: P(`Greenwood, Hanson, Shleifer and Sørensen (2022, Journal of Finance) studied dozens of countries since 1950. When rapid credit growth and rapid asset-price growth occurred together (their "red zone"), the chance of a financial crisis within three years was roughly 40%, several times the normal rate. Either one alone was a much weaker signal. They built the test separately for businesses (with stock prices) and households (with house prices), which is what the two dials here follow.`),
      held: P("The paper is recent, so there is little post-publication evidence yet. The US has had few financial crises, so US-only statistics are thin. The cut-offs (top 20% of credit growth, top third of price growth) follow the paper and use only data available at each date."),
      ours() {
        const c = (val().credit || {})[key === "red_zone_business" ? "business_red_zone" : "household_red_zone"];
        return c ? P(`Red in ${c.months_flagged} months since 1960 (${esc((c.flag_periods || []).slice(0, 6).map((p) => p.join(" to ")).join(", "))}). Three-year market return when red: ${pct(c.fwd3y_ann_when_on)} a year vs ${pct(c.fwd3y_ann_when_off)} otherwise; chance of a 30% drawdown within three years ${pct(c.prob_dd30_3y_on, 0)} vs ${pct(c.prob_dd30_3y_off, 0)}.`) + P(faLine("Credit dial red", "any credit dial was red")) : "";
      },
      refs: ["Greenwood, Hanson, Shleifer & Sørensen (2022), Predictable Financial Crises, Journal of Finance 77(2)"],
    });
    const cap = (s) => String(s).charAt(0).toUpperCase() + String(s).slice(1);
    M.red_zone_business = redZone("red_zone_business", "business", "stock prices");
    M.red_zone_household = redZone("red_zone_household", "household", "house prices");
    M.yield_curve = {
      title: "Recession odds (yield curve)",
      short: "The chance of a US recession within a year, implied by the gap between 10-year and 3-month Treasury yields.",
      now() {
        const d = dial("yield_curve");
        return P(`<b>${num(d.value, 0)}% chance of recession within four quarters</b>, ${statusWord(d.status)}. The 10-year yield is ${num(d.spread_qavg, 2)} points above the 3-month yield (quarter average).`) +
          P(H.isNum(d.spread_qavg) && d.spread_qavg < 0 ? "The curve is inverted: short rates above long rates. Historically that has come before most US recessions." : "The curve slopes upward (long rates above short rates), the normal shape, which is associated with low recession odds.") +
          P("Shown for context only: it does not feed the regime label.");
      },
      use: "Good at forecasting recessions, poor at timing stock-market peaks. Stocks have often kept rising for a year or more after an inversion.",
      origin: P("Estrella and Hardouvelis (1991) and Estrella and Mishkin (1996, 1998) showed that the slope of the yield curve predicts US recessions better than most economic indicators. The New York Fed publishes a recession-probability model built on the same 10-year minus 3-month spread; the probabilities here come from a probit fitted to Estrella and Mishkin's published table."),
      held: P("An inverted 10-year/3-month curve preceded every US recession from the late 1960s through 2020, with few false alarms. The 2022–24 inversion, the longest on record, was not followed by a recession within the usual window, a notable miss. Its record for timing stock-market peaks is much weaker than for recessions."),
      ours() {
        const y = val().yield_curve || {};
        return P(`Since ${esc(y.sample_start || "1953")}: area under the curve ${num(y.auc_recession_starts_within_12m)} for predicting a recession start within 12 months (0.5 = coin flip, 1.0 = perfect); ${num(y.auc_post_publication)} after 1996 (after the research was published).`) + P(faLine("Yield curve > 40% recession odds", "recession odds were above 40%") + " So it has been a weak warning for stock investors.");
      },
      refs: ["Estrella & Hardouvelis (1991), The Term Structure as a Predictor of Real Economic Activity, Journal of Finance", "Estrella & Mishkin (1996), The Yield Curve as a Predictor of U.S. Recessions, NY Fed Current Issues 2(7)", "Estrella & Mishkin (1998), Predicting U.S. Recessions: Financial Variables as Leading Indicators, Review of Economics and Statistics"],
    };
    M.ebp = {
      title: "Excess bond premium",
      short: "The part of corporate bond spreads that is not explained by default risk: a gauge of how nervous bond investors and lenders are.",
      now() {
        const d = dial("ebp");
        return P(`<b>${num(d.value, 2)} percentage points</b>, ${statusWord(d.status)}, as of ${esc(d.as_of || "–")} (${pct(d.percentile, 0)} percentile of history).`) +
          P(H.isNum(d.value) && d.value < 0 ? "Negative means investors are accepting <i>less</i> than usual compensation for corporate credit risk: credit conditions are easy, which can shade into complacency." : H.isNum(d.value) && d.value > 0.5 ? "Above 0.5 means lenders are demanding a large risk premium: credit is tightening, which has preceded economic slowdowns." : "Near zero means normal credit conditions.") +
          (H.isNum(d.recession_prob_ebp) ? P(`The Federal Reserve's own model using this premium puts recession odds at ${num(d.recession_prob_ebp, 0)}%.`) : "") + P("Shown for context only: it does not feed the regime label.");
      },
      use: "Watch for a sharp rise from low levels: that has signalled credit stress. A low reading is not by itself a buy signal.",
      origin: P("Gilchrist and Zakrajšek (2012, American Economic Review) split corporate bond spreads into expected default risk and a residual, the excess bond premium. The residual, which reflects the risk appetite of lenders, predicted economic activity better than standard spreads. The Federal Reserve publishes updated figures and a recession-probability model built on it (Favara, Gilchrist, Lewis & Zakrajšek 2016)."),
      held: P("It is widely used inside the Fed and in academic work as a measure of credit-market sentiment. It is estimated on the full bond panel, so historical values are not exactly what was knowable in real time."),
      ours() {
        const e = val().ebp || {};
        return P(`Area under the curve for predicting a recession start within 12 months: ${num(e.auc_recession_starts_within_12m)} (0.5 = coin flip). Its link to next-12-month stock returns is close to zero (R² ${num(e.fwd12_on_ebp && e.fwd12_on_ebp.r2, 3)}). That is why it was demoted to context only.`);
      },
      refs: ["Gilchrist & Zakrajšek (2012), Credit Spreads and Business Cycle Fluctuations, American Economic Review 102(4)", "Favara, Gilchrist, Lewis & Zakrajšek (2016), Updating the Recession Risk and the Excess Bond Premium, FEDS Notes"],
    };
    M.cape = {
      title: "Valuation (CAPE)",
      short: "The S&P 500's price divided by its average inflation-adjusted earnings over the past 10 years (the Shiller P/E).",
      now() {
        const d = dial("cape"), e = d.expected_10y_real || {};
        return P(`<b>${num(d.value, 1)}×</b>, higher than ${pct(d.percentile, 0)} of months since 1881 (${statusWord(d.status)}).`) +
          (H.isNum(e.median) ? P(`At this level, the historical relationship implies about ${pct(e.median)} a year after inflation over the next 10 years (range ${pct(e.low)} to ${pct(e.high)}). The long-run average has been roughly 6–7%.`) : "") +
          P("It does not say when prices will fall. The market has stayed expensive for years at a time.");
      },
      use: "Use it to set long-run return expectations and to think about how much risk you take. It has been a poor tool for deciding when to buy or sell.",
      origin: P("Campbell and Shiller (1988, 1998) showed that when prices are high relative to long-run average earnings, returns over the following decade tend to be low. Shiller popularised the measure in <i>Irrational Exuberance</i> (2000), warning before the dot-com peak."),
      held: P("The long-horizon link has held up reasonably well, but forecasts are wide. Goyal and Welch (2008) found that valuation ratios, CAPE included, mostly fail to beat a simple average for shorter-horizon forecasts out of sample. CAPE has been above its historical average for most of the last 30 years while returns stayed strong. Siegel (2016) argues accounting changes have pushed reported earnings down and CAPE up."),
      ours() {
        const c = val().cape || {};
        const q = c.fwd10y_by_cape_quintile || [];
        return P(`Ten-year returns by CAPE quintile, cheapest to most expensive: ${q.map((x) => pct(x)).join(", ")} a year. Fit: in-sample R² ${num(c.in_sample_r2)}, out-of-sample R² vs the historical average ${num(c.oos_r2_vs_hist_mean)} (positive = better than the average).`) + P(faLine("CAPE >= 90th pct", "CAPE was at the 90th percentile or higher"));
      },
      refs: ["Campbell & Shiller (1988), Stock Prices, Earnings, and Expected Dividends, Journal of Finance", "Campbell & Shiller (1998), Valuation Ratios and the Long-Run Stock Market Outlook, Journal of Portfolio Management", "Goyal & Welch (2008), A Comprehensive Look at the Empirical Performance of Equity Premium Prediction, Review of Financial Studies", "Siegel (2016), The Shiller CAPE Ratio: A New Look, Financial Analysts Journal"],
    };
    M.sentiment = {
      title: "Sentiment composite",
      short: "How euphoric or fearful investors are, from market prices: the VIX, high-beta vs low-volatility stocks, and recent IPOs vs the market.",
      now() {
        const d = dial("sentiment");
        return P(`<b>${num(d.value, 2)} standard deviations</b> from its five-year average: <b>${esc(d.label || "–")}</b> (${pct(d.percentile, 0)} percentile).`) +
          P(d.label === "euphoric" ? "Investors are paying up for risky, speculative stocks. Historically those stocks have done poorly afterwards." : d.label === "fearful" ? "Investors are avoiding risk. Historically speculative stocks and the market have done better after fearful readings." : "Neither euphoric (above +1) nor fearful (below −1).") +
          P("Euphoric sentiment pushes the regime toward Stretched and adds a penalty to small, young, volatile and unprofitable stocks.");
      },
      use: "Most useful for avoiding speculative stocks when sentiment is euphoric. It has not been a reliable warning of market-wide crashes.",
      origin: P("Baker and Wurgler (2006) built an investor-sentiment index and found that after high-sentiment periods, small, young, volatile, unprofitable and hard-to-value stocks earned relatively low returns. Stambaugh, Yu and Yuan (2012) found that many stock-return anomalies are stronger after high sentiment, mainly through overpriced stocks."),
      held: P("The cross-sectional effect has been widely replicated. The original index is estimated on the full sample, and the version this tool can download ends in 2018, so the tool uses a live proxy built from market prices. The proxy follows the same idea but is not the published index."),
      ours() {
        const s = val().sentiment || {};
        const hv = s.highvol_minus_lowvol || {}, m = s.market || {};
        return P(`Since 1960: high-volatility minus low-volatility stocks returned ${spct(hv.euphoric && hv.euphoric.fwd12_mean)} over the next 12 months after euphoric readings vs ${spct(hv.fearful && hv.fearful.fwd12_mean)} after fearful ones. The market returned ${spct(m.euphoric && m.euphoric.fwd12_mean)} after euphoric vs ${spct(m.fearful && m.fearful.fwd12_mean)} after fearful.`) + P(faLine("Sentiment euphoric", "sentiment was euphoric") + " So it is not a crash warning.");
      },
      refs: ["Baker & Wurgler (2006), Investor Sentiment and the Cross-Section of Stock Returns, Journal of Finance", "Baker & Wurgler (2007), Investor Sentiment in the Stock Market, Journal of Economic Perspectives", "Stambaugh, Yu & Yuan (2012), The Short of It: Investor Sentiment and Anomalies, Journal of Financial Economics"],
    };
    M.vrp = {
      title: "Variance risk premium",
      short: "How much more volatility options are pricing in (the VIX) than the market has actually delivered over the past month.",
      now() {
        const d = dial("vrp");
        return P(`<b>${num(d.value, 1)}</b> (${pct(d.percentile, 0)} percentile), ${statusWord(d.status)}.`) +
          P(H.isNum(d.percentile) && d.percentile >= 0.5 ? "Positive and above the median: investors are paying a normal-to-high premium for crash protection, which research links to better returns over the next few months." : "Low: either investors are complacent (cheap protection) or realised volatility has spiked above what options price. Both have come before weaker returns in research samples.") +
          P("Shown for context only: it does not feed the regime label.");
      },
      use: "A short-horizon (one quarter) indicator. Noisy on its own; treat it as context.",
      origin: P("Bollerslev, Tauchen and Zhou (2009) showed that the difference between implied and realised variance predicts stock returns over the following quarter, more strongly than common valuation ratios at that horizon."),
      held: P("The finding has been influential in options research. The effect is concentrated at horizons of a few months and is noisy, and the VIX history only starts in 1990, so the evidence base is short."),
      ours() {
        const v = val().vrp || {};
        const q = v.fwd_3m_by_vrp_quintile || [];
        return P(`Next-quarter market return by VRP quintile since 1990, lowest to highest: ${q.map((x) => spct(x)).join(", ")}. The slope is not statistically significant (t = ${num(v.fwd_3m_on_vrp && v.fwd_3m_on_vrp.t && v.fwd_3m_on_vrp.t.vrp, 1)}).`);
      },
      refs: ["Bollerslev, Tauchen & Zhou (2009), Expected Stock Returns and Variance Risk Premia, Review of Financial Studies"],
    };
    M.crash_guard = {
      title: "Momentum crash guard",
      short: "Turns on when the market has fallen over two years and volatility is high: the conditions in which momentum strategies have crashed.",
      now() {
        const d = dial("crash_guard");
        return P(`<b>${d.on ? "On" : "Off"}.</b> The market's two-year return is ${spct(d.market_24m)} and its six-month volatility is at the ${pct(d.vol_pct, 0)} percentile of history.`) +
          P(d.on ? "Momentum's weight in the market-neutral score is cut to a quarter until conditions ease." : "It turns on only when the two-year return is negative and volatility is in the top 20% of history. Momentum carries its full weight.");
      },
      use: "It only affects momentum, which is used in the market-neutral mode (research only).",
      origin: P("Daniel and Moskowitz (2016) found that momentum strategies (buy recent winners, sell recent losers) suffer rare but severe crashes, as in 1932 and 2009. They happen when the market rebounds sharply after a bear market with high volatility, because the beaten-down losers rally hardest. Barroso and Santa-Clara (2015) showed that scaling momentum down when its volatility is high avoids much of the damage."),
      held: P("Momentum crashes in panic states are well documented across markets. Dynamic and volatility-scaled versions improve risk-adjusted returns in the original studies; later work debates how much of the gain survives trading costs."),
      ours() {
        const c = ((D().backtest || {}).factor || {}).crash_guard;
        return c && c.mom_when_on ? P(`Since 1927 the guard was on in ${c.months_on} months. Momentum returned ${spct(c.mom_when_on.cagr)} a year in those months vs ${spct(c.mom_when_off.cagr)} otherwise. Worst month unguarded ${spct(c.worst_month_unguarded)} vs guarded ${spct(c.worst_month_guarded)}.`) : "";
      },
      refs: ["Daniel & Moskowitz (2016), Momentum Crashes, Journal of Financial Economics", "Barroso & Santa-Clara (2015), Momentum Has Its Moments, Journal of Financial Economics"],
    };
    M.buy_watch = {
      title: "Buy watch",
      short: "Fires after the market has fallen 15% or more and then closes a month back above its 10-month average: the one entry rule here that worked both before and after 2000.",
      now() {
        const d = dial("buy_watch");
        return P(`<b>${esc(d.state || "–")}.</b> ${esc(d.text || "")}`) +
          P(`The market is ${spct(d.drawdown_now)} from its high; the worst drawdown in the last 12 months was ${spct(d.worst_drawdown_lookback)}. It is ${d.above_sma ? "above" : "below"} its 10-month average. VIX ${num(d.vix, 1)}.`) +
          P(d.capitulation ? "The capitulation flag is on: a deep drawdown with panic. Historically these were the best entry points, but they are rare." : "The capitulation flag (20%+ drawdown with VIX ≥ 35 or fearful sentiment) is off.");
      },
      use: "Use it to speed up money you were already going to invest. Holding cash to wait for it has cost more than it saved.",
      origin: P("This rule comes from this tool's own crash study. Buy rules were chosen using 1960–1999 only and then judged on 2000 onward, so the later period is a genuine out-of-sample test. The 10-month average follows Faber (2007)."),
      held: P("Only a few dozen bear markets and rebounds exist in the data, so even the best rule rests on a small number of signals. Research on dip-buying generally finds that waiting in cash for better entry points lowers long-run returns."),
      ours() {
        const r = (((D().crashes || {}).buy_timing || {}).rules || []).find((x) => x.rule === "Drawdown >= 15% then trend turns up");
        const deps = (((D().crashes || {}).buy_timing || {}).deployments || [])[0];
        const full = deps && deps.periods && deps.periods["1960-2026"];
        return (r ? P(`Since 1960 it fired ${r.full.n} times. Average extra market return over the next 24 months vs an average month: ${spct(r.in_sample.edge24)} in 1960–99 and ${spct(r.out_of_sample.edge24)} after 2000.`) : "") +
          (full ? P(`Holding a 20% cash reserve to deploy on signals like this returned ${pct(full.buy_the_dip.cagr, 1)} a year since 1960 vs ${pct(full.fully_invested.cagr, 1)} for staying fully invested.`) : "");
      },
      refs: ["Faber (2007), A Quantitative Approach to Tactical Asset Allocation, Journal of Wealth Management", "This tool's crash study: docs/data/crashes.json (Crashes page)"],
    };
    M.trend = {
      title: "Market trend",
      short: "Whether an asset is in an uptrend: its 12-month return above cash and its price above its 10-month average.",
      now(ctx) {
        const t = ((D().regime || {}).trend || {})[ctx];
        if (!t) return P("Each asset is up when both its 12-month excess return over T-bills is positive and its price is above its 10-month average; down when both fail; mixed otherwise.");
        return P(`<b>${esc(cap(ctx))} (${esc(t.symbol)}): ${esc(t.signal || "–")}.</b> 12-month return ${spct(t.ret_12m)} (${spct(t.excess_12m)} over T-bills); ${t.above_10m_sma ? "above" : "below"} its 10-month average.`) +
          P(ctx === "stocks" ? "For stocks this also drives the absolute-return mode's trend switch: below the 10-month average, the stock sleeve is halved." : "Assets trending up get a share of the absolute-return mode's 30% trend sleeve; those trending down are left in cash.");
      },
      use: "Trend rules cut losses in slow, grinding bear markets and cost money in fast V-shaped rebounds.",
      origin: P("Faber (2007) showed that holding an asset only when its price is above its 10-month average sharply reduced drawdowns across asset classes with similar returns. Moskowitz, Ooi and Pedersen (2012) documented time-series momentum: an asset's own past 12-month return predicts its next month, across dozens of futures markets. Hurst, Ooi and Pedersen (2017) found trend-following profitable in every decade since the 1880s."),
      held: P("Trend-following has held up out of sample over long periods, especially in prolonged crises (1970s, 2000–02, 2008). It has struggled in choppy, quickly reversing markets such as the 2010s and the 2020 crash and rebound."),
      ours() {
        const t = val().trend || {};
        const s = t.stocks || {}, b = t.bonds || {};
        return s.trend ? P(`US stocks since 1927: trend rule ${pct(s.trend.cagr)} a year, Sharpe ${num(s.trend.sharpe)}, worst drawdown ${spct(s.trend.max_dd, 0)}; buy-and-hold ${pct(s.buy_hold.cagr)}, Sharpe ${num(s.buy_hold.sharpe)}, worst drawdown ${spct(s.buy_hold.max_dd, 0)}.${b.trend ? ` Bonds since 1954: Sharpe ${num(b.trend.sharpe)} vs ${num(b.buy_hold.sharpe)}.` : ""}`) : "";
      },
      refs: ["Faber (2007), A Quantitative Approach to Tactical Asset Allocation, Journal of Wealth Management", "Moskowitz, Ooi & Pedersen (2012), Time Series Momentum, Journal of Financial Economics", "Hurst, Ooi & Pedersen (2017), A Century of Evidence on Trend-Following Investing, Journal of Portfolio Management"],
    };

    // =================================================================== bubble watch
    const gsyOrigin = P("Fama has long argued that bubbles cannot be identified in advance: big price run-ups do not predict low average returns. Greenwood, Shleifer and You (2019) tested this on US industries since 1926. They agreed that run-ups do not predict lower <i>average</i> returns, but found they do predict a much higher chance of a crash (a 40% fall), especially when the rise accelerates, volatility climbs, and companies issue new shares.");
    const gsyHeld = P("The paper is recent; the pattern also appears in international data in the original study. Flagged industries often keep rising for a while, so a flag is a risk warning, not a sell signal.");
    const gsyOurs = () => {
      const b = val().bubbles || {};
      return P(`Replication on 49 US industries since 1926 (${b.n_events} events): chance of a 40% crash within two years ${pct(b.crash_prob_after_100pct, 0)} after a 100% run-up beyond the market, ${pct(b.crash_prob_after_150pct, 0)} after 150%, ${pct(b.crash_prob_with_acceleration, 0)} with acceleration, against ${pct(b.base_crash_prob, 0)} for any industry. Yet the average excess return over the next two years was ${spct(b.mean_fwd_2y_excess)}: exactly the paper's point that crash risk rises without average returns falling.`);
    };
    const gsyRefs = ["Greenwood, Shleifer & You (2019), Bubbles for Fama, Journal of Financial Economics 131(1)"];
    M.bubble_watch = {
      title: "Bubble watch",
      short: "Flags sector and industry ETFs whose price has run far ahead of the market over two years, the pattern that has preceded crashes.",
      now() {
        const b = D().bubble || {};
        return P(`<b>${b.n_flagged ?? "–"} of ${b.n_valid ?? "–"} ETFs flagged (${pct(b.flagged_share, 0)}).</b> ${b.stretched ? "That is above the 20% that tips the regime to Stretched." : "Below the 20% that tips the regime to Stretched."}`) +
          P("Amber: the ETF beat SPY by 100%+ over two years. Red: by 150%+, or 100%+ with acceleration. Warnings list the features that raise crash odds.");
      },
      use: "A flag means higher crash risk, not lower expected returns. Consider trimming or not adding, rather than shorting.",
      origin: gsyOrigin, held: gsyHeld, ours: gsyOurs, refs: gsyRefs,
    };
    M.bubble = {
      title: "Bubble reading for this ETF",
      short: "Whether this ETF's two-year run-up is large enough, and has the warning features, to signal elevated crash risk.",
      now(ctx) {
        const r = bub(ctx);
        if (!r) return M.bubble_watch.now();
        return P(`<b>${esc(r.ticker)} (${esc(r.name)}): ${statusWord(r.status)}.</b> Two-year return ${spct(r.return_2y, 0)} vs ${spct(r.market_2y, 0)} for SPY, so ${spct(r.excess_2y, 0)} ahead.`) +
          P(`Share of the gain earned in the last six months: ${pct(r.accel_share, 0)} (above 50% = accelerating). Volatility ratio, last year vs the year before: ${num(r.vol_ratio)} (above 1.25 = rising volatility).`) +
          P(H.isNum(r.excess_2y) && r.excess_2y >= 1 ? "The run-up is in bubble territory by the paper's definition." : "The run-up is below the 100% excess threshold, so it is not flagged on size, whatever its warnings say.") +
          ((r.warnings || []).length ? P(`Warnings: ${esc(r.warnings.join(", "))}.`) : "");
      },
      use: M.bubble_watch.use, origin: gsyOrigin, held: gsyHeld, ours: gsyOurs, refs: gsyRefs,
    };
    M.b_ret2y = { title: "Two-year return", short: "The ETF's total return over the last two years.", now: () => P("Raw two-year return. On its own it says little; what matters is how far it is ahead of the market (next column)."), use: "Read together with the excess over SPY.", origin: gsyOrigin, held: gsyHeld, ours: gsyOurs, refs: gsyRefs };
    M.b_excess = { title: "Two-year return vs SPY", short: "How far the ETF's two-year return is ahead of SPY's.", now: () => P("100% or more ahead = amber; 150% or more = red. These cut-offs follow Greenwood, Shleifer and You, who define a run-up as an industry beating the market by 100%+ over two years."), use: "The single most important bubble input.", origin: gsyOrigin, held: gsyHeld, ours: gsyOurs, refs: gsyRefs };
    M.b_accel = { title: "Last six months' share of the gain", short: "How much of the two-year gain came in the most recent six months.", now: () => P("Above 50% means the rise is accelerating. In the research, accelerating run-ups crashed more often."), use: "Acceleration turns an amber flag red.", origin: gsyOrigin, held: gsyHeld, ours: gsyOurs, refs: gsyRefs };
    M.b_volratio = { title: "Volatility ratio", short: "Volatility over the last year divided by volatility in the year before.", now: () => P("Above 1.25 means volatility is rising. Rising volatility during a run-up was associated with higher crash odds."), use: "A warning feature, not a flag on its own.", origin: gsyOrigin, held: gsyHeld, ours: gsyOurs, refs: gsyRefs };

    // =================================================================== stock signals
    const factorEntry = (k, o) => ({
      title: o.title, short: o.short,
      now: (ctx) => stockNow(k, ctx, o.generic || ""),
      use: o.use, origin: P(o.origin), held: P(o.held),
      ours: () => P(mp(o.factorKey || k)) + P(sl(k)) + (o.oursExtra ? P(o.oursExtra) : ""),
      refs: o.refs,
    });
    M.value = factorEntry("value", {
      title: "Value", short: "How cheap a stock is relative to sector peers, using book value to market value and earnings to price.",
      use: "Value works over long periods, with long droughts. Pair it with profitability so cheap does not mean broken.",
      origin: "Basu (1977) found low price-to-earnings stocks beat high ones. Fama and French (1992, 1993) made book-to-market a core factor after showing cheap stocks earned higher returns, and Lakonishok, Shleifer and Vishny (1994) argued it reflects investors overreacting to bad news.",
      held: "The value premium was strong for decades but weak from about 2007 to 2020, its worst stretch on record (Israel, Laursen & Richardson 2021). It recovered partly after 2021. Book value also understates companies built on intangible assets.",
      refs: ["Basu (1977), Journal of Finance", "Fama & French (1992), The Cross-Section of Expected Stock Returns, Journal of Finance", "Lakonishok, Shleifer & Vishny (1994), Contrarian Investment, Extrapolation, and Risk, Journal of Finance", "Israel, Laursen & Richardson (2021), Is (Systematic) Value Investing Dead?, Journal of Portfolio Management"],
    });
    M.momentum = factorEntry("momentum", {
      title: "Momentum", short: "The stock's return over the past 12 months, skipping the latest month, ranked within its sector.",
      use: "Strong historically but trades a lot; used only in the market-neutral mode, and costs eat much of it.",
      origin: "Jegadeesh and Titman (1993) found that stocks with the best returns over the past 3–12 months kept outperforming for the next several months. Asness, Moskowitz and Pedersen (2013) found the same across countries and asset classes.",
      held: "One of the most robust anomalies, but it needs high turnover; Novy-Marx and Velikov (2016) show trading costs consume much of it. It also suffers sudden crashes (see the crash guard).",
      refs: ["Jegadeesh & Titman (1993), Returns to Buying Winners and Selling Losers, Journal of Finance", "Asness, Moskowitz & Pedersen (2013), Value and Momentum Everywhere, Journal of Finance", "Novy-Marx & Velikov (2016), A Taxonomy of Anomalies and Their Trading Costs, Review of Financial Studies"],
    });
    M.profitability = factorEntry("profitability", {
      title: "Profitability", short: "Gross profit and operating income relative to total assets, ranked within the sector.",
      use: "Helps avoid value traps. Banks and insurers often lack gross profit data, so coverage is thinner there.",
      origin: "Novy-Marx (2013) showed that gross profit divided by assets predicts returns about as well as book-to-market, and works best combined with value. Fama and French (2015) added a profitability factor (RMW) to their five-factor model; Ball et al. (2015) favour operating profitability.",
      held: "It is now part of standard factor models. Like most published signals, its premium has been smaller since publication, and it has been weak in the last few years.",
      refs: ["Novy-Marx (2013), The Other Side of Value: The Gross Profitability Premium, Journal of Financial Economics", "Fama & French (2015), A Five-Factor Asset Pricing Model, Journal of Financial Economics", "Ball, Gerakos, Linnainmaa & Nikolaev (2015), Deflating Profitability, Journal of Financial Economics"],
    });
    M.investment = factorEntry("investment", {
      title: "Investment (asset growth)", short: "How fast the company grew its total assets over the past year. Slower growth ranks higher.",
      use: "Companies expanding aggressively (often by acquisition) have tended to disappoint.",
      origin: "Cooper, Gulen and Schill (2008) found that firms with the slowest asset growth outperformed the fastest-growing firms by a wide margin. Fama and French added it to their five-factor model as CMA (conservative minus aggressive).",
      held: "It has weakened since publication in US large caps but remains part of standard factor models.",
      refs: ["Cooper, Gulen & Schill (2008), Asset Growth and the Cross-Section of Stock Returns, Journal of Finance", "Fama & French (2015), A Five-Factor Asset Pricing Model, Journal of Financial Economics"],
    });
    M.issuance = factorEntry("issuance", {
      title: "Net share issuance", short: "The change in shares outstanding over the past year (adjusted for splits). Buybacks rank high; heavy issuance ranks low.",
      use: "Companies tend to issue shares when they are expensive and buy back when cheap; this signal follows management's timing.",
      origin: "Loughran and Ritter (1995) documented long-run underperformance after stock offerings ('the new issues puzzle'). Pontiff and Woodgate (2008) and Daniel and Titman (2006) showed that net share issuance predicts returns across all stocks.",
      held: "It has generally survived replication tests and kept its premium after publication over the long run, but it has not worked in US large caps since about 2011.",
      refs: ["Loughran & Ritter (1995), The New Issues Puzzle, Journal of Finance", "Pontiff & Woodgate (2008), Share Issuance and Cross-Sectional Returns, Journal of Finance", "Daniel & Titman (2006), Market Reactions to Tangible and Intangible Information, Journal of Finance"],
    });
    M.insiders = factorEntry("insiders", {
      title: "Opportunistic insider buying", short: "Net buying by company insiders, counting only 'opportunistic' trades (not routine ones made the same month every year).",
      use: "The one stock signal here with a clear edge since 2011. Most useful as a research lead on top of the other scores.",
      origin: "Insider purchases have long been known to predict returns (Seyhun 1986; Lakonishok & Lee 2001). Cohen, Malloy and Pomorski (2012) showed that the information is in 'opportunistic' trades: routine traders (who trade in the same month each year) carry no signal, while opportunistic trades predict significant abnormal returns.",
      held: "Free history starts with SEC Form 4 data sets, so long, bias-free tests are not possible here; the published evidence covers 1986–2007.",
      oursExtra: "Not available at factor level: no free long history.",
      refs: ["Cohen, Malloy & Pomorski (2012), Decoding Inside Information, Journal of Finance", "Seyhun (1986), Insiders' Profits, Costs of Trading, and Market Efficiency, Journal of Financial Economics", "Lakonishok & Lee (2001), Are Insider Trades Informative?, Review of Financial Studies"],
    });
    M.short_interest = factorEntry("short_interest", {
      title: "Short interest", short: "Shares sold short as a share of shares outstanding. Low short interest ranks high; extreme short interest ranks low.",
      use: "Short sellers are often well informed. Very high short interest also flags squeeze risk.",
      origin: "Dechow et al. (2001) found short sellers target overvalued stocks. Asquith, Pathak and Ritter (2005) found heavily shorted stocks underperform, and Boehmer, Huszar and Jordan (2010) found lightly shorted stocks earn positive abnormal returns.",
      held: "Supported in later work, but free US exchange-listed short-interest history only starts in mid-2021, so this tool cannot backtest it.",
      oursExtra: "Not backtested: free history is too short.",
      refs: ["Asquith, Pathak & Ritter (2005), Short Interest, Institutional Ownership, and Stock Returns, Journal of Financial Economics", "Boehmer, Huszar & Jordan (2010), The Good News in Short Interest, Journal of Financial Economics", "Dechow, Hutton, Meulbroek & Sloan (2001), Short-Sellers, Fundamental Analysis, and Stock Returns, Journal of Financial Economics"],
    });
    M.sue = factorEntry("sue", {
      title: "Earnings surprise (SUE)", short: "How much the latest quarter's earnings per share beat the same quarter a year earlier, in standard deviations, dated to the earnings press release.",
      use: "Shown for information; it carries no weight in any mode because it showed no edge here.",
      origin: "Ball and Brown (1968) and Bernard and Thomas (1989) found that prices keep drifting in the direction of an earnings surprise for weeks after the announcement (post-earnings-announcement drift).",
      held: "The drift has largely disappeared in recent US large-cap data; Martineau (2022) argues it is now close to zero as markets react faster.",
      refs: ["Ball & Brown (1968), An Empirical Evaluation of Accounting Income Numbers, Journal of Accounting Research", "Bernard & Thomas (1989), Post-Earnings-Announcement Drift, Journal of Accounting Research", "Martineau (2022), Rest in Peace Post-Earnings Announcement Drift, Critical Finance Review"],
    });
    M.lowvol = factorEntry("lowvol", {
      title: "Low volatility", short: "How calm the stock is: one-year volatility and two-year beta, both ranked low-is-good within the sector.",
      use: "Used in the absolute-return mode to dampen risk. It has not added return since 2011.",
      origin: "Black, Jensen and Scholes (1972) found low-beta stocks earned more than theory predicts. Ang et al. (2006) found high-volatility stocks earn low returns, and Frazzini and Pedersen (2014) built 'betting against beta' on the idea that leverage-constrained investors overpay for risky stocks.",
      held: "Robust over long histories and across countries, but low-volatility stocks have lagged badly in the strong, growth-led bull markets of the last decade.",
      refs: ["Black, Jensen & Scholes (1972), The Capital Asset Pricing Model: Some Empirical Tests", "Ang, Hodrick, Xing & Zhang (2006), The Cross-Section of Volatility and Expected Returns, Journal of Finance", "Frazzini & Pedersen (2014), Betting Against Beta, Journal of Financial Economics"],
    });
    M.score = {
      title: "Composite score",
      short: "The weighted average of the stock's signal percentiles for the selected mode, minus a penalty for speculative stocks when sentiment is euphoric.",
      now(ctx) {
        const r = ctx && stock(ctx), s = D().stocks || {}, w = ((s.modes || {})[mode()] || {}).weights || {};
        const wt = Object.entries(w).map(([k, v]) => `${k.replace("_", " ")} ${pct(v, 0)}`).join(", ");
        return (r ? P(`<b>${esc(r.t)}: ${H.isNum(r.m[mode()]) ? Math.round(r.m[mode()] * 100) : "–"} in ${MODE_NAMES[mode()]} mode.</b> ${esc((r.why || {})[mode()] || "")}.`) : "") +
          P(`Weights in ${MODE_NAMES[mode()]} mode: ${wt}. Missing signals are skipped and the others re-weighted; a stock needs data for 60% of the weight to get a score.`) +
          P(s.penalty_active ? "Sentiment is euphoric, so small, young, volatile and unprofitable stocks lose 5 points per characteristic." : "The euphoric-sentiment penalty is not active.");
      },
      use: "Use the score to build a research list. Since 2011 the long-term mode roughly matched the market, so do not expect it to beat the market by itself.",
      origin: P("Combining several independent signals is standard practice: the signals' errors partly cancel, so a composite is steadier than any single one. Ranking within sectors avoids betting on whole industries by accident."),
      held: P("Many published signals shrink after publication (McLean & Pontiff 2016), and many fail replication (Hou, Xue & Zhang 2020). Signals that failed replication (Piotroski F-score, Altman Z, dividend yield, most liquidity measures) are excluded."),
      ours() {
        const sb = (D().stock_backtest || {}).modes || {}, x = sb[mode()];
        return x && x.stats ? P(`Stock-level backtest of the ${MODE_NAMES[mode()]} score since 2011: ${pct(x.stats.cagr)} a year vs ${pct(x.spy && x.spy.cagr)} for SPY, worst drawdown ${spct(x.max_dd, 0)}. Survivorship bias flatters this by roughly 2–3% a year.`) : "";
      },
      refs: ["McLean & Pontiff (2016), Does Academic Research Destroy Stock Return Predictability?, Journal of Finance", "Hou, Xue & Zhang (2020), Replicating Anomalies, Review of Financial Studies"],
    };
    const rawEntry = (sigKey, title, short) => Object.assign({}, M[sigKey], { title, short });
    M.raw_bm = rawEntry("value", "Book / market", "Book value of equity divided by market value. Higher = cheaper.");
    M.raw_ep = rawEntry("value", "Earnings yield", "Trailing 12-month net income divided by market value (the inverse of P/E). Higher = cheaper.");
    M.raw_mom = rawEntry("momentum", "12–1 momentum", "Total return over the past 12 months, excluding the most recent month (which tends to reverse).");
    M.raw_gpa = rawEntry("profitability", "Gross profit / assets", "Revenue minus cost of goods sold, over the last four quarters, divided by total assets.");
    M.raw_opa = rawEntry("profitability", "Operating income / assets", "Operating income over the last four quarters divided by total assets.");
    M.raw_ag = rawEntry("investment", "Asset growth", "Percentage change in total assets over the past year.");
    M.raw_iss = rawEntry("issuance", "Net issuance", "Percentage change in split-adjusted shares outstanding over the past year. Negative = buybacks.");
    M.raw_ins = rawEntry("insiders", "Net opportunistic insiders", "Number of opportunistic insider buyers minus sellers over the last six months.");
    M.raw_si = rawEntry("short_interest", "Short interest", "Shares sold short divided by shares outstanding (FINRA, twice a month).");
    M.raw_dtc = rawEntry("short_interest", "Days to cover", "Shares sold short divided by average daily volume: how many days of normal trading it would take short sellers to buy back.");
    M.raw_sue = rawEntry("sue", "SUE", "Standardized unexpected earnings: the year-over-year change in quarterly EPS, divided by the standard deviation of past changes.");
    M.raw_vol = rawEntry("lowvol", "Volatility", "Annualized standard deviation of daily returns over the past year.");
    M.raw_beta = rawEntry("lowvol", "Beta", "How much the stock moves with the market, estimated over two years. 1 = moves with the market; below 1 = calmer.");

    // =================================================================== ETFs
    const etfNow = (ctx, generic) => {
      const r = ctx && etf(ctx);
      if (!r) return P(generic);
      return P(`<b>${esc(r.t)} (${esc(r.name)}): score ${H.isNum(r.score) ? Math.round(r.score * 100) : "–"} in ${MODE_NAMES[mode()]} mode.</b>`) +
        P(`12-month return ${spct(r.ret_12m)}; momentum ${spct(r.momentum)}; trend ${r.trend === 1 ? "up" : r.trend === 0 ? "down" : "mixed"} (${r.above_sma ? "above" : "below"} its 10-month average); volatility ${pct(r.vol, 0)}.`) +
        P(`Look-through: ${H.isNum(r.lookthrough) ? Math.round(r.lookthrough * 100) + ` (holdings coverage ${pct(r.lookthrough_coverage, 0)})` : "not available (daily holdings are only free for SPDR funds)"}. Bubble: ${esc(r.bubble || "n/a")}${r.bubble_penalty ? `, penalty ${pct(r.bubble_penalty, 0)}` : ""}. Expense ratio ${H.isNum(r.er) ? r.er.toFixed(2) + "%" : "–"}.`) +
        P("The score ranks ETFs against each other; it is a relative-strength rating, not a valuation or a buy/sell signal.");
    };
    const etfOrigin = P("ETF scores combine momentum (Moskowitz & Grinblatt 1999 found industry momentum explains much of stock momentum), trend (Faber 2007; Moskowitz, Ooi & Pedersen 2012), and, for SPDR funds, the quality of the stocks held, with a penalty from the bubble watch.");
    const etfHeld = P("Industry momentum and trend have long, out-of-sample records but are noisy month to month. The ETF scores themselves have not been backtested as a strategy in this tool.");
    const etfRefs = ["Moskowitz & Grinblatt (1999), Do Industries Explain Momentum?, Journal of Finance", "Moskowitz, Ooi & Pedersen (2012), Time Series Momentum, Journal of Financial Economics", "Faber (2007), A Quantitative Approach to Tactical Asset Allocation, Journal of Wealth Management"];
    const etfE = (title, short, generic, use) => ({ title, short, now: (ctx) => etfNow(ctx, generic), use, origin: etfOrigin, held: etfHeld, ours: () => "", refs: etfRefs });
    M.etf = etfE("ETF score", "A relative-strength rating of each ETF for the selected mode.", "Weights depend on the mode: long-term leans on look-through quality; absolute return uses volatility-adjusted momentum and trend.", "Use it to compare ETFs in a shortlist, not to decide whether to own equities at all.");
    M.etf_mom = etfE("ETF momentum", "Return over the past 12 months, skipping the latest month.", "Higher ranks better. In absolute-return mode, momentum is divided by volatility so calmer trends rank higher.", "Strong momentum can reverse in sharp rebounds.");
    M.etf_trend = etfE("ETF trend", "Up when the 12-month excess return is positive and price is above the 10-month average; down when both fail.", "Up = both conditions met, Down = neither, Mixed = one of the two.", "A down trend is the closest thing here to an exit signal for an individual ETF, but it has not been tested ETF by ETF.");
    M.etf_look = etfE("Look-through score", "The holdings-weighted average stock score of what the ETF owns (SPDR funds only; their daily holdings are free).", "Higher = the fund holds stocks that score well on value, profitability and the other signals. Coverage shows how much of the fund matched the stock universe.", "Unavailable for most non-SPDR funds.");
    M.etf_bubble = Object.assign({}, M.bubble, { now: (ctx) => ctx && bub(ctx) ? M.bubble.now(ctx) : P("Sector and industry ETFs only. Amber or red flags subtract 10 or 25 points from the score.") });
    M.etf_er = { title: "Expense ratio", short: "The fund's annual fee as a percentage of assets.", now: (ctx) => { const r = ctx && etf(ctx); return P(r ? `${esc(r.t)} charges ${H.isNum(r.er) ? r.er.toFixed(2) + "%" : "–"} a year.` : "Maintained by hand from issuer sites.") + P("Fees are one of the few reliable predictors of fund returns: every 0.1% comes straight off the return."); }, use: "Between similar funds, prefer the cheaper one.", origin: P("Carhart (1997) found that expenses are one of the most consistent predictors of fund underperformance, and French (2008) estimated the aggregate cost of active investing."), held: P("Strongly supported across decades of fund data."), ours: () => "", refs: ["Carhart (1997), On Persistence in Mutual Fund Performance, Journal of Finance", "French (2008), The Cost of Active Investing, Journal of Finance"] };
    M.etf_dv = { title: "Dollar volume per day", short: "Average daily trading value: a measure of how easily you can trade the fund.", now: () => P("Higher volume generally means tighter bid-ask spreads. Above about $5M a day is comfortably tradable for individual investors."), use: "Avoid very thinly traded funds; use limit orders.", origin: P("Trading costs rise as liquidity falls; this is standard market microstructure."), held: P("Well established."), ours: () => "", refs: [] };

    // =================================================================== portfolio
    const pf = () => ((D().portfolio || {}).modes || {})[mode()] || {};
    const pE = (title, short, now, use, origin, held, refs) => ({ title, short, now, use, origin: P(origin), held: P(held || ""), ours: () => "", refs: refs || [] });
    M.p_holdings = pE("Holdings", "Number of stocks in the model portfolio.", () => P(`${(pf().summary || {}).n ?? "–"} stocks. The long-term mode holds 30 in Calm, 40 in Stretched and 50 in Fragile regimes (more names, less concentration as risk rises); absolute return holds 40.`), "More names lower single-stock risk; beyond about 30–50, the extra diversification is small.", "Diversification research going back to Evans and Archer (1968) shows most single-stock risk is removed with a few dozen names.", "", ["Evans & Archer (1968), Diversification and the Reduction of Dispersion, Journal of Finance"]);
    M.p_exposure = pE("Gross / net exposure", "Gross = total of all positions; net = longs minus shorts. Shows how much of your capital is invested.", () => { const s = pf().summary || {}; return P(`Gross ${pct(s.gross, 0)}, net ${pct(s.net, 0)}.`) + P(mode() === "absolute_return" ? "Absolute return holds cash when volatility is high or the regime is not Calm, so net exposure is often well below 100%." : mode() === "market_neutral" ? "Market-neutral holds equal-risk longs and shorts, so net exposure is near zero." : "Long-term is fully invested in Calm regimes and holds 10–20% cash otherwise."); }, "Net exposure below 100% means less market risk and less market return.", "Standard portfolio accounting.");
    M.p_beta = pE("Portfolio beta", "How much the portfolio is expected to move for a 1% move in the market.", () => P(`${num((pf().summary || {}).beta)}. A beta of 0.5 means the portfolio would be expected to fall about 5% if the market fell 10%.`), "Beta is estimated from two years of history and changes over time.", "Beta comes from the capital asset pricing model (Sharpe 1964; Lintner 1965).", "", ["Sharpe (1964), Capital Asset Prices, Journal of Finance"]);
    M.p_turnover = pE("Turnover", "Share of the portfolio replaced since the last run.", () => { const t = pf().turnover || {}; return P(t.first_build ? "First build, so no turnover yet." : `${pct(t.turnover, 0)} of the portfolio changed since the last run.`) + P("Turnover is limited by keeping holdings that still rank within 1.5× the target count and replacing at most 30% of names per rebalance."); }, "Lower turnover means lower trading costs and taxes.", "Novy-Marx and Velikov (2016) show that turnover-driven trading costs erase many published strategies.", "", ["Novy-Marx & Velikov (2016), A Taxonomy of Anomalies and Their Trading Costs, Review of Financial Studies"]);
    M.p_cost = pE("Estimated trading cost", "Estimated cost of the trades since the last run, in basis points (1 bp = 0.01%) of the portfolio.", () => P(`${num((pf().turnover || {}).cost_bps, 1)} bps, using 5 bps per trade for large, liquid stocks, 15 for mid and 30 for small.`), "A rough guide; actual costs depend on your broker and order size.", "The bucket costs are conservative assumptions in line with published trading-cost estimates for US equities.");
    M.p_voltarget = pE("Volatility-target exposure", "How much of the stock sleeve is invested, set so the sleeve targets 10% annual volatility, then scaled by the regime and the trend switch.", () => { const m = pf().meta || {}; return P(`${num(m.vol_target_exposure)}× (equity weight ${pct(m.equity_weight, 0)}, cash ${pct(m.cash, 0)}, trend switch ${num(m.trend_switch, 1)}×).`) + P("When the stocks are volatile, exposure is cut; when calm, it can rise up to 1.5×."); }, "Volatility targeting smooths the ride; it does not reliably add return.", "Moreira and Muir (2017) found that scaling exposure down when recent volatility is high improved risk-adjusted returns for many factors.", "Cederburg et al. (2020) found the benefit is much weaker out of sample and across strategies.", ["Moreira & Muir (2017), Volatility-Managed Portfolios, Journal of Finance", "Cederburg, O'Doherty, Wang & Yan (2020), On the Performance of Volatility-Managed Portfolios, Journal of Financial Economics"]);
    M.p_scenarios = pE("Scenarios", "Estimated 12-month return of today's holdings in five economic scenarios, weighted by their probabilities.", () => P("Probabilities start from rough historical frequencies and are nudged by the dials (for example, higher recession odds from the yield curve). Each holding's return is built from its market beta, sector, factor exposures and interest-rate sensitivity."), "These are calibrated assumptions, not forecasts. Use them to see which scenario hurts the portfolio most.", "Scenario analysis is standard risk-management practice; the shocks are set in config.yaml from post-1950 episodes.");
    M.p_stress = pE("Historical stress tests", "How today's holdings did in past crises (dot-com, 2008, COVID, 2022).", () => P("The replay uses only today's names that traded in each window, so it flatters results: companies that failed are missing. The beta estimate (portfolio beta × SPY's move) is a cross-check."), "Look at the beta estimate as much as the replay.", "Survivorship bias in performance studies is well documented (Brown, Goetzmann, Ibbotson & Ross 1992).", "", ["Brown, Goetzmann, Ibbotson & Ross (1992), Survivorship Bias in Performance Studies, Review of Financial Studies"]);
    M.p_mc = pE("Monte Carlo, 1 year", "10,000 simulated one-year paths using the portfolio's volatility and fat-tailed daily returns.", () => { const m = pf().monte_carlo || {}; return P(`Median ${spct(m.median)}, 5th percentile ${spct(m.p5)}, chance of a 20%+ loss ${pct(m.prob_loss_20)}, expected worst drawdown during the year ${spct(m.expected_max_drawdown)}.`); }, "It shows the range of outcomes consistent with today's volatility, not a prediction.", "Daily returns are drawn from a Student-t distribution because real returns have fatter tails than a normal distribution (Mandelbrot 1963).", "", ["Mandelbrot (1963), The Variation of Certain Speculative Prices, Journal of Business"]);

    // =================================================================== backtest statistics
    const mE = (title, short, now, use, origin, refs) => ({ title, short, now: () => P(now), use, origin: P(origin), held: "", ours: () => "", refs: refs || [] });
    M.m_cagr = mE("CAGR", "Compound annual growth rate: the steady yearly return that would turn the starting value into the ending value.", "Higher is better, but compare it with risk (volatility and drawdown).", "Read alongside the market's CAGR over the same period.", "Standard performance measure.");
    M.m_vol = mE("Volatility", "Annualized standard deviation of monthly returns: how bumpy the ride was.", "The US market's long-run volatility is about 15% a year. Half that means roughly half the swings.", "Lower volatility with similar return is the point of the absolute-return mode.", "Standard risk measure (Markowitz 1952).", ["Markowitz (1952), Portfolio Selection, Journal of Finance"]);
    M.m_sharpe = mE("Sharpe ratio", "Return above T-bills divided by volatility: return per unit of risk.", "The US market's long-run Sharpe is about 0.4–0.5. Above 0.6 over decades is strong; above 1 usually means a short or lucky sample.", "Compare strategies on the same period; small differences (under 0.1) are usually noise.", "Sharpe (1966) proposed it to compare mutual funds.", ["Sharpe (1966), Mutual Fund Performance, Journal of Business"]);
    M.m_maxdd = mE("Maximum drawdown", "The largest peak-to-trough fall over the period.", "The US market's worst drawdowns were about −50% (2008) and −84% (1929–32). It is the loss you would have had to sit through.", "Often more relevant to whether investors stick with a strategy than volatility.", "Standard risk measure.");
    M.m_hit = mE("Hit rate", "Share of months with a positive return (or, for a difference row, months the strategy beat the market).", "A 60% hit rate means 6 out of 10 months were up.", "A high hit rate does not mean a high return; a few big losses can outweigh many small gains.", "Standard descriptive statistic.");
    M.m_alpha = mE("CAPM alpha", "Return not explained by exposure to the market (beta), per year.", "Positive alpha with a t-stat above 2 suggests skill or a real factor premium; below 2 can be luck.", "Low-beta strategies often show positive alpha simply because low-risk stocks have beaten CAPM predictions.", "Jensen (1968) introduced alpha to evaluate mutual funds.", ["Jensen (1968), The Performance of Mutual Funds in the Period 1945–1964, Journal of Finance"]);
    M.m_tstat = mE("t-statistic", "How many standard errors a result is from zero. Here corrected for overlapping and autocorrelated data (Newey-West).", "Above 2 is the traditional bar; with hundreds of signals tested across the profession, Harvey, Liu and Zhu (2016) argue for 3.", "Treat results with t below 2 as unproven.", "Newey and West (1987) developed the standard correction.", ["Newey & West (1987), Econometrica", "Harvey, Liu & Zhu (2016), …and the Cross-Section of Expected Returns, Review of Financial Studies"]);
    M.m_beta = Object.assign({}, M.p_beta, { title: "Beta (backtest)", now: () => P("How much the strategy moved with the market over the backtest. A beta of 0.5 means it moved about half as much.") });
    M.m_roll10 = mE("Rolling 10-year windows beating the market", "Share of all 10-year periods in which the strategy's return beat the market's.", "Shows how consistent the edge was. 50% = no better than a coin flip.", "A long-term investor experiences one 10-year window, not the average.", "Standard robustness check.");
    M.m_mp = mE("Post-publication ÷ pre-publication", "The signal's average return after its research was published, divided by its return before.", "1.0 = unchanged; 0.5 = halved; below 0 = reversed. McLean and Pontiff (2016) found returns of published anomalies fell about 58% on average after publication, partly because investors traded on them.", "Signals that kept most of their return after publication are more credible.", "McLean and Pontiff (2016) studied 97 published anomalies.", ["McLean & Pontiff (2016), Does Academic Research Destroy Stock Return Predictability?, Journal of Finance"]);
    M.m_health = mE("36-month health", "Compares the signal's Sharpe ratio over the last three years with its full-history Sharpe.", "Healthy = holding up; fading = below half its long-run level; decayed = negative recently.", "Three years is short; one bad stretch is not proof a signal is dead.", "Rolling-window monitoring is standard practice for factor strategies.");
    M.m_ic = mE("Rank IC", "The correlation between a signal's rank this month and stocks' rank of return next month, averaged over time.", "Typical useful values are small: 0.02–0.05. Values near zero mean the signal has not ranked stocks usefully.", "Small ICs can still add value across many stocks and months (the fundamental law of active management).", "Grinold (1989) and Grinold and Kahn's Active Portfolio Management made IC the standard measure of signal skill.", ["Grinold (1989), The Fundamental Law of Active Management, Journal of Portfolio Management"]);
    M.m_survivorship = mE("Survivorship check", "Estimates how much the stock-level backtest is flattered by using only today's surviving companies.", "It compares today's universe, equal-weighted, with RSP (the equal-weighted S&P 500 ETF, which held the companies that later failed). The gap is the approximate bias.", "Judge the stock-level screener by its spread over its own universe, not by its return vs SPY.", "Brown, Goetzmann, Ibbotson and Ross (1992) and Shumway (1997) document how survivorship and missing delisting returns bias backtests upward.", ["Brown, Goetzmann, Ibbotson & Ross (1992), Review of Financial Studies", "Shumway (1997), The Delisting Bias in CRSP Data, Journal of Finance"]);
    M.m_v2 = mE("Version 2 vs version 1", "Each proposed change, tested on its own: added to version 1, and switched on or off in version 2.", "A change was adopted only if it improved the 1963–2010 Sharpe ratio by at least 0.01 in one test without hurting it in the other. 2011 onward is shown separately as a check.", "Requiring the early period to improve guards against tuning to the last decade.", "Out-of-sample testing guards against data mining (Harvey, Liu & Zhu 2016).", ["Harvey, Liu & Zhu (2016), Review of Financial Studies"]);
    M.m_track = mE("Live track record", "Each night's top picks are saved and their returns measured against SPY going forward.", "This is the only test that cannot be overfitted. It needs years of data before it means much.", "Check the mature (21+ trading days) snapshots.", "Live, out-of-sample tracking is the gold standard for evaluating any strategy.");
    M.m_auc = mE("AUC (area under the curve)", "How well a dial separates periods before recessions from other periods.", "0.5 = no better than a coin flip; 1.0 = perfect. Above 0.8 is strong for economic forecasting.", "A high AUC for recessions does not mean the dial times stock-market peaks.", "Standard measure for evaluating early-warning indicators.");
    M.m_oosr2 = mE("Out-of-sample R²", "How much better a forecast did than simply using the historical average, using only data available at the time.", "Positive = beat the historical average; negative = worse. Goyal and Welch (2008) found most predictors are negative.", "Even positive values are usually small.", "Campbell and Thompson (2008) and Goyal and Welch (2008) set this as the standard test for return predictors.", ["Goyal & Welch (2008), Review of Financial Studies", "Campbell & Thompson (2008), Predicting Excess Stock Returns Out of Sample, Review of Financial Studies"]);

    // =================================================================== crash study
    M.c_warning = mE("Warning lead times", "For each bear market since 1960, how many months before the peak each warning first fired (and was still on near the peak).", "Blank = no warning. The last column compares how often each warning was followed by a bear market with the rate for any month.", "A warning is only useful if it fires ahead of peaks more often than a random month would.", "This is this tool's own replay; see the Crashes page for every episode.");
    M.c_edge = mE("Buy-rule edge", "Average excess market return in the 12 or 24 months after a buy signal, minus the average over all months in the same period.", "Positive = buying on the signal beat buying at a random time. Signals are counted at most once per 12 months.", "Few signals means wide error bands; prefer rules that worked in both periods.", "Rules were chosen on 1960–1999 and judged on 2000 onward.");
    M.c_composite = mE("Composite buy score", "An unfitted average of drawdown depth, credit-spread level, fear and cheapness.", "Q5 = strongest buy reading. A rising pattern from Q1 to Q5 means higher scores were followed by higher returns.", "It was not fitted to returns, so it cannot be overfitted, but it is also not optimised.", "This tool's own construction.");
    M.c_reserve = mE("Cash reserve for dips", "Holding 20% in T-bills and deploying it when a buy rule fires, compared with staying fully invested.", "Every variant beat holding static cash but trailed staying fully invested.", "Waiting for dips has cost more than it saved.", "This tool's own simulation, 1960 onward.");

    // =================================================================== lead indicators
    const LD = () => D().leads || {};
    const LB = () => (D().leads_backtest || {}).leads || {};
    const leadRow = (k, t) => { const x = (LD().leads || {})[k] || {}; return (x.bull || []).find((r) => r.t === t) ? { side: "bull", r: (x.bull || []).find((r) => r.t === t) } : (x.bear || []).find((r) => r.t === t) ? { side: "bear", r: (x.bear || []).find((r) => r.t === t) } : null; };
    const leadsWith = (t) => ((LD().convergence || []).find((c) => c.t === t) || {}).leads || [];
    const evid = (k) => {
      const b = LB()[k];
      if (!b || !b.flagged_minus_universe) return P("The weekly backtest has not produced results for this indicator yet.");
      const f = b.flagged_minus_universe, n = b.net_of_costs || {}, per = (x) => x && H.isNum(x.ann_mean) ? `${spct(x.ann_mean)} a year (t = ${num(x.t, 1)}, ${x.n} months)` : "no data";
      return P(`<b>Status: ${esc(b.status)}.</b> ${esc(b.reason || "")}.`) +
        P(`Flagged names vs the liquid universe, next month: before 2011 ${per(f.pre)}; 2011 on ${per(f.post)}; full period net of costs ${per(n.full)}. On average ${num(b.avg_flagged, 0)} names were flagged and ${pct(b.avg_new_share, 0)} of them were new each month.`) +
        (b.top30_minus_bottom30 ? P(`Top 30% minus bottom 30% of all liquid stocks: ${per(b.top30_minus_bottom30.full)}. Average rank IC ${num(b.ic_mean, 3)}.`) : "") +
        (b.bearish_minus_universe ? P(`Bearish side (bottom 10%) vs universe: ${per(b.bearish_minus_universe.full)}.`) : "") +
        P("Tested on today's S&P 1500, so survivorship bias remains, though comparing flagged names with the same universe cancels much of it.");
    };
    const leadNow = (k, ctx, generic, perStock) => {
      const x = (LD().leads || {})[k];
      if (ctx) {
        const hit = leadRow(k, ctx);
        if (hit) {
          const others = leadsWith(ctx).filter((y) => y !== k);
          return P(perStock(hit.r, hit.side)) + (others.length ? P(`${esc(ctx)} is also flagged by: ${others.map((y) => esc(((LD().leads || {})[y] || {}).label || y)).join(", ")}.`) : "") +
            P(`This indicator's status: <b>${esc((LB()[k] || {}).status || (x || {}).status || "not yet tested")}</b>. A flag is a reason to research the company, not a buy signal.`);
        }
      }
      return P(generic) + (x ? P(`Today: ${x.n_bull} stocks flagged${x.n_bear ? ` and ${x.n_bear} on the bearish side` : ""}; data for ${pct(x.coverage, 0)} of liquid stocks. Status: <b>${esc((LB()[k] || {}).status || x.status)}</b>.`) : "");
    };
    M.mispricing = {
      title: "Fundamental mispricing",
      short: "How far a company's market value sits from the value implied by its balance sheet and earnings, compared with similar companies in its sector.",
      now: (ctx) => leadNow("mispricing", ctx,
        "Each day the tool fits one model across all stocks: market value (scaled by total assets) explained by equity, liabilities, revenue, gross profit, operating income, profits, losses and the sector. A stock's fair value is what that model predicts. The top 10% by fair value over price are flagged as cheap, and the bottom 10% as expensive.",
        (r, side) => `<b>${esc(r.t)}: fundamentals imply a value ${spct(r.v, 0)} ${r.v >= 0 ? "above" : "versus"} its market value</b>, which puts it in the ${pct(r.p, 0)} percentile (${side === "bull" ? "cheap end" : "expensive end"}). Market cap ${H.isNum(r.mc) ? "$" + (r.mc / 1e9).toFixed(1) + "B" : "–"}. Large gaps are often explained by things the model cannot see (a pending lawsuit, a one-off gain, a business in decline), so check the filings.`),
      use: "Use the cheap list as a starting point for valuation work. The model is a comps regression without judgment; large gaps can be data errors or real problems.",
      origin: P("Bartram and Grinblatt (2018) estimated each stock's fair value every month by regressing market capitalization on a few dozen accounting items across all US stocks, with no theory about which items matter (hence 'agnostic'). Stocks trading below fair value earned significantly higher returns, which standard risk factors did not explain. A 2021 follow-up by the same authors found similar results in international markets. This tool uses a smaller set of items, scales them by total assets so mega caps do not dominate the fit, and adds sector effects."),
      held: P("The method is recent, so independent out-of-sample evidence is limited. Like other value-type signals it can take a long time to pay off, and errors in reported financial data create false positives."),
      ours: () => evid("mispricing"),
      refs: ["Bartram & Grinblatt (2018), Agnostic Fundamental Analysis Works, Journal of Financial Economics", "Bartram & Grinblatt (2021), Global Market Inefficiencies, Journal of Financial Economics"],
    };
    M.resid_mom = {
      title: "Residual momentum",
      short: "A stock's return over the past year (skipping the latest month) after removing what the market, size and value factors explain.",
      now: (ctx) => leadNow("resid_mom", ctx,
        "For each stock, 36 months of returns are regressed on the market, size and value factors. Residual momentum adds up the leftover, stock-specific returns from 12 months ago to last month, divided by their volatility. High values mean the company has been rising for its own reasons, not because it rode the market or a style.",
        (r) => `<b>${esc(r.t)}: residual momentum ${num(r.v, 2)}</b> (${pct(r.p, 0)} percentile). Its stock-specific returns over the past year have been consistently positive relative to what its factor exposures would predict.`),
      use: "A cleaner version of momentum that is less prone to sudden crashes. Useful for spotting companies with steady, company-specific improvement.",
      origin: P("Grundy and Martin (2001) showed that much of momentum's risk comes from its changing factor exposures. Blitz, Huij and Martens (2011) ranked stocks on residual returns instead of total returns and found roughly twice the risk-adjusted profit of ordinary momentum, with smaller losses in momentum crashes such as 2009."),
      held: P("Residual momentum has held up better than raw momentum in later studies, though, like all momentum strategies, it trades often, so costs matter."),
      ours: () => {
        const ind = (D().leads_backtest || {}).industry_residual_momentum || {};
        const per = (x) => x && H.isNum(x.ann_mean) ? `${spct(x.ann_mean)} a year (t = ${num(x.t, 1)})` : "no data";
        return evid("resid_mom") + (ind.residual ? P(`Survivorship-free check on 49 industries since 1963 (top 10 vs all): residual momentum ${per(ind.residual.pre)} before 2011 and ${per(ind.residual.post)} after; raw momentum ${per(ind.raw && ind.raw.pre)} and ${per(ind.raw && ind.raw.post)}. Worst month ${spct(ind.residual_worst_month)} vs ${spct(ind.raw_worst_month)} for raw momentum.`) : "");
      },
      refs: ["Blitz, Huij & Martens (2011), Residual Momentum, Journal of Empirical Finance", "Grundy & Martin (2001), Understanding the Nature of the Risks and the Source of the Rewards to Momentum Investing, Review of Financial Studies"],
    };
    M.insider_cluster = {
      title: "Insider cluster buying",
      short: "Two or more different insiders (officers or directors) buying the company's stock on the open market within 60 days.",
      now: (ctx) => leadNow("insider_cluster", ctx,
        "Counts distinct insiders whose open-market purchases exceeded their sales over the last 60 days, using SEC Form 4 filings available by today. Two or more is flagged. Insiders sell for many reasons (taxes, diversification, planned sales), but they generally buy for one: they think the stock is cheap.",
        (r) => `<b>${esc(r.t)}: ${H.isNum(r.v) ? r.v.toFixed(0) : "–"} insiders bought in the last 60 days</b>, about ${H.isNum(r.value) ? "$" + (r.value >= 1e6 ? (r.value / 1e6).toFixed(1) + "M" : (r.value / 1e3).toFixed(0) + "K") : "–"} in total. Read the Form 4s: purchases by the CEO or CFO, with their own money, at size relative to their pay, carry the most information.`),
      use: "One of the most direct lead indicators available. Check who bought, how much, and whether it was a planned or discretionary purchase.",
      origin: P("Insider purchases have predicted returns in many studies (Seyhun 1986; Lakonishok & Lee 2001), while sales carry much less information. Cohen, Malloy and Pomorski (2012) showed the signal comes from opportunistic trades rather than routine ones. Requiring several insiders to buy at once is a common refinement; this tool tests it directly rather than assuming it adds value."),
      held: P("Insider-purchase signals have survived for decades, though their returns concentrate in smaller companies, and S&P 1500 stocks have relatively few open-market purchases."),
      ours: () => evid("insider_cluster"),
      refs: ["Cohen, Malloy & Pomorski (2012), Decoding Inside Information, Journal of Finance", "Lakonishok & Lee (2001), Are Insider Trades Informative?, Review of Financial Studies", "Seyhun (1986), Insiders' Profits, Costs of Trading, and Market Efficiency, Journal of Financial Economics"],
    };
    M.earnings_reaction = {
      title: "Earnings reaction",
      short: "How much the stock beat or lagged the market over the three trading days around its latest earnings release.",
      now: (ctx) => leadNow("earnings_reaction", ctx,
        "Uses the date of each company's earnings press release (8-K item 2.02 filings) and the stock's return minus SPY's from the day before to the day after. Only releases from the last 30 days count. The strongest 10% of reactions are flagged; the weakest 10% form the bearish list.",
        (r, side) => `<b>${esc(r.t)}: ${spct(r.v)} versus the market around its ${esc(r.release || "")} earnings release</b> (${pct(r.p, 0)} percentile${side === "bear" ? ", bearish side" : ""}). The market's verdict on results tends to be more informative than the reported numbers alone, because it reflects guidance and quality.`),
      use: "A strong reaction flags companies whose results changed investors' minds. Read the release and the call transcript to see why.",
      origin: P("Chan, Jegadeesh and Lakonishok (1996) found that stocks with large positive returns around earnings announcements kept outperforming over the following months, separately from price momentum. Later work (for example Brandt, Kishore, Santa-Clara and Venkatachalam's 'Earnings announcements are full of surprises') found the announcement return predicts more drift than the accounting surprise."),
      held: P("Post-earnings drift has weakened in recent large-cap US data as markets react faster, so this indicator needs its own evidence, shown below."),
      ours: () => evid("earnings_reaction"),
      refs: ["Chan, Jegadeesh & Lakonishok (1996), Momentum Strategies, Journal of Finance", "Brandt, Kishore, Santa-Clara & Venkatachalam (2008), Earnings Announcements Are Full of Surprises, working paper"],
    };
    M.abnormal_volume = {
      title: "Abnormal volume",
      short: "How much more the stock has traded over the last five days than over the 50 days before.",
      now: (ctx) => leadNow("abnormal_volume", ctx,
        "Volume over the last five trading days divided by average volume over the prior 50. The top 10% are flagged. A volume spike means something drew attention: news, an index change, a large holder trading.",
        (r) => `<b>${esc(r.t)}: trading at ${H.isNum(r.v) ? Math.exp(r.v).toFixed(1) + "×" : "–"} its normal volume</b> over the last five days, with a ${spct(r.ret_5d)} return over the same days. Find out what happened before reading anything into it.`),
      use: "A prompt to look for news. On its own it is the weakest kind of lead.",
      origin: P("Gervais, Kaniel and Mingelgrin (2001) found that stocks with unusually high trading volume over a day or a week tended to rise over the following month, which they attributed to increased visibility attracting new investors. Kaniel, Ozoguz and Starks (2012) found the same pattern across many countries."),
      held: P("The effect is strongest in smaller, less-followed stocks; in large, heavily traded S&P 500 names it may be weak."),
      ours: () => evid("abnormal_volume"),
      refs: ["Gervais, Kaniel & Mingelgrin (2001), The High-Volume Return Premium, Journal of Finance", "Kaniel, Ozoguz & Starks (2012), The High Volume Return Premium: Cross-Country Evidence, Journal of Financial Economics"],
    };
    M.leads_overview = mE("Lead indicators", "Five signals that flag individual stocks worth researching, each with its own backtest.",
      "The page lists today's flagged names for each indicator, the stocks flagged by two or more, and the evidence behind each indicator. Nothing here changes the strategy scores; an indicator would only be considered for the scores after it reaches Validated.",
      "Start with names flagged by two or more indicators, then read the filings: insider Form 4s, the earnings release, the balance sheet. The flags find candidates; the research decides.",
      "The indicators come from published research (see each one). The status labels come from this tool's own monthly backtests, split at 2011 where the data allow.");
    M.lead_status = mE("Status labels", "How much evidence each indicator has in this tool's own backtest.",
      "<b>Validated</b>: flagged stocks beat the liquid universe with t ≥ 3 over the full period, and did so both before and after 2011. <b>Promising</b>: t ≥ 2 and positive in both periods (or only post-2011 data exists). <b>Unproven</b>: everything else, including indicators that worked in reverse.",
      "A t of 3 is a deliberately high bar: with many indicators tested, a t of 2 is easy to reach by luck (Harvey, Liu & Zhu 2016). Free data starts in 2004 (prices), 2008 (insider filings), 2009 (earnings releases) and about 2010 (fundamentals), so some indicators have little or no pre-2011 history.",
      "Out-of-sample testing and higher t-stat thresholds guard against data mining.", ["Harvey, Liu & Zhu (2016), …and the Cross-Section of Expected Returns, Review of Financial Studies"]);
    M.convergence = mE("Flagged by two or more", "Stocks that appear on at least two bullish lead lists today.",
      "Independent signals pointing the same way are more informative than any single one, as long as they measure different things (for example insider buying plus a cheap fundamental value).",
      "The best place to start research.", "Combining weakly correlated signals improves reliability; this is the logic of multi-factor models.");

    return M;
  };
})();
