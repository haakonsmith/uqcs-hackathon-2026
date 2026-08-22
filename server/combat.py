"""Marching orders, and what happens where they collide.

Orders are collected through the phase and resolved in one pass at the end,
which is what lets two players march into the same territory on the same turn.
Applying each order as it arrived would make the game about who typed faster.

Resolution is a headcount, not a dice roll:

- every order leaves its source, whether or not it arrives anywhere useful
- arrivals are bucketed per territory, per owner, and the garrison already
  standing there is just another stack
- one owner present: they hold the ground with everything they brought
- several: the biggest stack takes it and loses as many troops as the next
  biggest had, so winning a close fight costs nearly everything
- a tie at the top: both wipe out and the territory is left unowned, free for
  anyone to walk into next round

Kept apart from the server so the rules can be tested without a socket.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from protocol.rounds import BattleReport, MoveOrder, Stack
from protocol.terrain import Territory, WorldMap

logger = logging.getLogger("Combat")


class IllegalMove(Exception):
    """The move breaks a rule. Carries the reason a player should be shown."""


@dataclass
class Orders:
    """Everything ordered this phase, per player, in the order it was given."""

    by_player: dict[UUID, list[MoveOrder]] = field(default_factory=lambda: defaultdict(list))

    def add(self, player: UUID, order: MoveOrder) -> None:
        self.by_player[player].append(order)

    def clear(self, player: UUID) -> None:
        self.by_player.pop(player, None)

    def for_player(self, player: UUID) -> list[MoveOrder]:
        return list(self.by_player.get(player, ()))

    def committed(self, player: UUID, source: int) -> int:
        """Troops already promised out of a territory, so it cannot overdraw."""
        return sum(o.count for o in self.by_player.get(player, ()) if o.source == source)

    def __bool__(self) -> bool:
        return any(self.by_player.values())


@dataclass(frozen=True)
class Resolution:
    battles: list[BattleReport]
    touched: tuple[int, ...]


def validate(board: WorldMap, orders: Orders, player: UUID, order: MoveOrder) -> None:
    """Check one order against the board and what is already promised."""
    source = _territory(board, order.source)
    target = _territory(board, order.target)

    if source.owner != player:
        raise IllegalMove(f"territory {order.source} is not yours")
    if order.target not in source.neighbours:
        raise IllegalMove(f"territory {order.target} does not border {order.source}")
    if order.count < 1:
        raise IllegalMove("move at least one soldier")

    # Everything already ordered out counts against the garrison, or a player
    # could order the same soldiers away twice.
    spare = source.soldiers - orders.committed(player, order.source)
    if order.count > spare:
        raise IllegalMove(f"territory {order.source} has {spare} left to send, not {order.count}")
    _ = target


def place(board: WorldMap, player: UUID, territory_id: int, count: int) -> None:
    """Add troops to a territory this player owns."""
    territory = _territory(board, territory_id)
    if territory.owner != player:
        raise IllegalMove(f"territory {territory_id} is not yours")
    if count < 1:
        raise IllegalMove("place at least one soldier")
    territory.soldiers += count


def resolve(board: WorldMap, orders: Orders, names: dict[UUID, str]) -> Resolution:
    """Carry out every order at once and fight wherever they meet."""
    touched: set[int] = set()
    # territory -> owner -> troops arriving
    arrivals: dict[int, dict[UUID, int]] = defaultdict(lambda: defaultdict(int))

    for player, player_orders in orders.by_player.items():
        for order in player_orders:
            source = board.territories[order.source]
            # An order given before the source was lost is simply not carried
            # out: those troops were captured with the ground they stood on.
            if source.owner != player or source.soldiers < order.count:
                logger.debug("dropping %s from %s: source no longer holds it", order, player)
                continue
            source.soldiers -= order.count
            arrivals[order.target][player] += order.count
            touched.add(order.source)

    battles: list[BattleReport] = []
    for territory_id, incoming in arrivals.items():
        touched.add(territory_id)
        battle = _settle(board.territories[territory_id], incoming, names)
        if battle is not None:
            battles.append(battle)

    return Resolution(battles=battles, touched=tuple(sorted(touched)))


def _settle(
    territory: Territory,
    incoming: dict[UUID, int],
    names: dict[UUID, str],
) -> BattleReport | None:
    """Work out who is left standing in one territory."""
    stacks: dict[UUID | None, int] = defaultdict(int)
    if territory.soldiers > 0 or territory.owner is not None:
        stacks[territory.owner] += territory.soldiers
    for player, troops in incoming.items():
        stacks[player] += troops

    # An owner with nothing standing is not a side in a fight.
    contenders = {owner: troops for owner, troops in stacks.items() if troops > 0}

    if len(contenders) <= 1:
        owner, troops = next(iter(contenders.items()), (territory.owner, 0))
        territory.owner = owner
        territory.soldiers = troops
        return None

    ranked = sorted(contenders.items(), key=lambda item: item[1], reverse=True)
    (top_owner, top), (_, second) = ranked[0], ranked[1]
    report_stacks = [
        Stack(owner=str(owner) if owner else None, name=_name(owner, names), troops=troops) for owner, troops in ranked
    ]

    if top == second:
        # Nobody is left to hold it, so it goes back to being nobody's.
        territory.owner = None
        territory.soldiers = 0
        return BattleReport(territory=territory.id, stacks=report_stacks, winner=None, survivors=0)

    territory.owner = top_owner
    territory.soldiers = top - second
    return BattleReport(
        territory=territory.id,
        stacks=report_stacks,
        winner=str(top_owner) if top_owner else None,
        winner_name=_name(top_owner, names),
        survivors=territory.soldiers,
    )


def eliminated(board: WorldMap, player: UUID) -> bool:
    """True once a player holds no ground at all."""
    return not any(t.owner == player for t in board.territories)


def _name(owner: UUID | None, names: dict[UUID, str]) -> str:
    if owner is None:
        return "nobody"
    return names.get(owner, str(owner)[:8])


def _territory(board: WorldMap, territory_id: int) -> Territory:
    if not 0 <= territory_id < len(board.territories):
        raise IllegalMove(f"no territory {territory_id}")
    return board.territories[territory_id]
