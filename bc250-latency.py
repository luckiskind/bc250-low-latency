#!/usr/bin/env python3
"""Install, inspect, or remove the BC-250's measured Linux wakeup hints."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import stat
import struct
import subprocess
import sys
import time

VERSION = '1.0.0'
UNIT = 'bc250-low-latency.service'
STATE = Path('/var/lib/bc250-low-latency')
HELPER = Path('/usr/local/libexec/bc250-low-latency.py')
SERVICE = Path('/etc/systemd/system') / UNIT
SYSTEM_CONF = Path('/etc/systemd/system.conf.d/80-bc250-low-latency.conf')
USER_CONF = Path('/etc/systemd/user.conf.d/80-bc250-low-latency.conf')
DEVICE = Path('/dev/cpu_dma_latency')
DROPIN = b'[Manager]\nTimerSlackNSec=1\n'


def unit_text():
    return f'''[Unit]
Description=BC-250 CPU wakeup latency hint
After=systemd-udev-trigger.service
ConditionPathExists=/dev/cpu_dma_latency

[Service]
Type=exec
ExecStart=/usr/bin/python3 {HELPER} hold
User=root
TimerSlackNSec=1
Restart=on-failure
RestartSec=3s
TimeoutStopSec=5s
NoNewPrivileges=yes
CapabilityBoundingSet=
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
PrivateTmp=yes
DevicePolicy=closed
DeviceAllow=/dev/cpu_dma_latency rw

[Install]
WantedBy=multi-user.target
'''.encode()


def command(*args, check=True):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=30)
    if check and result.returncode:
        raise RuntimeError(f"{' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def require_root():
    if platform.system() != 'Linux' or os.geteuid() != 0:
        raise RuntimeError('Use sudo on the Linux BC-250 for install/uninstall.')


def preflight():
    require_root()
    if not Path('/run/systemd/system').is_dir():
        raise RuntimeError('A running systemd system manager is required.')
    for name in ('systemctl', 'systemd-analyze'):
        if not shutil.which(name):
            raise RuntimeError(f'{name} is required.')
    version = command('systemctl', '--version').splitlines()[0].split()[1]
    if int(version) < 252:
        raise RuntimeError('systemd 252 or newer is required by this installer.')
    if not Path('/usr/bin/python3').is_file():
        raise RuntimeError('/usr/bin/python3 is required.')
    if not stat.S_ISCHR(DEVICE.stat().st_mode):
        raise RuntimeError('/dev/cpu_dma_latency is not a character device.')
    read_qos()


def read_qos():
    fd = os.open(DEVICE, os.O_RDWR | os.O_CLOEXEC)
    try:
        data = os.read(fd, 4)
        if len(data) != 4:
            raise RuntimeError('Incomplete CPU latency readback.')
        return struct.unpack('i', data)[0]
    finally:
        os.close(fd)


def hold():
    # A descriptor owns just this request. Never restore a saved aggregate:
    # that would overwrite independent requests made by other applications.
    signals = {signal.SIGTERM, signal.SIGINT}
    signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    fd = os.open(DEVICE, os.O_RDWR | os.O_CLOEXEC)
    try:
        if os.write(fd, struct.pack('i', 1)) != 4:
            raise RuntimeError('Incomplete CPU idle latency request.')
        print('Holding CPU idle-exit latency request: 1 microsecond', flush=True)
        signal.sigwait(signals)
    finally:
        os.close(fd)


def payloads():
    return {HELPER: Path(__file__).resolve().read_bytes(), SERVICE: unit_text(),
            SYSTEM_CONF: DROPIN, USER_CONF: DROPIN}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_exclusive(path, data, mode=0o644):
    # Refuse symlinked managed paths or parent directories. No existing file
    # is overwritten, including an unrelated administrator's configuration.
    for parent in path.parents:
        if parent.is_symlink():
            raise RuntimeError(f'Refusing symlinked parent: {parent}')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def load_manifest():
    if STATE.is_symlink() or (STATE / 'manifest.json').is_symlink():
        raise RuntimeError('Refusing symlinked installation state.')
    data = json.loads((STATE / 'manifest.json').read_text())
    expected = {str(p) for p in (HELPER, SERVICE, SYSTEM_CONF, USER_CONF)}
    if set(data['files']) != expected:
        raise RuntimeError('Installation manifest contains unexpected paths.')
    for name, saved in data['files'].items():
        path = Path(name)
        if path.is_symlink():
            raise RuntimeError(f'Refusing symlinked managed file: {path}')
        if path.exists() and digest(path.read_bytes()) != saved:
            raise RuntimeError(f'File edited since installation; preserve or move it first: {path}')
    return data


def start_service():
    command('systemctl', 'daemon-reload')
    command('systemctl', 'enable', '--now', UNIT)
    for _ in range(30):
        if command('systemctl', 'is-active', UNIT, check=False) == 'active' and read_qos() <= 1:
            return
        time.sleep(0.1)
    raise RuntimeError('Idle request did not become active; inspect the service journal.')


def install():
    preflight()
    if STATE.exists() or STATE.is_symlink():
        data = load_manifest()
        if data['version'] != VERSION or not all(Path(p).is_file() for p in data['files']):
            raise RuntimeError('Different or incomplete installation; uninstall it first.')
        start_service()
        print('Already installed; CPU idle request is active.')
        return
    files = payloads()
    for path in files:
        if path.exists() or path.is_symlink():
            raise RuntimeError(f'Existing file left untouched: {path}')
    # Refuse an unrelated same-named vendor unit, even outside /etc.
    if command('systemctl', 'show', UNIT, '-p', 'LoadState', '--value', check=False) != 'not-found':
        raise RuntimeError(f'An existing {UNIT} is already registered.')
    for parent in (STATE, *STATE.parents):
        if parent.is_symlink():
            raise RuntimeError(f'Refusing symlinked installation state: {parent}')
    STATE.mkdir(mode=0o700)
    created = []
    try:
        manifest = {'version': VERSION, 'files': {str(p): digest(b) for p, b in files.items()}}
        write_exclusive(STATE / 'manifest.json', json.dumps(manifest, indent=2).encode(), 0o600)
        for path, content in files.items():
            write_exclusive(path, content)
            created.append(path)
        command('systemd-analyze', 'verify', str(SERVICE))
        start_service()
    except Exception:
        # These paths were absent before this transaction. Retain any file
        # changed concurrently and its manifest rather than deleting it.
        command('systemctl', 'disable', '--now', UNIT, check=False)
        retained = []
        for path in created:
            if not path.is_symlink() and path.exists() and digest(path.read_bytes()) == digest(files[path]):
                path.unlink()
            elif path.exists() or path.is_symlink():
                retained.append(path)
        if not retained:
            (STATE / 'manifest.json').unlink(missing_ok=True)
            STATE.rmdir()
        command('systemctl', 'daemon-reload', check=False)
        raise
    print('Installed. CPU idle request is active now and enabled for startup.')
    print('Reboot once when convenient to apply the 1ns timer default throughout your desktop.')
    print('No automatic reboot or service-manager re-execution was performed.')
    print('Tradeoff: potentially higher idle power and temperature. Gaming gains are unverified.')


def uninstall():
    require_root()
    if not STATE.exists() and not STATE.is_symlink():
        print('No installation managed by this project was found.')
        return
    data = load_manifest()  # Complete validation before stopping or removing anything.
    if command('systemctl', 'show', UNIT, '-p', 'LoadState', '--value', check=False) != 'not-found':
        command('systemctl', 'disable', '--now', UNIT)
    for name in data['files']:
        Path(name).unlink(missing_ok=True)
    (STATE / 'manifest.json').unlink()
    STATE.rmdir()
    command('systemctl', 'daemon-reload')
    print('Uninstalled; only this service\'s CPU idle request has been released.')
    print('Reboot when convenient to clear inherited timer defaults from running processes.')
    print('Existing administrator configuration and other applications\' requests are preserved.')


def status(as_json=False):
    def query(*args):
        try:
            return command(*args, check=False) or 'unavailable'
        except (OSError, RuntimeError):
            return 'unavailable'
    try:
        qos = read_qos()
    except OSError:
        qos = 'unavailable (try sudo for device readback)'
    try:
        manifest = load_manifest()
        installed = all(Path(p).is_file() for p in manifest['files'])
    except (OSError, ValueError, KeyError, RuntimeError):
        installed = False
    report = {
        'version': VERSION, 'managed_installation_intact': installed,
        'service_active': query('systemctl', 'is-active', UNIT),
        'startup_enabled': query('systemctl', 'is-enabled', UNIT),
        'aggregate_idle_latency_us': qos,
        'system_manager_timer_slack_ns': query('systemctl', 'show', '-p', 'TimerSlackNSec', '--value'),
        'system_timer_dropin_present': SYSTEM_CONF.is_file(),
        'user_timer_dropin_present': USER_CONF.is_file(),
        'note': 'Timer default targets 1ns. Existing processes may need a reboot; applications can override it.',
    }
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        for key, value in report.items():
            print(f'{key.replace("_", " ")}: {value}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=VERSION)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('install', help='Enable the idle hint now and save both startup defaults')
    check = commands.add_parser('status', help='Show installed files, service state and effective settings')
    check.add_argument('--json', action='store_true')
    commands.add_parser('uninstall', help='Remove only this project\'s settings')
    commands.add_parser('hold', help='Hold the idle request (used by the background service)')
    args = parser.parse_args()
    try:
        if args.action == 'status':
            status(args.json)
        else:
            {'install': install, 'uninstall': uninstall, 'hold': hold}[args.action]()
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'bc250-low-latency: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
