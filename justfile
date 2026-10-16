# https://just.systems

# list available recipes
default:
    @just --list

# install the dev environment and pre-commit hooks
install:
    uv sync
    uv run pre-commit install

# run tests with pytest
test:
    uv run pytest

# run tests with pytest and show coverage
test-cov:
    uv run pytest --cov --cov-report=term-missing

# run linting and formatting on all files
lint:
    uv run pre-commit run --all-files

# run type checking
typecheck:
    uv run pre-commit run mypy --all-files

# upgrade locked dependency versions (commit the resulting uv.lock)
upgrade:
    uv lock --upgrade

# upgrade a single locked dependency, e.g. `just upgrade-package numpy`
upgrade-package package:
    uv lock --upgrade-package {{ package }}

# serve a live, sortable/filterable view of a registry, e.g. `just browse ~/data-registries/SEM-tracking`
browse registry_root:
    uv run --extra browse python scripts/open_browser.py {{ registry_root }}

# build wheel and sdist
build:
    uv build

# tag and release <version>
release version:
    git tag -a {{ version }} -m {{ version }}
    git push origin --follow-tags
