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
HEADERS  = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}
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


def text_or_none(node) -> str | None:
    if not node:
        return None
    text = node.get_text(" ", strip=True)
    return text or None


def to_int(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"-?\d+", text.replace(",", ""))
    return int(m.group()) if m else None


def to_float(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return float(m.group()) if m else None


def parse_race_page_info(soup: BeautifulSoup, race_id: str) -> dict:
    race_data = {"race_id": race_id, "venue_code": race_id[4:6]}
    race_data["race_number"] = int(race_id[-2:])

    name = soup.select_one(".RaceName")
    race_data["race_name"] = text_or_none(name) or ""

    page_text = soup.get_text(" ", strip=True)
    m_date = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", page_text)
    if m_date:
        race_data["race_date"] = (
            f"{m_date.group(1)}-{m_date.group(2).zfill(2)}-{m_date.group(3).zfill(2)}"
        )

    race_data01 = text_or_none(soup.select_one(".RaceData01")) or ""
    if "芝" in race_data01:
        race_data["surface"] = "芝"
    elif "ダ" in race_data01:
        race_data["surface"] = "ダート"
    elif "障" in race_data01:
        race_data["surface"] = "障害"

    if "右" in race_data01:
        race_data["direction"] = "右"
    elif "左" in race_data01:
        race_data["direction"] = "左"
    elif "直線" in race_data01:
        race_data["direction"] = "直線"

    m_distance = re.search(r"(\d+)m", race_data01)
    if m_distance:
        race_data["distance"] = int(m_distance.group(1))

    m_weather = re.search(r"天候:([^/\s]+)", race_data01)
    if m_weather:
        race_data["weather"] = m_weather.group(1)

    m_condition = re.search(r"馬場:([^/\s]+)", race_data01)
    if m_condition:
        race_data["track_condition"] = m_condition.group(1)

    race_data02 = soup.select_one(".RaceData02")
    spans = [text_or_none(span) for span in race_data02.select("span")] if race_data02 else []
    spans = [span for span in spans if span]
    if len(spans) >= 5:
        race_data["class"] = spans[4]

    return race_data


def parse_weight(text: str | None) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    weight = to_int(text)
    m_diff = re.search(r"\(([-+]?\d+)\)", text)
    return weight, int(m_diff.group(1)) if m_diff else None


def ids_from_result_row(row) -> tuple[str | None, str | None, str | None]:
    horse_href = row.select_one(".Horse_Name a")
    jockey_href = row.select_one(".Jockey a")
    trainer_href = row.select_one(".Trainer a")

    horse_id = None
    jockey_id = None
    trainer_id = None
    if horse_href:
        m = re.search(r"/horse/(\w+)", horse_href.get("href", ""))
        horse_id = m.group(1) if m else None
    if jockey_href:
        m = re.search(r"/jockey/(?:result/recent/)?(\w+)", jockey_href.get("href", ""))
        jockey_id = m.group(1) if m else None
    if trainer_href:
        m = re.search(r"/trainer/(?:result/recent/)?(\w+)", trainer_href.get("href", ""))
        trainer_id = m.group(1) if m else None

    return horse_id, jockey_id, trainer_id


def payout_combinations(result_cell) -> list[str]:
    groups = result_cell.find_all("ul", recursive=False)
    if groups:
        return [
            "-".join(span.get_text(strip=True) for span in group.find_all("span") if span.get_text(strip=True))
            for group in groups
        ]

    spans = [span.get_text(strip=True) for span in result_cell.find_all("span") if span.get_text(strip=True)]
    return spans


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
    url = f"{BASE_RACE}/race/result.html?race_id={race_id}"
    soup = fetch(url)
    if not soup:
        return

    upsert("races", parse_race_page_info(soup, race_id))

    # ── 出走・結果テーブル ──
    result_table = soup.select_one("#All_Result_Table")
    if result_table:
        for tr in result_table.select("tbody tr.HorseList"):
            cols = tr.find_all("td")
            if len(cols) < 15:
                continue
            try:
                horse_id, jockey_id, trainer_id = ids_from_result_row(tr)
                horse_name = text_or_none(tr.select_one(".HorseNameSpan")) or ""

                # race.netkeibaの結果ページにある最小情報を先に保存する。
                if horse_id:
                    upsert("horses", {"horse_id": horse_id, "horse_name": horse_name})
                if jockey_id:
                    upsert("jockeys", {"jockey_id": jockey_id, "jockey_name": text_or_none(tr.select_one(".JockeyNameSpan")) or ""})
                if trainer_id:
                    upsert("trainers", {"trainer_id": trainer_id, "trainer_name": text_or_none(tr.select_one(".TrainerNameSpan")) or ""})

                weight, weight_diff = parse_weight(text_or_none(cols[14]))
                finish_pos = text_or_none(cols[0])
                entry = {
                    "race_id":          race_id,
                    "horse_id":         horse_id,
                    "jockey_id":        jockey_id,
                    "trainer_id":       trainer_id,
                    "post_position":    to_int(text_or_none(cols[1])),
                    "horse_number":     to_int(text_or_none(cols[2])),
                    "finish_position":  int(finish_pos) if finish_pos.isdigit() else None,
                    "age":              to_int(text_or_none(cols[4])),
                    "burden_weight":    to_float(text_or_none(cols[5])),
                    "finish_time":      text_or_none(cols[7]),
                    "margin":           text_or_none(cols[8]),
                    "odds":             to_float(text_or_none(cols[10])),
                    "popularity":       to_int(text_or_none(cols[9])),
                    "weight":           weight,
                    "weight_diff":      weight_diff,
                }
                upsert("race_entries", entry)

            except Exception as e:
                logger.warning(f"entry parse error: {race_id} -> {e}")

    # ── 払い戻しテーブル ──
    payout_tables = soup.select("table.Payout_Detail_Table")
    for pt in payout_tables:
        for tr in pt.find_all("tr"):
            th = tr.find("th")
            cols = tr.find_all("td")
            if not th or len(cols) < 2:
                continue
            try:
                bet_type = th.text.strip()
                combos = payout_combinations(cols[0])
                amounts = [s.strip() for s in cols[1].get_text("\n", strip=True).split("\n") if s.strip()]
                pops = [s.strip() for s in cols[2].get_text("\n", strip=True).split("\n") if s.strip()] if len(cols) > 2 else []

                for i, combo in enumerate(combos):
                    upsert("payouts", {
                        "race_id":    race_id,
                        "bet_type":   bet_type,
                        "combination": combo,
                        "payout":     to_int(amounts[i]) if i < len(amounts) else 0,
                        "popularity": to_int(pops[i]) if i < len(pops) else None,
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
