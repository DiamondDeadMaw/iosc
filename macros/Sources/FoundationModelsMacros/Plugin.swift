import SwiftCompilerPlugin
import SwiftSyntaxMacros

@main
struct FoundationModelsMacrosPlugin: CompilerPlugin {
    let providingMacros: [Macro.Type] = [
        GenerableMacro.self,
        GuideMacro.self,
        SessionPropertyEntryMacro.self,
        SessionPropertyEntryDefaultValueMacro.self,
    ]
}
