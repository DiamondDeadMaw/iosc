from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import re

from iosc.build import assets, bundle, dsym, package, packages
from iosc.build import compile as build_compile
from iosc.build import link as build_link
from iosc.build import interfaces
from iosc.build import localization
from iosc.build.graph import (
    BuildLayout,
    BuildReport,
    Stage,
    execute,
    fingerprint,
    layout_for,
)
from iosc.account import session
from iosc.account.developer_services import DeveloperSession
from iosc.codesign.identity import identity_from_bytes
from iosc.codesign.signer import sign_app_recursive
from iosc.config import manifest as manifest_module
from iosc.config import paths
from iosc.config.manifest import Dependency, Manifest, Requirement
from iosc.config.settings import Settings
from iosc.core import (
    BuildError,
    BundleError,
    PackageError,
    get_reporter,
    rmtree_force,
    write_atomic,
)
from iosc.formats.mobileprovision import Profile
from iosc.formats.pki import SigningIdentity
from iosc.toolchain import discovery, git
from iosc.toolchain.discovery import Toolchain

SIGN_STAGE = "sign"
SIGN_RESULT_NAME = "sign-result.json"
CODE_SIGNATURE_DIR = "_CodeSignature"
CODE_RESOURCES = "CodeResources"
EMBEDDED_PROFILE = "embedded.mobileprovision"


@dataclass(frozen=True)
class Project:
    manifest: Manifest
    layout: BuildLayout
    product: str


@dataclass(frozen=True)
class BuildResult:
    app: Path
    product: str
    report: BuildReport


@dataclass(frozen=True)
class SignSummary:
    identifier: str
    cdhash: str
    team_id: str | None
    profile: str | None
    resources: int
    adhoc: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class SignResult:
    app: Path
    report: BuildReport
    summary: SignSummary


def open_project(project_dir: Path | str) -> Project:
    manifest = manifest_module.load(Path(project_dir))
    product = bundle.product_name(manifest)
    return Project(manifest, layout_for(project_dir, product), product)


def toolchain_identity(toolchain: Toolchain, sdk_root: Path) -> str:
    return f"{toolchain.swift_version}|{toolchain.linker}|{sdk_root}"


def resolve_toolchain(settings: Settings | None = None) -> tuple[Toolchain, Path]:
    return discovery.detect(settings), paths.require(paths.sdk_root())


def plan_build(project: Project, toolchain: Toolchain, sdk_root: Path) -> list[Stage]:
    manifest, layout, product = project.manifest, project.layout, project.product
    partial = bundle.partial_plist_path(layout)
    executable = build_link.executable_path(layout, product)
    graph = packages.load_graph(manifest, layout, toolchain)
    return [
        *packages.package_stages(
            graph, layout, toolchain, sdk_root, manifest.deployment_target
        ),
        build_compile.compile_stage(
            manifest, layout, toolchain, sdk_root, product, graph
        ),
        build_link.link_stage(manifest, layout, toolchain, sdk_root, product, graph),
        dsym.dsym_stage(
            layout, executable, product, depends_on=(build_link.STAGE_NAME,)
        ),
        assets.assets_stage(manifest, layout, partial),
        localization.localization_stage(manifest, layout),
        interfaces.interfaces_stage(manifest, layout),
        bundle.bundle_stage(
            manifest,
            layout,
            executable,
            product,
            depends_on=(
                build_link.STAGE_NAME,
                assets.STAGE_NAME,
                localization.STAGE_NAME,
                interfaces.STAGE_NAME,
                *packages.resource_stage_names(graph),
            ),
            nested=packages.resource_bundles(graph, layout),
            swift_library_dirs=paths.swift_backdeploy_dirs(),
        ),
    ]


def build(
    project_dir: Path | str,
    force: bool = False,
    settings: Settings | None = None,
) -> BuildResult:
    project = open_project(project_dir)
    toolchain, sdk_root = resolve_toolchain(settings)
    stages = plan_build(project, toolchain, sdk_root)

    report = execute(
        stages, project.layout, toolchain_identity(toolchain, sdk_root), force=force
    )

    problems = bundle.verify(project.layout.app)
    if problems:
        raise BundleError(
            f"{project.layout.app} is not a usable bundle, " + "; ".join(problems)
        )
    return BuildResult(project.layout.app, project.product, report)


# sign rewrites the executable and writes the signature dir and profile
# those are stage products, not inputs
def signable_inputs(app: Path, executable: str) -> list[Path]:
    found: list[Path] = []
    for path in app.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(app)
        if relative.parts[0] in (CODE_SIGNATURE_DIR, EMBEDDED_PROFILE):
            continue
        if relative.as_posix() == executable:
            continue
        found.append(path)
    return sorted(found, key=lambda p: p.relative_to(app).as_posix())


def _profile_key(profile: Profile | bytes | Path | None) -> str | None:
    if profile is None:
        return None
    if isinstance(profile, Profile):
        return profile.uuid
    if isinstance(profile, bytes):
        return Profile.from_bytes(profile).uuid
    return Profile.from_file(profile).uuid


@dataclass(frozen=True)
class SigningMaterial:
    identity: SigningIdentity
    profile: Profile


# codesign wants a chained identity built from apple's raw cert bytes
# only this layer sees both ends. cli supplies the interaction callbacks
def signing_material(
    bundle_id: str,
    udid: str,
    apple_id: str,
    password_callback: Callable[[], str] | None = None,
    code_callback: Callable[[], str] | None = None,
    device_name: str | None = None,
    team_id: str | None = None,
    revoke_and_reissue: bool = False,
    anisette_provider: str = "local",
) -> SigningMaterial:
    state = str(paths.state_dir())
    account = session.login(
        apple_id,
        provider_name=anisette_provider,
        cache_dir=state,
        password_callback=password_callback,
        code_callback=code_callback,
    )
    developer = DeveloperSession(account, cache_dir=state)
    issued, provisioning = developer.obtain_identity_and_profile(
        bundle_id,
        udid,
        device_name=device_name,
        team_id=team_id,
        revoke_and_reissue=revoke_and_reissue,
    )
    return SigningMaterial(
        identity=identity_from_bytes(issued.certificate, issued.private_key),
        profile=provisioning.profile,
    )


def sign_result_path(layout: BuildLayout) -> Path:
    return layout.root / SIGN_RESULT_NAME


def read_sign_summary(layout: BuildLayout) -> SignSummary:
    path = sign_result_path(layout)
    if not path.is_file():
        raise BuildError(f"no signing result at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return SignSummary(
        identifier=data["identifier"],
        cdhash=data["cdhash"],
        team_id=data["team_id"],
        profile=data["profile"],
        resources=data["resources"],
        adhoc=data["adhoc"],
        warnings=tuple(data["warnings"]),
    )


def sign_stage(
    project: Project,
    identity: SigningIdentity | None,
    profile: Profile | bytes | Path | None,
    udid: str | None = None,
    depends_on: tuple[str, ...] = (),
) -> Stage:
    layout, product = project.layout, project.product
    app = layout.app
    result_path = sign_result_path(layout)

    print_ = fingerprint(
        {
            "certificate": identity.certificate.der.hex() if identity else None,
            "profile": _profile_key(profile),
            "udid": udid,
            "entitlements": sorted(project.manifest.entitlements),
        }
    )

    def run() -> None:
        signed = sign_app_recursive(
            app, identity=identity, profile=profile, udid=udid
        )
        payload = {
            "identifier": signed.identifier,
            "cdhash": signed.cdhash.hex(),
            "team_id": signed.team_id,
            "profile": signed.profile,
            "resources": signed.resources,
            "adhoc": signed.adhoc,
            "warnings": list(signed.warnings),
        }
        write_atomic(result_path, (json.dumps(payload, indent=2) + "\n").encode("utf-8"))
        reporter = get_reporter()
        for warning in signed.warnings:
            reporter.warn(warning)

    return Stage(
        name=SIGN_STAGE,
        run=run,
        fingerprint=print_,
        depends_on=depends_on,
        resolve_inputs=lambda: signable_inputs(app, product),
        outputs=(
            app / product,
            app / CODE_SIGNATURE_DIR / CODE_RESOURCES,
            result_path,
        ),
    )


def sign(
    project_dir: Path | str,
    identity: SigningIdentity | None = None,
    profile: Profile | bytes | Path | None = None,
    udid: str | None = None,
    force: bool = False,
    settings: Settings | None = None,
) -> SignResult:
    project = open_project(project_dir)
    toolchain, sdk_root = resolve_toolchain(settings)

    stages = plan_build(project, toolchain, sdk_root)
    stages.append(
        sign_stage(project, identity, profile, udid, depends_on=(bundle.STAGE_NAME,))
    )

    report = execute(
        stages, project.layout, toolchain_identity(toolchain, sdk_root), force=force
    )
    return SignResult(project.layout.app, report, read_sign_summary(project.layout))


def package_ipa(project_dir: Path | str) -> Path:
    project = open_project(project_dir)
    if not project.layout.app.is_dir():
        raise BuildError(f"no bundle at {project.layout.app}, run 'iosc build' first")
    return package.write_ipa(project.layout.app, project.layout.ipa)


@dataclass(frozen=True)
class DependencyReport:
    dependencies: tuple[packages.DependencyStatus, ...]
    # pinned packages pulled in by other packages
    indirect: dict[str, str]
    targets: tuple[packages.PackageTarget, ...]


def _dependency_report(project: Project, graph: packages.PackageGraph) -> DependencyReport:
    statuses = packages.dependency_statuses(project.manifest, project.layout)
    direct = {s.identity for s in statuses}
    pins = packages.pinned_versions(project.layout)
    return DependencyReport(
        dependencies=tuple(statuses),
        indirect={k: v for k, v in sorted(pins.items()) if k not in direct},
        targets=graph.targets,
    )


def show_dependencies(project_dir: Path | str, settings: Settings | None = None) -> DependencyReport:
    project = open_project(project_dir)
    graph = packages.load_graph(project.manifest, project.layout, discovery.detect(settings))
    return _dependency_report(project, graph)


def resolve_dependencies(project_dir: Path | str, settings: Settings | None = None) -> DependencyReport:
    project = open_project(project_dir)
    graph = packages.load_graph(
        project.manifest, project.layout, discovery.detect(settings), refresh=True
    )
    return _dependency_report(project, graph)


def update_dependencies(
    project_dir: Path | str, names: Sequence[str] = (), settings: Settings | None = None
) -> DependencyReport:
    project = open_project(project_dir)
    graph = packages.load_graph(
        project.manifest, project.layout, discovery.detect(settings), update=list(names)
    )
    return _dependency_report(project, graph)


RE_GIT_SOURCE = re.compile(r"^[\w.+-]+://|^[\w.-]+@[\w.-]+:")


def dependency_from_source(
    project_dir: Path | str,
    source: str,
    name: str | None = None,
    requirement: Requirement | None = None,
    products: Sequence[str] = (),
) -> Dependency:
    if RE_GIT_SOURCE.match(source):
        key = name or re.sub(r"\.git$", "", re.split(r"[/:]", source.rstrip("/"))[-1])
        if requirement is None:
            latest = git.latest_release(discovery.find_git(), source)
            if latest is None:
                raise PackageError(
                    f"{source} has no release tags like 1.2.3, pass --branch or --revision"
                )
            requirement = Requirement("from", latest)
        return Dependency(name=key, url=source, requirement=requirement, products=tuple(products))

    if requirement is not None:
        raise PackageError(f"{source} is a local path, which takes no version rule")
    location = Path(project_dir) / source
    if not (location / "Package.swift").is_file():
        raise PackageError(f"{source} is neither a git url nor a folder holding a Package.swift")
    key = name or location.resolve().name
    return Dependency(name=key, path=Path(source).as_posix(), products=tuple(products))


# edit reverts unless the package resolves and builds a graph
def add_dependency(
    project_dir: Path | str,
    source: str,
    name: str | None = None,
    requirement: Requirement | None = None,
    products: Sequence[str] = (),
    settings: Settings | None = None,
) -> tuple[Dependency, DependencyReport]:
    project = open_project(project_dir)
    dependency = dependency_from_source(project_dir, source, name, requirement, products)
    manifest_path = manifest_module.path_for(project_dir)
    original = manifest_path.read_bytes()
    edited = manifest_module.add_dependency(original.decode("utf-8"), dependency)
    lock = packages.project_lock(project.layout)
    lock_before = lock.read_bytes() if lock.is_file() else None

    write_atomic(manifest_path, edited.encode("utf-8"))
    try:
        report = resolve_dependencies(project_dir, settings)
    except BaseException:
        write_atomic(manifest_path, original)
        if lock_before is None:
            lock.unlink(missing_ok=True)
        else:
            write_atomic(lock, lock_before)
        raise
    return dependency, report


def clean(project_dir: Path | str) -> Path:
    project = open_project(project_dir)
    rmtree_force(project.layout.root)
    get_reporter().detail(f"removed {project.layout.root}")
    return project.layout.root
