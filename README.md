# Auto-monitoring
주간 국책과제 입찰공고 자동 모니터링 시스템

https://engicorix-lab.github.io/Auto-monitoring/

## 구성
| 파일 | 역할 |
|---|---|
| `update_data.py` | IRIS · NTIS · SMTECH · 과기정통부 · 나라장터 · 한국기계연구원 공고를 수집해 `data.json`으로 저장 |
| `filter_rules.py` | 추천 점수 키워드와 가중치 (추천 A/B 기준) |
| `index.html` | `data.json`을 읽어 화면에 표시 |
| `.github/workflows/update.yml` | 2시간마다 수집 후 `data.json` 커밋 |

## 추천 기준
- **추천 A · R&D/지원사업**: IRIS·NTIS·SMTECH 등에서 설비·시설·공정·실증·제조 관련 과제 (우리 회사가 주관/참여기관으로 설비 구축)
- **추천 B · 설비 제작/납품 입찰**: 나라장터·기계연 입찰 중 장비·설비의 설계/제작/설치 공고 (발주 장비를 제작·납품)

공고명에 포함된 키워드의 가중치 합이 `RECOMMEND_THRESHOLD`(기본 4점) 이상이면 추천 탭에 표시됩니다.
키워드를 바꾸려면 `filter_rules.py`만 수정하면 됩니다.

## 설정
나라장터·과기정통부 OpenAPI는 공공데이터포털 인증키가 필요합니다.
저장소 Settings → Secrets and variables → Actions → New repository secret에서 아래 이름으로 등록하세요.

| Secret 이름 | 용도 |
|---|---|
| `G2B_API_KEY` | 조달청 나라장터 입찰공고정보서비스 |
| `MSIT_API_KEY` | 과학기술정보통신부 사업공고 |

Encoding 키, Decoding 키 어느 쪽이든 됩니다. 한 키로 두 API를 모두 활용신청했다면 `DATA_GO_KR_API_KEY` 하나만 등록해도 됩니다.
