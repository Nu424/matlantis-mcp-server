# Matlantis MCP Server

Matlantis環境で計算化学シミュレーションコードを実行するためのMCP (Model Context Protocol) サーバーです。ローカルのPythonスクリプトとデータをリモートMatlantis環境にアップロードし、実行結果を自動的に取得します。

## 目次

- [主な機能](#主な機能)
- [仕組みの概要](#仕組みの概要)
- [前提条件](#前提条件)
- [インストール](#インストール)
- [環境変数の設定](#環境変数の設定)
- [クイックスタート](#クイックスタート)
- [MCPツール一覧](#mcpツール一覧)
- [利用ワークフロー](#利用ワークフロー)
- [仕様と制約](#仕様と制約)
- [Python API（直接利用）](#python-api直接利用)
- [トラブルシューティング](#トラブルシューティング)
- [開発者向けガイド](#開発者向けガイド)
- [ライセンス](#ライセンス)

## 主な機能

- **SSH接続管理**: WebSocket経由でMatlantis環境に安全に接続
- **ディレクトリアップロード**: ローカルディレクトリをリモートへZIP圧縮で効率的に転送
- **ディレクトリダウンロード**: リモートの実行結果をローカルへダウンロード
- **Pythonスクリプト実行**: リモート環境でPythonスクリプトを実行し、ログと成果物を取得
- **タスク管理**: バックグラウンドでの単一タスク実行と進捗管理
- **MCPプロトコル対応**: Cursor、Claude Desktopなどのクライアントから直接利用可能

## 仕組みの概要

### アーキテクチャ

```
┌─────────────────┐
│  MCPクライアント │  (Cursor / Claude Desktop など)
│  (Claude AI)    │
└────────┬────────┘
         │ MCP Protocol (stdio)
         │
┌────────▼────────┐
│   server.py     │  MCPサーバー (FastMCP)
│  - ツール登録   │
└────────┬────────┘
         │
┌────────▼────────┐
│ task_manager.py │  タスク管理・排他制御
│  - 実行スレッド │
│  - 進捗管理     │
└────────┬────────┘
         │
┌────────▼───────────────┐
│ matlantis_ssh_service  │  SSH接続・ファイル転送
│  - websocat経由接続    │
│  - ZIP転送             │
│  - Pythonスクリプト実行│
└────────┬───────────────┘
         │ WebSocket over SSH
         │
┌────────▼────────┐
│ Matlantis環境   │
│  ~/             │
│  ~/.matlantis-  │
│      jobs/      │
│      {job_id}/  │
└─────────────────┘
```

### 実行フロー

```
1. submit (MCPツール呼び出し)
   ↓
2. [initializing] タスク受付・検証
   ↓
3. [uploading] ディレクトリをZIP化してリモートに転送
   ↓           リモートディレクトリ: ~/.matlantis-jobs/{job_id}/
   ↓
4. [executing] リモートでPythonスクリプトを実行
   ↓           ログ出力: ~/.matlantis-jobs/{job_id}/execution.log
   ↓
5. [downloading] 実行結果をローカルにダウンロード
   ↓             ローカルディレクトリ: ./runs/{job_id}/
   ↓
6. [finalizing] SSH切断・結果記録 (succeeded / failed)
```

## 前提条件

### 必須

- **Python 3.10以上**
- **Matlantisアカウント**: [Matlantis](https://matlantis.com/)へのアクセス権
- **websocat**: WebSocket接続用バイナリ
  - [vi/websocat リリースページ](https://github.com/vi/websocat/releases)からダウンロード
  - Windows: `websocat.x86_64-pc-windows-gnu.exe`
  - Linux: `websocat.x86_64-unknown-linux-musl`
  - macOS: `websocat.x86_64-apple-darwin`
- **SSH秘密鍵**: Matlantis環境にアクセス可能な秘密鍵（通常は `~/.ssh/id_rsa`）
- **Notebook Pre-Shared Key**: Matlantis NotebookのPre-Shared Key

### 推奨

- **uv**: 高速なPythonパッケージマネージャー（または pip）
- **MCPクライアント**: Cursor、Claude Desktopなど

## インストール

### uvを使用する場合（推奨）

```bash
# uvのインストール（未インストールの場合）
# https://docs.astral.sh/uv/getting-started/installation/

# プロジェクトディレクトリに移動
cd matlantis-mcp-server

# 依存関係のインストール
uv pip install -e .
```

### pipを使用する場合

```bash
cd matlantis-mcp-server
pip install -e .
```

## 環境変数の設定

プロジェクトのルートディレクトリに `.env` ファイルを作成します：

```env
# websocatのバイナリパス（絶対パス推奨）
WEBSOCAT_BIN=C:\\tools\\websocat.x86_64-pc-windows-gnu.exe

# Matlantisのドメイン
MATLANTIS_DOMAIN=your-matlantis-domain.com

# MatlantisのユーザーID
MATLANTIS_USER_ID=your-user-id

# Notebook Pre-Shared Key（Matlantis NotebookのSettings > Securityから取得）
NOTEBOOK_PRE_SHARED_KEY=your-pre-shared-key

# SSHユーザー名（通常は jovyan）
USER_NAME=jovyan

# SSH秘密鍵のパス（絶対パス推奨）
IDENTITY_FILE=C:\\Users\\you\\.ssh\\id_rsa

# ローカルポート（デフォルト: 2222）
LOCAL_PORT=2222
```

**注意（Windows）**:
- パスの区切り文字は `\\` または `/` を使用
- 例: `C:\\tools\\websocat.exe` または `C:/tools/websocat.exe`

**注意（Linux/macOS）**:
```env
WEBSOCAT_BIN=/usr/local/bin/websocat
IDENTITY_FILE=~/.ssh/id_rsa
```

## クイックスタート

### 1. MCPクライアントの設定

#### Cursorの場合

`%APPDATA%\Cursor\User\globalStorage\rooveterinaryinc.roo-cline\settings\cline_mcp_settings.json` に以下を追加：

```json
{
  "mcpServers": {
    "matlantis-mcp-server": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/matlantis-mcp-server",
        "run",
        "server.py"
      ],
      "alwaysAllow": [
        "execute_python_script_in_matlantis",
        "get_execution_status",
        "get_last_result"
      ],
      "disabled": false
    }
  }
}
```

#### Claude Desktopの場合

`~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）または `%APPDATA%\Claude\claude_desktop_config.json`（Windows）に追加：

```json
{
  "mcpServers": {
    "matlantis-mcp-server": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/matlantis-mcp-server",
        "run",
        "server.py"
      ]
    }
  }
}
```

### 2. サーバーの起動確認

MCPクライアントを再起動すると、サーバーが自動的に起動します。クライアント側のログで以下のようなメッセージが表示されれば成功です：

```
MCP server 'matlantis-mcp-server' connected successfully
Available tools: execute_python_script_in_matlantis, get_execution_status, get_last_result
```

### 3. 使用例

MCPクライアント（Cursor/Claude Desktop）で以下のように指示します：

```
「C:\projects\my_simulation\run.py を Matlantis環境で実行してください。
ディレクトリは C:\projects\my_simulation です。」
```

AIが自動的に以下を実行します：
1. `execute_python_script_in_matlantis` を呼び出し
2. 定期的に `get_execution_status` で進捗確認
3. 完了後に `get_last_result` で結果を取得

## MCPツール一覧

### 1. `execute_python_script_in_matlantis`

Matlantis環境でPythonスクリプトを実行します。

**パラメータ:**

| パラメータ       | 型     | 説明                                        |
|------------------|--------|---------------------------------------------|
| `script_path`    | string | 実行するPythonスクリプトのパス（ローカル、絶対パス） |
| `directory_path` | string | スクリプトを実行するディレクトリのパス（ローカル、絶対パス） |

**戻り値:**

```json
{
  "accepted": true,
  "job_id": "a1b2c3d4e5f6",
  "message": "タスクを受け付けました"
}
```

または拒否された場合：

```json
{
  "accepted": false,
  "reason": "busy",
  "message": "既にタスクが実行中です"
}
```

**拒否理由:**
- `busy`: 既にタスクが実行中
- `invalid_input`: スクリプトまたはディレクトリが存在しない

### 2. `get_execution_status`

現在の実行状況を取得します。

**パラメータ:** なし

**戻り値:**

```json
{
  "status": "running",
  "job_id": "a1b2c3d4e5f6",
  "stage": "executing",
  "progress_pct": 50,
  "started_at": "2025-10-16T12:34:56.789012",
  "ended_at": null,
  "script_path": "C:/projects/my_simulation/run.py",
  "directory_path": "C:/projects/my_simulation"
}
```

**ステータス値:**

| ステータス   | 説明               |
|--------------|-------------------|
| `idle`       | アイドル状態       |
| `running`    | 実行中             |
| `succeeded`  | 成功               |
| `failed`     | 失敗               |

**ステージ値（`status=running` の場合）:**

| ステージ       | 説明                           | 進捗目安 |
|----------------|-------------------------------|---------|
| `initializing` | 初期化中                       | 0-10%   |
| `uploading`    | ディレクトリをアップロード中   | 10-40%  |
| `executing`    | Pythonスクリプトを実行中       | 50-70%  |
| `downloading`  | 結果をダウンロード中           | 80-90%  |
| `finalizing`   | 終了処理中                     | 95-100% |

### 3. `get_last_result`

最後に実行したタスクの結果を取得します。

**パラメータ:** なし

**戻り値（成功時）:**

```json
{
  "available": true,
  "job_id": "a1b2c3d4e5f6",
  "status": "succeeded",
  "message": "タスクが正常に完了しました",
  "error": null,
  "traceback": null,
  "remote_log_path": "~/.matlantis-jobs/a1b2c3d4e5f6/execution.log",
  "local_artifacts_path": "./runs/a1b2c3d4e5f6"
}
```

**戻り値（失敗時）:**

```json
{
  "available": true,
  "job_id": "a1b2c3d4e5f6",
  "status": "failed",
  "message": "タスクの実行中にエラーが発生しました",
  "error": "スクリプトの実行が失敗しました (exit code: 1)",
  "traceback": "Traceback (most recent call last):\n...",
  "remote_log_path": "~/.matlantis-jobs/a1b2c3d4e5f6/execution.log",
  "local_artifacts_path": "./runs/a1b2c3d4e5f6"
}
```

**戻り値（結果なし）:**

```json
{
  "available": false,
  "message": "実行結果がありません"
}
```

## 利用ワークフロー

### 典型的な実行フロー

1. **タスク投入**
   ```
   ユーザー → MCPクライアント → execute_python_script_in_matlantis
   ```

2. **進捗確認（ポーリング）**
   ```
   定期的に get_execution_status を呼び出し
   → status: running, stage: uploading (進捗: 20%)
   → status: running, stage: executing (進捗: 60%)
   → status: running, stage: downloading (進捗: 85%)
   ```

3. **結果取得**
   ```
   status: succeeded を確認
   → get_last_result で詳細取得
   → local_artifacts_path から成果物を確認
   ```

### ディレクトリ構成

**リモート（Matlantis環境）:**
```
~/.matlantis-jobs/
  └── {job_id}/              # ジョブごとのディレクトリ
      ├── run.py             # アップロードされたスクリプト
      ├── data/              # アップロードされたデータ
      ├── results/           # スクリプトが生成したファイル
      └── execution.log      # 実行ログ（stdout/stderr）
```

**ローカル:**
```
./runs/
  └── {job_id}/              # ダウンロードされた成果物
      ├── run.py             # スクリプト（参照用）
      ├── data/              # データ（参照用）
      ├── results/           # 生成されたファイル
      └── execution.log      # 実行ログ
```

## 仕様と制約

### タスク管理

- **同時実行数**: 1タスクのみ
- **実行中の新規タスク**: 拒否され `{"accepted": false, "reason": "busy"}` が返される
- **キャンセル機能**: 未実装（実行中のタスクは完了またはエラーまで待機が必要）
- **履歴保持**: 最新のタスク結果のみ保持（古い結果は上書きされる）

### ファイル転送

**転送方式**: ZIP圧縮による一時ファイル転送
- Python標準ライブラリ `zipfile` を使用
- 追加の外部コマンド不要

**無視パターン**: 以下のファイル/ディレクトリは自動的に除外されます
```python
.git
.venv
__pycache__
.ipynb_checkpoints
.DS_Store
```

**カスタマイズ**: `matlantis_ssh_service.py` の `DEFAULT_IGNORE` を編集

### 接続

- **プロトコル**: WebSocket over SSH (`wss://`)
- **ローカルポート**: デフォルト `2222`（環境変数 `LOCAL_PORT` で変更可能）
- **認証**: SSH秘密鍵 + Notebook Pre-Shared Key

### エラーハンドリング

- スクリプトが非ゼロの終了コードを返した場合、タスクは `failed` となる
- エラー情報は `get_last_result()` の `error` と `traceback` フィールドに記録される
- リモートログは常に `~/.matlantis-jobs/{job_id}/execution.log` に保存される

## Python API（直接利用）

MCPサーバーを経由せず、直接Pythonスクリプトから利用する場合：

### 基本的な使い方

```python
import os
from dotenv import load_dotenv
from matlantis_ssh_service import MatlantisSSHService

# 環境変数を読み込み
load_dotenv()

# サービスのインスタンスを作成
service = MatlantisSSHService()

# Matlantis環境に接続
service.connect(
    websocat_bin_path=os.getenv("WEBSOCAT_BIN"),
    matlantis_domain=os.getenv("MATLANTIS_DOMAIN"),
    matlantis_user_id=os.getenv("MATLANTIS_USER_ID"),
    notebook_pre_shared_key=os.getenv("NOTEBOOK_PRE_SHARED_KEY"),
    user_name=os.getenv("USER_NAME", "jovyan"),
    identity_file=os.getenv("IDENTITY_FILE"),
    local_port=int(os.getenv("LOCAL_PORT", "2222"))
)

try:
    # ローカルディレクトリをリモートにアップロード
    service.upload_directory(
        local_path="./my_project",
        remote_path="~/remote_project"
    )
    
    # リモートでPythonスクリプトを実行
    result = service.execute_python_script(
        script_path="~/remote_project/main.py",
        script_log_path="~/remote_project/output.log"
    )
    print(f"終了コード: {result.return_code}")
    print(f"標準出力: {result.stdout}")
    print(f"標準エラー: {result.stderr}")
    
    # リモートディレクトリをローカルにダウンロード
    service.download_directory(
        remote_path="~/remote_project/results",
        local_path="./results",
        allow_overwrite=True
    )
    
finally:
    # 接続を切断
    service.disconnect()
```

### API リファレンス

#### `MatlantisSSHService.connect()`

```python
service.connect(
    websocat_bin_path: str,      # websocatのバイナリパス
    matlantis_domain: str,        # Matlantisのドメイン
    matlantis_user_id: str,       # MatlantisのユーザーID
    notebook_pre_shared_key: str, # Notebook Pre-Shared Key
    user_name: str,               # SSHユーザー名（通常 jovyan）
    identity_file: str,           # SSH秘密鍵のパス
    local_port: int = 2222        # ローカルポート
)
```

#### `MatlantisSSHService.upload_directory()`

```python
service.upload_directory(
    local_path: str,   # ローカルディレクトリパス
    remote_path: str   # リモートディレクトリパス（~ 展開対応）
)
```

#### `MatlantisSSHService.download_directory()`

```python
service.download_directory(
    remote_path: str,           # リモートディレクトリパス（~ 展開対応）
    local_path: str,            # ローカルディレクトリパス
    allow_overwrite: bool = False  # 既存ディレクトリの上書き許可
)
```

#### `MatlantisSSHService.execute_python_script()`

```python
result = service.execute_python_script(
    script_path: str,              # リモートスクリプトパス
    script_log_path: str = None    # リモートログパス（省略可）
)
# 戻り値: fabric.Result (stdout, stderr, return_code 属性を持つ)
```

## トラブルシューティング

### エラー: `websocat`が見つからない

**症状:**
```
FileNotFoundError: [Errno 2] No such file or directory: 'websocat'
```

**対処:**
1. `WEBSOCAT_BIN` 環境変数が正しく設定されているか確認
2. websocatバイナリが実際に存在するか確認
3. パスに空白が含まれる場合は、絶対パスを使用
4. Windowsの場合、パス区切り文字を `\\` または `/` で統一

### エラー: SSH接続に失敗

**症状:**
```
paramiko.ssh_exception.AuthenticationException: Authentication failed
```

**対処:**
1. `IDENTITY_FILE` が正しいSSH秘密鍵を指しているか確認
2. 秘密鍵のパーミッション確認（Linux/macOS: `chmod 600 ~/.ssh/id_rsa`）
3. 秘密鍵のフォーマット確認（OpenSSH形式推奨、PuTTY形式の場合は変換が必要）
4. `NOTEBOOK_PRE_SHARED_KEY` が正しいか確認（Matlantis NotebookのSettings > Securityから取得）

### エラー: `busy` で拒否される

**症状:**
```json
{"accepted": false, "reason": "busy", "message": "既にタスクが実行中です"}
```

**対処:**
1. `get_execution_status()` で現在の状況を確認
2. 実行中のタスクが完了するまで待機
3. 完了後（`status: succeeded` または `status: failed`）に再投入

### エラー: リモートにPythonが見つからない

**症状:**
```
RuntimeError: リモートにPythonが見つかりません
```

**対処:**
1. Matlantis環境で `which python3` または `which python` が成功するか確認
2. Matlantis環境が正しくセットアップされているか確認
3. SSH接続後、手動で `python3 --version` を実行してバージョン確認

### エラー: ディレクトリが既に存在する

**症状:**
```
ValueError: ローカルパス ./results は既に存在し中身があります
```

**対処:**
1. `download_directory()` に `allow_overwrite=True` を指定
2. または、既存ディレクトリを手動で削除/リネームしてから実行

### エラー: スクリプトの実行が失敗

**症状:**
```json
{
  "status": "failed",
  "error": "スクリプトの実行が失敗しました (exit code: 1)"
}
```

**対処:**
1. `get_last_result()` の `traceback` フィールドを確認
2. `local_artifacts_path` の `execution.log` を確認
3. リモート環境で必要なパッケージがインストールされているか確認
4. スクリプトのパスや依存ファイルが正しいか確認

### デバッグモード

`server.py` の `FastMCP` 初期化で `debug=True` が設定されているため、MCPクライアントのログに詳細な情報が出力されます。

## 開発者向けガイド

### モジュール概要

#### `server.py`

MCP サーバーの定義とツール登録を行います。

- **フレームワーク**: FastMCP（MCP SDK）
- **通信方式**: stdio（標準入出力）
- **主な責務**:
  - MCPツールの登録（`@mcp.tool()` デコレータ）
  - タスクマネージャーへのリクエスト転送
  - JSON形式でのレスポンス返却

**主要コード:**
```python
from mcp.server.fastmcp import FastMCP
from task_manager import MatlantisTaskManager

mcp = FastMCP("Demo", debug=True)
task_manager = MatlantisTaskManager()

@mcp.tool()
async def execute_python_script_in_matlantis(
    script_path: str, directory_path: str, ctx: Context
) -> str:
    result = task_manager.submit(script_path, directory_path)
    return json.dumps(result, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

#### `task_manager.py`

タスクの実行管理と排他制御を行います。

- **並行制御**: スレッドロック（`threading.Lock`）による単一実行保証
- **実行方式**: バックグラウンドスレッド（`daemon=True`）
- **主な責務**:
  - タスクの受付と検証
  - 実行ステージと進捗の管理
  - 結果の保持（最新1件）
  - エラーハンドリングとトレースバック記録

**実行フローの実装:**
```python
def _execute(self, job_id: str, script_path: str, directory_path: str):
    # 1. 環境変数の読み込みと検証
    # 2. SSH接続の確立
    # 3. [uploading] ディレクトリのアップロード
    # 4. [executing] スクリプトの実行
    # 5. [downloading] 結果のダウンロード
    # 6. [finalizing] 切断と結果記録
```

**ステージ更新:**
```python
self._update_job(stage="uploading", progress_pct=10)
self._update_job(progress_pct=20)
# ...
```

#### `matlantis_ssh_service.py`

SSH接続、ファイル転送、スクリプト実行のユーティリティを提供します。

- **SSH/SFTP**: Fabric（Paramiko ベース）
- **WebSocket**: websocat プロセスをバックグラウンド起動
- **主な責務**:
  - websocat経由のSSH接続管理
  - ZIP圧縮によるディレクトリ転送（アップロード/ダウンロード）
  - リモートPythonスクリプトの実行
  - リモートパスの展開（`~` など）

**ZIP転送の実装:**
```python
# アップロード
1. ローカルでディレクトリをZIP化（無視パターン適用）
2. SFTPでZIPファイルをリモートに転送
3. リモートでPythonスクリプトを使ってZIPを展開
4. 一時ファイルをクリーンアップ

# ダウンロード
1. リモートでPythonスクリプトを使ってディレクトリをZIP化
2. SFTPでZIPファイルをローカルに転送
3. ローカルでZIPを展開
4. 一時ファイルをクリーンアップ
```

### アーキテクチャの設計判断

#### なぜZIP転送？

- **速度**: 単一ファイル転送で済み、SFTP接続のオーバーヘッドを削減
- **互換性**: Python標準ライブラリのみで完結（`tar`、`rsync` などの外部コマンド不要）
- **シンプルさ**: 一時ファイルのクリーンアップが容易

#### なぜwebsocat？

- **WebSocket対応**: MatlantisはWebSocket経由のSSH接続を提供
- **軽量**: 単一バイナリで動作、追加の依存関係不要
- **柔軟性**: TCP→WebSocket変換をローカルで実行

#### なぜ単一実行？

- **リソース管理**: Matlantis環境のリソース競合を回避
- **シンプルさ**: ジョブキューの実装が不要
- **明確性**: 現在の状態を常に一意に特定可能

### 新しいMCPツールの追加

最小例: リモートファイルの一覧を取得するツール

```python
# server.py に追加

@mcp.tool()
async def list_remote_files(remote_path: str) -> str:
    """リモートディレクトリのファイル一覧を取得する
    
    Args:
        remote_path: リモートディレクトリパス
        
    Returns:
        str: ファイル一覧のJSON文字列
    """
    # 新しいSSH接続を確立（タスクマネージャーは使わない短時間操作）
    service = MatlantisSSHService()
    try:
        service.connect(
            websocat_bin_path=os.getenv("WEBSOCAT_BIN"),
            matlantis_domain=os.getenv("MATLANTIS_DOMAIN"),
            matlantis_user_id=os.getenv("MATLANTIS_USER_ID"),
            notebook_pre_shared_key=os.getenv("NOTEBOOK_PRE_SHARED_KEY"),
            user_name=os.getenv("USER_NAME", "jovyan"),
            identity_file=os.getenv("IDENTITY_FILE"),
            local_port=int(os.getenv("LOCAL_PORT", "2222"))
        )
        
        # リモートコマンドを実行
        result = service._execute_command(f"ls -la {remote_path}")
        
        return json.dumps({
            "success": True,
            "output": result.stdout
        }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e)
        }, ensure_ascii=False, indent=2)
        
    finally:
        if service.is_connected:
            service.disconnect()
```

### テスト

現在、自動テストは未実装です。手動テストは以下の手順で行います：

1. `.env` ファイルを設定
2. MCPクライアント（Cursorなど）を起動
3. サーバーが正しく接続されることを確認
4. 各ツールを順次実行して動作確認

**テスト用スクリプト例:**

```python
# test_script.py
import time
print("Starting calculation...")
for i in range(5):
    print(f"Progress: {(i+1)*20}%")
    time.sleep(1)
print("Calculation completed!")

# 成果物を生成
with open("result.txt", "w") as f:
    f.write("Simulation result: 42\n")
```

### 拡張のアイデア

- **複数ジョブキュー**: `queue.Queue` でタスクをキューイング
- **ジョブ履歴**: データベース（SQLite）に履歴を保存
- **キャンセル機能**: スレッドイベントによる中断シグナル
- **リトライ機構**: 一時的なネットワークエラーに対する自動リトライ
- **通知**: ジョブ完了時のメール/Slack通知
- **進捗ストリーミング**: リモートログのリアルタイム取得

### コーディング規約

- **型ヒント**: すべての関数シグネチャに型ヒントを記述
- **docstring**: 主要な関数にGoogle形式のdocstringを記述
- **エラーハンドリング**: 具体的な例外をキャッチし、ユーザーフレンドリーなメッセージを提供
- **ロギング**: 現在は未実装（将来的に `logging` モジュールの使用を推奨）

## ライセンス

MIT License

---

## 参考リンク

- [Matlantis](https://matlantis.com/)
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
- [websocat](https://github.com/vi/websocat)
- [Fabric (SSH library)](https://www.fabfile.org/)
- [FastMCP](https://github.com/jlowin/fastmcp)
