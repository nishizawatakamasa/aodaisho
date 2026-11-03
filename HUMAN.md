# sqlite 化 作業指示

この文書は **作業のやり方** である。仕様の正本ではない。

文脈（設計チャット、口頭の経緯）は不要。ここから始めてよい。

| 文書 | 役割 |
|---|---|
| **`architecture_sqlite.md`** | 実装の正本。API・契約・例・足さないもの・§7 の番号 |
| **この `HUMAN.md`** | 誰が何をどの順で切るか、AI への出し方、人間の検収 |
| **`README.md`** | 今は旧梱包の例。§7 の 7 で新例に差し替える。それまで対照用 |

仕様が口頭やこの文書と食い違ったら **`architecture_sqlite.md` を取る。**  
仕様のコピーをプロンプト用 md に増やさない。正本が割れる。

---

## 何をする仕事か

aodaisho は自分用のスクレイピング道具である。ドメイン（物件の列名など）は持たない。

**今:** scrape が 1 URL 1 HTML ファイル。メタは HTML 先頭の `<meta>`。失敗は `failed.csv`。extract は `glob_paths` + `process_map`。

**あと:** scrape が 1 run 1 sqlite（メタは列、HTML は zstd BLOB）。失敗も同じ `insert`。extract は `read_sqlite(...).table(...).map(fn)`。思想（描画後 HTML が正本、再抽出できる）は変えない。梱包だけ変える。

リポジトリは小さい（`aodaisho/*.py` が本体、テストはまだ無い）。失敗するのは量ではなく、**足さないものを足す**ことと **削除を先にやる**こと。

---

## 読む順

1. この文書の「完成の絵」と「原則」と「バッチ」
2. `architecture_sqlite.md` を通読（飛ばさない。§1 境界、§4 契約、§5 例、§6 足さないもの、§7 順）
3. 現状コード: `aodaisho/{kit,vault,tree,live}.py`、`README.md`、`pyproject.toml`

実装中に仕様を決めたくなったら、推測で API を足さず正本を直すか、質問して止まる。

---

## 完成の絵

呼び出し側のパイプライン（正本 §2 / §5）:

```text
crawl.py     urls.csv のまま（sqlite にしない）
scrape.py    ブラウザ → 1 run 1 sqlite
extract.py   read_sqlite.table.map → parquet
clean.ipynb  今どおり pandas（対象外でも壊さない）
```

ライブラリ:

- `kit` … `from_here` / `parse_html` / `save_log` / `write_csv` / `write_parquet` / `write_bytes` / `hash_name`（画像ファイル名）
- `vault` … `open_sqlite` / `read_sqlite` / `zstd_compress` / `zstd_decompress` だけ（csv 等は置かない）
- `tree` … `TreeParser` が `LexborHTMLParser | None` を持つ。`meta()` は最終的に無い。`TreePage` は今のまま
- `live` … `page()` が無く `next_page()` だけ

終わったあとに **無い** もの: `process_map` / `glob_paths` / `meta_tags` / `append_csv` / `write_text` / `TreeParser.meta`。互換レイヤは置かない。

---

## 原則（人間も AI も）

1. **削除は最後。** 作業中は旧関数を残して参照する。`process_map` を序盤で消さない。
2. **`.map` は `process_map` を見ても、呼んでも、import してもいけない。** 中身（ProcessPool + tqdm + 例外を吞む + chunksize 式）を vault の裏に書き写す。kit と vault は互いに import しない。
3. **削除対象を kit に仮置きしない。** `append_csv` / `write_text` は移さない。
4. **`TreePage` は触らない。** `TreeParser` 以外の tree 変更は `meta()` 削除（バッチ D）だけ。
5. **正本 §6 を実装しない。** 既定テーブル、`save_page`、`.map` の `where`、`html_tree`、URL 一覧の sqlite、画像 BLOB など。
6. **設計チャットの続きで実装しない。** 口頭の経緯より文書。実装は新規チャット。
7. **AI にチェックリストを自己完了させない。** バッチのあと人間が検収して次を切る。

---

## バッチ（この四つ。番号ごと 8 チャットにしない）

正本 §7 の番号 1〜8 を、リスクで四つに束ねる。1〜4 は機械的なのでまとめる。5 と 6 は分けない（テスト無しの vault は完了にしない）。7 と 8 は分ける（README の勢いで関数まで消す）。

| バッチ | 正本 §7 | 中身 | 次に進む条件 |
|---|---|---|---|
| **A** | 1〜4 | 残す関数を kit へ。`zstandard`。`TreeParser` が None を持つ（`meta()` は残す）。`next_page()` | 下の検収 A |
| **B** | 5〜6 | vault を sqlite + zstd + `.map`。単体テスト新設 | 検収 B。テストが通る |
| **C** | 7 | README だけ新 API。関数はまだ消さない | 検収 C |
| **D** | 8 | 削除対象をまとめて消す | 検収 D。これで完了 |

バッチの途中で「ついでに」他番号をやらない。A のチャットで vault を書き始めない。D より前に削除対象を消さない。

人手でも同じ四つでよい。AI にやらせるなら **バッチごとに新規チャット**（A のスレで B を続けない。文脈が膨らみ、足さないものを足す）。

---

## AI に出すとき

1. **新規チャット**（前バッチのスレを続けない。設計チャットにも混ぜない）
2. Agent モード
3. 下の該当ブロックを **そのまま全部** 貼る。前置きと追記を自分で組み立てない
4. `@architecture_sqlite.md` と `@HUMAN.md` が付く。Cursor ならそのままで読める

プロンプト用の別仕様 md は作らない。

### バッチ A（§7 の 1〜4）

```text
@architecture_sqlite.md @HUMAN.md
正本は architecture_sqlite.md。作業の切り方は HUMAN.md。口頭の経緯より文書。
今やるのは HUMAN.md のバッチ A（正本 §7 の 1〜4）だけ。バッチ B/C/D は触らない。vault の sqlite 化も README も削除もやらない。
§6 足さないものは実装しない。推測で API を足さない。
削除対象（process_map / glob_paths / meta_tags / append_csv / write_text / TreeParser.meta）は消さない・kit に移さない。
vault は kit / tree / pandas / selectolax を import しない（このバッチで vault を sqlite にしない）。

やること（正本 §7 の 1〜4）:
1. 残す関数だけ vault → kit（write_csv / write_parquet / hash_name / write_bytes / save_log）。シグネチャと docstring は変えない。_ensure_parent は kit に移し、vault 用は同名の私有を残してよい。append_csv / write_text は kit に移さない（vault に残す）。
2. pyproject.toml に zstandard を足す。
3. TreeParser が LexborHTMLParser | None を持つ。into / into_every は欠落時空ノード / 空グループ。meta() は残す。TreePage は触らない。
4. live.py の page() を next_page() に改名する。page() の別名は残さない。docstring と live.py 内の例も直す。README はまだ旧例のままでよい。

終わったら、やった番号とやらなかった番号を正本 §7 どおりに報告する。
HUMAN.md の検収 A を自分で「完了」にしない。人間が見る。
```

### バッチ B（§7 の 5〜6）

```text
@architecture_sqlite.md @HUMAN.md
正本は architecture_sqlite.md。作業の切り方は HUMAN.md。口頭の経緯より文書。
今やるのは HUMAN.md のバッチ B（正本 §7 の 5〜6）だけ。バッチ A のやり直し・C の README・D の削除はやらない。
§6 足さないものは実装しない。推測で API を足さない。
削除対象（process_map / glob_paths / meta_tags / append_csv / write_text / TreeParser.meta）は消さない・kit に移さない。
vault は kit / tree / pandas / selectolax を import しない。
.map は kit.process_map を見ても呼んでも import してもいけない。中身（ProcessPool + tqdm + 行単位で例外を吞む + chunksize 省略時の式）を vault の裏に書き写す。

やること（正本 §7 の 5〜6）:
5. vault.py に sqlite + zstd を足す。公開は正本 §3。内部契約は正本 §4。この時点で append_csv / write_text が同じファイルに残っていてもよい（公開し直さない、kit に移さない）。kit へ移済みの write_csv 等を vault に残さない。
6. 単体は正本 §7-6 の箇条書きを全部満たす。テストが無ければ作る。pytest を dev 依存にしてよい。ブラウザ・ネットワークは使わない。extract 用 fn はトップレベル（Windows spawn）。
   満たすこと: 書き込み → read_sqlite....map で dict が戻る。圧縮往復（UTF-8 strict）。キー不一致 insert が False。goto 失敗相当（blob None）の行が同じキー・値 None で残る。fn が例外を出した行だけリストから欠ける。open_sqlite が WAL + checkpoint + close。.map が rowid のみ親、initializer で子接続、タスクは int。create_table / insert が識別子クォート、列集合一致、即 commit、失敗時 rollback。zstd_compress が UTF-8 strict。zstd_* は None を扱わない。

終わったら、やった番号とやらなかった番号を正本 §7 どおりに報告する。テストの走らせ方を書く。
HUMAN.md の検収 B を自分で「完了」にしない。人間が見る。
```

### バッチ C（§7 の 7）

```text
@architecture_sqlite.md @HUMAN.md
正本は architecture_sqlite.md。作業の切り方は HUMAN.md。口頭の経緯より文書。
今やるのは HUMAN.md のバッチ C（正本 §7 の 7）だけ。コードの関数削除はバッチ D。vault の再設計もしない。
§6 足さないものは実装しない。推測で API を足さない。
削除対象（process_map / glob_paths / meta_tags / append_csv / write_text / TreeParser.meta）は関数としてコードに残す。README の例からだけ消す。kit に移さない。

やること（正本 §7 の 7）:
README だけ直す。新例は正本 §5。
- crawl: write_csv、next_page()、tree.raw。save_log は kit。URL 一覧は urls.csv のまま（sqlite にしない）。
- scrape: open_sqlite、next_page()、tree.raw、zstd_compress。meta_tags / write_text / hash_name（HTML パス）/ append_csv / s.page() を使わない。
- extract: read_sqlite.table.map、tree_parser(parse_html(zstd_decompress(blob) if blob else None))、メタは row[...]。tree.meta を使わない。
- discover: glob_paths / process_map をやめて read_sqlite....map。
import を kit / vault の新配置に揃える。関数自体は消さない。

終わったら、やった番号とやらなかった番号を正本 §7 どおりに報告する。
HUMAN.md の検収 C を自分で「完了」にしない。人間が見る。
```

### バッチ D（§7 の 8）

```text
@architecture_sqlite.md @HUMAN.md
正本は architecture_sqlite.md。作業の切り方は HUMAN.md。口頭の経緯より文書。
今やるのは HUMAN.md のバッチ D（正本 §7 の 8）だけ。新しい API を足さない。README の例を作り直さない（残参照だけ直す）。
§6 足さないものは実装しない。

やること（正本 §7 の 8）:
次をコードから消す。参照が残っていたら削除を止めて報告する。
- process_map / _SafeWorker / _auto_chunksize
- glob_paths
- meta_tags
- append_csv
- write_text
- TreeParser.meta
vault に sqlite 以外の書き出し（csv / parquet / save_log 等）が残っていたら除く。
kit の残す関数（from_here / parse_html / save_log / write_csv / write_parquet / write_bytes / hash_name）は消さない。
検収 B のテストがまだ通ることを確認する。

終わったら、消した名前と残した名前を報告する。
HUMAN.md の検収 D を自分で「完了」にしない。人間が見る。
```

---

## 人間の検収（AI の自己申告で次へ行かない）

リポジトリルートで行う。コマンドは目安。Windows でも同じ意図ならよい。

削除対象（D まで残っているべき）:

```text
process_map  glob_paths  meta_tags  append_csv  write_text  TreeParser.meta
```

### 検収 A（1〜4 のあと）※人間がチェック済、無事完了。

- [ ] `from aodaisho.kit import write_csv, write_parquet, hash_name, write_bytes, save_log` できる
- [ ] 上の関数のシグネチャが、移す前の vault 版と同じ（docstring も）
- [ ] `kit.process_map` / `kit.glob_paths` / `kit.meta_tags` がまだある
- [ ] `append_csv` / `write_text` が kit に増えていない
- [ ] `pyproject.toml` に `zstandard`
- [ ] `tree_parser(None)` が `TreeParser` を返す。`into` / `into_every` が空ノード / 空グループ。`.text` が `None`
- [ ] `TreeParser.meta` がまだある
- [ ] `TreePage` / `_PageScoped._page` が `Page` のまま（`None` を受けない）
- [ ] `live.page` が無く `next_page` がある。別名 `page()` が残っていない

### 検収 B（5〜6 のあと）※人間がチェック済、無事完了。

- [ ] `from aodaisho.vault import open_sqlite, read_sqlite, zstd_compress, zstd_decompress`
- [ ] vault が kit / tree / pandas / selectolax を import していない
- [ ] `.map` の実装が `process_map` を呼んでいない（grep）
- [ ] 公開が正本 §3 の表どおり。`sqlite_rows` 等を公開していない
- [ ] 単体が通る。少なくとも:
  - 書き込み → `read_sqlite....map` で dict が戻る
  - 圧縮往復（UTF-8 strict、`None` は圧縮関数に渡さない）
  - キー不一致 `insert` が `False`
  - goto 失敗相当（blob `None`）の行が、同じキー・値 `None` で残る
  - `fn` が例外を出した行だけリストから欠ける
- [ ] 削除対象の関数がまだコード上にある
- [ ] README はまだ旧例のままでよい

### 検収 C（7 のあと）※人間がチェック済、無事完了。

- [ ] README の crawl / scrape / extract / discover が正本 §5 の形（`next_page`、`tree.raw`、`read_sqlite....map`、`row[...]`、`tree_parser(None)`）
- [ ] README に `meta_tags` / `tree.meta` / `write_text` / `append_csv` / `glob_paths` / `process_map` / `s.page()` が無い
- [ ] crawl が `write_csv` のまま（URL 一覧を sqlite にしていない）
- [ ] 削除対象の **関数** がまだコード上にある

### 検収 D（8 のあと）＝完了

- [ ] 削除対象の名前が `aodaisho/` と README から無い
- [ ] vault に sqlite 以外の書き出し（csv / parquet / `save_log` 等）が無い
- [ ] kit に残す関数は残っている
- [ ] 検収 B のテストがまだ通る
- [ ] 正本 §6 のものが実装されていない

---

## 人手でやる場合

バッチ A→B→C→D と同じ。正本 §7 の「順」と「やってはいけない」に従う。

B の `.map` は `aodaisho/kit.py` の `process_map` / `_SafeWorker` / `_auto_chunksize` を開いて写す。呼び出しに置き換えない。Windows では worker が spawn されるので、テスト用 `fn` もトップレベル。

テストランナーはリポジトリにまだ無い。B で足す。ブラウザは使わない。

---

## よくある逸脱（先に折る）

| やりがち | 正 |
|---|---|
| 最初に `process_map` を消す | バッチ D まで残す。参照する |
| `vault.map` が `from aodaisho.kit import process_map` | 禁止。書き写す |
| `append_csv` を kit に移して後で消す | 移さない |
| `tree is None` や dict 二枚 | `tree_parser(None)` で一本 |
| 空 HTML を番兵にする | しない。parser の `None` を包む |
| `TreePage` にも `None` | しない |
| `.process_map` という公開名 | `.map`。Process を名前に出さない |
| URL 一覧も sqlite | `urls.csv` + `write_csv` |
| 画像を pages の BLOB | `write_bytes` |
| `save_page`、既定 `pages` 表 | 呼び出し側の例。ライブラリに置かない |
| README と一緒に関数削除 | C と D を分ける |
| この HUMAN に仕様を追記する | 正本を直す |

---

## 現状メモ（作業開始時）

- Python `>=3.12`。依存は `pyproject.toml`（まだ `zstandard` は無い）
- `aodaisho/vault.py` … csv / parquet / `write_text` / `write_bytes` 等。sqlite は無い
- `aodaisho/kit.py` … `parse_html` / `meta_tags` / `process_map` / `glob_paths`
- `aodaisho/tree.py` … `TreeParser.meta` あり。`tree_parser` は `None` を受けない
- `aodaisho/live.py` … `page()`。`next_page()` は無い
- テストディレクトリは無い

作業が終わったら、正本 §7 のチェックリストを人間が付ける。AI の報告だけで完了にしない。
