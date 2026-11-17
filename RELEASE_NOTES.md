Download `RazerBatteryIndicator.exe` and double-click it. It installs itself: shortcuts on the desktop and in the Start menu, autostart, and an entry in Settings > Apps > Installed apps for uninstalling. No admin rights needed. If you already have 1.1.0 or newer, the program offers this update itself in the right-click menu.

Скачайте `RazerBatteryIndicator.exe` и запустите двойным щелчком, программа установится сама. Если у вас уже стоит 1.1.0 или новее, она сама предложит обновиться в меню по правому клику.

Windows SmartScreen will warn because the file isn't signed with a paid certificate: "More info", then "Run anyway". / SmartScreen предупредит, что файл без платной подписи: «Подробнее», затем «Выполнить в любом случае».

What's new in 1.2.1:
- Fixed a crash right after an in-app update: the new version tried to delete the old exe while it was still closing.
- Unexpected startup errors go to %APPDATA%\RazerBatteryIndicator\error.log instead of a Python error window.

If 1.2.0 showed "Unhandled exception" after updating, just start it again from the desktop shortcut; the update itself went through.

Tested on Razer Barracuda X 2.4 only.
