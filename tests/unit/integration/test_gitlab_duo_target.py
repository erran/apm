"""Acceptance tests for the GitLab Duo target profile and deployment."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from apm_cli.core.target_detection import detect_target
from apm_cli.integration.agent_integrator import AgentIntegrator
from apm_cli.integration.skill_integrator import SkillIntegrator
from apm_cli.integration.targets import KNOWN_TARGETS
from apm_cli.models.apm_package import (
    APMPackage,
    GitReferenceType,
    PackageInfo,
    PackageType,
    ResolvedReference,
)


def _make_package_info(
    package_dir: Path,
    name: str = "test-pkg",
    package_type: PackageType | None = None,
) -> PackageInfo:
    package = APMPackage(
        name=name,
        version="1.0.0",
        package_path=package_dir,
        source=f"github.com/test/{name}",
    )
    resolved_ref = ResolvedReference(
        original_ref="main",
        ref_type=GitReferenceType.BRANCH,
        resolved_commit="abc123",
        ref_name="main",
    )
    return PackageInfo(
        package=package,
        install_path=package_dir,
        resolved_reference=resolved_ref,
        installed_at=datetime.now().isoformat(),
        package_type=package_type,
    )


def test_gitlab_duo_target_profile_matches_ratified_layout() -> None:
    target = KNOWN_TARGETS["gitlab-duo"]

    assert target.root_dir == ".gitlab/duo"
    assert target.auto_create is False
    assert target.detect_by_dir is True
    assert target.user_supported is False
    assert set(target.primitives) == {"agents", "skills"}

    agents = target.primitives["agents"]
    assert agents.subdir == "agents"
    assert agents.extension == ".agent.md"
    assert agents.deploy_root == ".agents"

    skills = target.primitives["skills"]
    assert skills.subdir == "skills"
    assert skills.extension == "/SKILL.md"
    assert skills.format_id == "skill_standard"
    assert skills.deploy_root == ".agents"


def test_gitlab_duo_pack_prefixes_cover_both_roots() -> None:
    target = KNOWN_TARGETS["gitlab-duo"]
    assert target.effective_pack_prefixes == (".gitlab/duo/", ".agents/")


def test_gitlab_duo_auto_detected_from_existing_directory(tmp_path: Path) -> None:
    (tmp_path / ".gitlab" / "duo").mkdir(parents=True)

    target, reason = detect_target(tmp_path)

    assert target == "gitlab-duo"
    assert ".gitlab/duo" in reason


def test_gitlab_duo_not_selected_without_directory(tmp_path: Path) -> None:
    target, _reason = detect_target(tmp_path)

    assert target == "minimal"


def test_gitlab_duo_skills_deploy_to_shared_agents_dir(tmp_path: Path) -> None:
    (tmp_path / ".gitlab" / "duo").mkdir(parents=True)
    package_dir = tmp_path / "skill-pkg"
    package_dir.mkdir()
    (package_dir / "SKILL.md").write_text(
        "---\nname: skill-pkg\ndescription: Demo skill\n---\n\n# Demo\n",
        encoding="utf-8",
    )

    result = SkillIntegrator().integrate_package_skill(
        _make_package_info(package_dir, "skill-pkg", PackageType.CLAUDE_SKILL),
        tmp_path,
        targets=[KNOWN_TARGETS["gitlab-duo"]],
    )

    target_file = tmp_path / ".agents" / "skills" / "skill-pkg" / "SKILL.md"
    assert result.skill_created is True
    assert target_file.read_text(encoding="utf-8") == (
        "---\nname: skill-pkg\ndescription: Demo skill\n---\n\n# Demo\n"
    )
    # Nothing is written directly under .gitlab/duo/ -- it is only the
    # opt-in presence signal and the MCP config home.
    assert not (tmp_path / ".gitlab" / "duo" / "skills").exists()


def test_gitlab_duo_excluded_from_auto_detected_targets_without_directory(tmp_path: Path) -> None:
    """Without an explicit --target, auto-detection must not select gitlab-duo.

    ``auto_create=False`` + ``detect_by_dir=True`` means gitlab-duo only
    participates in auto-detected (implicit) installs when ``.gitlab/duo/``
    already exists -- mirroring cursor/kiro/windsurf/codex. An explicitly
    passed ``--target gitlab-duo`` still deploys regardless (matching the
    same targets), since explicit selection is a stronger signal than the
    directory-presence heuristic.
    """
    from apm_cli.integration.targets import active_targets

    assert KNOWN_TARGETS["gitlab-duo"] not in active_targets(tmp_path)

    (tmp_path / ".gitlab" / "duo").mkdir(parents=True)
    assert KNOWN_TARGETS["gitlab-duo"] in active_targets(tmp_path)


def test_gitlab_duo_agents_deploy_as_agent_md_under_shared_agents_dir(tmp_path: Path) -> None:
    (tmp_path / ".gitlab" / "duo").mkdir(parents=True)
    package_dir = tmp_path / "agent-pkg"
    agents_dir = package_dir / ".apm" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "reviewer.agent.md").write_text(
        "---\nname: reviewer\ndescription: Reviews code\n---\n\nReview the diff.\n",
        encoding="utf-8",
    )

    result = AgentIntegrator().integrate_agents_for_target(
        KNOWN_TARGETS["gitlab-duo"],
        _make_package_info(package_dir, "agent-pkg"),
        tmp_path,
    )

    target_file = tmp_path / ".agents" / "agents" / "reviewer.agent.md"
    assert result.files_integrated == 1
    assert target_file.exists()
    assert not (tmp_path / ".gitlab" / "duo" / "agents").exists()


def test_gitlab_duo_agents_skipped_without_opt_in_directory(tmp_path: Path) -> None:
    package_dir = tmp_path / "agent-pkg"
    agents_dir = package_dir / ".apm" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "reviewer.agent.md").write_text(
        "---\nname: reviewer\ndescription: Reviews code\n---\n\nReview the diff.\n",
        encoding="utf-8",
    )

    result = AgentIntegrator().integrate_agents_for_target(
        KNOWN_TARGETS["gitlab-duo"],
        _make_package_info(package_dir, "agent-pkg"),
        tmp_path,
    )

    assert result.files_integrated == 0
    assert not (tmp_path / ".agents" / "agents").exists()
