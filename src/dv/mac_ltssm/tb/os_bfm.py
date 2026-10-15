"""Ordered-set and frame layer for the pcie_mac_ltssm testbench
(wiki/src/dv/mac_ltssm/DV_PLAN.md, Strategy).

The LTSSM requests ordered sets from mac_os, hands the line to mac_frame
for idle symbols, and reads both through events
(wiki/src/rtl/mac_ltssm/MAS.md, Sibling interfaces). Until those blocks are
integrated, this class plays them at the PIPE level: it turns the LTSSM's
outputs into a ``PipeTx`` word and the transmit events of that word, and a
received ``PipeRx`` word into the receive events. It restates the
architecture model's ordered-set layer (``pcie_mac_arch.Mac._tx_word``,
``_refill``, ``_queue_ts``, ``_rx_sym``) with the model's own scrambler,
symbols and constants, so the symbols on the wire and the event timing are
the model's.
"""

from __future__ import annotations

from collections import deque

# What the LTSSM's outputs ask for.
TX_NONE, TX_TS, TX_IDLE, TX_COMPLIANCE = 0, 1, 2, 3

PAD_BYTE = 0xF7
TS_IDS = {0x4A: (False, False), 0x45: (True, False),      # (ts2, inverted)
          0x4A ^ 0xFF: (False, True), 0x45 ^ 0xFF: (True, True)}


class OsBfm:
    """One side's ordered-set block: transmit from the LTSSM's outputs,
    receive events for the LTSSM's inputs."""

    def __init__(self, arch, n_fts: int = 32, skp_interval: int = 1180) -> None:
        self.arch = arch
        self.n_fts = n_fts
        self.skp_interval = skp_interval
        self.reset()

    def reset(self) -> None:
        a = self.arch
        self.txq: deque = deque()
        self.tx_scr = a.Scrambler()
        self.rx_scr = a.Scrambler()
        self.skp_credit = 0
        self.compliance_phase = 0
        self._p = 0
        self._ts: list = []

    # ------------------------------------------------------------ transmit

    def tx(self, o: dict):
        """``o``: the LTSSM outputs of this cycle. Returns the PIPE word
        (``PipeTx``) and its transmit events: ``ack`` (a requested set
        starts), ``valid`` and ``last`` (an ordered-set word, the last of its
        set) and ``idle`` (two idle symbols from the frame)."""
        a = self.arch
        ev = {"ack": 0, "valid": 0, "last": 0, "idle": 0}
        if not o["reset_n"]:
            self.reset()
            return a.PipeTx(reset_n=False), ev
        if o["elec_idle"]:
            self.txq.clear()
            return (a.PipeTx(elec_idle=True, detect_rx=bool(o["detect_rx"]),
                             polarity=bool(o["polarity"]), power_down=o["pd"]), ev)
        if o["ts_req"]:
            mode = TX_TS
        elif o["compliance_req"]:
            mode = TX_COMPLIANCE
        elif o["tx_l0"]:
            mode = TX_IDLE
        else:
            mode = TX_NONE
        while len(self.txq) < 2:
            self._refill(mode, o)
        x, y = self.txq.popleft(), self.txq.popleft()
        scr = self.tx_scr
        s0 = (x[0], scr.feed(x[0], x[1], x[2]))
        s1 = (y[0], scr.feed(y[0], y[1], y[2]))
        compliance = (mode == TX_COMPLIANCE and (x[0], x[1]) == (1, 0xBC)
                      and self.compliance_phase == 1)
        if mode != TX_COMPLIANCE:
            self.skp_credit += 2
        tags = x[3] + y[3]
        ev["ack"] = int("A" in tags)
        ev["valid"] = int("O" in tags)
        ev["last"] = int("L" in tags)
        ev["idle"] = int("I" in tags)
        return (a.PipeTx(syms=(s0, s1), elec_idle=False, detect_rx=bool(o["detect_rx"]),
                         compliance=compliance, polarity=bool(o["polarity"]),
                         power_down=o["pd"]), ev)

    def _set(self, syms, requested: bool) -> None:
        """Queue one ordered set; tags: O ordered-set symbol, A the first of
        a requested set, L the last."""
        n = len(syms)
        for i, (k, b) in enumerate(syms):
            tag = "O" + ("A" if requested and i == 0 else "") + ("L" if i == n - 1 else "")
            self.txq.append((k, b, False, tag))

    def _refill(self, mode: int, o: dict) -> None:
        if mode == TX_COMPLIANCE:
            self.compliance_phase = 1
            self._set(((1, 0xBC), (0, 0xB5), (1, 0xBC), (0, 0x4A)), True)
            return
        if not self.txq and self.skp_credit >= self.skp_interval:
            self._set(((1, 0xBC), (1, 0x1C), (1, 0x1C), (1, 0x1C)), False)
            self.skp_credit -= self.skp_interval
            return
        if mode == TX_TS:
            link = (1, PAD_BYTE) if o["link_pad"] else (0, o["link"])
            lane = (1, PAD_BYTE) if o["lane_pad"] else (0, o["lane"])
            ident = 0x45 if o["ts2"] else 0x4A
            self._set(((1, 0xBC), link, lane, (0, self.n_fts), (0, 0x02), (0, o["ctl"]))
                      + ((0, ident),) * 10, True)
            return
        if mode == TX_IDLE:
            self.txq.extend(((0, 0, True, "I"), (0, 0, True, "I")))
            return
        # Nothing requested with the transmitter active: only a set already
        # started may finish, and then the queue is never this short.
        raise ValueError("transmitter active with nothing requested and nothing to finish")

    # ------------------------------------------------------------- receive

    def rx(self, rx) -> dict:
        """Receive events of one ``PipeRx`` word."""
        ev = {"ts": 0, "ts_bad": 0, "ts_inv": 0, "ts2": 0, "link": 0, "link_pad": 0,
              "link_num": 0, "lane": 0, "lane_pad": 0, "lane_num": 0, "n_fts": 0,
              "ctl": 0, "skp": 0, "eios": 0, "fts": 0, "os_sym": 0, "idle": 0}
        if rx.valid and not rx.elec_idle:
            for i, (k, b) in enumerate(rx.syms):
                self._rx_sym(i, k, b, ev)
        return ev

    def _ts_event(self, ev: dict, ts) -> None:
        assert not (ev["ts"] or ev["ts_bad"]), "two TS ended in one word"
        if ts is None:
            ev["ts_bad"] = 1
            return
        ids = ts[5:]
        first = ids[0]
        if first[0] or any(s != first for s in ids) or first[1] not in TS_IDS:
            ev["ts_bad"] = 1
            return
        ts2, inv = TS_IDS[first[1]]

        def field(sym):
            return {"pad": int(sym == (1, PAD_BYTE)), "num": int(not sym[0])}

        link, lane = field(ts[0]), field(ts[1])
        ev.update(ts=1, ts_inv=int(inv), ts2=int(ts2), link=ts[0][1], link_pad=link["pad"],
                  link_num=link["num"], lane=ts[1][1], lane_pad=lane["pad"],
                  lane_num=lane["num"], n_fts=ts[2][1], ctl=ts[4][1] & 0x1F)

    def _rx_sym(self, i: int, k: int, b: int, ev: dict) -> None:
        scr = self.rx_scr
        osb = 1 << i
        if k and b == 0xBC:
            ev["os_sym"] |= osb
            if self._p == 2:
                self._ts_event(ev, None)       # a TS cut short by COM
            scr.feed(k, b, False)
            self._p = 1
            return
        p = self._p
        if p in (1, 2):
            ev["os_sym"] |= osb
        if p == 1:
            scr.feed(k, b, False)
            if k and b == 0x1C:
                self._p = 3
                ev["skp"] = 1
            elif k and b == 0x7C:
                self._p = 4
                ev["eios"] = 1
            elif k and b == 0x3C:
                self._p = 4
                ev["fts"] = 1
            else:
                self._p = 2
                self._ts = [(k, b)]
            return
        if p == 2:
            scr.feed(k, b, False)
            self._ts.append((k, b))
            if len(self._ts) == 15:
                self._p = 0
                self._ts_event(ev, self._ts)
            return
        if p == 3:
            if k and b == 0x1C:
                ev["os_sym"] |= osb
                scr.feed(k, b, False)
                return
            self._p = 0
        elif p == 4:
            if k and b in (0x7C, 0x3C):
                ev["os_sym"] |= osb
                scr.feed(k, b, False)
                return
            self._p = 0
        d = scr.feed(k, b, True)
        if not k and d == 0:
            ev["idle"] |= osb                  # the frame's idle symbol
