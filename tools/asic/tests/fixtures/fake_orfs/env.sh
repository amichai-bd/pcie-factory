# Fake ORFS environment for tools/asic unit tests: puts the fake tools first.
export PATH="$(dirname "${BASH_SOURCE[0]}")/bin:$PATH"
