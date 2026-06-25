"""Python API documentation extractor — AST-only, Chirp-native."""

from __future__ import annotations

import ast
import fnmatch
from pathlib import Path
from typing import Any

from furatena.catalog.autodoc.elements import DocElement


def _module_name(path: Path, source_root: Path) -> str:
    rel = path.resolve().relative_to(source_root.resolve())
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    prefix = source_root.name
    if not parts:
        return prefix
    return f"{prefix}.{'.'.join(parts)}"


def _should_skip(path: Path, exclude_patterns: list[str]) -> bool:
    text = str(path)
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(text, pattern) or fnmatch.fnmatch(path.name, pattern):
            return True
    return False


def _docstring(node: ast.AST) -> str:
    raw = ast.get_docstring(node, clean=True)
    return raw.strip() if raw else ""


def _annotation(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _default(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _build_signature(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = func.args
    parts: list[str] = []
    defaults_offset = len(args.args) - len(args.defaults)
    for index, arg in enumerate(args.args):
        chunk = arg.arg
        ann = _annotation(arg.annotation)
        if ann:
            chunk += f": {ann}"
        default_index = index - defaults_offset
        if default_index >= 0:
            default = _default(args.defaults[default_index])
            if default is not None:
                chunk += f" = {default}"
        parts.append(chunk)
    if args.vararg:
        ann = _annotation(args.vararg.annotation)
        parts.append(f"*{args.vararg.arg}" + (f": {ann}" if ann else ""))
    if args.kwonlyargs:
        for index, arg in enumerate(args.kwonlyargs):
            chunk = arg.arg
            ann = _annotation(arg.annotation)
            if ann:
                chunk += f": {ann}"
            if index < len(args.kw_defaults) and args.kw_defaults[index] is not None:
                default = _default(args.kw_defaults[index])
                if default is not None:
                    chunk += f" = {default}"
            parts.append(chunk)
    if args.kwarg:
        ann = _annotation(args.kwarg.annotation)
        parts.append(f"**{args.kwarg.arg}" + (f": {ann}" if ann else ""))
    prefix = "async def" if isinstance(func, ast.AsyncFunctionDef) else "def"
    ret = _annotation(func.returns)
    ret_suffix = f" -> {ret}" if ret else ""
    return f"{prefix} {func.name}({', '.join(parts)}){ret_suffix}"


def _is_private(name: str, *, include_private: bool) -> bool:
    if include_private:
        return False
    return name.startswith("_") and not name.startswith("__")


def _is_special(name: str, *, include_special: bool) -> bool:
    if include_special:
        return False
    return name.startswith("__") and name.endswith("__")


class PythonExtractor:
    """Extract Python modules/classes/functions via AST parsing."""

    def __init__(
        self,
        *,
        exclude_patterns: list[str] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.exclude_patterns = list(exclude_patterns or [])
        cfg = config or {}
        self.include_private = bool(cfg.get("include_private", False))
        self.include_special = bool(cfg.get("include_special", False))

    def extract(self, source: Path) -> list[DocElement]:
        """Extract documentation elements from a file or directory."""
        source = source.resolve()
        if source.is_file():
            element = self._extract_file(source, source_root=source.parent)
            return [element] if element is not None else []
        if not source.is_dir():
            return []
        elements: list[DocElement] = []
        for path in sorted(source.rglob("*.py")):
            if _should_skip(path, self.exclude_patterns):
                continue
            element = self._extract_file(path, source_root=source)
            if element is not None:
                elements.append(element)
        return elements

    def _extract_file(self, path: Path, *, source_root: Path) -> DocElement | None:
        if _should_skip(path, self.exclude_patterns):
            return None
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            return None
        module_name = _module_name(path, source_root)
        if not module_name:
            return None
        module = DocElement(
            name=module_name.rsplit(".", 1)[-1] if module_name else path.stem,
            qualified_name=module_name,
            description=_docstring(tree),
            element_type="module",
            source_file=path,
            line_number=1,
        )
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                if _is_private(node.name, include_private=self.include_private):
                    continue
                if _is_special(node.name, include_special=self.include_special):
                    continue
                module.children.append(self._extract_class(node, module_name, path))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _is_private(node.name, include_private=self.include_private):
                    continue
                module.children.append(self._extract_function(node, module_name, path))
        return module

    def _extract_class(self, node: ast.ClassDef, module_name: str, path: Path) -> DocElement:
        qname = f"{module_name}.{node.name}"
        element = DocElement(
            name=node.name,
            qualified_name=qname,
            description=_docstring(node),
            element_type="class",
            source_file=path,
            line_number=node.lineno,
        )
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _is_private(child.name, include_private=self.include_private):
                    continue
                if _is_special(child.name, include_special=self.include_special):
                    continue
                element.children.append(self._extract_function(child, qname, path, method=True))
            elif isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        if _is_private(target.id, include_private=self.include_private):
                            continue
                        element.children.append(
                            DocElement(
                                name=target.id,
                                qualified_name=f"{qname}.{target.id}",
                                description="",
                                element_type="attribute",
                                source_file=path,
                                line_number=child.lineno,
                            )
                        )
        return element

    def _extract_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parent_name: str,
        path: Path,
        *,
        method: bool = False,
    ) -> DocElement:
        qname = f"{parent_name}.{node.name}"
        return DocElement(
            name=node.name,
            qualified_name=qname,
            description=_docstring(node),
            element_type="method" if method else "function",
            source_file=path,
            line_number=node.lineno,
            metadata={"signature": _build_signature(node)},
        )
