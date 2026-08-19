"""Contract-identity validator for vendor data sources.

Ensures every contract identity from a vendor matches the canonical
NSE option contract specification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Set, Tuple

from src.sources.adapter import ContractIdentity


# NSE weekly/monthly expiry pattern: last Thursday of month for monthly,
# specific weekday for weekly
_EXPIRY_PATTERN = re.compile(r"^\d{2}[A-Z]{3}\d{2}$")  # e.g. 25JUL24 -> obsolete
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{1,15}$")


@dataclass(frozen=True)
class ValidationResult:
    identity: ContractIdentity
    is_valid: bool
    reasons: List[str]


class ContractIdentityValidator:
    def __init__(self, known_security_ids: Optional[Set[int]] = None):
        self._known_ids = known_security_ids or set()

    def validate(self, identity: ContractIdentity) -> ValidationResult:
        reasons: List[str] = []

        if identity.exchange not in ("NSE", "BSE", "NFO", "CDS"):
            reasons.append(f"unsupported exchange: {identity.exchange}")

        if identity.option_type not in ("CE", "PE"):
            reasons.append(f"invalid option type: {identity.option_type}")

        if identity.strike <= 0:
            reasons.append(f"invalid strike: {identity.strike}")

        min_date = date(2000, 1, 1)
        max_date = date(2030, 12, 31)
        if not (min_date <= identity.expiry <= max_date):
            reasons.append(f"expiry out of range: {identity.expiry}")

        if identity.security_id <= 0:
            reasons.append(f"invalid security_id: {identity.security_id}")

        if self._known_ids and identity.security_id not in self._known_ids:
            reasons.append(f"unknown security_id: {identity.security_id}")

        if not _SYMBOL_PATTERN.match(identity.trading_symbol):
            reasons.append(f"invalid trading symbol: {identity.trading_symbol}")

        return ValidationResult(
            identity=identity,
            is_valid=len(reasons) == 0,
            reasons=reasons,
        )

    def validate_batch(self, identities: List[ContractIdentity]) -> List[ValidationResult]:
        return [self.validate(c) for c in identities]


def build_trading_symbol(identity: ContractIdentity) -> str:
    return f"{identity.exchange}:{identity.security_id}:{identity.expiry.isoformat()}:{identity.option_type}:{identity.strike}"
