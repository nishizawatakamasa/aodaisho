import os
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import zstandard as zstd
from loguru import logger
from tqdm import tqdm

__all__ = [
    'open_sqlite',
    'read_sqlite',
    'zstd_compress',
    'zstd_decompress',
    'SqliteDb',
    'SqliteTable',
    'SqliteRead',
    'SqliteReadTable',
]


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _quote_ident(name: str) -> str:
    if not name or '"' in name:
        raise ValueError(f'invalid identifier: {name!r}')
    return f'"{name}"'


def _connect(path: Path, readonly: bool) -> sqlite3.Connection:
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


class SqliteTable:
    def __init__(
        self,
        conn: sqlite3.Connection,
        name: str,
        columns: tuple[str, ...],
    ) -> None:
        self._conn = conn
        self._name = name
        self._columns = columns

    def insert(self, row: Mapping[str, Any]) -> bool:
        if set(row) != set(self._columns):
            logger.error(
                f'[insert] {self._name} column mismatch: '
                f'got {set(row)} expected {set(self._columns)}'
            )
            return False
        quoted_table = _quote_ident(self._name)
        quoted_cols = ', '.join(_quote_ident(c) for c in self._columns)
        placeholders = ', '.join('?' for _ in self._columns)
        values = tuple(row[c] for c in self._columns)
        sql = f'INSERT INTO {quoted_table} ({quoted_cols}) VALUES ({placeholders})'
        try:
            self._conn.execute(sql, values)
            self._conn.commit()
            return True
        except Exception as e:
            logger.error(f'[insert] {self._name} {type(e).__name__}: {e}')
            try:
                self._conn.rollback()
            except Exception:
                pass
            return False


class SqliteDb:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @property
    def raw(self) -> sqlite3.Connection:
        return self._conn

    def create_table(self, name: str, columns: Mapping[str, str]) -> SqliteTable:
        if not columns:
            raise ValueError('columns must not be empty')
        quoted_table = _quote_ident(name)
        col_sql = ', '.join(
            f'{_quote_ident(col)} {typ}' for col, typ in columns.items()
        )
        sql = f'CREATE TABLE IF NOT EXISTS {quoted_table} ({col_sql})'
        try:
            self._conn.execute(sql)
            self._conn.commit()
        except Exception as e:
            logger.error(f'[create_table] {name} {type(e).__name__}: {e}')
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise
        return SqliteTable(self._conn, name, tuple(columns))


@contextmanager
def open_sqlite(
    path: str | Path,
    *,
    readonly: bool = False,
) -> Iterator[SqliteDb]:
    path = Path(path)
    if not readonly:
        _ensure_parent(path)
    conn = _connect(path, readonly)
    try:
        yield SqliteDb(conn)
    finally:
        if not readonly:
            try:
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            except Exception as e:
                logger.error(f'[open_sqlite] {type(e).__name__}: {e}')
        conn.close()


_map_conn: sqlite3.Connection | None = None
_map_table: str | None = None
_map_fn: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None


def _map_init(
    path: Path,
    table: str,
    fn: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> None:
    global _map_conn, _map_table, _map_fn
    _map_conn = _connect(Path(path), readonly=True)
    _map_table = table
    _map_fn = fn


def _map_one(rowid: int) -> dict[str, Any] | None:
    try:
        conn = _map_conn
        table = _map_table
        fn = _map_fn
        if conn is None or table is None or fn is None:
            raise RuntimeError('map worker not initialized')
        cur = conn.execute(
            f'SELECT * FROM {_quote_ident(table)} WHERE rowid = ?',
            (rowid,),
        )
        fetched = cur.fetchone()
        return fn(dict(fetched))
    except Exception as e:
        logger.error(f'[map] {type(e).__name__}: {e}')
        return None


def _auto_chunksize(n: int, workers: int | None) -> int:
    w = workers or os.cpu_count() or 4
    return max(1, min(64, n // (w * 4)))


class SqliteReadTable:
    def __init__(self, path: Path, name: str) -> None:
        self._path = path
        self._name = name

    def map(
        self,
        fn: Callable[[dict[str, Any]], dict[str, Any] | None],
        *,
        workers: int | None = None,
        chunksize: int | None = None,
    ) -> list[dict[str, Any]]:
        quoted = _quote_ident(self._name)
        conn = _connect(self._path, readonly=True)
        try:
            try:
                cur = conn.execute(f'SELECT rowid FROM {quoted} ORDER BY rowid')
                rowids = [int(row[0]) for row in cur.fetchall()]
            except Exception as e:
                logger.error(f'[map] {self._name} {type(e).__name__}: {e}')
                raise
        finally:
            conn.close()
        if not rowids:
            return []
        cs = chunksize if chunksize is not None else _auto_chunksize(len(rowids), workers)
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_map_init,
            initargs=(self._path, self._name, fn),
        ) as ex:
            return [
                row
                for row in tqdm(
                    ex.map(_map_one, rowids, chunksize=cs),
                    total=len(rowids),
                    unit='row',
                )
                if row is not None
            ]


class SqliteRead:
    def __init__(self, path: Path) -> None:
        self._path = path

    def table(self, name: str) -> SqliteReadTable:
        _quote_ident(name)
        return SqliteReadTable(self._path, name)


def read_sqlite(path: str | Path) -> SqliteRead:
    return SqliteRead(Path(path).resolve())
