-- ============================================================
-- 競馬予測システム 特徴量エンジニアリング SQLビュー定義
-- 対象：JRA平地レース（障害除外）
-- DB：Supabase (PostgreSQL)
-- ============================================================


-- ============================================================
-- 1. 騎手成績ビュー
-- ============================================================
CREATE OR REPLACE VIEW v_jockey_stats AS
SELECT
    e.jockey_id,
    r.venue_code,
    r.surface,
    r.track_condition,
    ROUND(AVG(CASE WHEN e.finish_position <= 3 THEN 1.0 ELSE 0.0 END)::NUMERIC, 4) AS fukusho_rate,
    COUNT(*) AS total_races
FROM race_entries e
JOIN races r ON e.race_id = r.race_id
WHERE e.finish_position IS NOT NULL
  AND r.surface != '障害'
GROUP BY e.jockey_id, r.venue_code, r.surface, r.track_condition;


-- ============================================================
-- 2. 調教師成績ビュー
-- ============================================================
CREATE OR REPLACE VIEW v_trainer_stats AS
SELECT
    e.trainer_id,
    r.surface,
    r.distance,
    ROUND(AVG(CASE WHEN e.finish_position <= 3 THEN 1.0 ELSE 0.0 END)::NUMERIC, 4) AS fukusho_rate,
    COUNT(*) AS total_races
FROM race_entries e
JOIN races r ON e.race_id = r.race_id
WHERE e.finish_position IS NOT NULL
  AND r.surface != '障害'
GROUP BY e.trainer_id, r.surface, r.distance;


-- ============================================================
-- 3. 父産駒成績ビュー
-- ============================================================
CREATE OR REPLACE VIEW v_father_stats AS
SELECT
    p.father,
    r.surface,
    r.distance,
    ROUND(AVG(CASE WHEN e.finish_position <= 3 THEN 1.0 ELSE 0.0 END)::NUMERIC, 4) AS fukusho_rate,
    COUNT(*) AS total_races
FROM race_entries e
JOIN races r ON e.race_id = r.race_id
JOIN horse_pedigrees p ON e.horse_id = p.horse_id
WHERE e.finish_position IS NOT NULL
  AND r.surface != '障害'
GROUP BY p.father, r.surface, r.distance;


-- ============================================================
-- 4. 母父（BMS）産駒成績ビュー
-- ============================================================
CREATE OR REPLACE VIEW v_mother_father_stats AS
SELECT
    p.mother_father,
    r.surface,
    ROUND(AVG(CASE WHEN e.finish_position <= 3 THEN 1.0 ELSE 0.0 END)::NUMERIC, 4) AS fukusho_rate,
    COUNT(*) AS total_races
FROM race_entries e
JOIN races r ON e.race_id = r.race_id
JOIN horse_pedigrees p ON e.horse_id = p.horse_id
WHERE e.finish_position IS NOT NULL
  AND p.mother_father IS NOT NULL
  AND r.surface != '障害'
GROUP BY p.mother_father, r.surface;


-- ============================================================
-- 5. 馬の過去成績集計ビュー（直近5走）
-- ============================================================
CREATE OR REPLACE VIEW v_horse_recent_stats AS
WITH ranked AS (
    SELECT
        e.horse_id,
        e.race_id,
        e.finish_position,
        r.race_date,
        r.distance,
        r.surface,
        r.venue_code,
        e.weight,
        e.weight_diff,
        ROW_NUMBER() OVER (PARTITION BY e.horse_id ORDER BY r.race_date DESC) AS rn
    FROM race_entries e
    JOIN races r ON e.race_id = r.race_id
    WHERE e.finish_position IS NOT NULL
      AND r.surface != '障害'
)
SELECT
    horse_id,
    -- 直近3走
    ROUND(AVG(finish_position) FILTER (WHERE rn <= 3)::NUMERIC, 2) AS avg_finish_3,
    ROUND(AVG(CASE WHEN finish_position <= 3 THEN 1.0 ELSE 0.0 END) FILTER (WHERE rn <= 3)::NUMERIC, 4) AS fukusho_rate_3,
    -- 直近5走
    ROUND(AVG(finish_position) FILTER (WHERE rn <= 5)::NUMERIC, 2) AS avg_finish_5,
    ROUND(AVG(CASE WHEN finish_position <= 3 THEN 1.0 ELSE 0.0 END) FILTER (WHERE rn <= 5)::NUMERIC, 4) AS fukusho_rate_5,
    -- 前走情報
    MIN(finish_position) FILTER (WHERE rn = 1) AS last_finish,
    MIN(race_date) FILTER (WHERE rn = 1) AS last_race_date,
    MIN(distance) FILTER (WHERE rn = 1) AS last_distance,
    MIN(weight) FILTER (WHERE rn = 1) AS current_weight,
    MIN(weight_diff) FILTER (WHERE rn = 1) AS current_weight_diff
FROM ranked
GROUP BY horse_id;


-- ============================================================
-- 6. 出走頭数ビュー
-- ============================================================
CREATE OR REPLACE VIEW v_entry_count AS
SELECT
    race_id,
    COUNT(*) AS entry_count
FROM race_entries
GROUP BY race_id;


-- ============================================================
-- 7. メイン特徴量ビュー（モデル学習用）
-- ============================================================
-- 使い方：このビューをColabからSELECTして特徴量として使用する
-- ※ ターゲット変数 target（3着以内=1）も含む
CREATE OR REPLACE VIEW v_features AS
SELECT
    -- ID・日付（学習時は除外）
    e.entry_id,
    e.race_id,
    r.race_date,
    e.horse_id,
    e.jockey_id,
    e.trainer_id,

    -- ターゲット変数
    CASE WHEN e.finish_position <= 3 THEN 1 ELSE 0 END AS target,

    -- レース条件
    r.venue_code,
    r.distance,
    r.surface,
    r.track_condition,
    r.weather,
    e.post_position,
    ec.entry_count,
    r.class,

    -- 馬の過去成績
    hrs.avg_finish_3,
    hrs.avg_finish_5,
    hrs.fukusho_rate_3,
    hrs.fukusho_rate_5,
    hrs.last_finish,
    EXTRACT(DAY FROM (r.race_date - hrs.last_race_date))::INT AS rest_days,
    (r.distance - hrs.last_distance)::INT AS distance_diff,
    e.weight AS horse_weight,
    e.weight_diff AS horse_weight_diff,

    -- 騎手成績
    js.fukusho_rate AS jockey_fukusho_rate,

    -- 調教師成績
    ts.fukusho_rate AS trainer_fukusho_rate,

    -- 血統（父）
    fs.fukusho_rate AS father_fukusho_rate,

    -- 血統（母父）
    mfs.fukusho_rate AS mother_father_fukusho_rate,

    -- オッズ・人気
    e.odds,
    e.popularity,
    ROUND((e.popularity::NUMERIC / ec.entry_count), 4) AS popularity_ratio

FROM race_entries e
JOIN races r ON e.race_id = r.race_id
LEFT JOIN v_entry_count ec ON ec.race_id = e.race_id
LEFT JOIN v_horse_recent_stats hrs ON hrs.horse_id = e.horse_id
LEFT JOIN v_jockey_stats js ON js.jockey_id = e.jockey_id
    AND js.venue_code = r.venue_code
    AND js.surface = r.surface
LEFT JOIN v_trainer_stats ts ON ts.trainer_id = e.trainer_id
    AND ts.surface = r.surface
LEFT JOIN horse_pedigrees hp ON hp.horse_id = e.horse_id
LEFT JOIN v_father_stats fs ON fs.father = hp.father
    AND fs.surface = r.surface
LEFT JOIN v_mother_father_stats mfs ON mfs.mother_father = hp.mother_father
    AND mfs.surface = r.surface
WHERE e.finish_position IS NOT NULL
  AND r.surface != '障害';
