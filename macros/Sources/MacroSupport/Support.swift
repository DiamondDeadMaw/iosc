import SwiftSyntax
import SwiftSyntaxMacros
import SwiftDiagnostics

// Thrown by an unimplemented macro. The message names the macro, its plugin type,
// and its framework so a failed build points straight at the work item. A missing
// macro must fail the build loudly, never degrade into a silent miscompile.
public struct MacroNotImplemented: Error, CustomStringConvertible {
    public let pluginType: String
    public let displayName: String
    public let framework: String

    public init(pluginType: String, displayName: String, framework: String) {
        self.pluginType = pluginType
        self.displayName = displayName
        self.framework = framework
    }

    public var description: String {
        "iosc macro not implemented: "
            + displayName + " (" + pluginType + ", " + framework + "). "
            + "Implement it in product/macros/Sources or hand-write the expansion. "
            + "See product/macros/README.md."
    }
}

// Emits the not-implemented error as a real compiler diagnostic on the attribute,
// then throws so expansion aborts.
public func failNotImplemented(
    pluginType: String,
    displayName: String,
    framework: String,
    node: some SyntaxProtocol,
    context: some MacroExpansionContext
) throws -> Never {
    let err = MacroNotImplemented(pluginType: pluginType, displayName: displayName, framework: framework)
    let message = SimpleDiagnosticMessage(
        message: err.description,
        diagnosticID: MessageID(domain: "iosc.macros", id: pluginType),
        severity: .error
    )
    context.diagnose(Diagnostic(node: Syntax(node), message: message))
    throw err
}

// Reports an expansion the macro cannot perform correctly. Preferred over emitting
// a placeholder type, which surfaces later as a type mismatch inside expanded code
// the author never wrote.
public struct MacroExpansionRefused: Error, CustomStringConvertible {
    public let reason: String

    public init(reason: String) {
        self.reason = reason
    }

    public var description: String { reason }
}

public func failExpansion(
    id: String,
    reason: String,
    node: some SyntaxProtocol,
    context: some MacroExpansionContext
) throws -> Never {
    let message = SimpleDiagnosticMessage(
        message: reason,
        diagnosticID: MessageID(domain: "iosc.macros", id: id),
        severity: .error
    )
    context.diagnose(Diagnostic(node: Syntax(node), message: message))
    throw MacroExpansionRefused(reason: reason)
}

public struct SimpleDiagnosticMessage: DiagnosticMessage {
    public let message: String
    public let diagnosticID: MessageID
    public let severity: DiagnosticSeverity

    public init(message: String, diagnosticID: MessageID, severity: DiagnosticSeverity) {
        self.message = message
        self.diagnosticID = diagnosticID
        self.severity = severity
    }
}
