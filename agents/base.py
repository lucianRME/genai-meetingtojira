# agents/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, Any

class Agent(ABC):
    name: str = "agent"

    @abstractmethod
    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def on_error(self, err: Exception, state: Dict[str, Any]) -> Dict[str, Any]:
        raise err