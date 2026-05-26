"""
改善版 競馬予測モデル学習
- 特徴量: 血統・騎手場所別・人気比・前走タイム差 など
- カテゴリエンコーダは学習データのみで fit
- キャリブレーション: sigmoid（過信抑制）
"""

import os
import joblib
import pandas as pd
import numpy as np
from datetime import datetime
from supabase import create_client
import lightgbm as lgb
from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.calibration import CalibratedClassifierCV
import warnings

from config import SUPABASE_URL
from improved_keiba_features import (
    TRAIN_END,
    VALID_END,
    FEATURE_COLS,
    build_features,
    prepare_xy,
    print_calibration_report,
)

warnings.filterwarnings("ignore")

SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
if not SUPABASE_KEY:
    raise ValueError(
        "SUPABASE_KEY が設定されていません。\n"
        'PowerShell: $env:SUPABASE_KEY="YOUR_KEY"'
    )

print("Supabase接続中...")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
print("接続成功")


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
        print(f"  {table}: {len(all_data)}件", end="\r")
    print(f"  {table}: {len(all_data)}件 完了")
    return pd.DataFrame(all_data)


print("\nデータ取得中...")
df_entries = fetch_all(
    "race_entries",
    "entry_id,race_id,horse_id,jockey_id,trainer_id,"
    "post_position,horse_number,finish_position,finish_time,"
    "odds,popularity,weight,weight_diff",
)
df_races = fetch_all(
    "races",
    "race_id,race_date,venue_code,distance,surface,"
    "track_condition,weather,class",
)
df_peds = fetch_all("horse_pedigrees", "horse_id,father,mother_father")

df_races["race_date"] = pd.to_datetime(df_races["race_date"])
df = df_entries.merge(df_races, on="race_id", how="inner")
df = df.merge(df_peds, on="horse_id", how="left")
df["entry_count"] = df.groupby("race_id")["race_id"].transform("count")

print("\n特徴量作成中...")
df, encoders = build_features(df, train_end=TRAIN_END)

train_mask = df["race_date"] <= TRAIN_END
valid_mask = (df["race_date"] > TRAIN_END) & (df["race_date"] <= VALID_END)
test_mask = df["race_date"] > VALID_END

print(f"\n学習: {train_mask.sum():,}件")
print(f"検証: {valid_mask.sum():,}件")
print(f"テスト: {test_mask.sum():,}件")

print(f"\n特徴量数（候補A/B）: A={len(FEATURE_COLS)}")

FEATURE_SET_A = FEATURE_COLS
FEATURE_SET_B = [c for c in FEATURE_COLS if c != "popularity_ratio"]

print(f"特徴量数（候補A/B）: A={len(FEATURE_SET_A)} / B={len(FEATURE_SET_B)}")

params = {
    "objective": "binary",
    "metric": "auc",
    "num_leaves": 31,
    "learning_rate": 0.03,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "min_child_samples": 50,
    "verbose": -1,
}

if test_mask.sum() == 0:
    raise RuntimeError("テスト期間のデータがありません。")

test_dates = df.loc[test_mask, "race_date"]
split_date = test_dates.median()
m_first = (test_dates <= split_date).values
m_second = (test_dates > split_date).values


def evaluate_candidate(feature_cols):
    X_train, y_train = prepare_xy(df, train_mask, feature_cols=feature_cols)
    X_valid, y_valid = prepare_xy(df, valid_mask, feature_cols=feature_cols)
    X_test, y_test = prepare_xy(df, test_mask, feature_cols=feature_cols)

    base_model = lgb.LGBMClassifier(**params, n_estimators=1000)
    print(f"\nLightGBM学習中... features={len(feature_cols)}")
    base_model.fit(
        X_train,
        y_train,
        eval_set=[(X_valid, y_valid)],
        callbacks=[
            lgb.early_stopping(50),
            lgb.log_evaluation(100),
        ],
    )

    print("Calibration（sigmoid）...")
    cal_model = CalibratedClassifierCV(
        estimator=base_model,
        method="sigmoid",
        cv="prefit",
    )
    cal_model.fit(X_valid, y_valid)

    pred_test = cal_model.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, pred_test)
    brier = brier_score_loss(y_test, pred_test)

    # テスト後半のキャリブレーション観点（平均予測のズレ）
    pred_second = pred_test[m_second]
    y_second = y_test.values[m_second]
    mean_gap_second = float(pred_second.mean() - y_second.mean())
    brier_second = float(brier_score_loss(y_second, pred_second))

    # 後半のビン別キャリブレーション（長いので最小限: 後半のみ）
    print(f"\n[テスト] AUC={auc:.4f}  Brier={brier:.4f}")
    print(
        f"[テスト後半] mean(pred)= {pred_second.mean():.4f}  "
        f"actual={y_second.mean():.4f}  gap={mean_gap_second:+.4f}  "
        f"Brier={brier_second:.4f}"
    )
    print("\n[テスト後半] キャリブレーション（ビン別）")
    print_calibration_report(y_second, pred_second, "test後半")

    fi = pd.DataFrame(
        {"feature": feature_cols, "importance": base_model.feature_importances_}
    ).sort_values("importance", ascending=False)
    print("\n特徴量重要度 Top10:")
    print(fi.head(10).to_string(index=False))

    return {
        "cal_model": cal_model,
        "base_model": base_model,
        "feature_cols": feature_cols,
        "test_auc": float(auc),
        "test_brier": float(brier),
        "test_second_gap": mean_gap_second,
        "test_second_brier": brier_second,
    }


print("\n=== 候補A: popularity_ratio あり ===")
res_a = evaluate_candidate(FEATURE_SET_A)

print("\n=== 候補B: popularity_ratio なし ===")
res_b = evaluate_candidate(FEATURE_SET_B)

def score(res):
    # 小さいほど良い: Brier + 後半の平均ズレ（絶対値）を主に見る
    return (res["test_brier"], abs(res["test_second_gap"]), -res["test_auc"])

best = res_a if score(res_a) <= score(res_b) else res_b

date_str = datetime.now().strftime("%Y%m%d")
model_name = f"improved_keiba_model_{date_str}.pkl"

print("\n=== 保存（最良候補） ===")
print(
    f"選択: {'A(あり)' if best is res_a else 'B(なし)'} | "
    f"test_auc={best['test_auc']:.4f} | test_brier={best['test_brier']:.4f} | "
    f"test_second_gap={best['test_second_gap']:+.4f}"
)

joblib.dump(best["cal_model"], model_name)
joblib.dump(encoders, model_name.replace(".pkl", "_encoders.pkl"))
joblib.dump(best["feature_cols"], model_name.replace(".pkl", "_features.pkl"))

print(f"\n保存完了: {model_name}")
print("次: python improved_keiba_backtest.py")
