import os, re, json, requests, urllib3
urllib3.disable_warnings()
s = requests.Session(); s.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36"
os.makedirs("out", exist_ok=True)
L = "https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituList.do"
out = {}
for page in (1, 2, 50, 200):
    d = s.post(L, data={"pageIndex": str(page), "ancmPrg": "ancmEnd"}, verify=False, timeout=30).json()
    rows = d.get("listBsnsAncmBtinSitu") or []
    out[f"end_p{page}"] = {"pag": d.get("paginationInfo"), "rows": [(r.get("ancmId"), r.get("ancmDe"), r.get("rcveEndDe"), r.get("sorgnNm"), (r.get("ancmTl") or "")[:40]) for r in rows]}
# try bigger page size
for key in ("recordCountPerPage", "pageUnit", "pageSize"):
    d = s.post(L, data={"pageIndex": "1", "ancmPrg": "ancmEnd", key: "100"}, verify=False, timeout=30).json()
    out[f"size_{key}"] = len(d.get("listBsnsAncmBtinSitu") or [])
aid = out["end_p1"]["rows"][0][0]
r = s.get(f"https://www.iris.go.kr/contents/retrieveBsnsAncmView.do?ancmId={aid}&ancmPrg=ancmEnd", verify=False, timeout=30)
open("out/detail.html", "w", encoding="utf-8").write(r.text)
idx = [m.start() for m in re.finditer(r"\.hwp|\.pdf|\.zip|download|atchFile|fileDown", r.text)]
out["detail_snips"] = [r.text[max(0, i-300):i+200] for i in idx[:8]]
out["js"] = sorted(set(re.findall(r'src="([^"]+\.js)"', r.text)))
json.dump(out, open("out/iris.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("done")
