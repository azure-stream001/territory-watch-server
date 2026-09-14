# Territory Watch Japan — サーバー

**Territory Watch Japan** のバックエンド API / 衛星画像解析基盤です。

Django REST Framework を中心に、監視エリア、衛星シーン、検知ジョブ・結果を管理し、Sentinel-2 衛星画像を利用した土地利用変化・森林伐採等の検知処理を提供します。

## 概要

本サーバーは以下の機能を提供します。

- 監視エリア API
- 衛星シーン API
- 検知ジョブ / 検知結果 API
- JWT 認証
- OpenAPI / Swagger / ReDoc
- Copernicus Data Space Ecosystem による Sentinel-2 データ検索・取得
- 衛星画像の前処理
- NDVI 計算
- NDVI 変化による森林減少・土地利用変化の検知
- 検知ポリゴンの GeoJSON 生成
- 空間パターン・面積分析
- 違反可能性の評価
- 精度評価
- 衛星シーンのプレビュー
- 地図タイル提供
- Sentinel Hub を利用した NDVI 変化 API
- クイック検知 API
- PostgreSQL によるデータ管理
- Redis / Celery によるバックグラウンド処理基盤

## システム構成

```text
Next.js クライアント
       │
       │ REST / JSON
       ▼
Django + Django REST Framework
       │
       ├── 監視エリア
       ├── 検知ジョブ / 結果
       ├── 衛星シーン
       ├── OpenAPI
       │
       ├──────────────► PostgreSQL
       │
       ├──────────────► Redis / Celery
       │
       └──────────────► Copernicus Data Space
                            │
                            └── Sentinel-2

オプション:
Django ────────────────► Sentinel Hub Processing API
```

## 技術スタック

### Web / API

- Python
- Django 4.2+
- Django REST Framework
- Simple JWT
- drf-spectacular
- django-cors-headers

### データベース / ジョブ

- PostgreSQL
- Redis
- Celery

### 衛星画像 / GIS

- Copernicus Data Space Ecosystem
- Sentinel-2
- Sentinel Hub Processing API（オプション）
- rasterio
- GeoPandas
- Shapely
- SentinelSat

### 画像解析 / 機械学習

- NumPy
- OpenCV
- scikit-image
- scikit-learn
- XGBoost

### 可視化

- Folium
- Matplotlib
- Plotly

## 必要環境

推奨環境：

- Python 3.10 以上
- PostgreSQL
- Redis
- Sentinel-2 データ取得用の Copernicus Data Space アカウント

Sentinel Hub を利用する機能については、別途 Sentinel Hub の OAuth クライアント設定が必要です。

## インストール

仮想環境を作成します。

```bash
python -m venv .venv
```

macOS / Linux:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

依存パッケージをインストールします。

```bash
pip install -r requirements.txt
```

## 環境変数

サンプルファイルをコピーします。

```bash
cp .env.example .env
```

### PostgreSQL

```env
PGDATABASE=territory_watch
PGUSER=postgres
PGPASSWORD=your_password
PGHOST=localhost
PGPORT=5432
```

### Django

```env
DJANGO_SECRET_KEY=your-secret-key
DEBUG=1
ALLOWED_HOSTS=localhost,127.0.0.1
CORS_ORIGINS=http://localhost:3333
```

### Redis / Celery

```env
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

## Copernicus Data Space の設定

Sentinel-2 データの検索・ダウンロードには Copernicus Data Space Ecosystem の認証情報を利用します。

推奨される設定：

```env
CDSE_USERNAME=your_dataspace_login_email
CDSE_PASSWORD=your_dataspace_password
```

OAuth クライアントを使用する場合：

```env
CDSE_CLIENT_ID=your_client_id
CDSE_CLIENT_SECRET=your_client_secret
```

両方が設定されている場合、ダウンロード処理ではユーザー名 / パスワードによる認証が優先されます。

## Sentinel Hub（オプション）

NDVI Processing API を利用する場合は、以下を設定します。

```env
SENTINELHUB_CLIENT_ID=your_sentinelhub_client_id
SENTINELHUB_CLIENT_SECRET=your_sentinelhub_client_secret
```

## データベースのセットアップ

`territory_watch` という PostgreSQL データベースを作成するか、`PGDATABASE` を既存のデータベースに設定します。

マイグレーションを実行します。

```bash
python manage.py migrate
```

データベース接続を確認する場合：

```bash
python manage.py check_db
```

## サーバー起動

```bash
python manage.py runserver 8000
```

API:

```text
http://localhost:8000/api/
```

## API ドキュメント

OpenAPI Schema:

```text
/api/schema/
```

Swagger UI:

```text
/api/docs/
```

ReDoc:

```text
/api/redoc/
```

Django Admin:

```text
/admin/
```

## 認証 API

JWT トークン取得：

```text
POST /api/auth/token/
```

トークン更新：

```text
POST /api/auth/token/refresh/
```

REST Framework では JWT / Session Authentication が設定されています。

なお、現在のプロトタイプでは一部の API が `AllowAny` に設定されており、すべての API で認証が必須となっているわけではありません。

## API エンドポイント

### 監視エリア

```text
/api/areas/
/api/areas/{id}/
```

Django REST Framework の `ModelViewSet` による標準 CRUD を提供します。

地理情報のベクタータイル：

```text
/api/geoshape-tile/{z}/{x}/{y}.pbf
```

### 検知ジョブ

```text
/api/detection-jobs/
/api/detection-jobs/{id}/
/api/detection-jobs/{id}/result/
```

検知ジョブには以下の情報が含まれます。

- 監視エリア
- Before 期間
- After 期間
- 検知パラメータ
- 処理ステータス
- エラー情報

主なステータス：

```text
pending
running
completed
failed
cancelled
```

### 検知結果

```text
/api/detection-results/
/api/detection-results/{id}/
```

検知結果には、例えば以下の情報が含まれます。

- Before / After の衛星シーン
- 変化検出 GeoJSON
- 検出面積（ha）
- パッチ数
- 空間的な分断・フラグメンテーション指標
- 違反可能性に関する指標
- 精度評価
- 結果マップ
- 地理的な表示範囲

### 衛星シーン

```text
/api/scenes/
/api/scenes/{id}/
/api/scenes/{id}/preview/
```

衛星シーンの作成：

```text
POST /api/scenes/
```

エリア、対象日、最大雲量などを指定して衛星画像を取得します。

主なステータス：

```text
pending
downloading
downloaded
failed
```

### クイック検知

```text
POST /api/quick-detect/
```

クイック検知では、指定したエリアと Before / After の日付・年を利用して、衛星データ取得から変化検知・可視化までを実行します。

主な入力：

```text
footprint_wkt
center_lat
center_lon
before_year
after_year
before_date
after_date
veg_threshold
ndvi_threshold
max_cloud_coverage
panel_size
```

### NDVI 変化 API

```text
POST /api/ndvi-change/
```

Sentinel Hub Processing API を利用して NDVI の変化を計算します。

以下の入力に対応しています。

- `bbox` または `footprint_wkt`
- Before / Old の期間
- After / Recent の期間
- NDVI 変化しきい値
- 森林 NDVI の最小値
- 最大雲量

この機能を利用するには Sentinel Hub の認証情報が必要です。

## 衛星画像検知パイプライン

主要な検知処理は以下に実装されています。

```text
satellite/pipeline.py
```

処理の流れ：

```text
DetectionJob
    │
    ├── Before の Sentinel-2 シーンを検索 / ダウンロード
    │
    ├── After の Sentinel-2 シーンを検索 / ダウンロード
    │
    ├── RED / GREEN / BLUE / NIR バンドを読み込み
    │
    ├── 前処理
    │
    ├── NDVI を計算
    │
    ├── NDVI 低下・森林変化を検知
    │
    ├── 空間パターンを分析
    │
    ├── 検出結果を GeoJSON 化
    │
    ├── 違反可能性を評価
    │
    ├── 精度を評価
    │
    └── DetectionResult に保存
```

Before / After のどちらか一方しか取得できない場合でも、取得済みのシーンは保持され、後続の処理で比較を完了できる構成になっています。

## 検知パラメータ

主なパラメータ：

| パラメータ | デフォルト | 内容 |
|---|---:|---|
| `ndvi_threshold` | `-0.3` | NDVI 低下量の検知しきい値 |
| `min_area_ha` | `1.0` | 検出対象とする最小面積 |
| `max_cloud_coverage` | `10` | 許容する最大雲量（%） |

## Sentinel-2 データ取得

Sentinel-2 のプロダクトは Copernicus Data Space Ecosystem を通して検索します。

基本的な流れ：

1. 監視エリアから地理的なフットプリントを作成
2. Sentinel-2 L2A プロダクトを検索
3. 日付と雲量でフィルタリング
4. 対象プロダクトをダウンロード
5. Django の `MEDIA_ROOT` 配下に保存
6. 衛星シーン情報を PostgreSQL に保存
7. 必要なスペクトルバンドを読み込み、解析を実行

ダウンロードしたデータは通常、以下に保存されます。

```text
media/sentinel/
```

## Django Management Commands

利用可能な主な管理コマンド：

```bash
python manage.py check_db
python manage.py seed_ito_area
python manage.py debug_sentinel_query <area_id> <YYYY-MM-DD>
python manage.py regenerate_previews
python manage.py regenerate_result_map
python manage.py run_detection_ito
python manage.py use_sentinel_zip
```

各コマンドの詳細は以下で確認できます。

```bash
python manage.py <command> --help
```

## Celery

Celery / Redis は `config/settings.py` で設定されています。

検知タスク：

```text
detections/tasks.py
```

検知タスクではパイプラインを実行し、`DetectionResult` を保存します。

現在のプロトタイプでは、検知 API の基本的なリクエストフローでタスク処理を同期的に実行する構成も含まれています。そのため、基本動作だけであれば Celery Worker が必須とは限りません。

バックグラウンド処理を利用する場合：

```bash
celery -A config worker -l info
```

## ディレクトリ構成

```text
.
├── areas/
│   ├── models.py
│   ├── serializers.py
│   ├── views.py
│   ├── urls.py
│   └── geo_utils.py
├── detections/
│   ├── models.py
│   ├── serializers.py
│   ├── views.py
│   ├── tasks.py
│   └── urls.py
├── satellite/
│   ├── models.py
│   ├── serializers.py
│   ├── views.py
│   ├── services.py
│   ├── pipeline.py
│   ├── preprocessing.py
│   ├── change_detection.py
│   ├── illegality.py
│   ├── accuracy.py
│   ├── io_sentinel.py
│   ├── dataspace_client.py
│   └── management/
├── core/
├── config/
│   ├── settings.py
│   ├── urls.py
│   ├── celery.py
│   └── wsgi.py
├── manage.py
├── requirements.txt
└── .env.example
```

## CORS

ローカル開発時のフロントエンドは以下です。

```env
CORS_ORIGINS=http://localhost:3333
```

別のフロントエンド URL を利用する場合は、環境に合わせて変更してください。

## 本番環境について

本リポジトリはプロトタイプとして構成されています。本番運用前には、少なくとも以下を確認してください。

- すべての API に対する認証・認可
- Secret Key などの秘密情報の安全な管理
- CORS / ALLOWED_HOSTS の適切な設定
- HTTPS
- PostgreSQL のバックアップ
- 衛星画像・生成ファイルのオブジェクトストレージ化
- Celery Worker の監視
- レート制限
- 衛星データ提供サービスの利用制限
- 大容量 Sentinel-2 データの保存容量
- エラーログ・監視基盤
- 検知結果および違反可能性評価の妥当性検証

## 関連プロジェクト

フロントエンド：

`territory-watch-client`

Next.js による地図ベースの監視・検知 UI を提供します。
