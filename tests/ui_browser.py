"""Reuse only the browser process; each managed context starts with fresh state."""
from contextlib import contextmanager

import pytest
from playwright.sync_api import sync_playwright


def pytest_addoption(parser):
    parser.addoption('--ui-browser-scope', choices=('function', 'module'),
                     default='module', help='UI browser lifetime (context is always fresh)')


def browser_scope(*, fixture_name, config):
    return config.getoption('--ui-browser-scope')


@pytest.fixture(scope=browser_scope)
def ui_browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


@contextmanager
def isolated_context(browser, **options):
    context = browser.new_context(**options)
    try:
        yield context
    finally:
        context.close()


@pytest.fixture
def ui_context(ui_browser):
    # Do not expose a shared context or saved storage_state between tests.
    return lambda **options: isolated_context(ui_browser, **options)
