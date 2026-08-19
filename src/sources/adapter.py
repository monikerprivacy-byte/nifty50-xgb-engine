"""Vendor source adapter interface for historical data qualification.

Every external data vendor must implement this interface before any of
its data enters the Gold layer (PROJECT_CONSTITUTION §4.5).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple


class SourceStatus(Enum):
    APPROVED = "APPROVED"
    LIMITED = "LIMITED"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"


class LiquidityClass(Enum):
    LIQUID = "LIQUID"
    SEMI_LIQUID = "SEMI_LIQUID"
    ILLIQUID = "ILLIQUID"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ContractIdentity:
    exchange: str
    trading_symbol: str
    security_id: int
    expiry: date
    option_type: str  # CE or PE
    strike: float


@dataclass(frozen=True)
class SourceManifest:
    source_name: str
    source_version: str
    coverage_start: date
    coverage_end: Optional[date]
    contract_count: int
    day_count: int
    file_format: str
    checksum_algorithm: str
    checksum: str
    ingestion_timestamp: datetime
    metadata: Dict[str, str]


@dataclass(frozen=True)
class AuditReport:
    source_name: str
    audit_timestamp: datetime
    status: SourceStatus
    checks: Dict[str, bool]
    failure_reasons: List[str]
    contract_samples: List[ContractIdentity]


@dataclass(frozen=True)
class DataSlice:
    security_id: int
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    open_interest: Optional[int]
    source: str
    liquidity: LiquidityClass


class SourceAdapter(ABC):

    @abstractmethod
    def source_name(self) -> str:
        ...

    @abstractmethod
    def list_contracts(self) -> List[ContractIdentity]:
        ...

    @abstractmethod
    def fetch_range(
        self,
        contract: ContractIdentity,
        start: date,
        end: date,
    ) -> List[DataSlice]:
        ...

    @abstractmethod
    def validate_identity(self, identity: ContractIdentity) -> bool:
        ...

    @abstractmethod
    def coverage_gap_analysis(self) -> List[Tuple[date, date]]:
        ...

    @abstractmethod
    def manifest(self) -> SourceManifest:
        ...
