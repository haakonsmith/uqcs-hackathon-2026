"""The wire's own bookkeeping: that every message can actually travel.

A message is declared in one place and admitted to a union in another, and
nothing connects the two. Left out of the union, a request serialises fine and
is rejected at the far end as an unknown tag - which reads as the server
ignoring the player rather than as a missing line in a type alias.
"""

from __future__ import annotations

import typing

import pytest

from protocol import actions
from protocol.actions import EVENT_ADAPTER, REQUEST_ADAPTER, RESPONSE_ADAPTER
from protocol.core import Request, response_types


def _union_members(alias: object) -> set[type]:
    """The concrete classes behind an `Annotated[A | B, Field(...)]` alias."""
    return set(typing.get_args(typing.get_args(alias)[0]))


def _declared(base: type) -> set[type]:
    return {
        value
        for value in vars(actions).values()
        if isinstance(value, type) and issubclass(value, base) and value is not base
    }


def test_every_request_is_in_the_client_union() -> None:
    missing = _declared(Request) - _union_members(actions.ClientRequest)
    assert not missing, f"unreachable requests: {sorted(c.__name__ for c in missing)}"


@pytest.mark.parametrize(
    ("alias", "adapter"),
    [
        (actions.ClientRequest, REQUEST_ADAPTER),
        (actions.ServerResponse, RESPONSE_ADAPTER),
        (actions.ServerEvent, EVENT_ADAPTER),
    ],
)
def test_every_union_member_round_trips_through_its_adapter(alias: object, adapter: object) -> None:
    """Each member's tag has to resolve back to the member that carries it."""
    for member in _union_members(alias):
        tag_field = "action" if issubclass(member, Request) else "kind"
        tag = getattr(member, tag_field, None) or member.__dataclass_fields__[tag_field].default
        assert isinstance(tag, str), member.__name__
        assert tag, member.__name__


def test_tags_are_unique_within_each_union() -> None:
    for alias, field_name in (
        (actions.ClientRequest, "action"),
        (actions.ServerResponse, "kind"),
        (actions.ServerEvent, "kind"),
    ):
        tags = [getattr(m, field_name) for m in _union_members(alias)]
        assert len(tags) == len(set(tags)), f"duplicate discriminators: {sorted(tags)}"


def test_every_request_names_a_response_it_can_actually_get() -> None:
    """`response_types` is what `Connection.send` checks the answer against."""
    answerable = _union_members(actions.ServerResponse)
    for request in _declared(Request):
        declared = set(response_types(request))
        assert declared, request.__name__
        stray = declared - answerable
        assert not stray, f"{request.__name__} expects {sorted(c.__name__ for c in stray)}, which no server sends"


def test_a_submission_waits_longer_than_an_ordinary_request() -> None:
    """The judge runs a sandbox before it can answer; a placement is a dict write."""
    assert actions.SubmitSolution.timeout_seconds > actions.PlaceTroops.timeout_seconds
