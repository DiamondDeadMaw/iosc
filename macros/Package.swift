// swift-tools-version: 6.0
import PackageDescription
import CompilerPluginSupport

// Reimplementations of Apple's closed-source Tier B macro plugins, built as native
// Windows compiler plugins. Two plugin modules match the names the SDK interfaces
// reference in #externalMacro(module:type:): SwiftUIMacros and SwiftDataMacros.
// Each is loaded into swiftc with -load-plugin-executable <tool.exe>#<Module>.
let pluginDeps: [Target.Dependency] = [
    "MacroSupport",
    .product(name: "SwiftSyntax", package: "swift-syntax"),
    .product(name: "SwiftSyntaxBuilder", package: "swift-syntax"),
    .product(name: "SwiftSyntaxMacros", package: "swift-syntax"),
    .product(name: "SwiftCompilerPlugin", package: "swift-syntax"),
]

let package = Package(
    name: "IoscMacros",
    platforms: [.macOS(.v13)],
    dependencies: [
        .package(url: "https://github.com/swiftlang/swift-syntax.git", branch: "release/6.4.x"),
    ],
    targets: [
        .target(
            name: "MacroSupport",
            dependencies: [
                .product(name: "SwiftSyntax", package: "swift-syntax"),
                .product(name: "SwiftSyntaxMacros", package: "swift-syntax"),
                .product(name: "SwiftDiagnostics", package: "swift-syntax"),
            ]
        ),
        .macro(
            name: "SwiftUIMacros",
            dependencies: [
                "MacroSupport",
                .product(name: "SwiftSyntax", package: "swift-syntax"),
                .product(name: "SwiftSyntaxBuilder", package: "swift-syntax"),
                .product(name: "SwiftSyntaxMacros", package: "swift-syntax"),
                .product(name: "SwiftCompilerPlugin", package: "swift-syntax"),
            ]
        ),
        .macro(
            name: "SwiftDataMacros",
            dependencies: [
                "MacroSupport",
                .product(name: "SwiftSyntax", package: "swift-syntax"),
                .product(name: "SwiftSyntaxBuilder", package: "swift-syntax"),
                .product(name: "SwiftSyntaxMacros", package: "swift-syntax"),
                .product(name: "SwiftCompilerPlugin", package: "swift-syntax"),
            ]
        ),
        .macro(name: "PreviewsMacros", dependencies: pluginDeps),
        .macro(name: "FoundationModelsMacros", dependencies: pluginDeps),
        .macro(name: "StateReportingMacros", dependencies: pluginDeps),
        .macro(name: "TipKitMacros", dependencies: pluginDeps),
        .macro(name: "AppIntentsMacros", dependencies: pluginDeps),
        // Empty consumers so a plain build has a product that pulls each plugin,
        // forcing every -tool executable to link. They use no macro, so they build
        // green while the macro bodies are still stubs.
        .executableTarget(name: "SwiftUISmoke", dependencies: ["SwiftUIMacros"]),
        .executableTarget(name: "SwiftDataSmoke", dependencies: ["SwiftDataMacros"]),
        .executableTarget(name: "PreviewsSmoke", dependencies: ["PreviewsMacros"]),
        .executableTarget(name: "FoundationModelsSmoke", dependencies: ["FoundationModelsMacros"]),
        .executableTarget(name: "StateReportingSmoke", dependencies: ["StateReportingMacros"]),
        .executableTarget(name: "TipKitSmoke", dependencies: ["TipKitMacros"]),
        .executableTarget(name: "AppIntentsSmoke", dependencies: ["AppIntentsMacros"]),
    ]
)
