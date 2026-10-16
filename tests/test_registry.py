import subprocess
from pathlib import Path

import pytest
import tomli_w

from dataset_registry import Registry


def _write_toml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        tomli_w.dump(data, f)


def _write_sample(registry_root: Path, dirname: str, **fields) -> Path:
    sample_dir = registry_root / dirname
    _write_toml(sample_dir / "sample.toml", {"id": dirname, "name": dirname, **fields})
    return sample_dir


def _write_representation(
    registry_root: Path, sample_dirname: str, filename: str, **fields
) -> Path:
    path = registry_root / sample_dirname / "representations" / f"{filename}.toml"
    _write_toml(path, {"id": filename, "name": filename, **fields})
    return path


def test_load_sample_reads_its_fields(tmp_path):
    _write_sample(tmp_path, "SLS161", description="a lightsheet embryo")
    registry = Registry(tmp_path)

    sample = registry.load_sample("SLS161")

    assert sample.id == "SLS161"
    assert sample.name == "SLS161"
    assert sample.description == "a lightsheet embryo"


def test_load_sample_raises_when_missing(tmp_path):
    registry = Registry(tmp_path)

    with pytest.raises(FileNotFoundError, match="no sample with id"):
        registry.load_sample("does-not-exist")


def test_load_sample_finds_a_sample_whose_id_does_not_match_its_directory(tmp_path):
    _write_toml(
        tmp_path / "some-directory" / "sample.toml", {"id": "elsewhere", "name": "Embryo A"}
    )
    registry = Registry(tmp_path)

    sample = registry.load_sample("elsewhere")

    assert sample.name == "Embryo A"


def test_load_representation_reads_its_fields(tmp_path):
    _write_sample(tmp_path, "SLS161")
    _write_representation(tmp_path, "SLS161", "raw", path="/groups/shroff/raw.tif")
    registry = Registry(tmp_path)

    representation = registry.load_representation("SLS161", "raw")

    assert representation.id == "raw"
    assert representation.name == "raw"
    assert representation.path == "/groups/shroff/raw.tif"


def test_load_representation_raises_when_missing(tmp_path):
    _write_sample(tmp_path, "SLS161")
    registry = Registry(tmp_path)

    with pytest.raises(FileNotFoundError, match="no representation"):
        registry.load_representation("SLS161", "does-not-exist")


def test_load_representation_finds_a_representation_whose_id_does_not_match_its_filename(tmp_path):
    _write_sample(tmp_path, "SLS161")
    _write_toml(
        tmp_path / "SLS161" / "representations" / "some-file.toml",
        {"id": "elsewhere", "name": "Raw scan", "path": "a"},
    )
    registry = Registry(tmp_path)

    representation = registry.load_representation("SLS161", "elsewhere")

    assert representation.name == "Raw scan"


def test_load_representation_reads_metadata_and_depends_on(tmp_path):
    _write_sample(tmp_path, "SLS161")
    _write_representation(
        tmp_path,
        "SLS161",
        "raw",
        path="/groups/shroff/raw.tif",
    )
    _write_representation(
        tmp_path,
        "SLS161",
        "cellpose_v3",
        path="/nrs/shroff/masks.zarr",
        depends_on=["raw"],
        metadata={"channel_label": "nuclear"},
    )
    registry = Registry(tmp_path)

    representation = registry.load_representation("SLS161", "cellpose_v3")

    assert representation.depends_on == ["raw"]
    assert representation.metadata.model_dump(exclude_none=True) == {"channel_label": "nuclear"}


def test_depends_on_is_loaded_via_load_representation_with_the_same_sample_id(tmp_path):
    _write_sample(tmp_path, "SLS161")
    _write_representation(tmp_path, "SLS161", "raw", path="/groups/shroff/raw.tif")
    _write_representation(
        tmp_path, "SLS161", "cellpose_v3", path="/nrs/shroff/masks.zarr", depends_on=["raw"]
    )
    registry = Registry(tmp_path)

    segmentation = registry.load_representation("SLS161", "cellpose_v3")
    (dependency_id,) = segmentation.depends_on
    dependency = registry.load_representation("SLS161", dependency_id)

    assert dependency.path == "/groups/shroff/raw.tif"


def test_list_samples_finds_only_directories_with_a_sample_toml(tmp_path):
    _write_sample(tmp_path, "SLS161")
    _write_sample(tmp_path, "SLS042")
    (tmp_path / "not_a_sample").mkdir()
    registry = Registry(tmp_path)

    assert registry.list_samples() == ["SLS042", "SLS161"]


def test_list_representations_finds_every_representation_for_a_sample(tmp_path):
    _write_sample(tmp_path, "SLS161")
    _write_representation(tmp_path, "SLS161", "raw", path="a")
    _write_representation(tmp_path, "SLS161", "cellpose_v3", path="b")
    registry = Registry(tmp_path)

    assert registry.list_representations("SLS161") == ["cellpose_v3", "raw"]


def test_find_samples_by_name_returns_every_match(tmp_path):
    _write_sample(tmp_path, "a", name="Embryo A")
    _write_sample(tmp_path, "b", name="Embryo A")
    _write_sample(tmp_path, "c", name="Embryo B")
    registry = Registry(tmp_path)

    matches = registry.find_samples_by_name("Embryo A")

    assert sorted(sample.id for sample in matches) == ["a", "b"]


def test_find_samples_by_name_returns_empty_list_when_no_match(tmp_path):
    _write_sample(tmp_path, "a", name="Embryo A")
    registry = Registry(tmp_path)

    assert registry.find_samples_by_name("does-not-exist") == []


def test_find_representations_by_name_returns_every_match(tmp_path):
    _write_sample(tmp_path, "SLS161")
    _write_representation(tmp_path, "SLS161", "a", name="Raw scan", path="x")
    _write_representation(tmp_path, "SLS161", "b", name="Raw scan", path="y")
    _write_representation(tmp_path, "SLS161", "c", name="Segmentation", path="z")
    registry = Registry(tmp_path)

    matches = registry.find_representations_by_name("SLS161", "Raw scan")

    assert sorted(representation.id for representation in matches) == ["a", "b"]


def test_rename_sample_changes_name_without_moving_directory(tmp_path):
    _write_sample(tmp_path, "SLS161")
    registry = Registry(tmp_path)

    registry.rename_sample("SLS161", "Embryo A")

    assert (tmp_path / "SLS161").is_dir()
    sample = registry.load_sample("SLS161")
    assert sample.id == "SLS161"
    assert sample.name == "Embryo A"


def test_rename_representation_changes_name_without_moving_file(tmp_path):
    _write_sample(tmp_path, "SLS161")
    _write_representation(tmp_path, "SLS161", "raw", path="a")
    registry = Registry(tmp_path)

    registry.rename_representation("SLS161", "raw", "Raw scan")

    representation_path = tmp_path / "SLS161" / "representations" / "raw.toml"
    assert representation_path.is_file()
    representation = registry.load_representation("SLS161", "raw")
    assert representation.id == "raw"
    assert representation.name == "Raw scan"


def test_create_sample_generates_a_unique_id_and_is_loadable(tmp_path):
    registry = Registry(tmp_path)

    sample_id = registry.create_sample("Embryo A", description="a lightsheet embryo")

    sample = registry.load_sample(sample_id)
    assert sample.id == sample_id
    assert sample.name == "Embryo A"
    assert sample.description == "a lightsheet embryo"


def test_create_representation_generates_a_unique_id_and_is_loadable(tmp_path):
    registry = Registry(tmp_path)
    sample_id = registry.create_sample("Embryo A")

    representation_id = registry.create_representation(
        sample_id, "Raw scan", path="/groups/shroff/raw.tif"
    )

    representation = registry.load_representation(sample_id, representation_id)
    assert representation.id == representation_id
    assert representation.name == "Raw scan"
    assert representation.path == "/groups/shroff/raw.tif"


def test_generate_sample_id_slugifies_the_name_with_a_random_word_suffix(tmp_path):
    registry = Registry(tmp_path)

    sample_id = registry.generate_sample_id("Embryo A")

    prefix, _, suffix = sample_id.rpartition("-")
    assert prefix == "embryo-a"
    assert suffix  # a random word, always present


def test_generate_sample_id_caps_the_slug_at_four_words(tmp_path):
    registry = Registry(tmp_path)

    sample_id = registry.generate_sample_id("Embryo A cross of SLS267 and OH15257")

    prefix, _, _suffix = sample_id.rpartition("-")
    assert prefix == "embryo-a-cross-of"


def test_generate_sample_id_retries_on_a_forced_collision(tmp_path, monkeypatch):
    words = iter(["taken", "taken", "fresh"])
    monkeypatch.setattr("dataset_registry._registry._random_word", lambda: next(words))
    registry = Registry(tmp_path)
    registry.create_sample("First")  # takes "first-taken"

    sample_id = registry.generate_sample_id("First")

    assert sample_id == "first-fresh"


def test_depends_on_resolves_by_id_after_the_referenced_representation_is_renamed(tmp_path):
    registry = Registry(tmp_path)
    sample_id = registry.create_sample("Embryo A")
    raw_id = registry.create_representation(sample_id, "Raw scan", path="a")
    seg_id = registry.create_representation(sample_id, "Cellpose v3", path="b", depends_on=[raw_id])

    registry.rename_representation(sample_id, raw_id, "Renamed raw scan")

    segmentation = registry.load_representation(sample_id, seg_id)
    (dependency_id,) = segmentation.depends_on
    dependency = registry.load_representation(sample_id, dependency_id)

    assert dependency.id == raw_id
    assert dependency.name == "Renamed raw scan"


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(["init", "-q", "-b", "main"], cwd=remote)
    _git(["config", "user.email", "test@example.com"], cwd=remote)
    _git(["config", "user.name", "Test"], cwd=remote)
    _write_sample(remote, "SLS161")
    _git(["add", "-A"], cwd=remote)
    _git(["commit", "-q", "-m", "initial"], cwd=remote)
    return remote


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    # Every test gets its own cache dir, so runs never share (or pollute)
    # the real user cache directory or each other's clones.
    monkeypatch.setattr(
        "dataset_registry._registry.user_cache_dir", lambda _app: str(tmp_path / "cache")
    )


def test_registry_uses_a_local_directory_directly(tmp_path):
    local = tmp_path / "local-registry"
    _write_sample(local, "SLS161")

    registry = Registry(local)

    assert registry.path == local
    assert registry.commit is None  # not a git repo


def test_registry_reports_commit_for_a_local_git_directory(tmp_path):
    remote = _init_remote(tmp_path)

    registry = Registry(remote)

    assert registry.path == remote
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=remote, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert registry.commit == expected


def test_registry_clones_a_git_url_into_the_cache(tmp_path):
    remote = _init_remote(tmp_path)

    registry = Registry(remote.as_uri())

    assert registry.path != remote
    assert registry.load_sample("SLS161").id == "SLS161"
    assert registry.commit is not None


def test_registry_reuses_the_cache_and_picks_up_new_commits(tmp_path):
    remote = _init_remote(tmp_path)

    first = Registry(remote.as_uri())

    _write_sample(remote, "SLS042")
    _git(["add", "-A"], cwd=remote)
    _git(["commit", "-qm", "update"], cwd=remote)

    second = Registry(remote.as_uri())

    assert second.path == first.path  # same cache directory reused
    assert second.commit != first.commit
    assert second.list_samples() == ["SLS042", "SLS161"]


def test_registry_falls_back_to_cache_on_fetch_failure(tmp_path, monkeypatch):
    remote = _init_remote(tmp_path)

    first = Registry(remote.as_uri())

    def _boom(*args, **kwargs):
        raise subprocess.SubprocessError("simulated network failure")

    monkeypatch.setattr("dataset_registry._registry._fetch_latest", _boom)

    second = Registry(remote.as_uri())

    assert second.path == first.path
    assert second.commit == first.commit


def test_registry_raises_when_no_cache_and_clone_fails(tmp_path):
    with pytest.raises(subprocess.CalledProcessError):
        Registry((tmp_path / "no-such-remote").as_uri())


def test_registry_with_fetch_false_reuses_cache_without_fetching(tmp_path, monkeypatch):
    remote = _init_remote(tmp_path)

    first = Registry(remote.as_uri())

    _write_sample(remote, "SLS042")
    _git(["add", "-A"], cwd=remote)
    _git(["commit", "-qm", "update"], cwd=remote)

    def _boom(*args, **kwargs):
        raise AssertionError("fetch=False should not fetch")

    monkeypatch.setattr("dataset_registry._registry._fetch_latest", _boom)

    second = Registry(remote.as_uri(), fetch=False)

    assert second.path == first.path
    assert second.commit == first.commit
    assert second.list_samples() == ["SLS161"]  # SLS042 not picked up -- no fetch happened


def test_registry_with_fetch_false_still_clones_when_no_cache_exists(tmp_path):
    remote = _init_remote(tmp_path)

    registry = Registry(remote.as_uri(), fetch=False)

    assert registry.list_samples() == ["SLS161"]
