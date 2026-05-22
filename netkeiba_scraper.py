"""
netkeiba.com スクレイピング → Supabase 取り込みスクリプト
実行環境: GitHub Actions (Python 3.11)

必要なパッケージ:
  pip install requests beautifulsoup4 supabase python-dotenv

環境変数（GitHub Secrets に登録）:
  SUPABASE_URL  : SupabaseプロジェクトのURL
  SUPABASE_KEY  : Supabaseのservice_role key
"""

import os
import re
import time
import logging
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# Supabase クライアント
# ─────────────────────────────────────────
supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"],
)

# ─────────────────────────────────────────
# 定数
# ─────────────────────────────────────────
BASE_DB  = "https://db.netkeiba.com"
BASE_RACE = "https://race.netkeiba.com"
HEADERS  = {"User-Agent": "Mozilla/5.0"}
INTERVAL = 2  # リクエスト間隔（秒）※サーバー負荷軽減のため必ず守ること

BET_TYPES = ["単勝", "複勝", "枠連", "馬連", "馬単", "ワイド", "3連複", "3連単"]


# ─────────────────────────────────────────
# ユーティリティ
# ─────────────────────────────────────────
def fetch(url: str) -> BeautifulSoup | None:
    """GETしてBeautifulSoupを返す。失敗時はNone。"""
    try:
        time.sleep(INTERVAL)
        res = requests.get(url, headers=HEADERS, timeout=15)
        res.encoding = "EUC-JP"  # netkeibaはEUC-JP
        res.raise_for_status()
        return BeautifulSoup(res.text, "html.parser")
    except Exception as e:
        logger.error(f"fetch error: {url} -> {e}")
        return None


def upsert(table: str, data: dict) -> None:
    """Supabaseにupsert（重複時は更新）。"""
    try:
        supabase.table(table).upsert(data).execute()
    except Exception as e:
        logger.error(f"upsert error: {table} -> {e}")


# ─────────────────────────────────────────
# レース一覧の取得
# ─────────────────────────────────────────
def get_race_id_list(date: str) -> list[str]:
    """
    指定日のレースID一覧を取得。
    date: "YYYYMMDD"
    """
    url = f"{BASE_RACE}/top/race_list_sub.html?kaisai_date={date}"
    soup = fetch(url)
    if not soup:
        return []

    race_ids = []
    for a in soup.find_all("a", href=True):
        m = re.search(r"race_id=(\d{12})", a["href"])
        if m:
            race_ids.append(m.group(1))

    return list(set(race_ids))


# ─────────────────────────────────────────
# 馬情報の取得・保存
# ─────────────────────────────────────────
def scrape_horse(horse_id: str) -> None:
    """馬プロフィールと血統をDBに保存。"""
    url = f"{BASE_DB}/horse/{horse_id}/"
    soup = fetch(url)
    if not soup:
        return

    # プロフィールテーブル
    profile = {}
    table = soup.find("table", class_="db_prof_table")
    if table:
        for tr in table.find_all("tr"):
            th = tr.find("th")
            td = tr.find("td")
            if th and td:
                profile[th.text.strip()] = td.text.strip()

    upsert("horses", {
        "horse_id":   horse_id,
        "horse_name": soup.find("div", class_="horse_title").find("h1").text.strip() if soup.find("div", class_="horse_title") else "",
        "birth_date": profile.get("生年月日"),
        "sex":        profile.get("性別"),
        "coat_color": profile.get("毛色"),
    })

    # 血統テーブル（pandas read_html が楽だが、ここでは bs4 で対応）
    ped_table = soup.find("table", class_="blood_table")
    if ped_table:
        cells = [td.text.strip() for td in ped_table.find_all("td")]
        if len(cells) >= 6:
            upsert("horse_pedigrees", {
                "horse_id":      horse_id,
                "father":        cells[0] if len(cells) > 0 else None,
                "father_father": cells[1] if len(cells) > 1 else None,
                "father_mother": cells[2] if len(cells) > 2 else None,
                "mother":        cells[3] if len(cells) > 3 else None,
                "mother_father": cells[4] if len(cells) > 4 else None,
                "mother_mother": cells[5] if len(cells) > 5 else None,
            })


# ─────────────────────────────────────────
# レース結果の取得・保存
# ─────────────────────────────────────────
def scrape_race(race_id: str) -> None:
    """レース結果・払い戻しをDBに保存。"""
    url = f"{BASE_DB}/race/{race_id}/"
    soup = fetch(url)
    if not soup:
        return

    # ── レース基本情報 ──
    race_data = {"race_id": race_id}

    try:
        head = soup.find("div", class_="race_head_inner") or soup.find("div", class_="mainrace_data")
        race_data["race_name"] = head.find("h1").text.strip() if head else ""

        # 日付・会場・レース番号
        data_intro = soup.find("div", class_="data_intro")
        if data_intro:
            p_texts = [p.text.strip() for p in data_intro.find_all("p")]
            for text in p_texts:
                m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
                if m:
                    race_data["race_date"] = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
                m2 = re.search(r"(\d+)R", text)
                if m2:
                    race_data["race_number"] = int(m2.group(1))

        # コース情報（例: "芝・右 2000m"）
        course_text = soup.find("div", class_="race_data")
        if course_text:
            t = course_text.text
            if "芝"   in t: race_data["surface"] = "芝"
            elif "ダ" in t: race_data["surface"] = "ダート"
            elif "障"  in t: race_data["surface"] = "障害"
            if "右"    in t: race_data["direction"] = "右"
            elif "左"  in t: race_data["direction"] = "左"
            elif "直線" in t: race_data["direction"] = "直線"
            m = re.search(r"(\d+)m", t)
            if m:
                race_data["distance"] = int(m.group(1))
            # 天候・馬場
            m_w = re.search(r"天候:(\S+)", t)
            if m_w:
                race_data["weather"] = m_w.group(1)
            m_t = re.search(r"馬場:(\S+)", t)
            if m_t:
                race_data["track_condition"] = m_t.group(1)

        # venue_code: race_idの5-6文字目
        race_data["venue_code"] = race_id[4:6]

    except Exception as e:
        logger.warning(f"race info parse error: {race_id} -> {e}")

    upsert("races", race_data)

    # ── 出走・結果テーブル ──
    result_table = soup.find("table", class_="race_table_01")
    if result_table:
        for tr in result_table.find_all("tr")[1:]:  # ヘッダー除く
            cols = tr.find_all("td")
            if len(cols) < 10:
                continue
            try:
                # 馬・騎手・調教師IDをhrefから抽出
                horse_href   = cols[3].find("a")["href"] if cols[3].find("a") else ""
                jockey_href  = cols[6].find("a")["href"] if cols[6].find("a") else ""
                trainer_href = cols[7].find("a")["href"] if cols[7].find("a") else ""

                horse_id   = re.search(r"/horse/(\w+)",   horse_href).group(1)   if re.search(r"/horse/(\w+)",   horse_href)   else None
                jockey_id  = re.search(r"/jockey/(\w+)",  jockey_href).group(1)  if re.search(r"/jockey/(\w+)",  jockey_href)  else None
                trainer_id = re.search(r"/trainer/(\w+)", trainer_href).group(1) if re.search(r"/trainer/(\w+)", trainer_href) else None

                # 騎手・調教師をマスタに保存
                if jockey_id:
                    upsert("jockeys", {"jockey_id": jockey_id, "jockey_name": cols[6].text.strip()})
                if trainer_id:
                    upsert("trainers", {"trainer_id": trainer_id, "trainer_name": cols[7].text.strip()})

                # 馬情報を取得（初回のみ）
                if horse_id:
                    existing = supabase.table("horses").select("horse_id").eq("horse_id", horse_id).execute()
                    if not existing.data:
                        scrape_horse(horse_id)

                finish_pos = cols[0].text.strip()
                entry = {
                    "race_id":          race_id,
                    "horse_id":         horse_id,
                    "jockey_id":        jockey_id,
                    "trainer_id":       trainer_id,
                    "post_position":    int(cols[1].text.strip()) if cols[1].text.strip().isdigit() else None,
                    "horse_number":     int(cols[2].text.strip()) if cols[2].text.strip().isdigit() else None,
                    "finish_position":  int(finish_pos) if finish_pos.isdigit() else None,
                    "age":              int(re.sub(r"\D", "", cols[4].text.strip())) if re.sub(r"\D", "", cols[4].text.strip()) else None,
                    "burden_weight":    float(cols[5].text.strip()) if cols[5].text.strip().replace(".", "").isdigit() else None,
                    "finish_time":      cols[8].text.strip() if len(cols) > 8 else None,
                    "margin":           cols[9].text.strip() if len(cols) > 9 else None,
                    "odds":             float(cols[12].text.strip()) if len(cols) > 12 and cols[12].text.strip().replace(".", "").isdigit() else None,
                    "popularity":       int(cols[13].text.strip()) if len(cols) > 13 and cols[13].text.strip().isdigit() else None,
                    "weight":           int(re.search(r"(\d+)", cols[14].text).group(1)) if len(cols) > 14 and re.search(r"(\d+)", cols[14].text) else None,
                    "weight_diff":      int(re.search(r"[+-]?\d+", cols[14].text.replace("(", "").replace(")", "")).group()) if len(cols) > 14 and re.search(r"[+-]?\d+", cols[14].text) else None,
                }
                upsert("race_entries", entry)

            except Exception as e:
                logger.warning(f"entry parse error: {race_id} -> {e}")

    # ── 払い戻しテーブル ──
    payout_tables = soup.find_all("table", class_="pay_table_01")
    for pt in payout_tables:
        for tr in pt.find_all("tr"):
            cols = tr.find_all("td")
            if len(cols) < 2:
                continue
            try:
                bet_type = cols[0].text.strip()
                combos   = [s.strip() for s in cols[1].text.strip().split("\n") if s.strip()]
                amounts  = [s.strip() for s in cols[2].text.strip().split("\n") if s.strip()]
                pops     = [s.strip() for s in cols[3].text.strip().split("\n") if s.strip()] if len(cols) > 3 else []

                for i, combo in enumerate(combos):
                    upsert("payouts", {
                        "race_id":    race_id,
                        "bet_type":   bet_type,
                        "combination": combo,
                        "payout":     int(amounts[i].replace(",", "").replace("円", "")) if i < len(amounts) else 0,
                        "popularity": int(pops[i].replace("番人気", "")) if i < len(pops) else None,
                    })
            except Exception as e:
                logger.warning(f"payout parse error: {race_id} -> {e}")

    logger.info(f"done: {race_id}")


# ─────────────────────────────────────────
# メイン：日付範囲でスクレイピング
# ─────────────────────────────────────────
def scrape_date_range(start: str, end: str) -> None:
    """
    start, end: "YYYYMMDD"
    指定期間の全レースを取得してSupabaseに保存。
    """
    current = datetime.strptime(start, "%Y%m%d")
    end_dt  = datetime.strptime(end,   "%Y%m%d")

    while current <= end_dt:
        date_str = current.strftime("%Y%m%d")
        logger.info(f"=== {date_str} ===")

        race_ids = get_race_id_list(date_str)
        logger.info(f"  {len(race_ids)} races found")

        for race_id in race_ids:
            scrape_race(race_id)

        current += timedelta(days=1)


# ─────────────────────────────────────────
# エントリーポイント
# ─────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if len(sys.argv) == 3:
        # 例: python netkeiba_scraper.py 20240101 20240131
        scrape_date_range(sys.argv[1], sys.argv[2])
    else:
        # 引数なし → 昨日分を取得（GitHub Actions定期実行用）
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        scrape_date_range(yesterday, yesterday)
