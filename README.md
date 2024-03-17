# Minecraft Auto-fishing Script on Wayland

Due to the couple between gstreamer and python interface, we won't use a virtual environment.

Currently for Linux/Wayland ONLY.

*inspired by: [Rob Dundas](https://medium.com/geekculture/lets-go-fishing-writing-a-minecraft-1-17-auto-fishing-bot-in-python-opencv-and-pyautogui-6bfb5d539fcf)*

Wayland version of [the old one](https://github.com/hyx0329/mc-auto-fishing)

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
    - pillow
    - pynput
    - gobject
    - dbus
    - maybe something else?

## Usage

1. Enter game & load world
1. find a fishing spot
1. maximize screen or switch to full screen
1. press esc to open menu
1. open a terminal to run the script with `python -m AutoFishing`
1. select the game window
1. refocus to the game window within 2 seconds
1. wait and adjust your pointing direction
    - goal: leave as much black wire near the cross pointer as possible
1. enjoy auto fishing

*You might need to do some adjustments, eg. right click once to make everything synchronized correctly.*


## Notes

### When `pipewiresrc` installed but not found

```
rm -rf ~/.cache/gstreamer-1.0
```