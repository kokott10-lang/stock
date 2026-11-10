"""
주식 백테스트 - Streamlit 버전 (Streamlit Community Cloud 배포용)

로컬 실행:  streamlit run streamlit_app.py
"""
import os
import tempfile
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import backtest_core as core

try:
    from streamlit_searchbox import st_searchbox
except ImportError:  # requirements.txt 의 streamlit-searchbox 가 없을 때
    st_searchbox = None

st.set_page_config(page_title="주식 백테스트", page_icon="📈", layout="wide")


@st.cache_data(ttl=60 * 60 * 24, show_spinner="종목 목록 불러오는 중…")
def get_listing():
    return core.load_listing()


@st.cache_data(ttl=60 * 60, show_spinner="시세 불러오는 중…")
def get_prices(code, start, end):
    return core.load_prices(code, start, end)


def trade_markers(curve: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """거래 내역 → 자산 곡선 위에 찍을 매수/매도 점 (같은 날 여러 건은 하나로 합침)"""
    rows = []
    for _, t in trades.iterrows():
        rows.append(("매수", t["매수일"], t["매수가"], t["수량"], None))
        if isinstance(t["매도일"], str):
            rows.append(("매도", t["매도일"], t["매도가"], t["수량"], t["수익률(%)"]))
    if not rows:
        return pd.DataFrame(columns=["날짜", "구분", "자산", "가격", "수량", "수익률(%)"])
    m = pd.DataFrame(rows, columns=["구분", "날짜", "가격", "수량", "수익률(%)"])
    m["날짜"] = pd.to_datetime(m["날짜"])
    m = (m.groupby(["날짜", "구분"], as_index=False)
          .agg(가격=("가격", "mean"), 수량=("수량", "sum"), **{"수익률(%)": ("수익률(%)", "mean")}))
    eq = curve["Equity"]
    m["자산"] = eq.reindex(m["날짜"], method="ffill").values
    return m


def crisis_frame(curve: pd.DataFrame) -> pd.DataFrame:
    """차트 기간과 겹치는 급락 구간 (고점~저점)"""
    lo, hi = curve.index.min(), curve.index.max()
    rows = []
    for name_, peak, trough, *_ in core.CRISES:
        a, b = pd.Timestamp(peak), pd.Timestamp(trough)
        if b >= lo and a <= hi:
            rows.append({"위기": name_, "시작": max(a, lo), "끝": min(b, hi),
                         "기간": f"{peak} ~ {trough}"})
    return pd.DataFrame(rows)


def crisis_layer(curve: pd.DataFrame):
    cf = crisis_frame(curve)
    if cf.empty:
        return None
    return alt.Chart(cf).mark_rect(color="#e8590c", opacity=0.12).encode(
        x="시작:T", x2="끝:T", tooltip=["위기:N", alt.Tooltip("기간:N", title="고점 ~ 저점")])


def equity_chart(curve: pd.DataFrame, trades: pd.DataFrame, log: bool = False,
                 crises: bool = True) -> alt.Chart:
    line_df = (curve[["Equity", "BuyHold"]]
               .rename(columns={"Equity": "전략 자산", "BuyHold": "단순 보유"})
               .rename_axis("날짜").reset_index()
               .melt("날짜", var_name="구분", value_name="자산"))
    lines = alt.Chart(line_df).mark_line(strokeWidth=1.8).encode(
        x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%Y-%m")),
        y=alt.Y("자산:Q", title="자산(원)", axis=alt.Axis(format=",.0f"),
                scale=alt.Scale(type="log", nice=False) if log else alt.Scale(zero=False)),
        color=alt.Color("구분:N", title=None,
                        scale=alt.Scale(domain=["전략 자산", "단순 보유"], range=["#2f6fed", "#9aa3ad"]),
                        legend=alt.Legend(orient="top")),
        strokeDash=alt.StrokeDash("구분:N", legend=None,
                                  scale=alt.Scale(domain=["전략 자산", "단순 보유"], range=[[1, 0], [4, 3]])),
        tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "구분:N",
                 alt.Tooltip("자산:Q", format=",.0f")],
    )
    band = crisis_layer(curve) if crises else None
    if band is not None:
        lines = alt.layer(band, lines)
    m = trade_markers(curve, trades)
    if m.empty:
        return lines.properties(height=380)
    pts = alt.Chart(m).mark_point(filled=True, size=110, opacity=0.95, stroke="white", strokeWidth=0.8).encode(
        x="날짜:T",
        y="자산:Q",
        shape=alt.Shape("구분:N", scale=alt.Scale(domain=["매수", "매도"],
                                                  range=["triangle-up", "triangle-down"]), legend=None),
        fill=alt.Fill("구분:N", scale=alt.Scale(domain=["매수", "매도"], range=["#d63b3b", "#2463d1"]),
                      legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "구분:N",
                 alt.Tooltip("가격:Q", format=",.0f", title="가격(원)"), alt.Tooltip("수량:Q", title="수량(주)"),
                 alt.Tooltip("수익률(%):Q", format="+.2f")],
    )
    return alt.layer(lines, pts).resolve_scale(color="independent").properties(height=380).interactive(bind_y=False)


def fmt_pct(v):
    return "-" if v is None else f"{v:+.2f}%"


# ---------------------------------------------------------------------------
# 사이드바: 설정
# ---------------------------------------------------------------------------
st.sidebar.header("설정")

listing, listing_errs = None, []
try:
    listing, listing_errs = get_listing()
except Exception as e:  # noqa: BLE001
    listing_errs = [str(e)]

EMPTY = pd.DataFrame(columns=["Code", "Name", "Market", "_key"])


def search_options(term: str):
    """입력할 때마다 호출되는 자동완성 후보: (보여줄 이름, (코드, 이름))"""
    if not term or not term.strip():
        return []
    hits = core.search_listing(listing if listing is not None else EMPTY, term, limit=15)
    return [(f"{r.Name}  ·  {r.Code}  ·  {r.Market}", (r.Code, r.Name)) for r in hits.itertuples()]


code, name = None, None
with st.sidebar:
    if st_searchbox is not None:
        picked = st_searchbox(
            search_options,
            label="종목 · ETF 검색",
            placeholder="예: 현대차, 나스닥100, S&P500, 코스피 지수, QQQ",
            key="stock_search",
            debounce=200,
        )
    else:  # 부품이 없으면 일반 입력창 + 목록으로 대체
        q = st.text_input("종목 · ETF 검색", placeholder="예: 현대차, 나스닥100, S&P500, 코스피 지수, QQQ")
        opts = search_options(q)
        picked = None
        if opts:
            i = st.selectbox("검색 결과", range(len(opts)), format_func=lambda k: opts[k][0])
            picked = opts[i][1]
        elif q:
            st.caption("검색 결과가 없습니다.")
    if picked:
        code, name = picked
        st.success(f"선택: {name} ({code})")
    if listing_errs:
        st.caption("⚠️ 불러오지 못한 목록: " + ", ".join(listing_errs)
                   + " — 6자리 종목코드나 미국 티커를 직접 입력해도 됩니다.")

today = date.today()
MIN_DATE = date(1990, 1, 1)

# 기간 바로가기: 고르면 시작일/종료일이 자동으로 바뀜
PRESETS = {
    "최근 3년": (today - timedelta(days=365 * 3), today),
    "최근 5년": (today - timedelta(days=365 * 5), today),
    "최근 10년": (today - timedelta(days=365 * 10), today),
    "2000년부터": (date(2000, 1, 1), today),
    "가능한 전체 (1990년~)": (MIN_DATE, today),
}
for name_, _peak, _trough, s_, e_ in core.CRISES:
    PRESETS[f"📉 {name_} ({_peak[:4]})"] = (date.fromisoformat(s_), min(date.fromisoformat(e_), today))

if "start_date" not in st.session_state:
    st.session_state["start_date"], st.session_state["end_date"] = PRESETS["최근 3년"]


def apply_preset():
    key = st.session_state.get("preset")
    if key in PRESETS:
        st.session_state["start_date"], st.session_state["end_date"] = PRESETS[key]


st.sidebar.selectbox("기간 바로가기", ["직접 선택"] + list(PRESETS), key="preset", on_change=apply_preset,
                     help="위기 구간을 고르면 위기 전부터 회복기까지 기간이 자동으로 들어갑니다.")
start = st.sidebar.date_input("시작일", key="start_date", min_value=MIN_DATE, max_value=today)
end = st.sidebar.date_input("종료일", key="end_date", min_value=MIN_DATE, max_value=today)
c1, c2 = st.sidebar.columns(2)
cash = c1.number_input("초기 자금(원)", min_value=100_000, value=10_000_000, step=1_000_000)
commission = c2.number_input("거래비용(편도 %)", min_value=0.0, value=0.1, step=0.01, format="%.2f")

skey = st.sidebar.selectbox("전략", list(core.STRATEGIES), format_func=lambda k: core.STRATEGIES[k]["name"])
info = core.STRATEGIES[skey]
st.sidebar.caption(info["desc"])
params = {}
cols = st.sidebar.columns(2)
for j, (k, label, default) in enumerate(info["params"]):
    params[k] = cols[j % 2].number_input(label, value=float(default), format="%g", key=f"{skey}_{k}")

run = st.sidebar.button("백테스트 실행", type="primary", use_container_width=True, disabled=code is None)

# ---------------------------------------------------------------------------
# 본문
# ---------------------------------------------------------------------------
st.title("📈 주식 백테스트")
st.caption("FinanceDataReader 시세 + backtesting.py 엔진 · 과거 성과가 미래 수익을 보장하지 않습니다.")

if run:
    if start >= end:
        st.error("시작일이 종료일보다 앞이어야 합니다.")
    else:
        try:
            df = get_prices(code, start.isoformat(), end.isoformat())
            plot_path = os.path.join(tempfile.gettempdir(), f"bt_{code}_{skey}.html")
            res = core.run_backtest(df, skey, float(cash), float(commission), params, plot_path,
                                    fractional=True if code.upper() in core.INDICES else None)
            plot_html = None
            if res["plot"]:
                with open(res["plot"], encoding="utf-8") as f:
                    plot_html = f.read()
            st.session_state["result"] = dict(res=res, name=name, code=code, strategy=info["name"],
                                              bars=len(df), plot_html=plot_html, note=core.price_note(code))
        except Exception as e:  # noqa: BLE001
            st.error(f"실행 실패: {e}")

r = st.session_state.get("result")
if not r:
    st.info("왼쪽에서 종목이나 ETF를 검색해 선택하고 **백테스트 실행**을 누르세요. (모바일은 왼쪽 위 ☰ 메뉴)")
    st.stop()

s = r["res"]["summary"]
st.subheader(f"{r['name']} ({r['code']}) · {r['strategy']}")
st.caption(f"{s.get('Start')} ~ {s.get('End')} · {r['bars']}거래일")
if r.get("note"):
    st.info(r["note"])

k = st.columns(4)
k[0].metric("전략 수익률", fmt_pct(s.get("Return [%]")))
k[1].metric("단순 보유 수익률", fmt_pct(s.get("Buy & Hold Return [%]")))
k[2].metric("연환산 수익률", fmt_pct(s.get("Return (Ann.) [%]")))
k[3].metric("최대 낙폭 (MDD)", fmt_pct(s.get("Max. Drawdown [%]")))
k = st.columns(4)
k[0].metric("최종 자산(원)", f"{s.get('Equity Final [$]') or 0:,.0f}")
k[1].metric("거래 횟수", f"{int(s.get('# Trades') or 0)}")
wr, sh = s.get("Win Rate [%]"), s.get("Sharpe Ratio")
k[2].metric("승률", "-" if wr is None else f"{wr:.1f}%")
k[3].metric("샤프 지수", "-" if sh is None else f"{sh:.2f}")

trades = r["res"]["trades"]

st.markdown("#### 자산 곡선")
t1_, t2_ = st.columns(2)
log_scale = t1_.toggle("로그 스케일 (기간이 길 때 비교하기 쉬움)", value=r["bars"] > 252 * 5)
show_crises = t2_.toggle("급락 구간 표시", value=True)
st.altair_chart(equity_chart(r["res"]["curve"], trades, log=log_scale, crises=show_crises),
                use_container_width=True)
st.caption("▲ 빨강 = 매수, ▼ 파랑 = 매도 · 주황 음영 = 역사적 급락 구간(고점~저점, 대략) · "
           "점이나 음영에 마우스를 올리면 내용이 보입니다.")

cf = crisis_frame(r["res"]["curve"])
if show_crises and not cf.empty:
    eqc = r["res"]["curve"]
    rows = []
    for _, c in cf.iterrows():
        seg = eqc.loc[c["시작"]:c["끝"]]
        if len(seg) < 2:
            continue
        after = eqc.loc[c["시작"]:]
        row = {"위기": c["위기"], "기간": c["기간"]}
        for col, who in (("Equity", "전략"), ("BuyHold", "단순 보유")):
            start_v = seg[col].iloc[0]
            row[f"{who} 낙폭(%)"] = (seg[col].min() / start_v - 1) * 100
            rec = after[after[col] >= start_v]
            rec = rec[rec.index > seg[col].idxmin()]
            row[f"{who} 회복"] = rec.index[0].strftime("%Y-%m") if not rec.empty else "미회복"
        rows.append(row)
    if rows:
        with st.expander(f"급락 구간별 성과 ({len(rows)}개)", expanded=True):
            st.caption("낙폭 = 급락 시작일 대비 그 구간 최저 자산. 회복 = 급락 시작일 자산을 다시 넘은 달.")
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                         column_config={c: st.column_config.NumberColumn(format="%.1f")
                                        for c in ["전략 낙폭(%)", "단순 보유 낙폭(%)"]})

st.markdown(f"#### 거래 내역 ({len(trades)}건)")
st.dataframe(
    trades,
    hide_index=True,
    use_container_width=True,
    column_config={
        "매수가": st.column_config.NumberColumn(format="%,d"),
        "매도가": st.column_config.NumberColumn(format="%,d"),
        "손익(원)": st.column_config.NumberColumn(format="%,d"),
        "수익률(%)": st.column_config.NumberColumn(format="%.2f"),
    },
)

# ---------------------------------------------------------------------------
# 투자기간 분석
# ---------------------------------------------------------------------------
st.markdown("#### 투자기간 분석")
curve_all = r["res"]["curve"]
COLORS = alt.Scale(domain=["전략", "단순 보유"], range=["#2f6fed", "#9aa3ad"])
tab1, tab2, tab3 = st.tabs(["보유기간별", "연도별", "매수 시점별"])

with tab1:
    ht = core.horizon_table(curve_all)
    if ht.empty:
        st.info("기간이 너무 짧아 분석할 수 없습니다. 시작일을 앞당겨 보세요.")
    else:
        st.caption("기간 안의 **모든 날짜에 샀다고 가정**하고, 정해진 기간만큼 보유했을 때의 수익률 분포입니다. "
                   "막대는 최저~최고, 점은 중앙값입니다.")
        order = [h for h, _ in core.HORIZONS if h in set(ht["보유기간"])]
        base = alt.Chart(ht).encode(
            x=alt.X("보유기간:N", sort=order, title=None, axis=alt.Axis(labelAngle=0)),
            xOffset=alt.XOffset("구분:N", sort=["전략", "단순 보유"]),
            color=alt.Color("구분:N", scale=COLORS, legend=alt.Legend(orient="top", title=None)),
        )
        tips = ["보유기간", "구분", alt.Tooltip("중앙값(%):Q", format="+.1f"),
                alt.Tooltip("최저(%):Q", format="+.1f"), alt.Tooltip("최고(%):Q", format="+.1f"),
                alt.Tooltip("플러스 확률(%):Q", format=".0f")]
        rng = base.mark_rule(strokeWidth=6, opacity=0.35).encode(
            y=alt.Y("최저(%):Q", title="수익률(%)"), y2="최고(%):Q", tooltip=tips)
        med = base.mark_point(filled=True, size=90).encode(y="중앙값(%):Q", tooltip=tips)
        zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#999", strokeDash=[3, 3]).encode(y=alt.Y("y:Q", title="수익률(%)"))
        st.altair_chart((rng + med + zero).properties(height=340), use_container_width=True)

        if not any(h in order for h in ("3년", "5년")):
            st.caption("💡 3년·5년 보유 분석을 보려면 시작일을 더 앞으로 당겨 보세요.")
        st.dataframe(
            ht, hide_index=True, use_container_width=True,
            column_config={c: st.column_config.NumberColumn(format="%.1f")
                           for c in ht.columns if c.endswith("(%)")},
        )

with tab2:
    yt = core.yearly_table(curve_all)
    st.caption("달력 기준 연도별 수익률입니다. 첫해와 마지막 해는 데이터가 있는 기간만 계산합니다.")
    ylong = yt.melt("연도", value_vars=["전략", "단순 보유"], var_name="구분", value_name="수익률(%)")
    bars = alt.Chart(ylong).mark_bar().encode(
        x=alt.X("연도:O", title=None, axis=alt.Axis(labelAngle=0)),
        xOffset=alt.XOffset("구분:N", sort=["전략", "단순 보유"]),
        y=alt.Y("수익률(%):Q"),
        color=alt.Color("구분:N", scale=COLORS, legend=alt.Legend(orient="top", title=None)),
        tooltip=["연도:O", "구분:N", alt.Tooltip("수익률(%):Q", format="+.2f")],
    )
    st.altair_chart(bars.properties(height=300), use_container_width=True)
    st.dataframe(yt, hide_index=True, use_container_width=True,
                 column_config={c: st.column_config.NumberColumn(format="%+.2f") for c in ["전략", "단순 보유", "차이(%p)"]})

with tab3:
    avail = [(h, d) for h, d in core.HORIZONS if len(curve_all) >= d + core.MIN_SAMPLES]
    if not avail:
        st.info("기간이 너무 짧아 분석할 수 없습니다.")
    else:
        labels = [h for h, _ in avail]
        h = st.selectbox("보유기간", labels, index=labels.index("1년") if "1년" in labels else len(labels) - 1)
        days = dict(avail)[h]
        rf = core.rolling_frame(curve_all, days)
        st.caption(f"x축 날짜에 사서 **{h}** 보유했다면 얻었을 수익률입니다. 0선 아래는 손실 구간입니다.")
        long = rf.rename_axis("매수일").reset_index().melt("매수일", var_name="구분", value_name="수익률(%)")
        line = alt.Chart(long).mark_line(strokeWidth=1.6).encode(
            x=alt.X("매수일:T", title="매수일", axis=alt.Axis(format="%Y-%m")),
            y=alt.Y("수익률(%):Q", title="수익률(%)"),
            color=alt.Color("구분:N", scale=COLORS, legend=alt.Legend(orient="top", title=None)),
            tooltip=[alt.Tooltip("매수일:T", format="%Y-%m-%d"), "구분:N",
                     alt.Tooltip("수익률(%):Q", format="+.2f")],
        )
        zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#999", strokeDash=[3, 3]).encode(y=alt.Y("y:Q", title="수익률(%)"))
        st.altair_chart((line + zero).properties(height=320).interactive(bind_y=False), use_container_width=True)
        c = st.columns(3)
        c[0].metric(f"전략 {h} 플러스 확률", f"{(rf['전략'] > 0).mean() * 100:.0f}%")
        c[1].metric(f"단순 보유 {h} 플러스 확률", f"{(rf['단순 보유'] > 0).mean() * 100:.0f}%")
        c[2].metric("전략이 더 나았던 비율", f"{(rf['전략'] > rf['단순 보유']).mean() * 100:.0f}%")

st.caption("※ 전략의 기간별 수익률은 전체 백테스트 자산 곡선에서 잘라 계산한 값이라, 그 날짜에 전략을 새로 시작한 결과와는 조금 다를 수 있습니다.")

if r["plot_html"]:
    with st.expander("backtesting.py 상세 차트 (캔들 · 매매 시점 · 지표)"):
        components.html(r["plot_html"], height=820, scrolling=True)
