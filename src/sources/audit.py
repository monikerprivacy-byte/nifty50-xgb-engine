"""14-point source acceptance audit (PROJECT_CONSTITUTION §4.5).

Every vendor data source must pass all 14 checks before entering Gold layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from src.sources.adapter import (
    AuditReport,
    ContractIdentity,
    SourceAdapter,
    SourceManifest,
    SourceStatus,
)

logger = logging.getLogger(__name__)

AUDIT_ITEMS: List[str] = [
    "contract_identity_accuracy",
    "timestamp_timezone_precision",
    "expiry_mapping",
    "strike_adjustment",
    "oi_semantics",
    "volume_semantics",
    "bid_ask_availability",
    "missing_minute_rate",
    "duplicate_primary_keys",
    "unexplained_negative_volume",
    "timezone_ambiguity",
    "corporate_action_handling",
    "late_correction_policy",
    "survivorship_coverage",
]

_REQUIRED_CHECKS = len(AUDIT_ITEMS)


@dataclass
class AuditContext:
    source_name: str
    adapter: SourceAdapter
    manifest: SourceManifest
    start_date: date
    end_date: date
    contracts_sampled: int = 100
    checks: Dict[str, bool] = field(default_factory=dict)
    failures: List[str] = field(default_factory=list)
    contract_samples: List[ContractIdentity] = field(default_factory=list)


def run_audit(ctx: AuditContext) -> AuditReport:
    for check in AUDIT_ITEMS:
        fn = _CHECK_REGISTRY.get(check)
        if fn is None:
            ctx.checks[check] = False
            ctx.failures.append(f"no implementation for {check}")
            continue
        try:
            passed = fn(ctx)
            ctx.checks[check] = passed
            if not passed:
                ctx.failures.append(f"{check}: FAILED")
        except Exception as e:
            ctx.checks[check] = False
            ctx.failures.append(f"{check}: error — {e}")

    failed_count = sum(1 for v in ctx.checks.values() if not v)
    if failed_count == 0:
        status = SourceStatus.APPROVED
    elif failed_count <= 3:
        status = SourceStatus.LIMITED
    elif failed_count <= 6:
        status = SourceStatus.QUARANTINED
    else:
        status = SourceStatus.REJECTED

    return AuditReport(
        source_name=ctx.source_name,
        audit_timestamp=datetime.now(timezone.utc),
        status=status,
        checks=dict(ctx.checks),
        failure_reasons=list(ctx.failures),
        contract_samples=list(ctx.contract_samples),
    )


# ---- check implementations ----

_CHECK_REGISTRY: Dict[str, callable] = {}


def _register(name: str):
    def decorator(fn):
        _CHECK_REGISTRY[name] = fn
        return fn
    return decorator


def _sample_contracts(ctx: AuditContext) -> List[ContractIdentity]:
    if not ctx.contract_samples:
        all_contracts = ctx.adapter.list_contracts()
        step = max(1, len(all_contracts) // ctx.contracts_sampled)
        ctx.contract_samples = all_contracts[::step][:ctx.contracts_sampled]
    return ctx.contract_samples


@_register("contract_identity_accuracy")
def check_contract_identity(ctx: AuditContext) -> bool:
    samples = _sample_contracts(ctx)
    for c in samples:
        if not ctx.adapter.validate_identity(c):
            return False
    return True


@_register("timestamp_timezone_precision")
def check_timestamp_tz(ctx: AuditContext) -> bool:
    meta = ctx.manifest.metadata
    tz_known = "timezone" in meta and meta["timezone"] in ("IST", "Asia/Kolkata", "UTC")
    precision_ok = meta.get("timestamp_precision") in ("second", "millisecond", "microsecond")
    return tz_known and precision_ok


@_register("expiry_mapping")
def check_expiry(ctx: AuditContext) -> bool:
    samples = _sample_contracts(ctx)
    for c in samples:
        if not isinstance(c.expiry, date):
            return False
        if c.expiry < ctx.start_date:
            return False
    return True


@_register("strike_adjustment")
def check_strike_adjustment(ctx: AuditContext) -> bool:
    return ctx.manifest.metadata.get("strike_adjustment_documented") == "true"


@_register("oi_semantics")
def check_oi_semantics(ctx: AuditContext) -> bool:
    oi = ctx.manifest.metadata.get("oi_semantics", "")
    return oi in ("opening", "EOD", "intraday", "snapshot")


@_register("volume_semantics")
def check_volume_semantics(ctx: AuditContext) -> bool:
    vol = ctx.manifest.metadata.get("volume_semantics", "")
    return vol in ("derived", "reported", "cumulative")


@_register("bid_ask_availability")
def check_bid_ask(ctx: AuditContext) -> bool:
    ba = ctx.manifest.metadata.get("bid_ask_available", "")
    return ba in ("true", "false")  # known either way


@_register("missing_minute_rate")
def check_missing_rate(ctx: AuditContext) -> bool:
    rate = ctx.manifest.metadata.get("missing_minute_rate")
    if rate is None:
        return False
    try:
        val = float(rate)
        return 0.0 <= val <= 100.0
    except ValueError:
        return False


@_register("duplicate_primary_keys")
def check_duplicate_pks(ctx: AuditContext) -> bool:
    samples = _sample_contracts(ctx)
    seen: Set[Tuple] = set()
    for c in samples:
        samples_data = ctx.adapter.fetch_range(c, ctx.start_date, ctx.end_date)
        for sl in samples_data:
            key = (sl.security_id, sl.timestamp)
            if key in seen:
                return False
            seen.add(key)
    return True


@_register("unexplained_negative_volume")
def check_negative_volume(ctx: AuditContext) -> bool:
    samples = _sample_contracts(ctx)
    for c in samples:
        samples_data = ctx.adapter.fetch_range(c, ctx.start_date, ctx.end_date)
        for sl in samples_data:
            if sl.volume < 0:
                return False
    return True


@_register("timezone_ambiguity")
def check_tz_ambiguity(ctx: AuditContext) -> bool:
    tz = ctx.manifest.metadata.get("timezone", "")
    dst_policy = ctx.manifest.metadata.get("dst_policy", "")
    if not tz:
        return False
    if tz in ("IST", "Asia/Kolkata"):
        return True  # India has no DST
    return bool(dst_policy)


@_register("corporate_action_handling")
def check_corp_action(ctx: AuditContext) -> bool:
    return ctx.manifest.metadata.get("corporate_action_documented") == "true"


@_register("late_correction_policy")
def check_late_correction(ctx: AuditContext) -> bool:
    return ctx.manifest.metadata.get("late_correction_documented") == "true"


@_register("survivorship_coverage")
def check_survivorship(ctx: AuditContext) -> bool:
    cov = ctx.manifest.metadata.get("survivorship_coverage", "")
    return cov in ("full", "current_only", "known_gaps", "unknown")


def audit_summary(report: AuditReport) -> str:
    passed = sum(1 for v in report.checks.values() if v)
    failed = len(report.checks) - passed
    lines = [
        f"Source: {report.source_name}",
        f"Status: {report.status.value}",
        f"Checks: {passed}/{len(report.checks)} passed, {failed} failed",
    ]
    if report.failure_reasons:
        lines.append("Failures:")
        for r in report.failure_reasons:
            lines.append(f"  - {r}")
    return "\n".join(lines)
