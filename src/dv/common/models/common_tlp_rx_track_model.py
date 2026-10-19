"""Cycle model of common_tlp_rx_track
(wiki/src/rtl/common/common_tlp_rx_track.md#behavior).

Beats of a TLP stream are numbered from 0 (the sop) within their TLP;
the count saturates at 63. For the beat taken in a cycle the tracker says
whether it is a sop, a later beat of the open TLP, the TLP's last header
DW (beat 2, or 3 for a 4-DW header), or header DW 0, 1 or 2; it also shows
the state before the beat: whether a TLP is open, its header size, its
beats taken so far and its last header beat. The state stays as the eop
left it until the next sop; a reset clears it.
"""

from dataclasses import dataclass

BEAT_MAX = 63


@dataclass(frozen=True)
class Pos:
    sop: bool
    mid: bool
    hdr_last: bool
    en_dw: int          # bit i: header DW i
    busy: bool
    hdr4: bool
    beat: int
    hdr_end: int


class TlpRxTrackModel:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.busy = False
        self.beat = 0
        self.hdr4 = False

    def framing_error(self, take: bool, sop: bool, eop: bool) -> str | None:
        """The whole-TLP contract a beat breaks, or None."""
        if not take:
            return None
        if not sop and not self.busy:
            return "beat outside a TLP"
        if sop and self.busy:
            return "sop inside a TLP"
        if eop and (sop or self.beat < self.hdr_end):
            return "eop before the last header DW"
        return None

    @property
    def hdr_end(self) -> int:
        return 3 if self.hdr4 else 2

    def pos(self, take: bool, sop: bool) -> Pos:
        s = take and sop
        m = take and not sop and self.busy
        en = int(s) | (int(m and self.beat == 1) << 1) | (int(m and self.beat == 2) << 2)
        return Pos(sop=s, mid=m, hdr_last=m and self.beat == self.hdr_end, en_dw=en,
                   busy=self.busy, hdr4=self.hdr4, beat=self.beat, hdr_end=self.hdr_end)

    def edge(self, take: bool, sop: bool, eop: bool, hdr4: bool) -> None:
        if take and sop:
            self.busy = True
            self.beat = 1
            self.hdr4 = hdr4
        elif take and self.busy:
            self.beat = min(self.beat + 1, BEAT_MAX)
        if take and eop:
            self.busy = False
