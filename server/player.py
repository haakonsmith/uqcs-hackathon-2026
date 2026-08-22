from uuid import UUID

Position = tuple[int, int]


class Player:
    def __init__(self, id: UUID):
        self.id: UUID = id
