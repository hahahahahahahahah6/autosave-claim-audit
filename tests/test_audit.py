import contextlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from autosave_claim_audit.cli import audit, claims, main, POSITIVE, NEGATIVE

TIMER = 'import threading\nthreading.Timer(1, save).start()\n'
CHANGE = 'def on_modified(event):\n    state.save()\n'
POS = 'documented-autosave-claim-false'
NEG = 'documented-no-autosave-claim-false'


class Fixtures(unittest.TestCase):
    def run_fixture(self, doc, source=None, path='README.md', args=()):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(doc, encoding='utf-8')
            if source is not None:
                (root / 'app.py').write_text(source, encoding='utf-8')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([directory, '--json', *args])
            return code, json.loads(output.getvalue())

    def check(self, doc, source=None, rule=None, severity='HIGH', path='README.md', args=()):
        code, result = self.run_fixture(doc, source, path, args)
        expected = [] if rule is None else [{'rule': rule, 'severity': severity, 'path': path}]
        self.assertEqual(result['findings'], expected)
        self.assertEqual(code, 2 if rule and (severity == 'HIGH' or 'HIGH' not in args) else 0)
        return result

    def test_good_timer(self): self.check('autosaves your work', TIMER)
    def test_good_change(self): self.check('automatically saves', CHANGE)
    def test_good_config(self): self.check('autosave support', 'autosave = True\nsave()')
    def test_good_negative(self): self.check('does not autosave', 'x = 1')
    def test_bad_manual(self): self.check('autosaves your work', 'def save(): pass\nsave()', POS)
    def test_bad_negative(self):
        result = self.check('does not autosave', TIMER, NEG)
        self.assertEqual(result['claims'], ['NOAUTOSAVE'])
    def test_bad_never(self): self.check('never autosaves', CHANGE, NEG)
    def test_no_source_positive(self): self.check('autosaves', rule=POS)
    def test_no_source_negative(self): self.check('does not autosave')
    def test_fenced(self): self.check('```text\nautosaves\n```\n~~~\nauto save\n~~~')
    def test_inline(self): self.check('It `autosaves your work`.', rule=POS)
    def test_wrapped(self): self.check('It automatically\nsaves your work.', rule=POS)
    def test_testonly(self):
        self.check('autosaves', rule=POS, severity='MEDIUM', path='docs/tests/fixture.md')
        self.check('autosaves', rule=POS, severity='MEDIUM', path='docs/tests/fixture.md', args=('--fail-on', 'HIGH'))
        self.check('autosaves', rule=POS, severity='MEDIUM', path='docs/tests/fixture.md', args=('--fail-on', 'MEDIUM'))
    def test_bare_nouns(self): self.check('A save, saving and manual save.')
    def test_not_evidence(self): self.check('autosaves', 'import atexit\ndef save(): pass\natexit.register(save)', POS)
    def test_self_audit(self):
        root = Path(__file__).resolve().parents[1]
        result = audit(root)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['claims'], [])
        readme = (root / 'README.md').read_text()
        prose = re.sub(r'```.*?```', '', readme, flags=re.S)
        self.assertNotIn('autosave', prose.lower())
        self.assertIsNone(re.search(r'\bpython\s+(?:-m|\S+\.py)', readme))
    def test_json(self): self.assertEqual(self.run_fixture('plain')[1]['status'], 'PASS')
    def test_usage(self):
        for args in (['--unknown'], ['--fail-on', 'LOW'], ['/nonexistent/path']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                main(args)
            self.assertEqual(raised.exception.code, 1)
    def test_version(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            main(['--version'])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), '0.1.0')
    def test_all_phrases(self):
        for phrase in POSITIVE:
            with self.subTest(phrase=phrase): self.assertEqual(claims(phrase), {'AUTOSAVE'})
        for phrase in NEGATIVE:
            with self.subTest(phrase=phrase): self.assertEqual(claims(phrase), {'NOAUTOSAVE'})
    def test_normalization_and_boundaries(self):
        self.assertEqual(claims('AUTO-SAVES\n your work'), {'AUTOSAVE'})
        self.assertEqual(claims('autosaver auto saver'), set())
        self.assertEqual(claims('automatically\n\nsaves'), set())
    def test_evidence_variants(self):
        sources = [
            'from threading import Timer as T\nT(1, state.save)',
            'import threading as t\nt.Timer(interval=1, function=save)',
            'import sched\ns=sched.scheduler()\ns.enter(1, 1, save)',
            'import sched\ns=sched.scheduler()\ns.enterabs(time=1, priority=1, action=state.save)',
            'import asyncio\nasync def worker():\n await asyncio.sleep(1)\n save()',
            'import time\ndef worker():\n while True:\n  time.sleep(1)\n  save()',
            'import time\ndef worker():\n for i in range(3):\n  time.sleep(1)\n  save()',
            'register(callback=save)', 'register(on_change=state.save)',
            'register(on_save=save)', 'register(handler=save)',
            'auto_save: bool = True\nsave()', 'config={"autosave": True}\nsave()',
        ]
        for source in sources:
            with self.subTest(source=source): self.check('autosaves', source)
    def test_non_evidence_variants(self):
        sources = ['autosave = True', 'autosave = False\nsave()',
                   'print("autosave")', 'import subprocess\nsubprocess.run(["save"])',
                   '"""save()"""', '# save()\nx=1', 'json.dump(state, file)',
                   'def on_modified(e):\n dump()',
                   'import time\ndef worker():\n time.sleep(1)\n save()',
                   'def worker():\n def on_manual():\n  save()']
        for source in sources:
            with self.subTest(source=source): self.check('autosaves', source, POS)
    def test_project_wide_flag_and_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'README.md').write_text('autosaves')
            (root / 'flags.py').write_text('autosave=True')
            (root / 'writer.py').write_text('save()')
            self.assertEqual(audit(root)['findings'], [])
            self.assertEqual(audit(root, ['writer.py'])['findings'][0]['rule'], POS)
            (root / 'writer.py').unlink()
            (root / 'tests').mkdir()
            (root / 'tests' / 'timer.py').write_text(TIMER)
            (root / 'test_timer.py').write_text(TIMER)
            self.assertEqual(audit(root)['findings'][0]['rule'], POS)
    def test_never_reads_environment_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').touch()
            original = Path.read_text
            def guarded(path, *args, **kwargs):
                self.assertNotEqual(path.name, '.env')
                return original(path, *args, **kwargs)
            with patch.object(Path, 'read_text', guarded):
                self.assertEqual(audit(root)['findings'], [])
    def test_deduplication_and_verbose(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'README.md').write_text('autosaves. autosaves your work.')
            (root / 'docs').mkdir()
            (root / 'docs' / 'extra.md').write_text('autosaves')
            output = io.StringIO()
            with contextlib.redirect_stdout(output): code = main([directory, '--verbose'])
            self.assertEqual(code, 2)
            self.assertEqual(output.getvalue().splitlines(), ['FAIL', POS + ' HIGH README.md'])
    def test_module_entry(self):
        result = subprocess.run([sys.executable, '-m', 'autosave_claim_audit', '--version'], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, '0.1.0'))
    def test_invalid_source(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'bad.py').write_text('def ???')
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                main([directory])
            self.assertEqual(raised.exception.code, 1)


if __name__ == '__main__':
    unittest.main()
