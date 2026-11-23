# Flashing Echo Dots and Echo Shows for HomeHub

Two Amazon devices are supported, both through the upstream
[echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist) project (hassmic). After flashing, the Echo
is an ESPHome voice satellite that HomeHub adopts like any other ESPHome device.

| Device | Codename | Model on the label | USB access |
|---|---|---|---|
| Echo Dot 3rd gen (2018) | `donut` | **D9N29T** only | Solder 3 wires to test pads (or fit a USB-C port, below) |
| Echo Show 8 1st gen (2019) | `crown` | **C7H6N3** only | Its own micro-USB port, nothing to open |

Not supported, even though they look the same: the 2019-2020 Dot 3 refresh (C78MP8), the Dot 3 with clock
(36EBT3) and the Echo Show 8 2nd gen (A8H3N2).

## Read this first

- **The Dot 3 unlock can brick the Echo.** Upstream issue
  [#112](https://github.com/Gamer92000/echo-dot-assist/issues/112) (open): on some recently updated Dots the
  unlock finishes without an error and the Echo stays locked, with no known way back. Unlock **one** Dot first,
  and only one you can afford to lose, until that issue is closed.
- **Never let the Echo reach Amazon.** No Alexa app setup and no Wi-Fi with internet until the install has locked
  it down. An Amazon update can close the hole the unlock uses. Block the Echo's MAC at the router before you start.
- **The unlock itself is done by hand.** Its author asks that it is never run from a script. The helper below
  stops, points you to the XDA thread, and carries on once TWRP is up:
  [Dot 3 thread](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-dot-3rd-gen-2018-donut.4801400/),
  [Show 8 thread](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-show-8-1st-gen-2019-crown.4766687/).
- **Flash from a Linux PC running on the hardware**, not macOS, a VM or WSL (the unlock needs direct USB). A
  Linux live USB stick on any laptop works; keep the repository on a real drive. Show 8: about 7 GB free.

## The easy way

On the Linux PC, from a SharedLLM checkout:

```sh
scripts/echo_flash.sh donut      # Echo Dot 3
scripts/echo_flash.sh crown      # Echo Show 8 1st gen
```

It fetches echo-dot-assist at the pinned, tested commit, checks the tools, warns about #112 for a Dot, runs the
upstream guided setup (`scripts/setup.sh <device>`, which waits while you unlock by hand), and at the end prints
the Echo's IP address. Then adopt it: **Jarvis > Devices > Add device > ESPHome**, the Echo's IP, port **26053**,
no encryption key (HomeHub sets one). Give the Echo a DHCP reservation.

Doing it by hand instead: follow the upstream device page
([donut](https://github.com/Gamer92000/echo-dot-assist/blob/main/devices/donut/README.md),
[crown](https://github.com/Gamer92000/echo-dot-assist/blob/main/devices/crown/README.md)); everything it says about
Home Assistant applies to HomeHub's ESPHome integration.

## Echo Show 8 (crown): no soldering

1. A **USB-A to micro-USB data cable** (a charge-only cable will not show up in `adb`) and the Echo's own power
   supply.
2. Run `scripts/echo_flash.sh crown`. When TWRP asks "Keep System Read only?", tap **Keep Read Only**.
3. Optional: the setup offers Kiosk Satellite for the screen. Point it at Jarvis's `/display` page and leave the
   app's own voice assistant off, so hassmic keeps the microphone.

## Echo Dot 3 (donut): what to solder

The Dot 3 has no USB socket. Its USB data lines end on test pads on the main board. Open the case (the base comes
off under the rubber foot; the XDA thread links teardown guides), then connect:

| From | To pad on the Dot's board | Note |
|---|---|---|
| GND (black) | **TM3** | or any ground: the metal shield works |
| D- (white) | **TM4** | keep D- and D+ short (under 5 cm) and twisted together |
| D+ (green) | **TM5** | |
| VBUS / 5 V (red) | **nothing** | cut it back and insulate it; the Dot is powered by its own 12 V adapter |

That is all: three wires. The Dot always runs from its own 12 V / 1.25 A barrel adapter; USB is data only.

Pick one of two ways:

- **A. Temporary (one Echo, done once).** Cut a USB-A cable, solder its black, white and green wires to TM3, TM4,
  TM5, insulate the red one. After the install, updates go over Wi-Fi and the wires can come off. Use a USB-A
  plug at the PC end: a cut USB-C cable needs a 5.1 kOhm resistor from its CC wire to ground or the PC sees
  nothing.
- **B. Permanent USB-C port in the base (recommended for several Dots).** Solder the three wires from the
  breakout's **GND, D-, D+** pads to TM3, TM4, TM5. Leave the breakout's **VBUS pad unconnected**. Cut a slot in
  the bottom shell next to the barrel jack, outside the rubber foot so the Dot still sits flat, and glue the board
  in with epoxy (or two M2 screws). Tape the wires down with Kapton tape away from the speaker. Any USB-C cable
  then works, because the breakout carries the 5.1 kOhm CC resistors.

Solderless alternative for a one-off unlock: a 3D-printed pogo-pin holder for TM3/TM4/TM5
([Printables](https://www.printables.com/model/1904685-echo-dot-3-donut-pogo-pin-holder-for-tm3-tm4-tm5-s)).
Pressed-on contacts can slip mid-unlock; the bootrom still answers afterwards, so reseat and run it again.

### Parts list (per Dot, option B)

| Part | Qty | Example | Why this one |
|---|---|---|---|
| USB-C female breakout, device side, with 5.1 kOhm CC pull-downs | 1 | [Adafruit USB Type C Breakout Board, Downstream Connection (PID 4090)](https://www.amazon.com/Adafruit-Industries-Breakout-Downstream-Connection/dp/B0979M2WWD) | D+/D- pads broken out, CC resistors fitted, 20.4 x 14.2 x 5.0 mm |
| 30 AWG silicone (or Kynar) wire, 3 colors | ~15 cm | any | thin enough for the test pads |
| Kapton tape | a few cm | any | holds the wires away from the speaker |
| 2-part epoxy or 2x M2 screws | 1 | any | fixes the breakout in the base |
| USB-C to USB-A **data** cable | 1 | any | for the PC end |

Buying a cheaper multi-pack instead? Check the listing photos before you buy:
- it has **D+ and D- pads** (2-pin "power only" boards will not work),
- it has **two 5.1 kOhm resistors** ("512" printed on them) from CC1 and CC2 to GND. Many 6-pin boards only break
  CC1/CC2 out to pins with no resistor: add a 5.1 kOhm resistor from each to GND yourself, or always use a USB-A
  to USB-C cable (which needs none).

Tools: fine-tip soldering iron, flux, tweezers, precision driver set, a plastic pry tool.

## After flashing

- The Echo boots dark and silent, locks itself down to the local network, and is a satellite about a minute later.
- Keep `secrets/update.key` from the echo-dot-assist checkout: it signs later updates and opens adb over Wi-Fi
  (`scripts/adb-wifi.sh <echo-ip>`). Back it up encrypted, not in git.
- Updates later: `scripts/ota-push.sh <echo-ip>` from the echo-dot-assist checkout. No USB needed.
- Bluetooth speaker: on by default on the Echo's settings page (`http://<echo-ip>:28931/`); the "Bluetooth
  pairing" switch then shows up on the device in HomeHub.
