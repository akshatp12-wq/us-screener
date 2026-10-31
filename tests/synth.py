"""Synthetic versions of every data source, in each source's real file format.

Used by the offline end-to-end test: Fetcher.get is routed here instead of the network, so the whole
pipeline (parsers included) runs without internet. Numbers are random; only formats are realistic.
"""
from __future__ import annotations

import io
import json
import re
import zipfile

import numpy as np
import pandas as pd

RNG = np.random.default_rng(42)
SECTORS = ["Information Technology", "Financials", "Energy", "Health Care", "Industrials",
           "Consumer Discretionary", "Consumer Staples", "Utilities", "Materials", "Real Estate",
           "Communication Services"]
N_STOCKS = 240
TODAY = pd.Timestamp("2026-10-30")
BDAYS = pd.bdate_range("2004-01-02", TODAY)


def tickers():
    return [f"S{i:03d}" for i in range(N_STOCKS)]


def universe_frame():
    t = tickers()
    return pd.DataFrame({"ticker": t, "name": [f"Company {x}" for x in t],
                         "sector": [SECTORS[i % len(SECTORS)] for i in range(N_STOCKS)],
                         "sub_industry": [f"Sub {i % 23}" for i in range(N_STOCKS)],
                         "cik": [100000 + i for i in range(N_STOCKS)]})


# ----------------------------------------------------------------------------- prices
def price_panels(etfs: list[str]) -> dict:
    n = len(BDAYS)
    mkt = RNG.normal(0.0003, 0.011, n)
    cols, rets = [], []
    u = universe_frame()
    sec_f = {s: RNG.normal(0, 0.006, n) for s in SECTORS}
    for i, t in enumerate(u["ticker"]):
        beta = 0.6 + RNG.random() * 0.9
        r = beta * mkt + sec_f[u.loc[i, "sector"]] + RNG.normal(0.0001, 0.015 + 0.01 * RNG.random(), n)
        start = 0 if i % 9 else int(n * (0.3 + 0.6 * RNG.random()))  # some young listings
        r[:start] = np.nan
        cols.append(t)
        rets.append(r)
    for e in etfs:
        if e == "^VIX":
            continue
        r = (0.9 + 0.3 * RNG.random()) * mkt + RNG.normal(0.0001, 0.006, n)
        if e in ("IEF", "TLT", "SHY", "AGG", "TIP"):
            r = RNG.normal(0.0001, 0.004, n)
        if e in ("SMH", "SOXX"):  # make two industries "bubbly"
            r = r + 0.0035
        if e == "IPO":
            r[: n // 2] = np.nan
        cols.append(e)
        rets.append(r)
    ret = pd.DataFrame(np.column_stack(rets), index=BDAYS, columns=cols)
    close = 50 * (1 + ret.fillna(0)).cumprod().where(ret.notna())
    vix = pd.Series(np.clip(18 + np.cumsum(RNG.normal(0, 0.6, n)) * 0.1 + RNG.normal(0, 2, n), 9, 80), index=BDAYS)
    close["^VIX"] = vix
    ret["^VIX"] = vix.pct_change()
    vol = pd.DataFrame(RNG.integers(200_000, 5_000_000, size=close.shape), index=BDAYS, columns=close.columns)
    splits = pd.DataFrame(1.0, index=BDAYS, columns=close.columns)
    splits.loc[pd.Timestamp("2024-06-10"), "S001"] = 2.0
    return {"ret": ret, "close": close, "volume": vol.astype(float), "splits": splits}


# ----------------------------------------------------------------------------- FRED
def fred_csv(series: str) -> bytes:
    if series in ("CRDQUSAPABIS", "QUSNAM770A", "QUSHAM770A"):
        idx = pd.date_range("1952-01-01", "2026-04-01", freq="QS")
        base = {"CRDQUSAPABIS": 120, "QUSNAM770A": 60, "QUSHAM770A": 60}[series]
        v = base + np.cumsum(RNG.normal(0.3, 1.2, len(idx)))
    elif series == "CSUSHPINSA":
        idx = pd.date_range("1987-01-01", "2026-08-01", freq="MS")
        v = 60 * np.exp(np.cumsum(RNG.normal(0.003, 0.006, len(idx))))
    elif series in ("GS10", "TB3MS", "USREC"):
        idx = pd.date_range("1953-04-01", "2026-09-01", freq="MS")
        if series == "USREC":
            v = (RNG.random(len(idx)) < 0.12).astype(int)
            v = pd.Series(v).rolling(6, min_periods=1).max().to_numpy()
        else:
            v = np.clip(5 + np.cumsum(RNG.normal(0, 0.2, len(idx))) * 0.3, 0.05, 15) + (1.2 if series == "GS10" else 0)
    else:
        idx = pd.bdate_range("1990-01-02" if series == "VIXCLS" else "1982-01-04", "2026-10-29")
        if series == "VIXCLS":
            v = np.clip(18 + RNG.normal(0, 4, len(idx)), 9, 80)
        elif series == "T10Y3M":
            v = 1.0 + np.cumsum(RNG.normal(0, 0.03, len(idx))) * 0.2
        elif series == "BAMLH0A0HYM2":
            idx = idx[idx >= "2023-10-01"]
            v = 3.5 + RNG.normal(0, 0.3, len(idx))
        else:
            v = np.clip(2 + RNG.normal(0, 0.1, len(idx)), 0, 10)
    df = pd.DataFrame({"observation_date": idx.strftime("%Y-%m-%d"), series: np.round(v, 4).astype(object)})
    df.loc[df.sample(frac=0.01, random_state=1).index, series] = "."
    return df.to_csv(index=False).encode()


# ----------------------------------------------------------------------------- Ken French
def _table(title, idx, cols, scale=4.5, mean=0.7, fmt="%Y%m"):
    lines = [title, "," + ",".join(f"{c:>8}" for c in cols)]
    for d in idx:
        vals = RNG.normal(mean, scale, len(cols))
        lines.append(d.strftime(fmt) + "," + ",".join(f"{v:8.2f}" for v in vals))
    return "\n".join(lines) + "\n\n"


def french_zip(name: str) -> bytes:
    mi = pd.date_range("1926-07-31", "2026-09-30", freq="ME")
    mi63 = mi[mi >= "1963-07-31"]
    if name == "F-F_Research_Data_5_Factors_2x3":
        txt = "This file was created using the 202609 CRSP database.\n\n" + \
              _table("", mi63, ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"], 3.0, 0.3)
    elif name == "F-F_Research_Data_Factors":
        txt = "header text\n\n" + _table("", mi, ["Mkt-RF", "SMB", "HML", "RF"], 4.0, 0.5)
    elif name == "F-F_Momentum_Factor":
        txt = "Missing data are indicated by -99.99.\n\n" + _table("", mi, ["Mom"], 4.0, 0.6)
    elif name == "F-F_Research_Data_Factors_daily":
        di = pd.bdate_range("1926-07-01", "2026-09-30")
        txt = "daily file\n\n" + _table("", di, ["Mkt-RF", "SMB", "HML", "RF"], 1.0, 0.03, "%Y%m%d")
    elif name == "49_Industry_Portfolios":
        cols = [f"Ind{i}" for i in range(49)]
        txt = "  Average Value Weighted Returns -- Monthly\n" + _table("", mi, cols, 6.0, 1.0)[1:] + \
              "  Average Equal Weighted Returns -- Monthly\n" + _table("", mi, cols, 6.0, 1.0)[1:]
    elif name == "10_Portfolios_Prior_12_2":
        cols = ["Lo PRIOR", "PRIOR 2", "PRIOR 3", "PRIOR 4", "PRIOR 5", "PRIOR 6", "PRIOR 7", "PRIOR 8", "PRIOR 9", "Hi PRIOR"]
        txt = "  Average Value Weighted Returns -- Monthly\n" + _table("", mi, cols)[1:]
    else:
        cols = ["Lo 20", "Qnt 2", "Qnt 3", "Qnt 4", "Hi 20", "Lo 10", "Dec 2", "Dec 3", "Dec 4", "Dec 5",
                "Dec 6", "Dec 7", "Dec 8", "Dec 9", "Hi 10"]
        if name in ("Portfolios_Formed_on_BE-ME", "Portfolios_Formed_on_ME", "Portfolios_Formed_on_OP", "Portfolios_Formed_on_INV"):
            cols = ["<= 0", "Lo 30", "Med 40", "Hi 30"] + cols
        txt = "  Value Weight Returns -- Monthly\n" + _table("", mi63, cols)[1:] + \
              "  Equal Weight Returns -- Monthly\n" + _table("", mi63, cols)[1:] + \
              "  Value Weight Returns -- Annual from January to December\n" + \
              _table("", pd.date_range("1964", "2025", freq="YE"), cols, fmt="%Y")[1:]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{name}.csv", txt)
    return buf.getvalue()


# ----------------------------------------------------------------------------- Shiller, EBP, BW
def shiller_xlsx() -> bytes:
    idx = pd.date_range("1871-01-01", "2026-09-01", freq="MS")
    n = len(idx)
    p = 5 * np.exp(np.cumsum(RNG.normal(0.004, 0.04, n)))
    e = p / (15 + 5 * np.sin(np.arange(n) / 100))
    d = e * 0.5
    cpi = 10 * np.exp(np.cumsum(RNG.normal(0.002, 0.003, n)))
    real_p = p * cpi[-1] / cpi
    real_tr = real_p * np.exp(np.cumsum(np.full(n, 0.003)))
    cape = pd.Series(real_p).rolling(120).mean().to_numpy() / pd.Series(e * cpi[-1] / cpi).rolling(120).mean().to_numpy() * 1.0
    dates = [f"{d.year}.{d.month:02d}" for d in idx]
    dates = [float(x) for x in dates]
    rows = [["Stock market data"], [], [], [], [],
            ["Date", "P", "D", "E", "CPI", "Date Fraction", "Rate GS10", "Real Price", "Real Dividend",
             "Real Total Return Price", "Real Earnings", "Real TR Scaled Earnings", "CAPE"]]
    for i in range(n):
        rows.append([dates[i], p[i], d[i], e[i], cpi[i], 0, 4.0, real_p[i], d[i], real_tr[i], e[i], e[i],
                     None if np.isnan(cape[i]) else cape[i] * 20])
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Data", header=False, index=False)
    return buf.getvalue()


def ebp_csv() -> bytes:
    idx = pd.date_range("1973-01-01", "2026-09-01", freq="MS")
    df = pd.DataFrame({"date": idx.strftime("%Y-%m-%d"), "gz_spread": RNG.normal(2, 0.5, len(idx)),
                       "ebp": RNG.normal(0, 0.4, len(idx)), "est_prob": RNG.random(len(idx)) * 0.4})
    return df.to_csv(index=False).encode()


def bw_xlsx() -> bytes:
    idx = pd.date_range("1965-07-01", "2018-12-01", freq="MS")
    df = pd.DataFrame({"yearmo": idx.strftime("%Y%m").astype(int), "SENT^": RNG.normal(0, 1, len(idx)),
                       "SENT": RNG.normal(0, 1, len(idx))})
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


# ----------------------------------------------------------------------------- universe, SEC
def wiki_html(index: str) -> bytes:
    u = universe_frame()
    part = {"sp500": u.iloc[:120], "sp400": u.iloc[120:180], "sp600": u.iloc[180:]}[index]
    df = pd.DataFrame({"Symbol": part["ticker"], "Security": part["name"], "GICS Sector": part["sector"],
                       "GICS Sub-Industry": part["sub_industry"], "Date added": "2010-01-01"})
    return ("<html><body>" + df.to_html(index=False) + "</body></html>").encode()


def sec_tickers() -> bytes:
    u = universe_frame()
    d = {str(i): {"cik_str": int(r.cik), "ticker": r.ticker, "title": r.name} for i, r in u.iterrows()}
    return json.dumps(d).encode()


def _company_facts(cik: int) -> dict:
    facts = {"us-gaap": {}, "dei": {}}

    def add(tax, name, unit, rows):
        facts[tax].setdefault(name, {"units": {}})["units"][unit] = rows

    q_ends = pd.date_range("2008-03-31", "2026-06-30", freq="QE")
    rev, cogs, ni, eps, assets, eq, shares = [], [], [], [], [], [], []
    a = 1e9 * (0.5 + RNG.random())
    sh = 1e8 * (0.5 + RNG.random())
    margin = 0.2 + 0.5 * RNG.random()
    for qe in q_ends:
        a *= 1 + RNG.normal(0.015, 0.03)
        sh *= 1 + RNG.normal(0.002, 0.01)
        r = a * 0.25 * (1 + RNG.normal(0, 0.05))
        filed = (qe + pd.Timedelta(days=40)).strftime("%Y-%m-%d")
        start = (qe - pd.offsets.QuarterBegin(startingMonth=1) + pd.Timedelta(days=0))
        start = (qe - pd.DateOffset(months=3) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        form = "10-K" if qe.month == 12 else "10-Q"
        if qe.month == 12:
            fstart = (qe - pd.DateOffset(years=1) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            filed = (qe + pd.Timedelta(days=60)).strftime("%Y-%m-%d")
            ann_r = r * 4
            rev.append({"start": fstart, "end": qe.strftime("%Y-%m-%d"), "val": ann_r, "form": form, "filed": filed})
            cogs.append({"start": fstart, "end": qe.strftime("%Y-%m-%d"), "val": ann_r * (1 - margin), "form": form, "filed": filed})
            ni.append({"start": fstart, "end": qe.strftime("%Y-%m-%d"), "val": ann_r * 0.1, "form": form, "filed": filed})
            eps.append({"start": fstart, "end": qe.strftime("%Y-%m-%d"), "val": ann_r * 0.1 / sh, "form": form, "filed": filed})
            nstart = (qe - pd.DateOffset(years=1) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            # 9-month YTD from prior 10-Q
            nend = (qe - pd.DateOffset(months=3)).strftime("%Y-%m-%d")
            for lst, mult in ((rev, 1), (cogs, 1 - margin), (ni, 0.1)):
                lst.append({"start": nstart, "end": nend, "val": r * 3 * mult, "form": "10-Q",
                            "filed": (qe - pd.DateOffset(months=3) + pd.Timedelta(days=40)).strftime("%Y-%m-%d")})
            eps.append({"start": nstart, "end": nend, "val": r * 3 * 0.1 / sh, "form": "10-Q",
                        "filed": (qe - pd.DateOffset(months=3) + pd.Timedelta(days=40)).strftime("%Y-%m-%d")})
        else:
            q_eps = r * 0.1 * (1 + RNG.normal(0, 0.2)) / sh
            rev.append({"start": start, "end": qe.strftime("%Y-%m-%d"), "val": r, "form": form, "filed": filed})
            cogs.append({"start": start, "end": qe.strftime("%Y-%m-%d"), "val": r * (1 - margin), "form": form, "filed": filed})
            ni.append({"start": start, "end": qe.strftime("%Y-%m-%d"), "val": r * 0.1, "form": form, "filed": filed})
            eps.append({"start": start, "end": qe.strftime("%Y-%m-%d"), "val": q_eps, "form": form, "filed": filed})
        assets.append({"end": qe.strftime("%Y-%m-%d"), "val": a, "form": form, "filed": filed})
        eq.append({"end": qe.strftime("%Y-%m-%d"), "val": a * 0.4, "form": form, "filed": filed})
        shares.append({"end": (qe + pd.Timedelta(days=30)).strftime("%Y-%m-%d"), "val": sh, "form": form, "filed": filed})
    add("us-gaap", "Revenues", "USD", rev)
    add("us-gaap", "CostOfRevenue", "USD", cogs)
    add("us-gaap", "NetIncomeLoss", "USD", ni)
    add("us-gaap", "EarningsPerShareDiluted", "USD/shares", eps)
    add("us-gaap", "Assets", "USD", assets)
    add("us-gaap", "StockholdersEquity", "USD", eq)
    add("dei", "EntityCommonStockSharesOutstanding", "shares", shares)
    return {"cik": cik, "entityName": f"Co {cik}", "facts": facts}


def companyfacts_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for cik in universe_frame()["cik"]:
            z.writestr(f"CIK{int(cik):010d}.json", json.dumps(_company_facts(int(cik))))
    return buf.getvalue()


def form345_zip(yq: str) -> bytes:
    y, q = int(yq[:4]), int(yq[-1])
    months = [3 * (q - 1) + 1, 3 * (q - 1) + 2, 3 * q]
    sub, own, tr = [], [], []
    k = 0
    u = universe_frame()
    for i in range(0, N_STOCKS, 3):
        cik = int(u.loc[i, "cik"])
        for owner in (1, 2):
            m = months[0] if owner == 1 else months[(i // 3) % 3]
            acc = f"0000{cik}-{y}{q}{owner}{k}"
            k += 1
            d = pd.Timestamp(year=y, month=m, day=10)
            sub.append([acc, d.strftime("%d-%b-%Y"), str(cik), u.loc[i, "ticker"], "4"])
            own.append([acc, str(900000 + i * 10 + owner)])
            code = "P" if (i + owner + q) % 3 == 0 else "S"
            tr.append([acc, d.strftime("%d-%b-%Y"), code, "1000", "25.5", "A" if code == "P" else "D"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SUBMISSION.tsv", "ACCESSION_NUMBER\tFILING_DATE\tISSUERCIK\tISSUERTRADINGSYMBOL\tDOCUMENT_TYPE\n" +
                   "\n".join("\t".join(r) for r in sub))
        z.writestr("REPORTINGOWNER.tsv", "ACCESSION_NUMBER\tRPTOWNERCIK\n" + "\n".join("\t".join(r) for r in own))
        z.writestr("NONDERIV_TRANS.tsv", "ACCESSION_NUMBER\tTRANS_DATE\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE\t"
                   "TRANS_ACQUIRED_DISP_CD\n" + "\n".join("\t".join(r) for r in tr))
    return buf.getvalue()


def finra_csv() -> bytes:
    u = universe_frame()
    lines = ["accountingYearMonthNumber|symbolCode|issueName|currentShortPositionQuantity|previousShortPositionQuantity|"
             "averageDailyVolumeQuantity|daysToCoverQuantity|settlementDate"]
    for i, t in enumerate(u["ticker"]):
        lines.append(f"202610|{t}|Co|{int(RNG.integers(1e5, 3e7))}|0|{int(RNG.integers(1e5, 3e6))}|"
                     f"{RNG.random() * 12:.2f}|2026-10-15")
    for j in range(1200):
        lines.append(f"202610|OTC{j}|Other|1000|0|100|1.0|2026-10-15")
    return "\n".join(lines).encode()


def ssga_xlsx(ticker: str) -> bytes:
    u = universe_frame()
    sample = u.sample(40, random_state=abs(hash(ticker)) % 1000)
    rows = [["Fund Name:", ticker], ["Ticker Symbol:", ticker], ["Holdings:", "As of 29-Oct-2026"], [],
            ["Name", "Ticker", "Identifier", "SEDOL", "Weight", "Sector", "Shares Held", "Local Currency"]]
    w = RNG.random(len(sample))
    w = w / w.sum() * 100
    for (_, r), wi in zip(sample.iterrows(), w):
        rows.append([r["name"], r["ticker"], "x", "x", wi, r["sector"], 1000, "USD"])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as wr:
        pd.DataFrame(rows).to_excel(wr, header=False, index=False)
    return buf.getvalue()


# ----------------------------------------------------------------------------- router
_CACHE: dict = {}


def route(url: str) -> bytes:
    if url in _CACHE:
        return _CACHE[url]
    out = None
    if "fredgraph.csv" in url:
        out = fred_csv(url.split("id=")[1])
    elif "ken.french" in url:
        out = french_zip(url.rsplit("/", 1)[1].replace("_CSV.zip", ""))
    elif "shiller" in url:
        out = shiller_xlsx()
    elif "ebp_csv" in url:
        out = ebp_csv()
    elif "jwurgler" in url:
        out = bw_xlsx()
    elif "wikipedia" in url:
        idx = "sp500" if "S%26P_500" in url else ("sp400" if "S%26P_400" in url else "sp600")
        out = wiki_html(idx)
    elif "company_tickers.json" in url:
        out = sec_tickers()
    elif "companyfacts.zip" in url:
        out = companyfacts_zip()
    elif "api/xbrl/companyfacts" in url:
        cik = int(re.search(r"CIK(\d+)", url).group(1))
        out = json.dumps(_company_facts(cik)).encode()
    elif "daily-index" in url:
        out = b"Form Type   Company Name   CIK   Date Filed   File Name\n----------\n10-Q        Co 100001     100001    20261028    edgar/data/x.txt\n"
    elif "form345" in url:
        out = form345_zip(re.search(r"(\d{4}q\d)", url).group(1))
    elif "finra" in url:
        if "20261015" in url:
            out = finra_csv()
    elif "ssga" in url:
        out = ssga_xlsx(re.search(r"en-(\w+)\.xlsx", url).group(1).upper())
    if out is None:
        raise FileNotFoundError(url)
    _CACHE[url] = out
    return out
