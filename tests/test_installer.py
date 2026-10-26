import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('latency', Path(__file__).resolve().parents[1] / 'bc250-latency.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.calls = []
        self.started = False
        self.fail_start = False
        patches = {
            'STATE': root / 'var/lib/bc250-low-latency',
            'HELPER': root / 'usr/local/libexec/helper.py',
            'SERVICE': root / 'etc/systemd/system' / app.UNIT,
            'SYSTEM_CONF': root / 'etc/systemd/system.conf.d/80-latency.conf',
            'USER_CONF': root / 'etc/systemd/user.conf.d/80-latency.conf',
        }
        self.addCleanup(mock.patch.stopall)
        mock.patch.multiple(app, **patches).start()
        app.STATE.parent.mkdir(parents=True)
        mock.patch.object(app, 'preflight').start()
        mock.patch.object(app, 'require_root').start()
        mock.patch.object(app, 'read_qos', return_value=1).start()
        mock.patch.object(app, 'command', side_effect=self.command).start()
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def command(self, *args, check=True):
        self.calls.append(args)
        if args[:2] == ('systemctl', 'show'):
            return 'loaded' if app.SERVICE.exists() else 'not-found'
        if args[:3] == ('systemctl', 'enable', '--now'):
            if self.fail_start:
                raise RuntimeError('Simulated service startup failure')
            self.started = True
        if args[:3] == ('systemctl', 'disable', '--now'):
            self.started = False
        if args[:2] == ('systemctl', 'is-active'):
            return 'active' if self.started else 'inactive'
        return ''

    def test_install_remove_preserves_unrelated_configuration(self):
        unrelated = app.SYSTEM_CONF.parent / '10-existing.conf'
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text('[Manager]\nDefaultTimeoutStopSec=10s\n')
        before = unrelated.read_bytes()
        app.install()
        self.assertTrue(self.started)
        self.assertEqual(app.SYSTEM_CONF.read_bytes(), app.DROPIN)
        app.uninstall()
        self.assertFalse(self.started)
        self.assertFalse(app.STATE.exists())
        self.assertFalse(app.HELPER.exists())
        self.assertEqual(unrelated.read_bytes(), before)

    def test_install_is_idempotent(self):
        app.install()
        before = (app.STATE / 'manifest.json').read_bytes()
        app.install()
        self.assertEqual(before, (app.STATE / 'manifest.json').read_bytes())
        self.assertTrue(self.started)

    def test_refuses_existing_file_without_mutation(self):
        app.SYSTEM_CONF.parent.mkdir(parents=True)
        app.SYSTEM_CONF.write_text('administrator settings')
        with self.assertRaisesRegex(RuntimeError, 'Existing file'):
            app.install()
        self.assertEqual(app.SYSTEM_CONF.read_text(), 'administrator settings')
        self.assertFalse(app.STATE.exists())
        self.assertFalse(self.started)

    def test_refuses_existing_vendor_service(self):
        with mock.patch.object(app, 'command', return_value='loaded'):
            with self.assertRaisesRegex(RuntimeError, 'already registered'):
                app.install()
        self.assertFalse(app.STATE.exists())

    def test_service_failure_rolls_back_created_files(self):
        self.fail_start = True
        with self.assertRaisesRegex(RuntimeError, 'startup failure'):
            app.install()
        for path in (app.HELPER, app.SERVICE, app.SYSTEM_CONF, app.USER_CONF, app.STATE):
            self.assertFalse(path.exists(), path)
        self.assertFalse(self.started)

    def test_uninstall_refuses_edited_file_before_stopping(self):
        app.install()
        app.SYSTEM_CONF.write_text('administrator edited this file')
        self.calls.clear()
        with self.assertRaisesRegex(RuntimeError, 'File edited'):
            app.uninstall()
        self.assertTrue(self.started)
        self.assertTrue(app.HELPER.exists())
        self.assertFalse(any('disable' in call for call in self.calls))

    def test_tampered_manifest_cannot_remove_another_file(self):
        app.install()
        protected = Path(self.temp.name) / 'important.txt'
        protected.write_text('retain me')
        manifest = app.json.loads((app.STATE / 'manifest.json').read_text())
        manifest['files'][str(protected)] = app.digest(protected.read_bytes())
        (app.STATE / 'manifest.json').write_text(app.json.dumps(manifest))
        with self.assertRaisesRegex(RuntimeError, 'unexpected paths'):
            app.uninstall()
        self.assertEqual(protected.read_text(), 'retain me')
        self.assertTrue(self.started)

    def test_uninstall_when_absent_does_not_touch_services(self):
        app.uninstall()
        self.assertEqual(self.calls, [])

    def test_recovers_install_interrupted_before_service_was_written(self):
        files = app.payloads()
        app.STATE.mkdir()
        manifest = {'version': app.VERSION, 'files': {str(p): app.digest(b) for p, b in files.items()}}
        (app.STATE / 'manifest.json').write_text(app.json.dumps(manifest))
        app.write_exclusive(app.HELPER, files[app.HELPER])
        app.uninstall()
        self.assertFalse(app.HELPER.exists())
        self.assertFalse(app.STATE.exists())

    def test_unit_validation_failure_cleans_up_without_starting(self):
        original = self.command
        def fail_verify(*args, check=True):
            if args[0] == 'systemd-analyze':
                raise RuntimeError('Simulated unit validation failure')
            return original(*args, check=check)
        with mock.patch.object(app, 'command', side_effect=fail_verify):
            with self.assertRaisesRegex(RuntimeError, 'unit validation failure'):
                app.install()
        self.assertFalse(app.STATE.exists())
        self.assertFalse(app.SERVICE.exists())
        self.assertFalse(self.started)

    def test_symlinked_managed_parent_is_rejected(self):
        target = Path(self.temp.name) / 'other'
        target.mkdir()
        app.HELPER.parent.parent.mkdir(parents=True)
        try:
            app.HELPER.parent.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest('Host cannot create symlinks')
        with self.assertRaisesRegex(RuntimeError, 'symlinked parent'):
            app.install()
        self.assertEqual(list(target.iterdir()), [])
        self.assertFalse(app.STATE.exists())


if __name__ == '__main__':
    unittest.main()
