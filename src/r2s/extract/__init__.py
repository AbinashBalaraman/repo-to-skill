"""Extraction: turning a repository into a reviewed operation inventory.

Evidence sources, not a fallback ladder. Each answers a different question and they
corroborate rather than short-circuit:

  T0 declared dialects   -- which operations exist, and in what order
  T1 effect tracing      -- what each operation requires, and its effect class
  T2 provider catalog    -- which capabilities the repo uses at all
  T3 docs and --help     -- gates, intent, optionality
  T4 LLM proposal        -- gap-filling only, never auto-accepted
"""

from .pipeline import extract

__all__ = ["extract"]
