"""
주식 백테스트 - Flask 버전 (내 PC에서 실행)

실행:  python app.py  → http://localhost:5000
"""
import os
import tempfile
import threading
import uuid
from datetime import date, timedelta

from flask import Flask, abort, jsonify, render_template, request, send_file

import backtest_core as core

app = Flask(__name__)
PLOT_DIR = os.path.join(tempfile.gettempdir(), "bt_plots")
os.makedirs(PLOT_DIR, exist_ok=True)

_listing = None
_lock = threading.Lock()


def listing():
    global _listing
    with _lock:
        if _listing is None:
            _listing = core.load_listing()
        return _listing


@app.get("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    try:
        df = listing()
    except Exception as e:  # noqa: BLE001
        if q.isdigit() and len(q) == 6:  # 목록이 없어도 종목코드는 그대로 사용
            return jsonify([{"Code": q, "Name": q, "Market": "코드 직접 입력"}])
        return jsonify({"error": f"종목 목록을 불러오지 못했습니다. 6자리 종목코드로 입력해 보세요. ({e})"}), 502
    return jsonify(core.search_listing(df, q).to_dict("records"))


@app.post("/api/backtest")
def api_backtest():
    body = request.get_json(force=True) or {}
    code = str(body.get("code", "")).strip()
    strategy = body.get("strategy", "split")
    if not code:
        return jsonify({"error": "종목을 선택하세요."}), 400
    if strategy not in core.STRATEGIES:
        return jsonify({"error": "알 수 없는 전략입니다."}), 400
    start = body.get("start") or (date.today() - timedelta(days=365 * 3)).isoformat()
    end = body.get("end") or date.today().isoformat()

    try:
        df = core.load_prices(code, start, end)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"시세를 불러오지 못했습니다: {e}"}), 502

    plot_id = uuid.uuid4().hex
    try:
        res = core.run_backtest(
            df, strategy,
            cash=float(body.get("cash") or 10_000_000),
            commission_pct=float(body.get("commission") or 0),
            params=body.get("params") or {},
            plot_path=os.path.join(PLOT_DIR, plot_id + ".html"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"백테스트 실행 오류: {e}"}), 500

    c = res["curve"]
    t = res["trades"]
    return jsonify({
        "code": code,
        "bars": len(df),
        "summary": res["summary"],
        "curve": {
            "dates": [d.strftime("%Y-%m-%d") for d in c.index],
            "equity": [round(float(x)) for x in c["Equity"]],
            "buyhold": [round(float(x)) for x in c["BuyHold"]],
        },
        "trades": [
            {
                "entryTime": r["매수일"], "exitTime": r["매도일"], "size": int(r["수량"]),
                "entryPrice": core.to_plain(r["매수가"]), "exitPrice": core.to_plain(r["매도가"]),
                "pnl": core.to_plain(r["손익(원)"]), "returnPct": core.to_plain(r["수익률(%)"]),
            }
            for _, r in t.iterrows()
        ],
        "plotUrl": f"/plot/{plot_id}" if res["plot"] else None,
    })


@app.get("/plot/<plot_id>")
def plot(plot_id):
    path = os.path.join(PLOT_DIR, plot_id + ".html")
    if not plot_id.isalnum() or not os.path.exists(path):
        abort(404)
    return send_file(path)


@app.get("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)))
