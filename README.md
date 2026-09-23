# K뷰티 전방수요 트래커

화장품 전방 수요 지표(수출·아마존 US 침투율·네이버 검색 트렌드)와 관련 뉴스를 매일 자동 수집해
GitHub Pages 정적 대시보드로 보여준다. `macro-econ-tracker`와 같은 구조.

```
raw/        ← 수동 엑셀 (관세청_*.xlsx, 네이버트렌드_*.xlsx, 아마존_*.xlsx, brand_master.xlsx)
scripts/    ← 수집기 (아래 순서로 실행)
data/       ← 결과 JSON (커밋됨, 사이트가 직접 읽음)
index.html  ← 대시보드 (의존성 없음)
.github/workflows/update.yml ← 매일 07:00 KST 수집 → 커밋 → Pages 배포
```

## 로컬 실행 (macOS)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/ingest_excel.py       # raw/ 엑셀 → data/
python scripts/fetch_news.py         # Google News RSS (키 불필요)
python scripts/summarize_news.py     # 기사 원문 추출 → 3줄 요약 (ANTHROPIC_API_KEY 있으면 Claude, 없으면 추출 요약)
python scripts/fetch_companies.py    # ODM 3사·메디컬 에스테틱 3사 실적·컨센서스·주가 (네이버증권, 키 불필요)
python scripts/fetch_industry.py     # UN Comtrade 미·일 화장품 수입 국가별, 한국 수출, 환율 (키 불필요, 첫 실행 백필 10분+)
python scripts/fetch_trade.py        # 관세청 API (DATA_GO_KR_KEY 있을 때만)
python scripts/fetch_naver_trend.py  # 데이터랩 API (NAVER_CLIENT_ID/SECRET 있을 때만)
python scripts/fetch_amazon.py       # Wayback 스냅샷 파싱
python scripts/build_summary.py      # 대시보드용 집계
python3 -m http.server 8000          # http://localhost:8000
```

## 소스별 갱신 방식
| 소스 | 자동 | 수동 폴백 |
|---|---|---|
| 뉴스 | Google News RSS, 키워드는 `scripts/fetch_news.py`의 `KEYWORDS` | – |
| 관세청 수출 (HS 3304, 9018 등) | 공공데이터포털 `DATA_GO_KR_KEY` 시크릿 등록 시 | unipass.customs.go.kr → 수출입실적(품목별) 엑셀 → `raw/관세청_이름.xlsx` |
| 네이버 트렌드 | `NAVER_CLIENT_ID/SECRET` 등록 시 (API 값은 `api:` 접두로 별도 저장) | 데이터랩에서 다운로드 → `raw/네이버트렌드_이름.xlsx` |
| 뉴스 요약 | 원문 추출 후 요약. `ANTHROPIC_API_KEY` 시크릿 등록 시 Claude(`claude-opus-5`, `NEWS_SUMMARY_MODEL`로 변경 가능) 3줄 요약, 미등록 시 리드문·핵심문장 추출 요약 | – |
| 기업 6사 | WiseReport(연 5년+추정 3년, 분기 5+추정 3) → 실패 시 네이버 모바일 API. 과거 기간은 누적 보관 | 종목은 `scripts/fetch_companies.py`의 `COMPANIES` |
| 산업 지표 | UN Comtrade 공개 API: 미국·일본 HS 3304 수입 국가별, 미국 HS 3002.49 수입, 한국 수출 상대국별 / 환율 frankfurter(ECB) | 조합은 `scripts/fetch_industry.py`의 `SERIES` |
| 아마존 Top50 | Wayback Machine 스냅샷 파싱. 미분류 브랜드는 `data/amazon_unmapped.json` → `raw/brand_master.xlsx`에 추가 | – |

## GitHub 설정
1. 저장소 생성 후 push (`main`)
2. Settings → Pages → Source: **GitHub Actions**
3. Settings → Secrets → Actions: `DATA_GO_KR_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `ANTHROPIC_API_KEY` (모두 선택)
4. Actions 탭에서 `update-data` 수동 실행 (workflow_dispatch)
