"""Abstract base strategy — all strategies implement scan() and describe()."""
from abc import ABC, abstractmethod


class BaseStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy identifier."""
        ...

    @abstractmethod
    def scan_candidates(self) -> list[str]:
        """Return list of symbol strings worth analyzing today."""
        ...

    def describe(self) -> str:
        return f"Strategy: {self.name}"
