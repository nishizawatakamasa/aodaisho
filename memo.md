# スクレイピング・データパイプライン設計要件

使う側の短さと取り回しを優先する。SQL は手打ちしない。ドメインは README の使用例に沿う。

## 1. 基本思想

* **置き場**
  * **SQLite（run ごとに1ファイル）**: その回の作業領域であり、生 HTML（`zstd` BLOB）のアーカイブでもある。`urls`（この回のキュー）と `pages`（結果）を同居させ、CSV を介さない。
  * **Parquet**: 抽出後の表（`extract.parquet` / `clean.parquet`）専用。列指向の旨みが出るデータだけ置く。
* **1 run = crawl 全取り1回 + scrape**
  * crawl は毎回サイトを全走査する。昨日の URL に今日を追記する、という表の使い方はしない。
  * 発見したらその場で `insert`（メモリに溜めて最後に一括、ではない）。
  * 一覧またぎの URL 重複はそのまま入れてよい。一意にするのは scrape 側の `unique()`。
  * crawl の開始時に `urls` を読まない。resume 用の `seen` も持たない。途中落ちたら、その run の crawl はやり直す（README と同じ割り切り）。残った途中行は気にしないか、新しい db ファイルで始める。
  * scrape は長いので、同じ run の db を再実行する。未完了だけ取り、同じ URL は `upsert` で上書きする。
* **生データはパースせず保存する**
  * 収集時は HTML をパースしない。`zstd` BLOB として SQLite に逐次保存する。抽出ロジックはあとから何度でも `map_sqlite` で再実行する。
* **BLOB 付き表は Parquet に焼かない**
  * 行単位の変換は行志向の SQLite が筋。完成後の「触りたくない」は、収集後に `open_sqlite` で書き込まないことで満たす。
* **SQLite の入口はふたつだけ**
  * 基本は全部 `open_sqlite`（接続セッション: DDL / INSERT / 軽い SELECT）。
  * 特殊なのは並列変換だけで、単体関数 `map_sqlite`。discover / extract はここ。db オブジェクトは出さない。
  * 手打ち SQL（`pd.read_sql`）も、生ページの `pd.read_parquet` もしない。
* **スキーマの正本はスクレイピングプロジェクト側**
  * aodaisho は表の形を知らない。列定義はプロジェクト内の `schema.py` に置き、crawl / scrape が同じ dict で `ensure_table` する。run ファイルが複数でも、形はプロジェクトで1つ。

## 2. API

基本の SQLite アクセスはすべて `open_sqlite`。`map_sqlite` は並列変換専用の単体関数で、`open_sqlite` に似せたチェーン（`read_sqlite(path).table().map()`）は作らない。

| | `open_sqlite` | `map_sqlite` |
| :--- | :--- | :--- |
| 形 | `with` で接続を握る | 関数1発。db オブジェクトなし |
| 仕事 | `ensure_table` / `table` → `insert` / `upsert` / `to_df` | 全行をプロセスプールで `fn` に渡す |
| 接続 | 1本 | ワーカーが path から readonly で開き直す |

extract を `with open_sqlite(..., readonly=True)` に寄せない。呼び出し側は `conn.close()` を書かない（開閉はライブラリ内）。

### 接続セッション: `with open_sqlite(path) as db`

| メソッド | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- |
| **`ensure_table`** | `name: str`, `schema: dict[str, str]` | `None` | `CREATE TABLE IF NOT EXISTS`。既存表は変更しない。upsert の判定キーはここには置かない。 |
| **`table`** | `name: str` | `Table` | 既存テーブルを開く唯一の窓口。 |

`ensure_table` はスキーマ保証、`table` は取得。役割を分ける（CQS のための `None` にこだわらないが、2行で揃える）。

### `Table`

| メソッド | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- |
| **`insert`** | `row: dict[str, Any]` | `bool` | 常に1行追加。`on` なし。`OR IGNORE` も DO NOTHING もしない。失敗はログして `False`。省略した列は SQLite に任せる。 |
| **`upsert`** | `row: dict[str, Any]`, `on: str` | `bool` | そのキーで書く。無ければ insert、あれば他列を更新。`INSERT ... ON CONFLICT(on) DO UPDATE`。`INSERT OR REPLACE` は使わない。`on` で指定する列はスキーマで UNIQUE。 |
| **`to_df`** | `*columns: str` | `pd.DataFrame` | 指定列だけ読む（BLOB を飛ばす）。引数なしは全列。**list は受けない**（列名が変数なら `to_df(*cols)`）。 |

`insert` と `upsert` のふたつで足りる。重複エラー専用の第三メソッドも `insert_new` も作らない。`Table` に `map` は生やさない。

### 並列変換: `map_sqlite(path, table, fn)`

| 引数 | 型 | 説明 |
| :--- | :--- | :--- |
| **`path`** | `str \| Path` | SQLite ファイル |
| **`table`** | `str` | テーブル名 |
| **`fn`** | `Callable[[dict], dict \| None]` | 1行分の dict を受け、dict か `None` を返す。`None` は捨てる |

戻り値は `list[dict]`。`write_parquet` にそのまま渡す。`tqdm` 付き。行の形は `insert` / `upsert` と同じ dict（属性アクセス用の Record は作らない）。`fn` の中で `zstd_decompress` → パースする。並列の切り方は実装側（`rowid` 範囲スキャンなど）。

```python
rows = map_sqlite(db_path, 'pages', extract)
write_parquet(here('parquet/extract.parquet'), rows)
```

### 重複の扱い

* crawl（`insert`）: 重複したまま入れる。開始時に表を読まない。
* scrape: キューは `to_df('url')['url'].unique()`。保存は `upsert(..., on='request_url')`。失敗行も残す。再実行はまだ 200 でない URL（未着手を含む）。

## 3. スキーマ

スクレイピングプロジェクトの `schema.py`（副作用なし。`crawl.py` から import しない）。基本は型の箱。`NOT NULL` は付けない。UNIQUE は `upsert` する列だけ。

```python
URLS_SCHEMA = {
    'url': 'TEXT',
    'saved_at': 'TEXT',
}

PAGES_SCHEMA = {
    'saved_at': 'TEXT',
    'request_url': 'TEXT UNIQUE',
    'final_url': 'TEXT',
    'http_status': 'INTEGER',
    'html_zstd': 'BLOB',
}
```

`pages` の1行は、その URL を取りに行った結果（成功でも失敗でも）。`html_zstd` は失敗時 `None`。

## 4. パイプライン実装例

ブラウザ部分は README 相当。vault の使い方だけを示す。

```python
from schema import URLS_SCHEMA, PAGES_SCHEMA
from aodaisho.kit import now_jst
```

### ① crawl.py

発見した URL をその場で `insert` する。表は読まない。

```python
with open_sqlite(db_path) as db:
    db.ensure_table('urls', URLS_SCHEMA)
    urls_table = db.table('urls')

    for url in bukken_elems.urls:
        urls_table.insert({
            'url': url,
            'saved_at': now_jst(),
        })
```

### ② scrape.py

未完了だけアクセスする。URL の重複はここで落とす。保存は `upsert`。

```python
with open_sqlite(db_path) as db:
    db.ensure_table('urls', URLS_SCHEMA)
    db.ensure_table('pages', PAGES_SCHEMA)
    urls_table = db.table('urls')
    pages_table = db.table('pages')

    s_urls = urls_table.to_df('url')['url'].unique()
    df_pages = pages_table.to_df('request_url', 'http_status')
    done = set(df_pages.loc[df_pages['http_status'].eq(200), 'request_url'])
    todo = [u for u in s_urls if u not in done]

    for url in todo:
        res = fetch(url)
        pages_table.upsert({
            'saved_at': now_jst(),
            'request_url': url,
            'final_url': res.url if res else None,
            'http_status': res.status if res else None,
            'html_zstd': zstd_compress(res.content) if res else None,
        }, on='request_url')
```

`pages` の失敗行だけを `todo` にすると、未着手が落ちる。

完走後、この SQLite はアーカイブになる。以降は `open_sqlite` で書き込まず、extract は `map_sqlite` を使う。

### ③ discover.py / extract.py

`map_sqlite` で1行ずつ変換する。生ページを pandas で全件ロードしない。失敗行は blob が無いので `fn` が `None` を返せば捨てられる。

```python
rows = map_sqlite(db_path, 'pages', extract)
write_parquet(here('parquet/extract.parquet'), rows)


def extract(row: dict) -> dict | None:
    blob = row['html_zstd']
    if not blob:
        return None
    tree = tree_parser(parse_html(zstd_decompress(blob)))
    return {
        'request_url': row['request_url'],
        # ...
    }
```

### ④ clean.ipynb

BLOB の無い抽出結果だけ pandas で読む。

```python
df_raw = pd.read_parquet('parquet/extract.parquet')
# ...
df.to_parquet('parquet/clean.parquet')
```

## 5. やらないこと

* `INSERT OR IGNORE`、`ON CONFLICT DO NOTHING`、`insert_new`、ignore 用の引数
* `insert` に `on=` を付ける（常に追加。キー付き書き込みは `upsert` だけ）
* crawl 開始時に `urls` を読む / `seen` で DB と照合する
* 同じ `urls` 表に複数回の crawl を追記する
* `INSERT OR REPLACE`（行を消して入れ直す）
* `ensure_table(..., on=...)`（判定キーは `upsert` 側）
* `to_df(['a', 'b'])`（list）。`to_df('a', 'b')` か `to_df(*cols)` だけ
* `Table.map`、および `read_sqlite(path).table().map()` のようなチェーン（並列変換は `map_sqlite` だけ）
* 行を Record / SimpleNamespace にする（`insert` も parquet も dict）
* 生 HTML の `raw_pages.parquet` 化、およびそれを入力にした extract
* 収集完了と同時に SQLite を破棄する
* extract / discover からのプロセスプール削除
* 手打ち SQL
* サイト用スキーマを aodaisho に置く / `crawl.py` からスキーマを import する
* 今の `vault.py` をパッチして `create_table` をリネームする（公開 API は本 memo で置き換える）

## 6. 実装時: 今の `vault.py` の扱い

公開 API とモジュールの骨格は本 memo が正本。今の `vault.py` はテンプレートにしない。クラス名、関数の切り方、`create_table` 周辺を少し直して残さない。ファイルごと、memo の呼び出しに合わせて書いてよい。

再利用するのは **振る舞いのエッセンス** だけ。名前や配置は捨てて、同じ仕事を memo 側の型に載せ直す。

* 識別子のクオート、親ディレクトリ作成
* 接続: `row_factory`、timeout、書き込みは WAL、読み取りは `mode=ro`
* `with open_sqlite` の寿命: 終了時に WAL を checkpoint して close
* `zstd` の圧縮・解凍（公開名 `zstd_compress` / `zstd_decompress` は今の呼び出しと揃える）
* `map_sqlite`: プロセスプール、ワーカーは path から readonly で開く（Windows の spawn）、`tqdm`、`None` は捨てる。行は dict。呼び出し側は接続を見ない
* 失敗は loguru。`insert` / `upsert` はロールバックして `False`
* 1行ごとに commit（scrape の中断でそこまで残る。バッファして一括 commit しない）

今の公開契約は使わない。`create_table`、全列一致の `insert`、`read_sqlite`、`open_sqlite(..., readonly=True)` を extract 入口にすること、接続の上の `map`。

`map_sqlite` の並列の切り方も、今の「1行ずつ `WHERE rowid = ?`」に合わせなくてよい。プロセスプールと path からの開き直しという要点だけ借りて、`rowid` 範囲スキャンなどにしてよい。
