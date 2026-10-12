from __future__ import annotations

from collections.abc import Callable, Iterator
import random
import re
import time
import unicodedata as ud
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin

from loguru import logger
from patchright.sync_api import Frame as PatchFrame, Page as PatchPage, ElementHandle as PatchElementHandle, Response as PatchResponse
from playwright.sync_api import Frame as PlayFrame, Page as PlayPage, ElementHandle as PlayElementHandle, Response as PlayResponse
from selectolax.lexbor import LexborHTMLParser, LexborNode


Page = PatchPage | PlayPage
ElementHandle = PatchElementHandle | PlayElementHandle
Response = PatchResponse | PlayResponse
Frame = PatchFrame | PlayFrame

WaitState = Literal['attached', 'detached', 'visible', 'hidden']

_UNUSABLE_INLINE_URL = re.compile(r'(?i)^(?:#|javascript:|mailto:|tel:|data:)')

_ELEMENT_NEXT = 'nextElementSibling'
_ELEMENT_PREV = 'previousElementSibling'
_ELEMENT_PARENT = 'parentElement'

_NODE_NEXT = 'next'
_NODE_PREV = 'prev'
_NODE_PARENT = 'parent'


def _collect_str[T](items: list[T], getter: Callable[[T], str | None]) -> list[str]:
    return [v for item in items if (v := getter(item))]


def ao_page(page: Page) -> AoPage:
    return AoPage(page)


def ao_element(page: Page, elem: ElementHandle | None) -> AoElement:
    return AoElement(page, elem)


def ao_element_group(page: Page, elems: list[AoElement]) -> AoElementGroup:
    return AoElementGroup(page, elems)


def ao_frame(page: Page, frame: Frame | None) -> AoFrame:
    return AoFrame(page, frame)


def ao_shadow_root(page: Page, host: ElementHandle | None) -> AoShadowRoot:
    return AoShadowRoot(page, host)


class _PageScoped:
    _page: Page

    def ao_element(self, elem: ElementHandle | None) -> AoElement:
        return ao_element(self._page, elem)

    def ao_element_group(self, elems: list[AoElement]) -> AoElementGroup:
        return ao_element_group(self._page, elems)

    def ao_frame(self, frame: Frame | None) -> AoFrame:
        return ao_frame(self._page, frame)

    def ao_shadow_root(self, host: ElementHandle | None) -> AoShadowRoot:
        return ao_shadow_root(self._page, host)


def ao_parser(parser: LexborHTMLParser) -> AoParser:
    return AoParser(parser)


def ao_node(node: LexborNode | None) -> AoNode:
    return AoNode(node)


def ao_node_group(nodes: list[AoNode]) -> AoNodeGroup:
    return AoNodeGroup(nodes)


class AoPage(_PageScoped):
    def __init__(self, page: Page) -> None:
        self._page = page

    @property
    def raw(self) -> Page:
        return self._page

    def i(self, selector: str) -> AoElement:
        '''in'''
        elem = self._page.query_selector(selector)
        return self.ao_element(elem)

    def ii(self, selector: str) -> AoElementGroup:
        '''in all'''
        elems = self._page.query_selector_all(selector)
        return self.ao_element_group([self.ao_element(e) for e in elems])

    def goto(
        self,
        url: str | None,
        try_cnt: int = 3,
        wait_range: tuple[float, float] = (3, 5),
        sleep_after: tuple[float, float] | None = (1, 2),
    ) -> Response | None:
        if not url:
            return None
        for i in range(try_cnt):
            try:
                response = self._page.goto(url)
                if response is not None:
                    if sleep_after is not None:
                        time.sleep(random.uniform(*sleep_after))
                    return response
                reason = 'response is None'
            except Exception as e:
                reason = f'{type(e).__name__}: {e}'
            logger.warning(f'[goto] retry ({i+1}/{try_cnt}) {reason}: {url!r}')
            if i + 1 < try_cnt:
                time.sleep(random.uniform(*wait_range))
        logger.error(f'[goto] retries exhausted ({try_cnt}): {url!r}')
        return None
    
    def bytes_at(self, url: str | None) -> bytes | None:
        if not url:
            return None
        new_page = self._page.context.new_page()
        try:
            res = ao_page(new_page).goto(url)
            if not res:
                return None
            if res.ok:
                return res.body()
            logger.warning(
                f'[bytes_at] HTTP {res.status} {res.status_text!r} | url={url!r} | response_url={res.url!r}'
            )
            return None
        except Exception as e:
            logger.warning(f'[bytes_at] {type(e).__name__}: {e} | url={url!r}')
            return None
        finally:
            new_page.close()

    def w(self, selector: str, state: WaitState = 'attached', timeout: int = 15000) -> AoElement:
        '''wait'''
        try:
            elem = self._page.wait_for_selector(selector, state=state, timeout=timeout)
            return self.ao_element(elem)
        except Exception as e:
            logger.warning(f'[wait] {type(e).__name__}: {e} | selector={selector!r} | url={self._page.url!r}')
            return self.ao_element(None)


class AoElement(_PageScoped):
    def __init__(self, page: Page, elem: ElementHandle | None) -> None:
        self._page = page
        self._elem = elem

    def __bool__(self) -> bool:
        return self._elem is not None

    @property
    def raw(self) -> ElementHandle | None:
        return self._elem

    def i(self, selector: str) -> AoElement:
        '''in'''
        if self._elem is None:
            return self.ao_element(None)
        elem = self._elem.query_selector(selector)
        return self.ao_element(elem)

    def ii(self, selector: str) -> AoElementGroup:
        '''in all'''
        if self._elem is None:
            return self.ao_element_group([])
        elems = self._elem.query_selector_all(selector)
        return self.ao_element_group([self.ao_element(e) for e in elems])

    @property
    def frame(self) -> AoFrame:
        if self._elem is None:
            return self.ao_frame(None)
        try:
            return self.ao_frame(self._elem.content_frame())
        except Exception as e:
            logger.error(f'[frame] {type(e).__name__}: {e}')
            return self.ao_frame(None)

    @property
    def shadow(self) -> AoShadowRoot:
        return self.ao_shadow_root(self._elem)

    def _walk_relative(self, selector: str, axis: str, label: str) -> AoElement:
        if self._elem is None:
            return self.ao_element(None)
        try:
            elem = self._elem.evaluate_handle(
                '''(el, args) => {
                    const [sel, axis] = args;
                    let cur = el[axis];
                    while (cur) {
                        if (cur.matches(sel)) return cur;
                        cur = cur[axis];
                    }
                    return null;
                }''',
                [selector, axis],
            ).as_element()
            return self.ao_element(elem)
        except Exception as e:
            logger.error(f'[{label}] {self._elem} {type(e).__name__}: {e}')
            return self.ao_element(None)

    def n(self, selector: str) -> AoElement:
        '''next'''
        return self._walk_relative(selector, _ELEMENT_NEXT, 'n')

    def p(self, selector: str) -> AoElement:
        '''prev'''
        return self._walk_relative(selector, _ELEMENT_PREV, 'p')

    def o(self, selector: str) -> AoElement:
        '''out'''
        return self._walk_relative(selector, _ELEMENT_PARENT, 'o')

    @property
    def text(self) -> str | None:
        if self._elem is None:
            return None
        return text if (text := self._elem.text_content()) else None

    def attr(self, attr_name: str) -> str | None:
        if self._elem is None:
            return None
        return attr if (attr := self._elem.get_attribute(attr_name)) else None

    def _resolved_url_from_attr(self, attr_name: str) -> str | None:
        if self._elem is None:
            return None
        if not (attr := self._elem.get_attribute(attr_name)):
            return None
        if not (a := attr.strip()):
            return None
        if _UNUSABLE_INLINE_URL.search(a):
            return None
        return urljoin(self._page.url, a)

    @property
    def url(self) -> str | None:
        return self._resolved_url_from_attr('href')

    @property
    def src(self) -> str | None:
        return self._resolved_url_from_attr('src')

    def scroll_into_view(self) -> None:
        if self._elem is None:
            logger.warning('[scroll_into_view] element is None')
            return
        try:
            self._elem.evaluate(
                '''(el) => el.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });'''
            )
            self._elem.wait_for_element_state('stable')
        except Exception as e:
            logger.warning(f'[scroll_into_view] {type(e).__name__}: {e} | url={self._page.url!r}')

    @staticmethod
    def _isolate_visibility_css(scope: str, attr: str) -> str:
        return (
            f'{scope} * {{\n'
            f'  visibility: hidden !important;\n'
            f'}}\n'
            f'[{attr}],\n'
            f'[{attr}] * {{\n'
            f'  visibility: visible !important;\n'
            f'}}\n'
        )

    def _isolate_apply(self, attr: str, css: str, style_id: str) -> None:
        self._elem.evaluate(
            '''(el, args) => {
                const [attr, css, styleId] = args;
                el.setAttribute(attr, '');
                const s = document.createElement('style');
                s.id = styleId;
                s.textContent = css;
                (document.head || document.documentElement).appendChild(s);
            }''',
            [attr, css, style_id],
        )

    def _isolate_remove(self, attr: str, style_id: str) -> None:
        try:
            self._elem.evaluate(
                '''(el, args) => {
                    const [attr, styleId] = args;
                    el.removeAttribute(attr);
                    const node = document.getElementById(styleId);
                    if (node) node.remove();
                }''',
                [attr, style_id],
            )
        except Exception as e:
            logger.warning(
                f'[screenshot isolate cleanup] {type(e).__name__}: {e} | url={self._page.url!r}'
            )

    def screenshot(
        self,
        path: Path,
        image_type: Literal['png', 'jpeg'] = 'png',
        *,
        isolate: bool = False,
        isolate_scope: str = 'body',
        isolate_attr: str = 'data-aodaisho-screenshot-root',
        isolate_style_id: str = 'aodaisho-screenshot-isolate',
    ) -> bool:
        if self._elem is None:
            logger.warning('[screenshot] element is None')
            return False
        if isolate:
            style_id = f'{isolate_style_id}-{time.time_ns()}'
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if isolate:
                css = self._isolate_visibility_css(isolate_scope, isolate_attr)
                self._isolate_apply(isolate_attr, css, style_id)
            self._elem.screenshot(
                path=path,
                type=image_type,
                animations='disabled',
            )
            return True
        except Exception as e:
            logger.warning(f'[screenshot] {type(e).__name__}: {e} | url={self._page.url!r}')
            return False
        finally:
            if isolate:
                self._isolate_remove(isolate_attr, style_id)


class AoElementGroup(_PageScoped):
    def __init__(self, page: Page, elems: list[AoElement]) -> None:
        self._page = page
        self._elems = elems

    def __iter__(self) -> Iterator[AoElement]:
        return iter(self._elems)

    def __len__(self) -> int:
        return len(self._elems)

    def __getitem__(self, key: int | slice) -> AoElement | AoElementGroup:
        if isinstance(key, slice):
            return AoElementGroup(self._page, self._elems[key])
        return self._elems[key]

    def __add__(self, other: AoElementGroup) -> AoElementGroup:
        if not isinstance(other, AoElementGroup):
            raise TypeError(
                'AoElementGroup 同士のみ + で結合できます '
                f'（右辺は {type(other).__name__}）'
            )
        if self._page is not other._page:
            raise ValueError('異なる Page に紐づいた AoElementGroup は結合できません')
        return AoElementGroup(self._page, self._elems + other._elems)

    @property
    def raw(self) -> list[AoElement]:
        return self._elems

    @property
    def scan(self) -> ElementScan:
        pairs: list[tuple[str, AoElement]] = []
        for e in self._elems:
            if (t := e.text):
                pairs.append((ud.normalize('NFKC', t), e))
        return ElementScan(self._page, pairs)

    @property
    def texts(self) -> list[str]:
        return _collect_str(self._elems, lambda e: e.text)

    def attrs(self, attr_name: str) -> list[str]:
        return _collect_str(self._elems, lambda e: e.attr(attr_name))

    @property
    def urls(self) -> list[str]:
        return _collect_str(self._elems, lambda e: e.url)

    @property
    def srcs(self) -> list[str]:
        return _collect_str(self._elems, lambda e: e.src)


class ElementScan(_PageScoped):
    def __init__(self, page: Page, pairs: list[tuple[str, AoElement]]) -> None:
        self._page = page
        self._pairs = pairs

    def m(self, pattern: str) -> AoElement:
        '''match'''
        try:
            prog = re.compile(pattern)
            for text, e in self._pairs:
                if prog.search(text):
                    return e
        except Exception as e:
            logger.warning(f'[scan] {type(e).__name__}: {e} | pattern={pattern!r}')
        return self.ao_element(None)

    def mm(self, pattern: str) -> AoElementGroup:
        '''match all'''
        try:
            prog = re.compile(pattern)
            filtered = [e for text, e in self._pairs if prog.search(text)]
            return self.ao_element_group(filtered)
        except Exception as e:
            logger.warning(f'[scan] {type(e).__name__}: {e} | pattern={pattern!r}')
            return self.ao_element_group([])


class AoFrame(_PageScoped):
    def __init__(self, page: Page, frame: Frame | None) -> None:
        self._page = page
        self._frame = frame

    def __bool__(self) -> bool:
        return self._frame is not None

    @property
    def raw(self) -> Frame | None:
        return self._frame

    def i(self, selector: str) -> AoElement:
        '''in'''
        if self._frame is None:
            return self.ao_element(None)
        elem = self._frame.query_selector(selector)
        return self.ao_element(elem)

    def ii(self, selector: str) -> AoElementGroup:
        '''in all'''
        if self._frame is None:
            return self.ao_element_group([])
        elems = self._frame.query_selector_all(selector)
        return self.ao_element_group([self.ao_element(e) for e in elems])

    def w(self, selector: str, state: WaitState = 'attached', timeout: int = 15000) -> AoElement:
        '''wait'''
        if self._frame is None:
            return self.ao_element(None)
        try:
            elem = self._frame.wait_for_selector(selector, state=state, timeout=timeout)
            return self.ao_element(elem)
        except Exception as e:
            logger.warning(
                f'[wait] {type(e).__name__}: {e} | selector={selector!r} | url={self._page.url!r}'
            )
            return self.ao_element(None)


class AoShadowRoot(_PageScoped):
    def __init__(self, page: Page, host: ElementHandle | None) -> None:
        self._page = page
        self._host = host

    def __bool__(self) -> bool:
        if self._host is None:
            return False
        try:
            return bool(self._host.evaluate('el => Boolean(el.shadowRoot)'))
        except Exception as e:
            logger.error(f'[shadow] {type(e).__name__}: {e}')
            return False

    def i(self, selector: str) -> AoElement:
        '''in'''
        if not self:
            return self.ao_element(None)
        try:
            elem = self._host.evaluate_handle(
                '(el, sel) => el.shadowRoot?.querySelector(sel) ?? null',
                selector,
            ).as_element()
            return self.ao_element(elem)
        except Exception as e:
            logger.error(f'[shadow i] {type(e).__name__}: {e} | selector={selector!r}')
            return self.ao_element(None)

    def ii(self, selector: str) -> AoElementGroup:
        '''in all'''
        if not self:
            return self.ao_element_group([])
        try:
            n = self._host.evaluate(
                '(el, sel) => el.shadowRoot?.querySelectorAll(sel)?.length ?? 0',
                selector,
            )
            elems = []
            for idx in range(n):
                elem = self._host.evaluate_handle(
                    '''(el, args) => {
                        const [sel, i] = args;
                        return el.shadowRoot.querySelectorAll(sel)[i];
                    }''',
                    [selector, idx],
                ).as_element()
                elems.append(self.ao_element(elem))
            return self.ao_element_group(elems)
        except Exception as e:
            logger.error(f'[shadow ii] {type(e).__name__}: {e} | selector={selector!r}')
            return self.ao_element_group([])

    def w(self, selector: str, timeout: int = 15000) -> AoElement:
        '''wait (attached in shadow root only)'''
        if not self:
            return self.ao_element(None)
        frame = self._host.owner_frame()
        if frame is None:
            logger.warning('[shadow wait] owner_frame is None')
            return self.ao_element(None)
        try:
            frame.wait_for_function(
                '([el, sel]) => Boolean(el.shadowRoot?.querySelector(sel))',
                [self._host, selector],
                timeout=timeout,
            )
            return self.i(selector)
        except Exception as e:
            logger.warning(
                f'[shadow wait] {type(e).__name__}: {e} | selector={selector!r} | url={self._page.url!r}'
            )
            return self.ao_element(None)


class AoParser:
    def __init__(self, parser: LexborHTMLParser) -> None:
        self._parser = parser

    @property
    def raw(self) -> LexborHTMLParser:
        return self._parser

    def i(self, selector: str) -> AoNode:
        '''in'''
        node = self._parser.css_first(selector)
        return ao_node(node)

    def ii(self, selector: str) -> AoNodeGroup:
        '''in all'''
        nodes = self._parser.css(selector)
        return ao_node_group([ao_node(n) for n in nodes])

    def meta(self, name: str) -> str | None:
        return self.i(f'meta[name="{name}"]').attr('content')


class AoNode:
    def __init__(self, node: LexborNode | None) -> None:
        self._node = node

    def __bool__(self) -> bool:
        return self._node is not None

    @property
    def raw(self) -> LexborNode | None:
        return self._node

    def i(self, selector: str) -> AoNode:
        '''in'''
        if self._node is None:
            return ao_node(None)
        node = self._node.css_first(selector)
        return ao_node(node)

    def ii(self, selector: str) -> AoNodeGroup:
        '''in all'''
        if self._node is None:
            return ao_node_group([])
        nodes = self._node.css(selector)
        return ao_node_group([ao_node(n) for n in nodes])

    def _walk_relative(self, selector: str, axis: str) -> AoNode:
        if self._node is None:
            return ao_node(None)
        cur = getattr(self._node, axis)
        while cur is not None:
            if cur.is_element_node and cur.css_matches(selector):
                return ao_node(cur)
            cur = getattr(cur, axis)
        return ao_node(None)

    def n(self, selector: str) -> AoNode:
        '''next'''
        return self._walk_relative(selector, _NODE_NEXT)

    def p(self, selector: str) -> AoNode:
        '''prev'''
        return self._walk_relative(selector, _NODE_PREV)

    def o(self, selector: str) -> AoNode:
        '''out'''
        return self._walk_relative(selector, _NODE_PARENT)

    @property
    def text(self) -> str | None:
        if self._node is None:
            return None
        return text if (text := self._node.text()) else None

    def attr(self, attr_name: str) -> str | None:
        if self._node is None:
            return None
        return attr if (attr := self._node.attributes.get(attr_name)) else None


class AoNodeGroup:
    def __init__(self, nodes: list[AoNode]) -> None:
        self._nodes = nodes

    def __iter__(self) -> Iterator[AoNode]:
        return iter(self._nodes)

    def __len__(self) -> int:
        return len(self._nodes)

    def __getitem__(self, key: int | slice) -> AoNode | AoNodeGroup:
        if isinstance(key, slice):
            return AoNodeGroup(self._nodes[key])
        return self._nodes[key]

    def __add__(self, other: AoNodeGroup) -> AoNodeGroup:
        if not isinstance(other, AoNodeGroup):
            raise TypeError(
                'AoNodeGroup 同士のみ + で結合できます '
                f'（右辺は {type(other).__name__}）'
            )
        return AoNodeGroup(self._nodes + other._nodes)

    @property
    def raw(self) -> list[AoNode]:
        return self._nodes

    @property
    def scan(self) -> NodeScan:
        pairs: list[tuple[str, AoNode]] = []
        for n in self._nodes:
            if (t := n.text):
                pairs.append((ud.normalize('NFKC', t), n))
        return NodeScan(pairs)

    @property
    def texts(self) -> list[str]:
        return _collect_str(self._nodes, lambda n: n.text)

    def attrs(self, attr_name: str) -> list[str]:
        return _collect_str(self._nodes, lambda n: n.attr(attr_name))


class NodeScan:
    def __init__(self, pairs: list[tuple[str, AoNode]]) -> None:
        self._pairs = pairs

    def m(self, pattern: str) -> AoNode:
        '''match'''
        try:
            prog = re.compile(pattern)
            for text, n in self._pairs:
                if prog.search(text):
                    return n
        except Exception as e:
            logger.warning(f'[scan] {type(e).__name__}: {e} | pattern={pattern!r}')
        return ao_node(None)

    def mm(self, pattern: str) -> AoNodeGroup:
        '''match all'''
        try:
            prog = re.compile(pattern)
            filtered = [n for text, n in self._pairs if prog.search(text)]
            return ao_node_group(filtered)
        except Exception as e:
            logger.warning(f'[scan] {type(e).__name__}: {e} | pattern={pattern!r}')
            return ao_node_group([])
