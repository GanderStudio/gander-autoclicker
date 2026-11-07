
# Gander AutoClicker

Gander AutoClicker is a lightweight desktop autoclicker developed by **Gander Studio**.

The application is designed to provide simple, reliable mouse automation while keeping resource usage low. Development currently focuses on Linux, with plans to expand support to Windows and macOS.

## Current status

**Linux beta — active development.** (school just started so updates are going to be slow)

The current Linux version includes:

- Configurable clicks per second (1–500 CPS)
- Left, right, and middle mouse button support
- Start/Stop controls
- Configurable global hotkey on GNOME, KDE Plasma, Hyprland, Sway, X11 desktops and more
- Persistent settings for CPS and mouse button selection
- Direct Linux mouse input through `/dev/uinput`
- A custom interface based on the Gander Studio visual identity

### Linux compatibility

The current version is developed and tested on Arch Linux with GNOME and Wayland.

Mouse input is handled through a virtual Linux input device. Initial system configuration may be required to grant access to `/dev/uinput`.

Global shortcuts use the best method available on the current desktop, falling back to the next one automatically:

| Desktop | Method |
| --- | --- |
| GNOME (X11 and Wayland) | GNOME custom keyboard shortcut |
| Hyprland | Runtime bind through `hyprctl` (Lua and classic config), removed on exit |
| Sway | Runtime `bindsym` through `swaymsg`, removed on exit |
| KDE Plasma, COSMIC and other Wayland desktops | XDG Desktop Portal `GlobalShortcuts` (the desktop may ask you to confirm the shortcut and lets you change it in its own settings) |
| XFCE, Cinnamon, MATE, i3, Openbox and other X11 sessions | X11 key grab |
| Anything else | Direct keyboard input from `/dev/input`, if your user can read the keyboard devices (e.g. member of the `input` group) |

If none of these work, the app shows a command you can bind to a key in your desktop or window manager settings yourself:

```sh
/path/to/python /path/to/main.py --toggle
```

## Future plans

- Add native support for Windows and macOS
- Expand compatibility with other Linux desktop environments
- Improve the clicking backend for greater efficiency and timing consistency
- Further refine the Gander Studio interface and user experience
- Simplify installation and automate Linux input permission setup
- Improve the application architecture for cross-platform development

## Gander Studio

Gander AutoClicker is developed by [Gander Studio](https://ganderstudio.be/).
