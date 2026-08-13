"""Acceptance tests for GitLab Duo MCP adapter support."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from apm_cli.adapters.client.gitlab_duo import GitLabDuoClientAdapter
from apm_cli.factory import ClientFactory


class TestGitLabDuoClientFactory:
    """Verify GitLabDuoClientAdapter registration."""

    def test_factory_creates_gitlab_duo_adapter(self) -> None:
        adapter = ClientFactory.create_client("gitlab-duo")
        assert isinstance(adapter, GitLabDuoClientAdapter)

    def test_factory_accepts_case_insensitive_name(self) -> None:
        adapter = ClientFactory.create_client("GitLab-Duo")
        assert isinstance(adapter, GitLabDuoClientAdapter)

    def test_supported_clients_includes_gitlab_duo(self) -> None:
        assert "gitlab-duo" in ClientFactory.supported_clients()


class TestGitLabDuoClientAdapter:
    """Core config operations for GitLabDuoClientAdapter."""

    def test_config_path_is_gitlab_duo_mcp_json(self, tmp_path: Path) -> None:
        adapter = GitLabDuoClientAdapter(project_root=tmp_path)
        assert adapter.get_config_path() == str(tmp_path / ".gitlab" / "duo" / "mcp.json")

    def test_supports_user_scope_is_true(self) -> None:
        assert GitLabDuoClientAdapter.supports_user_scope is True

    def test_client_label_is_gitlab_duo(self) -> None:
        assert GitLabDuoClientAdapter._client_label == "GitLab Duo"

    def test_update_config_skips_project_without_gitlab_duo_dir(self, tmp_path: Path) -> None:
        adapter = GitLabDuoClientAdapter(project_root=tmp_path)
        adapter.update_config({"srv": {"type": "stdio", "command": "node"}})
        assert not (tmp_path / ".gitlab" / "duo" / "mcp.json").exists()

    def test_update_config_writes_inside_existing_gitlab_duo_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".gitlab" / "duo").mkdir(parents=True)
        adapter = GitLabDuoClientAdapter(project_root=tmp_path)

        adapter.update_config({"srv": {"type": "stdio", "command": "node", "args": ["server.js"]}})

        mcp_json = tmp_path / ".gitlab" / "duo" / "mcp.json"
        data = json.loads(mcp_json.read_text(encoding="utf-8"))
        assert data["mcpServers"]["srv"]["type"] == "stdio"
        assert data["mcpServers"]["srv"]["command"] == "node"
        assert data["mcpServers"]["srv"]["args"] == ["server.js"]
        if sys.platform != "win32":
            assert mcp_json.stat().st_mode & 0o777 == 0o600

    def test_configure_mcp_server_skips_silently_without_dir(self, tmp_path: Path) -> None:
        adapter = GitLabDuoClientAdapter(project_root=tmp_path)
        result = adapter.configure_mcp_server(
            "some/server",
            server_name="srv",
            server_info_cache={"some/server": {"name": "srv", "_raw_stdio": {"command": "node"}}},
        )
        assert result is True
        assert not (tmp_path / ".gitlab" / "duo").exists()


class TestGitLabDuoFormatServerConfig:
    """``_format_server_config`` must emit GitLab Duo's schema."""

    def test_raw_stdio_emits_type_stdio_and_approved_tools(self) -> None:
        adapter = GitLabDuoClientAdapter()
        server_info = {
            "name": "my-server",
            "_raw_stdio": {
                "command": "path/to/server",
                "args": ["--arg1", "value1"],
                "env": {"ENV_VAR": "value"},
            },
        }

        config = adapter._format_server_config(server_info)

        assert config == {
            "type": "stdio",
            "command": "path/to/server",
            "env": {"ENV_VAR": "value"},
            "args": ["--arg1", "value1"],
            "approvedTools": True,
        }

    def test_http_remote_uses_type_http_and_tool_allowlist(self) -> None:
        adapter = GitLabDuoClientAdapter()
        server_info = {
            "name": "http-server",
            "remotes": [{"transport_type": "http", "url": "http://localhost:3000/mcp"}],
            "approved_tools": ["read_file", "search"],
        }

        config = adapter._format_server_config(server_info)

        assert config == {
            "type": "http",
            "url": "http://localhost:3000/mcp",
            "approvedTools": ["read_file", "search"],
        }

    def test_sse_remote_uses_type_sse(self) -> None:
        adapter = GitLabDuoClientAdapter()
        server_info = {
            "name": "sse-server",
            "remotes": [{"transport_type": "sse", "url": "http://localhost:3000/mcp/sse"}],
        }

        config = adapter._format_server_config(server_info)

        assert config["type"] == "sse"
        assert config["url"] == "http://localhost:3000/mcp/sse"

    def test_missing_transport_defaults_to_http(self) -> None:
        adapter = GitLabDuoClientAdapter()
        server_info = {
            "name": "default-server",
            "remotes": [{"url": "http://localhost:3000/mcp"}],
        }

        config = adapter._format_server_config(server_info)

        assert config["type"] == "http"

    def test_unsupported_transport_raises(self) -> None:
        import pytest

        adapter = GitLabDuoClientAdapter()
        server_info = {
            "name": "bad-server",
            "remotes": [{"transport_type": "websocket", "url": "wss://example.com"}],
        }

        with pytest.raises(ValueError, match="Unsupported remote transport"):
            adapter._format_server_config(server_info)

    def test_npm_package_dispatches_to_stdio(self) -> None:
        adapter = GitLabDuoClientAdapter()
        server_info = {
            "name": "npm-server",
            "packages": [
                {
                    "registry_name": "npm",
                    "name": "@scope/mcp-server",
                    "runtime_hint": "npx",
                }
            ],
        }

        config = adapter._format_server_config(server_info)

        assert config["type"] == "stdio"
        assert config["command"] == "npx"
        assert "@scope/mcp-server" in config["args"]
        assert config["approvedTools"] is True

    def test_tools_and_id_are_never_emitted(self) -> None:
        adapter = GitLabDuoClientAdapter()
        server_info = {
            "id": "registry-uuid",
            "name": "my-server",
            "_raw_stdio": {"command": "node"},
        }

        config = adapter._format_server_config(server_info)

        assert "tools" not in config
        assert "id" not in config


class TestGitLabDuoRuntimeDiscovery:
    """GitLab Duo's presence signal is the ``.gitlab/duo`` directory."""

    def test_not_discovered_without_dir(self, tmp_path: Path) -> None:
        from apm_cli.integration.mcp_integrator_install import _discover_installed_runtimes

        runtimes = _discover_installed_runtimes(tmp_path, user_scope=False)

        assert "gitlab-duo" not in runtimes

    def test_discovered_with_dir(self, tmp_path: Path) -> None:
        from apm_cli.integration.mcp_integrator_install import _discover_installed_runtimes

        (tmp_path / ".gitlab" / "duo").mkdir(parents=True)
        runtimes = _discover_installed_runtimes(tmp_path, user_scope=False)

        assert "gitlab-duo" in runtimes

    def test_discovered_unconditionally_at_user_scope(self, tmp_path: Path) -> None:
        from apm_cli.integration.mcp_integrator_install import _discover_installed_runtimes

        assert not (tmp_path / ".gitlab").exists()

        runtimes = _discover_installed_runtimes(tmp_path, user_scope=True)

        assert "gitlab-duo" in runtimes


class TestGitLabDuoUserScopeConfigDir:
    """User-scope config directory resolution mirrors the ``glab`` CLI."""

    def _clear_env(self, monkeypatch) -> None:
        monkeypatch.delenv("GLAB_CONFIG_DIR", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("APPDATA", raising=False)

    def test_default_posix_path_is_home_gitlab_duo(self, monkeypatch, tmp_path: Path) -> None:
        self._clear_env(monkeypatch)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        adapter = GitLabDuoClientAdapter(user_scope=True)

        assert adapter.get_config_path() == str(tmp_path / ".gitlab" / "duo" / "mcp.json")

    def test_default_windows_path_is_appdata_gitlab_duo(self, monkeypatch, tmp_path: Path) -> None:
        self._clear_env(monkeypatch)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))

        adapter = GitLabDuoClientAdapter(user_scope=True)

        assert adapter.get_config_path() == str(
            tmp_path / "AppData" / "Roaming" / "GitLab" / "duo" / "mcp.json"
        )

    def test_glab_config_dir_overrides_default(self, monkeypatch, tmp_path: Path) -> None:
        self._clear_env(monkeypatch)
        monkeypatch.setattr(sys, "platform", "linux")
        custom = tmp_path / "custom-glab"
        monkeypatch.setenv("GLAB_CONFIG_DIR", str(custom))

        adapter = GitLabDuoClientAdapter(user_scope=True)

        assert adapter.get_config_path() == str(custom / "duo" / "mcp.json")

    def test_xdg_config_home_overrides_default(self, monkeypatch, tmp_path: Path) -> None:
        self._clear_env(monkeypatch)
        monkeypatch.setattr(sys, "platform", "linux")
        custom = tmp_path / "custom-xdg"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(custom))

        adapter = GitLabDuoClientAdapter(user_scope=True)

        assert adapter.get_config_path() == str(custom / "gitlab" / "duo" / "mcp.json")

    def test_glab_config_dir_takes_precedence_over_xdg(self, monkeypatch, tmp_path: Path) -> None:
        self._clear_env(monkeypatch)
        monkeypatch.setattr(sys, "platform", "linux")
        glab_dir = tmp_path / "glab-wins"
        monkeypatch.setenv("GLAB_CONFIG_DIR", str(glab_dir))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-loses"))

        adapter = GitLabDuoClientAdapter(user_scope=True)

        assert adapter.get_config_path() == str(glab_dir / "duo" / "mcp.json")

    def test_non_absolute_glab_config_dir_raises(self, monkeypatch) -> None:
        import pytest

        from apm_cli.utils.path_security import PathTraversalError

        self._clear_env(monkeypatch)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("GLAB_CONFIG_DIR", "relative/path")

        adapter = GitLabDuoClientAdapter(user_scope=True)

        with pytest.raises(PathTraversalError):
            adapter.get_config_path()

    def test_project_scope_ignores_user_scope_env_vars(self, monkeypatch, tmp_path: Path) -> None:
        self._clear_env(monkeypatch)
        monkeypatch.setenv("GLAB_CONFIG_DIR", str(tmp_path / "should-not-be-used"))

        adapter = GitLabDuoClientAdapter(project_root=tmp_path)

        assert adapter.get_config_path() == str(tmp_path / ".gitlab" / "duo" / "mcp.json")


class TestGitLabDuoUserScopeReadWrite:
    """User-scope reads/writes always proceed and create the directory on demand."""

    def _clear_env(self, monkeypatch) -> None:
        monkeypatch.delenv("GLAB_CONFIG_DIR", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    def test_update_config_creates_directory_on_demand(self, monkeypatch, tmp_path: Path) -> None:
        self._clear_env(monkeypatch)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        adapter = GitLabDuoClientAdapter(user_scope=True)
        assert not (tmp_path / ".gitlab").exists()

        result = adapter.update_config({"srv": {"type": "stdio", "command": "node"}})

        assert result is True
        mcp_json = tmp_path / ".gitlab" / "duo" / "mcp.json"
        data = json.loads(mcp_json.read_text(encoding="utf-8"))
        assert data["mcpServers"]["srv"]["command"] == "node"
        if sys.platform != "win32":
            assert mcp_json.stat().st_mode & 0o777 == 0o600

    def test_configure_mcp_server_proceeds_without_any_directory(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        self._clear_env(monkeypatch)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        adapter = GitLabDuoClientAdapter(user_scope=True)
        result = adapter.configure_mcp_server(
            "some/server",
            server_name="srv",
            server_info_cache={"some/server": {"name": "srv", "_raw_stdio": {"command": "node"}}},
        )

        assert result is True
        mcp_json = tmp_path / ".gitlab" / "duo" / "mcp.json"
        assert mcp_json.exists()
        data = json.loads(mcp_json.read_text(encoding="utf-8"))
        assert data["mcpServers"]["srv"]["command"] == "node"
