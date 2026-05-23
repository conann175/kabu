"""
netkeiba.com スクレイピング -> Supabase
db.netkeiba.com を使用（ローカルPC実行用）
使い方: python scraper.py 20260517 20260518
"""

import os, re, time, logging, random, sys
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup
from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://infypumigexmpdmijhnx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

BASE_DB   = "https://db.netkeiba.com"
BASE_RACE = "https://race.netkeiba.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
    "Referer": "https://db.netkeiba.com/",
}

session = requests.Session()
session.headers.update(HEADERS)


def fetch(url):
    try:
        time.sleep(random.uniform(1, 3))
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


def upsert(table, data):
    try:
        supabase.table(table).upsert(data).execute()
    except Exception as e:
        logger.error(f"upsert {table}: {e}")


def ensure_horse(horse_id, horse_name=""):
    """馬がhorsesテーブルに存在することを保証する"""
    global known_horse_ids
    try:
        if horse_id in known_horse_ids:
            return  # キャッシュで確認済み

        # db.netkeiba.comから詳細取得を試みる
        url = f"{BASE_DB}/horse/{horse_id}/"
        soup = fetch(url)
        if soup:
            profile = {}
            tbl = soup.find("table", class_="db_prof_table")
            if tbl:
                for tr in tbl.find_all("tr"):
                    th = tr.find("th")
                    td = tr.find("td")
                    if th and td:
                        profile[th.text.strip()] = td.text.strip()
            title = soup.find("div", class_="horse_title")
            name = title.find("h1").text.strip() if title else horse_name
            known_horse_ids.add(horse_id)
            upsert("horses", {
                "horse_id":   horse_id,
                "horse_name": name,
                "birth_date": (lambda d: (lambda m: f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}" if m else None)(re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", d)) if d else None)(profile.get("生年月日")),
                "sex":        profile.get("性別"),
                "coat_color": profile.get("毛色"),
            })
            # 血統
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
        else:
            # 取得失敗でも最低限馬名だけ登録してFK違反を防ぐ
            known_horse_ids.add(horse_id)
            upsert("horses", {"horse_id": horse_id, "horse_name": horse_name})
    except Exception as e:
        logger.error(f"ensure_horse error: {horse_id} -> {e}")
        # 最後の手段：馬名だけで登録
        try:
            upsert("horses", {"horse_id": horse_id, "horse_name": horse_name})
        except Exception:
            pass


def get_race_id_list(date):
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


def scrape_race(race_id, kaisai_date=None):
    url = f"{BASE_DB}/race/{race_id}/"
    soup = fetch(url)
    if not soup:
        return

    race_date = f"{kaisai_date[0:4]}-{kaisai_date[4:6]}-{kaisai_date[6:8]}" if kaisai_date else None

    race_data = {
        "race_id":     race_id,
        "venue_code":  race_id[4:6],
        "race_number": int(race_id[10:12]),
        "race_date":   race_date,
    }

    head = soup.find("div", class_="race_head_inner") or soup.find("div", class_="mainrace_data")
    if head:
        h1 = head.find("h1")
        if h1:
            race_data["race_name"] = h1.text.strip()

    ct = soup.find("div", class_="race_data")
    if ct:
        t = ct.text
        if "芝" in t:    race_data["surface"] = "芝"
        elif "ダ" in t:  race_data["surface"] = "ダート"
        elif "障" in t:  race_data["surface"] = "障害"
        if "右" in t:    race_data["direction"] = "右"
        elif "左" in t:  race_data["direction"] = "左"
        m = re.search(r"(\d+)m", t)
        if m: race_data["distance"] = int(m.group(1))
        mw = re.search(r"天候:(\S+)", t)
        if mw: race_data["weather"] = mw.group(1)
        mt = re.search(r"馬場:(\S+)", t)
        if mt: race_data["track_condition"] = mt.group(1)

    upsert("races", race_data)

    rt = soup.find("table", class_="race_table_01")
    if rt:
        for tr in rt.find_all("tr")[1:]:
            cols = tr.find_all("td")
            if len(cols) < 10:
                continue
            try:
                # リンクを安全に取得
                horse_link   = tr.find("a", href=re.compile(r"/horse/"))
                jockey_link  = tr.find("a", href=re.compile(r"/jockey/"))
                trainer_link = tr.find("a", href=re.compile(r"/trainer/"))

                horse_id   = re.search(r"/horse/(\w+)",   horse_link["href"]).group(1)   if horse_link   else None
                jockey_id  = re.search(r"/jockey/(\w+)",  jockey_link["href"]).group(1)  if jockey_link  else None
                trainer_id = re.search(r"/trainer/(\w+)", trainer_link["href"]).group(1) if trainer_link else None
                horse_name  = horse_link.text.strip()  if horse_link  else ""
                jockey_name = jockey_link.text.strip()  if jockey_link else ""
                trainer_name= trainer_link.text.strip() if trainer_link else ""

                if jockey_id:
                    upsert("jockeys", {"jockey_id": jockey_id, "jockey_name": jockey_name})
                if trainer_id:
                    upsert("trainers", {"trainer_id": trainer_id, "trainer_name": trainer_name})

                # FK違反防止：必ず馬を先に登録
                if horse_id:
                    ensure_horse(horse_id, horse_name)

                fp = cols[0].text.strip()
                upsert("race_entries", {
                    "race_id":         race_id,
                    "horse_id":        horse_id,
                    "jockey_id":       jockey_id,
                    "trainer_id":      trainer_id,
                    "post_position":   int(cols[1].text.strip()) if cols[1].text.strip().isdigit() else None,
                    "horse_number":    int(cols[2].text.strip()) if cols[2].text.strip().isdigit() else None,
                    "finish_position": int(fp) if fp.isdigit() else None,
                    "finish_time":     cols[8].text.strip() if len(cols) > 8 else None,
                    "margin":          cols[9].text.strip() if len(cols) > 9 else None,
                    "odds":            float(cols[12].text.strip()) if len(cols) > 12 and re.match(r"[\d.]+$", cols[12].text.strip()) else None,
                    "popularity":      int(cols[13].text.strip()) if len(cols) > 13 and cols[13].text.strip().isdigit() else None,
                    "weight":          int(re.search(r"(\d+)", cols[14].text).group(1)) if len(cols) > 14 and re.search(r"(\d+)", cols[14].text) else None,
                    "weight_diff":     int(re.search(r"[+-]?\d+", cols[14].text.replace("(","").replace(")","")).group()) if len(cols) > 14 and re.search(r"[+-]?\d+", cols[14].text) else None,
                })
            except Exception as e:
                logger.warning(f"entry error: {race_id} -> {e}")

    for pt in soup.find_all("table", class_="pay_table_01"):
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
                logger.warning(f"payout error: {race_id} -> {e}")

    logger.info(f"done: {race_id} / date: {race_date}")


# 登録済み馬IDのキャッシュ（Supabaseへの問い合わせ回数を削減）
known_horse_ids = set()

def load_known_horses():
    """起動時に登録済み馬IDを全件読み込む"""
    global known_horse_ids
    logger.info("Loading known horses from Supabase...")
    offset = 0
    while True:
        res = supabase.table("horses").select("horse_id").range(offset, offset + 999).execute()
        if not res.data:
            break
        for row in res.data:
            known_horse_ids.add(row["horse_id"])
        offset += 1000
        if len(res.data) < 1000:
            break
    logger.info(f"Loaded {len(known_horse_ids)} known horses")


def scrape_date_range(start, end):
    logger.info("Initializing session...")
    session.get(f"{BASE_DB}/", timeout=20)
    time.sleep(2)
    load_known_horses()

    current = datetime.strptime(start, "%Y%m%d")
    end_dt  = datetime.strptime(end,   "%Y%m%d")
    total   = (end_dt - current).days + 1
    done    = 0

    while current <= end_dt:
        ds = current.strftime("%Y%m%d")
        race_ids = get_race_id_list(ds)
        logger.info(f"=== {ds} | {len(race_ids)}レース | {done}/{total}日完了 ===")
        for race_id in race_ids:
            scrape_race(race_id, ds)
        done += 1
        current += timedelta(days=1)

    logger.info("完了！")


if __name__ == "__main__":
    if len(sys.argv) == 3:
        scrape_date_range(sys.argv[1], sys.argv[2])
    else:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        scrape_date_range(yesterday, yesterday)
