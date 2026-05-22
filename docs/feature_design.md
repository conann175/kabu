# 特徴量エンジニアリング設計書

---

## 1. 概要

生データ（races・race_entriesなど）から機械学習モデルの入力となる特徴量を算出します。
特徴量はSQLのVIEWとして定義し、モデル学習時にColabから参照します。

---

## 2. 特徴量一覧

### レース条件（固定情報）
| 特徴量名 | 型 | 算出方法 | 優先度 |
|----------|-----|----------|--------|
| venue_code | VARCHAR | races.venue_code そのまま | 高 |
| distance | INT | races.distance そのまま | 高 |
| surface | VARCHAR | races.surface（芝/ダート） | 高 |
| track_condition | VARCHAR | races.track_condition | 高 |
| class | VARCHAR | races.class | 高 |
| weather | VARCHAR | races.weather | 中 |
| post_position | INT | race_entries.post_position | 中 |
| entry_count | INT | 同race_idの出走頭数をCOUNT | 中 |

### 馬の過去成績（集計値）
| 特徴量名 | 型 | 算出方法 | 優先度 |
|----------|-----|----------|--------|
| horse_avg_finish_3 | FLOAT | 過去3走の着順平均 | 高 |
| horse_avg_finish_5 | FLOAT | 過去5走の着順平均 | 高 |
| horse_fukusho_rate_3 | FLOAT | 過去3走の複勝率 | 高 |
| horse_fukusho_rate_5 | FLOAT | 過去5走の複勝率 | 高 |
| horse_fukusho_distance | FLOAT | 同距離±200m以内での複勝率 | 高 |
| horse_fukusho_surface | FLOAT | 同芝/ダートでの複勝率 | 高 |
| horse_fukusho_venue | FLOAT | 同開催場所での複勝率 | 中 |
| horse_last_finish | INT | 前走の着順 | 高 |
| horse_rest_days | INT | 前走からの間隔（日数） | 中 |
| horse_distance_diff | INT | 前走との距離差 | 中 |
| horse_weight | INT | 馬体重（kg） | 中 |
| horse_weight_diff | INT | 前走比増減 | 中 |

### 騎手（集計値）
| 特徴量名 | 型 | 算出方法 | 優先度 |
|----------|-----|----------|--------|
| jockey_fukusho_rate | FLOAT | 全体複勝率 | 高 |
| jockey_fukusho_distance | FLOAT | 距離±200m以内の複勝率 | 中 |
| jockey_fukusho_venue | FLOAT | 開催場所別複勝率 | 中 |
| jockey_fukusho_surface | FLOAT | 芝/ダート別複勝率 | 中 |
| jockey_fukusho_condition | FLOAT | 馬場状態別複勝率 | 中 |

### 調教師（集計値）
| 特徴量名 | 型 | 算出方法 | 優先度 |
|----------|-----|----------|--------|
| trainer_fukusho_rate | FLOAT | 全体複勝率 | 中 |
| trainer_fukusho_distance | FLOAT | 距離別複勝率 | 低 |

### 血統（集計値）
| 特徴量名 | 型 | 算出方法 | 優先度 |
|----------|-----|----------|--------|
| father_fukusho_rate | FLOAT | 父産駒の全体複勝率 | 中 |
| father_fukusho_distance | FLOAT | 父産駒×距離の複勝率 | 中 |
| father_fukusho_surface | FLOAT | 父産駒×芝/ダートの複勝率 | 中 |
| mother_father_fukusho_rate | FLOAT | 母父産駒の全体複勝率 | 低 |

### オッズ・人気
| 特徴量名 | 型 | 算出方法 | 優先度 |
|----------|-----|----------|--------|
| odds | FLOAT | race_entries.odds | 高 |
| popularity | INT | race_entries.popularity | 高 |
| popularity_ratio | FLOAT | 人気順 ÷ 出走頭数 | 高 |

---

## 3. SQLビュー定義

詳細は `feature_views.sql` を参照。

### ビュー一覧
| ビュー名 | 概要 |
|----------|------|
| v_jockey_stats | 騎手×場所×馬場×芝ダートの複勝率 |
| v_trainer_stats | 調教師×芝ダート×距離の複勝率 |
| v_father_stats | 父産駒×芝ダート×距離の複勝率 |
| v_mother_father_stats | 母父産駒×芝ダートの複勝率 |
| v_horse_recent_stats | 馬の直近5走の成績集計 |
| v_entry_count | レースごとの出走頭数 |
| v_features | 全特徴量を結合したメインビュー（モデル学習用） |

---

## 4. 注意事項

- **データリーク防止**：特徴量の集計は必ず「対象レース開催日より前」のデータのみを使用
- **最小サンプル数**：集計件数が10件未満の場合はNULLとして扱いモデル側で補完
- **障害レース除外**：`surface = '障害'` を除外条件に必ず含める
- **最低データ量**：2〜3年分（約14,000レース）以上揃ってからモデル学習を開始
