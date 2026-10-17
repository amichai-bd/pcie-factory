Part of #12.

## What
Docs only.

## Validation
At head 0123456789abcdef0123456789abcdef01234567.

| Command | Result |
| --- | --- |
| `./build check` | PASS |

### Not run
- `./build wiki build --strict`: no page links changed; CI builds the site.
