from pathlib import Path

PLAN = (Path(__file__).parent / 'fixtures/actual.sqlplan').read_text(encoding='utf-8')
