"""pcie_dll tests (wiki/src/dv/dll/DV_PLAN.md).

Every test runs on the testbench top tb/pcie_dll_tb.sv through the
environment tb/dll_env.py: the phy stand-in, the far end (the independent
partner or hand-driven DLLPs) and the architecture-model shadow. Seeded
tests take cocotb.RANDOM_SEED, which the regression passes.
"""

from __future__ import annotations

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup
from tb.dll_env import (ACTIVE, CLASSES, COV_INIT, ERR_BAD_DLLP, INACTIVE, INIT1, INIT2,
                        STATE_NAMES, Env, arch_partner_init, fc_field, ptn, raw_dllp)

# The HAS's init target (wiki/src/arch/dll/HAS.md#targets): both ends in
# DL_Active within 200 cycles of the later link-up, error-free; within 4500
# with 10% of DLLPs corrupted.
INIT_TARGET = 200
INIT_TARGET_ERRORS = 4500
# The DUT and the shadow (arch model) change state within this many cycles
# of each other: the two differ only in their own transmit timing, so a
# state exit can wait for at most one more InitFC resend (128 cycles) and
# its group.
SHADOW_BOUND = 200

# An UpdateFC's start may lag its due cycle by the packets ahead of it on a
# stalling stream (ready_p 0.5): at most two DLLPs and their stalls.
STALL_SLACK = 64

# Known answers (wiki/src/arch/dll/DLLP.md#crc-16 and the partner's own,
# wiki/src/dv/dll/PARTNER.md#crcs): body and CRC-16 bytes 4, 5.
KAT = (
    ("00000000", "b362"),
    ("00000fff", "25a8"),
    ("40020020", "f534"),
    ("902949c3", "b394"),
    ("10000005", "7d70"),
)

# Reset values of the outputs (wiki/src/arch/dll/HAS.md#reset-values).
RESET_VALUES = ("tlp_tx_ready_o", "tlp_rx_valid_o", "tlp_rx_sop_o", "tlp_rx_eop_o",
                "fc_ph_avail_o", "fc_pd_avail_o", "fc_nph_avail_o", "fc_npd_avail_o",
                "fc_cplh_avail_o", "fc_cpld_avail_o", "dl_up_o", "dl_state_o", "err_o",
                "pkt_tx_valid_o", "retrain_req_o")
INPUTS = {
    "tlp_tx_valid_i": 1, "tlp_tx_data_i": 32, "tlp_tx_sop_i": 1, "tlp_tx_eop_i": 1,
    "tlp_rx_ready_i": 1, "pkt_rx_valid_i": 1,
    "pkt_rx_data_i": 16, "pkt_rx_sop_i": 1, "pkt_rx_eop_i": 1, "pkt_rx_dllp_i": 1,
    "pkt_rx_err_i": 1, "pkt_rx_nullify_i": 1, "ltssm_state_i": 6,
}

COV_SMOKE = Covergroup("dll_reset")
COV_SMOKE.coverpoint("rst", bins={"low": 0, "high": 1})
COV_SMOKE.coverpoint("link_up", bins={"down": 0, "up": 1})
COV_SMOKE.cross("rst_x_link_up", "rst", "link_up")

# Partner advertisements for the far end: its default (completions
# infinite, the Endpoint and Root Port rule), finite completions (a role
# that may advertise them), all infinite, and the smallest finite values.
PARTNER_CREDITS = {
    "default": {"P": (8, 32), "NP": (8, 8), "Cpl": (0, 0)},
    "finite_cpl": {"P": (16, 64), "NP": (4, 2), "Cpl": (16, 64)},
    "infinite": {"P": (0, 0), "NP": (0, 0), "Cpl": (0, 0)},
    "smallest": {"P": (1, 8), "NP": (1, 2), "Cpl": (1, 8)},
}


def fc_dllp(typ: int, hdr: int, data: int) -> bytes:
    return raw_dllp(ptn.dllp_body(typ, hdr, data))


def update(limit: tuple[int, int], new: tuple[int, int]) -> tuple[int, int]:
    """A received UpdateFC's effect: finite fields take the new value, an
    infinite (0) one stays infinite."""
    return tuple(0 if old == 0 else v for old, v in zip(limit, new))


async def wait_sent(env: Env, count: int, limit: int = 2000) -> None:
    await env.run_until(lambda: len(env.sent) >= count, limit, f"{count} DLLPs sent")


async def wait_state(env: Env, state: int, limit: int) -> int:
    return await env.run_until(lambda: env.state == state, limit, STATE_NAMES[state])


# ---------------------------------------------------------------------------


@cocotb.test()
async def smoke(dut):
    """L0: every output holds its reset value while rst_i is high and in
    DL_Inactive, with the other inputs toggling at random."""
    rng = random.Random(cocotb.RANDOM_SEED)
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    for name in INPUTS:
        getattr(dut, name).value = 0
    # The phy stream contract holds: ready from sop to eop (phy HAS).
    dut.pkt_tx_ready_i.value = 1
    dut.link_up_i.value = 0
    dut.rst_i.value = 1
    await FallingEdge(dut.clk_i)
    link = 0
    checked = 0
    for cycle in range(256):
        rst = int(cycle % 64 < 8)
        if cycle % 16 == 0:
            link = rng.getrandbits(1)
        for name, width in INPUTS.items():
            getattr(dut, name).value = rng.getrandbits(width)
        state = int(dut.dl_state_o.value)
        if int(dut.rst_i.value) or state == INACTIVE:
            for name in RESET_VALUES:
                got = int(getattr(dut, name).value)
                assert got == 0, f"cycle {cycle}: {name} {got:#x}, expected 0"
            checked += 1
        dut.rst_i.value = rst
        dut.link_up_i.value = link
        COV_SMOKE.sample(rst=rst, link_up=link)
        await FallingEdge(dut.clk_i)
        if rst or not link:
            assert int(dut.dl_state_o.value) == INACTIVE, (
                f"cycle {cycle}: not DL_Inactive after rst {rst} link_up {link}")
    assert checked > 100


@cocotb.test()
async def crc16_kat(dut):
    """L0: CRC-16 known answers on both sides. The InitFC1-P the DUT sends
    with default credits is the DLLP page's example; the known-answer DLLPs
    and each single-bit corruption of them are received correctly; random
    DLLPs with the models' CRC are good and one flipped bit makes them bad;
    flagged and wrong-length DLLPs are bad; nothing is reported in
    DL_Inactive."""
    rng = random.Random(cocotb.RANDOM_SEED)
    env = Env(dut, cocotb.RANDOM_SEED)
    await env.start()
    for body, crc in KAT:
        assert raw_dllp(bytes.fromhex(body))[4:].hex() == crc, f"model CRC of {body}"

    # DL_Inactive: received DLLPs, bad or not, are discarded silently.
    env.inject(bytes.fromhex("00000000b363"))
    env.inject(bytes.fromhex("00000000b362"), err=True)
    await env.run(20)

    env.dut_link = True
    await wait_sent(env, 3)
    if env.adv["P"] == (8, 32):
        assert env.sent[0][3].hex() == "40020020f534", (
            f"InitFC1-P {env.sent[0][3].hex()}, expected the DLLP page's 40020020f534")

    async def drain() -> None:
        await env.run_until(lambda: not env.raw, 5000, "injected DLLPs received")
        await env.run(4)

    for body, crc in KAT:
        pkt = bytes.fromhex(body + crc)
        env.inject(pkt, gaps=rng.randint(0, 2))
        for bit in range(48):
            bad = bytearray(pkt)
            bad[bit // 8] ^= 1 << (bit % 8)
            env.inject(bytes(bad), gaps=rng.randint(0, 2))
    await drain()
    for _ in range(150):
        pkt = raw_dllp(bytes(rng.getrandbits(8) for _ in range(4)))
        env.inject(pkt, gaps=rng.randint(0, 2))
        bad = bytearray(pkt)
        bit = rng.randrange(48)
        bad[bit // 8] ^= 1 << (bit % 8)
        env.inject(bytes(bad), gaps=rng.randint(0, 2))
    # Flagged by phy, and every wrong length from 1 to 4 beats.
    good = raw_dllp(bytes.fromhex("00000123"))
    env.inject(good, err=True)
    for length in (2, 4, 8):
        env.inject(good[:length] if length < 6 else good + bytes(length - 6))
    # Too long, yet ending in the CRC-16 of the first four bytes: only the
    # length check rejects these.
    for extra in (2, 4):
        env.inject(good[:4] + bytes(rng.getrandbits(8) for _ in range(extra)) + good[4:])
    env.inject(good)
    await drain()
    assert env.bad_expected == 5 * 48 + 150 + 6, f"{env.bad_expected} bad DLLPs injected"
    env.check_clean()


@cocotb.test()
async def fc_init_directed(dut):
    """L0: data link control and flow-control initialisation against a
    hand-driven far end: the InitFC1 group and its 128-cycle resend; the
    first InitFC per class recorded (InitFC1 or InitFC2) with its reserved
    bits ignored, later ones and other VCs, other types and UpdateFC
    ignored in FC_INIT1; FC_INIT1 left
    only with all three recorded and its group complete; FC_INIT2 not
    ended by InitFC1, ended by InitFC2 in one epoch and by UpdateFC (which
    also sets the limit) in the next; the entry UpdateFC of every class in
    order; credits available; an UpdateFC moving a finite limit and not an
    infinite one; link down from each state."""
    env = Env(dut, cocotb.RANDOM_SEED)
    await env.start()
    adv = env.adv
    far = {"P": (12, 0), "NP": (0, 40), "Cpl": (3, 96)}   # mixed finite and infinite

    for epoch, ending in enumerate(("initfc2", "updatefc")):
        env.dut_link = True
        await wait_state(env, INIT1, 10)
        start = len(env.sent)
        await wait_sent(env, start + 3)
        # Groups every 128 cycles while nothing is recorded.
        await env.run(300)
        starts = env.group_starts[-3:]
        assert [b - a for a, b in zip(starts, starts[1:])] == [128, 128], (
            f"InitFC1 groups start at {starts}, expected 128 cycles apart")
        # Ignored: another VC, a class code 3, bit 3 set, reserved and
        # other types (0x20, 0x30, 0x31), UpdateFC in FC_INIT1.
        for typ in (ptn.INITFC1["P"] | 1, 0x70, ptn.INITFC1["P"] | 8, 0x20, 0x30, 0x31,
                    ptn.UPDATEFC["P"]):
            env.inject(fc_dllp(typ, 5, 5))
        # Recorded: the first of each class, InitFC1 or InitFC2; reserved
        # bits (byte 1 bits 7:6, byte 2 bits 5:4) are ignored.
        body = bytearray(ptn.dllp_body(ptn.INITFC1["P"], *far["P"]))
        body[1] |= 0xC0
        body[2] |= 0x30
        env.inject(raw_dllp(bytes(body)))
        env.inject(fc_dllp(ptn.INITFC1["P"], 99, 99))
        env.inject(fc_dllp(ptn.INITFC2["NP"], *far["NP"]))
        await env.run(200)
        assert env.state == INIT1, "left FC_INIT1 with Cpl not recorded"
        env.inject(fc_dllp(ptn.INITFC1["Cpl"], *far["Cpl"]))
        await wait_state(env, INIT2, 200)
        last_init1 = max(s for s, k, _, _ in env.sent if k == "init1")
        assert env.states[-1][0] > last_init1 + 2, "FC_INIT2 entered with the group unfinished"
        # FC_INIT2: InitFC1 does not end it; groups of InitFC2 go out.
        await wait_sent(env, len(env.sent) + 3)
        env.inject(fc_dllp(ptn.INITFC1["P"], *far["P"]))
        await env.run(200)
        assert env.state == INIT2, "an InitFC1 ended FC_INIT2"
        assert env.dut.dl_up_o.value == 1
        if ending == "initfc2":
            env.inject(fc_dllp(ptn.INITFC2["Cpl"], *far["Cpl"]))
            expect = far
        else:
            # An UpdateFC ends FC_INIT2 and sets the finite fields.
            env.inject(fc_dllp(ptn.UPDATEFC["Cpl"], 7, 100))
            expect = dict(far, Cpl=update(far["Cpl"], (7, 100)))
        COV_INIT.sample(init2_end=ending)
        await wait_state(env, ACTIVE, 300)
        entered = env.states[-1][0]
        await env.run(30)
        upd = [(k, c) for s, k, c, _ in env.sent if s >= entered][:3]
        assert upd == [("update", 0), ("update", 1), ("update", 2)], (
            f"entry UpdateFCs {upd}, expected P, NP, Cpl")
        await env.run(5)
        env.check_avail(expect)
        # UpdateFC in DL_Active: a finite field moves, an infinite one not.
        env.inject(fc_dllp(ptn.UPDATEFC["P"], 20, 7))
        env.inject(fc_dllp(ptn.UPDATEFC["NP"], 9, 41))
        await env.run(40)
        env.check_avail(dict(expect, P=update(expect["P"], (20, 7)),
                             NP=update(expect["NP"], (9, 41))))
        for cls in CLASSES:
            COV_INIT.sample(adv_hdr=fc_field(adv[cls][0]), adv_data=fc_field(adv[cls][1]),
                            partner_hdr=fc_field(far[cls][0]),
                            partner_data=fc_field(far[cls][1]))
        # Link down: DL_Inactive at once, everything cleared.
        env.dut_link = False
        await wait_state(env, INACTIVE, 3)
        await env.run(30)
        far = {"P": (5, 8), "NP": (6, 2), "Cpl": (2, 8)}

    # Link down from FC_INIT1 and from FC_INIT2.
    for target in (INIT1, INIT2):
        env.dut_link = True
        await wait_state(env, INIT1, 10)
        if target == INIT2:
            for cls in CLASSES:
                env.inject(fc_dllp(ptn.INITFC1[cls], 1, 8))
            await wait_state(env, INIT2, 300)
        await env.run(17)
        env.dut_link = False
        await wait_state(env, INACTIVE, 3)
        await env.run(40)
    env.check_clean()


@cocotb.test()
async def updatefc_refresh(dut):
    """L1, seeded: in DL_Active every class gets an UpdateFC again 2048
    cycles after its last one, with no credits returned, infinite classes
    included, round robin. The phy stalls between packets, so a class can
    fall due while another's UpdateFC is still waiting on the stream."""
    env = Env(dut, cocotb.RANDOM_SEED, ready_p=0.5)
    await env.start()
    env.dut_link = True
    await wait_state(env, INIT1, 10)
    for cls in CLASSES:
        env.inject(fc_dllp(ptn.INITFC1[cls], 4, 16))
    await wait_state(env, INIT2, 300)
    env.inject(fc_dllp(ptn.INITFC2["P"], 4, 16))
    await wait_state(env, ACTIVE, 300)
    await env.run(2 * 2048 + 200)
    for c, cls in enumerate(CLASSES):
        # Counted from the arbiter's take: the first cycle the sop is offered.
        starts = [o for o, (_, k, cc, _) in zip(env.offers, env.sent)
                  if k == "update" and cc == c]
        assert len(starts) == 3, f"{cls}: {len(starts)} UpdateFCs, expected 3 in 2 periods"
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        assert all(2048 <= g <= 2048 + STALL_SLACK for g in gaps), (
            f"{cls}: UpdateFC every {gaps} cycles, expected 2048 plus the wait for the "
            f"stream, at most {STALL_SLACK}")
    order = [cc for _, k, cc, _ in env.sent if k == "update"]
    assert order == [0, 1, 2] * 3, f"UpdateFC classes in order {order}"
    env.check_clean()


async def link_epoch(env: Env, *, first: str, skew: int,
                     bound: int, cross_check: bool) -> int:
    """One link-up against the partner: ``first`` comes up ``skew`` cycles
    before the other. Both must reach DL_Active within ``bound`` cycles of
    the later link-up. Returns that time."""
    p = env.partner
    start = env.cycle
    up = {"dut": 0, "partner": 0}
    if first in up:
        up[[k for k in up if k != first][0]] = skew
    while env.cycle - start < max(up.values()):
        env.dut_link = env.cycle - start >= up["dut"]
        env.partner_link = env.cycle - start >= up["partner"]
        await env.step()
    env.dut_link = env.partner_link = True
    later = env.cycle
    await env.run_until(lambda: env.state == ACTIVE and p.state == ptn.ACTIVE, bound,
                        "both ends in DL_Active")
    took = env.cycle - later
    COV_INIT.sample(first_up=first)
    if cross_check:
        # The architecture model against a fresh partner, same skew.
        fresh = ptn.Partner("fresh", credits=p.adv, seed=0)
        n, model = arch_partner_init(env.adv, fresh, arch_up=up["dut"],
                                     partner_up=up["partner"])
        arch_took = n - max(up.values())
        assert abs(arch_took - took) <= SHADOW_BOUND, (
            f"DL_Active after {took} cycles with the RTL, {arch_took} with the arch model")
        assert not fresh.violations, "\n".join(fresh.violations[:10])
        for i, cls in enumerate(CLASSES):
            want = tuple(None if v == 0 else v for v in env.adv[cls])
            assert fresh.avail(cls) == want, f"partner recorded {fresh.avail(cls)} from the arch"
            got = (model.avail(i, 0), model.avail(i, 1))
            exp = tuple((0xFF, 0xFFF)[f] if p.adv[cls][f] == 0 else p.adv[cls][f]
                        for f in (0, 1))
            assert got == exp, f"arch recorded {got} for {cls}, expected {exp}"
        env.log_xcheck.append((first, skew, took, arch_took))
    await env.run(300)
    # Partner and DUT recorded each other's advertisement.
    for cls in CLASSES:
        want = tuple(None if v == 0 else v for v in env.adv[cls])
        assert p.avail(cls) == want, f"partner recorded {cls} {p.avail(cls)}, DUT sent {want}"
        COV_INIT.sample(adv_hdr=fc_field(env.adv[cls][0]), adv_data=fc_field(env.adv[cls][1]),
                        partner_hdr=fc_field(p.adv[cls][0]),
                        partner_data=fc_field(p.adv[cls][1]))
    env.check_avail(p.adv)
    env.check_clean()
    # Both links down together, long enough to empty the lines.
    env.dut_link = env.partner_link = False
    await env.run(40)
    return took


async def link_run(dut, *, epochs: int, errors: bool, stalls: bool) -> None:
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    names = sorted(PARTNER_CREDITS)
    credits = PARTNER_CREDITS[names[seed % len(names)]]
    inject = ptn.Inject()
    if errors:
        inject = ptn.Inject(tx_dllp_corrupt=0.05, tx_dllp_drop=0.03, tx_dllp_flag=0.02,
                            rx_dllp_drop=0.05)
    partner = ptn.Partner("partner", credits=credits, inject=inject, seed=seed)
    env = Env(dut, seed, partner=partner, ready_p=0.8 if stalls else 1.0,
              gap_p=0.2 if stalls else 0.0)
    env.log_xcheck = []
    await env.start()
    dut._log.info("seed %d, DUT credits %s, partner credits %s", seed, env.adv, credits)
    clean = not errors and not stalls
    bound = INIT_TARGET if clean else INIT_TARGET_ERRORS
    worst = 0
    for i in range(epochs):
        first = ("dut", "partner", "same")[(i + seed) % 3]
        skew = 0 if first == "same" else rng.randint(1, 300)
        took = await link_epoch(env, first=first, skew=skew, bound=bound,
                                cross_check=not errors)
        worst = max(worst, took)
        dut._log.info("epoch %d: %s first by %d, both in DL_Active after %d cycles",
                      i, first, skew, took)
    shadow = env.check_shadow(SHADOW_BOUND)
    dut._log.info("worst link-up %d cycles; DUT vs arch shadow state changes at most %d "
                  "cycles apart; RTL vs arch-model link-up times %s", worst, shadow,
                  env.log_xcheck)
    dut._log.info("partner stats %s", partner.stats)
    env.check_clean()


@cocotb.test()
async def link_up(dut):
    """L1, seeded: link-up to DL_Active against the independent partner, the
    DUT first, the partner first and both together, error-free and with no
    stalls: within the HAS's 200 cycles, credits recorded both ways, every
    DLLP checked, cross-checked with the architecture model (shadow and an
    arch-model-against-partner run)."""
    await link_run(dut, epochs=6, errors=False, stalls=False)


@cocotb.test()
async def link_up_stress(dut):
    """L1/L2, seeded: the same with phy ready stalls, gaps inside received
    packets, and corrupted, dropped and phy-flagged DLLPs both ways; within
    the HAS's 4500 cycles."""
    await link_run(dut, epochs=6, errors=True, stalls=True)
