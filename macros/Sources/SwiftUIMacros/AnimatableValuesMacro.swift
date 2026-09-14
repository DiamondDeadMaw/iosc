import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// @Animatable (SwiftUICore)
// Roles: extension, member
// STUB. Replace each body with the real expansion, verified against the
// post expansion API in the SDK .swiftinterface for SwiftUICore.
public struct AnimatableValuesMacro: ExtensionMacro, MemberMacro {
    public static func expansion(
        of node: AttributeSyntax,
        attachedTo declaration: some DeclGroupSyntax,
        providingExtensionsOf type: some TypeSyntaxProtocol,
        conformingTo protocols: [TypeSyntax],
        in context: some MacroExpansionContext
    ) throws -> [ExtensionDeclSyntax] {
        let ext: ExtensionDeclSyntax = try ExtensionDeclSyntax("extension \(type.trimmed): Animatable {}")
        return [ext]
    }

    private struct StoredProp {
        let name: String
        let type: String
    }

    public static func expansion(
        of node: AttributeSyntax,
        providingMembersOf declaration: some DeclGroupSyntax,
        conformingTo protocols: [TypeSyntax],
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        var animatableProperties: [StoredProp] = []

        for member in declaration.memberBlock.members {
            guard let varDecl = member.decl.as(VariableDeclSyntax.self) else {
                continue
            }

            // Skip static or class variables
            let isStatic = varDecl.modifiers.contains { modifier in
                modifier.name.text == "static" || modifier.name.text == "class"
            }
            if isStatic {
                continue
            }

            // Skip if marked @AnimatableIgnored
            let isIgnored = varDecl.attributes.contains { attr in
                if case .attribute(let attribute) = attr {
                    let attrName = attribute.attributeName.trimmedDescription
                    return attrName == "AnimatableIgnored" || attrName == "SwiftUICore.AnimatableIgnored"
                }
                return false
            }
            if isIgnored {
                continue
            }

            // For each binding in the variable declaration
            for binding in varDecl.bindings {
                // Must have a type annotation
                guard let typeAnnotation = binding.typeAnnotation?.type.trimmedDescription else {
                    continue
                }

                // Check accessors: if computed (has get/set block) without willSet/didSet, skip
                if let accessorBlock = binding.accessorBlock {
                    switch accessorBlock.accessors {
                    case .accessors(let accessors):
                        let hasGet = accessors.contains { acc in
                            acc.accessorSpecifier.text == "get" || acc.accessorSpecifier.text == "_read"
                        }
                        if hasGet {
                            continue
                        }
                    case .getter:
                        // Computed property with single expression
                        continue
                    }
                }

                // Get property name
                if let pattern = binding.pattern.as(IdentifierPatternSyntax.self) {
                    let name = pattern.identifier.text
                    animatableProperties.append(StoredProp(name: name, type: typeAnnotation))
                }
            }
        }

        if animatableProperties.isEmpty {
            let decl: DeclSyntax = """
            public var animatableData: EmptyAnimatableData {
                get {
                    EmptyAnimatableData()
                }
                set {
                }
            }
            """
            return [decl]
        }

        if animatableProperties.count == 1 {
            let prop = animatableProperties[0]
            let decl: DeclSyntax = """
            public var animatableData: \(raw: prop.type) {
                get {
                    \(raw: prop.name)
                }
                set {
                    \(raw: prop.name) = newValue
                }
            }
            """
            return [decl]
        }

        // Build AnimatablePair type recursively
        // For [p0, p1, p2], type is AnimatablePair<T0, AnimatablePair<T1, T2>>
        func makePairType(for props: [StoredProp]) -> String {
            if props.count == 1 {
                return props[0].type
            }
            let first = props[0].type
            let rest = makePairType(for: Array(props.dropFirst()))
            return "AnimatablePair<\(first), \(rest)>"
        }

        // Build getter expression:
        // For 2 props: AnimatablePair(p0, p1)
        // For 3 props: AnimatablePair(p0, AnimatablePair(p1, p2))
        func makeGetter(for props: [StoredProp]) -> String {
            if props.count == 1 {
                return props[0].name
            }
            let first = props[0].name
            let rest = makeGetter(for: Array(props.dropFirst()))
            return "AnimatablePair(\(first), \(rest))"
        }

        // Build setter statements:
        // newValue.first, newValue.second...
        func makeSetter(for props: [StoredProp], currentAccess: String) -> [String] {
            if props.count == 1 {
                return ["\(props[0].name) = \(currentAccess)"]
            }
            if props.count == 2 {
                return [
                    "\(props[0].name) = \(currentAccess).first",
                    "\(props[1].name) = \(currentAccess).second"
                ]
            }
            var lines = ["\(props[0].name) = \(currentAccess).first"]
            lines.append(contentsOf: makeSetter(for: Array(props.dropFirst()), currentAccess: "\(currentAccess).second"))
            return lines
        }

        let pairType = makePairType(for: animatableProperties)
        let getterExpr = makeGetter(for: animatableProperties)
        let setterLines = makeSetter(for: animatableProperties, currentAccess: "newValue").joined(separator: "\n        ")

        let decl: DeclSyntax = """
        public var animatableData: \(raw: pairType) {
            get {
                \(raw: getterExpr)
            }
            set {
                \(raw: setterLines)
            }
        }
        """
        return [decl]
    }
}
