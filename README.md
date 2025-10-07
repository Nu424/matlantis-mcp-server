# Matlantis MCP Server

Matlantis環境へのSSH接続と、ディレクトリ転送・Pythonスクリプト実行を行うサービスクラスです。

## 機能

- **SSH接続管理**: Websocket経由でMatlantis環境に安全に接続
- **ディレクトリアップロード**: ローカルディレクトリをリモートへ効率的に転送
- **ディレクトリダウンロード**: リモートディレクトリをローカルへダウンロード
- **Pythonスクリプト実行**: リモート環境でPythonスクリプトを実行し、結果を取得

## インストール

```bash
# uvを使用する場合
uv pip install -e .
```

## 使用例

### 基本的な使い方

```python
from matlantis_ssh_service import MatlantisSSHService

# サービスのインスタンスを作成
service = MatlantisSSHService()

# Matlantis環境に接続
service.connect(
    websocat_bin_path="./websocat.x86_64-pc-windows-gnu.exe",
    matlantis_domain="your-matlantis-domain.com",
    matlantis_user_id="your-user-id",
    notebook_pre_shared_key="your-pre-shared-key",
    user_name="your-username",
    identity_file="~/.ssh/id_rsa",
    local_port=2222
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
    print(f"実行結果: {result.return_code}")
    
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

### 詳細な使用例

#### 1. ディレクトリのアップロード

```python
# ローカルのプロジェクトディレクトリをリモートにアップロード
# .git, .venv, __pycache__ などは自動的に除外されます
service.upload_directory(
    local_path="./my_data",
    remote_path="~/matlantis_workspace/data"
)
```

#### 2. ディレクトリのダウンロード

```python
# リモートの結果をローカルにダウンロード
# 既存ディレクトリがある場合は allow_overwrite=True を指定
service.download_directory(
    remote_path="~/matlantis_workspace/results",
    local_path="./results",
    allow_overwrite=True
)
```

#### 3. Pythonスクリプトの実行

```python
# ログファイルなしで実行（標準出力/エラーを取得）
result = service.execute_python_script(
    script_path="~/scripts/calculate.py"
)
print(f"標準出力: {result.stdout}")
print(f"標準エラー: {result.stderr}")
print(f"終了コード: {result.return_code}")

# ログファイルありで実行（リモートにログを保存）
result = service.execute_python_script(
    script_path="~/scripts/long_running_task.py",
    script_log_path="~/logs/task.log"
)
```

## 無視パターン

ディレクトリの転送時、以下のファイル/ディレクトリは自動的に除外されます：

- `.git`
- `.venv`
- `__pycache__`
- `.ipynb_checkpoints`
- `.DS_Store`

## 技術仕様

- **転送方式**: ZIP圧縮による一時ファイル転送（高速・効率的）
- **Python標準ライブラリ**: `zipfile`を使用（追加の外部コマンド不要）
- **プロトコル**: WebSocket over SSH (wss://)
- **依存パッケージ**: `fabric` (SSH/SFTP接続)

## ライセンス

MIT License

