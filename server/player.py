from dataclasses import dataclass
from uuid import UUID

Position = tuple[int, int]


@dataclass
class Player:
    id: UUID

    def __init__(self, id: UUID):
        self.id: UUID = id
