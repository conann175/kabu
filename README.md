# kabu

競馬予測システムのスクレイピング、特徴量SQL、学習・予測Notebook、設計資料をまとめたリポジトリです。

## ディレクトリ構成

```text
kabu/
├── README.md
├── src/
│   └── netkeiba_scraper.py        # netkeiba scraping -> Supabase
├── sql/
│   └── feature_views.sql          # feature view definitions
├── notebooks/
│   ├── netkeiba_colab.ipynb       # data collection notebook
│   ├── keiba_train.ipynb          # model training notebook
│   ├── keiba_predict.ipynb        # prediction notebook
│   └── keiba_backtest.ipynb       # backtest notebook
├── docs/
│   ├── system_design.md
│   ├── db_design.md
│   ├── feature_design.md
│   ├── model_design.md
│   ├── backtest_design.md
│   ├── ops_manual.md
│   ├── glossary.md
│   ├── minutes.md
│   └── exports/                   # generated PDF/DOCX artifacts
└── .github/
    └── workflows/
        └── scrape.yml             # scheduled scraper workflow
```

## 主要ファイル

- [src/netkeiba_scraper.py](src/netkeiba_scraper.py): netkeiba からレース・馬・払戻情報を取得して Supabase に保存します。
- [sql/feature_views.sql](sql/feature_views.sql): 学習・予測で使う特徴量ビューを定義します。
- [notebooks/keiba_train.ipynb](notebooks/keiba_train.ipynb): LightGBM モデルの学習Notebookです。
- [notebooks/keiba_predict.ipynb](notebooks/keiba_predict.ipynb): 予測と購入候補出力のNotebookです。
- [notebooks/keiba_backtest.ipynb](notebooks/keiba_backtest.ipynb): 回収率シミュレーション用Notebookです。

## 実行例

```bash
python src/netkeiba_scraper.py 20240101 20240131
```

引数を省略すると前日分を取得します。
