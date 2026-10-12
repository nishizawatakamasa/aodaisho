from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, fields
from types import TracebackType
from typing import Any, Self

from camoufox.sync_api import Camoufox
from patchright.sync_api import (
    BrowserContext as PatchrightBrowserContext,
    Page as PatchrightPage,
    Playwright,
    sync_playwright,
)
from playwright.sync_api import BrowserContext as PlaywrightBrowserContext
from playwright.sync_api import Page as PlaywrightPage

Page = PatchrightPage | PlaywrightPage
Context = PatchrightBrowserContext | PlaywrightBrowserContext


@dataclass(frozen=True, slots=True)
class RecycleEvery:
    browser: int | None = None
    context: int | None = None
    page: int | None = None

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if value is not None and value < 1:
                raise ValueError(f'{f.name} は 1 以上で指定してください (got {value})')


class _SessionBase:
    def __init__(
        self,
        *,
        browser_options: dict[str, Any] | None = None,
        context_options: dict[str, Any] | None = None,
        recycle: RecycleEvery | None = None,
        setup_context: Callable[[Context], None] | None = None,
    ) -> None:
        self._recycle = recycle or RecycleEvery()
        self._browser_options = browser_options or {}
        self._context_options = context_options or {}
        self._setup_context = setup_context
        self._browser = None
        self._context: Context | None = None
        self._page: Page | None = None
        self._page_calls = 0
        self._entered = False

    def page(self) -> Page:
        """Playwright の Page を返す。

        初回は browser → context → page を遅延オープンする。
        以降は呼ぶたびに ``RecycleEvery`` の閾値を見て、必要なら page / context / browser を再生成する。
        純粋な getter ではなく、呼び出しのたびに内部カウンタが進む（リサイクル間隔もこの回数基準）。
        """
        if not self._entered:
            raise RuntimeError('with ブロックの外で page() を呼べません')
        if self._browser is None:
            self._open_browser()
        elif (b := self._recycle.browser) and self._page_calls % b == 0:
            self._close_browser()
            self._open_browser()
        elif (c := self._recycle.context) and self._page_calls % c == 0:
            self._close_context()
            self._open_context()
        elif (p := self._recycle.page) and self._page_calls % p == 0:
            self._close_page()
            self._open_page()
        self._page_calls += 1
        return self._page

    def _open_page(self) -> None:
        self._page = self._context.new_page()

    def _close_page(self) -> None:
        if self._page is not None:
            self._page.close()
        self._page = None

    def _open_context(self) -> None:
        self._context = self._browser.new_context(**self._context_options)
        if self._setup_context is not None:
            self._setup_context(self._context)
        self._open_page()

    def _close_context(self) -> None:
        self._close_page()
        if self._context is not None:
            self._context.close()
        self._context = None


class PatchrightSession(_SessionBase):
    def __init__(
        self,
        *,
        browser_options: dict[str, Any] | None = None,
        context_options: dict[str, Any] | None = None,
        recycle: RecycleEvery | None = None,
        setup_context: Callable[[Context], None] | None = None,
    ) -> None:
        super().__init__(
            browser_options=browser_options,
            context_options=context_options,
            recycle=recycle,
            setup_context=setup_context,
        )
        self._pw: Playwright | None = None

    def __enter__(self) -> Self:
        self._pw = sync_playwright().start()
        self._entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self._entered:
            return
        self._close_browser()
        self._pw.stop()
        self._pw = None
        self._page_calls = 0
        self._entered = False

    def _open_browser(self) -> None:
        self._browser = self._pw.chromium.launch(**self._browser_options)
        self._open_context()

    def _close_browser(self) -> None:
        self._close_context()
        if self._browser is not None:
            self._browser.close()
        self._browser = None


class CamoufoxSession(_SessionBase):
    def __init__(
        self,
        *,
        browser_options: dict[str, Any] | None = None,
        context_options: dict[str, Any] | None = None,
        recycle: RecycleEvery | None = None,
        setup_context: Callable[[Context], None] | None = None,
    ) -> None:
        super().__init__(
            browser_options=browser_options,
            context_options=context_options,
            recycle=recycle,
            setup_context=setup_context,
        )
        self._stack: ExitStack | None = None

    def __enter__(self) -> Self:
        self._entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self._entered:
            return
        self._close_browser()
        self._page_calls = 0
        self._entered = False

    def _open_browser(self) -> None:
        fox = Camoufox(**self._browser_options)
        self._stack = ExitStack()
        self._browser = self._stack.enter_context(fox)
        self._open_context()

    def _close_browser(self) -> None:
        self._close_context()
        if self._stack is not None:
            self._stack.close()
        self._stack = None
        self._browser = None


def open_patchright(
    *,
    browser_options: dict[str, Any] | None = None,
    context_options: dict[str, Any] | None = None,
    recycle: RecycleEvery | None = None,
    setup_context: Callable[[Context], None] | None = None,
) -> PatchrightSession:
    """Patchright ブラウザセッションを開く。

    例::

        with open_patchright(
            browser_options={'channel': 'chrome', 'headless': False},
            context_options={'viewport': {'width': 1920, 'height': 1080}},
            recycle=RecycleEvery(browser=300, context=100, page=20),
        ) as s:
            page = s.page()
    """
    return PatchrightSession(
        browser_options=browser_options,
        context_options=context_options,
        recycle=recycle,
        setup_context=setup_context,
    )


def open_camoufox(
    *,
    browser_options: dict[str, Any] | None = None,
    context_options: dict[str, Any] | None = None,
    recycle: RecycleEvery | None = None,
    setup_context: Callable[[Context], None] | None = None,
) -> CamoufoxSession:
    """Camoufox ブラウザセッションを開く。

    例::

        with open_camoufox(
            browser_options={'headless': False, 'humanize': True},
            context_options={'viewport': {'width': 1920, 'height': 1080}},
            recycle=RecycleEvery(browser=300, context=100, page=20),
        ) as s:
            page = s.page()
    """
    return CamoufoxSession(
        browser_options=browser_options,
        context_options=context_options,
        recycle=recycle,
        setup_context=setup_context,
    )
