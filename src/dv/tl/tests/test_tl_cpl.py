"""pcie_tl requester-completion tests (wiki/src/dv/tl/DV_PLAN.md, plan):
the tracker's matching, checks and errors, split completions, delivery on
rc and its order, tag exhaustion, the completion timeout and its disable.
The checks are the environment's (tb/tl_env.py, the completion reference
models/pcie_tl_cpl_ref.py); each test adds what it could fail on.
"""

import random

import cocotb

from tb import tl_env
from tb.tl_env import cref
from tb.tl_env import ref
from tb.tl_env import TlError
from tests.test_tl_rx import random_tlp_td


# ------------------------------------------------------------ host faults

def mutate(tlp: bytes, *, bc=None, la=None, status=None, ep=None, rid=None, tag=None,
           locked=None) -> bytes:
    """A completion with some header fields changed (TLP.md, completions)."""
    b = bytearray(tlp)
    if bc is not None:
        b[6] = (b[6] & 0xF0) | ((bc >> 8) & 0xF)
        b[7] = bc & 0xFF
    if la is not None:
        b[11] = la & 0x7F
    if status is not None:
        b[6] = (b[6] & 0x1F) | (status << 5)
    if ep is not None:
        b[2] = (b[2] & ~0x40) | (ep << 6)
    if rid is not None:
        b[8], b[9] = rid >> 8, rid & 0xFF
    if tag is not None:
        b[10] = tag
    if locked is not None:
        b[0] = (b[0] & 0xE0) | (ref.TYPE_CPL_LOCKED if locked else ref.TYPE_CPL)
    return bytes(b)


def no_data(tlp: bytes) -> bytes:
    """The same header as a completion without data."""
    f = cref.fields(tlp)
    return ref.completion(status=f["status"], byte_count=f["bc"], rid=f["rid"], tag=f["tag"],
                          lower_addr=f["la"])


def resized(tlp: bytes, n: int) -> bytes:
    """The same header with ``n`` data DWs."""
    f = cref.fields(tlp)
    data = (f["data"] + [0x5A5A0000 + i for i in range(n)])[:n]
    return ref.completion(status=f["status"], byte_count=f["bc"], rid=f["rid"], tag=f["tag"],
                          lower_addr=f["la"], data=data)


# Directed faults (COMPLETIONS.md, checks): (name, hook on the legal
# completions, what the read ends with).
def _first(fn):
    return lambda c: [fn(c[0])] + c


FAULTS = [
    ("byte_count", _first(lambda x: mutate(x, bc=cref.fields(x)["bc"] - 1)), "data"),
    ("lower_address", _first(lambda x: mutate(x, la=cref.fields(x)["la"] ^ 4)), "data"),
    ("boundary", _first(lambda x: resized(x, len(cref.fields(x)["data"]) - 1)), "data"),
    ("past_end", lambda c: c[:-1] + [resized(c[-1], len(cref.fields(c[-1])["data"]) + 1),
                                    c[-1]], "data"),
    ("no_data", _first(no_data), "data"),
    ("crs", _first(lambda x: mutate(x, status=cref.CRS)), "data"),
    ("ur", lambda c: [no_data(mutate(c[0], status=cref.UR))], "ur"),
    ("ca", lambda c: [no_data(mutate(c[0], status=cref.CA))], "ca"),
    ("reserved", lambda c: [no_data(mutate(c[0], status=0b110))], "ur"),
    ("poisoned_first", lambda c: [mutate(c[0], ep=1)] + c[1:], "failed"),
    ("poisoned_last", lambda c: c[:-1] + [mutate(c[-1], ep=1)], "failed"),
    ("rid", _first(lambda x: mutate(x, rid=cref.fields(x)["rid"] ^ 0x0100)), "data"),
    ("tag_high", _first(lambda x: mutate(x, tag=cref.fields(x)["tag"] | 0x08)), "data"),
    ("locked", _first(lambda x: mutate(x, locked=True)), "data"),
    ("digest", lambda c: [ref.with_digest(x, 0x0BADC0DE) for x in c], "data"),
    ("duplicate_after", lambda c: c + [c[-1]], "data"),
]


ENDS = {"data": cref.RC_OK, "ur": cref.RC_UR, "ca": cref.RC_CA, "failed": cref.RC_FAILED}


async def read_with(env: tl_env.TlEnv, tag: int, addr: int, length: int, hook,
                    fbe: int = 0xF, lbe: int | None = None) -> None:
    """One read answered by the host through ``hook``, drained."""
    env.host_hook = lambda e, t, req, tlps: hook(tlps)
    env.request(False, addr, length, fbe, lbe, tag=tag)
    await env.drain()
    env.host_hook = None


# ------------------------------------------------------------------ tests

@cocotb.test()
async def cpl_matching(dut):
    """L1: reads of every split (one, two, three completions), byte-enable
    and zero-length reads, 3- and 4-DW requests; then each completion
    check (Byte Count, Lower Address, boundary, past the end, no data,
    Configuration Request Retry) dropping a bad completion while its read
    waits, each error status ending a read (UR, CA, reserved), a poisoned
    completion failing its read, the unexpected cases (Requester ID, tag 8
    or more, locked, after the read ended, before the read was sent), a
    digest; then an rc packet waiting behind a posted write the
    application holds, and interleaved reads with rc stalls."""
    env = await tl_env.started(dut, cocotb.RANDOM_SEED, host_lat=(4, 20))
    shapes = [
        (0x1004, 32, 0xF, 0xF), (0x1080, 32, 0xF, 0xF), (0x10F8, 4, 0xF, 0xF),
        (0x1038, 3, 0b1100, 0b0001), (0x1100, 1, 0, 0), (0x1104, 1, 0b0110, 0),
        (0x1108, 2, 0b1000, 0b0001), ((0x3 << 40) | 0x2044, 17, 0xE, 0x7),
        (0x1FC0, 16, 0xF, 0xF),
    ]
    for p in (1.0, 0.0, 0.5):
        env.host_split_p = p
        for i, (a, n, f, l) in enumerate(shapes):
            env.request(False, a, n, f, l, tag=i & 7)
        await env.drain()
    if set(env.cpl_splits) != {1, 2, 3}:
        raise TlError(f"reads finished in {sorted(set(env.cpl_splits))} completions; 1, 2 and 3 "
                      "expected")
    # Each fault on a three-part read (128 bytes at offset 4, every cut).
    env.host_split_p = 1.0
    for k, (name, hook, end) in enumerate(FAULTS):
        before = len(env.cpl_cases)
        await read_with(env, k & 7, 0x4004 + 0x100 * k, 32, hook)
        cases = env.cpl_cases[before:]
        env.log.info("fault %s: %s", name, cases)
        if all(c in ("partial", "last") for c in cases) and name != "digest":
            raise TlError(f"fault {name} made no error case: {cases}")
        if env.rc_ends[-1] != (k & 7, ENDS[end]):
            raise TlError(f"fault {name}: the read ended with {env.rc_ends[-1]}, status "
                          f"{ENDS[end]} expected")
    # Before the read is sent: non-posted credits held, the read taken.
    env.cr_avail["NP"] = [0, 0]
    env.cr_hold.add("NP")
    env.request(False, 0x5000, 1, 0xF, 0, tag=3)
    await env.run(30)
    if 3 not in env.cpl.reads or env.cpl.reads[3].sent is not None:
        raise TlError("the read was not taken, or was sent with no credit")
    env.send(ref.completion(byte_count=4, rid=env.my_id, tag=3, lower_addr=0, data=[1]))
    await env.run(40)
    env.cr_hold.discard("NP")
    env.cr_avail["NP"] = [8, 8]
    await env.drain()
    if "unexpected_unsent" not in env.cpl_cases:
        raise TlError("a completion before its read was sent was not unexpected")
    # rc behind a posted write the application holds on cq.
    env.host_lat = (60, 60)
    env.request(False, 0x6000, 8, tag=1)
    await env.run(20)
    env.cq_stall_until = env.cycle + 400
    env.send(ref.memory(True, env.regs.bar0_base + 0x40, 4))
    await env.run(150)
    if not env.rc_exp:
        raise TlError("the completion did not arrive behind the held write")
    await env.drain()
    # Interleaved reads, rc stalling.
    env.host_lat, env.rc_ready_p, env.host_split_p = (2, 30), 0.3, 0.7
    for i in range(16):
        env.request(False, 0x7000 + 0x84 * i, 1 + (7 * i) % 32, tag=None)
    await env.drain()


@cocotb.test()
async def cpl_tags(dut):
    """L1: tag exhaustion: eight reads of 128 bytes outstanding, the next
    reads waiting for a tag; the application holding rc so every read's
    data sits in tl (1 KiB), Transactions Pending while any read waits;
    then everything delivered and the waiting reads issued."""
    env = await tl_env.started(dut, cocotb.RANDOM_SEED, host_lat=(150, 200), host_split_p=0.5)
    env.rc_hold = True
    for i in range(12):
        env.request(False, 0x8000 + 0x100 * i + 4 * (i % 3), 32, tag=None)
    await env.run(120)
    if len(env.rq_tags) != 8 or len(env.rq_q) != 4 or env.rq_cur is not None:
        raise TlError(f"tags in use {sorted(env.rq_tags)}, {len(env.rq_q)} reads waiting; "
                      "8 and 4 expected")
    for _ in range(2000):
        await env.step()
        if not env.cpl.pending() and not env.cpl_judge and not env.host_due:
            break
    held = sum(len(e["pkt"].data) for e in env.rc_exp)
    held += len(env.rc_cur["pkt"].data) if env.rc_cur else 0     # offered, not taken
    if held != 8 * 32:
        raise TlError(f"{held} DWs held for rc, 256 expected")
    if len(env.rq_q) != 4:
        raise TlError("a read was issued while all tags were in use")
    env.rc_hold = False
    await env.drain()
    env.rc_ready_p = 0.5
    for i in range(24):
        env.request(i % 3 == 0, 0x9000 + 0x80 * i, 1 + i, tag=None)
    await env.drain()


@cocotb.test()
async def cpl_timeout(dut):
    """L1, CPL_TIMEOUT_DIV 1024: reads with no completion time out once,
    between 2048 and 2176 cycles after they were sent (several at one
    pulse), a read after part of its data, a completion after its timeout
    is unexpected; with the timeout disabled no read times out and
    Transactions Pending stays 1, and when the disable clears each read
    gets a full timeout from then."""
    env = await tl_env.started(dut, cocotb.RANDOM_SEED)
    lo, hi = env.cpl.window()
    if (lo, hi) != (2048, 2176):
        raise TlError(f"timeout window {lo, hi}: the test needs CPL_TIMEOUT_DIV 1024")
    late = {}
    keep = {}                                    # tag -> completions the host sends

    def hook(e, tag, req, tlps):
        k = keep.pop(tag, len(tlps))
        late[tag] = tlps[k:]
        return tlps[:k]

    env.host_hook = hook
    # Three reads lost together, one after its first part.
    env.host_split_p = 1.0
    for tag in (0, 1, 2):
        keep[tag] = 0
        env.request(False, 0x1000 * (tag + 1), 4, tag=tag)
    keep[5] = 1
    env.request(False, 0x5004, 32, tag=5)
    await env.drain(limit=4000)
    if len(env.tmo_cycles) != 4:
        raise TlError(f"{len(env.tmo_cycles)} timeouts, 4 expected")
    # Their completions, late: unexpected.
    for tlps in late.values():
        for x in tlps:
            env.send(x)
    await env.drain()
    if env.cpl_cases.count("unexpected_no_read") < 4:
        raise TlError("late completions not unexpected")
    # Disabled: a lost read waits past 17 periods; cleared, a full timeout.
    env.dis = True
    keep[6] = 0
    env.request(False, 0x6000, 2, tag=6)
    await env.run(hi + 600)
    if 6 not in env.cpl.reads or not env.cpl.pending():
        raise TlError("a read timed out while the timeout was disabled")
    n = len(env.tmo_cycles)
    env.dis = False
    await env.drain(limit=4000)
    if len(env.tmo_cycles) != n + 1:
        raise TlError("the read did not time out once the disable cleared")
    if not all(lo < c <= hi for c in env.tmo_cycles):
        raise TlError(f"timeouts outside ({lo}, {hi}]: {env.tmo_cycles}")
    env.log.info("timeout cycles from sent: %s", env.tmo_cycles)


async def random_cpl(dut, *, segments: int, per_segment: int) -> tl_env.TlEnv:
    """Random rq reads and writes with the host's completions split at
    random and some faulted (lost, poisoned, error status, malformed,
    duplicated, digest), host traffic, rc stalls, the disable toggled,
    segments separated by link-downs."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed ^ 0x292)
    env = await tl_env.started(dut, seed, host_lat=(2, 120))

    def hook(e, tag, req, tlps):
        pick = rng.random()
        if pick < 0.04:
            return []                                        # lost: times out
        if pick < 0.08:
            return tlps[:-1]
        if pick < 0.13:
            k = rng.randrange(len(tlps))
            tlps[k] = mutate(tlps[k], ep=1)
        elif pick < 0.17:
            return [no_data(mutate(tlps[0], status=rng.choice([cref.UR, cref.CA, 0b011, 0b111])))]
        elif pick < 0.27:
            k = rng.randrange(len(tlps))
            x = tlps[k]
            f = cref.fields(x)
            bad = rng.choice([mutate(x, bc=f["bc"] + 4), mutate(x, la=f["la"] ^ 0x10),
                              mutate(x, status=cref.CRS), no_data(x),
                              resized(x, max(1, len(f["data"]) - 1)),
                              mutate(x, rid=f["rid"] ^ 0x0800), mutate(x, locked=True),
                              mutate(x, tag=0x80 | tag)])
            tlps.insert(k, bad)
        elif pick < 0.31:
            tlps.append(tlps[-1])                            # a duplicate after the end
        return [ref.with_digest(x, rng.getrandbits(32)) if rng.random() < 0.1 else x
                for x in tlps]

    for s in range(segments):
        if s:
            await env.link_down(rng.randint(1, 6))
        env.host_hook = hook
        env.host_split_p = rng.choice([0.0, 0.5, 1.0])
        env.rc_ready_p = rng.choice([1.0, 0.6, 0.2])
        env.tx_ready_p = rng.choice([1.0, 0.6])
        env.dis = rng.random() < 0.1
        reads = [4]
        for _ in range(per_segment):
            pick = rng.random()
            if pick < 0.6:
                n = rng.randint(1, 32)
                a = (rng.choice([0, 0x5 << 36]) + 0x1000 * rng.randrange(64)
                     + 4 * rng.randrange(1024 - n + 1))
                f = rng.choice([0xF, 0xE, 0xC, 0x8]) if n > 1 else rng.choice([0xF, 0x1, 0x6, 0])
                l = rng.choice([0xF, 0x7, 0x3, 0x1]) if n > 1 else 0
                env.request(rng.random() < 0.25, a, n, f, l, tag=None)
            else:
                env.send(random_tlp_td(rng, env.regs, reads))
        if rng.random() < 0.2:
            await env.run(rng.randint(5, 400))
            continue                                         # cut short by the link-down
        if env.dis:
            await env.run(rng.randint(100, 2500))
            env.dis = False
        await env.drain(limit=60000)
    return env


@cocotb.test()
async def cpl_random(dut):
    """L1, seeded: random requester traffic with faulted completions."""
    await random_cpl(dut, segments=4, per_segment=40)


@cocotb.test()
async def cpl_random_long(dut):
    """L2, seeded: the random traffic of cpl_random, longer."""
    await random_cpl(dut, segments=16, per_segment=100)
