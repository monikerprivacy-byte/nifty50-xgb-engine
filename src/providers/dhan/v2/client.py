"""Dhan v2 WebSocket client with health state machine and connection generation guard."""

from __future__ import annotations

import asyncio
import json
import logging
from enum import Enum
from typing import Callable, Optional

from .protocol import (
    DHAN_WS_URL,
    EXCHANGE_SEGMENT,
    MODE_TO_REQ,
    MODE_TO_UNSUB_REQ,
    QUOTE,
)
from .decoder import parse_feed_response
from .subscriptions import build_subscribe_json, build_unsubscribe_json

logger = logging.getLogger(__name__)

# Minimum distinct instruments that must be observed before STREAMING_HEALTHY
MIN_HEALTHY_INSTRUMENTS = 2


class HealthState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    SUBSCRIPTIONS_SENT = "SUBSCRIPTIONS_SENT"
    FIRST_PACKET_RECEIVED = "FIRST_PACKET_RECEIVED"
    STREAMING_HEALTHY = "STREAMING_HEALTHY"
    STALLED = "STALLED"
    RECONNECTING = "RECONNECTING"


class DhanWebSocketClient:
    def __init__(
        self,
        client_id: str,
        access_token: str,
        mode: int = QUOTE,
        url: str = DHAN_WS_URL,
        exchange_segment: str = EXCHANGE_SEGMENT,
        min_healthy_instruments: int = MIN_HEALTHY_INSTRUMENTS,
    ):
        self.client_id = client_id
        self.access_token = access_token
        self._token_masked = (
            access_token[:8] + "..." + access_token[-4:]
            if len(access_token) > 12
            else "***masked***"
        )
        self.mode = mode
        self.url = url
        self.exchange_segment = exchange_segment

        self._ws = None
        self._desired_subscription_ids: set[int] = set()
        self._sent_subscription_ids: set[int] = set()
        self._seen_subscription_ids: set[int] = set()
        self._seen_generation: int = 0
        self._pending_subscription_ids: set[int] = set()
        self._running = False
        self._reconnect_count = 0
        self._callback = None
        self._conn_lock = asyncio.Lock()
        self._sub_lock = asyncio.Lock()
        self._health: HealthState = HealthState.DISCONNECTED
        self._connection_generation = 0
        self._min_healthy_instruments = min_healthy_instruments

    @property
    def health(self) -> HealthState:
        return self._health

    @property
    def connection_generation(self) -> int:
        return self._connection_generation

    def _set_health(self, state: HealthState):
        old = self._health
        self._health = state
        if old != state:
            logger.info(f"Health: {old.value} -> {state.value}")

    def _generation_guard(self, generation: int) -> bool:
        if generation != self._connection_generation:
            logger.debug(
                f"Ignoring stale callback from generation {generation}, "
                f"current is {self._connection_generation}"
            )
            return False
        return True

    def set_callback(self, callback: Optional[Callable]):
        self._callback = callback

    def add_subscription(self, security_id: int):
        """Register a security ID for subscription (registers intent, wire send happens in subscribe/connect)."""
        self._desired_subscription_ids.add(security_id)

    def remove_subscription(self, security_id: int):
        """Remove a security ID from desired subscriptions."""
        self._desired_subscription_ids.discard(security_id)
        self._sent_subscription_ids.discard(security_id)
        self._seen_subscription_ids.discard(security_id)

    async def _close_existing_socket(self):
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def connect(self):
        try:
            import websockets as _ws_mod
        except ImportError:
            raise ImportError("websockets package required: pip install websockets")

        self._connection_generation += 1
        gen = self._connection_generation

        self._set_health(HealthState.CONNECTING)
        await self._close_existing_socket()

        self._sent_subscription_ids.clear()
        self._seen_subscription_ids.clear()
        self._seen_generation = gen
        self._pending_subscription_ids.clear()

        ws_url = (
            f"{self.url}"
            f"?version=2"
            f"&token={self.access_token}"
            f"&clientId={self.client_id}"
            f"&authType=2"
        )
        self._ws = await _ws_mod.connect(
            ws_url,
            origin="https://dhanhq.co",
            ping_interval=10,
            ping_timeout=5,
            close_timeout=5,
            max_size=2**20,
        )

        if not self._generation_guard(gen):
            await self._close_existing_socket()
            return

        self._reconnect_count = 0
        self._set_health(HealthState.CONNECTED)
        logger.info(f"WebSocket connected (generation {gen})")

        if self._desired_subscription_ids:
            ids = list(self._desired_subscription_ids)
            mode = self.mode
            request_code = MODE_TO_REQ.get(mode)
            if request_code is not None:
                chunks = [ids[i : i + 100] for i in range(0, len(ids), 100)]
                async with self._sub_lock:
                    if not self._generation_guard(gen):
                        await self._close_existing_socket()
                        return
                    for chunk in chunks:
                        msg = build_subscribe_json(chunk, request_code)
                        await self._ws.send(msg)
                        self._sent_subscription_ids.update(chunk)
                logger.info(
                    f"Replayed {len(ids)} desired subscriptions on new socket "
                    f"(mode {mode}, gen {gen})"
                )
                self._set_health(HealthState.SUBSCRIPTIONS_SENT)

    async def disconnect(self):
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._set_health(HealthState.DISCONNECTED)

    async def subscribe(self, security_ids: list[int], mode: Optional[int] = None):
        mode = mode or self.mode
        if not security_ids:
            return

        async with self._sub_lock:
            new_ids = [sid for sid in security_ids if sid not in self._sent_subscription_ids]
            if not new_ids:
                return

            self._desired_subscription_ids.update(new_ids)
            request_code = MODE_TO_REQ.get(mode)
            if request_code is None:
                logger.warning(f"No request code for mode {mode}")
                return
            chunks = [new_ids[i : i + 100] for i in range(0, len(new_ids), 100)]
            for chunk in chunks:
                msg = build_subscribe_json(chunk, request_code)
                await self._ws.send(msg)
                self._sent_subscription_ids.update(chunk)

            logger.info(f"Subscribed to {len(new_ids)} securities (mode {mode})")
            self._set_health(HealthState.SUBSCRIPTIONS_SENT)

    async def unsubscribe(self, security_ids: list[int]):
        async with self._sub_lock:
            ids = [sid for sid in security_ids if sid in self._sent_subscription_ids]
            if not ids:
                return

            unsub_code = MODE_TO_UNSUB_REQ.get(self.mode)
            if unsub_code is None:
                return
            chunks = [ids[i : i + 100] for i in range(0, len(ids), 100)]
            for chunk in chunks:
                msg = build_unsubscribe_json(chunk, unsub_code)
                await self._ws.send(msg)
                self._sent_subscription_ids.difference_update(chunk)
                self._desired_subscription_ids.difference_update(chunk)

    def notify_packet_received(self, security_id: int, gen: int, valid: bool = True):
        if not self._generation_guard(gen):
            return

        if gen != self._seen_generation:
            logger.warning(
                f"seen_generation mismatch: notify gen={gen}, seen_gen={self._seen_generation}"
            )
            return

        # Only count semantically valid, properly decoded packets
        if not valid:
            return

        # Only count packets from instruments within desired/sent universe
        if security_id not in self._desired_subscription_ids:
            logger.debug(f"Packet from unsubscribed security {security_id} — not counting")
            return

        self._seen_subscription_ids.add(security_id)

        # Health progression
        if self._health in (HealthState.SUBSCRIPTIONS_SENT, HealthState.CONNECTED):
            self._set_health(HealthState.FIRST_PACKET_RECEIVED)

        # Only transition to STREAMING_HEALTHY when minimum instruments observed
        if len(self._seen_subscription_ids) >= self._min_healthy_instruments:
            if self._health != HealthState.STREAMING_HEALTHY:
                self._set_health(HealthState.STREAMING_HEALTHY)
        else:
            logger.info(
                f"Waiting for {self._min_healthy_instruments - len(self._seen_subscription_ids)} "
                f"more instruments before STREAMING_HEALTHY"
            )

    def notify_stalled(self):
        self._set_health(HealthState.STALLED)

    def notify_reconnecting(self):
        self._set_health(HealthState.RECONNECTING)

    async def run(self, callback=None):
        if callback:
            self._callback = callback

        self._running = True

        while self._running:
            gen = self._connection_generation + 1
            try:
                await self.connect()
                gen = self._connection_generation
                async for message in self._ws:
                    if not self._generation_guard(gen):
                        break
                    if isinstance(message, bytes):
                        records = parse_feed_response(message)
                        if records:
                            for r in records:
                                sid = r.get("security_id")
                                packet_valid = (
                                    sid is not None
                                    and "error" not in r
                                    and r.get("response_code") is not None
                                )
                                if sid is not None:
                                    self.notify_packet_received(sid, gen, valid=packet_valid)
                        if self._callback and records:
                            await self._callback(records)
                    elif isinstance(message, str):
                        try:
                            data = json.loads(message)
                            logger.debug(f"WS text: {data}")
                        except json.JSONDecodeError:
                            logger.debug(f"WS text: {message[:100]}")
                    else:
                        logger.debug(f"WS unknown type: {type(message)}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"WebSocket error (gen {gen}): {e}")
                self.notify_reconnecting()
                if not self._running:
                    break
                await self._reconnect()

    async def _reconnect(self):
        self._reconnect_count += 1
        delay = min(1.0 * (2 ** (self._reconnect_count - 1)), 64.0)
        logger.info(f"Reconnecting in {delay:.1f}s (attempt {self._reconnect_count})")
        await asyncio.sleep(delay)

    async def _run_forever(self):
        """Read messages on the current connection until it drops or stop()."""
        gen = self._connection_generation
        async for message in self._ws:
            if not self._generation_guard(gen):
                break
            if isinstance(message, bytes):
                records = parse_feed_response(message)
                if records:
                    for r in records:
                        sid = r.get("security_id")
                        packet_valid = (
                            sid is not None
                            and "error" not in r
                            and r.get("response_code") is not None
                        )
                        if sid is not None:
                            self.notify_packet_received(sid, gen, valid=packet_valid)
                    if self._callback:
                        await self._callback(records)

    async def stop(self):
        self._running = False
        await self.disconnect()
