"""Dhan v2 subscription message builders."""

from __future__ import annotations

import json

from .protocol import EXCHANGE_SEGMENT


def build_subscribe_json(security_ids: list[int], request_code: int) -> str:
    return json.dumps({
        "RequestCode": request_code,
        "InstrumentCount": len(security_ids),
        "InstrumentList": [
            {
                "ExchangeSegment": EXCHANGE_SEGMENT,
                "SecurityId": str(sid),
            }
            for sid in security_ids
        ],
    })


def build_unsubscribe_json(security_ids: list[int], request_code: int) -> str:
    return json.dumps({
        "RequestCode": request_code,
        "InstrumentCount": len(security_ids),
        "InstrumentList": [
            {
                "ExchangeSegment": EXCHANGE_SEGMENT,
                "SecurityId": str(sid),
            }
            for sid in security_ids
        ],
    })
