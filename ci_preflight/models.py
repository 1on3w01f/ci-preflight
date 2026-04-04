from dataclasses import dataclass, field
from typing import List


@dataclass
class ChangeSet:
    """
    Represents what changed in a commit or PR.
    This is the raw input the engine reasons over.
    """
    changed_files: List[str] = field(default_factory=list)

    def has_file(self, filename: str) -> bool:
        return filename in self.changed_files

    def files_matching(self, suffix: str) -> List[str]:
        return [f for f in self.changed_files if f.endswith(suffix)]

    def has_any(self, filenames: List[str]) -> bool:
        return any(f in self.changed_files for f in filenames)


@dataclass
class Signal:
    """
    A meaningful observation extracted from the ChangeSet.
    Signals are the evidence the risk engine reasons with.
    """
    id: str
    description: str


@dataclass
class Prediction:
    """
    The engine's output: a predicted failure with context and a recommendation.
    This is what the engineer sees.
    """
    failure_type: str
    violated_contract: str
    signals: List[Signal]
    confidence: float          # 0.0 → 1.0
    impact_stage: str          # e.g. build, test, validate, deploy
    recommendation: str

    def severity(self) -> str:
        if self.confidence >= 0.8:
            return "HIGH"
        elif self.confidence >= 0.5:
            return "MEDIUM"
        return "LOW"
