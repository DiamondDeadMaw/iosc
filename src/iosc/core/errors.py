class IoscError(Exception):
    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = message


class ConfigError(IoscError):
    pass


class MissingExternalAsset(IoscError):
    pass


class ManifestError(IoscError):
    pass


class SdkError(IoscError):
    pass


class ToolchainError(IoscError):
    pass


class ExternalToolError(IoscError):
    def __init__(
        self,
        message: str = "",
        argv: list[str] | None = None,
        exit_code: int = 0,
        stderr: str = "",
    ):
        super().__init__(message)
        self.argv = argv if argv is not None else []
        self.exit_code = exit_code
        self.stderr = stderr


class CompileError(IoscError):
    pass


class LinkError(IoscError):
    pass


class BuildError(IoscError):
    pass


class BundleError(IoscError):
    pass


class PackageError(IoscError):
    pass


class SigningError(IoscError):
    pass




class AuthError(IoscError):
    pass


class GrandSlamError(AuthError):
    def __init__(self, message: str = "", code: int = 0) -> None:
        super().__init__(message)
        self.code = code


class DevServicesError(AuthError):
    def __init__(self, message: str = "", code: int = 0) -> None:
        super().__init__(message)
        self.code = code


class CertificateLimitError(DevServicesError):
    def __init__(self, message: str = "", certificates: list | None = None, code: int = 0) -> None:
        super().__init__(message, code=code)
        self.certificates = list(certificates or [])


class AscError(AuthError):
    def __init__(self, message: str = "", status: int | None = None, errors: list | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.errors = errors or []


class AnisetteError(IoscError):
    pass


class DeviceError(IoscError):
    pass


class InstallError(IoscError):
    pass


class DebugError(IoscError):
    pass


class UsageError(IoscError):
    pass


class FormatError(IoscError):
    pass


class Asn1Error(FormatError):
    pass


class AesError(FormatError):
    pass


class MachOError(FormatError):
    pass


class PngError(FormatError):
    pass


class CarError(FormatError):
    pass


class XcassetsError(FormatError):
    pass


class UnsupportedVariant(XcassetsError):
    pass


class StringsError(FormatError):
    pass


class XibError(FormatError):
    pass




class PkiError(FormatError):
    pass


class PlistError(FormatError):
    pass


class MobileprovisionError(FormatError):
    pass


