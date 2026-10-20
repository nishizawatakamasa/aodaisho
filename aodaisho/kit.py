import html
import os
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Callable, Iterable

from loguru import logger
from selectolax.lexbor import LexborHTMLParser
from tqdm import tqdm


def parse_html(html: str | bytes) -> LexborHTMLParser | None:
    try:
        return LexborHTMLParser(html)
    except Exception as e:
        logger.error(f'[parse_html] {type(e).__name__}: {e}')
        return None


def meta_tags(meta: Mapping[str, object | None]) -> str:
    return ''.join(
        f'<meta name="{html.escape(name)}" content="{html.escape(str(content))}">'
        for name, content in meta.items()
        if content is not None
    )


def from_here(file: str) -> Callable[[str], Path]:
    base = Path(file).resolve().parent
    return lambda path: base / path


class _SafeWorker:
    def __init__(self, fn: Callable) -> None:
        self.fn = fn

    def __call__(self, x):
        try:
            return self.fn(x)
        except Exception as e:
            logger.error(f'[process_map] {type(e).__name__}: {e}')
            return None


def _auto_chunksize(n: int, workers: int | None) -> int:
    '''``chunksize`` を自動で決める（``process_map`` で未指定のとき）。

    子プロセスへは 1 件ずつより、まとめて送った方が速くなりやすい。そのまとめ数。

    ``w`` は並列数。引数で決まっていなければ ``os.cpu_count()``、それも無ければ 4。
    この **4** は「CPU が分からないときの仮の並列数」。式 ``n // (w * 4)`` の **4** とは別物。

    ``n // (w * 4)`` の方の **4** は経験則の係数。ざっくり言うとチャンクの個数が
    ``w * 4`` 前後になりやすく、負荷が均等ならワーカーあたりだいたい **4 回分の塊**
    を処理するイメージ（厳密ではない）。

    例: ``n=200``, ``w=5`` なら ``200 // 20 = 10`` が chunksize。全体は 20 チャンク、
    5 人で割ると 1 人あたり平均 4 チャンク（各 10 件）。

    結果は ``min(64, …)`` で上限。塊が大きすぎると **負荷が偏りやすい**。
    タスクの重さがバラバラなとき、太い塊の中に遅いのが多く入ったワーカーだけが
    長引き、他は先に終わって手待ちしがち（終盤のムラ）。塊を細かくすると配り直しの
    機会が増えて和らぎやすい。進捗バーも細かく動きやすい。

    ``max(1, …)`` で下限。割り算で 0 になっても最低 1 件は送る。
    '''
    w = workers or os.cpu_count() or 4
    return max(1, min(64, n // (w * 4)))


def process_map[T, R](
    worker: Callable[[T], R],
    items: Iterable[T],
    workers: int | None = None,
    *,
    chunksize: int | None = None,
) -> list[R | None]:
    '''``ProcessPoolExecutor`` で ``worker`` を並列実行する。

    子プロセスで例外が出た分は ``None`` で返す。全体は止めない。
    進捗バーは常に tqdm。

    ``chunksize`` は子へまとめて送る件数。省略なら自動。
    進捗を細かくしたい・タスクの重さがバラバラで末尾に重いのが残る、なら ``chunksize=1``。
    '''
    safe = _SafeWorker(worker)
    item_list = list(items)
    cs = chunksize if chunksize is not None else _auto_chunksize(len(item_list), workers)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(
            tqdm(ex.map(safe, item_list, chunksize=cs), total=len(item_list), unit='file')
        )

def glob_paths(dir_path: Path, pattern: str = '*.html') -> list[str]:
    '''
    ``dir_path`` 直下で ``pattern`` に一致するパスを ``str`` のリストで返す。

    ``str`` にしているのは ``process_map`` 等のプロセスプールへ渡すとき pickle しやすくするため。
    '''
    return [str(p) for p in dir_path.glob(pattern)]
