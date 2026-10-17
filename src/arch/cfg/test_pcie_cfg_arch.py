"""Self-tests of the pcie_cfg architecture model; no simulator needed.

Run: python3 -m pytest src/arch/cfg (./build ci unit runs them as arch:<ip>).

Each test states one rule of the cfg HAS (wiki/src/arch/cfg/HAS.md,
REGISTERS.md, CAPS.md) or of the tl-cfg interface it implements.
"""

import random

import pytest

import pcie_cfg_arch as m
from pcie_cfg_arch import (CAP_MSI, CAP_MSIX, CAP_PCIE, CAP_PM, CMD_BME, CMD_MSE, CMD_PER,
                           CMD_SERR, D0, D3HOT, DC_CERE, DC_FERE, DC_NFERE, DC_URRE, DS_CED,
                           DS_FED, DS_NFED, DS_URD, MSG_ERR_COR, MSG_ERR_FATAL,
                           MSG_ERR_NONFATAL, MSG_IRQ, PBA_OFFSET, STS_DPE, STS_MDPE, STS_RMA,
                           STS_RTA, STS_SSE, Cfg, Params)

DEVCTL = CAP_PCIE + 8
ADDR = 0xFEE0_0000


def ready(c, msix=False, msi=False):
    """Bus mastering and memory on, one interrupt mode enabled."""
    c.cfg_write(0x04, CMD_MSE | CMD_BME, be=0x3)
    if msi:
        c.cfg_write(CAP_MSI + 4, ADDR)
        c.cfg_write(CAP_MSI + 12, 0x1234)
        c.cfg_write(CAP_MSI, 1 << 16, be=0x4)
    if msix:
        for v in range(c.p.msix_table_size):
            c.bar2_write(16 * v, ADDR + 4 * v)
            c.bar2_write(16 * v + 8, 0x100 + v)
            c.bar2_write(16 * v + 12, 0)
        c.cfg_write(CAP_MSIX, 0x8000 << 16, be=0x8)


# ---------------------------------------------------------------- header

def test_reset_values():
    c = Cfg()
    assert c.cfg_read(0x00) == 0x0000_0000           # placeholder IDs (F7, Q12)
    assert c.cfg_read(0x04) == 0x0010_0000           # Capabilities List only
    assert c.cfg_read(0x08) == 0xFF00_0000           # class FFh, revision 0
    assert c.cfg_read(0x34) == CAP_PM
    assert c.cfg_read(0x3C) == 0                     # Interrupt Pin 0
    assert (c.max_payload, c.max_read_req, c.d_state) == (0, 2, D0)
    assert not (c.mem_space_en or c.bus_master_en or c.bar0_base or c.bar2_base)


def test_parameters_reach_the_header():
    p = Params(vendor_id=0x1AB4, device_id=0x5678, subsys_vendor_id=0x9ABC, subsys_id=0xDEF0,
               class_code=0x058000, revision_id=0x7)
    c = Cfg(p)
    assert c.cfg_read(0x00) == 0x5678_1AB4
    assert c.cfg_read(0x08) == 0x0580_0007
    assert c.cfg_read(0x2C) == 0xDEF0_9ABC


@pytest.mark.parametrize("bad", [{"bar0_addr_w": 11}, {"bar0_addr_w": 29},
                                 {"msix_table_size": 0}, {"msix_table_size": 65},
                                 {"vendor_id": 0xFFFF}])
def test_parameter_ranges(bad):
    with pytest.raises(ValueError):
        Params(**bad)


@pytest.mark.parametrize("w", [12, 20, 28])
def test_bar_sizing(w):
    c = Cfg(Params(bar0_addr_w=w))
    assert m.size_bar(c, 0x10) == 1 << w
    assert m.size_bar(c, 0x18) == 4096
    for a in (0x14, 0x1C, 0x20, 0x24, 0x30):         # unused BARs, expansion ROM
        c.cfg_write(a, 0xFFFFFFFF)
        assert c.cfg_read(a) == 0
    c.cfg_write(0x10, 0xFFFFFFFF)
    assert c.cfg_read(0x10) == (0xFFFFFFFF << w) & 0xFFFFFFFF   # type bits 0
    assert c.bar0_base == c.cfg_read(0x10)


def test_byte_enables_and_read_only_bits():
    c = Cfg()
    c.cfg_write(0x04, 0xFFFF_FFFF, be=0x1)           # byte 0 only: MSE, BME, PER
    assert c.cfg_read(0x04) & 0xFFFF == CMD_MSE | CMD_BME | CMD_PER
    c.cfg_write(0x04, 0xFFFF_FFFF, be=0x2)           # byte 1: SERR#, Interrupt Disable
    assert c.cfg_read(0x04) & 0xFFFF == 0x0546
    c.cfg_write(0x00, 0xFFFF_FFFF)                    # IDs are read-only
    assert c.cfg_read(0x00) == 0
    c.cfg_write(0x0C, 0xFFFF_FFFF)                    # only Cache Line Size is RW
    assert c.cfg_read(0x0C) == 0xFF


def test_unimplemented_space_reads_zero():
    c = Cfg()
    for a in list(range(0x48, 0x50, 4)) + list(range(0xAC, 0x1000, 4)):
        c.cfg_write(a, 0xFFFF_FFFF)
        assert c.cfg_read(a) == 0, hex(a)
    assert c.cfg_read(0x100) == 0                     # no extended capability


def test_bus_and_device_captured_on_every_write():
    c = Cfg()
    c.cfg_read(0x00)
    assert (c.bus_num, c.dev_num) == (0, 0)
    c.cfg_write(0x200, 0, bus=5, dev=3)               # even an unimplemented DW
    assert (c.bus_num, c.dev_num) == (5, 3)


def test_rw1c_status():
    c = Cfg()
    c.error(tl=1 << 7 | 1 << 8 | 1 << 3)
    assert c.cfg_read(0x04) & (STS_RMA | STS_RTA | STS_DPE) == STS_RMA | STS_RTA | STS_DPE
    c.cfg_write(0x04, STS_RMA, be=0x8)                # clear one bit, keep the others
    assert c.cfg_read(0x04) & (STS_RMA | STS_RTA | STS_DPE) == STS_RTA | STS_DPE
    c.cfg_write(0x04, STS_RTA, be=0x4)                # byte 2 not the bit's byte: no effect
    assert c.cfg_read(0x04) & STS_RTA


def test_capability_list():
    c = Cfg()
    assert m.find_caps(c) == {0x01: CAP_PM, 0x05: CAP_MSI, 0x11: CAP_MSIX, 0x10: CAP_PCIE}
    assert c.cfg_read(CAP_PCIE) >> 16 == 0x0002      # version 2, Endpoint
    assert c.cfg_read(CAP_PCIE + 0x0C) & 0x3FF == 0x11   # 2.5 GT/s, x1
    assert c.cfg_read(CAP_PCIE + 0x10) >> 16 == 0x0011   # current 2.5 GT/s, x1


def test_device_control_and_link_control():
    c = Cfg()
    c.cfg_write(DEVCTL, 5 << 5 | 1 << 12, be=0x3)     # MPS 4096, MRRS 256
    assert (c.max_payload, c.max_read_req) == (5, 1)
    c.cfg_write(CAP_PCIE + 0x10, 0xFFFF, be=0x3)      # RCB, retrain, disable stay 0
    assert c.cfg_read(CAP_PCIE + 0x10) & 0xFFFF == 0x00C3
    c.cfg_write(CAP_PCIE + 0x30, 0xFFFF_FFFF)         # Link Control 2 hardwired
    assert c.cfg_read(CAP_PCIE + 0x30) == 1
    c.cfg_write(CAP_PCIE + 0x28, 0xFFFF_FFFF)         # Completion Timeout Value, Disable RO 0
    assert c.cfg_read(CAP_PCIE + 0x24) == 0 and c.cfg_read(CAP_PCIE + 0x28) == 0
    c.cfg_write(CAP_PCIE + 0x04, 0xFFFF_FFFF)         # slot power limit and the rest RO
    assert c.cfg_read(CAP_PCIE + 0x04) == 1 << 15


# ---------------------------------------------------------------- power

def test_power_states():
    c = Cfg()
    for s in (1, 2):                                  # D1, D2 not supported: discarded
        c.cfg_write(CAP_PM + 4, s, be=0x1)
        assert c.d_state == D0
    c.cfg_write(CAP_PM + 4, 3, be=0x1)
    assert c.d_state == D3HOT
    assert c.cfg_read(CAP_PM + 4) == 0x0B             # No_Soft_Reset kept
    c.cfg_write(0x10, 0xABC0_0000)
    c.cfg_write(CAP_PM + 4, 0, be=0x1)                # back to D0: state kept
    assert c.d_state == D0 and c.bar0_base == 0xABC0_0000


# ---------------------------------------------------------------- errors

def test_error_status_without_enables():
    c = Cfg()
    c.error(tl=1 << 0)                                # posted UR: non-fatal, UR detected
    assert c.cfg_read(DEVCTL) >> 16 == (DS_URD | DS_NFED) >> 16
    c.error(tl=1 << 1)                                # non-posted UR: advisory, correctable
    assert c.cfg_read(DEVCTL) & DS_CED
    c.error(dl=1 << 4)
    assert c.cfg_read(DEVCTL) & DS_FED
    assert c.offer() is None                          # no enables, no message


@pytest.mark.parametrize("tl,dl,rcv,dctl,serr,kind", [
    (1 << 2, 0, 0, DC_FERE, False, MSG_ERR_FATAL),
    (1 << 2, 0, 0, 0, True, MSG_ERR_FATAL),            # SERR# enable alone
    (1 << 6, 0, 0, DC_NFERE, False, MSG_ERR_NONFATAL),
    (1 << 0, 0, 0, DC_NFERE, False, None),             # UR needs UR Reporting Enable
    (1 << 0, 0, 0, DC_NFERE | DC_URRE, False, MSG_ERR_NONFATAL),
    (1 << 1, 0, 0, DC_CERE | DC_URRE, False, MSG_ERR_COR),
    (1 << 1, 0, 0, DC_CERE, False, None),
    (1 << 5, 0, 0, DC_CERE, False, MSG_ERR_COR),       # unexpected completion: advisory
    (1 << 7, 0, 0, 0xF, True, None),                   # received UR: status bit only
    (0, 1 << 0, 0, DC_CERE, False, MSG_ERR_COR),
    (0, 0, 1, DC_CERE, False, MSG_ERR_COR),
    (0, 1 << 0, 0, DC_NFERE | DC_FERE, True, None),    # SERR# never gates ERR_COR
    (0, 1 << 5, 0, DC_FERE, False, MSG_ERR_FATAL),
])
def test_error_message_decision(tl, dl, rcv, dctl, serr, kind):
    c = Cfg()
    c.cfg_write(DEVCTL, dctl, be=0x1)
    c.cfg_write(0x04, CMD_SERR if serr else 0, be=0x2)
    c.error(tl=tl, dl=dl, rcv=rcv)
    got = c.offer()
    assert (got.kind if got else None) == kind


def test_signaled_system_error_and_parity_bits():
    c = Cfg()
    c.cfg_write(0x04, CMD_SERR | CMD_PER, be=0x3)
    c.error(tl=1 << 4)                                # poisoned completion
    assert c.cfg_read(0x04) & (STS_DPE | STS_MDPE) == STS_DPE | STS_MDPE
    assert not c.cfg_read(0x04) & STS_SSE
    assert c.take().kind == MSG_ERR_NONFATAL
    assert c.cfg_read(0x04) & STS_SSE


def test_messages_by_priority_and_merged():
    c = Cfg()
    c.cfg_write(DEVCTL, DC_CERE | DC_NFERE | DC_FERE, be=0x1)
    c.error(dl=0b0010011, rcv=1)                       # three correctable, one fatal ...
    c.error(tl=1 << 6)                                 # ... and one non-fatal
    assert [c.take().kind for _ in range(3)] == [MSG_ERR_FATAL, MSG_ERR_NONFATAL, MSG_ERR_COR]
    assert c.offer() is None


def test_no_error_message_outside_d0():
    c = Cfg()
    c.cfg_write(DEVCTL, DC_CERE | DC_FERE, be=0x1)
    c.error(rcv=1)
    assert c.offer().kind == MSG_ERR_COR
    c.cfg_write(CAP_PM + 4, 3, be=0x1)                 # offered, then D3hot: withdrawn
    assert c.offer() is None
    c.error(dl=1 << 4)
    assert c.cfg_read(DEVCTL) & DS_FED                 # status still recorded
    c.cfg_write(CAP_PM + 4, 0, be=0x1)
    assert c.offer() is None                           # nothing held for D0


# ---------------------------------------------------------------- interrupts

def test_dropped_without_msi_or_msix():
    c = Cfg()
    ready(c)
    assert c.irq_request(0) == "dropped"


def test_msi_write_and_pending():
    c = Cfg()
    ready(c, msi=True)
    c.cfg_write(CAP_MSI + 8, 0x1)                      # upper address
    assert c.irq_request(5) is None                    # vector ignored under MSI
    msg = c.take()
    assert (msg.kind, msg.addr, msg.data) == (MSG_IRQ, 1 << 32 | ADDR, 0x1234)
    assert c.irq_status == "sent"
    c.cfg_write(0x04, CMD_MSE, be=0x1)                 # bus mastering off
    assert c.irq_request() == "pending"
    assert c.irq_request() == "pending"                # merged
    assert c.offer() is None
    c.cfg_write(0x04, CMD_MSE | CMD_BME, be=0x1)
    c.take()
    assert c.offer() is None                           # one write for both


def test_msi_pending_cleared_by_disable():
    c = Cfg()
    ready(c, msi=True)
    c.cfg_write(CAP_PM + 4, 3, be=0x1)
    assert c.irq_request() == "pending"
    c.cfg_write(CAP_MSI, 0, be=0x4)
    c.cfg_write(CAP_MSI, 1 << 16, be=0x4)
    c.cfg_write(CAP_PM + 4, 0, be=0x1)
    assert c.offer() is None


def test_msix_masks_and_pending():
    c = Cfg()
    ready(c, msix=True)
    c.bar2_write(16 * 3 + 12, 1)                       # mask vector 3
    assert c.irq_request(3) == "pending"
    assert c.bar2_read(PBA_OFFSET) == 1 << 3
    c.bar2_write(PBA_OFFSET, 0)                        # PBA is read-only
    assert c.bar2_read(PBA_OFFSET) == 1 << 3
    c.bar2_write(16 * 3 + 12, 0)                       # unmask: cfg sends it
    msg = c.take()
    assert (msg.addr, msg.data) == (ADDR + 12, 0x103)
    assert c.bar2_read(PBA_OFFSET) == 0
    c.cfg_write(CAP_MSIX, 0xC000 << 16, be=0x8)        # function mask
    assert c.irq_request(1) == "pending"
    c.cfg_write(CAP_MSIX, 0x8000 << 16, be=0x8)
    assert c.take().addr == ADDR + 4


def test_msix_request_merges_into_pending_bit():
    """A request for a vector already pending, even one about to go out,
    gives no second write."""
    c = Cfg()
    ready(c, msix=True)
    c.cfg_write(CAP_MSIX, 0xC000 << 16, be=0x8)
    assert c.irq_request(2) == "pending"
    c.cfg_write(CAP_MSIX, 0x8000 << 16, be=0x8)        # eligible, not yet taken
    assert c.irq_request(2) == "pending"
    assert c.take().addr == ADDR + 8
    assert c.offer() is None


def test_msix_wins_over_msi():
    c = Cfg()
    ready(c, msi=True, msix=True)
    c.irq_request(2)
    assert c.take().addr == ADDR + 8


def test_msix_pending_kept_while_disabled():
    c = Cfg()
    ready(c, msix=True)
    c.cfg_write(0x04, CMD_MSE, be=0x1)
    assert c.irq_request(4) == "pending"
    c.cfg_write(CAP_MSIX, 0, be=0x8)                   # disable MSI-X, switch to MSI
    c.cfg_write(0x04, CMD_MSE | CMD_BME, be=0x1)
    assert c.offer() is None and c.bar2_read(PBA_OFFSET) == 1 << 4
    c.cfg_write(CAP_MSIX, 0x8000 << 16, be=0x8)
    assert c.take().addr == ADDR + 16


def test_offered_write_withdrawn_when_blocked():
    c = Cfg()
    ready(c, msix=True)
    assert c.irq_request(6) is None and c.irq_busy
    c.cfg_write(0x04, CMD_MSE, be=0x1)                 # bus mastering off before the take
    assert c.offer() is None
    assert (c.irq_busy, c.irq_status) == (False, "pending")
    assert c.bar2_read(PBA_OFFSET) == 1 << 6
    c.cfg_write(0x04, CMD_MSE | CMD_BME, be=0x1)
    assert c.take().addr == ADDR + 24


def test_mask_after_offer_does_not_withdraw():
    c = Cfg()
    ready(c, msix=True)
    c.irq_request(1)
    assert c.offer().addr == ADDR + 4                  # offered
    c.bar2_write(16 * 1 + 12, 1)                       # masked after the offer
    c.cfg_write(CAP_MSIX, 0xC000 << 16, be=0x8)        # function mask too
    assert c.take().addr == ADDR + 4 and c.irq_status == "sent"
    c.irq_request(1)
    assert c.irq_status == "pending" and c.offer() is None


def test_waiting_request_masked_before_its_offer():
    """The application's write waits behind an error message; a mask before
    its offer turns it pending."""
    c = Cfg()
    ready(c, msix=True)
    c.cfg_write(DEVCTL, DC_CERE, be=0x1)
    c.error(rcv=1)
    assert c.offer().kind == MSG_ERR_COR
    assert c.irq_request(2) is None and c.irq_busy
    c.bar2_write(16 * 2 + 12, 1)
    assert (c.irq_busy, c.irq_status) == (False, "pending")
    assert c.take().kind == MSG_ERR_COR and c.offer() is None


def test_msi_offer_withdrawal():
    c = Cfg()
    ready(c, msi=True)
    for v in range(c.p.msix_table_size):
        c.bar2_write(16 * v, ADDR + 4 * v)
    c.irq_request(5)
    assert c.offer().addr == ADDR
    c.cfg_write(CAP_MSIX, 0x8000 << 16, be=0x8)        # not a withdrawal cause
    assert c.offer().addr == ADDR
    c.cfg_write(CAP_MSI, 0, be=0x4)                    # MSI Enable cleared: withdrawn
    assert c.irq_status == "pending"                   # MSI-X on: its vector pends
    assert c.bar2_read(PBA_OFFSET) == 1 << 5
    c.cfg_write(CAP_MSIX, 0, be=0x8)
    c.cfg_write(CAP_MSI, 1 << 16, be=0x4)
    c.irq_request(0)
    assert c.offer() is not None
    c.cfg_write(CAP_MSI, 0, be=0x4)                    # neither mode left: dropped
    assert c.irq_status == "dropped"


def test_transactions_pending():
    c = Cfg()
    c.np_pending = True
    assert c.cfg_read(DEVCTL) >> 21 & 1
    c.cfg_write(DEVCTL, 0xFFFF << 16, be=0xC)          # RO: not cleared by a write
    assert c.cfg_read(DEVCTL) >> 21 & 1
    c.np_pending = False
    assert not c.cfg_read(DEVCTL) >> 21 & 1


def test_vector_out_of_range():
    c = Cfg(Params(msix_table_size=4))
    ready(c, msix=True)
    with pytest.raises(ValueError):
        c.irq_request(4)


def test_table_reads_and_reserved_bits():
    c = Cfg(Params(msix_table_size=2))
    c.bar2_write(0, 0xFFFF_FFFF)
    c.bar2_write(12, 0xFFFF_FFFF)
    assert c.bar2_read(0) == 0xFFFF_FFFC
    assert c.bar2_read(12) == 1
    assert c.bar2_read(28) == 1                        # entry 1 masked at reset
    c.bar2_write(32, 0xFFFF_FFFF)                      # beyond the table
    assert c.bar2_read(32) == 0
    assert c.bar2_read(PBA_OFFSET + 8) == 0


def test_reset_clears_everything():
    c = Cfg()
    ready(c, msix=True)
    c.cfg_write(0x04, CMD_MSE, be=0x1)
    c.irq_request(0)
    c.cfg_write(0x10, 0xFFFF_F000, bus=9, dev=9)
    c.reset()
    assert (c.pba, c.bus_num, c.bar0_base, c.msix_en) == (0, 0, 0, False)
    assert c.bar2_read(12) == 1


# ---------------------------------------------------------------- host and perf

@pytest.mark.parametrize("seed", range(5))
def test_host_enumeration(seed):
    rng = random.Random(seed)
    c = Cfg(m._random_params(rng))
    assert m.enumerate_function(c, rng, bus=3, dev=7) == []


def test_enumeration_catches_a_wrong_model():
    """The host check can fail: a BAR0 that sizes wrong is reported."""
    c = Cfg(Params(bar0_addr_w=16))
    c._spec[0x10 >> 2] = (0, 0xFFFFF000, 0)
    assert any("bar0 size" in e for e in m.enumerate_function(c, random.Random(1)))


def test_perf_scenarios_meet_targets():
    e = m.perf({"kind": "enumeration", "trials": 50}, 1)
    assert e["enumeration_errors"] == 0
    s = m.perf({"kind": "stress", "ops": 3000, "vectors": 8}, 2)
    assert s["requests"] > 500 and s["writes"] > 100 and s["error_messages"] > 0
    assert s["interrupts_lost"] == s["writes_while_blocked"] == s["messages_outside_d0"] == 0


def test_perf_rejects_unknown_kind():
    with pytest.raises(ValueError):
        m.perf({"kind": "nope"}, 1)
