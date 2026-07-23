#!/usr/bin/env python3
"""공고 첨부파일 텍스트 추출 — sole-search.

지원: PDF(pdftotext 필요), HWPX(zip+XML, 표준 라이브러리),
HWP(구버전 OLE 바이너리)는 폴백 체인으로 시도한다:
  1) hwp5txt 명령이 있으면 전체 텍스트 추출 (ok, reason 없음)
  2) OLE(CFB) 컨테이너의 PrvText 스트림(UTF-16LE 미리보기)을 순수 파이썬으로 추출
     — **부분 추출**이므로 reason: "hwp_preview_only" (SKILL.md 규칙:
     attachments_complete가 아니므로 그 후보는 '확인됨' 판정 금지)
  3) 둘 다 실패하면 hwp_binary_unsupported로 명시적 실패

추출 실패 시 -o 경로에 빈 파일을 만들지 않는다 — 대신 <출력경로>.failed.json에
reason을 기록한다.

사용법:
  python3 attach_extract.py <file> [-o out.txt]

extract() 반환: {"ok": bool, "text": str, "reason": str}
  ok=True에 reason이 있으면 부분 추출(예: hwp_preview_only)이다.
"""
import argparse
import json
import pathlib
import re
import shutil
import struct
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


_CFB_END = {0xFFFFFFFC, 0xFFFFFFFD, 0xFFFFFFFE, 0xFFFFFFFF}
MAX_HWP_BYTES = 100 * 1024 * 1024


def _cfb_read_stream(data, target):
    """최소 CFB(OLE2) 파서 — target 스트림 바이트를 반환, 없으면 None.

    PrvText는 보통 4096B 미만이라 ministream에 있다 — miniFAT 경로까지 구현.
    손상 파일은 struct.error/IndexError로 터진다 — 호출부에서 잡는다.
    """
    if len(data) < 512 or data[:8] != HWP_MAGIC:
        return None
    ssz = 1 << struct.unpack_from("<H", data, 30)[0]
    mssz = 1 << struct.unpack_from("<H", data, 32)[0]
    dir_start = struct.unpack_from("<I", data, 48)[0]
    mini_cutoff = struct.unpack_from("<I", data, 56)[0]
    minifat_start = struct.unpack_from("<I", data, 60)[0]
    difat_start = struct.unpack_from("<I", data, 68)[0]
    num_difat = struct.unpack_from("<I", data, 72)[0]
    per = ssz // 4

    def sector(n):
        return data[512 + n * ssz:512 + (n + 1) * ssz]

    difat = list(struct.unpack_from("<109I", data, 76))
    s = difat_start
    for _ in range(num_difat):
        if s in _CFB_END:
            break
        vals = struct.unpack(f"<{per}I", sector(s))
        difat.extend(vals[:-1])
        s = vals[-1]
    fat = []
    for fs in difat:
        if fs in _CFB_END:
            continue
        fat.extend(struct.unpack(f"<{per}I", sector(fs)))

    def chain(start, table):
        out, cur, seen = [], start, set()
        while cur not in _CFB_END and cur < len(table) and cur not in seen:
            seen.add(cur)
            out.append(cur)
            cur = table[cur]
        return out

    dir_bytes = b"".join(sector(x) for x in chain(dir_start, fat))
    root = stream = None
    for off in range(0, len(dir_bytes) - 127, 128):
        e = dir_bytes[off:off + 128]
        nlen = struct.unpack_from("<H", e, 64)[0]
        if nlen < 2 or nlen > 64:
            continue
        name = e[:nlen - 2].decode("utf-16-le", "ignore")
        etype = e[66]
        start = struct.unpack_from("<I", e, 116)[0]
        size = struct.unpack_from("<I", e, 120)[0]
        if etype == 5:
            root = (start, size)
        elif etype == 2 and name == target:
            stream = (start, size)
    if stream is None:
        return None
    start, size = stream
    if size >= mini_cutoff:
        return b"".join(sector(x) for x in chain(start, fat))[:size]
    if root is None:
        return None
    ministream = b"".join(sector(x) for x in chain(root[0], fat))[:root[1]]
    minifat = []
    for x in chain(minifat_start, fat):
        minifat.extend(struct.unpack(f"<{per}I", sector(x)))
    raw = b"".join(ministream[m * mssz:(m + 1) * mssz] for m in chain(start, minifat))
    return raw[:size]


def extract_hwp(path):
    """HWP 바이너리 폴백 체인: hwp5txt(전체) → PrvText(미리보기, 부분) → 실패."""
    try:
        if path.stat().st_size > MAX_HWP_BYTES:
            return _result(False, reason="hwp_too_large")
        data = path.open("rb").read(8)
    except OSError as e:
        return _result(False, reason=f"read_error: {e}")
    if data[:4] == b"PK\x03\x04":
        return extract_hwpx(path)  # 확장자만 .hwp인 HWPX(zip) — 실서비스에서 관측됨
    if data != HWP_MAGIC:
        return _result(False, reason="hwp_binary_unsupported")  # OLE 시그니처 아님
    if shutil.which("hwp5txt"):
        try:
            p = subprocess.run(["hwp5txt", str(path)], capture_output=True, timeout=120)
            if p.returncode == 0:
                text = p.stdout.decode("utf-8", "replace").strip()
                if text:
                    return _result(True, text=text)
        except (subprocess.TimeoutExpired, OSError):
            pass  # PrvText 폴백으로
    try:
        raw = _cfb_read_stream(path.read_bytes(), "PrvText")
    except (struct.error, IndexError, ValueError, MemoryError, OSError):
        raw = None
    if raw:
        text = raw.decode("utf-16-le", "ignore").replace("\x00", "").strip()
        if text:
            return _result(True, text=text, reason="hwp_preview_only")
    return _result(False, reason="hwp_binary_unsupported")


def extract(path_str):
    path = pathlib.Path(path_str)
    if not path.exists():
        return _result(False, reason="file_not_found")
    suffix = path.suffix.lower()
    if suffix == ".hwp":
        return extract_hwp(path)
    # 확장자가 틀린 hwp 파일 방어
    try:
        head = path.open("rb").read(8)
        if head == HWP_MAGIC and suffix not in (".doc", ".xls", ".ppt"):
            return extract_hwp(path)
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
        if r["ok"]:
            pathlib.Path(args.output).write_text(r["text"], encoding="utf-8")
        else:
            # 실패 시 빈 파일을 만들지 않는다 — reason을 담은 마커 파일만 생성
            failed = args.output + ".failed.json"
            pathlib.Path(failed).write_text(json.dumps(
                {"ok": False, "reason": r["reason"], "file": args.file},
                ensure_ascii=False), encoding="utf-8")
            print(f"[sole-search] 추출 실패 — {failed} 기록 (빈 출력 파일 미생성)",
                  file=sys.stderr)
        print(json.dumps({"ok": r["ok"], "reason": r["reason"],
                          "chars": len(r["text"])}, ensure_ascii=False))
    else:
        print(r["text"] if r["ok"] else json.dumps(r, ensure_ascii=False))
    sys.exit(0 if r["ok"] else 1)


if __name__ == "__main__":
    main()
