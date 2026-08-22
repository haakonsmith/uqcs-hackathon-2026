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


class ProtocolError(Exception):
    """The peer answered with something the request never asked for."""


@dataclass(frozen=True)
class Request[R]:
    """R is the response type this request expects. Never stored."""


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

    (base,) = (b for b in types.get_original_bases(cls) if typing.get_origin(b) is Request)
    (declared,) = typing.get_args(base)
    # A union yields its members; a lone type yields nothing, so use it as-is.
    resolved = typing.get_args(declared) or (declared,)
    _RESPONSE_TYPES[cls] = resolved
    return resolved
