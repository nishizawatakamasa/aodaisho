# SQLite 保存ヘルパ 実装仕様

`architecture_sqlite_migration.md` はたたき台。**この文書が実装の正本。**  
作業の切り方・AI への出し方・検収は `HUMAN.md`。仕様はここに書く。  
スキーマ・run の置き場・成功/失敗の列は呼び出し側の例であり、ライブラリの契約ではない。

依存に `zstandard` を `pyproject.toml` へ追加する。モジュール配置は下の「モジュール」を正とする。

---

## 1. 設計思想

### 何を守るか
描画後 HTML を正本にする。サイトのセレクタが壊れても、ローカルで何度でも再抽出できる。scrape と extract を切り、取得失敗を再クロールにしない。

梱包だけを変える。1 ページ 1 HTML ファイルをやめる理由は、定期実行のファイル数・メタの `<meta>` 埋め込み・`failed.csv` の分断・非圧縮・`write_text(..., errors='replace')` による毀損。思想は変えない。

### 道具の境界
aodaisho はドメインを持たない。列名を知らない。SQLite 化でも同じ。

ツールに入れるのは、間違えると遅いか壊れる機械部分だけ。

- 接続の開閉（親ディレクトリ、WAL、timeout、明示 `close`、書き込み終了時の checkpoint）
- 列定義辞書からの `CREATE TABLE IF NOT EXISTS` と dict `INSERT`
- 1 行ごとの `commit`
- UTF-8 strict と zstd
- extract: 表の全行を `fn(row: dict) -> dict` に通す。並列・rowid・1 行 SELECT は vault の中

呼び出し側が書くもの。

- 表名、`{列名: 型文字列}`、run のパス
- insert / extract の戻り dict のキーと値
- HTML のどの列を解凍するか、パースとセレクタ
- 行のフィルタ、クレンジング

既定の `pages` 表、`save_page(url, html)`、ORM、Python 型マッピングは置かない。似た列が続いてもライブラリのデフォルトに昇格させない。例は README / 本資料の呼び出し側に置く。

### 正本と派生
- run の `.db` は再抽出用の再生バッファ（直近だけ残してファイルごと消してよい）
- crawl の `urls.csv` は入力キュー。寿命が違うので sqlite に入れない
- `extract.parquet` 以降が長期の倉庫
- 時系列の絞り込みは parquet / notebook。生 HTML を 1 本の巨大 DB に積まない
- 画像はファイル（`write_bytes`）。pages の BLOB にしない

### scrape と extract の温度
どちらも **dict のキーを自分で書く。** ツールがメタを自動結合しない。空 `{}` を和集合で埋めない。成功も失敗も同じキー、値が `None` なだけ。scrape の `insert({...})` と同じ形。

読み書きで入口を分ける。

- scrape: 接続を持ち続ける → `with open_sqlite(path) as db`
- extract: 開いて読んで終わる → `read_sqlite(path).table(name).map(fn)`（呼び出し側に `with` も接続も出さない）

### モジュール
モジュール名は技術名ではなく役割の名詞（`live` は Playwright ではない、`tree` は selectolax ではない）。金庫に当たるのは書き出し関数ではなく、**1 run の正本 sqlite** である。

```text
kit    道具（パス、パース、csv / parquet / log / bytes の書き出し）
live   生きているブラウザ
tree   DOM
vault  1 run の正本（sqlite + zstd）。読む並列もここ
```

今の `vault.py` から kit へ移すのは **`write_csv` / `write_parquet` / `hash_name` / `write_bytes` / `save_log` だけ。** シグネチャと docstring は変えない。`_ensure_parent` は kit に移し、vault 用は同名の私有関数を vault に置いてよい。

HTML ファイル梱包の対は **移さず消す。** 互換レイヤは置かない。**今すぐ消さない。** 削除は §7 の最後。作業中は残して参照する。

- `append_csv` … `failed.csv`。失敗も同じ `insert`
- `write_text` … HTML ファイル正本。`errors='replace'` が毀損の原因。本線では使わない
- `meta_tags` … HTML 先頭へのメタ埋め込み。メタは sqlite の列
- `TreeParser.meta` … その `<meta name>` を読む糖衣。extract は `row[...]`。サイト本来の meta は `into(...).attr('content')`
- `kit.process_map` … HTML ファイルをパスで回す用。同じ仕組み（ProcessPool + tqdm + 行単位で例外を吞む）は vault の `.map` 内部へ
- `glob_paths` … `html/*.html` 列挙

残す理由:

- `write_csv` … crawl の URL キュー。Excel / BOM。キューは目で削る・消さない。`.db` は捨ててよいので sqlite に混ぜない
- `write_parquet` … extract の倉庫
- `write_bytes` … 画像。`bytes_at` の行き先。pages の BLOB にしない（行が肥える。再抽出しない）
- `hash_name` … 画像ファイル名。HTML ファイル用ではない
- `from_here` / `parse_html` / `save_log`

sqlite の公開は空になった `vault.py` に置く。`from aodaisho.vault import open_sqlite, read_sqlite, zstd_compress`。

やらないこと:

- `pack.py` を足して今の vault を残す
- 今の vault を `dump` 等に改名する
- sqlite も parquet もログも全部 vault に残す
- `sqlite_rows` / `sqlite_row` / `SqliteRowRef` を公開する（実装の名残であり製品の形ではない）

`tree.py` の変更は **`TreeParser` が `LexborHTMLParser | None` を持つ**ことと **`meta()` の削除**だけ。`TreePage` は触らない（`.raw` は既にある）。`live.py` は `page()` を `next_page()` に改名するだけ。vault は kit / tree / pandas / selectolax を import しない（`_ensure_parent` は vault 側に小さく残してよい）。kit は vault を import しない。

README の import を直す。`process_map` / `glob_paths` / `meta_tags` / `append_csv` / `write_text` / `tree.meta` の例は消す。crawl は `write_csv`、discover / extract は `read_sqlite....map`。互換レイヤは置かない。

---

## 2. 機能の思想

### パイプライン

```text
crawl.py     URL リスト（今どおり urls.csv でよい）
scrape.py    ブラウザで取得 → 1 run 1 sqlite（メタ列 + 圧縮 HTML）
extract.py   read_sqlite.table.map → parquet
clean.ipynb  フィルタ・正規化・特徴量（pandas）
```

extract はフィルタではない。取得失敗行も残す。落とすのは clean。

```text
pages（sqlite）                         extract の戻り dict
url_index, request_url, error_reason,  →  呼び出し側が選んだメタ
html_zstd, ...                             + HTML から展開した列
                                           （html_zstd を出すかどうかも呼び出し側）
```

### 読みの公開形

```python
rows = read_sqlite(db_path).table('pages').map(extract)

def extract(row: dict[str, Any]) -> dict:
    ...
```

親が BLOB 付き dict を並べてから並列してはいけない。`.map` の中で親は rowid だけ集め、子が 1 行 `SELECT *` して `fn(row)` する。`fn` に bytes や `tree` を渡さない。解凍もパースも extract 内。vault は DOM / selectolax / `parse_html` を知らない。

### 命名
- `open_sqlite(...) as db`（`connect_sqlite` にしない）
- `read_sqlite(path).table(name).map(fn)`（`.process_map` にしない。Process を名前に出さない）
- `open_patchright(...) as session`（`as s` / `as browser` にしない）
- `db.create_table`（`create` でも `table()` でもない。`table()` は read 側の既存表指定）
- `SqliteDb` の公開メソッドは `create_table` と `.raw` だけ。`.map` を足さない

### セッションと tree（`page` を二つ持たない）
`session.page()` は getter に見えるが、呼ぶたびにカウンタが進み、必要なら page / context / browser を作り直す。名前を **`next_page()`** にする。中身の Recycle ロジックは変えない。`page()` の別名は残さない。`session.next_tree()` も作らない（`live` が `tree` に依存する）。

ループでは Playwright の `Page` をローカルに残さない。

```python
tree = tree_page(session.next_page())
response = tree.goto(request_url)
tree.raw.url
tree.raw.content()
```

`tree_page(page)` は Page をコピーしない。`tree._page` は渡したオブジェクトそのものなので、`goto` のあとに外の `page.url` が変わって見えるのは伝搬ではなくエイリアスである。`page` と `tree` を両方持つとそのように魔法に見える。ハンドルは `tree` だけ、Playwright が要るとき `tree.raw`。`url` / `content()` を `TreePage` に足さない。

README の crawl / scrape 例も `next_page()` と `tree.raw` に揃える。

### TreeParser（欠落は parser 側の根だけ）

昔の extract はパース失敗を `return None` で行ごと捨てていた。sqlite では捨てない。成功も失敗も同じキー、値が `None`。

その欠落を `tree is None` にしてセレクタごとに `if tree else None` しない。同じキーの dict を `if parser` で二枚にしない。空 HTML を番兵にしない。vault がキーを埋めない。

`tree_parser` が `LexborHTMLParser | None` を受け、`TreeParser` がそれを持つ。`into` / `into_every` は parser が無ければ `tree_node(None)` / 空グループ。以降は既存のノード連鎖。`tree` はいつも `TreeParser`。Python の `None` は葉の `.text` / `.attr` だけ。

`parse_html` は今どおり `str | bytes` を受け、例外時 `None`。`zstd_decompress` は `None` を扱わない。呼び出し側の分岐は blob の有無だけ。

```python
tree = tree_parser(parse_html(zstd_decompress(blob)) if blob else None)
```

解凍+パースの `html_tree` は足さない。どの列を解凍するかは呼び出し側。

`TreePage` は `None` を持たない。`next_page()` は `Page` を返すか例外。goto 失敗は `Response | None`。live の欠落は `TreeElement(None)` まで。`TreePage(None)` にすると `_PageScoped` が全部 `Page | None` になる。

---

## 3. 公開 API

| 名前 | 役割 |
|---|---|
| `open_sqlite` | 書き込みセッション。WAL・checkpoint・`close`。`SqliteDb` を yield |
| `SqliteDb.create_table` | `{列: 型}` から CREATE。`SqliteTable` を返す |
| `SqliteDb.raw` | 生の `sqlite3.Connection` |
| `SqliteTable.insert` | dict を 1 行 INSERT して `commit` |
| `read_sqlite` | 読み。パスを解決するだけ。接続はまだ開かない。`with` 不要 |
| `SqliteRead.table` | 既存表名を足す。`SELECT` しない |
| `SqliteReadTable.map` | 全行を `fn(dict) -> dict` に通す。list を返す |
| `zstd_compress` / `zstd_decompress` | HTML の可逆圧縮 |

公開しない: 生 SQL を取る関数、`sqlite_rows` / `sqlite_row` / `SqliteRowRef`、`sqlite_map_html`。`process_map` / `glob_paths` / `meta_tags` / `append_csv` / `write_text` / `TreeParser.meta` は削除する（公開しない、残さない）。

---

## 4. 内部実装契約

失敗は loguru、関数名は `[name]` 付き。pandas は使わない。  
kit へ移すのは `write_csv` / `write_parquet` / `hash_name` / `write_bytes` / `save_log`。シグネチャと docstring は変えない。`_ensure_parent` は kit に移し、vault 用は同名の私有関数を vault に置いてよい。`append_csv` / `write_text` は移さない。

読みと書きで共有してよい私有は **`_quote_ident` と `_connect` だけ。** 参照一覧や 1 行 dict は `.map` の裏に閉じる。

### 識別子
表名と列名はダブルクォートで囲む。`"` を含む名前は `ValueError`。  
INSERT は位置 `?`。値はスキーマの列順。

```python
def _quote_ident(name: str) -> str:
    if not name or '"' in name:
        raise ValueError(f'invalid identifier: {name!r}')
    return f'"{name}"'
```

### 接続 `_connect(path: Path, readonly: bool) -> sqlite3.Connection`

共通:

- `timeout=30.0`
- `conn.row_factory = sqlite3.Row`
- `PRAGMA busy_timeout = 5000`

書き込み:

- `_ensure_parent(path)` は `open_sqlite` 側
- `sqlite3.connect(path, timeout=30.0)`（URI にしない）
- `PRAGMA journal_mode = WAL;`
- `PRAGMA synchronous = NORMAL;`

読み込み:

- WAL pragma は発行しない
- URI: `Path(path).resolve().as_uri() + '?mode=ro'` を `sqlite3.connect(uri, uri=True, timeout=30.0)`
- Windows のバックスラッシュを path として渡さない

`with sqlite3.connect(...)` は接続を閉じない。必ず自前で `close()` する。

### `open_sqlite`

```python
def open_sqlite(
    path: str | Path,
    *,
    readonly: bool = False,
) -> ContextManager[SqliteDb]:
```

`contextlib.contextmanager` でよい。extract はこれを使わない（`readonly=True` は `db.raw` 用の逃げ道として残してよい）。

1. `path = Path(path)`。書き込みなら `_ensure_parent(path)`
2. `conn = _connect(path, readonly)`
3. `yield SqliteDb(conn)`
4. `finally`:
   - 書き込みなら `PRAGMA wal_checkpoint(TRUNCATE)`（失敗はログ、その後も close する）
   - `conn.close()`

checkpoint は `.db` 単体でコピーできるようにするため。

### `read_sqlite` / `SqliteRead` / `SqliteReadTable`

公開は 3 段、各 1 仕事。

```python
def read_sqlite(path: str | Path) -> SqliteRead:
    ...

class SqliteRead:
    def table(self, name: str) -> SqliteReadTable: ...

class SqliteReadTable:
    def map(
        self,
        fn: Callable[[dict[str, Any]], dict[str, Any] | None],
        *,
        workers: int | None = None,
        chunksize: int | None = None,
    ) -> list[dict[str, Any]]: ...
```

- `read_sqlite`: `Path(path).resolve()` だけ保持。ファイルは開かない。`with` にしない
- `table`: 名前を `_quote_ident` で検証して保持。存在確認も SELECT もしない。他メソッドなし
- `map`: ここで実行して終わる。`where` / `insert` / `raw` / `__iter__`（親に dict を出す）は足さない
- 書き込みの `SqliteTable` とクラスを混ぜない
- `.map` を `open_sqlite` の `SqliteDb` に足さない

### `.map` の内部（4 段）

Process にする理由は kit 互換ではなく、selectolax / zstd の C 側が落ちても親が死なないこと。スレッド池にしない。tqdm はここ（`unit='row'`）。chunksize 省略時は旧 `process_map` と同じ自動式でよい。

1. **親・短い readonly 接続**  
   `SELECT rowid FROM "table" ORDER BY rowid` → `list[int]`。BLOB は読まない。すぐ `close`。表が無い等はログして再送出。空表なら `[]`
2. **ProcessPoolExecutor**  
   `initializer` で子を一度だけ用意する。タスクの pickle は **rowid の int だけ**
3. **子の initializer（ワーカーあたり 1 回）**  
   `_connect(path, readonly=True)`。表名と `fn` を束ねる。`fn` はトップレベルであること（Windows spawn）
4. **子の 1 件**  
   `SELECT * FROM "table" WHERE rowid = ?` → `dict(row)` → `fn(row)`。例外はログして `None`。接続は initializer のものを使い回す（1 行 1 connect にしない）

`executor.map` が順序を保つ。戻りから例外由来の `None` だけ除く。goto 失敗は呼び出し側が同じキーの dict を返すので残る。`fn` が `None` を返して行を落とすフィルタには使わない。

親で `SELECT *` してから並列してはいけない。

### `SqliteDb`

```python
class SqliteDb:
    def __init__(self, conn: sqlite3.Connection) -> None: ...
    @property
    def raw(self) -> sqlite3.Connection: ...
    def create_table(self, name: str, columns: Mapping[str, str]) -> SqliteTable: ...
```

他のメソッドを足さない。`create` 別名禁止。`columns` が空なら `ValueError`。順序は dict の挿入順。

```sql
CREATE TABLE IF NOT EXISTS "pages" (
    "url_index" INTEGER,
    "request_url" TEXT NOT NULL,
    "html_zstd" BLOB
)
```

型欄に `'TEXT NOT NULL'` を書いてよい。ALTER しない。`IF NOT EXISTS` は落ちた scrape の再開用。readonly で `create_table` したら例外をログして再送出。

### `SqliteTable`

コンテキストマネージャにしない。寿命は `open_sqlite`。

```python
class SqliteTable:
    def insert(self, row: Mapping[str, Any]) -> bool: ...
```

保持: `conn`、表名、列名のタプル（スキーマ順）。

- `set(row) != set(列名)` ならログして `False`
- `INSERT INTO "t" ("c1", "c2") VALUES (?, ?)` を列順で `execute` → すぐ `commit`
- 例外はログ、`rollback`、`False`
- `None` は SQL の NULL。圧縮は呼び出し側が済ませてから渡す

### `zstd_compress` / `zstd_decompress`

```python
def zstd_compress(data: str | bytes, level: int = 3) -> bytes: ...
def zstd_decompress(data: bytes) -> bytes: ...
```

- `zstandard` のワンショット
- `str` は `encode('utf-8')` strict（`errors='replace'` は使わない）
- 解凍は `bytes` のまま
- `None` は扱わない。呼び出し側が `zstd_compress(x) if x else None`
- 例外は握りつぶさない。`.map` 内ならその行が `None` になりリストから除かれる

辞書圧縮は今回やらない。レベル既定 3。

### `tree_parser` / `TreeParser`

`tree.py` のこのクラスだけ変える。`TreeNode` と同じ欠落の入れ物にする。

```python
def tree_parser(parser: LexborHTMLParser | None) -> TreeParser:
    return TreeParser(parser)
```

- `__init__` の引数と `_parser` は `LexborHTMLParser | None`
- `__bool__` は `self._parser is not None`（`TreeNode` と同じ）
- `.raw` は `LexborHTMLParser | None`
- `into`: parser が無ければ `tree_node(None)`。`into_every`: 空の `TreeNodeGroup`
- `meta()` は削除する。`into` / `attr` は残る
- 空 HTML を捏造して parser を常に本物にしない
- `TreePage` / `tree_page` / `_PageScoped._page` は今のまま `Page`。`None` を受けない
- `parse_html` のシグネチャは変えない

---

## 5. 使用例

ディレクトリや run 名は呼び出し側の都合。

```text
data/runs/2026-10-18_030000.db
parquet/extract.parquet
```

`crawl.py` は今どおり `urls.csv`（`write_csv`。sqlite にしない）。INDEX は作らない。必要なら `db.raw.execute('CREATE INDEX ...')`。

括弧つき `with` でインデントを一段に収める。1 ページごとに sqlite を開閉しない。

### scrape.py

```python
from datetime import datetime, timezone

import pandas as pd

from aodaisho.tree import tree_page
from aodaisho.live import RecycleEvery, open_patchright
from aodaisho.kit import from_here, save_log
from aodaisho.vault import open_sqlite, zstd_compress

here = from_here(__file__)
save_log(here('log/scraping.log'))

PAGES = {
    'url_index': 'INTEGER',
    'saved_at': 'TEXT',
    'request_url': 'TEXT NOT NULL',
    'final_url': 'TEXT',
    'http_status': 'INTEGER',
    'error_reason': 'TEXT',
    'html_zstd': 'BLOB',
}

items = list(pd.read_csv(here('csv/urls.csv'))['url'].items())
db_path = here('data/runs/2026-10-18_030000.db')

with (
    open_sqlite(db_path) as db,
    open_patchright(
        browser_options={'channel': 'chrome', 'headless': False},
        context_options={'viewport': {'width': 1920, 'height': 1080}},
        recycle=RecycleEvery(browser=300, context=100),
    ) as session,
):
    pages = db.create_table('pages', PAGES)
    for url_index, request_url in items:
        tree = tree_page(session.next_page())
        response = tree.goto(request_url)
        pages.insert({
            'url_index': url_index,
            'saved_at': datetime.now(timezone.utc).isoformat(),
            'request_url': request_url,
            'final_url': tree.raw.url if response else None,
            'http_status': response.status if response else None,
            'error_reason': None if response else 'goto',
            'html_zstd': zstd_compress(tree.raw.content()) if response else None,
        })
```

- HTML 先頭へ `meta_tags` を結合しない。メタは列。`write_text` / `append_csv` は使わない。`hash_name` は画像ファイル用（HTML パスには使わない）
- 失敗も成功も同じ dict。`insert` は 1 回。`tree.raw.content()` は `response` が truthy のときだけ評価される
- `{**row, **extra}` で BLOB を parquet に流さない（extract も同じ）
- 画像は `write_bytes`。この表に載せない

### extract.py

```python
from typing import Any

from aodaisho.tree import tree_parser
from aodaisho.kit import from_here, parse_html, write_parquet, save_log
from aodaisho.vault import read_sqlite, zstd_decompress


def main() -> None:
    here = from_here(__file__)
    save_log(here('log/extract.log'))
    db_path = here('data/runs/2026-10-18_030000.db')
    rows = read_sqlite(db_path).table('pages').map(extract)
    write_parquet(here('parquet/extract.parquet'), rows)


def extract(row: dict[str, Any]) -> dict:
    blob = row['html_zstd']
    tree = tree_parser(parse_html(zstd_decompress(blob)) if blob else None)
    return {
        'url_index': row['url_index'],
        'saved_at': row['saved_at'],
        'request_url': row['request_url'],
        'final_url': row['final_url'],
        'goto_status': row['http_status'],
        'error_reason': row['error_reason'],
        '取り扱い店舗': tree.into_every('p').seeking.one(r'取り扱い店舗').after('p').text,
        '価格': tree.into_every('dt').seeking.one(r'価格').after('dd').text,
    }


if __name__ == '__main__':
    main()
```

- `extract` はトップレベル（initializer 経由で子へ pickle できること）
- 取得失敗もパース失敗も **同じキー**。値が `None`
- `tree` はいつも `TreeParser`。セレクタに `if tree else None` を付けない。dict を二枚にしない
- メタは `row[...]`。`tree.meta` は使わない（メソッド自体を消す）
- `return {}` にしない
- 行を落とさない。`if r is not None` は呼び出し側に書かない（例外行だけ `.map` が除く）
- `with` は使わない

---

## 6. 足さないもの

- 既定カラム、`save_page`、ORM、Python 型 → SQLite 型
- INDEX 引数、マイグレーション、リレーション
- `SqliteDb` への `.map` / `create` 別名
- `SqliteReadTable` への `where` / `__iter__` / 親 dict 化
- 遅延 dict（`[]` で初めて SELECT）
- 親で全行 `SELECT *`、`pd.read_sql`
- vault から `parse_html` / `tree_parser` を呼ぶこと
- `sqlite_rows` / `sqlite_row` / `SqliteRowRef` / `sqlite_map_html` の公開
- extract 内フィルタ（HTML ありだけ、先頭 N 件）
- `kit.process_map` / `glob_paths` / `meta_tags` / `append_csv` / `write_text` / `TreeParser.meta` の残置
- `pack.py` 新設、今の vault の `dump` 改名、書き出しを vault に残したまま sqlite を足すこと
- URL 一覧を sqlite にすること（キューと再生バッファを混ぜない）
- 画像を pages の BLOB にすること
- `session.page()` の別名、`session.next_tree()`、`TreePage.url` / `content()`
- `TreePage` が `None` を持つこと、`tree_page` が `Page | None` を受けること
- extract 用 `html_tree`、空 HTML 番兵、キー一覧で `None` 埋め
- 読みの `with` 必須化

---

## 7. 実装順とチェックリスト

製品の終わりに互換レイヤは無い。**作業の途中で削除対象を消さない。** 旧実装を見ながら新側を足す。README とテストが新 API に乗ってから、削除対象だけまとめて消す。

### やってはいけない

- 序盤で `process_map` / `glob_paths` / `meta_tags` / `append_csv` / `write_text` / `TreeParser.meta` を消す
- `vault` の `.map` が `kit.process_map` を import する、または呼ぶ（kit と vault は互いに import しない）
- 削除対象を kit に移して仮置きする（`append_csv` / `write_text` を kit へ退避しない）
- 削除を残したまま「完了」にする

### 参照（消すまでコードを残す）

- `kit.process_map` / `_SafeWorker` / `_auto_chunksize` … ProcessPool + tqdm + 行単位で例外を吞む。chunksize 省略時の式。`.map` の裏に**書き写す**（呼び出さない）
- 旧 README の crawl / scrape / extract / discover … 呼び出し側の対照。新例は本資料 §5
- `vault.append_csv` / `vault.write_text` … 置換直前まで今の `vault.py` を見る。kit に移さない

### 順（この番号）

1. 残す関数だけ vault → kit（`write_csv` / `write_parquet` / `hash_name` / `write_bytes` / `save_log`）。シグネチャ維持。削除対象はまだ触らない
2. `pyproject.toml` に `zstandard`
3. `TreeParser` が `LexborHTMLParser | None` を持つ。`into` / `into_every` は欠落時空。`meta()` はまだ消さない。`TreePage` は触らない
4. `live.py` の `page()` を `next_page()` に改名（`page()` は残さない）
5. `vault.py` に sqlite + zstd を足す。kit / tree / pandas / selectolax を import しない。`.map` の並列は kit の `process_map` を見て内部に書く（呼ばない）。この時点で `append_csv` / `write_text` が同じファイルに残っていてもよい（公開し直さない、kit に移さない）
6. 単体: 書き込み → `read_sqlite....map` で dict が戻る、圧縮往復、キー不一致 insert が `False`、goto 失敗行が残る、例外行だけ欠ける。`open_sqlite` が WAL + checkpoint + close。`.map` が rowid のみ親、initializer で子接続、タスクは int。`create_table` / `insert` が識別子クォート、列集合一致、即 commit、失敗時 rollback。`zstd_compress` が UTF-8 strict
7. README を kit / vault の新 import、`next_page()`、`tree.raw`、`read_sqlite....map`、`tree_parser(None)`、`row[...]` に直す。旧例（`meta_tags` / `tree.meta` / `write_text` / `append_csv` / `glob_paths` / `process_map`）は README から消す。関数自体はまだ残っていてよい
8. **最後（これより前に削除しない）:** `process_map` / `_SafeWorker` / `_auto_chunksize` / `glob_paths` / `meta_tags` / `append_csv` / `write_text` / `TreeParser.meta` を消す。vault に sqlite 以外の書き出しが残っていたら除く

### チェックリスト

- [ ] 1. 残す関数を kit へ。削除対象は残す
- [ ] 2. `zstandard`
- [ ] 3. `TreeParser` が None を持つ。`meta()` は残す
- [ ] 4. `next_page()`
- [ ] 5. vault が sqlite + zstd。`.map` は `process_map` を呼ばない
- [ ] 6. 単体が通る
- [ ] 7. README が新 API
- [ ] 8. 削除対象をまとめて消す（1〜7 が終わってから）
