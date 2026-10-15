"""Importing this package registers every command with ``REGISTRY``."""

from . import audio, effects, interchange, keyframes, motion, project, timeline  # noqa: F401
from .registry import REGISTRY, CommandError

__all__ = ["REGISTRY", "CommandError"]
