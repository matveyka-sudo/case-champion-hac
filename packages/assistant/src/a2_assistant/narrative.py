"""Typed compatibility facade; module identity preserves monkeypatch clients."""

import sys

from .grounding import narrative as _implementation
from .grounding.narrative import ANSWER_MODES as ANSWER_MODES
from .grounding.narrative import AnswerMode as AnswerMode
from .grounding.narrative import _comparison as _comparison
from .grounding.narrative import _coverage as _coverage
from .grounding.narrative import _journey as _journey
from .grounding.narrative import _methodology as _methodology
from .grounding.narrative import _proven_observed_auxiliary as _proven_observed_auxiliary
from .grounding.narrative import _safety as _safety
from .grounding.narrative import conversational_finding as conversational_finding
from .grounding.narrative import verified_conversational_finding as verified_conversational_finding

sys.modules[__name__] = _implementation
