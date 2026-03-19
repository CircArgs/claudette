"""Tests for the pipeline config and integration."""

from __future__ import annotations

from pathlib import Path

from claudette.core.config import Config, DiscoveryConfig, PipelineConfig


class TestPipelineConfig:
    def test_defaults(self):
        pc = PipelineConfig()
        assert pc.enabled is True
        assert pc.stages == ["scout", "architect", "builder", "tester", "reviewer"]
        assert pc.skip_stages == []

    def test_skip_stages(self):
        pc = PipelineConfig(skip_stages=["scout", "architect"])
        active = [s for s in pc.stages if s not in pc.skip_stages]
        assert active == ["builder", "tester", "reviewer"]

    def test_disabled(self):
        pc = PipelineConfig(enabled=False)
        assert pc.enabled is False

    def test_custom_stages(self):
        pc = PipelineConfig(stages=["plan", "build", "test"])
        assert pc.stages == ["plan", "build", "test"]

    def test_config_includes_pipeline(self, tmp_path: Path):
        config = Config(project_dir=tmp_path)
        assert config.pipeline.enabled is True
        assert len(config.pipeline.stages) == 5

    def test_config_save_load_pipeline(self, tmp_path: Path):
        config = Config(
            project_dir=tmp_path,
            pipeline=PipelineConfig(skip_stages=["scout"]),
        )
        config.save()

        loaded = Config.load(tmp_path)
        assert loaded is not None
        assert loaded.pipeline.skip_stages == ["scout"]
        assert loaded.pipeline.enabled is True


class TestDiscoveryConfig:
    def test_defaults(self):
        dc = DiscoveryConfig()
        assert dc.enabled is False
        assert dc.sources == ["todos", "coverage"]
        assert dc.min_coverage_threshold == 50.0
        assert ".py" in dc.file_extensions

    def test_custom(self):
        dc = DiscoveryConfig(enabled=True, min_coverage_threshold=80.0)
        assert dc.enabled is True
        assert dc.min_coverage_threshold == 80.0

    def test_config_includes_discovery(self, tmp_path: Path):
        config = Config(project_dir=tmp_path)
        assert config.discovery.enabled is False

    def test_config_save_load_discovery(self, tmp_path: Path):
        config = Config(
            project_dir=tmp_path,
            discovery=DiscoveryConfig(enabled=True, min_coverage_threshold=75.0),
        )
        config.save()

        loaded = Config.load(tmp_path)
        assert loaded is not None
        assert loaded.discovery.enabled is True
        assert loaded.discovery.min_coverage_threshold == 75.0
