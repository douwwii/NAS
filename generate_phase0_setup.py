from config_common import (
    GENERATED_DIR,
    add_interface_block,
    get_core_allocations,
    get_loopbacks,
    load_intent,
    provider_routers,
    router_hostname_lines,
    wildcard_mask,
    write_router_configs,
)


def build_phase0_configs(intent: dict) -> dict[str, list[str]]:
    process_id = intent["provider"]["ospf_process"]
    area = intent["provider"]["ospf_area"]
    loopbacks = get_loopbacks(intent)

    configs = {
        router_name: router_hostname_lines(router_name)
        for router_name in provider_routers(intent)
    }

    for router_name, loopback in loopbacks.items():
        if router_name not in configs:
            continue
        add_interface_block(
            configs[router_name],
            loopback["interface"],
            f"Router-ID {loopback['ip']}",
            loopback["ip"],
            loopback["mask"],
        )

    for allocation in get_core_allocations(intent):
        link = allocation["link"]
        add_interface_block(
            configs[link["routeur_a"]],
            link["interface_a"],
            f"CORE to {link['routeur_b']}",
            allocation["a_ip"],
            allocation["mask"],
        )
        add_interface_block(
            configs[link["routeur_b"]],
            link["interface_b"],
            f"CORE to {link['routeur_a']}",
            allocation["b_ip"],
            allocation["mask"],
        )

    for router_name in configs:
        configs[router_name].extend(
            [
                f"router ospf {process_id}",
                f" router-id {loopbacks[router_name]['ip']}",
                " passive-interface Loopback0",
                f" network {loopbacks[router_name]['ip']} 0.0.0.0 area {area}",
            ]
        )

    for allocation in get_core_allocations(intent):
        link = allocation["link"]
        if not link.get("ospf"):
            continue
        subnet = allocation["subnet"]
        network_line = f" network {subnet.network_address} {wildcard_mask(subnet)} area {area}"
        configs[link["routeur_a"]].extend(
            [
                f" no passive-interface {link['interface_a']}",
                network_line,
            ]
        )
        configs[link["routeur_b"]].extend(
            [
                f" no passive-interface {link['interface_b']}",
                network_line,
            ]
        )

    for router_name in configs:
        configs[router_name].extend(["!", "end", "write memory"])

    return configs


def main() -> None:
    intent = load_intent()
    configs = build_phase0_configs(intent)
    write_router_configs(configs, GENERATED_DIR, "phase0_setup")
    print(f"Generated {len(configs)} Phase 0 config files in {GENERATED_DIR}")


if __name__ == "__main__":
    main()
