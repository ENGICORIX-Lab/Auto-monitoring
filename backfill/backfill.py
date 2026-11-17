"""2026년 하반기 나라장터 공고를 사이트와 같은 기준으로 다시 채점합니다 (일회성 분석용)."""
import os, sys, json, time, traceback
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import update_data as u
from attachments import extract_text
from filter_rules import small_business_only

NOW_KST = datetime.utcnow() + timedelta(hours=9)
START = datetime.strptime(os.environ.get("BACKFILL_START", "2026-07-01"), "%Y-%m-%d")
END = min(datetime.strptime(os.environ.get("BACKFILL_END", "2099-01-01"), "%Y-%m-%d"), NOW_KST)
T0 = time.time()
LOG = {"calls": 0, "errors": [], "windows": []}


def call(op, params, tries=4):
    for i in range(tries):
        try:
            LOG["calls"] += 1
            return u.g2b_items(op, params)
        except Exception as e:
            err = str(e)[:200]
            if i == tries - 1:
                LOG["errors"].append(f"{op} {params.get('inqryBgnDt')} p{params.get('pageNo')}: {err}")
                raise
            time.sleep(5 * (i + 1))


def windows(days):
    cur = START
    while cur < END:
        nxt = min(cur + timedelta(days=days), END)
        yield cur.strftime("%Y%m%d%H%M"), (nxt - timedelta(minutes=1)).strftime("%Y%m%d%H%M")
        cur = nxt


def fetch_all(op, days, on_rows):
    """기간을 나눠 한 오퍼레이션의 모든 행을 읽습니다. 실패한 구간은 건너뛰고 기록합니다."""
    total_rows = 0
    for bgn, end in windows(days):
        page = 1
        try:
            while True:
                rows, total = call(op, {"numOfRows": 999, "pageNo": page, "inqryDiv": 1,
                                        "inqryBgnDt": bgn, "inqryEndDt": end})
                on_rows(rows)
                total_rows += len(rows)
                if page * 999 >= total or not rows:
                    break
                page += 1
        except Exception:
            LOG["windows"].append(f"{op} {bgn}~{end} 실패")
    print(f"[{op}] {total_rows}행, 누적 호출 {LOG['calls']}, {int(time.time()-T0)}초", flush=True)
    return total_rows


# ---------- 1. 공고 목록 → 사이트와 같은 기준으로 채점 ----------
cands, seen_total = {}, {"물품": 0, "용역": 0, "공사": 0}


def make_handler(bid_type):
    def handle(rows):
        for bid in rows:
            seen_total[bid_type] += 1
            title = bid.get("bidNtceNm", "") or ""
            if bid_type == "공사" and not any(k in title for k in ["설비", "장치", "플랜트", "배관", "기계"]):
                continue
            item = {"source": "나라장터", "kind": "bid", "title": " ".join(title.split()), "bid_type": bid_type}
            if bid_type == "물품":
                item["products"] = u.g2b_products(bid)
            else:
                item["service_class"] = [v for k, v in bid.items() if k.endswith("ClsfcNm") and isinstance(v, str) and v]
            u.classify(item)
            if item["track"] != "B":
                continue
            no, ord_ = bid.get("bidNtceNo"), bid.get("bidNtceOrd") or "000"
            prev = cands.get(no)
            if prev and prev["bid_ord"] >= ord_:
                continue
            item.update(
                bid_no=no, bid_ord=ord_, org=bid.get("dminsttNm") or bid.get("ntceInsttNm") or "",
                notice_org=bid.get("ntceInsttNm") or "", kind_name=bid.get("ntceKindNm") or "",
                posted=bid.get("bidNtceDt") or "", close=bid.get("bidClseDt") or "", open=bid.get("opengDt") or "",
                price=bid.get("presmptPrce") or "", budget=bid.get("asignBdgtAmt") or "",
                contract=bid.get("cntrctCnclsMthdNm") or "", award=bid.get("sucsfbidMthdNm") or "",
                url=bid.get("bidNtceDtlUrl") or bid.get("bidNtceUrl") or "",
                attachments=[{"name": bid.get(f"ntceSpecFileNm{i}") or "", "url": bid[f"ntceSpecDocUrl{i}"]}
                             for i in range(1, 11) if bid.get(f"ntceSpecDocUrl{i}")],
            )
            cands[no] = item
    return handle


for op, bid_type in [("getBidPblancListInfoThngPPSSrch02", "물품"), ("getBidPblancListInfoServcPPSSrch02", "용역"),
                     ("getBidPblancListInfoCnstwkPPSSrch02", "공사")]:
    fetch_all(op, 7, make_handler(bid_type))
print("전체 공고", seen_total, "후보", len(cands), flush=True)

# ---------- 2. 참가가능지역·면허제한 (기간 일괄 조회 후 후보와 결합) ----------
regions, licenses = {}, {}


def collect(store, field):
    def handle(rows):
        for r in rows:
            no = r.get("bidNtceNo")
            if no in cands and r.get(field):
                store.setdefault((no, r.get("bidNtceOrd") or "000"), []).append(
                    {"name": r[field].split("/")[0].strip(), "group": r.get("lmtGrpNo", ""),
                     "allowed": r.get("permsnIndstrytyList", "")})
    return handle


bulk_ok = True
try:
    fetch_all("getBidPblancListInfoPrtcptPsblRgn02", 7, collect(regions, "prtcptPsblRgnNm"))
    fetch_all("getBidPblancListInfoLicenseLimit02", 7, collect(licenses, "lcnsLmtNm"))
except Exception:
    bulk_ok = False
    traceback.print_exc()

for no, it in cands.items():
    key = (no, it["bid_ord"])
    # 정정공고는 제한 정보가 이전 차수에만 등록돼 있을 수 있어 같은 공고번호의 다른 차수도 봅니다.
    def pick(store):
        if key in store:
            return store[key]
        alt = [v for (n, o), v in store.items() if n == no]
        return alt[-1] if alt else []
    it["regions"] = list(dict.fromkeys(r["name"] for r in pick(regions)))
    it["license_rows"] = pick(licenses)
    it["licenses"] = list(dict.fromkeys(r["name"] for r in it["license_rows"]))

# ---------- 3. 업종 미등록 공고의 공고문에서 소기업·소상공인 전용 여부 확인 ----------
from filter_rules import missing_licenses, region_ok


def need_check(it):
    if it["regions"] and not any(region_ok(r) for r in it["regions"]):
        return False
    return bool(missing_licenses(it["licenses"]))


def to_won(it):
    try:
        return float(it["price"] or it["budget"] or 0)
    except ValueError:
        return 0


targets = sorted((it for it in cands.values() if need_check(it)), key=to_won, reverse=True)[:3000]
DEADLINE = T0 + 150 * 60


def check(it):
    if time.time() > DEADLINE:
        return
    files = sorted(it["attachments"], key=lambda f: 0 if "공고" in f["name"] else 1)[:4]
    read, hit = 0, ""
    for f in files:
        try:
            res = u.session.get(f["url"], verify=False, timeout=40)
            res.raise_for_status()
            text = extract_text(f["name"], res.content)
        except Exception:
            continue
        if text.strip():
            read += 1
        hit = small_business_only(text)
        if hit:
            break
    it["sme_only"], it["attach_checked"] = hit, f"{read}/{len(files)}"


with ThreadPoolExecutor(6) as pool:
    list(pool.map(check, targets))
print("공고문 확인", sum("attach_checked" in it for it in cands.values()), "/", len(targets), flush=True)

for it in cands.values():
    it.pop("attachments", None)
LOG.update(seen_total=seen_total, candidates=len(cands), bulk_ok=bulk_ok, seconds=int(time.time() - T0),
           period=[START.strftime("%Y-%m-%d"), END.strftime("%Y-%m-%d %H:%M")])
os.makedirs("out", exist_ok=True)
json.dump({"log": LOG, "items": list(cands.values())}, open("out/backfill.json", "w", encoding="utf-8"), ensure_ascii=False)
print(json.dumps(LOG, ensure_ascii=False)[:3000])
