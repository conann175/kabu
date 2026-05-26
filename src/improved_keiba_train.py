"""
改善版 競馬予測モデル学習スクリプト
- calibration追加
- edge戦略対応
- class_weight削除
- オッズ依存軽減
"""

import os
import joblib
import pandas as pd
import numpy as np
from datetime import datetime
from supabase import create_client
import lightgbm as lgb
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import LabelEncoder
from sklearn.calibration import CalibratedClassifierCV
import warnings

warnings.filterwarnings("ignore")

SUPABASE_URL = "YOUR_SUPABASE_URL"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

TRAIN_END = "2024-08-31"
VALID_END = "2024-10-31"

FEATURE_COLS = [
    'post_position',
    'horse_number',
    'weight',
    'weight_diff',
    'distance',
    'entry_count',
    'avg_finish_3',
    'fukusho_rate_3',
    'avg_finish_5',
    'fukusho_rate_5',
    'last_finish',
    'rest_days',
    'distance_diff',
    'last_time_diff',
    'jockey_fukusho_rate',
    'trainer_fukusho_rate',
    'father_fukusho_rate',
    'mother_father_fukusho_rate',
    'nick_fukusho_rate',
    'venue_code_enc',
    'surface_enc',
    'track_condition_enc',
    'weather_enc',
    'class_enc'
]

print("Supabase接続中...")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def fetch_all(table, select):
    all_data = []
    offset = 0

    while True:
        q = supabase.table(table).select(select)

        if table == "race_entries":
            q = q.not_.is_("finish_position", "null")

        res = q.range(offset, offset + 999).execute()

        if not res.data:
            break

        all_data.extend(res.data)
        offset += 1000

        print(f"{table}: {len(all_data)}件", end="\r")

    print(f"{table}: {len(all_data)}件 完了")
    return pd.DataFrame(all_data)

print("データ取得中...")

df_entries = fetch_all(
    "race_entries",
    "entry_id,race_id,horse_id,jockey_id,trainer_id,"
    "post_position,horse_number,finish_position,finish_time,"
    "odds,popularity,weight,weight_diff"
)

df_races = fetch_all(
    "races",
    "race_id,race_date,venue_code,distance,surface,"
    "track_condition,weather,class"
)

df_peds = fetch_all(
    "horse_pedigrees",
    "horse_id,father,mother_father"
)

df_races["race_date"] = pd.to_datetime(df_races["race_date"])

df = df_entries.merge(df_races, on="race_id")
df = df.merge(df_peds, on="horse_id", how="left")

df["entry_count"] = df.groupby("race_id")["race_id"].transform("count")
df["target"] = (df["finish_position"].astype(float) <= 3).astype(int)

df = df.sort_values(["horse_id", "race_date"])

train_mask = df["race_date"] <= TRAIN_END
valid_mask = (
    (df["race_date"] > TRAIN_END) &
    (df["race_date"] <= VALID_END)
)
test_mask = df["race_date"] > VALID_END

train_df = df[train_mask].copy()

def calc_stats(df_train, group_cols, min_count=10):
    stats = df_train.groupby(group_cols)["target"].agg(["mean", "count"])
    stats.columns = ["rate", "count"]

    stats.loc[stats["count"] < min_count, "rate"] = np.nan

    return stats["rate"]

print("統計特徴量作成中...")

jockey_all = calc_stats(train_df, ["jockey_id"], 20)
trainer_all = calc_stats(train_df, ["trainer_id"], 20)

df["jockey_fukusho_rate"] = df["jockey_id"].map(jockey_all)
df["trainer_fukusho_rate"] = df["trainer_id"].map(trainer_all)

def time_to_sec(t):
    try:
        parts = str(t).split(":")
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except:
        return np.nan

df["finish_sec"] = df["finish_time"].apply(time_to_sec)

shift_finish = (
    df.groupby("horse_id")["finish_position"]
    .shift(1)
)

shift_target = (
    df.groupby("horse_id")["target"]
    .shift(1)
)

df["avg_finish_3"] = (
    shift_finish
    .groupby(df["horse_id"])
    .rolling(3)
    .mean()
    .reset_index(level=0, drop=True)
)

df["avg_finish_5"] = (
    shift_finish
    .groupby(df["horse_id"])
    .rolling(5)
    .mean()
    .reset_index(level=0, drop=True)
)

df["fukusho_rate_3"] = (
    shift_target
    .groupby(df["horse_id"])
    .rolling(3)
    .mean()
    .reset_index(level=0, drop=True)
)

df["fukusho_rate_5"] = (
    shift_target
    .groupby(df["horse_id"])
    .rolling(5)
    .mean()
    .reset_index(level=0, drop=True)
)

df["last_finish"] = shift_finish

df["rest_days"] = (
    df.groupby("horse_id")["race_date"]
    .diff()
    .dt.days
)

df["distance_diff"] = (
    df.groupby("horse_id")["distance"]
    .diff()
)

cat_cols = [
    "venue_code",
    "surface",
    "track_condition",
    "weather",
    "class"
]

encoders = {}

for col in cat_cols:
    le = LabelEncoder()

    df[col + "_enc"] = le.fit_transform(
        df[col].fillna("unknown").astype(str)
    )

    encoders[col] = le

X_train = df[train_mask][FEATURE_COLS].astype(float)
y_train = df[train_mask]["target"]

X_valid = df[valid_mask][FEATURE_COLS].astype(float)
y_valid = df[valid_mask]["target"]

X_test = df[test_mask][FEATURE_COLS].astype(float)
y_test = df[test_mask]["target"]

params = {
    "objective": "binary",
    "metric": "auc",
    "num_leaves": 31,
    "learning_rate": 0.03,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "min_child_samples": 50,
    "verbose": -1,
}

print("LightGBM学習中...")

base_model = lgb.LGBMClassifier(
    **params,
    n_estimators=1000
)

base_model.fit(
    X_train,
    y_train,
    eval_set=[(X_valid, y_valid)],
    callbacks=[
        lgb.early_stopping(50),
        lgb.log_evaluation(50)
    ]
)

print("Calibration中...")

model = CalibratedClassifierCV(
    estimator=base_model,
    method="isotonic",
    cv="prefit"
)

model.fit(X_valid, y_valid)

pred = model.predict_proba(X_test)[:, 1]

auc = roc_auc_score(y_test, pred)

print(f"AUC: {auc:.4f}")
print(f"平均予測確率: {pred.mean():.4f}")
print(f"実際複勝率: {y_test.mean():.4f}")

date_str = datetime.now().strftime("%Y%m%d")

model_name = f"improved_keiba_model_{date_str}.pkl"

joblib.dump(model, model_name)
joblib.dump(encoders, model_name.replace(".pkl", "_encoders.pkl"))
joblib.dump(FEATURE_COLS, model_name.replace(".pkl", "_features.pkl"))

print(f"保存完了: {model_name}")
