"""Typed compatibility facade; module identity preserves monkeypatch clients."""

import sys

from .context import focus as _implementation
from .context.focus import _TOPIC_WORDS as _TOPIC_WORDS
from .context.focus import _WORD as _WORD
from .context.focus import SavedFact as SavedFact
from .context.focus import _by_question as _by_question
from .context.focus import _focused as _focused
from .context.focus import _overview as _overview
from .context.focus import _words as _words
from .context.focus import context_facts as context_facts
from .context.focus import focused_metrics as focused_metrics
from .context.focus import focused_scores as focused_scores

sys.modules[__name__] = _implementation
