"""A registry of samples and representations, stored as TOML on disk."""

import hashlib
import logging
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any, TypeVar

import tomli_w
from coolname import generate_slug
from platformdirs import user_cache_dir
from pydantic import BaseModel

from dataset_registry._schema import (
    DataReference,
    FreeformMetadata,
    RegistryInfo,
    Representation,
    Sample,
)

SAMPLE_FILENAME = "sample.toml"
REPRESENTATIONS_DIRNAME = "representations"
REGISTRY_INFO_FILENAME = "registry.toml"

_ID_GENERATION_ATTEMPTS = 100
_CACHE_APP_NAME = "dataset-registry"
_SLUG_MAX_WORDS = 4

_logger = logging.getLogger(__name__)

# Sentinel distinguishing "leave this field unchanged" from "set it to None".
_UNSET = object()

_DataReferenceT = TypeVar("_DataReferenceT", bound=DataReference)


def _load_raw(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        raw: dict[str, Any] = tomllib.load(f)
    return raw


def _load_model(
    model_cls: type[_DataReferenceT],
    raw: dict[str, Any],
    *,
    metadata_model: type[BaseModel],
) -> _DataReferenceT:
    """`model_cls.model_validate(raw)`, with `metadata` validated into `metadata_model`.

    `metadata` in `raw` is always a plain dict (TOML has no concept of a
    pydantic model) -- this is the one place that turns it into a
    `metadata_model` instance before `model_cls`'s own fields are validated.
    """
    raw = {**raw, "metadata": metadata_model.model_validate(raw.get("metadata", {}))}
    return model_cls.model_validate(raw)


def _write_model(path: Path, model: BaseModel) -> None:
    """Serialize `model` and write it to `path` as TOML.

    Writing always goes through a validated model -- never a hand-built
    dict -- so a caller can't persist a `sample.toml`/representation TOML
    that wouldn't itself pass `model_validate` on the next load.
    """
    raw = model.model_dump(exclude_none=True)
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


def _generate_id(name: str, existing_ids: set[str]) -> str:
    """A slugified-`name`-plus-random-word id (e.g. `"embryo-a-kudu"`) not in `existing_ids`.

    The random word is always appended, even with no collision, so an id
    never looks like it might just *be* the current display name -- `name`
    is free to change later without the id looking like a stale copy of it.

    Raises:
        RuntimeError: If every attempt collided -- vanishingly unlikely for
            any registry that isn't enormous, but a silent infinite loop
            would be worse than a loud, rare failure.
    """
    slug = _slugify(name) or "unnamed"
    for _ in range(_ID_GENERATION_ATTEMPTS):
        candidate = f"{slug}-{_random_word()}"
        if candidate not in existing_ids:
            return candidate
    raise RuntimeError(f"could not generate a unique id after {_ID_GENERATION_ATTEMPTS} attempts")


def _cache_dir_for(git_url: str) -> Path:
    # Keyed by a hash of the URL rather than the URL itself, so the cache
    # directory name is always filesystem-safe regardless of what characters
    # the URL contains.
    digest = hashlib.sha256(git_url.encode()).hexdigest()[:16]
    return Path(user_cache_dir(_CACHE_APP_NAME)) / digest


def _run_git(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60, check=True
    )


def _clone(git_url: str, cache_dir: Path) -> None:
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["clone", "--depth=1", git_url, str(cache_dir)])


def _fetch_latest(cache_dir: Path) -> None:
    _run_git(["fetch", "--depth=1", "origin"], cwd=cache_dir)
    remote_head = _run_git(["rev-parse", "origin/HEAD"], cwd=cache_dir).stdout.strip()
    _run_git(["reset", "--hard", remote_head], cwd=cache_dir)


def _current_commit(path: Path) -> str | None:
    try:
        return _run_git(["rev-parse", "HEAD"], cwd=path).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _resolve_local_path(location: str | Path, *, fetch: bool) -> Path:
    """Resolve `location` to a local directory, cloning it first if it's a git URL.

    If `location` is an existing local directory, returns it directly -- no
    cloning, no cache, no network -- which is both the simple common case
    (a registry already checked out locally, e.g. on a shared fileshare) and
    what keeps tests fast and network-free.

    Otherwise, treats `location` as a git URL: clones (or reuses a cached
    clone of) it under a per-user cache directory, keyed by `location`, as a
    shallow (`--depth=1`) clone. If a cache already exists and `fetch` is
    true, fetches and resets it to the remote's default branch tip first;
    if `fetch` is false, the existing cache is used as-is, however stale.

    If the fetch fails (e.g. no network), falls back to the existing cache
    with a warning rather than failing outright -- better to work against a
    possibly-stale registry than not run at all. If no cache exists yet, a
    clone always happens regardless of `fetch` -- there's nothing to fall
    back to otherwise.
    """
    local_path = Path(location)
    if local_path.is_dir():
        return local_path

    git_url = str(location)
    cache_dir = _cache_dir_for(git_url)

    if not cache_dir.is_dir():
        _clone(git_url, cache_dir)
    elif fetch:
        try:
            _fetch_latest(cache_dir)
        except (OSError, subprocess.SubprocessError) as e:
            _logger.warning(
                "could not fetch latest for %s (%s); using cached clone at %s, which may be stale",
                git_url,
                e,
                cache_dir,
            )
    return cache_dir


class Registry:
    """A registry of samples and representations: a local or git-hosted directory of TOML files.

    `location` is either an existing local directory (used directly, no
    cloning) or a git URL (shallow-cloned, or reused from a per-user cache).
    Every load/create/rename/set call below is scoped to this one resolved
    directory.

        <path>/
          <sample directory>/
            sample.toml
            representations/
              <representation file>.toml
              ...

    Directory and file names carry no meaning of their own -- `id`/`name`
    are always read from a `sample.toml`/representation TOML's own fields,
    never inferred from where it lives on disk.
    """

    path: Path
    """Local path this registry resolved to."""

    commit: str | None
    """The full commit SHA `path` is checked out at, or `None` if `path`
    isn't inside a git repository -- e.g. a local directory passed directly,
    with no git history of its own."""

    def __init__(
        self,
        location: str | Path,
        *,
        fetch: bool = True,
        sample_metadata_model: type[BaseModel] = FreeformMetadata,
        representation_metadata_model: type[BaseModel] = FreeformMetadata,
    ) -> None:
        """Resolve `location` to a local registry directory.

        Args:
            location: An existing local directory, or a git URL cloneable
                with `git clone` (e.g. `"https://github.com/<owner>/<repo>"`).
            fetch: If `location` is a git URL and a cached clone from a
                previous `Registry(location)` call already exists, whether
                to fetch and reset it to the remote's default branch tip
                before use. Defaults to `True`, matching the historical
                behavior of always pulling on open. Pass `False` to reuse
                the existing cache as-is, however stale -- e.g. to avoid a
                network round-trip on every call, or to pin to whatever was
                last fetched until an explicit `Registry(location, fetch=True)`.
                Ignored for a local-directory `location`, and for a git URL
                with no existing cache -- both always use the freshest data
                available (the directory itself, or a fresh clone).
            sample_metadata_model: Every `load_sample` call validates
                `metadata` into this pydantic model -- a project-specific
                `BaseModel` with its own typed fields, or the default
                `FreeformMetadata`, which accepts any keys with no validation.
            representation_metadata_model: Same as `sample_metadata_model`,
                for `load_representation`.
        """
        path = _resolve_local_path(location, fetch=fetch)
        if not path.is_dir():
            raise NotADirectoryError(f"{path} is not a directory")
        self.path = path
        self.commit = _current_commit(path)
        self._sample_metadata = sample_metadata_model
        self._representation_metadata = representation_metadata_model

    def __repr__(self) -> str:
        title = self.load_registry_info().title
        if title is not None:
            return f"Registry(title={title!r}, path={str(self.path)!r})"
        return f"Registry(path={str(self.path)!r})"

    def _sample_dirs(self) -> list[Path]:
        return sorted(p.parent for p in self.path.glob(f"*/{SAMPLE_FILENAME}"))

    def _find_sample_dir(self, sample_id: str) -> Path:
        """Find the directory whose sample has `sample_id`.

        A sample's directory name carries no meaning of its own -- `id` is
        only ever read from `sample.toml`'s own `id` field -- so this always
        scans every sample in the registry.

        Raises:
            FileNotFoundError: If no sample with this id exists.
        """
        for sample_dir in self._sample_dirs():
            raw = _load_raw(sample_dir / SAMPLE_FILENAME)
            if raw.get("id") == sample_id:
                return sample_dir
        raise FileNotFoundError(f"no sample with id {sample_id!r} found under {self.path}")

    def _find_representation_path(self, sample_id: str, representation_id: str) -> Path:
        """Find the file whose representation has `representation_id`.

        Same reasoning as `_find_sample_dir`: a representation's filename
        carries no meaning of its own, so this always scans every
        representation belonging to the sample.

        Raises:
            FileNotFoundError: If no such sample, or no representation with
                this id under it, exists.
        """
        sample_dir = self._find_sample_dir(sample_id)
        representations_dir = sample_dir / REPRESENTATIONS_DIRNAME

        for representation_path in sorted(representations_dir.glob("*.toml")):
            raw = _load_raw(representation_path)
            if raw.get("id") == representation_id:
                return representation_path

        raise FileNotFoundError(
            f"no representation with id {representation_id!r} found for sample "
            f"{sample_id!r} under {self.path}"
        )

    def generate_sample_id(self, name: str) -> str:
        """A slugified-`name`-plus-random-word id not already used as a sample id here."""
        existing_ids = {
            _load_raw(sample_dir / SAMPLE_FILENAME)["id"] for sample_dir in self._sample_dirs()
        }
        return _generate_id(name, existing_ids)

    def generate_representation_id(self, sample_id: str, name: str) -> str:
        """A slugified-`name`-plus-random-word id, unique among this sample's representations."""
        sample_dir = self._find_sample_dir(sample_id)
        representations_dir = sample_dir / REPRESENTATIONS_DIRNAME
        existing_ids = {
            _load_raw(path)["id"] for path in sorted(representations_dir.glob("*.toml"))
        }
        return _generate_id(name, existing_ids)

    def load_sample(self, sample_id: str) -> Sample:
        """Load one sample by id.

        Args:
            sample_id: The sample's stable id.

        Returns:
            The loaded sample, with `metadata` validated into this
            registry's `sample_metadata model`.

        Raises:
            FileNotFoundError: If no sample with this id exists.
        """
        sample_dir = self._find_sample_dir(sample_id)
        raw = _load_raw(sample_dir / SAMPLE_FILENAME)
        sample = _load_model(Sample, raw, metadata_model=self._sample_metadata)
        return sample

    def load_representation(self, sample_id: str, representation_id: str) -> Representation:
        """Load one representation.

        Args:
            sample_id: The owning sample's id.
            representation_id: The representation's stable id.

        Returns:
            The loaded representation, with `metadata` validated into this
            registry's `representation_metadata`.

        Raises:
            FileNotFoundError: If no such sample or representation exists.
        """
        representation_path = self._find_representation_path(sample_id, representation_id)
        raw = _load_raw(representation_path)
        representation = _load_model(
            Representation, raw, metadata_model=self._representation_metadata
        )
        return representation

    def load_registry_info(self) -> RegistryInfo:
        """Load registry-level settings from an optional `registry.toml` at this registry's root.

        Returns:
            The registry's settings, or an all-defaults `RegistryInfo` if
            `registry.toml` doesn't exist -- this file is optional, unlike
            `sample.toml`.
        """
        registry_info_path = self.path / REGISTRY_INFO_FILENAME
        if not registry_info_path.is_file():
            return RegistryInfo()
        registry_info = RegistryInfo.model_validate(_load_raw(registry_info_path))
        return registry_info

    def list_samples(self) -> list[str]:
        """The ids of every sample in this registry.

        Returns:
            Sample ids, sorted, for every subdirectory containing a `sample.toml`.
        """
        return sorted(
            _load_raw(sample_dir / SAMPLE_FILENAME)["id"] for sample_dir in self._sample_dirs()
        )

    def list_representations(self, sample_id: str) -> list[str]:
        """The ids of every representation belonging to one sample.

        Args:
            sample_id: The owning sample's id.

        Returns:
            Representation ids, sorted.
        """
        sample_dir = self._find_sample_dir(sample_id)
        representations_dir = sample_dir / REPRESENTATIONS_DIRNAME
        return sorted(_load_raw(path)["id"] for path in representations_dir.glob("*.toml"))

    def find_samples_by_name(self, name: str) -> list[Sample]:
        """Every sample in this registry whose `name` matches exactly.

        Names aren't required to be unique (`id` is the only unambiguous
        handle), so this returns every match -- callers should handle 0
        (no such name), 1, or more than 1 (ambiguous name) explicitly.

        Args:
            name: The display name to match, exactly and case-sensitively.

        Returns:
            Matching samples, in no particular order.
        """
        return [
            sample
            for sample_id in self.list_samples()
            if (sample := self.load_sample(sample_id)).name == name
        ]

    def find_representations_by_name(self, sample_id: str, name: str) -> list[Representation]:
        """Every representation of one sample whose `name` matches exactly.

        Names aren't required to be unique (`id` is the only unambiguous
        handle), so this returns every match -- callers should handle 0
        (no such name), 1, or more than 1 (ambiguous name) explicitly.

        Args:
            sample_id: The owning sample's id.
            name: The display name to match, exactly and case-sensitively.

        Returns:
            Matching representations, in no particular order.
        """
        return [
            representation
            for representation_id in self.list_representations(sample_id)
            if (representation := self.load_representation(sample_id, representation_id)).name
            == name
        ]

    def create_sample(
        self, name: str, *, description: str | None = None, metadata: dict[str, Any] | None = None
    ) -> str:
        """Create a new sample with a freshly generated id.

        Args:
            name: The sample's initial display name.
            description: Initial description, if any.
            metadata: Initial metadata table, if any.

        Returns:
            The newly generated sample id.
        """
        sample_id = self.generate_sample_id(name)
        sample_dir = self.path / sample_id
        sample_dir.mkdir(parents=True)

        sample = Sample(
            id=sample_id,
            name=name,
            description=description,
            metadata=self._sample_metadata.model_validate(metadata or {}),
        )
        _write_model(sample_dir / SAMPLE_FILENAME, sample)
        return sample_id

    def create_representation(
        self,
        sample_id: str,
        name: str,
        *,
        path: str,
        depends_on: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create a new representation with a freshly generated id.

        Args:
            sample_id: The owning sample's id.
            name: The representation's initial display name.
            path: Filesystem path to the representation's data.
            depends_on: Ids of other representations of the same sample that
                this one was produced from.
            metadata: Initial metadata table, if any.

        Returns:
            The newly generated representation id.
        """
        representation_id = self.generate_representation_id(sample_id, name)
        sample_dir = self._find_sample_dir(sample_id)
        representations_dir = sample_dir / REPRESENTATIONS_DIRNAME
        representations_dir.mkdir(exist_ok=True)

        representation = Representation(
            id=representation_id,
            sample_id=sample_id,
            name=name,
            path=path,
            depends_on=depends_on,
            metadata=self._representation_metadata.model_validate(metadata or {}),
        )
        representation_path = representations_dir / f"{representation_id}.toml"
        _write_model(representation_path, representation)
        return representation_id

    def rename_sample(self, sample_id: str, new_name: str) -> None:
        """Change a sample's display `name`.

        Args:
            sample_id: The sample's stable id.
            new_name: The new display name.
        """
        sample_path = self._find_sample_dir(sample_id) / SAMPLE_FILENAME
        sample = _load_model(Sample, _load_raw(sample_path), metadata_model=self._sample_metadata)
        sample.name = new_name
        _write_model(sample_path, sample)

    def rename_representation(self, sample_id: str, representation_id: str, new_name: str) -> None:
        """Change a representation's display `name`.

        Args:
            sample_id: The owning sample's id.
            representation_id: The representation's stable id.
            new_name: The new display name.
        """
        representation_path = self._find_representation_path(sample_id, representation_id)
        representation = _load_model(
            Representation,
            _load_raw(representation_path),
            metadata_model=self._representation_metadata,
        )
        representation.name = new_name
        _write_model(representation_path, representation)

    def set_sample_fields(
        self,
        sample_id: str,
        *,
        description: str | None = _UNSET,  # type: ignore[assignment]
        metadata: dict[str, Any] | None = _UNSET,  # type: ignore[assignment]
    ) -> None:
        """Update one or more of a sample's own fields in place, in a single write.

        Only the fields actually passed are changed -- `name` and every
        other field not accepted by this method is left untouched (use
        `rename_sample` to change `name`). Passing `None` for `description`
        clears that field (distinct from not passing it at all, which
        leaves it as-is). `metadata`, when passed, replaces the entire
        `[metadata]` table rather than merging into it -- callers that want
        to change one key should read the sample's current `metadata` first.

        Validates the updated sample before writing, so a call that would
        produce an invalid sample fails loudly rather than silently
        corrupting the file.

        Args:
            sample_id: The sample's stable id.
            description: The new description text, `None` to clear it, or
                omitted to leave it unchanged.
            metadata: The new `metadata` table, replacing it wholesale, or
                omitted to leave it unchanged.
        """
        sample_path = self._find_sample_dir(sample_id) / SAMPLE_FILENAME
        sample = _load_model(Sample, _load_raw(sample_path), metadata_model=self._sample_metadata)

        if description is not _UNSET:
            sample.description = description
        if metadata is not _UNSET:
            sample.metadata = self._sample_metadata.model_validate(metadata or {})

        _write_model(sample_path, sample)

    def set_representation_fields(
        self,
        sample_id: str,
        representation_id: str,
        *,
        path: str = _UNSET,  # type: ignore[assignment]
        depends_on: list[str] | None = _UNSET,  # type: ignore[assignment]
        metadata: dict[str, Any] | None = _UNSET,  # type: ignore[assignment]
    ) -> None:
        """Update one or more of a representation's own fields in place, in a single write.

        Only the fields actually passed are changed -- `name` and every
        other field not accepted by this method is left untouched (use
        `rename_representation` to change `name`). `path` has no "clear"
        option since it's required. `depends_on`, when passed, replaces the
        entire list (`None` clears it). `metadata`, when passed, replaces
        the entire `[metadata]` table rather than merging into it --
        callers that want to change one key should read the
        representation's current `metadata` first.

        Validates the updated representation before writing, so a call that
        would produce an invalid representation fails loudly rather than
        silently corrupting the file.

        Args:
            sample_id: The owning sample's id.
            representation_id: The representation's stable id.
            path: The new filesystem path, or omitted to leave it unchanged.
            depends_on: The new list of ids of other representations of the
                same sample that this one was produced from, `None` to
                clear it, or omitted to leave it unchanged.
            metadata: The new `metadata` table, replacing it wholesale, or
                omitted to leave it unchanged.
        """
        representation_path = self._find_representation_path(sample_id, representation_id)
        representation = _load_model(
            Representation,
            _load_raw(representation_path),
            metadata_model=self._representation_metadata,
        )

        if path is not _UNSET:
            representation.path = path
        if depends_on is not _UNSET:
            representation.depends_on = depends_on
        if metadata is not _UNSET:
            representation.metadata = self._representation_metadata.model_validate(metadata or {})

        _write_model(representation_path, representation)


def create_registry(
    path: str | Path,
    *,
    title: str | None = None,
) -> None:
    """Create a new, empty registry directory on disk

    Args:
        path: Local directory to create the registry in. Created (with any
            missing parents) if it doesn't already exist; an existing empty
            directory is fine too.
        title: The registry's display title, written to `registry.toml` as
            `RegistryInfo.title`. Mutually exclusive with `registry_info` --
            pass whichever is more convenient; omit both to write no
            `registry.toml` at all (same as an already-existing registry
            with no title set).

    Raises:
        FileExistsError: If `path` already exists and is non-empty --
            `create_registry` is for new registries, not for re-initializing
            or overwriting one that already has contents.
    """
    registry_path = Path(path)
    if registry_path.is_dir() and any(registry_path.iterdir()):
        raise FileExistsError(f"{registry_path} already exists and is non-empty")
    registry_path.mkdir(parents=True, exist_ok=True)

    if title is not None:
        registry_info = RegistryInfo(title=title)
        _write_model(registry_path / REGISTRY_INFO_FILENAME, registry_info)
