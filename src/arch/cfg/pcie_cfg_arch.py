"""Architecture model of pcie_cfg, the configuration space
(wiki/src/arch/cfg/HAS.md, REGISTERS.md, CAPS.md).

For definition and performance analysis, not verification
(wiki/bkm/arch.md). A transaction-level model of the tl-cfg interface
(wiki/src/arch/core/TL_CFG.md): one call per register access, error event,
interrupt request or message hand-over, with no cycle timing.

- ``Cfg`` holds the Type 0 header, the capability list, the MSI-X table and
  pending bits, the error status and the message offer, and drives the
  register state outputs (``bus_num``, ``bar0_base`` and the rest).
- ``enumerate_function`` is a host-style enumeration: IDs, capability walk,
  BAR sizing and assignment, enables, MSI and MSI-X set-up, one interrupt
  each way. ``perf`` runs it over random parameters, and runs a random
  interrupt and error stress that counts lost interrupts, writes sent while
  blocked and messages sent outside D0.

Python standard library only.
"""

import random
from dataclasses import dataclass

# ---------------------------------------------------------------- layout

CFG_DWS = 1024                 # 4 KiB configuration space, 1024 DWs
CAP_PM = 0x40                  # capability offsets (REGISTERS.md, Map)
CAP_MSI = 0x50
CAP_MSIX = 0x60
CAP_PCIE = 0x70
CAP_IDS = ((CAP_PM, 0x01), (CAP_MSI, 0x05), (CAP_MSIX, 0x11), (CAP_PCIE, 0x10))
BAR2_BYTES = 4096
PBA_OFFSET = 0x800             # pending-bit array offset in BAR2

# Command register bits (DW 0x04, [15:0]) [cite: PCIe Base Spec, rev 3.0, §7.5.1.1]
CMD_MSE, CMD_BME, CMD_PER, CMD_SERR, CMD_INTDIS = 1 << 1, 1 << 2, 1 << 6, 1 << 8, 1 << 10
# Status register bits, as bits of DW 0x04 [cite: PCIe Base Spec, rev 3.0, §7.5.1.2]
STS_CAPLIST = 1 << 20
STS_MDPE, STS_RTA, STS_RMA, STS_SSE, STS_DPE = 1 << 24, 1 << 28, 1 << 29, 1 << 30, 1 << 31
# Device Control bits, DW 0x78 [15:0] [cite: PCIe Base Spec, rev 3.0, §7.8.4]
DC_CERE, DC_NFERE, DC_FERE, DC_URRE = 1, 2, 4, 8
# Device Status bits, as bits of DW 0x78 [cite: PCIe Base Spec, rev 3.0, §7.8.5]
DS_CED, DS_NFED, DS_FED, DS_URD = 1 << 16, 1 << 17, 1 << 18, 1 << 19

MSG_IRQ, MSG_ERR_COR, MSG_ERR_NONFATAL, MSG_ERR_FATAL = 0, 1, 2, 3
D0, D3HOT = 0, 3

# Error events: (Status bits, Device Status bits, severity, is UR, advisory).
# Severity: "cor", "nf", "fatal" or None (no Device Status error, no message).
# HAS Errors table; default severities [cite: PCIe Base Spec, rev 3.0, §6.2.7].
TL_EVENTS = (
    (0, DS_URD, "nf", True, False),             # 0 UR, posted
    (0, DS_URD, "nf", True, True),              # 1 UR, non-posted (advisory)
    (0, 0, "fatal", False, False),              # 2 malformed TLP
    (STS_DPE, 0, "nf", False, False),           # 3 poisoned request
    (STS_DPE, 0, "nf", False, False),           # 4 poisoned completion (+MDPE if PER)
    (0, 0, "nf", False, True),                  # 5 unexpected completion (advisory)
    (0, 0, "nf", False, False),                 # 6 completion timeout
    (STS_RMA, 0, None, False, False),           # 7 received UR completion
    (STS_RTA, 0, None, False, False),           # 8 received CA completion
)
DL_EVENTS = ("cor", "cor", "cor", "cor", "fatal", "fatal", "fatal")
RCV_EVENT = "cor"


@dataclass(frozen=True)
class Params:
    """The cfg parameters (HAS, Parameters)."""
    bar0_addr_w: int = 12
    msix_table_size: int = 8
    vendor_id: int = 0x0000
    device_id: int = 0x0000
    subsys_vendor_id: int = 0x0000
    subsys_id: int = 0x0000
    class_code: int = 0xFF0000
    revision_id: int = 0x00

    def __post_init__(self):
        if not 12 <= self.bar0_addr_w <= 28:
            raise ValueError("bar0_addr_w must be 12 to 28")
        if not 1 <= self.msix_table_size <= 64:
            raise ValueError("msix_table_size must be 1 to 64")
        for name in ("vendor_id", "device_id", "subsys_vendor_id", "subsys_id"):
            v = getattr(self, name)
            if not 0 <= v < 0xFFFF:
                raise ValueError(f"{name} must be 16 bits and not 0xFFFF")
        if not 0 <= self.class_code < 1 << 24 or not 0 <= self.revision_id < 1 << 8:
            raise ValueError("class_code is 24 bits, revision_id 8 bits")


@dataclass(frozen=True)
class Msg:
    """One offer on the message channel; ``src`` says what taking it clears."""
    kind: int
    addr: int = 0
    data: int = 0
    src: tuple = ()


def byte_mask(be: int) -> int:
    return sum(0xFF << 8 * i for i in range(4) if be >> i & 1)


class Cfg:
    """The configuration space behind the tl-cfg interface."""

    def __init__(self, params: Params | None = None):
        self.p = params or Params()
        self._layout()
        self.reset()

    # ------------------------------------------------------------ registers

    def _layout(self) -> None:
        """Per implemented DW: (reset value, RW mask, RW1C mask). Bits in
        neither mask are read-only at their reset value (REGISTERS.md)."""
        p = self.p
        n = p.msix_table_size
        regs = {
            0x00: (p.vendor_id | p.device_id << 16, 0, 0),
            0x04: (STS_CAPLIST, CMD_MSE | CMD_BME | CMD_PER | CMD_SERR | CMD_INTDIS,
                   STS_MDPE | STS_RTA | STS_RMA | STS_SSE | STS_DPE),
            0x08: (p.revision_id | p.class_code << 8, 0, 0),
            0x0C: (0, 0xFF, 0),                       # Cache Line Size RW, header type 0
            0x10: (0, ~((1 << p.bar0_addr_w) - 1) & 0xFFFFFFFF, 0),
            0x18: (0, ~(BAR2_BYTES - 1) & 0xFFFFFFFF, 0),
            0x2C: (p.subsys_vendor_id | p.subsys_id << 16, 0, 0),
            0x34: (CAP_PM, 0, 0),
            0x3C: (0, 0xFF, 0),                       # Interrupt Line RW, Pin 0
            # Power Management: version 3, D0 and D3hot only, no PME.
            CAP_PM: (0x01 | CAP_MSI << 8 | 0x0003 << 16, 0, 0),
            CAP_PM + 4: (1 << 3, 0x3, 0),             # PowerState RW, No_Soft_Reset 1
            # MSI: 1 vector, 64-bit address, no per-vector masking.
            CAP_MSI: (0x05 | CAP_MSIX << 8 | 0x0080 << 16, 0x0071 << 16, 0),
            CAP_MSI + 4: (0, 0xFFFFFFFC, 0),
            CAP_MSI + 8: (0, 0xFFFFFFFF, 0),
            CAP_MSI + 12: (0, 0xFFFF, 0),
            # MSI-X: table size N - 1; table at BAR2 + 0, PBA at BAR2 + 0x800.
            CAP_MSIX: (0x11 | CAP_PCIE << 8 | (n - 1) << 16, 0xC000 << 16, 0),
            CAP_MSIX + 4: (0x00000002, 0, 0),
            CAP_MSIX + 8: (PBA_OFFSET | 2, 0, 0),
            # PCI Express capability, version 2, Endpoint.
            CAP_PCIE: (0x10 | 0x0002 << 16, 0, 0),
            CAP_PCIE + 0x04: (1 << 15, 0, 0),        # Device Capabilities: role-based errors
            CAP_PCIE + 0x08: (0x2810, 0x78FF, DS_CED | DS_NFED | DS_FED | DS_URD),
            CAP_PCIE + 0x0C: (0x00400011, 0, 0),      # Link Capabilities: 2.5 GT/s x1
            CAP_PCIE + 0x10: (0x0011 << 16, 0x00C3, 0),
            CAP_PCIE + 0x24: (1 << 4, 0, 0),          # Device Capabilities 2: CT Disable Supported
            CAP_PCIE + 0x28: (0, 1 << 4, 0),          # Device Control 2: CT Disable RW
            CAP_PCIE + 0x2C: (0x00000002, 0, 0),      # Link Capabilities 2: 2.5 GT/s
            CAP_PCIE + 0x30: (0x00000001, 0, 0),      # Link Control 2: target 2.5 GT/s
        }
        self._spec = {a >> 2: v for a, v in regs.items()}

    def reset(self) -> None:
        """rst_i, or dl_up_o low: every register, the table masks, the
        pending bits and every offer to its reset value."""
        self.r = [0] * CFG_DWS
        for dw, (rst, _, _) in self._spec.items():
            self.r[dw] = rst
        self.bus_num = 0
        self.dev_num = 0
        # Table entries: address and data sit in a RAM that reset leaves
        # alone (undefined at power-on: the model starts them at 0 once, at
        # construction); only the mask bits reset, to 1.
        if not hasattr(self, "table"):
            self.table = [[0, 0, 0, 1] for _ in range(self.p.msix_table_size)]
        for e in self.table:
            e[3] = 1
        self.pba = 0
        self.msi_pending = False
        self.np_pending = False   # tl_np_pending_i
        self.err_due = {MSG_ERR_COR: False, MSG_ERR_NONFATAL: False, MSG_ERR_FATAL: False}
        self.app = None           # the application request offered, a Msg
        self.irq_busy = False     # irq_ready_o is 0
        self.irq_status = None    # the last irq_done_o status
        self._offer = None

    def _reg(self, addr: int) -> int:
        return self.r[addr >> 2]

    # register state outputs (TL_CFG, Register state)
    @property
    def mem_space_en(self) -> bool:
        return bool(self._reg(0x04) & CMD_MSE)

    @property
    def bus_master_en(self) -> bool:
        return bool(self._reg(0x04) & CMD_BME)

    @property
    def d_state(self) -> int:
        return self._reg(CAP_PM + 4) & 3

    @property
    def bar0_base(self) -> int:
        return self._reg(0x10)

    @property
    def cpl_timeout_dis(self) -> bool:
        return bool(self._reg(CAP_PCIE + 0x28) >> 4 & 1)

    @property
    def bar2_base(self) -> int:
        return self._reg(0x18)

    @property
    def max_payload(self) -> int:
        return self._reg(CAP_PCIE + 8) >> 5 & 7

    @property
    def max_read_req(self) -> int:
        return self._reg(CAP_PCIE + 8) >> 12 & 7

    @property
    def msi_en(self) -> bool:
        return bool(self._reg(CAP_MSI) >> 16 & 1)

    @property
    def msix_en(self) -> bool:
        return bool(self._reg(CAP_MSIX) >> 31 & 1)

    @property
    def msix_fmask(self) -> bool:
        return bool(self._reg(CAP_MSIX) >> 30 & 1)

    @property
    def allowed(self) -> bool:
        """An interrupt may be sent: bus mastering on and D0 (Q11)."""
        return self.bus_master_en and self.d_state == D0

    # ------------------------------------------------------------ access

    def cfg_read(self, addr: int) -> int:
        """Configuration read of the DW at byte ``addr``: the whole DW,
        unimplemented DWs 0. Reads have no side effects."""
        self._check_addr(addr, CFG_DWS * 4)
        if addr == CAP_PCIE + 8:
            return self.r[addr >> 2] | int(self.np_pending) << 21   # Transactions Pending
        return self.r[addr >> 2]

    def cfg_write(self, addr: int, data: int, be: int = 0xF, bus: int = 0, dev: int = 0) -> None:
        """Configuration write: captures the bus and device number, then
        writes the enabled bytes of the RW bits and clears the RW1C bits
        written 1. Unimplemented DWs and read-only bits ignore it."""
        self._check_addr(addr, CFG_DWS * 4)
        self.bus_num, self.dev_num = bus & 0xFF, dev & 0x1F
        dw = addr >> 2
        if dw in self._spec:
            _, rw, w1c = self._spec[dw]
            m = byte_mask(be)
            if addr == CAP_PM + 4 and data & 3 in (1, 2):
                rw &= ~3                              # D1, D2: discarded
            v = self.r[dw]
            v = (v & ~(rw & m)) | (data & rw & m)
            v &= ~(data & w1c & m)
            self.r[dw] = v & 0xFFFFFFFF
            if not self.msi_en:
                self.msi_pending = False
        self._settle()

    def bar2_read(self, offset: int) -> int:
        """BAR2 DW read: table entries at 16 bytes each, then the PBA."""
        self._check_addr(offset, BAR2_BYTES)
        dw = offset >> 2
        if dw < 4 * self.p.msix_table_size:
            return self.table[dw >> 2][dw & 3]
        if PBA_OFFSET >> 2 <= dw < (PBA_OFFSET >> 2) + 2:
            return self.pba >> 32 * (dw - (PBA_OFFSET >> 2)) & 0xFFFFFFFF
        return 0

    def bar2_write(self, offset: int, data: int, be: int = 0xF) -> None:
        """BAR2 DW write: the table's RW bits in enabled bytes; the PBA and
        the rest of BAR2 ignore writes."""
        self._check_addr(offset, BAR2_BYTES)
        dw = offset >> 2
        if dw < 4 * self.p.msix_table_size:
            rw = (0xFFFFFFFC, 0xFFFFFFFF, 0xFFFFFFFF, 0x1)[dw & 3]
            m = byte_mask(be) & rw
            e = self.table[dw >> 2]
            e[dw & 3] = (e[dw & 3] & ~m) | (data & m)
        self._settle()

    @staticmethod
    def _check_addr(addr: int, size: int) -> None:
        if addr % 4 or not 0 <= addr < size:
            raise ValueError(f"DW address 0x{addr:x} out of range")

    # ------------------------------------------------------------ errors

    def error(self, tl: int = 0, dl: int = 0, rcv: int = 0) -> None:
        """One cycle of error pulses: tl_err (9 bits), dl_err (7), rcv_err."""
        for bit, (sts, ds, sev, ur, adv) in enumerate(TL_EVENTS):
            if tl >> bit & 1:
                if bit == 4 and self._reg(0x04) & CMD_PER:
                    sts |= STS_MDPE
                self.r[1] |= sts
                self.r[(CAP_PCIE + 8) >> 2] |= ds
                if sev:
                    self._log(sev, ur, adv)
        for bit, sev in enumerate(DL_EVENTS):
            if dl >> bit & 1:
                self._log(sev, False, False)
        if rcv:
            self._log(RCV_EVENT, False, False)
        self._settle()

    def _log(self, sev: str, ur: bool, advisory: bool) -> None:
        """Device Status bit, then the message decision (HAS, Errors)."""
        if advisory:
            sev = "cor"
        dctl = self._reg(CAP_PCIE + 8)
        serr = bool(self._reg(0x04) & CMD_SERR)
        self.r[(CAP_PCIE + 8) >> 2] |= {"cor": DS_CED, "nf": DS_NFED, "fatal": DS_FED}[sev]
        if ur and not dctl & DC_URRE:
            return
        if sev == "cor":
            kind, on = MSG_ERR_COR, bool(dctl & DC_CERE)
        elif sev == "nf":
            kind, on = MSG_ERR_NONFATAL, bool(dctl & DC_NFERE) or serr
        else:
            kind, on = MSG_ERR_FATAL, bool(dctl & DC_FERE) or serr
        if on and self.d_state == D0:
            self.err_due[kind] = True

    # ------------------------------------------------------------ interrupts

    def irq_request(self, vector: int = 0) -> str | None:
        """The application's interrupt request (APP, Interrupts). Returns
        the irq_done_o status when it resolves at once ("pending" or
        "dropped"); None while its write is offered ("sent" on the take)."""
        if self.irq_busy:
            raise RuntimeError("irq request while irq_ready_o is 0")
        self.irq_status = None
        if self.msix_en:
            if not 0 <= vector < self.p.msix_table_size:
                raise ValueError("vector at or above MSIX_TABLE_SIZE")
            bit = 1 << vector
            if self.pba & bit or not self._msix_open(vector):
                self.pba |= bit
                return self._done("pending")
            e = self.table[vector]
            self.app = Msg(MSG_IRQ, e[0] | e[1] << 32, e[2], ("app", "msix", vector))
        elif self.msi_en:
            if self.msi_pending or not self.allowed:
                self.msi_pending = True
                return self._done("pending")
            self.app = Msg(MSG_IRQ, *self._msi_msg(), ("app", "msi", vector))
        else:
            return self._done("dropped")
        self.irq_busy = True
        self._settle()
        return self.irq_status

    def _done(self, status: str) -> str:
        self.irq_status, self.irq_busy, self.app = status, False, None
        return status

    def _msix_open(self, vector: int) -> bool:
        """An MSI-X write for this vector may go out now."""
        return (self.msix_en and not self.msix_fmask and not self.table[vector][3] & 1
                and self.allowed)

    def _msi_open(self) -> bool:
        return self.msi_en and not self.msix_en and self.allowed

    def _msi_msg(self) -> tuple[int, int]:
        lo, hi = self._reg(CAP_MSI + 4), self._reg(CAP_MSI + 8)
        return lo | hi << 32, self._reg(CAP_MSI + 12) & 0xFFFF

    # ------------------------------------------------------------ messages

    def _sendable(self, m: Msg) -> bool:
        if self.d_state != D0:
            return False
        if m.src[0] == "err":
            return True
        if m.src[1] == "msix":
            return self._msix_open(m.src[2])
        return self._msi_open()

    def _kept(self, m: Msg) -> bool:
        """An offer stays up unless the withdrawal rule removes it: outside
        D0, Bus Master Enable cleared, or its own mode's enable cleared
        (TL_CFG, Withdrawal). A mask does not withdraw it."""
        if self.d_state != D0:
            return False
        if m.src[0] == "err":
            return True
        return self.bus_master_en and (self.msix_en if m.src[1] == "msix" else self.msi_en)

    def _settle(self) -> None:
        """Withdraw an offer that may no longer go out, and turn a waiting
        application request pending when it may not go out (HAS, Messages)."""
        if self.d_state != D0:
            for k in self.err_due:
                self.err_due[k] = False
        if self._offer is not None and not self._kept(self._offer):
            self._offer = None
        if self.app is not None and self._offer is not self.app and not self._sendable(self.app):
            mode, vector = self.app.src[1], self.app.src[2]
            if mode == "msi" and self.msi_en:
                self.msi_pending = True
                self._done("pending")
            elif mode == "msix" or self.msix_en:
                if not 0 <= vector < self.p.msix_table_size:
                    raise ValueError("vector at or above MSIX_TABLE_SIZE")
                self.pba |= 1 << vector
                self._done("pending")
            else:
                self._done("dropped")

    def offer(self) -> Msg | None:
        """msg_valid and its fields: the held offer, else the next one by
        priority: ERR_FATAL, ERR_NONFATAL, ERR_COR, the application's write,
        the MSI pending write, the lowest pending MSI-X vector."""
        if self._offer is not None:
            return self._offer
        if self.d_state != D0:
            return None
        for kind in (MSG_ERR_FATAL, MSG_ERR_NONFATAL, MSG_ERR_COR):
            if self.err_due[kind]:
                self._offer = Msg(kind, src=("err", kind))
                return self._offer
        if self.app is not None:
            self._offer = self.app
        elif self.msi_pending and self._msi_open():
            self._offer = Msg(MSG_IRQ, *self._msi_msg(), ("pend", "msi", 0))
        else:
            for v in range(self.p.msix_table_size):
                if self.pba >> v & 1 and self._msix_open(v):
                    e = self.table[v]
                    self._offer = Msg(MSG_IRQ, e[0] | e[1] << 32, e[2], ("pend", "msix", v))
                    break
        return self._offer

    def take(self) -> Msg:
        """msg_ready with msg_valid: tl takes the offer."""
        m = self.offer()
        if m is None:
            raise RuntimeError("take with no offer")
        self._offer = None
        if m.src[0] == "err":
            self.err_due[m.kind] = False
            if m.kind != MSG_ERR_COR and self._reg(0x04) & CMD_SERR:
                self.r[1] |= STS_SSE
        elif m.src[0] == "app":
            self._done("sent")
        elif m.src[1] == "msi":
            self.msi_pending = False
        else:
            self.pba &= ~(1 << m.src[2])
        return m


# ---------------------------------------------------------------- host

MSI_ADDR = 0xFEE0_0000


def find_caps(c: Cfg) -> dict[int, int]:
    """Walk the capability list: {capability ID: offset}."""
    caps, ptr, seen = {}, c.cfg_read(0x34) & 0xFC, 0
    while ptr and seen < 48:
        hdr = c.cfg_read(ptr)
        caps[hdr & 0xFF] = ptr
        ptr, seen = hdr >> 8 & 0xFC, seen + 1
    return caps


def size_bar(c: Cfg, addr: int, bus: int = 0, dev: int = 0) -> int:
    """Write all ones, read the mask back, restore: the BAR size in bytes,
    0 for an unimplemented BAR."""
    old = c.cfg_read(addr)
    c.cfg_write(addr, 0xFFFFFFFF, 0xF, bus, dev)
    mask = c.cfg_read(addr) & 0xFFFFFFF0
    c.cfg_write(addr, old, 0xF, bus, dev)
    return (~mask + 1) & 0xFFFFFFFF if mask else 0


def enumerate_function(c: Cfg, rng: random.Random, bus: int = 1, dev: int = 0) -> list[str]:
    """A host enumeration, as goal item 4 of the M3 record lists it. Returns
    the mismatches against the parameters and the HAS (empty when clean)."""
    p, bad = c.p, []

    def wr(addr, data, be=0xF):
        c.cfg_write(addr, data, be, bus, dev)

    def expect(what, got, want):
        if got != want:
            bad.append(f"{what}: got 0x{got:x} want 0x{want:x}")

    expect("ids", c.cfg_read(0x00), p.vendor_id | p.device_id << 16)
    expect("class", c.cfg_read(0x08), p.revision_id | p.class_code << 8)
    expect("header type", c.cfg_read(0x0C) >> 16 & 0xFF, 0)
    expect("cap list bit", c.cfg_read(0x04) >> 20 & 1, 1)
    caps = find_caps(c)
    expect("capabilities", len(caps), 4)
    for off, cid in CAP_IDS:
        expect(f"cap 0x{cid:02x}", caps.get(cid, 0), off)
    expect("extended header", c.cfg_read(0x100), 0)
    sizes = [size_bar(c, 0x10 + 4 * i, bus, dev) for i in range(6)]
    expect("bar0 size", sizes[0], 1 << p.bar0_addr_w)
    expect("bar2 size", sizes[2], BAR2_BYTES)
    for i in (1, 3, 4, 5):
        expect(f"bar{i} size", sizes[i], 0)
    base0 = rng.randrange(1, 1 << (32 - p.bar0_addr_w)) << p.bar0_addr_w
    base2 = rng.randrange(1, 1 << 20) << 12
    wr(0x10, base0)
    wr(0x18, base2)
    wr(0x04, CMD_MSE | CMD_BME | CMD_SERR, be=0x3)
    expect("bar0_base", c.bar0_base, base0)
    expect("bar2_base", c.bar2_base, base2)
    expect("bus/dev", c.bus_num << 5 | c.dev_num, bus << 5 | dev)
    expect("enables", int(c.mem_space_en) << 1 | int(c.bus_master_en), 3)
    # MSI: address, data, enable; one interrupt.
    msi = caps.get(0x05, CAP_MSI)
    expect("msi 64-bit", c.cfg_read(msi) >> 23 & 1, 1)
    hi = rng.choice((0, rng.randrange(1, 1 << 32)))
    data = rng.randrange(1 << 16)
    wr(msi + 4, MSI_ADDR)
    wr(msi + 8, hi)
    wr(msi + 12, data)
    wr(msi, 1 << 16, be=0x4)
    c.irq_request(0)
    m = c.offer()
    want = (MSG_IRQ, MSI_ADDR | hi << 32, data)
    if m is None or (m.kind, m.addr, m.data) != want:
        bad.append(f"msi write: got {m} want {want}")
    else:
        c.take()
    expect("msi status", int(c.irq_status == "sent"), 1)
    wr(msi, 0, be=0x4)
    # MSI-X: table location, one vector programmed and unmasked, function
    # mask during set-up; the interrupt pends, then goes on the unmask.
    mx = caps.get(0x11, CAP_MSIX)
    expect("msix size", (c.cfg_read(mx) >> 16 & 0x7FF) + 1, p.msix_table_size)
    expect("msix table", c.cfg_read(mx + 4), 0x2)
    expect("msix pba", c.cfg_read(mx + 8), PBA_OFFSET | 2)
    v = rng.randrange(p.msix_table_size)
    xdata = rng.randrange(1 << 32)
    wr(mx, 0xC000 << 16, be=0x8)            # enable, function masked
    c.bar2_write(16 * v, MSI_ADDR + 4 * v)
    c.bar2_write(16 * v + 4, 0)
    c.bar2_write(16 * v + 8, xdata)
    c.bar2_write(16 * v + 12, 0)
    expect("msix pending status", int(c.irq_request(v) == "pending"), 1)
    expect("pba bit", c.bar2_read(PBA_OFFSET + 4 * (v // 32)) >> v % 32 & 1, 1)
    wr(mx, 0x8000 << 16, be=0x8)            # clear the function mask
    m = c.offer()
    want = (MSG_IRQ, MSI_ADDR + 4 * v, xdata)
    if m is None or (m.kind, m.addr, m.data) != want:
        bad.append(f"msix pending write: got {m} want {want}")
    else:
        c.take()
    expect("pba cleared", c.bar2_read(PBA_OFFSET + 4 * (v // 32)), 0)
    c.irq_request(v)
    m = c.offer()
    if m is None or (m.kind, m.addr, m.data) != want:
        bad.append(f"msix write: got {m} want {want}")
    else:
        c.take()
    expect("msix status", int(c.irq_status == "sent"), 1)
    return bad


# ---------------------------------------------------------------- perf

def _random_params(rng: random.Random) -> Params:
    return Params(bar0_addr_w=rng.randint(12, 28), msix_table_size=rng.randint(1, 64),
                  vendor_id=rng.randrange(0xFFFF), device_id=rng.randrange(0xFFFF),
                  subsys_vendor_id=rng.randrange(0xFFFF), subsys_id=rng.randrange(0xFFFF),
                  class_code=rng.randrange(1 << 24), revision_id=rng.randrange(256))


def _perf_enumeration(w: dict, rng: random.Random) -> dict:
    trials, errors = int(w["trials"]), 0
    for _ in range(trials):
        errors += len(enumerate_function(Cfg(_random_params(rng)), rng,
                                         rng.randrange(256), rng.randrange(32)))
    return {"enumeration_errors": errors, "trials": trials}


def _perf_stress(w: dict, rng: random.Random) -> dict:
    """Random interrupt requests, masks, enables, D-state changes, error
    events, and message offers taken at once or left waiting across the
    other operations; then a drain with everything allowed. A request is
    lost when it is reported pending with no pending bit or flag holding
    it, or when it is still owed a write after the drain."""
    c = Cfg(Params(msix_table_size=int(w.get("vectors", 8))))
    n = c.p.msix_table_size
    for v in range(n):                                 # one address per vector
        c.bar2_write(16 * v, MSI_ADDR + 4 * v)
        c.bar2_write(16 * v + 8, v)
    c.cfg_write(CAP_MSI + 4, MSI_ADDR + 0x100)
    c.cfg_write(CAP_MSI + 12, 0xAB)
    c.cfg_write(CAP_PCIE + 8, DC_CERE | DC_NFERE | DC_FERE | DC_URRE, be=0x1)
    owed: dict[object, int] = {}                     # key -> requests owed a write
    stats = {"requests": 0, "writes": 0, "writes_while_blocked": 0,
             "messages_outside_d0": 0, "error_messages": 0}
    unheld = 0                                       # pending reports with nothing holding them
    open_vector = None                               # vector of the request not yet resolved

    def key_of(m: Msg):
        return "msi" if m.addr == MSI_ADDR + 0x100 else (m.addr - MSI_ADDR) // 4

    def take():
        m = c.offer()
        if m is None:
            return
        if c.d_state != D0:
            stats["messages_outside_d0"] += 1
        if m.kind == MSG_IRQ:
            k = key_of(m)
            blocked = not c.allowed or not (c.msi_en if k == "msi" else c.msix_en)
            stats["writes_while_blocked"] += int(blocked)
            stats["writes"] += 1
            owed[k] = 0
        else:
            stats["error_messages"] += 1
        c.take()

    for _ in range(int(w["ops"])):
        op = rng.random()
        if op < 0.30:
            if not c.irq_busy:
                v = rng.randrange(n)
                key = v if c.msix_en else "msi"
                status = c.irq_request(v)
                stats["requests"] += 1
                open_vector = v
                if status != "dropped":
                    owed[key] = owed.get(key, 0) + 1
        elif op < 0.48:
            take()
        elif op < 0.55:
            c.offer()                                  # msg_valid seen, msg_ready 0
        elif op < 0.65:
            v = rng.randrange(n)
            c.bar2_write(16 * v + 12, int(rng.random() < 0.3))
        elif op < 0.72:
            en, fmask = rng.random() < 0.7, rng.random() < 0.2
            c.cfg_write(CAP_MSIX, (en << 1 | fmask) << 30, be=0x8)
        elif op < 0.78:
            c.cfg_write(CAP_MSI, rng.getrandbits(1) << 16, be=0x4)
            if not c.msi_en:
                owed.pop("msi", None)                  # software discards MSI pending
        elif op < 0.86:
            bme = CMD_BME if rng.random() < 0.8 else 0
            c.cfg_write(0x04, CMD_MSE | CMD_SERR | bme, be=0x3)
        elif op < 0.93:
            c.cfg_write(CAP_PM + 4, rng.choice((0, 0, 0, 3, 1)), be=0x1)
        elif op < 0.96:
            c.error(tl=rng.getrandbits(9), dl=rng.getrandbits(7), rcv=rng.getrandbits(1))
        if open_vector is not None and c.irq_status is not None:
            # A request reported pending must be held by a pending bit or
            # the MSI pending flag, as the host would later see it sent.
            if c.irq_status == "pending" and not (c.pba >> open_vector & 1 or c.msi_pending):
                unheld += 1
            open_vector = None
    # Drain: D0, bus mastering, MSI alone, then MSI-X unmasked.
    c.cfg_write(CAP_PM + 4, 0, be=0x1)
    c.cfg_write(0x04, CMD_MSE | CMD_BME, be=0x3)
    for _ in range(4):
        take()
    if c.msi_en:
        c.cfg_write(CAP_MSIX, 0, be=0x8)
        for _ in range(4):
            take()
    lost_msi = int(c.msi_en and owed.get("msi", 0) > 0)   # MSI on, write never sent
    c.cfg_write(CAP_MSI, 0, be=0x4)
    owed.pop("msi", None)
    c.cfg_write(CAP_MSIX, 0x8000 << 16, be=0x8)
    for v in range(n):
        c.bar2_write(16 * v + 12, 0)
    for _ in range(n + 4):
        take()
    stats["interrupts_lost"] = unheld + lost_msi + sum(1 for k, cnt in owed.items() if cnt)
    return stats


def perf(workload: dict, seed: int) -> dict:
    """Workloads: {"kind": "enumeration", "trials": N} or
    {"kind": "stress", "ops": N, "vectors": V}."""
    rng = random.Random(seed)
    kind = workload.get("kind")
    if kind == "enumeration":
        return _perf_enumeration(workload, rng)
    if kind == "stress":
        return _perf_stress(workload, rng)
    raise ValueError(f"unknown workload kind {kind!r}")
