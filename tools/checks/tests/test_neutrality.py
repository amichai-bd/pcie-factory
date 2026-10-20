import subprocess
from pathlib import Path

import pytest

from build.ip import REPO_ROOT
from checks import neutrality
from checks.common import CheckConfigError

# A made-up word list: these tests must not name the real design target.
CONFIG = """# neutrality words for the test repository
neutrality:
  words: [frobnitz, widget bus]
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "remote", "add", "origin", "https://github.com/someone/frobnitz-factory.git")
    write(r, "cfg/checks.yaml", CONFIG)
    write(r, "tools/ok.py", "x = 1\n")
    write(r, "src/rtl/a/frobnitz_a.sv", "// frobnitz is fine in src/\n")
    write(r, "wiki/src/a/MAS.md", "Frobnitz\n")
    write(r, "wiki/tracker/data/1.json", '{"frobnitz": 1}\n')
    git(r, "add", "-A")
    return r


def findings(repo: Path):
    return neutrality.run(repo).findings


def test_clean_repo_passes(repo):
    r = neutrality.run(repo)
    assert r.passed, r.findings
    # cfg/checks.yaml and tools/ok.py; the three exempt trees are skipped
    assert r.checked.startswith("2 file(s) (tracked and untracked, not ignored) outside "
                                "src/, wiki/src/, wiki/tracker/data/")
    assert "slug exempt in URLs: someone/frobnitz-factory" in r.checked


def test_words_in_tools_and_wiki_fail(repo):
    write(repo, "tools/x.py", "NAME = 'FrobNitz'\n\nbus = 'Widget-Bus'\n")
    write(repo, "wiki/bkm/x.md", "a widget_bus, a  widget \t bus and a WidgetBus\n")
    assert findings(repo) == [
        "tools/x.py:1: 'FrobNitz'",
        "tools/x.py:3: 'Widget-Bus'",
        "wiki/bkm/x.md:1: 'widget_bus'",
        "wiki/bkm/x.md:1: 'widget \\t bus'",
        "wiki/bkm/x.md:1: 'WidgetBus'",  # separator-free variant
    ]


def test_path_names_are_scanned(repo):
    write(repo, "tools/frobnitz_util.py", "")
    assert findings(repo) == ["tools/frobnitz_util.py: path contains 'frobnitz'"]


@pytest.mark.parametrize("text, ok", [
    ("see https://github.com/someone/frobnitz-factory/issues/1", True),
    ("git@github.com:someone/frobnitz-factory.git", True),
    ("https://someone.github.io/frobnitz-factory/", True),
    ("https://GitHub.com/Someone/Frobnitz-Factory", True),
    ("github.com/someone/frobnitz-factory", True),
    ("https://github.com/someone/frobnitz-factory-extra", False),
    ("https://github.com/someone/frobnitz-factory.frobnitz", False),
    ("https://github.com/other/frobnitz-factory", False),
    ("the frobnitz-factory repository", False),
    ("https://example.com/someone/frobnitz-factory", False),
    ("https://evilgithub.com/someone/frobnitz-factory", False),
    ("https://xsomeone.github.io/frobnitz-factory/", False),
])
def test_slug_exempt_only_in_github_urls(repo, text, ok):
    write(repo, "README.md", text + "\n")
    assert (findings(repo) == []) is ok


def test_configured_slug_overrides_remote(repo):
    write(repo, "cfg/checks.yaml", CONFIG + "  repo_slug: me/frobnitz-x\n")
    write(repo, "README.md", "https://github.com/me/frobnitz-x and "
                             "https://github.com/someone/frobnitz-factory\n")
    assert findings(repo) == ["README.md:1: 'frobnitz'"]  # the slug value itself is blanked


def test_no_remote_no_slug(repo):
    git(repo, "remote", "remove", "origin")
    write(repo, "README.md", "https://github.com/someone/frobnitz-factory\n")
    assert findings(repo) == ["README.md:1: 'frobnitz'"]


def test_untracked_files_scanned_ignored_and_binary_skipped(repo):
    write(repo, ".gitignore", "workdir/\n")
    write(repo, "workdir/log.txt", "frobnitz\n")
    write(repo, "notes.md", "frobnitz\n")  # untracked, not ignored
    (repo / "tools/blob.bin").write_bytes(b"\0frobnitz")
    assert findings(repo) == ["notes.md:1: 'frobnitz'"]


def test_config_file_only_word_list_and_slug_are_blanked(repo):
    assert findings(repo) == []  # the word list itself does not trip the check
    write(repo, "cfg/checks.yaml", CONFIG + "# frobnitz design notes\n")
    assert findings(repo) == ["cfg/checks.yaml:4: 'frobnitz'"]
    write(repo, "cfg/checks.yaml", CONFIG.replace("neutrality:", "neutrality:  # widget bus"))
    assert findings(repo) == ["cfg/checks.yaml:2: 'widget bus'"]
    write(repo, "cfg/checks.yaml", CONFIG)
    write(repo, "cfg/other.yaml", "x: frobnitz\n")
    assert findings(repo) == ["cfg/other.yaml:1: 'frobnitz'"]


def test_exempt_trees_are_fixed(repo):
    write(repo, "docs/x.md", "frobnitz\n")
    write(repo, "wiki/srcx/x.md", "frobnitz\n")  # prefixes are whole directories
    assert findings(repo) == ["docs/x.md:1: 'frobnitz'", "wiki/srcx/x.md:1: 'frobnitz'"]
    write(repo, "cfg/checks.yaml", CONFIG + "  exclude: [docs/]\n")
    with pytest.raises(CheckConfigError, match="optional 'repo_slug' only"):
        neutrality.run(repo)


@pytest.mark.parametrize("text, msg", [
    ("neutrality: {words: [a]}\nextra: 1\n", "top level must be"),
    ("citation: {max_quote_words: 5}\n", "top level must be"),
    ("publishability: {allow: []}\n", "top level must be"),
    ("neutrality: {repo_slug: a/b}\n", "'words' and optional"),
    ("neutrality: {words: [a], note: x}\n", "'words' and optional"),
    ("neutrality: {words: []}\n", "words is empty"),
    ("neutrality: {words: [1]}\n", "neutrality.words must be a list"),
    ("neutrality:\n  words: [a]\n  repo_slug: nope\n", "owner/repo"),
    ("neutrality: {words: [a]}\n", "one flow list on one line"),
    ("neutrality:\n  words:\n    - a\n", "one flow list on one line"),
    ("neutrality:\n  words: [a,\n    b]\n", "one flow list on one line"),
    ("neutrality: [\n", "invalid YAML"),
    ("neutrality:\n  words: [a]\n  repo_slug: x/frobnitz\n  repo_slug: o/r\n",
     "repo_slug appears more than once"),
])
def test_config_errors(repo, text, msg):
    write(repo, "cfg/checks.yaml", text)
    with pytest.raises(CheckConfigError, match=msg):
        neutrality.run(repo)


def test_citation_section_allowed(repo):
    write(repo, "cfg/checks.yaml", CONFIG + "citation:\n  max_quote_words: 5\n")
    assert neutrality.run(repo).passed


def test_missing_config(tmp_path):
    with pytest.raises(CheckConfigError):
        neutrality.load_config(tmp_path)


def test_slug_from_remote_forms(repo):
    for url in ("git@github.com:o/r.git", "https://github.com/o/r",
                "http://proxy@127.0.0.1:9/git/o/r"):
        git(repo, "remote", "set-url", "origin", url)
        assert neutrality.slug_from_remote(repo) == "o/r"


# --- IP ids used as identifiers (#311) ---------------------------------------
# The placeholder IP id "bridge" is an ordinary English word, like the real ids
# that motivated the rule; it comes from discovery, never from the tool.

# The sentence a review found in a tools/ fixture README (#306), with the real
# IP id replaced by the placeholder.
SENTENCE_306 = ("the first CI fit with RAMs (`bridge` on PR #276) filled every field")


def add_ip(repo: Path, ip: str) -> None:
    write(repo, f"src/rtl/{ip}/{ip}.rtl.f", f"{ip}.sv\n")
    write(repo, f"src/rtl/{ip}/{ip}.sv", "module m; endmodule\n")


@pytest.fixture
def ip_repo(repo) -> Path:
    add_ip(repo, "bridge")
    return repo


def test_306_fixture_sentence_is_flagged(ip_repo):
    write(ip_repo, "tools/fpga/tests/fixtures/README.md", SENTENCE_306 + "\n")
    assert findings(ip_repo) == [
        "tools/fpga/tests/fixtures/README.md:1: IP id 'bridge' used as an identifier"]
    r = neutrality.run(ip_repo)
    assert "1 IP id(s) as identifiers" in r.checked


@pytest.mark.parametrize("text", [
    "`bridge`", "'bridge'", '"bridge"',                 # whole code span or quoted string
    "see bridge/README.md", "src/rtl/bridge/x.sv",      # leading and inner path segment
    "./build sim --ip bridge", "--ip=bridge",           # IP selector
    "run `bridge:test_reset`",                          # <id>:<test>
    "the bridge IP", "the bridge sub-IP", "two bridge IPs",
])
def test_identifier_forms_are_flagged(ip_repo, text):
    write(ip_repo, "wiki/bkm/x.md", text + "\n")
    assert findings(ip_repo) == ["wiki/bkm/x.md:1: IP id 'bridge' used as an identifier"]


# Ordinary English use passes by design: the rule is identifier-scoped. A
# capitalized "Bridge IP" also passes (ids are lower-case and matched
# case-sensitively); that is a documented limit, left to review.
@pytest.mark.parametrize("text", [
    "the bridge layer forwards packets",                # prose
    "a bridge between two clock domains",
    "`bridge layer`", "`bridges`", "`bridge_x`",        # a longer span is not the id
    "tools/bridge.py", "die/bridge",                    # a file stem, a last segment
    "Bridge IP",                                        # capitalized: passes by design
    "--ipx bridge",
])
def test_plain_english_passes(ip_repo, text):
    write(ip_repo, "wiki/bkm/x.md", text + "\n")
    assert findings(ip_repo) == []


def test_ip_names_in_paths_are_flagged(ip_repo):
    write(ip_repo, "tools/bridge/util.py", "")
    write(ip_repo, "tools/bridge.py", "")  # a file stem is not a path segment
    assert findings(ip_repo) == ["tools/bridge/util.py: path names IP id 'bridge'"]


def test_ip_ids_come_from_discovery(repo):
    write(repo, "wiki/bkm/x.md", SENTENCE_306 + "\n")
    assert findings(repo) == []  # no IP named "bridge" under src/
    add_ip(repo, "bridge")
    assert findings(repo) == ["wiki/bkm/x.md:1: IP id 'bridge' used as an identifier"]
    other = repo.parent / "elsewhere"
    add_ip(other, "valve")  # an explicit source root replaces <repo>/src
    write(repo, "wiki/bkm/x.md", "`valve` and `bridge`\n")
    assert neutrality.run(repo, src_root=other / "src").findings == [
        "wiki/bkm/x.md:1: IP id 'valve' used as an identifier"]


def test_ip_rule_respects_exempt_trees_and_slug(ip_repo):
    write(ip_repo, "wiki/src/bridge/MAS.md", "`bridge`\n")
    write(ip_repo, "wiki/tracker/data/x.json", '{"bridge": 1}\n')
    write(ip_repo, "README.md", "https://github.com/someone/frobnitz-factory\n")
    assert findings(ip_repo) == []


def test_ip_id_equal_to_a_top_level_entry_skips_only_entry_forms(ip_repo):
    # As a path segment or a whole quoted string, "tools" names the top-level
    # folder; its selector forms and "tools IP" still name the IP.
    add_ip(ip_repo, "tools")
    write(ip_repo, "wiki/bkm/x.md", "edit tools/ok.py, x/tools/y, load(root / \"tools\") "
                                    "and `tools`\n`bridge`\n--ip tools\nrun `tools:test_a`\n"
                                    "the tools IP\n")
    assert findings(ip_repo) == [f"wiki/bkm/x.md:{n}: IP id '{i}' used as an identifier"
                                 for n, i in ((2, "bridge"), (3, "tools"), (4, "tools"),
                                              (5, "tools"))]


def test_minified_contents_skip_only_the_ip_rule(ip_repo):
    write(ip_repo, "tools/site/vendor/lib.min.js", 'a("bridge");frobnitz\n')
    write(ip_repo, "tools/site/vendor/lib.min.css", '.x{content:"bridge"}\n')
    write(ip_repo, "tools/bridge/lib.min.js", "")
    assert findings(ip_repo) == [
        "tools/bridge/lib.min.js: path names IP id 'bridge'",
        "tools/site/vendor/lib.min.js:1: 'frobnitz'",
    ]


def test_real_config_and_repository_is_neutral():
    cfg = neutrality.load_config(REPO_ROOT)
    assert len(set(cfg.words)) == len(cfg.words) >= 5  # target, protocol and terms
    assert neutrality.EXEMPT == ("src/", "wiki/src/", "wiki/tracker/data/")
    r = neutrality.run(REPO_ROOT)
    assert r.passed, r.findings
