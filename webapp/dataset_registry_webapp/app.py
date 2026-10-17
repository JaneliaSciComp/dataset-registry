"""Routes for the dataset-registry web app: list, view, create, and edit samples/representations."""

from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dataset_registry import Registry

_PACKAGE_DIR = Path(__file__).parent


def _registry_title(registry: Registry) -> str:
    """A human-readable title for a registry.

    Uses `registry.toml`'s `title` field when set; otherwise falls back to
    a title derived from the registry root's directory name (which mangles
    acronyms, e.g. "SEM-tracking" -> "Sem Tracking" -- set `title` in
    `registry.toml` to avoid that).
    """
    title = registry.load_registry_info().title
    if title is not None:
        return title
    return registry.path.name.replace("-", " ").replace("_", " ").title()


def _coerce_metadata_value(text: str) -> Any:
    """Parse a metadata value typed as plain text back into int/float/bool/str.

    The metadata row UI only has one text field per value (no per-field
    type picker), so a value typed as e.g. ``4`` or ``true`` should round-trip
    as an int/bool rather than becoming the string ``"4"``/``"true"`` --
    matching what hand-editing the TOML directly would produce.
    """
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _parse_metadata(keys: list[str], values: list[str]) -> dict[str, Any]:
    metadata = {}
    for key, value in zip(keys, values, strict=True):
        key = key.strip()
        if key:
            metadata[key] = _coerce_metadata_value(value)
    return metadata


def _depends_on_names(registry: Registry, sample_id: str, depends_on: list[str]) -> str:
    names = [registry.load_representation(sample_id, dep_id).name for dep_id in depends_on]
    return ", ".join(names)


def create_app(registry: Registry) -> FastAPI:
    """Build the FastAPI app for browsing and editing `registry`.

    Args:
        registry: The registry to serve.

    Returns:
        A FastAPI app with routes for listing, viewing, creating, and
        editing every sample and representation in `registry`.
    """
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=_PACKAGE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=_PACKAGE_DIR / "templates")

    def _render(request: Request, template_name: str, context: dict[str, Any]) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            template_name,
            {"registry_title": _registry_title(registry), "subtitle": None, **context},
        )

    @app.get("/")
    def index() -> RedirectResponse:
        return RedirectResponse("/samples")

    @app.get("/samples")
    def list_samples(request: Request) -> HTMLResponse:
        samples = [
            {
                "id": sample_id,
                "name": registry.load_sample(sample_id).name,
                "description": registry.load_sample(sample_id).description,
                "n_representations": len(registry.list_representations(sample_id)),
            }
            for sample_id in registry.list_samples()
        ]
        return _render(request, "samples_list.html", {"samples": samples})

    @app.get("/samples/new")
    def new_sample_form(request: Request) -> HTMLResponse:
        return _render(
            request,
            "sample_form.html",
            {
                "sample": None,
                "rows": [],
                "add_row_url": "/samples/metadata-row",
                "action": "/samples",
                "cancel_url": "/samples",
                "error": None,
            },
        )

    @app.post("/samples")
    def create_sample(
        name: Annotated[str, Form()],
        description: Annotated[str, Form()] = "",
        metadata_key: Annotated[list[str], Form()] = [],  # noqa: B006
        metadata_value: Annotated[list[str], Form()] = [],  # noqa: B006
    ) -> RedirectResponse:
        metadata = _parse_metadata(metadata_key, metadata_value)
        sample_id = registry.create_sample(name, description=description or None, metadata=metadata)
        return RedirectResponse(f"/samples/{sample_id}", status_code=303)

    @app.post("/samples/metadata-row")
    def add_sample_metadata_row(
        request: Request,
        metadata_key: Annotated[list[str], Form()] = [],  # noqa: B006
        metadata_value: Annotated[list[str], Form()] = [],  # noqa: B006
    ) -> HTMLResponse:
        rows = [*zip(metadata_key, metadata_value, strict=True), ("", "")]
        return templates.TemplateResponse(
            request,
            "_metadata_rows.html",
            {"rows": rows, "add_row_url": "/samples/metadata-row"},
        )

    @app.get("/samples/{sample_id}")
    def sample_detail(request: Request, sample_id: str) -> HTMLResponse:
        sample = registry.load_sample(sample_id)
        representations = [
            {
                "id": representation_id,
                "name": (
                    representation := registry.load_representation(sample_id, representation_id)
                ).name,
                "path": representation.path,
                "description": representation.description,
                "depends_on_names": _depends_on_names(
                    registry, sample_id, representation.depends_on or []
                ),
            }
            for representation_id in registry.list_representations(sample_id)
        ]
        return _render(
            request,
            "sample_detail.html",
            {
                "subtitle": sample.name,
                "sample": sample,
                "metadata": sample.metadata.model_dump(exclude_none=True),
                "representations": representations,
            },
        )

    @app.get("/samples/{sample_id}/edit")
    def edit_sample_form(request: Request, sample_id: str) -> HTMLResponse:
        sample = registry.load_sample(sample_id)
        return _render(
            request,
            "sample_form.html",
            {
                "subtitle": sample.name,
                "sample": sample,
                "rows": list(sample.metadata.model_dump(exclude_none=True).items()),
                "add_row_url": "/samples/metadata-row",
                "action": f"/samples/{sample_id}",
                "cancel_url": f"/samples/{sample_id}",
                "error": None,
            },
        )

    @app.post("/samples/{sample_id}")
    def update_sample(
        sample_id: str,
        name: Annotated[str, Form()],
        description: Annotated[str, Form()] = "",
        metadata_key: Annotated[list[str], Form()] = [],  # noqa: B006
        metadata_value: Annotated[list[str], Form()] = [],  # noqa: B006
    ) -> RedirectResponse:
        metadata = _parse_metadata(metadata_key, metadata_value)
        with registry.edit_sample(sample_id) as sample:
            sample.name = name
            sample.description = description or None
            sample.metadata = metadata
        return RedirectResponse(f"/samples/{sample_id}", status_code=303)

    @app.get("/samples/{sample_id}/representations/new")
    def new_representation_form(request: Request, sample_id: str) -> HTMLResponse:
        sample = registry.load_sample(sample_id)
        other_representations = [
            registry.load_representation(sample_id, representation_id)
            for representation_id in registry.list_representations(sample_id)
        ]
        return _render(
            request,
            "representation_form.html",
            {
                "subtitle": sample.name,
                "sample": sample,
                "representation": None,
                "other_representations": other_representations,
                "selected_depends_on": [],
                "rows": [],
                "add_row_url": f"/samples/{sample_id}/representations/metadata-row",
                "action": f"/samples/{sample_id}/representations",
                "cancel_url": f"/samples/{sample_id}",
                "error": None,
            },
        )

    @app.post("/samples/{sample_id}/representations")
    def create_representation(
        sample_id: str,
        name: Annotated[str, Form()],
        path: Annotated[str, Form()],
        description: Annotated[str, Form()] = "",
        depends_on: Annotated[list[str], Form()] = [],  # noqa: B006
        metadata_key: Annotated[list[str], Form()] = [],  # noqa: B006
        metadata_value: Annotated[list[str], Form()] = [],  # noqa: B006
    ) -> RedirectResponse:
        metadata = _parse_metadata(metadata_key, metadata_value)
        representation_id = registry.create_representation(
            sample_id,
            name,
            path=path,
            description=description or None,
            depends_on=depends_on or None,
            metadata=metadata,
        )
        return RedirectResponse(
            f"/samples/{sample_id}/representations/{representation_id}", status_code=303
        )

    @app.post("/samples/{sample_id}/representations/metadata-row")
    def add_representation_metadata_row(
        request: Request,
        sample_id: str,
        metadata_key: Annotated[list[str], Form()] = [],  # noqa: B006
        metadata_value: Annotated[list[str], Form()] = [],  # noqa: B006
    ) -> HTMLResponse:
        rows = [*zip(metadata_key, metadata_value, strict=True), ("", "")]
        return templates.TemplateResponse(
            request,
            "_metadata_rows.html",
            {"rows": rows, "add_row_url": f"/samples/{sample_id}/representations/metadata-row"},
        )

    @app.get("/samples/{sample_id}/representations/{representation_id}")
    def representation_detail(
        request: Request, sample_id: str, representation_id: str
    ) -> HTMLResponse:
        sample = registry.load_sample(sample_id)
        representation = registry.load_representation(sample_id, representation_id)
        return _render(
            request,
            "representation_detail.html",
            {
                "subtitle": f"{sample.name} / {representation.name}",
                "sample": sample,
                "representation": representation,
                "metadata": representation.metadata.model_dump(exclude_none=True),
                "depends_on_names": _depends_on_names(
                    registry, sample_id, representation.depends_on or []
                ),
            },
        )

    @app.get("/samples/{sample_id}/representations/{representation_id}/edit")
    def edit_representation_form(
        request: Request, sample_id: str, representation_id: str
    ) -> HTMLResponse:
        sample = registry.load_sample(sample_id)
        representation = registry.load_representation(sample_id, representation_id)
        other_representations = [
            registry.load_representation(sample_id, other_id)
            for other_id in registry.list_representations(sample_id)
            if other_id != representation_id
        ]
        return _render(
            request,
            "representation_form.html",
            {
                "subtitle": f"{sample.name} / {representation.name}",
                "sample": sample,
                "representation": representation,
                "other_representations": other_representations,
                "selected_depends_on": representation.depends_on or [],
                "rows": list(representation.metadata.model_dump(exclude_none=True).items()),
                "add_row_url": f"/samples/{sample_id}/representations/metadata-row",
                "action": f"/samples/{sample_id}/representations/{representation_id}",
                "cancel_url": f"/samples/{sample_id}/representations/{representation_id}",
                "error": None,
            },
        )

    @app.post("/samples/{sample_id}/representations/{representation_id}")
    def update_representation(
        sample_id: str,
        representation_id: str,
        name: Annotated[str, Form()],
        path: Annotated[str, Form()],
        description: Annotated[str, Form()] = "",
        depends_on: Annotated[list[str], Form()] = [],  # noqa: B006
        metadata_key: Annotated[list[str], Form()] = [],  # noqa: B006
        metadata_value: Annotated[list[str], Form()] = [],  # noqa: B006
    ) -> RedirectResponse:
        metadata = _parse_metadata(metadata_key, metadata_value)
        with registry.edit_representation(sample_id, representation_id) as representation:
            representation.name = name
            representation.path = path
            representation.description = description or None
            representation.depends_on = depends_on or None
            representation.metadata = metadata
        return RedirectResponse(
            f"/samples/{sample_id}/representations/{representation_id}", status_code=303
        )

    return app
