"""
Global shortcut backends for Linux.

There is no single global hotkey API that works on every Linux desktop,
so this module tries several backends, most specific first:

    1. GNOME          gsettings custom keybinding (X11 and Wayland)
    2. Hyprland       runtime bind through hyprctl (Lua and legacy config)
    3. Sway           runtime bindsym through swaymsg
    4. XDG portal     org.freedesktop.portal.GlobalShortcuts
                      (KDE Plasma, COSMIC, GNOME 48+, ...)
    5. X11            XGrabKey on the root window (XFCE, Cinnamon, MATE,
                      i3, Openbox, ... on X11)
    6. evdev          reads keyboards directly from /dev/input
                      (any session, requires read access to the keyboards)
    7. Manual         shows the toggle command so the user can bind it in
                      their own desktop or window manager

Every backend calls `on_activate` when the shortcut is pressed. Backends
that listen in a worker thread emit through a Qt signal, so the callback
always runs in the GUI thread.
"""

import ast
import asyncio
import ctypes
import ctypes.util
import json
import os
import re
import select
import shutil
import subprocess
import threading
import uuid

from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal


APP_ID = "be.ganderstudio.GanderAutoClicker"
APP_NAME = "Gander AutoClicker"


class ShortcutError(Exception):
    pass


class ShortcutConflict(ShortcutError):
    """The backend works, but the requested key is already taken."""


# =====================================================
# HOTKEY MODEL
# =====================================================

MODIFIER_ORDER = ("Ctrl", "Alt", "Shift", "Meta")

KEYSYM_NAMES = {
    "Space": "space",
    "Return": "Return",
    "Esc": "Escape",
}


@dataclass(frozen=True)
class Hotkey:
    """A shortcut such as Ctrl+Shift+A, stored in Qt's PortableText form."""

    modifiers: tuple
    key: str

    @classmethod
    def parse(cls, text):
        if not text:
            raise ShortcutError("Choose a keyboard shortcut.")

        parts = text.split("+")
        key = parts[-1]

        if not key:
            raise ShortcutError("Choose a valid key.")

        if len(key) == 1:
            key = key.upper()

        modifiers = set()

        for modifier in parts[:-1]:
            if modifier not in MODIFIER_ORDER:
                raise ShortcutError(
                    f"Unsupported modifier: {modifier}"
                )

            modifiers.add(modifier)

        function_key = re.fullmatch(
            r"F(?:[1-9]|1[0-9]|2[0-4])",
            key,
        )

        if not function_key and not modifiers:
            raise ShortcutError(
                "Use a function key or a shortcut with modifiers."
            )

        if not (
            function_key
            or (len(key) == 1 and key.isascii() and key.isalnum())
            or key in KEYSYM_NAMES
        ):
            raise ShortcutError(
                "Use F1-F24, or a letter/number with modifiers."
            )

        ordered = tuple(
            modifier
            for modifier in MODIFIER_ORDER
            if modifier in modifiers
        )

        return cls(ordered, key)

    def text(self):
        return "+".join([*self.modifiers, self.key])

    def keysym(self):
        """X keysym name, used by X11, Wayland compositors and portals."""

        if len(self.key) == 1:
            return self.key.lower()

        return KEYSYM_NAMES.get(self.key, self.key)

    # GNOME ----------------------------------------------

    GNOME_MODIFIERS = {
        "Ctrl": "<Control>",
        "Alt": "<Alt>",
        "Shift": "<Shift>",
        "Meta": "<Super>",
    }

    def gnome(self):
        prefix = "".join(
            self.GNOME_MODIFIERS[modifier]
            for modifier in self.modifiers
        )

        return prefix + self.keysym()

    @classmethod
    def from_gnome(cls, accelerator):
        names = {
            "control": "Ctrl",
            "primary": "Ctrl",
            "ctrl": "Ctrl",
            "shift": "Shift",
            "alt": "Alt",
            "super": "Meta",
        }

        modifiers = [
            names.get(name.lower(), name)
            for name in re.findall(r"<([^>]+)>", accelerator)
        ]

        key = re.sub(r"<[^>]+>", "", accelerator)

        reverse_keysyms = {
            value.lower(): name
            for name, value in KEYSYM_NAMES.items()
        }

        key = reverse_keysyms.get(key.lower(), key)

        return cls.parse("+".join([*modifiers, key]))

    # Hyprland -------------------------------------------

    HYPRLAND_MODIFIERS = {
        "Ctrl": ("CTRL", 4),
        "Alt": ("ALT", 8),
        "Shift": ("SHIFT", 1),
        "Meta": ("SUPER", 64),
    }

    def hyprland_modmask(self):
        return sum(
            self.HYPRLAND_MODIFIERS[modifier][1]
            for modifier in self.modifiers
        )

    def hyprland_modifiers(self):
        return [
            self.HYPRLAND_MODIFIERS[modifier][0]
            for modifier in self.modifiers
        ]

    # Sway -----------------------------------------------

    SWAY_MODIFIERS = {
        "Ctrl": "Ctrl",
        "Alt": "Mod1",
        "Shift": "Shift",
        "Meta": "Mod4",
    }

    def sway(self):
        return "+".join([
            *(self.SWAY_MODIFIERS[m] for m in self.modifiers),
            self.keysym(),
        ])

    # XDG GlobalShortcuts portal -------------------------

    XDG_MODIFIERS = {
        "Ctrl": "CTRL",
        "Alt": "ALT",
        "Shift": "SHIFT",
        "Meta": "LOGO",
    }

    def xdg_trigger(self):
        return "+".join([
            *(self.XDG_MODIFIERS[m] for m in self.modifiers),
            self.keysym(),
        ])

    # X11 ------------------------------------------------

    X11_MODIFIERS = {
        "Ctrl": 1 << 2,   # ControlMask
        "Alt": 1 << 3,    # Mod1Mask
        "Shift": 1 << 0,  # ShiftMask
        "Meta": 1 << 6,   # Mod4Mask
    }

    def x11_mask(self):
        return sum(
            self.X11_MODIFIERS[modifier]
            for modifier in self.modifiers
        )

    # evdev ----------------------------------------------

    def evdev_codes(self, ecodes):
        special = {
            "Space": ["KEY_SPACE"],
            "Return": ["KEY_ENTER", "KEY_KPENTER"],
            "Esc": ["KEY_ESC"],
        }

        names = special.get(self.key, [f"KEY_{self.key}"])

        return {getattr(ecodes, name) for name in names}


# =====================================================
# HELPERS
# =====================================================

def current_desktops():
    value = os.environ.get("XDG_CURRENT_DESKTOP", "")
    return {part.strip().lower() for part in value.split(":") if part}


def is_wayland_session():
    return (
        os.environ.get("XDG_SESSION_TYPE") == "wayland"
        or bool(os.environ.get("WAYLAND_DISPLAY"))
    )


def is_x11_session():
    if is_wayland_session():
        return False

    return (
        os.environ.get("XDG_SESSION_TYPE") == "x11"
        or bool(os.environ.get("DISPLAY"))
    )


def run(command, check=True):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=5,
    )

    if check and result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        raise ShortcutError(message or f"{command[0]} failed.")

    return result.stdout.strip()


class ActivationBridge(QObject):
    """Moves activations from worker threads to the GUI thread."""

    activated = Signal()
    status = Signal(str)


# =====================================================
# BACKEND BASE
# =====================================================

class ShortcutBackend:
    name = "Unknown"

    # True when the app can change the shortcut itself.
    configurable = True

    def __init__(self, toggle_command, bridge):
        self.toggle_command = toggle_command
        self.bridge = bridge

    @classmethod
    def available(cls):
        return False

    def start(self, hotkey):
        """Register the shortcut. Returns the active Hotkey."""
        raise NotImplementedError

    def change(self, hotkey, previous=None):
        """Replace the shortcut. Returns the active Hotkey."""
        self.stop()

        try:
            return self.start(hotkey)
        except Exception:
            # Put the old shortcut back so the user is not left without one.
            if previous:
                self.start(previous)
            raise

    def stop(self):
        pass

    def hint(self):
        """Extra information shown below the shortcut, if any."""
        return ""


# =====================================================
# 1. GNOME
# =====================================================

GNOME_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_SCHEMA = GNOME_SCHEMA + ".custom-keybinding"
CUSTOM_PATH = (
    "/org/gnome/settings-daemon/plugins/media-keys/"
    "custom-keybindings/gander-autoclicker/"
)
SHORTCUT_SCHEMA = f"{CUSTOM_SCHEMA}:{CUSTOM_PATH}"


def gsettings(*arguments):
    return run(["gsettings", *arguments])


class GnomeBackend(ShortcutBackend):
    """
    GNOME custom keyboard shortcut. GNOME runs `main.py --toggle`,
    which signals the running app through its local socket.
    """

    name = "GNOME"

    @classmethod
    def available(cls):
        if "gnome" not in current_desktops():
            return False

        if not shutil.which("gsettings"):
            return False

        try:
            cls.read_shortcut_list()
            return True
        except Exception:
            return False

    @staticmethod
    def read_shortcut_list():
        value = gsettings("get", GNOME_SCHEMA, "custom-keybindings")

        if value.startswith("@as"):
            value = value[3:].strip()

        return ast.literal_eval(value)

    @staticmethod
    def read_binding(schema=SHORTCUT_SCHEMA):
        return ast.literal_eval(gsettings("get", schema, "binding"))

    def start(self, hotkey):
        shortcuts = self.read_shortcut_list()

        if CUSTOM_PATH not in shortcuts:
            gsettings("set", SHORTCUT_SCHEMA, "name", repr(APP_NAME))
            gsettings("set", SHORTCUT_SCHEMA, "binding", repr(hotkey.gnome()))
            gsettings(
                "set",
                GNOME_SCHEMA,
                "custom-keybindings",
                repr([*shortcuts, CUSTOM_PATH]),
            )

        # Keep the command updated if the project location changes.
        gsettings(
            "set",
            SHORTCUT_SCHEMA,
            "command",
            repr(self.toggle_command),
        )

        # The binding stored in GNOME is the source of truth here, so a
        # change made in GNOME Settings is respected.
        return Hotkey.from_gnome(self.read_binding())

    def change(self, hotkey, previous=None):
        accelerator = hotkey.gnome()

        for path in self.read_shortcut_list():
            if path == CUSTOM_PATH:
                continue

            existing = self.read_binding(f"{CUSTOM_SCHEMA}:{path}")

            if existing.lower() == accelerator.lower():
                raise ShortcutError(
                    "This shortcut is already used by another "
                    "custom GNOME shortcut."
                )

        gsettings("set", SHORTCUT_SCHEMA, "binding", repr(accelerator))

        saved = self.read_binding()

        if saved.lower() != accelerator.lower():
            raise ShortcutError(
                "GNOME did not save the requested shortcut."
            )

        return Hotkey.from_gnome(saved)

    # The GNOME shortcut is persistent; when the app is closed the
    # toggle client simply finds no socket and exits.


# =====================================================
# 2. HYPRLAND
# =====================================================

class HyprlandBackend(ShortcutBackend):
    """
    Adds a bind at runtime through hyprctl and removes it on exit.
    Supports both the Lua config (Hyprland 0.55+) and hyprlang.
    """

    name = "Hyprland"

    def __init__(self, toggle_command, bridge):
        super().__init__(toggle_command, bridge)
        self.active = None
        self.lua_config = None

    @classmethod
    def available(cls):
        return (
            bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))
            and shutil.which("hyprctl") is not None
        )

    def uses_lua(self):
        if self.lua_config is None:
            output = run(["hyprctl", "eval", "return 1"], check=False)
            self.lua_config = "unknown request" not in output.lower()

        return self.lua_config

    def lua(self, code):
        output = run(["hyprctl", "eval", code])

        if output.lower().startswith("error"):
            raise ShortcutError(output)

    def keyword(self, name, value):
        output = run(["hyprctl", "keyword", name, value])

        if output.strip().lower() != "ok":
            raise ShortcutError(output)

    def combo_lua(self, hotkey):
        return " + ".join([*hotkey.hyprland_modifiers(), hotkey.keysym()])

    def combo_legacy(self, hotkey):
        return " ".join(hotkey.hyprland_modifiers()) + ", " + hotkey.keysym()

    def matching_binds(self, hotkey):
        binds = json.loads(run(["hyprctl", "binds", "-j"]))

        return [
            bind
            for bind in binds
            if bind.get("modmask") == hotkey.hyprland_modmask()
            and str(bind.get("key", "")).lower() == hotkey.keysym().lower()
            and not bind.get("submap")
        ]

    def is_ours(self, bind):
        return (
            bind.get("description") == APP_NAME
            or self.toggle_command in str(bind.get("arg", ""))
        )

    def unbind(self, hotkey):
        if self.uses_lua():
            self.lua(f'hl.unbind("{self.combo_lua(hotkey)}")')
        else:
            self.keyword("unbind", self.combo_legacy(hotkey))

    def start(self, hotkey):
        existing = self.matching_binds(hotkey)

        if any(not self.is_ours(bind) for bind in existing):
            raise ShortcutConflict(
                f"{hotkey.text()} is already bound in Hyprland."
            )

        # A bind left behind by a crashed instance.
        if existing:
            self.unbind(hotkey)

        if self.uses_lua():
            self.lua(
                f'hl.bind("{self.combo_lua(hotkey)}", '
                f"hl.dsp.exec_cmd([==[{self.toggle_command}]==]), "
                f'{{ description = "{APP_NAME}" }})'
            )
        else:
            self.keyword(
                "bindd",
                f"{self.combo_legacy(hotkey)}, {APP_NAME}, exec, "
                f"{self.toggle_command}",
            )

        if not self.matching_binds(hotkey):
            raise ShortcutError("Hyprland did not accept the shortcut.")

        self.active = hotkey
        return hotkey

    def stop(self):
        if self.active:
            try:
                self.unbind(self.active)
            except Exception:
                pass

            self.active = None


# =====================================================
# 3. SWAY
# =====================================================

class SwayBackend(ShortcutBackend):
    """Adds a bindsym at runtime through swaymsg and removes it on exit."""

    name = "Sway"

    def __init__(self, toggle_command, bridge):
        super().__init__(toggle_command, bridge)
        self.active = None

    @classmethod
    def available(cls):
        return (
            bool(os.environ.get("SWAYSOCK"))
            and shutil.which("swaymsg") is not None
        )

    def swaymsg(self, command):
        output = run(["swaymsg", "-r", command])

        for reply in json.loads(output):
            if not reply.get("success"):
                raise ShortcutError(reply.get("error", "swaymsg failed."))

    @staticmethod
    def normalize(combo):
        return frozenset(part.lower() for part in combo.split("+"))

    def configured_combos(self):
        """Best-effort list of bindsym combos from the sway config."""

        config = json.loads(
            run(["swaymsg", "-r", "-t", "get_config"])
        ).get("config", "")

        variables = dict(
            re.findall(r"^\s*set\s+(\$\S+)\s+(\S+)", config, re.MULTILINE)
        )

        combos = set()

        for line in re.findall(r"^\s*bindsym\s+(.+)$", config, re.MULTILINE):
            words = [word for word in line.split() if not word.startswith("--")]

            if not words:
                continue

            combo = words[0]

            for name, value in variables.items():
                combo = combo.replace(name, value)

            combos.add(self.normalize(combo))

        return combos

    def start(self, hotkey):
        try:
            taken = self.normalize(hotkey.sway()) in self.configured_combos()
        except Exception:
            taken = False

        if taken:
            raise ShortcutConflict(
                f"{hotkey.text()} is already bound in Sway."
            )

        command = self.toggle_command.replace("\\", "\\\\").replace('"', '\\"')

        self.swaymsg(f'bindsym --no-warn {hotkey.sway()} exec "{command}"')

        self.active = hotkey
        return hotkey

    def stop(self):
        if self.active:
            try:
                self.swaymsg(f"unbindsym {self.active.sway()}")
            except Exception:
                pass

            self.active = None


# =====================================================
# 4. XDG DESKTOP PORTAL (GlobalShortcuts)
# =====================================================

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_SHORTCUTS = "org.freedesktop.portal.GlobalShortcuts"
SHORTCUT_ID = "toggle"


class PortalBackend(ShortcutBackend):
    """
    Uses the freedesktop GlobalShortcuts portal. The desktop decides the
    final key: the requested shortcut is only a suggestion, and the user
    can change it in the desktop's own shortcut settings.
    """

    name = "Desktop portal"
    configurable = False

    def __init__(self, toggle_command, bridge):
        super().__init__(toggle_command, bridge)
        self.thread = None
        self.loop = None
        self.stop_event = None
        self.version = 0
        self.session = None

    @classmethod
    def available(cls):
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            return False

        try:
            import dbus_next  # noqa: F401
        except ImportError:
            return False

        return True

    def start(self, hotkey):
        ready = threading.Event()
        error = []

        self.thread = threading.Thread(
            target=self.run_loop,
            args=(hotkey, ready, error),
            name="gander-portal",
            daemon=True,
        )

        self.thread.start()

        if not ready.wait(5):
            self.stop()
            raise ShortcutError("The desktop portal did not respond.")

        if error:
            self.stop()
            raise ShortcutError(error[0])

        return hotkey

    def hint(self):
        if self.version >= 2:
            return "Use “Configure shortcut” to change it in your desktop."

        return "Change it in your desktop's keyboard shortcut settings."

    def stop(self):
        if self.loop and self.stop_event:
            try:
                self.loop.call_soon_threadsafe(self.stop_event.set)
            except RuntimeError:
                pass

        if self.thread:
            self.thread.join(2)

        self.thread = None
        self.loop = None
        self.session = None

    def open_settings(self):
        if not (self.loop and self.session and self.version >= 2):
            raise ShortcutError(self.hint())

        asyncio.run_coroutine_threadsafe(
            self.configure(),
            self.loop,
        ).result(5)

    # Worker thread --------------------------------------

    def run_loop(self, hotkey, ready, error):
        self.loop = asyncio.new_event_loop()

        try:
            self.loop.run_until_complete(
                self.main(hotkey, ready, error)
            )
        except Exception as exception:
            if not ready.is_set():
                error.append(str(exception))
                ready.set()
        finally:
            self.loop.close()

    async def main(self, hotkey, ready, error):
        from dbus_next import Message, MessageType, Variant
        from dbus_next.aio import MessageBus

        self.Message = Message
        self.MessageType = MessageType
        self.Variant = Variant

        self.stop_event = asyncio.Event()
        self.pending = {}

        self.bus = await MessageBus().connect()
        self.bus.add_message_handler(self.on_message)

        try:
            await self.setup(hotkey, ready)
        except Exception as exception:
            if not ready.is_set():
                error.append(str(exception))
                ready.set()
                return

            self.bridge.status.emit(f"Shortcut unavailable: {exception}")

        await self.stop_event.wait()

        if self.session:
            await self.call(
                self.session,
                "org.freedesktop.portal.Session",
                "Close",
            )

        self.bus.disconnect()

    async def setup(self, hotkey, ready):
        Variant = self.Variant

        # Tell the portal who we are (xdg-desktop-portal 1.19+). Needed by
        # KDE for unsandboxed apps; ignored by older portals.
        await self.call(
            PORTAL_PATH,
            "org.freedesktop.host.portal.Registry",
            "Register",
            "sa{sv}",
            [APP_ID, {}],
            check=False,
        )

        version = await self.call(
            PORTAL_PATH,
            "org.freedesktop.DBus.Properties",
            "Get",
            "ss",
            [PORTAL_SHORTCUTS, "version"],
        )

        self.version = version[0].value

        for rule in (
            "type='signal',interface='org.freedesktop.portal.Request',"
            "member='Response'",
            f"type='signal',interface='{PORTAL_SHORTCUTS}'",
        ):
            await self.bus.call(self.Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=[rule],
            ))

        results = await self.request(
            "CreateSession",
            "a{sv}",
            lambda token: [{
                "handle_token": Variant("s", token),
                "session_handle_token": Variant("s", self.token()),
            }],
        )

        self.session = results["session_handle"].value
        ready.set()

        self.bridge.status.emit("Waiting for the desktop to confirm…")

        results = await self.request(
            "BindShortcuts",
            "oa(sa{sv})sa{sv}",
            lambda token: [
                self.session,
                [[SHORTCUT_ID, {
                    "description": Variant("s", "Start or stop clicking"),
                    "preferred_trigger": Variant("s", hotkey.xdg_trigger()),
                }]],
                "",
                {"handle_token": Variant("s", token)},
            ],
            timeout=None,
        )

        shortcuts = results.get("shortcuts")
        self.report(shortcuts.value if shortcuts else [])

    async def configure(self):
        await self.call(
            PORTAL_PATH,
            PORTAL_SHORTCUTS,
            "ConfigureShortcuts",
            "osa{sv}",
            [self.session, "", {}],
        )

    def report(self, shortcuts):
        for shortcut_id, properties in shortcuts:
            if shortcut_id != SHORTCUT_ID:
                continue

            description = properties.get("trigger_description")

            if description and description.value:
                self.bridge.status.emit(description.value)
                return

        self.bridge.status.emit("Not assigned yet")

    @staticmethod
    def token():
        return "gander_" + uuid.uuid4().hex

    async def call(
        self,
        path,
        interface,
        member,
        signature="",
        body=None,
        check=True,
    ):
        reply = await self.bus.call(self.Message(
            destination=PORTAL_BUS,
            path=path,
            interface=interface,
            member=member,
            signature=signature,
            body=body or [],
        ))

        if check and reply.message_type == self.MessageType.ERROR:
            detail = reply.body[0] if reply.body else reply.error_name
            raise ShortcutError(detail)

        return reply.body

    async def request(self, member, signature, make_body, timeout=10):
        token = self.token()
        sender = self.bus.unique_name[1:].replace(".", "_")
        path = f"{PORTAL_PATH}/request/{sender}/{token}"

        future = asyncio.get_running_loop().create_future()
        self.pending[path] = future

        try:
            await self.call(
                PORTAL_PATH,
                PORTAL_SHORTCUTS,
                member,
                signature,
                make_body(token),
            )

            response, results = await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(path, None)

        if response == 1:
            raise ShortcutError("The shortcut request was cancelled.")

        if response != 0:
            raise ShortcutError("The desktop refused the shortcut.")

        return results

    def on_message(self, message):
        if message.message_type != self.MessageType.SIGNAL:
            return

        if (
            message.interface == "org.freedesktop.portal.Request"
            and message.member == "Response"
        ):
            future = self.pending.get(message.path)

            if future and not future.done():
                future.set_result((message.body[0], message.body[1]))

        elif message.interface == PORTAL_SHORTCUTS:
            if message.body[0] != self.session:
                return

            if (
                message.member == "Activated"
                and message.body[1] == SHORTCUT_ID
            ):
                self.bridge.activated.emit()

            elif message.member == "ShortcutsChanged":
                self.report(message.body[1])


# =====================================================
# 5. X11
# =====================================================

class XErrorEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("resourceid", ctypes.c_ulong),
        ("serial", ctypes.c_ulong),
        ("error_code", ctypes.c_ubyte),
        ("request_code", ctypes.c_ubyte),
        ("minor_code", ctypes.c_ubyte),
    ]


class XEvent(ctypes.Union):
    _fields_ = [
        ("type", ctypes.c_int),
        ("pad", ctypes.c_long * 24),
    ]


X_ERROR_HANDLER = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.POINTER(XErrorEvent),
)

X_KEY_PRESS = 2
X_KEY_RELEASE = 3
X_BAD_ACCESS = 10
X_GRAB_MODE_ASYNC = 1

# Grab with Caps Lock and Num Lock in any state.
X_LOCK_MASKS = (0, 1 << 1, 1 << 4, (1 << 1) | (1 << 4))


class X11Backend(ShortcutBackend):
    """Grabs the key on the X11 root window using libX11 (no extra deps)."""

    name = "X11"

    def __init__(self, toggle_command, bridge):
        super().__init__(toggle_command, bridge)
        self.thread = None
        self.stopping = threading.Event()

    @classmethod
    def available(cls):
        return is_x11_session() and ctypes.util.find_library("X11") is not None

    def start(self, hotkey):
        ready = threading.Event()
        error = []

        self.stopping.clear()

        self.thread = threading.Thread(
            target=self.listen,
            args=(hotkey, ready, error),
            name="gander-x11",
            daemon=True,
        )

        self.thread.start()
        ready.wait(5)

        if error or not ready.is_set():
            self.stop()

            if error and isinstance(error[0], ShortcutError):
                raise error[0]

            raise ShortcutError(error[0] if error else "X11 did not respond.")

        return hotkey

    def stop(self):
        self.stopping.set()

        if self.thread:
            self.thread.join(2)

        self.thread = None

    def listen(self, hotkey, ready, error):
        x = ctypes.CDLL(ctypes.util.find_library("X11"))

        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XStringToKeysym.restype = ctypes.c_ulong
        x.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XGrabKey.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong,
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ]
        x.XUngrabKey.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong,
        ]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x.XPending.argtypes = [ctypes.c_void_p]
        x.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(XEvent)]
        x.XConnectionNumber.argtypes = [ctypes.c_void_p]
        x.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x.XkbSetDetectableAutoRepeat.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
        ]

        display = x.XOpenDisplay(None)

        if not display:
            error.append("Cannot connect to the X11 display.")
            ready.set()
            return

        grab_failed = []

        def on_error(_display, event):
            if event.contents.error_code == X_BAD_ACCESS:
                grab_failed.append(True)
            return 0

        handler = X_ERROR_HANDLER(on_error)
        previous_handler = x.XSetErrorHandler(handler)

        root = x.XDefaultRootWindow(display)
        keysym = x.XStringToKeysym(hotkey.keysym().encode())
        keycode = x.XKeysymToKeycode(display, keysym) if keysym else 0
        mask = hotkey.x11_mask()

        try:
            if not keycode:
                error.append(
                    f"The key {hotkey.key} is not on the current keyboard layout."
                )
                return

            for lock in X_LOCK_MASKS:
                x.XGrabKey(
                    display, keycode, mask | lock, root, 0,
                    X_GRAB_MODE_ASYNC, X_GRAB_MODE_ASYNC,
                )

            x.XSync(display, 0)

            if grab_failed:
                error.append(ShortcutConflict(
                    f"{hotkey.text()} is already used by another application."
                ))
                return

            # Report key repeats as presses only, so holding the key
            # does not toggle repeatedly.
            x.XkbSetDetectableAutoRepeat(display, 1, None)

            ready.set()

            fd = x.XConnectionNumber(display)
            event = XEvent()
            held = False

            while not self.stopping.is_set():
                select.select([fd], [], [], 0.25)

                while x.XPending(display):
                    x.XNextEvent(display, ctypes.byref(event))

                    if event.type == X_KEY_PRESS and not held:
                        held = True
                        self.bridge.activated.emit()

                    elif event.type == X_KEY_RELEASE:
                        held = False

        finally:
            if keycode:
                for lock in X_LOCK_MASKS:
                    x.XUngrabKey(display, keycode, mask | lock, root)

            x.XSync(display, 0)
            x.XSetErrorHandler(previous_handler)
            x.XCloseDisplay(display)
            ready.set()


# =====================================================
# 6. EVDEV (any session, needs read access to keyboards)
# =====================================================

class EvdevBackend(ShortcutBackend):
    """
    Reads keyboard events straight from /dev/input. Works on every
    desktop, but requires read access to the keyboard devices, for
    example by adding the user to the `input` group.

    Keys are matched by their physical position (US layout names).
    """

    name = "Keyboard input"

    def __init__(self, toggle_command, bridge):
        super().__init__(toggle_command, bridge)
        self.thread = None
        self.stopping = threading.Event()

    @staticmethod
    def keyboards():
        from evdev import InputDevice, ecodes, list_devices

        devices = []

        for path in list_devices():
            try:
                device = InputDevice(path)
            except OSError:
                continue

            keys = device.capabilities().get(ecodes.EV_KEY, [])

            if (
                device.name != APP_NAME
                and ecodes.KEY_A in keys
                and ecodes.KEY_SPACE in keys
            ):
                devices.append(device)
            else:
                device.close()

        return devices

    @classmethod
    def available(cls):
        try:
            devices = cls.keyboards()
        except Exception:
            return False

        for device in devices:
            device.close()

        return bool(devices)

    def start(self, hotkey):
        from evdev import ecodes

        self.stopping.clear()

        self.thread = threading.Thread(
            target=self.listen,
            args=(hotkey, ecodes),
            name="gander-evdev",
            daemon=True,
        )

        self.thread.start()
        return hotkey

    def stop(self):
        self.stopping.set()

        if self.thread:
            self.thread.join(2)

        self.thread = None

    def listen(self, hotkey, ecodes):
        modifier_codes = {
            "Ctrl": {ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL},
            "Alt": {ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT},
            "Shift": {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT},
            "Meta": {ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA},
        }

        targets = hotkey.evdev_codes(ecodes)
        wanted = set(hotkey.modifiers)
        pressed = set()
        devices = {}
        rescan = 0

        try:
            while not self.stopping.is_set():
                # Pick up keyboards that were plugged in later.
                if rescan <= 0:
                    known = {device.path for device in devices.values()}

                    for device in self.keyboards():
                        if device.path in known:
                            device.close()
                        else:
                            devices[device.fd] = device

                    rescan = 20

                rescan -= 1

                readable, _, _ = select.select(list(devices), [], [], 0.25)

                for fd in readable:
                    device = devices[fd]

                    try:
                        events = list(device.read())
                    except OSError:
                        devices.pop(fd).close()
                        continue

                    for event in events:
                        if event.type != ecodes.EV_KEY:
                            continue

                        if event.value == 1:
                            pressed.add(event.code)
                        elif event.value == 0:
                            pressed.discard(event.code)

                        # value 2 is key repeat: ignore it.
                        if event.value != 1 or event.code not in targets:
                            continue

                        active = {
                            name
                            for name, codes in modifier_codes.items()
                            if pressed & codes
                        }

                        if active == wanted:
                            self.bridge.activated.emit()

        finally:
            for device in devices.values():
                device.close()


# =====================================================
# 7. MANUAL
# =====================================================

class ManualBackend(ShortcutBackend):
    """No automatic method available: the user binds the command."""

    name = "Manual"
    configurable = False

    @classmethod
    def available(cls):
        return True

    def start(self, hotkey):
        return None

    def hint(self):
        return (
            "No global shortcut method was found for this desktop. "
            "Bind this command to a key in your desktop settings:\n"
            f"{self.toggle_command}"
        )


# =====================================================
# SELECTION
# =====================================================

BACKENDS = (
    GnomeBackend,
    HyprlandBackend,
    SwayBackend,
    PortalBackend,
    X11Backend,
    EvdevBackend,
)


def start_shortcut(hotkey, toggle_command, on_activate, on_status):
    """
    Starts the first backend that works on this desktop.
    Returns (backend, active_hotkey, notes).
    """

    bridge = ActivationBridge()

    bridge.activated.connect(
        on_activate,
        Qt.ConnectionType.QueuedConnection,
    )

    bridge.status.connect(
        on_status,
        Qt.ConnectionType.QueuedConnection,
    )

    notes = []

    for backend_class in BACKENDS:
        # The portal only exists on Wayland desktops in practice; on X11
        # the direct grab is more reliable.
        if backend_class is PortalBackend and not is_wayland_session():
            continue

        try:
            if not backend_class.available():
                continue

            backend = backend_class(toggle_command, bridge)
            active = backend.start(hotkey)

        except ShortcutConflict as error:
            # This desktop is supported; the user just has to pick
            # another key, so don't fall back to a weaker method.
            notes.append(str(error))
            return backend, None, notes

        except Exception as error:
            notes.append(f"{backend_class.name}: {error}")
            continue

        return backend, active, notes

    backend = ManualBackend(toggle_command, bridge)
    return backend, None, notes
