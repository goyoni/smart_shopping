"""Phoenix trace viewer launcher.

Starts an Arize Phoenix instance for viewing OpenTelemetry traces
collected by the smart-shopping-agent backend.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Launch Phoenix trace viewer")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PHOENIX_PORT", "6006")),
        help="Port for the Phoenix web UI (default: 6006)",
    )
    parser.add_argument(
        "--storage-dir",
        type=str,
        default=os.environ.get("PHOENIX_WORKING_DIR", "data/phoenix"),
        help="Directory for Phoenix SQLite storage (default: data/phoenix)",
    )
    args = parser.parse_args(argv)

    try:
        import phoenix  # noqa: F401
    except ImportError:
        print(
            "Error: arize-phoenix is not installed.\n"
            'Install it with: pip install -e ".[dashboard]"',
            file=sys.stderr,
        )
        sys.exit(1)

    storage_path = Path(args.storage_dir)
    storage_path.mkdir(parents=True, exist_ok=True)
    os.environ["PHOENIX_WORKING_DIR"] = str(storage_path.resolve())
    os.environ["PHOENIX_PORT"] = str(args.port)

    import phoenix as px

    print(f"Starting Phoenix trace viewer on http://localhost:{args.port}")
    print(f"Storage: {storage_path.resolve()}")
    print("Press Ctrl+C to stop.\n")

    px.launch_app()

    try:
        input("Phoenix is running. Press Enter or Ctrl+C to stop.\n")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        print("\nShutting down Phoenix...")


if __name__ == "__main__":
    main()
