"""Xiaohongshu platform adapter."""

from src.platforms.xhs.adapter import XhsAdapter
from src.platforms.xhs.resolver import XhsTarget, parse_xhs_target, resolve_target

__all__ = ["XhsAdapter", "XhsTarget", "parse_xhs_target", "resolve_target"]
