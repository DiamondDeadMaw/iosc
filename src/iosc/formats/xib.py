"""Compile a modern XIB3 document to a NIBArchive nib.

Parses the .xib XML, builds the UIKit object graph, hands it to nib_build. Coverage
is narrow and fail loud. An unknown element or attribute raises XIBUnsupported.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET

from iosc.core.errors import XibError
from iosc.formats import nib_build as B
from iosc.formats.nib_build import BuildObject as O, Ref, rect, point, nsstring


class XIBUnsupported(XibError):
    pass


# attributes on many elements that carry no nib payload
_BENIGN_ATTRS = {"id", "userLabel", "customClass", "customModule",
                 "customModuleProvider", "translatesAutoresizingMaskIntoConstraints"}

_AUTORESIZE_FLAGS = {
    "flexibleMinX": 1, "widthSizable": 2, "flexibleMaxX": 4,
    "flexibleMinY": 8, "heightSizable": 16, "flexibleMaxY": 32,
}

_TEXT_ALIGNMENT = {"left": 0, "center": 1, "right": 2, "justified": 3, "natural": 4}

_LAYOUT_ATTR = {
    "left": 1, "right": 2, "top": 3, "bottom": 4, "leading": 5, "trailing": 6,
    "width": 7, "height": 8, "centerX": 9, "centerY": 10,
    "lastBaseline": 11, "baseline": 11, "firstBaseline": 12,
    "leftMargin": 13, "rightMargin": 14, "topMargin": 15, "bottomMargin": 16,
    "leadingMargin": 17, "trailingMargin": 18,
    "centerXWithinMargins": 19, "centerYWithinMargins": 20,
}

_LAYOUT_RELATION = {"lessThanOrEqual": -1, "greaterThanOrEqual": 1}

# UIControlEvents bitmask. corpus exercises touchUpInside and valueChanged
# the rest come from the uikit enum
_CONTROL_EVENTS = {
    "touchDown": 1 << 0, "touchDownRepeat": 1 << 1,
    "touchDragInside": 1 << 2, "touchDragOutside": 1 << 3,
    "touchDragEnter": 1 << 4, "touchDragExit": 1 << 5,
    "touchUpInside": 1 << 6, "touchUpOutside": 1 << 7, "touchCancel": 1 << 8,
    "valueChanged": 1 << 12, "primaryActionTriggered": 1 << 13,
    "menuActionTriggered": 1 << 14,
    "editingDidBegin": 1 << 16, "editingChanged": 1 << 17,
    "editingDidEnd": 1 << 18, "editingDidEndOnExit": 1 << 19,
}

_ACTION_ATTRS = {"id", "selector", "destination", "eventType"}

# colors a concrete view handler reads out of captured. anything else fails loud
_CAPTURED_COLORS = {"textColor"}


class Context:
    def __init__(self):
        self.builder = B.GraphBuilder()
        self.by_id: dict[str, O] = {}
        self.top_level: list[O] = []
        self.all_objects: list[O] = []
        self.connections: list[O] = []
        self.pending_outlets: list[tuple[O, str, str]] = []  # source, property, dest id
        self.pending_actions: list[tuple[O, str, str, int | None]] = []
        self.selectors: dict[str, O] = {}  # one NSString per distinct selector
        self.pending_constraints: list[tuple[O, ET.Element, O]] = []  # obj, el, owner

    def selector(self, name: str) -> O:
        obj = self.selectors.get(name)
        if obj is None:
            obj = nsstring(name)
            self.selectors[name] = obj
        return obj


def _require_no_extra_attrs(el: ET.Element, handled: set[str]):
    leftover = set(el.attrib) - handled - _BENIGN_ATTRS
    if leftover:
        raise XIBUnsupported(
            f"<{el.tag}> has unsupported attributes {sorted(leftover)}")


def _compact(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _color(el: ET.Element) -> O:
    handled = {"key"}
    space = el.get("colorSpace")
    custom = el.get("customColorSpace")
    if el.get("cocoaTouchSystemColor") or el.get("systemColor"):
        raise XIBUnsupported("system colors are not supported yet")
    if space == "custom":
        handled.add("customColorSpace")
        space = custom
    if space in ("calibratedRGB", "deviceRGB", "sRGB", "adobeRGB1998"):
        handled |= {"colorSpace", "red", "green", "blue", "alpha"}
        r = float(el.get("red")); g = float(el.get("green"))
        b = float(el.get("blue")); a = float(el.get("alpha"))
        _require_no_extra_attrs(el, handled)
        return _rgb_color(r, g, b, a)
    if space == "calibratedWhite":
        handled |= {"colorSpace", "white", "alpha"}
        w = float(el.get("white")); a = float(el.get("alpha"))
        _require_no_extra_attrs(el, handled)
        return _white_color(w, a)
    raise XIBUnsupported(f"unsupported color colorSpace={space!r}")


def _with_double(members: list, key: str, value: float):
    f32 = struct.unpack("<f", struct.pack("<f", value))[0]
    members.append((key, B.F32(value)))
    if float(f32) != value:
        members.append((key + "-Double", value))


def _rgb_color(r: float, g: float, b: float, a: float) -> O:
    members = [("UIColorComponentCount", 4)]
    _with_double(members, "UIRed", r)
    _with_double(members, "UIGreen", g)
    _with_double(members, "UIBlue", b)
    _with_double(members, "UIAlpha", a)
    parts = [_compact(r), _compact(g), _compact(b)]
    if a != 1:
        parts.append(_compact(a))
    members.append(("NSRGB", " ".join(parts).encode("utf-8")))
    members.append(("NSColorSpace", 2))
    return O("UIColor", members)


def _white_color(w: float, a: float) -> O:
    members = [("UIColorComponentCount", 2)]
    _with_double(members, "UIWhite", w)
    _with_double(members, "UIAlpha", a)
    text = f"{_compact(w)} {_compact(a)}" if a != 1 else _compact(w)
    members.append(("NSWhite", text.encode("utf-8")))
    members.append(("NSColorSpace", 4))
    return O("UIColor", members)


def _parse_frame(el: ET.Element) -> tuple[float, float, float, float]:
    _require_no_extra_attrs(el, {"key", "x", "y", "width", "height"})
    return (float(el.get("x", 0)), float(el.get("y", 0)),
            float(el.get("width", 0)), float(el.get("height", 0)))


# apply UIView level members from child elements. control-specific children (fonts,
# button states, named colors) are stashed in captured for the concrete handler.
# returns the attrs consumed
def _add_view_common(view: O, el: ET.Element, ctx: Context, captured: dict) -> set[str]:
    handled_attrs = {"contentMode", "opaque", "hidden", "clipsSubviews",
                     "userInteractionEnabled", "multipleTouchEnabled"}
    frame = None
    background = None
    tint = None
    autoresize = 0
    subviews: list[O] = []
    constraints_el = None
    safe_area_el = None

    for child in el:
        key = child.get("key")
        if child.tag == "rect" and key == "frame":
            frame = _parse_frame(child)
        elif child.tag == "autoresizingMask" and key == "autoresizingMask":
            for name, bit in _AUTORESIZE_FLAGS.items():
                if child.get(name) == "YES":
                    autoresize |= bit
        elif child.tag == "color" and key == "backgroundColor":
            background = _color(child)
        elif child.tag == "color" and key == "tintColor":
            tint = _color(child)
        elif child.tag == "color" and key in _CAPTURED_COLORS:
            captured.setdefault("colors", {})[key] = child
        elif child.tag == "color":
            raise XIBUnsupported(f"unsupported <color key={key!r}> in <{el.tag}>")
        elif child.tag == "subviews":
            for sub in child:
                subviews.append(_convert_view(sub, ctx, is_root=False))
        elif child.tag == "connections":
            _parse_connections(child, view, ctx)
        elif child.tag == "fontDescription" and key == "fontDescription":
            captured["font"] = child
        elif child.tag == "state":
            captured.setdefault("states", []).append(child)
        elif child.tag == "textInputTraits":
            captured["textInputTraits"] = child  # consumed, encoding not emitted yet
        elif child.tag == "constraints":
            constraints_el = child
        elif child.tag == "viewLayoutGuide":
            safe_area_el = child
        else:
            raise XIBUnsupported(f"unsupported child <{child.tag}> in <{el.tag}>")

    if frame is not None:
        x, y, w, h = frame
        view.add("UIBounds", rect(0, 0, w, h))
        view.add("UICenter", point(x + w / 2, y + h / 2))
    if subviews:
        arr = O("NSMutableArray", [("NSInlinedValue", False)]
                + [("UINibEncoderEmptyKey", Ref(s)) for s in subviews])
        view.add("UISubviews", Ref(arr))
    if background is not None:
        view.add("UIBackgroundColor", Ref(background))
    if tint is not None:
        view.add("UITintColor", Ref(tint))
    if el.get("opaque") != "NO":
        view.add("UIOpaque", True)
    if autoresize:
        view.add("UIAutoresizingMask", autoresize)
    translates = el.get("translatesAutoresizingMaskIntoConstraints")
    if translates is not None:
        view.add("UIViewDoesNotTranslateAutoresizingMaskIntoConstraints",
                 translates == "NO")
    if constraints_el is not None:
        _parse_constraints(constraints_el, view, ctx)
    if safe_area_el is not None:
        _add_safe_area_guide(view, safe_area_el, ctx)

    return handled_attrs


_SYMBOLIC_FONT_SIZE = {"system": 14.0, "button": 14.0, "small": 12.0, "label": 17.0}

# UIFontDescriptorSymbolicTraits, the bits the corpus shows
_FONT_TRAIT_ITALIC = 1
_FONT_TRAIT_BOLD = 2

# UIFontDescriptorOptions, one for a usage-named system font, one for a face-named
# one. fixed across all 54 corpus nibs with a font
_FONT_OPTIONS_SYSTEM = 0x80008404
_FONT_OPTIONS_NAMED = 0x80000000

# system font weight -> face name, usage attr, override usage, symbolic traits.
# corpus backed. the bold row uses an emphasized usage plus a bold override and
# stores its attrs in a mutable dict. weights the corpus never shows fail loud
_SYSTEM_FONT_FACES = {
    "regular": (".SFUI-Regular", "CTFontRegularUsage", None, 0),
    "medium": (".SFUI-Medium", "CTFontMediumUsage", None, 0),
    "semibold": (".SFUI-Semibold", "CTFontDemiUsage", None, _FONT_TRAIT_BOLD),
    "bold": (".SFUI-Bold", "CTFontEmphasizedUsage", "CTFontBoldUsage", _FONT_TRAIT_BOLD),
}

# italicSystem has no corpus sample. oblique usage name and override unknown
# fails loud
_SYSTEM_FONT_TYPE_WEIGHT = {"system": "regular", "boldSystem": "bold"}
_BUTTON_STATE = {"normal": 0, "highlighted": 1, "disabled": 2, "selected": 4}
_BORDER_STYLE = {"none": 0, "line": 1, "bezel": 2, "roundedRect": 3}


def _font_descriptor(attributes: list[tuple[str, O]], options: int,
                     mutable: bool) -> O:
    # the UIFontDescriptor a UIFont hangs off. attributes are an alternating key then
    # value run in an NS(Mutable)Dictionary, mutable only when an override attr is present
    entries: list = [("NSInlinedValue", False)]
    for key, value in attributes:
        entries.append(("UINibEncoderEmptyKey", Ref(nsstring(key))))
        entries.append(("UINibEncoderEmptyKey", Ref(value)))
    table = O("NSMutableDictionary" if mutable else "NSDictionary", entries)
    return O("UIFontDescriptor", [
        ("UIFontDescriptorAttributes", Ref(table)),
        ("UIFontDescriptorOptions", options),
    ])


# a named face carries the symbolic traits of the font it resolves to, which ibtool
# reads from the installed font and we cant. read them off the face name instead
def _custom_font_traits(name: str) -> int:
    lowered = name.lower()
    traits = 0
    if any(word in lowered for word in ("bold", "semibold", "heavy", "black")):
        traits |= _FONT_TRAIT_BOLD
    if "italic" in lowered or "oblique" in lowered:
        traits |= _FONT_TRAIT_ITALIC
    return traits


def _font(el: ET.Element) -> O:
    handled = {"key"}
    ftype = el.get("type")
    if ftype:
        handled.add("type")
        if ftype not in _SYSTEM_FONT_TYPE_WEIGHT:
            raise XIBUnsupported(f"unsupported font type {ftype!r}")
    weight = el.get("weight")
    if weight:
        handled.add("weight")
    name = el.get("name")
    if name:
        handled.add("name")
    if el.get("family"):
        handled.add("family")
    point = el.get("pointSize")
    if point:
        handled.add("pointSize")
    symbolic = el.get("size")
    if symbolic:
        handled.add("size")
        if symbolic not in _SYMBOLIC_FONT_SIZE:
            raise XIBUnsupported(f"unsupported symbolic font size {symbolic!r}")
    _require_no_extra_attrs(el, handled)

    size = float(point) if point else _SYMBOLIC_FONT_SIZE.get(symbolic, 14.0)
    number = O("NSNumber", [("NS.dblval", size)])

    if ftype:
        # a system font is named by its usage, not a face the document carries
        implied = _SYSTEM_FONT_TYPE_WEIGHT[ftype]
        if weight and implied != "regular" and weight != implied:
            raise XIBUnsupported(
                f"font type {ftype!r} and weight {weight!r} disagree")
        resolved = weight or implied
        if resolved not in _SYSTEM_FONT_FACES:
            raise XIBUnsupported(f"unsupported system font weight {resolved!r}")
        face, usage, override, traits = _SYSTEM_FONT_FACES[resolved]
        attributes = [("NSCTFontUIUsageAttribute", nsstring(usage))]
        if override:
            attributes.append(("NSCTFontUIUsageOverrideAttribute", nsstring(override)))
        attributes.append(("NSFontSizeAttribute", number))
        descriptor = _font_descriptor(attributes, _FONT_OPTIONS_SYSTEM, bool(override))
        face_name = nsstring(face)
        # consistent across the corpus. usage-named system font -> UISystemFont false
        # face-named -> true
        is_system = False
    elif name:
        face_name = nsstring(name)
        traits = _custom_font_traits(name)
        descriptor = _font_descriptor(
            [("NSFontNameAttribute", face_name), ("NSFontSizeAttribute", number)],
            _FONT_OPTIONS_NAMED, False)
        is_system = True
    else:
        raise XIBUnsupported("<fontDescription> needs a type or a name")

    font = O("UIFont")
    font.add("UIFontName", Ref(face_name))
    font.add("UIFontDescriptor", Ref(descriptor))
    font.add("UIFontPointSize", size)
    font.add("UIFontTextStyleForScaling", None)
    font.add("UIFontPointSizeForScaling", 0.0)
    font.add("UIFontMaximumPointSizeAfterScaling", 0.0)
    font.add("UIFontTraits", traits)
    font.add("UISystemFont", is_system)
    font.add("NSName", Ref(face_name))
    font.add("NSSize", size)
    return font


def _swift_mangle(module: str, name: str) -> str:
    return f"_TtC{len(module)}{module}{len(name)}{name}"


# turn a view with a customClass into a UIClassSwapper
# keeps its members, gains UIClassName and UIOriginalClassName
# a customModule marks a swift class, runtime name is module mangled
def _apply_custom_class(obj: O, el: ET.Element):
    custom = el.get("customClass")
    if not custom:
        return
    module = el.get("customModule")
    swapped = _swift_mangle(module, custom) if module else custom
    base = obj.class_name
    obj.add("UIClassName", nsstring(swapped))
    obj.add("UIOriginalClassName", nsstring(base))
    obj.class_name = "UIClassSwapper"


def _convert_view(el: ET.Element, ctx: Context, is_root: bool) -> O:
    handler = _VIEW_HANDLERS.get(el.tag)
    if handler is None:
        raise XIBUnsupported(f"unsupported view element <{el.tag}>")
    obj = handler(el, ctx, is_root)
    _apply_custom_class(obj, el)
    obj_id = el.get("id")
    if obj_id:
        ctx.by_id[obj_id] = obj
    ctx.builder.add(obj)
    ctx.all_objects.append(obj)
    return obj


def _convert_plain_view(el: ET.Element, ctx: Context, is_root: bool) -> O:
    view = O("UIView")
    captured: dict = {}
    handled = _add_view_common(view, el, ctx, captured)
    if captured:
        raise XIBUnsupported(f"<view> has unexpected children {sorted(captured)}")
    _require_no_extra_attrs(el, handled)
    return view


def _convert_label(el: ET.Element, ctx: Context, is_root: bool) -> O:
    label = O("UILabel")
    captured: dict = {}
    handled = _add_view_common(label, el, ctx, captured)
    text = el.get("text")
    if text is not None:
        label.add("UIText", nsstring(text))
    color = captured.get("colors", {}).get("textColor")
    if color is not None:
        label.add("UITextColor", Ref(_color(color)))
    align = el.get("textAlignment")
    if align:
        if align not in _TEXT_ALIGNMENT:
            raise XIBUnsupported(f"unsupported textAlignment={align!r}")
        if _TEXT_ALIGNMENT[align] != 0:
            label.add("UITextAlignment", _TEXT_ALIGNMENT[align])
    lines = el.get("numberOfLines")
    if lines and int(lines) != 1:
        label.add("UINumberOfLines", int(lines))
    if "font" in captured:
        label.add("UIFont", Ref(_font(captured["font"])))
    label_attrs = {"text", "textAlignment", "numberOfLines", "lineBreakMode",
                   "baselineAdjustment", "adjustsFontSizeToFit"}
    _require_no_extra_attrs(el, handled | label_attrs)
    return label


def _convert_image_view(el: ET.Element, ctx: Context, is_root: bool) -> O:
    view = O("UIImageView")
    captured: dict = {}
    handled = _add_view_common(view, el, ctx, captured)
    if captured:
        raise XIBUnsupported(f"<imageView> has unexpected children {sorted(captured)}")
    image = el.get("image")
    if image:
        view.add("UIImage", Ref(_image_resource(image)))
    _require_no_extra_attrs(el, handled | {"image"})
    return view


def _image_resource(name: str) -> O:
    from iosc.formats.nib_build import F32
    return O("UIImageNibPlaceholder", [
        ("UIImageWidth", F32(1.0)),
        ("UIImageHeight", F32(1.0)),
        ("UIResourceName", nsstring(name)),
    ])


def _convert_text_field(el: ET.Element, ctx: Context, is_root: bool) -> O:
    field = O("UITextField")
    captured: dict = {}
    handled = _add_view_common(field, el, ctx, captured)
    text = el.get("text")
    if text is not None:
        field.add("UIText", nsstring(text))
    placeholder = el.get("placeholder")
    if placeholder is not None:
        field.add("UIPlaceholder", nsstring(placeholder))
    border = el.get("borderStyle")
    if border:
        if border not in _BORDER_STYLE:
            raise XIBUnsupported(f"unsupported borderStyle {border!r}")
        if _BORDER_STYLE[border] != 0:
            field.add("UIBorderStyle", _BORDER_STYLE[border])
    color = captured.get("colors", {}).get("textColor")
    if color is not None:
        field.add("UITextColor", Ref(_color(color)))
    if "font" in captured:
        field.add("UIFont", Ref(_font(captured["font"])))
    _require_no_extra_attrs(el, handled | {"text", "placeholder", "borderStyle"})
    return field


def _convert_button(el: ET.Element, ctx: Context, is_root: bool) -> O:
    button = O("UIButton")
    captured: dict = {}
    handled = _add_view_common(button, el, ctx, captured)

    entries: list = []
    for state_el in captured.get("states", []):
        name = state_el.get("key")
        if name not in _BUTTON_STATE:
            raise XIBUnsupported(f"unsupported button state {name!r}")
        content = _button_content(state_el)
        if content is not None:
            number = O("NSNumber", [("NS.intval", _BUTTON_STATE[name])])
            entries.append(Ref(number))
            entries.append(Ref(content))
    if entries:
        content_dict = O("NSMutableDictionary", [("NSInlinedValue", False)]
                         + [("UINibEncoderEmptyKey", e) for e in entries])
        button.add("UIButtonStatefulContent", Ref(content_dict))

    button_type = el.get("buttonType")
    if button_type and button_type not in ("system", "roundedRect", "custom"):
        raise XIBUnsupported(f"unsupported buttonType {button_type!r}")
    type_value = 0 if button_type == "custom" else 1
    button.add("UIButtonType", type_value)
    button.add("UIAdjustsImageWhenHighlighted", False)
    button.add("UIAdjustsImageWhenDisabled", False)
    if "font" in captured:
        button.add("UIFont", Ref(_font(captured["font"])))
    _require_no_extra_attrs(el, handled | {"buttonType", "lineBreakMode"})
    return button


def _button_content(state_el: ET.Element) -> O | None:
    handled = {"key"}
    members: list = []
    title = state_el.get("title")
    if title is not None:
        handled.add("title")
        members.append(("UITitle", nsstring(title)))
    for child in state_el:
        if child.tag == "color" and child.get("key") == "titleColor":
            members.append(("UITitleColor", Ref(_color(child))))
        else:
            raise XIBUnsupported(f"unsupported <{child.tag}> in <state>")
    _require_no_extra_attrs(state_el, handled)
    return O("UIButtonContent", members) if members else None


def _parse_connections(el: ET.Element, owner: O, ctx: Context):
    for conn in el:
        if conn.tag == "outlet":
            prop = conn.get("property")
            dest = conn.get("destination")
            if not prop or not dest:
                raise XIBUnsupported("outlet missing property or destination")
            ctx.pending_outlets.append((owner, prop, dest))
        elif conn.tag == "action":
            ctx.pending_actions.append(parse_action(conn, owner))
        else:
            raise XIBUnsupported(f"unsupported connection <{conn.tag}>")


# read one <action> into a pending target action record. event mask is absent when
# the sender is not a UIControl, e.g. a bar button item
def parse_action(el: ET.Element, source: O) -> tuple[O, str, str, int | None]:
    selector = el.get("selector")
    dest = el.get("destination")
    if not selector or not dest:
        raise XIBUnsupported("action missing selector or destination")
    leftover = set(el.attrib) - _ACTION_ATTRS
    if leftover:
        raise XIBUnsupported(f"<action> has unsupported attributes {sorted(leftover)}")
    event = el.get("eventType")
    if event is None:
        mask = None
    elif event in _CONTROL_EVENTS:
        mask = _CONTROL_EVENTS[event]
    else:
        raise XIBUnsupported(f"unsupported action eventType {event!r}")
    return (source, selector, dest, mask)


def build_event_connection(ctx: Context, source: O, selector: str, dest: O,
                           mask: int | None) -> O:
    members = [
        ("UILabel", ctx.selector(selector)),
        ("UISource", Ref(source)),
        ("UIDestination", Ref(dest)),
    ]
    if mask is not None:
        members.append(("UIEventMask", mask))
    conn = O("UIRuntimeEventConnection", members)
    ctx.builder.add(conn)
    ctx.connections.append(conn)
    return conn


# a constraint shell per <constraint>, hung off the owning view
# item refs resolve in a later pass once every id is known, like outlets
def _parse_constraints(el: ET.Element, view: O, ctx: Context):
    shells: list[O] = []
    for c in el:
        if c.tag != "constraint":
            raise XIBUnsupported(f"unsupported <{c.tag}> in <constraints>")
        if c.get("placeholder") == "YES":
            continue  # design time only, stripped from the runtime nib
        obj = O("NSLayoutConstraint")
        ctx.builder.add(obj)
        ctx.all_objects.append(obj)
        ctx.pending_constraints.append((obj, c, view))
        shells.append(obj)
    if shells:
        arr = O("NSMutableArray", [("NSInlinedValue", False)]
                + [("UINibEncoderEmptyKey", Ref(s)) for s in shells])
        ctx.builder.add(arr)
        view.add("UIViewAutolayoutConstraints", Ref(arr))


def _layout_attr(name: str | None) -> int:
    if name is None:
        return 0
    if name not in _LAYOUT_ATTR:
        raise XIBUnsupported(f"unsupported constraint attribute {name!r}")
    return _LAYOUT_ATTR[name]


def _multiplier(text: str) -> float:
    if ":" in text:
        num, den = text.split(":", 1)
        value = float(num) / float(den)
    else:
        value = float(text)
    return struct.unpack("<f", struct.pack("<f", value))[0]  # xcode widens a float32


def _resolve_item(item_id: str | None, owner: O, ctx: Context) -> O:
    if item_id is None:
        return owner  # a self constraint names no first item
    dest = ctx.by_id.get(item_id)
    if dest is None:
        raise XIBUnsupported(f"constraint item id {item_id!r} not found")
    return dest


def _resolve_constraints(ctx: Context):
    handled = {"id", "firstItem", "firstAttribute", "secondItem", "secondAttribute",
               "constant", "multiplier", "priority", "relation", "identifier",
               "symbolic", "placeholder"}
    for obj, el, owner in ctx.pending_constraints:
        _require_no_extra_attrs(el, handled)
        first_attr = _layout_attr(el.get("firstAttribute"))
        obj.add("NSFirstItem", Ref(_resolve_item(el.get("firstItem"), owner, ctx)))
        obj.add("NSFirstAttributeV2", first_attr)
        obj.add("NSFirstAttribute", first_attr)

        relation = el.get("relation")
        if relation:
            if relation not in _LAYOUT_RELATION:
                raise XIBUnsupported(f"unsupported constraint relation {relation!r}")
            obj.add("NSRelation", _LAYOUT_RELATION[relation])

        mult = el.get("multiplier")
        if mult:
            value = _multiplier(mult)
            if value != 1.0:
                obj.add("NSMultiplier", value)

        second_id = el.get("secondItem")
        if second_id:
            second_attr = _layout_attr(el.get("secondAttribute"))
            obj.add("NSSecondItem", Ref(_resolve_item(second_id, owner, ctx)))
            obj.add("NSSecondAttributeV2", second_attr)
            obj.add("NSSecondAttribute", second_attr)

        const = float(el.get("constant", 0) or 0)
        if const:
            obj.add("NSConstantV2", const)
        obj.add("NSShouldBeArchived", False)
        priority = el.get("priority")
        if priority and int(priority) != 1000:
            obj.add("NSPriority", int(priority))
        if const:
            obj.add("NSConstant", const)
        identifier = el.get("identifier")
        if identifier:
            obj.add("NSLayoutIdentifier", nsstring(identifier))


def _system_constraint(first: O, first_attr: int, second: O, second_attr: int,
                       identifier: str) -> O:
    return O("NSLayoutConstraint", [
        ("NSFirstItem", Ref(first)),
        ("NSFirstAttributeV2", first_attr),
        ("NSFirstAttribute", first_attr),
        ("NSSecondItem", Ref(second)),
        ("NSSecondAttributeV2", second_attr),
        ("NSSecondAttribute", second_attr),
        ("NSShouldBeArchived", True),
        ("NSLayoutIdentifier", nsstring(identifier)),
    ])


# the safe area UILayoutGuide and its four system constraints, from the corpus
# attrs left 1, right 2, top 3, bottom 4
# guide leads top and left, view leads bottom and right, insets stay non-negative
def _safe_area_guide(view: O) -> O:
    guide = O("UILayoutGuide", [
        ("UILayoutGuideOwningView", Ref(view)),
        ("UILayoutGuideIdentifier", nsstring("UIViewSafeAreaLayoutGuide")),
        ("UILayoutGuideOwningViewIsLocked", False),
        ("UILayoutGuideShouldBeArchived", False),
        ("UILayoutGuideAllowsNegativeDimensions", False),
    ])
    system = [
        _system_constraint(guide, 3, view, 3, "UIViewSafeAreaLayoutGuide-top"),
        _system_constraint(guide, 1, view, 1, "UIViewSafeAreaLayoutGuide-left"),
        _system_constraint(view, 4, guide, 4, "UIViewSafeAreaLayoutGuide-bottom"),
        _system_constraint(view, 2, guide, 2, "UIViewSafeAreaLayoutGuide-right"),
    ]
    array = O("NSArray", [("NSInlinedValue", False)]
              + [("UINibEncoderEmptyKey", Ref(c)) for c in system])
    guide.add("UILayoutGuideSystemConstraints", Ref(array))
    return guide


def _add_safe_area_guide(view: O, el: ET.Element, ctx: Context):
    if el.get("key") != "safeArea":
        raise XIBUnsupported(f"unsupported viewLayoutGuide key {el.get('key')!r}")
    guide = _safe_area_guide(view)
    ctx.builder.add(guide)
    guide_id = el.get("id")
    if guide_id:
        ctx.by_id[guide_id] = guide  # so constraints can anchor to the safe area
    guides = O("NSMutableArray", [("NSInlinedValue", False),
                                  ("UINibEncoderEmptyKey", Ref(guide))])
    view.add("UIViewLayoutGuides", Ref(guides))
    view.add("UIViewInsetsLayoutMarginsFromSafeArea", 0)


def _convert_placeholder(el: ET.Element, ctx: Context) -> O:
    ident = el.get("placeholderIdentifier")
    if ident not in ("IBFilesOwner", "IBFirstResponder"):
        raise XIBUnsupported(f"unsupported placeholder {ident!r}")
    proxy = O("UIProxyObject", [("UIProxiedObjectIdentifier", nsstring(ident))])
    obj_id = el.get("id")
    if obj_id:
        ctx.by_id[obj_id] = proxy
    ctx.builder.add(proxy)
    ctx.all_objects.append(proxy)
    ctx.top_level.append(proxy)
    for child in el:
        if child.tag == "connections":
            _parse_connections(child, proxy, ctx)
        else:
            raise XIBUnsupported(f"unsupported child <{child.tag}> in placeholder")
    return proxy


_VIEW_HANDLERS = {
    "view": _convert_plain_view,
    "label": _convert_label,
    "imageView": _convert_image_view,
    "textField": _convert_text_field,
    "button": _convert_button,
}


def compile_xib(xml_text: str) -> bytes:
    root = ET.fromstring(xml_text)
    if root.tag != "document":
        raise XIBUnsupported(f"expected <document>, got <{root.tag}>")

    ctx = Context()
    objects_el = root.find("objects")
    if objects_el is None:
        raise XIBUnsupported("document has no <objects>")

    for el in objects_el:
        if el.tag == "placeholder":
            _convert_placeholder(el, ctx)
        elif el.tag in _VIEW_HANDLERS:
            obj = _convert_view(el, ctx, is_root=True)
            ctx.top_level.append(obj)
        elif el.tag == "customObject":
            raise XIBUnsupported("<customObject> not supported yet")
        else:
            raise XIBUnsupported(f"unsupported top level <{el.tag}>")

    _resolve_outlets(ctx)
    _resolve_actions(ctx)
    _resolve_constraints(ctx)

    return _assemble(ctx)


def _resolve_outlets(ctx: Context):
    for source, prop, dest_id in ctx.pending_outlets:
        dest = ctx.by_id.get(dest_id)
        if dest is None:
            raise XIBUnsupported(f"outlet destination id {dest_id!r} not found")
        conn = O("UIRuntimeOutletConnection", [
            ("UILabel", nsstring(prop)),
            ("UISource", Ref(source)),
            ("UIDestination", Ref(dest)),
        ])
        ctx.builder.add(conn)
        ctx.connections.append(conn)


def _resolve_actions(ctx: Context):
    for source, selector, dest_id, mask in ctx.pending_actions:
        dest = ctx.by_id.get(dest_id)
        if dest is None:
            raise XIBUnsupported(f"action destination id {dest_id!r} not found")
        build_event_connection(ctx, source, selector, dest, mask)


def _assemble(ctx: Context) -> bytes:
    from iosc.formats import nibarchive as N

    empty = O("NSArray", [("NSInlinedValue", False)])

    def array(objs: list[O]) -> O:
        return O("NSArray", [("NSInlinedValue", False)]
                 + [("UINibEncoderEmptyKey", Ref(o)) for o in objs])

    top = array(ctx.top_level)
    allobjs = array(ctx.all_objects)
    conns = array(ctx.connections)

    root = O("NSObject", [
        ("UINibTopLevelObjectsKey", Ref(top)),
        ("UINibObjectsKey", Ref(allobjs)),
        ("UINibConnectionsKey", Ref(conns)),
        ("UINibVisibleWindowsKey", Ref(empty)),
        ("UINibAccessibilityConfigurationsKey", Ref(empty)),
        ("UINibTraitStorageListsKey", Ref(empty)),
        ("UINibKeyValuePairsKey", Ref(empty)),
    ])

    # root first, then the structural arrays, then everything already registered
    ordered = [root, top, allobjs, conns, empty]
    for o in ordered:
        ctx.builder.add(o)

    return N.serialize(ctx.builder.build())


