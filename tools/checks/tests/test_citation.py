import time
from pathlib import Path

import pytest

from build.ip import REPO_ROOT
from checks import citation
from checks.common import CheckConfigError

# Made-up standard names: these tests must not name a real design target.
GOOD = "[cite: Widget Bus Spec, rev 2.1, §4.3.2]"


def words(n: int) -> str:
    return " ".join(f"w{i}" for i in range(n))


def scan(text: str, markdown: bool = True, max_words: int = 5) -> list[str]:
    return [f"{n}: {m}" for n, m in citation.scan_text(text, markdown, max_words)]


# --- citation form -----------------------------------------------------------

@pytest.mark.parametrize("cite", [
    GOOD,
    "[cite: XYZ 1234, rev 2017, §A.3]",
    "[cite: Frob (Base), rev r1.0-draft, §12]",
])
def test_well_formed_citations_pass(cite):
    assert scan(f"The block paraphrases the rule {cite}.\n") == []


@pytest.mark.parametrize("cite", [
    "[cite: Widget Bus Spec, §4.3.2]",               # no revision
    "[cite: Widget Bus Spec, rev 2.1]",              # no section
    "[cite: Widget Bus Spec, rev 2.1, 4.3.2]",       # section without §
    "[cite: Widget Bus Spec, §4.3.2, rev 2.1]",      # wrong order
    "[Cite: Widget Bus Spec, rev 2.1, §4.3.2]",      # wrong case
    "[cite:Widget Bus Spec, rev 2.1, §4.3.2]",       # spacing
    "[cite: , rev 2.1, §4.3.2]",                     # empty standard
    "[cite: Widget Bus Spec, rev 2.1, §4.3.2",       # unclosed
    "[cite: Widget Bus Spec, rev 2.1, §4.3.2 and §5]",
])
def test_malformed_citations_fail(cite):
    hits = scan(f"Rule {cite} here\n")
    assert hits and "malformed citation" in hits[0] and hits[0].startswith("1: ")
    assert citation.FORM in hits[0]


@pytest.mark.parametrize("text", [
    "The block resets [cite: Widget Bus\nSpec, rev 2.1, §4.3.2] first.\n",
    "The block resets [cite: Widget Bus Spec,\n  rev 2.1, §4.3.2] first.\n",
    "The block resets [cite: Widget Bus Spec, rev\n2.1,\n§4.3.2] first.\n",
    "The block resets [cite:\nWidget Bus Spec, rev 2.1, §4.3.2] first.\n",
])
def test_wrapped_citation_passes_in_markdown(text):
    assert scan(text) == []
    # Source comments are not wrapped prose: the same text is malformed there.
    hits = scan(text, markdown=False)
    assert hits and "malformed citation" in hits[0]


def test_wrapped_malformed_citation_reported_once():
    hits = scan("x\n[cite: Widget Bus Spec,\nrev 2.1 §4.3.2] y\n")
    assert len(hits) == 1 and hits[0].startswith("2: malformed citation"), hits


@pytest.mark.parametrize("markdown", [True, False])
def test_long_indented_malformed_wrapped_citation_is_fast(markdown):
    # Regression: the wrapped form once backtracked exponentially here.
    body = "".join(f"   \n        w{i}   " for i in range(30))
    text = f"x [cite: Widget Bus Spec{body} rev 2.1 §4]\n" * 3
    start = time.perf_counter()
    hits = scan(text, markdown=markdown)
    assert time.perf_counter() - start < 0.5
    assert any("malformed citation" in h for h in hits)


def test_long_wrapped_well_formed_citation_passes_fast():
    name = "\n      ".join(f"Part{i}" for i in range(40))
    start = time.perf_counter()
    assert scan(f"x [cite: {name},\n  rev 2.1,\n  §4.3]\n") == []
    assert time.perf_counter() - start < 0.5


def test_unclosed_backticks_stay_in_their_paragraph():
    text = ("a ` b\n\n" * 2000) + "§1\n"
    start = time.perf_counter()
    assert scan(text) == ["4001: section reference '§' outside a citation "
                          f"(expected '{citation.FORM}')"]
    assert time.perf_counter() - start < 0.5


def test_citation_does_not_wrap_over_a_blank_line():
    hits = scan("x [cite: Widget Bus Spec,\n\nrev 2.1, §4.3.2]\n")
    assert [h.split(" ")[1] for h in hits] == ["malformed", "section"], hits


@pytest.mark.parametrize("text", [
    "See §4.3 of the bus spec.",
    "As section 4.3.2 requires, the bus resets.",
    "Per Clause 10.2.1, idle is low.",
    "sec. 3.1 says so",
])
def test_uncited_section_reference_fails(text):
    hits = scan(text + "\n")
    assert len(hits) == 1 and "outside a citation" in hits[0]


def test_plain_numbers_and_internal_words_pass():
    assert scan("Section headings stay stable; version 4.3.2 of the tool.\n") == []


def test_malformed_citation_in_source_comment():
    hits = scan("// reset per [cite: Widget Bus Spec rev 2.1 §4]\n", markdown=False)
    assert len(hits) == 1 and "malformed citation" in hits[0]


# --- quotes ------------------------------------------------------------------

def test_block_quote_boundary():
    assert scan(f"> {words(5)}\n") == []
    hits = scan(f"Intro.\n\n> {words(3)}\n> {words(3)}\n\nAfter.\n")
    assert hits == ["3: block quote of 6 words (max 5): 'w0 w1 w2 w0 w1 w2'"]


def test_quoted_run_boundary_and_markdown_paragraph():
    assert scan(f'He said "{words(5)}".\n') == []
    assert scan(f'He said "{words(6)}".\n')[0].startswith("1: quoted run of 6 words")
    # A Markdown paragraph may wrap a quote over lines, but not over a blank line.
    assert scan(f'Text "{words(3)}\n{words(3)}" end.\n')[0].startswith("1: quoted run of 6")
    assert scan(f'Text "{words(3)}\n\n{words(3)}" end.\n') == []


def test_typographic_quotes():
    assert scan(f"Text “{words(6)}” end.\n", markdown=False)[0].startswith("1: quoted run")


def test_source_file_quotes_are_single_line_and_skip_triple_quotes():
    assert scan(f'# "{words(6)}"\n', markdown=False)[0].startswith("1: quoted run")
    assert scan(f'x = "{words(3)}\n{words(3)}"\n', markdown=False) == []
    assert scan(f'"""{words(20)}"""\n', markdown=False) == []
    # Block quotes are a Markdown construct only.
    assert scan(f"> {words(6)}\n", markdown=False) == []


def test_markdown_code_is_not_prose():
    text = (f"```\n> {words(9)}\nsee §4.3 \"{words(9)}\" [cite: x]\n```\n"
            f"Use `[cite: bad]` and ``\"{words(9)}\"`` inline.\n")
    assert scan(text) == []
    # The same in a source file is scanned.
    assert scan(text, markdown=False)


# --- run and config ----------------------------------------------------------

def write(root: Path, rel: str, data) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())


@pytest.fixture
def tree(tmp_path) -> Path:
    write(tmp_path, "cfg/checks.yaml", "citation:\n  max_quote_words: 5\n")
    write(tmp_path, "src/rtl/a/a.sv", f"// idle low {GOOD}\n")
    write(tmp_path, "wiki/src/rtl/a/MAS.md", f"Paraphrase {GOOD}.\n")
    return tmp_path


def test_run_clean_tree_passes(tree):
    r = citation.run(tree, tree / "src")
    assert r.passed, r.findings
    assert r.checked.startswith("2 text file(s) under src/, wiki/src/; quotes up to 5 words")


def test_run_reports_paths_and_skips_binary_and_hidden(tree):
    write(tree, "wiki/src/dv/a/DV_PLAN.md", f"ok\n> {words(6)}\n")
    write(tree, "src/dv/a/test_a.py", f'# see §4\n"{words(6)}"\n')
    write(tree, "src/dv/a/blob.bin", b"\0" + f'"{words(9)}"'.encode())
    write(tree, "src/.hidden/x.md", "§1\n")
    write(tree, "src/dv/a/.x.md", "§1\n")
    write(tree, "docs/out.md", "§1\n")  # outside src/ and wiki/src/
    r = citation.run(tree, tree / "src")
    assert [f.split(": ")[0] + ": " + f.split(": ")[1].split(" ")[0] for f in r.findings] == [
        "src/dv/a/test_a.py:1: section",
        "src/dv/a/test_a.py:2: quoted",
        "wiki/src/dv/a/DV_PLAN.md:2: block",
    ]


@pytest.mark.parametrize("text, msg", [
    ("neutrality: {words: [a]}\n", "'max_quote_words' only"),
    ("citation: {}\n", "'max_quote_words' only"),
    ("citation: {max_quote_words: 5, roots: [x]}\n", "'max_quote_words' only"),
    ("citation: {max_quote_words: 0}\n", "positive integer"),
    ("citation: {max_quote_words: true}\n", "positive integer"),
    ("citation: {max_quote_words: '5'}\n", "positive integer"),
    ("citation: [\n", "invalid YAML"),
])
def test_config_errors(tree, text, msg):
    write(tree, "cfg/checks.yaml", text)
    with pytest.raises(CheckConfigError, match=msg):
        citation.run(tree, tree / "src")


def test_missing_config(tmp_path):
    with pytest.raises(CheckConfigError):
        citation.load_config(tmp_path)


def test_real_config_and_repository_pass():
    assert citation.load_config(REPO_ROOT) >= 10
    r = citation.run(REPO_ROOT, REPO_ROOT / "src")
    assert r.passed, r.findings
