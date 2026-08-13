"""GitLab Duo implementation of MCP client adapter.

GitLab Duo reads MCP server configuration from ``mcp.json`` at project scope
(``.gitlab/duo/mcp.json``) and at user scope. Every entry is wrapped under
``mcpServers`` and carries an explicit transport discriminator (``type``:
``stdio`` / ``http`` / ``sse``) plus an ``approvedTools`` field (boolean or
an explicit tool allowlist) recording whether the server's tools are
pre-approved for unattended use.

APM only writes to the project-scope ``.gitlab/duo/mcp.json`` when the
``.gitlab/duo/`` directory already exists -- GitLab Duo support is opt-in,
matching the Cursor/Kiro convention for tool-specific directories APM does
not create on its own. User scope always writes -- creating the config
directory on demand, matching Kiro/JetBrains.

User-scope config directory resolution (matching the ``glab`` CLI's own
config precedence):

1. ``$GLAB_CONFIG_DIR/duo`` when ``GLAB_CONFIG_DIR`` is set.
2. ``$XDG_CONFIG_HOME/gitlab/duo`` when ``XDG_CONFIG_HOME`` is set.
3. Platform default: ``%APPDATA%\\GitLab\\duo`` on Windows,
   ``~/.gitlab/duo`` on Linux/macOS.
"""

from __future__ import annotations

import json
import ntpath
import os
import posixpath
import sys
from pathlib import Path
from typing import Any

from ...core.token_manager import GitHubTokenManager
from ...utils.path_security import PathTraversalError, ensure_path_within
from .copilot import CopilotClientAdapter


def _absolute_env_path(name: str) -> Path | None:
    """Return an environment variable's value as an absolute ``Path``.

    Returns ``None`` when the variable is unset or empty. Raises
    :class:`PathTraversalError` when it is set but not an absolute path,
    so a misconfigured environment fails closed instead of silently
    resolving to an unintended location.
    """
    value = os.environ.get(name, "")
    if not value:
        return None
    path_module = ntpath if sys.platform == "win32" else posixpath
    if not path_module.isabs(value):
        raise PathTraversalError(
            f"{name} is set to a non-absolute path; cannot locate the "
            "GitLab Duo user configuration directory."
        )
    return Path(value)


def _gitlab_duo_user_config_dir() -> Path:
    """Return the canonical GitLab Duo user-scope MCP configuration directory."""
    glab_config_dir = _absolute_env_path("GLAB_CONFIG_DIR")
    if glab_config_dir is not None:
        config_dir = glab_config_dir / "duo"
        ensure_path_within(config_dir, glab_config_dir)
        return config_dir

    xdg_config_home = _absolute_env_path("XDG_CONFIG_HOME")
    if xdg_config_home is not None:
        config_dir = xdg_config_home / "gitlab" / "duo"
        ensure_path_within(config_dir, xdg_config_home)
        return config_dir

    if sys.platform == "win32":
        appdata = _absolute_env_path("APPDATA")
        if appdata is None:
            raise PathTraversalError(
                "APPDATA is unset or not an absolute path; cannot locate the "
                "GitLab Duo user configuration directory."
            )
        config_dir = appdata / "GitLab" / "duo"
        ensure_path_within(config_dir, appdata)
        return config_dir

    home = Path.home()
    config_dir = home / ".gitlab" / "duo"
    ensure_path_within(config_dir, home)
    return config_dir


class GitLabDuoClientAdapter(CopilotClientAdapter):
    """GitLab Duo MCP client adapter.

    Inherits config-path and read/write scaffolding from
    :class:`CopilotClientAdapter` but overrides ``_format_server_config`` to
    emit GitLab Duo's ``type`` transport discriminator and ``approvedTools``
    field instead of Copilot-only fields (``tools``, ``id``).
    """

    supports_user_scope: bool = True
    _client_label: str = "GitLab Duo"
    target_name: str = "duo"
    mcp_servers_key: str = "mcpServers"

    # GitLab Duo's runtime-substitution support has not been individually
    # audited (see #1152). Pin to legacy install-time resolution, matching
    # the other recently-added adapters (Cursor/Windsurf), until GitLab
    # Duo's own placeholder syntax is confirmed.
    _supports_runtime_env_substitution: bool = False

    def _apply_auth_and_headers(
        self, config, remote, server_info, env_overrides, runtime_label="GitLab Duo"
    ):
        """Inject GitHub token and registry-supplied headers into *config*.

        Overrides the parent to supply ``GitHubTokenManager`` from *this*
        module's namespace, allowing tests to patch
        ``apm_cli.adapters.client.gitlab_duo.GitHubTokenManager`` correctly.
        """
        self._apply_auth_and_headers_impl(
            config, remote, server_info, env_overrides, runtime_label, GitHubTokenManager
        )

    def _gitlab_duo_project_dir(self) -> Path:
        """Return the ``.gitlab/duo`` directory in the project."""
        return self.project_root / ".gitlab" / "duo"

    def _gitlab_duo_config_dir(self) -> Path:
        """Return the active-scope GitLab Duo configuration directory."""
        if self.user_scope:
            return _gitlab_duo_user_config_dir()
        return self._gitlab_duo_project_dir()

    def get_config_path(self) -> str:
        """Return the path to the active-scope ``mcp.json``.

        Project scope is a **repo-local** path: ``.gitlab/duo/`` is *not*
        created automatically -- APM only writes there when the directory
        already exists. User scope always resolves to a concrete path
        (created on demand by :meth:`update_config`).
        """
        return str(self._gitlab_duo_config_dir() / "mcp.json")

    def update_config(self, config_updates: dict[str, dict[str, Any]]) -> bool | None:
        """Merge *config_updates* into GitLab Duo's ``mcpServers`` object.

        At project scope, ``.gitlab/duo/`` must already exist; if it does
        not, this method returns without writing (opt-in behaviour). At
        user scope the configuration directory is created on demand.
        """
        if not self.user_scope and not self._gitlab_duo_project_dir().is_dir():
            return None

        config_path = Path(self.get_config_path())
        current_config = self.get_current_config()
        if not isinstance(current_config.get(self.mcp_servers_key), dict):
            current_config[self.mcp_servers_key] = {}
        current_config[self.mcp_servers_key].update(config_updates)

        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(current_config, f, indent=2)
        os.chmod(config_path, 0o600)
        return True

    def get_current_config(self) -> dict[str, Any]:
        """Read the current active-scope ``mcp.json`` contents."""
        config_path = Path(self.get_config_path())
        if not config_path.exists():
            return {}
        try:
            with open(config_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _approved_tools(server_info: dict[str, Any]) -> bool | list[str]:
        """Return the ``approvedTools`` value for one server entry.

        Defaults to ``True`` (every tool pre-approved -- installing a
        dependency via ``apm install`` is itself the approval step); a
        package may narrow this to an explicit allowlist by declaring
        ``approved_tools`` as a list of tool names.
        """
        tools = server_info.get("approved_tools")
        if isinstance(tools, list):
            return tools
        return True

    def _format_server_config(
        self,
        server_info: dict[str, Any],
        env_overrides: dict[str, str] | None = None,
        runtime_vars: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Format server info into GitLab Duo's ``mcp.json`` schema.

        GitLab Duo uses a transport discriminator field ``type`` to
        determine how to launch an MCP server:

        - ``"type": "stdio"`` for local process servers (raw stdio or packages)
        - ``"type": "http"`` or ``"type": "sse"`` for remote servers, per the
          registry-declared transport

        Every entry also carries ``approvedTools`` (see :meth:`_approved_tools`).
        Copilot-only fields ``tools`` and ``id`` are never emitted.
        """
        if runtime_vars is None:
            runtime_vars = {}

        config: dict[str, Any] = {}

        # --- raw stdio (self-defined deps) ---
        raw = server_info.get("_raw_stdio")
        if raw:
            config["type"] = "stdio"
            config["command"] = raw["command"]
            resolved_env_for_args: dict[str, Any] = {}
            if raw.get("env"):
                resolved_env_for_args = self._resolve_environment_variables(
                    raw["env"], env_overrides=env_overrides
                )
                config["env"] = resolved_env_for_args
                self._warn_input_variables(raw["env"], server_info.get("name", ""), "GitLab Duo")
            args = raw.get("args") or []
            config["args"] = [
                self._resolve_variable_placeholders(arg, resolved_env_for_args, runtime_vars)
                if isinstance(arg, str)
                else arg
                for arg in args
            ]
            config["approvedTools"] = self._approved_tools(server_info)
            self._merge_extra(config, server_info)
            return config

        # --- remote endpoints ---
        remotes = server_info.get("remotes", [])
        if remotes:
            remote = self._select_remote_with_url(remotes) or remotes[0]

            transport = (remote.get("transport_type") or "").strip()
            if not transport or transport == "streamable-http":
                transport = "http"
            elif transport not in ("sse", "http"):
                raise ValueError(
                    f"Unsupported remote transport '{transport}' for GitLab Duo. "
                    f"Server: {server_info.get('name', 'unknown')}. "
                    f"Supported transports: http, sse, streamable-http."
                )

            config["type"] = transport
            config["url"] = (remote.get("url") or "").strip()

            self._apply_auth_and_headers(config, remote, server_info, env_overrides, "GitLab Duo")
            config["approvedTools"] = self._approved_tools(server_info)
            self._merge_extra(config, server_info)
            return config

        # --- local packages ---
        packages = server_info.get("packages", [])

        if not packages and not remotes:
            raise ValueError(
                f"MCP server has incomplete configuration in registry - "
                f"no package information or remote endpoints available. "
                f"This appears to be a temporary registry issue. "
                f"Server: {server_info.get('name', 'unknown')}"
            )

        if packages:
            package = self._select_and_dispatch_best_package(
                config, packages, env_overrides, runtime_vars, set_type_stdio=True
            )
            if not package:
                raise ValueError(
                    f"No supported package type found for GitLab Duo. "
                    f"Server: {server_info.get('name', 'unknown')}. "
                    f"Available packages: "
                    f"{[p.get('registry_name', 'unknown') for p in packages]}."
                )

        config["approvedTools"] = self._approved_tools(server_info)
        self._merge_extra(config, server_info)
        return config

    def configure_mcp_server(
        self,
        server_url,
        server_name=None,
        enabled=True,
        env_overrides=None,
        server_info_cache=None,
        runtime_vars=None,
    ):
        """Configure an MCP server in GitLab Duo's ``mcp.json``.

        Delegates entirely to the shared formatting/write path but skips
        silently at project scope when ``.gitlab/duo/`` does not exist
        (opt-in target); user scope always proceeds.
        """
        if not server_url:
            print("Error: server_url cannot be empty")
            return False

        if not self.user_scope and not self._gitlab_duo_project_dir().is_dir():
            return True  # nothing to do, not an error

        try:
            server_info = self._fetch_server_info(server_url, server_info_cache)
            if server_info is None:
                return False

            config_key = self._determine_config_key(server_url, server_name)

            server_config = self._format_server_config(server_info, env_overrides, runtime_vars)
            self.update_config({config_key: server_config})

            print(f"Successfully configured MCP server '{config_key}' for GitLab Duo")
            return True

        except Exception as e:
            print(f"Error configuring MCP server: {e}")
            return False
