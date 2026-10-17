"""Fail closed on every required dependency; skipped is not successful."""


def run(results: dict[str, str]) -> int:
    for job, result in results.items():
        print(f"[ci-gate] {job}: {result}")
    return 0 if results and all(result == "success" for result in results.values()) else 1
