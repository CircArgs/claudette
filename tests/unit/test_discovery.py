"""Tests for the discovery module."""

from __future__ import annotations

import json
from pathlib import Path

from claudette.core.discovery import (
    discover_coverage_gaps,
    discover_stale_dependencies,
    discover_todo_comments,
)


class TestDiscoverTodoComments:
    def test_finds_todo_in_python(self, tmp_path: Path):
        src = tmp_path / "app.py"
        src.write_text("x = 1\n# TODO: add rate limiting\ny = 2\n")

        results = discover_todo_comments(str(tmp_path))
        assert len(results) == 1
        assert results[0]["file"] == "app.py"
        assert results[0]["line"] == 2
        assert results[0]["type"] == "TODO"
        assert "rate limiting" in results[0]["text"]

    def test_finds_fixme(self, tmp_path: Path):
        src = tmp_path / "db.py"
        src.write_text("conn = get()\n# FIXME: connection pool leak\n")

        results = discover_todo_comments(str(tmp_path))
        assert len(results) == 1
        assert results[0]["type"] == "FIXME"

    def test_finds_hack_and_xxx(self, tmp_path: Path):
        src = tmp_path / "util.py"
        src.write_text("# HACK: workaround for bug\n# XXX: needs refactor\n")

        results = discover_todo_comments(str(tmp_path))
        types = {r["type"] for r in results}
        assert "HACK" in types
        assert "XXX" in types

    def test_finds_js_style_comments(self, tmp_path: Path):
        src = tmp_path / "app.js"
        src.write_text("// TODO: implement auth\nconst x = 1;\n")

        results = discover_todo_comments(str(tmp_path))
        assert len(results) == 1
        assert results[0]["type"] == "TODO"

    def test_skips_pycache(self, tmp_path: Path):
        cache_dir = tmp_path / "__pycache__"
        cache_dir.mkdir()
        src = cache_dir / "mod.py"
        src.write_text("# TODO: should not be found\n")

        results = discover_todo_comments(str(tmp_path))
        assert len(results) == 0

    def test_skips_node_modules(self, tmp_path: Path):
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        src = nm / "index.js"
        src.write_text("// TODO: should not be found\n")

        results = discover_todo_comments(str(tmp_path))
        assert len(results) == 0

    def test_respects_extensions_filter(self, tmp_path: Path):
        py = tmp_path / "app.py"
        py.write_text("# TODO: in python\n")
        txt = tmp_path / "notes.txt"
        txt.write_text("# TODO: in text\n")

        results = discover_todo_comments(str(tmp_path), extensions=[".py"])
        assert len(results) == 1
        assert results[0]["file"] == "app.py"

    def test_empty_dir(self, tmp_path: Path):
        results = discover_todo_comments(str(tmp_path))
        assert results == []

    def test_nonexistent_dir(self, tmp_path: Path):
        results = discover_todo_comments(str(tmp_path / "nonexistent"))
        assert results == []


class TestDiscoverCoverageGaps:
    def _write_cobertura_xml(self, path: Path, files: list[dict]) -> None:
        lines = ['<?xml version="1.0" ?>', "<coverage>", '  <packages><package name="pkg">']
        lines.append("    <classes>")
        for f in files:
            lines.append(
                f'      <class filename="{f["name"]}" line-rate="{f["rate"]}">'
            )
            lines.append("        <lines>")
            for i in range(f.get("total_lines", 10)):
                hits = 1 if i < int(f["rate"] * f.get("total_lines", 10)) else 0
                lines.append(f'          <line number="{i+1}" hits="{hits}"/>')
            lines.append("        </lines>")
            lines.append("      </class>")
        lines.append("    </classes>")
        lines.append("  </package></packages>")
        lines.append("</coverage>")
        (path / "coverage.xml").write_text("\n".join(lines))

    def test_finds_low_coverage(self, tmp_path: Path):
        self._write_cobertura_xml(
            tmp_path,
            [
                {"name": "src/auth.py", "rate": 0.2, "total_lines": 10},
                {"name": "src/main.py", "rate": 0.8, "total_lines": 10},
            ],
        )

        results = discover_coverage_gaps(str(tmp_path), min_threshold=50.0)
        assert results is not None
        assert len(results) == 1
        assert results[0]["file"] == "src/auth.py"
        assert results[0]["coverage"] == 20.0

    def test_returns_none_without_coverage_file(self, tmp_path: Path):
        results = discover_coverage_gaps(str(tmp_path))
        assert results is None

    def test_all_above_threshold(self, tmp_path: Path):
        self._write_cobertura_xml(
            tmp_path,
            [{"name": "src/good.py", "rate": 0.9, "total_lines": 10}],
        )

        results = discover_coverage_gaps(str(tmp_path), min_threshold=50.0)
        assert results is not None
        assert len(results) == 0


class TestDiscoverStaleDependencies:
    def test_finds_requirements_txt(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("flask>=2.0\nrequests==2.28.0\n# comment\n")

        results = discover_stale_dependencies(str(tmp_path))
        assert results is not None
        assert len(results) == 1
        assert results[0]["file"] == "requirements.txt"
        assert "flask" in results[0]["dependencies"]
        assert "requests" in results[0]["dependencies"]

    def test_finds_package_json(self, tmp_path: Path):
        pkg = {
            "dependencies": {"react": "^18.0", "axios": "^1.0"},
            "devDependencies": {"jest": "^29.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        results = discover_stale_dependencies(str(tmp_path))
        assert results is not None
        assert len(results) == 1
        assert results[0]["file"] == "package.json"
        assert len(results[0]["dependencies"]) == 3

    def test_returns_none_without_dep_files(self, tmp_path: Path):
        results = discover_stale_dependencies(str(tmp_path))
        assert results is None

    def test_finds_pyproject_toml(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "myapp"\ndependencies = [\n'
            '  "click",\n  "pydantic",\n]\n'
        )

        results = discover_stale_dependencies(str(tmp_path))
        assert results is not None
        assert len(results) == 1
        assert results[0]["file"] == "pyproject.toml"
        assert "click" in results[0]["dependencies"]
        assert "pydantic" in results[0]["dependencies"]
