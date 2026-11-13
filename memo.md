# スクレイピング・データパイプライン設計要件

使う側の短さと取り回しを優先する。SQL は手打ちしない。

## 1. 基本思想

* **置き場**
  * **SQLite（run ごとに1ファイル）**: 収集の作業領域であり、生 HTML（`zstd` BLOB）のアーカイブでもある。`urls`（キュー）と `pages`（結果）を同居させ、CSV を介さない。中断・再開・追記はここだけで完結する。
  * **Parquet**: 抽出後の表（`extract.parquet` / `clean.parquet`）専用。列指向の旨みが出るデータだけ置く。
* **生データはパースせず保存する**
  * 収集時は HTML をパースしない。`zstd` BLOB として SQLite に逐次保存する。抽出ロジックはあとから何度でも `map` で再実行する。
* **BLOB 付き表は Parquet に焼かない**
  * 行単位の `map`（1ページ分の HTML を1ワーカーへ）は行志向の SQLite が筋。Parquet は列志向で、今の `map`（行ごと）と相性が悪い。完成後の「触りたくない」はフォーマット変更ではなく、読み取り専用 API で満たす。
* **書き込みと読み取りを分ける**
  * 収集中: `open_sqlite`（追記・resume）。
  * 収集後: `read_sqlite` のみ（readonly）。discover / extract / 再抽出はここから `map` する。同じファイルを `open_sqlite` で開き直さない。
  * 手打ち SQL（`pd.read_sql`）も、生ページの `pd.read_parquet` もしない。
* **スキーマの正本はスクレイピングプロジェクト側**
  * aodaisho は表の形を知らない。列定義はプロジェクト内の `schema.py` に置き、crawl / scrape が同じ dict で `ensure_table` する。run ファイルが複数でも、形はプロジェクトで1つ。

## 2. API

### 書き込み: `with open_sqlite(path) as db`

| メソッド | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- |
| **`ensure_table`** | `name: str`, `schema: dict[str, str]` | `None` | `CREATE TABLE IF NOT EXISTS`。既存表は変更しない。upsert の判定キーはここには置かない。 |
| **`table`** | `name: str` | `Table` | 既存テーブルを開く唯一の窓口。resume のたびにスキーマを再宣言してハンドルを得ない。 |

`ensure_table` はスキーマ保証、`table` は取得。役割を分ける（CQS のための `None` にこだわらないが、2行で揃える）。

### `Table`（書き込み側）

| メソッド | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- |
| **`insert`** | `row: dict[str, Any]` | `bool` | 普通の `INSERT`。URL 集め用。「無かったら足す」。`OR IGNORE` もフラグも付けない。失敗はログして `False`。省略した列は SQLite に任せる（`INTEGER PRIMARY KEY` の自動採番など）。 |
| **`upsert`** | `row: dict[str, Any]`, `on: str` | `bool` | 同じキーなら上書き。`INSERT ... ON CONFLICT(on) DO UPDATE`。`INSERT OR REPLACE` は使わない（行を消して入れ直すと `url_index` が振り直される）。`on` はこの書き込みの話なので `upsert` 側に置く。 |
| **`to_df`** | `*columns: str` | `pd.DataFrame` | 指定列だけ読む（BLOB を飛ばして差分計算する）。引数なしは全列。**list は受けない**（列名が変数なら `to_df(*cols)`）。 |

`insert` と `upsert` は動詞が違うので両方残す。書き込み中の `Table` に `map` は生やさない。`map` はプロセスを分けて path から開き直す読み取り専用の仕事。

### 読み取り: `read_sqlite(path).table(name)`

| メソッド | 引数 | 戻り値 | 説明 |
| :--- | :--- | :--- | :--- |
| **`map`** | `fn: Callable[[dict], dict \| None]` | `list[dict]` | 全行をプロセスプールで `fn` に渡す。`None` は捨てる。`tqdm` 付き。入力は常に1行分の dict（BLOB 含む）。 |

`fn` の中で `zstd_decompress` → パースする。並列の切り方は実装側（`rowid` 範囲スキャンなど）。呼び出し側は今と同じ `map(fn)` だけ。

### 重複の扱い

UNIQUE はスキーマの約束。

* crawl（`insert`）: 想定内の重複は入れる前に弾く（`seen`）。当たったら失敗してログ。黙って捨てない。
* scrape（`upsert`）: 同じ `request_url` は上書き。失敗行も残す。

## 3. スキーマ

スクレイピングプロジェクトの `schema.py`（副作用なし。`crawl.py` から import しない）。

```python
URLS_SCHEMA = {
    'url_index': 'INTEGER PRIMARY KEY',  # 省略 INSERT で自動採番。resume してもずれない
    'url': 'TEXT UNIQUE',
    'created_at': 'TEXT',
}

PAGES_SCHEMA = {
    'url_index': 'INTEGER UNIQUE',
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
            'created_at': datetime.now(timezone.utc).isoformat(),
        })
        seen.add(url)
```

### ② scrape.py

未完了だけアクセスする。`url_index` は crawl 時点の安定 ID を使う（`enumerate` しない）。保存は `upsert`。

```python
with open_sqlite(db_path) as db:
    db.ensure_table('urls', URLS_SCHEMA)
    db.ensure_table('pages', PAGES_SCHEMA)
    urls_table = db.table('urls')
    pages_table = db.table('pages')

    df_urls = urls_table.to_df('url_index', 'url')
    df_pages = pages_table.to_df('request_url', 'http_status')
    done = set(df_pages.loc[df_pages['http_status'].eq(200), 'request_url'])
    todo = df_urls[~df_urls['url'].isin(done)]

    for url_index, url in todo[['url_index', 'url']].itertuples(index=False):
        res = fetch(url)
        pages_table.upsert({
            'url_index': url_index,
            'saved_at': datetime.now(timezone.utc).isoformat(),
            'request_url': url,
            'final_url': res.url if res else None,
            'http_status': res.status if res else None,
            'html_zstd': zstd_compress(res.content) if res else None,
        }, on='request_url')
```

`todo` は URL の set に落とさない（`url_index` が要る）。`pages` の失敗行だけを `todo` にすると、未着手が落ちる。

完走後、この SQLite はアーカイブになる。以降は `read_sqlite` だけ使う。

### ③ discover.py / extract.py

SQLite から直接 `map`。生ページを pandas で全件ロードしない。失敗行は blob が無いので `fn` が `None` を返せば捨てられる。

```python
rows = read_sqlite(db_path).table('pages').map(extract)
write_parquet(here('parquet/extract.parquet'), rows)


def extract(row: dict) -> dict | None:
    blob = row['html_zstd']
    if not blob:
        return None
    tree = tree_parser(parse_html(zstd_decompress(blob)))
    return {
        'url_index': row['url_index'],
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
* `INSERT OR REPLACE`（`url_index` が振り直される）
* `ensure_table(..., on=...)`（判定キーは `upsert` 側）
* `to_df(['a', 'b'])`（list）。`to_df('a', 'b')` か `to_df(*cols)` だけ
* 書き込み `Table.map`（接続の役割が混ざる）
* 生 HTML の `raw_pages.parquet` 化、およびそれを入力にした extract
* 収集完了と同時に SQLite を破棄する
* extract / discover からのプロセスプール削除
* 手打ち SQL
* サイト用スキーマを aodaisho に置く / `crawl.py` からスキーマを import する
