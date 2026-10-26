import os
import re
import json
import traceback
import requests
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timedelta, timezone

from filter_rules import (
    RESTRICT_MARK, check_eligibility, RECOMMEND_THRESHOLD, PRODUCT_BONUS_MAX, PRODUCT_PENALTY_MAX, IT_SERVICE_CLASSES, TRACK_A_KEYWORDS, TRACK_A_EXCLUDE,
    TRACK_B_KEYWORDS, TRACK_B_EXCLUDE, TRACK_B_CORE, score_title,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 공공데이터포털 인증키는 GitHub Secrets로 주입합니다.
#   G2B_API_KEY: 나라장터 입찰공고정보서비스, MSIT_API_KEY: 과기정통부 사업공고
#   (한 키로 둘 다 승인받았다면 DATA_GO_KR_API_KEY 하나만 등록해도 됩니다.)
COMMON_KEY = os.environ.get("DATA_GO_KR_API_KEY", "").strip()
G2B_API_KEY = os.environ.get("G2B_API_KEY", "").strip() or COMMON_KEY
MSIT_API_KEY = os.environ.get("MSIT_API_KEY", "").strip() or COMMON_KEY

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TODAY = NOW.date()
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# 수집은 성공했지만 화면에 알려줄 참고사항 (출처별)
NOTES = {}

session = requests.Session()
session.headers.update(HEADERS)
# 기관 서버의 일시적인 접속 지연에 대비해 실패 시 최대 3번까지 다시 시도합니다.
_retry = Retry(total=3, connect=3, read=2, backoff_factor=2, status_forcelist=[502, 503, 504],
               allowed_methods=["GET", "POST"])
session.mount("https://", HTTPAdapter(max_retries=_retry))
session.mount("http://", HTTPAdapter(max_retries=_retry))


def parse_date(text):
    """'2026.10.30', '2026-10-30 10:00', '20261030' 등을 date로 변환합니다."""
    if not text:
        return None
    digits = re.sub(r"[^0-9]", "", str(text))[:8]
    if len(digits) != 8:
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d").date()
    except ValueError:
        return None


def format_won(value):
    """55000000 → '5,500만 원', 9000000000 → '90억 원'."""
    try:
        won = int(float(value))
    except (TypeError, ValueError):
        return ""
    if won <= 0:
        return ""
    eok, man = divmod(won // 10000, 10000)
    if eok:
        return f"{eok:,}억 {man:,}만 원" if man else f"{eok:,}억 원"
    return f"{man:,}만 원" if man else f"{won:,}원"


def parse_amount(num, unit):
    """('85.8', '억원') → 8580000000. 금액을 원 단위 정수로 바꿉니다."""
    try:
        value = float(num.replace(",", ""))
    except ValueError:
        return 0
    unit = re.sub(r"\s", "", unit)
    scale = {"억원": 10**8, "백만원": 10**6, "천만원": 10**7, "만원": 10**4, "천원": 10**3, "원": 1}.get(unit, 0)
    return int(value * scale)


AMOUNT = r"([\d][\d,]*(?:\.\d+)?)\s*(억\s*원|천만\s*원|백만\s*원|만\s*원|천\s*원|원)"


_TEXT_CACHE = {}


def page_text(url):
    """상세 페이지 본문 텍스트 (같은 실행 안에서는 한 번만 받아옵니다)."""
    if url not in _TEXT_CACHE:
        res = session.get(url, verify=False, timeout=20)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        for tag in soup(["script", "style", "header", "nav", "footer"]):
            tag.decompose()
        _TEXT_CACHE[url] = soup.get_text(" ", strip=True)
    return _TEXT_CACHE[url]


def eligibility_snippets(text):
    """본문에서 '…에만 지원', '…에 한함' 같은 제한 문장 주변만 잘라 둡니다."""
    out = []
    for m in re.finditer(RESTRICT_MARK, text):
        # 앞 문장의 단어가 섞이지 않도록 문장 경계(. ※ ○ ■ 등)에서 자릅니다.
        boundary = r"[.。※○■□▶●◦]|\s[가-하]\.\s|\s-\s"
        before = re.split(boundary, text[max(0, m.start() - 120):m.start()])[-1]
        after = re.split(boundary, text[m.end():m.end() + 40])[0]
        snippet = (before + text[m.start():m.end()] + after).strip()
        if snippet not in out:
            out.append(snippet)
    return out[:8]


def ntis_amount(url):
    """NTIS 공고 상세의 '공고금액 : 50 억원' 항목을 읽습니다."""
    m = re.search(r"공고금액\s*:?\s*" + AMOUNT, page_text(url))
    won = parse_amount(m.group(1), m.group(2)) if m else 0
    return f"공고금액 {format_won(won)}" if won else ""


def iris_amount(url):
    """IRIS 공고 본문에 적힌 '지원규모 총 85.8억원'을 읽습니다 (본문이 첨부파일에만 있으면 빈 값)."""
    text = page_text(url)
    for label, pattern in [("지원규모", r"지원\s*규모\s*(?:총\s*)?(?:약\s*)?"),
                           ("정부출연금", r"정부\s*출연금\s*(?:총\s*)?(?:약\s*)?"),
                           ("사업비", r"총\s*사업비\s*(?:약\s*)?")]:
        m = re.search(pattern + AMOUNT, text)
        if m:
            won = parse_amount(m.group(1), m.group(2))
            if won:
                return f"{label} {format_won(won)}"
    return ""


def make_item(source, kind, org, title, url, open_date=None, close_date=None, status="", budget=""):
    """모든 출처의 공고를 같은 형식으로 맞춥니다. 마감된 공고는 None을 반환합니다."""
    close = parse_date(close_date)
    if close and close < TODAY:
        return None
    opened = parse_date(open_date)
    dday = (close - TODAY).days if close else None
    return {
        "source": source,
        "kind": kind,  # rnd: R&D/지원사업, bid: 입찰
        "org": (org or source).strip(),
        "title": re.sub(r"\s+", " ", title or "").strip(),
        "url": url,
        "open_date": opened.isoformat() if opened else "",
        "close_date": close.isoformat() if close else "",
        "dday": dday,
        "status": status,
        "budget": budget or "",
    }


# ----------------- 1. [API] IRIS 범부처통합연구지원시스템 -----------------
def fetch_iris():
    items = []
    list_url = "https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituList.do"
    view_url = "https://www.iris.go.kr/contents/retrieveBsnsAncmView.do?ancmId={}&ancmPrg={}"
    # 접수중은 전부, 접수예정은 최근 공고 위주로 몇 페이지만 확인합니다 (페이지당 10건).
    for prg, max_pages in [("ancmIng", 20), ("ancmPre", 5)]:
        page = 1
        while page <= max_pages:
            res = session.post(list_url, data={"pageIndex": str(page), "ancmPrg": prg}, verify=False, timeout=20)
            res.raise_for_status()
            data = res.json()
            rows = data.get("listBsnsAncmBtinSitu") or []
            for r in rows:
                if prg == "ancmPre":
                    posted = parse_date(r.get("ancmDe"))
                    if not posted or (TODAY - posted).days > 45:
                        continue
                item = make_item(
                    "IRIS", "rnd",
                    r.get("sorgnNm") or r.get("blngGovdSeNm"),
                    r.get("ancmTl"),
                    view_url.format(r.get("ancmId"), prg),
                    open_date=r.get("rcveStrDe") or r.get("ancmDe"),
                    close_date=r.get("rcveEndDe"),
                    status="접수중" if prg == "ancmIng" else "접수예정",
                )
                if item:
                    item["ministry"] = r.get("blngGovdSeNm") or ""
                    items.append(item)
            total_pages = (data.get("paginationInfo") or {}).get("totalPageCount", 1)
            if not rows or page >= total_pages:
                break
            page += 1
    return items


# ----------------- 2. [크롤러] NTIS 국가R&D 통합공고 -----------------
def fetch_ntis():
    items, seen = [], set()
    list_url = "https://www.ntis.go.kr/rndgate/eg/un/ra/mng.do"
    view_url = "https://www.ntis.go.kr/rndgate/eg/un/ra/view.do?roRndUid={}&flag=rndList"
    for page in range(1, 6):
        res = session.post(list_url, data={"pageIndex": str(page), "pageUnit": "100", "searchStatusList": "P,B"},
                           verify=False, timeout=25)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        new_rows = 0
        for row in soup.find_all("tr"):
            title_td = row.find("td", attrs={"data-title": "공고명"})
            if not title_td:
                continue
            a = title_td.find("a")
            m = re.search(r"fn_view\('(\d+)'\)", a.get("onclick", "") if a else "")
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            new_rows += 1

            def col(name):
                td = row.find("td", attrs={"data-title": name})
                return td.get_text(" ", strip=True) if td else ""

            item = make_item(
                "NTIS", "rnd", col("부처명"),
                a.get("title") or a.get_text(" ", strip=True),
                view_url.format(m.group(1)),
                open_date=col("접수일"), close_date=col("마감일"), status=col("현황"),
            )
            if item:
                item["ministry"] = col("부처명")
                try:
                    item["budget"] = ntis_amount(item["url"])
                except requests.RequestException:
                    pass
                items.append(item)
        if new_rows < 100:
            break
    return items


# ----------------- 3. [크롤러] SMTECH 중소기업 R&D 사업공고 -----------------
def fetch_smtech():
    items = []
    base = "https://www.smtech.go.kr"
    for page in range(1, 4):
        res = session.get(f"{base}/front/ifg/no/notice02_list.do", params={"pageIndex": page}, verify=False, timeout=20)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        table = next((t for t in soup.find_all("table") if "사업공고 목록" in t.get_text()), None)
        if not table:
            break
        rows = [r for r in table.find_all("tr") if len(r.find_all("td")) >= 6]
        for row in rows:
            cols = row.find_all("td")
            system, biz, title_td, period, posted = (cols[1].get_text(strip=True), cols[2].get_text(" ", strip=True),
                                                     cols[3], cols[4].get_text(" ", strip=True), cols[5].get_text(strip=True))
            # IRIS로 넘어가는 공고는 IRIS 수집에서 이미 가져옵니다.
            if system != "SMTECH":
                continue
            a = row.find("a", href=True)
            href = a["href"] if a else ""
            href = re.sub(r";jsessionid=[^?]*", "", href)
            url = base + href if href.startswith("/") else f"{base}/front/ifg/no/notice02_list.do"
            dates = re.findall(r"\d{4}\.\s*\d{1,2}\.\s*\d{1,2}", period)
            norm = [re.sub(r"\s", "", d) for d in dates]
            norm = ["{}.{:0>2}.{:0>2}".format(*d.split(".")) for d in norm]
            item = make_item(
                "SMTECH", "rnd", "중소벤처기업부",
                f"{title_td.get_text(' ', strip=True)} ({biz})" if biz else title_td.get_text(" ", strip=True),
                url,
                open_date=norm[0] if norm else posted,
                close_date=norm[1] if len(norm) > 1 else None,
                status=cols[6].get_text(strip=True) if len(cols) > 6 else "",
            )
            if item:
                item["ministry"] = "중소벤처기업부"
                items.append(item)
        if len(rows) < 10:
            break
    return items


# ----------------- 4. [크롤러] 한국기계연구원 (KIMM) -----------------
def fetch_kimm():
    items = []
    res = session.get("https://www.kimm.re.kr/bidding", verify=False, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    for row in soup.find_all("tr"):
        cols = row.find_all("td")
        if len(cols) < 7:
            continue
        a = cols[2].find("a")
        if not a:
            continue
        link = a.get("href", "")
        if link.startswith("/"):
            link = "https://www.kimm.re.kr" + link
        item = make_item(
            "기계연", "bid", "한국기계연구원", a.get_text(" ", strip=True), link,
            open_date=cols[5].get_text(strip=True), close_date=cols[6].get_text(strip=True),
            status=cols[3].get_text(strip=True),
        )
        if item:
            item["bid_type"] = cols[1].get_text(strip=True)
            items.append(item)
    return items


# ----------------- 5. [API] 조달청 나라장터 (G2B) -----------------
def g2b_products(bid):
    """입찰공고의 세부 품명·분류명 목록을 모읍니다 (예: '태양광발전장치', '가스미터')."""
    names = []
    # 구매대상물품목록: '[1^4111250801^가스미터],[2^...^...]'
    names += re.findall(r"\^([^\^\]]+)\]", bid.get("purchsObjPrdctList") or "")
    names.append(bid.get("dtilPrdctClsfcNoNm") or "")
    out = []
    for n in names:
        n = n.strip()
        if n and n not in out:
            out.append(n)
    return out



def fetch_g2b():
    if not G2B_API_KEY:
        raise RuntimeError("G2B_API_KEY가 설정되지 않았습니다 (GitHub Secrets 확인)")
    items, seen = [], set()
    bgn_dt = (NOW - timedelta(days=14)).strftime("%Y%m%d0000")
    end_dt = NOW.strftime("%Y%m%d2359")
    # 이미 URL 인코딩된 키(%2B 등)는 그대로, 디코딩된 키는 requests가 인코딩하도록 합니다.
    key_q = f"serviceKey={G2B_API_KEY}" if "%" in G2B_API_KEY else None
    endpoints = {
        "getBidPblancListInfoThngPPSSrch": "물품",
        "getBidPblancListInfoServcPPSSrch": "용역",
        "getBidPblancListInfoCnstwkPPSSrch": "공사",
    }
    for ep, bid_type in endpoints.items():
        for page in range(1, 5):
            params = {"numOfRows": 500, "pageNo": page, "inqryDiv": 1,
                      "inqryBgnDt": bgn_dt, "inqryEndDt": end_dt, "type": "json"}
            url = f"https://apis.data.go.kr/1230000/ad/BidPublicInfoService/{ep}"
            if key_q:
                url += "?" + key_q
            else:
                params["serviceKey"] = G2B_API_KEY
            res = session.get(url, params=params, verify=False, timeout=30)
            res.raise_for_status()
            if res.text.strip().startswith("<"):
                raise RuntimeError(f"{bid_type} API 오류 응답: {res.text[:200]}")
            body = res.json().get("response", {}).get("body", {})
            bids = body.get("items") or []
            if isinstance(bids, dict):
                bids = bids.get("item", [bids])
            if isinstance(bids, dict):
                bids = [bids]
            for bid in bids:
                key = (bid.get("bidNtceNo"), bid.get("bidNtceOrd"))
                if key in seen:
                    continue
                seen.add(key)
                title = bid.get("bidNtceNm", "")
                # 공사는 설비 관련 공고만 남깁니다.
                if bid_type == "공사" and not any(k in title for k in ["설비", "장치", "플랜트", "배관", "기계"]):
                    continue
                budget = bid.get("presmptPrce") or bid.get("asignBdgtAmt") or ""
                # '1원'처럼 형식적으로 입력된 금액은 미공개로 봅니다.
                try:
                    budget = format_won(budget) if float(budget or 0) >= 10000 else ""
                except ValueError:
                    budget = ""
                budget = budget or "금액 미공개"
                item = make_item(
                    "나라장터", "bid", bid.get("dminsttNm") or bid.get("ntceInsttNm") or "조달청",
                    title, bid.get("bidNtceDtlUrl") or bid.get("bidNtceUrl") or "https://www.g2b.go.kr",
                    open_date=bid.get("bidNtceDt"), close_date=bid.get("bidClseDt"),
                    status="입찰공고", budget=budget,
                )
                if item:
                    item["bid_no"], item["bid_ord"] = bid.get("bidNtceNo"), bid.get("bidNtceOrd")
                    item["bid_type"] = bid_type
                    if bid_type == "물품":
                        item["products"] = g2b_products(bid)
                    else:
                        # 용역은 품명 대신 'ICT 서비스' 같은 포괄 분류만 있어 IT 용역을 거르는 데만 씁니다.
                        item["service_class"] = [v for k, v in bid.items() if k.endswith("ClsfcNm") and isinstance(v, str) and v]
                    items.append(item)
            total = int(body.get("totalCount") or 0)
            if page * 500 >= total:
                break
    return items


# ----------------- 6. [API] 과학기술정보통신부 사업공고 -----------------
def fetch_msit():
    if not MSIT_API_KEY:
        raise RuntimeError("MSIT_API_KEY가 설정되지 않았습니다 (GitHub Secrets 확인)")
    url = "http://apis.data.go.kr/1721000/msitannouncementinfo/businessAnnouncMentList"
    params = {"numOfRows": 50, "pageNo": 1, "returnType": "json"}
    if "%" in MSIT_API_KEY:
        url += f"?serviceKey={MSIT_API_KEY}"
    else:
        params["serviceKey"] = MSIT_API_KEY
    res = session.get(url, params=params, verify=False, timeout=20)
    res.raise_for_status()
    if res.text.strip().startswith("<"):
        raise RuntimeError(f"API 오류 응답: {res.text[:200]}")
    data = res.json()

    # 응답 구조가 {"response": [{"header":…}, {"body":…}]}처럼 문서와 다를 수 있어
    # 'subject' 필드를 가진 항목을 구조에 상관없이 찾아냅니다.
    def walk(node):
        if isinstance(node, dict):
            if "subject" in node:
                yield node
            else:
                for v in node.values():
                    yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)

    found = list(walk(data))
    if not found:
        NOTES["과기정통부"] = f"API 응답에 공고 없음: {res.text[:200]}"
    items = []
    for r in found:
        posted = parse_date(r.get("pressDt"))
        if posted and (TODAY - posted).days > 30:
            continue
        item = make_item("과기정통부", "rnd", r.get("deptName") or "과학기술정보통신부", r.get("subject"),
                         r.get("viewUrl") or "https://www.msit.go.kr", open_date=r.get("pressDt"), status="게시")
        if item:
            item["ministry"] = "과학기술정보통신부"
            items.append(item)
    if found and not items:
        latest = max((r.get("pressDt") or "" for r in found), default="")
        NOTES["과기정통부"] = f"최근 30일 내 공고 없음 (API 응답 {len(found)}건, 최신 게시일 {latest})"
    return items


SOURCES = [
    ("IRIS", fetch_iris),
    ("NTIS", fetch_ntis),
    ("SMTECH", fetch_smtech),
    ("과기정통부", fetch_msit),
    ("나라장터", fetch_g2b),
    ("기계연", fetch_kimm),
]


def norm_title(t):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", t)


def is_test_post(title):
    """IRIS 등에 올라오는 시험용 게시물('TEST', 'fsdfsdf' 등)을 걸러냅니다."""
    t = title.strip()
    return len(t) < 8 or re.search(r"\bTEST\b|테스트 입니다", t, re.I) is not None


# 하나의 공고를 '공고명(…)_(2026-DCP-65_에너지…)', '공고명(…)_NRF'처럼 세부과제·전담기관별로
# 나눠 올리는 경우가 많아, 같은 공고명이 2건 이상이면 하나로 묶어서 보여줍니다.
SUB_ANCM = re.compile(r"^(.+?\))_(.+)$")


def group_sub_announcements(items):
    def split(it):
        m = SUB_ANCM.match(it["title"])
        return (m.group(1).strip(), m.group(2).strip("() ")) if m else (None, None)

    base_count = {}
    for it in items:
        base, _ = split(it)
        if base:
            key = (it["source"], norm_title(base))
            base_count[key] = base_count.get(key, 0) + 1

    grouped, order = {}, []
    for it in items:
        base, suffix = split(it)
        key = (it["source"], norm_title(base)) if base else None
        if not key or base_count.get(key, 0) < 2:
            order.append(it)
            continue
        variant = {"label": suffix, "url": it["url"], "score": it["score"], "hits": it["hits"], "budget": it["budget"]}
        if key not in grouped:
            grouped[key] = dict(it, title=base, variants=[])
            order.append(grouped[key])
        head = grouped[key]
        head["variants"].append(variant)
        # 가장 점수가 높은 세부과제를 대표로 사용합니다.
        if it["score"] > head["score"]:
            head.update(score=it["score"], hits=it["hits"], minus=it["minus"], track=it["track"], url=it["url"],
                        budget=it["budget"], elig_text=it.get("elig_text", []))
        if it["dday"] is not None and (head["dday"] is None or it["dday"] < head["dday"]):
            head.update(dday=it["dday"], close_date=it["close_date"])
    # IRIS에 묶음 공고 본문이 따로 있고 NTIS에 세부공고가 있는 경우, 세부공고가 있는 쪽만 남깁니다.
    group_bases = {norm_title(h["title"]) for h in grouped.values()}
    order = [it for it in order if "variants" in it or norm_title(it["title"]) not in group_bases]
    for head in grouped.values():
        head["variants"].sort(key=lambda v: -v["score"])
        head["title"] = f"{head['title']} [세부공고 {len(head['variants'])}건]"
    return order


def classify(item):
    if item["kind"] == "rnd":
        score, hits, minus = score_title(item["title"], TRACK_A_KEYWORDS, TRACK_A_EXCLUDE)
        if item["source"] == "SMTECH":  # 중소기업 대상 사업
            score += 1
        track = "A"
    else:
        score, hits, minus = score_title(item["title"], TRACK_B_KEYWORDS, TRACK_B_EXCLUDE)
        if item.get("bid_type") == "물품" or item["source"] == "기계연":
            score += 1
        # 나라장터 세부 품명에서 제목에 없던 키워드를 찾아 보조 점수로 더합니다.
        product_text = " ".join(item.get("products") or [])
        if product_text:
            _, p_hits, p_minus = score_title(product_text, TRACK_B_KEYWORDS, TRACK_B_EXCLUDE)
            p_hits = [k for k in p_hits if k not in hits]
            p_minus = [k for k in p_minus if k not in minus]
            score += min(PRODUCT_BONUS_MAX, sum(TRACK_B_KEYWORDS[k] for k in p_hits))
            score += max(-PRODUCT_PENALTY_MAX, sum(TRACK_B_EXCLUDE[k] for k in p_minus))
            item["product_hits"] = p_hits
            item["product_minus"] = p_minus
        it_class = [c for c in item.get("service_class") or [] if any(k in c for k in IT_SERVICE_CLASSES)]
        if it_class:
            score -= 4
            minus = minus + [f"{it_class[0]}(분류)"]
        track = "B"
    item["score"] = score
    item["hits"] = hits
    item["minus"] = minus
    if track == "B" and not any(k in item["title"] + " " + " ".join(item.get("products") or []) for k in TRACK_B_CORE):
        track = ""
    item["track"] = track if score >= RECOMMEND_THRESHOLD else ""


def fill_rnd_budgets(items):
    """IRIS 공고는 목록에 금액이 없어, 같은 공고명의 NTIS 공고금액을 쓰고 없으면 IRIS 본문에서 찾습니다."""
    ntis_budget = {norm_title(it["title"]): it["budget"] for it in items if it["source"] == "NTIS" and it["budget"]}
    for it in items:
        if it["source"] != "IRIS" or it["budget"]:
            continue
        it["budget"] = ntis_budget.get(norm_title(it["title"]), "")
        if not it["budget"]:
            try:
                it["budget"] = iris_amount(it["url"])
            except requests.RequestException:
                pass


def collect_eligibility_text(items):
    """IRIS·NTIS 상세 본문에서 지원 자격 제한 문장을 모읍니다."""
    for it in items:
        if it["source"] not in ("IRIS", "NTIS"):
            continue
        try:
            it["elig_text"] = eligibility_snippets(page_text(it["url"]))
        except requests.RequestException:
            pass


def load_previous():
    """직전 data.json의 나라장터 제한조건 조회 결과를 재사용해 API 호출량을 줄입니다."""
    try:
        with open("data.json", encoding="utf-8") as f:
            return {it["url"]: it for it in json.load(f).get("items", []) if "limits_checked" in it}
    except (OSError, ValueError):
        return {}


def g2b_limits(item, previous):
    """나라장터 공고의 참가가능지역·면허제한을 조회합니다."""
    prev = previous.get(item["url"])
    if prev:
        item["regions"], item["licenses"] = prev.get("regions", []), prev.get("licenses", [])
        item["limits_checked"] = True
        return
    out = {}
    for ep, field in [("getBidPblancListInfoPrtcptPsblRgn", "prtcptPsblRgnNm"),
                      ("getBidPblancListInfoLicenseLimit", "lcnsLmtNm")]:
        url = f"https://apis.data.go.kr/1230000/ad/BidPublicInfoService/{ep}"
        params = {"numOfRows": 50, "pageNo": 1, "inqryDiv": 2, "type": "json",
                  "bidNtceNo": item["bid_no"], "bidNtceOrd": item["bid_ord"] or "000"}
        if "%" in G2B_API_KEY:
            url += f"?serviceKey={G2B_API_KEY}"
        else:
            params["serviceKey"] = G2B_API_KEY
        res = session.get(url, params=params, verify=False, timeout=20)
        res.raise_for_status()
        rows = res.json().get("response", {}).get("body", {}).get("items") or []
        if isinstance(rows, dict):
            rows = [rows]
        # 면허명은 '기계설비ㆍ가스공사업/6202' 형태라 코드 부분은 뺍니다.
        out[field] = list(dict.fromkeys(r.get(field, "").split("/")[0].strip() for r in rows if r.get(field)))
    item["regions"], item["licenses"] = out["prtcptPsblRgnNm"], out["lcnsLmtNm"]
    item["limits_checked"] = True


def apply_eligibility(items):
    previous = load_previous()
    for it in items:
        if it["source"] == "나라장터" and it["track"] and it.get("bid_no"):
            try:
                g2b_limits(it, previous)
            except (requests.RequestException, ValueError) as e:
                print(f"[나라장터] 제한조건 조회 실패 {it['bid_no']}: {e}")
        reason = check_eligibility(it["title"], it.get("elig_text"), it.get("regions"), it.get("licenses"))
        if reason:
            it["ineligible"] = reason
            if it["track"]:
                it["excluded_track"], it["track"] = it["track"], ""


def main():
    all_items, status = [], []
    for name, fn in SOURCES:
        try:
            got = fn()
            status.append({"name": name, "ok": True, "count": len(got), "error": NOTES.get(name, "")})
            all_items.extend(got)
            print(f"[{name}] {len(got)}건")
        except Exception as e:
            traceback.print_exc()
            status.append({"name": name, "ok": False, "count": 0, "error": str(e)[:300]})
            print(f"[{name}] 실패: {e}")

    fill_rnd_budgets(all_items)
    collect_eligibility_text(all_items)

    # NTIS 통합공고는 IRIS 공고와 겹치므로 같은 공고명은 하나만 남깁니다 (먼저 수집된 출처 우선).
    unique, seen = [], set()
    for it in all_items:
        key = norm_title(it["title"])
        if not key or key in seen or is_test_post(it["title"]):
            continue
        seen.add(key)
        classify(it)
        unique.append(it)

    unique = group_sub_announcements(unique)
    apply_eligibility(unique)

    # 나라장터는 건수가 많아 점수가 1점 이상인 공고만 전체 목록에 남깁니다.
    unique = [it for it in unique if it["source"] != "나라장터" or it["score"] >= 1]
    unique.sort(key=lambda it: (-(it["track"] != ""), it["dday"] if it["dday"] is not None else 999, -it["score"]))

    out = {
        "generated_at": NOW.strftime("%Y-%m-%d %H:%M:%S"),
        "threshold": RECOMMEND_THRESHOLD,
        "sources": status,
        "items": unique,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    rec_a = sum(1 for it in unique if it["track"] == "A")
    rec_b = sum(1 for it in unique if it["track"] == "B")
    excluded = sum(1 for it in unique if it.get("excluded_track"))
    print(f"동기화 완료: 총 {len(unique)}건 (추천 A {rec_a}건, B {rec_b}건, 자격 미해당 제외 {excluded}건)")


if __name__ == "__main__":
    main()
