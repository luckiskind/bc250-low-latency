# Validation

Validated October 26, 2026 on native BC-250 Linux. Installer version: 1.0.0.

## Installer checks

All **11** unit tests passed on the BC-250 with Python 3.14. They use a temporary
filesystem tree and simulated service commands, and cover:

- Install/uninstall while preserving an unrelated configuration file.
- Repeated installation without replacing the saved manifest.
- Refusal to overwrite existing files or an existing vendor service.
- Cleanup after a simulated service startup failure or unit validation failure.
- Refusal to remove files edited after installation.
- Rejection of a manifest that names another file.
- Recovery from installation interrupted before the service file was written.
- Refusal to follow a symlinked managed parent directory.
- An uninstall with no managed installation present.

Windows also passed the tests applicable there; its symlink test was skipped.
The full Linux run had no skips. The workflow repeats the Linux tests and
compiles the optional C benchmark with `-Wall -Wextra -Werror -O2`.

## Live packaged runtime

The generated service passed `systemd-analyze verify`. A temporary systemd unit
then ran the project's actual `hold` command with the same service restrictions
and properties. The test verified:

- Its process held an open `/dev/cpu_dma_latency` descriptor.
- Its thread timer slack was 1ns and the aggregate idle request was at most 1us.
- Stopping the temporary unit ended its process and closed the descriptor.
- The existing aggregate idle request and previously installed study service
  remained intact.
- The temporary runtime copy was removed.
- Governor/overclock configuration hashes and the board's boot ID were unchanged.

The machine already had the study's always-on 1us request. Therefore the packaged
runtime check did not isolate its effect on the aggregate idle limit. Independent
request preservation had also been tested earlier using a separate 100us request
while stopping/starting the original study service.

See [runtime validation data](runtime-validation.json) and
[measurement details](MEASUREMENTS.md). The runtime data's SHA256 identifies the
exact script tested.

## Remaining limits

The project's permanent install/uninstall lifecycle has been exercised with a
temporary filesystem and simulated service commands. The packaged runtime and
generated unit were tested live, but the complete permanent installer was not
deployed over the board's existing study installation. No subsequent boot was
performed. Other distributions, systemd versions, board configurations, and
actual games remain untested.

The published wakeup results came from the earlier equivalent software settings,
not from a post-reboot run of this reusable installer. They establish an effect
for one workload on one board, not a universal latency or FPS improvement.
