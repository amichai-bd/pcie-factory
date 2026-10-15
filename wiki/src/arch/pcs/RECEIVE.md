# pcie_pcs receive path

How the [`pcs`](HAS.md) turns the unaligned 20-bit words on
`ser_rx_data_i` into PIPE receive words: comma alignment and symbol lock
in the `ser_rx_clk_i` domain, the SKP elastic buffer into `clk_i`, then
polarity, [8b/10b decode](8B10B.md) and RxStatus. These rules are
normative (decision Q21 in the
[M1 epic](https://github.com/amichai-bd/pcie-factory/issues/65)); the
[architecture model](../../../../src/arch/pcs/pcie_pcs_arch.py) runs them as
a two-clock simulation.

```mermaid
flowchart LR
  rx["ser_rx_data_i<br/>ser_rx_clk_i"] --> al["aligner<br/>comma search, lock"]
  al --> wr["elastic buffer write side<br/>SKP removal, packing"]
  wr --> fifo["common_cdc_fifo<br/>16 x 2 symbols"]
  fifo --> rd["elastic buffer read side<br/>SKP addition (clk_i)"]
  rd --> dec["decoder<br/>polarity, decode"]
  dec --> pipe["RxData, RxDataK,<br/>RxValid, RxStatus"]
  dec -. "errors: relock request<br/>common_cdc_pulse" .-> al
  rd -. "fill high<br/>common_cdc_sync2" .-> wr
```

## Comma alignment and symbol lock

Each `ser_rx_clk_i` cycle the aligner registers the incoming word and
looks at a 40-bit window: the previous word then the current one, earliest
bit first.

- **Hunt.** While unlocked, and only when neither word of the window was
  received with `ser_rx_elec_idle_i` high, the aligner searches the window
  at bit offsets 0 to 19 for a [comma](8B10B.md#comma). At the lowest
  offset `p` where one starts, it sets the symbol boundary to `p mod 10`
  and takes **symbol lock** at once, in that cycle. Each time it takes
  lock it flips a **lock-generation** bit, which travels with every word
  through the elastic buffer. A relock in comma-dense traffic (training
  sets, FTS) can retake lock in the same cycle it dropped, so no unlocked
  word separates the two runs; the generation bit still tells them apart.
- **Locked.** The boundary is frozen. Each cycle the aligner sends the two
  10-bit symbols that start at the boundary and at the boundary plus 10,
  tagged locked. Commas at other offsets are ignored.
- **Loss of lock.** Lock drops in the cycle after `ser_rx_elec_idle_i` is
  seen high, and when a relock request arrives from the `clk_i` side. The
  aligner then hunts again.
- **Relock request.** On the `clk_i` side an error counter watches the
  words that leave with `pipe_rx_valid_o` high. A word with a code or
  disparity error adds 1; eight error-free words in a row take 1 away (not
  below 0). At 3 the PCS sends one relock request (a
  [`common_cdc_pulse`](../../rtl/common/common_cdc_pulse.md) to the aligner)
  and clears the counter. It sends no other request until a locked word
  with the other lock-generation value reaches PIPE, which proves the
  aligner has relocked. The counter also clears whenever the lock
  generation of the words on PIPE changes. The request needs no unlocked
  word to re-arm, so it works however soon lock returns, and even when
  recentring drops the unlocked words.

A single flipped bit marks at most two words: the symbol it hits (a code
or disparity error) and, through the corrupted running disparity, at most
one later symbol. So isolated errors never reach 3 and never cost lock. A
bit slip makes errors recur: in packet traffic nearly every symbol, and in
training sets at least every shifted COM, since a one-bit shift turns the
D10.2 identifiers into valid D21.5 codes. It is caught within a few words
either way (the model's [slip scenarios](HAS.md#architecture-model)). Lock is
taken on the first comma rather than on several: a false lock leads to
errors and a relock, and lock on one comma keeps L0s exit (FTS ordered
sets, one comma each) short. The receiver gets the partner's symbols from
COM in training sets, FTS and SKP ordered sets
[cite: PCIe Base Spec, rev 3.0, §4.2.4.1]
[cite: PCIe Base Spec, rev 3.0, §4.2.7].

## Elastic buffer

The buffer absorbs the offset between `ser_rx_clk_i` and `clk_i` by
adding or removing single SKP symbols inside SKP ordered sets. A SKP
ordered set is a COM followed by SKP symbols; it ends at the first symbol
that is neither. SKP is disparity neutral and its two columns are each
other's complement, so a SKP can be added or removed without disturbing the
decoder, and found without decoding.

- **Storage.** A [`common_cdc_fifo`](../../rtl/common/common_cdc_fifo.md)
  of 16 words (`ADDR_W` 4). A word holds two raw 10-bit symbols, the lock
  tag, the lock-generation bit, a removed-SKP flag per symbol and an
  overflow flag (25 bits).
- **Visible fill.** The read side's fill is twice the words it can see
  (the FIFO's read-side occupancy: written words whose pointer has
  crossed) plus the symbols held in its one-symbol leftover register. The
  FIFO outputs that occupancy (`dst_fill_o`, added in
  [#84](https://github.com/amichai-bd/pcie-factory/issues/84)). Nominal fill
  is 8 symbols.
- **Fill high.** A `clk_i` flop is 1 while the visible fill is 9 or more;
  a [`common_cdc_sync2`](../../rtl/common/common_cdc_sync2.md) brings it to
  the write side.
- **Remove (write side).** While fill high is seen, the write side drops a
  locked SKP that directly follows a COM: the first SKP of a SKP ordered
  set, so at most one per set. A set that arrived with one SKP leaves as a
  bare COM, which the MAC accepts ([PIPE](#decode-and-rxstatus)). The
  symbol after the dropped SKP carries the removed flag. A packing register
  joins the remaining symbols back into two-symbol words, so that cycle may
  write no word.
- **Add (read side).** When the read side sends a locked COM while the
  visible fill is 7 or less, it looks at the next symbol (in its leftover
  register or at the FIFO head, without reading it); if that is a SKP, it
  sends one extra copy of it right after the COM: at most one per set. (A
  copy is the right code: it is sent at the same RD as the original, and
  SKP leaves RD unchanged.) That cycle may read no word. If the next symbol
  is not yet visible, nothing is added for that set.
- **Start.** After `rst_i` the read side reads nothing until the visible
  fill reaches 8; `pipe_rx_valid_o` stays 0. `pipe_reset_ni` does not
  restart this: the read side, its leftover symbol and recentring keep
  running through a PIPE reset, so the buffer never fills up behind it
  ([PIPE control](PIPE_CONTROL.md#reset-and-phystatus)).
- **Unlocked recentring.** Data that is not locked has no SKP to work
  with, yet electrical idle can last milliseconds. While fill high is seen
  and the word is unlocked, the write side drops the whole word, at most
  once per 8 cycles; while the head word is unlocked and the visible fill
  is 7 or less, the read side reads nothing for a cycle, at most once per
  8 cycles. The fill is therefore near 8 whenever lock returns.
- **Lock start.** When lock starts, an unlocked symbol still held in the
  write side's packing register or the read side's leftover register is
  dropped, so the first locked symbols start a word of their own and are
  never paired with an unlocked one (which would delay RxValid by a word).
- **Overflow and underflow.** If the write side finds the FIFO full, the
  word is lost and the next word written carries the overflow flag
  (RxStatus 5): the word that takes the lost symbols' place in the
  stream. If the read side, once started, has fewer than two symbols
  to send, it sends EDB, EDB as K symbols with RxStatus 6. Neither happens
  within the [targets](HAS.md#targets).

### Depth versus clock offset

| Quantity | Value | Why |
| --- | --- | --- |
| Clock offset | at most 600 ppm | Each end's reference is within ±300 ppm [cite: PCIe Base Spec, rev 3.0, §4.2.7]. |
| Drift | at most 0.0006 symbols per symbol | Offset times symbol count. |
| SKP ordered sets | one every 1180 to 1538 symbols; one due inside a packet waits for its end, and all that are due go out back to back [cite: PCIe Base Spec, rev 3.0, §4.2.7] | The `mac` sends them ([phy HAS](../phy/HAS.md#packet-stream)). |
| Correction rate | at least 1/1538 = 650 ppm | One SKP per ordered set, more than the 600 ppm drift. |
| Longest gap | 1538 + 4123 = 5661 symbols | A set falls due one symbol into a 4124-symbol maximum TLP (4122 bytes plus STP and END). |
| Drift over that gap | 5661 x 0.0006 = 3.4 symbols | Followed by at least 3 back-to-back sets, one correction each. |
| Thresholds | add at 7 or less, remove at 9 or more | No dead band: only a fill of exactly 8 is left alone at a SKP set. |
| Sampling jitter | 1 symbol | Words arrive and leave on two unrelated clocks, so the visible fill can read one symbol off between cycles. |
| Lowest fill | 8 - 3.4 - 1 = 3.6, so at least 4 | The fill is 8 when a long gap starts, drifts down 3.4 and reads up to 1 low. |
| Highest fill | 8 + 3.4 + 1 = 12.4, so at most 12 | The same upward. |
| Removal lag | at most 1 extra removal per burst of sets | Fill high reaches the write side two or three RX edges late, and back-to-back sets are two cycles apart. That removal lands after a high excursion, so the fill stays well above the lowest. |
| Underflow margin | 2 symbols | The read side underflows below 2 symbols; the lowest fill is 4. |
| Overflow margin (visible window) | 4 symbols | The highest fill, 12, is within the 16-symbol window the `phy` budget states. |
| Write-side view | at most 12/2 + 3 + 3 = 12 words | Each side sees the other's pointer two or three edges late. The write side therefore counts up to 3 already-read words, and the read side misses up to 3 already-written words. |
| Storage | 16 words (32 symbols) | 12 < 16 leaves 4 words of margin. Eight words would not hold the nominal 4 visible words plus 6 in flight. PIPE's minimum for a half-full buffer at 600 ppm is 7 symbols [cite: PIPE Spec, rev 3.0, §6.13]; the pointer lag is what makes this one larger. |

The model measures, at ±600 ppm with maximum TLPs and both interval
limits over 12 seeds, a visible fill of 4 to 12 symbols and a write-side
view of at most 10 words (scenarios in the
[HAS](HAS.md#architecture-model)).

## Decode and RxStatus

On the `clk_i` side each word read (after any SKP addition) is inverted
when `pipe_rx_polarity_i` is 1 in that cycle, then decoded symbol by symbol
([decoder](8B10B.md#decoder-and-error-classes)). The decoder's RD becomes
unknown at the start of each locked run and when `pipe_rx_polarity_i`
changes. Comma lock and SKP matching do not depend on polarity, so the MAC
finds an inverted lane from the decoded training sets and sets RxPolarity
[cite: PCIe Base Spec, rev 3.0, §4.2.4].

`pipe_rx_valid_o` is 1 when both symbols of the word are locked. Each word
carries one `pipe_rx_status_o` code:

| Code | Meaning | Word that carries it |
| --- | --- | --- |
| 0 | Data OK | Any other word. |
| 1 | One SKP added | The word holding the COM of that SKP ordered set. |
| 2 | One SKP removed | The word holding the COM of that SKP ordered set (the read side sees the removed flag on the symbol after the COM). Exception: if that symbol is not yet visible when the COM leaves (only at a visible fill below 4, outside the targets), the word holding that symbol. |
| 3 | Receiver detected | Only with the receiver-detection PhyStatus pulse ([PIPE control](PIPE_CONTROL.md#receiver-detection)). |
| 4 | 8b/10b code error | The word holding the symbol; that byte is EDB with its K bit set. |
| 5 | Elastic-buffer overflow | The first word written after the lost one. |
| 6 | Elastic-buffer underflow | The EDB, EDB word sent in its place. |
| 7 | Disparity error | The word holding the symbol; the byte is the decoded value. |

When several apply to one word, the first in this order wins: 4, 5, 6, 7,
1, 2, then 0; errors therefore win over SKP changes. Codes 4 and 7 are
reported only while `pipe_rx_valid_o` is 1 (an unlocked word's decode
errors are not reported); codes 3, 5 and 6 do not depend on it.
A code error is always reported as 4, a disparity error as 7 (PIPE also
allows reporting it as 4; this PCS does not). If the COMs of two SKP
ordered sets share a word, one code covers both changes.

These codes, their meaning, the EDB substitutions, the reporting of SKP
changes on the COM's word and the error precedence agree with PIPE
[cite: PIPE Spec, rev 3.0, §5.1] [cite: PIPE Spec, rev 3.0, §6.13]
[cite: PIPE Spec, rev 3.0, §6.14]. PIPE allows removing the only SKP of a
set, leaving a bare COM [cite: PIPE Spec, rev 3.0, §6.13], and asks for
inverted data within 20 PCLK cycles of RxPolarity
[cite: PIPE Spec, rev 3.0, §6.16]; this PCS inverts from the next word.
