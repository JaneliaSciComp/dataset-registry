import random
from pathlib import Path

import pytest
import tomli_w

from dataset_registry import Registry
from dataset_registry._registry import _load_raw
from dataset_registry._schema import FreeformMetadata


@pytest.fixture(autouse=True)
def _seed_random():
    """Seed the global `random` module before each test.

    `coolname.generate_slug` (used by `generate_sample_id`/
    `generate_representation_id` for their random-word suffix) draws from
    Python's global `random` state, so without this, ids generated via the
    registry API vary from run to run.
    """
    random.seed(0)


@pytest.fixture
def registry(tmp_path) -> Registry:
    return Registry(tmp_path)


@pytest.fixture
def sample_id(registry: Registry) -> str:
    return registry.create_sample(
        "SLS161", description="a lightsheet embryo", metadata={"strain": "SLS267"}
    )


@pytest.fixture
def repr_id(registry, sample_id):
    return registry.create_representation(
        sample_id,
        "raw",
        path="/data/SLS161/raw.zarr",
        metadata={"channel_label": "nuclear"},
    )


@pytest.fixture
def repr2_id(registry, sample_id, repr_id):
    return registry.create_representation(
        sample_id,
        "cellpose_v3",
        path="/data/SLS161/masks.zarr",
        depends_on=[repr_id],
    )


def write_toml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        tomli_w.dump(data, f)


def overwrite_id(path: Path, new_id: str) -> None:
    """Rewrite the `id` field of the TOML file at `path`, in place."""
    toml = _load_raw(path)
    toml["id"] = new_id
    write_toml(path, toml)


class TestRegistry:
    def test_write_sample(self, registry):
        registry.create_sample(
            "SLS161", description="a lightsheet embryo", metadata={"strain": "SLS267"}
        )

    def test_load_sample(self, registry: Registry, sample_id: str):
        sample = registry.load_sample(sample_id)
        assert sample.id == sample_id
        assert sample.name == "SLS161"
        assert sample.description == "a lightsheet embryo"
        assert isinstance(sample.metadata, FreeformMetadata)
        assert sample.metadata.strain == "SLS267"

        with pytest.raises(FileNotFoundError, match="no Sample with id"):
            registry.load_sample("does-not-exist")

        # mutate the sample_id artificially so it doesn't match the directory
        sample_path = registry._sample_store._find_dir(sample_id) / "sample.toml"
        new_id = "test"
        overwrite_id(sample_path, new_id)
        sample = registry.load_sample(new_id)
        assert sample.id == new_id
        assert sample.name == "SLS161"
        assert sample.description == "a lightsheet embryo"

    def test_load_representation(
        self, registry: Registry, sample_id: str, repr_id: str, repr2_id: str
    ):
        representation = registry.load_representation(sample_id, repr_id)
        assert representation.id == repr_id
        assert representation.name == "raw"
        assert representation.path == "/data/SLS161/raw.zarr"
        assert isinstance(representation.metadata, FreeformMetadata)
        assert representation.metadata.channel_label == "nuclear"

        representation2 = registry.load_representation(sample_id, repr2_id)
        assert representation2.depends_on == [repr_id]

        with pytest.raises(FileNotFoundError, match="no Representation"):
            registry.load_representation(sample_id, "does-not-exist")

        # mutate the representation_id artificially so it doesn't match the directory name
        representation_dir = registry._get_repr_store(sample_id)._find_dir(repr_id)
        new_id = "test"
        overwrite_id(representation_dir / "representation.toml", new_id)
        representation = registry.load_representation(sample_id, new_id)
        assert representation.id == new_id
        assert representation.name == "raw"
        assert representation.path == "/data/SLS161/raw.zarr"

    def test_list_samples(self, registry: Registry, sample_id: str):
        # empty registry: no samples yet besides the fixture-created one
        assert registry.list_samples() == [sample_id]

        # a second sample, with an id that sorts before the first
        other_id = registry.create_sample("Embryo A")
        assert registry.list_samples() == sorted([sample_id, other_id])

        # a directory with no sample.toml in it is ignored
        (registry.path / "not_a_sample").mkdir()
        assert registry.list_samples() == sorted([sample_id, other_id])

        # a sample's id comes from its sample.toml, not its directory name
        sample_path = registry._sample_store._find_dir(sample_id) / "sample.toml"
        overwrite_id(sample_path, "renamed-id")
        assert registry.list_samples() == sorted(["renamed-id", other_id])

    def test_list_representations(
        self, registry: Registry, sample_id: str, repr_id: str, repr2_id: str
    ):
        assert registry.list_representations(sample_id) == sorted([repr_id, repr2_id])

        # a directory with no representation.toml in it is ignored, .toml or not
        representation_dir = registry._get_repr_store(sample_id)._find_dir(repr_id)
        other_dir = representation_dir.parent / "not_a_representation"
        other_dir.mkdir()
        (other_dir / "not_a_representation.toml").write_text("")
        assert registry.list_representations(sample_id) == sorted([repr_id, repr2_id])

        # a representation's id comes from its own file, not its directory name
        overwrite_id(representation_dir / "representation.toml", "renamed-id")
        assert registry.list_representations(sample_id) == sorted(["renamed-id", repr2_id])
