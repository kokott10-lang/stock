# 📈 주식 백테스트

국내 주식·ETF와 미국 ETF를 **종목 검색 → 전략 선택 → 과거 성과 확인**까지 웹 화면에서 할 수 있는 백테스트 도구입니다.

- 시세: [FinanceDataReader](https://github.com/FinanceData/FinanceDataReader) (종목 목록 실패 시 [pykrx](https://github.com/sharebook-kr/pykrx))
- 엔진: [backtesting.py](https://github.com/kernc/backtesting.py)
- 화면: Streamlit(온라인 배포용) / Flask(내 PC용)

## 🌐 온라인으로 쓰기 (Streamlit Community Cloud, 무료)

GitHub Pages는 파이썬을 실행할 수 없어서, 이 저장소를 Streamlit Cloud에 연결해 실행합니다.

1. <https://share.streamlit.io> 접속 → **GitHub 계정으로 로그인**
2. **Create app** → **Deploy a public app from GitHub**
3. 입력
   - Repository: `kokott10-lang/stock`
   - Branch: `main`
   - Main file path: `streamlit_app.py`
   - (선택) App URL: 원하는 주소, 예) `kokott10-stock`
4. **Deploy** → 1~3분 후 `https://<주소>.streamlit.app` 에서 실행 (휴대폰에서도 접속 가능)

이후 이 저장소에 push 하면 앱이 자동으로 갱신됩니다.
무료 앱은 한동안 접속이 없으면 잠들고, 다시 열 때 깨우는 데 30초~1분 정도 걸립니다.

## 💻 내 PC에서 쓰기

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

- Streamlit 버전: `streamlit run streamlit_app.py` → 브라우저가 자동으로 열림
- Flask 버전: `python app.py` → <http://localhost:5000> 접속

> `templates/index.html` 을 더블클릭해서 열면 동작하지 않습니다. 반드시 위 명령으로 서버를 띄운 뒤 주소로 접속하세요.

## 기능

| 전략 | 내용 |
|---|---|
| 분할 익절 + 추적 손절 | 시작일 매수 → +1차% 일부, +2차% 일부 익절 → 1차 익절 후 고점 대비 -추적% 전량 매도, 진입가 대비 -손절% 전량 매도. 재진입 옵션: 청산 후 골든크로스 시 재매수 |
| 이동평균 골든/데드크로스 | 단기 이평이 장기 이평을 상향 돌파 시 매수, 하향 돌파 시 매도 |
| RSI 역추세 | RSI가 과매도선을 위로 넘으면 매수, 과매수선 이상이면 매도 |
| 매수 후 보유 | 비교 기준선 |

결과: 수익률 · 단순 보유 대비 · 연환산 · MDD · 승률 · 샤프 지수, 자산 곡선, 거래 내역, backtesting.py 상세 차트(캔들·매매 시점·지표)

### 검색 가능한 종목

| 구분 | 범위 | 예시 검색어 |
|---|---|---|
| 국내 주식 | KOSPI·KOSDAQ 전 종목 | `현대차`, `005380` |
| 국내 ETF | 상장 ETF 전체 (네이버 목록) | `코스피200` → KODEX 200, `나스닥100` → TIGER 미국나스닥100, `S&P500` |
| 미국 ETF | 주요 40여 개 (`backtest_core.py` 의 `US_ETFS`) | `QQQ`, `SPY`, `나스닥100`, `반도체` |

- `코스피200`, `나스닥100`, `S&P500`, `sp500` 처럼 띄어쓰기·기호가 달라도 찾아지도록 별칭(`ALIASES`)을 붙였습니다.
- 목록에 없는 종목도 **6자리 종목코드** 또는 **미국 티커**(예: `TSLA`)를 입력하면 바로 백테스트할 수 있습니다.
- 미국 종목은 달러 가격을 **일별 원/달러 환율로 원화 환산**해 계산합니다. 그래서 수익률에 환율 변동이 포함되고, 초기 자금도 원화 기준입니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `backtest_core.py` | 데이터 로딩, 지표, 전략, 백테스트 실행 (공용) |
| `streamlit_app.py` | Streamlit 화면 |
| `app.py`, `templates/index.html` | Flask 화면 |

## 새 전략 추가

1. `backtest_core.py` 에 `Strategy` 를 상속한 클래스 작성 (조절할 값은 클래스 변수로)
2. 같은 파일의 `STRATEGIES` 에 이름·설명·파라미터와 함께 등록 → Streamlit 화면에 자동 반영
3. Flask 화면도 쓰려면 `templates/index.html` 의 `STRATS` 와 `<select id="strategy">` 에 추가

## 주의

- 거래비용은 편도 기준(기본 0.1%, 왕복 약 0.2% — 매도 거래세 포함 근사치)
- 주문은 신호 다음 날 시가에 체결(backtesting.py 기본 동작)
- 배당 미반영 등으로 실제 수익과 차이가 날 수 있으며, 과최적화에 주의하세요.
- 학습·연구용이며 투자 권유가 아닙니다.
