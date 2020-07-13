import json
import datetime
import time
import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters

CONF_FILE = './trial.json'
LOG_FILE = './log.txt'
TOKEN_FILE = './token.txt'

with open(TOKEN_FILE) as f:
    TOKEN = f.readline()

RING_NOTIFICATION = "SOMEONE'S AT THE DOOR! IS IT THE COPS? GO CHECK!"
# bad name, time to avoid re-ringing due to multiple signals
TIME_AVOID_RING = 10


def print_log(message):
    print(message)
    with open(LOG_FILE, 'a+') as f:
        f.write(str(message) + '\n')


class BotHandler:
    def __init__(self, alwaysupdate=True):
        try:
            f = open(CONF_FILE)
            self.conf = json.load(f)
            f.close()
        except:
            self.conf = {}
        print_log("---NEW SESSION---")
        print_log(datetime.datetime.now())
        self.lastring = 0

        self.updater = Updater(TOKEN, use_context=True)

        self.updater.dispatcher.add_handler(
            CommandHandler('start', self.addtoconf))
        self.updater.dispatcher.add_handler(
            CommandHandler('addchat', self.addtoconf))
        self.updater.dispatcher.add_handler(
            CommandHandler('removechat', self.removefromconf))
        self.updater.dispatcher.add_handler(
            CommandHandler('reload', self.reloadconf))
        self.updater.dispatcher.add_handler(
            CommandHandler('pingall', self.ping_all))

        self.updater.dispatcher.add_handler(
            MessageHandler(Filters.status_update.new_chat_members, self.addfromnewmember))

        self.updater.dispatcher.add_handler(
            MessageHandler(Filters.status_update.left_chat_member, self.exitleftchat))

        self.alwaysupdate = alwaysupdate

    def addtoconf(self, update, context):
        print_log(datetime.datetime.now())
        print_log("\tAdding new chat..")
        added = self.addChat(update.effective_chat.id,
                             update.effective_chat.title)
        print_log("\tDone!")
        if self.alwaysupdate and added:
            with open(CONF_FILE, 'w') as f:
                print_log("\t\tSaving to file..")
                json.dump(self.conf, f, indent=4)  # consider sort_keys=True
                print_log("\t\tSaved!")
        if added:
            update.message.reply_text(
                "added your chat. it'll have to be verified by a moderator before you're clear!")
        else:
            update.message.reply_text(
                "I already added your chat")

    def removefromconf(self, update, context):
        print_log(datetime.datetime.now())
        if self.conf is {}:
            update.message.reply_text(
                "i'm not sending updates to anyone right now...")
            print_log("No keys in dict")
            return
        print_log("\tRemoving chat...")
        removed = self.removeChat(update.effective_chat.id)
        print_log("\tDone!")
        if removed:
            update.message.reply_text(
                "removed this chat! you'll no longer receive notifications from me")
        else:
            update.message.reply_text(
                "chat not found. are you sure you know what you're doing?")

    def reloadconf(self, update, context):
        print_log(datetime.datetime.now())
        print_log("Received reload request")
        with open(CONF_FILE) as f:
            print_log(datetime.datetime.now())
            print_log("\tReloading conf file...")
            self.conf = json.load(f)
            print_log("\tReloaded!")
            update.message.reply_text("reloaded configuration file!")

    def relax(self):
        self.updater.start_polling()  # not sure if necessary
        self.updater.idle()

    def send_to_enabled(self, message=RING_NOTIFICATION):
        print_log("\tReceived signal...")
        # reload configuration file, just to make sure (in case someone
        # added a new chat and forgot to reload before the doorbell rang)
        with open(CONF_FILE) as f:
            print_log(datetime.datetime.now())
            print_log("\t\tReloading conf file...")
            self.conf = json.load(f)
            print_log("\t\tReloaded!")

        enabled = {}
        enabled = {key: value for (
            key, value) in self.conf.items() if value[1] == 1}

        if self.lastring + TIME_AVOID_RING < time.time():
            print_log("\tSENDINGNOTIFICATION!!")
            for chat in enabled.keys():
                self.updater.bot.send_message(chat, message)
            self.lastring = time.time()
        else:
            print_log("\tToo little time since last notification")

    def ping_all(self, update, context):
        self.send_to_enabled(message='PING!')
        update.message.reply_text("did you get pinged?")

    def addfromnewmember(self, update, context):
        if self.updater.bot.id in [member.id for member in update.message.new_chat_members]:
            print_log("Just got added to a chat")
            self.addtoconf(update, context)
            print_log("Done!")

    def exitleftchat(self, update, context):
        if update.message.left_chat_member.id == self.updater.bot.id:
            print_log("Received removal update")

            self.removefromconf(update, context)
    ####################### UTILS #############################################

    def addChat(self, chat_id, chat_name):
        added = False
        if chat_id not in self.conf.keys():
            self.conf[chat_id] = (chat_name, 0)
            added = True
            print_log("\t\tAdded new chat to conf")
        else:
            print_log("\t\tThe chat was already in conf")
        return added

    def removeChat(self, chat_id):
        removed = False
        if chat_id in self.conf.keys():
            self.conf.pop(chat_id, None)
            removed = True
        if removed and self.alwaysupdate:
            with open(CONF_FILE, 'w') as f:
                print_log("\t\tSaving to file..")
                # consider sort_keys=True
                json.dump(self.conf, f, indent=4)
                print_log("\t\tSaved!")
        return removed


if __name__ == '__main__':
    handler = BotHandler()
    handler.updater.start_polling()
    handler.updater.idle()
