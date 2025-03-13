import asyncio
import datetime
import html
import json
import os
import random
import signal
import sys
import time
import traceback

from gpiozero import LED, Button
from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import (
    BadRequest,
    ChatMigrated,
    NetworkError,
    TelegramError,
    TimedOut,
)
from telegram.ext import (
    Application,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
)

from utils import write_current_pid_in_file

ENV_PROD = False

if not ENV_PROD:
    write_current_pid_in_file()

MAX_LEN_TELEGRAM_MESSAGE = 3999
PIN_OPEN = 4
PIN_RING = 2
# will call this pin even though the second one is gpio

CURRENT_DIR = os.path.dirname(__file__)
# all relative to the SCRIPT, to avoid workdir hassle


class PATHS:
    TOKEN_FILE = "./tokens.json"
    CONF_FILE = "./config.json"
    LOG_FILE = "./log.txt"
    DEL_FILE = "./trash.txt"
    RESPONSE_FILE = "./responses.json"


with open(PATHS.TOKEN_FILE) as f:
    tokens = json.load(f)
    TOKEN = tokens["bot_token"]
    DEVELOPER_CHAT_ID = tokens["admin_chat_id"]

# responses and stuff
FIRST_RUN = "Yo! Just woke up. Do you need something?"
RING_PREFIX = "[cancello]"
DEFAULT_RING_NOTIFICATION = "SOMEONE'S AT THE DOOR! IS IT THE COPS? GO CHECK!"
OPEN_PREFIX = "[apro]"
DEFAULT_OPEN_NOTIFICATION = "WHO LET THE DOGS IN! WHO! WHO! whowho!"
# bad name, time to avoid re-ringing due to multiple signals
TIME_AVOID_RING = 15
TIME_AVOID_OPEN = 10
OPEN_TIME_SLEEP = 0.3


# responses for callback
RING = "ring_notifications"
OPEN = "open_notifications"
IGNORE = "ignore"

CONFIRM = "confirm"
QUIT = "quit"

# per-convo attributes
LOCATION = "location"
ACTION = "action"
PAGE = "page"
INDEX_TO_RESPONSE = "indexToResponse"
RESPONSE_TO_INDEX = "responseToIndex"
MAX_RESPONSE_SENT = "maxResponseSent"
LAST_KNOWN_STATE = "lastKnownState"
WARN_RESPONSE = "warnResponse"

# json fields. might move to pandas dataframe but REALLY not worth the effort
ENABLED = "enabled"
NAME = "name"


def print_log(message: str, level: int = 0, tag: str | int = None):
    if tag is not None:
        if isinstance(tag, Update):
            if tag.effective_chat.title is not None:
                chat_name = "group: " + tag.effective_chat.title
            elif tag.effective_chat.username is not None:
                chat_name = tag.effective_chat.username
            else:
                chat_name = "unknown"
        elif isinstance(tag, str):
            chat_name = tag
        message = f"[{chat_name}] {message}"
    message = str(datetime.datetime.now()) + " " + "\t" * level + message
    print(message)
    with open(PATHS.LOG_FILE, "a+") as f:
        f.write(str(message) + "\n")


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
                chat_id=DEVELOPER_CHAT_ID, text=message, parse_mode=ParseMode.HTML
            )

    return inner


class BotHandler:
    def __init__(self, open_dev, ring_dev, alwaysupdate=True):
        try:
            f = open(PATHS.CONF_FILE)
            self.conf = json.load(f)
            f.close()
        except:
            self.conf = {}

        try:
            f = open(PATHS.RESPONSE_FILE)
            self.responses = json.load(f)
            f.close()
        except:
            self.responses = {
                RING: [DEFAULT_RING_NOTIFICATION],
                OPEN: [DEFAULT_OPEN_NOTIFICATION],
            }

        print_log("---NEW SESSION---")
        # all alerts sent but not answered. used when someone answer and
        # everyone else sees the notification disappear
        self.pending_alerts: list[Message] = []
        # last time notification went out
        self.lastring = 0
        self.lastopen = 0
        self.open_dev = open_dev
        self.ring_dev = ring_dev
        self.reply_to_ring = [
            [
                InlineKeyboardButton("Apri", callback_data=OPEN),
                InlineKeyboardButton("Ignora", callback_data=IGNORE),
            ]
        ]
        self.application = (
            Application.builder()
            .token(TOKEN)
            .read_timeout(30)
            .write_timeout(30)
            .build()
        )
        self.lock = asyncio.Lock()

        self.application.add_handler(CommandHandler("addchat", self.add_to_conf))
        self.application.add_handler(
            CommandHandler("removechat", self.remove_from_conf)
        )
        self.application.add_handler(CommandHandler("reload", self.reload_settings))
        self.application.add_handler(CommandHandler("pingall", self.ping_all))

        self.application.add_handler(CommandHandler("open_gate", self.open_gate))
        self.application.add_handler(CallbackQueryHandler(self.process_response))
        self.application.add_error_handler(self.process_error)

        # set notification on signal received
        self.ring_dev.when_pressed = lambda: self.application.job_queue.run_once(
            self.handle_ring, 0
        )

        self.alwaysupdate = alwaysupdate

    async def process_error(self, update: Update, context: CallbackContext):
        print_log(f"error raised!: {context.error}")

        if (
            isinstance(context.error, BadRequest)
            and "Message is not modified" in context.error.message
        ):
            # thrown many times by Telegram, when two or more CallbackRequests are fired for the same message:
            # the first one successfully edits the message, while all the following ones try to edit it
            # but without making any changes -> telegram gets angry. I don't need to be notified of every
            # time it happens, and i don't want to pollute the logs too much
            print_log("Message was edited twice... whatever", 1)
            return

        # traceback.format_exception is list of strings.
        tb_list = traceback.format_exception(
            None, context.error, context.error.__traceback__
        )
        tb_string = "".join(tb_list)
        # Build the message with some markup and additional information about what happened.
        update_str = update.to_dict() if isinstance(update, Update) else str(update)
        formatted_for_telegram, formatted_for_logs = self.format_error(
            update_str, context, tb_string
        )

        print_log(
            f"updating developer with error details:\n*****\n{formatted_for_logs}\n****\n"
        )
        if len(formatted_for_telegram) > MAX_LEN_TELEGRAM_MESSAGE:
            # then pick the version without tags, because sending a message without a closing
            # tag makes telegram ANGRY
            formatted_for_telegram = formatted_for_logs
        messages_list = [
            formatted_for_telegram[i : i + 4000]
            for i in range(0, len(formatted_for_telegram), 4000)
        ]
        # Finally, send the message
        try:
            for i in messages_list:
                await context.bot.send_message(
                    chat_id=DEVELOPER_CHAT_ID, text=i, parse_mode=ParseMode.HTML
                )
        except Exception as e:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            print_log(
                "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
            )
            print_log(
                "updating developer failed... network must be down. very sad! will not retry"
            )
        try:
            raise context.error
        except BadRequest as e:
            # handle malformed requests - could be different things. no simple solution :/
            pass
        except TimedOut:
            # handle slow connection problems
            pass
        except NetworkError:
            # handle other connection problems. suuuure
            pass
        except ChatMigrated as e:
            pass

        except TelegramError:
            # handle all other telegram related errors
            pass

    @check_enabled
    async def open_gate(self, update: Update, context):
        print_log("Received request to open", 1, update)
        if self.lastopen + TIME_AVOID_OPEN < time.time():
            print_log("Last open time old enough, OPENING GATE...", 2, update)
            self.open_dev.on()
            time.sleep(OPEN_TIME_SLEEP)
            self.open_dev.off()
            print_log("Signal sent. Is it open?", 2)
            self.lastopen = time.time()
            answer_message = self.selectOpenedResponse()
            print_log("Clearing pending alerts...", 1)
            for message in self.pending_alerts:
                # check current content
                await self.application.bot.edit_message_text(
                    "Gate was opened", message.chat_id, message.message_id
                )
            self.pending_alerts.clear()
        else:
            print_log(
                f"Received 2 requests within {TIME_AVOID_OPEN} seconds; ignoring...", 2
            )
            answer_message = "It should still be open... relax"

        sent_message = await self.application.bot.send_message(
            update.effective_chat.id, answer_message, disable_notification=True
        )
        print_log(
            f"Request handling complete. Response message id={sent_message.message_id}, to {update.effective_chat.id}\n\n",
            1,
            update,
        )

    async def handle_ring(self, context: CallbackContext):
        # when the doorbell rings, the bot receives many, many requests. to be able to
        # distinguish their handling, i create a random int for each, to be attached to
        # all subsequent logs related to this request
        tag = str(random.randint(1, 100000))
        print_log(f"Picked up signal, waiting for lock...", 1, tag)
        async with self.lock:
            print_log("Lock acquired!...", 2, tag)
            print_log("Verifying...", 2, tag)
            if self.lastring + TIME_AVOID_RING < time.time():
                print_log("Last ring is old enough, alerting all chats...", 2)
                await self.send_to_enabled()
                self.lastring = time.time()
            else:
                print_log("Too little time since last notification", 2)
            print_log("Alert completed, back to idle...\n\n", 1, tag)

    async def send_to_enabled(self, message=None):
        enabled = {}
        enabled = {
            key: value for (key, value) in self.conf.items() if value[ENABLED] == 1
        }
        message = message or f"{self.selectRing()}"
        print_log(f"ALERTING enabled chats:{str(enabled)}", 2)
        for chat in enabled.keys():
            # send message and save to pending_alerts
            final_message = await self.application.bot.send_message(
                chat, message, reply_markup=InlineKeyboardMarkup(self.reply_to_ring)
            )
            self.pending_alerts.append(final_message)

    @check_enabled
    async def process_response(self, update, context):
        query = update.callback_query
        print_log("Received callback query...", 1)
        await self.clean_query_remove_markup(update.callback_query)

        if query.data == OPEN:
            print_log("... to open the gate...", 2)
            await self.open_gate(update, context)
        else:
            print_log("... to ignore. Ignored.", 2)

    ###################### OTHER DIRECT COMMANDS HANDLERS ###########################################

    async def add_to_conf(self, update, context):
        print_log("Adding new chat..", 1)
        name = update.effective_chat.title or update.effective_chat.username
        added = self.addChat(update.effective_chat.id, name)
        print_log("Done!", 1)
        if self.alwaysupdate and added:
            self.update_file(PATHS.CONF_FILE, self.conf)
        if added:
            await update.message.reply_text(
                "added your chat. it'll have to be verified by a moderator before you're clear!"
            )
        else:
            await update.message.reply_text("I already added your chat")

    async def remove_from_conf(self, update, context):
        if self.conf is {}:
            await update.message.reply_text(
                "i'm not sending updates to anyone right now..."
            )
            print_log("No keys in dict")
            return
        print_log("Removing chat...", 1)
        removed = self.removeChat(update.effective_chat.id)
        print_log("Done!", 1)
        if update.message is not None:
            if removed:
                await update.message.reply_text(
                    "removed this chat! you'll no longer receive notifications from me"
                )
            else:
                await update.message.reply_text(
                    "chat not found. are you sure you know what you're doing?"
                )

    async def reload_settings(self, update, context):
        print_log("Received reload request")

        with open(PATHS.CONF_FILE) as f:
            print_log(f"Reloading {PATHS.CONF_FILE} file...", 1)
            self.conf = json.load(f)
            print_log("Reloaded!", 1)

        await update.message.reply_text("reloaded configuration files!")

    @check_enabled
    async def ping_all(self, update: Update, context):
        print_log(
            f"pinging all because of message from {update.effective_chat.id}, {update.effective_chat.full_name}"
        )
        await self.send_to_enabled(message="PING!")
        await update.message.reply_text("did you get pinged?")

    async def first_message(self, context):
        print_log("Sending start message...")
        await self.send_to_enabled(FIRST_RUN)
        print_log("Sent start message.", 1)
        await context.bot.send_message(
            chat_id=DEVELOPER_CHAT_ID,
            text=f"Bot started. Since script start it's been {round(elapsed,3)} seconds.",
        )
        print_log("Admin updated", 1)

    def start(self):
        self.application.run_polling()

    def addChat(self, chat_id, chat_name):
        chat_id = str(chat_id)
        added = False
        if chat_id not in self.conf:
            self.conf[chat_id] = {NAME: chat_name, ENABLED: 0}
            added = True
            print_log("Added new chat to conf", 2, chat_name)
        else:
            print_log("The chat was already in conf", 2, chat_name)
        return added

    def removeChat(self, chat_id):
        chat_id = str(chat_id)
        removed = False
        if chat_id in self.conf:
            self.conf.pop(chat_id)
            removed = True
            print_log("Removed chat", 2)
        else:
            print_log("Chat wasn't in conf...", 2)
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
        with open(file, "w") as f:
            print_log("Saving to file..", 2)
            # consider sort_keys=True
            json.dump(obj, f, indent=4)
            print_log("Saved!", 2)

    async def clean_query_remove_markup(self, query: CallbackQuery):
        if query != None:
            await query.answer()
            await query.edit_message_text(text="Selected option: {}".format(query.data))
            # remove it from pending, it's been handled
            for message in self.pending_alerts:
                if message.message_id == query.message.message_id:
                    self.pending_alerts.remove(message)
                    break

    def format_error(self, update_str, context, tb_string):
        telegram_message = (
            "An exception was raised while handling an update\n"
            f"<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}</pre>\n\n"
            f"<pre>context.chat_data = {html.escape(str(context.chat_data))}</pre>\n\n"
            f"<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n"
            f"<pre>{html.escape(tb_string)}</pre>"
        )
        log_message = (
            "An exception was raised while handling an update\n"
            f"update = {json.dumps(update_str, indent=2, ensure_ascii=False)}\n"
            f"context.chat_data = {str(context.chat_data)}\n\n"
            f"context.user_data = {str(context.user_data)}\n\n" + tb_string
        )
        return telegram_message, log_message


class mock:
    def __init__(self):
        self.when_pressed = None

    def on(self):
        print("[MOCK GATE]I'm getting turned on...")

    def off(self):
        print("[MOCK GATE]turning off...")


if __name__ == "__main__":
    start_execution = time.time()
    if ENV_PROD:
        # unfortunately can't start it outside the try/catch, i need the
        # lock to be bound to this same event loop
        open_pin = LED(PIN_OPEN)
        ring_pin = Button(PIN_RING)
        handler = BotHandler(open_pin, ring_pin)
    else:
        handler = BotHandler(mock(), mock())
        signal.signal(
            signal.SIGUSR1,
            lambda signum, n: handler.application.job_queue.run_once(
                handler.handle_ring, 0
            ),
        )
    print_log("Robobibi initialized. attempting to connect...")
    elapsed = time.time() - start_execution
    handler.application.job_queue.run_once(handler.first_message, 0)
    handler.start()
    # if loop ends with no exception, a KeyboardInterrupt was used
    print_log("Assuming KeyboardInterrupt, exiting gracefully...")
    if ENV_PROD:
        print_log("Cleaning up GPIO ports...", 1)
        if open_pin != None:
            open_pin.close()
        if ring_pin != None:
            ring_pin.close()
