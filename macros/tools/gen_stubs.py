"""Generate one stub file per Tier B macro plus the plugin registry for each module.

Each stub conforms to the macro protocols its role set requires and fails loudly at
expansion. A worker replaces the body of its assigned stubs. Re-running never clobbers
an edited stub (existing impl files are skipped); the Plugin.swift registry is always
rewritten so it stays in lockstep with this table.

Roles map to protocols:
  peer            -> PeerMacro
  accessor        -> AccessorMacro
  member          -> MemberMacro
  memberAttribute -> MemberAttributeMacro
  extension       -> ExtensionMacro
  declaration     -> DeclarationMacro   (freestanding)
  expression      -> ExpressionMacro    (freestanding)
"""

from pathlib import Path

# (plugin_type, display_name, [roles])
SWIFTUI = [
    ("EntryMacro", "@Entry", ["accessor", "peer"]),
    ("EntryDefaultValueMacro", "__EntryDefaultValue", ["accessor"]),
    ("SendableEntryDefaultMacro", "_EntryDefaultValue", ["declaration"]),
    ("UnsafeEntryDefaultMacro", "_EntryDefaultValue", ["declaration"]),
    ("EntryTypeCheckMacro", "_SwiftUIEntryTypeCheck", ["expression"]),
    ("EntryTypeCheckClassMacro", "_SwiftUIEntryTypeCheck", ["expression"]),
    ("ProjectedValueMacro", "_PropertyWrapperProjectedValue", ["accessor"]),
    ("AnimatableValuesMacro", "@Animatable", ["extension", "member"]),
    ("AnimatableIgnoredMacro", "@AnimatableIgnored", ["accessor"]),
    ("AnimatablePairDataPropertyMacro", "_SwiftUIAnimatableDataProperty", ["declaration"]),
    ("AnimatableValuesDataPropertyMacro", "_SwiftUIAnimatableDataProperty", ["declaration"]),
    ("AnimatableValuesDataMacro", "_AnimatableData", ["accessor"]),
    ("AnimatablePairDataMacro", "_AnimatablePairData", ["accessor"]),
    ("AnimatablePropertyMacro", "_SwiftUIAnimatableProperty", ["expression"]),
    ("InvalidAnimatablePropertyMacro", "_SwiftUIAnimatableProperty", ["expression"]),
    ("StateMacro", "@State", ["accessor", "peer"]),
    ("StatePropertyWrapperStorageMacro", "_StatePropertyWrapperStorage", ["accessor"]),
    ("StateInitialStoredValueMacro", "_StateInitialStoredValue", ["accessor"]),
    ("StateProjectedValueMacro", "_StateProjectedValue", ["accessor"]),
    ("StateTypeMacro", "_SwiftUIState", ["declaration"]),
]

SWIFTDATA = [
    ("PersistentModelMacro", "@Model", ["member", "memberAttribute", "extension"]),
    ("PersistentModelActorMacro", "@ModelActor", ["member", "extension"]),
    ("AttributePropertyMacro", "@Attribute", ["peer"]),
    ("RelationshipPropertyMacro", "@Relationship", ["peer"]),
    ("TransientPropertyMacro", "@Transient", ["peer"]),
    ("UniqueConstraintsMacro", "#Unique", ["declaration"]),
    ("IndexMacro", "#Index", ["declaration"]),
    ("PersistedPropertyMacro", "_PersistedProperty", ["accessor", "peer"]),
    ("TransformablePersistedPropertyMacro", "_TransformablePersistedProperty", ["accessor", "peer"]),
    ("QueryMacro", "@Query", ["accessor", "peer"]),
]

PREVIEWS = [
    ("SwiftUIView", "#Preview", ["declaration"]),
    ("SwiftUIViewGroup_1", "#Preview", ["declaration"]),
    ("KitViewMacro", "#Preview", ["declaration"]),
    ("PreviewCommonGroup", "#Preview", ["declaration"]),
    ("Common", "#Preview", ["declaration"]),
    ("Previewable", "@Previewable", ["peer"]),
]

FOUNDATIONMODELS = [
    ("GenerableMacro", "@Generable", ["extension", "member"]),
    ("GuideMacro", "@Guide", ["peer"]),
    ("SessionPropertyEntryMacro", "@SessionPropertyEntry", ["accessor", "peer"]),
    ("SessionPropertyEntryDefaultValueMacro", "@__SessionPropertyEntryDefaultValue", ["accessor"]),
]

STATEREPORTING = [
    ("ReportableMetadata", "@ReportableMetadata", ["member", "extension"]),
    ("ReportableMetadataIgnored", "@ReportableMetadataIgnored", ["peer"]),
    ("ReportableMetadataKey", "@ReportableMetadataKey", ["peer"]),
]

TIPKIT = [
    ("ParameterMacro", "@Parameter", ["accessor", "peer"]),
    ("RuleMacro", "#Rule", ["expression"]),
]

APPINTENTS = [
    ("EntityPropertyMacro", "@ComputedProperty/@DeferredProperty", ["peer", "accessor"]),
]

PROTOCOL = {
    "peer": "PeerMacro",
    "accessor": "AccessorMacro",
    "member": "MemberMacro",
    "memberAttribute": "MemberAttributeMacro",
    "extension": "ExtensionMacro",
    "declaration": "DeclarationMacro",
    "expression": "ExpressionMacro",
}


def fail_body(ptype, display, framework):
    return (
        '        try failNotImplemented(pluginType: "' + ptype + '", '
        'displayName: "' + display + '", framework: "' + framework + '", '
        "node: node, context: context)\n"
    )


def method(role, ptype, display, framework):
    b = fail_body(ptype, display, framework)
    if role == "peer":
        return (
            "    public static func expansion(\n"
            "        of node: AttributeSyntax,\n"
            "        providingPeersOf declaration: some DeclSyntaxProtocol,\n"
            "        in context: some MacroExpansionContext\n"
            "    ) throws -> [DeclSyntax] {\n" + b + "    }\n"
        )
    if role == "accessor":
        return (
            "    public static func expansion(\n"
            "        of node: AttributeSyntax,\n"
            "        providingAccessorsOf declaration: some DeclSyntaxProtocol,\n"
            "        in context: some MacroExpansionContext\n"
            "    ) throws -> [AccessorDeclSyntax] {\n" + b + "    }\n"
        )
    if role == "member":
        return (
            "    public static func expansion(\n"
            "        of node: AttributeSyntax,\n"
            "        providingMembersOf declaration: some DeclGroupSyntax,\n"
            "        conformingTo protocols: [TypeSyntax],\n"
            "        in context: some MacroExpansionContext\n"
            "    ) throws -> [DeclSyntax] {\n" + b + "    }\n"
        )
    if role == "memberAttribute":
        return (
            "    public static func expansion(\n"
            "        of node: AttributeSyntax,\n"
            "        attachedTo declaration: some DeclGroupSyntax,\n"
            "        providingAttributesFor member: some DeclSyntaxProtocol,\n"
            "        in context: some MacroExpansionContext\n"
            "    ) throws -> [AttributeSyntax] {\n" + b + "    }\n"
        )
    if role == "extension":
        return (
            "    public static func expansion(\n"
            "        of node: AttributeSyntax,\n"
            "        attachedTo declaration: some DeclGroupSyntax,\n"
            "        providingExtensionsOf type: some TypeSyntaxProtocol,\n"
            "        conformingTo protocols: [TypeSyntax],\n"
            "        in context: some MacroExpansionContext\n"
            "    ) throws -> [ExtensionDeclSyntax] {\n" + b + "    }\n"
        )
    if role == "declaration":
        return (
            "    public static func expansion(\n"
            "        of node: some FreestandingMacroExpansionSyntax,\n"
            "        in context: some MacroExpansionContext\n"
            "    ) throws -> [DeclSyntax] {\n" + b + "    }\n"
        )
    if role == "expression":
        return (
            "    public static func expansion(\n"
            "        of node: some FreestandingMacroExpansionSyntax,\n"
            "        in context: some MacroExpansionContext\n"
            "    ) throws -> ExprSyntax {\n" + b + "    }\n"
        )
    raise ValueError(role)


def stub_source(ptype, display, framework, roles):
    conf = ", ".join(PROTOCOL[r] for r in roles)
    header = (
        "import SwiftSyntax\n"
        "import SwiftSyntaxMacros\n"
        "import MacroSupport\n\n"
        "// " + display + " (" + framework + ")\n"
        "// Roles: " + ", ".join(roles) + "\n"
        "// STUB. Replace each body with the real expansion, verified against the\n"
        "// post expansion API in the SDK .swiftinterface for " + framework + ".\n"
        "public struct " + ptype + ": " + conf + " {\n"
    )
    methods = "\n".join(method(r, ptype, display, framework) for r in roles)
    return header + methods + "}\n"


def plugin_source(struct_name, macros):
    lines = ",\n".join("        " + m[0] + ".self" for m in macros)
    return (
        "import SwiftCompilerPlugin\n"
        "import SwiftSyntaxMacros\n\n"
        "@main\n"
        "struct " + struct_name + ": CompilerPlugin {\n"
        "    let providingMacros: [Macro.Type] = [\n" + lines + ",\n    ]\n}\n"
    )


def emit(module_dir, framework, macros, plugin_struct):
    module_dir.mkdir(parents=True, exist_ok=True)
    created, skipped = [], []
    for ptype, display, roles in macros:
        f = module_dir / (ptype + ".swift")
        if f.exists():
            skipped.append(f.name)
            continue
        f.write_text(stub_source(ptype, display, framework, roles), encoding="utf-8")
        created.append(f.name)
    (module_dir / "Plugin.swift").write_text(plugin_source(plugin_struct, macros), encoding="utf-8")
    print(module_dir.name + ": created " + str(len(created)) + ", skipped " + str(len(skipped)) + " (existing)")
    for n in skipped:
        print("  kept " + n)


def main():
    root = Path(__file__).resolve().parent.parent / "Sources"
    emit(root / "SwiftUIMacros", "SwiftUICore", SWIFTUI, "SwiftUIMacrosPlugin")
    emit(root / "SwiftDataMacros", "SwiftData", SWIFTDATA, "SwiftDataMacrosPlugin")
    emit(root / "PreviewsMacros", "Previews", PREVIEWS, "PreviewsMacrosPlugin")
    emit(root / "FoundationModelsMacros", "FoundationModels", FOUNDATIONMODELS, "FoundationModelsMacrosPlugin")
    emit(root / "StateReportingMacros", "StateReporting", STATEREPORTING, "StateReportingMacrosPlugin")
    emit(root / "TipKitMacros", "TipKit", TIPKIT, "TipKitMacrosPlugin")
    emit(root / "AppIntentsMacros", "AppIntents", APPINTENTS, "AppIntentsMacrosPlugin")
    total = sum(len(t) for t in (SWIFTUI, SWIFTDATA, PREVIEWS, FOUNDATIONMODELS, STATEREPORTING, TIPKIT, APPINTENTS))
    print("total plugin types: " + str(total))


if __name__ == "__main__":
    main()
