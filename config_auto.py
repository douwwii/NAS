import argparse
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from config_common import GENERATED_DIR, load_intent, write_router_configs, write_visualization_intent
from generate_phase0_setup import build_phase0_configs
from generate_phase1_mpls import build_phase1_configs
from generate_phase2_vpnv4 import build_phase2_configs
from generate_phase3_clients import build_phase3_configs
from gns3_runtime_clean import clean_dynamips_configs
from telnet_push import DEFAULT_PROJECT, push_router_config, reset_router_before_push, router_console


PHASE_ORDER = [
    "phase0_setup",
    "phase1_mpls",
    "phase2_vpnv4",
    "phase3_clients",
]


def resolved_workers(task_count: int, requested_workers: int) -> int:
    if task_count <= 0:
        return 0
    auto_workers = min(task_count, max(1, os.cpu_count() or 1))
    workers = auto_workers if requested_workers <= 0 else max(1, requested_workers)
    return min(workers, task_count)


def pushable_routers(phase_configs: dict[str, list[str]], project_path: Path) -> tuple[list[str], list[tuple[str, str]]]:
    pushable: list[str] = []
    skipped: list[tuple[str, str]] = []
    for router_name in phase_configs:
        try:
            router_console(project_path, router_name)
        except ValueError as exc:
            skipped.append((router_name, str(exc)))
            continue
        pushable.append(router_name)
    return pushable, skipped


def run_parallel_router_jobs(
    router_names: list[str],
    workers: int,
    action,
) -> list[tuple[str, Exception | None]]:
    if not router_names:
        return []
    if workers <= 1:
        results = []
        for router_name in router_names:
            try:
                action(router_name)
                results.append((router_name, None))
            except Exception as exc:
                results.append((router_name, exc))
        return results

    results: list[tuple[str, Exception | None]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(action, router_name): router_name for router_name in router_names}
        for future in as_completed(future_map):
            router_name = future_map[future]
            try:
                future.result()
                results.append((router_name, None))
            except Exception as exc:
                results.append((router_name, exc))
    return results


def trim_footer(lines: list[str]) -> list[str]:
    trimmed = list(lines)
    while trimmed and trimmed[-1] in {"write memory", "end", "!"}:
        trimmed.pop()
    return trimmed


def without_header(lines: list[str]) -> list[str]:
    return trim_footer(lines)[3:]


def build_full_configs(intent: dict) -> dict[str, list[str]]:
    phase0 = build_phase0_configs(intent)
    phase1 = build_phase1_configs(intent)
    phase2 = build_phase2_configs(intent)
    phase3 = build_phase3_configs(intent)

    full_configs: dict[str, list[str]] = {}
    for router_name in intent["routeurs"]:
        lines = [
            "configure terminal",
            f"hostname {router_name}",
            "!",
        ]
        for phase_configs in (phase0, phase1, phase2, phase3):
            if router_name not in phase_configs:
                continue
            body = without_header(phase_configs[router_name])
            if body:
                lines.extend(body + ["!"])
        lines.extend(["end", "write memory"])
        full_configs[router_name] = lines

    return full_configs


def generate_all_configs() -> dict[str, dict[str, list[str]]]:
    intent = load_intent()
    write_visualization_intent(intent)
    phase_map = {
        "phase0_setup": build_phase0_configs(intent),
        "phase1_mpls": build_phase1_configs(intent),
        "phase2_vpnv4": build_phase2_configs(intent),
        "phase3_clients": build_phase3_configs(intent),
    }
    for phase_name, configs in phase_map.items():
        write_router_configs(configs, GENERATED_DIR, phase_name)

    full_configs = build_full_configs(intent)
    write_router_configs(full_configs, GENERATED_DIR, "full")
    phase_map["full"] = full_configs
    return phase_map


def push_in_order(
    phase_map: dict[str, dict[str, list[str]]],
    phase_names: list[str],
    project_path: Path,
    host: str,
    enable_password: str,
    workers: int,
) -> None:
    for phase_name in phase_names:
        print(f"\n=== {phase_name} ===")
        phase_pushable, skipped = pushable_routers(phase_map[phase_name], project_path)
        for router_name, reason in skipped:
            print(f"Skipped {router_name} {phase_name}: {reason}")
        phase_workers = resolved_workers(len(phase_pushable), workers)
        if phase_workers > 1:
            print(f"Parallel push for {phase_name}: {phase_workers} workers")
        results = run_parallel_router_jobs(
            phase_pushable,
            phase_workers,
            lambda router_name: push_router_config(router_name, phase_name, project_path, host, enable_password),
        )
        for router_name, error in sorted(results, key=lambda item: item[0]):
            if error is None:
                print(f"Pushed {router_name} {phase_name}")
            else:
                print(f"Failed {router_name} {phase_name}: {error}")


def reset_pushable_routers(
    phase_map: dict[str, dict[str, list[str]]],
    project_path: Path,
    host: str,
    enable_password: str,
    workers: int,
) -> None:
    resettable, skipped = pushable_routers(phase_map["full"], project_path)
    for router_name, reason in skipped:
        print(f"Skipped reset for {router_name}: {reason}")
    reset_workers = resolved_workers(len(resettable), workers)
    if reset_workers > 1:
        print(f"Parallel reset: {reset_workers} workers")
    results = run_parallel_router_jobs(
        resettable,
        reset_workers,
        lambda router_name: reset_router_before_push(router_name, project_path, host, enable_password),
    )
    for router_name, error in sorted(results, key=lambda item: item[0]):
        if error is None:
            print(f"Reset {router_name}")
        else:
            print(f"Failed reset {router_name}: {error}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate phased configs and optionally push them to GNS in validation order."
    )
    parser.add_argument(
        "--push-phases",
        action="store_true",
        help="Push Phase 0, then Phase 1, then Phase 2, then Phase 3 through Telnet.",
    )
    parser.add_argument(
        "--push-full",
        help="Push the final full config of one router or ALL routers.",
    )
    parser.add_argument(
        "--project",
        help="Path to the .gns3 project file. Default: first .gns3 file found in the current directory.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Telnet host used for all console connections.",
    )
    parser.add_argument(
        "--enable-pass",
        default="",
        help="Enable password if required by the routers.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Number of parallel router jobs for reset/push (0=auto).",
    )
    args = parser.parse_args()

    phase_map = generate_all_configs()
    print(f"Generated phased and full configs in {GENERATED_DIR}")

    project_path = Path(args.project) if args.project else DEFAULT_PROJECT
    if (args.push_phases or args.push_full) and project_path is None:
        raise SystemExit("No .gns3 project found. Use --project.")

    if args.push_phases:
        clean_dynamips_configs(Path.cwd())
        reset_pushable_routers(phase_map, project_path, args.host, args.enable_pass, args.workers)
        push_in_order(phase_map, PHASE_ORDER, project_path, args.host, args.enable_pass, args.workers)

    if args.push_full:
        clean_dynamips_configs(Path.cwd())
        reset_pushable_routers(phase_map, project_path, args.host, args.enable_pass, args.workers)
        if args.push_full == "ALL":
            full_pushable, skipped = pushable_routers(phase_map["full"], project_path)
            for router_name, reason in skipped:
                print(f"Skipped {router_name} full: {reason}")
            full_workers = resolved_workers(len(full_pushable), args.workers)
            if full_workers > 1:
                print(f"Parallel full push: {full_workers} workers")
            results = run_parallel_router_jobs(
                full_pushable,
                full_workers,
                lambda router_name: push_router_config(router_name, "full", project_path, args.host, args.enable_pass),
            )
            for router_name, error in sorted(results, key=lambda item: item[0]):
                if error is None:
                    print(f"Pushed {router_name} full")
                else:
                    print(f"Failed {router_name} full: {error}")
        else:
            router_console(project_path, args.push_full)
            push_router_config(args.push_full, "full", project_path, args.host, args.enable_pass)
            print(f"Pushed {args.push_full} full")


if __name__ == "__main__":
    main()
