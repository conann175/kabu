"""
改善版バックテスト
- edge戦略（calibration済み確率）
- 閾値グリッドは検証期間（後半）の回収率で評価
- 探索期間（前半）との乖離を警告
- 大穴依存を抑える odds 上限フィルタ対応
"""

import glob
import os
import re
import joblib
import pandas as pd
import numpy as np
from supabase import create_client
from config import SUPABASE_URL
from improved_keiba_features import (
    TRAIN_END,
    VALID_END,
    build_features,
    prepare_xy,
)


SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
if not SUPABASE_KEY:
    raise ValueError(
        "SUPABASE_KEY が設定されていません。\n"
        'PowerShell: $env:SUPABASE_KEY="YOUR_KEY"'
    )

model_files = sorted(
    [
        f for f in glob.glob("improved_keiba_model_*.pkl")
        if re.match(r"improved_keiba_model_\d{8}\.pkl$", f)
    ],
    reverse=True,
)
if not model_files:
    print("モデルファイルが見つかりません。先に improved_keiba_train_fixed.py を実行してください。")
    raise SystemExit(1)

model_path = model_files[0]
model = joblib.load(model_path)
feature_cols = joblib.load(model_path.replace(".pkl", "_features.pkl"))
print(f"モデル読み込み: {model_path} ({len(feature_cols)}特徴量)")

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
    return pd.DataFrame(all_data)


print("データ取得中...")

df_entries = fetch_all(
    "race_entries",
    "entry_id,race_id,horse_id,jockey_id,trainer_id,"
    "post_position,horse_number,finish_position,"
    "finish_time,odds,popularity,weight,weight_diff",
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

print("特徴量計算中...")
df, _ = build_features(df, train_end=TRAIN_END)

test_mask = df["race_date"] > VALID_END
df_test = df[test_mask].copy()
X, _ = prepare_xy(df, test_mask, feature_cols=feature_cols)
df_test["proba"] = model.predict_proba(X)[:, 1]

df_test["market_prob"] = (1 / df_test["odds"]) * 0.8
df_test["edge"] = df_test["proba"] - df_test["market_prob"]

print("複勝払戻取得中...")
all_pays = []
offset = 0
while True:
    res = (
        supabase.table("payouts")
        .select("race_id,bet_type,combination,payout")
        .eq("bet_type", "複勝")
        .range(offset, offset + 999)
        .execute()
    )
    if not res.data:
        break
    all_pays.extend(res.data)
    offset += 1000

df_pay = pd.DataFrame(all_pays) if all_pays else pd.DataFrame(
    columns=["race_id", "combination", "payout"]
)
if len(df_pay) > 0:
    df_pay["horse_number"] = pd.to_numeric(df_pay["combination"], errors="coerce")
    df_pay["fukusho_odds"] = df_pay["payout"] / 100
    df_pay = df_pay.drop_duplicates(subset=["race_id", "horse_number"])

if len(df_pay) > 0:
    df_test = df_test.merge(
        df_pay[["race_id", "horse_number", "fukusho_odds"]],
        on=["race_id", "horse_number"],
        how="left",
    )
    races_with_payouts = set(df_pay["race_id"].unique())
    df_bt = df_test[df_test["race_id"].isin(races_with_payouts)].copy()
else:
    df_bt = df_test.copy()
    df_bt["fukusho_odds"] = np.nan

df_bt = df_bt[df_bt["odds"].notna()].copy()


def filter_buy(df, proba_min, edge_min, odds_min, odds_max=None):
    mask = (
        (df["proba"] >= proba_min)
        & (df["edge"] >= edge_min)
        & (df["odds"] >= odds_min)
    )
    if odds_max is not None:
        mask &= df["odds"] <= odds_max
    return df[mask]


def backtest_metrics(buy):
    if len(buy) == 0:
        return None
    bet = len(buy) * 100
    hit = buy[buy["target"] == 1]
    ret = hit["fukusho_odds"].fillna(0).sum() * 100
    return {
        "n": len(buy),
        "hit_rate": len(hit) / len(buy) * 100,
        "recovery": ret / bet * 100,
        "profit": int(ret - bet),
    }


def print_metrics(label, m):
    if m is None:
        print(f"{label}: 購入0件")
        return
    print(
        f"{label}: 購入{m['n']:,} | 的中率{m['hit_rate']:.2f}% | "
        f"回収率{m['recovery']:.2f}% | 損益{m['profit']:+,}円"
    )


def grid_search_dual(
    df_explore,
    df_oos,
    proba_list,
    edge_list,
    odds_list,
    odds_max=None,
    min_buys_oos=200,
):
    """各閾値について探索期間・検証期間の成績を同時に記録"""
    rows = []
    for p in proba_list:
        for e in edge_list:
            for o in odds_list:
                m_exp = backtest_metrics(
                    filter_buy(df_explore, p, e, o, odds_max)
                )
                m_oos = backtest_metrics(filter_buy(df_oos, p, e, o, odds_max))
                if not m_oos or m_oos["n"] < min_buys_oos:
                    continue
                row = {
                    "proba": p,
                    "edge": e,
                    "odds": o,
                    "odds_max": odds_max,
                    "n_oos": m_oos["n"],
                    "hit_rate_oos": m_oos["hit_rate"],
                    "recovery_oos": m_oos["recovery"],
                    "profit_oos": m_oos["profit"],
                }
                if m_exp:
                    row["n_exp"] = m_exp["n"]
                    row["recovery_exp"] = m_exp["recovery"]
                else:
                    row["n_exp"] = 0
                    row["recovery_exp"] = 0.0
                rows.append(row)
    return pd.DataFrame(rows)


def count_grid_combos(proba_list, edge_list, odds_list):
    return len(proba_list) * len(edge_list) * len(odds_list)


def print_grid_warning(n_combos):
    print(f"\n⚠ グリッドは {n_combos} 通りを試行（多重比較）。偶然100%超も出ます。")
    print("  採用判断は検証期間（後半）の回収率のみ。探索期間の好成績は無視してください。")


def print_calibration_slice(df, proba_min, label):
    sub = df[df["proba"] >= proba_min]
    if len(sub) < 30:
        print(f"  {label}: 件数不足 (n={len(sub)})")
        return
    actual = sub["target"].mean()
    pred = sub["proba"].mean()
    print(
        f"  {label}: n={len(sub):,}  proba_mean={pred:.3f}  "
        f"actual={actual:.3f}  差={pred - actual:+.3f}"
    )
    if pred - actual > 0.10:
        print("    → 高確率帯で予測が実績より上振れの可能性")


def print_monthly_breakdown(buy, title):
    if len(buy) == 0:
        print(f"\n{title}: 購入0件")
        return
    buy = buy.copy()
    buy["month"] = buy["race_date"].dt.to_period("M")
    print(f"\n{title}")
    print(f"{'月':>10} | {'購入':>6} | {'的中率':>7} | {'回収率':>7} | {'損益':>10}")
    print("-" * 52)
    for month, grp in buy.groupby("month"):
        m = backtest_metrics(grp)
        print(
            f"{str(month):>10} | {m['n']:>6,} | {m['hit_rate']:>6.1f}% | "
            f"{m['recovery']:>6.1f}% | {m['profit']:>+10,}円"
        )
    total = backtest_metrics(buy)
    print("-" * 52)
    print(
        f"{'合計':>10} | {total['n']:>6,} | {total['hit_rate']:>6.1f}% | "
        f"{total['recovery']:>6.1f}% | {total['profit']:>+10,}円"
    )


def pick_best_oos(grid_df, min_recovery_oos=100.0, min_buys_oos=200):
    """検証期間の回収率で最良を選ぶ"""
    if len(grid_df) == 0:
        return None
    g = grid_df[grid_df["n_oos"] >= min_buys_oos]
    if min_recovery_oos:
        cand = g[g["recovery_oos"] >= min_recovery_oos]
        if len(cand) > 0:
            g = cand
    if len(g) == 0:
        return None
    return g.sort_values(
        ["recovery_oos", "profit_oos"], ascending=False
    ).iloc[0]


def pick_best_explore(grid_df):
    """探索期間のみで最良（過学習の参考用・採用しない）"""
    if len(grid_df) == 0:
        return None
    return grid_df.sort_values(
        ["recovery_exp", "n_exp"], ascending=False
    ).iloc[0]


def warn_explore_oos_gap(m_exp, m_oos):
    """探索と検証の回収率乖離を警告"""
    if not m_exp or not m_oos:
        return
    if m_exp["recovery"] >= 100 and m_oos["recovery"] < 100:
        print(
            f"  ⚠ 探索 {m_exp['recovery']:.1f}% vs 検証 {m_oos['recovery']:.1f}% — "
            "全テスト成績は信頼しないでください（前半の好成績に引っ張られます）"
        )
    gap = m_exp["recovery"] - m_oos["recovery"]
    if gap > 50:
        print(
            f"  ⚠ 回収率差 {gap:.0f}pt — 期間ドリフトまたは高配当の偏りを疑ってください"
        )
    if m_oos["recovery"] < 100:
        print("  → 検証期間で回収率100%未満: 本番採用は非推奨")


def format_odds_cap(odds_max):
    return f"〜{odds_max:.0f}" if odds_max is not None else "上限なし"


def print_threshold_report(
    name, df_bt, df_explore, df_oos, p, e, o, odds_max=None, show_monthly=True
):
    """1つの閾値セットを検証期間優先で表示"""
    cap = format_odds_cap(odds_max)
    print(
        f"\n【{name}】proba>={p:.2f}, edge>={e:.2f}, "
        f"odds>={o:.0f}{'' if odds_max is None else f' & odds<={odds_max:.0f}'}"
    )
    m_oos = backtest_metrics(filter_buy(df_oos, p, e, o, odds_max))
    m_exp = backtest_metrics(filter_buy(df_explore, p, e, o, odds_max))
    m_all = backtest_metrics(filter_buy(df_bt, p, e, o, odds_max))
    print_metrics("  検証期間（採用判断）", m_oos)
    print_metrics("  探索期間", m_exp)
    print_metrics("  全テスト（参考・信頼しない）", m_all)
    warn_explore_oos_gap(m_exp, m_oos)
    if m_oos and m_oos["recovery"] >= 100:
        print("  ✓ 検証期間で回収率100%以上")
    elif m_oos:
        print(f"  ✗ 検証期間で回収率100%未満（{m_oos['recovery']:.1f}%）— 本番非推奨")
    if m_oos and m_oos["hit_rate"] < 20 and (odds_max is None or odds_max >= 15):
        print("  ※ 的中率が低い — 大穴・高配当依存の可能性")
    if show_monthly and m_oos and m_oos["n"] > 0:
        print_monthly_breakdown(
            filter_buy(df_oos, p, e, o, odds_max),
            f"月別（検証期間・{cap}）",
        )


def print_dual_grid_table(df_grid, title, sort_col="recovery_oos", top_n=20, oos_min=None):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    if len(df_grid) == 0:
        print("（該当なし）")
        return
    g = df_grid.sort_values(sort_col, ascending=False)
    if oos_min is not None:
        g = g[g["recovery_oos"] >= oos_min]
    print(
        f"{'proba':>5} {'edge':>5} {'odds':>4} | "
        f"{'探索回収':>7} | {'検証回収':>7} | {'検証購入':>7} | "
        f"{'検証的中':>7} | {'検証損益':>10}"
    )
    print("-" * 80)
    for _, r in g.head(top_n).iterrows():
        print(
            f"{r['proba']:>5.2f} {r['edge']:>5.2f} {r['odds']:>4.0f} | "
            f"{r['recovery_exp']:>6.1f}% | {r['recovery_oos']:>6.1f}% | "
            f"{int(r['n_oos']):>7,} | {r['hit_rate_oos']:>6.1f}% | "
            f"{int(r['profit_oos']):>+10,}円"
        )


# --- 概要 ---
print("=" * 60)
print(f"テスト期間: {df_test['race_date'].min().date()} 〜 {df_test['race_date'].max().date()}")
print(f"テスト件数: {len(df_test):,}  (payoutsあり: {len(df_bt):,})")
print("=" * 60)

# --- 実複勝率 vs 予測確率（キャリブレーション確認）---
actual_rate = df_bt["target"].mean()
mean_proba = df_bt["proba"].mean()
median_proba = df_bt["proba"].median()
gap = mean_proba - actual_rate

print("\n【キャリブレーション】実複勝率 vs 予測確率（テスト全体）")
print(f"  実複勝率 (target.mean):  {actual_rate:.4f}  ({actual_rate*100:.2f}%)")
print(f"  予測確率平均 (proba.mean): {mean_proba:.4f}  ({mean_proba*100:.2f}%)")
print(f"  予測確率中央値:            {median_proba:.4f}")
print(f"  差 (平均 - 実績):          {gap:+.4f}  ({gap*100:+.2f}pt)")
if gap > 0.03:
    print("  → 予測確率が実績より上振れしている可能性あり")
elif gap < -0.03:
    print("  → 予測確率が実績より下振れしている可能性あり")
else:
    print("  → 平均ベースでは大きなズレは小さい")

print("\n  確率ビン別（予測 vs 実績）")
bins = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 1.01]
df_bt["_bin"] = pd.cut(df_bt["proba"], bins=bins, right=False)
for label, grp in df_bt.groupby("_bin", observed=True):
    if len(grp) < 50:
        continue
    print(
        f"    {label}: n={len(grp):>6,}  "
        f"proba_mean={grp['proba'].mean():.3f}  actual={grp['target'].mean():.3f}"
    )
df_bt.drop(columns=["_bin"], inplace=True)

print("\n  高確率帯（全テスト）")
print_calibration_slice(df_bt, 0.70, "proba>=0.70")
print_calibration_slice(df_bt, 0.60, "proba>=0.60")
print_calibration_slice(df_bt, 0.40, "proba>=0.40")

print("\n予測確率分布（テストデータ）")
print(df_bt["proba"].describe())

# テスト期間を前半（探索）・後半（検証）に分割
split_date = df_bt["race_date"].median()
df_explore = df_bt[df_bt["race_date"] <= split_date].copy()
df_oos = df_bt[df_bt["race_date"] > split_date].copy()

print("\n  期間別キャリブレーション（proba>=0.40）")
print_calibration_slice(df_explore, 0.40, "探索期間 proba>=0.40")
print_calibration_slice(df_oos, 0.40, "検証期間 proba>=0.40")
print(
    f"\n期間分割（過学習チェック用）: "
    f"探索 {df_explore['race_date'].min().date()}〜{df_explore['race_date'].max().date()} "
    f"({len(df_explore):,}件) / "
    f"検証 {df_oos['race_date'].min().date()}〜{df_oos['race_date'].max().date()} "
    f"({len(df_oos):,}件)"
)
print(f"  分割日: {split_date.date()}（テスト期間の中央値）")

# --- ベースライン ---
print_threshold_report(
    "ベースライン", df_bt, df_explore, df_oos,
    0.18, 0.05, 4, odds_max=None, show_monthly=False,
)

# --- 閾値グリッド（検証期間の回収率で評価）---
PROBA_GRID = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
EDGE_GRID = [0.03, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20]
ODDS_GRID = [3, 4, 5, 6, 8, 10]
OOS_MIN_BUYS = 200
ODDS_CAP = 15  # 大穴抑制（単勝15倍以下）

n_grid = count_grid_combos(PROBA_GRID, EDGE_GRID, ODDS_GRID)
print_grid_warning(n_grid)
print(
    f"\n閾値グリッド探索中（検証期間で評価・odds上限{ODDS_CAP}・"
    f"検証購入>={OOS_MIN_BUYS}件）..."
)
grid_dual = grid_search_dual(
    df_explore,
    df_oos,
    PROBA_GRID,
    EDGE_GRID,
    ODDS_GRID,
    odds_max=ODDS_CAP,
    min_buys_oos=OOS_MIN_BUYS,
)

over100_oos = (
    grid_dual[grid_dual["recovery_oos"] >= 100.0] if len(grid_dual) else grid_dual
)
print(
    f"\n検証期間: 回収率100%超（odds<={ODDS_CAP}）: "
    f"{len(over100_oos)} / {len(grid_dual)} 通り"
)
print_dual_grid_table(
    over100_oos,
    f"検証期間・回収率100%超（odds<={ODDS_CAP}・採用候補）",
    top_n=20,
)
print_dual_grid_table(
    grid_dual,
    f"検証期間・全グリッド上位（odds<={ODDS_CAP}）",
    top_n=15,
)

best_oos = pick_best_oos(grid_dual, min_recovery_oos=100.0, min_buys_oos=OOS_MIN_BUYS)
if best_oos is not None:
    print(
        f"\n【検証期間ベスト（採用候補）】"
        f"proba>={best_oos['proba']:.2f}, edge>={best_oos['edge']:.2f}, "
        f"odds>={best_oos['odds']:.0f} & odds<={ODDS_CAP}"
    )
    print(
        f"  検証: 購入{int(best_oos['n_oos']):,} / "
        f"回収率{best_oos['recovery_oos']:.1f}% / "
        f"損益{int(best_oos['profit_oos']):+,}円"
    )
    print(
        f"  探索: 回収率{best_oos['recovery_exp']:.1f}% "
        f"（参考・{best_oos['recovery_exp'] - best_oos['recovery_oos']:+.0f}pt差）"
    )
    print_threshold_report(
        "検証ベスト詳細",
        df_bt,
        df_explore,
        df_oos,
        best_oos["proba"],
        best_oos["edge"],
        best_oos["odds"],
        odds_max=ODDS_CAP,
        show_monthly=True,
    )
else:
    best_oos = pick_best_oos(grid_dual, min_recovery_oos=0, min_buys_oos=OOS_MIN_BUYS)
    if best_oos is not None:
        print(
            f"\n【参考】検証期間で最もマシな条件（100%未満）: "
            f"proba>={best_oos['proba']:.2f}, edge>={best_oos['edge']:.2f}, "
            f"odds>={best_oos['odds']:.0f} → 検証回収率{best_oos['recovery_oos']:.1f}%"
        )
    else:
        print("\n検証期間で十分な購入件数の組み合わせがありません。")

# 探索期間だけで選んだ場合の落とし穴（対比）
grid_explore_only = grid_dual.copy()
if len(grid_explore_only) > 0:
    trap = pick_best_explore(grid_explore_only)
    if trap is not None:
        print(
            f"\n【対比・探索期間だけで選ぶと危険】"
            f"proba>={trap['proba']:.2f}, edge>={trap['edge']:.2f}, odds>={trap['odds']:.0f}"
        )
        print(
            f"  探索回収率 {trap['recovery_exp']:.1f}% → "
            f"検証回収率 {trap['recovery_oos']:.1f}% "
            f"（差 {trap['recovery_exp'] - trap['recovery_oos']:+.0f}pt）"
        )
        if trap["recovery_exp"] >= 100 and trap["recovery_oos"] < 100:
            print("  → 全テストがプラスに見えても検証で破綻する典型パターン")

# --- 固定条件（グリッドに含めない・事前指定）---
print("\n" + "=" * 60)
print("固定条件の比較（いずれもグリッド未使用）")
print("=" * 60)

FIXED_STRATEGIES = [
    ("旧条件・大穴寄り（非推奨例）", 0.40, 0.10, 8, None),
    ("中穴上限15", 0.35, 0.08, 4, 15),
    ("人気寄り上限8", 0.35, 0.05, 3, 8),
    ("堅め上限10", 0.40, 0.08, 4, 10),
]

for name, p, e, o, cap in FIXED_STRATEGIES:
    print_threshold_report(
        name, df_bt, df_explore, df_oos, p, e, o, odds_max=cap, show_monthly=False
    )

print("\n" + "=" * 60)
print("【採用の原則】")
print("  1. 検証期間（後半）の回収率が100%以上か")
print("  2. 探索期間が好成績でも、検証が100%未満なら不採用")
print("  3. 全テストの数字は前後半を混ぜた参考値（信頼しない）")
print("  4. odds上限で大穴依存を抑える（グリッドは既定で上限15）")
print("=" * 60)
