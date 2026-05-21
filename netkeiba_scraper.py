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

supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"],
)

BASE_DB   = "https://db.netkeiba.com"
BASE_RACE = "https://race.netkeiba.com"
HEADERS   = {"User-Agent": "Mozilla/5.0"}
INTERVAL  = 2

BET_TYPES = ["単勝", "複勝", "枠連", "馬連", "馬単", "ワイド", "3連複", "3連単"]


def fetch(url: str):
    try:
        time.sleep(INTERVAL)
        res = requests.get(url, headers=HEADERS, timeout=15)
        res.encoding = "EUC-JP"
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


def scrape_horse(horse_id: str) -> None:
    url = f"{BASE_DB}/horse/{horse_id}/"
    soup = fetch(url)
    if not soup:
        return
    profile = {}
    tbl = soup.find("table", class_="db_prof_table")
    if tbl:
        for tr in tbl.find_all("tr"):
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
    ped = soup.find("table", class_="blood_table")
    if ped:
        cells = [td.text.strip() for td in ped.find_all("td")]
        if len(cells) >= 6:
            upsert("horse_pedigrees", {
                "horse_id":      horse_id,
                "father":        cells[0], "father_father": cells[1],
                "father_mother": cells[2], "mother":        cells[3],
                "mother_father": cells[4], "mother_mother": cells[5],
            })


def scrape_race(race_id: str) -> None:
    url = f"{BASE_DB}/race/{race_id}/"
    soup = fetch(url)
    if not soup:
        return
    race_data = {"race_id": race_id, "venue_code": race_id[4:6]}
    try:
        head = soup.find("div", class_="race_head_inner") or soup.find("div", class_="mainrace_data")
        race_data["race_name"] = head.find("h1").text.strip() if head else ""
        intro = soup.find("div", class_="data_intro")
        if intro:
            for p in intro.find_all("p"):
                t = p.text.strip()
                m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", t)
                if m:
                    race_data["race_date"] = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
                m2 = re.search(r"(\d+)R", t)
                if m2:
                    race_data["race_number"] = int(m2.group(1))
        ct = soup.find("div", class_="race_data")
        if ct:
            t = ct.text
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
    except Exception as e:
        logger.warning(f"race info parse error: {race_id} -> {e}")
    upsert("races", race_data)
    rt = soup.find("table", class_="race_table_01")
    if rt:
        for tr in rt.find_all("tr")[1:]:
            cols = tr.find_all("td")
            if len(cols) < 10:
                continue
            try:
                hh = cols[3].find("a")["href"] if cols[3].find("a") else ""
                jh = cols[6].find("a")["href"] if cols[6].find("a") else ""
                th2 = cols[7].find("a")["href"] if cols[7].find("a") else ""
                hid = re.search(r"/horse/(\w+)", hh)
                jid = re.search(r"/jockey/(\w+)", jh)
                tid = re.search(r"/trainer/(\w+)", th2)
                horse_id   = hid.group(1) if hid else None
                jockey_id  = jid.group(1) if jid else None
                trainer_id = tid.group(1) if tid else None
                if jockey_id:
                    upsert("jockeys", {"jockey_id": jockey_id, "jockey_name": cols[6].text.strip()})
                if trainer_id:
                    upsert("trainers", {"trainer_id": trainer_id, "trainer_name": cols[7].text.strip()})
                if horse_id:
                    ex = supabase.table("horses").select("horse_id").eq("horse_id", horse_id).execute()
                    if not ex.data:
                        scrape_horse(horse_id)
                fp = cols[0].text.strip()
                upsert("race_entries", {
                    "race_id": race_id, "horse_id": horse_id,
                    "jockey_id": jockey_id, "trainer_id": trainer_id,
                    "post_position":   int(cols[1].text.strip()) if cols[1].text.strip().isdigit() else None,
                    "horse_number":    int(cols[2].text.strip()) if cols[2].text.strip().isdigit() else None,
                    "finish_position": int(fp) if fp.isdigit() else None,
                    "finish_time":     cols[8].text.strip() if len(cols) > 8 else None,
                    "margin":          cols[9].text.strip() if len(cols) > 9 else None,
                    "odds":            float(cols[12].text.strip()) if len(cols) > 12 and cols[12].text.strip().replace(".", "").isdigit() else None,
                    "popularity":      int(cols[13].text.strip()) if len(cols) > 13 and cols[13].text.strip().isdigit() else None,
                })
            except Exception as e:
                logger.warning(f"entry parse error: {race_id} -> {e}")
    for pt in soup.find_all("table", class_="pay_table_01"):
        for tr in pt.find_all("tr"):
            cols = tr.find_all("td")
            if len(cols) < 3:
                continue
            try:
                bt = cols[0].text.strip()
                combos  = [s.strip() for s in cols[1].text.strip().split("\n") if s.strip()]
                amounts = [s.strip() for s in cols[2].text.strip().split("\n") if s.strip()]
                pops    = [s.strip() for s in cols[3].text.strip().split("\n") if s.strip()] if len(cols) > 3 else []
                for i, combo in enumerate(combos):
                    upsert("payouts", {
                        "race_id": race_id, "bet_type": bt, "combination": combo,
                        "payout": int(amounts[i].replace(",", "").replace("円", "")) if i < len(amounts) else 0,
                        "popularity": int(pops[i].replace("番人気", "")) if i < len(pops) else None,
                    })
            except Exception as e:
                logger.warning(f"payout parse error: {race_id} -> {e}")
    logger.info(f"done: {race_id}")


def scrape_date_range(start: str, end: str) -> None:
    current = datetime.strptime(start, "%Y%m%d")
    end_dt  = datetime.strptime(end,   "%Y%m%d")
    while current <= end_dt:
        ds = current.strftime("%Y%m%d")
        logger.info(f"=== {ds} ===")
        for race_id in get_race_id_list(ds):
            scrape_race(race_id)
        current += timedelta(days=1)


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3:
        scrape_date_range(sys.argv[1], sys.argv[2])
    else:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        scrape_date_range(yesterday, yesterday)
