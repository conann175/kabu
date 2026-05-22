# 運用手順書

---

## 1. システム構成

| ツール | URL/場所 | 用途 |
|--------|----------|------|
| Google Colab | colab.research.google.com | スクレイピング・モデル学習 |
| Supabase | infypumigexmpdmijhnx.supabase.co | データ確認・SQL実行 |
| GitHub | github.com/conann175/kabu | スクリプト管理 |

---

## 2. 過去データ一括取得（初回のみ）

1. Google Colabを開く
2. `netkeiba_colab.ipynb` をアップロード
3. セル①〜③を順番に実行
4. セル④の日付を変更して実行（年ごとに分割推奨）

### 推奨分割実行
| 回 | 開始日 | 終了日 | 目安時間 |
|----|--------|--------|----------|
| 1回目 | 20240101 | 20240630 | 約4時間 |
| 2回目 | 20240701 | 20241231 | 約4時間 |
| 3回目 | 20250101 | 20250630 | 約4時間 |
| 4回目 | 20250701 | 20251231 | 約4時間 |
| 5回目 | 20260101 | 直近 | 約2〜3時間 |

> ⚠️ Colabは最大12時間でセッション切断。切れた場合は続きの日付から再実行。

---

## 3. 直近データ取得（週次）

毎週月曜日などに前週分を取得します。

```python
# 例：5/17（土）〜5/18（日）
scrape_date_range('20260517', '20260518')
```

---

## 4. Supabaseデータ確認

### データ件数確認
```sql
SELECT 'races' AS tbl, COUNT(*) AS cnt FROM races UNION ALL
SELECT 'race_entries', COUNT(*) FROM race_entries UNION ALL
SELECT 'horses', COUNT(*) FROM horses UNION ALL
SELECT 'payouts', COUNT(*) FROM payouts UNION ALL
SELECT 'jockeys', COUNT(*) FROM jockeys UNION ALL
SELECT 'trainers', COUNT(*) FROM trainers
ORDER BY tbl;
```

### 直近データ確認
```sql
SELECT race_date, COUNT(*) as race_count
FROM races
ORDER BY race_date DESC
LIMIT 10;
```

### 異常データ確認
```sql
-- racesに登録されていないrace_entriesの確認
SELECT COUNT(*) FROM race_entries e
LEFT JOIN races r ON e.race_id = r.race_id
WHERE r.race_id IS NULL;
```

---

## 5. トラブルシューティング

| 症状 | 対処法 |
|------|--------|
| 403 Forbiddenが出る | しばらく待ってから再実行 |
| Colabセッションが切れた | 最後のdone表示の翌日から再実行 |
| Supabase 401エラー | SUPABASE_KEYがservice_role keyか確認 |
| 外部キーエラー | racesテーブルへの登録が失敗している |
| データが重複 | upsertのため重複登録はされない。問題なし |
