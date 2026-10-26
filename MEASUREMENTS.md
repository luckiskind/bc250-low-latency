# Measurement details

Tested October 26, 2026 on one AMD BC-250, BIOS P3.00, 8 Zen 2 cores / 16
threads, two 4MiB L3 groups, and shared GDDR6 at the existing 1750MHz setting.
The CPU had an existing 3.75GHz configuration. Neither those clocks nor memory
timings were changed by these software tests. Kernel:
`7.2.8-1.219-cachyos-bore-bc250`; systemd 262; `acpi_idle` / `menu` CPU idle policy.

The existing C2 and C3 states advertised 350us and 400us exit latency. The
descriptor-owned 1us PM QoS request excludes those states while allowing C1.
It affects idle-state selection across the machine; it does not pin CPUs at 100%
load or directly force a CPU frequency. The timer default reduces intentional
timer coalescing, but scheduler delays and other sources of jitter remain.

## Wakeup workload

The original `wakeup-bench.c` source is included under `tools/`:

- 256MiB anonymous mapping, aligned to 2MiB, `MADV_HUGEPAGE`.
- A deterministic random Hamiltonian cycle across 64-byte nodes.
- 5ms absolute sleeps with `clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME)`.
- 512 serial dependent loads per wakeup; 50 warmups, then 1,000 measurements.
- Ordinary scheduling policy, pinned to logical CPU 8.
- Hugepage backing verified externally through `smaps_rollup` during each run.
- No competing storage probe; measured regions had zero major or minor faults.

The earlier 12-run factorial experiment interleaved three repeats of all four
combinations: original vs 1ns timer slack, and no added vs 1us idle request.
Before this work, the aggregate idle request was 12000us and timer slack 50000ns.

| Hints | Mean wakeup delay (us) | Mean deadline-to-finish (us) |
| --- | ---: | ---: |
| Original defaults | 99.987933 | 188.090400 |
| Timer only | 49.621433 | 142.376000 |
| Idle only | 54.932667 | 141.209833 |
| Both | 5.577733 | 93.621533 |

After those results were verified, both settings were enabled persistently on
the test board. Three further 1,000-wakeup runs used a fresh ordinary SSH session,
with no launcher and with the benchmark's own hints disabled. Their average wakeup
delay was **5.758167us**, and average deadline-to-finish was **92.088333us**.
Per-run records are in `results.json`. These follow-up runs were not interleaved
with a newly repeated control. The largest wakeup delay in them was 334.101us.

The board was not rebooted. System/user service-manager children and fresh SSH
children were checked for inherited 1ns slack. Startup enablement was verified,
but a subsequent boot has not been tested. The original installation applied
the defaults live; this reusable installer deliberately asks for one normal
reboot instead of changing existing processes or re-executing service managers.

## What this does not establish

Separate repeated steady-state pointer-chase tests stayed around 144ns with or
without the idle hint. The workload above includes wakeup and an intermittent
load batch; its timings must not be described as isolated DRAM timings.

Transparent hugepages improved the forced-4KiB control, but ordinary allocations
already received hugepages on this machine. No additional gain from changing
the default allocator or reserving explicit hugepages was found. This installer
does not change global hugepage policy.

No game, FPS, 1% low, frame-time, end-to-end input latency, energy, or sustained
GPU power-budget comparison has been performed. Results are specific to one
board and workload and do not guarantee a fixed upper latency bound.

## Optional reproduction

```sh
cc -O2 -o wakeup-bench tools/wakeup-bench.c
# As an account allowed to open /dev/cpu_dma_latency, or with sudo:
taskset -c 8 ./wakeup-bench -1 1000 0
```

Use an available logical CPU if CPU 8 is absent. The arguments above do not add
an idle hint or change timer slack; they measure inherited settings. A normal
user may lack permission to open the device. Do not change device permissions
just for this test. If using sudo, use it consistently for both comparisons.

Collect comparable runs before install and after a normal reboot following
install, keeping temperature, background work, clocks, page backing, and CPU
placement comparable. Inspect backing with `/proc/PID/smaps_rollup` while it runs;
the tool does not itself prove hugepage backing. Its output alone is insufficient
to claim the same result if those controls differ.
