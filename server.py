"""
{
    "mcpServers": {
        "matlantis-mcp-server": {
            "command": "uv",
            "args": [
                "--directory",
                "matlantis-mcp-serverがあるディレクトリのパス",
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

"""

# server.py
import asyncio
import json
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Context

from task_manager import MatlantisTaskManager

load_dotenv()

# Create an MCP server with debug enabled
mcp = FastMCP("Demo", debug=True)

# タスクマネージャーのシングルトンインスタンス
task_manager = MatlantisTaskManager()


@mcp.tool()
async def long_sleep(minutes: int, ctx: Context) -> str:
    """長時間、待つ。

    Args:
        minutes: 待つ時間（分）
        ctx: コンテキスト

    Returns:
        str: 終わったよ！

    None:
        場合によってはタイムアウトする可能性がある。
    """
    REPORT_INTERVAL = 30  # 60秒ごとに進捗を報告
    MAX_COUNT = minutes * 60 // REPORT_INTERVAL
    for i in range(MAX_COUNT):
        await ctx.report_progress(i, MAX_COUNT)
        await asyncio.sleep(REPORT_INTERVAL)
    return "終わったよ！"


@mcp.tool()
async def execute_python_script_in_matlantis(
    script_path: str, directory_path: str, ctx: Context
) -> str:
    """Matlantis環境でPythonスクリプトを実行する

    Args:
        script_path: 実行するPythonスクリプトのパス（ローカル, 絶対パス）
        directory_path: スクリプトを実行するディレクトリのパス（ローカル, 絶対パス）

    Returns:
        str: 実行結果のJSON文字列
            - accepted: タスクが受理されたかどうか
            - job_id: ジョブID（受理された場合）
            - reason: 拒否理由（拒否された場合）
            - message: メッセージ

    Notes:
        作成したシミュレーションコードを、実際にMatlantis環境で実行する際に使用する。
        同時実行は1タスクのみで、実行中は新規タスクを拒否する。
        実行状況は get_execution_status() で確認できる。
    """
    result = task_manager.submit(script_path, directory_path)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_execution_status() -> str:
    """実行状況を取得する

    Returns:
        str: 実行状況のJSON文字列
            - status: idle / running / succeeded / failed
            - job_id: ジョブID（実行中/完了時）
            - stage: 実行段階（initializing / uploading / executing / downloading / finalizing）
            - progress_pct: 進捗率（0-100）
            - started_at: 開始時刻
            - ended_at: 終了時刻（完了時）
            - script_path: スクリプトパス
            - directory_path: ディレクトリパス
    """
    status = task_manager.get_status()
    return json.dumps(status, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_last_result() -> str:
    """最後に実行したタスクの結果を取得する

    Returns:
        str: 実行結果のJSON文字列
            - available: 結果が利用可能かどうか
            - job_id: ジョブID
            - status: succeeded / failed
            - message: メッセージ
            - error: エラーメッセージ（失敗時）
            - traceback: トレースバック（失敗時）
            - remote_log_path: リモートログファイルのパス
            - local_artifacts_path: ローカル成果物ディレクトリのパス

    Notes:
        タスクが成功または失敗で完了した後にのみ、結果が利用可能になる。
        実行中や未実行の場合は available=false が返される。
    """
    result = task_manager.get_last_result()
    return json.dumps(result, ensure_ascii=False, indent=2)


# ----------
# ---アプリの実行
# ----------
if __name__ == "__main__":
    mcp.run(transport="stdio")
