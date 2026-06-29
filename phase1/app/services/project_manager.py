"""
app/services/project_manager.py
---------------------------------
Sprint 3 (Multi-file Projects) logic on top of app/services/staging.py:
    - Project File Tree View
    - Cross-file Dependency Resolution

Per-file + project-wide lint hooks live in lint_stub.py and are called from
the projects router, not here -- this module only knows about file
structure, not lint.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.schemas import DependencyEdge, FileTreeNode

_MODULE_DECL_RE = re.compile(r"\bmodule\s+([A-Za-z_]\w*)")
_INSTANTIATION_RE = re.compile(r"\b([A-Za-z_]\w*)\s+[A-Za-z_]\w*\s*\(")
_KEYWORDS_NOT_MODULES = {
    # Common SV keywords that can precede an identifier+paren and would
    # otherwise look like an instantiation to the regex above.
    "if", "for", "while", "case", "function", "task", "always", "initial",
}


def build_file_tree(root: Path) -> list[FileTreeNode]:
    """Builds a nested file tree for the UI's Project File Tree View."""

    def _walk(dir_path: Path) -> list[FileTreeNode]:
        nodes = []
        for entry in sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name)):
            rel = str(entry.relative_to(root))
            if entry.is_dir():
                nodes.append(
                    FileTreeNode(name=entry.name, path=rel, is_dir=True, children=_walk(entry))
                )
            else:
                nodes.append(FileTreeNode(name=entry.name, path=rel, is_dir=False, children=[]))
        return nodes

    return _walk(root)


def guess_top_module(rtl_files: list[Path]) -> str | None:
    """
    Heuristic also used by code/synthesis_runner.py: a module declared but
    never instantiated elsewhere in the project is likely the top of the
    hierarchy. Ambiguous results return None -- caller should ask the user.
    """
    declared, instantiated = set(), set()
    for f in rtl_files:
        text = f.read_text(errors="ignore")
        declared.update(_MODULE_DECL_RE.findall(text))
        for match in _INSTANTIATION_RE.findall(text):
            if match not in _KEYWORDS_NOT_MODULES:
                instantiated.add(match)

    candidates = declared - instantiated
    return next(iter(candidates)) if len(candidates) == 1 else None


def build_dependency_graph(rtl_files: list[Path]) -> list[DependencyEdge]:
    """
    Cross-file Dependency Resolution: for each module declared in this
    project, which other in-project modules does it instantiate?

    This is a best-effort static-text heuristic (regex, not a real parser),
    same caveat as guess_top_module -- good enough to drive a dependency
    graph view, not a substitute for elaboration in a real simulator.
    """
    declared_modules: set[str] = set()
    file_text_by_module: dict[str, str] = {}

    for f in rtl_files:
        text = f.read_text(errors="ignore")
        for module_name in _MODULE_DECL_RE.findall(text):
            declared_modules.add(module_name)
            file_text_by_module[module_name] = text

    edges: list[DependencyEdge] = []
    for module_name, text in file_text_by_module.items():
        instantiated = {
            m for m in _INSTANTIATION_RE.findall(text)
            if m in declared_modules and m != module_name and m not in _KEYWORDS_NOT_MODULES
        }
        edges.append(DependencyEdge(module=module_name, instantiates=sorted(instantiated)))

    return edges
