"""Importing this package registers every command with ``REGISTRY``."""

from . import effects, interchange, motion, project, timeline  # noqa: F401
from .registry import REGISTRY, CommandError

__all__ = ["REGISTRY", "CommandError"]
