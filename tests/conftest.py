"""sole-search 스크립트 테스트 공용 픽스처 — 네트워크 없는 importlib 로드."""
import importlib
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sole-search" / "scripts"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name):
    return importlib.import_module(name)


@pytest.fixture(scope="session")
def sbiz():
    return _load("sbiz_crawl")


@pytest.fixture(scope="session")
def sources():
    return _load("sources_crawl")


@pytest.fixture(scope="session")
def region():
    return _load("region_crawl")


@pytest.fixture(scope="session")
def attach():
    return _load("attach_extract")


@pytest.fixture(scope="session")
def gov24():
    return _load("gov24_crawl")


@pytest.fixture(scope="session")
def diff():
    return _load("diff_surveys")


@pytest.fixture(scope="session")
def fixtures_dir():
    return FIXTURES


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """테스트에서 예의상 딜레이는 생략한다 — 실네트워크가 없으므로 무의미."""
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)


@pytest.fixture
def no_network(monkeypatch):
    """실수로 실네트워크를 치면 즉시 실패시킨다."""
    import urllib.request

    def _blocked(*a, **k):
        raise AssertionError("네트워크 호출 금지 — fixture 테스트")

    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
