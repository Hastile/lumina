"""Importing this package registers every command with ``REGISTRY``."""

from . import interchange, project, timeline  # noqa: F401
from .registry import REGISTRY, CommandError

__all__ = ["REGISTRY", "CommandError"]
