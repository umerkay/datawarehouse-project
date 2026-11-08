"""The public offline command-line interface."""
import argparse
from pathlib import Path
import sys
import json
import os
import tempfile
import zipfile

from .build import build_site
from ..core.common import ROOT, SCALES, write_json
from ..core.sources import load_scale


def pack_plans(scale, folder, config_path):
    """Validate and package original XML bytes with stable archive metadata."""
    config = json.loads(config_path.read_text(encoding='utf-8'))
    source = load_scale(folder.parent, {'plans': folder.name})
    if source['errors'] or not source['plans']:
        raise ValueError('\n'.join(source['errors']) or 'No plans found to package')
    spec = config.setdefault(scale, {})
    configured = Path(spec.get('plans', ''))
    target = config_path.parent / (configured if configured.suffix.lower() == '.zip' else Path('datasets')/scale/'plans.zip')
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.plans-', suffix='.tmp', dir=target.parent)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temporary, 'w') as archive:
            for path in sorted(folder.iterdir()):
                if path.suffix.lower() not in ('.xml', '.sqlplan'):
                    continue
                if path.stat().st_size > 32 * 1024 * 1024:
                    raise ValueError('Plan exceeds 32 MiB limit: ' + path.name)
                info = zipfile.ZipInfo(path.name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip():
                raise ValueError('Archive verification failed')
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    spec['plans'] = os.path.relpath(target, config_path.parent).replace('\\', '/')
    write_json(config_path, config)
    print(f'{scale}: {len(source["plans"])} original plans packaged in {target}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Build the offline TPC-DS plan-analysis website. No SQL connection or raw .dat datasets required.')
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build', help='Generate all pages from configured plan files and timing CSVs')
    build.add_argument('--config', type=Path, default=ROOT / 'datasets' / 'sources.json')
    build.add_argument('--output', type=Path, default=ROOT / 'site')
    pack = commands.add_parser('pack', help='Package captured plans into one shareable ZIP per size')
    pack.add_argument('scale', choices=SCALES)
    pack.add_argument('--input', type=Path, help='Loose XML/sqlplan folder; defaults to datasets/<scale>/plans')
    pack.add_argument('--config', type=Path, default=ROOT / 'datasets' / 'sources.json')
    args = parser.parse_args(argv)
    try:
        if args.command == 'pack':
            pack_plans(args.scale, args.input or ROOT/'datasets'/args.scale/'plans', args.config)
            return 0
        sources, errors = build_site(args.config, args.output)
    except (OSError, ValueError, KeyError) as exc:
        print(f'Build failed: {exc}', file=sys.stderr)
        return 1
    for scale, source in sources.items():
        print(f'{scale}: {len(source["plans"])} plans; {len(source["timings"])} timing rows; {len(source["errors"])} errors')
    print(f'Open {args.output / "index.html"}')
    if errors:
        print('\n'.join(errors), file=sys.stderr)
    return int(bool(errors))
