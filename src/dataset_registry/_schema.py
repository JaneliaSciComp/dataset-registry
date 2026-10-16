from typing import Any

from janelia_pathlib import from_janelia_uri, to_janelia_uri
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class FreeformMetadata(BaseModel):
    """The default, project-agnostic shape for `Sample`/`Representation` `metadata`.

    Accepts any keys with no validation -- the registry's own fallback when
    no project-specific `sample_metadata_model`/`representation_metadata_model`
    is given to `Registry.__init__`. A project that wants validated fields
    passes its own `BaseModel` subclass there instead.
    """

    model_config = ConfigDict(extra="allow")


class RegistryInfo(BaseModel):
    """Registry-level settings, read from an optional `registry.toml` at the registry root.

    Entirely optional: a registry with no `registry.toml` still works with
    every other function in this package -- see `load_registry_info`'s
    fallback behavior.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    """Human-readable display title, e.g. for the `browse` UI. Falls back to
    a title derived from the registry root's directory name when unset."""


class Sample(BaseModel):
    """A named, version-controlled entity that groups one or more representations of data."""

    model_config = ConfigDict(
        extra="forbid", validate_assignment=True, arbitrary_types_allowed=True
    )

    id: str
    """Stable identity, immutable once set -- unaffected by renaming `name`."""

    name: str
    """Display name. Free to change without affecting `id`."""

    description: str | None = None
    metadata: Any = Field(default_factory=FreeformMetadata)
    """Always a `BaseModel` instance: `FreeformMetadata` (accepts any keys)
    unless `Registry.__init__` was given a `sample_metadata_model`, in which
    case it's an instance of that model instead. Typed `Any` rather than a
    `BaseModel | dict` union -- pydantic-core's union serializer doesn't
    recurse into an arbitrary nested `BaseModel` correctly, silently
    dropping its fields on write; `Any` serializes a runtime `BaseModel`
    value as-is."""


class Representation(BaseModel):
    """A named path to a specific piece of data belonging to a `Sample`."""

    model_config = ConfigDict(
        extra="forbid", validate_assignment=True, arbitrary_types_allowed=True
    )

    id: str
    """Stable identity, immutable once set -- unaffected by renaming `name`."""

    name: str
    """Display name. Free to change without affecting `id`."""

    path: str
    """Filesystem path to this representation's data. The whole point of
    this registry: code refers to a representation by `id`, so `path` can
    change (the data gets moved) without any change to downstream code."""

    @field_validator("path", mode="before")
    @classmethod
    def _resolve_path(cls, value: str) -> str:
        """Resolve a stored `janelia://` URI to a real local path on read."""
        return from_janelia_uri(value)

    @field_serializer("path")
    def _serialize_path(self, value: str) -> str:
        """Translate `value` to its canonical `janelia://` URI -- the inverse of `_resolve_path`."""
        return to_janelia_uri(value)

    depends_on: list[str] | None = None
    """Ids of other representations of the same sample that this one was
    produced from. IDs, not display names -- stable across renames. Load a
    dependency with ``registry.load_representation(sample_id, dependency_id)``,
    reusing this representation's own `sample_id`."""

    metadata: Any = Field(default_factory=FreeformMetadata)
    """Always a `BaseModel` instance: `FreeformMetadata` (accepts any keys)
    unless `Registry.__init__` was given a `representation_metadata_model`,
    in which case it's an instance of that model instead. See
    `Sample.metadata` for why this is typed `Any` rather than a
    `BaseModel | dict` union."""
