"""
EliteX Traders — WinGO Backend
Logic 1: Follow last result (BIG -> BIG, SMALL -> SMALL)
Logic 2: Reverse after 2 consecutive Logic 1 losses (zigzag detection)
"""

import time
import threading
import requests
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ─── CONFIG ───────────────────────────────────────────────
URL_CURRENT_30S = "https://draw.ar-lottery01.com/WinGo/WinGo_30S.json"
URL_HISTORY_30S = "https://draw.ar-lottery01.com/WinGo/WinGo_30S/GetHistoryIssuePage.json"
URL_CURRENT_1M  = "https://draw.ar-lottery01.com/WinGo/WinGo_1M.json"
URL_HISTORY_1M  = "https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json"

HEADERS = {
    "User-Agent":     "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
    "Referer":        "https://dkwin.club/",
    "Origin":         "https://dkwin.club",
    "Accept":         "application/json, text/plain, */*",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
}

POLL_SEC = 2
PORT     = 5055

# ─── STATE (per market) ───────────────────────────────────
def make_state():
    return {
        "period":     "Loading...",
        "prediction": "WAIT",
        "logic":      "Initializing...",
        "logic_num":  1,
        "countdown":  30,
        "mode":       "",
        "win_result": None,
    }

markets = {
    "30s": {
        "state":          make_state(),
        "history":        [],
        "pred_history":   [],
        "last_period":    None,
        "logic_mode":     1,
        "l1_loss_streak": 0,
        "last_predicted": None,
        "url_current":    URL_CURRENT_30S,
        "url_history":    URL_HISTORY_30S,
        "mode_label":     "WinGo 30sec",
    },
    "1m": {
        "state":          make_state(),
        "history":        [],
        "pred_history":   [],
        "last_period":    None,
        "logic_mode":     1,
        "l1_loss_streak": 0,
        "last_predicted": None,
        "url_current":    URL_CURRENT_1M,
        "url_history":    URL_HISTORY_1M,
        "mode_label":     "WinGo 1Min",
    },
}


# ─── HELPERS ──────────────────────────────────────────────
def get_ts():
    return int(time.time() * 1000)


def number_to_bigsmall(number):
    return "BIG" if number >= 5 else "SMALL"


# ─── FETCH CURRENT GAME ───────────────────────────────────
def fetch_current(url):
    try:
        r = requests.get(url, params={"ts": get_ts()}, headers=HEADERS, timeout=8)
        r.raise_for_status()
        data = r.json()
        current   = data.get("current", {})
        period    = current.get("issueNumber", "")
        end_ts    = current.get("endTime", 0)
        now_ms    = int(time.time() * 1000)
        remaining = max(0, int((end_ts - now_ms) / 1000))
        return {"period": period, "remaining": remaining}
    except Exception as e:
        print(f"[fetch_current] {e}")
        return None


# ─── FETCH HISTORY ────────────────────────────────────────
def fetch_history(url):
    try:
        r = requests.get(url, params={"ts": get_ts()}, headers=HEADERS, timeout=8)
        r.raise_for_status()
        data     = r.json()
        raw_list = data.get("data", {}).get("list", [])
        results  = []
        for item in raw_list:
            try:
                num      = int(item.get("number", 0))
                period   = item.get("issueNumber", "")
                bigsmall = number_to_bigsmall(num)
                results.append({"period": period, "number": num, "big_small": bigsmall})
            except Exception:
                continue
        return results
    except Exception as e:
        print(f"[fetch_history] {e}")
        return []


# ─── PREDICTION ───────────────────────────────────────────
def make_prediction(logic_mode, raw_history):
    """
    Logic 1: Follow last result
    Logic 2: Reverse last result
    Returns (prediction, logic_text, logic_num)
    """
    if not raw_history:
        return "WAIT", "Collecting data...", 1

    last = raw_history[0]["big_small"]

    if logic_mode == 1:
        pred  = last
        label = f"Logic 1 — Follow ({last})"
        num   = 1
    else:
        pred  = "SMALL" if last == "BIG" else "BIG"
        label = f"Logic 2 — Reverse ({last} -> {pred})"
        num   = 2

    return pred, label, num


# ─── POLLING LOOP ─────────────────────────────────────────
def polling_loop(mk):
    md = markets[mk]
    print(f"[EliteX] Polling started: {mk}")

    while True:
        try:
            cur  = fetch_current(md["url_current"])
            hist = fetch_history(md["url_history"])

            if hist:
                md["history"] = hist[:20]

            if cur:
                period    = cur["period"]
                remaining = cur["remaining"]
                win_result = None

                if md["last_period"] and md["last_period"] != period and md["history"]:
                    prev_list = [h for h in md["history"] if h["period"] == md["last_period"]]
                    prev_pred = md["last_predicted"]

                    if prev_list and prev_pred and prev_pred not in ("WAIT",):
                        actual = prev_list[0]["big_small"]
                        won    = (actual == prev_pred)
                        win_result = "WIN" if won else "LOSS"

                        # record in pred_history
                        md["pred_history"].insert(0, {
                            "period":    md["last_period"][-6:],
                            "actual":    actual,
                            "predicted": prev_pred,
                            "result":    "Win" if won else "Loss",
                        })
                        if len(md["pred_history"]) > 20:
                            md["pred_history"].pop()

                        # logic state machine
                        if md["logic_mode"] == 1:
                            if won:
                                md["l1_loss_streak"] = 0
                                print(f"[{mk}] WIN L1 | pred={prev_pred} actual={actual}")
                            else:
                                md["l1_loss_streak"] += 1
                                print(f"[{mk}] LOSS L1 streak={md['l1_loss_streak']} | pred={prev_pred} actual={actual}")
                                if md["l1_loss_streak"] >= 3:
                                    md["logic_mode"]     = 2
                                    md["l1_loss_streak"] = 0
                                    print(f"[{mk}] -> Switch to Logic 2")
                        else:  # logic 2
                            if won:
                                print(f"[{mk}] WIN L2 | pred={prev_pred} actual={actual}")
                            else:
                                print(f"[{mk}] LOSS L2 | -> Revert to Logic 1")
                                md["logic_mode"]     = 1
                                md["l1_loss_streak"] = 0

                md["last_period"] = period

                pred, label, num = make_prediction(md["logic_mode"], md["history"])
                md["last_predicted"] = pred

                md["state"].update({
                    "period":     period,
                    "prediction": pred,
                    "logic":      label,
                    "logic_num":  num,
                    "countdown":  remaining,
                    "mode":       md["mode_label"],
                    "win_result": win_result,
                })

                print(f"[{mk}] {period[-6:]} | {pred:5s} | {label} | {remaining}s")

        except Exception as e:
            print(f"[{mk}] Loop error: {e}")

        time.sleep(POLL_SEC)


# ─── FLASK ROUTES ─────────────────────────────────────────
@app.after_request
def add_ngrok_header(response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response


def build_response(mk):
    s = markets[mk]["state"]
    return jsonify({
        "period":     s["period"],
        "prediction": s["prediction"],
        "logic":      s["logic"],
        "logic_num":  s["logic_num"],
        "countdown":  s["countdown"],
        "mode":       s["mode"],
        "win_result": s["win_result"],
        "history":    markets[mk]["pred_history"],
    })


@app.route("/api/prediction")
def api_30s():
    return build_response("30s")


@app.route("/api/prediction/1m")
def api_1m():
    return build_response("1m")


@app.route("/")
def index():
    return "EliteX Backend Running", 200


# ─── ENTRY POINT ──────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  EliteX Traders — WinGO Backend")
    print(f"  30s: http://localhost:{PORT}/api/prediction")
    print(f"  1m : http://localhost:{PORT}/api/prediction/1m")
    print("=" * 55)

    for mk in ("30s", "1m"):
        t = threading.Thread(target=polling_loop, args=(mk,), daemon=True)
        t.start()

    app.run(host="0.0.0.0", port=PORT, debug=False)
