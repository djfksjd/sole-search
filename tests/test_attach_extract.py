import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "skills/sole-search/scripts"))
import attach_extract

FIX = pathlib.Path(__file__).parent / "fixtures"


def test_hwpx_extracts_text():
    r = attach_extract.extract(str(FIX / "sample.hwpx"))
    assert r["ok"] is True
    assert "지원사업 테스트 문서" in r["text"]
    assert "신청자격" in r["text"]


def test_hwp_binary_fails_explicitly():
    r = attach_extract.extract(str(FIX / "sample.hwp"))
    assert r["ok"] is False
    assert r["reason"] == "hwp_binary_unsupported"


def test_pdf_extraction():
    r = attach_extract.extract(str(FIX / "sample.pdf"))
    if shutil.which("pdftotext"):
        assert r["ok"] is True
        assert "SUPPORT-TEST" in r["text"]
    else:
        assert r["ok"] is False
        assert r["reason"] == "pdftotext_unavailable"


def test_unknown_extension_fails_explicitly():
    r = attach_extract.extract("foo.zip")
    assert r["ok"] is False
    assert r["reason"] in ("unsupported_extension", "file_not_found")


def test_missing_file_fails_explicitly():
    r = attach_extract.extract("no/such/file.pdf")
    assert r["ok"] is False
    assert r["reason"] == "file_not_found"


def test_hwpx_sections_natural_sort():
    assert attach_extract._section_no("Contents/section10.xml") == 10
    names = ["Contents/section10.xml", "Contents/section2.xml", "Contents/section1.xml"]
    assert sorted(names, key=attach_extract._section_no) == [
        "Contents/section1.xml", "Contents/section2.xml", "Contents/section10.xml"]


def test_hwpx_zip_bomb_entry_limit(tmp_path):
    import zipfile
    p = tmp_path / "bomb.hwpx"
    with zipfile.ZipFile(p, "w") as z:
        for i in range(attach_extract.MAX_ZIP_ENTRIES + 1):
            z.writestr(f"junk{i}.txt", "x")
    r = attach_extract.extract(str(p))
    assert r["ok"] is False and r["reason"] == "hwpx_too_many_entries"
