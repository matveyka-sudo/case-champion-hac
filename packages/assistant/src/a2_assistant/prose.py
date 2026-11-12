"""Typed compatibility facade; module identity preserves monkeypatch clients."""

import sys

from .grounding import prose as _implementation
from .grounding.prose import _ABSENT as _ABSENT
from .grounding.prose import _CAUSAL as _CAUSAL
from .grounding.prose import _FORECAST as _FORECAST
from .grounding.prose import _NUMERAL as _NUMERAL
from .grounding.prose import _ORDER as _ORDER
from .grounding.prose import _PROMOTION as _PROMOTION
from .grounding.prose import _PROSE_RULES as _PROSE_RULES
from .grounding.prose import ProseLimits as ProseLimits
from .grounding.prose import ProseRejected as ProseRejected
from .grounding.prose import _check_qualitative as _check_qualitative
from .grounding.prose import _digest as _digest
from .grounding.prose import _display as _display
from .grounding.prose import _GroundedExplanation as _GroundedExplanation
from .grounding.prose import _locally_limited as _locally_limited
from .grounding.prose import _source_digest as _source_digest
from .grounding.prose import _Witness as _Witness
from .grounding.prose import approved_finding as approved_finding
from .grounding.prose import fact_placeholder as fact_placeholder
from .grounding.prose import generated_finding as generated_finding
from .grounding.prose import prose_limits as prose_limits
from .grounding.prose import seal_explanation as seal_explanation

sys.modules[__name__] = _implementation
