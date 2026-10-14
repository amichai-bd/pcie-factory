"""``./build ci summary``: render a regress summary.json as short Markdown,
for the GitHub job summary."""

from __future__ import annotations

import json
from pathlib import Path


def render(path: Path) -> str:
    try:
        s = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return f"### Regression\n\nNo summary at `{path}` (regress did not finish).\n"
    except (OSError, ValueError) as e:
        return f"### Regression\n\nUnreadable summary `{path}`: {e}\n"
    verdict = "PASS" if s.get("passed") else "FAIL"
    levels = ", ".join(f"L{n}" for n in s.get("levels", []))
    lines = [f"### Regression {levels}: {verdict}", ""]
    ips = s.get("ips", {})
    if ips:
        lines += ["| IP | Selected because |", "| --- | --- |"]
        lines += [f"| `{ip}` | {why} |" for ip, why in ips.items()]
    else:
        lines.append("No IPs selected.")
    totals = s.get("totals", {})
    if totals:
        lines += ["", "| Level | Passed | Runtime (s) | Budget (s) |", "| --- | --- | --- | --- |"]
        for key, t in totals.items():
            over = " (over budget)" if t.get("over_budget") else ""
            lines.append(f"| {key} | {t['passed']}/{t['tests']} | "
                         f"{t['runtime_s']:.1f}{over} | {t['budget_s']:.0f} |")
    bad = [b for b in s.get("builds", []) if b.get("status") != "pass"]
    bad += [t for t in s.get("tests", []) if t.get("status") != "pass"]
    if bad:
        lines += ["", "Not passing:", ""]
        for r in bad:
            what = r.get("name", r.get("build"))
            seed = f" seed={r['seed']}" if "seed" in r else ""
            lines.append(f"- `{r['ip']}:{what}`{seed}: {r['status']} (`{r.get('log', '')}`)")
    if "wall_s" in s:
        lines += ["", f"Wall time: {s['wall_s']:.1f}s"]
    return "\n".join(lines) + "\n"
