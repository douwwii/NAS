from config_common import GENERATED_DIR, get_loopbacks, load_intent, pe_routers, router_hostname_lines, write_router_configs


def build_phase2_configs(intent: dict) -> dict[str, list[str]]:
    provider_as = intent["bgp"]["provider_as"]
    loopbacks = get_loopbacks(intent)
    pes = pe_routers(intent)
    configs: dict[str, list[str]] = {}

    for router_name in pes:
        lines = router_hostname_lines(router_name)
        lines.extend(
            [
                f"router bgp {provider_as}",
                f" bgp router-id {intent['routeurs'][router_name]['routeurID']}",
                " no bgp default ipv4-unicast",
            ]
        )
        for peer_name in pes:
            if peer_name == router_name:
                continue
            lines.extend(
                [
                    f" neighbor {loopbacks[peer_name]['ip']} remote-as {provider_as}",
                    f" neighbor {loopbacks[peer_name]['ip']} update-source Loopback0",
                ]
            )
        lines.extend(
            [
                " address-family vpnv4",
            ]
        )
        for peer_name in pes:
            if peer_name == router_name:
                continue
            lines.extend(
                [
                    f"  neighbor {loopbacks[peer_name]['ip']} activate",
                    f"  neighbor {loopbacks[peer_name]['ip']} send-community both",
                ]
            )
        lines.extend([" exit-address-family", "!", "end", "write memory"])
        configs[router_name] = lines

    return configs


def main() -> None:
    intent = load_intent()
    configs = build_phase2_configs(intent)
    write_router_configs(configs, GENERATED_DIR, "phase2_vpnv4")
    print(f"Generated {len(configs)} Phase 2 config files in {GENERATED_DIR}")


if __name__ == "__main__":
    main()
