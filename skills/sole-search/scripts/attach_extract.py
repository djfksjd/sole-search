#!/usr/bin/env python3
"""공고 첨부파일 텍스트 추출 — sole-search.

지원: PDF(pdftotext 필요), HWPX(zip+XML, 표준 라이브러리).
HWP(구버전 바이너리)는 추출하지 않고 **명시적으로 실패**한다 — 스펙 규칙:
첨부를 읽지 못한 후보는 '확인됨' 판정 금지, '확인 필요'로 처리.

사용법:
  python3 attach_extract.py <file> [-o out.txt]

extract() 반환: {"ok": bool, "text": str, "reason": str}
"""
import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import zipfile
from xml.etree import ElementTree

HWP_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")  # OLE2 (구버전 .hwp)


def _result(ok, text="", reason=""):
    return {"ok": ok, "text": text, "reason": reason}


MAX_PDF_BYTES = 100 * 1024 * 1024


def extract_pdf(path):
    if path.stat().st_size > MAX_PDF_BYTES:
        return _result(False, reason="pdf_too_large")
    if not shutil.which("pdftotext"):
        return _result(False, reason="pdftotext_unavailable")
    try:
        p = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
                           capture_output=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError) as e:
        return _result(False, reason=f"pdftotext_error: {e}")
    if p.returncode != 0:
        return _result(False, reason=f"pdftotext_exit_{p.returncode}")
    text = p.stdout.decode("utf-8", "replace").strip()
    if not text:
        return _result(False, reason="pdf_no_text_layer")  # 스캔본 등
    return _result(True, text=text)


MAX_ZIP_ENTRIES = 2000
MAX_UNCOMPRESSED = 200 * 1024 * 1024  # 200MB — ZIP bomb 방어


def _section_no(name):
    m = re.search(r"section(\d+)\.xml$", name)
    return int(m.group(1)) if m else 0


def extract_hwpx(path):
    try:
        texts = []
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                return _result(False, reason="hwpx_too_many_entries")
            if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
                return _result(False, reason="hwpx_uncompressed_too_large")
            sections = sorted((n for n in z.namelist()
                               if re.match(r"Contents/section\d+\.xml$", n)),
                              key=_section_no)
            if not sections:
                return _result(False, reason="hwpx_no_sections")
            for name in sections:
                root = ElementTree.fromstring(z.read(name))
                for el in root.iter():
                    if el.tag.endswith("}t") and el.text:
                        texts.append(el.text)
        text = "\n".join(texts).strip()
        if not text:
            return _result(False, reason="hwpx_empty")
        return _result(True, text=text)
    except (zipfile.BadZipFile, ElementTree.ParseError, OSError) as e:
        return _result(False, reason=f"hwpx_error: {e}")


def extract(path_str):
    path = pathlib.Path(path_str)
    if not path.exists():
        return _result(False, reason="file_not_found")
    suffix = path.suffix.lower()
    if suffix == ".hwp":
        return _result(False, reason="hwp_binary_unsupported")
    # 확장자가 틀린 hwp 파일 방어
    try:
        head = path.open("rb").read(8)
        if head == HWP_MAGIC and suffix not in (".doc", ".xls", ".ppt"):
            return _result(False, reason="hwp_binary_unsupported")
    except OSError as e:
        return _result(False, reason=f"read_error: {e}")
    if suffix == ".pdf":
        return extract_pdf(path)
    if suffix == ".hwpx":
        return extract_hwpx(path)
    return _result(False, reason="unsupported_extension")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()
    r = extract(args.file)
    if args.output:
        pathlib.Path(args.output).write_text(r["text"], encoding="utf-8")
        print(json.dumps({"ok": r["ok"], "reason": r["reason"],
                          "chars": len(r["text"])}, ensure_ascii=False))
    else:
        print(r["text"] if r["ok"] else json.dumps(r, ensure_ascii=False))
    sys.exit(0 if r["ok"] else 1)


if __name__ == "__main__":
    main()
