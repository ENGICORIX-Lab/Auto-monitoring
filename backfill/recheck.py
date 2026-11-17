"""공고문을 확인하지 못한 공고만 첨부파일정보조회로 다시 받아 소기업·소상공인 전용 여부를 확인합니다."""
import os, sys, json, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import update_data as u
from attachments import extract_text
from filter_rules import small_business_only, bid_conditions

T0 = time.time()
targets = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "recheck_targets.json")))


def check(t):
    no, ord_ = t
    out = {"files": 0, "read": 0, "sme_only": "", "error": "", "conditions": []}
    conds = {}
    try:
        rows = []
        for i in range(3):
            try:
                rows, _ = u.g2b_items("getBidPblancListInfoAtchFileInfo02", {
                    "numOfRows": 30, "pageNo": 1, "inqryDiv": 2, "bidNtceNo": no, "bidNtceOrd": ord_})
                break
            except Exception as e:
                out["error"] = str(e)[:120]
                time.sleep(3 * (i + 1))
        files = [(r.get("atchFileNm") or "", r["atchFileUrl"]) for r in rows if r.get("atchFileUrl")]
        files.sort(key=lambda f: 0 if "공고" in f[0] else 1)
        out["files"] = len(files)
        for name, url in files[:5]:
            try:
                res = u.session.get(url, verify=False, timeout=40)
                res.raise_for_status()
                text = extract_text(name, res.content)
            except Exception:
                continue
            if text.strip():
                out["read"] += 1
            for c in bid_conditions(text):
                conds.setdefault(c["t"], c)
            hit = small_business_only(text)
            if hit:
                out["sme_only"] = hit
                break
            if conds and "공고" in name:
                break
    except Exception as e:
        out["error"] = str(e)[:120]
    out["conditions"] = list(conds.values())
    return no, out


with ThreadPoolExecutor(8) as pool:
    result = dict(pool.map(check, targets))
os.makedirs("out", exist_ok=True)
json.dump(result, open("out/backfill.json", "w", encoding="utf-8"), ensure_ascii=False)
print("recheck", len(result), "read", sum(v["read"] > 0 for v in result.values()), "sme", sum(bool(v["sme_only"]) for v in result.values()),
      "nofiles", sum(v["files"] == 0 for v in result.values()), "errors", sum(bool(v["error"]) for v in result.values()), int(time.time() - T0), "s")
