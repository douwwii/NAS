import ipaddress

from config_common import (
    GENERATED_DIR,
    add_interface_block,
    ce_routers,
    get_ce_loopbacks,
    get_customer_lans,
    get_pe_ce_allocations,
    load_intent,
    pe_routers,
    router_hostname_lines,
    write_router_configs,
)


def get_customer_site_by_ce(intent: dict) -> dict[str, dict]:
    site_by_ce: dict[str, dict] = {}
    for customer_name, sites in intent["customer_sites"].items():
        for site in sites:
            site_by_ce[site["ce"]] = {
                "customer": customer_name,
                "lan_prefix": site["lan_prefix"],
            }
    return site_by_ce


def append_vrf_definition(lines: list[str], vrf_name: str, vrf_data: dict) -> None:
    lines.extend(
        [
            f"ip vrf {vrf_name}",
            f" rd {vrf_data['rd']}",
        ]
    )
    for route_target in vrf_data["rt_import"]:
        lines.append(f" route-target import {route_target}")
    for route_target in vrf_data["rt_export"]:
        lines.append(f" route-target export {route_target}")
    lines.append("!")


def build_phase3_configs(intent: dict) -> dict[str, list[str]]:
    provider_as = intent["bgp"]["provider_as"]
    customer_lans = get_customer_lans(intent)
    ce_loopbacks = get_ce_loopbacks(intent)
    customer_sites = get_customer_site_by_ce(intent)
    pe_ce_allocations = get_pe_ce_allocations(intent)

    configs: dict[str, list[str]] = {}

    for router_name in pe_routers(intent):
        lines = router_hostname_lines(router_name)
        for vrf_name, vrf_data in intent["vrfs"].items():
            append_vrf_definition(lines, vrf_name, vrf_data)
        for allocation in pe_ce_allocations:
            link = allocation["link"]
            if link["routeur_a"] != router_name:
                continue
            add_interface_block(
                lines,
                link["interface_a"],
                f"{link['vrf']} to {link['routeur_b']}",
                allocation["pe_ip"],
                allocation["mask"],
                extra_lines=[f" ip vrf forwarding {link['vrf']}"],
            )
        lines.extend(
            [
                f"router bgp {provider_as}",
            ]
        )
        for allocation in pe_ce_allocations:
            link = allocation["link"]
            if link["routeur_a"] != router_name:
                continue
            ce_name = link["routeur_b"]
            ce_data = intent["routeurs"][ce_name]
            lines.extend(
                [
                    f" address-family ipv4 vrf {link['vrf']}",
                    f"  neighbor {allocation['ce_ip']} remote-as {ce_data['ce_as']}",
                    f"  neighbor {allocation['ce_ip']} activate",
                    f"  neighbor {allocation['ce_ip']} send-community both",
                    f"  network {allocation['subnet'].network_address} mask {allocation['mask']}",
                    " exit-address-family",
                ]
            )
        lines.extend(["!", "end", "write memory"])
        configs[router_name] = lines

    for router_name in ce_routers(intent):
        router_data = intent["routeurs"][router_name]
        site = customer_sites[router_name]
        allocation = next(
            entry for entry in pe_ce_allocations if entry["link"]["routeur_b"] == router_name
        )
        lines = router_hostname_lines(router_name)
        add_interface_block(
            lines,
            allocation["link"]["interface_b"],
            f"WAN to {allocation['link']['routeur_a']}",
            allocation["ce_ip"],
            allocation["mask"],
        )
        add_interface_block(
            lines,
            customer_lans[router_name]["interface"],
            f"{site['customer']} LAN",
            customer_lans[router_name]["ip"],
            customer_lans[router_name]["mask"],
        )
        if router_name in ce_loopbacks:
            add_interface_block(
                lines,
                ce_loopbacks[router_name]["interface"],
                "Customer Loopback",
                ce_loopbacks[router_name]["ip"],
                ce_loopbacks[router_name]["mask"],
            )
        lines.extend(
            [
                f"router bgp {router_data['ce_as']}",
                f" bgp router-id {ce_loopbacks[router_name]['ip'] if router_name in ce_loopbacks else customer_lans[router_name]['ip']}",
                f" neighbor {allocation['pe_ip']} remote-as {provider_as}",
                " address-family ipv4",
                f"  neighbor {allocation['pe_ip']} activate",
                f"  network {customer_lans[router_name]['network'].network_address} mask {customer_lans[router_name]['mask']}",
                *(
                    [
                        f"  network {ipaddress.ip_network(ce_loopbacks[router_name]['network']).network_address} mask {ce_loopbacks[router_name]['mask']}"
                    ]
                    if router_name in ce_loopbacks
                    else []
                ),
                " exit-address-family",
                "!",
                "end",
                "write memory",
            ]
        )
        configs[router_name] = lines

    return configs


def main() -> None:
    intent = load_intent()
    configs = build_phase3_configs(intent)
    write_router_configs(configs, GENERATED_DIR, "phase3_clients")
    print(f"Generated {len(configs)} Phase 3 config files in {GENERATED_DIR}")


if __name__ == "__main__":
    main()
