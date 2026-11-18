Download `RazerBatteryIndicator.exe` and double-click it. It installs itself: shortcuts on the desktop and in the Start menu, autostart, and an entry in Settings > Apps > Installed apps for uninstalling. No admin rights needed. If you already have 1.1.0 or newer, the program offers this update itself in the right-click menu.

Скачайте `RazerBatteryIndicator.exe` и запустите двойным щелчком, программа установится сама. Если у вас уже стоит 1.1.0 или новее, она сама предложит обновиться в меню по правому клику.

Windows SmartScreen will warn because the file isn't signed with a paid certificate: "More info", then "Run anyway". / SmartScreen предупредит, что файл без платной подписи: «Подробнее», затем «Выполнить в любом случае».

What's new in 1.3.0:
- `--once` now also lists every Razer HID interface it sees, with its VID:PID, the program version and the Windows build, so a device report has everything in one file.
- `--once --json` writes the levels as JSON to `%APPDATA%\RazerBatteryIndicator\diagnostics.json`, for scripts and Rainmeter.
- Screen readers and UI Automation tools can now read the level: it is in the widget's window title.
- Silent uninstall with `--uninstall --silent`, for winget.

Tested on Razer Barracuda X 2.4 only.
