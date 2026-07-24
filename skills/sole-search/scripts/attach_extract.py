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
import os
import pathlib
import re
import select
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from xml.etree import ElementTree

HWP_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")  # OLE2 (구버전 .hwp)

MAX_EXTRACT_OUTPUT = 60 * 1024 * 1024  # 추출기 stdout 상한 60MB — 텍스트 폭탄 방어


def _result(ok, text="", reason=""):
    return {"ok": ok, "text": text, "reason": reason}


def _run_capped(cmd, timeout, max_bytes=MAX_EXTRACT_OUTPUT):
    """subprocess를 돌리되 stdout를 max_bytes까지만, 전체를 timeout 안에서만 읽는다
    (Codex sole #4: 신뢰불가 문서가 무한/거대 텍스트를 뿜어 메모리·시간을 고갈시키는
    것을 막는다). select로 데드라인을 지키고 상한 초과 시 프로세스를 죽인다.
    반환: (returncode, stdout_bytes) — 상한 초과나 타임아웃이면 각각 RuntimeError/
    TimeoutExpired를 올린다."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    fd = p.stdout.fileno()
    chunks, total = [], 0
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(cmd, timeout)
            r, _, _ = select.select([fd], [], [], remaining)
            if not r:
                raise subprocess.TimeoutExpired(cmd, timeout)
            # os.read는 **지금 준비된 만큼만** 반환한다(최대 1MB) — 버퍼드 read(1<<20)는
            # 1MB가 찰 때까지 블로킹해, 1바이트만 쓰고 멈춘 자식이 데드라인을 넘겨
            # 매달리게 한다(Codex #4). select가 준비를 알린 fd에서만 읽는다.
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError("extract_output_too_large")
            chunks.append(chunk)
    finally:
        try:
            p.stdout.close()
        except OSError:
            pass
        if p.poll() is None:
            p.kill()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    return p.returncode, b"".join(chunks)


MAX_PDF_BYTES = 100 * 1024 * 1024


def extract_pdf(path):
    if path.stat().st_size > MAX_PDF_BYTES:
        return _result(False, reason="pdf_too_large")
    if not shutil.which("pdftotext"):
        return _result(False, reason="pdftotext_unavailable")
    try:
        rc, out = _run_capped(
            ["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"], timeout=120)
    except subprocess.TimeoutExpired:
        return _result(False, reason="pdftotext_timeout")
    except RuntimeError:
        return _result(False, reason="pdf_output_too_large")
    except OSError as e:
        return _result(False, reason=f"pdftotext_error: {e}")
    if rc != 0:
        return _result(False, reason=f"pdftotext_exit_{rc}")
    text = out.decode("utf-8", "replace").strip()
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

    # 신뢰불가 파일 방어(Codex sole #4): num_difat/섹터 체인은 헤더가 조작할 수
    # 있다. 실제 섹터 수는 파일 크기로 상한이 정해지므로 그 이상은 무의미하고,
    # 자기참조 체인(sector 0→0)은 무한 루프가 된다 — 파일 크기 기반 상한 +
    # 방문 집합으로 사이클을 끊는다.
    max_sectors = len(data) // ssz + 1 if ssz else 0
    difat = list(struct.unpack_from("<109I", data, 76))
    s = difat_start
    seen_difat = set()
    for _ in range(min(num_difat, max_sectors)):
        if s in _CFB_END or s in seen_difat or s >= max_sectors:
            break
        seen_difat.add(s)
        vals = struct.unpack(f"<{per}I", sector(s))
        difat.extend(vals[:-1])
        s = vals[-1]
    fat = []
    seen_fat = set()
    for fs in difat:
        if fs in _CFB_END or fs in seen_fat or fs >= max_sectors:
            continue
        seen_fat.add(fs)
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
            rc, out = _run_capped(["hwp5txt", str(path)], timeout=120)
            if rc == 0:
                text = out.decode("utf-8", "replace").strip()
                if text:
                    return _result(True, text=text)
        except (subprocess.TimeoutExpired, RuntimeError, OSError):
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
            text = r["text"]
            if r.get("reason") == "hwp_preview_only":
                # 파일만 보는 소비자도 부분 추출임을 알 수 있게 파일 안에 마커를 남긴다
                text = ("[HWP_PREVIEW_ONLY — 미리보기 부분 추출: 전체 본문 아님. "
                        "이 텍스트만으로 '확인됨' 판정 금지]\n\n") + text
            pathlib.Path(args.output).write_text(text, encoding="utf-8")
        else:
            # 실패 시 빈 파일을 만들지 않는다 — reason을 담은 마커 파일만 생성.
            # 이전 실행의 성공 출력이 남아 있으면 stale로 밀어 혼동을 막는다.
            outp = pathlib.Path(args.output)
            if outp.exists():
                stale = args.output + ".stale-" + time.strftime("%Y%m%d-%H%M%S")
                outp.rename(stale)
                print(f"[sole-search] 이전 출력은 {stale}로 보존(현재 추출 실패)",
                      file=sys.stderr)
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
