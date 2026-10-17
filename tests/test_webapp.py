import pytest
from fastapi.testclient import TestClient

from dataset_registry import Registry
from dataset_registry_webapp import create_app


@pytest.fixture
def registry(tmp_path):
    return Registry(tmp_path)


@pytest.fixture
def client(registry):
    return TestClient(create_app(registry))


def test_index_redirects_to_samples(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/samples"


def test_samples_list_shows_no_samples_message_when_empty(client):
    response = client.get("/samples")

    assert response.status_code == 200
    assert "No samples yet." in response.text


def test_create_sample_via_form_persists_it_and_redirects_to_detail(client, registry):
    response = client.post(
        "/samples",
        data={
            "name": "Embryo A",
            "description": "a lightsheet embryo",
            "metadata_key": ["stage"],
            "metadata_value": ["gastrula"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    (sample_id,) = registry.list_samples()
    assert response.headers["location"] == f"/samples/{sample_id}"
    sample = registry.load_sample(sample_id)
    assert sample.name == "Embryo A"
    assert sample.description == "a lightsheet embryo"
    assert sample.metadata.model_dump() == {"stage": "gastrula"}


def test_sample_detail_shows_its_fields_and_representations(client, registry):
    sample_id = registry.create_sample("Embryo A", description="a lightsheet embryo")
    registry.create_representation(sample_id, "Raw scan", path="/data/raw.tif")

    response = client.get(f"/samples/{sample_id}")

    assert response.status_code == 200
    assert "Embryo A" in response.text
    assert "a lightsheet embryo" in response.text
    assert "Raw scan" in response.text
    assert "/data/raw.tif" in response.text


def test_update_sample_via_form_renames_and_updates_fields(client, registry):
    sample_id = registry.create_sample("Embryo A")

    response = client.post(
        f"/samples/{sample_id}",
        data={"name": "Embryo A (renamed)", "description": "updated"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    sample = registry.load_sample(sample_id)
    assert sample.name == "Embryo A (renamed)"
    assert sample.description == "updated"


def test_create_representation_via_form_persists_it_with_depends_on(client, registry):
    sample_id = registry.create_sample("Embryo A")
    raw_id = registry.create_representation(sample_id, "Raw scan", path="/data/raw.tif")

    response = client.post(
        f"/samples/{sample_id}/representations",
        data={
            "name": "Cellpose v3",
            "path": "/data/seg.tif",
            "depends_on": [raw_id],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    [seg_id] = [r for r in registry.list_representations(sample_id) if r != raw_id]
    assert response.headers["location"] == f"/samples/{sample_id}/representations/{seg_id}"
    representation = registry.load_representation(sample_id, seg_id)
    assert representation.name == "Cellpose v3"
    assert representation.depends_on == [raw_id]


def test_create_representation_via_form_persists_description(client, registry):
    sample_id = registry.create_sample("Embryo A")

    response = client.post(
        f"/samples/{sample_id}/representations",
        data={
            "name": "Raw scan",
            "path": "/data/raw.tif",
            "description": "the raw lightsheet scan",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    (representation_id,) = registry.list_representations(sample_id)
    representation = registry.load_representation(sample_id, representation_id)
    assert representation.description == "the raw lightsheet scan"


def test_update_representation_via_form_changes_description(client, registry):
    sample_id = registry.create_sample("Embryo A")
    representation_id = registry.create_representation(sample_id, "Raw scan", path="/data/raw.tif")

    response = client.post(
        f"/samples/{sample_id}/representations/{representation_id}",
        data={"name": "Raw scan", "path": "/data/raw.tif", "description": "updated"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    representation = registry.load_representation(sample_id, representation_id)
    assert representation.description == "updated"


def test_representation_detail_shows_depends_on_names(client, registry):
    sample_id = registry.create_sample("Embryo A")
    raw_id = registry.create_representation(sample_id, "Raw scan", path="/data/raw.tif")
    seg_id = registry.create_representation(
        sample_id, "Cellpose v3", path="/data/seg.tif", depends_on=[raw_id]
    )

    response = client.get(f"/samples/{sample_id}/representations/{seg_id}")

    assert response.status_code == 200
    assert "Raw scan" in response.text


def test_update_representation_via_form_changes_depends_on(client, registry):
    sample_id = registry.create_sample("Embryo A")
    raw_id = registry.create_representation(sample_id, "Raw scan", path="/data/raw.tif")
    seg_id = registry.create_representation(sample_id, "Cellpose v3", path="/data/seg.tif")

    response = client.post(
        f"/samples/{sample_id}/representations/{seg_id}",
        data={"name": "Cellpose v3", "path": "/data/seg.tif", "depends_on": [raw_id]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    representation = registry.load_representation(sample_id, seg_id)
    assert representation.depends_on == [raw_id]


def test_edit_sample_form_prefills_existing_metadata_rows(client, registry):
    sample_id = registry.create_sample("Embryo A", metadata={"stage": "gastrula"})

    response = client.get(f"/samples/{sample_id}/edit")

    assert response.status_code == 200
    assert 'value="stage"' in response.text
    assert 'value="gastrula"' in response.text


def test_edit_representation_form_prefills_existing_metadata_rows(client, registry):
    sample_id = registry.create_sample("Embryo A")
    representation_id = registry.create_representation(
        sample_id, "Raw scan", path="/data/raw.tif", metadata={"channel": "nuclear"}
    )

    response = client.get(f"/samples/{sample_id}/representations/{representation_id}/edit")

    assert response.status_code == 200
    assert 'value="channel"' in response.text
    assert 'value="nuclear"' in response.text


def test_saving_sample_edit_form_without_touching_metadata_keeps_it(client, registry):
    sample_id = registry.create_sample("Embryo A", metadata={"stage": "gastrula"})

    response = client.post(
        f"/samples/{sample_id}",
        data={
            "name": "Embryo A",
            "description": "",
            "metadata_key": ["stage"],
            "metadata_value": ["gastrula"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    sample = registry.load_sample(sample_id)
    assert sample.metadata.model_dump() == {"stage": "gastrula"}


def test_add_metadata_row_endpoint_appends_a_blank_row(client):
    response = client.post(
        "/samples/metadata-row",
        data={"metadata_key": ["stage"], "metadata_value": ["gastrula"]},
    )

    assert response.status_code == 200
    assert response.text.count('name="metadata_key"') == 2
