"""Delt Playwright-browser. Brukes både til fulltekst-fallback (content-fasen)
og til playwright listing-fetcheren. Én Chromium startes per `with`-blokk og
gjenbrukes for alle sider, akkurat som openpaper sin delte browser."""

from typing import Optional


class BrowserSession:
    def __init__(self, *, nav_timeout_ms: int = 30000, settle_ms: int = 1500):
        self.nav_timeout_ms = nav_timeout_ms
        self.settle_ms = settle_ms
        self._pw = None
        self._browser = None

    def __enter__(self) -> "BrowserSession":
        # Importeres lazy så appen kjører selv om Playwright/Chromium ikke er
        # installert (da brukes bare statisk henting).
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def _new_page(self):
        page = self._browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            )
        )
        page.set_default_timeout(self.nav_timeout_ms)
        return page

    def render(self, url: str) -> Optional[str]:
        """Returnerer rendret HTML for en side, eller None ved feil."""
        page = None
        try:
            page = self._new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout_ms)
            page.wait_for_timeout(self.settle_ms)
            return page.content()
        except Exception as e:
            print(f"[browser] render feilet {url}: {e}")
            return None
        finally:
            if page:
                page.close()

    def link_candidates(self, url: str, limit: int = 40) -> list[dict]:
        """Høster <a>-lenker med tekst + class-info, for å la LLM-en foreslå en
        selector for artikkel-lenker på en feedløs side."""
        js = """
        (limit) => {
          const out = []; const seen = new Set();
          for (const a of document.querySelectorAll('a[href]')) {
            const text = (a.innerText || '').trim();
            const href = a.href || '';
            if (text.length < 25) continue;
            if (!href || href.startsWith('javascript')) continue;
            if (seen.has(href)) continue;
            seen.add(href);
            out.push({
              text: text.slice(0, 100),
              href,
              cls: a.className || '',
              parentCls: (a.parentElement ? a.parentElement.className : '') || ''
            });
            if (out.length >= limit) break;
          }
          return out;
        }
        """
        page = None
        try:
            page = self._new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout_ms)
            page.wait_for_timeout(self.settle_ms)
            return page.evaluate(js, limit) or []
        except Exception as e:
            print(f"[browser] link_candidates feilet {url}: {e}")
            return []
        finally:
            if page:
                page.close()

    def links(self, url: str, selector: str) -> list[tuple[str, str]]:
        """Returnerer (href, lenketekst) for alle elementer som matcher
        CSS-selector på en rendret side."""
        page = None
        out: list[tuple[str, str]] = []
        try:
            page = self._new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout_ms)
            page.wait_for_timeout(self.settle_ms)
            for el in page.query_selector_all(selector):
                href = el.get_attribute("href")
                if not href:
                    continue
                text = (el.inner_text() or "").strip()
                out.append((href, text))
        except Exception as e:
            print(f"[browser] links feilet {url}: {e}")
        finally:
            if page:
                page.close()
        return out


def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401

        return True
    except Exception:
        return False
