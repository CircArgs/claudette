"""Tests for config parsing and validation."""

from pathlib import Path

from claudette.core.config import Config, ProjectRegistry, RepoConfig


class TestConfig:
    def test_defaults(self, tmp_path: Path):
        config = Config(project_dir=tmp_path)
        assert config.system.polling_interval_minutes == 5
        assert config.system.session_timeout_minutes == 45
        assert config.budget.max_tokens_per_repo_per_day == 5_000_000

    def test_load_from_project_dir(self, tmp_path: Path):
        import yaml

        dot_dir = tmp_path / ".claudette"
        dot_dir.mkdir()
        config_data = {
            "project_dir": str(tmp_path),
            "system": {
                "polling_interval_minutes": 10,
                "session_timeout_minutes": 60,
            },
            "repositories": [
                {"name": "owner/repo", "default_branch": "develop"},
            ],
        }
        with open(dot_dir / "config.yaml", "w") as f:
            yaml.safe_dump(config_data, f)

        config = Config.load(tmp_path)
        assert config is not None
        assert config.system.polling_interval_minutes == 10
        assert config.system.session_timeout_minutes == 60
        assert len(config.repositories) == 1
        assert config.repositories[0].name == "owner/repo"
        assert config.repositories[0].default_branch == "develop"

    def test_load_missing_returns_none(self, tmp_path: Path):
        config = Config.load(tmp_path / "nonexistent")
        assert config is None

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        config = Config(
            project_dir=tmp_path,
            repositories=[RepoConfig(name="owner/repo", default_branch="master")],
        )
        config.save()
        assert (tmp_path / ".claudette" / "config.yaml").exists()

        loaded = Config.load(tmp_path)
        assert loaded is not None
        assert loaded.repositories[0].name == "owner/repo"
        assert loaded.repositories[0].default_branch == "master"

    def test_directory_properties(self, tmp_path: Path):
        config = Config(project_dir=tmp_path)
        assert config.dot_dir == tmp_path / ".claudette"
        assert config.state_dir == tmp_path / ".claudette" / "state"
        assert config.log_dir == tmp_path / ".claudette" / "logs"
        assert config.memory_dir == tmp_path / ".claudette" / "memory"
        assert config.prompts_dir == tmp_path / ".claudette" / "prompts"
        assert config.worktree_dir == tmp_path / ".claudette" / "worktrees"
        assert config.config_file == tmp_path / ".claudette" / "config.yaml"

    def test_find_from_cwd(self, tmp_path: Path):
        config = Config(project_dir=tmp_path)
        config.save()

        # Should find config from a subdirectory
        sub = tmp_path / "some" / "deep" / "subdir"
        sub.mkdir(parents=True)
        found = Config.find_from_cwd(sub)
        assert found is not None
        assert found.project_dir.resolve() == tmp_path.resolve()

    def test_find_from_cwd_not_found(self, tmp_path: Path):
        found = Config.find_from_cwd(tmp_path)
        assert found is None

    def test_repo_config_path(self):
        repo = RepoConfig(name="owner/repo", path="/home/user/project/my-repo")
        assert repo.path == "/home/user/project/my-repo"


class TestProjectRegistry:
    def test_register_and_find(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("claudette.core.config.GLOBAL_HOME", tmp_path / ".claudette")
        registry = ProjectRegistry()
        registry.register("my-project", tmp_path / "my-project")
        registry.save()

        loaded = ProjectRegistry.load()
        assert len(loaded.projects) == 1
        assert loaded.projects[0].name == "my-project"

        found = loaded.find_by_path(tmp_path / "my-project")
        assert found is not None
        assert found.name == "my-project"

    def test_unregister(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("claudette.core.config.GLOBAL_HOME", tmp_path / ".claudette")
        registry = ProjectRegistry()
        registry.register("proj-a", tmp_path / "a")
        registry.register("proj-b", tmp_path / "b")
        assert registry.unregister("proj-a")
        assert len(registry.projects) == 1
        assert registry.projects[0].name == "proj-b"

    def test_register_updates_existing(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("claudette.core.config.GLOBAL_HOME", tmp_path / ".claudette")
        registry = ProjectRegistry()
        registry.register("my-project", tmp_path / "old-path")
        registry.register("my-project", tmp_path / "new-path")
        assert len(registry.projects) == 1
        assert registry.projects[0].path == str((tmp_path / "new-path").resolve())
