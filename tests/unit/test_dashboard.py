"""Tests for the Phoenix dashboard launcher."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_dashboard_main_missing_phoenix():
    """When arize-phoenix is not installed, exit with a helpful message."""
    from src.dashboard.server import main

    # Simulate ImportError for phoenix
    with patch.dict(sys.modules, {"phoenix": None}):
        with pytest.raises(SystemExit) as exc_info:
            main(["--port", "9999"])
        assert exc_info.value.code == 1


def test_dashboard_creates_storage_dir(tmp_path: Path):
    """Storage directory should be created when it doesn't exist."""
    from src.dashboard.server import main

    storage = tmp_path / "nested" / "phoenix_store"
    assert not storage.exists()

    mock_session = MagicMock()
    mock_px = MagicMock()
    mock_px.launch_app.return_value = mock_session
    # Simulate user pressing Enter to stop
    mock_session.view.return_value = None

    with (
        patch.dict(sys.modules, {"phoenix": mock_px}),
        patch("src.dashboard.server.px", mock_px, create=True),
        patch("builtins.input", side_effect=KeyboardInterrupt),
    ):
        # We need to re-import to pick up the patched module
        import importlib

        import src.dashboard.server as srv

        importlib.reload(srv)
        try:
            srv.main(["--port", "7777", "--storage-dir", str(storage)])
        except (SystemExit, KeyboardInterrupt):
            pass

    assert storage.exists()
