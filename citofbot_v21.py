from telegram.ext import Application, CommandHandler
import os
from collections import namedtuple
from telegram.error import (TelegramError, BadRequest,
                            TimedOut, ChatMigrated, NetworkError)
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, CallbackQueryHandler
from telegram.constants import ParseMode
from gpiozero import Button
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, Update
from gpiozero import Button
from gpiozero import LED
import json
import datetime
import random
import time
import telegram
import traceback
import html

ENV_PROD = False


PIN_OPEN = 4
PIN_RING = 2
# will call this pin even though the second one is gpio

CURRENT_DIR = os.path.dirname(__file__)
# all relative to the SCRIPT, to avoid workdir hassle


class PATHS:
    TOKEN_FILE = './tokens.json'
    CONF_FILE = './config.json'
    LOG_FILE = './log.txt'
    DEL_FILE = './trash.txt'


with open(PATHS.TOKEN_FILE) as f:
    tokens = json.load(f)
    TOKEN = tokens["bot_token"]
    DEVELOPER_CHAT_ID = tokens["admin_chat_id"]

# responses and stuff
FIRST_RUN = "Yo! Just woke up. Do you need something?"
RING_PREFIX = '[cancello]'
DEFAULT_RING_NOTIFICATION = "SOMEONE'S AT THE DOOR! IS IT THE COPS? GO CHECK!"
OPEN_PREFIX = '[apro]'
DEFAULT_OPEN_NOTIFICATION = "WHO LET THE DOGS IN! WHO! WHO! whowho!"
# bad name, time to avoid re-ringing due to multiple signals
TIME_AVOID_RING = 10
TIME_AVOID_OPEN = 10
OPEN_TIME_SLEEP = 0.3


# responses for callback
RING = 'ring_notifications'
OPEN = 'open_notifications'
IGNORE = 'ignore'

CONFIRM = 'confirm'
QUIT = 'quit'

# per-convo attributes
LOCATION = 'location'
ACTION = 'action'
PAGE = 'page'
INDEX_TO_RESPONSE = 'indexToResponse'
RESPONSE_TO_INDEX = 'responseToIndex'
MAX_RESPONSE_SENT = 'maxResponseSent'
LAST_KNOWN_STATE = 'lastKnownState'
WARN_RESPONSE = 'warnResponse'

# json fields. might move to pandas dataframe but REALLY not worth the effort
ENABLED = 'enabled'
NAME = 'name'


def print_log(message):
    print(message)
    with open(PATHS.LOG_FILE, 'a+') as f:
        f.write(str(message) + '\n')


def save_state_factory(state):
    def save_state_wrap(func):
        def inner(self, update, context):
            context.user_data[LAST_KNOWN_STATE] = state
            print_log(f"saving last state {state}")
            return func(self, update, context)
        return inner
    return save_state_wrap


def check_enabled(func):
    async def inner(self, update, context):
        src = str(update.effective_chat.id)
        src_name = update.effective_chat.title or update.effective_chat.username
        if src in self.conf.keys() and self.conf[src][ENABLED] == 1:
            return await func(self, update, context)
        else:
            message = f"received unauthorized request from {src}({src_name})"
            print_log(message)
            await context.bot.send_message(
                chat_id=DEVELOPER_CHAT_ID, text=message, parse_mode=ParseMode.HTML)
    return inner


class BotHandler():
    def __init__(self, open_dev, ring_dev, alwaysupdate=True):
        try:
            f = open(PATHS.CONF_FILE)
            self.conf = json.load(f)
            f.close()
        except:
            self.conf = {}

        self.responses = {
            RING: [DEFAULT_RING_NOTIFICATION],
            OPEN: [DEFAULT_OPEN_NOTIFICATION]
        }

        print_log("---NEW SESSION---")
        print_log(datetime.datetime.now())
        # all alerts sent but not answered. used when someone answer and
        # everyone else sees the notification disappear
        self.pending_alerts = []
        # last time notification went out
        self.lastring = 0
        self.lastopen = 0
        self.open_dev = open_dev
        self.ring_dev = ring_dev
        self.reply_to_ring = [[InlineKeyboardButton("Apri", callback_data=OPEN),
                               InlineKeyboardButton("Ignora", callback_data=IGNORE)]]
        self.application = Application.builder().token(TOKEN).build()

        self.application.add_handler(
            CommandHandler('addchat', self.add_to_conf))
        self.application.add_handler(
            CommandHandler('removechat', self.remove_from_conf))
        self.application.add_handler(
            CommandHandler('reload', self.reload_settings))
        self.application.add_handler(
            CommandHandler('pingall', self.ping_all))

        self.application.add_handler(
            CommandHandler('open_gate', self.open_gate))
        self.application.add_handler(
            CallbackQueryHandler(self.process_response))
        self.application.add_error_handler(self.process_error)

        # set notification on signal received
        self.ring_dev.when_pressed = self.send_to_enabled

        self.alwaysupdate = alwaysupdate

    async def process_error(self, update, context):
        print_log(datetime.datetime.now())
        print_log(f"error raised!: {context.error}")
        # traceback.format_exception is list of strings.
        tb_list = traceback.format_exception(
            None, context.error, context.error.__traceback__)
        tb_string = "".join(tb_list)
        # Build the message with some markup and additional information about what happened.
        update_str = update.to_dict() if isinstance(update, Update) else str(update)
        formatted_for_telegram, formatted_for_logs = self.format_error(
            update_str, context, tb_string)
        print_log(
            f"updating developer with error details: {formatted_for_logs}")
        messages_list = [formatted_for_telegram[i:i+4000]
                         for i in range(0, len(formatted_for_telegram), 4000)]
        # Finally, send the message
        try:
            for i in messages_list:
                await context.bot.send_message(chat_id=DEVELOPER_CHAT_ID, text=i, parse_mode=ParseMode.HTML)
        except:
            print_log(
                "updating developer failed... network must be down. very sad! will not retry")
        try:
            raise context.error
        except BadRequest:
            # handle malformed requests - could be different things. no simple solution :/
            pass
        except TimedOut:
            # handle slow connection problems
            pass
        except NetworkError:
            # handle other connection problems. suuuure
            pass
        except ChatMigrated as e:
            # this is not needed. there's currently a bug: telegram does not
            # pass the old_chat_id, and the update currently being handled can
            # be the /pingall sender, so screw this i'll just come and fix it
            # myself if it happens
            # old_chat_id = str(update.effective_chat.id)
            # new_chat_id = str(e.new_chat_id)
            # old_chat_name = self.conf[old_chat_id][NAME]
            # self.removeChat(old_chat_id)
            # self.conf[new_chat_id] = {NAME: old_chat_name, ENABLED: 1}
            # self.update_file(PATHS.CONF_FILE, self.conf)
            # # the chat_id of a group has changed, use e.new_chat_id instead
            pass

        except TelegramError:
            # handle all other telegram related errors
            pass

    @check_enabled
    async def open_gate(self, update, context):
        print_log(datetime.datetime.now())
        print_log("\tReceived open request... Opening gate")
        if self.lastopen + TIME_AVOID_OPEN < time.time():
            print_log("opening gate...")
            self.open_dev.on()
            time.sleep(OPEN_TIME_SLEEP)
            self.open_dev.off()
            print_log("\tSignal sent. Is it open?")
            self.lastopen = time.time()
            answer_message = self.selectOpenedResponse()
            for message in self.pending_alerts:
                await self.application.bot.edit_message_text(
                    "Gate was opened", message.chat_id, message.message_id)
            self.pending_alerts.clear()
        else:
            print_log(
                f"\tReceived 2 requests within {TIME_AVOID_OPEN} seconds; ignoring...")
            answer_message = "It should still be open... relax"

        await self.application.bot.send_message(
            update.effective_chat.id, answer_message, disable_notification=True)

    async def send_to_enabled(self, message=None):
        print_log("\tReceived signal...")
        # reload configuration file, just to make sure (in case someone
        # added a new chat and forgot to reload before the doorbell rang)
        with open(PATHS.CONF_FILE) as f:
            print_log(datetime.datetime.now())
            print_log("\t\tReloading conf file...")
            self.conf = json.load(f)
            print_log("\t\tReloaded!")

        enabled = {}
        enabled = {key: value for
                   (key, value) in self.conf.items() if value[ENABLED] == 1}
        if message == None:
            message = self.selectRing()
        print(str(enabled))
        if self.lastring + TIME_AVOID_RING < time.time():
            print_log("\tSENDINGNOTIFICATION!!")
            for chat in enabled.keys():
                # send message and save to pending_alerts
                print(f"alerting chat {chat}, {enabled[chat]['name']}")
                final_message = await self.application.bot.send_message(
                    chat, message, reply_markup=InlineKeyboardMarkup(
                        self.reply_to_ring))
                self.pending_alerts.append(final_message)
            self.lastring = time.time()
        else:
            print_log("\tToo little time since last notification")

    @check_enabled
    async def process_response(self, update, context):
        query = update.callback_query
        print_log(datetime.datetime.now())
        print_log("Received response...")
        if query.data == OPEN:
            print_log("Request approved! Opening..")
            await self.open_gate(update, context)

        else:
            print_log("\tRequest ignored")

        await self.clean_query_remove_markup(update.callback_query)

    ###################### OTHER DIRECT COMMANDS HANDLERS ###########################################

    async def add_to_conf(self, update, context):
        print_log(datetime.datetime.now())
        print_log("\tAdding new chat..")
        name = update.effective_chat.title or update.effective_chat.username
        added = self.addChat(update.effective_chat.id,
                             name)
        print_log("\tDone!")
        if self.alwaysupdate and added:
            self.update_file(PATHS.CONF_FILE, self.conf)
        if added:
            await update.message.reply_text(
                "added your chat. it'll have to be verified by a moderator before you're clear!")
        else:
            await update.message.reply_text(
                "I already added your chat")

    async def remove_from_conf(self, update, context):
        print_log(datetime.datetime.now())
        if self.conf is {}:
            await update.message.reply_text(
                "i'm not sending updates to anyone right now...")
            print_log("No keys in dict")
            return
        print_log("\tRemoving chat...")
        removed = self.removeChat(update.effective_chat.id)
        print_log("\tDone!")
        if update.message is not None:
            if removed:
                await update.message.reply_text(
                    "removed this chat! you'll no longer receive notifications from me")
            else:
                await update.message.reply_text(
                    "chat not found. are you sure you know what you're doing?")

    async def reload_settings(self, update, context):
        print_log(datetime.datetime.now())
        print_log("Received reload request")

        with open(PATHS.CONF_FILE) as f:
            print_log(datetime.datetime.now())
            print_log(f"\tReloading {PATHS.CONF_FILE} file...")
            self.conf = json.load(f)
            print_log("\tReloaded!")

        await update.message.reply_text(f"reloaded configuration files!")

    @check_enabled
    async def ping_all(self, update: Update, context):
        print(
            f"pinging all because of message from {update.effective_chat.id}, {update.effective_chat.full_name}")
        await self.send_to_enabled(message='PING!')
        await update.message.reply_text("did you get pinged?")

    def start(self):
        print_log("Sending start message...")
        self.send_to_enabled(FIRST_RUN)
        print_log("\tSent start message.")
        self.application.run_polling()

    def addChat(self, chat_id, chat_name):
        chat_id = str(chat_id)
        added = False
        if chat_id not in self.conf:
            self.conf[chat_id] = {NAME: chat_name, ENABLED: 0}
            added = True
            print_log("\t\tAdded new chat to conf")
        else:
            print_log("\t\tThe chat was already in conf")
        return added

    def removeChat(self, chat_id):
        chat_id = str(chat_id)
        removed = False
        if chat_id in self.conf:
            self.conf.pop(chat_id)
            removed = True
            print_log("\t\tRemoved chat")
        else:
            print_log("\t\tChat wasn't in conf...")
        if removed and self.alwaysupdate:
            self.update_file(PATHS.CONF_FILE, self.conf)

        return removed

    def selectOpenedResponse(self):
        choice = random.choice(self.responses[OPEN])
        if choice != None:
            return OPEN_PREFIX + random.choice(self.responses[OPEN])
        else:
            return OPEN_PREFIX + DEFAULT_OPEN_NOTIFICATION

    def selectRing(self):
        choice = random.choice(self.responses[RING])
        if choice != None:
            return RING_PREFIX + random.choice(self.responses[RING])
        else:
            return RING_PREFIX + DEFAULT_RING_NOTIFICATION

    def update_file(self, file, obj):
        with open(file, 'w') as f:
            print_log("\t\tSaving to file..")
            # consider sort_keys=True
            json.dump(obj, f, indent=4)
            print_log("\t\tSaved!")

    async def clean_query_remove_markup(self, query):
        if (query != None):
            await query.answer()
            await query.edit_message_text(
                text="Selected option: {}".format(query.data))

    def format_error(self, update_str, context, tb_string):
        telegram_message = (
            "An exception was raised while handling an update\n"
            f"<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}"
            "</pre>\n\n"
            f"<pre>context.chat_data = {html.escape(str(context.chat_data))}</pre>\n\n"
            f"<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n"
            f"<pre>{html.escape(tb_string)}</pre>"
        )
        log_message = (
            "An exception was raised while handling an update\n"
            f"update = {json.dumps(update_str, indent=2, ensure_ascii=False)}"
            "\n"
            f"context.chat_data = {str(context.chat_data)}\n\n"
            f"context.user_data = {str(context.user_data)}\n\n"
            + tb_string
        )
        return telegram_message, log_message


class mock():
    def __init__(self):
        self.when_pressed = None

    def on(self):
        print("[MOCK GATE]I'm getting turned on...")

    def off(self):
        print("[MOCK GATE]turning off...")


MAX_CONN_ATTEMPT = 50
DELAY = 1

if __name__ == '__main__':
    if ENV_PROD:
        handler = BotHandler(LED(PIN_OPEN), Button(PIN_RING))
    else:
        handler = BotHandler(mock(), mock())
    # handler.send_to_enabled(FIRST_RUN)
    print_log("Robobibi initialized. Start polling messages...")
    for attempt in range(MAX_CONN_ATTEMPT):
        print_log(
            f"{datetime.datetime.now()}: attempt n.{attempt} of {MAX_CONN_ATTEMPT}")
        try:
            handler.start()
        except Exception as e:
            print_log(
                f"attempt n.{attempt} failed with error {e}. Will try again in {DELAY} seconds...")
            time.sleep(DELAY)
    print_log(
        f"Tried {MAX_CONN_ATTEMPT} times, over {MAX_CONN_ATTEMPT * DELAY} seconds; never worked. Giving up :(")
