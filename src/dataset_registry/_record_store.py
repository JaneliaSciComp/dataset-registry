import re
import tomllib
from collections.abc import Container
from pathlib import Path
from typing import Any, Generic, TypeVar

import tomli_w
from coolname import generate_slug
from pydantic import BaseModel

from dataset_registry._schema import (
    DataRecord,
)

SAMPLE_FILENAME = "sample.toml"
REPRESENTATION_FILENAME = "representation.toml"
REGISTRY_INFO_FILENAME = "registry.toml"
_ID_GENERATION_ATTEMPTS = 100
_SLUG_MAX_WORDS = 4


_DataRecordT = TypeVar("_DataRecordT", bound=DataRecord)


def _load_raw(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        raw: dict[str, Any] = tomllib.load(f)
    return raw


def _write_raw(path: Path, raw: dict[str, Any]) -> None:
    """Serialize `raw` as TOML and write it to `path`."""
    with path.open("wb") as f:
        tomli_w.dump(raw, f)


def _slugify(name: str) -> str:
    """Lowercase the first `_SLUG_MAX_WORDS` words of `name`, joined with `-`.

    Non-alphanumeric runs are treated as word boundaries. Capped at
    `_SLUG_MAX_WORDS` so a long display name (a full sentence, say) doesn't
    produce an unwieldy id -- the random word suffix (see `_generate_id`)
    still makes the id unique even when truncation collapses two different
    names to the same prefix.
    """
    words = [word for word in re.split(r"[^a-z0-9]+", name.lower()) if word]
    return "-".join(words[:_SLUG_MAX_WORDS])


def _random_word() -> str:
    # coolname has no single-word pattern; take one word off a two-word
    # slug rather than fighting its config for a custom pattern.
    slug: str = generate_slug(2)
    return slug.split("-")[-1]


class RecordStore(Generic[_DataRecordT]):
    """Groups what's different between samples and representations, so the generic
    find/list/load/write logic in `Registry` can be written once and shared.

    A "record" is a directory somewhere under `parent_dir`, holding one
    `filename` TOML file whose own `id` field (not its directory's name) is
    the source of truth for its identity -- same shape for both kinds, just
    at a different place in the tree: `<registry root>/*/sample.toml` for
    samples, `<sample dir>/*/representation.toml` for representations. A
    record's directory can hold more than its own TOML file (e.g. a
    virtual-zarr manifest, a thumbnail) -- `RecordStore` only ever touches
    `filename` within it.

    Generic in `_DataReferenceT` -- `RecordStore(Sample, ...)` types as
    `RecordStore[Sample]`, so `.load()` returns a `Sample` and `.write()`
    requires one, with no manual annotation needed at the call site.
    """

    def __init__(
        self,
        model_cls: type[_DataRecordT],
        metadata_model: type[BaseModel],
        parent_dir: Path,
        filename: str,
    ) -> None:
        self.model_cls = model_cls
        self.metadata_model = metadata_model
        self.parent_dir = parent_dir
        """Directory holding one subdirectory per record of this kind."""
        self.filename = filename
        """Name of the TOML file inside each record's own directory."""

    def _file_paths(self) -> list[Path]:
        """Every `filename` found one level under `parent_dir`, sorted."""
        return sorted(self.parent_dir.glob(f"*/{self.filename}"))

    def _find_dir(self, record_id: str) -> Path | None:
        """The directory of the existing record whose own `id` field is `record_id`.

        Always scans `parent_dir` rather than trusting a directory name to
        carry identity -- see the class docstring.

        Returns:
            The record's directory, or `None` if no `filename` under
            `parent_dir` has `id` equal to `record_id`.
        """
        for candidate in self._file_paths():
            if _load_raw(candidate).get("id") == record_id:
                return candidate.parent
        return None

    def _deduplicate(self, root: str, existing_list: Container[str]) -> str:
        """A `root`-plus-random-word string (e.g. `"embryo-a-kudu"`) not in `existing_list`.

        A random word is appended on every call, even when `root` itself
        doesn't collide -- callers needing that "use as-is unless it
        collides" fast path (see `_generate_id`) check `existing_list`
        themselves before calling this.

        Raises:
            RuntimeError: If every attempt collided -- vanishingly unlikely
                for any registry that isn't enormous, but a silent infinite
                loop would be worse than a loud, rare failure.
        """
        for _ in range(_ID_GENERATION_ATTEMPTS):
            candidate = f"{root}-{_random_word()}"
            if candidate not in existing_list:
                return candidate
        raise RuntimeError(
            f"could not generate a unique id after {_ID_GENERATION_ATTEMPTS} attempts"
        )

    def _generate_id(self, name: str) -> str:
        """A slugified-`name`-plus-random-word id (e.g. `"embryo-a-kudu"`) not in `existing_ids`.

        The random word is always appended, even with no collision, so an id
        never looks like it might just *be* the current display name -- `name`
        is free to change later without the id looking like a stale copy of it.

        Raises:
            RuntimeError: If every attempt collided -- vanishingly unlikely for
                any registry that isn't enormous, but a silent infinite loop
                would be worse than a loud, rare failure.
        """
        existing_ids = self.existing_ids()
        first_try = f"{_slugify(name)}-{_random_word()}"
        if first_try not in existing_ids:
            return first_try
        else:
            return self._deduplicate(first_try, existing_ids)

    def _new_dir(self, record_id: str) -> str:
        """The directory name a new record with `record_id` should be written to.

        Prefers `record_id` itself as the directory name, for readability
        on disk -- see the class docstring's note that directory names
        carry no meaning of their own; this picks a convenient one, not a
        load-bearing one. Falls back to `_deduplicate` if a directory of
        that name already exists for a *different* record (its `filename`'s
        `id` doesn't match `record_id`) -- not expected in practice, since
        ids are generated to be unique, but not actually impossible, e.g. a
        caller that invented its own id instead of using
        `generate_sample_id`/`generate_representation_id`.
        """
        existing_dirs = [file.parent.name for file in self._file_paths()]
        if record_id not in existing_dirs:
            return record_id
        else:
            return self._deduplicate(record_id, existing_dirs)

    def existing_ids(self) -> set[str]:
        """The `id` field of every `filename` found one level under `parent_dir`."""
        return {_load_raw(path)["id"] for path in self._file_paths()}

    def load(self, record_id: str) -> _DataRecordT:
        """Load and validate the record whose own `id` field is `record_id`.

        `metadata` is validated into `self.metadata_model` first (it's
        always a plain dict in the raw TOML -- TOML has no concept of a
        pydantic model), then the rest of the record is validated into
        `self.model_cls`.

        Raises:
            FileNotFoundError: If no record with this id exists under
                `parent_dir`.
        """
        record_dir = self._find_dir(record_id)
        if record_dir is None:
            raise FileNotFoundError(f"no {self.model_cls.__name__} with id {record_id!r} found")
        raw = _load_raw(record_dir / self.filename)
        raw = {**raw, "metadata": self.metadata_model.model_validate(raw.get("metadata", {}))}
        return self.model_cls.model_validate(raw)

    def write(self, model: _DataRecordT) -> None:
        """Persist `model`, overwriting its existing `filename` if one exists for its `id`.

        Looks up `model.id` to decide where to write: if a record with that
        id already exists, overwrites its current `filename` in place
        (whatever directory that happens to be); otherwise picks a new
        directory via `_new_dir` and creates it. Either way, writing always
        goes through `model.model_dump()` -- a validated model, never a
        hand-built dict -- so a caller can't persist a record that wouldn't
        itself pass validation on the next `load`.
        """
        record_dir = self._find_dir(model.id)
        if record_dir is None:
            record_dir = self.parent_dir / Path(self._new_dir(model.id))
            record_dir.mkdir()
        raw = model.model_dump(exclude_none=True)
        outdir = record_dir / self.filename
        _write_raw(outdir, raw)
