"""Envelopes that carry messages over the socket.

- `ReqFrame` carries a client request and the id to answer on
- `ResFrame` carries the answer, tagged with the id it belongs to
- `EvtFrame` carries something the server pushed, unprompted

The frame tag and the message tag are separate discriminators, so a response
body inside a request frame is rejected at parse time - the envelope enforces
direction, not just shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from protocol.actions import ClientRequest, ServerEvent, ServerResponse


@dataclass(frozen=True)
class ReqFrame:
    id: str
    body: ClientRequest
    t: Literal["req"] = "req"


@dataclass(frozen=True)
class ResFrame:
    id: str
    body: ServerResponse
    t: Literal["res"] = "res"


@dataclass(frozen=True)
class EvtFrame:
    body: ServerEvent
    t: Literal["evt"] = "evt"


Frame = Annotated[ReqFrame | ResFrame | EvtFrame, Field(discriminator="t")]

FRAME_ADAPTER: TypeAdapter[Frame] = TypeAdapter(Frame)


def parse_frame(raw: str | bytes) -> Frame:
    return FRAME_ADAPTER.validate_json(raw)


def dump_frame(frame: Frame) -> str:
    """Serialise with the adapter that parses, so the tags always agree."""
    return FRAME_ADAPTER.dump_json(frame).decode()
