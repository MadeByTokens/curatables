from dataclasses import dataclass


@dataclass
class Tag:
    name: str
    id: int | None = None
