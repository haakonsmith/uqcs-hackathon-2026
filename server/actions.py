from dataclasses import dataclass
from typing import Annotated, Literal, assert_never

from pydantic import Field, TypeAdapter


@dataclass(frozen=True)
class Join:
    room: str
    action: Literal["join"] = "join"


@dataclass(frozen=True)
class Echo:
    action: Literal["echo"] = "echo"


# Module scope on purpose: inside a function this is an ordinary local, not a
# type alias, so TypeAdapter has no type expression to bind and degrades to
# TypeAdapter[Unknown].
ServerAction = Annotated[Join | Echo, Field(discriminator="action")]

# Built once - TypeAdapter compiles a validator, so per-call construction would
# redo that work on every message.
ACTION_ADAPTER: TypeAdapter[ServerAction] = TypeAdapter(ServerAction)


def parse_action(raw: str) -> ServerAction:
    return ACTION_ADAPTER.validate_json(raw)
