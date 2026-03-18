from config_common import GENERATED_DIR, get_core_allocations, load_intent, provider_routers, router_hostname_lines, write_router_configs


def build_phase1_configs(intent: dict) -> dict[str, list[str]]:
    configs = {
        router_name: router_hostname_lines(router_name)
        for router_name in provider_routers(intent)
    }

    label_protocol = intent["mpls"]["label_protocol"].lower()
    router_id_source = intent["mpls"]["ldp_router_id_source"]

    for router_name in configs:
        configs[router_name].extend(
            [
                "ip cef",
                f"mpls label protocol {label_protocol}",
                f"mpls ldp router-id {router_id_source} force",
                "!",
            ]
        )

    for allocation in get_core_allocations(intent):
        link = allocation["link"]
        if not link.get("mpls"):
            continue
        configs[link["routeur_a"]].extend(
            [
                f"interface {link['interface_a']}",
                " mpls ip",
                "!",
            ]
        )
        configs[link["routeur_b"]].extend(
            [
                f"interface {link['interface_b']}",
                " mpls ip",
                "!",
            ]
        )

    for router_name in configs:
        configs[router_name].extend(["end", "write memory"])

    return configs


def main() -> None:
    intent = load_intent()
    configs = build_phase1_configs(intent)
    write_router_configs(configs, GENERATED_DIR, "phase1_mpls")
    print(f"Generated {len(configs)} Phase 1 config files in {GENERATED_DIR}")


if __name__ == "__main__":
    main()
