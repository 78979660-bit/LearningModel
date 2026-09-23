from __future__ import annotations

from dataclasses import dataclass
import ctypes
import hashlib
import json
import threading
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timedelta

from study_app.paths import CACHE_DIR, LOGS_DIR


@dataclass(frozen=True)
class ChatGPTBridgeResult:
    success: bool
    code: str
    message: str
    pdf_path: str | None = None


_HELPER_PATH = Path(__file__).with_name("fill_chatgpt_prompt.ps1")
_WORKFLOW_HELPER_PATH = Path(__file__).with_name("run_chatgpt_pdf_workflow.ps1")
_CAPTURE_HELPER_PATH = Path(__file__).with_name("capture_chatgpt_pdf.ps1")
GENERATED_PDF_DIR = CACHE_DIR / "generated_pdfs_temp"
TRACE_LOG_PATH = LOGS_DIR / "chatgpt_pdf_trace.log"
DEFAULT_PDF_GENERATION_TIMEOUT_SECONDS = 15 * 60
PDF_WORKFLOW_PROCESS_PATTERN = (
    "run_chatgpt_pdf_workflow|capture_chatgpt_pdf|fill_chatgpt_prompt|"
    "study-chatgpt-prompt|PERSONAL_LEARNING_OS_PDF_WORKFLOW"
)
_ACTIVE_PDF_PROCESSES: set[subprocess.Popen] = set()
_PDF_LIFECYCLE_LOCK = threading.RLock()
_ACTIVE_PDF_PROCESSES_LOCK = _PDF_LIFECYCLE_LOCK
_PDF_SHUTDOWN_EVENT = threading.Event()
_ERROR_MESSAGES = {
    "chatgpt_not_running": "未检测到 ChatGPT Windows 桌面端，请先启动并登录。",
    "input_not_found": "已检测到 ChatGPT，但没有找到可写入的聊天输入框。",
    "input_readonly": "ChatGPT 输入框当前不可编辑。",
    "input_not_empty": "ChatGPT 输入框已有内容，为避免覆盖，未执行写入。",
    "value_pattern_unavailable": "ChatGPT 输入框暂不支持后台写入。",
    "write_failed": "已找到 ChatGPT 输入框，但写入或校验失败。",
    "timeout": "等待 ChatGPT 桌面端响应超时。",
    "helper_missing": "桌面桥接助手文件缺失。",
    "bridge_error": "调用 ChatGPT 桌面桥接时发生错误。",
    "send_failed": "提示词已填入，但未能确认 ChatGPT 已开始生成。",
    "chatgpt_window_not_visible": "ChatGPT 仅在后台运行，但没有可见窗口。请先打开 ChatGPT 桌面窗口后重试。",
    "chatgpt_background": "ChatGPT 窗口位于后台、已最小化或无法保持前台，生成任务已及时停止。请打开 ChatGPT 窗口后重试。",
    "pdf_link_not_found": "ChatGPT 已收到提示词，但等待生成 PDF 超时。",
    "pdf_download_failed": "已发现 PDF 下载入口，但未能取得下载文件。",
    "generation_failed": "ChatGPT 未能完成本次生成请求，已停止抓取旧 PDF。",
    "job_marker_not_found": "尚未在 ChatGPT 对话中识别到本次生成请求。",
}


def request_chatgpt_pdf_shutdown() -> None:
    with _PDF_LIFECYCLE_LOCK:
        _PDF_SHUTDOWN_EVENT.set()


def _shutdown_result() -> ChatGPTBridgeResult:
    return ChatGPTBridgeResult(False, "cancelled", "应用正在退出，已停止 PDF 生成。")


def _process_exited(process: subprocess.Popen) -> bool:
    try:
        return process.poll() is not None
    except OSError:
        return False


def _stop_and_reap_process(process: subprocess.Popen, *, deadline: float) -> bool:
    if _process_exited(process):
        return True
    try:
        process.terminate()
    except OSError:
        pass
    remaining = deadline - time.monotonic()
    if remaining > 0:
        try:
            process.wait(timeout=remaining)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if _process_exited(process):
        return True
    try:
        process.kill()
    except OSError:
        return _process_exited(process)
    remaining = deadline - time.monotonic()
    if remaining > 0:
        try:
            process.wait(timeout=remaining)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return _process_exited(process)


def _communicate_registered_process(
    process: subprocess.Popen,
    *,
    timeout: float,
) -> tuple[str, str]:
    try:
        return process.communicate(timeout=timeout)
    except Exception:
        _stop_and_reap_process(process, deadline=time.monotonic() + 2.0)
        raise
    finally:
        if _process_exited(process):
            with _ACTIVE_PDF_PROCESSES_LOCK:
                _ACTIVE_PDF_PROCESSES.discard(process)


def _trace(event: str, **fields: object) -> None:
    """Append a lightweight trace line for the desktop PDF workflow."""
    try:
        TRACE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            **fields,
        }
        with TRACE_LOG_PATH.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def fill_chatgpt_prompt(prompt: str, *, dry_run: bool = False, timeout: int = 10) -> ChatGPTBridgeResult:
    """Fill ChatGPT's desktop prompt without focusing the window or sending it."""
    if not prompt.strip():
        return ChatGPTBridgeResult(False, "empty_prompt", "出题提示词为空，未执行写入。")
    if not _HELPER_PATH.exists():
        return ChatGPTBridgeResult(False, "helper_missing", _ERROR_MESSAGES["helper_missing"])

    prompt_file: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".txt",
            prefix="study-chatgpt-prompt-",
            delete=False,
        ) as handle:
            handle.write(prompt)
            prompt_file = Path(handle.name)

        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(_HELPER_PATH),
            "-PromptFile",
            str(prompt_file),
        ]
        if dry_run:
            command.append("-DryRun")
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        output = completed.stdout.strip().lstrip("\ufeff")
        payload = json.loads(output) if output else {}
        success = bool(payload.get("success"))
        code = str(payload.get("code") or "bridge_error")
        detail = str(payload.get("message") or "").strip()
        if success and code == "already_filled":
            message = "同一份提示词已经写入 ChatGPT 桌面端。"
        elif success:
            message = "已连接 ChatGPT 桌面端。"
        else:
            message = _ERROR_MESSAGES.get(code, detail or _ERROR_MESSAGES["bridge_error"])
        return ChatGPTBridgeResult(success, code, message)
    except subprocess.TimeoutExpired:
        return ChatGPTBridgeResult(False, "timeout", _ERROR_MESSAGES["timeout"])
    except Exception as error:
        return ChatGPTBridgeResult(False, "bridge_error", f"{_ERROR_MESSAGES['bridge_error']} {error}")
    finally:
        if prompt_file is not None:
            prompt_file.unlink(missing_ok=True)


def detect_chatgpt_desktop() -> ChatGPTBridgeResult:
    """Check whether the desktop prompt is accessible without changing it."""
    return fill_chatgpt_prompt("desktop bridge detection", dry_run=True)


def cancel_chatgpt_pdf_generation(
    *,
    deadline: float | None = None,
) -> ChatGPTBridgeResult:
    """Stop helper processes created by the ChatGPT PDF workflow."""
    started_at = time.monotonic()
    deadline = deadline if deadline is not None else started_at + 4.0
    process_deadline = min(deadline, started_at + 2.0)
    terminate_deadline = min(process_deadline, started_at + 1.5)
    with _ACTIVE_PDF_PROCESSES_LOCK:
        processes = list(_ACTIVE_PDF_PROCESSES)
    running_processes = [
        process for process in processes if not _process_exited(process)
    ]
    for process in processes:
        if process.poll() is not None:
            continue
        try:
            process.terminate()
        except OSError:
            continue

    for process in processes:
        if process.poll() is not None:
            continue
        remaining = terminate_deadline - time.monotonic()
        if remaining <= 0:
            continue
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            pass
    for process in processes:
        if process.poll() is not None:
            continue
        try:
            process.kill()
        except OSError:
            pass
    for process in processes:
        if process.poll() is not None:
            continue
        remaining = process_deadline - time.monotonic()
        if remaining <= 0:
            continue
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            pass

    stopped = sum(_process_exited(process) for process in running_processes)

    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        (
            "$pattern = '"
            + PDF_WORKFLOW_PROCESS_PATTERN.replace("'", "''")
            + "'; "
            "$self = $PID; "
            "$matches = Get-CimInstance Win32_Process | "
            "Where-Object { $_.ProcessId -ne $self -and $_.CommandLine -match $pattern }; "
            "$count = @($matches).Count; "
            "foreach ($p in $matches) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }; "
            "Get-ChildItem -Path $env:TEMP -Filter 'study-chatgpt-prompt-*' -ErrorAction SilentlyContinue | "
            "Remove-Item -Force -ErrorAction SilentlyContinue; "
            "Write-Output $count"
        ),
    ]
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ChatGPTBridgeResult(
                False,
                "cancel_failed",
                "停止 PDF 生成超过退出时限。",
            )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=remaining,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        unreaped = sum(process.poll() is None for process in processes)
        if completed.returncode or unreaped:
            _trace(
                "cancel_command_failed",
                returncode=completed.returncode,
                stderr_chars=len(completed.stderr or ""),
                unreaped=unreaped,
            )
            return ChatGPTBridgeResult(
                False,
                "cancel_failed",
                f"已确认结束 {stopped} 个 ChatGPT PDF 自动化进程，但命令行兜底清理失败。",
            )
        return ChatGPTBridgeResult(
            True,
            "cancelled",
            f"已确认结束 {stopped} 个 ChatGPT PDF 自动化进程，并执行系统级清理。",
        )
    except Exception:
        return ChatGPTBridgeResult(False, "cancel_failed", "停止 PDF 生成失败。")


def cleanup_chatgpt_temp_artifacts() -> int:
    """Remove prompt metadata and staging directories left by PDF workflows."""
    temp_dir = Path(tempfile.gettempdir())
    removed = 0
    for pattern in (
        "study-chatgpt-prompt-*",
        "study-chatgpt-placeholders-*",
        "study-chatgpt-download-keywords-*",
        "study-chatgpt-download-*",
        "study-chatgpt-capture-*",
    ):
        for path in temp_dir.glob(pattern):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
    return removed


def cleanup_generated_pdfs(*, retention_days: int = 14) -> int:
    """Remove old generated PDFs and return the number removed."""
    GENERATED_PDF_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now() - timedelta(days=max(1, retention_days))
    removed = 0
    for path in GENERATED_PDF_DIR.glob("*.pdf"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _visible_window_handles() -> set[int]:
    handles: set[int] = set()
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def collect(hwnd, _):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            handles.add(int(hwnd))
        return True

    ctypes.windll.user32.EnumWindows(enum_proc(collect), 0)
    return handles


def _window_title(hwnd: int) -> str:
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def _open_pdf_and_focus(path: Path, *, timeout: float = 12.0) -> bool:
    """Open a PDF through Windows and verify that a reader window appears."""
    before = _visible_window_handles()
    result = ctypes.windll.shell32.ShellExecuteW(None, "open", str(path), None, str(path.parent), 1)
    if int(result) <= 32:
        return False

    stem = path.stem.casefold()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidates = _visible_window_handles()
        matching = [
            hwnd
            for hwnd in candidates
            if stem in _window_title(hwnd).casefold()
        ]
        new_windows = [hwnd for hwnd in candidates - before if _window_title(hwnd).strip()]
        target = matching[0] if matching else (new_windows[0] if new_windows else None)
        if target:
            ctypes.windll.user32.ShowWindow(target, 9)
            ctypes.windll.user32.SetForegroundWindow(target)
            return True
        time.sleep(0.4)
    return False


def _pdf_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_archived_duplicate(path: Path) -> bool:
    try:
        digest = _pdf_digest(path)
    except OSError:
        return False
    for archived in GENERATED_PDF_DIR.glob("*.pdf"):
        try:
            if archived.stat().st_size == path.stat().st_size and _pdf_digest(archived) == digest:
                return True
        except OSError:
            continue
    return False


def _validate_pdf_candidate(path: Path) -> tuple[bool, str]:
    """Check that a captured file is a stable, structurally complete PDF."""
    try:
        first_size = path.stat().st_size
        if first_size < 5_000:
            return False, "PDF 文件过小，可能尚未生成完整。"
        time.sleep(0.8)
        if path.stat().st_size != first_size:
            return False, "PDF 文件仍在写入。"
        with path.open("rb") as file:
            header = file.read(5)
            file.seek(max(0, first_size - 2048))
            tail = file.read()
        if header != b"%PDF-":
            return False, "文件缺少 PDF 文件头。"
        if b"%%EOF" not in tail:
            return False, "PDF 文件缺少结束标记，可能尚未生成完整。"
        return True, ""
    except OSError as error:
        return False, f"无法读取 PDF：{error}"


def _wait_for_fresh_chatgpt_pdf(*, timeout: int, job_id: str) -> ChatGPTBridgeResult:
    """Poll until a new complete PDF appears, tolerating slow generation."""
    if _PDF_SHUTDOWN_EVENT.is_set():
        return _shutdown_result()
    deadline = time.monotonic() + max(30, timeout)
    initial_wait = min(20, max(5, timeout // 8))
    _trace("wait_for_pdf_start", job_id=job_id, timeout=timeout, initial_wait=initial_wait)
    time.sleep(initial_wait)
    if _PDF_SHUTDOWN_EVENT.is_set():
        return _shutdown_result()
    last_result = ChatGPTBridgeResult(False, "pdf_link_not_found", "尚未发现新 PDF。")
    retryable = {
        "stale_pdf_link",
        "pdf_link_not_found",
        "pdf_download_failed",
        "invalid_pdf",
        "bridge_error",
        "job_marker_not_found",
    }
    while time.monotonic() < deadline:
        if _PDF_SHUTDOWN_EVENT.is_set():
            return _shutdown_result()
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            break
        attempt_timeout = max(8, min(30, remaining))
        _trace("capture_attempt_start", job_id=job_id, attempt_timeout=attempt_timeout, remaining=remaining)
        last_result = capture_current_chatgpt_pdf(timeout=attempt_timeout, job_id=job_id)
        _trace(
            "capture_attempt_result",
            job_id=job_id,
            success=last_result.success,
            code=last_result.code,
            message=last_result.message,
            pdf_path=last_result.pdf_path,
        )
        if last_result.success:
            return last_result
        if last_result.code not in retryable:
            return last_result
        time.sleep(min(8, max(2, remaining // 10)))
        if _PDF_SHUTDOWN_EVENT.is_set():
            return _shutdown_result()
    return ChatGPTBridgeResult(
        False,
        "pdf_link_not_found",
        f"在 {timeout} 秒内未检测到新的完整 PDF。最后状态：{last_result.message}",
    )


def generate_pdf_with_chatgpt(
    prompt: str,
    *,
    timeout: int = DEFAULT_PDF_GENERATION_TIMEOUT_SECONDS,
) -> ChatGPTBridgeResult:
    """Send a prompt, wait for ChatGPT's PDF, archive it, and open it."""
    if _PDF_SHUTDOWN_EVENT.is_set():
        return _shutdown_result()
    _trace("generate_start", prompt_chars=len(prompt), timeout=timeout)
    if not prompt.strip():
        _trace("generate_reject", code="empty_prompt")
        return ChatGPTBridgeResult(False, "empty_prompt", "出题提示词为空，未执行生成。")
    if not _WORKFLOW_HELPER_PATH.exists():
        _trace("generate_reject", code="helper_missing", helper=str(_WORKFLOW_HELPER_PATH))
        return ChatGPTBridgeResult(False, "helper_missing", _ERROR_MESSAGES["helper_missing"])

    cleanup_generated_pdfs()
    job_id = f"PLOS_JOB_{uuid.uuid4().hex[:12].upper()}"
    _trace("job_created", job_id=job_id)
    workflow_prompt = f"{prompt.rstrip()}\n\n[JOB_ID:{job_id}]"
    prompt_file: Path | None = None
    placeholder_file: Path | None = None
    download_keyword_file: Path | None = None
    process: subprocess.Popen | None = None
    staging_dir: Path | None = None
    _PDF_LIFECYCLE_LOCK.acquire()
    lifecycle_lock_held = True
    try:
        if _PDF_SHUTDOWN_EVENT.is_set():
            return _shutdown_result()
        staging_dir = Path(tempfile.mkdtemp(prefix="study-chatgpt-download-"))
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".txt",
            prefix="study-chatgpt-prompt-",
            delete=False,
        ) as handle:
            handle.write(workflow_prompt)
            prompt_file = Path(handle.name)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".txt",
            prefix="study-chatgpt-placeholders-",
            delete=False,
        ) as handle:
            handle.write("\n".join(["与 ChatGPT 聊天", "有问题，尽管问", "询问任何问题"]))
            placeholder_file = Path(handle.name)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".txt",
            prefix="study-chatgpt-download-keywords-",
            delete=False,
        ) as handle:
            handle.write("\n".join(["下载", "Download", ".pdf", "PDF"]))
            download_keyword_file = Path(handle.name)
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(_WORKFLOW_HELPER_PATH),
            "-PromptFile",
            str(prompt_file),
            "-DownloadDirectory",
            str(Path.home() / "Downloads"),
            "-StagingDirectory",
            str(staging_dir),
            "-TimeoutSeconds",
            str(timeout),
            "-SendDelaySeconds",
            "5",
            "-RetryDelaySeconds",
            "5",
            "-JobId",
            job_id,
            "-PlaceholderFile",
            str(placeholder_file),
            "-DownloadKeywordFile",
            str(download_keyword_file),
            "-TraceFile",
            str(TRACE_LOG_PATH),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with _ACTIVE_PDF_PROCESSES_LOCK:
            _ACTIVE_PDF_PROCESSES.add(process)
        _PDF_LIFECYCLE_LOCK.release()
        lifecycle_lock_held = False
        _trace("workflow_process_started", job_id=job_id, pid=process.pid)
        stdout, _stderr = _communicate_registered_process(
            process,
            timeout=timeout + 30,
        )
        completed_returncode = process.returncode
        output = (stdout or "").strip().lstrip("\ufeff")
        _trace(
            "workflow_process_finished",
            job_id=job_id,
            returncode=completed_returncode,
            stdout_preview=output[:600],
            stderr_preview=(_stderr or "")[:600],
        )
        payload = json.loads(output) if output else {}
        success = bool(payload.get("success"))
        code = str(payload.get("code") or "bridge_error")
        detail = str(payload.get("message") or "").strip()
        staged_pdf = Path(str(payload.get("pdf_path") or ""))
        if success and code == "generation_started":
            _trace("generation_started", job_id=job_id)
            return _wait_for_fresh_chatgpt_pdf(timeout=timeout, job_id=job_id)
        if not success or not staged_pdf.is_file():
            _trace("workflow_failed", job_id=job_id, code=code, detail=detail)
            return ChatGPTBridgeResult(
                False,
                code,
                _ERROR_MESSAGES.get(code, detail or _ERROR_MESSAGES["bridge_error"]),
            )

        GENERATED_PDF_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = staged_pdf.name or "chatgpt_practice.pdf"
        target = GENERATED_PDF_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{safe_name}"
        shutil.move(str(staged_pdf), target)
        _trace("pdf_archived", job_id=job_id, path=str(target), size=target.stat().st_size)
        if _open_pdf_and_focus(target):
            _trace("pdf_opened", job_id=job_id, path=str(target))
            return ChatGPTBridgeResult(True, "pdf_opened", "PDF 已生成、归档并打开。", str(target))
        _trace("pdf_open_failed", job_id=job_id, path=str(target))
        return ChatGPTBridgeResult(True, "pdf_downloaded", "PDF 已生成并归档，但未能自动打开。", str(target))
    except subprocess.TimeoutExpired:
        _trace("workflow_timeout", job_id=job_id, timeout=timeout)
        return ChatGPTBridgeResult(False, "pdf_link_not_found", "PDF 抓取进程超时，请确认 ChatGPT 已生成 PDF。")
    except Exception as error:
        _trace("workflow_exception", job_id=job_id, error_type=type(error).__name__)
        return ChatGPTBridgeResult(False, "bridge_error", _ERROR_MESSAGES["bridge_error"])
    finally:
        if lifecycle_lock_held:
            _PDF_LIFECYCLE_LOCK.release()
        if prompt_file is not None:
            prompt_file.unlink(missing_ok=True)
        if placeholder_file is not None:
            placeholder_file.unlink(missing_ok=True)
        if download_keyword_file is not None:
            download_keyword_file.unlink(missing_ok=True)
        if staging_dir is not None:
            shutil.rmtree(staging_dir, ignore_errors=True)


def capture_current_chatgpt_pdf(*, timeout: int = 120, job_id: str | None = None) -> ChatGPTBridgeResult:
    """Download the newest PDF exposed by the current ChatGPT conversation."""
    if _PDF_SHUTDOWN_EVENT.is_set():
        return _shutdown_result()
    _trace("capture_start", job_id=job_id or "", timeout=timeout)
    cleanup_generated_pdfs()
    staging_dir: Path | None = None
    keyword_file: Path | None = None
    process: subprocess.Popen | None = None
    started_at_utc = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    _PDF_LIFECYCLE_LOCK.acquire()
    lifecycle_lock_held = True
    try:
        if _PDF_SHUTDOWN_EVENT.is_set():
            return _shutdown_result()
        staging_dir = Path(tempfile.mkdtemp(prefix="study-chatgpt-capture-"))
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".txt",
            prefix="study-chatgpt-download-keywords-",
            delete=False,
        ) as handle:
            handle.write("\n".join(["下载", "Download", ".pdf", "PDF"]))
            keyword_file = Path(handle.name)
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(_CAPTURE_HELPER_PATH),
            "-DownloadDirectory",
            str(Path.home() / "Downloads"),
            "-StagingDirectory",
            str(staging_dir),
            "-DownloadKeywordFile",
            str(keyword_file),
            "-TimeoutSeconds",
            str(timeout),
            "-JobId",
            str(job_id or ""),
            "-StartedAtUtc",
            started_at_utc,
            "-TraceFile",
            str(TRACE_LOG_PATH),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with _ACTIVE_PDF_PROCESSES_LOCK:
            _ACTIVE_PDF_PROCESSES.add(process)
        _PDF_LIFECYCLE_LOCK.release()
        lifecycle_lock_held = False
        stdout, _stderr = _communicate_registered_process(
            process,
            timeout=timeout + 20,
        )
        output = (stdout or "").strip().lstrip("\ufeff")
        _trace(
            "capture_process_finished",
            job_id=job_id or "",
            returncode=process.returncode,
            stdout_preview=output[:600],
        )
        payload = json.loads(output or "{}")
        staged_pdf = Path(str(payload.get("pdf_path") or ""))
        if not payload.get("success") or not staged_pdf.is_file():
            code = str(payload.get("code") or "pdf_link_not_found")
            _trace("capture_failed", job_id=job_id or "", code=code, message=str(payload.get("message") or code))
            return ChatGPTBridgeResult(False, code, _ERROR_MESSAGES.get(code, str(payload.get("message") or code)))
        valid, validation_message = _validate_pdf_candidate(staged_pdf)
        if not valid:
            _trace("capture_invalid_pdf", job_id=job_id or "", path=str(staged_pdf), message=validation_message)
            return ChatGPTBridgeResult(False, "invalid_pdf", validation_message)
        if _is_archived_duplicate(staged_pdf):
            _trace("capture_stale_duplicate", job_id=job_id or "", path=str(staged_pdf))
            return ChatGPTBridgeResult(
                False,
                "stale_pdf_link",
                "抓取到的是历史重复 PDF，已阻止再次打开。请保持最新 ChatGPT 回复可见后重试。",
            )
        GENERATED_PDF_DIR.mkdir(parents=True, exist_ok=True)
        target = GENERATED_PDF_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{staged_pdf.name}"
        shutil.move(str(staged_pdf), target)
        _trace("capture_archived", job_id=job_id or "", path=str(target), size=target.stat().st_size)
        if _open_pdf_and_focus(target):
            _trace("capture_opened", job_id=job_id or "", path=str(target))
            return ChatGPTBridgeResult(True, "pdf_opened", "当前 ChatGPT PDF 已归档并打开。", str(target))
        _trace("capture_open_failed", job_id=job_id or "", path=str(target))
        return ChatGPTBridgeResult(True, "pdf_downloaded", "当前 ChatGPT PDF 已归档，但未能自动打开。", str(target))
    except subprocess.TimeoutExpired:
        _trace("capture_timeout", job_id=job_id or "", timeout=timeout)
        return ChatGPTBridgeResult(False, "pdf_link_not_found", "PDF 抓取进程超时，请确认 ChatGPT 已生成 PDF。")
    except Exception as error:
        _trace("capture_exception", job_id=job_id or "", error_type=type(error).__name__)
        return ChatGPTBridgeResult(False, "bridge_error", _ERROR_MESSAGES["bridge_error"])
    finally:
        if lifecycle_lock_held:
            _PDF_LIFECYCLE_LOCK.release()
        if keyword_file is not None:
            keyword_file.unlink(missing_ok=True)
        if staging_dir is not None:
            shutil.rmtree(staging_dir, ignore_errors=True)
