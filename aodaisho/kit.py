import csv
import hashlib
from pathlib import Path
from typing import Callable

from loguru import logger
from selectolax.lexbor import LexborHTMLParser


def parse_html(html: str | bytes) -> LexborHTMLParser | None:
    try:
        return LexborHTMLParser(html)
    except Exception as e:
        logger.error(f'[parse_html] {type(e).__name__}: {e}')
        return None


def from_here(file: str) -> Callable[[str], Path]:
    base = Path(file).resolve().parent
    return lambda path: base / path


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, rows: list[dict]) -> None:
    '''``rows`` を CSV ファイルとして書き出す（上書き）。

    Excel 互換のため UTF-8 BOM（``utf-8-sig``）とヘッダ行を付ける。
    ``rows`` が空ならスキップ（警告のみ）。列順は先頭行の ``keys()`` の順で、
    2 回目以降のキーずれは検知しない。
    書き込み失敗はログしたあと例外を再送出する。
    '''
    try:
        if not rows:
            logger.warning(f'[write_csv] {path} no rows, skipped')
            return
        _ensure_parent(path)
        with open(path, mode='w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    except Exception as e:
        logger.error(f'[write_csv] {path} {type(e).__name__}: {e}')
        raise

def write_parquet(path: Path, rows: list[dict]) -> None:
    '''``rows`` を Parquet ファイルとして書き出す。

    pyarrow を直接使う（pandas 非依存）。``rows`` が空ならスキップ（警告のみ）。
    列スキーマは各列の最初の non-None 値から推論されるので、**同一キーで型が
    混在するとエラーになる**ことがある点に注意。
    書き込み失敗はログしたあと例外を再送出する。
    '''
    import pyarrow as pa
    import pyarrow.parquet as pq

    try:
        if not rows:
            logger.warning(f'[write_parquet] {path} no rows, skipped')
            return
        _ensure_parent(path)
        pq.write_table(pa.Table.from_pylist(rows), path)
    except Exception as e:
        logger.error(f'[write_parquet] {path} {type(e).__name__}: {e}')
        raise

def hash_name(key: str) -> str:
    return hashlib.md5(key.encode()).hexdigest()

def write_bytes(path: Path, data: bytes) -> bool:
    try:
        _ensure_parent(path)
        path.write_bytes(data)
        return True
    except Exception as e:
        logger.error(f'[write_bytes] {path} {type(e).__name__}: {e}')
        return False

def save_log(path: Path, level: str = 'WARNING') -> None:
    '''コンソール（stderr）に出るログと同じ内容を、指定ファイルにも残す。'''
    _ensure_parent(path)
    logger.add(path, level=level, encoding='utf-8')
