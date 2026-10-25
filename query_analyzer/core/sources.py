"""Portable input configuration and validated profiler CSVs. No database access."""
import csv
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from .common import SCALES
from .analysis import analyze, read_plan_documents
from .plan_structure import structure


def read_timings(path):
    timings, errors = {}, []
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream, strict=True)
        if not reader.fieldnames or not {'query_id', 'average_ms'} <= set(reader.fieldnames):
            raise ValueError('Timing CSV requires query_id and average_ms columns')
        for line, row in enumerate(reader, 2):
            try:
                query = row['query_id'].strip()
                if not re.fullmatch(r'[A-Za-z0-9_.-]+', query):
                    raise ValueError('invalid query_id')
                duration = float(row['average_ms'])
                if not math.isfinite(duration) or duration < 0:
                    raise ValueError('average_ms must be finite and nonnegative')
                if query in timings:
                    raise ValueError('duplicate query_id: ' + query)
                timings[query] = duration
            except (TypeError, ValueError, AttributeError) as exc:
                errors.append(f'{Path(path).name}:{line}: {exc}')
    return timings, errors


def load_scale(base, spec):
    source = dict(plans={}, timings={}, errors=[], plan_source=spec.get('plans'),
                  timing_source=spec.get('summary'))
    def add_plan(name, documents):
        path = Path(name)
        if path.name != name or '/' in name or '\\' in name or not re.fullmatch(r'[A-Za-z0-9_.-]+', path.stem):
            raise ValueError('invalid query filename: ' + name)
        if path.stem in source['plans']:
            raise ValueError('duplicate query filename stem: ' + path.stem)
        result = dict(analyze(path, documents=documents), **structure(path, documents=documents))
        separator = '!/' if str(spec['plans']).lower().endswith('.zip') else '/'
        result['source'] = str(spec['plans']).replace('\\', '/') + separator + name
        source['plans'][path.stem] = result

    folder = Path(base) / spec['plans'] if spec.get('plans') else None
    if folder and folder.is_dir():
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in ('.xml', '.sqlplan'):
                continue
            try:
                add_plan(path.name, read_plan_documents(path))
            except (ValueError, OSError, RecursionError, ET.ParseError) as exc:
                source['errors'].append(f'{path.name}: {exc}')
    elif folder and folder.is_file() and folder.suffix.lower() == '.zip':
        try:
            with zipfile.ZipFile(folder) as archive:
                for member in sorted(archive.infolist(), key=lambda entry: entry.filename):
                    if member.is_dir() or Path(member.filename).suffix.lower() not in ('.xml', '.sqlplan'):
                        continue
                    try:
                        if member.file_size > 32 * 1024 * 1024:
                            raise ValueError('plan exceeds 32 MiB uncompressed limit')
                        if '/' in member.filename or '\\' in member.filename:
                            raise ValueError('ZIP must contain plan files directly, without subfolders')
                        add_plan(member.filename, read_plan_documents(member.filename, raw=archive.read(member)))
                    except (ValueError, OSError, RuntimeError, RecursionError, ET.ParseError, zipfile.BadZipFile) as exc:
                        source['errors'].append(f'{member.filename}: {exc}')
        except (OSError, zipfile.BadZipFile) as exc:
            source['errors'].append(f'{folder.name}: {exc}')
    summary = Path(base) / spec['summary'] if spec.get('summary') else None
    if summary and summary.is_file():
        try:
            source['timings'], errors = read_timings(summary)
            source['errors'].extend(errors)
        except (ValueError, OSError, csv.Error) as exc:
            source['errors'].append(f'{summary.name}: {exc}')
    return source


def load_sources(config_path):
    config_path = Path(config_path)
    config = json.loads(config_path.read_text(encoding='utf-8'))
    if not isinstance(config, dict) or set(config) - set(SCALES):
        raise ValueError('Configuration must map only 1GB, 2GB, 5GB and 10GB')
    if any(not isinstance(spec, dict) or any(not isinstance(value, str) for value in spec.values()) for spec in config.values()):
        raise ValueError('Each dataset must contain string input paths')
    sources = {}
    for scale in SCALES:
        sources[scale] = load_scale(config_path.parent, config.get(scale, {}))
    return sources
