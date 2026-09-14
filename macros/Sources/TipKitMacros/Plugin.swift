import SwiftCompilerPlugin
import SwiftSyntaxMacros

@main
struct TipKitMacrosPlugin: CompilerPlugin {
    let providingMacros: [Macro.Type] = [
        ParameterMacro.self,
        RuleMacro.self,
    ]
}
