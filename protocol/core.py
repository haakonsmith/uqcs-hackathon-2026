"""The typed request/response primitive, with no knowledge of concrete messages.

`Request[R]` names the response type a request expects. `R` is phantom - never
stored on an instance - so a send function can be typed `(Request[R]) -> R` and
every call site gets the narrow response type without annotating it.

This module sits below `actions`, which declares the concrete messages, which
in turn sits below `frames`, which wraps them for the wire.
"""

from __future__ import annotations

import types
import typing
from dataclasses import dataclass
from typing import ClassVar

# Bump whenever a change to the messages in `actions` or `rounds` would stop an
# older peer reading them: a field removed or renamed, a required field added, a
# type narrowed. An optional field with a default does not need one.
PROTOCOL_VERSION = 1

# What a peer that predates the handshake reports, being the default for an
# absent field.
UNVERSIONED = 0


class ProtocolError(Exception):
    """The peer answered with something the request never asked for."""


@dataclass(frozen=True)
class Request[R]:
    """R is the response type this request expects. Never stored.

    `timeout_seconds` is how long the client waits for the answer. Per request
    because a placement is a dictionary write and a submission runs a stranger's
    code in a sandbox.
    """

    timeout_seconds: ClassVar[float] = 10.0


# Memoised by hand rather than with functools.cache, whose Hashable bound does
# not accept `type[Request[R]]` under a strict checker.
_RESPONSE_TYPES: dict[type, tuple[type, ...]] = {}


def response_types(cls: type) -> tuple[type, ...]:
    """The runtime counterpart of `R`, read back off the `Request[...]` base.

    Phantom parameters leave a trace in the original bases, so one declaration
    feeds both the type checker and the runtime check in `Connection.send()`,
    and the two cannot drift apart.
    """
    cached = _RESPONSE_TYPES.get(cls)
    if cached is not None:
        return cached

    # The introspection APIs are typed tuple[Any, ...] - anything writable in a
    # type expression. Widening to object keeps that Any from leaking; the
    # isinstance filter below is what earns the tuple[type, ...] return.
    bases: tuple[object, ...] = types.get_original_bases(cls)
    base = next((b for b in bases if typing.get_origin(b) is Request), None)
    if base is None:
        raise TypeError(f"{cls.__name__} does not subclass Request[...]")

    declared: tuple[object, ...] = typing.get_args(base)
    if len(declared) != 1:
        raise TypeError(f"{cls.__name__} must name exactly one response type")

    # A union yields its members; a lone type yields nothing, so use it as-is.
    members: tuple[object, ...] = typing.get_args(declared[0]) or declared
    resolved = tuple(member for member in members if isinstance(member, type))
    if len(resolved) != len(members):
        raise TypeError(f"{cls.__name__} declares a response that is not a class")

    _RESPONSE_TYPES[cls] = resolved
    return resolved
