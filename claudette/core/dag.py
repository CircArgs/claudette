"""Dependency graph builder with cross-repo support and cycle detection."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claudette.protocols.github import Issue


@dataclass
class DependencyGraph:
    """Directed acyclic graph of issue dependencies, keyed by 'owner/repo#number'."""

    edges: dict[str, set[str]] = field(default_factory=dict)
    nodes: dict[str, Issue] = field(default_factory=dict)

    @property
    def all_keys(self) -> set[str]:
        keys = set(self.nodes.keys())
        for deps in self.edges.values():
            keys.update(deps)
        return keys


def issue_key(repo: str, number: int) -> str:
    return f"{repo}#{number}"


def build_dag(issues: list[Issue], dependency_pattern: str) -> DependencyGraph:
    """Build a unified dependency graph from issues across all repos."""
    pattern = re.compile(dependency_pattern, re.MULTILINE)
    graph = DependencyGraph()

    for issue in issues:
        key = issue_key(issue.repo, issue.number)
        graph.nodes[key] = issue
        graph.edges.setdefault(key, set())

        for match in pattern.finditer(issue.body):
            dep_repo = match.group(1) or issue.repo
            dep_number = int(match.group(2))
            dep_key = issue_key(dep_repo, dep_number)
            graph.edges[key].add(dep_key)

    return graph


def find_cycles(graph: DependencyGraph) -> list[list[str]]:
    """Find all cycles in the graph. Returns a list of cycles, each a list of node keys."""
    visited: set[str] = set()
    in_stack: set[str] = set()
    stack: list[str] = []
    cycles: list[list[str]] = []

    def dfs(node: str) -> None:
        visited.add(node)
        in_stack.add(node)
        stack.append(node)

        for dep in graph.edges.get(node, set()):
            if dep not in visited:
                dfs(dep)
            elif dep in in_stack:
                cycle_start = stack.index(dep)
                cycles.append(stack[cycle_start:])

        stack.pop()
        in_stack.discard(node)

    for node in graph.edges:
        if node not in visited:
            dfs(node)

    return cycles


def topological_sort(graph: DependencyGraph) -> list[str]:
    """Return nodes in dependency order. Raises ValueError if cycles exist."""
    cycles = find_cycles(graph)
    if cycles:
        cycle_str = ", ".join(" -> ".join(c) for c in cycles)
        raise ValueError(f"Circular dependencies detected: {cycle_str}")

    visited: set[str] = set()
    order: list[str] = []

    def dfs(node: str) -> None:
        if node in visited:
            return
        visited.add(node)
        for dep in graph.edges.get(node, set()):
            dfs(dep)
        order.append(node)

    for node in graph.edges:
        dfs(node)

    return order


def get_blocked_issues(graph: DependencyGraph) -> dict[str, set[str]]:
    """Return a map of issue keys to the set of open issues blocking them."""
    blocked: dict[str, set[str]] = {}

    for key, deps in graph.edges.items():
        open_deps = set()
        for dep in deps:
            dep_issue = graph.nodes.get(dep)
            if dep_issue is None or dep_issue.state == "open":
                open_deps.add(dep)
        if open_deps:
            blocked[key] = open_deps

    return blocked


def get_ready_issues(graph: DependencyGraph) -> list[str]:
    """Return issue keys that have no open dependencies."""
    blocked = get_blocked_issues(graph)
    return [key for key in graph.nodes if key not in blocked]
