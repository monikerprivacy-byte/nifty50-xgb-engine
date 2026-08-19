from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Dict, List, Optional, Set


class ReadinessLevel(str, Enum):
    NONE = "NONE"
    QUOTE_READY = "QUOTE_READY"
    FEATURE_READY = "FEATURE_READY"


@dataclass
class ContractReadiness:
    contract_id: str
    security_id: int
    has_valid_packet: bool = False
    ltp_valid: bool = False
    exchange_ts_valid: bool = False
    volume_valid: bool = False
    bid_ask_valid: bool = False
    depth_valid: bool = False
    first_packet_ts: Optional[float] = None
    latest_packet_ts: Optional[float] = None
    packet_count: int = 0

    @property
    def is_quote_ready(self) -> bool:
        return (
            self.has_valid_packet
            and self.ltp_valid
            and self.exchange_ts_valid
        )

    @property
    def is_full_ready(self) -> bool:
        return (
            self.is_quote_ready
            and self.bid_ask_valid
            and self.depth_valid
        )


class ReadinessChecker:
    def __init__(self):
        self._lock = Lock()
        self._contracts: Dict[str, ContractReadiness] = {}
        self._packet_timestamps: Dict[str, List[float]] = {}
        self._unregistered_packet_count = 0

    @property
    def unregistered_packet_count(self) -> int:
        return self._unregistered_packet_count

    def register_contract(self, contract_id: str, security_id: int):
        with self._lock:
            if contract_id not in self._contracts:
                self._contracts[contract_id] = ContractReadiness(
                    contract_id=contract_id, security_id=security_id
                )

    def update_from_packet(self, contract_id: str, packet: dict):
        with self._lock:
            cr = self._contracts.get(contract_id)
            if cr is None:
                self._unregistered_packet_count += 1
                return

            now = time.time()
            cr.has_valid_packet = True
            cr.packet_count += 1
            cr.latest_packet_ts = now
            if cr.first_packet_ts is None:
                cr.first_packet_ts = now

            ltp = packet.get("ltp")
            cr.ltp_valid = ltp is not None and ltp > 0

            ts = packet.get("last_trade_time")
            cr.exchange_ts_valid = ts is not None and ts > 0

            vol = packet.get("last_trade_volume")
            cr.volume_valid = vol is not None and vol > 0

            bid = packet.get("bid")
            ask = packet.get("ask")
            cr.bid_ask_valid = (
                bid is not None and ask is not None
                and bid <= ask and bid > 0
            )

            depth = packet.get("bids")
            cr.depth_valid = depth is not None and len(depth) >= 1

            ts_key = f"{contract_id}|{int(now)}"
            self._packet_timestamps.setdefault(contract_id, []).append(now)
            if len(self._packet_timestamps[contract_id]) > 100:
                self._packet_timestamps[contract_id] = self._packet_timestamps[contract_id][-50:]

    def ce_pe_pair_ready(self, base_contract_id: str) -> bool:
        ce_id = f"{base_contract_id}|CE"
        pe_id = f"{base_contract_id}|PE"
        with self._lock:
            ce = self._contracts.get(ce_id)
            pe = self._contracts.get(pe_id)
            if not ce or not pe:
                return False
            return ce.is_quote_ready and pe.is_quote_ready

    def level_for_contracts(
        self,
        contract_ids: Set[str],
        strict: bool = True,
        min_complete_ratio: float = 1.0,
    ) -> ReadinessLevel:
        with self._lock:
            ready = sum(
                1 for cid in contract_ids
                if cid in self._contracts and self._contracts[cid].is_quote_ready
            )
            if strict:
                return ReadinessLevel.QUOTE_READY if ready == len(contract_ids) else ReadinessLevel.NONE
            ratio = ready / len(contract_ids) if contract_ids else 1.0
            return ReadinessLevel.QUOTE_READY if ratio >= min_complete_ratio else ReadinessLevel.NONE

    def recently_updated(self, contract_id: str, max_age: float = 5.0) -> bool:
        with self._lock:
            cr = self._contracts.get(contract_id)
            if not cr or cr.latest_packet_ts is None:
                return False
            return (time.time() - cr.latest_packet_ts) <= max_age

    def warm_contracts(self, contract_ids: Set[str]) -> Set[str]:
        with self._lock:
            return {
                cid for cid in contract_ids
                if cid in self._contracts and self._contracts[cid].packet_count >= 3
            }

    def reset_for(self, contract_ids: Set[str]):
        with self._lock:
            for cid in contract_ids:
                self._contracts.pop(cid, None)
                self._packet_timestamps.pop(cid, None)
