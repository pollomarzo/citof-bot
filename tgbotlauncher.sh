#!/bin/sh
# launcher.sh
# navigates to correct directory, launches python doorbell script, navigates back

cd /
cd home/pi/bot/citof-bot
sudo python citofbot_v21.py
cd
