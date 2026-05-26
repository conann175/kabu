"""
競馬予測システム 共通設定
feature_colsはここだけで管理する
"""
from dotenv import load_dotenv
load_dotenv()
FEATURE_COLS = [
    # レース条件
    'venue_code_enc', 'distance', 'surface_enc', 'track_condition_enc',
    'weather_enc', 'post_position', 'entry_count', 'class_enc', 'popularity_ratio',
    # 馬の過去成績
    'avg_finish_3', 'avg_finish_5', 'fukusho_rate_3', 'fukusho_rate_5',
    'last_finish', 'rest_days', 'distance_diff', 'last_time_diff',
    'weight', 'weight_diff',
    # 騎手・調教師
    'jockey_fukusho_rate', 'trainer_fukusho_rate',
    # 血統
    'father_fukusho_rate', 'mother_father_fukusho_rate', 'nick_fukusho_rate',
]

SUPABASE_URL = "https://infypumigexmpdmijhnx.supabase.co"
