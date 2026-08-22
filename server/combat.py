"""A phase of play, and what happens where it collides.

Placements are collected through the phase and carried out in one pass at the
end, which is what lets two players commit to the same territory on the same
turn. Applying each as it arrived would make the game about who typed faster.

Placing is the only way troops reach the board, and the only way ground changes
hands. Troops go on your own territories or on ones touching them: on your own
they join the garrison, next door they are an assault. A player therefore
spends each round's award at whichever frontier they want to push, and a
garrison already standing stays where it is.

Resolution is a headcount, not a dice roll:

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

from protocol.rounds import BattleReport, DroppedOrder, Placement, Stack
from protocol.terrain import Territory, WorldMap

logger = logging.getLogger("Combat")


class IllegalMove(Exception):
    """The move breaks a rule. Carries the reason a player should be shown."""


@dataclass
class Plan:
    """Everything a player has committed this phase, not yet on the board.

    Placements live here rather than on the board because until the phase ends
    they are a promise nobody else can see, and one the player can still tear
    up. Only when the phase resolves do they become troops.
    """

    # player -> territory -> troops promised to it, summed rather than listed:
    # placing twice on one territory is one reinforcement, not two.
    placements: dict[UUID, dict[int, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))

    def place(self, player: UUID, territory: int, count: int) -> None:
        self.placements[player][territory] += count

    def clear(self, player: UUID) -> int:
        """Forget a player's plan. Returns the troops that go back into hand."""
        return sum(self.placements.pop(player, {}).values())

    def placements_for(self, player: UUID) -> list[Placement]:
        # Sorted by territory so a client redrawing the plan does not have the
        # order of it change under them from one answer to the next.
        return [Placement(territory=t, count=c) for t, c in sorted(self.placements.get(player, {}).items())]

    def placed(self, player: UUID, territory: int) -> int:
        """Troops promised to a territory this phase, not yet standing on it."""
        return self.placements.get(player, {}).get(territory, 0)

    def __bool__(self) -> bool:
        return any(self.placements.values())


@dataclass(frozen=True)
class Resolution:
    battles: list[BattleReport]
    touched: tuple[int, ...]
    # Placements the phase could not carry out. Separate from `battles` because
    # they change nothing on the board, so a client with only the delta and the
    # battle list has no way to show that they happened.
    dropped: list[DroppedOrder] = field(default_factory=list)


def check_placement(board: WorldMap, player: UUID, territory_id: int, count: int) -> None:
    """Check troops may go on a territory, before they are promised to it.

    Nothing is written: a placement is part of the plan until the phase ends,
    so all that happens here is the refusal a player would otherwise only
    discover when their troops failed to appear.

    Reach, not ownership: troops may also be dropped on a neighbour of
    something the player holds, which resolves as an assault on it.
    """
    territory = _territory(board, territory_id)
    if territory.owner != player and not _borders(board, player, territory):
        raise IllegalMove(f"territory {territory_id} is neither yours nor next to yours")
    if count < 1:
        raise IllegalMove("place at least one soldier")


def resolve(board: WorldMap, plan: Plan, names: dict[UUID, str]) -> Resolution:
    """Carry out a whole phase at once: every placement lands together."""
    touched: set[int] = set()
    dropped: list[DroppedOrder] = []
    # territory -> owner -> troops arriving
    arrivals: dict[int, dict[UUID, int]] = defaultdict(lambda: defaultdict(int))

    def drop(player: UUID, territory: int, count: int, reason: str) -> None:
        """Record troops the phase could not land, and say who lost them."""
        logger.info("dropping %d of %s at %d: %s", count, _name(player, names), territory, reason)
        dropped.append(DroppedOrder(player=str(player), name=_name(player, names), territory=territory, count=count, reason=reason))

    # Troops placed on your own ground join its garrison outright. Placed on
    # somebody else's they never garrison it: they arrive on it, and are
    # settled against whatever else lands there.
    for player, placements in plan.placements.items():
        for territory_id, count in placements.items():
            territory = board.territories[territory_id]
            if territory.owner == player:
                territory.soldiers += count
            elif _borders(board, player, territory):
                arrivals[territory_id][player] += count
            else:
                drop(player, territory_id, count, "out of reach when the phase ended")
                continue
            touched.add(territory_id)

    battles: list[BattleReport] = []
    for territory_id, incoming in arrivals.items():
        touched.add(territory_id)
        battle = _settle(board.territories[territory_id], incoming, names)
        if battle is not None:
            battles.append(battle)

    return Resolution(battles=battles, touched=tuple(sorted(touched)), dropped=dropped)


def _settle(
    territory: Territory,
    incoming: dict[UUID, int],
    names: dict[UUID, str],
) -> BattleReport | None:
    """Work out who is left standing in one territory."""
    # Read before anything below reassigns it: this is the only moment the
    # board still knows who the ground belonged to going in.
    defender = territory.owner
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

    held_by = str(defender) if defender is not None else None
    if top == second:
        # Nobody is left to hold it, so it goes back to being nobody's.
        territory.owner = None
        territory.soldiers = 0
        return BattleReport(
            territory=territory.id,
            stacks=report_stacks,
            winner=None,
            survivors=0,
            defender=held_by,
            defender_name=_name(defender, names),
        )

    territory.owner = top_owner
    territory.soldiers = top - second
    return BattleReport(
        territory=territory.id,
        stacks=report_stacks,
        winner=str(top_owner) if top_owner else None,
        winner_name=_name(top_owner, names),
        survivors=territory.soldiers,
        defender=held_by,
        defender_name=_name(defender, names),
    )


def eliminated(board: WorldMap, player: UUID) -> bool:
    """True once a player holds no ground at all."""
    return not any(t.owner == player for t in board.territories)


def _borders(board: WorldMap, player: UUID, territory: Territory) -> bool:
    """True when the player holds something the territory touches."""
    return any(board.territories[n].owner == player for n in territory.neighbours)


def _name(owner: UUID | None, names: dict[UUID, str]) -> str:
    if owner is None:
        return "nobody"
    return names.get(owner, str(owner)[:8])


def _territory(board: WorldMap, territory_id: int) -> Territory:
    if not 0 <= territory_id < len(board.territories):
        raise IllegalMove(f"no territory {territory_id}")
    return board.territories[territory_id]
