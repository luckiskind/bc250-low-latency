# BC-250 Low Latency

A small, reversible Linux installer for two **measured CPU wakeup improvements**
on the AMD BC-250. No Python packages or driver builds are needed.

It holds a **1 microsecond CPU idle-exit latency request** and configures a
**1 nanosecond timer-slack default** for systemd's system and user managers.
Applications can override the timer default. These are operating-system hints;
they do not guarantee real-time response or make RAM accesses take 1ns.

**Tradeoff:** potentially higher idle power and temperature. Gaming FPS, frame
pacing, and input-lag gains have not been measured. Raw GDDR6 access latency and
the BC-250's L3 capacity are unchanged.

## Install

Requires native Linux with systemd **252+**, Python **3.9+** at
`/usr/bin/python3`, and `/dev/cpu_dma_latency`. Tested on one BC-250 running
CachyOS with systemd 262. Other distributions and kernels are not yet validated.

Download the project, review the script, and run:

```sh
git clone https://github.com/luckiskind/bc250-low-latency.git
cd bc250-low-latency
sudo python3 bc250-latency.py install
```

The idle hint becomes active immediately and is enabled for startup. **Reboot
once when convenient** to apply the tighter timer default throughout your
desktop. The installer never reboots, re-executes PID 1, or restarts your desktop.
It does not modify the timer policy of already-running applications.

No need for a Steam launch option once the defaults have taken effect.

## Check

```sh
sudo python3 bc250-latency.py status
# Machine-readable output:
sudo python3 bc250-latency.py status --json
```

Expected: intact installation, service `active`, startup `enabled`, aggregate
idle latency **1us or lower**, and system-manager timer slack **1ns** after reboot.
An independent application's stricter idle request can make the aggregate lower.
Service state and configuration presence do not prove every game uses these hints.

The installed copy also works if you later remove the download folder:

```sh
sudo python3 /usr/local/libexec/bc250-low-latency.py status
```

## Uninstall

```sh
sudo python3 /usr/local/libexec/bc250-low-latency.py uninstall
```

This disables and stops only this project's service and removes only its own
files. Closing its device descriptor releases its idle request; other programs'
requests survive. Reboot when convenient to clear inherited timer settings from
running processes and reload the remaining administrator defaults.

Existing files are never overwritten. If someone edits an installed file,
uninstall refuses before changing anything; preserve or move that file first.
Re-running install is supported for the same version; uninstall before upgrading.

To temporarily release **only** the idle request:

```sh
sudo systemctl stop bc250-low-latency.service
# Resume it:
sudo systemctl start bc250-low-latency.service
```

Stopping the service does not remove timer defaults or disable the next startup.
Other installed latency tools may continue holding their own requests.

## What was measured

On one BC-250, the prior controlled baseline and a subsequent default-on check
gave these averages:

| Metric | Original defaults | Both hints enabled |
| --- | ---: | ---: |
| Timer wakeup delay | 99.988us | 5.758us |
| Deadline to memory-task completion | 188.090us | 92.088us |

The workload wakes every 5ms and performs 512 dependent loads over a randomized
256MiB working set. Each column averages three runs of 1,000 measured wakeups.
The final check used ordinary inherited settings, with no per-test hints.
Both columns used verified transparent hugepages and had no measured page faults.
The comparisons were conducted in the same study, not as a simultaneous control.
Outliers remain: the final check included a 334us wakeup delay.

Separate controlled testing found steady-state random access remained roughly
**144ns** with or without the idle hint. The large percentage reduction above
applies to the synthetic wakeup delay, **not FPS, whole-system latency, or RAM**.
See [measurement details](MEASUREMENTS.md), [sanitized results](results.json),
and the optional [benchmark source](tools/wakeup-bench.c).

## Scope

The project does not change BIOS, CMOS, GDDR6 timings, memory/fabric profiles,
CPU/GPU clocks, voltage, governors, CPU affinity, SMT, GPU limits, or kernel boot
arguments. It leaves transparent hugepage policy alone: the test machine already
had it enabled, and changing it globally is outside this installer's scope.

Installed files:

```text
/etc/systemd/system/bc250-low-latency.service
/etc/systemd/system.conf.d/80-bc250-low-latency.conf
/etc/systemd/user.conf.d/80-bc250-low-latency.conf
/usr/local/libexec/bc250-low-latency.py
/var/lib/bc250-low-latency/manifest.json
```

Other systemd drop-ins or application-specific timer settings may override the
default. Power/temperature effects and game frame times should be checked on
your own board. If a shared power or thermal limit is reached, the extra power
use could offset performance benefits; that possibility has not been measured.

## Development

```sh
python3 -m unittest discover -s tests -v
```

Tests use a temporary directory and simulated service operations. They do not
install anything or change the host's latency policy. Live runtime validation
and its limits are documented in [VALIDATION.md](VALIDATION.md).

## References

- [Linux CPU idle and PM QoS](https://www.kernel.org/doc/html/latest/admin-guide/pm/cpuidle.html)
- [Linux timer slack](https://man7.org/linux/man-pages/man2/PR_SET_TIMERSLACK.2const.html)
- [systemd manager defaults](https://www.freedesktop.org/software/systemd/man/latest/systemd-system.conf.html)

MIT licensed. No firmware utility or toolkit code is bundled.
