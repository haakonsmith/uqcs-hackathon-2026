from uuid import UUID
from dataclasses import dataclass

Position = tuple[int, int]

@dataclass
class Player:
    id: UUID

    def __init__(self, id: UUID):
        self.id: UUID = id