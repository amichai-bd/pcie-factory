"""Reference model of common_cdc_rst (wiki/src/rtl/common/common_cdc_rst.md#behavior).
Pure Python, so its unit tests run without a simulator.

Times are integer picoseconds. The model takes the changes of src_rst_i and
the rising dst_clk_i edges, and gives the changes dst_rst_o must make in
zero-delay simulation, where no flop resolves late.
"""

from bisect import bisect_right


def expected_output(src_changes: list[tuple[int, int]], edges: list[int],
                    stages: int) -> list[tuple[int, int]]:
    """(time, value) changes of dst_rst_o for ``src_changes``, the (time,
    value) changes of src_rst_i in increasing time, the first a rise.

    Assertion: dst_rst_o rises at the time src_rst_i rises, unless it is
    already high. Release: after the last fall of src_rst_i, dst_rst_o
    falls at the ``stages``-th rising edge strictly after that fall, unless
    src_rst_i rises again first (then the count restarts at its next
    fall). A src_rst_i change at the time of an edge is outside the model:
    the caller keeps them apart."""
    if not src_changes or src_changes[0][1] != 1:
        raise ValueError("the first src_rst_i change must be a rise")
    edge_set = set(edges)
    out: list[tuple[int, int]] = []
    level = 0
    for i, (t, v) in enumerate(src_changes):
        if t in edge_set:
            raise ValueError(f"src_rst_i changes at {t} ps, on a dst_clk_i edge")
        if v and not level:
            out.append((t, 1))
            level = 1
        elif not v:
            k = bisect_right(edges, t) + stages - 1
            nxt = src_changes[i + 1][0] if i + 1 < len(src_changes) else None
            if k < len(edges) and (nxt is None or edges[k] < nxt):
                out.append((edges[k], 0))
                level = 0
    return out


def release_edge(fall: int, edges: list[int], stages: int) -> int | None:
    """The rising edge at which a release that starts at ``fall`` ends, or
    None when the recorded edges stop before it."""
    k = bisect_right(edges, fall) + stages - 1
    return edges[k] if k < len(edges) else None
