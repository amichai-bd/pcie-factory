#!/bin/bash
# Provision the pinned toolchain in a Claude Code cloud session.
#
# Owning spec: wiki/tools/host/SPEC.md. Pins live in cfg/host.yaml and
# tools/requirements.txt; nothing is pinned here. Every step is skipped when
# its tool already matches the pin, so a cached container starts in seconds.
#
# Provenance: shape adapted from nand2anything .claude/hooks/session-start.sh
# (commit 085ef4b; no license file in that repository, same owner). Rewritten:
# YAML pins via a stdlib parser, Verilator and Python packages only.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

ROOT=${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}

# Read pins with the system python3 and the stdlib only: PyYAML may not be
# installed yet. The parser accepts the subset documented in cfg/host.yaml.
# Also enforces python.minimum. An assignment (not eval "$(...)") fails
# under set -e when python3 does.
pins=$(python3 -I - "$ROOT/cfg/host.yaml" <<'PY'
import shlex, sys

def scalar(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text

def parse(path):
    root = {}
    stack = [(-1, root)]
    for raw in open(path, encoding="utf-8"):
        line = raw.split(" #", 1)[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        key, sep, value = line.strip().partition(":")
        if not sep:
            sys.exit(f"session-start: cannot parse {path}: {raw.strip()!r}")
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        value = value.strip()
        if not value:
            parent[key] = {}
            stack.append((indent, parent[key]))
        elif value.startswith("[") and value.endswith("]"):
            parent[key] = [scalar(v) for v in value[1:-1].split(",") if v.strip()]
        else:
            parent[key] = scalar(value)
    return root

host = parse(sys.argv[1])
v, py, specs = host["verilator"], host["python"], host["specs"]
minimum = tuple(int(x) for x in py["minimum"].split("."))
if sys.version_info[:len(minimum)] < minimum:
    sys.exit(f"session-start: python3 {sys.version.split()[0]} < required {py['minimum']}")
values = {
    "VERILATOR_VERSION": v["version"],
    "VERILATOR_TAG": v["tag"],
    "VERILATOR_COMMIT": v["commit"],
    "VERILATOR_REPO": v["repo"],
    "VERILATOR_PREFIX": v["prefix"],
    "VERILATOR_DEPS": " ".join(v["build_deps"]),
    "REQUIREMENTS": py["requirements"],
    "SPECS_FETCH": specs["fetch"],
    "SPECS_TEXT_PACKAGE": specs["text_package"],
}
for key, value in values.items():
    if not isinstance(value, str) or not value:
        sys.exit(f"session-start: cfg/host.yaml value for {key} must be a non-empty string")
    print(f"{key}={shlex.quote(value)}")
PY
)
eval "$pins"

# Test-only overrides, for exercising the build path without touching the
# pinned install: HOST_VERILATOR_PREFIX (install prefix) and
# HOST_VERILATOR_TAG (tag to clone; the commit check still applies).
PREFIX=${HOST_VERILATOR_PREFIX:-$VERILATOR_PREFIX}
TAG=${HOST_VERILATOR_TAG:-$VERILATOR_TAG}
VERILATOR=$PREFIX/bin/verilator

# PREFIX is written to $CLAUDE_ENV_FILE, which the session shell
# sources: accept only a plain absolute path.
if [[ ! "$PREFIX" =~ ^/[A-Za-z0-9._/+-]+$ || "$PREFIX" == *..* ]]; then
  echo "session-start: unsafe Verilator prefix: $PREFIX" >&2
  exit 1
fi

verilator_ok() {
  "$VERILATOR" --version 2>/dev/null | grep -q "^Verilator ${VERILATOR_VERSION} "
}

src=''
trap '[ -z "$src" ] || rm -rf "$src"' EXIT

if ! verilator_ok; then
  # One build per prefix at a time, even across concurrent sessions.
  exec 9>"/tmp/session-start-verilator-${PREFIX//\//_}.lock"
  flock 9
  if ! verilator_ok; then
    log=$(mktemp /tmp/session-start-verilator.XXXXXX.log)
    echo "session-start: building Verilator $VERILATOR_VERSION ($TAG) into $PREFIX (log: $log)"
    read -r -a deps <<<"$VERILATOR_DEPS"
    missing=()
    for pkg in "${deps[@]}"; do
      dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
    done
    src=$(mktemp -d)
    # A separate bash keeps errexit in force: inside `if ! ( ... )` the
    # current shell would ignore set -e and run on past a failed step.
    if ! bash -euo pipefail -c '
      src=$1 repo=$2 tag=$3 commit=$4 prefix=$5; shift 5
      if [ "$#" -gt 0 ]; then
        apt-get update -q
        DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$@"
      fi
      git -c advice.detachedHead=false clone -q --depth 1 -b "$tag" "$repo" "$src/verilator"
      cd "$src/verilator"
      head=$(git rev-parse HEAD)
      if [ "$head" != "$commit" ]; then
        echo "session-start: $tag is $head, expected $commit" >&2
        exit 1
      fi
      autoconf
      ./configure --prefix="$prefix"
      make -j"$(nproc)"
      make install
    ' build "$src" "$VERILATOR_REPO" "$TAG" "$VERILATOR_COMMIT" "$PREFIX" \
        "${missing[@]}" >"$log" 2>&1; then
      tail -n 40 "$log" >&2
      echo "session-start: Verilator build failed; full log: $log" >&2
      exit 1
    fi
    verilator_ok || { echo "session-start: built Verilator does not report $VERILATOR_VERSION" >&2; exit 1; }
  fi
  flock -u 9
fi

# Python packages go into the system python3 that ./build and cocotb use, so
# no venv or PATH juggling is needed. Skip pip when every exact pin is met.
req=$ROOT/$REQUIREMENTS
if ! python3 -I - "$req" <<'PY'
import sys
from importlib import metadata
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.split("#", 1)[0].strip()
    if not line:
        continue
    name, _, version = line.partition("==")
    try:
        if metadata.version(name.strip()) != version.strip():
            sys.exit(1)
    except metadata.PackageNotFoundError:
        sys.exit(1)
PY
then
  pip_flags=(-q --disable-pip-version-check --root-user-action=ignore)
  # PEP 668: Debian/Ubuntu mark their python3 externally managed. The cloud
  # container is single-purpose and disposable, so installing into it (pip
  # writes to /usr/local, not the apt-owned dist-packages) is the simplest
  # way to give ./build one interpreter with every pin.
  if python3 -I -c 'import os, sys, sysconfig; sys.exit(not os.path.exists(os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED")))'; then
    pip_flags+=(--break-system-packages)
  fi
  # Debian-packaged modules (for example python3-yaml) have no RECORD file,
  # so pip cannot upgrade them in place; --ignore-installed installs the pin
  # alongside, and /usr/local precedes the apt path on sys.path.
  pip_flags+=(--ignore-installed)
  echo "session-start: installing $REQUIREMENTS"
  python3 -m pip install "${pip_flags[@]}" -r "$req"
fi

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  line="export PATH=\"$PREFIX/bin:\$PATH\""
  grep -qxF "$line" "$CLAUDE_ENV_FILE" 2>/dev/null || echo "$line" >>"$CLAUDE_ENV_FILE"
fi

# Reference documents (wiki/tools/specs/SPEC.md): best effort, so a blocked
# network, a sha256 mismatch or a missing text extractor is reported and
# never fails the session start. The cache lives outside the repository.
if [ "$SPECS_FETCH" = true ]; then
  if ! command -v pdftotext >/dev/null 2>&1; then
    echo "session-start: installing $SPECS_TEXT_PACKAGE (best effort)"
    export DEBIAN_FRONTEND=noninteractive
    { timeout 300 apt-get install -y -q "$SPECS_TEXT_PACKAGE" \
      || { timeout 300 apt-get update -q && timeout 300 apt-get install -y -q "$SPECS_TEXT_PACKAGE"; }; } \
      >/dev/null 2>&1 || echo "session-start: $SPECS_TEXT_PACKAGE not installed; text will not be extracted"
  fi
  timeout 300 "$ROOT/build" specs fetch || echo "session-start: specs fetch incomplete (see above); continuing"
fi

"$VERILATOR" --version
python3 -I -c 'import sys, cocotb, pytest, yaml, markdown_it, pygments
print("python", sys.version.split()[0], "cocotb", cocotb.__version__,
      "pytest", pytest.__version__, "PyYAML", yaml.__version__,
      "markdown-it-py", markdown_it.__version__, "Pygments", pygments.__version__)'
