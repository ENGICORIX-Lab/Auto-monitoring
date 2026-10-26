import os, re, io, json, zlib, struct, zipfile, requests
from datetime import datetime, timedelta
s = requests.Session(); s.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36"
os.makedirs("out", exist_ok=True)
key = os.environ.get("G2B_API_KEY", "")
targets = set(json.load(open("probe/targets.json")))
end = datetime.utcnow() + timedelta(hours=9); bgn = end - timedelta(days=14)
found = {}
for ep in ["getBidPblancListInfoThngPPSSrch", "getBidPblancListInfoServcPPSSrch"]:
    for page in range(1, 6):
        url = f"https://apis.data.go.kr/1230000/ad/BidPublicInfoService/{ep}"
        p = dict(numOfRows=500, pageNo=page, inqryDiv=1, inqryBgnDt=bgn.strftime("%Y%m%d0000"), inqryEndDt=end.strftime("%Y%m%d2359"), type="json")
        if "%" in key: url += f"?serviceKey={key}"
        else: p["serviceKey"] = key
        body = s.get(url, params=p, timeout=60).json()["response"]["body"]
        for b in body.get("items") or []:
            if b.get("bidNtceNo") in targets: found[b["bidNtceNo"]] = b
        if page * 500 >= int(body.get("totalCount") or 0): break
print("found", len(found), "of", len(targets))
sample = next(iter(found.values()), {})
print("ALL KEYS:", sorted(sample.keys()))

def hwp_text(data):
    import olefile
    ole = olefile.OleFileIO(io.BytesIO(data))
    hdr = ole.openstream("FileHeader").read()
    compressed = hdr[36] & 1
    out = []
    i = 0
    while ole.exists(f"BodyText/Section{i}"):
        raw = ole.openstream(f"BodyText/Section{i}").read()
        if compressed: raw = zlib.decompress(raw, -15)
        pos = 0
        while pos + 4 <= len(raw):
            h = struct.unpack_from("<I", raw, pos)[0]; tag = h & 0x3FF; size = (h >> 20) & 0xFFF; pos += 4
            if size == 0xFFF: size = struct.unpack_from("<I", raw, pos)[0]; pos += 4
            if tag == 67:
                t = raw[pos:pos+size].decode("utf-16le", errors="ignore")
                out.append(re.sub(r"[\x00-\x1f]", " ", t))
            pos += size
        i += 1
    return "\n".join(out)

def extract(name, data):
    n = name.lower()
    if n.endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages[:30])
    if n.endswith(".hwpx"):
        z = zipfile.ZipFile(io.BytesIO(data))
        return "\n".join(re.sub(r"<[^>]+>", " ", z.read(f).decode("utf-8", "ignore")) for f in z.namelist() if f.startswith("Contents/section"))
    if n.endswith(".hwp"):
        return hwp_text(data)
    if n.endswith(".zip"):
        z = zipfile.ZipFile(io.BytesIO(data)); parts = []
        for f in z.namelist():
            try: parts.append(extract(f.encode("cp437").decode("cp949", "ignore") if not f.isascii() else f, z.read(f)))
            except Exception as e: parts.append(f"[zip member err {e}]")
        return "\n".join(parts)
    return ""

res = {}
for no, b in list(found.items())[:40]:
    r = {"title": b.get("bidNtceNm"), "price": b.get("presmptPrce"), "files": []}
    for i in range(1, 11):
        url, name = b.get(f"ntceSpecDocUrl{i}"), b.get(f"ntceSpecFileNm{i}")
        if not url: continue
        try:
            resp = s.get(url, timeout=60)
            text = extract(name or "", resp.content)
            hits = [text[max(0, m.start()-150):m.end()+100].replace("\n", " ") for m in re.finditer(r"소기업|소상공인", text)][:6]
            r["files"].append({"name": name, "status": resp.status_code, "ctype": resp.headers.get("content-type"), "bytes": len(resp.content), "chars": len(text), "hits": hits})
        except Exception as e:
            r["files"].append({"name": name, "err": str(e)[:200]})
    res[no] = r
json.dump(res, open("out/sme.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("done")
