from pathlib import Path
from typing import Literal

import pytest
import tomli_w
from pydantic import BaseModel, ConfigDict, ValidationError

from dataset_registry._record_store import SAMPLE_FILENAME, RecordStore
from dataset_registry._schema import Sample


def write_toml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        tomli_w.dump(data, f)


class MicroscopyMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["microscopy"]
    objective: str
    magnification: float = 20.0


class SequencingMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["sequencing"]
    platform: str
    read_length: int = 150


def test_multiple_metadata_models(tmp_path: Path):
    store = RecordStore[Sample](
        Sample,
        [MicroscopyMetadata, SequencingMetadata],
        tmp_path,
        SAMPLE_FILENAME,
    )

    microscopy_sample = Sample(
        id="microscopy-sample",
        name="Microscopy Sample",
        metadata=MicroscopyMetadata(type="microscopy", objective="20x"),
    )
    sequencing_sample = Sample(
        id="sequencing-sample",
        name="Sequencing Sample",
        metadata=SequencingMetadata(type="sequencing", platform="illumina"),
    )

    store.write(microscopy_sample)
    store.write(sequencing_sample)

    loaded_microscopy = store.load("microscopy-sample")
    assert isinstance(loaded_microscopy.metadata, MicroscopyMetadata)
    assert loaded_microscopy.metadata.objective == "20x"
    assert loaded_microscopy.metadata.magnification == 20.0

    loaded_sequencing = store.load("sequencing-sample")
    assert isinstance(loaded_sequencing.metadata, SequencingMetadata)
    assert loaded_sequencing.metadata.platform == "illumina"
    assert loaded_sequencing.metadata.read_length == 150


def test_metadata_type_mismatch_raises(tmp_path: Path):
    store = RecordStore[Sample](
        Sample,
        [MicroscopyMetadata, SequencingMetadata],
        tmp_path,
        SAMPLE_FILENAME,
    )

    write_toml(
        tmp_path / "bad-sample" / SAMPLE_FILENAME,
        {
            "id": "bad-sample",
            "name": "Bad Sample",
            "metadata": {"type": "microscopy", "objective": "20x", "read_length": 10},
        },
    )

    with pytest.raises(ValidationError):
        store.load("bad-sample")
