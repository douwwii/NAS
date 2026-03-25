from pathlib import Path


CONFIG_PATTERNS = ("startup-config", "private-config")
ROUTER_PATTERNS = ("c7200",)


def name_contains(filename: str, patterns: tuple[str, ...]) -> bool:
    name = filename.lower()
    return any(pattern in name for pattern in patterns)


def clean_dynamips_configs(project_root: Path) -> None:
    dynamips_dir = project_root / "project-files" / "dynamips"
    if not dynamips_dir.exists():
        print(f"[WARN] Dossier introuvable, nettoyage ignoré: {dynamips_dir}")
        return

    for router_dir in dynamips_dir.iterdir():
        if not router_dir.is_dir():
            continue

        configs_dir = router_dir / "configs"
        if configs_dir.is_dir():
            for file_path in configs_dir.iterdir():
                if file_path.is_file() and name_contains(file_path.name, CONFIG_PATTERNS):
                    file_path.unlink(missing_ok=True)

        for file_path in router_dir.iterdir():
            if file_path.is_file() and name_contains(file_path.name, ROUTER_PATTERNS):
                file_path.unlink(missing_ok=True)


def main() -> None:
    clean_dynamips_configs(Path.cwd())
    print("GNS3 Dynamips runtime cleaned")


if __name__ == "__main__":
    main()
