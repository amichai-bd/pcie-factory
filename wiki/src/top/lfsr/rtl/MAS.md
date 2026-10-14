# pcie_top_lfsr MAS

`pcie_top_lfsr` is the data-scrambler LFSR of the PCI Express Gen1/Gen2
(8b/10b) physical layer: it produces the key byte that its parent,
[`pcie_top`](../MAS.md), XORs with each data byte. It is a seed IP, small on
purpose, that gives the factory a real sub-IP to build, regress and review.

It is not a full scrambler: it sees no data, does not know which symbols are
K codes, and does not apply the SKP (do not advance) or COM (re-seed) rules;
the parent decides when to advance and re-seed. It is not the Gen3+
(128b/130b) scrambler, which uses a different polynomial.

## Position

```mermaid
flowchart LR
  ctl["pcie_top: valid_i, reseed_i"] --> lfsr["pcie_top_lfsr"]
  lfsr -- "key_o[7:0]" --> xor["pcie_top: data XOR key"]
```

No sub-IPs.

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Galois form, eight shifts unrolled per advance | One key byte per clock for a byte-wide datapath; 16 flops and a few dozen XORs need no pipelining. | Bit-serial LFSR on an 8x clock (second clock); table of precomputed states (area for nothing). |
| `key_o` from the registered state only | The parent's XOR has no combinational path through the control inputs. | Key from the next state (longer path, control-to-data timing arc). |
| Synchronous re-seed with priority over advance | Matches the COM rule: the byte after a COM uses the seed's key. | Advance priority (a COM byte would consume key). |

## Targets

| Item | Target |
| --- | --- |
| Throughput | One key byte per clock. |
| Latency | `key_o` is the seed key in the cycle after reset or re-seed. |
| Area | 16 flops. |
| Polynomial, seed | X^16 + X^5 + X^4 + X^3 + 1, `16'hFFFF`. |
