
# スクレイピングデータ永続化基盤 設計書（SQLite + zstd 移行）

## 1. 背景と設計思想 (Philosophy)

### 思想
* **道具の疎結合（UNIX哲学）：** 重厚なフレームワーク（Scrapy等）や特定サーバー（PostgreSQL等）に依存せず、Python標準と軽量なツール群で完結するポータブルな構成を保つ。
* **環境非依存・身軽さ：** コンテナや外部DBを立てることなく、ファイルごと移動・バックアップ・管理ができるポータビリティを最優先する。
* **完全なデータ無毀損：** サイト側のセレクタ変更やパース失敗時、何度でもローカルでオフライン再抽出を可能にするため、ブラウザ描画後の生HTML（`page.content()`）を1バイトも毀損せずに完全可逆で保持する。
* **クラッシュ耐性（即時コミット）：** ネットワーク断やブラウザクラッシュが発生しても、直前に取得した1ページまで確実にディスクに保存する。

---

## 2. コアコンセプト：Run（実行）単位のシングルファイルDB

定期実行によるファイル数の爆発とライフサイクル管理（古いデータの削除）を解決するため、**「1回の定期実行 ＝ 1つのSQLiteファイル」** とする。

### ディレクトリ構成
```text
data/
  ├── runs/
  │     ├── 2026-10-18_030000.db   # 1回の定期実行ごとに1ファイル生成
  │     ├── 2026-10-19_030000.db
  │     └── ...
parquet/
  └── extract.parquet              # 抽出結果の蓄積
```

### なぜこの構成か
* **実行の区別：** ファイル名そのものが `Run ID`（実行日時）となり、実行ごとの管理・比較が自明になる。
* **削除の容易さ：** 不要になった過去データの削除は、対象日付の `.db` ファイルを `unlink`（または手動ゴミ箱移動）するだけで完了する（OSのファイル数負荷ゼロ）。
* **容量の極小化：** 生HTMLを `zstandard (zstd)` で可逆圧縮して格納することで、元の容量の 1/8 〜 1/10 に抑える。

---

## 3. データスキーマ設計 (Schema)

成功データだけでなく、**取得失敗情報（エラーログ）も同一テーブルに統合**し、データの分断（`failed.csv` などの乱立）を解消する。

### テーブル定義：`pages`
```sql
CREATE TABLE IF NOT EXISTS pages (
    url_index    INTEGER,          -- 取得順インデックス
    saved_at     TEXT,             -- 取得完了日時 (ISO 8601 UTC)
    request_url  TEXT NOT NULL,    -- リクエストしたURL
    final_url    TEXT,             -- リダイレクト後の最終URL (失敗時NULL許容)
    http_status  INTEGER,          -- HTTPステータスコード (失敗時NULL許容)
    error_reason TEXT,             -- 成功時はNULL、失敗時はエラー理由 ('goto', 'timeout' 等)
    html_zstd    BLOB              -- zstd圧縮された生HTMLバイナリ (失敗時はNULL)
);

CREATE INDEX IF NOT EXISTS idx_pages_status ON pages(error_reason);
```

---

## 4. 全体パイプラインの流れ (Pipeline Flow)

```text
[crawl.py] (URLリスト作成)
    │
    ▼ urls.csv
[scrape.py] (Playwright/Patchright で巡回)
    │  ・1ページごとに zstd 圧縮
    │  ・1ページごとに INSERT & conn.commit() (即時物理保存)
    ▼
data/runs/YYYY-MM-DD_HHMMSS.db (生データDB)
    │
    ▼ (pd.read_sql で読み込み ➡ to_dict で辞書リスト化)
[extract.py] (ローカルオフライン抽出)
    │  ・process_map (並列ワーカー)
    │  ・メモリ上で zstd 解凍 ➡ selectolax で超高速パース
    ▼
parquet/extract.parquet (構造化データ)
    │
    ▼
[clean.ipynb] (Pandas によるクレンジング・正規化・GBDT特徴量化)
```

---

## 5. 技術選定と実装指針

### 採用スタック
* **ランタイム：** Python 3.12+
* **ブラウザ自動化：** `patchright` / `camoufox`（非ヘッドレス、描画後HTML取得）
* **DBエンジン：** Python標準 `sqlite3`（WALモード有効化）
* **データ圧縮：** `zstandard`（圧縮レベル 3：速度と圧縮率のベストバランス）
* **HTMLパーサー：** `selectolax.lexbor.LexborHTMLParser`（超高速C/Rustバインディング）
* **並列処理：** `concurrent.futures.ProcessPoolExecutor`（`tqdm` ラップの `process_map`）
* **データ操作：** `pandas`（SQLは最小限にし、DataFrame経由で操作）

### 実装上の注意点（AIエージェント向け）
1. **SQLiteの接続管理：**
   * Pythonの `with sqlite3.connect(...)` は接続を自動で `close()` しない（トランザクション制御のみ）。無駄なインデントを避け、明示的に `conn = sqlite3.connect(...)` して最後に `conn.close()` する素朴なスタイルを採用すること。
   * 書き込み時は `PRAGMA journal_mode = WAL;` を発行して高速化とクラッシュ耐性を高めること。
2. **メタデータの分離：**
   * 従来のように生HTMLの先頭に `<meta>` 文字列を結合する小細工は廃止し、メタデータはテーブルのカラム値として独立して扱うこと。
3. **Pandasとの連携：**
   * 抽出フェーズでは、SQLで複雑な結合を行わず、`pd.read_sql('SELECT * FROM pages WHERE html_zstd IS NOT NULL', conn)` で一括ロードし、`df.to_dict(orient='records')` 経由でマルチプロセスのワーカー関数へ流すこと。
