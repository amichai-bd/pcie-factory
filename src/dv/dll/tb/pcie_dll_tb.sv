// Testbench top of pcie_dll (wiki/src/dv/dll/DV_PLAN.md): the DUT with its
// credit parameters, every port passed through by name, plus cfg_fc_o,
// the parameters as the cocotb environment reads them ({FC_PH, FC_PD,
// FC_NPH, FC_NPD, FC_CPLH, FC_CPLD}, FC_PH in the most significant bits).
// The DUT is driven only through its ports, with one interim exception:
// the slot drivers below.
//
// Slot drivers (wiki/src/dv/dll/DV_PLAN.md, slot drivers): until the TLP
// transmit path (#212) and receive path (#213) attach at their MAS slots,
// the credit accounting of #214 has no port that reaches it. When a test
// sets slot_tx_i, tlp_tx_ready_o is forced to a stand-in for #212's TLP
// arbiter (DL_Active, and on a sop beat the credit gate fc_tx_ok); when it
// sets slot_rx_i, the receive decision rx_tlp and the upper receive stream
// are forced from the slot_rx_* inputs, a stand-in for #213. Both drive
// only the signals the MAS slot table names, and both are removed when the
// owning block lands. probe_* show the MAS-named gate and fit signals.
`default_nettype none

module pcie_dll_tb #(
  parameter int unsigned FC_PH   = 8,
  parameter int unsigned FC_PD   = 32,
  parameter int unsigned FC_NPH  = 8,
  parameter int unsigned FC_NPD  = 8,
  parameter int unsigned FC_CPLH = 0,
  parameter int unsigned FC_CPLD = 0
) (
  input  logic        clk_i,
  input  logic        rst_i,
  input  logic        tlp_tx_valid_i,
  output logic        tlp_tx_ready_o,
  input  logic [31:0] tlp_tx_data_i,
  input  logic        tlp_tx_sop_i,
  input  logic        tlp_tx_eop_i,
  output logic        tlp_rx_valid_o,
  input  logic        tlp_rx_ready_i,
  output logic [31:0] tlp_rx_data_o,
  output logic        tlp_rx_sop_o,
  output logic        tlp_rx_eop_o,
  output logic [7:0]  fc_ph_avail_o,
  output logic [11:0] fc_pd_avail_o,
  output logic [7:0]  fc_nph_avail_o,
  output logic [11:0] fc_npd_avail_o,
  output logic [7:0]  fc_cplh_avail_o,
  output logic [11:0] fc_cpld_avail_o,
  output logic        dl_up_o,
  output logic [1:0]  dl_state_o,
  output logic [6:0]  err_o,
  output logic        pkt_tx_valid_o,
  input  logic        pkt_tx_ready_i,
  output logic [15:0] pkt_tx_data_o,
  output logic        pkt_tx_sop_o,
  output logic        pkt_tx_eop_o,
  output logic        pkt_tx_dllp_o,
  input  logic        pkt_rx_valid_i,
  input  logic [15:0] pkt_rx_data_i,
  input  logic        pkt_rx_sop_i,
  input  logic        pkt_rx_eop_i,
  input  logic        pkt_rx_dllp_i,
  input  logic        pkt_rx_err_i,
  input  logic        pkt_rx_nullify_i,
  input  logic        link_up_i,
  input  logic [5:0]  ltssm_state_i,
  output logic        retrain_req_o,
  output logic [79:0] cfg_fc_o,
  // Slot drivers and probes (see above).
  input  logic        slot_tx_i,
  input  logic        slot_rx_i,
  input  logic [34:0] slot_rx_tlp_i,    // {valid, malformed, no_hdr, dw0}
  input  logic        slot_rx_valid_i,
  input  logic [31:0] slot_rx_data_i,
  input  logic        slot_rx_sop_i,
  input  logic        slot_rx_eop_i,
  output logic        probe_tx_ok_o,
  output logic        probe_rx_fits_o
);

  assign cfg_fc_o = {8'(FC_PH), 12'(FC_PD), 8'(FC_NPH), 12'(FC_NPD), 8'(FC_CPLH), 12'(FC_CPLD),
                     20'd0};

  pcie_dll #(
    .FC_PH   (FC_PH),
    .FC_PD   (FC_PD),
    .FC_NPH  (FC_NPH),
    .FC_NPD  (FC_NPD),
    .FC_CPLH (FC_CPLH),
    .FC_CPLD (FC_CPLD)
  ) u_dut (
    .clk_i            (clk_i),
    .rst_i            (rst_i),
    .tlp_tx_valid_i   (tlp_tx_valid_i),
    .tlp_tx_ready_o   (tlp_tx_ready_o),
    .tlp_tx_data_i    (tlp_tx_data_i),
    .tlp_tx_sop_i     (tlp_tx_sop_i),
    .tlp_tx_eop_i     (tlp_tx_eop_i),
    .tlp_rx_valid_o   (tlp_rx_valid_o),
    .tlp_rx_ready_i   (tlp_rx_ready_i),
    .tlp_rx_data_o    (tlp_rx_data_o),
    .tlp_rx_sop_o     (tlp_rx_sop_o),
    .tlp_rx_eop_o     (tlp_rx_eop_o),
    .fc_ph_avail_o    (fc_ph_avail_o),
    .fc_pd_avail_o    (fc_pd_avail_o),
    .fc_nph_avail_o   (fc_nph_avail_o),
    .fc_npd_avail_o   (fc_npd_avail_o),
    .fc_cplh_avail_o  (fc_cplh_avail_o),
    .fc_cpld_avail_o  (fc_cpld_avail_o),
    .dl_up_o          (dl_up_o),
    .dl_state_o       (dl_state_o),
    .err_o            (err_o),
    .pkt_tx_valid_o   (pkt_tx_valid_o),
    .pkt_tx_ready_i   (pkt_tx_ready_i),
    .pkt_tx_data_o    (pkt_tx_data_o),
    .pkt_tx_sop_o     (pkt_tx_sop_o),
    .pkt_tx_eop_o     (pkt_tx_eop_o),
    .pkt_tx_dllp_o    (pkt_tx_dllp_o),
    .pkt_rx_valid_i   (pkt_rx_valid_i),
    .pkt_rx_data_i    (pkt_rx_data_i),
    .pkt_rx_sop_i     (pkt_rx_sop_i),
    .pkt_rx_eop_i     (pkt_rx_eop_i),
    .pkt_rx_dllp_i    (pkt_rx_dllp_i),
    .pkt_rx_err_i     (pkt_rx_err_i),
    .pkt_rx_nullify_i (pkt_rx_nullify_i),
    .link_up_i        (link_up_i),
    .ltssm_state_i    (ltssm_state_i),
    .retrain_req_o    (retrain_req_o)
  );

  assign probe_tx_ok_o   = u_dut.fc_tx_ok;
  assign probe_rx_fits_o = u_dut.rx_fits;

  always @* begin
    if (slot_tx_i) begin
      force u_dut.tlp_tx_ready_o = u_dut.dl_state == pcie_dll_pkg::DL_ACTIVE
                                   && (!tlp_tx_sop_i || u_dut.fc_tx_ok);
    end else begin
      release u_dut.tlp_tx_ready_o;
    end
  end

  always @* begin
    if (slot_rx_i) begin
      force u_dut.rx_tlp         = pcie_dll_pkg::t_fc_rx_tlp'(slot_rx_tlp_i);
      force u_dut.tlp_rx_valid_o = slot_rx_valid_i;
      force u_dut.tlp_rx_data_o  = slot_rx_data_i;
      force u_dut.tlp_rx_sop_o   = slot_rx_sop_i;
      force u_dut.tlp_rx_eop_o   = slot_rx_eop_i;
    end else begin
      release u_dut.rx_tlp;
      release u_dut.tlp_rx_valid_o;
      release u_dut.tlp_rx_data_o;
      release u_dut.tlp_rx_sop_o;
      release u_dut.tlp_rx_eop_o;
    end
  end

endmodule

`default_nettype wire
