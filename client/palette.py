"""Colours, and which side flies which one. Client-side only.

The server deals territories to `Player.id` UUIDs and says nothing about
colour: a side's colour is not part of the rules, so each client picks its own.
That is deliberate rather than a shortcut - the one border a player has to find
at a glance is their own, and "mine" is only knowable inside the client doing
the drawing, so the local player is always the allied blue and everyone else is
an enemy colour. Two clients showing the same enemy in different reds disagree
about nothing that matters.

Layered below `render`, which imports the colours and the assignment from here
and turns them into escape sequences.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from protocol import LobbyPlayer, terrain

Color = tuple[int, int, int]

# Whoever is holding this keyboard. Blue reads as "us" in every war map ever
# drawn, and nothing on the board's land palette is close to it.
ALLIED: Color = (96, 168, 244)

# Everyone else, in this order. Front-loaded with the ones hardest to confuse
# with each other, because a lobby is four players far more often than seven.
#
# The order is measured rather than judged by eye: each colour is the one that
# stays furthest from every colour already in use, taking the worst case across
# normal, protanopic and deuteranopic vision. Sorting by eye put crimson first
# and iron fourth, which reads well enough in full colour but leaves the first
# four only 19 points apart in CIE Lab for a red-green colour-blind player -
# around 8% of men. This order leaves them 30 apart.
HOSTILE: tuple[Color, ...] = (
    (224, 170, 58),  # ochre
    (196, 200, 208),  # iron
    (212, 62, 58),  # crimson
    (198, 100, 182),  # plum
    (198, 106, 44),  # rust
    (150, 174, 72),  # olive
)


def blend(top: Color, bottom: Color, alpha: float) -> Color:
    """`top` laid over `bottom` at `alpha` opacity.

    A terminal cell has one background colour and no alpha channel, so
    transparency has to be mixed here and emitted as a single flat colour.
    """
    rest = 1.0 - alpha
    return (
        round(top[0] * alpha + bottom[0] * rest),
        round(top[1] * alpha + bottom[1] * rest),
        round(top[2] * alpha + bottom[2] * rest),
    )


@dataclass(frozen=True)
class Factions:
    """Who holds what, and the colour their frontier is drawn in.

    A side is an index rather than a player id, so the renderer indexes a list
    per cell instead of hashing a UUID. Side 0 is the local player whenever
    they hold ground, which is also the order the legend lists.

    A snapshot of ownership as the board arrived: call `assign` again once
    territories start changing hands.
    """

    side_of: dict[int, int]  # territory id -> side
    colors: list[Color]  # side -> frontier colour
    names: list[str]  # side -> the player's name
    you: int | None  # the local player's side, if they were dealt anything

    @classmethod
    def assign(cls, world: terrain.WorldMap, me: str, roster: Sequence[LobbyPlayer] = ()) -> Factions:
        """Hand out a colour per player holding ground on `world`.

        `me` is the id from the join, and `roster` the lobby the game started
        with - the board carries ids but no names, so the legend needs both.
        """
        names = {player.id: player.name for player in roster}
        # Stable, and False sorts first: the local player leads, everyone else
        # keeps the order they first appear on the board in.
        holders = sorted((str(owner) for owner in world.owners), key=lambda held: held != me)

        colors: list[Color] = []
        hostiles = 0
        for held in holders:
            if held == me:
                colors.append(ALLIED)
            else:
                colors.append(HOSTILE[hostiles % len(HOSTILE)])
                hostiles += 1

        sides = {held: side for side, held in enumerate(holders)}
        return cls(
            side_of={t.id: sides[str(t.owner)] for t in world.territories if t.owner is not None},
            colors=colors,
            names=[names.get(held, f"player {held[:4]}") for held in holders],
            you=sides.get(me),
        )

    @property
    def count(self) -> int:
        return len(self.colors)

    def label(self, side: int) -> str:
        """A side's name as the legend and the status bar say it."""
        return f"{self.names[side]} (you)" if side == self.you else self.names[side]
