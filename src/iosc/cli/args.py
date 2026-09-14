from pathlib import Path


def project_dir(args) -> Path:
    return Path(args.project) if args.project else Path.cwd()
