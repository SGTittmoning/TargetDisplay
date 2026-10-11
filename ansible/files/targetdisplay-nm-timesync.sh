#!/bin/sh
# Ziel: /etc/NetworkManager/dispatcher.d/50-targetdisplay-timesync
#
# Loest systemd-timesyncd sofort nach dem Netzwerk-Link-up neu aus.
#
# Hintergrund: timesyncd versucht beim Boot als Erstes zu synchronisieren,
# wenn das Netz noch gar nicht oben ist (Link-up und DHCP dauern einige
# Sekunden). Der Versuch scheitert, danach verdoppelt timesyncd das
# Wiederholungsintervall (30s, 60s, 120s, 240s, ...). Unter NetworkManager
# (statt systemd-networkd) bekommt timesyncd das spaetere "Netz ist da" nicht
# mit - die erste erfolgreiche Synchronisation kam im Test erst nach rund
# 7,5 Minuten, in der die Uhr (kein RTC) falsch ging. Ein Neustart des Dienstes
# beim "up"-Ereignis setzt den Backoff zurueck, der Server wird sofort befragt.
#
# NetworkManager ruft Dispatcher-Skripte mit $1 = Interface, $2 = Ereignis auf.

[ "$2" = "up" ] || exit 0
[ "$1" = "lo" ] && exit 0

systemctl try-restart systemd-timesyncd.service
exit 0
