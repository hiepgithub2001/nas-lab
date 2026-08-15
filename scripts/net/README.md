# Network scripts

Scripts here target **`admin-pc-1`** (the Windows PC / WSL host), not the NAS.
They live in this repo for lookup and version history; they must be *run on Windows*.

## `Fix-Ethernet.ps1`

Diagnoses and repairs the Realtek 2.5GbE link silently negotiating **10 Mbps**.

### Symptom

Everything looks healthy — `Connected`, full duplex, zero error counters — but the
whole machine is on a 1.25 MB/s pipe. It presents exactly like an ISP outage or a
broken NAS service, so it burns time at the wrong layer.

### Why it happens

Ethernet autonegotiation runs **once**, at link-up, and then latches. There is no
background retry at a higher speed. If the Realtek PHY negotiates while in a
low-power state it advertises only its cheapest mode — 10BASE-T full duplex, which
needs the least signal amplitude and none of the DSP/echo-cancellation cost of
1000BASE-T — and the link stays there. The result is a *valid, error-free* 10 Mbps
link, which is why every health indicator reads green.

Four driver properties can push the PHY into that state. The script enforces all four:

| Property | Wanted | Path it affects |
|---|---|---|
| `WolShutdownLinkSpeed` | `2` (Not Speed Down) | shutdown / S5 |
| `EnableGreenEthernet` | `0` | runtime + resume |
| `PowerSavingMode` | `0` | runtime + resume |
| `GigaLite` | `0` | runtime + resume |

Fixing only `WolShutdownLinkSpeed` is **not** sufficient — that was tried on
2026-08-12 and the fault returned on 2026-08-15 with the setting still correct.
The two groups are different code paths with an identical symptom.

### A warm reboot does not fix it

The NIC keeps power via the **+5VSB** standby rail so Wake-on-LAN works. Through a
warm reboot the PHY never loses power, the link never drops, autonegotiation never
re-runs, and the stale 10 Mbps result rides straight through. Only a real
link-down/up recovers it — which is what `Restart-NetAdapter` forces.

### Not a cable fault

A damaged cable with only two good pairs negotiates **100 Mbps**, because both
100BASE-TX and 10BASE-T use two pairs. Physical damage does not produce 10 Mbps.
A clean 10 Mbps full-duplex link with zero errors is a *negotiated* choice.

## Usage

Run on Windows. Copy to the PC first, or keep the working copy at
`C:\Users\Admin\Scripts\Fix-Ethernet.ps1`.

```powershell
# read-only health report - no admin rights needed
.\Fix-Ethernet.ps1 -Check

# diagnose, and repair automatically if anything is wrong (prompts for UAC)
.\Fix-Ethernet.ps1

# skip the ~8s throughput test
.\Fix-Ethernet.ps1 -Check -SkipSpeedTest

# force the repair even when nothing looks wrong
.\Fix-Ethernet.ps1 -Fix
```

What it checks, cheapest layer first:

1. **Link speed** — what the NIC actually negotiated, vs `-MinLinkMbps` (default 1000)
2. **Config drift** — the four properties above, against the table
3. **Throughput** — real measured download, time-boxed to ~8s

Repair = reapply the four properties, then `Restart-NetAdapter` to force a fresh
autonegotiation, then re-verify and report before/after.

Exit code `0` = healthy, `1` = problem found (`-Check`) or repair failed.
Log: `%LOCALAPPDATA%\Fix-Ethernet.log`.

### Expected healthy output

```
  Adapter    : Ethernet  -  Realtek Gaming 2.5GbE Family Controller
  Status     : Up   Duplex: Full
  Link speed : 1 Gbps   (threshold 1000 Mbps)
  Errors     : 0 rx+tx packet errors, 0 discarded

  Power-saving config: all 4 properties correct
  Throughput : 199.7 Mbps (23.8 MB/s)  = 20% of link

  VERDICT: healthy. Nothing to do.
```

`1 Gbps` on a 2.5GbE card is the **router/switch port ceiling**, not a fault — the
Xiaomi router at `192.168.31.1` has gigabit ports. Throughput at ~200 Mbps is the
WAN/ISP rate; it is well under the link speed, so the link is not the bottleneck.

## When it can come back

The settings are written to the registry
(`HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}\0002`)
and survive reboots, sleep, and shutdown. The realistic way they revert is a
**Realtek driver update** — Windows Update ships these, and a driver reinstall
rewrites that class key back to defaults. That is exactly the drift the script
detects, so after any driver update, run `-Check`.

## Notes for future edits

Three things that are easy to get wrong and were hit while writing this:

- PowerShell hash keys are **case-insensitive** — `Mbps` and `MBps` collide as
  duplicate keys and fail at parse time.
- `Get-ConfigDrift` returns `, $drift` so an empty result stays an empty array.
  Callers must assign it directly; wrapping in `@()` turns the empty array into a
  1-element array containing an empty array, producing a false drift positive.
- PowerShell 5.1 negotiates **TLS 1.0** by default, which Cloudflare refuses, and
  `speed.cloudflare.com` **403s any `?bytes=` at or above 100 MB** — hence the 90 MB
  chunk loop.
