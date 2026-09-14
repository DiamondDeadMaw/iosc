import SwiftCompilerPlugin
import SwiftSyntaxMacros

@main
struct StateReportingMacrosPlugin: CompilerPlugin {
    let providingMacros: [Macro.Type] = [
        ReportableMetadata.self,
        ReportableMetadataIgnored.self,
        ReportableMetadataKey.self,
    ]
}
