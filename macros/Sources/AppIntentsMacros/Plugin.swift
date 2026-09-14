import SwiftCompilerPlugin
import SwiftSyntaxMacros

@main
struct AppIntentsMacrosPlugin: CompilerPlugin {
    let providingMacros: [Macro.Type] = [
        EntityPropertyMacro.self,
    ]
}
