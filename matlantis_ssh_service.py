import os
import posixpath
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path

from fabric import Connection


class MatlantisSSHService:
    # 転送時に無視するパターン
    DEFAULT_IGNORE = {'.git', '.venv', '__pycache__',
                      '.ipynb_checkpoints', '.DS_Store', 'mms_runs'}

    def __init__(self):
        self.is_connected = False  # SSH接続が成立しているかどうか
        self.websocat_proc = None  # websocatのプロセスオブジェクト
        self.ssh_connection = None  # FabricのSSH接続オブジェクト

    # ----------
    # ---SSH接続・切断
    # ----------
    def connect(self,
                websocat_bin_path: str,
                matlantis_domain: str,
                matlantis_user_id: str,
                notebook_pre_shared_key: str,
                user_name: str,
                identity_file: str,
                local_port: int = 2222):
        """
        リモートのMatlantis環境にSSH接続する

        Args:
            websocat_bin_path(str): websocatのバイナリパス
            notebook_pre_shared_key(str): ノートブックの事前共有キー
            matlantis_domain(str): Matlantisのドメイン
            matlantis_user_id(str): MatlantisのユーザーID
            user_name(str): ユーザー名
            identity_file(str): 秘密鍵のパス
            local_port(int): ローカルのポート
        """
        try:
            # ---websocatを、ローカルTCPリスンの形で起動する
            self.websocat_proc = subprocess.Popen([
                websocat_bin_path,
                "--binary",
                f'-H=cookie: matlantis-notebook-pre-shared-key={notebook_pre_shared_key}',
                f"tcp-l:0.0.0.0:{local_port}",  # 接続をローカルで待つ
                f"wss://{matlantis_domain}/nb/{matlantis_user_id}/default/api/ssh-over-ws"
            ])
            # ---FabricでSSH接続する
            self.ssh_connection = Connection(
                host="127.0.0.1",
                user=user_name,
                port=local_port,
                connect_kwargs={"key_filename": identity_file}
            )
            self.ssh_connection.open() # 明示的に接続を開く
            self.is_connected = True
        except Exception as e:
            self.disconnect()
            raise e

    def disconnect(self):
        """
        リモートのMatlantis環境からSSH接続を切断する
        """
        if self.is_connected:
            self.ssh_connection.close()
            self.websocat_proc.terminate()
            self.websocat_proc.wait()
            self.is_connected = False

    def _execute_command(self, command: str):
        """
        リモートのMatlantis環境でコマンドを実行する

        Args:
            command(str): 実行するコマンド
        """
        if not self.is_connected:
            raise Exception("SSH接続が成立していません")
        return self.ssh_connection.run(command, hide=True)

    # ----------
    # ---内部ユーティリティ
    # ----------
    def _get_sftp(self):
        """SFTP クライアントを取得する（毎回新規のSFTPクライアントを開く）"""
        if not self.is_connected:
            raise RuntimeError("SSH接続が成立していません")
        # Fabricのキャッシュを避けて毎回新規のSFTPを開く
        # これにより、sftp.close()後も次回呼び出し時に新しいSFTPが取得できる
        return self.ssh_connection.client.open_sftp()

    # ---リモートのファイル・パス操作
    def _remote_path_join(self, *parts):
        """リモート(Linux)のパス結合を行う"""
        return posixpath.join(*parts)

    def _get_remote_home(self) -> str:
        """リモートユーザーのホームディレクトリの絶対パスを取得する"""
        if not self.is_connected:
            raise RuntimeError("SSH接続が成立していません")
        result = self._execute_command('printf %s "$HOME"')
        home = result.stdout.strip()
        if not home:
            raise RuntimeError("リモートのHOMEが取得できませんでした")
        return home

    def _expand_remote_path(self, path: str) -> str:
        """'~' をリモートのHOMEで展開した絶対パスを返す（先頭の '~' のみ対応）"""
        if not path:
            return path
        if path == '~':
            return self._get_remote_home()
        if path.startswith('~/'):
            return self._remote_path_join(self._get_remote_home(), path[2:])
        return path

    def _remote_exists(self, sftp, path: str) -> bool:
        """リモートパスの存在を確認する"""
        try:
            sftp.stat(path)
            return True
        except IOError:
            return False

    def _remote_isdir(self, sftp, path: str) -> bool:
        """リモートパスがディレクトリかどうかを確認する"""
        try:
            import stat
            return stat.S_ISDIR(sftp.stat(path).st_mode)
        except IOError:
            return False

    def _ensure_remote_dir(self, sftp, path: str):
        """リモートディレクトリを再帰的に作成する (mkdir -p 相当)"""
        if self._remote_exists(sftp, path):
            return

        parent = posixpath.dirname(path)
        if parent and parent != path:
            self._ensure_remote_dir(sftp, parent)

        try:
            sftp.mkdir(path)
        except IOError:
            # 既に存在する場合は無視
            pass

    # ---リモートのPython環境確認
    def _detect_remote_python(self, priority_version: str = None) -> str:
        """リモートのPythonコマンドを検出する
        
        ~/.py39/bin/python3, ~/.py310/bin/python3 等を探索し、
        最新のバージョンのPythonを優先して返す。
        見つからない場合は which python3 || which python にフォールバックする。
        """
        if priority_version:
            return f"~/.py{priority_version}/bin/python3"
        # まず ~/.py*/bin/python3 の候補を探索し、最新を選択
        detect_script = """
set -e
CANDS=$(ls -1d ~/.py*/bin/python3 2>/dev/null || true)
if [ -n "$CANDS" ]; then
  BEST=$(for p in $CANDS; do
           [ -x "$p" ] || continue
           v=$("$p" -V 2>&1 | awk '{print $2}')
           [ -n "$v" ] && echo "$v $p"
         done | sort -V | tail -n1 | awk '{print $2}')
  if [ -n "$BEST" ]; then
    echo "$BEST"
    exit 0
  fi
fi
which python3 2>/dev/null || which python 2>/dev/null
"""
        result = self._execute_command(detect_script)
        python_cmd = result.stdout.strip()
        if not python_cmd:
            raise RuntimeError("リモートにPythonが見つかりません")
        print(f"リモートにPythonが見つかりました: {python_cmd}")
        return python_cmd

    # ---アップロードする際の処理
    def _should_ignore(self, name: str) -> bool:
        """ファイル/ディレクトリ名が無視対象かどうかを判定する"""
        return name in self.DEFAULT_IGNORE

    def _create_zip_from_directory(self, local_path: str, zip_path: str):
        """ローカルディレクトリをzip化する（無視パターン適用）"""
        local_path = Path(local_path)
        if not local_path.is_dir():
            raise ValueError(f"{local_path} はディレクトリではありません")

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(local_path):
                # 無視対象のディレクトリを除外
                dirs[:] = [d for d in dirs if not self._should_ignore(d)]

                root_path = Path(root)
                for file in files:
                    if self._should_ignore(file):
                        continue

                    file_path = root_path / file
                    # zipファイル内のパスはlocal_pathからの相対パスにする
                    arcname = file_path.relative_to(local_path)
                    zf.write(file_path, arcname)

    # ----------
    # ---各種機能
    # ----------
    def upload_directory(self, local_path: str, remote_path: str, priority_version: str = None):
        """
        ローカルのディレクトリをリモートにアップロードする

        Args:
            local_path(str): ローカルのディレクトリパス
            remote_path(str): リモートのディレクトリパス
        """
        if not self.is_connected:
            raise RuntimeError("SSH接続が成立していません")

        local_path = Path(local_path)
        if not local_path.exists():
            raise FileNotFoundError(f"ローカルパス {local_path} が存在しません")
        if not local_path.is_dir():
            raise ValueError(f"{local_path} はディレクトリではありません")

        sftp = self._get_sftp()
        local_zip = None
        remote_zip = None

        try:
            # 1. ローカルで一時zipファイルを作成
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
                local_zip = tmp.name

            self._create_zip_from_directory(str(local_path), local_zip)

            # 2. リモートの一時ディレクトリとzipパスを準備（HOME を絶対パスとして解決）
            remote_home = self._get_remote_home()
            remote_tmp_dir = self._remote_path_join(remote_home, '.matlantis-ssh-service', 'tmp')
            self._execute_command(f"mkdir -p '{remote_tmp_dir}'")

            remote_zip = f"{remote_tmp_dir}/upload_{uuid.uuid4().hex}.zip"

            # 3. zipファイルをリモートにアップロード
            sftp.put(local_zip, remote_zip)

            # 4. リモートでターゲットディレクトリを作成（~ を展開）
            expanded_remote_path = self._expand_remote_path(remote_path)
            self._execute_command(f"mkdir -p '{expanded_remote_path}'")

            # 5. リモートでzipを解凍（~ や環境変数を展開してから使用）
            python_cmd = self._detect_remote_python(priority_version)
            unzip_script = f"""
import os
import zipfile

zip_path = os.path.expanduser(os.path.expandvars('{remote_zip}'))
target_path = os.path.expanduser(os.path.expandvars('{expanded_remote_path}'))
with zipfile.ZipFile(zip_path, 'r') as zf:
    zf.extractall(target_path)
"""
            self._execute_command(f"{python_cmd} -c \"{unzip_script}\"")

            # 6. リモートの一時zipファイルを削除
            self._execute_command(f"rm -f {remote_zip}")

        finally:
            # ローカルの一時zipファイルを削除
            if local_zip and os.path.exists(local_zip):
                os.remove(local_zip)

            sftp.close()

    def download_directory(self, remote_path: str, local_path: str, allow_overwrite: bool = False, priority_version: str = None):
        """
        リモートのディレクトリをローカルにダウンロードする

        Args:
            remote_path(str): リモートのディレクトリパス
            local_path(str): ローカルのディレクトリパス
            allow_overwrite(bool): 上書きを許可するかどうか
        """
        if not self.is_connected:
            raise RuntimeError("SSH接続が成立していません")

        sftp = self._get_sftp()
        expanded_remote_path = self._expand_remote_path(remote_path)

        # リモートパスの存在確認
        if not self._remote_exists(sftp, expanded_remote_path):
            raise FileNotFoundError(f"リモートパス {remote_path} が存在しません")
        if not self._remote_isdir(sftp, expanded_remote_path):
            raise ValueError(f"{remote_path} はディレクトリではありません")

        # ローカルパスの確認
        local_path = Path(local_path)
        if not allow_overwrite and local_path.exists():
            # ディレクトリが存在し、中身がある場合はエラー
            if local_path.is_dir() and any(local_path.iterdir()):
                raise ValueError(
                    f"ローカルパス {local_path} は既に存在し中身があります。上書きする場合は allow_overwrite=True を指定してください")
            elif local_path.is_file():
                raise ValueError(f"ローカルパス {local_path} はファイルとして存在します")

        remote_zip = None
        local_zip = None

        try:
            # 1. リモートでzipを作成
            python_cmd = self._detect_remote_python(priority_version)
            remote_home = self._get_remote_home()
            remote_tmp_dir = self._remote_path_join(remote_home, '.matlantis-ssh-service', 'tmp')
            self._execute_command(f"mkdir -p '{remote_tmp_dir}'")

            remote_zip = f"{remote_tmp_dir}/download_{uuid.uuid4().hex}.zip"

            # リモートでzip作成スクリプトを実行
            zip_script = f"""
import os
import zipfile

ignore_patterns = {list(self.DEFAULT_IGNORE)}

def should_ignore(name):
    return name in ignore_patterns

zip_path = os.path.expanduser(os.path.expandvars('{remote_zip}'))
base_path = os.path.expanduser(os.path.expandvars('{expanded_remote_path}'))

with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(base_path):
        # 無視対象のディレクトリを除外
        dirs[:] = [d for d in dirs if not should_ignore(d)]
        
        for file in files:
            if should_ignore(file):
                continue
            
            file_path = os.path.join(root, file)
            # zipファイル内のパスはremote_pathからの相対パスにする
            arcname = os.path.relpath(file_path, base_path)
            zf.write(file_path, arcname)
"""
            self._execute_command(f"{python_cmd} -c \"{zip_script}\"")

            # 2. ローカルに一時zipファイルを作成
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
                local_zip = tmp.name

            # 3. zipファイルをローカルにダウンロード
            sftp.get(remote_zip, local_zip)

            # 4. ローカルでzipを解凍
            local_path.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(local_zip, 'r') as zf:
                zf.extractall(local_path)

            # 5. リモートの一時zipファイルを削除
            self._execute_command(f"rm -f {remote_zip}")

        finally:
            # ローカルの一時zipファイルを削除
            if local_zip and os.path.exists(local_zip):
                os.remove(local_zip)

            sftp.close()

    def execute_python_script(self, script_path: str, script_log_path: str = None, priority_version: str = None, python_path: str = None, pid_file: str = None):
        """
        リモートのPythonスクリプトを実行する

        Args:
            script_path(str): リモートのPythonスクリプトパス（絶対パスまたは相対パス）
            script_log_path(str): スクリプトのログを保存するパス(リモート)。Noneの場合はログを保存しない。
            priority_version(str): 優先するPythonバージョン
            python_path(str): PYTHONPATHに追加するパス
            pid_file(str): PIDを保存するファイルのパス(リモート)。指定時は新しいセッションで起動し、プロセスグループIDをPIDファイルに記録する。

        Returns:
            fabric.Result: コマンド実行結果（stdout, stderr, return_code を含む）
        """
        if not self.is_connected:
            raise RuntimeError("SSH接続が成立していません")

        # パスの正規化（WindowsのバックスラッシュをPOSIXに）と ~ 展開
        script_path_str = str(script_path).replace('\\', '/')
        expanded_script_path = self._expand_remote_path(script_path_str)

        expanded_log_path = None
        if script_log_path:
            script_log_path_str = str(script_log_path).replace('\\', '/')
            expanded_log_path = self._expand_remote_path(script_log_path_str)

        expanded_pid_file = None
        if pid_file:
            pid_file_str = str(pid_file).replace('\\', '/')
            expanded_pid_file = self._expand_remote_path(pid_file_str)

        # シェル用の安全な単一引用符クオート
        def _sh_quote(p: str) -> str:
            return "'" + p.replace("'", "'\"'\"'") + "'"

        quoted_script = _sh_quote(expanded_script_path)
        quoted_log = _sh_quote(expanded_log_path) if expanded_log_path else None
        quoted_pid_file = _sh_quote(expanded_pid_file) if expanded_pid_file else None

        # リモートスクリプトの存在確認
        check_result = self._execute_command(
            f"test -f {quoted_script} && echo 'exists' || echo 'not_found'")
        if check_result.stdout.strip() != 'exists':
            raise FileNotFoundError(f"リモートスクリプト {expanded_script_path} が存在しません")

        # Pythonコマンドの検出
        python_cmd = self._detect_remote_python(priority_version)

        # PYTHONPATHを設定…実行ディレクトリを追加することで、実行ディレクトリ内のモジュールを参照できるようにする
        if python_path:
            python_cmd = f"PYTHONPATH={python_path} {python_cmd}"


        # 実行コマンドの構築
        if quoted_pid_file:
            # PIDファイル指定時: 新しいセッションで起動し、プロセスグループリーダーのPIDを記録
            # ディレクトリ作成コマンド
            dir_cmds = []
            dir_cmds.append(f"mkdir -p $(dirname {quoted_pid_file})")
            if quoted_log:
                dir_cmds.append(f"mkdir -p $(dirname {quoted_log})")
            
            # リダイレクト部分
            redirect = f">> {quoted_log} 2>&1" if quoted_log else ">/dev/null 2>&1"
            
            # PIDファイル付き実行コマンド
            # setsidで新しいセッション(=新しいプロセスグループ)を作成し、バックグラウンドで実行
            # セッションリーダーのPIDをファイルに記録し、waitで完了を待つ
            command = f"""
{'; '.join(dir_cmds)}
rm -f {quoted_pid_file}
setsid bash -lc '{python_cmd} -u {quoted_script} {redirect}' &
child=$!
echo -n "$child" > {quoted_pid_file}
wait "$child"
""".strip()
        else:
            # 従来の動作: -u オプションで標準出力のバッファリングを無効化
            if quoted_log:
                # ログファイルに出力する場合
                command = f"{python_cmd} -u {quoted_script} > {quoted_log} 2>&1"
            else:
                # 標準出力/エラーを取得する場合
                command = f"{python_cmd} -u {quoted_script}"

        # スクリプトを実行（エラーでも例外を投げない設定）
        result = self._execute_command(command)

        return result

    def _read_remote_pid(self, pid_file: str) -> int:
        """
        リモートのPIDファイルからPIDを読み取る

        Args:
            pid_file(str): PIDファイルのパス（リモート）

        Returns:
            int: PID

        Raises:
            FileNotFoundError: PIDファイルが存在しない
            ValueError: PIDファイルの内容が不正
        """
        if not self.is_connected:
            raise RuntimeError("SSH接続が成立していません")

        expanded_pid_file = self._expand_remote_path(pid_file.replace('\\', '/'))
        
        def _sh_quote(p: str) -> str:
            return "'" + p.replace("'", "'\"'\"'") + "'"
        
        quoted_pid_file = _sh_quote(expanded_pid_file)
        
        # PIDファイルの存在確認と読み取り
        result = self._execute_command(
            f"test -f {quoted_pid_file} && cat {quoted_pid_file} || echo 'not_found'"
        )
        
        pid_str = result.stdout.strip()
        if pid_str == 'not_found':
            raise FileNotFoundError(f"PIDファイル {pid_file} が見つかりません")
        
        try:
            pid = int(pid_str)
            if pid <= 0:
                raise ValueError(f"不正なPID: {pid}")
            return pid
        except ValueError as e:
            raise ValueError(f"PIDファイルの内容が不正です: {pid_str}") from e

    def terminate_by_pid_file(self, pid_file: str, grace_seconds: int = 10):
        """
        PIDファイルに記録されたプロセスグループを終了する
        
        SIGTERM を送信し、grace_seconds 待機後もプロセスが生存していれば SIGKILL を送信する。

        Args:
            pid_file(str): PIDファイルのパス（リモート）
            grace_seconds(int): SIGTERMからSIGKILLまでの猶予時間（秒）

        Raises:
            RuntimeError: SSH接続が成立していない
            FileNotFoundError: PIDファイルが見つからない（best-effortのため警告のみ）
        """
        if not self.is_connected:
            raise RuntimeError("SSH接続が成立していません")

        try:
            # PIDを読み取る
            pid = self._read_remote_pid(pid_file)
            
            # プロセスグループIDを取得（通常はセッションリーダーのPIDと同じ）
            pgid_result = self._execute_command(f"ps -o pgid= -p {pid} 2>/dev/null || echo ''")
            pgid_str = pgid_result.stdout.strip()
            
            if not pgid_str:
                # プロセスが既に終了している可能性
                print(f"プロセス {pid} は既に終了しているか、見つかりません")
                return
            
            try:
                pgid = int(pgid_str)
            except ValueError:
                print(f"PGIDの取得に失敗しました: {pgid_str}")
                return
            
            # SIGTERMを送信（プロセスグループ全体に）
            print(f"プロセスグループ {pgid} に SIGTERM を送信します...")
            self._execute_command(f"kill -TERM -{pgid} 2>/dev/null || true")
            
            # grace_seconds 間、プロセスの終了を待つ
            import time
            for i in range(grace_seconds):
                time.sleep(1)
                check_result = self._execute_command(f"ps -p {pid} >/dev/null 2>&1 && echo 'alive' || echo 'dead'")
                if check_result.stdout.strip() == 'dead':
                    print(f"プロセス {pid} は正常に終了しました")
                    return
            
            # まだ生存している場合はSIGKILLを送信
            print(f"プロセスグループ {pgid} に SIGKILL を送信します...")
            self._execute_command(f"kill -KILL -{pgid} 2>/dev/null || true")
            
            # 最終確認
            time.sleep(1)
            check_result = self._execute_command(f"ps -p {pid} >/dev/null 2>&1 && echo 'alive' || echo 'dead'")
            if check_result.stdout.strip() == 'dead':
                print(f"プロセス {pid} は強制終了されました")
            else:
                print(f"警告: プロセス {pid} の終了を確認できませんでした")
                
        except FileNotFoundError as e:
            print(f"警告: {e}")
        except Exception as e:
            print(f"終了処理中にエラーが発生しました: {e}")
