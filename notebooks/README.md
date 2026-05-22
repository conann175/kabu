# 🐴 競馬予測システム

複勝（3着以内）の期待値を算出し、長期的な回収率プラスを目指す機械学習予測システムです。

---

## 概要

| 項目 | 内容 |
|------|------|
| 対象 | JRA平地レース（障害除外） |
| 予測 | 複勝（3着以内）の確率 |
| モデル | LightGBM |
| DB | Supabase（PostgreSQL） |
| スクレイピング元 | db.netkeiba.com |

## ディレクトリ構成

```
kabu/
├── README.md
├── netkeiba_scraper.py          # スクレイピングスクリプト
├── feature_views.sql            # 特徴量SQLビュー定義
├── notebooks/
│   ├── netkeiba_colab.ipynb     # データ取得用Colab
│   ├── keiba_train.ipynb        # モデル学習用Colab
│   ├── keiba_backtest.ipynb     # バックテスト用Colab
│   └── keiba_predict.ipynb      # 期待値算出・出力用Colab
├── docs/
│   ├── system_design.md         # システム設計書
│   ├── db_design.md             # DB設計書
│   ├── feature_design.md        # 特徴量エンジニアリング設計書
│   ├── model_design.md          # モデル設計書
│   ├── ops_manual.md            # 運用手順書
│   ├── backtest_design.md       # バックテスト設計書
│   ├── glossary.md              # 用語集
│   └── minutes.md               # 議事録・決定事項
└── .github/workflows/
    └── scrape.yml               # GitHub Actions（現在未使用）
```

## 進捗状況

- [x] Supabaseプロジェクト作成・テーブル設計
- [x] スクレイピングスクリプト作成・動作確認
- [x] 各種設計書・Colabノートブック作成
- [ ] 過去2〜3年分データ取り込み
- [ ] SQLビュー実行・特徴量確認
- [ ] LightGBMモデル学習
- [ ] バックテスト実施
- [ ] 実運用開始

## 期待値算出式

```
期待値 = 予測複勝率 × 複勝オッズ
期待値 > 1.0 → 買い
```

## 注意事項

- 本システムは個人・非商用利用を前提としています
- 馬券購入はご自身の判断と責任で行ってください
