"""gov24_crawl — 첫 페이지 0건 fail-closed (CI 스모크가 파손을 녹색 통과하지 않도록)."""
import argparse
import json

import pytest


def _args(tmp_path, max_pages=None):
    return argparse.Namespace(output=str(tmp_path / "gov24.jsonl"),
                              filter_target=None, delay=0.5, max_pages=max_pages)


def _svc(sid, name):
    return {"서비스ID": sid, "서비스명": name, "신청기한": "상시",
            "소관기관명": "행정안전부", "지원유형": "현금"}


def _patch_api(monkeypatch, gov24, payload):
    monkeypatch.setattr(gov24, "api_get", lambda path, key, params: payload)


def test_empty_first_page_with_positive_total_is_failed(monkeypatch, gov24, tmp_path,
                                                        no_network, capsys):
    """totalCount>0인데 data=[] → 파서/API 파손 신호, exit 2 (Codex NO-GO 3 회귀).
    스모크(--max-pages 1)에서도 동일하게 실패해야 한다."""
    _patch_api(monkeypatch, gov24, {"totalCount": 10, "data": []})
    assert gov24.cmd_list(_args(tmp_path), key="k") == 2
    assert "0건" in capsys.readouterr().err
    assert gov24.cmd_list(_args(tmp_path, max_pages=1), key="k") == 2


def test_zero_total_is_failed_signal(monkeypatch, gov24, tmp_path, no_network):
    """totalCount=0은 기존 계약대로 장애 신호(exit 2) — 전량 사라진 응답을
    '공고 없음 성공'으로 넘기지 않는다."""
    _patch_api(monkeypatch, gov24, {"totalCount": 0, "data": []})
    assert gov24.cmd_list(_args(tmp_path), key="k") == 2


def test_first_page_smoke_success(monkeypatch, gov24, tmp_path, no_network):
    """정상 첫 페이지 + --max-pages 1 → coverage 검증 생략하고 성공."""
    _patch_api(monkeypatch, gov24,
               {"totalCount": 1000, "data": [_svc("SVC1", "소상공인 요금감면")]})
    assert gov24.cmd_list(_args(tmp_path, max_pages=1), key="k") == 0
    recs = [json.loads(l) for l in
            (tmp_path / "gov24.jsonl").read_text().splitlines()]
    assert recs[0]["source_id"] == "SVC1"


def test_missing_id_fields_fail_closed(monkeypatch, gov24, tmp_path, no_network):
    _patch_api(monkeypatch, gov24,
               {"totalCount": 5, "data": [{"이상한키": "값"}]})
    assert gov24.cmd_list(_args(tmp_path, max_pages=1), key="k") == 2
