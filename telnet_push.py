import argparse
import json
import re
import telnetlib
import time
from datetime import datetime
from pathlib import Path

from config_common import GENERATED_DIR, ROOT


DEFAULT_TELNET_HOST = "127.0.0.1"
DEFAULT_PROJECT = next(iter(sorted(ROOT.glob("*.gns3"))), None)
SUPPORTED_ROUTER_TYPES = {"dynamips", "iou", "qemu"}
WRAPPER_LINES = {"enable", "configure terminal", "conf t", "end", "write memory", "wr mem"}

RE_PRESS_RETURN = re.compile(rb"Press RETURN to get started", re.IGNORECASE)
RE_SETUP_DIALOG = re.compile(rb"initial configuration dialog\?\s*\[yes/no\]:", re.IGNORECASE)
RE_AUTOINSTALL = re.compile(rb"terminate autoinstall\?\s*\[yes\]:", re.IGNORECASE)
RE_PASSWORD = re.compile(rb"Password:\s*$", re.IGNORECASE)
RE_OVERWRITE_CONFIRM = re.compile(rb"overwrite.*\[\s*confirm\s*\]", re.IGNORECASE)
RE_CONFIRM_ONLY = re.compile(rb"\[\s*confirm\s*\]\s*$", re.IGNORECASE)
RE_YES_NO = re.compile(rb"\[\s*yes/no\s*\]:\s*$", re.IGNORECASE)
RE_DEST_FILENAME = re.compile(rb"Destination filename.*\?\s*$", re.IGNORECASE)
RE_PROMPT_ANY = re.compile(rb"(?m)^[^\r\n]*[>#]\s*$")
RE_PROMPT_PRIV = re.compile(rb"(?m)^[^\r\n]*#\s*$")
RE_PROMPT_USER = re.compile(rb"(?m)^[^\r\n]*>\s*$")
RE_PROMPT_CONF = re.compile(rb"(?m)^[^\r\n]*\([^\)]*\)#\s*$")


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def log_router(router_name: str, message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{router_name}] {message}", flush=True)


def load_gns3_nodes(project_path: Path) -> list[dict]:
    data = json.loads(project_path.read_text(encoding="utf-8", errors="replace"))
    return data.get("topology", {}).get("nodes", [])


def load_gns3_consoles(project_path: Path) -> dict[str, dict]:
    consoles: dict[str, dict] = {}
    for node in load_gns3_nodes(project_path):
        name = node.get("name")
        console = node.get("console")
        if not name or console is None:
            continue
        consoles[name] = {
            "port": int(console),
            "node_type": node.get("node_type"),
        }
    return consoles


def config_path(router_name: str, suffix: str) -> Path:
    return GENERATED_DIR / f"{router_name}_{suffix}.cfg"


def clean_config_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        stripped = line.strip()
        if not stripped or stripped == "!":
            continue
        if stripped.lower() in WRAPPER_LINES:
            continue
        lines.append(line)
    return lines


def load_router_commands(router_name: str, suffix: str) -> list[str]:
    path = config_path(router_name, suffix)
    return clean_config_lines(path.read_text(encoding="utf-8"))


def tn_drain(session: telnetlib.Telnet, seconds: float = 0.35) -> bytes:
    end_time = time.time() + seconds
    buffer = b""
    while time.time() < end_time:
        try:
            chunk = session.read_very_eager()
        except EOFError:
            break
        if chunk:
            buffer += chunk
        else:
            time.sleep(0.05)
    return buffer


def send_line(session: telnetlib.Telnet, line: str) -> None:
    session.write(line.encode("utf-8", errors="replace") + b"\r\n")


def spam_enter(session: telnetlib.Telnet, count: int = 8, delay: float = 0.15) -> None:
    for _ in range(count):
        session.write(b"\r\n")
        time.sleep(delay)


def get_last_prompt_line(buffer: bytes) -> bytes | None:
    matches = list(RE_PROMPT_ANY.finditer(buffer))
    if not matches:
        return None
    return matches[-1].group(0).strip()


def wait_for_stable_prompt(
    session: telnetlib.Telnet,
    router_name: str,
    timeout: float = 12.0,
    require_twice: bool = True,
) -> bytes:
    start_time = time.time()
    last_prompt = None
    same_count = 0
    buffer = b""

    while time.time() - start_time < timeout:
        spam_enter(session, count=3, delay=0.12)
        buffer += tn_drain(session, 0.6)

        if RE_PRESS_RETURN.search(buffer):
            session.write(b"\r\n")
            buffer = b""
            continue
        if RE_SETUP_DIALOG.search(buffer):
            log_router(router_name, "Setup dialog detected -> answering 'no'")
            send_line(session, "no")
            buffer = b""
            continue
        if RE_AUTOINSTALL.search(buffer):
            log_router(router_name, "Autoinstall prompt detected -> answering 'no'")
            send_line(session, "no")
            buffer = b""
            continue

        prompt = get_last_prompt_line(buffer)
        if prompt:
            if not require_twice:
                return buffer
            if prompt == last_prompt:
                same_count += 1
            else:
                last_prompt = prompt
                same_count = 1
            if same_count >= 2:
                return buffer
        time.sleep(0.1)

    raise RuntimeError(f"{router_name}: unable to determine stable prompt")


def ensure_privileged(
    session: telnetlib.Telnet,
    router_name: str,
    enable_password: str = "",
) -> None:
    buffer = wait_for_stable_prompt(session, router_name, timeout=14.0, require_twice=True)
    if RE_PROMPT_PRIV.search(buffer):
        return
    if RE_PROMPT_USER.search(buffer):
        send_line(session, "enable")
        buffer = tn_drain(session, 0.8)
        if RE_PASSWORD.search(buffer):
            if not enable_password:
                raise RuntimeError(f"{router_name}: enable password requested but not provided")
            send_line(session, enable_password)
        buffer = wait_for_stable_prompt(session, router_name, timeout=14.0, require_twice=True)
        if RE_PROMPT_PRIV.search(buffer):
            return
    raise RuntimeError(f"{router_name}: unable to enter privileged mode")


def calm_console_spam(session: telnetlib.Telnet, router_name: str) -> None:
    send_line(session, "terminal length 0")
    time.sleep(0.2)
    tn_drain(session, 0.4)
    send_line(session, "configure terminal")
    time.sleep(0.2)
    tn_drain(session, 0.4)
    send_line(session, "no logging console")
    time.sleep(0.2)
    tn_drain(session, 0.4)
    send_line(session, "end")
    time.sleep(0.2)
    tn_drain(session, 0.4)
    log_router(router_name, "Console spam reduced")


def enter_config_mode(session: telnetlib.Telnet, router_name: str) -> None:
    send_line(session, "configure terminal")
    buffer = wait_for_stable_prompt(session, router_name, timeout=12.0, require_twice=False)
    if RE_PROMPT_CONF.search(buffer):
        return
    send_line(session, "configure terminal")
    buffer = wait_for_stable_prompt(session, router_name, timeout=12.0, require_twice=False)
    if not RE_PROMPT_CONF.search(buffer):
        raise RuntimeError(f"{router_name}: failed to enter config mode")


def push_config_lines(session: telnetlib.Telnet, router_name: str, lines: list[str], progress_every: int = 25) -> None:
    total = len(lines)
    log_router(router_name, f"Pushing {total} config lines")
    for index, line in enumerate(lines, start=1):
        session.write(line.encode("utf-8", errors="replace") + b"\r\n")
        if index % progress_every == 0 or index == total:
            time.sleep(0.15)
            tn_drain(session, 0.25)


def handle_save_prompts(session: telnetlib.Telnet, router_name: str, timeout: float = 10.0) -> None:
    start_time = time.time()
    buffer = b""
    while time.time() - start_time < timeout:
        buffer += tn_drain(session, 0.6)
        if RE_DEST_FILENAME.search(buffer):
            session.write(b"\r\n")
            buffer = b""
            continue
        if RE_YES_NO.search(buffer):
            send_line(session, "yes")
            buffer = b""
            continue
        if RE_OVERWRITE_CONFIRM.search(buffer) or RE_CONFIRM_ONLY.search(buffer):
            session.write(b"\r\n")
            buffer = b""
            continue
        if get_last_prompt_line(buffer):
            return
    log_router(router_name, "Save prompt timeout, continuing")


def connect_privileged(host: str, port: int, router_name: str, enable_password: str = "") -> telnetlib.Telnet:
    session = telnetlib.Telnet(host, port, timeout=20)
    time.sleep(3.0)
    tn_drain(session, 1.0)
    wait_for_stable_prompt(session, router_name, timeout=18.0, require_twice=True)
    ensure_privileged(session, router_name, enable_password=enable_password)
    calm_console_spam(session, router_name)
    return session


def reset_router_runtime(
    host: str,
    port: int,
    router_name: str,
    enable_password: str = "",
) -> None:
    log_router(router_name, "Resetting router runtime: erase startup-config only")
    session = None
    try:
        session = connect_privileged(host, port, router_name, enable_password=enable_password)
        send_line(session, "erase startup-config")
        time.sleep(0.4)
        handle_save_prompts(session, router_name, timeout=6.0)
        tn_drain(session, 0.8)
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


def deploy_with_retries(
    host: str,
    port: int,
    router_name: str,
    cfg_lines: list[str],
    enable_password: str = "",
    max_attempts: int = 8,
) -> None:
    last_error = None
    for attempt in range(1, max_attempts + 1):
        warmup = min(2.0 + attempt * 1.2, 12.0)
        session = None
        try:
            log_router(router_name, f"Attempt {attempt}/{max_attempts}: connecting to {host}:{port}")
            session = telnetlib.Telnet(host, port, timeout=20)
            time.sleep(warmup)
            tn_drain(session, 1.2)
            wait_for_stable_prompt(session, router_name, timeout=18.0, require_twice=True)
            ensure_privileged(session, router_name, enable_password=enable_password)
            calm_console_spam(session, router_name)
            enter_config_mode(session, router_name)
            push_config_lines(session, router_name, cfg_lines)
            send_line(session, "end")
            time.sleep(0.2)
            tn_drain(session, 0.5)
            send_line(session, "write memory")
            time.sleep(0.4)
            handle_save_prompts(session, router_name)
            tn_drain(session, 0.8)
            log_router(router_name, "DONE")
            return
        except Exception as exc:
            last_error = exc
            log_router(router_name, f"Attempt {attempt} FAILED: {exc}")
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
    raise RuntimeError(f"{router_name}: all attempts failed. Last error: {last_error}")


def router_console(project_path: Path, router_name: str) -> int:
    consoles = load_gns3_consoles(project_path)
    if router_name not in consoles:
        raise ValueError(f"{router_name}: router not found in {project_path.name}")
    node_info = consoles[router_name]
    if node_info["node_type"] not in SUPPORTED_ROUTER_TYPES:
        raise ValueError(
            f"{router_name}: node type {node_info['node_type']} is not IOS-compatible for this Telnet push"
        )
    return node_info["port"]


def push_router_config(
    router_name: str,
    suffix: str = "full",
    project_path: Path | None = None,
    host: str = DEFAULT_TELNET_HOST,
    enable_password: str = "",
) -> None:
    project = project_path or DEFAULT_PROJECT
    if project is None:
        raise FileNotFoundError("No .gns3 project found in the current directory")
    commands = load_router_commands(router_name, suffix)
    port = router_console(project, router_name)
    deploy_with_retries(host, port, router_name, commands, enable_password=enable_password)


def reset_router_before_push(
    router_name: str,
    project_path: Path | None = None,
    host: str = DEFAULT_TELNET_HOST,
    enable_password: str = "",
) -> None:
    project = project_path or DEFAULT_PROJECT
    if project is None:
        raise FileNotFoundError("No .gns3 project found in the current directory")
    port = router_console(project, router_name)
    reset_router_runtime(host, port, router_name, enable_password=enable_password)


def main() -> None:
    parser = argparse.ArgumentParser(description="Robust GNS3 Telnet config push")
    parser.add_argument("router", help="Router name, for example PE1")
    parser.add_argument("--suffix", default="full", help="Config suffix to push")
    parser.add_argument("--project", help="Path to .gns3 project file")
    parser.add_argument("--host", default=DEFAULT_TELNET_HOST, help="Telnet host")
    parser.add_argument("--enable-pass", default="", help="Enable password if required")
    args = parser.parse_args()

    project = Path(args.project) if args.project else DEFAULT_PROJECT
    if project is None:
        raise SystemExit("No .gns3 project found")
    push_router_config(args.router, args.suffix, project, args.host, args.enable_pass)


if __name__ == "__main__":
    main()
