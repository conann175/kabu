"""
競馬予測モデル学習スクリプト（データリーク対策版）
使い方: python keiba_train.py
"""

import os, json, joblib
import pandas as pd
import numpy as np
from datetime import datetime
from supabase import create_client
import lightgbm as lgb
from sklearn.metrics import roc_auc_score
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
# Supabase接続
# ============================================================
print("Supabase接続中...")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
print("✅ 接続OK")

# ============================================================
# データ取得
# ============================================================
def fetch_all(table, select, filters=None):
    all_data = []
    offset = 0
    while True:
        q = supabase.table(table).select(select)
        if filters:
            for k, v in filters.items():
                q = q.eq(k, v)
        res = q.not_.is_('finish_position', 'null').range(offset, offset+999).execute() \
            if table == 'race_entries' else q.range(offset, offset+999).execute()
        if not res.data: break
        all_data.extend(res.data)
        offset += 1000
        print(f"  {table}: {len(all_data)}件取得中...", end='\r')
    print(f"  {table}: {len(all_data)}件 ✅")
    return pd.DataFrame(all_data)

print("\nデータ取得中...")
df_entries = fetch_all('race_entries',
    'entry_id,race_id,horse_id,jockey_id,trainer_id,'
    'post_position,horse_number,finish_position,finish_time,'
    'odds,popularity,weight,weight_diff')

df_races = fetch_all('races',
    'race_id,race_date,venue_code,distance,surface,'
    'track_condition,weather,class')
df_races['race_date'] = pd.to_datetime(df_races['race_date'])

df_peds = fetch_all('horse_pedigrees', 'horse_id,father,mother_father')

# 結合
df = df_entries.merge(df_races, on='race_id', how='inner')
df = df.merge(df_peds, on='horse_id', how='left')
df['entry_count'] = df.groupby('race_id')['race_id'].transform('count')
df['target'] = (df['finish_position'].astype(float) <= 3).astype(int)
df = df.sort_values(['race_date', 'race_id', 'horse_number']).reset_index(drop=True)

print(f"\n✅ データ結合完了: {len(df):,}件")
print(f"期間: {df.race_date.min().date()} 〜 {df.race_date.max().date()}")

# ============================================================
# 時系列分割
# ============================================================
train_mask = df['race_date'] <= TRAIN_END
valid_mask = (df['race_date'] > TRAIN_END) & (df['race_date'] <= VALID_END)
test_mask  = df['race_date'] > VALID_END

print(f"\n学習: {train_mask.sum():,}件")
print(f"検証: {valid_mask.sum():,}件")
print(f"テスト: {test_mask.sum():,}件")

# ============================================================
# データリーク対策：学習データのみで統計を計算
# ============================================================
print("\n特徴量計算中（学習データのみ）...")

train_df = df[train_mask].copy()

def calc_stats(df_train, group_cols, min_count=10):
    stats = df_train.groupby(group_cols)['target'].agg(['mean', 'count'])
    stats.columns = ['rate', 'count']
    stats.loc[stats['count'] < min_count, 'rate'] = np.nan
    return stats['rate']

# 各種統計を計算
jockey_venue = calc_stats(train_df, ['jockey_id', 'venue_code', 'surface'])
jockey_all   = calc_stats(train_df, ['jockey_id'], min_count=20)
trainer_st   = calc_stats(train_df, ['trainer_id', 'surface'])
father_st    = calc_stats(train_df, ['father', 'surface'])
mf_st        = calc_stats(train_df, ['mother_father', 'surface'])
nick_st      = calc_stats(train_df, ['father', 'mother_father', 'surface'])

print("  騎手・調教師・血統統計 ✅")

# finish_timeを秒に変換
def time_to_sec(t):
    try:
        if not t or str(t) == 'nan':
            return None
        parts = str(t).split(':')
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except:
        return None

df['finish_sec'] = df['finish_time'].apply(time_to_sec)

# 距離×芝ダ別の平均タイムを学習データで計算
print("  距離×コース別平均タイムを計算中...")
avg_time = train_df.copy()
avg_time['finish_sec'] = avg_time['finish_time'].apply(time_to_sec)
avg_time_map = avg_time.groupby(['distance', 'surface'])['finish_sec'].mean().to_dict()

df['avg_time_by_course'] = df.apply(
    lambda r: avg_time_map.get((r['distance'], r['surface']), None), axis=1
)
df['time_diff_from_avg'] = df['finish_sec'] - df['avg_time_by_course']

# 馬の過去成績（対象レースより前のみ）
print("  馬の過去成績計算中（時間かかります）...")
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
        horse_records['avg_finish_3'].append(p3['finish_position'].astype(float).mean() if len(p3) > 0 else np.nan)
        horse_records['fukusho_rate_3'].append(p3['target'].mean() if len(p3) > 0 else np.nan)
        horse_records['avg_finish_5'].append(p5['finish_position'].astype(float).mean() if len(p5) > 0 else np.nan)
        horse_records['fukusho_rate_5'].append(p5['target'].mean() if len(p5) > 0 else np.nan)
        horse_records['last_finish'].append(float(past.iloc[-1]['finish_position']) if len(past) > 0 else np.nan)
        horse_records['rest_days'].append((row['race_date'] - past.iloc[-1]['race_date']).days if len(past) > 0 else np.nan)
        horse_records['distance_diff'].append((row['distance'] - past.iloc[-1]['distance']) if len(past) > 0 else np.nan)
        horse_records['last_time_diff'].append(past.iloc[-1]['time_diff_from_avg'] if len(past) > 0 else np.nan)
    if i % 500 == 0:
        print(f"    {i}/{len(grouped)}頭完了...", end='\r')

df_horse = pd.DataFrame(horse_records)
df = df.merge(df_horse, on='entry_id', how='left')
print("  馬の過去成績 ✅                    ")

# 統計を全データに適用
def apply_multi(row, stats, keys):
    key = tuple(row[k] for k in keys)
    return stats.get(key, np.nan)

df['jockey_fukusho_rate'] = df.apply(
    lambda r: jockey_venue.get((r['jockey_id'], r['venue_code'], r['surface']),
              jockey_all.get(r['jockey_id'], np.nan)), axis=1)
df['trainer_fukusho_rate']       = df.apply(lambda r: trainer_st.get((r['trainer_id'], r['surface']), np.nan), axis=1)
df['father_fukusho_rate']        = df.apply(lambda r: father_st.get((r['father'], r['surface']), np.nan), axis=1)
df['mother_father_fukusho_rate'] = df.apply(lambda r: mf_st.get((r['mother_father'], r['surface']), np.nan), axis=1)
df['nick_fukusho_rate']          = df.apply(lambda r: nick_st.get((r['father'], r['mother_father'], r['surface']), np.nan), axis=1)
df['popularity_ratio']           = df['popularity'].astype(float) / df['entry_count']

print("✅ 特徴量計算完了")

# ============================================================
# LightGBM学習
# ============================================================
print("\nLightGBM学習中...")

cat_cols = ['venue_code', 'surface', 'track_condition', 'weather', 'class']
encoders = {}
for col in cat_cols:
    le = LabelEncoder()
    df[col+'_enc'] = le.fit_transform(df[col].fillna('unknown').astype(str))
    encoders[col] = le

feature_cols = FEATURE_COLS

X_train = df[train_mask][feature_cols].astype(float)
y_train = df[train_mask]['target']
X_valid = df[valid_mask][feature_cols].astype(float)
y_valid = df[valid_mask]['target']
X_test  = df[test_mask][feature_cols].astype(float)
y_test  = df[test_mask]['target']

params = {
    'objective': 'binary', 'metric': 'auc',
    'num_leaves': 31, 'learning_rate': 0.05,
    'feature_fraction': 0.8, 'bagging_fraction': 0.8,
    'min_child_samples': 20, 'class_weight': 'balanced',
    'verbose': -1,
}

model = lgb.LGBMClassifier(**params, n_estimators=1000)
model.fit(
    X_train, y_train,
    eval_set=[(X_valid, y_valid)],
    callbacks=[lgb.early_stopping(50), lgb.log_evaluation(50)]
)

if len(X_test) > 0:
    auc = roc_auc_score(y_test, model.predict_proba(X_test)[:, 1])
    print(f'\n✅ テストAUC: {auc:.4f}')
else:
    print('\n⚠️ テストデータなし（データが少ない可能性）')

# ============================================================
# 特徴量重要度
# ============================================================
print("\n特徴量重要度 TOP10:")
importance = pd.Series(model.feature_importances_, index=feature_cols)
for feat, imp in importance.nlargest(10).items():
    print(f"  {feat}: {imp}")

# ============================================================
# モデル保存
# ============================================================
date_str = datetime.now().strftime('%Y%m%d')
fname = f'keiba_model_{date_str}.pkl'
joblib.dump(model, fname)
feat_path = fname.replace('.pkl', '_features.pkl')
joblib.dump(feature_cols, feat_path)
enc_path = fname.replace('.pkl', '_encoders.pkl')
joblib.dump(encoders, enc_path)
print(f'\n✅ モデル保存: {fname}')
print(f'✅ 特徴量保存: {feat_path}')
print(f'✅ エンコーダ保存: {enc_path}')
