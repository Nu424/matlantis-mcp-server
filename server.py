import asyncio
import json
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Context

from task_manager import MatlantisTaskManager, TaskStatus

load_dotenv()

# MCPサーバーを作成
mcp = FastMCP("Matlantis MCP Server", debug=True)

# タスクマネージャーのシングルトンインスタンス
task_manager = MatlantisTaskManager()


@mcp.tool()
async def wait_for_task_completion(seconds: int, ctx: Context) -> str:
    """タスクが完了するまで待つ

    Args:
        seconds: 待つ時間（秒）

    Returns:
        str: Done!

    None:
        - タスクが実行されている場合、そのステータスを確認する。
        - タスクが完了したら、自動的に待機を終了する。
        - 場合によってはタイムアウトする可能性がある。その場合は、get_execution_status() で状態を確認したり、再度 wait_for_task_completion() を呼び出したりする。
    """
    for i in range(seconds):
        await ctx.report_progress(i + 1, seconds)

        # タスクが実行されている場合、そのステータスを確認する
        status = task_manager.get_status()
        state = status.get("status")

        if state == TaskStatus.RUNNING.value:
            await ctx.info(
                f"Status: {state}, Stage: {status.get('stage')}, Progress: {status.get('progress_pct')}%"
            )
        elif state in (
            TaskStatus.SUCCEEDED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
        ):
            await ctx.info(
                f"Status: {state}, Stage: {status.get('stage')}, Progress: {status.get('progress_pct')}%"
            )
            break

        await asyncio.sleep(1)
    return "Done!"


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
        - 作成したシミュレーションコードを、実際にMatlantis環境で実行する際に使用する。
        - 同時実行は1タスクのみで、実行中は新規タスクを拒否する。
        - 実行状況は get_execution_status() で確認できる。
        - タスクが完了すると、作業ディレクトリ内のmms_runs/{job_id}に成果物が保存される。
            - 最新の完了タスクのjob_idは get_last_result() で取得できる。
    """
    result = task_manager.submit(script_path, directory_path)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_execution_status() -> str:
    """実行状況を取得する

    Returns:
        str: 実行状況のJSON文字列
            - status: idle / running / succeeded / failed / cancelled
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
            - status: succeeded / failed / cancelled
            - message: メッセージ
            - error: エラーメッセージ（失敗時）
            - traceback: トレースバック（失敗時）
            - remote_log_path: リモートログファイルのパス
            - local_artifacts_path: ローカル成果物ディレクトリのパス

    Notes:
        タスクが成功、失敗、またはキャンセルで完了した後にのみ、結果が利用可能になる。
        実行中や未実行の場合は available=false が返される。
    """
    result = task_manager.get_last_result()
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def terminate_current_task(reason: str = "", grace_seconds: int = 10) -> str:
    """実行中のタスクを強制終了する

    Args:
        reason: 終了理由（省略可）
        grace_seconds: SIGTERMからSIGKILLまでの猶予時間（秒）、デフォルトは10秒

    Returns:
        str: 終了要求の結果をJSON文字列で返す
            - accepted: 要求が受理されたかどうか
            - reason: 拒否理由（拒否された場合）
            - message: メッセージ

    Notes:
        - 実行中のタスクがない場合は拒否される。
        - キャンセル処理は各ステージ（アップロード/実行/ダウンロード）に応じて適切に処理される。
        - 実行中の場合はリモートプロセスにSIGTERMを送信し、猶予時間後もプロセスが生存していればSIGKILLを送信する。
        - キャンセルされたタスクの状態は get_execution_status() や get_last_result() で確認できる。
    """
    result = task_manager.terminate_current_task(reason, grace_seconds)
    return json.dumps(result, ensure_ascii=False, indent=2)


# ----------
# ---アプリの実行
# ----------
if __name__ == "__main__":
    mcp.run(transport="stdio")
