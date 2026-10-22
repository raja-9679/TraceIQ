"""Cost intelligence in money (WP12d, E23 / decision R4).

Turns usage TraceIQ already records into an amount of money:

* LLM tokens — `LLMUsageEvent` rows (per workspace / project / run / model /
  feature), priced per 1M input and per 1M output tokens of the model.
* Worker time — the sum of `TestCaseResult.duration_ms` of automated runs,
  priced per browser-minute. This is an APPROXIMATION and every report says
  so: it is the time cases spent executing, not the time a worker container
  was up (idle time, browser start-up between jobs, retries' backoff and
  continuous jobs' shared browser are not in it). Manual runs (a person's
  time, not a worker's) and runs on a developer's local worker (their
  machine) are excluded.

Prices come from the instance settings (admin-editable, group "cost"):
COST_CURRENCY, COST_LLM_PRICES, COST_BROWSER_MINUTE_PRICE. All default to
empty and nothing here invents a vendor price: with no currency set no money
is shown at all, and usage whose model has no price is reported as "not
priced" (amount None) instead of as 0 — a zero would read as "free".

Arithmetic is Decimal end to end; amounts are rounded once, at the edge
(`money`), half-up to the currency's minor unit. A total is the rounded sum of
unrounded parts, so it can differ from the sum of the rounded lines by a
minor unit — the same convention an invoice uses.
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

APPROXIMATION_NOTE = (
    "Browser-minutes are the summed execution time of automated test cases, "
    "as recorded on each test result. They approximate worker cost: worker idle "
    "time, browser start-up and retry back-off are not included. Manual runs "
    "and runs on a local worker are excluded.")

MAX_PRICE = Decimal("1000000")
MAX_MODELS = 200
MAX_MODEL_NAME = 200
BUDGET_THRESHOLDS = (80, 100)

# ISO 4217 currencies with no minor unit. Everything else rounds to cents.
_ZERO_DECIMAL = frozenset({
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF",
    "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
})
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class PriceTableError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Validation (instance-settings save path) — strict, with readable errors
# ---------------------------------------------------------------------------


def _decimal(value: Any, what: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise PriceTableError(f"{what} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise PriceTableError(f"{what} must be a finite number")
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise PriceTableError(f"{what} must be a number")
    if not d.is_finite():
        raise PriceTableError(f"{what} must be a finite number")
    if d < 0:
        raise PriceTableError(f"{what} cannot be negative")
    if d > MAX_PRICE:
        raise PriceTableError(f"{what} is implausibly large (max {MAX_PRICE})")
    return d


def validate_currency(value: Any) -> Optional[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise PriceTableError("COST_CURRENCY must be a 3-letter ISO 4217 code, e.g. EUR")
    code = value.strip().upper()
    if not _CURRENCY_RE.match(code):
        raise PriceTableError("COST_CURRENCY must be a 3-letter ISO 4217 code, e.g. EUR")
    return code


def validate_minute_price(value: Any) -> Optional[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return str(_decimal(value, "COST_BROWSER_MINUTE_PRICE"))


def validate_llm_prices(value: Any) -> Dict[str, Dict[str, float]]:
    """{"<model>": {"input_per_1m": n, "output_per_1m": n}} — the model name
    exactly as LLMUsageEvent.model records it (matched case-insensitively).
    Accepts the dict or its JSON text. Returns the normalised dict."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise PriceTableError("COST_LLM_PRICES must be a JSON object")
    if not isinstance(value, dict):
        raise PriceTableError(
            'COST_LLM_PRICES must be an object: {"<model>": {"input_per_1m": 1.0, "output_per_1m": 2.0}}')
    if len(value) > MAX_MODELS:
        raise PriceTableError(f"COST_LLM_PRICES: at most {MAX_MODELS} models")
    out: Dict[str, Dict[str, float]] = {}
    seen = set()
    for model, entry in value.items():
        name = str(model).strip()
        if not name or len(name) > MAX_MODEL_NAME:
            raise PriceTableError(f"COST_LLM_PRICES: model names are 1-{MAX_MODEL_NAME} characters")
        if name.lower() in seen:
            raise PriceTableError(f"COST_LLM_PRICES: '{name}' is listed twice")
        seen.add(name.lower())
        if not isinstance(entry, dict):
            raise PriceTableError(f"COST_LLM_PRICES['{name}'] must be an object with input_per_1m and output_per_1m")
        unknown = set(entry) - {"input_per_1m", "output_per_1m"}
        if unknown:
            raise PriceTableError(f"COST_LLM_PRICES['{name}']: unknown field(s) {', '.join(sorted(unknown))}")
        if "input_per_1m" not in entry or "output_per_1m" not in entry:
            raise PriceTableError(f"COST_LLM_PRICES['{name}'] needs both input_per_1m and output_per_1m")
        out[name] = {
            k: float(_decimal(entry[k], f"COST_LLM_PRICES['{name}'].{k}"))
            for k in ("input_per_1m", "output_per_1m")
        }
    return out


# ---------------------------------------------------------------------------
# The effective price table (read path) — tolerant: a bad stored value is
# treated as unset and logged, never a 500 on the report page
# ---------------------------------------------------------------------------


@dataclass
class Prices:
    currency: Optional[str] = None
    models: Dict[str, Tuple[Decimal, Decimal]] = field(default_factory=dict)  # lower(model) -> (in, out)
    browser_minute: Optional[Decimal] = None

    @property
    def enabled(self) -> bool:
        return self.currency is not None

    def model_price(self, model: Optional[str]) -> Optional[Tuple[Decimal, Decimal]]:
        if not self.enabled or not model:
            return None
        return self.models.get(model.strip().lower())

    def minute_price(self) -> Optional[Decimal]:
        return self.browser_minute if self.enabled else None


def load_prices(get: Optional[Callable[[str], Any]] = None) -> Prices:
    if get is None:
        from app.services.instance_settings import effective as get
    prices = Prices()
    try:
        prices.currency = validate_currency(get("COST_CURRENCY"))
    except PriceTableError as exc:
        logger.warning("cost: ignoring COST_CURRENCY (%s)", exc)
    try:
        table = validate_llm_prices(get("COST_LLM_PRICES"))
        prices.models = {
            k.lower(): (Decimal(str(v["input_per_1m"])), Decimal(str(v["output_per_1m"])))
            for k, v in table.items()}
    except PriceTableError as exc:
        logger.warning("cost: ignoring COST_LLM_PRICES (%s)", exc)
    try:
        mp = validate_minute_price(get("COST_BROWSER_MINUTE_PRICE"))
        prices.browser_minute = Decimal(mp) if mp is not None else None
    except PriceTableError as exc:
        logger.warning("cost: ignoring COST_BROWSER_MINUTE_PRICE (%s)", exc)
    return prices


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------

_MILLION = Decimal(1_000_000)
_MS_PER_MINUTE = Decimal(60_000)


def token_cost(input_tokens: int, output_tokens: int, price: Tuple[Decimal, Decimal]) -> Decimal:
    p_in, p_out = price
    return (Decimal(int(input_tokens or 0)) * p_in + Decimal(int(output_tokens or 0)) * p_out) / _MILLION


def minutes(ms: Any) -> Decimal:
    return Decimal(str(ms or 0)) / _MS_PER_MINUTE


def money(amount: Optional[Decimal], currency: Optional[str]) -> Optional[float]:
    """Round once, at the edge, to the currency's minor unit (half-up)."""
    if amount is None or currency is None:
        return None
    exp = Decimal("1") if currency in _ZERO_DECIMAL else Decimal("0.01")
    return float(amount.quantize(exp, rounding=ROUND_HALF_UP))


def budget_crossed(spent: Decimal, budget: Decimal) -> List[int]:
    """Thresholds (percent) this month's spend has reached."""
    if budget is None or budget <= 0:
        return []
    return [t for t in BUDGET_THRESHOLDS if spent * 100 >= budget * t]


# ---------------------------------------------------------------------------
# Report assembly (pure: rows in, JSON-able dict out)
# ---------------------------------------------------------------------------


def _day(v: Any) -> str:
    return v.isoformat() if isinstance(v, (date, datetime)) else str(v)


def window(days: int, now: Optional[datetime] = None) -> Tuple[datetime, datetime, List[str]]:
    """[since, until) covering `days` UTC calendar days ending today."""
    now = now or datetime.utcnow()
    today = datetime(now.year, now.month, now.day)
    since = today - timedelta(days=days - 1)
    return since, now, [(since + timedelta(days=i)).date().isoformat() for i in range(days)]


def month_start(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.utcnow()
    return datetime(now.year, now.month, 1)


class _Acc:
    """Running total of priced + unpriced usage."""
    __slots__ = ("amount", "tokens", "unpriced_tokens", "input_tokens", "output_tokens", "ms")

    def __init__(self):
        self.amount = Decimal(0)
        self.tokens = self.unpriced_tokens = self.input_tokens = self.output_tokens = 0
        self.ms = Decimal(0)


def project_spend(prices: Prices, llm_rows: Iterable[dict], browser_rows: Iterable[dict]) -> Dict[Any, Decimal]:
    """Priced spend per project_id (None = workspace-level LLM use)."""
    out: Dict[Any, Decimal] = {}
    for r in llm_rows:
        price = prices.model_price(r.get("model"))
        if price is not None:
            pid = r.get("project_id")
            out[pid] = out.get(pid, Decimal(0)) + token_cost(r["input_tokens"], r["output_tokens"], price)
    mp = prices.minute_price()
    if mp is not None:
        for r in browser_rows:
            pid = r.get("project_id")
            out[pid] = out.get(pid, Decimal(0)) + minutes(r["ms"]) * mp
    return out


def build_report(prices: Prices, *, days: List[str], llm_rows: List[dict],
                 browser_rows: List[dict], llm_run_rows: List[dict],
                 browser_run_rows: List[dict], run_meta: Dict[int, dict],
                 top_n: int = 10) -> Dict[str, Any]:
    """llm_rows: {day, project_id, provider, model, feature, input_tokens, output_tokens}
    browser_rows: {day, project_id, ms}; llm_run_rows: {run_id, model, input_tokens,
    output_tokens}; browser_run_rows: {run_id, ms}."""
    cur = prices.currency
    by_model: Dict[Tuple[str, str], _Acc] = {}
    by_feature: Dict[str, _Acc] = {}
    daily: Dict[str, Dict[str, Any]] = {
        d: {"llm": Decimal(0), "browser": Decimal(0), "tokens": 0, "ms": Decimal(0)} for d in days}
    llm_total = _Acc()

    for r in llm_rows:
        tin, tout = int(r["input_tokens"] or 0), int(r["output_tokens"] or 0)
        price = prices.model_price(r.get("model"))
        cost = token_cost(tin, tout, price) if price is not None else None
        for acc in (by_model.setdefault((r.get("provider") or "", r.get("model") or ""), _Acc()),
                    by_feature.setdefault(r.get("feature") or "unknown", _Acc()), llm_total):
            acc.input_tokens += tin
            acc.output_tokens += tout
            acc.tokens += tin + tout
            if cost is None:
                acc.unpriced_tokens += tin + tout
            else:
                acc.amount += cost
        d = daily.get(_day(r["day"]))
        if d is not None:
            d["tokens"] += tin + tout
            if cost is not None:
                d["llm"] += cost

    mp = prices.minute_price()
    browser_ms = Decimal(0)
    for r in browser_rows:
        ms = Decimal(str(r["ms"] or 0))
        browser_ms += ms
        d = daily.get(_day(r["day"]))
        if d is not None:
            d["ms"] += ms
            if mp is not None:
                d["browser"] += minutes(ms) * mp
    browser_amount = minutes(browser_ms) * mp if mp is not None else None

    total = None
    if prices.enabled:
        total = llm_total.amount + (browser_amount or Decimal(0))

    # Top runs by cost: tokens and browser time per run, priced.
    runs: Dict[int, Dict[str, Any]] = {}
    for r in llm_run_rows:
        e = runs.setdefault(r["run_id"], {"tokens": 0, "unpriced_tokens": 0, "llm": Decimal(0), "ms": Decimal(0)})
        tin, tout = int(r["input_tokens"] or 0), int(r["output_tokens"] or 0)
        e["tokens"] += tin + tout
        price = prices.model_price(r.get("model"))
        if price is None:
            e["unpriced_tokens"] += tin + tout
        else:
            e["llm"] += token_cost(tin, tout, price)
    for r in browser_run_rows:
        e = runs.setdefault(r["run_id"], {"tokens": 0, "unpriced_tokens": 0, "llm": Decimal(0), "ms": Decimal(0)})
        e["ms"] += Decimal(str(r["ms"] or 0))

    def run_total(e):
        return e["llm"] + (minutes(e["ms"]) * mp if mp is not None else Decimal(0))

    ranked = sorted(runs.items(), key=lambda kv: (run_total(kv[1]), kv[1]["tokens"], kv[1]["ms"]),
                    reverse=True)[:top_n]
    top_runs = []
    for run_id, e in ranked:
        meta = run_meta.get(run_id, {})
        top_runs.append({
            "run_id": run_id,
            "suite_name": meta.get("suite_name"),
            "project_id": meta.get("project_id"),
            "created_at": meta.get("created_at"),
            "tokens": e["tokens"],
            "unpriced_tokens": e["unpriced_tokens"],
            "browser_minutes": round(float(minutes(e["ms"])), 2),
            "llm_amount": money(e["llm"], cur) if prices.enabled else None,
            "browser_amount": money(minutes(e["ms"]) * mp, cur) if mp is not None else None,
            "total": money(run_total(e), cur) if prices.enabled else None,
        })

    def acc_view(acc: _Acc) -> Dict[str, Any]:
        # amount: what the priced part costs (None when nothing in it has a
        # price); priced: every token in it had one.
        any_priced = prices.enabled and (acc.tokens == 0 or acc.unpriced_tokens < acc.tokens)
        return {
            "input_tokens": acc.input_tokens, "output_tokens": acc.output_tokens,
            "tokens": acc.tokens, "unpriced_tokens": acc.unpriced_tokens,
            "amount": money(acc.amount, cur) if any_priced else None,
            "priced": prices.enabled and acc.unpriced_tokens == 0,
        }

    return {
        "currency": cur,
        "priced": prices.enabled,
        "llm": {
            **acc_view(llm_total),
            "by_model": sorted(
                ({"provider": p, "model": m, **acc_view(a)} for (p, m), a in by_model.items()),
                key=lambda x: (x["amount"] or 0, x["tokens"]), reverse=True),
            "by_feature": sorted(
                ({"feature": f, **acc_view(a)} for f, a in by_feature.items()),
                key=lambda x: (x["amount"] or 0, x["tokens"]), reverse=True),
        },
        "browser": {
            "minutes": round(float(minutes(browser_ms)), 2),
            "amount": money(browser_amount, cur),
            "priced": browser_amount is not None,
            "price_per_minute": float(mp) if mp is not None else None,
            "note": APPROXIMATION_NOTE,
        },
        "total": money(total, cur),
        "total_is_partial": bool(prices.enabled and (
            llm_total.unpriced_tokens > 0 or (browser_ms > 0 and mp is None))),
        "daily": [{
            "date": d,
            "llm_amount": money(v["llm"], cur) if prices.enabled else None,
            "browser_amount": money(v["browser"], cur) if mp is not None else None,
            "total": money(v["llm"] + v["browser"], cur) if prices.enabled else None,
            "tokens": v["tokens"],
            "browser_minutes": round(float(minutes(v["ms"])), 2),
        } for d, v in daily.items()],
        "top_runs": top_runs,
    }


# ---------------------------------------------------------------------------
# Queries (sync; the async API calls them through session.run_sync so the
# report and the budget task share one implementation)
# ---------------------------------------------------------------------------


def collect(session, *, project_ids: List[int], since: datetime, until: datetime,
            workspace_id_for_unassigned: Optional[int] = None,
            with_runs: bool = True) -> Dict[str, Any]:
    """Usage rows for the given projects in [since, until). LLM calls made
    with no project (workspace-level generation, say) are included only when
    `workspace_id_for_unassigned` is given — the caller decides who may see
    them."""
    from sqlalchemy import and_, func, or_
    from sqlmodel import select

    from app.models import ExecutorType, LLMUsageEvent, TestCaseResult, TestRun

    project_ids = list(project_ids or [])
    llm_scope = []
    if project_ids:
        llm_scope.append(LLMUsageEvent.project_id.in_(project_ids))
    if workspace_id_for_unassigned is not None:
        llm_scope.append(and_(LLMUsageEvent.workspace_id == workspace_id_for_unassigned,
                              LLMUsageEvent.project_id.is_(None)))
    out: Dict[str, Any] = {"llm_rows": [], "browser_rows": [], "llm_run_rows": [],
                           "browser_run_rows": [], "run_meta": {}}
    if llm_scope:
        llm_where = (or_(*llm_scope), LLMUsageEvent.created_at >= since,
                     LLMUsageEvent.created_at < until)
        day = func.date(LLMUsageEvent.created_at)
        tin = func.coalesce(func.sum(LLMUsageEvent.input_tokens), 0)
        tout = func.coalesce(func.sum(LLMUsageEvent.output_tokens), 0)
        for r in session.exec(
                select(day, LLMUsageEvent.project_id, LLMUsageEvent.provider, LLMUsageEvent.model,
                       LLMUsageEvent.feature, tin, tout)
                .where(*llm_where)
                .group_by(day, LLMUsageEvent.project_id, LLMUsageEvent.provider,
                          LLMUsageEvent.model, LLMUsageEvent.feature)).all():
            out["llm_rows"].append({"day": r[0], "project_id": r[1], "provider": r[2], "model": r[3],
                                    "feature": r[4], "input_tokens": int(r[5]), "output_tokens": int(r[6])})
        if with_runs:
            for r in session.exec(
                    select(LLMUsageEvent.run_id, LLMUsageEvent.model, tin, tout)
                    .where(*llm_where, LLMUsageEvent.run_id.is_not(None))
                    .group_by(LLMUsageEvent.run_id, LLMUsageEvent.model)
                    .order_by((tin + tout).desc()).limit(2000)).all():
                out["llm_run_rows"].append({"run_id": r[0], "model": r[1],
                                            "input_tokens": int(r[2]), "output_tokens": int(r[3])})

    if project_ids:
        run_where = (TestRun.project_id.in_(project_ids), TestRun.created_at >= since,
                     TestRun.created_at < until,
                     TestRun.executor != ExecutorType.MANUAL.value,
                     TestRun.local_worker_id.is_(None))
        rday = func.date(TestRun.created_at)
        ms = func.coalesce(func.sum(TestCaseResult.duration_ms), 0)
        for r in session.exec(
                select(rday, TestRun.project_id, ms)
                .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
                .where(*run_where)
                .group_by(rday, TestRun.project_id)).all():
            out["browser_rows"].append({"day": r[0], "project_id": r[1], "ms": float(r[2] or 0)})
        if with_runs:
            for r in session.exec(
                    select(TestRun.id, ms)
                    .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
                    .where(*run_where)
                    .group_by(TestRun.id).order_by(ms.desc()).limit(200)).all():
                out["browser_run_rows"].append({"run_id": r[0], "ms": float(r[1] or 0)})

    if with_runs:
        ids = {r["run_id"] for r in out["llm_run_rows"]} | {r["run_id"] for r in out["browser_run_rows"]}
        if ids:
            # Only runs of the projects in scope: an LLM event's run_id is a
            # soft reference and must not surface another project's run name.
            for r in session.exec(
                    select(TestRun.id, TestRun.suite_name, TestRun.project_id, TestRun.created_at)
                    .where(TestRun.id.in_(ids), TestRun.project_id.in_(project_ids or [-1]))).all():
                out["run_meta"][r[0]] = {"suite_name": r[1], "project_id": r[2],
                                         "created_at": r[3].isoformat() if r[3] else None}
            allowed = set(out["run_meta"])
            out["llm_run_rows"] = [r for r in out["llm_run_rows"] if r["run_id"] in allowed]
            out["browser_run_rows"] = [r for r in out["browser_run_rows"] if r["run_id"] in allowed]
    return out
