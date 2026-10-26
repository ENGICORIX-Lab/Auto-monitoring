"""나라장터 공고 첨부파일(PDF·HWP·HWPX·ZIP)에서 본문 텍스트를 뽑습니다."""
import io
import re
import zlib
import struct
import zipfile

import olefile
from pypdf import PdfReader

MAX_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 40


def _hwp_text(data):
    """한글(HWP 5.x) 문서의 문단 텍스트를 읽습니다."""
    ole = olefile.OleFileIO(io.BytesIO(data))
    compressed = ole.openstream("FileHeader").read()[36] & 1
    out, i = [], 0
    while ole.exists(f"BodyText/Section{i}"):
        raw = ole.openstream(f"BodyText/Section{i}").read()
        if compressed:
            raw = zlib.decompress(raw, -15)
        pos = 0
        while pos + 4 <= len(raw):
            header = struct.unpack_from("<I", raw, pos)[0]
            tag, size = header & 0x3FF, (header >> 20) & 0xFFF
            pos += 4
            if size == 0xFFF:
                size = struct.unpack_from("<I", raw, pos)[0]
                pos += 4
            if tag == 67:  # HWPTAG_PARA_TEXT
                text = raw[pos:pos + size].decode("utf-16le", errors="ignore")
                out.append(re.sub(r"[\x00-\x1f]", " ", text))
            pos += size
        i += 1
    return "\n".join(out)


def _hwpx_text(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    sections = [f for f in z.namelist() if f.startswith("Contents/section")]
    return "\n".join(re.sub(r"<[^>]+>", " ", z.read(f).decode("utf-8", "ignore")) for f in sections)


def _pdf_text(data):
    return "\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(data)).pages[:MAX_PDF_PAGES])


def extract_text(name, data):
    """파일 이름의 확장자에 맞춰 텍스트를 뽑습니다. 읽을 수 없는 형식이면 빈 문자열입니다."""
    if len(data) > MAX_BYTES:
        return ""
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    try:
        if ext == "pdf":
            return _pdf_text(data)
        if ext == "hwpx":
            return _hwpx_text(data)
        if ext == "hwp":
            return _hwp_text(data)
        if ext == "zip":
            z = zipfile.ZipFile(io.BytesIO(data))
            parts = []
            for member in z.namelist():
                # 한글 파일명은 cp949로 저장된 경우가 많습니다.
                try:
                    member_name = member.encode("cp437").decode("cp949")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    member_name = member
                parts.append(extract_text(member_name, z.read(member)))
            return "\n".join(parts)
    except Exception:
        # 확장자와 실제 형식이 다른 경우(HWPX로 저장된 HWP 등)를 한 번 더 시도합니다.
        if ext == "hwpx":
            try:
                return _hwp_text(data)
            except Exception:
                return ""
    return ""
