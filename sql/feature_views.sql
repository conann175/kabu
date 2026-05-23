-- ============================================================
-- 競馬予測システム 特徴量エンジニアリング SQLビュー定義
-- 対象：JRA平地レース（障害除外）
-- DB：Supabase (PostgreSQL)
-- ニックス・母父詳細ビュー追加済み
-- ============================================================

-- 1. 騎手成績ビュー
CREATE OR REPLACE VIEW v_jockey_stats AS
SELECT e.jockey_id, r.venue_code, r.surface, r.track_condition,
    ROUND(AVG(CASE WHEN e.finish_position <= 3 THEN 1.0 ELSE 0.0 END)::NUMERIC, 4) AS fukusho_rate,
    COUNT(*) AS total_races
FROM race_entries e JOIN races r ON e.race_id = r.race_id
WHERE e.finish_position IS NOT NULL AND r.surface != '障害'
GROUP BY e.jockey_id, r.venue_code, r.surface, r.track_condition;

-- 2. 調教師成績ビュー
CREATE OR REPLACE VIEW v_trainer_stats AS
SELECT e.trainer_id, r.surface, r.distance,
    ROUND(AVG(CASE WHEN e.finish_position <= 3 THEN 1.0 ELSE 0.0 END)::NUMERIC, 4) AS fukusho_rate,
    COUNT(*) AS total_races
FROM race_entries e JOIN races r ON e.race_id = r.race_id
WHERE e.finish_position IS NOT NULL AND r.surface != '障害'
GROUP BY e.trainer_id, r.surface, r.distance;

-- 3. 父産駒成績ビュー
CREATE OR REPLACE VIEW v_father_stats AS
SELECT p.father, r.surface, r.distance,
    ROUND(AVG(CASE WHEN e.finish_position <= 3 THEN 1.0 ELSE 0.0 END)::NUMERIC, 4) AS fukusho_rate,
    COUNT(*) AS total_races
FROM race_entries e JOIN races r ON e.race_id = r.race_id
JOIN horse_pedigrees p ON e.horse_id = p.horse_id
WHERE e.finish_position IS NOT NULL AND r.surface != '障害'
GROUP BY p.father, r.surface, r.distance;

-- 4. 母父（BMS）産駒成績ビュー
CREATE OR REPLACE VIEW v_mother_father_stats AS
SELECT p.mother_father, r.surface,
    ROUND(AVG(CASE WHEN e.finish_position <= 3 THEN 1.0 ELSE 0.0 END)::NUMERIC, 4) AS fukusho_rate,
    COUNT(*) AS total_races
FROM race_entries e JOIN races r ON e.race_id = r.race_id
JOIN horse_pedigrees p ON e.horse_id = p.horse_id
WHERE e.finish_position IS NOT NULL AND p.mother_father IS NOT NULL AND r.surface != '障害'
GROUP BY p.mother_father, r.surface;

-- 5. 馬の過去成績集計ビュー
CREATE OR REPLACE VIEW v_horse_recent_stats AS
WITH ranked AS (
    SELECT e.horse_id, e.finish_position, r.race_date, r.distance, e.weight, e.weight_diff,
        ROW_NUMBER() OVER (PARTITION BY e.horse_id ORDER BY r.race_date DESC) AS rn
    FROM race_entries e JOIN races r ON e.race_id = r.race_id
    WHERE e.finish_position IS NOT NULL AND r.surface != '障害'
)
SELECT horse_id,
    ROUND(AVG(finish_position) FILTER (WHERE rn <= 3)::NUMERIC, 2) AS avg_finish_3,
    ROUND(AVG(CASE WHEN finish_position <= 3 THEN 1.0 ELSE 0.0 END) FILTER (WHERE rn <= 3)::NUMERIC, 4) AS fukusho_rate_3,
    ROUND(AVG(finish_position) FILTER (WHERE rn <= 5)::NUMERIC, 2) AS avg_finish_5,
    ROUND(AVG(CASE WHEN finish_position <= 3 THEN 1.0 ELSE 0.0 END) FILTER (WHERE rn <= 5)::NUMERIC, 4) AS fukusho_rate_5,
    MIN(finish_position) FILTER (WHERE rn = 1) AS last_finish,
    MIN(race_date) FILTER (WHERE rn = 1) AS last_race_date,
    MIN(distance) FILTER (WHERE rn = 1) AS last_distance,
    MIN(weight) FILTER (WHERE rn = 1) AS current_weight,
    MIN(weight_diff) FILTER (WHERE rn = 1) AS current_weight_diff
FROM ranked GROUP BY horse_id;

-- 6. 出走頭数ビュー
CREATE OR REPLACE VIEW v_entry_count AS
SELECT race_id, COUNT(*) AS entry_count FROM race_entries GROUP BY race_id;

-- 7. メイン特徴量ビュー（モデル学習用）
CREATE OR REPLACE VIEW v_features AS
SELECT
    e.entry_id, e.race_id, r.race_date, e.horse_id, e.jockey_id, e.trainer_id,
    CASE WHEN e.finish_position <= 3 THEN 1 ELSE 0 END AS target,
    r.venue_code, r.distance, r.surface, r.track_condition, r.weather,
    e.post_position, ec.entry_count, r.class,
    hrs.avg_finish_3, hrs.avg_finish_5, hrs.fukusho_rate_3, hrs.fukusho_rate_5,
    hrs.last_finish,
    (r.race_date - hrs.last_race_date::date) AS rest_days,
    (r.distance - hrs.last_distance) AS distance_diff,
    e.weight AS horse_weight, e.weight_diff AS horse_weight_diff,
    js.fukusho_rate AS jockey_fukusho_rate,
    ts.fukusho_rate AS trainer_fukusho_rate,
    fs.fukusho_rate AS father_fukusho_rate,
    mfs.fukusho_rate AS mother_father_fukusho_rate,
    ns.fukusho_rate AS nick_fukusho_rate,
    mfds.fukusho_rate AS mother_father_distance_fukusho_rate,
    mfcs.fukusho_rate AS mother_father_condition_fukusho_rate,
    e.odds, e.popularity,
    ROUND((e.popularity::NUMERIC / ec.entry_count), 4) AS popularity_ratio
FROM race_entries e
JOIN races r ON e.race_id = r.race_id
LEFT JOIN v_entry_count ec ON ec.race_id = e.race_id
LEFT JOIN v_horse_recent_stats hrs ON hrs.horse_id = e.horse_id
LEFT JOIN v_jockey_stats js ON js.jockey_id = e.jockey_id AND js.venue_code = r.venue_code AND js.surface = r.surface
LEFT JOIN v_trainer_stats ts ON ts.trainer_id = e.trainer_id AND ts.surface = r.surface
LEFT JOIN horse_pedigrees hp ON hp.horse_id = e.horse_id
LEFT JOIN v_father_stats fs ON fs.father = hp.father AND fs.surface = r.surface
LEFT JOIN v_mother_father_stats mfs ON mfs.mother_father = hp.mother_father AND mfs.surface = r.surface
LEFT JOIN v_nick_stats ns ON ns.father = hp.father AND ns.mother_father = hp.mother_father AND ns.surface = r.surface
LEFT JOIN v_mother_father_distance_stats mfds ON mfds.mother_father = hp.mother_father AND mfds.surface = r.surface
    AND mfds.distance_category = CASE WHEN r.distance <= 1200 THEN '短距離' WHEN r.distance <= 1600 THEN 'マイル' WHEN r.distance <= 2000 THEN '中距離' ELSE '長距離' END
LEFT JOIN v_mother_father_condition_stats mfcs ON mfcs.mother_father = hp.mother_father AND mfcs.surface = r.surface AND mfcs.track_condition = r.track_condition
WHERE e.finish_position IS NOT NULL AND r.surface != '障害';

-- 8. 父×母父（ニックス）複勝率ビュー
CREATE OR REPLACE VIEW v_nick_stats AS
SELECT p.father, p.mother_father, r.surface,
    ROUND(AVG(CASE WHEN e.finish_position <= 3 THEN 1.0 ELSE 0.0 END)::NUMERIC, 4) AS fukusho_rate,
    COUNT(*) AS total_races
FROM race_entries e JOIN races r ON e.race_id = r.race_id
JOIN horse_pedigrees p ON e.horse_id = p.horse_id
WHERE e.finish_position IS NOT NULL AND p.father IS NOT NULL AND p.mother_father IS NOT NULL AND r.surface != '障害'
GROUP BY p.father, p.mother_father, r.surface;

-- 9. 母父×距離帯別複勝率ビュー
CREATE OR REPLACE VIEW v_mother_father_distance_stats AS
SELECT p.mother_father,
    CASE WHEN r.distance <= 1200 THEN '短距離' WHEN r.distance <= 1600 THEN 'マイル' WHEN r.distance <= 2000 THEN '中距離' ELSE '長距離' END AS distance_category,
    r.surface,
    ROUND(AVG(CASE WHEN e.finish_position <= 3 THEN 1.0 ELSE 0.0 END)::NUMERIC, 4) AS fukusho_rate,
    COUNT(*) AS total_races
FROM race_entries e JOIN races r ON e.race_id = r.race_id
JOIN horse_pedigrees p ON e.horse_id = p.horse_id
WHERE e.finish_position IS NOT NULL AND p.mother_father IS NOT NULL AND r.surface != '障害'
GROUP BY p.mother_father, distance_category, r.surface;

-- 10. 母父×馬場状態別複勝率ビュー
CREATE OR REPLACE VIEW v_mother_father_condition_stats AS
SELECT p.mother_father, r.track_condition, r.surface,
    ROUND(AVG(CASE WHEN e.finish_position <= 3 THEN 1.0 ELSE 0.0 END)::NUMERIC, 4) AS fukusho_rate,
    COUNT(*) AS total_races
FROM race_entries e JOIN races r ON e.race_id = r.race_id
JOIN horse_pedigrees p ON e.horse_id = p.horse_id
WHERE e.finish_position IS NOT NULL AND p.mother_father IS NOT NULL AND r.track_condition IS NOT NULL AND r.surface != '障害'
GROUP BY p.mother_father, r.track_condition, r.surface;
