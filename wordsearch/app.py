#!/usr/bin/env python3
"""
Python port of the Bash+awk word-search solver.

Input file format (same as the original script expects):
- First line: "<rows>x<cols>" (e.g., "5x5")
- Next <rows> lines: the grid. Cells may be either space-separated characters
  (e.g., "A B C D E") or contiguous characters (e.g., "ABCDE"). Mixed spacing is handled.
- Remaining lines: one word per line to search (spaces inside words are ignored).

Output format matches the awk script:
    <word> <start_r>:<start_c> <end_r>:<end_c>
Where coordinates are zero-indexed (r=row, c=column).

Usage:
    python wordsearch.py filename.txt
"""
from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple, Iterable


@dataclass(frozen=True)
class Match:
    word: str
    start: Tuple[int, int]
    end: Tuple[int, int]

    def to_output(self) -> str:
        (r1, c1) = self.start
        (r2, c2) = self.end
        return f"{self.word} {r1}:{c1} {r2}:{c2}"


def _parse_file(path: Path) -> Tuple[int, int, List[List[str]], List[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError("Input file is empty")

    dims = lines[0].strip()
    if "x" not in dims:
        raise ValueError("First line must be of the form '<rows>x<cols>'")
    try:
        rows_s, cols_s = dims.split("x", 1)
        rows = int(rows_s.strip())
        cols = int(cols_s.strip())
    except Exception as e:
        raise ValueError("Invalid dimensions line; expected '<rows>x<cols>'") from e

    grid: List[List[str]] = []
    words: List[str] = []

    # Next rows lines belong to the grid
    for i in range(1, min(1 + rows, len(lines))):
        raw = lines[i].rstrip("\n")
        # Support either space-separated cells or contiguous characters
        tokens = [t for t in raw.replace("\t", " ").split(" ") if t != ""]
        if len(tokens) == 0 and len(raw) > 0:
            # Edge case: all spaces — treat as empty row
            tokens = []
        if len(tokens) == 1 and len(tokens[0]) == cols:
            # e.g., "ABCDE" -> ["A","B","C","D","E"]
            row = list(tokens[0])
        else:
            # e.g., "A B C D E" -> tokens already cells (may be multi-char)
            row = tokens
        if len(row) != cols:
            raise ValueError(
                f"Grid row {i} has {len(row)} cells; expected {cols}. Raw line: '{raw}'"
            )
        grid.append(row)

    if len(grid) != rows:
        raise ValueError(f"File declares {rows} rows but provided {len(grid)} grid rows")

    # Remaining lines are words; strip spaces inside words (to mirror awk's gsub)
    for i in range(1 + rows, len(lines)):
        w = lines[i].strip()
        if w == "":
            continue
        words.append("".join(ch for ch in w if not ch.isspace()))

    return rows, cols, grid, words


def _in_bounds(r: int, c: int, rows: int, cols: int) -> bool:
    return 0 <= r < rows and 0 <= c < cols


def _collect(grid: List[List[str]], r: int, c: int, dr: int, dc: int, length: int) -> List[str]:
    rows, cols = len(grid), len(grid[0]) if grid else 0
    letters: List[str] = []
    rr, cc = r, c
    for _ in range(length):
        if not _in_bounds(rr, cc, rows, cols):
            return []  # out of bounds; no match possible in this direction
        letters.append(grid[rr][cc])
        rr += dr
        cc += dc
    return letters


def find_matches(rows: int, cols: int, grid: List[List[str]], words: Iterable[str]) -> List[Match]:
    # 8 directions: (dr, dc)
    dirs = [
        (0, 1),   # → right
        (0, -1),  # ← left
        (1, 0),   # ↓ down
        (-1, 0),  # ↑ up
        (1, 1),   # ↘ diag
        (-1, -1), # ↖ diag
        (1, -1),  # ↙ anti-diag
        (-1, 1),  # ↗ anti-diag
    ]

    out: List[Match] = []

    # Allow multi-character cells just like awk ($i can be multi-char)
    for word in words:
        if not word:
            continue
        wlen = len(word)
        for r in range(rows):
            for c in range(cols):
                for dr, dc in dirs:
                    chunk = _collect(grid, r, c, dr, dc, wlen)
                    if not chunk:
                        continue
                    if "".join(chunk) == word:
                        end_r = r + (wlen - 1) * dr
                        end_c = c + (wlen - 1) * dc
                        out.append(Match(word, (r, c), (end_r, end_c)))
    return out


def run_file(path: Path) -> List[str]:
    rows, cols, grid, words = _parse_file(path)
    matches = find_matches(rows, cols, grid, words)
    return [m.to_output() for m in matches]


def main(argv: List[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("Usage: wordsearch.py <filename>", file=sys.stderr)
        return 2
    path = Path(argv[0])
    try:
        for line in run_file(path):
            print(line)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
