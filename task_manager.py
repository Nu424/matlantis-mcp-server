"""
Matlantis タスク実行管理
単一実行のみをサポートし、実行中は新規タスクを拒否する
"""

import os
import posixpath
import threading
import traceback
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

from matlantis_ssh_service import MatlantisSSHService

load_dotenv()

class TaskStatus(Enum):
    """タスクの実行ステータス"""

    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class MatlantisJob:
    """実行中のジョブ情報"""

    job_id: str
    script_path: str
    directory_path: str
    stage: str  # uploading, executing, downloading, finalizing
    progress_pct: int  # 0..100
    started_at: str
    ended_at: Optional[str] = None


@dataclass
class MatlantisJobResult:
    """ジョブの最終結果"""

    job_id: str
    status: str  # succeeded, failed
    message: str
    error: Optional[str] = None
    traceback: Optional[str] = None
    remote_log_path: Optional[str] = None
    local_artifacts_path: Optional[str] = None


class MatlantisTaskManager:
    """
    Matlantis環境でのタスク実行を管理するマネージャー
    同時実行は1タスクのみで、実行中は新規タスクを拒否する
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._status = TaskStatus.IDLE
        self._current_job: Optional[MatlantisJob] = None
        self._last_result: Optional[MatlantisJobResult] = None
        self._execute_thread: Optional[threading.Thread] = None

    def submit(self, script_path: str, directory_path: str) -> dict:
        """
        タスクを投入する

        Args:
            script_path: 実行するPythonスクリプトのパス（ローカル）
            directory_path: スクリプトを実行するディレクトリのパス（ローカル）

        Returns:
            dict: {"accepted": bool, "job_id": str | None, "reason": str | None}

        Notes:
            - まず、タスクが実行可能な状況かを確認する(すでに動いている？存在する？)
            - ジョブを作成する(MatlantisJobというデータ型で)
            - バックグラウンドスレッドで実行する
                - 内部で実際に処理するのは、`_execute`関数である
        """
        with self._lock:
            # 実行中なら拒否
            if self._status == TaskStatus.RUNNING:
                return {
                    "accepted": False,
                    "reason": "busy",
                    "message": "既にタスクが実行中です",
                }

            # 入力検証
            if not os.path.exists(script_path):
                return {
                    "accepted": False,
                    "reason": "invalid_input",
                    "message": f"スクリプトが見つかりません: {script_path}",
                }

            if not os.path.exists(directory_path):
                return {
                    "accepted": False,
                    "reason": "invalid_input",
                    "message": f"ディレクトリが見つかりません: {directory_path}",
                }

            if not os.path.isdir(directory_path):
                return {
                    "accepted": False,
                    "reason": "invalid_input",
                    "message": f"指定されたパスはディレクトリではありません: {directory_path}",
                }

            # ジョブを作成
            job_id = uuid.uuid4().hex[:12]
            self._current_job = MatlantisJob(
                job_id=job_id,
                script_path=script_path,
                directory_path=directory_path,
                stage="initializing",
                progress_pct=0,
                started_at=datetime.now().isoformat(),
            )
            self._status = TaskStatus.RUNNING

            # バックグラウンドスレッドで実行
            self._execute_thread = threading.Thread(
                target=self._execute,
                args=(job_id, script_path, directory_path),
                daemon=True,
            )
            self._execute_thread.start()

            return {
                "accepted": True,
                "job_id": job_id,
                "message": "タスクを受け付けました",
            }

    def get_status(self) -> dict:
        """
        現在の実行状況を取得する

        Returns:
            dict: ステータス情報
        """
        with self._lock:
            if self._status == TaskStatus.IDLE:
                return {"status": TaskStatus.IDLE.value, "message": "アイドル状態です"}

            if self._current_job is None:
                return {
                    "status": self._status.value,
                    "message": "ステータス情報がありません",
                }

            return {
                "status": self._status.value,
                "job_id": self._current_job.job_id,
                "stage": self._current_job.stage,
                "progress_pct": self._current_job.progress_pct,
                "started_at": self._current_job.started_at,
                "ended_at": self._current_job.ended_at,
                "script_path": self._current_job.script_path,
                "directory_path": self._current_job.directory_path,
            }

    def get_last_result(self) -> dict:
        """
        最後に実行したタスクの結果を取得する

        Returns:
            dict: 結果情報（利用可能な場合）
        """
        with self._lock:
            if self._last_result is None:
                return {"available": False, "message": "実行結果がありません"}

            result_dict = asdict(self._last_result)
            result_dict["available"] = True
            return result_dict

    # ----------
    # ---内部関数
    # ----------
    def _execute(self, job_id: str, script_path: str, directory_path: str):
        """
        実際にMatlantis環境でタスクを実行する（バックグラウンドスレッド用）

        Args:
            job_id: ジョブID
            script_path: 実行するPythonスクリプトのパス
            directory_path: スクリプトを実行するディレクトリのパス

        Notes: 以下のような流れで処理する
            - 接続を確立する
            - ローカルのディレクトリを、リモートにアップロードする
                - リモートのディレクトリ: `~/mms-jobs/{job_id}`
                - 各ジョブごとにディレクトリとしてまとまっているので、比較的管理がしやすいかも
            - リモートのPythonスクリプトを実行する
                - スクリプトのパス: `~/mms-jobs/{job_id}/{script_name}`
                - ログ出力: `~/mms-jobs/{job_id}/execution.log`
            - リモートの結果をローカルにダウンロードする
                - ローカルの、`./runs/{job_id}`に結果をまとめる
            - 終了処理
                - 接続を切断する
                - 成功/失敗の記録…MatlantisJobResultとして記録する。これは、get_last_result()で取得できる。
        """
        ssh_service = None
        remote_work_dir = None
        local_artifacts_dir = None
        remote_log_path = None

        try:
            # 環境変数から接続情報を取得
            websocat_bin = os.getenv("WEBSOCAT_BIN")
            matlantis_domain = os.getenv("MATLANTIS_DOMAIN")
            matlantis_user_id = os.getenv("MATLANTIS_USER_ID")
            notebook_psk = os.getenv("NOTEBOOK_PRE_SHARED_KEY")
            ssh_user = os.getenv("USER_NAME", "jovyan")
            ssh_key = os.getenv("IDENTITY_FILE")
            local_port = int(os.getenv("LOCAL_PORT", "2222"))
            priority_version = os.getenv("PRIORITY_VERSION", None)

            # 必須環境変数のチェック
            missing_vars = []
            for var_name, var_value in [
                ("WEBSOCAT_BIN", websocat_bin),
                ("MATLANTIS_DOMAIN", matlantis_domain),
                ("MATLANTIS_USER_ID", matlantis_user_id),
                ("NOTEBOOK_PRE_SHARED_KEY", notebook_psk),
                ("IDENTITY_FILE", ssh_key),
            ]:
                if not var_value:
                    missing_vars.append(var_name)

            if missing_vars:
                raise ValueError(
                    f"必須の環境変数が設定されていません: {', '.join(missing_vars)}"
                )

            # --- Stage 1: アップロード ---
            self._update_job(stage="uploading", progress_pct=10)

            # SSH接続を確立
            ssh_service = MatlantisSSHService()
            ssh_service.connect(
                websocat_bin_path=websocat_bin,
                matlantis_domain=matlantis_domain,
                matlantis_user_id=matlantis_user_id,
                notebook_pre_shared_key=notebook_psk,
                user_name=ssh_user,
                identity_file=ssh_key,
                local_port=local_port,
            )

            self._update_job(progress_pct=20)

            # リモート作業ディレクトリを設定
            remote_work_dir = f"~/mms-jobs/{job_id}"

            # ディレクトリをアップロード
            ssh_service.upload_directory(
                local_path=directory_path, remote_path=remote_work_dir, priority_version=priority_version
            )

            self._update_job(progress_pct=40)

            # --- Stage 2: 実行 ---
            self._update_job(stage="executing", progress_pct=50)

            # スクリプトのリモートパスを構築
            # directory_pathを基準として、script_pathからの相対パスを計算
            script_relative_path = os.path.relpath(script_path, directory_path)
            # WindowsのバックスラッシュをPOSIXのスラッシュに正規化
            script_relative_path_posix = script_relative_path.replace("\\", "/")
            # リモート(Linux)側のパス結合はPOSIXで行う
            remote_script_path = posixpath.join(remote_work_dir, script_relative_path_posix)
            remote_log_path = posixpath.join(remote_work_dir, "execution.log")

            # カレントディレクトリをリモートに移動
            ssh_service._execute_command(f"cd {remote_work_dir}")

            # スクリプトを実行
            result = ssh_service.execute_python_script(
                script_path=remote_script_path, script_log_path=remote_log_path, priority_version=priority_version, python_path="."
            )

            self._update_job(progress_pct=70)

            # 実行結果をチェック
            if result.return_code != 0:
                raise RuntimeError(
                    f"スクリプトの実行が失敗しました (exit code: {result.return_code})\n"
                    f"stdout: {result.stdout}\n"
                    f"stderr: {result.stderr}"
                )

            # --- Stage 3: ダウンロード ---
            self._update_job(stage="downloading", progress_pct=80)

            # ローカルの成果物ディレクトリを準備（実行ディレクトリ内のmms_runs）
            local_artifacts_dir = Path(directory_path) / "mms_runs" / job_id
            Path(local_artifacts_dir).mkdir(parents=True, exist_ok=True)

            # 結果をダウンロード
            ssh_service.download_directory(
                remote_path=remote_work_dir,
                local_path=local_artifacts_dir,
                allow_overwrite=True,
                priority_version=priority_version,
            )

            self._update_job(progress_pct=90)

            # --- Stage 4: 終了処理 ---
            self._update_job(stage="finalizing", progress_pct=95)

            # 接続を切断
            ssh_service.disconnect()
            ssh_service = None

            self._update_job(progress_pct=100)

            # 成功を記録
            self._finalize_success(
                job_id=job_id,
                message="タスクが正常に完了しました",
                remote_log_path=remote_log_path,
                local_artifacts_path=local_artifacts_dir,
            )

        except Exception as e:
            # エラーを記録
            error_message = str(e)
            error_traceback = traceback.format_exc()

            self._finalize_failure(
                job_id=job_id,
                error_message=error_message,
                error_traceback=error_traceback,
                remote_log_path=remote_log_path,
                local_artifacts_path=local_artifacts_dir,
            )

        finally:
            # 接続が残っていれば切断
            if ssh_service and ssh_service.is_connected:
                try:
                    ssh_service.disconnect()
                except Exception:
                    pass

    # ----------
    # ---ジョブの進捗を更新する
    # ----------
    def _update_job(
        self, stage: Optional[str] = None, progress_pct: Optional[int] = None
    ):
        """ジョブの進捗を更新する"""
        with self._lock:
            if self._current_job is None:
                return

            if stage is not None:
                self._current_job.stage = stage
            if progress_pct is not None:
                self._current_job.progress_pct = progress_pct

    # ----------
    # ---成功・失敗を記録する
    # ----------
    def _finalize_success(
        self,
        job_id: str,
        message: str,
        remote_log_path: Optional[str],
        local_artifacts_path: Optional[str],
    ):
        """タスクの成功を記録する"""
        with self._lock:
            if self._current_job:
                self._current_job.ended_at = datetime.now().isoformat()

            self._last_result = MatlantisJobResult(
                job_id=job_id,
                status="succeeded",
                message=message,
                remote_log_path=remote_log_path,
                local_artifacts_path=local_artifacts_path,
            )
            self._status = TaskStatus.SUCCEEDED

    def _finalize_failure(
        self,
        job_id: str,
        error_message: str,
        error_traceback: str,
        remote_log_path: Optional[str],
        local_artifacts_path: Optional[str],
    ):
        """タスクの失敗を記録する"""
        with self._lock:
            if self._current_job:
                self._current_job.ended_at = datetime.now().isoformat()

            self._last_result = MatlantisJobResult(
                job_id=job_id,
                status="failed",
                message="タスクの実行中にエラーが発生しました",
                error=error_message,
                traceback=error_traceback,
                remote_log_path=remote_log_path,
                local_artifacts_path=local_artifacts_path,
            )
            self._status = TaskStatus.FAILED
