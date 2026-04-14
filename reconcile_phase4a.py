from __future__ import annotations

from collections import defaultdict

from desired_state import load_phase4a_desired_state


def _normalize_line(line: str) -> str:
    return line.rstrip()


def parse_running_config(text: str) -> dict:
    state = {
        "interfaces": {},
        "vrfs": {},
        "bgp": None,
    }

    current_interface = None
    current_vrf = None
    current_bgp = None
    current_af = None

    for raw_line in text.splitlines():
        line = _normalize_line(raw_line)
        stripped = line.strip()
        if not stripped:
            continue

        if not line.startswith(" "):
            current_af = None
            if stripped.startswith("interface "):
                current_interface = stripped.split(maxsplit=1)[1]
                state["interfaces"][current_interface] = {
                    "description": None,
                    "vrf": None,
                    "ip": None,
                    "mask": None,
                    "shutdown": False,
                }
                current_vrf = None
                current_bgp = None
                continue
            if stripped.startswith("ip vrf "):
                current_vrf = stripped.split(maxsplit=2)[2]
                state["vrfs"][current_vrf] = {
                    "rd": None,
                    "rt_import": set(),
                    "rt_export": set(),
                }
                current_interface = None
                current_bgp = None
                continue
            if stripped.startswith("router bgp "):
                current_bgp = {
                    "as": int(stripped.split()[2]),
                    "router_id": None,
                    "neighbors": {},
                    "address_families": defaultdict(
                        lambda: {
                            "neighbors": {},
                            "networks": set(),
                        }
                    ),
                }
                state["bgp"] = current_bgp
                current_interface = None
                current_vrf = None
                continue

            current_interface = None
            current_vrf = None
            current_bgp = None
            continue

        content = stripped
        if current_interface is not None:
            if content.startswith("description "):
                state["interfaces"][current_interface]["description"] = content.split(" ", 1)[1]
            elif content.startswith("ip vrf forwarding "):
                state["interfaces"][current_interface]["vrf"] = content.split(" ", 3)[3]
            elif content.startswith("ip address "):
                parts = content.split()
                if len(parts) >= 4:
                    state["interfaces"][current_interface]["ip"] = parts[2]
                    state["interfaces"][current_interface]["mask"] = parts[3]
            elif content == "shutdown":
                state["interfaces"][current_interface]["shutdown"] = True
            continue

        if current_vrf is not None:
            if content.startswith("rd "):
                state["vrfs"][current_vrf]["rd"] = content.split(" ", 1)[1]
            elif content.startswith("route-target import "):
                state["vrfs"][current_vrf]["rt_import"].add(content.split(" ", 2)[2])
            elif content.startswith("route-target export "):
                state["vrfs"][current_vrf]["rt_export"].add(content.split(" ", 2)[2])
            continue

        if current_bgp is not None:
            if content.startswith("bgp router-id "):
                current_bgp["router_id"] = content.split(" ", 2)[2]
                continue
            if content.startswith("neighbor ") and current_af is None:
                parts = content.split()
                if len(parts) >= 4 and parts[2] == "remote-as":
                    current_bgp["neighbors"][parts[1]] = {
                        "remote_as": int(parts[3]),
                    }
                continue
            if content.startswith("address-family "):
                current_af = content.split(" ", 1)[1]
                _ = current_bgp["address_families"][current_af]
                continue
            if content == "exit-address-family":
                current_af = None
                continue
            if current_af is not None:
                af_state = current_bgp["address_families"][current_af]
                parts = content.split()
                if len(parts) >= 4 and parts[0] == "neighbor" and parts[2] == "remote-as":
                    af_state["neighbors"][parts[1]] = {
                        "remote_as": int(parts[3]),
                        "activate": False,
                        "send_community_both": False,
                    }
                elif len(parts) >= 3 and parts[0] == "neighbor" and parts[2] == "activate":
                    af_state["neighbors"].setdefault(
                        parts[1],
                        {"remote_as": None, "activate": False, "send_community_both": False},
                    )["activate"] = True
                elif parts[:4] == ["neighbor", parts[1] if len(parts) > 1 else "", "send-community", "both"]:
                    af_state["neighbors"].setdefault(
                        parts[1],
                        {"remote_as": None, "activate": False, "send_community_both": False},
                    )["send_community_both"] = True
                elif len(parts) >= 4 and parts[0] == "network" and parts[2] == "mask":
                    af_state["networks"].add((parts[1], parts[3]))
            continue

    for vrf_state in state["vrfs"].values():
        vrf_state["rt_import"] = sorted(vrf_state["rt_import"])
        vrf_state["rt_export"] = sorted(vrf_state["rt_export"])

    if state["bgp"] is not None:
        state["bgp"]["address_families"] = dict(state["bgp"]["address_families"])

    return state


def render_reconcile_commands(router_name: str, desired: dict, current: dict) -> list[str]:
    role = desired["role"]
    lines = ["configure terminal"]
    changed = False

    if role == "PE":
        changed |= _reconcile_pe(lines, desired, current)
    elif role == "CE":
        changed |= _reconcile_ce(lines, desired, current)
    else:
        raise ValueError(f"{router_name}: unsupported role {role}")

    if not changed:
        return []

    lines.extend(["end", "write memory"])
    return lines


def _interface_needs_reset(current_interface: dict | None, desired_interface: dict) -> bool:
    if current_interface is None:
        return False
    return any(
        current_interface.get(key) != desired_interface.get(key)
        for key in ("description", "vrf", "ip", "mask")
    )


def _interface_matches(current_interface: dict | None, desired_interface: dict) -> bool:
    if current_interface is None:
        return False
    return all(
        current_interface.get(key) == desired_interface.get(key)
        for key in ("description", "vrf", "ip", "mask")
    )


def _reconcile_pe(lines: list[str], desired: dict, current: dict) -> bool:
    changed = False
    current_interfaces = current.get("interfaces", {})
    desired_interfaces = desired["interfaces"]
    current_vrfs = current.get("vrfs", {})
    desired_vrfs = desired["vrfs"]
    bgp_state = current.get("bgp") or {"address_families": {}, "neighbors": {}, "router_id": None}
    current_afs = bgp_state.get("address_families", {})

    for interface_name, interface_state in sorted(current_interfaces.items()):
        if interface_state.get("vrf") and interface_name not in desired_interfaces:
            lines.extend([f"default interface {interface_name}", "!"])
            changed = True

    for interface_name in sorted(desired_interfaces):
        desired_interface = desired_interfaces[interface_name]
        current_interface = current_interfaces.get(interface_name)
        if _interface_needs_reset(current_interface, desired_interface):
            lines.extend([f"default interface {interface_name}", "!"])
            changed = True

    for vrf_name in sorted(set(current_vrfs) - set(desired_vrfs)):
        lines.extend([f"no ip vrf {vrf_name}", "!"])
        changed = True

    for vrf_name in sorted(desired_vrfs):
        desired_vrf = desired_vrfs[vrf_name]
        current_vrf = current_vrfs.get(vrf_name)
        if current_vrf != desired_vrf:
            if current_vrf is not None:
                lines.extend([f"no ip vrf {vrf_name}", "!"])
            lines.extend(
                [
                    f"ip vrf {vrf_name}",
                    f" rd {desired_vrf['rd']}",
                ]
            )
            for route_target in desired_vrf["rt_import"]:
                lines.append(f" route-target import {route_target}")
            for route_target in desired_vrf["rt_export"]:
                lines.append(f" route-target export {route_target}")
            lines.append("!")
            changed = True

    for interface_name in sorted(desired_interfaces):
        desired_interface = desired_interfaces[interface_name]
        current_interface = current_interfaces.get(interface_name)
        if not _interface_matches(current_interface, desired_interface):
            lines.extend(
                [
                    f"interface {interface_name}",
                    f" description {desired_interface['description']}",
                    f" ip vrf forwarding {desired_interface['vrf']}",
                    f" ip address {desired_interface['ip']} {desired_interface['mask']}",
                    " no shutdown",
                    "!",
                ]
            )
            changed = True

    bgp_changed = False
    desired_vrf_names = set(desired["bgp_vrfs"])
    current_vrf_af_names = {
        af_name.split()[-1]
        for af_name in current_afs
        if af_name.startswith("ipv4 vrf ")
    }
    bgp_lines = [f"router bgp {desired['bgp_as']}"]

    for vrf_name in sorted(current_vrf_af_names - desired_vrf_names):
        af_name = f"ipv4 vrf {vrf_name}"
        af_state = current_afs.get(af_name, {"neighbors": {}, "networks": set()})
        bgp_lines.append(f" address-family {af_name}")
        for neighbor_ip in sorted(af_state["neighbors"]):
            bgp_lines.append(f"  no neighbor {neighbor_ip}")
        for network, mask in sorted(af_state["networks"]):
            bgp_lines.append(f"  no network {network} mask {mask}")
        bgp_lines.append(" exit-address-family")
        bgp_changed = True

    for vrf_name in sorted(desired["bgp_vrfs"]):
        af_name = f"ipv4 vrf {vrf_name}"
        desired_af = desired["bgp_vrfs"][vrf_name]
        current_af = current_afs.get(af_name, {"neighbors": {}, "networks": set()})
        desired_neighbors = desired_af["neighbors"]
        current_neighbors = current_af.get("neighbors", {})
        desired_networks = {(entry["network"], entry["mask"]) for entry in desired_af["networks"]}
        current_networks = set(current_af.get("networks", set()))

        af_lines = [f" address-family {af_name}"]
        af_changed = False

        for neighbor_ip in sorted(set(current_neighbors) - set(desired_neighbors)):
            af_lines.append(f"  no neighbor {neighbor_ip}")
            af_changed = True

        for network, mask in sorted(current_networks - desired_networks):
            af_lines.append(f"  no network {network} mask {mask}")
            af_changed = True

        for neighbor_ip in sorted(desired_neighbors):
            desired_neighbor = desired_neighbors[neighbor_ip]
            current_neighbor = current_neighbors.get(neighbor_ip)
            if current_neighbor != {
                "remote_as": desired_neighbor["remote_as"],
                "activate": True,
                "send_community_both": desired_neighbor["send_community_both"],
            }:
                af_lines.extend(
                    [
                        f"  neighbor {neighbor_ip} remote-as {desired_neighbor['remote_as']}",
                        f"  neighbor {neighbor_ip} activate",
                    ]
                )
                if desired_neighbor["send_community_both"]:
                    af_lines.append(f"  neighbor {neighbor_ip} send-community both")
                af_changed = True

        for network, mask in sorted(desired_networks - current_networks):
            af_lines.append(f"  network {network} mask {mask}")
            af_changed = True

        af_lines.append(" exit-address-family")
        if af_changed:
            bgp_lines.extend(af_lines)
            bgp_changed = True

    if bgp_changed:
        bgp_lines.append("!")
        lines.extend(bgp_lines)
    return changed or bgp_changed


def _reconcile_ce(lines: list[str], desired: dict, current: dict) -> bool:
    changed = False
    current_interfaces = current.get("interfaces", {})
    desired_interfaces = desired["interfaces"]
    bgp_state = current.get("bgp") or {"address_families": {}, "neighbors": {}, "router_id": None}
    current_af = bgp_state.get("address_families", {}).get("ipv4", {"neighbors": {}, "networks": set()})
    desired_bgp = desired["bgp_ipv4"]

    for interface_name in sorted(set(current_interfaces) - set(desired_interfaces)):
        if interface_name == "Loopback0" or interface_name.startswith("GigabitEthernet"):
            lines.extend([f"default interface {interface_name}", "!"])
            changed = True

    for interface_name in sorted(desired_interfaces):
        desired_interface = desired_interfaces[interface_name]
        current_interface = current_interfaces.get(interface_name)
        if _interface_needs_reset(current_interface, desired_interface):
            lines.extend([f"default interface {interface_name}", "!"])
            changed = True
        if not _interface_matches(current_interface, desired_interface):
            lines.extend(
                [
                    f"interface {interface_name}",
                    f" description {desired_interface['description']}",
                    f" ip address {desired_interface['ip']} {desired_interface['mask']}",
                    " no shutdown",
                    "!",
                ]
            )
            changed = True

    desired_neighbor = desired_bgp["neighbor"]
    desired_networks = {(entry["network"], entry["mask"]) for entry in desired_bgp["networks"]}
    current_neighbor_map = current_af.get("neighbors", {})
    current_global_neighbors = bgp_state.get("neighbors", {})
    current_networks = set(current_af.get("networks", set()))
    current_bgp_as = bgp_state.get("as")
    recreate_bgp_process = current_bgp_as not in {None, desired["bgp_as"]}

    bgp_lines = [f"router bgp {desired['bgp_as']}"]
    bgp_changed = False

    if recreate_bgp_process:
        lines.extend([f"no router bgp {current_bgp_as}", "!"])
        bgp_lines = [f"router bgp {desired['bgp_as']}"]
        bgp_changed = True
        current_neighbor_map = {}
        current_global_neighbors = {}
        current_networks = set()
        bgp_state = {"router_id": None}

    if desired_bgp["router_id"] and bgp_state.get("router_id") != desired_bgp["router_id"]:
        bgp_lines.append(f" bgp router-id {desired_bgp['router_id']}")
        bgp_changed = True

    current_neighbor_ips = set(current_global_neighbors) | set(current_neighbor_map)
    desired_neighbor_ip = desired_neighbor["ip"] if desired_neighbor else None

    for neighbor_ip in sorted(current_neighbor_ips):
        if neighbor_ip != desired_neighbor_ip:
            bgp_lines.append(f" no neighbor {neighbor_ip}")
            bgp_changed = True

    if desired_neighbor is not None:
        expected_global = {"remote_as": desired_neighbor["remote_as"]}
        if current_global_neighbors.get(desired_neighbor["ip"]) != expected_global:
            bgp_lines.append(f" neighbor {desired_neighbor['ip']} remote-as {desired_neighbor['remote_as']}")
            bgp_changed = True

    af_lines = [" address-family ipv4"]
    af_changed = False

    for neighbor_ip in sorted(set(current_neighbor_map) - ({desired_neighbor_ip} if desired_neighbor_ip else set())):
        af_lines.append(f"  no neighbor {neighbor_ip}")
        af_changed = True

    for network, mask in sorted(current_networks - desired_networks):
        af_lines.append(f"  no network {network} mask {mask}")
        af_changed = True

    if desired_neighbor is not None:
        expected_af_neighbor = {
            "remote_as": None,
            "activate": True,
            "send_community_both": False,
        }
        if current_neighbor_map.get(desired_neighbor["ip"]) != expected_af_neighbor:
            af_lines.append(f"  neighbor {desired_neighbor['ip']} activate")
            af_changed = True

    for network, mask in sorted(desired_networks - current_networks):
        af_lines.append(f"  network {network} mask {mask}")
        af_changed = True

    af_lines.append(" exit-address-family")
    if af_changed:
        bgp_lines.extend(af_lines)
        bgp_changed = True

    if bgp_changed:
        bgp_lines.append("!")
        lines.extend(bgp_lines)
    return changed or bgp_changed


def build_reconcile_commands(running_configs: dict[str, str]) -> dict[str, list[str]]:
    desired_state = load_phase4a_desired_state()
    commands: dict[str, list[str]] = {}
    for router_name, running_config in running_configs.items():
        if router_name not in desired_state:
            continue
        desired_router = desired_state[router_name]
        current_router = parse_running_config(running_config)
        router_commands = render_reconcile_commands(router_name, desired_router, current_router)
        if router_commands:
            commands[router_name] = router_commands
    return commands
