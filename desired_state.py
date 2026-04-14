from __future__ import annotations

from config_common import (
    get_ce_loopbacks,
    get_customer_lans,
    get_pe_ce_allocations,
    load_intent,
)


def _sorted_networks(entries: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [
        {"network": network, "mask": mask}
        for network, mask in sorted(set(entries), key=lambda item: (item[0], item[1]))
    ]


def build_phase4a_desired_state(intent: dict) -> dict[str, dict]:
    customer_lans = get_customer_lans(intent)
    ce_loopbacks = get_ce_loopbacks(intent)
    pe_ce_allocations = get_pe_ce_allocations(intent)

    state: dict[str, dict] = {}

    for router_name, router_data in intent["routeurs"].items():
        role = router_data["role"]
        if role == "PE":
            state[router_name] = {
                "role": role,
                "bgp_as": intent["bgp"]["provider_as"],
                "vrfs": {},
                "interfaces": {},
                "bgp_vrfs": {},
            }
        elif role == "CE":
            state[router_name] = {
                "role": role,
                "bgp_as": router_data["ce_as"],
                "interfaces": {},
                "bgp_ipv4": {
                    "router_id": None,
                    "neighbor": None,
                    "networks": [],
                },
            }

    for router_name, router_state in state.items():
        if router_state["role"] != "PE":
            continue
        for vrf_name, vrf_data in intent["vrfs"].items():
            router_state["vrfs"][vrf_name] = {
                "rd": vrf_data["rd"],
                "rt_import": sorted(vrf_data["rt_import"]),
                "rt_export": sorted(vrf_data["rt_export"]),
            }
            router_state["bgp_vrfs"][vrf_name] = {
                "neighbors": {},
                "networks": [],
            }

    for allocation in pe_ce_allocations:
        link = allocation["link"]
        pe_name = link["routeur_a"]
        ce_name = link["routeur_b"]
        vrf_name = link["vrf"]

        state[pe_name]["interfaces"][link["interface_a"]] = {
            "description": f"{vrf_name} to {ce_name}",
            "vrf": vrf_name,
            "ip": allocation["pe_ip"],
            "mask": allocation["mask"],
        }
        state[pe_name]["bgp_vrfs"][vrf_name]["neighbors"][allocation["ce_ip"]] = {
            "remote_as": intent["routeurs"][ce_name]["ce_as"],
            "send_community_both": True,
        }
        state[pe_name]["bgp_vrfs"][vrf_name]["networks"].append(
            (str(allocation["subnet"].network_address), allocation["mask"])
        )

        state[ce_name]["interfaces"][link["interface_b"]] = {
            "description": f"WAN to {pe_name}",
            "ip": allocation["ce_ip"],
            "mask": allocation["mask"],
        }
        state[ce_name]["bgp_ipv4"]["neighbor"] = {
            "ip": allocation["pe_ip"],
            "remote_as": intent["bgp"]["provider_as"],
        }

    for router_name, lan_data in customer_lans.items():
        state[router_name]["interfaces"][lan_data["interface"]] = {
            "description": f"{lan_data['customer']} LAN",
            "ip": lan_data["ip"],
            "mask": lan_data["mask"],
        }
        state[router_name]["bgp_ipv4"]["networks"].append(
            (str(lan_data["network"].network_address), lan_data["mask"])
        )

    for router_name, loopback in ce_loopbacks.items():
        state[router_name]["interfaces"][loopback["interface"]] = {
            "description": "Customer Loopback",
            "ip": loopback["ip"],
            "mask": loopback["mask"],
        }
        state[router_name]["bgp_ipv4"]["router_id"] = loopback["ip"]
        state[router_name]["bgp_ipv4"]["networks"].append(
            (loopback["network"].split("/")[0], loopback["mask"])
        )

    for router_name, router_state in state.items():
        if router_state["role"] == "PE":
            for vrf_state in router_state["bgp_vrfs"].values():
                vrf_state["networks"] = _sorted_networks(vrf_state["networks"])
        elif router_state["role"] == "CE":
            router_state["bgp_ipv4"]["networks"] = _sorted_networks(router_state["bgp_ipv4"]["networks"])

    return state


def load_phase4a_desired_state() -> dict[str, dict]:
    return build_phase4a_desired_state(load_intent())
