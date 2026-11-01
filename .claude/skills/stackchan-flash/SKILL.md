---
name: stackchan-flash
description: Load any firmware onto the StackChan robot, back up its firmware, or roll it back to the pinned stock backup. Also checks what it is running. Use whenever the task involves writing to, restoring, dumping or identifying the robot's firmware, including esptool, usbipd, /dev/ttyACM0 or "el robot no arranca" after a flash.
---

# StackChan flash

Three operations, all through `scripts/stackchan_flash.py`. Do not call `esptool` or `idf.py flash` directly: the script holds the rules below.

```bash
S=.claude/skills/stackchan-flash/scripts/stackchan_flash.py

# Load a firmware (dry run unless --execute)
python3 $S flash --build path/to/build     # from flasher_args.json; keeps NVS
python3 $S flash --image app.bin --offset 0x20000
python3 $S flash --backup NAME             # a full backup; replaces all the flash

# Back up the firmware on the robot, about 9 minutes
python3 $S backup [--name PREFIX]

# Roll back: the same as flash --backup with the pinned baseline
python3 $S rollback

# Read-only helpers
python3 $S status
python3 $S list
python3 $S verify [--backup NAME | --image FILE]
```

The firmware does not have to be ours: anything with a `flasher_args.json`, a binary and an offset, or a backup works.

Backups always live in `.claude/skills/stackchan-flash/backups/<name>/` as `firmware.bin` and `manifest.json`. That location is fixed, and `backups/snapshots/` holds the state saved before each write.

## Workflow for writing

1. Run the dry run and show the user the plan.
2. Get explicit confirmation for this specific write. Earlier approval does not carry over.
3. Repeat with `--execute`. The script saves table, NVS and otadata to `backups/snapshots/` first.
4. Ask the user to press the robot's physical reset. Use `--reset` only when nobody is at the robot.
5. Confirm the boot by watching the screen or reading the serial log, and report the result.

Do not run an `--execute` to get past a `STOP:` message. Read it first. The `--allow-*` flags need the user's explicit decision, because each one accepts the loss it names.

## What this robot does that is not obvious

**USB and WSL**
- It is native USB-Serial/JTAG: `303a:1001`, `/dev/ttyACM0`. There is no CP210x or CH340 bridge, so `/dev/ttyUSB*` will never appear.
- To attach it, in an admin PowerShell: `usbipd list`, then `usbipd attach --wsl --busid <BUSID>`. The bus ID can change between ports.
- If it does not connect, enter download mode: hold RST (beside the microSD slot) about 3 s until the green LED lights, then release.
- Close any serial monitor first. Only one process can hold the port.

**Stock updates itself**
- Stock 1.4.4 with Wi-Fi saved checks for an update on boot and installs the newest version to `ota_1` by itself, then reboots. We saw 1.4.4 become 1.6.0 in about 4.5 minutes.
- A rollback is therefore only 1.4.4 until the first online boot. To compare against 1.4.4, keep its network away.
- Do not reset or use the port during that download. The screen shows that it is upgrading.
- If `status` shows an unexpected version after a rollback, this is the likely reason. It does not mean the rollback failed.

**Reading**
- A monolithic 16 MiB `read-flash` fails with `Packet content transfer stopped` near 1.38 MiB. The cause was never established.
- The working pattern is 16 runs of 1 MiB at 115200, with the stub for chunk 0 and `--no-stub` for chunks 1-15. `backup` does this.
- `backup` keeps the chip in the bootloader for the whole read and checks the dump against the flash before resetting. Firmware that runs between chunks can change NVS mid-dump.
- For checks after writing, use `verify-flash` per chunk with the stub. It does not hit this failure, and `--no-stub` exits immediately there.

**Writing**
- Never use `erase-flash`, `--erase-all` or `espefuse`.
- NVS (`0x9000`) holds Wi-Fi, MQTT tokens, `servo/zero_pos_*` and `phy/cal_*`, and exists only on this unit. Losing it means reconfiguring the robot and recalibrating the head.
- `flash --backup` replaces NVS with the one from the backup. That is intended, and it is limited to the MAC recorded in the backup.
- A merged image is dangerous even where it has no data, because the gaps are written too and they erase NVS. That applies to the M5Burner image and `merged-binary.bin`. Prefer `--build`.
- If you write an app without otadata and the bootloader selects the other slot, the write succeeds and the old firmware keeps booting. The script refuses this case. After a stock update the active slot is `ota_1`, so this does happen.
- Partition layouts differ between firmwares, so never reuse an offset from another project. Nous-Girl keeps assets at `0xB00000`, stock at `0xA00000`.

**Reset**
- A reset through esptool has hung the boot of stock firmware at least once. Another manual reset recovered it, and the cause is unknown.
- It is sporadic, not constant: full backups and custom-firmware flashes with `hard-reset` have also completed. So use `--after no-reset` between steps and the physical button to finish.
- After `no-reset` the robot stays in the bootloader and looks dead until it is reset. That is expected.

**Servos**
- Do not move the head by hand when you cannot tell whether the motors are powered, and keep the vertical axis within 5-85°.
- A bad NVS write resets the zero positions to their defaults.

**Data**
- `backups/` is gitignored and owner-only, because dumps and snapshots contain Wi-Fi passwords and tokens. Never commit them or paste their contents, and do not put MACs, IPs or SSIDs in tracked files.

## Baseline

`backups/baseline.json` names the backup that `rollback` loads. A load refuses a file with another hash or a device with another MAC.

The baseline is stock firmware 1.4.4. Factory images ship through M5Burner and do not track the public repo, so building from source does not recreate stock. The dump is the only exact way back.

To change the baseline, run `backup`, have the user confirm that the robot works in that state, then update `baseline.json`.

## Tests

```bash
python3 -m unittest discover -s .claude/skills/stackchan-flash/scripts
```
