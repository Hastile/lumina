"""Command registry.

Every document change in Lumina - a GUI drag, a keyboard shortcut, an MCP
tool call, a workflow recipe step - is one of these commands. The registry is
the single source for the RPC surface, the MCP tool list and the UI's typed
client, so a feature that exists in the GUI automatically exists for agents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from ..patch import Mutator
from .. import timebase


class CommandError(Exception):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **({"details": self.details} if self.details else {})}


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class Context:
    m: Mutator
    services: Any = None

    @property
    def doc(self) -> dict:
        return self.m.doc


@dataclass
class CommandSpec:
    name: str
    params: type[Params]
    handler: Callable[[Context, Any], Any]
    description: str
    undoable: bool = True
    category: str = ""

    def json_schema(self) -> dict:
        return self.params.model_json_schema()


@dataclass
class Registry:
    commands: dict[str, CommandSpec] = field(default_factory=dict)

    def register(self, name: str, params: type[Params], *, undoable: bool = True):
        def deco(fn: Callable[[Context, Any], Any]):
            doc = (fn.__doc__ or "").strip()
            self.commands[name] = CommandSpec(
                name=name,
                params=params,
                handler=fn,
                description=doc,
                undoable=undoable,
                category=name.split(".", 1)[0],
            )
            return fn

        return deco

    def get(self, name: str) -> CommandSpec:
        try:
            return self.commands[name]
        except KeyError:
            raise CommandError("UNKNOWN_COMMAND", f"unknown command {name!r}") from None

    def describe(self) -> list[dict]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "undoable": spec.undoable,
                "params": spec.json_schema(),
            }
            for spec in self.commands.values()
        ]


REGISTRY = Registry()
command = REGISTRY.register


# ---------------------------------------------------------------- time values

TimeValue = int | str
_TC = re.compile(r"^(\d+):(\d{1,2}):(\d{1,2})[:;](\d{1,3})$")


def resolve_time(value: TimeValue, settings: dict, *, snap: bool = True) -> int:
    """Accept ticks (int) or human forms: ``"12.5s"``, ``"375f"``, ``"00:00:12:15"``.

    Agents rarely want to compute ticks; the GUI always sends ticks.
    """
    fn, fd = settings["fps_num"], settings["fps_den"]
    if isinstance(value, bool):
        raise CommandError("BAD_TIME", "time must be ticks or a time string")
    if isinstance(value, int):
        ticks = value
    elif isinstance(value, str):
        v = value.strip()
        if v.endswith("s"):
            ticks = timebase.seconds_to_ticks(float(v[:-1]))
        elif v.endswith("f"):
            ticks = timebase.frames_to_ticks(int(v[:-1]), fn, fd)
        elif (mt := _TC.match(v)) is not None:
            h, mi, s, f = (int(g) for g in mt.groups())
            nominal = round(fn / fd)
            ticks = timebase.frames_to_ticks(((h * 60 + mi) * 60 + s) * nominal + f, fn, fd)
        elif v.lstrip("-").isdigit():
            ticks = int(v)
        else:
            raise CommandError("BAD_TIME", f"unrecognized time {value!r}; use ticks, '1.5s', '45f' or 'HH:MM:SS:FF'")
    else:
        raise CommandError("BAD_TIME", f"unrecognized time {value!r}")
    return timebase.snap_to_frame(ticks, fn, fd) if snap else ticks
