"""Compile a storyboard document to a .storyboardc bundle.

A storyboard is a directory of nibs indexed by a binary Info.plist. Each scene
compiles to a controller nib. Content view controllers also get a view nib. The
view hierarchy runs through the xib front end. Coverage is fail loud. One encoding
is guessed, a segue triggered by a control, see _add_trigger_segues.
"""

from __future__ import annotations

import os
import plistlib
import xml.etree.ElementTree as ET

from iosc.core import get_reporter
from iosc.formats.nib_build import BuildObject as O, F32, Ref, nsstring
from iosc.formats.xib import (Context, XIBUnsupported, _assemble, _color,
                 _convert_view, _parse_connections, _resolve_actions,
                 _resolve_constraints, _resolve_outlets, build_event_connection)


_SEGUE_KIND_CLASS = {
    "show": "UIStoryboardShowSegueTemplate",
    "showDetail": "UIStoryboardShowDetailSegueTemplate",
    "presentModally": "UIStoryboardPresentationSegueTemplate",
    "presentAsPopover": "UIStoryboardPopoverPresentationSegueTemplate",
    "custom": "UIStoryboardSegueTemplate",
    "push": "UIStoryboardPushSegueTemplate",
    "modal": "UIStoryboardModalSegueTemplate",
}

# handled by embedding, not a template
_SEGUE_RELATIONSHIP = "relationship"

# embed and unwind have no corpus sample yet
_SEGUE_DEFERRED = {"embed", "unwind"}

_SEGUE_ATTRS = {"id", "destination", "kind", "identifier", "action"}
_RELATIONSHIP_SEGUE_ATTRS = {"id", "destination", "kind", "relationship"}

# a control triggers its segue through a target action, selector perform:, target
# the segue template. from the WinObjC reference. unverified, no corpus sample
# see _add_trigger_segues
_PERFORM_SELECTOR = "perform:"
_PERFORM_EVENT_MASK = 0x40  # UIControlEventTouchUpInside

# only a UIControl can carry the trigger target action
# a bar button item triggers segues in xcode but sits in a different nib
# this encoding cant express that. fails loud
_TRIGGER_TAGS = {"button", "textField"}

# tag -> (base UIKit class, expected relationship name, bar element tag)
_CONTAINERS = {
    "navigationController": ("UINavigationController", "rootViewController", "navigationBar"),
    "tabBarController": ("UITabBarController", "viewControllers", "tabBar"),
}
_CONTROLLER_TAGS = {"viewController"} | set(_CONTAINERS)

# UIBarButtonSystemItem, order from the UIKit enum (WinObjC UIBarButtonItem.h).
_BAR_BUTTON_SYSTEM_ITEMS = {
    "done": 0, "cancel": 1, "edit": 2, "save": 3, "add": 4, "flexibleSpace": 5,
    "fixedSpace": 6, "compose": 7, "reply": 8, "action": 9, "organize": 10,
    "bookmarks": 11, "search": 12, "refresh": 13, "stop": 14, "camera": 15,
    "trash": 16, "play": 17, "pause": 18, "rewind": 19, "fastForward": 20,
    "undo": 21, "redo": 22, "pageCurl": 23,
}
_BAR_BUTTON_STYLES = {"plain": 0, "bordered": 1, "done": 2}
_LARGE_TITLE_MODES = {"automatic": 0, "always": 1, "never": 2}
_BAR_STYLES = {"default": 0, "black": 1}

# UITabBarSystemItem, order from the uikit enum
# no corpus sample, class unverified. members follow UIBarButtonItem
_TAB_BAR_SYSTEM_ITEMS = {
    "more": 0, "favorites": 1, "featured": 2, "topRated": 3, "recents": 4,
    "contacts": 5, "history": 6, "bookmarks": 7, "search": 8, "downloads": 9,
    "mostRecent": 10, "mostViewed": 11,
}

# UIBarTranslucence, from InternalWebBrowser.storyboardc
# translucent 1, opaque 2, unset 0. polarity is inferred
_BAR_TRANSLUCENCE = {"YES": 1, "NO": 2}

_NAV_BAR_ATTRS = {"key", "id", "contentMode", "barStyle", "translucent"}
_TAB_BAR_ATTRS = {"key", "id", "contentMode", "translucent"}
_BAR_BUTTON_ATTRS = {"id", "key", "style", "systemItem", "title", "image", "catalog",
                     "enabled", "width"}
_NAV_ITEM_ATTRS = {"key", "id", "title", "largeTitleDisplayMode"}
_TAB_ITEM_ATTRS = {"key", "id", "title", "image", "catalog", "tag", "systemItem"}

# children a controller element may carry. anything else fails loud
_CONTROLLER_CHILD_TAGS = {"view", "connections", "navigationItem", "tabBarItem",
                          "toolbarItems", "navigationBar", "tabBar"}


def _proxy(ctx: Context, identifier: str) -> O:
    proxy = O("UIProxyObject", [("UIProxiedObjectIdentifier", nsstring(identifier))])
    ctx.builder.add(proxy)
    return proxy


def _outlet(label: str, source: O, dest: O) -> O:
    return O("UIRuntimeOutletConnection", [
        ("UILabel", nsstring(label)),
        ("UISource", Ref(source)),
        ("UIDestination", Ref(dest)),
    ])


def _find_child(el: ET.Element, tag: str) -> ET.Element | None:
    for child in el:
        if child.tag == tag:
            return child
    return None


def _base_class(tag: str) -> str:
    if tag in _CONTAINERS:
        return _CONTAINERS[tag][0]
    return "UIViewController"


# scene members we accept without encoding. uikit provides these proxies at load time
_KNOWN_SCENE_PLACEHOLDERS = {"IBFirstResponder", "IBFilesOwner"}


def _scene_controller(objects_el: ET.Element) -> ET.Element:
    controllers = [c for c in objects_el if c.tag in _CONTROLLER_TAGS]
    if len(controllers) != 1:
        raise XIBUnsupported("a scene must hold exactly one view controller")
    for other in objects_el:
        if other.tag in _CONTROLLER_TAGS:
            continue
        if other.tag == "placeholder":
            ident = other.get("placeholderIdentifier")
            if ident not in _KNOWN_SCENE_PLACEHOLDERS:
                raise XIBUnsupported(f"unsupported scene placeholder {ident!r}")
            continue
        raise XIBUnsupported(f"unsupported scene object <{other.tag}>")
    return controllers[0]


def _controller_identity(vc_el: ET.Element) -> tuple[str, str, str | None, str]:
    vc_id = vc_el.get("id")
    if not vc_id:
        raise XIBUnsupported(f"<{vc_el.tag}> missing id")
    base = _base_class(vc_el.tag)
    class_name = vc_el.get("customClass") or base
    storyboard_id = vc_el.get("storyboardIdentifier")
    return vc_id, class_name, storyboard_id, storyboard_id or f"{class_name}-{vc_id}"


def _view_nib_name(vc_el: ET.Element) -> tuple[ET.Element, str]:
    view_el = _find_child(vc_el, "view")
    if view_el is None:
        raise XIBUnsupported(f"view controller {vc_el.get('id')!r} has no <view>")
    view_id = view_el.get("id")
    if not view_id:
        raise XIBUnsupported("scene <view> missing id")
    return view_el, f"{vc_el.get('id')}-view-{view_id}"


# map each scene placeholder identifier to the id connections reference it by
def _scene_proxy_ids(objects_el: ET.Element) -> dict[str, str]:
    ids: dict[str, str] = {}
    for el in objects_el:
        if el.tag == "placeholder":
            ident = el.get("placeholderIdentifier")
            el_id = el.get("id")
            if ident and el_id:
                ids[ident] = el_id
    return ids


def _register_proxy_aliases(ctx: Context, owner: O, responder: O,
                            owner_id: str | None, proxy_ids: dict[str, str]):
    # point the scene ids a connection can name at the proxies standing in for them
    if owner_id:
        ctx.by_id.setdefault(owner_id, owner)
    files_owner = proxy_ids.get("IBFilesOwner")
    if files_owner:
        ctx.by_id.setdefault(files_owner, owner)
    first_responder = proxy_ids.get("IBFirstResponder")
    if first_responder:
        ctx.by_id.setdefault(first_responder, responder)


def _build_view_nib(view_el: ET.Element, vc_el: ET.Element, vc_id: str,
                    proxy_ids: dict[str, str],
                    triggered: list[tuple[ET.Element, str]],
                    id_to_key: dict[str, str]) -> bytes:
    ctx = Context()
    owner = _proxy(ctx, "IBFilesOwner")
    responder = _proxy(ctx, "IBFirstResponder")
    ctx.all_objects.extend([owner, responder])
    ctx.top_level.extend([owner, responder])
    # file's owner of a scene view nib is the scene's view controller
    # a connection aimed at the controller id lands on the proxy
    _register_proxy_aliases(ctx, owner, responder, vc_id, proxy_ids)

    view_el.attrib.pop("key", None)  # scene names it key="view", not a view member
    root_view = _convert_view(view_el, ctx, is_root=True)
    ctx.top_level.append(root_view)

    conn = _outlet("view", owner, root_view)
    ctx.builder.add(conn)
    ctx.connections.append(conn)

    _controller_connections(vc_el, owner, ctx)
    _add_trigger_segues(ctx, triggered, id_to_key, owner)

    _resolve_outlets(ctx)
    _resolve_actions(ctx)
    _resolve_constraints(ctx)
    return _assemble(ctx)


def _extract_segues(scene_root: ET.Element,
                    vc_id: str) -> tuple[list[ET.Element], list[tuple[ET.Element, str]]]:
    # pull every <segue> out of a scene tree before the view compiler walks it
    # templates live on the controller
    # returns the controller's segues, and control-carried ones paired with the control id
    own: list[ET.Element] = []
    triggered: list[tuple[ET.Element, str]] = []
    for owner in list(scene_root.iter()):
        conns = _find_child(owner, "connections")
        if conns is None:
            continue
        for seg in [c for c in conns if c.tag == "segue"]:
            conns.remove(seg)
            owner_id = owner.get("id")
            if owner is scene_root or owner_id == vc_id:
                own.append(seg)
                continue
            if owner.tag not in _TRIGGER_TAGS:
                raise XIBUnsupported(
                    f"a segue triggered by <{owner.tag}> is not supported, only "
                    f"{sorted(_TRIGGER_TAGS)}")
            if not owner_id:
                raise XIBUnsupported(
                    f"<{owner.tag}> triggers a segue but has no id")
            triggered.append((seg, owner_id))
    return own, triggered


# parse a controller's <connections> onto obj, skipping segues (templates, not connections)
# a content controller's outlets and actions name views. they go in the view nib
# a container's name its bars and children. those go in the controller nib
def _controller_connections(vc_el: ET.Element, obj: O, ctx: Context):
    conns = _find_child(vc_el, "connections")
    if conns is None:
        return
    block = ET.Element("connections")
    block.extend(c for c in conns if c.tag != "segue")
    _parse_connections(block, obj, ctx)


# segues declared directly on a controller's <connections>
# read without mutating the tree. an embedded child's scene still needs them
def _direct_segues(vc_el: ET.Element) -> list[ET.Element]:
    conns = _find_child(vc_el, "connections")
    if conns is None:
        return []
    return [c for c in conns if c.tag == "segue"]


def _relationship_children(vc_el: ET.Element, expected: str) -> list[ET.Element]:
    for seg in _direct_segues(vc_el):
        if seg.get("kind") != _SEGUE_RELATIONSHIP:
            continue
        leftover = set(seg.attrib) - _RELATIONSHIP_SEGUE_ATTRS
        if leftover:
            raise XIBUnsupported(
                f"relationship <segue> has unsupported attributes {sorted(leftover)}")
        if seg.get("relationship") != expected:
            raise XIBUnsupported(
                f"<{vc_el.tag}> expects relationship {expected!r}, "
                f"got {seg.get('relationship')!r}")
    return [seg for seg in _direct_segues(vc_el)
            if seg.get("kind") == _SEGUE_RELATIONSHIP]


def _segue_template(seg: ET.Element, id_to_key: dict[str, str]) -> O:
    kind = seg.get("kind")
    if kind == _SEGUE_RELATIONSHIP:
        raise XIBUnsupported("relationship segues only belong on container controllers")
    if kind in _SEGUE_DEFERRED:
        raise XIBUnsupported(f"{kind!r} segues are not supported yet")
    cls = _SEGUE_KIND_CLASS.get(kind)
    if cls is None:
        raise XIBUnsupported(f"unsupported segue kind {kind!r}")
    leftover = set(seg.attrib) - _SEGUE_ATTRS
    if leftover:
        raise XIBUnsupported(f"<segue> has unsupported attributes {sorted(leftover)}")
    dest = seg.get("destination")
    if dest not in id_to_key:
        raise XIBUnsupported(f"segue destination {dest!r} not found")

    template = O(cls)
    identifier = seg.get("identifier")
    if identifier:
        template.add("UIIdentifier", nsstring(identifier))
    template.add("UIDestinationViewControllerIdentifier", nsstring(id_to_key[dest]))
    action = seg.get("action")
    if action:
        template.add("UIActionName", nsstring(action))
    return template


def _template_segues(segue_els: list[ET.Element], id_to_key: dict[str, str]) -> list[O]:
    # a relationship segue reaching here is misplaced. _segue_template fails loud on it
    return [_segue_template(s, id_to_key) for s in segue_els]


def _add_trigger_segues(ctx: Context, triggered: list[tuple[ET.Element, str]],
                        id_to_key: dict[str, str], owner: O):
    # wire each control to the segue it triggers, inside the view nib
    # guessed encoding, not corpus backed
    # connection is a WinObjC UIRuntimeEventConnection, selector perform:
    # the controller nib's template is unreachable from here
    # so the view nib carries a private copy pointed at file's owner. every compile warns
    for seg_el, source_id in triggered:
        source = ctx.by_id.get(source_id)
        if source is None:
            raise XIBUnsupported(
                f"segue trigger {source_id!r} is not part of the scene view")
        template = _segue_template(seg_el, id_to_key)
        ctx.builder.add(template)
        ctx.all_objects.append(template)
        conn = _outlet("viewController", template, owner)
        ctx.builder.add(conn)
        ctx.connections.append(conn)
        build_event_connection(ctx, source, _PERFORM_SELECTOR, template,
                               _PERFORM_EVENT_MASK)
        name = seg_el.get("identifier") or seg_el.get("id")
        get_reporter().warn(
            f"segue {name!r} is triggered by a control: the perform: connection is a "
            f"guessed encoding with no corpus sample, confirm it on a device")


def _reject_extra(el: ET.Element, allowed: set[str]):
    leftover = set(el.attrib) - allowed
    if leftover:
        raise XIBUnsupported(f"<{el.tag}> has unsupported attributes {sorted(leftover)}")


def _bool_attr(el: ET.Element, name: str, default: bool) -> bool:
    value = el.get(name)
    if value is None:
        return default
    return value.strip().upper() in ("YES", "TRUE")


# a bar item image, either an asset name or an SF Symbol (catalog="system")
def _build_image(el: ET.Element, ctx: Context) -> O | None:
    name = el.get("image")
    if name is None:
        return None
    image = O("UIImageNibPlaceholder", [
        ("UIImageWidth", F32(0.0)),
        ("UIImageHeight", F32(0.0)),
        ("UIResourceName", nsstring(name)),
    ])
    if el.get("catalog") == "system":
        image.add("UISystemSymbolResourceName", nsstring(name))
    ctx.builder.add(image)
    return image


def _build_bar_button(el: ET.Element, ctx: Context) -> O:
    if el.tag != "barButtonItem":
        raise XIBUnsupported(f"expected <barButtonItem>, got <{el.tag}>")
    _reject_extra(el, _BAR_BUTTON_ATTRS)

    item = O("UIBarButtonItem")
    for child in el:
        if child.tag != "connections":
            raise XIBUnsupported(f"unsupported <{child.tag}> in <barButtonItem>")
        _parse_connections(child, item, ctx)
    item.add("UIEnabled", _bool_attr(el, "enabled", True))
    system = el.get("systemItem")
    if system is not None:
        if system not in _BAR_BUTTON_SYSTEM_ITEMS:
            raise XIBUnsupported(f"unknown bar button systemItem {system!r}")
        item.add("UISystemItem", _BAR_BUTTON_SYSTEM_ITEMS[system])
        item.add("UIIsSystemItem", True)
    else:
        title = el.get("title")
        image = _build_image(el, ctx)
        if title is None and image is None:
            raise XIBUnsupported("bar button item needs a systemItem, title, or image")
        if title is not None:
            item.add("UITitle", nsstring(title))
        style = el.get("style")
        if style is not None:
            if style not in _BAR_BUTTON_STYLES:
                raise XIBUnsupported(f"unknown bar button style {style!r}")
            item.add("UIStyle", _BAR_BUTTON_STYLES[style])
        if image is not None:
            item.add("UIImage", Ref(image))
    ctx.builder.add(item)
    ctx.all_objects.append(item)
    return item


def _bar_button_group(nav_el: ET.Element, plural: str, single_key: str,
                      ctx: Context) -> list[O]:
    items: list[O] = []
    for child in nav_el:
        if child.tag == plural:
            items.extend(_build_bar_button(bb, ctx) for bb in child)
        elif child.tag == "barButtonItem" and child.get("key") == single_key:
            items.append(_build_bar_button(child, ctx))
    return items


def _build_nav_item(vc_el: ET.Element, ctx: Context) -> O | None:
    nav_el = _find_child(vc_el, "navigationItem")
    if nav_el is None:
        return None
    _reject_extra(nav_el, _NAV_ITEM_ATTRS)
    item = O("UINavigationItem")
    title = nav_el.get("title")
    if title is not None:
        item.add("UITitle", nsstring(title))
    for plural, single, key_left in [
        ("leftBarButtonItems", "UILeftBarButtonItem", "leftBarButtonItem"),
        ("rightBarButtonItems", "UIRightBarButtonItem", "rightBarButtonItem"),
    ]:
        group = _bar_button_group(nav_el, plural, key_left, ctx)
        if group:
            item.add(single, Ref(group[0]))
            array = _mutable_array(group)
            ctx.builder.add(array)
            item.add(single + "s", Ref(array))
    mode = nav_el.get("largeTitleDisplayMode")
    if mode is not None:
        if mode not in _LARGE_TITLE_MODES:
            raise XIBUnsupported(f"unknown largeTitleDisplayMode {mode!r}")
        item.add("UILargeTitleDisplayMode", _LARGE_TITLE_MODES[mode])
    for child in nav_el:
        if child.tag in ("leftBarButtonItems", "rightBarButtonItems"):
            continue
        if child.tag == "barButtonItem" and child.get("key") in (
                "leftBarButtonItem", "rightBarButtonItem"):
            continue
        raise XIBUnsupported(f"unsupported <{child.tag}> in <navigationItem>")
    ctx.builder.add(item)
    ctx.all_objects.append(item)
    return item


def _build_tab_item(vc_el: ET.Element, ctx: Context) -> O | None:
    tab_el = _find_child(vc_el, "tabBarItem")
    if tab_el is None:
        return None
    _reject_extra(tab_el, _TAB_ITEM_ATTRS)
    for child in tab_el:
        raise XIBUnsupported(f"unsupported <{child.tag}> in <tabBarItem>")
    item = O("UITabBarItem")
    system = tab_el.get("systemItem")
    if system is not None:
        if system not in _TAB_BAR_SYSTEM_ITEMS:
            raise XIBUnsupported(f"unknown tab bar systemItem {system!r}")
        item.add("UISystemItem", _TAB_BAR_SYSTEM_ITEMS[system])
        item.add("UIIsSystemItem", True)
    title = tab_el.get("title")
    if title is not None:
        item.add("UITitle", nsstring(title))
    image = _build_image(tab_el, ctx)
    if image is not None:
        item.add("UIImage", Ref(image))
    tag = tab_el.get("tag")
    if tag is not None:
        item.add("UITag", int(tag))
    ctx.builder.add(item)
    ctx.all_objects.append(item)
    return item


def _register_bar(bar_el: ET.Element, bar: O, ctx: Context):
    bar_id = bar_el.get("id")
    if bar_id:
        ctx.by_id[bar_id] = bar


# members a nav bar and a tab bar share. connections, tint, translucency
def _bar_common(bar_el: ET.Element, bar: O, ctx: Context):
    _register_bar(bar_el, bar, ctx)
    for child in bar_el:
        if child.tag == "connections":
            _parse_connections(child, bar, ctx)
        elif child.tag == "color" and child.get("key") == "tintColor":
            bar.add("UITintColor", Ref(_color(child)))
        else:
            key = child.get("key")
            raise XIBUnsupported(
                f"unsupported <{child.tag} key={key!r}> in <{bar_el.tag}>")
    translucent = bar_el.get("translucent")
    if translucent is not None:
        if translucent not in _BAR_TRANSLUCENCE:
            raise XIBUnsupported(f"unknown translucent {translucent!r}")
        bar.add("UIBarTranslucence", _BAR_TRANSLUCENCE[translucent])


# a controller's <toolbarItems> hang off its class swapper as UIToolbarItems
# shape from InternalWebBrowser.storyboardc. the toolbar belongs to the nav controller
def _add_toolbar_items(vc_el: ET.Element, swapper: O, ctx: Context):
    items_el = _find_child(vc_el, "toolbarItems")
    if items_el is None:
        return
    items = [_build_bar_button(bb, ctx) for bb in items_el]
    array = O("NSArray", [("NSInlinedValue", False)]
              + [("UINibEncoderEmptyKey", Ref(item)) for item in items])
    ctx.builder.add(array)
    swapper.add("UIToolbarItems", Ref(array))


def _build_nav_bar(vc_el: ET.Element, nav_swapper: O,
                   child_pairs: list[tuple[ET.Element, O]], bar_tag: str, ctx: Context):
    bar_el = _find_child(vc_el, bar_tag)
    nav_items: list[O] = []
    for child_el, child_swapper in child_pairs:
        nav_item = _build_nav_item(child_el, ctx)
        if nav_item is not None:
            child_swapper.add("UINavigationItem", Ref(nav_item))
            nav_items.append(nav_item)

    bar = O("UINavigationBar")
    if bar_el is not None:
        _reject_extra(bar_el, _NAV_BAR_ATTRS)
        _bar_common(bar_el, bar, ctx)
        style = bar_el.get("barStyle")
        if style is not None:
            if style not in _BAR_STYLES:
                raise XIBUnsupported(f"unknown navigationBar barStyle {style!r}")
            bar.add("UIBarStyle", _BAR_STYLES[style])
    items_array = _mutable_array(nav_items)
    ctx.builder.add(items_array)
    bar.add("UIItems", Ref(items_array))
    bar.add("UIDelegate", Ref(nav_swapper))
    ctx.builder.add(bar)
    ctx.all_objects.append(bar)
    nav_swapper.add("UINavigationBar", Ref(bar))
    for nav_item in nav_items:  # bar back reference uikit expects on each item
        nav_item.add("UINavigationBar", Ref(bar))


def _build_tab_bar(vc_el: ET.Element, tab_swapper: O,
                   child_pairs: list[tuple[ET.Element, O]], bar_tag: str, ctx: Context):
    bar_el = _find_child(vc_el, bar_tag)
    tab_items: list[O] = []
    for child_el, child_swapper in child_pairs:
        tab_item = _build_tab_item(child_el, ctx)
        if tab_item is not None:
            child_swapper.add("UITabBarItem", Ref(tab_item))
            tab_items.append(tab_item)

    bar = O("UITabBar")
    if bar_el is not None:
        _reject_extra(bar_el, _TAB_BAR_ATTRS)
        _bar_common(bar_el, bar, ctx)
    items_array = _mutable_array(tab_items)
    ctx.builder.add(items_array)
    bar.add("UIItems", Ref(items_array))
    bar.add("UIDelegate", Ref(tab_swapper))
    ctx.builder.add(bar)
    ctx.all_objects.append(bar)
    tab_swapper.add("UITabBar", Ref(bar))


def _mutable_array(objs: list[O]) -> O:
    return O("NSMutableArray", [("NSInlinedValue", False)]
             + [("UINibEncoderEmptyKey", Ref(o)) for o in objs])


class _Swapper:
    def __init__(self, obj: O, is_root: bool):
        self.obj = obj
        self.is_root = is_root


def _build_swapper(vc_el: ET.Element, ctx: Context, controllers: dict[str, ET.Element],
                   parent: O | None, records: list[_Swapper], is_root: bool) -> O:
    tag = vc_el.tag
    if tag not in _CONTROLLER_TAGS:
        raise XIBUnsupported(f"unsupported controller <{tag}>")
    for child in vc_el:
        if child.tag not in _CONTROLLER_CHILD_TAGS:
            raise XIBUnsupported(f"unsupported <{child.tag}> in <{tag}>")
    vc_id, class_name, storyboard_id, _ = _controller_identity(vc_el)
    swapper = O("UIClassSwapper")
    records.append(_Swapper(swapper, is_root))
    # a controller nib targets its controllers directly
    # an action on a bar button resolves to the swapper, not file's owner
    ctx.by_id[vc_id] = swapper

    if tag in _CONTAINERS:
        _base, expected, bar_tag = _CONTAINERS[tag]
        if _find_child(vc_el, "view") is not None:
            raise XIBUnsupported(f"<{tag}> must not carry its own <view>")
        children = []
        child_pairs: list[tuple[ET.Element, O]] = []
        for seg in _relationship_children(vc_el, expected):
            dest = seg.get("destination")
            child_el = controllers.get(dest)
            if child_el is None:
                raise XIBUnsupported(
                    f"relationship segue destination {dest!r} not found")
            child_swapper = _build_swapper(
                child_el, ctx, controllers, swapper, records, False)
            children.append(child_swapper)
            child_pairs.append((child_el, child_swapper))
        if tag == "navigationController":
            _build_nav_bar(vc_el, swapper, child_pairs, bar_tag, ctx)
        else:
            _build_tab_bar(vc_el, swapper, child_pairs, bar_tag, ctx)
        # a container's outlets and actions name its bar or children
        # all in this nib, wired here not in a view nib
        _controller_connections(vc_el, swapper, ctx)
        child_arr = _mutable_array(children)
        view_arr = _mutable_array(children)
        ctx.builder.add(child_arr)
        ctx.builder.add(view_arr)
        swapper.add("UIChildViewControllers", Ref(child_arr))
        swapper.add("UIViewControllers", Ref(view_arr))
    else:
        _, view_nib = _view_nib_name(vc_el)
        external = O("NSDictionary", [("NSInlinedValue", False)])
        ctx.builder.add(external)
        swapper.add("UIExternalObjectsTableForViewLoading", Ref(external))
        swapper.add("UINibName", nsstring(view_nib))

    if storyboard_id:
        swapper.add("UIStoryboardIdentifier", nsstring(storyboard_id))
    if parent is not None:
        swapper.add("UIParentViewController", Ref(parent))
    _add_toolbar_items(vc_el, swapper, ctx)
    swapper.add("UIClassName", nsstring(class_name))
    swapper.add("UIOriginalClassName", nsstring(_base_class(tag)))
    return swapper


def _build_controller_nib(scene_vc_el: ET.Element, controllers: dict[str, ET.Element],
                          id_to_key: dict[str, str],
                          segue_els: list[ET.Element],
                          proxy_ids: dict[str, str]) -> bytes:
    ctx = Context()
    owner = _proxy(ctx, "IBFilesOwner")
    responder = _proxy(ctx, "IBFirstResponder")
    ctx.all_objects.extend([owner, responder])
    ctx.top_level.extend([owner, responder])
    # _build_swapper claims the controller ids. only placeholders alias here
    _register_proxy_aliases(ctx, owner, responder, None, proxy_ids)

    records: list[_Swapper] = []
    root = _build_swapper(scene_vc_el, ctx, controllers, None, records, True)

    for rec in records:
        placeholder = _proxy(ctx, "UIStoryboardPlaceholder")
        ctx.all_objects.append(placeholder)
        ctx.top_level.append(placeholder)
        conn = _outlet("storyboard", rec.obj, placeholder)
        ctx.builder.add(conn)
        ctx.connections.append(conn)

    for rec in records:
        ctx.builder.add(rec.obj)
        ctx.all_objects.append(rec.obj)
        if rec.is_root:
            ctx.top_level.append(rec.obj)

    scene_conn = _outlet("sceneViewController", owner, root)
    ctx.builder.add(scene_conn)
    ctx.connections.append(scene_conn)

    segues = _template_segues(segue_els, id_to_key)
    if segues:
        array = O("NSArray", [("NSInlinedValue", False)]
                  + [("UINibEncoderEmptyKey", Ref(s)) for s in segues])
        root.add("UIStoryboardSegueTemplates", Ref(array))
        ctx.builder.add(array)
        for segue in segues:  # each template points back at its source controller
            ctx.builder.add(segue)
            ctx.all_objects.append(segue)
            conn = _outlet("viewController", segue, root)
            ctx.builder.add(conn)
            ctx.connections.append(conn)

    _resolve_outlets(ctx)
    _resolve_actions(ctx)
    return _assemble(ctx)


class _Scene:
    def __init__(self, controller_nib: str, view_nib: str | None,
                 controller_bytes: bytes, view_bytes: bytes | None, vc_id: str):
        self.controller_nib = controller_nib
        self.view_nib = view_nib
        self.controller_bytes = controller_bytes
        self.view_bytes = view_bytes
        self.vc_id = vc_id


def _compile_scene(vc_el: ET.Element, objects_el: ET.Element,
                   controllers: dict[str, ET.Element],
                   id_to_key: dict[str, str]) -> _Scene:
    vc_id, _, _, controller_nib = _controller_identity(vc_el)
    proxy_ids = _scene_proxy_ids(objects_el)

    view_nib = None
    view_bytes = None
    if vc_el.tag in _CONTAINERS:
        segue_els = [s for s in _direct_segues(vc_el)
                     if s.get("kind") != _SEGUE_RELATIONSHIP]
    else:
        # strip segues before the view compiler walks the tree. a control-triggered
        # one still gets its template on the controller, plus a copy in the view nib
        # for the trigger
        own_segues, triggered = _extract_segues(vc_el, vc_id)
        segue_els = own_segues + [seg for seg, _ in triggered]
        view_el, view_nib = _view_nib_name(vc_el)
        view_bytes = _build_view_nib(view_el, vc_el, vc_id, proxy_ids, triggered,
                                     id_to_key)

    controller_bytes = _build_controller_nib(vc_el, controllers, id_to_key, segue_els,
                                             proxy_ids)
    return _Scene(controller_nib, view_nib, controller_bytes, view_bytes, vc_id)


# bundle file name -> bytes for the whole .storyboardc
def compile_storyboard(xml_text: str) -> dict[str, bytes]:
    root = ET.fromstring(xml_text)
    if root.tag != "document":
        raise XIBUnsupported(f"expected <document>, got <{root.tag}>")
    scenes_el = root.find("scenes")
    if scenes_el is None:
        raise XIBUnsupported("storyboard has no <scenes>")

    initial = root.get("initialViewController")
    id_to_nib: dict[str, str] = {}
    entry_point: str | None = None
    files: dict[str, bytes] = {}

    # first pass. map every controller id to its nib key, index elements for embedding
    controllers: list[tuple[ET.Element, ET.Element]] = []
    controllers_by_id: dict[str, ET.Element] = {}
    id_to_key: dict[str, str] = {}
    for scene_el in scenes_el:
        if scene_el.tag != "scene":
            raise XIBUnsupported(f"unsupported <{scene_el.tag}> in <scenes>")
        objects_el = _find_child(scene_el, "objects")
        if objects_el is None:
            raise XIBUnsupported("scene has no <objects>")
        vc_el = _scene_controller(objects_el)
        vc_id, _, _, key = _controller_identity(vc_el)
        id_to_key[vc_id] = key
        controllers_by_id[vc_id] = vc_el
        controllers.append((vc_el, objects_el))

    for vc_el, objects_el in controllers:
        scene = _compile_scene(vc_el, objects_el, controllers_by_id, id_to_key)
        files[scene.controller_nib + ".nib"] = scene.controller_bytes
        if scene.view_nib is not None:
            files[scene.view_nib + ".nib"] = scene.view_bytes
        id_to_nib[scene.controller_nib] = scene.controller_nib
        if initial and scene.vc_id == initial:
            entry_point = scene.controller_nib

    info: dict = {
        "UIViewControllerIdentifiersToNibNames": id_to_nib,
        "UIStoryboardVersion": 1,
    }
    if entry_point:
        info["UIStoryboardDesignatedEntryPointIdentifier"] = entry_point
    files["Info.plist"] = plistlib.dumps(info, fmt=plistlib.FMT_BINARY)
    return files


def write_storyboardc(xml_text: str, out_dir: str) -> list[str]:
    files = compile_storyboard(xml_text)
    os.makedirs(out_dir, exist_ok=True)
    for name, data in files.items():
        with open(os.path.join(out_dir, name), "wb") as handle:
            handle.write(data)
    return sorted(files)
