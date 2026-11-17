import os, sys, json
from datetime import datetime, timedelta
sys.path.insert(0, ".")
import update_data as u
now = datetime.utcnow() + timedelta(hours=9)
out = {}
def t(label, bgn, end, op="getBidPblancListInfoThngPPSSrch02"):
    try:
        rows, total = u.g2b_items(op, {"numOfRows": 5, "pageNo": 1, "inqryDiv": 1, "inqryBgnDt": bgn, "inqryEndDt": end})
        out[label] = [bgn, end, total, len(rows), (rows[0].get("bidNtceDt") if rows else None)]
    except Exception as e:
        out[label] = [bgn, end, "ERR " + str(e)[:300]]
f = "%Y%m%d%H%M"
for days in (1, 3, 7, 8, 10, 14, 15, 30):
    t(f"{days}d end2359", (now - timedelta(days=days)).strftime("%Y%m%d0000"), now.strftime("%Y%m%d2359"))
t("14d end now", (now - timedelta(days=14)).strftime("%Y%m%d0000"), now.strftime(f))
t("7d end yesterday", (now - timedelta(days=8)).strftime("%Y%m%d0000"), (now - timedelta(days=1)).strftime("%Y%m%d2359"))
t("servc 14d", (now - timedelta(days=14)).strftime("%Y%m%d0000"), now.strftime("%Y%m%d2359"), "getBidPblancListInfoServcPPSSrch02")
os.makedirs("out", exist_ok=True)
json.dump(out, open("out/g2b02.json", "w"), ensure_ascii=False, indent=1)
