import SwiftSyntax
import SwiftSyntaxMacros
import SwiftSyntaxBuilder
import MacroSupport

// Builds the PreviewRegistry conforming struct shared by every #Preview overload.
// DeveloperToolsSupport.PreviewRegistry requires fileID, line, column and makePreview().
// Apple's Preview initializers mirror the #Preview macro parameter lists one for one,
// so the expansion just forwards the original argument list and trailing closures into
// a call to Preview's initializer instead of reinterpreting each overload by hand.
func previewRegistryDecl(
    of node: some FreestandingMacroExpansionSyntax,
    in context: some MacroExpansionContext
) -> DeclSyntax {
    let location = context.location(of: node)
    let fileExpr = location?.file.trimmedDescription ?? "\"unknown\""
    let lineExpr = location?.line.trimmedDescription ?? "0"
    let columnExpr = location?.column.trimmedDescription ?? "0"
    let structName = context.makeUniqueName("Preview")

    let argumentsText = node.arguments.trimmedDescription

    var closuresText = ""
    if let trailing = node.trailingClosure {
        closuresText += " " + trailing.trimmedDescription
    }
    for extra in node.additionalTrailingClosures {
        closuresText += " " + extra.trimmedDescription
    }

    let callText = "DeveloperToolsSupport.Preview(\(argumentsText))\(closuresText)"

    return """
    struct \(structName): DeveloperToolsSupport.PreviewRegistry {
        static var fileID: Swift.String { \(raw: fileExpr) }
        static var line: Swift.Int { \(raw: lineExpr) }
        static var column: Swift.Int { \(raw: columnExpr) }
        @_Concurrency.MainActor static func makePreview() throws -> DeveloperToolsSupport.Preview {
            try \(raw: callText)
        }
    }
    """
}

// #Preview (Previews)
// Roles: declaration
// #Preview for a SwiftUI View, with an optional name and optional traits.
public struct SwiftUIView: DeclarationMacro {
    public static func expansion(
        of node: some FreestandingMacroExpansionSyntax,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        [previewRegistryDecl(of: node, in: context)]
    }
}
