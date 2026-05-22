# データベース設計書
## Supabase（PostgreSQL）テーブル定義

---

## 1. テーブル構成概要

| テーブル名 | 論理名 | 概要 |
|------------|--------|------|
| venues | 競馬場マスタ | JRA10場のコードと名称 |
| races | レース | レースの基本情報（日付・距離・馬場など） |
| race_entries | 出走・結果 | 各レースの出走馬・着順・オッズなど |
| horses | 競走馬 | 馬のプロフィール情報 |
| horse_pedigrees | 血統 | 父・母・祖父母の血統情報 |
| jockeys | 騎手 | 騎手の基本情報 |
| trainers | 調教師 | 調教師の基本情報 |
| payouts | 払い戻し | 単勝・複勝・馬連など払い戻し情報 |

### リレーション
```
venues ← races ← race_entries → horses → horse_pedigrees
                              → jockeys
                              → trainers
         races ← payouts
```

---

## 2. テーブル定義

### venues（競馬場マスタ）
| カラム名 | 型 | 制約 | 説明 |
|----------|-----|------|------|
| venue_code | VARCHAR(2) | PK | 競馬場コード（01〜10） |
| venue_name | VARCHAR(20) | NOT NULL | 競馬場名（例：東京、中山） |

### races（レース）
| カラム名 | 型 | 制約 | 説明 |
|----------|-----|------|------|
| race_id | VARCHAR(12) | PK | レースID（YYYYPPNNDDRR形式） |
| race_date | DATE | NOT NULL | 開催日 |
| race_name | VARCHAR(100) | | レース名 |
| venue_code | VARCHAR(2) | FK→venues | 競馬場コード |
| race_number | INT | | レース番号（1〜12） |
| distance | INT | | 距離（m） |
| surface | VARCHAR(10) | | 芝 / ダート |
| direction | VARCHAR(5) | | 右 / 左 / 直線 |
| weather | VARCHAR(10) | | 天候 |
| track_condition | VARCHAR(10) | | 馬場状態（良/稍重/重/不良） |
| class | VARCHAR(50) | | クラス（G1/G2/G3/OP/条件戦など） |
| prize_1st | INT | | 1着賞金（万円） |

### race_entries（出走・結果）
| カラム名 | 型 | 制約 | 説明 |
|----------|-----|------|------|
| entry_id | SERIAL | PK | 自動採番ID |
| race_id | VARCHAR(12) | FK→races | レースID |
| horse_id | VARCHAR(10) | FK→horses | 馬ID |
| jockey_id | VARCHAR(10) | FK→jockeys | 騎手ID |
| trainer_id | VARCHAR(10) | FK→trainers | 調教師ID |
| post_position | INT | | 枠番（1〜8） |
| horse_number | INT | | 馬番 |
| finish_position | INT | | 着順（除外・取消はNULL） |
| finish_time | VARCHAR(10) | | タイム（例：1:33.5） |
| margin | VARCHAR(20) | | 着差 |
| corner_position | VARCHAR(50) | | コーナー通過順 |
| last_3f | DECIMAL(4,1) | | 上がり3F（秒） |
| odds | DECIMAL(6,1) | | 単勝オッズ |
| popularity | INT | | 人気順 |
| weight | INT | | 馬体重（kg） |
| weight_diff | INT | | 馬体重増減（前走比） |
| age | INT | | 馬齢 |
| burden_weight | DECIMAL(4,1) | | 斤量（kg） |

### horses（競走馬）
| カラム名 | 型 | 制約 | 説明 |
|----------|-----|------|------|
| horse_id | VARCHAR(10) | PK | 馬ID |
| horse_name | VARCHAR(50) | NOT NULL | 馬名 |
| birth_date | DATE | | 生年月日 |
| sex | VARCHAR(5) | | 性別（牡/牝/騸） |
| coat_color | VARCHAR(20) | | 毛色 |

### horse_pedigrees（血統）
| カラム名 | 型 | 制約 | 説明 |
|----------|-----|------|------|
| horse_id | VARCHAR(10) | PK/FK→horses | 馬ID |
| father | VARCHAR(50) | | 父（種牡馬） |
| mother | VARCHAR(50) | | 母 |
| father_father | VARCHAR(50) | | 父の父 |
| father_mother | VARCHAR(50) | | 父の母 |
| mother_father | VARCHAR(50) | | 母の父（BMS） |
| mother_mother | VARCHAR(50) | | 母の母 |

### jockeys（騎手）
| カラム名 | 型 | 制約 | 説明 |
|----------|-----|------|------|
| jockey_id | VARCHAR(10) | PK | 騎手ID |
| jockey_name | VARCHAR(50) | NOT NULL | 騎手名 |
| belong | VARCHAR(50) | | 所属（美浦/栗東など） |

### trainers（調教師）
| カラム名 | 型 | 制約 | 説明 |
|----------|-----|------|------|
| trainer_id | VARCHAR(10) | PK | 調教師ID |
| trainer_name | VARCHAR(50) | NOT NULL | 調教師名 |
| belong | VARCHAR(50) | | 所属 |

### payouts（払い戻し）
| カラム名 | 型 | 制約 | 説明 |
|----------|-----|------|------|
| payout_id | SERIAL | PK | 自動採番ID |
| race_id | VARCHAR(12) | FK→races | レースID |
| bet_type | VARCHAR(10) | NOT NULL | 券種（単勝/複勝/馬連/馬単/ワイド/3連複/3連単） |
| combination | VARCHAR(20) | NOT NULL | 組み合わせ（例：1、1-3、1-3-7） |
| payout | INT | NOT NULL | 払い戻し金額（円） |
| popularity | INT | | 何番人気 |

---

## 3. インデックス

| インデックス名 | テーブル | カラム |
|----------------|----------|--------|
| idx_races_date | races | race_date |
| idx_races_venue | races | venue_code |
| idx_entries_race | race_entries | race_id |
| idx_entries_horse | race_entries | horse_id |
| idx_entries_jockey | race_entries | jockey_id |
| idx_payouts_race | payouts | race_id |
| idx_payouts_type | payouts | bet_type |

---

## 4. race_id の構造

```
例：202505010501
    ^^^^          → 2025年
        ^^        → 05（東京）
          ^^      → 01（第1回開催）
            ^^    → 05（5日目）
              ^^  → 01（1R）
```

| 桁 | 意味 | 例 |
|----|------|----|
| 1〜4 | 年 | 2025 |
| 5〜6 | 競馬場コード | 05=東京 |
| 7〜8 | 開催回次 | 01 |
| 9〜10 | 開催日次 | 05 |
| 11〜12 | レース番号 | 01〜12 |

---

## 5. 競馬場コード一覧

| コード | 競馬場 | コード | 競馬場 |
|--------|--------|--------|--------|
| 01 | 札幌 | 06 | 中山 |
| 02 | 函館 | 07 | 中京 |
| 03 | 福島 | 08 | 京都 |
| 04 | 新潟 | 09 | 阪神 |
| 05 | 東京 | 10 | 小倉 |
