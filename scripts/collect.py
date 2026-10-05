#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日股市晨報 — 資料收集 + 熱門族群排行 + AI 摘要 + 產生靜態網頁
由 GitHub Actions 於每個交易日台北時間 08:00 自動執行。

設計原則：每一個外部資料來源都用 try/except 包起來，
單一來源失敗不會讓整份報告產生失敗 —— 只會在該區塊顯示「暫時無法取得」，
其他區塊照常產生。所有錯誤訊息都會印到 Actions 的執行紀錄(log)方便除錯。
"""

import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone

import requests

try:
    import feedparser
except ImportError:
    feedparser = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

TPE = timezone(timedelta(hours=8))
NOW = datetime.now(TPE)
TODAY_STR = NOW.strftime("%Y-%m-%d")
TODAY_ROC = f"{NOW.year - 1911}年{NOW.month:02d}月{NOW.day:02d}日"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
TIMEOUT = 15

# ---------------------------------------------------------------------------
# 1. 熱門族群觀察名單 ——— 可自行增減股票代號
#    邏輯：抓當日(或最近交易日)全市場收盤資料，依這裡分的組別算平均漲跌幅，
#    漲幅最大的組別 = 今天最熱門的族群。
# ---------------------------------------------------------------------------
SECTOR_WATCHLIST = {
    "半導體-晶圓/記憶體": ["2330", "2303", "2337", "6770", "2408", "2449"],
    "半導體-IC設計":      ["2454", "3034", "2379", "3532", "3443"],
    "AI/伺服器":           ["2317", "2382", "3231", "6669", "2301"],
    "PCB/CCL":             ["3037", "2383", "6213", "2368"],
    "散熱":                ["3017", "2421", "4568"],
    "金融保險":            ["2881", "2882", "2891", "2886", "2880"],
    "航運":                ["2603", "2609", "2615"],
    "傳產/鋼鐵":           ["2002", "1301", "1303"],
    "重電/能源":           ["1503", "1513", "4939"],
    "生技醫療":            ["4743", "6446", "1795"],
    "電動車":              ["2308", "6213", "1536"],
}

NEWS_FEEDS = [
    ("工商時報", "https://ctee.com.tw/feed"),
    ("經濟日報(money.udn)", "https://money.udn.com/rssfeed/news/1001/5590?ch=money"),
    ("MoneyDJ 理財網", "https://www.moneydj.com/kmdj/rss/newslistrss.ashx?svc=NR&a=MB01"),
    ("鉅亨網台股", "https://news.cnyes.com/rss/cat/tw_stock_news"),
]

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()


def log(msg):
    print(f"[{datetime.now(TPE).strftime('%H:%M:%S')}] {msg}", file=sys.stderr)


def safe_get_json(url, **kwargs):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# 2. 全市場收盤資料 (TWSE OpenAPI, 公開不需金鑰)
# ---------------------------------------------------------------------------
def fetch_market_data():
    """回傳 {股票代號: {"name":..., "close":..., "change_pct":...}}；失敗回傳 None"""
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    data = safe_get_json(url)
    result = {}
    for row in data:
        code = row.get("Code") or row.get("證券代號")
        if not code:
            continue
        try:
            close = float(str(row.get("ClosingPrice", row.get("收盤價", "0"))).replace(",", ""))
            change = float(str(row.get("Change", row.get("漲跌價差", "0"))).replace(",", ""))
            prev_close = close - change
            change_pct = (change / prev_close * 100) if prev_close else 0.0
        except (TypeError, ValueError):
            continue
        result[code] = {
            "name": row.get("Name") or row.get("證券名稱") or code,
            "close": close,
            "change_pct": change_pct,
        }
    return result


def rank_sectors(market_data):
    """依 SECTOR_WATCHLIST 算每個族群平均漲跌幅，回傳依漲幅排序的 list"""
    ranked = []
    for sector, codes in SECTOR_WATCHLIST.items():
        rows = [market_data[c] for c in codes if c in market_data]
        if not rows:
            continue
        avg_pct = sum(r["change_pct"] for r in rows) / len(rows)
        leaders = sorted(rows, key=lambda r: r["change_pct"], reverse=True)[:3]
        ranked.append({
            "sector": sector,
            "avg_pct": avg_pct,
            "leaders": [f"{r['name']}({r['change_pct']:+.2f}%)" for r in leaders],
        })
    ranked.sort(key=lambda x: x["avg_pct"], reverse=True)
    return ranked


# ---------------------------------------------------------------------------
# 3. 美股/費半 收盤 (Yahoo Finance，公開 chart API)
# ---------------------------------------------------------------------------
def fetch_yahoo_quote(symbol, label):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
    data = safe_get_json(url)
    result = data["chart"]["result"][0]
    closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
    if len(closes) < 2:
        raise ValueError("not enough data points")
    last, prev = closes[-1], closes[-2]
    pct = (last - prev) / prev * 100
    return {"label": label, "price": last, "change_pct": pct}


def fetch_overnight_us():
    symbols = [
        ("^SOX", "費城半導體指數"),
        ("^IXIC", "那斯達克"),
        ("^GSPC", "S&P 500"),
        ("TWD=X", "美元兌台幣"),
    ]
    out = []
    for sym, label in symbols:
        try:
            out.append(fetch_yahoo_quote(sym, label))
        except Exception as e:
            log(f"美股資料 {label} 抓取失敗: {e}")
    return out


# ---------------------------------------------------------------------------
# 4. 新聞 RSS
# ---------------------------------------------------------------------------
def fetch_news():
    if feedparser is None:
        log("feedparser 未安裝，略過新聞區塊")
        return []
    items = []
    for name, url in NEWS_FEEDS:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            if not parsed.entries:
                log(f"新聞來源「{name}」沒有抓到任何項目，跳過")
                continue
            for entry in parsed.entries[:6]:
                items.append({
                    "source": name,
                    "title": entry.get("title", "").strip(),
                    "link": entry.get("link", ""),
                })
        except Exception as e:
            log(f"新聞來源「{name}」抓取失敗: {e}")
    return items


# ---------------------------------------------------------------------------
# 5. 法說會 (best-effort 爬取，失敗則只留連結)
# ---------------------------------------------------------------------------
IR_CALENDAR_LINK = "https://www.wantgoo.com/stock/calendar/investors-conference"
IR_PLATFORM_LINK = "https://irplatform.tdcc.com.tw/ir/zh/event/list"


def fetch_ir_events():
    if BeautifulSoup is None:
        log("BeautifulSoup 未安裝，略過法說會爬取，只保留連結")
        return []
    try:
        resp = requests.get(IR_CALENDAR_LINK, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("table tr")
        events = []
        for tr in rows[1:20]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) >= 2 and any(cells):
                events.append(" ・ ".join(c for c in cells if c))
        return events[:15]
    except Exception as e:
        log(f"法說會頁面抓取失敗: {e}")
        return []


# ---------------------------------------------------------------------------
# 6. AI 摘要 (選用；沒設定 GEMINI_API_KEY 就用規則式句子代替)
# ---------------------------------------------------------------------------
def build_rule_based_summary(ranked_sectors, overnight):
    if not ranked_sectors:
        return "今日族群數據暫時無法取得，請直接參考下方個股與新聞區塊。"
    top = ranked_sectors[0]
    lines = [
        f"今日觀察族群中，「{top['sector']}」平均漲幅最高，約 {top['avg_pct']:+.2f}%，"
        f"主要由 {('、'.join(top['leaders']))} 領漲。"
    ]
    if len(ranked_sectors) > 1:
        bottom = ranked_sectors[-1]
        lines.append(f"表現相對落後的是「{bottom['sector']}」，平均 {bottom['avg_pct']:+.2f}%。")
    sox = next((o for o in overnight if o["label"] == "費城半導體指數"), None)
    if sox:
        lines.append(f"隔夜費半指數{'上漲' if sox['change_pct']>=0 else '下跌'} {sox['change_pct']:+.2f}%，可留意台股半導體族群開盤反應。")
    return " ".join(lines)


def build_ai_summary(ranked_sectors, overnight, news):
    if not GEMINI_API_KEY:
        return None
    try:
        sector_text = "\n".join(
            f"- {r['sector']}: 平均漲跌 {r['avg_pct']:+.2f}%，領漲股 {', '.join(r['leaders'])}"
            for r in ranked_sectors
        )
        overnight_text = "\n".join(
            f"- {o['label']}: {o['change_pct']:+.2f}%" for o in overnight
        )
        news_text = "\n".join(f"- [{n['source']}] {n['title']}" for n in news[:20])

        prompt = (
            "你是台股盤前分析助理，請用繁體中文、精簡的語氣（3到5句話，不要條列、不要免責聲明），"
            "根據以下今日族群漲跌幅排行、隔夜美股/費半數據、以及今早財經新聞標題，"
            "總結今日最值得留意的熱門族群與可能原因。若新聞與族群數據吻合請一併指出，"
            "但不要給明確的買賣建議，只做現象描述與可能原因推測。\n\n"
            f"【族群平均漲跌幅排行】\n{sector_text}\n\n"
            f"【隔夜美股/費半】\n{overnight_text}\n\n"
            f"【今早新聞標題】\n{news_text}\n"
        )

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
        )
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        log(f"AI 摘要呼叫失敗，改用規則式摘要: {e}")
        return None


# ---------------------------------------------------------------------------
# 7. 產生 HTML
# ---------------------------------------------------------------------------
def render_html(ranked_sectors, overnight, news, ir_events, summary_text, used_ai):
    def pct_class(p):
        return "up" if p >= 0 else "down"

    sector_rows = "".join(
        f"""<tr>
          <td>{i+1}</td>
          <td>{r['sector']}</td>
          <td class="{pct_class(r['avg_pct'])}">{r['avg_pct']:+.2f}%</td>
          <td>{' / '.join(r['leaders'])}</td>
        </tr>"""
        for i, r in enumerate(ranked_sectors)
    ) or "<tr><td colspan='4'>今日資料暫時無法取得</td></tr>"

    overnight_cards = "".join(
        f"""<div class="card">
          <div class="card-label">{o['label']}</div>
          <div class="card-value {pct_class(o['change_pct'])}">{o['change_pct']:+.2f}%</div>
        </div>"""
        for o in overnight
    ) or "<div class='card'><div class='card-label'>暫時無法取得美股資料</div></div>"

    news_items = "".join(
        f"""<li><a href="{n['link']}" target="_blank" rel="noopener">{n['title']}</a>
          <span class="src">{n['source']}</span></li>"""
        for n in news
    ) or "<li>今日新聞來源暫時都抓不到，稍後重新整理看看。</li>"

    ir_items = "".join(f"<li>{e}</li>" for e in ir_events) or (
        f"<li>今日無法自動列出法說會清單，請參考 "
        f"<a href='{IR_CALENDAR_LINK}' target='_blank'>wantgoo 法說會行事曆</a> 或 "
        f"<a href='{IR_PLATFORM_LINK}' target='_blank'>集保結算所 IR 平台</a>。</li>"
    )

    badge = "AI 摘要 (Gemini)" if used_ai else "規則式摘要"

    html = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>股市晨報 {TODAY_STR}</title>
<style>
  :root {{
    --bg: #0f1117; --card: #171a22; --text: #e8eaed; --muted: #9aa0a6;
    --up: #ff5c5c; --down: #2ecc71; --accent: #5b8def; --border: #2a2d38;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, "PingFang TC", "Noto Sans TC", "Microsoft JhengHei", sans-serif;
    line-height: 1.6; padding: 0 0 48px;
  }}
  header {{ padding: 28px 20px 16px; max-width: 880px; margin: 0 auto; }}
  h1 {{ margin: 0 0 4px; font-size: 1.5rem; }}
  .sub {{ color: var(--muted); font-size: 0.9rem; }}
  main {{ max-width: 880px; margin: 0 auto; padding: 0 20px; }}
  section {{ margin-top: 28px; }}
  h2 {{ font-size: 1.05rem; border-left: 4px solid var(--accent); padding-left: 10px; margin-bottom: 12px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.92rem; }}
  th, td {{ text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--muted); font-weight: 500; }}
  .up {{ color: var(--up); font-weight: 600; }}
  .down {{ color: var(--down); font-weight: 600; }}
  .cards {{ display: flex; gap: 10px; flex-wrap: wrap; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px;
           padding: 12px 16px; min-width: 130px; }}
  .card-label {{ color: var(--muted); font-size: 0.8rem; margin-bottom: 4px; }}
  .card-value {{ font-size: 1.15rem; font-weight: 600; }}
  .summary-box {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px;
                  padding: 16px 18px; font-size: 0.95rem; }}
  .badge {{ display: inline-block; font-size: 0.72rem; color: var(--muted); border: 1px solid var(--border);
            border-radius: 20px; padding: 2px 10px; margin-bottom: 10px; }}
  ul {{ margin: 0; padding-left: 0; list-style: none; }}
  li {{ padding: 9px 0; border-bottom: 1px solid var(--border); font-size: 0.92rem; }}
  li a {{ color: var(--text); text-decoration: none; }}
  li a:hover {{ color: var(--accent); }}
  .src {{ display: block; color: var(--muted); font-size: 0.78rem; margin-top: 2px; }}
  footer {{ max-width: 880px; margin: 32px auto 0; padding: 0 20px; color: var(--muted); font-size: 0.78rem; }}
</style>
</head>
<body>
<header>
  <h1>📈 股市晨報</h1>
  <div class="sub">{TODAY_ROC} ・ 產生時間 {NOW.strftime('%H:%M')} (台北時間)</div>
</header>
<main>

  <section>
    <h2>今日摘要</h2>
    <div class="badge">{badge}</div>
    <div class="summary-box">{summary_text}</div>
  </section>

  <section>
    <h2>熱門族群排行（依觀察名單平均漲跌幅）</h2>
    <table>
      <thead><tr><th>#</th><th>族群</th><th>平均漲跌</th><th>領漲股</th></tr></thead>
      <tbody>{sector_rows}</tbody>
    </table>
  </section>

  <section>
    <h2>隔夜美股 / 費半</h2>
    <div class="cards">{overnight_cards}</div>
  </section>

  <section>
    <h2>今早財經新聞</h2>
    <ul>{news_items}</ul>
  </section>

  <section>
    <h2>法人說明會(法說會)</h2>
    <ul>{ir_items}</ul>
  </section>

</main>
<footer>
  本頁由 GitHub Actions 自動產生，僅供個人參考，不構成投資建議。資料來源：證交所 OpenAPI、Yahoo Finance、各新聞網站 RSS。
</footer>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    log("開始收集資料...")

    ranked_sectors = []
    try:
        market_data = fetch_market_data()
        ranked_sectors = rank_sectors(market_data)
        log(f"族群排行完成，共 {len(ranked_sectors)} 組")
    except Exception:
        log("抓取全市場收盤資料失敗：\n" + traceback.format_exc())

    overnight = fetch_overnight_us()
    news = fetch_news()
    ir_events = fetch_ir_events()

    ai_summary = build_ai_summary(ranked_sectors, overnight, news)
    used_ai = ai_summary is not None
    summary_text = ai_summary or build_rule_based_summary(ranked_sectors, overnight)

    html = render_html(ranked_sectors, overnight, news, ir_events, summary_text, used_ai)

    out_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    log(f"報告已寫入 {out_path}")


if __name__ == "__main__":
    main()
