import textwrap
from pathlib import Path
from wordsearch import _parse_file, find_matches, run_file


def make_file(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "input.txt"
    p.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return p


def test_parse_space_separated(tmp_path):
    p = make_file(
        tmp_path,
        """
        3x3
        A B C
        D E F
        G H I
        ABC
        CFI
        GDA
        """,
    )
    rows, cols, grid, words = _parse_file(p)
    assert (rows, cols) == (3, 3)
    assert grid == [["A","B","C"],["D","E","F"],["G","H","I"]]
    assert words == ["ABC","CFI","GDA"]


def test_parse_contiguous_rows(tmp_path):
    p = make_file(
        tmp_path,
        """
        2x4
        ABCD
        EFGH
        ABC
        """,
    )
    rows, cols, grid, words = _parse_file(p)
    assert grid == [["A","B","C","D"],["E","F","G","H"]]
    assert words == ["ABC"]


def test_find_matches_directions(tmp_path):
    p = make_file(
        tmp_path,
        """
        3x3
        C A T
        X F O
        G D G
        CAT
        TAF
        CFO
        CGG
        TOG
        """,
    )
    # Directly exercise the CLI-equivalent run
    lines = run_file(p)
    # Convert to a set for easy containment tests
    got = set(lines)
    assert "CAT 0:0 0:2" in got           # → right
    assert "TAF 0:2 1:1" in got           # ↙ anti-diag
    assert "CFO 0:0 2:2" in got           # ↘ diag
    assert "CGG 0:0 2:0" in got           # ↓ down
    assert "TOG 0:2 2:2" in got           # ↓ down last column


def test_multi_char_cells(tmp_path):
    p = make_file(
        tmp_path,
        """
        2x3
        TH IS OK
        AA BB CC
        THIS
        ISOK
        AABB
        BBCC
        """,
    )
    lines = run_file(p)
    got = set(lines)
    # Multi-character cells must be respected just like awk fields
    assert "THIS 0:0 0:1" in got
    assert "ISOK 0:1 0:2" in got
    assert "AABB 1:0 1:1" in got
    assert "BBCC 1:1 1:2" in got


def test_errors(tmp_path):
    p = make_file(
        tmp_path,
        """
        2x2
        A B C
        D E
        WORD
        """,
    )
    try:
        run_file(p)
    except Exception as e:
        assert "Grid row" in str(e)
    else:
        raise AssertionError("Expected an exception for malformed grid row")
