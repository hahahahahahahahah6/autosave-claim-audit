"""Static documentation and Python AST analysis; audited files are read only."""
import argparse
import ast
import fnmatch
import json
from pathlib import Path
import re
import sys

from . import __version__

POSITIVE = ('autosave', 'auto save', 'autosaves', 'auto saves',
            'automatically saves', 'saves automatically', 'automatic saving',
            'autosave support', 'auto save support', 'supports autosave',
            'supports auto save', 'autosaves your work', 'auto saves your work',
            'automatically saves your work', 'saves your work automatically',
            'autosaves changes', 'auto saves changes', 'saves changes automatically',
            'automatically saves changes')
NEGATIVE = ('does not autosave', 'does not auto save', 'never autosaves',
            'never auto saves', 'no autosave', 'no auto save',
            'does not save automatically', 'does not automatically save',
            'no automatic saving', 'must save manually', 'you must save manually',
            'manual saving only')


def pattern(phrases):
    return re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(p) for p in
                      sorted(phrases, key=len, reverse=True)) + r')(?!\w)')


POS_RE, NEG_RE = pattern(POSITIVE), pattern(NEGATIVE)


def claims(text):
    paragraphs, lines = [], []
    fence = None
    for line in text.splitlines():
        match = re.match(r'^\s{0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = None
            continue
        if match:
            paragraphs.append(' '.join(lines))
            lines = []
            fence = match[1]
        elif not line.strip():
            paragraphs.append(' '.join(lines))
            lines = []
        else:
            lines.append(line)
    paragraphs.append(' '.join(lines))
    result = set()
    for paragraph in paragraphs:
        normalized = re.sub(r'\s+', ' ', paragraph.lower().replace('-', ' '))
        if NEG_RE.search(normalized):
            result.add('NOAUTOSAVE')
        if POS_RE.search(NEG_RE.sub(' ', normalized)):
            result.add('AUTOSAVE')
    return result


def name(node):
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ''


def dotted(node):
    if isinstance(node, ast.Attribute):
        return dotted(node.value) + '.' + node.attr
    return name(node)


def save(node):
    return 'save' in name(node).lower()


def evidence(trees):
    any_save, flag, automatic = False, False, False
    for tree in trees:
        # Resolve ordinary imports and aliases for timer/sleep APIs.
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    aliases[item.asname or item.name] = item.name
            elif isinstance(node, ast.ImportFrom):
                for item in node.names:
                    aliases[item.asname or item.name] = (node.module or '') + '.' + item.name
        def api(node):
            parts = dotted(node).split('.')
            return '.'.join([aliases.get(parts[0], parts[0])] + parts[1:])
        schedulers = set()
        for assignment in ast.walk(tree):
            if isinstance(assignment, (ast.Assign, ast.AnnAssign)) and isinstance(assignment.value, ast.Call) and api(assignment.value.func) == 'sched.scheduler':
                targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
                schedulers.update(dotted(t) for t in targets)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                any_save |= save(node.func)
                target = api(node.func)
                index = 1 if target == 'threading.Timer' else 2 if isinstance(node.func, ast.Attribute) and node.func.attr in ('enter', 'enterabs') and (dotted(node.func.value) in schedulers or (isinstance(node.func.value, ast.Call) and api(node.func.value.func) == 'sched.scheduler')) else None
                if index is not None:
                    callback = node.args[index] if len(node.args) > index else next(
                        (k.value for k in node.keywords if k.arg == ('function' if index == 1 else 'action')), None)
                    automatic |= callback is not None and save(callback)
                automatic |= any(k.arg in ('on_change', 'on_save', 'callback', 'handler') and save(k.value) for k in node.keywords)
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                flag |= any(name(t) in ('autosave', 'auto_save') for t in targets) and isinstance(node.value, ast.Constant) and node.value.value is True
            if isinstance(node, ast.Dict):
                flag |= any(isinstance(k, ast.Constant) and k.value in ('autosave', 'auto_save') and isinstance(v, ast.Constant) and v.value is True for k, v in zip(node.keys, node.values))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Nested definitions do not execute as part of the enclosing function.
                def scope_nodes(root):
                    yield root
                    for child in ast.iter_child_nodes(root):
                        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                            yield from scope_nodes(child)
                nodes = list(scope_nodes(node))
                saves = any(isinstance(n, ast.Call) and save(n.func) for n in nodes)
                automatic |= saves and any(part in node.name.lower() for part in ('on_change', 'on_modified', 'on_created', 'on_event'))
                automatic |= saves and any(isinstance(n, ast.Await) and isinstance(n.value, ast.Call) and api(n.value.func) == 'asyncio.sleep' for n in nodes)
                for loop in (n for n in nodes if isinstance(n, (ast.For, ast.While, ast.AsyncFor))):
                    body_nodes = [n for stmt in loop.body for n in scope_nodes(stmt)]
                    automatic |= any(isinstance(n, ast.Call) and save(n.func) for n in body_nodes) and any(isinstance(n, ast.Call) and api(n.func) == 'time.sleep' for n in body_nodes)
    return bool(automatic or (flag and any_save))


def audit(root, ignores=()):
    docs, trees = [], []
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        rel = relative.as_posix()
        if path.is_symlink() or not path.is_file() or any(fnmatch.fnmatch(rel, glob) or relative.match(glob) for glob in ignores):
            continue
        if path.suffix == '.py' and 'tests' not in relative.parts and not path.name.startswith('test_'):
            trees.append(ast.parse(path.read_text(encoding='utf-8'), filename=rel))
        elif rel == 'README.md' or (relative.parts[0] == 'docs' and path.suffix == '.md'):
            docs.append((rel, claims(path.read_text(encoding='utf-8'))))
    present = evidence(trees)
    findings = []
    for family, false in (('AUTOSAVE', not present), ('NOAUTOSAVE', present)):
        if false:
            for path, families in docs:
                if family in families:
                    findings.append({'rule': 'documented-autosave-claim-false' if family == 'AUTOSAVE' else 'documented-no-autosave-claim-false',
                                     'severity': 'MEDIUM' if 'tests' in Path(path).parts else 'HIGH', 'path': path})
                    break
    return {'status': 'FAIL' if findings else 'PASS', 'evidence': present, 'claims': sorted(set().union(*(c for _, c in docs))), 'findings': findings}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, '{}: error: {}\n'.format(self.prog, message))


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('root', nargs='?', default='.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--ignore', nargs='+', action='extend', default=[])
    parser.add_argument('--fail-on', choices=('HIGH', 'MEDIUM'), default='MEDIUM')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        parser.error('ROOT must be a directory')
    try:
        result = audit(root, args.ignore)
    except (OSError, UnicodeError, SyntaxError) as exc:
        parser.exit(1, 'error: {}\n'.format(exc))
    failed = any(f['severity'] == 'HIGH' or args.fail_on == 'MEDIUM' for f in result['findings'])
    result['status'] = 'FAIL' if failed else 'PASS'
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(result['status'])
        for finding in result['findings']:
            print('{} {}{}'.format(finding['rule'], finding['severity'], ' ' + finding['path'] if args.verbose else ''))
    return 2 if failed else 0
