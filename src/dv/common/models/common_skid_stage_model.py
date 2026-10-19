"""Cycle model of common_skid_stage
(wiki/src/rtl/common/common_skid_stage.md#behavior).

The stage holds up to two beats in order: the oldest is offered
downstream (out), a second waits behind it (skid). Upstream is ready
while the stage holds fewer than two. At each rising edge the offered
beat leaves when downstream is ready, and the upstream beat enters when
it is valid and the stage was ready; the next beat offered is the oldest
one held. A beat is new in the first cycle it is offered: it was not
offered and left waiting at the previous edge.
"""

from collections import deque


class SkidStageModel:
    def __init__(self):
        self.beats = deque()
        self.held = False        # the beat offered at the last edge was not taken

    def reset(self) -> None:
        self.beats.clear()
        self.held = False

    # Outputs in the cycle before the next edge.
    @property
    def in_ready(self) -> bool:
        return len(self.beats) < 2

    @property
    def out_valid(self) -> bool:
        return bool(self.beats)

    @property
    def out_data(self):
        return self.beats[0] if self.beats else None

    @property
    def out_new(self) -> bool:
        return self.out_valid and not self.held

    def out_take(self, out_ready: bool) -> bool:
        return self.out_valid and out_ready

    def edge(self, in_valid: bool, in_data: int, out_ready: bool):
        """One rising edge; returns the beat taken downstream, or None."""
        taken = self.beats[0] if self.out_take(out_ready) else None
        accept = in_valid and self.in_ready
        self.held = self.out_valid and not out_ready
        if taken is not None:
            self.beats.popleft()
        if accept:
            self.beats.append(in_data)
        return taken
