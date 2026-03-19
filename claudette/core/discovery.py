"""Work discovery sources — scan repos for TODOs, coverage gaps, and dependencies."""

from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

# Extensions to scan for TODO comments
_DEFAULT_EXTENSIONS = {".py", ".js", ".ts", ".go", ".rs", ".java", ".rb", ".cpp", ".c", ".h"}

# Directories to always skip
_SKIP_DIRS = {
    "node_modules",
    "__pycache__",
    ".git",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".eggs",
    "venv",
    ".venv",
    "env",
}

# Pattern matching TODO/FIXME/HACK/XXX comments
_TODO_PATTERN = re.compile(
    r"#\s*(TODO|FIXME|HACK|XXX)\b[:\s]*(.*)|"
    r"//\s*(TODO|FIXME|HACK|XXX)\b[:\s]*(.*)|"
    r"/\*\s*(TODO|FIXME|HACK|XXX)\b[:\s]*(.*)",
    re.IGNORECASE,
)


def _get_gitignored_paths(repo_path: str) -> set[str]:
    """Get list of files tracked by .gitignore using git."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "--directory"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return {p.rstrip("/") for p in result.stdout.strip().splitlines() if p.strip()}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return set()


def _should_skip_dir(name: str) -> bool:
    """Check if a directory should be skipped."""
    return name in _SKIP_DIRS or name.startswith(".")


def discover_todo_comments(
    repo_path: str, extensions: list[str] | None = None
) -> list[dict]:
    """Scan source files for TODO/FIXME/HACK/XXX comments.

    Returns list of {"file": str, "line": int, "text": str, "type": str}.
    """
    ext_set = set(extensions) if extensions else _DEFAULT_EXTENSIONS
    results: list[dict] = []
    root = Path(repo_path)

    if not root.is_dir():
        return results

    for dirpath, dirnames, filenames in root.walk():
        # Prune directories in-place
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]

        for filename in filenames:
            filepath = dirpath / filename
            if filepath.suffix not in ext_set:
                continue

            try:
                text = filepath.read_text(errors="replace")
            except OSError:
                continue

            for line_num, line in enumerate(text.splitlines(), start=1):
                match = _TODO_PATTERN.search(line)
                if match:
                    # Extract the type and text from whichever group matched
                    groups = match.groups()
                    for i in range(0, len(groups), 2):
                        if groups[i] is not None:
                            todo_type = groups[i].upper()
                            todo_text = (groups[i + 1] or "").strip()
                            break
                    else:
                        continue

                    results.append(
                        {
                            "file": str(filepath.relative_to(root)),
                            "line": line_num,
                            "text": todo_text,
                            "type": todo_type,
                        }
                    )

    return results


def discover_coverage_gaps(
    repo_path: str, min_threshold: float = 50.0
) -> list[dict] | None:
    """Look for coverage reports and return files below the threshold.

    Returns list of {"file": str, "coverage": float, "missing_lines": int},
    or None if no coverage data found.
    """
    root = Path(repo_path)

    # Check for coverage.xml (Cobertura format)
    coverage_xml = root / "coverage.xml"
    if not coverage_xml.exists():
        return None

    return _parse_cobertura_xml(coverage_xml, min_threshold)


def _parse_cobertura_xml(
    xml_path: Path, min_threshold: float
) -> list[dict]:
    """Parse a Cobertura-format coverage.xml and return files below threshold."""
    results: list[dict] = []

    try:
        tree = ET.parse(xml_path)  # noqa: S314
    except (ET.ParseError, OSError):
        return results

    root_el = tree.getroot()

    for package in root_el.iter("package"):
        for cls in package.iter("class"):
            filename = cls.get("filename", "")
            line_rate_str = cls.get("line-rate", "0")

            try:
                line_rate = float(line_rate_str)
            except ValueError:
                line_rate = 0.0

            coverage_pct = line_rate * 100.0

            if coverage_pct < min_threshold:
                # Count missing lines
                missing = 0
                for line in cls.iter("line"):
                    hits = int(line.get("hits", "0"))
                    if hits == 0:
                        missing += 1

                results.append(
                    {
                        "file": filename,
                        "coverage": round(coverage_pct, 1),
                        "missing_lines": missing,
                    }
                )

    return results


def discover_stale_dependencies(repo_path: str) -> list[dict] | None:
    """Identify dependency files and list their packages.

    Returns list of {"file": str, "dependencies": list[str]},
    or None if no dependency files found.
    """
    import json as _json

    root = Path(repo_path)
    results: list[dict] = []

    # Check requirements.txt
    req_txt = root / "requirements.txt"
    if req_txt.exists():
        try:
            lines = req_txt.read_text().splitlines()
            deps = []
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and not stripped.startswith("-"):
                    pkg = stripped.split("==")[0].split(">=")[0].split("<=")[0]
                    pkg = pkg.split("~=")[0].split("[")[0].strip()
                    deps.append(pkg)
            deps = [d for d in deps if d]
            if deps:
                results.append({"file": "requirements.txt", "dependencies": deps})
        except OSError:
            pass

    # Check pyproject.toml
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text()
            # Simple extraction of dependencies from [project] section
            deps: list[str] = []
            in_deps = False
            for line in text.splitlines():
                if line.strip() == "dependencies = [":
                    in_deps = True
                    continue
                if in_deps:
                    if line.strip() == "]":
                        break
                    # Extract package name from quoted string
                    match = re.match(r'\s*"([^">=<~!\[]+)', line)
                    if match:
                        deps.append(match.group(1).strip())
            if deps:
                results.append({"file": "pyproject.toml", "dependencies": deps})
        except OSError:
            pass

    # Check package.json
    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            data = _json.loads(pkg_json.read_text())
            all_deps: list[str] = []
            for key in ("dependencies", "devDependencies"):
                if key in data and isinstance(data[key], dict):
                    all_deps.extend(data[key].keys())
            if all_deps:
                results.append({"file": "package.json", "dependencies": all_deps})
        except (OSError, _json.JSONDecodeError):
            pass

    return results if results else None
