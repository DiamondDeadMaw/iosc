import SwiftCompilerPlugin
import SwiftSyntaxMacros

@main
struct SwiftUIMacrosPlugin: CompilerPlugin {
    let providingMacros: [Macro.Type] = [
        EntryMacro.self,
        EntryDefaultValueMacro.self,
        SendableEntryDefaultMacro.self,
        UnsafeEntryDefaultMacro.self,
        EntryTypeCheckMacro.self,
        EntryTypeCheckClassMacro.self,
        ProjectedValueMacro.self,
        AnimatableValuesMacro.self,
        AnimatableIgnoredMacro.self,
        AnimatablePairDataPropertyMacro.self,
        AnimatableValuesDataPropertyMacro.self,
        AnimatableValuesDataMacro.self,
        AnimatablePairDataMacro.self,
        AnimatablePropertyMacro.self,
        InvalidAnimatablePropertyMacro.self,
        StateMacro.self,
        StatePropertyWrapperStorageMacro.self,
        StateInitialStoredValueMacro.self,
        StateProjectedValueMacro.self,
        StateTypeMacro.self,
    ]
}
