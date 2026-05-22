"""
netkeiba.com スクレイピング → Supabase 取り込みスクリプト
実行環境: GitHub Actions (Python 3.11)

race.netkeiba.com を使用（db.netkeiba.comはCloudフロムのIPをブロック）
レース結果URL: https://race.netkeiba.com/race/result.html?race_id={race_id}
"""

import os
import re
import time
import logging
import random
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"],
)

BASE_RACE = "https://race.netkeiba.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://race.netkeiba.com/",
}

session = requests.Session()
session.headers.update(HEADERS)


def fetch(url: str):
    try:
        time.sleep(random.uniform(3, 6))
        res = session.get(url, timeout=20)
        res.encoding = "EUC-JP"
        if res.status_code in (403, 404):
            logger.warning(f"{res.status_code}: {url} - skip")
            return None
        res.raise_for_status()
        return BeautifulSoup(res.text, "html.parser")
    except Exception as e:
        logger.error(f"fetch error: {url} -> {e}")
        return None


def upsert(table: str, data: dict) -> None:
    try:
        supabase.table(table).upsert(data).execute()
    except Exception as e:
        logger.error(f"upsert error: {table} -> {e}")


def get_race_id_list(date: str):
    """指定日のレースID一覧を取得。"""
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


def scrape_race(race_id: str) -> None:
    """race.netkeiba.comのresultページからレース結果を取得。"""
    url = f"{BASE_RACE}/race/result.html?race_id={race_id}&rf=race_list"
    soup = fetch(url)
    if not soup:
        return

    race_data = {"race_id": race_id, "venue_code": race_id[4:6]}

    # レース名
    race_name_el = soup.find("div", class_="RaceName") or soup.find("h1", class_="RaceName")
    if race_name_el:
        race_data["race_name"] = race_name_el.text.strip()

    # レース情報（距離・馬場・天候など）
    race_data01 = soup.find("div", class_="RaceData01")
    if race_data01:
        t = race_data01.text
        if "芝" in t: race_data["surface"] = "芝"
        elif "ダ" in t: race_data["surface"] = "ダート"
        elif "障" in t: race_data["surface"] = "障害"
        if "右" in t: race_data["direction"] = "右"
        elif "左" in t: race_data["direction"] = "左"
        m = re.search(r"(\d+)m", t)
        if m: race_data["distance"] = int(m.group(1))
        mw = re.search(r"天候:(\S+)", t)
        if mw: race_data["weather"] = mw.group(1)
        mt = re.search(r"馬場:(\S+)", t)
        if mt: race_data["track_condition"] = mt.group(1)

    # 日付・レース番号
    race_data02 = soup.find("div", class_="RaceData02")
    if race_data02:
        spans = race_data02.find_all("span")
        for span in spans:
            m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", span.text)
            if m:
                race_data["race_date"] = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
            m2 = re.search(r"(\d+)R", span.text)
            if m2:
                race_data["race_number"] = int(m2.group(1))

    upsert("races", race_data)

    # 出走・結果テーブル
    result_table = soup.find("table", class_="RaceTable01") or soup.find("table", id="All_Result_Tables")
    if result_table:
        for tr in result_table.find_all("tr")[1:]:
            cols = tr.find_all("td")
            if len(cols) < 8:
                continue
            try:
                # 馬名リンクから horse_id を取得
                horse_link = tr.find("a", href=re.compile(r"/horse/"))
                horse_id = re.search(r"/horse/(\w+)", horse_link["href"]).group(1) if horse_link else None

                # 騎手リンクから jockey_id を取得
                jockey_link = tr.find("a", href=re.compile(r"/jockey/"))
                jockey_id = re.search(r"/jockey/(\w+)", jockey_link["href"]).group(1) if jockey_link else None

                # 調教師リンクから trainer_id を取得
                trainer_link = tr.find("a", href=re.compile(r"/trainer/"))
                trainer_id = re.search(r"/trainer/(\w+)", trainer_link["href"]).group(1) if trainer_link else None

                if jockey_id and jockey_link:
                    upsert("jockeys", {"jockey_id": jockey_id, "jockey_name": jockey_link.text.strip()})
                if trainer_id and trainer_link:
                    upsert("trainers", {"trainer_id": trainer_id, "trainer_name": trainer_link.text.strip()})
                if horse_id and horse_link:
                    ex = supabase.table("horses").select("horse_id").eq("horse_id", horse_id).execute()
                    if not ex.data:
                        upsert("horses", {"horse_id": horse_id, "horse_name": horse_link.text.strip()})

                fp = cols[0].text.strip()
                upsert("race_entries", {
                    "race_id":         race_id,
                    "horse_id":        horse_id,
                    "jockey_id":       jockey_id,
                    "trainer_id":      trainer_id,
                    "post_position":   int(cols[1].text.strip()) if cols[1].text.strip().isdigit() else None,
                    "horse_number":    int(cols[2].text.strip()) if cols[2].text.strip().isdigit() else None,
                    "finish_position": int(fp) if fp.isdigit() else None,
                    "finish_time":     cols[7].text.strip() if len(cols) > 7 else None,
                    "odds":            float(cols[10].text.strip()) if len(cols) > 10 and re.match(r"[\d.]+", cols[10].text.strip()) else None,
                    "popularity":      int(cols[11].text.strip()) if len(cols) > 11 and cols[11].text.strip().isdigit() else None,
                })
            except Exception as e:
                logger.warning(f"entry parse error: {race_id} row -> {e}")

    # 払い戻しテーブル
    for pt in soup.find_all("table", class_="Harai"):
        for tr in pt.find_all("tr"):
            cols = tr.find_all("td")
            if len(cols) < 3:
                continue
            try:
                bt      = cols[0].text.strip()
                combos  = [s.strip() for s in cols[1].text.strip().split("\n") if s.strip()]
                amounts = [s.strip() for s in cols[2].text.strip().split("\n") if s.strip()]
                pops    = [s.strip() for s in cols[3].text.strip().split("\n") if s.strip()] if len(cols) > 3 else []
                for i, combo in enumerate(combos):
                    upsert("payouts", {
                        "race_id":     race_id,
                        "bet_type":    bt,
                        "combination": combo,
                        "payout":      int(amounts[i].replace(",", "").replace("円", "")) if i < len(amounts) else 0,
                        "popularity":  int(pops[i].replace("番人気", "")) if i < len(pops) else None,
                    })
            except Exception as e:
                logger.warning(f"payout parse error: {race_id} -> {e}")

    logger.info(f"done: {race_id}")


def scrape_date_range(start: str, end: str) -> None:
    logger.info("Initializing session...")
    session.get(f"{BASE_RACE}/", timeout=20)
    time.sleep(3)

    current = datetime.strptime(start, "%Y%m%d")
    end_dt  = datetime.strptime(end,   "%Y%m%d")
    while current <= end_dt:
        ds = current.strftime("%Y%m%d")
        logger.info(f"=== {ds} ===")
        race_ids = get_race_id_list(ds)
        logger.info(f"  {len(race_ids)} races found")
        for race_id in race_ids:
            scrape_race(race_id)
        current += timedelta(days=1)


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3:
        scrape_date_range(sys.argv[1], sys.argv[2])
    else:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        scrape_date_range(yesterday, yesterday)
