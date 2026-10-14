import subprocess
from pathlib import Path

import pytest

from build.ip import REPO_ROOT
from checks import publishability
from checks.common import CheckConfigError

# Every secret-shaped or personal value below is assembled at run time, so
# this file itself never matches a rule.
AT = "@"
GH_TOKEN = "gh" + "p_" + "Ab3" * 12
FINE_TOKEN = "github" + "_pat_" + "11ABCDEFG0" * 3
AWS_KEY = "AK" + "IA" + "Q7XW3RZ5TY2UV4PN"
GOOGLE_KEY = "AI" + "za" + "Sy" + "B" * 33
SLACK_TOKEN = "xo" + "xb-" + "1234567890-abcdefghij"
SK_KEY = "s" + "k-" + "proj-" + "x9" * 20
GITLAB_TOKEN = "gl" + "pat-" + "a1B2c3D4e5F6g7H8i9J0"
NPM_TOKEN = "np" + "m_" + "z" * 36
KEY_HEADER = "-----BEGIN " + "OPENSSH PRIVATE" + " KEY-----"
RSA_HEADER = "-----BEGIN " + "RSA PRIVATE" + " KEY-----"
HOME = "/ho" + "me/" + "jdoe"
MAC_HOME = "/Us" + "ers/" + "jdoe"
EMAIL = "jane.doe" + AT + "mailhost.net"
URL_CREDS = "https://" + "deploy:hunter2" + AT + "git.corp.net/x.git"
IP4 = "10.0." + "12.7"
IP4B = "192.168." + "1.20"
IP4C = "10.9." + "8.7"
IP4D = "10.1." + "1.1"
IP6 = "fe80:" + ":1a2b:3c4d"
IP6B = "2a01:4f8:c0c:12" + "34::1"

CONFIG = "neutrality:\n  words: [frobnitz]\npublishability:\n  allow: []\n"


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
    write(r, "cfg/checks.yaml", CONFIG)
    write(r, "tools/ok.py", "x = 1\n")
    git(r, "add", "-A")
    return r


def findings(repo: Path):
    return publishability.run(repo).findings


def test_clean_repo_passes(repo):
    r = publishability.run(repo)
    assert r.passed, r.findings
    assert r.checked == ("2 file(s) (tracked and untracked, not ignored); "
                         f"{len(publishability.RULES)} rule(s); 0 allow entry(ies)")


@pytest.mark.parametrize("rule, text, value", [
    ("private-key", KEY_HEADER, KEY_HEADER),
    ("private-key", RSA_HEADER, RSA_HEADER),
    ("token", f"token = '{GH_TOKEN}'", GH_TOKEN),
    ("token", f"export T={FINE_TOKEN}", FINE_TOKEN),
    ("token", f"key: {AWS_KEY}", AWS_KEY),
    ("token", f"key={GOOGLE_KEY}", GOOGLE_KEY),
    ("token", SLACK_TOKEN, SLACK_TOKEN),
    ("token", SK_KEY, SK_KEY),
    ("token", GITLAB_TOKEN, GITLAB_TOKEN),
    ("token", NPM_TOKEN, NPM_TOKEN),
    ("url-credentials", f"clone {URL_CREDS}", "deploy:hunter2"),
    ("ipv4", f"runner at {IP4}:22", IP4),
    ("ipv4", f"host {IP4B}.", IP4B),
    ("ipv6", f"addr {IP6}", IP6),
    ("ipv6", IP6B, IP6B),
    ("home-path", f"log in {HOME}/work/x.log", HOME),
    ("home-path", f"{MAC_HOME}/src", MAC_HOME),
    ("email", f"contact {EMAIL}.", EMAIL),
])
def test_each_rule_fails_its_fixture(repo, rule, text, value):
    write(repo, "wiki/bkm/x.md", f"intro\n{text}\n")
    assert findings(repo) == [f"wiki/bkm/x.md:2: {rule}: {value!r}"]


@pytest.mark.parametrize("text", [
    "serve on 127.0.0.1:8000 or 0.0.0.0",          # loopback, unspecified
    "doc ranges 192.0.2.1 198.51.100.7 203.0.113.9 2001:db8::1 ::1",
    "version 1.2.3, not 999.1.1.1 nor 1.2.3.4.5",  # not an address
    "a[1::2], pkg::name, a::b, std::vector, 12:30:45, aa:bb:cc:dd:ee:ff",
    "uses: actions/checkout" + AT + "v7.0.1",       # not an email domain
    "user" + AT + "example.com, a" + AT + "b.example, x" + AT + "host.invalid, t" + AT + "x.test",
    "noreply" + AT + "anthropic.com, no-reply" + AT + "corp.net",
    "123+someone" + AT + "users.noreply.github.com",
    "/home/ alone, /home/<user>, /opt/tools/bin, ~/notes",
    "gh" + "p_short, s" + "k-short, AK" + "IAlower",  # too short to be a token
    "http://proxy" + AT + "127.0.0.1:9/git/o/r",     # user without password
    "-----BEGIN " + "PUBLIC KEY-----",
])
def test_harmless_text_passes(repo, text):
    write(repo, "README.md", text + "\n")
    assert findings(repo) == []


def test_every_tree_and_paths_are_scanned(repo):
    write(repo, "src/rtl/a/notes.md", HOME + "\n")
    write(repo, "wiki/tracker/data/1.json", f'{{"h": "{IP4C}"}}\n')
    write(repo, f"wiki/people/{EMAIL}/x.md", "x\n")
    assert findings(repo) == [
        "src/rtl/a/notes.md:1: home-path: " + repr(HOME),
        "wiki/people/" + EMAIL + "/x.md (path): email: " + repr(EMAIL),
        f"wiki/tracker/data/1.json:1: ipv4: {IP4C!r}",
    ]


def test_untracked_scanned_ignored_and_binary_skipped(repo):
    write(repo, ".gitignore", "workdir/\n")
    write(repo, "workdir/log.txt", HOME + "\n")
    write(repo, "notes.md", HOME + "\n")  # untracked, not ignored
    (repo / "tools/blob.bin").write_bytes(b"\0" + HOME.encode())
    assert findings(repo) == ["notes.md:1: home-path: " + repr(HOME)]


def allow(*entries: str) -> str:
    return CONFIG.replace("  allow: []\n", "  allow:\n" + "".join(entries))


def test_allow_entry_exempts_exact_match_in_its_paths(repo):
    write(repo, "tools/tests/t.py", f"x = '{HOME}'\ny = '{HOME}x'\n")
    write(repo, "tools/a.py", f"x = '{HOME}'\n")
    write(repo, "cfg/checks.yaml", allow(
        f"    - {{rule: home-path, match: '{HOME}', path: 'tools/tests/*', reason: planted}}\n"))
    assert findings(repo) == [
        "tools/a.py:1: home-path: " + repr(HOME),           # outside the glob
        "tools/tests/t.py:2: home-path: " + repr(HOME + "x"),  # not the exact match
    ]
    write(repo, "tools/a.py", "")
    assert findings(repo) == ["tools/tests/t.py:2: home-path: " + repr(HOME + "x")]


def test_allow_entry_without_path_applies_everywhere(repo):
    write(repo, "a.md", "git" + AT + "github.com:o/r.git\n")
    assert findings(repo) == ["a.md:1: email: 'git" + AT + "github.com'"]
    write(repo, "cfg/checks.yaml", allow(
        "    - {rule: email, match: 'git" + AT + "github.com', reason: ssh user}\n"))
    assert findings(repo) == []


def test_allow_entry_is_per_rule(repo):
    write(repo, "a.md", IP4D + "\n")
    write(repo, "cfg/checks.yaml", allow(f"    - {{rule: ipv6, match: {IP4D}, reason: r}}\n"))
    assert findings(repo) == [
        f"a.md:1: ipv4: {IP4D!r}",
        f"cfg/checks.yaml:5: ipv4: {IP4D!r}",  # only an allowed value may sit in the config
        f"cfg/checks.yaml: publishability.allow[0] (ipv6 {IP4D!r}): stale, matches nothing"]


def test_stale_entry_fails(repo):
    write(repo, "cfg/checks.yaml", allow(
        f"    - {{rule: home-path, match: '{HOME}', path: 'tools/*', reason: gone}}\n"))
    assert findings(repo) == [
        f"cfg/checks.yaml: publishability.allow[0] (home-path {HOME!r} in tools/*): "
        "stale, matches nothing"]


def test_config_may_name_only_allowed_values(repo):
    write(repo, "tools/t.py", HOME + "\n")
    write(repo, "cfg/checks.yaml", allow(
        f"    - {{rule: home-path, match: '{HOME}', path: 'tools/*', reason: r}}\n")
        + f"# also {MAC_HOME}\n")
    assert findings(repo) == ["cfg/checks.yaml:6: home-path: " + repr(MAC_HOME)]


@pytest.mark.parametrize("section, msg", [
    (None, "mapping with only 'allow'"),
    ("publishability: {}\n", "mapping with only 'allow'"),
    ("publishability: {allow: [], skip: [src/]}\n", "mapping with only 'allow'"),
    ("publishability: {allow: x}\n", "allow must be a list"),
    ("publishability:\n  allow:\n    - {rule: email, match: a}\n", "needs 'rule', 'match'"),
    ("publishability:\n  allow:\n    - {rule: email, match: a, reason: r, glob: x}\n",
     "needs 'rule', 'match'"),
    ("publishability:\n  allow:\n    - {rule: secret, match: a, reason: r}\n", "unknown rule"),
    ("publishability:\n  allow:\n    - {rule: email, match: a, reason: ' '}\n",
     "'reason' must be a non-empty"),
    ("publishability:\n  allow:\n    - {rule: email, match: 1, reason: r}\n",
     "'match' must be a non-empty"),
    ("publishability:\n  allow:\n    - {rule: email, match: a, reason: r, path: []}\n",
     "'path' must be a non-empty"),
    ("publishability: [\n", "invalid YAML"),
])
def test_config_errors(repo, section, msg):
    text = "neutrality:\n  words: [frobnitz]\n" + (section or "")
    write(repo, "cfg/checks.yaml", text)
    with pytest.raises(CheckConfigError, match=msg):
        publishability.run(repo)


def test_empty_allow_list_is_valid(repo):
    write(repo, "cfg/checks.yaml", "neutrality:\n  words: [frobnitz]\npublishability:\n  allow:\n")
    assert findings(repo) == []


def test_missing_config(tmp_path):
    with pytest.raises(CheckConfigError):
        publishability.load_config(tmp_path)


def test_real_repository_is_publishable():
    allow_list = publishability.load_config(REPO_ROOT)
    assert all(e.reason for e in allow_list)
    r = publishability.run(REPO_ROOT)
    assert r.passed, r.findings
