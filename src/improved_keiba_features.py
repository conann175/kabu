"""
改善版モデル共通の特徴量生成
学習・バックテストで同一ロジックを使う（リーク防止）
"""

import pandas as pd
import numpy as np
from sklearn.preprocessing import LabelEncoder

TRAIN_END = "2024-08-31"
VALID_END = "2024-10-31"

CAT_COLS = ["venue_code", "surface", "track_condition", "weather", "class"]

FEATURE_COLS = [
    "post_position",
    "weight",
    "weight_diff",
    "distance",
    "entry_count",
    "popularity_ratio",
    "avg_finish_3",
    "fukusho_rate_3",
    "avg_finish_5",
    "fukusho_rate_5",
    "last_finish",
    "rest_days",
    "distance_diff",
    "last_time_diff",
    "jockey_fukusho_rate",
    "trainer_fukusho_rate",
    "father_fukusho_rate",
    "mother_father_fukusho_rate",
    "nick_fukusho_rate",
    "venue_code_enc",
    "surface_enc",
    "track_condition_enc",
    "weather_enc",
    "class_enc",
]


def time_to_sec(t):
    try:
        if t is None or (isinstance(t, float) and np.isnan(t)):
            return np.nan
        parts = str(t).split(":")
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, TypeError):
        return np.nan


def calc_stats(df_train, group_cols, min_count=10):
    stats = df_train.groupby(group_cols)["target"].agg(["mean", "count"])
    stats.columns = ["rate", "count"]
    stats.loc[stats["count"] < min_count, "rate"] = np.nan
    return stats["rate"]


def _map_rate(row, primary_keys, primary_stats, fallback_keys, fallback_stats):
    key = tuple(row[k] for k in primary_keys)
    val = primary_stats.get(key, np.nan)
    if not np.isnan(val):
        return val
    fb = tuple(row[k] for k in fallback_keys)
    return fallback_stats.get(fb, np.nan)


def build_features(df, train_end=TRAIN_END):
    """
    df: race_entries + races + pedigrees 結合済み、race_date 変換済み
    学習データのみで統計・エンコーダを fit
    """
    df = df.sort_values(["horse_id", "race_date", "race_id"]).reset_index(drop=True)

    fp = df["finish_position"].astype(float)
    df["target"] = ((fp >= 1) & (fp <= 3)).astype(int)

    train_mask = df["race_date"] <= train_end
    train_df = df[train_mask].copy()

    # 騎手・調教師・血統（学習データのみ）
    jockey_venue = calc_stats(train_df, ["jockey_id", "venue_code", "surface"])
    jockey_all = calc_stats(train_df, ["jockey_id"], min_count=20)
    trainer_st = calc_stats(train_df, ["trainer_id", "surface"])
    father_st = calc_stats(train_df, ["father", "surface"])
    mf_st = calc_stats(train_df, ["mother_father", "surface"])
    nick_st = calc_stats(train_df, ["father", "mother_father", "surface"])

    df["jockey_fukusho_rate"] = df.apply(
        lambda r: _map_rate(
            r,
            ["jockey_id", "venue_code", "surface"],
            jockey_venue,
            ["jockey_id"],
            jockey_all,
        ),
        axis=1,
    )
    df["trainer_fukusho_rate"] = df.apply(
        lambda r: trainer_st.get((r["trainer_id"], r["surface"]), np.nan), axis=1
    )
    df["father_fukusho_rate"] = df.apply(
        lambda r: father_st.get((r["father"], r["surface"]), np.nan), axis=1
    )
    df["mother_father_fukusho_rate"] = df.apply(
        lambda r: mf_st.get((r["mother_father"], r["surface"]), np.nan), axis=1
    )
    df["nick_fukusho_rate"] = df.apply(
        lambda r: nick_st.get(
            (r["father"], r["mother_father"], r["surface"]), np.nan
        ),
        axis=1,
    )

    pop = pd.to_numeric(df["popularity"], errors="coerce")
    df["popularity_ratio"] = pop / df["entry_count"].astype(float)

    # タイム差（前走のみ使用するため shift）
    df["finish_sec"] = df["finish_time"].apply(time_to_sec)
    avg_time = train_df.copy()
    avg_time["finish_sec"] = avg_time["finish_time"].apply(time_to_sec)
    avg_time_map = avg_time.groupby(["distance", "surface"])["finish_sec"].mean().to_dict()
    df["avg_time_by_course"] = df.apply(
        lambda r: avg_time_map.get((r["distance"], r["surface"]), np.nan), axis=1
    )
    df["time_diff_from_avg"] = df["finish_sec"] - df["avg_time_by_course"]
    df["last_time_diff"] = df.groupby("horse_id")["time_diff_from_avg"].shift(1)

    # 馬の過去成績（shift でリーク防止）
    shift_finish = df.groupby("horse_id")["finish_position"].shift(1)
    shift_target = df.groupby("horse_id")["target"].shift(1)

    df["avg_finish_3"] = (
        shift_finish.groupby(df["horse_id"])
        .rolling(3)
        .mean()
        .reset_index(level=0, drop=True)
    )
    df["avg_finish_5"] = (
        shift_finish.groupby(df["horse_id"])
        .rolling(5)
        .mean()
        .reset_index(level=0, drop=True)
    )
    df["fukusho_rate_3"] = (
        shift_target.groupby(df["horse_id"])
        .rolling(3)
        .mean()
        .reset_index(level=0, drop=True)
    )
    df["fukusho_rate_5"] = (
        shift_target.groupby(df["horse_id"])
        .rolling(5)
        .mean()
        .reset_index(level=0, drop=True)
    )
    df["last_finish"] = shift_finish
    df["rest_days"] = df.groupby("horse_id")["race_date"].diff().dt.days
    df["distance_diff"] = df.groupby("horse_id")["distance"].diff()

    encoders = fit_encoders(df, train_mask)
    df = apply_encoders(df, encoders)

    return df, encoders


def fit_encoders(df, train_mask):
    encoders = {}
    train_df = df[train_mask]
    for col in CAT_COLS:
        le = LabelEncoder()
        le.fit(train_df[col].fillna("unknown").astype(str))
        encoders[col] = le
    return encoders


def apply_encoders(df, encoders):
    df = df.copy()
    for col in CAT_COLS:
        le = encoders[col]
        known = {c: i for i, c in enumerate(le.classes_)}
        df[col + "_enc"] = (
            df[col].fillna("unknown").astype(str).map(known).fillna(-1).astype(int)
        )
    return df


def prepare_xy(df, mask, feature_cols=None):
    cols = feature_cols or FEATURE_COLS
    X = df.loc[mask, cols].fillna(-1).astype(float)
    y = df.loc[mask, "target"]
    return X, y


def print_calibration_report(y_true, proba, label, bins=None):
    bins = bins or [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 1.01]
    y_s = pd.Series(np.asarray(y_true))
    p_s = pd.Series(np.asarray(proba))
    print(f"\n  [{label}] 平均予測={p_s.mean():.4f}  実複勝率={y_s.mean():.4f}")
    binned = pd.cut(p_s, bins=bins, right=False)
    for interval, idx in p_s.groupby(binned, observed=True).groups.items():
        if len(idx) < 50:
            continue
        pred_m = p_s.loc[idx].mean()
        actual_m = y_s.loc[idx].mean()
        print(
            f"    {interval}: n={len(idx):>6,}  pred={pred_m:.3f}  actual={actual_m:.3f}"
        )
