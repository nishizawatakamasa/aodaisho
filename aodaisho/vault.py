import csv
import hashlib
from pathlib import Path

from loguru import logger


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def append_csv(path: Path, row: dict) -> None:
    '''``row`` を 1 行だけ CSV に追記する（ファイルが無ければ作成）。

    Excel 互換のため、**ファイル新規作成時のみ先頭に UTF-8 BOM** を書く
    （``utf-8-sig`` で open）。既存ファイルへの追記では BOM を書かない
    （中途 BOM は不正になるため）。ファイルが新規 / 空ならヘッダ行を書く。
    列順は ``row.keys()`` の順で、2 回目以降のキーずれは検知しない
    （pandas 版と同じ挙動）。
    '''
    try:
        _ensure_parent(path)
        need_header = not path.exists() or path.stat().st_size == 0
        encoding = 'utf-8-sig' if need_header else 'utf-8'
        with open(path, mode='a', newline='', encoding=encoding) as f:
            w = csv.DictWriter(f, fieldnames=list(row.keys()))
            if need_header:
                w.writeheader()
            w.writerow(row)
    except Exception as e:
        logger.error(f'[append_csv] {path} {row} {type(e).__name__}: {e}')

def write_csv(path: Path, rows: list[dict]) -> None:
    '''``rows`` を CSV ファイルとして書き出す（上書き）。

    Excel 互換のため UTF-8 BOM（``utf-8-sig``）とヘッダ行を付ける。
    ``rows`` が空ならスキップ（警告のみ）。列順は先頭行の ``keys()`` の順で、
    2 回目以降のキーずれは検知しない（``append_csv`` と同じ）。
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

def write_text(path: Path, data: str) -> bool:
    try:
        _ensure_parent(path)
        path.write_text(data, encoding='utf-8', errors='replace')
        return True
    except Exception as e:
        logger.error(f'[write_text] {path} {type(e).__name__}: {e}')
        return False

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
