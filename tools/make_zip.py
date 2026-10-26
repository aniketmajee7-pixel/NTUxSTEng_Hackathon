#!/usr/bin/env python3
"""Pack the agent into a ZIP for the VM.  Stdlib only; run from anywhere:

    python3 tools/make_zip.py [out.zip]

The ZIP holds the top-level *.py, config.json, requirements.txt, create_shortcut.ps1 and README.md at
its top level (never tests/ or __pycache__), plus BUILD.json:

    {"name": "agent-YYYYMMDD-HHMM", "files": {"main.py": "<sha256>", ...}}

`files` lists the .py files and config.json, the ones the agent runs from, each hashed with
CRLF turned into LF, so a copy that went through a Windows share still matches.  On the VM
main.build_differences() compares the folder with it before a run.  Without out.zip the
file is written to the current directory as <name>.zip.
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path
import sys
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
EXTRAS = ('config.json', 'requirements.txt', 'create_shortcut.ps1', 'README.md')
CHECKED = ('config.json',)           # besides the .py files


def digest(data):
    return hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest()


def collect(source=SOURCE):
    """The files that go in, as {name: path}, in name order."""
    found = {path.name: path for path in source.glob('*.py')}
    for name in EXTRAS:
        if (source/name).is_file():
            found[name] = source/name
    return dict(sorted(found.items()))


def build_manifest(files, now=None):
    now = now or datetime.now()
    return {'name': now.strftime('agent-%Y%m%d-%H%M'),
            'files': {name: digest(path.read_bytes()) for name, path in files.items()
                      if name.endswith('.py') or name in CHECKED}}


def make_zip(out=None, source=SOURCE, now=None):
    """Write the ZIP and return its path."""
    files = collect(Path(source))
    if not files:
        raise SystemExit(f'No agent files found in {source}')
    manifest = build_manifest(files, now)
    out = Path(out) if out else Path.cwd()/f'{manifest["name"]}.zip'
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, path in files.items():
            archive.write(path, name)
        archive.writestr('BUILD.json', json.dumps(manifest, indent=2)+'\n')
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) > 1 or argv[:1] in (['-h'], ['--help']):
        print(__doc__); return 2 if len(argv) > 1 else 0
    out = make_zip(argv[0] if argv else None)
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
    print(f'{out}: {len(names)} files, {json.loads(zipfile.ZipFile(out).read("BUILD.json"))["name"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
