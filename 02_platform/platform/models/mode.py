from enum import Enum, auto


class Mode(Enum):
    LIVE = auto()
    PAPER = auto()
    BACKTEST = auto()

    def __str__(self) -> str:
        return self.name.lower()
