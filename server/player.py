from dataclasses import dataclass
from uuid import UUID

Position = tuple[int, int]


@dataclass
class Player:
    id: UUID
    name: str = "Player"
    # Raised in the waiting room; the game starts when every player has.
    ready: bool = False
