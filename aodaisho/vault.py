import os
import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing, contextmanager
from functools import partial
from itertools import batched
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger
from tqdm import tqdm


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _quote_ident(name: str) -> str:
    if not name or '"' in name:
        raise ValueError(f'invalid identifier: {name!r}')
    return f'"{name}"'


def _connect(path: Path, *, readonly: bool) -> sqlite3.Connection:
    if readonly:
        uri = path.resolve().as_uri() + '?mode=ro'
        conn = sqlite3.connect(uri, uri=True, timeout=30.0, autocommit=True)
    else:
        conn = sqlite3.connect(path, timeout=30.0, autocommit=True)
        conn.execute('PRAGMA journal_mode = WAL')
        conn.execute('PRAGMA synchronous = NORMAL')
    return conn


class Table:
    def __init__(self, conn: sqlite3.Connection, name: str) -> None:
        self._conn = conn
        self._name = name
        self._quoted_name = _quote_ident(name)

    def _insert_sql(self, cols: Sequence[str]) -> str:
        quoted_cols = ', '.join(_quote_ident(c) for c in cols)
        placeholders = ', '.join(['?'] * len(cols))
        return f'INSERT INTO {self._quoted_name} ({quoted_cols}) VALUES ({placeholders})'

    def _write(self, op: str, sql: str, values: Sequence[Any]) -> bool:
        try:
            self._conn.execute(sql, values)
            return True
        except Exception as e:
            logger.error(f'[{op}] {self._name} {type(e).__name__}: {e}')
            return False

    def insert(self, row: Mapping[str, Any]) -> bool:
        if not row:
            logger.error(f'[insert] {self._name} empty row')
            return False
        sql = self._insert_sql(list(row))
        return self._write('insert', sql, tuple(row.values()))

    def upsert(self, row: Mapping[str, Any], on: str) -> bool:
        '''on で指定した列の値が同じならレコード上書き。対象列はスキーマで TEXT UNIQUE のようにする。'''
        if on not in row:
            logger.error(f'[upsert] {self._name} {on!r} missing from row')
            return False
        sets = ', '.join(
            f'{_quote_ident(c)}=excluded.{_quote_ident(c)}' for c in row if c != on
        )
        action = f'UPDATE SET {sets}' if sets else 'NOTHING'
        sql = f'{self._insert_sql(list(row))} ON CONFLICT({_quote_ident(on)}) DO {action}'
        return self._write('upsert', sql, tuple(row.values()))

    def to_df(self, *columns: str) -> pd.DataFrame:
        quoted_cols = ', '.join(_quote_ident(c) for c in columns) if columns else '*'
        sql = f'SELECT {quoted_cols} FROM {self._quoted_name}'
        try:
            return pd.read_sql_query(sql, self._conn)
        except Exception as e:
            logger.error(f'[to_df] {self._name} {type(e).__name__}: {e}')
            raise


class SqliteDb:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def ensure_table(self, name: str, schema: Mapping[str, str]) -> None:
        '''テーブルが無ければ schema（列名 → 型）で作成する。既にあればスキーマが違っても作り直さない。'''
        if not schema:
            raise ValueError('schema must not be empty')
        col_sql = ', '.join(f'{_quote_ident(col)} {typ}' for col, typ in schema.items())
        sql = f'CREATE TABLE IF NOT EXISTS {_quote_ident(name)} ({col_sql})'
        try:
            self._conn.execute(sql)
        except Exception as e:
            logger.error(f'[ensure_table] {name} {type(e).__name__}: {e}')
            raise

    def table(self, name: str) -> Table:
        return Table(self._conn, name)


@contextmanager
def open_sqlite(path: str | Path) -> Iterator[SqliteDb]:
    path = Path(path)
    _ensure_parent(path)
    with closing(_connect(path, readonly=False)) as conn:
        yield SqliteDb(conn)


def _map_range[T](
    path: Path,
    table: str,
    fn: Callable[[dict[str, Any]], T],
    bounds: tuple[int, int],
) -> tuple[list[T], list[str]]:
    sql = f'SELECT rowid, * FROM {_quote_ident(table)} WHERE rowid BETWEEN ? AND ? ORDER BY rowid'
    results: list[T] = []
    errors: list[str] = []
    with closing(_connect(path, readonly=True)) as conn:
        cur = conn.execute(sql, bounds)
        cols = [d[0] for d in cur.description[1:]]
        for rowid, *values in cur:
            try:
                results.append(fn(dict(zip(cols, values))))
            except Exception as e:
                errors.append(f'rowid={rowid} {type(e).__name__}: {e}')
    return results, errors


def _auto_chunksize(n: int, workers: int | None) -> int:
    w = workers or os.cpu_count() or 4
    return max(1, min(64, n // (w * 4)))


def map_sqlite[T](
    path: str | Path,
    table: str,
    fn: Callable[[dict[str, Any]], T],
    *,
    workers: int | None = None,
    chunksize: int | None = None,
) -> list[T]:
    '''table の各行を dict にして fn に渡し、プロセス並列で処理した結果を rowid 順のリストで返す。

    fn はモジュールのトップレベルに定義すること（子プロセスへ pickle で渡すため）。
    fn が例外を出した行は結果から除き、rowid 付きで親プロセスのログに残す。
    '''
    path = Path(path).resolve()
    quoted_table = _quote_ident(table)
    try:
        with closing(_connect(path, readonly=True)) as conn:
            cur = conn.execute(f'SELECT rowid FROM {quoted_table} ORDER BY rowid')
            rowids = [rowid for (rowid,) in cur]
    except Exception as e:
        logger.error(f'[map_sqlite] {table} {type(e).__name__}: {e}')
        raise
    if not rowids:
        return []
    cs = chunksize if chunksize is not None else _auto_chunksize(len(rowids), workers)
    bounds = [(chunk[0], chunk[-1]) for chunk in batched(rowids, cs)]
    out: list[T] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        parts = ex.map(partial(_map_range, path, table, fn), bounds)
        for results, errors in tqdm(parts, total=len(bounds), unit='chunk'):
            out.extend(results)
            for msg in errors:
                logger.error(f'[map_sqlite] {table} {msg}')
    return out
