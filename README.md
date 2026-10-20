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

from aodaisho.core import tree_page
from aodaisho.live import RecycleEvery, open_patchright
from aodaisho.utils import save_log, from_here, write_csv

here = from_here(__file__)
save_log(here('log/crawling.log'))

with open_patchright(
    browser_options={'channel': 'chrome', 'headless': False},
    context_options={'viewport': {'width': 1920, 'height': 1080}},
    recycle=RecycleEvery(browser=300, context=100, page=20),
) as s:
    page = s.page()
    tree = tree_page(page)
    tree.goto('https://home.katitas.jp/buyers_search')
    prefecture_urls = tree.into_every('div ul li a[href^="https://home.katitas.jp/buyers_search/area"]').urls

    n = len(prefecture_urls)
    urls = []
    for i, prefecture_url in enumerate(prefecture_urls, 1):
        print(f'prefecture_url {i}/{n}')
        for page_num in range(1, 200):
            page = s.page()
            tree = tree_page(page)
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
import time
from datetime import datetime, timezone

import pandas as pd

from aodaisho.core import tree_page
from aodaisho.live import RecycleEvery, open_patchright
from aodaisho.utils import (
    save_log,
    append_csv,
    from_here,
    meta_html,
    hash_name,
    write_text,
)

here = from_here(__file__)
save_log(here('log/scraping.log'))

start_time = time.perf_counter()

items = list(pd.read_csv(here('csv/urls.csv'))['url'].items())
# items = list(pd.read_csv(here('csv/failed.csv'), index_col='url_index')['request_url'].items())
n = len(items)

with open_patchright(
    browser_options={'channel': 'chrome', 'headless': False},
    context_options={'viewport': {'width': 1920, 'height': 1080}},
    recycle=RecycleEvery(browser=300, context=100),
) as s:
    for url_index, request_url in items:
        print(f'url_index {url_index}/{n - 1}')
        page = s.page()
        tree = tree_page(page)
        if not (response := tree.goto(request_url)):
            append_csv(here('csv/failed.csv'), {
                'url_index': url_index,
                'request_url': request_url,
                'reason': 'goto',
            })
            continue
        html = meta_html({
            'aodaisho:url_index': url_index,
            'aodaisho:saved_at': datetime.now(timezone.utc),
            'aodaisho:request_url': request_url,
            'aodaisho:final_url': page.url,
            'aodaisho:goto_status': response.status,
        }) + page.content()
        if not write_text(here('html') / f'{hash_name(page.url)}.html', html):
            append_csv(here('csv/failed.csv'), {
                'url_index': url_index,
                'request_url': request_url,
                'final_url': page.url,
                'reason': 'write_text',
            })
            continue

elapsed = time.perf_counter() - start_time
print(f'Total execution time: {elapsed:.2f}s')
```

### discover.py

```python
from pathlib import Path

import pyperclip

from aodaisho.core import tree_parser
from aodaisho.utils import from_here, glob_paths, parse_html, process_map


def main() -> None:
    here = from_here(__file__)
    html_paths = glob_paths(here('html'), '*.html')
    results = [r for r in process_map(labels_in_file, html_paths) if r]
    labels = [label for part in results for label in part]
    pyperclip.copy('\n'.join(set(labels)))


def labels_in_file(file_path: str) -> list[str] | None:
    if not (parser := parse_html(Path(file_path).read_bytes())):
        return None
    tree = tree_parser(parser)
    return [t.strip() for t in tree.into_every('dt').texts if t and t.strip()]


if __name__ == '__main__':
    main()
```

### extract.py

```python
from pathlib import Path

from aodaisho.core import tree_parser
from aodaisho.utils import from_here, glob_paths, parse_html, process_map, write_parquet

def main() -> None:
    here = from_here(__file__)
    html_paths = glob_paths(here('html'), '*.html')
    results = [r for r in process_map(extract, html_paths) if r]
    write_parquet(here('parquet/extract.parquet'), results)

def extract(file_path: str) -> dict[str, str] | None:
    if not (parser := parse_html(Path(file_path).read_bytes())):
        return None
    tree = tree_parser(parser)
    dt_seeker = tree.into_every('dt').seeking

    def dd_text(pattern: str) -> str | None:
        return dt_seeker.one(pattern).after('dd').text
    
    dt_texts = [
        '価格',
        '交通',
        '総戸数',
        '月々の支払い目安額',
        'セットバック',
        '引渡日（入居予定日）',
        '所在階',
        '土地面積',
        '取引態様',
        '設備・条件',
        '最寄りの学校',
        '専有面積',
        '接道状況',
        '間取り',
        '備考',
        '建ぺい率 /容積率',
        '物件種別',
        '管理会社',
        '次回更新予定日',
        '管理形態',
        '構造・階建て',
        '土地権利',
        '修繕積立費',
        '管理費',
        '物件番号',
        '都市計画',
        '車庫区分',
        '現況',
        '敷地の権利形態',
        'バルコニー面積・方位',
        '引渡日（引渡予定日）',
        '地目',
        '国土法提出',
        '建築確認番号',
        '建築条件',
        '駐車場',
        '用途地域',
        '情報更新日',
        '所在地',
        '建物構造',
        '築年月',
        '建物面積',
        '引渡し',
        '私道面積',
    ]

    return {
        'url_index': tree.meta('aodaisho:url_index'),
        'saved_at': tree.meta('aodaisho:saved_at'),
        'request_url': tree.meta('aodaisho:request_url'),
        'final_url': tree.meta('aodaisho:final_url'),
        'goto_status': tree.meta('aodaisho:goto_status'),
        'ファイル名': Path(file_path).name,

        '取り扱い店舗': tree.into_every('p').seeking.one(r'取り扱い店舗').after('p').text,

        'スタッフからのコメント': tree.into('.js-staff_comment').text,
        '物件の魅力': tree.into_every('p').seeking.one(r'物件の魅力').after('p').text,

        'img_desc': '\n'.join(tree.into_every('p.text-left').seeking.one(r'画像をクリックすると拡大画像がご覧に').after('ul').into_every('li').texts)
    } | {dt_text: dd_text(dt_text) for dt_text in dt_texts}

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
