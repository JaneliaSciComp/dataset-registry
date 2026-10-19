from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny


class FreeformMetadata(BaseModel):
    """The default, project-agnostic shape for `Sample`/`Representation` `metadata`.

    Accepts any keys with no validation.
    """

    model_config = ConfigDict(extra="allow")


class RegistryInfo(BaseModel):
    """Registry-level settings, read from an optional `registry.toml` at the registry root."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    """Human-readable display title, e.g. for the `browse` UI"""


class DataReference(BaseModel):
    """What's needed to describe an object or data: an id, name, description, and metadata."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    id: str
    """Stable identity, immutable once set -- unaffected by renaming `name`."""

    name: str
    """Display name. Free to change without affecting `id`."""

    description: str | None = None

    metadata: SerializeAsAny[BaseModel] = Field(default_factory=FreeformMetadata)
    """Wrapped in `SerializeAsAny` because pydantic-core
    otherwise serializes a `BaseModel`-typed field using `BaseModel`'s own
    (fieldless) schema, silently dropping subclass fields on write."""


class Sample(DataReference):
    """A named, version-controlled entity that groups one or more representations of data."""


class Representation(DataReference):
    """A named path to a specific piece of data belonging to a `Sample`."""

    sample_id: str
    """The owning `Sample`'s stable id. Stored here rather than inferred from
    where this representation's file lives on disk -- same reasoning as
    `id` itself: identity comes from a model's own fields, never from
    location, so storage could later move (different directory layout, a
    database) without changing what a `Representation` means."""

    path: str
    """Filesystem path to this representation's data. The whole point of
    this registry: code refers to a representation by `id`, so `path` can
    change (the data gets moved) without any change to downstream code."""

    depends_on: list[str] | None = None
    """Ids of other representations of the same sample that this one was
    produced from. IDs, not display names -- stable across renames"""
