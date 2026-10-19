"""Cycle model of common_sdp_ram_fifo
(wiki/src/rtl/common/ram/common_sdp_ram_fifo.md#behavior).

The queue is 2**ADDR_W entries in a RAM plus a head register. At each
rising edge, in this order: the oldest RAM entry written at an earlier
edge loads into the head when the head is empty or popped; a pop without
a load empties the head; a push appends to the RAM. So a push into an
empty queue shows at the head two edges later, and a pop and a refill
share an edge.
"""

from collections import deque


class FifoModel:
    def __init__(self, addr_w: int):
        self.cap = 2 ** addr_w   # RAM entries; the head is one more
        self.ram = deque()
        self.head_valid = False
        self.head = None

    def reset(self) -> None:
        self.ram.clear()
        self.head_valid = False
        self.head = None

    @property
    def full(self) -> bool:
        return len(self.ram) == self.cap

    @property
    def empty(self) -> bool:
        return not self.head_valid and not self.ram

    @property
    def ram_used(self) -> int:
        return len(self.ram)

    def edge(self, push: bool, data: int, pop: bool) -> None:
        """One rising edge. Pushing while full or popping with no head is
        outside the contract and raises."""
        if push and self.full:
            raise ValueError("push while full")
        if pop and not self.head_valid:
            raise ValueError("pop with no head")
        if self.ram and (not self.head_valid or pop):
            self.head = self.ram.popleft()
            self.head_valid = True
        elif pop:
            self.head_valid = False
        if push:
            self.ram.append(data)
