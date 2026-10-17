// Testbench top of pcie_dll (wiki/src/dv/dll/DV_PLAN.md): the DUT with its
// credit parameters, every port passed through by name, plus cfg_fc_o,
// the parameters as the cocotb environment reads them ({FC_PH, FC_PD,
// FC_NPH, FC_NPD, FC_CPLH, FC_CPLD}, FC_PH in the most significant bits).
// The DUT is driven only through its ports, with one interim exception:
// the slot driver below.
//
// Slot driver (wiki/src/dv/dll/DV_PLAN.md, slot driver): until the TLP
// transmit path (#212) attaches at its MAS slot, no port reaches the
// transmit credit gate. When a test sets slot_tx_i, tlp_tx_ready_o is
// forced to a stand-in for #212's TLP arbiter (DL_Active, and on a sop
// beat the credit gate fc_tx_ok). It drives only the signal the MAS slot
// table names and is removed when #212 lands. probe_tx_ok_o shows the
// MAS-named gate.
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
  // Slot driver and probe (see above).
  input  logic        slot_tx_i,
  output logic        probe_tx_ok_o
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

  always @* begin
    if (slot_tx_i) begin
      force u_dut.tlp_tx_ready_o = u_dut.dl_state == pcie_dll_pkg::DL_ACTIVE
                                   && (!tlp_tx_sop_i || u_dut.fc_tx_ok);
    end else begin
      release u_dut.tlp_tx_ready_o;
    end
  end

endmodule

`default_nettype wire
