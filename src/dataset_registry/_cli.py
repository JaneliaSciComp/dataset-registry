"""The `dsr` command-line entry point, installed via `[project.scripts]`."""

import argparse
import webbrowser

from dataset_registry._registry import Registry

_BROWSE_PORT = 5006


def _browse(location: str) -> None:
    try:
        import uvicorn
    except ImportError as e:
        raise SystemExit(
            "the 'browse' extra is required for this command: "
            "pip install 'dataset-registry[browse]'"
        ) from e

    from dataset_registry_webapp import create_app

    registry = Registry(location)
    app = create_app(registry)
    webbrowser.open(f"http://localhost:{_BROWSE_PORT}/samples")
    uvicorn.run(app, port=_BROWSE_PORT)


def main() -> None:
    """Parse `sys.argv` and dispatch to the requested `dsr` subcommand."""
    parser = argparse.ArgumentParser(prog="dsr", description="dataset-registry command line tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    browse_parser = subparsers.add_parser(
        "browse", help="serve a live, sortable/filterable view of a registry"
    )
    browse_parser.add_argument(
        "location", help="a local registry directory, or a git URL to clone/cache"
    )

    args = parser.parse_args()
    if args.command == "browse":
        _browse(args.location)


if __name__ == "__main__":
    main()
