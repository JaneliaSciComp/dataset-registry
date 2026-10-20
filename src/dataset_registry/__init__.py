"""Registry for versioned dataset paths and metadata."""

from importlib.metadata import PackageNotFoundError, version

from ._record_store import RecordStore
from ._registry import Registry, create_registry
from ._schema import DataRecord, FreeformMetadata, RegistryInfo, Representation, Sample

try:
    __version__ = version("dataset-registry")
except PackageNotFoundError:  # package is not installed
    __version__ = "uninstalled"

# Everything listed here becomes the public API and gets an API docs page.
# Implementation lives in underscore-prefixed modules; see CONTRIBUTING.md.
__all__ = [
    "DataRecord",
    "FreeformMetadata",
    "RecordStore",
    "Registry",
    "RegistryInfo",
    "Representation",
    "Sample",
    "__version__",
    "create_registry",
]
