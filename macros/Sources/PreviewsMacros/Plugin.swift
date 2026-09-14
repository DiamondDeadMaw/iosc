import SwiftCompilerPlugin
import SwiftSyntaxMacros

@main
struct PreviewsMacrosPlugin: CompilerPlugin {
    let providingMacros: [Macro.Type] = [
        SwiftUIView.self,
        SwiftUIViewGroup_1.self,
        KitViewMacro.self,
        PreviewCommonGroup.self,
        Common.self,
        Previewable.self,
    ]
}
