# razer-battery-tray

Shows the battery level of wireless Razer headsets, mice and keyboards on the Windows taskbar, next to the clock. Razer Synapse is not needed.

I wrote it because Synapse 4 doesn't know my Barracuda X 2.4 at all (its log says `supported type: unknown`), and Windows only reports battery for Bluetooth devices. The program asks the USB dongle directly.

## What you get

A small entry per device left of the tray icons: a headset, mouse or keyboard icon and the percentage. The background is transparent, so it sits on whatever taskbar you have (dark, light, accent colour, acrylic, TranslucentTB). The number turns yellow below 35% and red below 20%, and a lightning icon blinks while the device charges.

Hover over it and a card opens with a bar per device, the charging state and an estimate of how long the battery will last. The estimate is learned from how fast your own device actually drains, so the first one appears after about half an hour on battery. After that it is remembered.

A warning pops up at 20% and again at 10%, and once more when a device finishes charging. If a game or video is fullscreen at that moment, the warning waits until you leave it. The widget itself hides during fullscreen apps and never takes focus away from them.

Left click refreshes. Right click opens the menu: refresh, taskbar or "notch" mode (a small black pill at the top of the screen), start with Windows, exit.

It polls once a minute and uses about 20 MB of RAM. CPU use is close to zero.

## Install

1. Download `RazerBatteryIndicator.exe` from [Releases](../../releases).
2. Run it. Windows SmartScreen may warn about an unknown publisher because the exe isn't signed: click "More info", then "Run anyway". Some antivirus programs also flag PyInstaller builds by mistake. If you don't trust the exe, run it from source (below), it's one Python file.
3. Right click the percentage and tick "Запускать с Windows" if you want it on every login.

Windows 10 or 11, no admin rights required. The interface is in Russian for now.

To remove it: untick "Запускать с Windows", close it from the menu, delete the exe and the folder `%APPDATA%\RazerBatteryIndicator`.

## Supported devices

The program reads each dongle's HID report descriptor and picks the protocol from what it finds:

| Protocol | Devices | Tested |
|---|---|---|
| "PA" frames, report 2 | Barracuda X 2.4 (`1532:0550`), BlackShark V2 Pro 2023, BlackShark V2 Pro for PlayStation | Barracuda X 2.4 only |
| "PA" frames, report 1 | Barracuda Pro | no |
| feature report `0xFF` | Barracuda X with the older dongle (`1532:0536`), Nari | no |
| classic 90-byte Razer report, class `07` | BlackShark V2 Pro 2020, wireless mice (Viper, DeathAdder, Basilisk, Naga, Cobra, Pro Click and others), wireless keyboards (BlackWidow V3 Pro, DeathStalker V2 Pro and others) | no |

I only own the Barracuda X 2.4, so everything else is written from protocol notes by other people and may need fixes. Wired mice and keyboards are skipped on purpose, because they answer the battery request with garbage.

If your device doesn't show up or shows nonsense, run `RazerBatteryIndicator.exe --once`. It saves what it found to `%APPDATA%\RazerBatteryIndicator\diagnostics.txt`. Open an issue with that file and your device's `VID:PID` (Device Manager, the device, Details, Hardware Ids).

## Questions

**Is the percentage exact?** It is whatever the device's firmware reports. The program doesn't calculate it.

**Does it keep my headset from turning off?** Not sure yet. To read the battery the program has to talk to the headset over the radio once a minute, and I'm still checking whether the Barracuda X counts that as activity and skips its auto power-off. This answer will be updated.

**Can it run next to Synapse?** Synapse 4 ignores the Barracuda X 2.4, so with that headset there's nothing to clash with. I haven't tried it alongside Synapse on devices Synapse does support.

## Run from source

Python 3.10 or newer:

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pythonw razer_battery.py
```

`python razer_battery.py --once` prints what it finds and exits.

To build the exe: `pip install pyinstaller`, then `pyinstaller --onefile --noconsole --name RazerBatteryIndicator razer_battery.py`.

## How it works

The program goes through every Razer HID collection (vendor id `1532`) whose name looks like a wireless headset, mouse or keyboard, parses its report descriptor and tries the matching protocol. Whatever answers gets cached for the next poll. For the Barracuda X 2.4 that is the vendor collection on usage page `0xFF00` with report id `0x02`: it switches the dongle into remote mode, asks for `0x21` (percent) and `0x2A` (charging), and switches remote mode back off.

The protocol details and the list of wireless mice come from [OpenRazer](https://github.com/openrazer/openrazer), [HaloBattery](https://github.com/Waturus/HaloBattery) and [Barracuda_X_Helpers](https://github.com/GigaVibeSlopper67/Barracuda_X_Helpers). Thanks to their authors.

Settings and the learned drain rate are stored in `%APPDATA%\RazerBatteryIndicator\config.json`.

Not affiliated with Razer.

## Коротко по-русски

Показывает заряд беспроводных наушников, мышей и клавиатур Razer на панели задач, без Synapse. Скачайте exe из Releases и запустите. Если SmartScreen ругается, нажмите «Подробнее», затем «Выполнить в любом случае». Автозапуск включается в меню по правому клику. Проверено только на Razer Barracuda X 2.4. Если ваше устройство не видно, запустите `RazerBatteryIndicator.exe --once` и приложите к issue файл `%APPDATA%\RazerBatteryIndicator\diagnostics.txt`.

## License

MIT
