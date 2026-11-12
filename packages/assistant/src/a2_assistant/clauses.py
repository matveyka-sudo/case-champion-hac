"""Typed compatibility facade; module identity preserves monkeypatch clients."""

import sys

from .grounding import clauses as _implementation
from .grounding.clauses import _PRESENTATION as _PRESENTATION
from .grounding.clauses import ClauseId as ClauseId
from .grounding.clauses import _FindingSelection as _FindingSelection
from .grounding.clauses import _ModelSelection as _ModelSelection
from .grounding.clauses import factual_clause as factual_clause
from .grounding.clauses import legacy_presentation as legacy_presentation

sys.modules[__name__] = _implementation
