# dataset-registry

[![CI](https://github.com/JaneliaSciComp/dataset_registry/actions/workflows/ci.yaml/badge.svg)](https://github.com/JaneliaSciComp/dataset_registry/actions/workflows/ci.yaml)
[![codecov](https://codecov.io/gh/JaneliaSciComp/dataset_registry/branch/main/graph/badge.svg)](https://codecov.io/gh/JaneliaSciComp/dataset_registry)

A version-controlled way to keep track of dataset paths and refer to them by
name in code. Data moves -- to a new drive, a new fileshare, a new cluster --
without this registry, every script that hardcodes a path breaks. With it,
code refers to a stable id; only the registry's TOML files need to change
when a path moves.

A registry is a directory of plain TOML files (usually kept in git), one
subdirectory per **sample**, each with one TOML file per **representation**
(a named path to a piece of data belonging to that sample):

```
<registry root>/
  <sample directory>/
    sample.toml
    representations/
      <representation file>.toml
      ...
```

Both samples and representations carry a free-form `metadata` table, so a
project can layer its own validated fields on top (e.g. a pydantic model
that parses `metadata`) without any change to this package.

## Installation

```bash
pip install git+https://github.com/JaneliaSciComp/dataset_registry
```

To also use the `dsr browse` command, install the `browse` extra:

```bash
pip install "dataset-registry[browse] @ git+https://github.com/JaneliaSciComp/dataset_registry"
```

To get `dsr` on your `PATH` from anywhere (not just inside a project's own
virtual environment), install it as a [uv tool](https://docs.astral.sh/uv/guides/tools/)
instead:

```bash
uv tool install "dataset-registry[browse] @ git+https://github.com/JaneliaSciComp/dataset_registry"
```

## Usage

```python
from dataset_registry import Registry

# A local directory, or a git URL (cloned/cached automatically):
registry = Registry("/path/to/registry")
# registry = Registry("https://github.com/<owner>/<registry-repo>")

sample_id = registry.create_sample("Embryo A", description="a lightsheet embryo")
representation_id = registry.create_representation(
    sample_id, "Raw scan", path="/groups/shroff/raw.tif"
)

representation = registry.load_representation(sample_id, representation_id)
print(representation.path)  # "/groups/shroff/raw.tif"
```

If the data later moves, update `path` in place -- every caller that loads
this representation by id picks up the new location automatically:

```python
registry.set_representation_fields(sample_id, representation_id, path="/nrs/shroff/raw.tif")
```

### Git URL vs. local directory: two workflows

`Registry` accepts either a git URL or a local directory, and deliberately
treats them differently -- pick whichever matches how your team works:

- **Git URL** (`Registry("https://github.com/<owner>/<repo>")`): for teams
  where everyone is comfortable with git. Each user gets their own clone,
  cached under a per-user directory and re-fetched (reset to the remote's
  default branch tip) on every `Registry(...)` call by default. Anyone can
  edit and `git push` their own changes; everyone else picks them up next
  time they open the registry. Pass `fetch=False` to skip the network
  round-trip and reuse whatever was last fetched.
- **Local directory** (`Registry("/path/to/registry")`): for a shared
  location (e.g. a fileshare) that non-git-users edit directly, often
  through `dsr browse`. `Registry` is entirely git-oblivious here -- even if
  the directory happens to be a git working tree, it never fetches, resets,
  or commits anything on your behalf. A team lead can point the browser at
  that shared path, let collaborators edit through it, and separately run
  `git add`/`commit`/`push` by hand whenever they want to snapshot what
  accumulated.

### Browsing a registry

```bash
dsr browse /path/to/registry
```

Serves a live, sortable/filterable table of every sample and representation
in the registry, with pages to create and edit samples and representations.
Requires the `browse` extra (`fastapi`, `jinja2`, `uvicorn`).

## Development

This project uses [uv](https://docs.astral.sh/uv/) and
[just](https://just.systems). To set up a development environment:

```bash
git clone https://github.com/JaneliaSciComp/dataset_registry
cd dataset_registry
just install
```

See [CONTRIBUTING.md](https://github.com/JaneliaSciComp/dataset_registry/blob/main/CONTRIBUTING.md)
for more, or run `just` to list all available recipes.
