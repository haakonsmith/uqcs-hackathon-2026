"""A phase of play, and what happens where it collides.

Placements and marching orders are collected through the phase and carried out
in one pass at the end, which is what lets two players march into the same
territory on the same turn. Applying each as it arrived would make the game
about who typed faster.

The pass is placements, then marches. In that order because they were separate
phases in that order, and the strategy depended on it: reinforcements a player
put down were theirs to attack with the same turn, and folding the two phases
into one should not have taken that away.

Troops go on your own ground or on ground touching it. On your own they join
the garrison; next door they are an assault, and arrive the same way a march
does - so a player with nothing worth marching can still push a frontier.

Resolution is a headcount, not a dice roll:

- every order leaves its source, whether or not it arrives anywhere useful
- arrivals are bucketed per territory, per owner, and the garrison already
  standing there - reinforcements included - is just another stack
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

from protocol.rounds import BattleReport, DroppedOrder, MoveOrder, Placement, Stack, troops_to_send
from protocol.terrain import Territory, WorldMap

logger = logging.getLogger("Combat")


class IllegalMove(Exception):
    """The move breaks a rule. Carries the reason a player should be shown."""


@dataclass
class Plan:
    """Everything committed this phase, per player, in the order it was given.

    Placements live here beside the orders rather than on the board, because
    they are the same kind of thing: a promise nobody else can see until the
    phase ends. Keeping them together is also the only way to say what a
    territory has left to send, which is its garrison plus what has been placed
    on it less what has been ordered out.
    """

    orders: dict[UUID, list[MoveOrder]] = field(default_factory=lambda: defaultdict(list))
    # player -> territory -> troops promised to it, summed rather than listed:
    # placing twice on one territory is one reinforcement, not two.
    placements: dict[UUID, dict[int, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))

    def add(self, player: UUID, order: MoveOrder) -> None:
        self.orders[player].append(order)

    def place(self, player: UUID, territory: int, count: int) -> None:
        self.placements[player][territory] += count

    def clear(self, player: UUID) -> int:
        """Forget a player's plan. Returns the troops that go back into hand."""
        _ = self.orders.pop(player, None)
        return sum(self.placements.pop(player, {}).values())

    def orders_for(self, player: UUID) -> list[MoveOrder]:
        return list(self.orders.get(player, ()))

    def placements_for(self, player: UUID) -> list[Placement]:
        # Sorted by territory so a client redrawing the plan does not have the
        # order of it change under them from one answer to the next.
        return [Placement(territory=t, count=c) for t, c in sorted(self.placements.get(player, {}).items())]

    def placed(self, player: UUID, territory: int) -> int:
        """Troops promised to a territory this phase, not yet standing on it."""
        return self.placements.get(player, {}).get(territory, 0)

    def __bool__(self) -> bool:
        return any(self.orders.values()) or any(self.placements.values())


@dataclass(frozen=True)
class Resolution:
    battles: list[BattleReport]
    touched: tuple[int, ...]
    # Orders and placements the phase could not carry out. Separate from
    # `battles` because they change nothing on the board, so a client with only
    # the delta and the battle list has no way to show that they happened.
    dropped: list[DroppedOrder] = field(default_factory=list)


def sendable(board: WorldMap, plan: Plan, player: UUID, territory_id: int) -> int:
    """Troops a territory can still be ordered to march out.

    The sum itself is `troops_to_send`, shared with the client so the two ends
    cannot disagree about which orders are legal. This is only the half that
    knows where a board and a plan keep the three numbers it wants.
    """
    territory = _territory(board, territory_id)
    return troops_to_send(
        garrison=territory.soldiers,
        placed=plan.placed(player, territory_id),
        orders=plan.orders.get(player, ()),
        source=territory_id,
    )


def validate(board: WorldMap, plan: Plan, player: UUID, order: MoveOrder) -> None:
    """Check one order against the board and what is already promised."""
    source = _territory(board, order.source)
    _ = _territory(board, order.target)

    if source.owner != player:
        raise IllegalMove(f"territory {order.source} is not yours")
    if order.target not in source.neighbours:
        raise IllegalMove(f"territory {order.target} does not border {order.source}")
    if order.count < 1:
        raise IllegalMove("move at least one soldier")

    spare = sendable(board, plan, player, order.source)
    if order.count > spare:
        raise IllegalMove(f"territory {order.source} has {spare} left to send, not {order.count}")


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
    """Carry out a whole phase at once: troops land, then everybody marches."""
    touched: set[int] = set()
    dropped: list[DroppedOrder] = []
    # territory -> owner -> troops arriving
    arrivals: dict[int, dict[UUID, int]] = defaultdict(lambda: defaultdict(int))

    def drop(player: UUID, territory: int, count: int, reason: str) -> None:
        """Record troops the phase could not move, and say who lost them."""
        logger.info("dropping %d of %s at %d: %s", count, _name(player, names), territory, reason)
        dropped.append(DroppedOrder(player=str(player), name=_name(player, names), territory=territory, count=count, reason=reason))

    # Reinforcements first, so they are part of the garrison the orders below
    # march out of and fight with. Troops placed on somebody else's ground
    # never garrison it: they arrive on it, and are settled with everything
    # else that lands there.
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

    for player, player_orders in plan.orders.items():
        for order in player_orders:
            source = board.territories[order.source]
            # An order given before the source was lost is simply not carried
            # out: those troops were captured with the ground they stood on.
            if source.owner != player:
                drop(player, order.source, order.count, "no longer yours when the phase ended")
                continue
            if source.soldiers < order.count:
                drop(player, order.source, order.count, f"only {source.soldiers} were left to send")
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

    return Resolution(battles=battles, touched=tuple(sorted(touched)), dropped=dropped)


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
