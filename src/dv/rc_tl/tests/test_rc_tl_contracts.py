"""Directed contracts: share a live fixture across named assertion phases."""

import cocotb

from tb.rc_tl_env import Env
from tb.rc_tl_scenarios import phase
from tests import test_rc_tl_cmd as cmd
from tests import test_rc_tl_hdr as hdr
from tests import test_rc_tl_inb as inb
from tests import test_rc_tl_req as req

REQ_ROUTES = {1: 6, 6: (0x07 << 16) | (req.BUS << 8) | req.PRI,
              8: (req.LIMIT << 20) | (req.BASE << 4)}
CMD_ROUTES = {1: 2, 6: (0x07 << 16) | (cmd.BUS << 8),
              8: (cmd.LIMIT << 20) | (cmd.BASE << 4)}


async def fixture(dut):
    env = Env(dut)
    await env.start()                  # exactly one owned clock per contract
    return env


@cocotb.test()
async def header_contract(dut):
    env = await fixture(dut)
    for recipe in (hdr.case_hdr_reset, hdr.case_hdr_rw):
        await phase(env, "header_contract", recipe)


@cocotb.test()
async def frontend_contract(dut):
    env = await fixture(dut)
    for recipe in (cmd.case_cmd_checks, cmd.case_byte_enables, cmd.case_check1):
        await phase(env, "frontend_contract", recipe, CMD_ROUTES)


@cocotb.test()
async def request_completion_contract(dut):
    env = await fixture(dut)
    await phase(env, "request_completion_contract", req.case_req_status, REQ_ROUTES,
                rx_gap_p=0.2, answer_delay=(0, 5))
    for recipe in (req.case_req_malformed, req.case_req_split, req.case_req_unexpected):
        await phase(env, "request_completion_contract", recipe, REQ_ROUTES)


@cocotb.test()
async def request_lifecycle_contract(dut):
    env = await fixture(dut)
    for recipe in (req.case_req_timeout, req.case_req_link_down,
                   req.case_req_credits, req.case_err_record):
        await phase(env, "request_lifecycle_contract", recipe, REQ_ROUTES)


@cocotb.test()
async def host_memory_contract(dut):
    env = await fixture(dut)
    for recipe in (inb.case_host_cmds, inb.case_inb_write, inb.case_inb_read):
        await phase(env, "host_memory_contract", recipe, REQ_ROUTES)


@cocotb.test()
async def inbound_classification_contract(dut):
    env = await fixture(dut)
    # Messages require no earlier completion/MSI history. MSI's reset phase
    # is last; all original reset and capture assertions remain in it.
    for recipe in (inb.case_inb_msg, inb.case_inb_malformed, inb.case_msi_capture):
        await phase(env, "inbound_classification_contract", recipe, REQ_ROUTES)


@cocotb.test()
async def inbound_order_reset_contract(dut):
    env = await fixture(dut)
    for recipe in (inb.case_inb_order, inb.case_inb_link_down):
        await phase(env, "inbound_order_reset_contract", recipe, REQ_ROUTES)


@cocotb.test()
async def host_aw8_contract(dut):
    env = await fixture(dut)
    await phase(env, "host_aw8_contract", inb.case_host_cmds, REQ_ROUTES,
                entry="host_cmds_aw8")
    await phase(env, "host_aw8_contract", inb.case_inb_host_edge, REQ_ROUTES)
