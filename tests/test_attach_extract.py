"""attach_extract — 합성 HWPX 성공, unsupported의 .failed.json + stale 보존."""
import json
import zipfile

import pytest


def make_hwpx(path, text="지원대상: 상시근로자 5명 미만 소상공인"):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml",
                   f'<hp:p xmlns:hp="urn:hwpx"><hp:t>{text}</hp:t></hp:p>')


def test_synthetic_hwpx_extracts(attach, tmp_path):
    p = tmp_path / "notice.hwpx"
    make_hwpx(p)
    r = attach.extract(str(p))
    assert r["ok"] is True and not r["reason"]
    assert "상시근로자 5명 미만" in r["text"]


def test_hwpx_without_sections_fails(attach, tmp_path):
    p = tmp_path / "empty.hwpx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
    r = attach.extract(str(p))
    assert r["ok"] is False and r["reason"] == "hwpx_no_sections"


def test_unsupported_writes_failed_json_and_preserves_stale(attach, tmp_path,
                                                            monkeypatch, capsys):
    f = tmp_path / "attach.xyz"
    f.write_bytes(b"whatever")
    out = tmp_path / "attach.txt"
    out.write_text("이전 실행의 성공 출력", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["attach_extract.py", str(f), "-o", str(out)])
    with pytest.raises(SystemExit) as e:
        attach.main()
    assert e.value.code == 1
    assert not out.exists()  # 빈/유령 출력 금지 — stale로 이동
    stales = list(tmp_path.glob("attach.txt.stale-*"))
    assert len(stales) == 1
    assert stales[0].read_text(encoding="utf-8") == "이전 실행의 성공 출력"
    failed = json.loads((tmp_path / "attach.txt.failed.json").read_text())
    assert failed == {"ok": False, "reason": "unsupported_extension",
                      "file": str(f)}


def test_missing_file(attach):
    assert attach.extract("/nonexistent/x.pdf")["reason"] == "file_not_found"
