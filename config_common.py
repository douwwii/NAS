import ipaddress
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INTENT_PATH = ROOT / "intent.json"
GENERATED_DIR = ROOT / "generated_configs"
VISUALIZATION_INTENT_PATH = ROOT / "intent_visualization.json"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_intent() -> dict:
    return load_json(INTENT_PATH)


def sorted_links(intent: dict, link_type: str) -> list[dict]:
    return sorted(
        (link for link in intent["links"] if link["type"] == link_type),
        key=lambda link: link["link_id"],
    )


def allocate_subnets(pool: str, new_prefix: int, count: int) -> list[ipaddress.IPv4Network]:
    network = ipaddress.ip_network(pool)
    subnets = list(network.subnets(new_prefix=new_prefix))
    if len(subnets) < count:
        raise ValueError(
            f"Pool {pool} is too small: need {count} /{new_prefix} subnets, got {len(subnets)}"
        )
    return subnets[:count]


def host_ip(network: ipaddress.IPv4Network, host_index: int) -> str:
    return str(network.network_address + host_index)


def render_mask(network: ipaddress.IPv4Network) -> str:
    return str(network.netmask)


def wildcard_mask(network: ipaddress.IPv4Network) -> str:
    return str(ipaddress.IPv4Address(int(network.hostmask)))


def loopback_ip(pool: str, index: int) -> tuple[str, str, str]:
    network = ipaddress.ip_network(pool)
    subnets = list(network.subnets(new_prefix=30))
    if index <= 0 or index > len(subnets):
        raise ValueError(f"Loopback index {index} is out of range for pool {pool}")
    subnet = subnets[index - 1]
    return host_ip(subnet, 1), render_mask(subnet), str(subnet)


def get_loopbacks(intent: dict) -> dict[str, dict]:
    pool = intent["address_pools"]["loopbacks_v4"]
    loopbacks: dict[str, dict] = {}
    for router_name, router_data in intent["routeurs"].items():
        if "loopback_index" not in router_data:
            continue
        address, mask, subnet = loopback_ip(pool, router_data["loopback_index"])
        loopbacks[router_name] = {
            "interface": "Loopback0",
            "ip": address,
            "mask": mask,
            "network": subnet,
        }
    return loopbacks


def get_ce_loopbacks(intent: dict) -> dict[str, dict]:
    pool = intent["address_pools"]["customer_loopbacks_v4"]
    loopbacks: dict[str, dict] = {}
    for router_name, router_data in intent["routeurs"].items():
        if "ce_loopback_index" not in router_data:
            continue
        address, mask, subnet = loopback_ip(pool, router_data["ce_loopback_index"])
        loopbacks[router_name] = {
            "interface": "Loopback0",
            "ip": address,
            "mask": mask,
            "network": subnet,
        }
    return loopbacks


def get_core_allocations(intent: dict) -> list[dict]:
    links = sorted_links(intent, "core")
    subnets = allocate_subnets(intent["address_pools"]["core_links_v4"], 30, len(links))
    allocations = []
    for link, subnet in zip(links, subnets, strict=True):
        allocations.append(
            {
                "link": link,
                "subnet": subnet,
                "mask": render_mask(subnet),
                "a_ip": host_ip(subnet, 1),
                "b_ip": host_ip(subnet, 2),
            }
        )
    return allocations


def get_pe_ce_allocations(intent: dict) -> list[dict]:
    links = sorted_links(intent, "pe-ce")
    subnets = allocate_subnets(intent["address_pools"]["pe_ce_links_v4"], 30, len(links))
    allocations = []
    for link, subnet in zip(links, subnets, strict=True):
        allocations.append(
            {
                "link": link,
                "subnet": subnet,
                "mask": render_mask(subnet),
                "pe_ip": host_ip(subnet, 1),
                "ce_ip": host_ip(subnet, 2),
            }
        )
    return allocations


def get_customer_lans(intent: dict) -> dict[str, dict]:
    lans: dict[str, dict] = {}
    for customer_name, sites in intent["customer_sites"].items():
        for site in sites:
            network = ipaddress.ip_network(site["lan_prefix"])
            ce_name = site["ce"]
            lans[ce_name] = {
                "customer": customer_name,
                "interface": site.get("lan_interface", "GigabitEthernet0/1"),
                "network": network,
                "ip": host_ip(network, 1),
                "mask": render_mask(network),
            }
    return lans


def router_hostname_lines(router_name: str) -> list[str]:
    return [
        "configure terminal",
        f"hostname {router_name}",
        "!",
    ]


def provider_routers(intent: dict) -> list[str]:
    return [
        router_name
        for router_name, router_data in intent["routeurs"].items()
        if router_data["role"] in {"PE", "P"}
    ]


def pe_routers(intent: dict) -> list[str]:
    return [
        router_name
        for router_name, router_data in intent["routeurs"].items()
        if router_data["role"] == "PE"
    ]


def ce_routers(intent: dict) -> list[str]:
    return [
        router_name
        for router_name, router_data in intent["routeurs"].items()
        if router_data["role"] == "CE"
    ]


def add_interface_block(
    lines: list[str],
    interface: str,
    description: str,
    ip: str,
    mask: str,
    extra_lines: list[str] | None = None,
) -> None:
    lines.extend(
        [
            f"interface {interface}",
            f" description {description}",
        ]
    )
    if extra_lines:
        lines.extend(extra_lines)
    lines.extend(
        [
            f" ip address {ip} {mask}",
            " no shutdown",
            "!",
        ]
    )


def write_router_configs(configs: dict[str, list[str]], output_dir: Path, suffix: str) -> None:
    output_dir.mkdir(exist_ok=True)
    for router_name, lines in sorted(configs.items()):
        path = output_dir / f"{router_name}_{suffix}.cfg"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_visualization_intent(intent: dict) -> dict:
    provider_loopbacks = get_loopbacks(intent)
    visualization = {
        "provider": intent["provider"],
        "address_pools": intent["address_pools"],
        "routers": {},
        "links": [],
    }

    for router_name, router_data in intent["routeurs"].items():
        visualization["routers"][router_name] = {
            "role": router_data["role"],
            "addresses": [],
        }
        if "customer" in router_data:
            visualization["routers"][router_name]["customer"] = router_data["customer"]
        if "ce_as" in router_data:
            visualization["routers"][router_name]["ce_as"] = router_data["ce_as"]
        if router_name in provider_loopbacks:
            visualization["routers"][router_name]["routeurID"] = provider_loopbacks[router_name]["ip"]
        elif "routeurID" in router_data:
            visualization["routers"][router_name]["routeurID"] = router_data["routeurID"]

    for router_name, loopback in provider_loopbacks.items():
        visualization["routers"][router_name]["addresses"].append(
            {
                "interface": loopback["interface"],
                "type": "loopback",
                "ip": loopback["ip"],
                "mask": loopback["mask"],
                "network": loopback["network"],
            }
        )

    for router_name, loopback in get_ce_loopbacks(intent).items():
        visualization["routers"][router_name]["addresses"].append(
            {
                "interface": loopback["interface"],
                "type": "loopback",
                "ip": loopback["ip"],
                "mask": loopback["mask"],
                "network": loopback["network"],
            }
        )

    for allocation in get_core_allocations(intent):
        link = allocation["link"]
        subnet = str(allocation["subnet"])
        visualization["links"].append(
            {
                "type": "core",
                "link_id": link["link_id"],
                "network": subnet,
                "endpoints": [
                    {
                        "router": link["routeur_a"],
                        "interface": link["interface_a"],
                        "ip": allocation["a_ip"],
                        "mask": allocation["mask"],
                    },
                    {
                        "router": link["routeur_b"],
                        "interface": link["interface_b"],
                        "ip": allocation["b_ip"],
                        "mask": allocation["mask"],
                    },
                ],
            }
        )
        visualization["routers"][link["routeur_a"]]["addresses"].append(
            {
                "interface": link["interface_a"],
                "type": "core",
                "peer": link["routeur_b"],
                "ip": allocation["a_ip"],
                "mask": allocation["mask"],
                "network": subnet,
            }
        )
        visualization["routers"][link["routeur_b"]]["addresses"].append(
            {
                "interface": link["interface_b"],
                "type": "core",
                "peer": link["routeur_a"],
                "ip": allocation["b_ip"],
                "mask": allocation["mask"],
                "network": subnet,
            }
        )

    for allocation in get_pe_ce_allocations(intent):
        link = allocation["link"]
        subnet = str(allocation["subnet"])
        visualization["links"].append(
            {
                "type": "pe-ce",
                "link_id": link["link_id"],
                "vrf": link["vrf"],
                "network": subnet,
                "endpoints": [
                    {
                        "router": link["routeur_a"],
                        "interface": link["interface_a"],
                        "ip": allocation["pe_ip"],
                        "mask": allocation["mask"],
                    },
                    {
                        "router": link["routeur_b"],
                        "interface": link["interface_b"],
                        "ip": allocation["ce_ip"],
                        "mask": allocation["mask"],
                    },
                ],
            }
        )
        visualization["routers"][link["routeur_a"]]["addresses"].append(
            {
                "interface": link["interface_a"],
                "type": "pe-ce",
                "peer": link["routeur_b"],
                "vrf": link["vrf"],
                "ip": allocation["pe_ip"],
                "mask": allocation["mask"],
                "network": subnet,
            }
        )
        visualization["routers"][link["routeur_b"]]["addresses"].append(
            {
                "interface": link["interface_b"],
                "type": "pe-ce",
                "peer": link["routeur_a"],
                "vrf": link["vrf"],
                "ip": allocation["ce_ip"],
                "mask": allocation["mask"],
                "network": subnet,
            }
        )

    for ce_name, lan in get_customer_lans(intent).items():
        visualization["routers"][ce_name]["addresses"].append(
            {
                "interface": lan["interface"],
                "type": "customer-lan",
                "customer": lan["customer"],
                "ip": lan["ip"],
                "mask": lan["mask"],
                "network": str(lan["network"]),
            }
        )

    return visualization


def write_visualization_intent(intent: dict, path: Path = VISUALIZATION_INTENT_PATH) -> None:
    path.write_text(json.dumps(build_visualization_intent(intent), indent=2), encoding="utf-8")
