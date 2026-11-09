"""Canonical A2 models. Submodules are the stable import boundary."""

from .api import AnalysisRequest, ErrorEnvelope, Job, JobAccepted
from .dataset import AcceptedData, Evidence, SnapshotBundle, Source
from .journeys import JourneyRequest, JourneyResult
from .profiles import ProfileConfig, TaskCase, TaskSet
from .regions import StudyArea
from .results import ProfileResult
from .scenarios import Comparison, ScenarioRequest

__all__ = [
    "AcceptedData",
    "AnalysisRequest",
    "Comparison",
    "ErrorEnvelope",
    "Evidence",
    "Job",
    "JobAccepted",
    "JourneyRequest",
    "JourneyResult",
    "ProfileConfig",
    "ProfileResult",
    "ScenarioRequest",
    "SnapshotBundle",
    "Source",
    "StudyArea",
    "TaskCase",
    "TaskSet",
]
