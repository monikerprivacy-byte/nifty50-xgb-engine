import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Optional

from ...universe.resolver import UniverseResolver
from ...storage.schema import ContractIdentity

logger = logging.getLogger(__name__)


class SubscriptionEngine:
    def __init__(
        self,
        db_manager,
        ws_client,
        universe_resolver: Optional[UniverseResolver] = None,
        refresh_interval_minutes: int = 5,
    ):
        self.db = db_manager
        self.ws = ws_client
        self.resolver = universe_resolver or UniverseResolver(db_manager)
        self.refresh_interval = refresh_interval_minutes

        self._current_snapshot_id: Optional[str] = None
        self._contract_to_security_id: dict[str, int] = {}
        self._security_id_to_contract: dict[int, str] = {}
        self._last_refresh: Optional[datetime] = None
        self._running = False
        self._refresh_task: Optional[asyncio.Task] = None

    async def start(self):
        self._running = True
        self._refresh_universe()
        self._refresh_task = asyncio.create_task(self._periodic_refresh())

    async def stop(self):
        self._running = False
        if self._refresh_task:
            self._refresh_task.cancel()

    async def _periodic_refresh(self):
        while self._running:
            await asyncio.sleep(self.refresh_interval * 60)
            try:
                self._refresh_universe()
            except Exception as e:
                logger.error(f"Universe refresh failed: {e}")

    def _refresh_universe(self):
        now = datetime.now()
        snapshot = self.resolver.resolve_and_save(now)
        self._current_snapshot_id = snapshot.universe_snapshot_id
        self._last_refresh = now

        contracts = snapshot.selected_contracts
        security_ids = self._resolve_security_ids(contracts)

        current_ids = set(self._security_id_to_contract.keys())
        new_ids = set(security_ids.values())
        to_subscribe = list(new_ids - current_ids)
        to_unsubscribe = list(current_ids - new_ids)

        if to_unsubscribe:
            for sid in to_unsubscribe:
                ci = self._security_id_to_contract.pop(sid, None)
                if ci:
                    self._contract_to_security_id.pop(ci, None)

        if to_subscribe:
            id_map = {v: k for k, v in security_ids.items()}
            for sid in to_subscribe:
                ci = id_map[sid]
                self._contract_to_security_id[ci] = sid
                self._security_id_to_contract[sid] = ci

        logger.info(
            f"Universe refreshed: {len(to_subscribe)} added, "
            f"{len(to_unsubscribe)} removed, "
            f"{len(self._contract_to_security_id)} total contracts"
        )

        return to_subscribe

    def _resolve_security_ids(
        self, contracts: list[str]
    ) -> dict[str, int]:
        conn = self.db.get_reader()
        result: dict[str, int] = {}

        for ci_str in contracts:
            try:
                ci = ContractIdentity.from_canonical(ci_str)
            except (ValueError, IndexError):
                continue

            row = conn.execute(
                """SELECT security_id FROM instrument_master_history
                   WHERE symbol = ?
                     AND expiry_date = ?
                     AND strike_price = ?
                     AND option_type = ?
                     AND instrument_type IN ('OPTSTK', 'OPTIDX')
                     AND is_current = true
                   LIMIT 1""",
                [ci.underlying, ci.expiry_date, ci.strike_price, ci.option_type],
            ).fetchone()

            if row:
                result[ci_str] = int(row[0])

        return result

    def get_current_contracts(self) -> dict[str, int]:
        return dict(self._contract_to_security_id)

    def get_current_snapshot_id(self) -> Optional[str]:
        return self._current_snapshot_id
