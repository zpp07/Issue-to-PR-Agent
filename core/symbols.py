"""Recursive Python symbol indexing used by the bounded read_symbol tool."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class SymbolLocation:
    symbol: str
    kind: str
    start_line: int
    end_line: int

    def as_dict(self):
        return {
            "symbol": self.symbol,
            "kind": self.kind,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(self):
        self.scope: list[tuple[str, str]] = []
        self.locations: list[SymbolLocation] = []

    def _visit_definition(self, node, base_kind: str):
        qualified = ".".join([name for name, _ in self.scope] + [node.name])
        parent_kind = self.scope[-1][1] if self.scope else None
        if base_kind == "class":
            kind = "nested_class" if self.scope else "class"
        elif parent_kind in {"class", "nested_class"}:
            kind = "method"
        elif self.scope:
            kind = "nested_function"
        else:
            kind = "function"
        decorators = [item.lineno for item in getattr(node, "decorator_list", [])]
        start = min([node.lineno, *decorators])
        end = getattr(node, "end_lineno", None) or max(
            getattr(child, "end_lineno", None) or getattr(child, "lineno", node.lineno)
            for child in ast.walk(node)
        )
        self.locations.append(SymbolLocation(qualified, kind, start, end))
        self.scope.append((node.name, kind))
        for child in node.body:
            self.visit(child)
        self.scope.pop()

    def visit_ClassDef(self, node):
        self._visit_definition(node, "class")

    def visit_FunctionDef(self, node):
        self._visit_definition(node, "function")

    def visit_AsyncFunctionDef(self, node):
        self._visit_definition(node, "function")


def index_python_symbols(source: str) -> list[SymbolLocation]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # The project still supports Python 3.7 for dependency-light local
        # runs, while target repositories may use Python 3.8 positional-only
        # markers.  Replacing only the standalone marker in the parse copy
        # preserves line numbers and never changes repository source.
        compatible = re.sub(
            r"(?m)(?<=,)[ \t]*/[ \t]*,",
            lambda match: " " * len(match.group(0)),
            source,
        )
        compatible = re.sub(
            r"(?m)(?<=,)([ \t]*)/(?=[ \t]*\))", r"\1 ", compatible,
        )
        if compatible == source:
            raise
        tree = ast.parse(compatible)
    visitor = _SymbolVisitor()
    visitor.visit(tree)
    return visitor.locations


def read_symbol_source(
    path: str | Path,
    symbol: str,
    occurrence: int | None = None,
    context_lines: int = 3,
) -> dict:
    if not isinstance(symbol, str) or not symbol.strip():
        return {"status": "error", "error": "symbol must be a non-empty qualified name"}
    if (not isinstance(context_lines, int) or isinstance(context_lines, bool)
            or not 0 <= context_lines <= 50):
        return {"status": "error", "error": "context_lines must be an integer from 0 to 50"}
    if occurrence is not None and (
        not isinstance(occurrence, int) or isinstance(occurrence, bool) or occurrence < 0
    ):
        return {"status": "error", "error": "occurrence must be a non-negative integer"}

    try:
        source = Path(path).read_text(encoding="utf-8")
        locations = index_python_symbols(source)
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        return {"status": "error", "error": f"cannot index Python symbols: {exc}"}

    exact = [item for item in locations if item.symbol == symbol]
    matches = exact or [
        item for item in locations
        if item.symbol == symbol or item.symbol.endswith(f".{symbol}")
    ]
    if not matches:
        suggestions = [item.as_dict() for item in locations if symbol in item.symbol][:10]
        return {"status": "not_found", "symbol": symbol, "candidates": suggestions}
    if len(matches) > 1 and occurrence is None:
        return {
            "status": "ambiguous", "symbol": symbol,
            "candidates": [item.as_dict() for item in matches],
        }
    selected_index = occurrence or 0
    if selected_index >= len(matches):
        return {
            "status": "error", "error": "occurrence is outside the candidate range",
            "candidates": [item.as_dict() for item in matches],
        }

    selected = matches[selected_index]
    lines = source.splitlines()
    shown_start = max(1, selected.start_line - context_lines)
    shown_end = min(len(lines), selected.end_line + context_lines)
    numbered = "\n".join(
        f"{number}: {lines[number - 1]}" for number in range(shown_start, shown_end + 1)
    )
    return {
        "status": "ok",
        **selected.as_dict(),
        "context_start_line": shown_start,
        "context_end_line": shown_end,
        "occurrence": selected_index,
        "source": numbered,
    }
