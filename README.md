# Minecraft Auto-fishing Script on Wayland

Due to the couple between gstreamer and python interface, we won't use a virtual environment.

Currently for Linux/Wayland ONLY.

## Why this?

Using screenshot APIs provided by Wayland compositors is just SLOOOOOW. A few tweaks required. One of them is using screencast API.

## Prerequisites

- Arch Linux
- Wayland session & Pipewire
- `gst-plugin-pipewire`
- `xdg-desktop-portal`
    - `xdg-desktop-portal-gnome` (addtional for GNOME)
    - or other backends (refer [archwiki:XDG_Desktop_Portal](https://wiki.archlinux.org/title/XDG_Desktop_Portal))
- Python
    - numpy
    - pillow?
    - pynput
    - gobject
    - dbus


## Notes

### When `pipewiresrc` installed but not found

```
rm -rf ~/.cache/gstreamer-1.0
```