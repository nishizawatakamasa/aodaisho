import os
import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd
import zstandard as zstd
from loguru import logger
from tqdm import tqdm


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _quote_ident(name: str) -> str:
    if not name or '"' in name:
        raise ValueError(f'invalid identifier: {name!r}')
    return f'"{name}"'


def _connect(path: Path, *, readonly: bool) -> sqlite3.Connection:
    path = Path(path)
    if readonly:
        uri = path.resolve().as_uri() + '?mode=ro'
        conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    else:
        conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout = 5000')
    if not readonly:
        conn.execute('PRAGMA journal_mode = WAL')
        conn.execute('PRAGMA synchronous = NORMAL')
    return conn


def zstd_compress(data: str | bytes, level: int = 3) -> bytes:
    if isinstance(data, str):
        data = data.encode('utf-8')
    return zstd.ZstdCompressor(level=level).compress(data)


def zstd_decompress(data: bytes) -> bytes:
    return zstd.ZstdDecompressor().decompress(data)


def _execute_write(conn: sqlite3.Connection, sql: str, values: Sequence[Any], label: str) -> bool:
    try:
        conn.execute(sql, values)
        conn.commit()
        return True
    except Exception as e:
        logger.error(f'[{label}] {type(e).__name__}: {e}')
        try:
            conn.rollback()
        except Exception:
            pass
        return False


class Table:
    def __init__(self, conn: sqlite3.Connection, name: str) -> None:
        self._conn = conn
        self._name = name

    def insert(self, row: Mapping[str, Any]) -> bool:
        if not row:
            logger.error(f'[insert] {self._name} empty row')
            return False
        cols = list(row)
        quoted_table = _quote_ident(self._name)
        quoted_cols = ', '.join(_quote_ident(c) for c in cols)
        placeholders = ', '.join('?' for _ in cols)
        sql = f'INSERT INTO {quoted_table} ({quoted_cols}) VALUES ({placeholders})'
        return _execute_write(
            self._conn, sql, tuple(row[c] for c in cols), f'insert {self._name}'
        )

    def upsert(self, row: Mapping[str, Any], on: str) -> bool:
        if not row:
            logger.error(f'[upsert] {self._name} empty row')
            return False
        if on not in row:
            logger.error(f'[upsert] {self._name} {on!r} missing from row')
            return False
        cols = list(row)
        quoted_table = _quote_ident(self._name)
        quoted_cols = ', '.join(_quote_ident(c) for c in cols)
        placeholders = ', '.join('?' for _ in cols)
        update_cols = [c for c in cols if c != on]
        sql = (
            f'INSERT INTO {quoted_table} ({quoted_cols}) VALUES ({placeholders}) '
            f'ON CONFLICT({_quote_ident(on)}) DO UPDATE SET '
        )
        if update_cols:
            sql += ', '.join(
                f'{_quote_ident(c)}=excluded.{_quote_ident(c)}' for c in update_cols
            )
        else:
            sql += f'{_quote_ident(on)}=excluded.{_quote_ident(on)}'
        return _execute_write(
            self._conn, sql, tuple(row[c] for c in cols), f'upsert {self._name}'
        )

    def to_df(self, *columns: str) -> pd.DataFrame:
        quoted_table = _quote_ident(self._name)
        if columns:
            quoted_cols = ', '.join(_quote_ident(c) for c in columns)
        else:
            quoted_cols = '*'
        sql = f'SELECT {quoted_cols} FROM {quoted_table}'
        try:
            return pd.read_sql_query(sql, self._conn)
        except Exception as e:
            logger.error(f'[to_df] {self._name} {type(e).__name__}: {e}')
            raise


class SqliteDb:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def ensure_table(self, name: str, schema: Mapping[str, str]) -> None:
        if not schema:
            raise ValueError('schema must not be empty')
        quoted_table = _quote_ident(name)
        col_sql = ', '.join(
            f'{_quote_ident(col)} {typ}' for col, typ in schema.items()
        )
        sql = f'CREATE TABLE IF NOT EXISTS {quoted_table} ({col_sql})'
        try:
            self._conn.execute(sql)
            self._conn.commit()
        except Exception as e:
            logger.error(f'[ensure_table] {name} {type(e).__name__}: {e}')
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise

    def table(self, name: str) -> Table:
        _quote_ident(name)
        return Table(self._conn, name)


@contextmanager
def open_sqlite(path: str | Path) -> Iterator[SqliteDb]:
    path = Path(path)
    _ensure_parent(path)
    conn = _connect(path, readonly=False)
    try:
        yield SqliteDb(conn)
    finally:
        try:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except Exception as e:
            logger.error(f'[open_sqlite] {type(e).__name__}: {e}')
        conn.close()


_map_path: Path | None = None
_map_table: str | None = None
_map_fn: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None


def _map_init(
    path: Path,
    table: str,
    fn: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> None:
    global _map_path, _map_table, _map_fn
    _map_path = Path(path)
    _map_table = table
    _map_fn = fn


def _map_range(bounds: tuple[int, int]) -> list[dict[str, Any]]:
    start, end = bounds
    path = _map_path
    table = _map_table
    fn = _map_fn
    if path is None or table is None or fn is None:
        raise RuntimeError('map worker not initialized')
    conn = _connect(path, readonly=True)
    try:
        cur = conn.execute(
            f'SELECT * FROM {_quote_ident(table)} WHERE rowid BETWEEN ? AND ? ORDER BY rowid',
            (start, end),
        )
        rows: list[dict[str, Any]] = []
        for fetched in cur:
            try:
                row = fn(dict(fetched))
            except Exception as e:
                logger.error(f'[map_sqlite] {type(e).__name__}: {e}')
                continue
            if row is not None:
                rows.append(row)
        return rows
    finally:
        conn.close()


def _auto_chunksize(n: int, workers: int | None) -> int:
    w = workers or os.cpu_count() or 4
    return max(1, min(64, n // (w * 4) or 1))


def map_sqlite(
    path: str | Path,
    table: str,
    fn: Callable[[dict[str, Any]], dict[str, Any] | None],
    *,
    workers: int | None = None,
    chunksize: int | None = None,
) -> list[dict[str, Any]]:
    path = Path(path).resolve()
    quoted = _quote_ident(table)
    conn = _connect(path, readonly=True)
    try:
        try:
            cur = conn.execute(f'SELECT rowid FROM {quoted} ORDER BY rowid')
            rowids = [int(row[0]) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f'[map_sqlite] {table} {type(e).__name__}: {e}')
            raise
    finally:
        conn.close()
    if not rowids:
        return []
    cs = chunksize if chunksize is not None else _auto_chunksize(len(rowids), workers)
    bounds = [
        (rowids[i], rowids[min(i + cs, len(rowids)) - 1])
        for i in range(0, len(rowids), cs)
    ]
    out: list[dict[str, Any]] = []
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_map_init,
        initargs=(path, table, fn),
    ) as ex:
        for part in tqdm(
            ex.map(_map_range, bounds),
            total=len(bounds),
            unit='chunk',
        ):
            out.extend(part)
    return out
