"""Repository-aware hybrid retrieval for Python maintenance agents.

The dependency-free path combines AST/document chunks, BM25, a deterministic
hashed-vector index and a lexical reranker.  Optional Sentence Transformers
adapters provide learned dense retrieval and a BGE CrossEncoder without making
model downloads a prerequisite for tests or local operation.
"""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
from typing import Iterable, Sequence


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]")
CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
SUPPORTED_SUFFIXES = {".py", ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json"}
IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", ".local",
}


def tokenize(text: str) -> list[str]:
    """Tokenize prose and identifiers while retaining Chinese characters."""
    expanded = CAMEL_RE.sub(" ", text.replace("_", " "))
    return [match.group(0).lower() for match in TOKEN_RE.finditer(expanded)]


@dataclass(frozen=True)
class CodeChunk:
    id: str
    path: str
    start_line: int
    end_line: int
    symbol: str
    kind: str
    text: str

    @property
    def reference(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


@dataclass(frozen=True)
class SearchHit:
    chunk: CodeChunk
    score: float
    bm25_score: float
    dense_score: float
    rerank_score: float

    def as_dict(self) -> dict:
        return {
            "path": self.chunk.path,
            "start_line": self.chunk.start_line,
            "end_line": self.chunk.end_line,
            "symbol": self.chunk.symbol,
            "kind": self.chunk.kind,
            "score": round(self.score, 6),
            "bm25_score": round(self.bm25_score, 6),
            "dense_score": round(self.dense_score, 6),
            "rerank_score": round(self.rerank_score, 6),
            "content": self.chunk.text,
        }


def _line_windows(path: str, text: str, kind: str, window: int = 80) -> list[CodeChunk]:
    lines = text.splitlines()
    chunks = []
    for offset in range(0, len(lines) or 1, window):
        selected = lines[offset:offset + window]
        if not selected:
            continue
        chunks.append(_chunk(path, offset + 1, offset + len(selected), "", kind, "\n".join(selected)))
    return chunks


def _chunk(path: str, start: int, end: int, symbol: str, kind: str, text: str) -> CodeChunk:
    identity = f"{path}:{start}:{end}:{symbol}:{kind}"
    chunk_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return CodeChunk(chunk_id, path, start, end, symbol, kind, text.strip())


def _python_chunks(path: str, text: str, max_lines: int = 120) -> list[CodeChunk]:
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _line_windows(path, text, "python_text")

    chunks = []
    occupied = set()
    for node_index, node in enumerate(tree.body):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = min([node.lineno] + [item.lineno for item in getattr(node, "decorator_list", [])])
        # end_lineno is unavailable on Python 3.7, which is still useful for
        # dependency-light local tests.  The next top-level statement provides
        # a conservative source boundary in that runtime.
        next_line = tree.body[node_index + 1].lineno if node_index + 1 < len(tree.body) else len(lines) + 1
        end = getattr(node, "end_lineno", None) or (next_line - 1)
        while end > start and not lines[end - 1].strip():
            end -= 1
        symbol = getattr(node, "name", "")
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        for offset in range(start - 1, end, max_lines):
            window_end = min(offset + max_lines, end)
            chunks.append(_chunk(path, offset + 1, window_end, symbol, kind,
                                 "\n".join(lines[offset:window_end])))
        occupied.update(range(start, end + 1))

    module_lines = [(number, line) for number, line in enumerate(lines, 1)
                    if number not in occupied and line.strip()]
    if module_lines:
        groups = []
        current = []
        previous = None
        for item in module_lines:
            if previous is not None and (item[0] != previous + 1 or len(current) >= max_lines):
                groups.append(current)
                current = []
            current.append(item)
            previous = item[0]
        if current:
            groups.append(current)
        for group in groups:
            chunks.append(_chunk(path, group[0][0], group[-1][0], "<module>", "module",
                                 "\n".join(line for _, line in group)))
    return chunks or _line_windows(path, text, "python_text")


def _document_chunks(path: str, text: str, max_lines: int = 80) -> list[CodeChunk]:
    lines = text.splitlines()
    chunks = []
    start = 1
    heading = ""
    buffer = []

    def flush(end_line):
        if not buffer:
            return
        chunks.append(_chunk(path, start, end_line, heading, "document", "\n".join(buffer)))

    for number, line in enumerate(lines, 1):
        is_heading = bool(re.match(r"^#{1,6}\s+", line))
        if buffer and (is_heading or len(buffer) >= max_lines):
            flush(number - 1)
            buffer = []
            start = number
        if is_heading:
            heading = line.lstrip("#").strip()
        buffer.append(line)
    flush(len(lines))
    return chunks or _line_windows(path, text, "document")


def chunk_repository(root: str | Path, max_file_bytes: int = 300_000) -> list[CodeChunk]:
    root = Path(root).resolve()
    chunks = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        relative = path.relative_to(root)
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative_text = relative.as_posix()
        chunks.extend(_python_chunks(relative_text, text) if path.suffix.lower() == ".py"
                      else _document_chunks(relative_text, text))
    return [chunk for chunk in chunks if chunk.text]


class BM25Index:
    def __init__(self, documents: Sequence[str], k1: float = 1.5, b: float = 0.75):
        self.tokens = [tokenize(document) for document in documents]
        self.counts = [Counter(tokens) for tokens in self.tokens]
        self.lengths = [len(tokens) for tokens in self.tokens]
        self.average_length = sum(self.lengths) / max(len(self.lengths), 1)
        self.k1, self.b = k1, b
        document_frequency = Counter()
        for tokens in self.tokens:
            document_frequency.update(set(tokens))
        count = len(self.tokens)
        self.idf = {
            token: math.log(1 + (count - frequency + 0.5) / (frequency + 0.5))
            for token, frequency in document_frequency.items()
        }

    def scores(self, query: str) -> list[float]:
        query_tokens = tokenize(query)
        output = []
        for counts, length in zip(self.counts, self.lengths):
            score = 0.0
            for token in query_tokens:
                frequency = counts.get(token, 0)
                if not frequency:
                    continue
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * length / max(self.average_length, 1)
                )
                score += self.idf.get(token, 0.0) * frequency * (self.k1 + 1) / denominator
            output.append(score)
        return output


class DenseEncoder:
    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashingDenseEncoder:
    """Small deterministic vector backend used when learned models are unavailable."""

    def __init__(self, dimensions: int = 256):
        self.dimensions = dimensions

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            tokens = tokenize(text)
            for token in tokens:
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimensions
                sign = 1.0 if digest[4] & 1 else -1.0
                vector[index] += sign
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


class SentenceTransformerEncoder:
    """Optional learned dense retriever; importing models remains explicit."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("请安装 rag 依赖：pip install -e '.[rag]'") from exc
        self.model = SentenceTransformer(model_name)

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        values = self.model.encode(list(texts), normalize_embeddings=True)
        return [list(map(float, row)) for row in values]


class Reranker:
    def score(self, query: str, documents: Sequence[str]) -> list[float]: ...


class LexicalReranker:
    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        query_tokens = set(tokenize(query))
        if not query_tokens:
            return [0.0] * len(documents)
        return [len(query_tokens.intersection(tokenize(document))) / len(query_tokens)
                for document in documents]


class BGEReranker:
    """Optional BGE CrossEncoder adapter for the final candidate stage."""

    def __init__(self, model_name: str = "BAAI/bge-reranker-base"):
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RuntimeError("请安装 rag 依赖：pip install -e '.[rag]'") from exc
        self.model = CrossEncoder(model_name)

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        values = self.model.predict([(query, document) for document in documents])
        return [float(value) for value in values]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _normalize(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if high == low:
        return [1.0 if high > 0 else 0.0 for _ in values]
    return [(value - low) / (high - low) for value in values]


class HybridCodeSearch:
    """In-memory hybrid index suitable for a single local task worktree."""

    def __init__(self, root: str | Path, encoder: DenseEncoder | None = None,
                 reranker: Reranker | None = None):
        self.root = Path(root).resolve()
        self.chunks = chunk_repository(self.root)
        self.documents = [f"{chunk.path} {chunk.symbol}\n{chunk.text}" for chunk in self.chunks]
        self.bm25 = BM25Index(self.documents)
        self.encoder = encoder or HashingDenseEncoder()
        self.reranker = reranker or LexicalReranker()
        self.vectors = self.encoder.encode(self.documents)

    def search(self, query: str, top_k: int = 5, candidate_k: int = 20) -> list[SearchHit]:
        if not query.strip() or not self.chunks:
            return []
        top_k = max(1, min(int(top_k), 10))
        candidate_k = max(top_k, min(int(candidate_k), 100))
        bm25 = self.bm25.scores(query)
        query_vector = self.encoder.encode([query])[0]
        dense = [_cosine(query_vector, vector) for vector in self.vectors]
        normalized_bm25, normalized_dense = _normalize(bm25), _normalize(dense)
        hybrid = [0.65 * lexical + 0.35 * semantic
                  for lexical, semantic in zip(normalized_bm25, normalized_dense)]
        candidates = sorted(range(len(self.chunks)), key=lambda index: hybrid[index], reverse=True)[:candidate_k]
        rerank_values = self.reranker.score(query, [self.documents[index] for index in candidates])
        normalized_rerank = _normalize(rerank_values)
        hits = []
        for position, index in enumerate(candidates):
            final = 0.45 * hybrid[index] + 0.55 * normalized_rerank[position]
            hits.append(SearchHit(self.chunks[index], final, bm25[index], dense[index], rerank_values[position]))
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:top_k]


def build_code_search(root: str | Path) -> HybridCodeSearch:
    """Build configured backends; learned models are explicitly opt-in."""
    dense_model = os.environ.get("AGENT_DENSE_MODEL", "").strip()
    reranker_model = os.environ.get("AGENT_RERANKER_MODEL", "").strip()
    encoder = SentenceTransformerEncoder(dense_model) if dense_model else HashingDenseEncoder()
    reranker = BGEReranker(reranker_model) if reranker_model else LexicalReranker()
    return HybridCodeSearch(root, encoder=encoder, reranker=reranker)


def format_hits(hits: Iterable[SearchHit]) -> str:
    sections = []
    for number, hit in enumerate(hits, 1):
        sections.append(
            f"[{number}] {hit.chunk.reference} symbol={hit.chunk.symbol or '-'} "
            f"kind={hit.chunk.kind} score={hit.score:.3f}\n{hit.chunk.text}"
        )
    return "\n\n".join(sections) if sections else "没有检索到相关代码或文档。"
