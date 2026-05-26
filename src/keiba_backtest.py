"""
競馬予測バックテストスクリプト
使い方: python keiba_backtest.py
※ keiba_train.py を先に実行してモデルを保存しておくこと
"""

import os, re, joblib, glob
import pandas as pd
import numpy as np
from supabase import create_client
from sklearn.preprocessing import LabelEncoder
import warnings
from config import FEATURE_COLS
warnings.filterwarnings('ignore')

# ============================================================
# 設定
# ============================================================
SUPABASE_URL = "https://infypumigexmpdmijhnx.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

TRAIN_END = "2024-08-31"
VALID_END = "2024-10-31"

# ============================================================
# モデル読み込み
# ============================================================
model_files = sorted(
    [f for f in glob.glob("keiba_model_*.pkl") if re.match(r"keiba_model_\d{8}\.pkl$", f)],
    reverse=True
)
if not model_files:
    print("❌ モデルファイルが見つかりません。先にkeiba_train.pyを実行してください。")
    exit()
model_path = model_files[0]
model = joblib.load(model_path)
feat_path = model_path.replace('.pkl', '_features.pkl')
feature_cols = FEATURE_COLS
if os.path.exists(feat_path):
    feature_cols = joblib.load(feat_path)
    print(f"✅ モデル読み込み: {model_path} ({len(feature_cols)}特徴量)")
else:
    print(f"✅ モデル読み込み: {model_path}")
    print("⚠️ 特徴量ファイルなし - config.pyのFEATURE_COLSを使用")

# ============================================================
# データ取得
# ============================================================
print("\nSupabase接続中...")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def fetch_all(table, select):
    all_data = []
    offset = 0
    while True:
        q = supabase.table(table).select(select)
        if table == 'race_entries':
            q = q.not_.is_('finish_position', 'null')
        res = q.range(offset, offset+999).execute()
        if not res.data: break
        all_data.extend(res.data)
        offset += 1000
        print(f"  {table}: {len(all_data)}件...", end='\r')
    print(f"  {table}: {len(all_data)}件 ✅")
    return pd.DataFrame(all_data)

print("データ取得中...")
df_entries = fetch_all('race_entries',
    'entry_id,race_id,horse_id,jockey_id,trainer_id,'
    'post_position,horse_number,finish_position,finish_time,'
    'odds,popularity,weight,weight_diff')

df_races = fetch_all('races',
    'race_id,race_date,venue_code,distance,surface,'
    'track_condition,weather,class')
df_races['race_date'] = pd.to_datetime(df_races['race_date'])

df_peds = fetch_all('horse_pedigrees', 'horse_id,father,mother_father')

# 払い戻しデータ
print("  payouts取得中...")
all_pays = []
offset = 0
while True:
    res = supabase.table('payouts').select(
        'race_id,bet_type,combination,payout'
    ).eq('bet_type', '複勝').range(offset, offset+999).execute()
    if not res.data: break
    all_pays.extend(res.data)
    offset += 1000
df_pay = pd.DataFrame(all_pays) if all_pays else pd.DataFrame(columns=['race_id','combination','payout'])
if len(df_pay) > 0:
    df_pay['horse_number'] = pd.to_numeric(df_pay['combination'], errors='coerce')
    df_pay['fukusho_odds'] = df_pay['payout'] / 100
    df_pay = df_pay.drop_duplicates(subset=['race_id', 'horse_number'])
print(f"  payouts: {len(df_pay)}件 ✅")

# 結合
df = df_entries.merge(df_races, on='race_id', how='inner')
df = df.merge(df_peds, on='horse_id', how='left')
df['entry_count'] = df.groupby('race_id')['race_id'].transform('count')
df['target'] = (df['finish_position'].astype(float) <= 3).astype(int)
df = df.sort_values(['race_date', 'race_id', 'horse_number']).reset_index(drop=True)

# ============================================================
# 特徴量計算（学習データのみで統計）
# ============================================================
train_mask = df['race_date'] <= TRAIN_END
valid_mask = (df['race_date'] > TRAIN_END) & (df['race_date'] <= VALID_END)
test_mask  = df['race_date'] > VALID_END

train_df = df[train_mask].copy()

def calc_stats(df_train, group_cols, min_count=10):
    stats = df_train.groupby(group_cols)['target'].agg(['mean', 'count'])
    stats.columns = ['rate', 'count']
    stats.loc[stats['count'] < min_count, 'rate'] = np.nan
    return stats['rate']

print("\n特徴量計算中...")
jockey_venue = calc_stats(train_df, ['jockey_id', 'venue_code', 'surface'])
jockey_all   = calc_stats(train_df, ['jockey_id'], min_count=20)
trainer_st   = calc_stats(train_df, ['trainer_id', 'surface'])
father_st    = calc_stats(train_df, ['father', 'surface'])
mf_st        = calc_stats(train_df, ['mother_father', 'surface'])
nick_st      = calc_stats(train_df, ['father', 'mother_father', 'surface'])

# 平均タイム計算（学習データから・last_time_diff特徴量に使用）
def parse_time(t):
    try:
        parts = str(t).split(':')
        return float(parts[0])*60 + float(parts[1]) if len(parts)==2 else float(parts[0])
    except:
        return np.nan

train_df['finish_sec'] = train_df['finish_time'].apply(parse_time)
avg_time_map = train_df.groupby(['distance', 'surface'])['finish_sec'].mean().to_dict()

# 馬の過去成績
print("  馬の過去成績計算中...")
df_sorted = df.sort_values(['horse_id', 'race_date', 'race_id'])
horse_records = {k: [] for k in ['entry_id','avg_finish_3','fukusho_rate_3',
                                   'avg_finish_5','fukusho_rate_5',
                                   'last_finish','rest_days','distance_diff','last_time_diff']}
grouped = df_sorted.groupby('horse_id')
for i, (horse_id, grp) in enumerate(grouped):
    grp = grp.reset_index(drop=True)
    for j, row in grp.iterrows():
        past = grp[grp['race_date'] < row['race_date']]
        p3 = past.tail(3)
        p5 = past.tail(5)
        horse_records['entry_id'].append(row['entry_id'])
        horse_records['avg_finish_3'].append(p3['finish_position'].astype(float).mean() if len(p3)>0 else np.nan)
        horse_records['fukusho_rate_3'].append(p3['target'].mean() if len(p3)>0 else np.nan)
        horse_records['avg_finish_5'].append(p5['finish_position'].astype(float).mean() if len(p5)>0 else np.nan)
        horse_records['fukusho_rate_5'].append(p5['target'].mean() if len(p5)>0 else np.nan)
        horse_records['last_finish'].append(float(past.iloc[-1]['finish_position']) if len(past)>0 else np.nan)
        horse_records['rest_days'].append((row['race_date'] - past.iloc[-1]['race_date']).days if len(past)>0 else np.nan)
        horse_records['distance_diff'].append((row['distance'] - past.iloc[-1]['distance']) if len(past)>0 else np.nan)
        # 前走タイム差：前走のfinish_timeと距離から計算
        if len(past) > 0:
            last_row = past.iloc[-1]
            try:
                t = str(last_row['finish_time'])
                parts = t.split(':')
                last_sec = float(parts[0])*60 + float(parts[1]) if len(parts)==2 else float(parts[0])
                last_dist = last_row['distance']
                last_surf = last_row['surface']
                avg_sec = avg_time_map.get((last_dist, last_surf), None)
                horse_records['last_time_diff'].append(last_sec - avg_sec if avg_sec else np.nan)
            except:
                horse_records['last_time_diff'].append(np.nan)
        else:
            horse_records['last_time_diff'].append(np.nan)
    if i % 500 == 0:
        print(f"    {i}/{len(grouped)}頭...", end='\r')

df_horse = pd.DataFrame(horse_records)
df = df.merge(df_horse, on='entry_id', how='left')

# 統計適用
df['jockey_fukusho_rate'] = df.apply(
    lambda r: jockey_venue.get((r['jockey_id'], r['venue_code'], r['surface']),
              jockey_all.get(r['jockey_id'], np.nan)), axis=1)
df['trainer_fukusho_rate']       = df.apply(lambda r: trainer_st.get((r['trainer_id'], r['surface']), np.nan), axis=1)
df['father_fukusho_rate']        = df.apply(lambda r: father_st.get((r['father'], r['surface']), np.nan), axis=1)
df['mother_father_fukusho_rate'] = df.apply(lambda r: mf_st.get((r['mother_father'], r['surface']), np.nan), axis=1)
df['nick_fukusho_rate']          = df.apply(lambda r: nick_st.get((r['father'], r['mother_father'], r['surface']), np.nan), axis=1)
df['popularity_ratio']           = df['popularity'].astype(float) / df['entry_count']

cat_cols = ['venue_code', 'surface', 'track_condition', 'weather', 'class']
enc_path = model_path.replace('.pkl', '_encoders.pkl')
if os.path.exists(enc_path):
    encoders = joblib.load(enc_path)
    for col in cat_cols:
        le = encoders[col]
        known = {c: i for i, c in enumerate(le.classes_)}
        df[col+'_enc'] = df[col].fillna('unknown').astype(str).map(known).fillna(-1).astype(int)
else:
    for col in cat_cols:
        le = LabelEncoder()
        df[col+'_enc'] = le.fit_transform(df[col].fillna('unknown').astype(str))

print("✅ 特徴量計算完了")

# ============================================================
# 予測・バックテスト
# ============================================================
print("\nバックテスト実行中...")

# テストデータで予測
df_test = df[test_mask].copy()
X_test = df_test[feature_cols].astype(float)
df_test['proba'] = model.predict_proba(X_test)[:, 1]

# proba分布確認
print("\n予測確率の分布:")
print(df_test['proba'].describe().round(4))
print(f"\n実際の複勝率（3着以内）: {df_test['target'].mean():.3f}")
print(f"予測確率0.5以上: {(df_test['proba'] >= 0.5).sum()}件")
print(f"予測確率0.8以上: {(df_test['proba'] >= 0.8).sum()}件")

# 払い戻しデータを結合
if len(df_pay) > 0:
    df_test = df_test.merge(
        df_pay[['race_id','horse_number','fukusho_odds']],
        on=['race_id','horse_number'], how='left'
    )
else:
    df_test['fukusho_odds'] = np.nan

# 期待値計算
# 期待値：単勝オッズから複勝オッズを推定して計算
# 複勝オッズ ≈ 単勝オッズ ^ 0.75 （経験則）
df_test['est_fukusho_odds'] = df_test['odds'] ** 0.75
df_test['expected_value'] = df_test['proba'] * df_test['est_fukusho_odds']

print(f"\nテストデータ: {len(df_test):,}件")
print(f"期間: {df_test.race_date.min().date()} 〜 {df_test.race_date.max().date()}")
print(f"複勝データあり: {df_test['fukusho_odds'].notna().sum():,}件")

# ============================================================
# 回収率シミュレーション
# payoutsがあるレースのみを対象とする（外れ=0円で正しく計算するため）
# ============================================================
if len(df_pay) > 0:
    races_with_payouts = set(df_pay['race_id'].unique())
else:
    races_with_payouts = set()

df_bt = df_test[df_test['race_id'].isin(races_with_payouts)].copy()
print(f"\nテストデータ全件: {len(df_test):,}件")
print(f"うちpayoutsあるレース: {len(df_bt):,}件")

if len(df_bt) == 0:
    print("❌ payoutsデータのあるテストレースが見つかりません。")
else:
    print("\n" + "=" * 58)
    print(f"{'proba閾値':>8} | {'購入数':>8} | {'的中率':>8} | {'回収率':>8} | {'損益':>10}")
    print("-" * 58)

    for threshold in [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]:
        buy = df_bt[
            (df_bt['proba'] >= threshold) &
            (df_bt['odds'].notna())
        ].copy()
        if len(buy) == 0:
            print(f"{threshold:>8.2f} | {'データなし':>30}")
            continue
        total_bet    = len(buy) * 100
        hit          = buy[buy['target'] == 1]
        # 外れは払い戻し0円。payoutsデータ欠損もfillna(0)で保守的に扱う
        total_return = (hit['fukusho_odds'].fillna(0) * 100).sum()
        hit_rate     = len(hit) / len(buy) * 100
        recovery     = total_return / total_bet * 100
        profit       = int(total_return - total_bet)
        print(f"{threshold:>8.2f} | {len(buy):>8,} | {hit_rate:>7.1f}% | {recovery:>7.1f}% | {profit:>+10,}円")

    print("=" * 58)

    # ============================================================
    # 月別回収率
    # ============================================================
    threshold_base = 0.40
    buy_base = df_bt[
        (df_bt['proba'] >= threshold_base) &
        (df_bt['odds'].notna())
    ].copy()

    if len(buy_base) > 0:
        buy_base['month'] = buy_base['race_date'].dt.to_period('M')
        print(f"\n月別回収率（proba>={threshold_base}）：")
        print(f"{'月':>10} | {'購入数':>6} | {'回収率':>8}")
        print("-" * 32)
        for month, grp in buy_base.groupby('month'):
            bet = len(grp) * 100
            ret = (grp[grp['target'] == 1]['fukusho_odds'].fillna(0) * 100).sum()
            rec = ret / bet * 100
            print(f"{str(month):>10} | {len(grp):>6,} | {rec:>7.1f}%")

    # ============================================================
    # 期待値フィルタ（proba高 × オッズ高の2次元グリッド）
    # 推定複勝オッズ = 単勝オッズ ^ 0.75（経験則）
    # 期待値 = proba × 推定複勝オッズ
    # ============================================================
    print("\n" + "=" * 70)
    print("期待値フィルタ: proba下限 × 推定期待値閾値")
    print("=" * 70)

    proba_floors  = [0.30, 0.35, 0.40]
    ev_thresholds = [0.8, 0.9, 1.0, 1.1, 1.2, 1.3]

    for pf in proba_floors:
        print(f"\n[proba >= {pf}]")
        print(f"{'EV閾値':>7} | {'購入数':>7} | {'的中率':>7} | {'回収率':>7} | {'損益':>10}")
        print("-" * 52)
        for ev in ev_thresholds:
            buy = df_bt[
                (df_bt['proba'] >= pf) &
                (df_bt['expected_value'] >= ev) &
                (df_bt['odds'].notna())
            ].copy()
            if len(buy) == 0:
                print(f"{ev:>7.1f} | {'データなし':>40}")
                continue
            total_bet    = len(buy) * 100
            hit          = buy[buy['target'] == 1]
            total_return = (hit['fukusho_odds'].fillna(0) * 100).sum()
            hit_rate     = len(hit) / len(buy) * 100
            recovery     = total_return / total_bet * 100
            profit       = int(total_return - total_bet)
            print(f"{ev:>7.1f} | {len(buy):>7,} | {hit_rate:>6.1f}% | {recovery:>6.1f}% | {profit:>+10,}円")

    # 推薦組み合わせの月別内訳
    best_pf = 0.35
    best_ev = 1.0
    buy_ev = df_bt[
        (df_bt['proba'] >= best_pf) &
        (df_bt['expected_value'] >= best_ev) &
        (df_bt['odds'].notna())
    ].copy()

    if len(buy_ev) > 0:
        buy_ev['month'] = buy_ev['race_date'].dt.to_period('M')
        print(f"\n月別回収率（proba>={best_pf} かつ EV>={best_ev}）：")
        print(f"{'月':>10} | {'購入数':>6} | {'的中率':>7} | {'回収率':>7}")
        print("-" * 42)
        for month, grp in buy_ev.groupby('month'):
            bet = len(grp) * 100
            hit = grp[grp['target'] == 1]
            ret = (hit['fukusho_odds'].fillna(0) * 100).sum()
            print(f"{str(month):>10} | {len(grp):>6,} | {len(hit)/len(grp)*100:>6.1f}% | {ret/bet*100:>6.1f}%")

# ============================================================
# 三連複バックテスト（上位N頭ボックス買い）
# ============================================================
from itertools import combinations as itercombs

print("\n" + "=" * 65)
print("三連複バックテスト（上位N頭ボックス買い）")
print("=" * 65)

all_san = []
offset = 0
while True:
    res = supabase.table('payouts').select('race_id,combination,payout').eq('bet_type', '三連複').range(offset, offset + 999).execute()
    if not res.data:
        break
    all_san.extend(res.data)
    offset += 1000

df_san = pd.DataFrame(all_san) if all_san else pd.DataFrame(columns=['race_id', 'combination', 'payout'])
print(f"三連複payouts: {len(df_san)}件")

if len(df_san) > 0:
    san_dict = {}
    for _, row in df_san.iterrows():
        horses = frozenset(int(x.strip()) for x in row['combination'].split('-'))
        san_dict[row['race_id']] = (horses, row['payout'])

    races_with_san = set(san_dict.keys())
    df_bt_san = df_test[df_test['race_id'].isin(races_with_san)].copy()
    print(f"テストレース数（三連複あり）: {df_bt_san['race_id'].nunique():,}")

    print(f"\n{'上位N頭':>8} | {'組合数':>6} | {'レース数':>8} | {'的中率':>8} | {'回収率':>8} | {'損益':>12}")
    print("-" * 65)

    for n_horses in [3, 4, 5, 6]:
        n_combos = len(list(itercombs(range(n_horses), 3)))
        total_bet = 0
        total_return = 0
        hits = 0
        races_played = 0

        for race_id, race_df in df_bt_san.groupby('race_id'):
            top_n = race_df.nlargest(n_horses, 'proba')['horse_number'].dropna().astype(int).tolist()
            if len(top_n) < 3 or race_id not in san_dict:
                continue
            combs = list(itercombs(sorted(top_n), 3))
            total_bet += len(combs) * 100
            races_played += 1
            winning_set, payout = san_dict[race_id]
            for comb in combs:
                if frozenset(comb) == winning_set:
                    total_return += payout
                    hits += 1
                    break

        if races_played > 0 and total_bet > 0:
            hit_rate = hits / races_played * 100
            recovery = total_return / total_bet * 100
            profit = int(total_return - total_bet)
            print(f"{n_horses:>8}頭 | {n_combos:>6} | {races_played:>8,} | {hit_rate:>7.1f}% | {recovery:>7.1f}% | {profit:>+12,}円")

    print("=" * 65)

    best_n = 4
    buy_san_monthly = []
    for race_id, race_df in df_bt_san.groupby('race_id'):
        top_n = race_df.nlargest(best_n, 'proba')['horse_number'].dropna().astype(int).tolist()
        if len(top_n) < 3 or race_id not in san_dict:
            continue
        combs = list(itercombs(sorted(top_n), 3))
        winning_set, payout = san_dict[race_id]
        hit = any(frozenset(c) == winning_set for c in combs)
        race_date = race_df['race_date'].iloc[0]
        buy_san_monthly.append({
            'race_id': race_id, 'race_date': race_date,
            'n_combs': len(combs), 'hit': hit, 'payout': payout if hit else 0
        })

    if buy_san_monthly:
        df_monthly_san = pd.DataFrame(buy_san_monthly)
        df_monthly_san['month'] = pd.to_datetime(df_monthly_san['race_date']).dt.to_period('M')
        print(f"\n月別回収率（上位{best_n}頭ボックス）：")
        print(f"{'月':>10} | {'レース数':>8} | {'的中率':>8} | {'回収率':>8}")
        print("-" * 44)
        for month, grp in df_monthly_san.groupby('month'):
            bet = grp['n_combs'].sum() * 100
            ret = grp['payout'].sum()
            hit_r = grp['hit'].mean() * 100
            rec = ret / bet * 100 if bet > 0 else 0
            print(f"{str(month):>10} | {len(grp):>8,} | {hit_r:>7.1f}% | {rec:>7.1f}%")
