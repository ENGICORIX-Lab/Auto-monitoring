import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re
import urllib3
import json

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =====================================================================
API_KEY = "yqd2J707PpMlQORvHoa0ZsjNNDqQM3Of%2BOmqs3p9kJXpkcwC2lc%2FzOR6R9MqPf6QyYyp0B0HnmjluOJh%2FBkzHA%3D%3D"
# =====================================================================

CATEGORY_RULES = {
    "AI": ["AI", "인공지능", "딥러닝", "머신러닝", "비전", "알고리즘", "데이터", "빅데이터", "플랫폼", "SW", "자율", "지능형", "제어"],
    "소부장": ["장비", "공정", "반도체", "센서", "배터리", "이차전지", "로봇", "자동화", "검사", "카메라", "모듈", "기구", "설계", "컨베이어", "시제품", "가공", "동력", "프레임", "무인", "방산", "기계", "핸들러"],
    "용역": ["연구", "개발", "R&D", "구축", "실증", "기획", "분석", "표준화", "시험", "용역", "폐기물", "공사", "구매"]
}

def classify_target(title):
    matched_tags = []
    found_cat = None
    for cat, kws in CATEGORY_RULES.items():
        matched = [k for k in kws if k.lower() in title.lower()]
        if matched:
            if not found_cat: found_cat = cat
            matched_tags.extend(matched)
            
    if found_cat:
        return found_cat, list(set(matched_tags))
    return "일반", []

# ----------------- 1. [크롤러] 한국기계연구원 (KIMM) -----------------
def scrape_kimm():
    items = []
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get("https://www.kimm.re.kr/bidding", headers=headers, verify=False, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        for row in soup.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) >= 7:
                title_elem = cols[2].find("a")
                if not title_elem: continue
                title = title_elem.get_text(strip=True)
                link = title_elem.get("href", "")
                if link.startswith("/"): link = "https://www.kimm.re.kr" + link
                
                close_dt = cols[6].get_text(strip=True)
                category, matched = classify_target(title)
                items.append({
                    "org": "한국기계연구원", "category": category,
                    "cat_class": "cat-cons", "title": title,
                    "tags": " ".join([f"#{k}" for k in matched[:3]]) if matched else "#출연연공고", 
                    "budget": "공고문 참조", "close_date": close_dt, 
                    "dday_text": "진행중", "dday_class": "dday-safe", "url": link
                })
    except:
        pass
    return items

# ----------------- 2. [API] 조달청(G2B) -----------------
def fetch_g2b_api():
    items = []
    try:
        KST = timezone(timedelta(hours=9))
        now = datetime.now(KST)
        bgn_dt = (now - timedelta(days=14)).strftime("%Y%m%d0000")
        end_dt = now.strftime("%Y%m%d2359")
        
        endpoints = [
            "getBidPblancListInfoThngPPSSrch",
            "getBidPblancListInfoServcPPSSrch"
        ]
        
        target_orgs = ["생산기술연구원", "로봇산업진흥원", "국방기술품질원", "과학기술", "에너지기술연구원"]
        target_kws = ["컨베이어", "모듈", "검사", "자동화", "장비", "제어", "로봇", "AI", "기구"]
        
        for ep in endpoints:
            url = f"http://apis.data.go.kr/1230000/ad/BidPublicInfoService/{ep}?serviceKey={API_KEY}&numOfRows=500&pageNo=1&inqryDiv=1&inqryBgnDt={bgn_dt}&inqryEndDt={end_dt}&type=json"
            res = requests.get(url, verify=False, timeout=20)
            
            if res.status_code == 200 and not res.text.strip().startswith("<"):
                data = res.json()
                bids = data.get("response", {}).get("body", {}).get("items", [])
                if isinstance(bids, dict): bids = [bids]
                    
                for bid in bids:
                    org_name = bid.get('dminsttNm') or bid.get('ntceInsttNm') or '조달청'
                    title = bid.get('bidNtceNm', '')
                    
                    is_target_org = any(org in org_name for org in target_orgs)
                    is_target_kw = any(kw in title for kw in target_kws)
                    
                    if is_target_org or is_target_kw:
                        close_dt_str = bid.get('bidClseDt', '')
                        close_date_disp = "마감일 미정"
                        dday_label, dday_class = "진행중", "dday-safe"
                        
                        if close_dt_str:
                            date_part = close_dt_str.split(' ')[0]
                            try:
                                close_date = datetime.strptime(date_part, "%Y-%m-%d").date()
                                diff = (close_date - now.date()).days
                                
                                if diff < 0:
                                    continue
                                elif diff == 0:
                                    dday_label, dday_class = "D-Day", "dday-urgent"
                                elif diff <= 7:
                                    dday_label, dday_class = f"D-{diff}", "dday-urgent"
                                else:
                                    dday_label, dday_class = f"D-{diff}", "dday-normal"
                                close_date_disp = close_date.strftime("%Y-%m-%d")
                            except Exception:
                                close_date_disp = date_part
                        
                        category, matched = classify_target(title)
                        
                        items.append({
                            "org": org_name[:12], 
                            "category": category if category != "일반" else "소부장",
                            "cat_class": "cat-rd" if category == "AI" else "cat-cons", 
                            "title": title,
                            "tags": " ".join([f"#{k}" for k in matched[:3]]) if matched else "#조달청(매칭)", 
                            "budget": "공고문 참조", 
                            "close_date": close_date_disp, 
                            "dday_text": dday_label, 
                            "dday_class": dday_class, 
                            "url": bid.get('bidNtceDtlUrl') or bid.get('bidNtceUrl') or 'https://www.g2b.go.kr'
                        })
    except Exception as e:
        pass
    return items

# ----------------- 3. [API] 과학기술정보통신부 사업공고 -----------------
def fetch_msit_api():
    items = []
    try:
        KST = timezone(timedelta(hours=9))
        now = datetime.now(KST)
        
        # 가이드 문서 규격 엔드포인트
        url = f"http://apis.data.go.kr/1721000/msitannouncementinfo/businessAnnouncMentList?serviceKey={API_KEY}&numOfRows=50&pageNo=1&returnType=json"
        res = requests.get(url, verify=False, timeout=15)
        
        if res.status_code == 200 and not res.text.strip().startswith("<"):
            data = res.json()
            raw_items = data.get("response", {}).get("body", {}).get("items", {})
            announcements = raw_items.get("item", []) if isinstance(raw_items, dict) else []
            if isinstance(announcements, dict):
                announcements = [announcements]
                
            for item in announcements:
                title = item.get("subject", "")
                link = item.get("viewUrl", "https://www.msit.go.kr")
                press_dt_str = item.get("pressDt", "")
                dept = item.get("deptName", "과기정통부")
                
                # 등록일 기준 최근 30일 이내 공고만 수집
                if press_dt_str:
                    try:
                        press_date = datetime.strptime(press_dt_str, "%Y-%m-%d").date()
                        if (now.date() - press_date).days > 30:
                            continue
                    except:
                        pass
                
                category, matched = classify_target(title)
                
                items.append({
                    "org": "과기정통부",
                    "category": category if category != "일반" else "용역",
                    "cat_class": "cat-rd" if category == "AI" else "cat-bid",
                    "title": title,
                    "tags": " ".join([f"#{k}" for k in matched[:3]]) if matched else f"#{dept}",
                    "budget": "공고문 참조",
                    "close_date": f"게시일: {press_dt_str}",
                    "dday_text": "접수중",
                    "dday_class": "dday-safe",
                    "url": link
                })
    except Exception as e:
        pass
    return items

def update_html():
    # 3개 소스 통합
    bids = scrape_kimm() + fetch_g2b_api() + fetch_msit_api()
    
    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()

    KST = timezone(timedelta(hours=9))
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    
    html = re.sub(r'(<div[^>]*id="metaSync"[^>]*>).*?(</div>)', rf'\1<strong>최근 동기화:</strong> {now_str} (기계연+조달청+과기정통부 통합)\2', html, flags=re.DOTALL)

    if bids:
        rows_html = ""
        for b in bids:
            rows_html += f"""
        <tr>
          <td><span class="badge-org">{b['org']}</span></td>
          <td class="title-cell"><a href="{b['url']}" target="_blank">{b['title']}</a><br><small>{b['tags']}</small></td>
          <td><strong>{b['budget']}</strong></td>
          <td><span class="{b['dday_class']}">{b['dday_text']}</span><br><small style="color:#64748b;">{b['close_date']}</small></td>
          <td><a href="{b['url']}" target="_blank">공고문 ↗</a></td>
        </tr>"""
        
        html = re.sub(r'<tbody>.*?</tbody>', f'<tbody>\n{rows_html}\n      </tbody>', html, flags=re.DOTALL)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

if __name__ == "__main__":
    update_html()
