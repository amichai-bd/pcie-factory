"""Unit tests of the pcs PIPE-control reference model against the timing
rules of wiki/src/arch/pcs/PIPE_CONTROL.md, written out here independently.
No simulator needed.

Run: python3 -m pytest src/dv/pcs/models
"""

import pcie_pcs_model as m


def run(ref, n, rst=0, reset_n=1, pd=m.P1, det=0, idle=1, rxdet=0):
    return [ref.step(rst, reset_n, pd, det, idle, rxdet) for _ in range(n)]


def test_phy_status_after_reset():
    ref = m.PipeRef()
    outs = run(ref, 3, rst=1)
    assert all(o["phy_status"] == 1 for o in outs)
    outs = run(ref, 20)
    # High in the 16 cycles after the last reset cycle, low from the 17th.
    assert [o["phy_status"] for o in outs] == [1] * 16 + [0] * 4


def test_power_change_ack_and_no_pulse_before_ready():
    ref = m.PipeRef()
    run(ref, 2, reset_n=0)
    outs = run(ref, 5) + run(ref, 11, pd=m.P0, idle=0)   # change before ready
    assert all(o["phy_status"] for o in outs)
    outs = run(ref, 10, pd=m.P0, idle=0)
    assert not any(o["phy_status"] for o in outs), "change before ready pulsed"
    outs = run(ref, 8, pd=m.P1)                          # seen in cycle 0
    assert [o["phy_status"] for o in outs] == [0, 0, 0, 0, 1, 0, 0, 0]


def test_receiver_detection():
    for rxdet, code in ((1, 3), (0, 0)):
        ref = m.PipeRef()
        run(ref, 8, rst=1)
        run(ref, 17)
        outs = run(ref, 70, det=1, rxdet=rxdet)
        hits = [i for i, o in enumerate(outs) if o["phy_status"]]
        assert hits == [63], hits                        # detection cycle 64
        assert outs[63]["rx_status"] == code


def test_tx_idle_latency_and_reset_flush():
    ref = m.PipeRef()
    run(ref, 8, rst=1)
    run(ref, 17, pd=m.P1)
    run(ref, 10, pd=m.P0)                                # P0, still idle
    outs = run(ref, 4, pd=m.P0, idle=0)
    assert [o["tx_elec_idle"] for o in outs] == [1, 1, 0, 0]
    # A one-cycle PIPE reset drops the two words in flight: the line is idle
    # from the reset cycle on (the MAC holds TxElecIdle until PhyStatus falls).
    outs = run(ref, 1, pd=m.P0, idle=0, reset_n=0) + run(ref, 4, pd=m.P0)
    assert [o["tx_elec_idle"] for o in outs] == [1, 1, 1, 1, 1]
