import SwiftSyntax

// A property wrapper style macro that generates typed computed peers needs the
// wrapped value type. When the declaration has no type annotation the macro must
// recover it from the initializer expression, the same cases the Swift compiler
// itself can resolve without wider context: literals, constructor calls, explicit
// casts. Anything else returns nil and the caller asks for an explicit annotation.
public func inferredType(from expr: ExprSyntax) -> String? {
    let expr = expr.trimmed

    if expr.is(BooleanLiteralExprSyntax.self) {
        return "Swift.Bool"
    }
    if expr.is(IntegerLiteralExprSyntax.self) {
        return "Swift.Int"
    }
    if expr.is(FloatLiteralExprSyntax.self) {
        return "Swift.Double"
    }
    if expr.is(StringLiteralExprSyntax.self) || expr.is(SimpleStringLiteralExprSyntax.self) {
        return "Swift.String"
    }

    if let cast = expr.as(AsExprSyntax.self) {
        return cast.type.trimmedDescription
    }

    if let call = expr.as(FunctionCallExprSyntax.self) {
        return typeName(of: call.calledExpression)
    }

    // A dotted member on a type reference, e.g. an enum case or a static member
    // like Color.red, has the base type. Xcode's own inference makes the same bet.
    if let member = expr.as(MemberAccessExprSyntax.self), let base = member.base {
        return typeName(of: base)
    }

    // A bare type reference used as a value, e.g. an explicit metatype, is rare
    // here and deliberately not inferred.
    return nil
}

// The callee of a construction expression names the type being built when it is a
// plain type reference, a dotted nested type, a generic specialization, or an
// array or dictionary type literal.
private func typeName(of callee: ExprSyntax) -> String? {
    if let ref = callee.as(DeclReferenceExprSyntax.self) {
        let name = ref.baseName.text
        return startsUppercase(name) ? name : nil
    }
    if let member = callee.as(MemberAccessExprSyntax.self), let base = member.base {
        guard let baseName = typeName(of: base) else { return nil }
        return baseName + "." + member.declName.baseName.text
    }
    if let generic = callee.as(GenericSpecializationExprSyntax.self) {
        guard let base = typeName(of: generic.expression) else { return nil }
        return base + generic.genericArgumentClause.trimmedDescription
    }
    if let array = callee.as(ArrayExprSyntax.self) {
        return array.trimmedDescription
    }
    if let dict = callee.as(DictionaryExprSyntax.self) {
        return dict.trimmedDescription
    }
    return nil
}

private func startsUppercase(_ name: String) -> Bool {
    guard let first = name.first else { return false }
    return first.isUppercase || first == "_"
}
