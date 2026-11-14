# スクレイピング・データパイプライン設計要件

使う側の短さと取り回しを優先する。SQL は手打ちしない。

## 1. 基本思想

* **置き場**
  * **SQLite（run ごとに1ファイル）**: 収集の作業領域であり、生 HTML（`zstd` BLOB）のアーカイブでもある。`urls`（キュー）と `pages`（結果）を同居させ、CSV を介さない。中断・再開・追記はここだけで完結する。
  * **Parquet**: 抽出後の表（`extract.parquet` / `clean.parquet`）専用。列指向の旨みが出るデータだけ置く。
* **生データはパースせず保存する**
  * 収集時は HTML をパースしない。`zstd` BLOB として SQLite に逐次保存する。抽出ロジックはあとから何度でも `map` で再実行する。
* **BLOB 付き表は Parquet に焼かない**
  * 行単位の変換（1ページ分の HTML を1ワーカーへ）は行志向の SQLite が筋。Parquet は列志向で、この処理と相性が悪い。完成後の「触りたくない」はフォーマット変更ではなく、収集後は `open_sqlite` で書き込まないことで満たす。
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

extract を `with open_sqlite(..., readonly=True)` に寄せない。

### 接続セッション: `with open_sqlite(path) as db`

| メソッド | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- |
| **`ensure_table`** | `name: str`, `schema: dict[str, str]` | `None` | `CREATE TABLE IF NOT EXISTS`。既存表は変更しない。upsert の判定キーはここには置かない。 |
| **`table`** | `name: str` | `Table` | 既存テーブルを開く唯一の窓口。resume のたびにスキーマを再宣言してハンドルを得ない。 |

`ensure_table` はスキーマ保証、`table` は取得。役割を分ける（CQS のための `None` にこだわらないが、2行で揃える）。

### `Table`

| メソッド | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- |
| **`insert`** | `row: dict[str, Any]` | `bool` | 普通の `INSERT`。URL 集め用。「無かったら足す」。`OR IGNORE` もフラグも付けない。失敗はログして `False`。省略した列は SQLite に任せる。 |
| **`upsert`** | `row: dict[str, Any]`, `on: str` | `bool` | 同じキーなら上書き。`INSERT ... ON CONFLICT(on) DO UPDATE`。`INSERT OR REPLACE` は使わない（行を消して入れ直す）。`on` はこの書き込みの話なので `upsert` 側に置く。 |
| **`to_df`** | `*columns: str` | `pd.DataFrame` | 指定列だけ読む（BLOB を飛ばして差分計算する）。引数なしは全列。**list は受けない**（列名が変数なら `to_df(*cols)`）。 |

`insert` と `upsert` は動詞が違うので両方残す。`Table` に `map` は生やさない。

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

UNIQUE はスキーマの約束。

* crawl（`insert`）: 想定内の重複は入れる前に弾く（`seen`）。当たったら失敗してログ。黙って捨てない。
* scrape（`upsert`）: 同じ `request_url` は上書き。失敗行も残す。

## 3. スキーマ

スクレイピングプロジェクトの `schema.py`（副作用なし。`crawl.py` から import しない）。

```python
URLS_SCHEMA = {
    'url': 'TEXT UNIQUE',
    'saved_at': 'TEXT',
}

PAGES_SCHEMA = {
    'saved_at': 'TEXT',
    'request_url': 'TEXT NOT NULL UNIQUE',
    'final_url': 'TEXT',
    'http_status': 'INTEGER',
    'html_zstd': 'BLOB',
}
```

`pages` の1行は、その URL を取りに行った結果（成功でも失敗でも）。`html_zstd` は失敗時 `None`。再実行は **まだ 200 でない `urls`** を取る（pages に無い = 未着手も含む）。200 の行は触らない。

## 4. パイプライン実装例

ブラウザ部分は `fetch_urls` / `fetch` に省略。vault の使い方だけを示す。

```python
from schema import URLS_SCHEMA, PAGES_SCHEMA
from aodaisho.kit import now_jst
```

### ① crawl.py

発見した URL を逐次 `insert` する。途中落ちても `seen` ごと再開できる。

```python
with open_sqlite(db_path) as db:
    db.ensure_table('urls', URLS_SCHEMA)
    urls_table = db.table('urls')
    seen = set(urls_table.to_df('url')['url'])

    for url in fetch_urls():
        if url in seen:
            continue
        urls_table.insert({
            'url': url,
            'saved_at': now_jst(),
        })
        seen.add(url)
```

### ② scrape.py

未完了だけアクセスする。保存は `upsert`。identity は `request_url`。

```python
with open_sqlite(db_path) as db:
    db.ensure_table('urls', URLS_SCHEMA)
    db.ensure_table('pages', PAGES_SCHEMA)
    urls_table = db.table('urls')
    pages_table = db.table('pages')

    df_urls = urls_table.to_df('url')
    df_pages = pages_table.to_df('request_url', 'http_status')
    done = set(df_pages.loc[df_pages['http_status'].eq(200), 'request_url'])
    todo = df_urls.loc[~df_urls['url'].isin(done), 'url']

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

* `INSERT OR IGNORE`、および ignore 用の引数
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
* `map_sqlite`: プロセスプール、ワーカーは path から readonly で開く（Windows の spawn）、`tqdm`、`None` は捨てる。行は dict
* 失敗は loguru。`insert` / `upsert` はロールバックして `False`
* 1行ごとに commit（中断してもそこまで残る）

今の公開契約は使わない。`create_table`、全列一致の `insert`、`read_sqlite`、`open_sqlite(..., readonly=True)` を extract 入口にすること、接続の上の `map`。

`map_sqlite` の並列の切り方も、今の「1行ずつ `WHERE rowid = ?`」に合わせなくてよい。プロセスプールと path からの開き直しという要点だけ借りて、`rowid` 範囲スキャンなどにしてよい。
