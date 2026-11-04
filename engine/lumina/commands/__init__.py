"""Importing this package registers every command with ``REGISTRY``."""

from . import assets, audio, captions, edit_tools, effects, media, nest, interchange, keyframes, motion, project, shorts, timeline  # noqa: F401
from .registry import REGISTRY, CommandError
from . import text_editing  # noqa: F401
from . import shots, shorts_fx  # noqa: F401

__all__ = ["REGISTRY", "CommandError"]
