"""Server profiles and annotation-driven tool visibility.

``full`` (the default) is today's catalog. ``readonly`` keeps only tools whose
MCP ``readOnlyHint`` is ``True``. There are no domain mounts and no job
allowlists, so wire names stay as they are.

The annotation is the source of truth. A missing annotation, or a
``readOnlyHint`` other than ``True``, counts as a write.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.base import Tool
from fastmcp.utilities.versions import VersionSpec
from mcp.types import ToolAnnotations

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


@dataclass(frozen=True)
class Profile:
    """A named catalog: the full tool list, or the read-only subset."""

    name: str
    job: str
    readonly: bool = False


PROFILES: dict[str, Profile] = {
    profile.name: profile
    for profile in (
        Profile(
            name="full",
            job="Complete catalog: every tool, including order tools (still gated).",
        ),
        Profile(
            name="readonly",
            job="Inspect without side effects: every tool annotated readOnlyHint=True.",
            readonly=True,
        ),
    )
}


def get_profile(name: str) -> Profile:
    """Return the named profile or raise ``ValueError`` listing valid profiles."""
    key = name.strip().lower()
    if key not in PROFILES:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown profile {name!r}; valid profiles: {valid}.")
    return PROFILES[key]


def is_read_only_tool(tool: Tool | None) -> bool:
    """Return True only for a tool annotated ``readOnlyHint=True`` (fail closed)."""
    if tool is None or tool.annotations is None:
        return False
    return tool.annotations.read_only_hint is True


class ReadOnlyToolFilter(Transform):
    """Keep only tools annotated ``readOnlyHint=True``."""

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [tool for tool in tools if is_read_only_tool(tool)]

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        return tool if is_read_only_tool(tool) else None


class ReadOnlyAnnotations(Transform):
    """Annotate named synthetic discovery tools as ``readOnlyHint=True``.

    Tool Search's ``search_tools`` and Code Mode's ``search`` / ``get_schema``
    ship without annotations. They only read the catalog, so they stay visible
    under a read-only listing. ``call_tool`` and ``execute`` are not annotated:
    a write reached through them is still refused by the handler gate.
    """

    def __init__(self, names: Collection[str]) -> None:
        self.names = frozenset(names)

    def _annotate(self, tool: Tool) -> Tool:
        if tool.name not in self.names:
            return tool
        return tool.model_copy(update={"annotations": READ_ONLY_ANNOTATIONS})

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._annotate(tool) for tool in tools]

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        return None if tool is None else self._annotate(tool)
