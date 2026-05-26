"""
血統データ専用取得スクリプト（並列処理版）
使い方: python fetch_pedigree.py
"""

import os, re, time, logging, random
import requests
from bs4 import BeautifulSoup
from supabase import create_client
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://infypumigexmpdmijhnx.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
WORKERS = 3  # 同時リクエスト数（増やすとブロックリスク上がる）

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
BASE_DB = "https://db.netkeiba.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
    "Referer": "https://db.netkeiba.com/",
}


def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def fetch(session, url):
    try:
        time.sleep(random.uniform(1, 2))
        res = session.get(url, timeout=20)
        res.encoding = "EUC-JP"
        if res.status_code in (403, 404):
            return None
        res.raise_for_status()
        return BeautifulSoup(res.text, "html.parser")
    except Exception as e:
        logger.error(f"fetch error: {url} -> {e}")
        return None


def clean(td):
    if td:
        return td.text.strip().split("\n")[0].strip()
    return None


def get_pedigree(horse_id):
    session = make_session()
    soup = fetch(session, f"{BASE_DB}/horse/ped/{horse_id}/")
    if not soup:
        return None
    ped = soup.find("table", class_="blood_table")
    if not ped:
        return None
    rows = ped.find_all("tr")
    if len(rows) < 32:
        return None

    r0  = rows[0].find_all("td")
    r8  = rows[8].find_all("td")
    r16 = rows[16].find_all("td")
    r24 = rows[24].find_all("td")

    return {
        "horse_id":      horse_id,
        "father":        clean(r0[0])  if len(r0)  > 0 else None,
        "father_father": clean(r0[1])  if len(r0)  > 1 else None,
        "father_mother": clean(r8[0])  if len(r8)  > 0 else None,
        "mother":        clean(r16[0]) if len(r16) > 0 else None,
        "mother_father": clean(r16[1]) if len(r16) > 1 else None,
        "mother_mother": clean(r24[0]) if len(r24) > 0 else None,
    }


def process_horse(horse_id):
    ped = get_pedigree(horse_id)
    if ped:
        try:
            supabase.table("horse_pedigrees").upsert(ped).execute()
            return "ok"
        except Exception as e:
            logger.error(f"upsert error: {horse_id} -> {e}")
            return "error"
    return "skip"


def main():
    # 登録済み血統を取得
    logger.info("登録済み血統を確認中...")
    done_ids = set()
    offset = 0
    while True:
        res = supabase.table("horse_pedigrees").select("horse_id").range(offset, offset+999).execute()
        if not res.data:
            break
        for row in res.data:
            done_ids.add(row["horse_id"])
        offset += 1000
        if len(res.data) < 1000:
            break
    logger.info(f"血統登録済み: {len(done_ids)}頭")

    # 全馬IDを取得
    logger.info("全馬リストを取得中...")
    all_horses = []
    offset = 0
    while True:
        res = supabase.table("horses").select("horse_id").range(offset, offset+999).execute()
        if not res.data:
            break
        all_horses.extend([r["horse_id"] for r in res.data])
        offset += 1000
        if len(res.data) < 1000:
            break

    todo = [h for h in all_horses if h not in done_ids]
    logger.info(f"血統未取得: {len(todo)}頭 → {WORKERS}並列で取得開始")

    success = 0
    errors  = 0
    total   = len(todo)

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(process_horse, hid): hid for hid in todo}
        for i, future in enumerate(as_completed(futures)):
            result = future.result()
            if result == "ok":
                success += 1
            elif result == "error":
                errors += 1
            if (i + 1) % 100 == 0:
                logger.info(f"進捗: {i+1}/{total}頭 (成功:{success} エラー:{errors})")

    logger.info(f"✅ 完了！ 成功:{success} エラー:{errors} / {total}頭")


if __name__ == "__main__":
    main()
