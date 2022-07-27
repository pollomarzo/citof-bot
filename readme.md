# doorbell bot

citofono = doorbell so citof-bot.

we needed to open the gate at home from our phone. so we made a telegram bot

## outline

hardware was designed and completed by [dad](https://github.com/fmarzolo). so i had 2 pins of a raspberry: one was attached to the signal from the doorbell, the other opened it. respectively, they're used as a `Button` and as a `LED` respectively. the bot features plenty of useless features, offering a conversation to add responses

## setup

create a `token.txt` file with the bot token, in the same directory as the script. then run the script `python3 citofbot.py`. i think it needs su privileges for pins. there's a shell script if you like that better.
then set it up to run on boot. you can use `systemd` service, but i found crontab easier. `sudo crontab -e` -> add line `@reboot <PATH_TO_SCRIPT>/tgbotlauncher.sh > <PATH_TO_LOGS>/logs/citof-bot-log 2>&1` or any sensible variation of it and you're done

## config

the bot uses a `config.json` file to store enabled chats. dad chose that the easiest way to implement security was to make "enabling a chat == directly editing the config file". i agreed, since it's just vpn in local network + ssh into raspberry. so to add a new enabled chat, you call the `add_chat` command from the chat you want to add, then edit `config.json` to enable it. removing it is just calling `remove_chat`.

## personalized messages

a fallback message is defined as a constant somewhere around the top of `citofbot.py`. i wanted to play around with state machines and explore the library a little bit, so the bot takes you for a conversation if you input `change_responses`.

## languages

i switch back between english and italian sometimes. sorry. code is obv full english though :)

## technical notes

- i like my use of decorators to check if a chat is enabled before running the authed commands.
- editing the config file manually means that it must be checked before every operation. just in case, i added a `reload_conf` command.
- i didn't really process errors. they're logged at least, chat migration is handled, everything else is good luck
- bot initialization is wrapped in a retry loop. i was having a connection issue that went like this:
  - lights go out for some reason
  - a family member flips the switch back up
  - the DNS server + DHCP is handled by a local NAS, which takes a long time to boot up
  - the raspberry is up very quickly -> bot initialization fails
    i solved it by assigning a static IP, but left the retry loop in there anyway
- when you're adding a new response, the bot waits for a reply to its message. if it catches a message that's not a direct reply (needs to be admin in order to be able to read it), it tells you it's waiting for a reply, not just a message. i thought that was pretty cool
- i wrote this a while ago and had fun. didn't expect it to be the hands-down most-used personal project i wrote.
- i want to turn this into an app, so that i can just tap the app icon to open the gate, without having to: open telegram, find the correct telegram convo, click the command
