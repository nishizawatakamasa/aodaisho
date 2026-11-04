自分用・非汎用

## インストール

`uv add aodaisho`

`open_patchright` を使うとき：Google ChromeをPCにインストールしておく。  
`open_camoufox` を使うとき：`uv run camoufox fetch`

## 使用例

### crawl.py

```python
from urllib.parse import urlencode

from loguru import logger

from aodaisho.tree import tree_page
from aodaisho.live import RecycleEvery, open_patchright
from aodaisho.kit import from_here, save_log, write_csv

here = from_here(__file__)
save_log(here('log/crawling.log'))

with open_patchright(
    browser_options={'channel': 'chrome', 'headless': False},
    context_options={'viewport': {'width': 1920, 'height': 1080}},
    recycle=RecycleEvery(browser=300, context=100, page=20),
) as session:
    tree = tree_page(session.next_page())
    tree.goto('https://home.katitas.jp/buyers_search')
    prefecture_urls = tree.into_every('div ul li a[href^="https://home.katitas.jp/buyers_search/area"]').urls

    n = len(prefecture_urls)
    urls = []
    for i, prefecture_url in enumerate(prefecture_urls, 1):
        print(f'prefecture_url {i}/{n}')
        for page_num in range(1, 200):
            tree = tree_page(session.next_page())
            if not tree.goto(f'{prefecture_url}?{urlencode({"page": page_num})}'):
                break
            if not (bukken_elems := tree.into_every('ul li div a[href^="https://home.katitas.jp"]:has(p)')):
                break
            urls.extend(bukken_elems.urls)
        else:
            logger.warning(f'page limit reached: {prefecture_url!r}')
write_csv(here('csv/urls.csv'), [{'url': url} for url in set(urls)])
```

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
    table = db.create_table('pages', {
        'url_index': 'INTEGER',
        'saved_at': 'TEXT',
        'request_url': 'TEXT NOT NULL',
        'final_url': 'TEXT',
        'http_status': 'INTEGER',
        'error_reason': 'TEXT',
        'html_zstd': 'BLOB',
    })
    for url_index, request_url in items:
        tree = tree_page(session.next_page())
        response = tree.goto(request_url)
        table.insert({
            'url_index': url_index,
            'saved_at': datetime.now(timezone.utc).isoformat(),
            'request_url': request_url,
            'final_url': tree.raw.url if response else None,
            'http_status': response.status if response else None,
            'error_reason': None if response else 'goto',
            'html_zstd': zstd_compress(tree.raw.content()) if response else None,
        })
```

### discover.py

```python
from typing import Any

import pyperclip

from aodaisho.tree import tree_parser
from aodaisho.kit import from_here, parse_html
from aodaisho.vault import read_sqlite, zstd_decompress


def main() -> None:
    here = from_here(__file__)
    db_path = here('data/runs/2026-10-18_030000.db')
    results = read_sqlite(db_path).table('pages').map(labels_in_row)
    labels = [label for row in results for label in row['labels']]
    pyperclip.copy('\n'.join(set(labels)))


def labels_in_row(row: dict[str, Any]) -> dict:
    blob = row['html_zstd']
    tree = tree_parser(parse_html(zstd_decompress(blob)) if blob else None)
    return {
        'labels': [t.strip() for t in tree.into_every('dt').texts if t and t.strip()],
    }


if __name__ == '__main__':
    main()
```

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

### clean.ipynb

```python
import re

import pandas as pd
```
```python
df_shikutyoson = pd.read_csv('./shikutyoson.csv')
cities = df_shikutyoson["市区町村"].dropna().sort_values(key=lambda x: x.str.len(), ascending=False)
shikutyoson_pattern = "|".join(cities.map(lambda x: re.escape(x)))
```
```python
df_raw = pd.read_parquet('parquet/extract.parquet')
df_raw = df_raw.apply(lambda x: x.fillna('').str.normalize('NFKC').str.strip())
```
```python
# df_raw['現況'].unique()
```
```python
def normalize_ws(s):
    return s.replace(r'\s+', ' ', regex=True)
```
```python
df = df_raw.sort_values('saved_at')[['url_index', 'saved_at', 'request_url', 'final_url']].copy()

df['事例種別'] = df_raw['物件種別'].str.contains(r'中古|土地').map({True: '中古売出'})
df['総額'] = (
    df_raw['価格']
    .str.extract(r'([,\d]+)\s*万円', expand=False)
    .replace(',', '', regex=True)
    .pipe(lambda s: pd.to_numeric(s, errors='coerce') * 10000)
)
df['土地面積'] = df_raw['土地面積'].str.extract(r'([\d\.]+)', expand=False)

s1 = df_raw['建物面積'].str.extract(r'([\d\.]+)', expand=False)
s2 = df_raw['専有面積'].str.extract(r'([\d\.]+)', expand=False)
df['建物面積'] = s1.fillna(s2)

df['建物種別'] = df_raw['物件種別'].map({'中古戸建': '戸建て', '中古マンション': 'マンション', '土地': '土地'})
df[['所在都道府県', '所在市', '所在字', '所在番地']] = df_raw['所在地'].str.extract(fr'^(京都府|.+?[都道府県])({shikutyoson_pattern})(\D*)(.*)')

s1 = (
    df_raw['築年月']
    .replace({r'元年': r'1年'}, regex=True)
    .str.extract(r'(\d+)年', expand=False)
    .pipe(lambda s: pd.to_numeric(s, errors='coerce'))
)
s2 = df_raw['築年月'].str[:2].map({'令和': 2018, '平成': 1988, '昭和': 1925, '大正': 1911, '明治': 1867})
df['建築年'] = s1 + s2

s1 = df_raw['建物構造'].str.extract(r'^(\S+)', expand=False)
s2 = df_raw['構造・階建て'].str.extract(r'^(\S+)', expand=False)
df['構造体'] = s1.fillna(s2)

s1 = df_raw['建物構造'].str.extract(r'(\d+)階', expand=False)
s2 = df_raw['構造・階建て'].str.extract(r'(\d+)階', expand=False)
df['階層'] = s1.fillna(s2)

df['リノベ内容'] = df_raw['備考'].str.extract(r'(?s)^(20\d{2}/.*?)\n\D', expand=False)
df['間取り'] = normalize_ws(df_raw['間取り'])
df['成約年月'] = df_raw['現況'].map({
    '空': '販売中',
    '古家付': '販売中',
    '築後未入居': '販売中',
    '更地': '販売中',
    '居住中': '不明',
    '賃貸中': '不明',
})
df['私道負担'] = normalize_ws(df_raw['私道面積'])
df['接道'] = normalize_ws(df_raw['接道状況'])

s1 = df_raw['最寄りの学校'].str.extract(r'([^/\s【】・、(]+?小学校)', expand=False) 
s2 = df_raw['物件の魅力'].str.extract(r'([^/\s【】・、(]+?小学校)', expand=False)
s3 = df_raw['備考'].str.extract(r'([^/\s【】・、(]+?小学校)', expand=False)
s4 = df_raw['img_desc'].str.extract(r'([^/\s【】・、(]+?小学校)', expand=False) 
df['小学校'] = s1.fillna(s2).fillna(s3).fillna(s4)

s1 = df_raw['最寄りの学校'].str.extract(r'([^/\s【】・、(]+?中学校)', expand=False) 
s2 = df_raw['物件の魅力'].str.extract(r'([^/\s【】・、(]+?中学校)', expand=False)
s3 = df_raw['備考'].str.extract(r'([^/\s【】・、(]+?中学校)', expand=False)
s4 = df_raw['img_desc'].str.extract(r'([^/\s【】・、(]+?中学校)', expand=False) 
df['中学校'] = s1.fillna(s2).fillna(s3).fillna(s4)

df['周辺環境'] = df_raw['備考'].map(lambda x: '\n'.join(l for l in x.splitlines() if re.search(r'(?:\d分|\dm)$', l)))
df['都市計画'] = normalize_ws(df_raw['都市計画'])
df['用途地域'] = normalize_ws(df_raw['用途地域'])
df[['建ぺい率', '容積率']] = df_raw['建ぺい率 /容積率'].str.extract(r'(\d+%)\D*(\d+%)')
df['水道'] = df_raw['設備・条件'].str.extract(r'(公営水道|上水道)', expand=False)
df['下水'] = df_raw['設備・条件'].str.extract(r'(本下水|個別浄化槽|汲取|下水道)', expand=False)
df['ガス'] = df_raw['設備・条件'].str.extract(r'(個別LPG|集中LPG|都市ガス|プロパンガス|オール電化)', expand=False)
df['契約態様'] = normalize_ws(df_raw['取引態様'])
df['問合せ先'] = normalize_ws(df_raw['取り扱い店舗'])
df['駐車場'] = normalize_ws(df_raw['駐車場'])
df['交通'] = normalize_ws(df_raw['交通'])
df['物件の特徴'] = normalize_ws(df_raw['物件の魅力'])
df['仕様'] = normalize_ws(df_raw['設備・条件'])

df['土地権利'] = normalize_ws(df_raw['土地権利'])
df['地目'] = normalize_ws(df_raw['地目'])
df['引渡日（入居予定日）'] = normalize_ws(df_raw['引渡日（入居予定日）'])
df['物件番号'] = normalize_ws(df_raw['物件番号'])
df['情報更新日'] = normalize_ws(df_raw['情報更新日'])
```
```python
df.to_parquet('parquet/clean.parquet')
# df.to_clipboard(index=False)
```
