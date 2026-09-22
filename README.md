
# Gander AutoClicker

Gander AutoClicker is a lightweight desktop autoclicker developed by **Gander Studio**.

The application is designed to provide simple, reliable mouse automation while keeping resource usage low. Development currently focuses on Linux, with plans to expand support to Windows and macOS.

## Current status

**Linux beta — active development.** (school just strated so updates are going to be slow)

The current Linux version includes:

- Configurable clicks per second (1–500 CPS)
- Left, right, and middle mouse button support
- Start/Stop controls
- Configurable global hotkey on GNOME
- Persistent settings for CPS and mouse button selection
- Direct Linux mouse input through `/dev/uinput`
- A custom interface based on the Gander Studio visual identity

### Linux compatibility

The current version is developed and tested on Arch Linux with GNOME and Wayland.

Mouse input is handled through a virtual Linux input device. Initial system configuration may be required to grant access to `/dev/uinput`.

Global shortcuts currently use GNOME's custom keyboard shortcut system. Support for other Linux desktop environments is planned for future development.

## Future plans

- Add native support for Windows and macOS
- Expand compatibility with other Linux desktop environments
- Improve the clicking backend for greater efficiency and timing consistency
- Further refine the Gander Studio interface and user experience
- Simplify installation and automate Linux input permission setup
- Improve the application architecture for cross-platform development

## Gander Studio

Gander AutoClicker is developed by [Gander Studio](https://ganderstudio.be/).