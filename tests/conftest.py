import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from ddos.engine.dice import Roller


class TruccatoRoller(Roller):
    """Roller con i d20 pilotati: serve a testare 1 e 20 naturali."""

    def __init__(self, seed: str, d20_values):
        super().__init__(seed)
        self._queue = list(d20_values)

    def d20(self) -> int:
        return self._queue.pop(0) if self._queue else super().d20()
