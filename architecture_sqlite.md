# SQLite 保存ヘルパ 実装仕様

`architecture_sqlite_migration.md` はたたき台。**この文書が実装の正本。**  
スキーマ・run の置き場・成功/失敗の列は呼び出し側の例であり、ライブラリの契約ではない。

依存に `zstandard` を `pyproject.toml` へ追加する。モジュール配置は下の「モジュール」を正とする。

---

## 1. 設計思想

### 何を守るか
描画後 HTML を正本にする。サイトのセレクタが壊れても、ローカルで何度でも再抽出できる。scrape と extract を切り、取得失敗を再クロールにしない。

梱包だけを変える。1 ページ 1 HTML ファイルをやめる理由は、定期実行のファイル数・メタの `<meta>` 埋め込み・`failed.csv` の分断・非圧縮・`write_text(..., errors='replace')` による毀損。思想は変えない。

### 道具の境界
aodaisho はドメインを持たない。`write_text` も `append_csv` も `process_map` も列名を知らない。SQLite 化でも同じ。

ツールに入れるのは、間違えると遅いか壊れる機械部分だけ。

- 接続の開閉（親ディレクトリ、WAL、timeout、明示 `close`、書き込み終了時の checkpoint）
- 列定義辞書からの `CREATE TABLE IF NOT EXISTS` と dict `INSERT`
- 1 行ごとの `commit`
- UTF-8 strict と zstd
- extract 用の「参照一覧」と「子で 1 行読む」（`glob_paths` + `read_bytes` 相当）
- 子プロセスの接続再利用

呼び出し側が書くもの。

- 表名、`{列名: 型文字列}`、run のパス
- insert / extract の戻り dict のキーと値
- HTML のどの列を解凍するか、パースとセレクタ
- 行のフィルタ、クレンジング

既定の `pages` 表、`save_page(url, html)`、ORM、Python 型マッピングは置かない。似た列が続いてもライブラリのデフォルトに昇格させない。例は README / 本資料の呼び出し側に置く。

### 正本と派生
- run の `.db` は再抽出用の再生バッファ（直近だけ残してファイルごと消してよい）
- `extract.parquet` 以降が長期の倉庫
- 時系列の絞り込みは parquet / notebook。生 HTML を 1 本の巨大 DB に積まない

### scrape と extract の温度
どちらも **dict のキーを自分で書く。** ツールがメタを自動結合しない。空 `{}` を和集合で埋めない。成功も失敗も同じキー、値が `None` なだけ。scrape の `insert({...})` と同じ形。

### モジュール
モジュール名は技術名ではなく役割の名詞（`live` は Playwright ではない、`tree` は selectolax ではない）。金庫に当たるのは書き出し関数ではなく、**1 run の正本 sqlite** である。

```text
kit    道具（パス、並列、パース、csv / parquet / log / text の書き出し）
live   生きているブラウザ
tree   DOM
vault  1 run の正本（sqlite + zstd）
```

今の `vault.py`（`append_csv` / `write_csv` / `write_parquet` / `hash_name` / `write_text` / `write_bytes` / `save_log`）は **`kit.py` へ移す。** parquet は派生表で正本ではない。`save_log` も金庫ではない。`glob_paths` がすでに kit にあるので、書き出しを kit に寄せても境界は今より悪くならない。

sqlite API（`open_sqlite` / `create_table` / `sqlite_rows` / `sqlite_row` / zstd）は空になった `vault.py` に置く。`from aodaisho.vault import open_sqlite, sqlite_rows` が名前と一致する。

やらないこと:

- `pack.py` を足して今の vault を残す（金庫の意味がずれたままモジュールだけ増える）
- 今の vault を `dump` 等に改名する（寄せ集めに一語を当てると kit/live/tree より弱い）
- sqlite も parquet もログも全部 vault に残す（また名前がぼやける）

`tree.py` / `live.py` は変更しない。vault は kit / tree / pandas / selectolax を import しない（`_ensure_parent` は vault 側に小さく残してよい。kit に依存しない）。kit は vault を import しない。

README の `from aodaisho.vault import write_csv` 等は kit に直す。個人用 0.1.0 なので互換レイヤは置かない。

---

## 2. 機能の思想

### パイプライン

```text
crawl.py     URL リスト（今どおり urls.csv でよい）
scrape.py    ブラウザで取得 → 1 run 1 sqlite（メタ列 + 圧縮 HTML）
extract.py   全行を map → メタと抽出列を並べた dict のリスト → parquet
clean.ipynb  フィルタ・正規化・特徴量（pandas）
```

extract はフィルタではない。取得失敗行も残す。落とすのは clean。

```text
pages（sqlite）                         extract の戻り dict
url_index, request_url, error_reason,  →  呼び出し側が選んだメタ
html_zstd, ...                             + HTML から展開した列
                                           （html_zstd を出すかどうかも呼び出し側）
```

### 読みの形（現行ファイル抽出の進化）

```text
旧: glob_paths(...)          → process_map(extract, paths)
    extract(path): Path.read_bytes()

新: sqlite_rows(path, table) → process_map(extract, refs)
    extract(ref):  row = sqlite_row(ref)   # 子で 1 行 dict
```

`process_map` はそのまま使う。sqlite 用の並列 API は足さない。

親が渡してよいのは path / 表名 / rowid だけ。親で `SELECT *` して dict のリストを `process_map` に渡すのは禁止（BLOB が親メモリと pickle に乗る。pandas 一括と同じ）。

`fn(bytes)` やパース済み `tree` をツールが渡さない。vault は DOM / selectolax / `parse_html` を import しない。解凍も extract 内（scrape の `zstd_compress` と対称）。

### 命名
- `open_sqlite(...) as db`（`connect_sqlite` にしない。`open_patchright` に合わせる）
- `open_patchright(...) as session`（`as s` / `as browser` にしない）
- `db.create_table`（`create` でも `table()` でもない）
- 公開メソッドは `create_table` と `.raw` だけ（`tree` の `.raw` と同じ逃げ道）

---

## 3. 公開 API

| 名前 | 役割 |
|---|---|
| `open_sqlite` | 接続の寿命。WAL・checkpoint・`close`。`SqliteDb` を yield |
| `SqliteDb.create_table` | `{列: 型}` から CREATE。`SqliteTable` を返す |
| `SqliteDb.raw` | 生の `sqlite3.Connection` |
| `SqliteTable.insert` | dict を 1 行 INSERT して `commit` |
| `SqliteRowRef` | `process_map` に渡す安い参照（path, table, rowid） |
| `sqlite_rows` | 表の全 rowid を `SqliteRowRef` のリストに（`glob_paths` 相当） |
| `sqlite_row` | 子で 1 行を `dict` にする（`read_bytes` 相当） |
| `zstd_compress` / `zstd_decompress` | HTML の可逆圧縮 |

公開しない: 生 SQL を取る `sqlite_insert` / `sqlite_keys` / `sqlite_fetch`、`sqlite_map` / `sqlite_map_html`。

`kit.process_map` は今のまま。vault と kit は互いに import しない。

---

## 4. 内部実装契約

失敗は loguru、関数名は `[name]` 付き（今の `vault.py` / `kit.py` と同じ）。pandas は使わない。  
kit へ移す関数のシグネチャと docstring は変えない。`_ensure_parent` は kit に移し、vault 用は同名の私有関数を vault に置いてよい。

### 識別子
表名と列名はダブルクォートで囲む。`"` を含む名前は `ValueError`。  
INSERT は名前付きプレースホルダではなく **位置 `?`**（クォートした列とぶつからない）。値はスキーマの列順。

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
- Windows のバックスラッシュを path として渡さない。`as_uri()` を使う

`with sqlite3.connect(...)` は接続を閉じない。必ず自前で `close()` する。

### `open_sqlite`

```python
def open_sqlite(
    path: str | Path,
    *,
    readonly: bool = False,
) -> ContextManager[SqliteDb]:
```

`contextlib.contextmanager` でよい。

1. `path = Path(path)`。書き込みなら `_ensure_parent(path)`
2. `conn = _connect(path, readonly)`
3. `yield SqliteDb(conn)`
4. `finally`:
   - 書き込みなら `PRAGMA wal_checkpoint(TRUNCATE)`（失敗はログ、その後も close する）
   - `conn.close()`

SQL は受け取らない。checkpoint は `.db` 単体でコピーできるようにするため（`-wal` を本体へ巻き込む）。

### ワーカー接続（`sqlite_row` 用）

モジュールレベルの `dict[str, sqlite3.Connection]`。キーは `str(Path(path).resolve())`。

- 無ければ `_connect(..., readonly=True)` して入れる
- `Connection` を pickle しない（子で作る）
- 1 行ごとに connect しない（ここを怠ると extract が遅くなる）
- プロセス終了で破棄してよい。`atexit` は必須ではない

親の `open_sqlite` と子のキャッシュは別プロセスなので共有しない。

### `SqliteDb`

```python
class SqliteDb:
    def __init__(self, conn: sqlite3.Connection) -> None: ...
    @property
    def raw(self) -> sqlite3.Connection: ...
    def create_table(self, name: str, columns: Mapping[str, str]) -> SqliteTable: ...
```

他のメソッドを足さない。`create` 別名禁止。

`columns` が空なら `ValueError`。順序は dict の挿入順のまま。

内部 SQL 例（列はクォート、型文字列はそのまま）:

```sql
CREATE TABLE IF NOT EXISTS "pages" (
    "url_index" INTEGER,
    "request_url" TEXT NOT NULL,
    "html_zstd" BLOB
)
```

`'TEXT NOT NULL'` のように、型欄に制約を書いてよい。ALTER しない。`IF NOT EXISTS` なので既存表の列は変わらない（落ちた scrape の再開用）。

readonly 接続で `create_table` したら SQLite が失敗する。例外はログして再送出（握りつぶさない）。

### `SqliteTable`

コンテキストマネージャにしない。寿命は `open_sqlite`。

```python
class SqliteTable:
    def insert(self, row: Mapping[str, Any]) -> bool: ...
```

保持するもの: `conn`、表名、列名のタプル（スキーマ順）。

`insert`:

- `set(row) != set(列名)` ならログして `False`（欠けも余分も不可）
- `INSERT INTO "t" ("c1", "c2") VALUES (?, ?)` を列順で `execute`
- すぐ `commit`。成功 `True`
- 例外はログ、`rollback`、`False`（`write_text` と同じ bool）
- `None` は SQL の NULL。圧縮は呼び出し側が済ませてから渡す
- Python 型から SQLite 型への変換はしない

### `SqliteRowRef`

```python
@dataclass(frozen=True, slots=True)
class SqliteRowRef:
    path: str
    table: str
    rowid: int
```

`path` は `str`（`glob_paths` と同じく pickle しやすくする）。`sqlite_rows` が `str(Path(path).resolve())` を入れる。

### `sqlite_rows`

```python
def sqlite_rows(path: str | Path, table: str) -> list[SqliteRowRef]:
```

- 内部で `open_sqlite(path, readonly=True)`
- `SELECT rowid FROM "table" ORDER BY rowid`
- 各行を `SqliteRowRef(path=..., table=table, rowid=int)` にする
- 空表なら `[]`
- BLOB を読まない。SQL は内部だけ。WHERE で絞らない（extract は全行）

### `sqlite_row`

```python
def sqlite_row(ref: SqliteRowRef) -> dict[str, Any] | None:
```

- **ワーカー（または単一プロセスの呼び出し）から使う。** 親が全 ref に対してループで呼ぶ想定ではない
- キャッシュした READ ONLY 接続で `SELECT * FROM "table" WHERE rowid = ?`
- 1 行を `dict(row)` にする（列名 → 値。BLOB は `bytes`、NULL は `None`）
- 行が無い／例外: ログして `None`

### `zstd_compress` / `zstd_decompress`

```python
def zstd_compress(data: str | bytes, level: int = 3) -> bytes: ...
def zstd_decompress(data: bytes) -> bytes: ...
```

- `zstandard.compress` / `zstandard.decompress` のワンショット
- `str` は `data.encode('utf-8')`（strict。`errors='replace'` は使わない）
- 解凍は `bytes` のまま（`parse_html` は `str | bytes` を受け取る）
- `None` は扱わない。呼び出し側が `zstd_compress(x) if x else None`
- 例外は握りつぶさない。extract 内なら `process_map` が `None` にする

辞書圧縮は今回やらない。レベル既定 3。

---

## 5. 使用例

ディレクトリや run 名は呼び出し側の都合。

```text
data/runs/2026-10-18_030000.db
parquet/extract.parquet
```

`crawl.py` は今どおり `urls.csv`。INDEX は作らない。必要なら `db.raw.execute('CREATE INDEX ...')`。

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
        page = session.page()
        tree = tree_page(page)
        response = tree.goto(request_url)
        pages.insert({
            'url_index': url_index,
            'saved_at': datetime.now(timezone.utc).isoformat(),
            'request_url': request_url,
            'final_url': page.url if response else None,
            'http_status': response.status if response else None,
            'error_reason': None if response else 'goto',
            'html_zstd': zstd_compress(page.content()) if response else None,
        })
```

- HTML 先頭へ `meta_tags` を結合しない
- 失敗も成功も同じ dict。`insert` は 1 回。`page.content()` は `response` が truthy のときだけ評価される
- `{**row, **extra}` で BLOB を parquet に流さない（extract も同じ）

### extract.py

```python
from aodaisho.tree import tree_parser
from aodaisho.kit import from_here, parse_html, process_map, write_parquet, save_log
from aodaisho.vault import sqlite_rows, sqlite_row, zstd_decompress


def main() -> None:
    here = from_here(__file__)
    save_log(here('log/extract.log'))
    db_path = here('data/runs/2026-10-18_030000.db')
    refs = sqlite_rows(db_path, 'pages')
    results = process_map(extract, refs)
    write_parquet(
        here('parquet/extract.parquet'),
        [r for r in results if r is not None],
    )


def extract(ref) -> dict | None:
    row = sqlite_row(ref)
    if row is None:
        return None
    blob = row['html_zstd']
    parser = parse_html(zstd_decompress(blob)) if blob else None
    tree = tree_parser(parser) if parser else None
    return {
        'url_index': row['url_index'],
        'saved_at': row['saved_at'],
        'request_url': row['request_url'],
        'final_url': row['final_url'],
        'goto_status': row['http_status'],
        'error_reason': row['error_reason'],
        '取り扱い店舗': (
            tree.into_every('p').seeking.one(r'取り扱い店舗').after('p').text
            if tree else None
        ),
        '価格': (
            tree.into_every('dt').seeking.one(r'価格').after('dd').text
            if tree else None
        ),
    }


if __name__ == '__main__':
    main()
```

- `extract` はトップレベル（子へ pickle できること）
- 取得失敗もパース失敗も **同じキー** を返す。値が `None`
- `return {}` にしない（キーが消える）
- `WHERE html_zstd IS NOT NULL` で行を落とさない
- `process_map` が例外で `None` にした分だけ、`write_parquet` の前で除く（既存仕様。取得失敗とは別）
- `sqlite_row` は extract の先頭で一度。そのあとが `row: dict -> dict`

---

## 6. 足さないもの

- 既定カラム、`save_page`、ORM、Python 型 → SQLite 型
- INDEX 引数、マイグレーション、リレーション
- `SqliteDb` へのメソッド追加（`map` / `create` 別名）
- 遅延 dict（`[]` で初めて SELECT）
- 親で全行 dict 化、`pd.read_sql(SELECT *)`
- vault から `parse_html` / `tree_parser` を呼ぶこと
- extract 用の生 SQL 公開、`sqlite_map_html`
- extract 内フィルタ（HTML ありだけ、先頭 N 件）
- `pack.py` 新設、今の vault の `dump` 等への改名、書き出しを vault に残したまま sqlite を足すこと

---

## 7. 実装チェックリスト

- [ ] 今の `vault.py` の書き出し関数を `kit.py` へ移す（シグネチャ維持）
- [ ] README の import を kit / 新しい vault に直す
- [ ] `vault.py` を sqlite + zstd にする。kit / tree / pandas / selectolax を import しない
- [ ] `pyproject.toml` に `zstandard`
- [ ] `open_sqlite` が書き込みで WAL + checkpoint + close、読み込みで `mode=ro` URI
- [ ] `create_table` / `insert` が識別子をクォートし、insert は列集合一致・即 commit・失敗時 rollback
- [ ] `sqlite_rows` が BLOB を読まず `ORDER BY rowid`
- [ ] `sqlite_row` がワーカー接続をキャッシュする
- [ ] `zstd_compress` が UTF-8 strict
- [ ] 単体: 書き込み → `sqlite_rows` → `sqlite_row` で dict が戻る、圧縮往復、キー不一致 insert が `False`
