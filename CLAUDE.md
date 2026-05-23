# 競馬予測システム - Claude Code 自動タスク

## 自動実行タスク

Claude Codeが起動したら以下を自動で実行してください。

### 直近データのスクレイピング

今日の日付を確認し、直近7日分のスクレイピングを実行する。

```
set SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImluZnlwdW1pZ2V4bXBkbWlqaG54Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3OTM3MTE3MywiZXhwIjoyMDk0OTQ3MTczfQ.gSyqwb0w_mfo0DYO6BlxUizmr3y38oomqU3uq2R8juU
cd %USERPROFILE%\Downloads
python scraper.py <7日前YYYYMMDD> <今日YYYYMMDD>
```

## プロジェクト情報

- Supabase: https://infypumigexmpdmijhnx.supabase.co
- スクリプト: scraper.py（Downloadsフォルダ）
- 対象: JRA平地レース（土日のみ）

## 注意事項

- 平日は0レースで正常
- 同じ日を2回実行してもupsertなので重複しない
- IPブロック時は数時間待って再実行
