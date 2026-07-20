import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "skills/sole-search/scripts"))
import diff_surveys as d

BASE = {"source": "sbiz24", "source_id": "1", "title": "A사업", "status": "접수중",
        "apply_start": "2026-07-01", "apply_end": "2026-08-01",
        "agency": "소진공", "content_hash": "h1",
        "rate": "2.0%", "limit_amount": "7천만원"}


def test_unchanged():
    r = d.classify(dict(BASE), dict(BASE))
    assert r["kind"] == "UNCHANGED"


def test_deadline_change_detected():
    r = d.classify(dict(BASE), dict(BASE, apply_end="2026-09-01"))
    assert r["kind"] == "CHANGED"
    assert "apply_end" in r["changed_fields"]


def test_rate_change_detected_even_if_deadline_same():
    r = d.classify(dict(BASE), dict(BASE, rate="2.5%"))
    assert r["kind"] == "CHANGED"
    assert "rate" in r["changed_fields"]


def test_content_hash_change_detected():
    r = d.classify(dict(BASE), dict(BASE, content_hash="h2"))
    assert r["kind"] == "CHANGED"
    assert "content_hash" in r["changed_fields"]


def test_profile_fingerprint():
    p1 = {"industry_text": "치킨집", "district": "마포구", "sales_band": "100m_500m",
          "last_survey_at": "2026-07-01"}
    p2 = dict(p1, last_survey_at="2026-07-20")  # 판정 무관 필드 변경
    p3 = dict(p1, district="강남구")             # 판정 관련 필드 변경
    assert d.profile_fingerprint(p1) == d.profile_fingerprint(p2)
    assert d.profile_fingerprint(p1) != d.profile_fingerprint(p3)


def test_carryover_valid():
    assert d.carryover_valid("abc", "abc") is True
    assert d.carryover_valid("abc", "xyz") is False


def test_cli_diff(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    a, b, c = dict(BASE), dict(BASE, source_id="2", title="B사업"), \
        dict(BASE, source_id="3", title="C사업")
    (old / "s.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False)
                                           for x in [a, b]))
    b2 = dict(b, rate="3.0%")
    (new / "s.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False)
                                           for x in [b2, c]))
    script = pathlib.Path(__file__).parent.parent / "skills/sole-search/scripts/diff_surveys.py"
    out = tmp_path / "new_items.jsonl"
    p = subprocess.run([sys.executable, str(script), str(old), str(new),
                        "--out", str(out)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    kinds = {(r["record"]["source_id"], r["kind"]) for r in lines}
    assert ("3", "NEW") in kinds
    assert ("2", "CHANGED") in kinds
    assert "GONE" in p.stderr and "1" in p.stderr  # source_id=1 소멸
