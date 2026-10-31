"""SEC XBRL fundamentals (point-in-time) and Form 3/4/5 insider data sets.

Fundamentals come from companyfacts (each fact carries its `filed` date, so values are
only used once they were public). First run downloads the nightly bulk archive
(companyfacts.zip, one request); later runs read EDGAR daily form indexes and refetch
only companies that filed a 10-K/10-Q since the last update.
"""
from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .cache import Fetcher, FetchError, record

log = logging.getLogger("screener.data")

CONCEPTS = {
    "assets": [("us-gaap", "Assets")],
    "equity": [("us-gaap", "StockholdersEquity"),
               ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")],
    "revenue": [("us-gaap", "Revenues"), ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
                ("us-gaap", "SalesRevenueNet"), ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
                ("us-gaap", "RevenuesNetOfInterestExpense")],
    "cogs": [("us-gaap", "CostOfRevenue"), ("us-gaap", "CostOfGoodsAndServicesSold"),
             ("us-gaap", "CostOfGoodsSold"), ("us-gaap", "CostOfServices"),
             ("us-gaap", "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization")],
    "operating_income": [("us-gaap", "OperatingIncomeLoss")],
    "gross_profit": [("us-gaap", "GrossProfit")],
    "net_income": [("us-gaap", "NetIncomeLoss"), ("us-gaap", "ProfitLoss"),
                   ("us-gaap", "NetIncomeLossAvailableToCommonStockholdersBasic")],
    "eps": [("us-gaap", "EarningsPerShareDiluted"), ("us-gaap", "EarningsPerShareBasic")],
    "shares": [("dei", "EntityCommonStockSharesOutstanding"), ("us-gaap", "CommonStockSharesOutstanding"),
               ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding")],
}
CONCEPTS_VERSION = 2  # bump when CONCEPTS changes: forces one re-extraction from the bulk archive
# items where concepts must not be mixed within one company: use the first concept that has data
EXCLUSIVE = {"shares"}
INSTANT = {"assets", "equity", "shares"}
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F", "10-KT"}


# ----------------------------------------------------------------------------- extraction
def _typed(df: pd.DataFrame) -> pd.DataFrame:
    """Force column dtypes (empty frames from companies without facts would otherwise turn
    concatenated date columns into object dtype)."""
    for c in ("start", "end", "filed"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    df["val"] = pd.to_numeric(df["val"], errors="coerce")
    df["cik"] = pd.to_numeric(df["cik"], errors="coerce").astype("int64") if len(df) else df["cik"].astype("int64")
    df["prio"] = pd.to_numeric(df["prio"], errors="coerce")
    return df


def extract_facts(doc: dict) -> pd.DataFrame:
    """Compact long table of the concepts we use: item, start, end, val, filed, form."""
    cik = int(doc.get("cik", 0))
    facts = doc.get("facts", {})
    rows = []
    for item, cands in CONCEPTS.items():
        found = False
        for prio, (tax, name) in enumerate(cands):
            if found and item in EXCLUSIVE:
                break
            node = facts.get(tax, {}).get(name)
            if not node:
                continue
            for unit, arr in node.get("units", {}).items():
                if item == "shares" and unit != "shares":
                    continue
                if item == "eps" and "/shares" not in unit:
                    continue
                if item not in ("shares", "eps") and unit != "USD":
                    continue
                for x in arr:
                    if x.get("form") not in FORMS:
                        continue
                    rows.append((cik, item, prio, x.get("start"), x.get("end"), x.get("val"),
                                 x.get("filed"), x.get("form")))
                    found = True
    if not rows:
        return _typed(pd.DataFrame(columns=["cik", "item", "prio", "start", "end", "val", "filed", "form"]))
    df = pd.DataFrame(rows, columns=["cik", "item", "prio", "start", "end", "val", "filed", "form"])
    for c in ("start", "end", "filed"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    df["val"] = pd.to_numeric(df["val"], errors="coerce")
    df = df.dropna(subset=["end", "filed", "val"])
    # first report of each (item, period): the value the market actually saw first
    df = df.sort_values(["item", "prio", "start", "end", "filed"])
    df = df.drop_duplicates(["cik", "item", "start", "end"], keep="first")
    # keep the best-priority concept per (item, period)
    df = df.sort_values(["item", "start", "end", "prio", "filed"]).drop_duplicates(
        ["cik", "item", "start", "end"], keep="first")
    return df.reset_index(drop=True)


def load_fundamentals(f: Fetcher, cfg: dict, cache: Path, ciks: list[int]) -> pd.DataFrame:
    """Return compact facts for `ciks`, updating the cache incrementally."""
    store_p = cache / "sec_facts.pkl"
    meta_p = cache / "sec_facts_meta.json"
    store = pd.read_pickle(store_p) if store_p.exists() else None
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    if meta.get("concepts_version") != CONCEPTS_VERSION:
        store = None
    want = set(int(c) for c in ciks)
    have = set(store["cik"].unique()) if store is not None else set()
    today = date.today()
    errors = []

    to_fetch: set[int] = set()
    if store is None or len(want - have) > 200:
        # bulk archive (single request, ~1.3 GB)
        try:
            zp = f.get(cfg["sources"]["sec_companyfacts_bulk"], source="SEC companyfacts (bulk)",
                       ttl_hours=20, timeout=600, stream_to=cache / "companyfacts.zip")
            frames = []
            with zipfile.ZipFile(zp) as zf:
                names = set(zf.namelist())
                for cik in want:
                    n = f"CIK{cik:010d}.json"
                    if n in names:
                        frames.append(extract_facts(json.loads(zf.read(n))))
            frames = [x for x in frames if len(x)]
            store = _typed(pd.concat(frames, ignore_index=True)) if frames else store
            meta["last_update"] = today.isoformat()
            try:
                Path(zp).unlink()  # keep the cache small; compact store is what we keep
            except OSError:
                pass
        except Exception as e:  # noqa: BLE001
            errors.append(f"bulk: {e}")
            to_fetch |= want - have
    else:
        to_fetch |= want - have
        last = date.fromisoformat(meta.get("last_update", (today - timedelta(days=3)).isoformat()))
        filers = recent_filers(f, cfg, last, today)
        to_fetch |= (filers & want)
        meta["last_update"] = today.isoformat()

    new_frames = []
    for cik in sorted(to_fetch):
        url = cfg["sources"]["sec_companyfacts"].format(cik=cik)
        try:
            raw = f.get(url, source="SEC companyfacts (API)", ttl_hours=12)
            new_frames.append(extract_facts(json.loads(raw)))
        except FetchError as e:
            errors.append(str(e)[:200])
        except Exception as e:  # noqa: BLE001
            errors.append(f"{cik}: {e}"[:200])
    if store is not None:
        store = _typed(store)
    if new_frames:
        upd = _typed(pd.concat(new_frames, ignore_index=True))
        if store is not None:
            store = store[~store["cik"].isin(upd["cik"].unique())]
            store = pd.concat([store, upd], ignore_index=True)
        else:
            store = upd
    if store is None:
        record("SEC fundamentals", status="error", error="; ".join(errors)[:300] or "no data")
        return pd.DataFrame(columns=["cik", "item", "prio", "start", "end", "val", "filed", "form"])
    store.to_pickle(store_p)
    meta["concepts_version"] = CONCEPTS_VERSION
    meta_p.write_text(json.dumps(meta))
    record("SEC fundamentals", status="ok" if not errors else "partial",
           companies=int(store["cik"].nunique()), refetched=len(to_fetch),
           last_obs=str(store["filed"].max().date()), error=("; ".join(errors)[:300] or None))
    return store


def recent_filers(f: Fetcher, cfg: dict, since: date, until: date) -> set[int]:
    """CIKs with periodic reports in EDGAR daily form indexes between `since` and `until`."""
    out: set[int] = set()
    d = since
    while d <= until:
        if d.weekday() < 5:
            q = (d.month - 1) // 3 + 1
            url = cfg["sources"]["sec_daily_index"].format(year=d.year, q=q, ymd=d.strftime("%Y%m%d"))
            try:
                raw = f.get(url, source="SEC daily index", ttl_hours=24 * 30, allow_stale=False).decode("latin-1")
                out |= parse_form_index(raw)
            except Exception:  # noqa: BLE001 - holidays / not yet published
                pass
        d += timedelta(days=1)
    record("SEC daily index", status="ok", error=None, filers=len(out))
    return out


def parse_form_index(text: str) -> set[int]:
    ciks = set()
    started = False
    for line in text.splitlines():
        if line.startswith("---"):
            started = True
            continue
        if not started:
            continue
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) >= 4 and parts[0] in FORMS:
            try:
                ciks.add(int(parts[-3]))
            except ValueError:
                continue
    return ciks


# ----------------------------------------------------------------------------- derivation
def quarterly_flows(df: pd.DataFrame, item: str) -> pd.DataFrame:
    """Discrete quarterly values for a flow item with availability dates (first public filing).

    Q4 = annual minus nine-month YTD (or minus Q1..Q3). Output: end, val, avail.
    """
    x = df[df["item"] == item].dropna(subset=["start"]).copy()
    if x.empty:
        return pd.DataFrame(columns=["end", "val", "avail"])
    x["days"] = (x["end"] - x["start"]).dt.days
    q = x[(x["days"] >= 80) & (x["days"] <= 100)][["end", "val", "filed"]].rename(columns={"filed": "avail"})
    ann = x[(x["days"] >= 350) & (x["days"] <= 380)]
    ytd9 = x[(x["days"] >= 260) & (x["days"] <= 285)]
    rows = []
    qe = set(q["end"])
    for _, a in ann.iterrows():
        if a["end"] in qe:
            continue
        nine = ytd9[(ytd9["start"] - a["start"]).abs().dt.days <= 5]
        if len(nine):
            n = nine.iloc[0]
            rows.append((a["end"], a["val"] - n["val"], max(a["filed"], n["filed"])))
            continue
        prior = q[(q["end"] > a["start"]) & (q["end"] < a["end"] - pd.Timedelta(days=60))]
        if len(prior) == 3:
            rows.append((a["end"], a["val"] - prior["val"].sum(), max(a["filed"], prior["avail"].max())))
    if rows:
        q = pd.concat([q, pd.DataFrame(rows, columns=["end", "val", "avail"])], ignore_index=True)
    return q.sort_values("end").drop_duplicates("end", keep="first").reset_index(drop=True)


def ttm_series(qf: pd.DataFrame) -> pd.DataFrame:
    """Trailing-twelve-month sums of 4 consecutive quarters (ends spanning <= 380 days)."""
    if len(qf) < 4:
        return pd.DataFrame(columns=["end", "val", "avail"])
    out = []
    for i in range(3, len(qf)):
        w = qf.iloc[i - 3:i + 1]
        span = (w["end"].iloc[-1] - w["end"].iloc[0]).days
        if 250 <= span <= 300:
            out.append((w["end"].iloc[-1], w["val"].sum(), w["avail"].max()))
    return pd.DataFrame(out, columns=["end", "val", "avail"])


def instant_series(df: pd.DataFrame, item: str) -> pd.DataFrame:
    x = df[df["item"] == item][["end", "val", "filed"]].rename(columns={"filed": "avail"})
    return x.sort_values(["end", "avail"]).drop_duplicates("end", keep="first").reset_index(drop=True)


def sue_series(eq: pd.DataFrame, min_q: int = 8) -> pd.DataFrame:
    """Standardized unexpected earnings (seasonal random walk, Bernard & Thomas 1989).

    SUE_q = (EPS_q - EPS_{q-4}) / std of the previous `min_q` such changes. Availability is the
    filing date of quarter q (later than the press release, so this is conservative).
    """
    if eq is None or len(eq) < min_q + 5:
        return pd.DataFrame(columns=["end", "val", "avail"])
    e = eq.sort_values("end").reset_index(drop=True)
    chg = []
    for i in range(4, len(e)):
        if 330 <= (e["end"].iloc[i] - e["end"].iloc[i - 4]).days <= 400:
            chg.append((e["end"].iloc[i], e["val"].iloc[i] - e["val"].iloc[i - 4], e["avail"].iloc[i]))
    c = pd.DataFrame(chg, columns=["end", "d", "avail"])
    out = []
    for i in range(min_q, len(c)):
        sd = c["d"].iloc[i - min_q:i].std()
        if sd and sd > 0:
            out.append((c["end"].iloc[i], c["d"].iloc[i] / sd, c["avail"].iloc[i]))
    return pd.DataFrame(out, columns=["end", "val", "avail"])


class AsOf:
    """Fast point-in-time lookup: the latest-period value among facts filed on or before a date."""

    __slots__ = ("avail", "eff_end", "eff_val", "ends", "vals", "avails")

    def __init__(self, s: pd.DataFrame):
        s = s.dropna(subset=["end", "val", "avail"]).sort_values("avail")
        self.avail = s["avail"].to_numpy(dtype="datetime64[ns]")
        ends = s["end"].to_numpy(dtype="datetime64[ns]")
        vals = s["val"].to_numpy(dtype=float)
        n = len(s)
        self.eff_end = np.empty(n, dtype="datetime64[ns]")
        self.eff_val = np.empty(n)
        best_e, best_v = np.datetime64("NaT", "ns"), np.nan
        for i in range(n):
            if np.isnat(best_e) or ends[i] > best_e:
                best_e, best_v = ends[i], vals[i]
            self.eff_end[i], self.eff_val[i] = best_e, best_v
        order = np.argsort(ends)
        self.ends, self.vals, self.avails = ends[order], vals[order], self.avail[order]

    def get(self, when: np.datetime64):
        i = np.searchsorted(self.avail, when, side="right") - 1
        if i < 0:
            return None, None
        return self.eff_val[i], self.eff_end[i]

    def near(self, when: np.datetime64, target_end: np.datetime64, tol_days: int):
        if target_end is None or len(self.ends) == 0:
            return None
        ok = self.avails <= when
        if not ok.any():
            return None
        diff = np.abs((self.ends[ok] - target_end).astype("timedelta64[D]").astype(int))
        j = int(np.argmin(diff))
        return float(self.vals[ok][j]) if diff[j] <= tol_days else None


def derive_company(df: pd.DataFrame, min_q: int = 8) -> dict[str, AsOf]:
    """Per-company point-in-time series."""
    series = {it: instant_series(df, it) for it in INSTANT}
    for it in ("revenue", "cogs", "gross_profit", "net_income", "operating_income"):
        series[f"{it}_ttm"] = ttm_series(quarterly_flows(df, it))
    if series["gross_profit_ttm"].empty and not series["revenue_ttm"].empty and not series["cogs_ttm"].empty:
        m = series["revenue_ttm"].merge(series["cogs_ttm"], on="end", suffixes=("_r", "_c"))
        series["gross_profit_ttm"] = pd.DataFrame({"end": m["end"], "val": m["val_r"] - m["val_c"],
                                                   "avail": m[["avail_r", "avail_c"]].max(axis=1)})
    series["sue"] = sue_series(quarterly_flows(df, "eps"), min_q)
    keep = ("assets", "equity", "shares", "gross_profit_ttm", "net_income_ttm", "revenue_ttm",
            "operating_income_ttm", "sue")
    return {k: AsOf(series[k]) for k in keep}


def derive_all(store: pd.DataFrame, min_q: int = 8) -> dict[int, dict[str, AsOf]]:
    import traceback
    store = _typed(store.copy())
    out, errs = {}, []
    for cik, g in store.groupby("cik"):
        try:
            out[int(cik)] = derive_company(g, min_q)
        except Exception as e:  # noqa: BLE001
            if len(errs) < 3:
                errs.append(f"{cik}: {type(e).__name__}: {e} | {traceback.format_exc()[-600:]}")
    n = store["cik"].nunique()
    if errs:
        log.warning("point-in-time derivation failed for %d of %d companies; first: %s", n - len(out), n, errs[0])
    record("SEC point-in-time", status="ok" if len(out) > 0.9 * n else ("partial" if out else "error"),
           companies=len(out), failed=n - len(out), error=("; ".join(errs)[:900] or None))
    return out


def snapshot(derived: dict[int, dict[str, AsOf]], when, max_age_days: int = 548) -> pd.DataFrame:
    """Point-in-time fundamentals for every company as of `when` (only facts filed by then).

    Values whose fiscal period ended more than `max_age_days` before `when` are treated as missing.
    """
    w = np.datetime64(pd.Timestamp(when), "ns")
    year = np.timedelta64(365, "D")
    max_age = np.timedelta64(max_age_days, "D")
    rows = []
    for cik, d in derived.items():
        rec = {"cik": cik}
        ends = {}
        for k in ("assets", "equity", "shares", "gross_profit_ttm", "net_income_ttm", "revenue_ttm",
                  "operating_income_ttm"):
            v, e = d[k].get(w)
            ok = e is not None and not np.isnat(e) and (w - e) <= max_age
            rec[k] = float(v) if ok else None
            ends[k] = e if ok else None
        rec["assets_1y"] = d["assets"].near(w, ends["assets"] - year, 45) if ends["assets"] is not None else None
        rec["shares_1y"] = d["shares"].near(w, ends["shares"] - year, 60) if ends["shares"] is not None else None
        rec["shares_end"] = pd.Timestamp(ends["shares"]) if ends["shares"] is not None else None
        sv, se = d["sue"].get(w)
        if sv is not None and se is not None:
            # SUE is only informative for ~a quarter after the filing that revealed it
            idx = np.searchsorted(d["sue"].avail, w, side="right") - 1
            age = (w - d["sue"].avail[idx]).astype("timedelta64[D]").astype(int)
            rec["sue"] = float(sv) if age <= 95 else None
        else:
            rec["sue"] = None
        rec["fund_period_end"] = pd.Timestamp(ends["assets"]) if ends["assets"] is not None else None
        rows.append(rec)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- insiders
def _quarters_back(n: int, today: date | None = None) -> list[str]:
    today = today or date.today()
    y, q = today.year, (today.month - 1) // 3 + 1
    out = []
    for _ in range(n + 1):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        out.append(f"{y}q{q}")
    return out


def parse_form345_zip(raw: bytes) -> pd.DataFrame:
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = {n.upper().split("/")[-1]: n for n in zf.namelist()}

    def read(tsv, cols):
        with zf.open(names[tsv]) as fh:
            return pd.read_csv(fh, sep="\t", usecols=lambda c: c in cols, dtype=str, low_memory=False)

    sub = read("SUBMISSION.TSV", {"ACCESSION_NUMBER", "FILING_DATE", "ISSUERCIK", "ISSUERTRADINGSYMBOL", "DOCUMENT_TYPE"})
    own = read("REPORTINGOWNER.TSV", {"ACCESSION_NUMBER", "RPTOWNERCIK"})
    tr = read("NONDERIV_TRANS.TSV", {"ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
                                     "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD"})
    tr = tr[tr["TRANS_CODE"].isin(["P", "S"])]
    own = own.drop_duplicates("ACCESSION_NUMBER")
    df = tr.merge(sub, on="ACCESSION_NUMBER", how="left").merge(own, on="ACCESSION_NUMBER", how="left")
    df["trans_date"] = pd.to_datetime(df["TRANS_DATE"], format="mixed", errors="coerce", dayfirst=False)
    df["filed"] = pd.to_datetime(df["FILING_DATE"], format="mixed", errors="coerce", dayfirst=False)
    df["shares"] = pd.to_numeric(df["TRANS_SHARES"], errors="coerce")
    df["price"] = pd.to_numeric(df["TRANS_PRICEPERSHARE"], errors="coerce")
    df["value"] = df["shares"] * df["price"]
    df["side"] = np.where(df["TRANS_CODE"] == "P", 1, -1)
    df["issuer_cik"] = pd.to_numeric(df["ISSUERCIK"], errors="coerce")
    df["owner_cik"] = pd.to_numeric(df["RPTOWNERCIK"], errors="coerce")
    df["ticker"] = df["ISSUERTRADINGSYMBOL"].astype(str).str.upper().str.replace(".", "-", regex=False)
    return df[["issuer_cik", "ticker", "owner_cik", "trans_date", "filed", "side", "value"]].dropna(
        subset=["issuer_cik", "owner_cik", "trans_date"])


def _quarters_since(since: str, today: date | None = None) -> list[str]:
    today = today or date.today()
    y, q = int(since[:4]), int(since[-1])
    ly, lq = today.year, (today.month - 1) // 3 + 1
    out = []
    while (y, q) < (ly, lq):
        out.append(f"{y}q{q}")
        q += 1
        if q == 5:
            y, q = y + 1, 1
    return out


def load_insiders(f: Fetcher, cfg: dict, cache: Path, since: str | None = None) -> pd.DataFrame:
    n = cfg["signals"]["insiders"]["lookback_quarters"]
    quarters = _quarters_since(since) if since else _quarters_back(n)
    n = len(quarters)
    frames, errs = [], []
    for yq in quarters:
        p = cache / f"form345_{yq}.pkl"
        if p.exists():
            frames.append(pd.read_pickle(p))
            continue
        got = None
        for key in ("sec_form345", "sec_form345_alt"):
            url = cfg["sources"][key].format(yq=yq)
            try:
                raw = f.get(url, source="SEC Form 3/4/5", ttl_hours=24 * 30, timeout=300, allow_stale=False)
                got = parse_form345_zip(raw)
                break
            except Exception as e:  # noqa: BLE001
                errs.append(f"{yq}: {e}"[:120])
        if got is not None:
            got.to_pickle(p)
            frames.append(got)
    if not frames:
        record("SEC Form 3/4/5", status="error", error="; ".join(errs)[:300])
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    record("SEC Form 3/4/5", status="ok", rows=int(len(df)), quarters=len(frames),
           last_obs=str(df["filed"].max().date()), error=None if len(frames) > n - 2 else "; ".join(errs)[:300])
    return df


def diagnose(store: pd.DataFrame, ciks: dict[str, int]) -> dict:
    """Small per-company summary of raw facts and derived series (written to docs/data/diagnostics.json)."""
    out = {}
    st = _typed(store.copy())
    for tkr, cik in ciks.items():
        g = st[st["cik"] == cik]
        if g.empty:
            out[tkr] = {"cik": cik, "facts": 0}
            continue
        rep = {"cik": cik, "facts": int(len(g)), "items": {}}
        for it, x in g.groupby("item"):
            days = (x["end"] - x["start"]).dt.days
            buckets = {"instant": int(days.isna().sum()), "q(80-100)": int(days.between(80, 100).sum()),
                       "6m": int(days.between(170, 190).sum()), "9m": int(days.between(260, 285).sum()),
                       "annual": int(days.between(350, 380).sum())}
            tail = x.sort_values("end").tail(6)
            rep["items"][it] = {"buckets": buckets, "concept_prio": sorted(set(int(v) for v in x["prio"])),
                                "last": [[str(r.start.date()) if pd.notna(r.start) else None, str(r.end.date()),
                                          float(r.val), str(r.filed.date()), r.form] for r in tail.itertuples()]}
        try:
            d = {k: v for k, v in (("q_net_income", quarterly_flows(g, "net_income")),
                                     ("ttm_net_income", ttm_series(quarterly_flows(g, "net_income"))))}
            rep["derived"] = {k: [[str(e.date()), float(v), str(a.date())] for e, v, a in
                                  zip(x_["end"].tail(6), x_["val"].tail(6), x_["avail"].tail(6))] for k, x_ in d.items()}
        except Exception as e:  # noqa: BLE001
            rep["derived_error"] = f"{type(e).__name__}: {e}"
        out[tkr] = rep
    return out
