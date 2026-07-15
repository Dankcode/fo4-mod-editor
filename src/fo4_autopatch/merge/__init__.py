"""Source-retaining override patch planning and gated merge execution."""

from .merger import execute, plan_merge

__all__ = ["execute", "plan_merge"]
