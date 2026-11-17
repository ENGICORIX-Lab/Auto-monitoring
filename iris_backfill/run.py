"""2026년 IRIS 사업공고(마감·접수중)를 모아 지원 자격·업종 제한 문장을 찾습니다 (일회성 분석용)."""
import os, re, sys, json, time, html
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import update_data as u
from attachments import extract_text
from filter_rules import TRACK_A_KEYWORDS, TRACK_A_EXCLUDE, score_title, check_eligibility

T0 = time.time()
LIST = "https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituList.do"
VIEW = "https://www.iris.go.kr/contents/retrieveBsnsAncmView.do?ancmId={}&ancmPrg={}"
DOWN = "https://www.iris.go.kr/comm/file/fileDownload.do"
YEAR = "2026"
LOG = {"pages": 0, "errors": []}

rows = {}
for prg in ("ancmIng", "ancmEnd"):
    page = 1
    while page <= 400:
        try:
            d = u.session.post(LIST, data={"pageIndex": str(page), "ancmPrg": prg}, verify=False, timeout=30).json()
        except Exception as e:
            LOG["errors"].append(f"list {prg} p{page}: {str(e)[:100]}")
            page += 1
            continue
        LOG["pages"] += 1
        got = d.get("listBsnsAncmBtinSitu") or []
        if not got:
            break
        for r in got:
            if (r.get("ancmDe") or "").startswith(YEAR) and r.get("ancmId") not in rows:
                r["_prg"] = prg
                rows[r["ancmId"]] = r
        # 최신순 정렬이라 이전 연도만 나오는 페이지에서 멈춥니다.
        if all((r.get("ancmDe") or "9") < YEAR for r in got):
            break
        page += 1
print("2026 공고", len(rows), "목록 페이지", LOG["pages"], flush=True)

# 업종·면허·등록 관련 자격 문장
INDUSTRY = re.compile(r"업종|표준산업분류|산업분류|KSIC|면허|사업자\s*등록|등록(?:을)?\s*(?:한|된|필)\s*(?:자|기업|업체)|신고(?:를)?\s*(?:한|된)\s*(?:자|기업|업체)|전문기업|확인서|인증")
CONTEXT = re.compile(r"신청\s*자격|지원\s*자격|참여\s*자격|지원\s*대상|신청\s*대상|자격\s*요건|참여\s*제한|지원\s*제외|주관\s*(?:연구개발)?기관|신청\s*가능|에\s*한함|한하여|이어야|하여야")


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.다음임함])\s+|[※○■□▶●◦]|\s-\s", text) if 15 <= len(s.strip()) <= 400]


def industry_snips(text):
    out = []
    for s in sentences(text):
        if INDUSTRY.search(s) and CONTEXT.search(s) and s not in out:
            out.append(re.sub(r"\s+", " ", s)[:300])
    return out[:8]


def process(r):
    aid, prg = r["ancmId"], r["_prg"]
    title = " ".join((r.get("ancmTl") or "").split())
    score, hits, minus = score_title(title, TRACK_A_KEYWORDS, TRACK_A_EXCLUDE)
    item = {"id": aid, "title": title, "org": r.get("sorgnNm") or "", "ministry": r.get("blngGovdSeNm") or "",
            "posted": r.get("ancmDe") or "", "open": r.get("rcveStrDe") or "", "close": r.get("rcveEndDe") or "",
            "type": r.get("pbofrTpSeNmLst") or "", "state": r.get("rcveSttSeNmLst") or "",
            "url": VIEW.format(aid, prg), "score": score, "hits": hits, "minus": minus,
            "files": [], "read": 0, "chars": 0}
    try:
        res = u.session.get(item["url"], verify=False, timeout=30)
        res.raise_for_status()
        page = res.text
    except Exception as e:
        item["error"] = str(e)[:100]
        return item
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page, "html.parser")
    for tag in soup(["script", "style", "header", "nav", "footer"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    files = re.findall(r"f_bsnsAncm_downloadAtchFile\(\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'", page)
    files = [(d_, f_, html.unescape(n)) for d_, f_, n in files]
    item["files"] = [n for _, _, n in files]
    # 기준 점수에 가까운 공고만 첨부 공고문까지 읽습니다 (공고문 → 그 밖의 문서 순, 최대 3개).
    if score >= 2:
        order = sorted(files, key=lambda f: (0 if "공고" in f[2] else 1 if re.search(r"안내|계획|RFP|요청", f[2]) else 2))
        for doc, fid, name in order[:3]:
            if not re.search(r"\.(pdf|hwp|hwpx|zip)$", name.lower()):
                continue
            try:
                fr = u.session.get(DOWN, params={"atchDocId": doc, "atchFileId": fid}, verify=False, timeout=60)
                fr.raise_for_status()
                t = extract_text(name, fr.content)
            except Exception:
                continue
            if t.strip():
                item["read"] += 1
                text += "\n" + t
    item["chars"] = len(text)
    item["elig"] = u.eligibility_snippets(text)
    item["reason"] = check_eligibility(title, item["elig"], None)
    item["industry"] = industry_snips(text)
    return item


with ThreadPoolExecutor(5) as pool:
    items = list(pool.map(process, rows.values()))
LOG.update(count=len(items), seconds=int(time.time() - T0), with_files=sum(bool(i["files"]) for i in items),
           read=sum(i["read"] > 0 for i in items), errors_detail=sum("error" in i for i in items))
os.makedirs("out", exist_ok=True)
json.dump({"log": LOG, "items": items}, open("out/iris.json", "w", encoding="utf-8"), ensure_ascii=False)
print(json.dumps(LOG, ensure_ascii=False)[:1500])
