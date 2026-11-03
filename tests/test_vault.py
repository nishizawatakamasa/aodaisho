from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from aodaisho import vault
from aodaisho.vault import (
    open_sqlite,
    read_sqlite,
    zstd_compress,
    zstd_decompress,
)

PAGES = {
    'url_index': 'INTEGER',
    'request_url': 'TEXT NOT NULL',
    'html_zstd': 'BLOB',
}


def extract_passthrough(row: dict) -> dict:
    return dict(row)


def extract_goto_style(row: dict) -> dict:
    blob = row['html_zstd']
    return {
        'url_index': row['url_index'],
        'request_url': row['request_url'],
        'body': None if blob is None else 'ok',
    }


def extract_raise_on_two(row: dict) -> dict:
    if row['url_index'] == 2:
        raise RuntimeError('extract failed')
    return {
        'url_index': row['url_index'],
        'request_url': row['request_url'],
    }


def _write_pages(path: Path, rows: list[dict]) -> None:
    with open_sqlite(path) as db:
        table = db.create_table('pages', PAGES)
        for row in rows:
            assert table.insert(row) is True


def test_write_then_read_sqlite_map_returns_dicts(tmp_path: Path) -> None:
    db_path = tmp_path / 'run.db'
    html = zstd_compress('<html>青大将</html>')
    _write_pages(
        db_path,
        [
            {
                'url_index': 0,
                'request_url': 'https://example.test/a',
                'html_zstd': html,
            },
            {
                'url_index': 1,
                'request_url': 'https://example.test/b',
                'html_zstd': html,
            },
        ],
    )
    rows = read_sqlite(db_path).table('pages').map(
        extract_passthrough,
        workers=1,
        chunksize=1,
    )
    assert len(rows) == 2
    assert all(isinstance(row, dict) for row in rows)
    assert [row['url_index'] for row in rows] == [0, 1]
    assert rows[0]['request_url'] == 'https://example.test/a'
    assert rows[0]['html_zstd'] == html


def test_zstd_roundtrip_utf8_strict_and_rejects_none() -> None:
    text = '青大将 ☃'
    encoded = text.encode('utf-8')
    assert zstd_compress(text) == zstd_compress(encoded)
    assert zstd_decompress(zstd_compress(text)) == encoded
    assert zstd_decompress(zstd_compress(text)).decode('utf-8') == text
    raw_bytes = b'\xff\xfe not utf-8'
    assert zstd_decompress(zstd_compress(raw_bytes)) == raw_bytes

    src = inspect.getsource(zstd_compress)
    assert "encode('utf-8')" in src
    assert 'replace' not in src
    assert 'errors=' not in src
    assert 'None' not in src

    src_de = inspect.getsource(zstd_decompress)
    assert 'None' not in src_de

    with pytest.raises(TypeError):
        zstd_compress(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        zstd_decompress(None)  # type: ignore[arg-type]


def test_insert_key_mismatch_returns_false(tmp_path: Path) -> None:
    db_path = tmp_path / 'run.db'
    with open_sqlite(db_path) as db:
        table = db.create_table('pages', PAGES)
        assert table.insert(
            {
                'url_index': 0,
                'request_url': 'https://example.test/a',
                'html_zstd': None,
            }
        ) is True
        assert table.insert({'url_index': 1}) is False
        assert table.insert(
            {
                'url_index': 1,
                'request_url': 'https://example.test/b',
                'html_zstd': None,
                'extra': 1,
            }
        ) is False
        count = db.raw.execute('SELECT COUNT(*) FROM pages').fetchone()[0]
        assert count == 1


def test_goto_failure_blob_none_row_remains(tmp_path: Path) -> None:
    db_path = tmp_path / 'run.db'
    _write_pages(
        db_path,
        [
            {
                'url_index': 0,
                'request_url': 'https://example.test/ok',
                'html_zstd': zstd_compress('<html>ok</html>'),
            },
            {
                'url_index': 1,
                'request_url': 'https://example.test/fail',
                'html_zstd': None,
            },
        ],
    )
    rows = read_sqlite(db_path).table('pages').map(
        extract_goto_style,
        workers=1,
        chunksize=1,
    )
    assert [row['url_index'] for row in rows] == [0, 1]
    assert rows[0].keys() == rows[1].keys()
    assert rows[0]['body'] == 'ok'
    assert rows[1]['body'] is None
    assert rows[1]['request_url'] == 'https://example.test/fail'


def test_map_drops_only_rows_whose_fn_raises(tmp_path: Path) -> None:
    db_path = tmp_path / 'run.db'
    _write_pages(
        db_path,
        [
            {
                'url_index': 1,
                'request_url': 'https://example.test/a',
                'html_zstd': None,
            },
            {
                'url_index': 2,
                'request_url': 'https://example.test/b',
                'html_zstd': None,
            },
            {
                'url_index': 3,
                'request_url': 'https://example.test/c',
                'html_zstd': None,
            },
        ],
    )
    rows = read_sqlite(db_path).table('pages').map(
        extract_raise_on_two,
        workers=1,
        chunksize=1,
    )
    assert [row['url_index'] for row in rows] == [1, 3]
    assert all(set(row) == {'url_index', 'request_url'} for row in rows)


def test_open_sqlite_wal_checkpoint_close(tmp_path: Path) -> None:
    db_path = tmp_path / 'nested' / 'run.db'
    with open_sqlite(db_path) as db:
        mode = db.raw.execute('PRAGMA journal_mode').fetchone()[0]
        assert str(mode).lower() == 'wal'
        table = db.create_table('pages', {'id': 'INTEGER'})
        assert table.insert({'id': 1}) is True
        conn = db.raw
    wal = Path(str(db_path) + '-wal')
    assert (not wal.exists()) or wal.stat().st_size == 0
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute('SELECT 1')


def test_map_parent_rowid_only_initializer_child_int_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / 'run.db'
    _write_pages(
        db_path,
        [
            {
                'url_index': 10,
                'request_url': 'https://example.test/a',
                'html_zstd': zstd_compress('a'),
            },
            {
                'url_index': 11,
                'request_url': 'https://example.test/b',
                'html_zstd': zstd_compress('b'),
            },
        ],
    )
    parent_sql: list[str] = []
    real_connect = vault._connect

    def tracking_connect(path: Path, readonly: bool) -> sqlite3.Connection:
        conn = real_connect(path, readonly)
        conn.set_trace_callback(lambda sql: parent_sql.append(str(sql)))
        return conn

    recorded: dict = {}

    class RecordingExecutor:
        def __init__(self, *args, **kwargs) -> None:
            recorded['initializer'] = kwargs.get('initializer')
            recorded['initargs'] = kwargs.get('initargs')

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> bool:
            return False

        def map(self, fn, iterable, timeout=None, chunksize=1):
            recorded['worker'] = fn
            recorded['items'] = list(iterable)
            recorded['chunksize'] = chunksize
            return iter(())

    monkeypatch.setattr(vault, '_connect', tracking_connect)
    monkeypatch.setattr(vault, 'ProcessPoolExecutor', RecordingExecutor)

    result = read_sqlite(db_path).table('pages').map(
        extract_passthrough,
        workers=1,
        chunksize=1,
    )
    assert result == []
    assert any('SELECT rowid' in sql for sql in parent_sql)
    assert not any('SELECT *' in sql for sql in parent_sql)
    assert recorded['initializer'] is vault._map_init
    assert recorded['worker'] is vault._map_one
    items = recorded['items']
    assert items == [1, 2]
    assert all(type(item) is int for item in items)

    vault._map_init(*recorded['initargs'])
    try:
        assert vault._map_conn is not None
        child_sql: list[str] = []
        vault._map_conn.set_trace_callback(
            lambda sql: child_sql.append(str(sql))
        )
        out = vault._map_one(1)
        assert out is not None
        assert out['url_index'] == 10
        assert any('SELECT *' in sql and 'rowid' in sql for sql in child_sql)
    finally:
        if vault._map_conn is not None:
            vault._map_conn.close()
            vault._map_conn = None


def test_create_table_insert_quote_columns_commit_rollback(tmp_path: Path) -> None:
    db_path = tmp_path / 'run.db'
    with open_sqlite(db_path) as db:
        with pytest.raises(ValueError):
            db.create_table('pages', {})
        with pytest.raises(ValueError):
            db.create_table('foo"bar', {'a': 'TEXT'})
        with pytest.raises(ValueError):
            db.create_table('ok', {'a"b': 'TEXT'})
        with pytest.raises(ValueError):
            read_sqlite(db_path).table('bad"name')

        table = db.create_table(
            'order',
            {'select': 'TEXT NOT NULL', 'n': 'INTEGER'},
        )
        sql = db.raw.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'order'"
        ).fetchone()[0]
        assert '"order"' in sql
        assert '"select"' in sql
        assert '"n"' in sql

        assert table.insert({'n': 1, 'select': 'ok'}) is True

        other = sqlite3.connect(db_path)
        try:
            n = other.execute('SELECT COUNT(*) FROM "order"').fetchone()[0]
            assert n == 1
            row = other.execute(
                'SELECT "select", n FROM "order"'
            ).fetchone()
            assert tuple(row) == ('ok', 1)
        finally:
            other.close()

        assert table.insert({'n': 2, 'select': None}) is False
        remaining = db.raw.execute('SELECT n FROM "order" ORDER BY n').fetchall()
        assert [r[0] for r in remaining] == [1]
        assert table.insert({'n': 3, 'select': 'again'}) is True
        remaining = db.raw.execute('SELECT n FROM "order" ORDER BY n').fetchall()
        assert [r[0] for r in remaining] == [1, 3]


def test_vault_module_boundaries() -> None:
    src = Path(vault.__file__).read_text(encoding='utf-8')
    assert 'process_map' not in src
    assert 'from aodaisho.kit' not in src
    assert 'import aodaisho.kit' not in src
    assert 'from aodaisho.tree' not in src
    assert 'import pandas' not in src
    assert 'selectolax' not in src
    assert not hasattr(vault, 'sqlite_rows')
    assert not hasattr(vault, 'sqlite_row')
    assert not hasattr(vault, 'SqliteRowRef')
    assert not hasattr(vault, 'sqlite_map_html')
    assert not hasattr(vault, 'append_csv')
    assert not hasattr(vault, 'write_text')
    assert not hasattr(vault, 'write_csv')
    assert not hasattr(vault, 'write_parquet')
    assert not hasattr(vault, 'save_log')
    assert not hasattr(vault, 'write_bytes')
    assert not hasattr(vault, 'hash_name')


def test_auto_chunksize_matches_copied_formula() -> None:
    assert vault._auto_chunksize(200, 5) == 10
    assert vault._auto_chunksize(0, 4) == 1
    assert vault._auto_chunksize(10000, 1) == 64
