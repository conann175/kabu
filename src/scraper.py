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
            # 血統（別URL /horse/ped/{horse_id}/ から取得）
            ped_soup = fetch(f"{BASE_DB}/horse/ped/{horse_id}/")
            if ped_soup:
                ped = ped_soup.find("table", class_="blood_table")
                if ped:
                    rows = ped.find_all("tr")

                    def clean(td):
                        """td内の馬名（最初の行）を取得"""
                        return td.text.strip().split("\n")[0].strip() if td else None

                    # 5代血統表（32行）：各行の最初のtdが世代の起点
                    # 行0=父, 行8=父母, 行16=母, 行24=母母
                    ped_data = {"horse_id": horse_id}
                    if len(rows) >= 32:
                        # 父：行0の1番目、父父：行0の2番目
                        r0 = rows[0].find_all("td")
                        ped_data["father"]        = clean(r0[0]) if len(r0) > 0 else None
                        ped_data["father_father"] = clean(r0[1]) if len(r0) > 1 else None
                        # 父母：行8の1番目
                        r8 = rows[8].find_all("td")
                        ped_data["father_mother"] = clean(r8[0]) if len(r8) > 0 else None
                        # 母：行16の1番目、母父：行16の2番目
                        r16 = rows[16].find_all("td")
                        ped_data["mother"]        = clean(r16[0]) if len(r16) > 0 else None
                        ped_data["mother_father"] = clean(r16[1]) if len(r16) > 1 else None
                        # 母母：行24の1番目
                        r24 = rows[24].find_all("td")
                        ped_data["mother_mother"] = clean(r24[0]) if len(r24) > 0 else None
                        upsert("horse_pedigrees", ped_data)
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

    # コース情報：data_intro / racedata / race_data のいずれか
    ct = (soup.find("div", class_="data_intro")
          or soup.find("div", class_="racedata")
          or soup.find("div", class_="race_data"))
    if ct:
        # race_nameが未取得ならdata_introのh1から取得
        if "race_name" not in race_data:
            h1c = ct.find("h1")
            if h1c:
                race_data["race_name"] = h1c.text.strip()
        t = ct.text
        if "芝" in t:    race_data["surface"] = "芝"
        elif "ダ" in t:  race_data["surface"] = "ダート"
        elif "障" in t:  race_data["surface"] = "障害"
        if "右" in t:    race_data["direction"] = "右"
        elif "左" in t:  race_data["direction"] = "左"
        m = re.search(r"(\d+)m", t)
        if m: race_data["distance"] = int(m.group(1))
        # 「天候 : 晴」「天候:晴」両対応
        mw = re.search(r"天候\s*[:：]\s*(\S+)", t)
        if mw: race_data["weather"] = mw.group(1)
        # 「ダート : 良」「芝 : 良」「馬場 : 良」など、状態を示す語の後ろ
        mt = re.search(r"(?:ダート|芝|馬場|障)\s*[:：]\s*(良|稍重|重|不良)", t)
        if mt: race_data["track_condition"] = mt.group(1)
        # レース番号
        mr = re.search(r"(\d+)\s*R", t)
        if mr: race_data["race_number"] = int(mr.group(1))

    # クラス抽出（race_name + コース情報テキストを両方検索）
    search_text = race_data.get("race_name", "")
    if ct:
        search_text += " " + t
    for pattern, label in [
        (r"G[1１Ⅰ]", "G1"), (r"G[2２Ⅱ]", "G2"), (r"G[3３Ⅲ]", "G3"),
        (r"リステッド",   "Listed"),
        (r"新馬",        "新馬"),
        (r"未勝利",      "未勝利"),
        (r"1勝クラス",   "1勝クラス"),
        (r"2勝クラス",   "2勝クラス"),
        (r"3勝クラス",   "3勝クラス"),
        (r"オープン",    "オープン"),
        (r"重賞",        "重賞"),
    ]:
        if re.search(pattern, search_text):
            race_data["class"] = label
            break

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
                # 実際のカラム構造：
                # [0]着順 [1]枠 [2]馬番 [3]馬名 [4]性齢 [5]斤量 [6]騎手
                # [7]タイム [8]着差 [9-13]通過/上り等 [14]通過 [15]上り
                # [16]オッズ [17]人気 [18]馬体重(増減) [19-21]空 [22]調教師 [23]馬主
                weight_text = cols[18].text.strip() if len(cols) > 18 else ""
                upsert("race_entries", {
                    "race_id":         race_id,
                    "horse_id":        horse_id,
                    "jockey_id":       jockey_id,
                    "trainer_id":      trainer_id,
                    "post_position":   int(cols[1].text.strip()) if cols[1].text.strip().isdigit() else None,
                    "horse_number":    int(cols[2].text.strip()) if cols[2].text.strip().isdigit() else None,
                    "finish_position": int(fp) if fp.isdigit() else None,
                    "finish_time":     cols[7].text.strip() if len(cols) > 7 else None,
                    "margin":          cols[8].text.strip() if len(cols) > 8 else None,
                    "odds":            float(cols[16].text.strip()) if len(cols) > 16 and re.match(r"[\d.]+$", cols[16].text.strip()) else None,
                    "popularity":      int(cols[17].text.strip()) if len(cols) > 17 and cols[17].text.strip().isdigit() else None,
                    "weight":          int(re.search(r"(\d+)", weight_text).group(1)) if re.search(r"(\d+)", weight_text) else None,
                    "weight_diff":     int(re.search(r"\(([+-]?\d+)\)", weight_text).group(1)) if re.search(r"\(([+-]?\d+)\)", weight_text) else None,
                })
            except Exception as e:
                logger.warning(f"entry error: {race_id} -> {e}")

    def get_br_texts(td):
        """tdタグ内の全テキストノードをリストで返す（br入れ子構造に対応）"""
        return [s for s in td.stripped_strings]

    pay_tables = soup.find_all("table", class_="pay_table_01")
    if pay_tables:
        # 既存payoutsを削除してから再挿入（重複防止）
        try:
            supabase.table('payouts').delete().eq('race_id', race_id).execute()
        except Exception as e:
            logger.warning(f"payouts delete error: {race_id} -> {e}")
    current_bet_type = None
    for pt in pay_tables:
        for tr in pt.find_all("tr"):
            th = tr.find("th")
            if th and th.text.strip():  # 空<th>（複勝2〜3行目等）はbet_typeを引き継ぐ
                current_bet_type = th.text.strip()
            cols = tr.find_all("td")
            if len(cols) < 2 or not current_bet_type:
                continue
            try:
                combos  = get_br_texts(cols[0])
                amounts = get_br_texts(cols[1])
                pops    = get_br_texts(cols[2]) if len(cols) > 2 else []
                for i, combo in enumerate(combos):
                    upsert("payouts", {
                        "race_id":     race_id,
                        "bet_type":    current_bet_type,
                        "combination": combo,
                        "payout":      int(amounts[i].replace(",", "").replace("円", "")) if i < len(amounts) else 0,
                        "popularity":  int(pops[i].replace("番人気", "")) if i < len(pops) else None,
                    })
            except Exception as e:
                logger.warning(f"payout error: {race_id} -> {e}")

    logger.info(f"done: {race_id} / date: {race_date} / R{int(race_id[10:12])}")


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
        logger.info(f"=== {ds} | {len(race_ids)}レース | {done+1}/{total}日目 ===")
        for i, race_id in enumerate(sorted(race_ids)):
            scrape_race(race_id, ds)
            logger.info(f"  → {i+1}/{len(race_ids)}レース完了 ({ds})")
        done += 1
        current += timedelta(days=1)

    logger.info("完了！")


if __name__ == "__main__":
    if len(sys.argv) == 3:
        scrape_date_range(sys.argv[1], sys.argv[2])
    else:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        scrape_date_range(yesterday, yesterday)
