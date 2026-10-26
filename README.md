# Audit of documented unattended-saving claims

An offline Python CLI that compares documented promises about unattended
saving with static evidence in Python source. Version 0.1.0 requires Python
3.9 or newer, uses only the standard library at runtime, and makes no network
requests or writes to the audited tree. Environment credential files are
never opened.

Install and run:

```sh
python3 -m pip install .
autosave-claim-audit ./project
python3 -m autosave_claim_audit ./project --json --verbose
python3 -m autosave_claim_audit ./project --ignore 'vendor/*' 'build/*' --fail-on HIGH
autosave-claim-audit --version
```

ROOT defaults to the current directory. Exit codes are 0 for PASS, 2 for
findings at the selected threshold, and 1 for usage or input errors. The
default threshold is MEDIUM. HIGH ignores medium findings for exit status,
while still reporting them. JSON includes status, evidence, claims, and
findings; each finding includes rule, severity, and path. Verbose text adds
the path of the first false claim per family.

The scanner reads root README.md and Markdown files recursively under docs/.
Fenced code is excluded; inline code counts. Paragraph lines are joined,
letters lowercased, hyphens replaced with spaces, and whitespace collapsed.
Matching uses exact phrases with word boundaries, rather than semantics.
The supported phrase list and finding identifiers are:

```text
AUTOSAVE:
autosave
auto save
autosaves
auto saves
automatically saves
saves automatically
automatic saving
autosave support
auto save support
supports autosave
supports auto save
autosaves your work
auto saves your work
automatically saves your work
saves your work automatically
autosaves changes
auto saves changes
saves changes automatically
automatically saves changes

NOAUTOSAVE:
does not autosave
does not auto save
never autosaves
never auto saves
no autosave
no auto save
does not save automatically
does not automatically save
no automatic saving
must save manually
you must save manually
manual saving only

documented-autosave-claim-false
documented-no-autosave-claim-false
```

Negative spans are removed before positive matching. There is no generic
negation detection beyond the listed negative templates. Bare save, saving,
and manual save nouns carry no claim. Findings are deduplicated by family;
the first false claim supplies the path and severity. A path under tests/
gets MEDIUM; other paths get HIGH. A positive promise without source fails;
a negative statement without source passes. No claim passes.

Evidence comes from Python ASTs, excluding tests/ directories and files
starting with test_. Save-named calls contain “save” in their function or
attribute name. Recognized contexts are threading timers, scheduler actions,
functions awaiting an asynchronous sleep, loops with a timed sleep,
change-event handlers, and named callback arguments. An enabled literal
configuration flag plus any save-named call also qualifies. Imports and
ordinary import aliases are recognized for sleep and timer APIs.

Evidence is project-wide, not per-feature. This is a static approximation:
a Timer save call does not prove that the timer fires in production; a flag
set True does not prove that any code path reads it. Save-on-exit alone is
deliberately excluded, as are manual command flows, comments, docstrings,
logging text, subprocess strings, backup copies, and checkpoint writes that
lack a recognized trigger. Sources are parsed without executing project code.
Syntax, decoding, and read errors produce exit 1. Ignore globs match relative
paths; use directory patterns such as vendor/* to exclude descendants.
Symbolic-link files are skipped.

This audit asks whether work in progress is saved during normal operation
without a user request. Related tools address different dimensions:

```text
backup-claim-audit: copies before writes
atomic-claim-audit: temporary files and replacement
shutdown-claim-audit: teardown and interruption
resume-claim-audit: continuation from saved progress
watch-claim-audit: observing file changes
```

A watcher that saves on change can supply evidence for two dimensions:
observation and unattended saving. A checkpoint dump alone supplies neither
an automatic trigger nor a save-named call for this audit.

Develop and verify:

```sh
python3 -m unittest discover -s tests -v
python3 -m build
```

Build tooling is separate from the dependency-free runtime. Licensed under MIT.
