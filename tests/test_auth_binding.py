"""
SGOS Backend — Auth Binding Safety Tests (P0)

Verifies that the server fails closed when binding beyond loopback
without authentication, while preserving explicit loopback dev mode.
"""
import sys
from unittest.mock import patch

import pytest


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class TestAuthBindingSafety:
    """Server MUST refuse to start when non-loopback + no api_key."""

    def test_non_loopback_without_api_key_raises(self):
        """Binding 0.0.0.0 with empty api_key must raise at startup."""
        from config import Settings
        s = Settings(host="0.0.0.0", api_key="", port=8420)
        from main import validate_binding_safety
        with pytest.raises(SystemExit) as exc_info:
            validate_binding_safety(s)
        assert exc_info.value.code == 1

    def test_non_loopback_with_api_key_passes(self):
        """Binding 0.0.0.0 WITH api_key must pass."""
        from config import Settings
        s = Settings(host="0.0.0.0", api_key="test-secret-key-12345", port=8420)
        from main import validate_binding_safety
        # Should not raise
        validate_binding_safety(s)

    def test_loopback_without_api_key_passes(self):
        """Binding 127.0.0.1 without api_key (dev mode) must pass."""
        from config import Settings
        for host in LOOPBACK_HOSTS:
            s = Settings(host=host, api_key="", port=8420)
            from main import validate_binding_safety
            # Should not raise
            validate_binding_safety(s)

    def test_refusal_blocks_before_db_init(self):
        """validate_binding_safety exits before init_db or router setup runs."""
        from config import Settings
        s = Settings(host="0.0.0.0", api_key="", port=8420)
        from main import validate_binding_safety
        with patch("main.init_db") as mock_db, \
             pytest.raises(SystemExit):
            validate_binding_safety(s)
        mock_db.assert_not_called()


class TestStartScriptHostConsistency:
    """start.sh must not override validated host with hardcoded 0.0.0.0."""

    def _parse_start_sh(self):
        """Extract the uvicorn invocation line from start.sh."""
        from pathlib import Path
        content = (Path(__file__).resolve().parent.parent / "start.sh").read_text()
        return content

    def test_default_host_is_loopback(self):
        """start.sh default --host must be 127.0.0.1, not 0.0.0.0."""
        content = self._parse_start_sh()
        # Find the uvicorn line's --host value
        import re
        match = re.search(r'--host\s+(\S+)', content)
        assert match, "start.sh must contain --host flag"
        host_val = match.group(1)
        # Must be a variable reference or loopback literal, never bare 0.0.0.0
        assert host_val != "0.0.0.0", (
            f"start.sh hardcodes --host 0.0.0.0, bypassing validate_binding_safety"
        )

    def test_sgosh_env_passed_to_uvicorn(self):
        """start.sh must export SGOS_HOST and pass it directly to --host."""
        content = self._parse_start_sh()
        import re
        # Must export SGOS_HOST so pydantic-settings sees the same value
        assert re.search(r'export\s+SGOS_HOST=', content), (
            "start.sh must 'export SGOS_HOST=' so config.py and uvicorn agree"
        )
        # --host must use $SGOS_HOST directly (not an intermediate variable)
        match = re.search(r'--host\s+"?\$SGOS_HOST"?', content)
        assert match, (
            "start.sh --host must use $SGOS_HOST directly, not an alias"
        )
        # Default must be loopback
        default_match = re.search(
            r'export\s+SGOS_HOST="\$\{SGOS_HOST:-(.+?)\}"', content
        )
        assert default_match, "SGOS_HOST must have a fallback default"
        assert default_match.group(1) in ("127.0.0.1", "::1", "localhost"), (
            f"Default host '{default_match.group(1)}' is not loopback"
        )
