---
name: stackchan-flash
description: Load any firmware onto the StackChan robot, back up its firmware, or roll it back to the pinned stock backup. Also checks what it is running. Use whenever the task involves writing to, restoring, dumping or identifying the robot's firmware, including esptool, usbipd, /dev/ttyACM0 or "el robot no arranca" after a flash.
---

# StackChan flash

The real skill is `.claude/skills/stackchan-flash/SKILL.md` at the repository root. Read that file now and follow it exactly before running anything.

Run its script by the path it gives, `.claude/skills/stackchan-flash/scripts/stackchan_flash.py`. Every write to the robot needs explicit user confirmation.

This entry holds no scripts or backups on purpose: `backups/` contains Wi-Fi passwords and tokens and must never be read, copied or published.
