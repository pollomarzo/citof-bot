import json
import datetime
import time
import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, ConversationHandler, CallbackQueryHandler
from gpiozero import Button
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from gpiozero import Button
from gpiozero import LED
import random
import os
from collections import namedtuple

PIN_OPEN = 1
PIN_RING = 2
# will call this pin even though the second one is gpio

CURRENT_DIR = os.path.dirname(__file__)
# all relative to the SCRIPT, to avoid workdir hassle
dirstruct = namedtuple(
    'dirstruct', 'TOKEN_FILE CONF_FILE RESPONSE_FILE LOG_FILE DEL_FILE')
PATHS = dirstruct(
    TOKEN_FILE='./token.txt',
    CONF_FILE='./config.json',
    RESPONSE_FILE='./responses.json',
    LOG_FILE='./log.txt',
    DEL_FILE='./trash.txt'
)

with open(PATHS.TOKEN_FILE) as f:
    TOKEN = f.readline()

CHANGE_NOTIF_RESPONSE = 'Che cosa vuoi cambiare?'
ADD_NOTIFICATION_RESPONSE = 'Per quale notifica vuoi aggiungere una frase?'
SHOW_NOTIFICATION_RESPONSE = 'Per che evento vuoi vedere le notifiche?'
RING_PREFIX = '[cancello]'
RING_NOTIFICATION_FALLBACK = "SOMEONE'S AT THE DOOR! IS IT THE COPS? GO CHECK!"
OPEN_PREFIX = '[apro]'
OPEN_NOTIFICATION_FALLBACK = "WHO LET THE DOGS IN! WHO! WHO! whowho!"
# bad name, time to avoid re-ringing due to multiple signals
TIME_AVOID_RING = 10
TIME_AVOID_OPEN = 10


# responses for callback
RING = 'ring_notifications'
OPEN = 'open_notifications'
IGNORE = 'ignore'

ADD = 'addResponse'
REMOVE = 'removeResponse'
SHOW = 'showAllResponses'
ADD_RING = 'addRingNotif'
ADD_OPEN = 'addOpenNotif'
DELETE_RING = 'deleteRingNotif'
DELETE_OPEN = 'deleteOpenNotif'
SHOW_OPEN = 'showOpen'
SHOW_RING = 'showRing'
GO_BACK = 'goBack'
NEXT = 'next'
PREV = 'previous'
QUIT = 'quit'

# action -> name in RESPONSES
action_to_names = {
    ADD_RING: 'ring_notifications',
    ADD_OPEN: 'open_notifications',
    DELETE_RING: 'ring_notifications',
    DELETE_OPEN: 'open_notifications'
}

# states in conversation handler
FIRST = '1'
SECOND = '2'
THIRD = '3'
FOURTH = '4'
FIFTH = '5'
SIXTH = '6'
SEVENTH = '7'

PAGE_SIZE = 5

# per-convo attributes
LOCATION = 'location'
ACTION = 'action'
PAGE = 'page'
INDEX_TO_RESPONSE = 'indexToResponse'
RESPONSE_TO_INDEX = 'responseToIndex'
MAX_RESPONSE_SENT = 'maxResponseSent'


def print_log(message):
    print(message)
    with open(PATHS.LOG_FILE, 'a+') as f:
        f.write(str(message) + '\n')


class BotHandler:
    def __init__(self, ring_dev, open_dev, alwaysupdate=True):
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
            self.responses = {}

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

        self.updater = Updater(TOKEN, use_context=True)

        self.updater.dispatcher.add_handler(
            CommandHandler('addchat', self.add_to_conf))
        self.updater.dispatcher.add_handler(
            CommandHandler('removechat', self.remove_from_conf))
        self.updater.dispatcher.add_handler(
            CommandHandler('reload', self.reload_settings))
        self.updater.dispatcher.add_handler(
            CommandHandler('pingall', self.ping_all))
        self.updater.dispatcher.add_handler(
            CommandHandler('open_gate', self.open_gate)
        )

        self.updater.dispatcher.add_handler(
            ConversationHandler(
                entry_points=[CommandHandler(
                    'change_responses', self.change_response)],
                states={
                    FIRST: [
                        CallbackQueryHandler(
                            self.ask_where_add, pattern='^' + ADD + '$'),
                        CallbackQueryHandler(
                            self.ask_where_remove, pattern='^' + REMOVE + '$'),
                        CallbackQueryHandler(
                            self.ask_where_show, pattern='^' + SHOW + '$')],
                    SECOND: [
                        CallbackQueryHandler(
                            self.ask_new_notif, pattern='^' + f"{ADD_RING}|{ADD_OPEN}" + '$'),
                        CallbackQueryHandler(
                            self.ask_remove_notif, pattern='^' + f"{DELETE_RING}|{DELETE_OPEN}" + '$'),
                        CallbackQueryHandler(
                            self.show_list, pattern='^' + f"{SHOW_OPEN}|{SHOW_RING}" + '$')],
                    THIRD: [
                        MessageHandler(
                            Filters.text & Filters.reply, self.add_notif)
                    ],
                    FOURTH: [
                        CallbackQueryHandler(
                            self.remove_notif, pattern='^' + f"{DELETE_OPEN}|{DELETE_RING}" + '$'),
                        CallbackQueryHandler(
                            self.change_response, pattern='^' + GO_BACK + '$')
                    ],
                    FIFTH: [
                        CallbackQueryHandler(
                            self.pick_remove_notif, pattern='^' + f"{NEXT}|{PREV}" + '$'),
                        CallbackQueryHandler(
                            self.abort_conversation, pattern='^' + QUIT + '$'),
                        MessageHandler(
                            Filters.text & Filters.reply, self.remove_selected_notif)
                    ]
                },
                fallbacks=[CommandHandler(
                    'abort', self.abort_conversation)],
                per_message=False

            )
        )
        self.updater.dispatcher.add_handler(
            MessageHandler(Filters.status_update.new_chat_members, self.addfromnewmember))
        self.updater.dispatcher.add_handler(
            MessageHandler(Filters.status_update.left_chat_member, self.exitleftchat))
        self.updater.dispatcher.add_handler(
            CallbackQueryHandler(self.process_response)
        )

        # set notification on signal received
        self.ring_dev.when_pressed = self.send_to_enabled

        self.alwaysupdate = alwaysupdate

    def change_response(self, update, context):
        print_log(datetime.datetime.now())
        print_log("\tReceived request to change response")

        self.updater.bot.send_message(
            update.effective_chat.id, CHANGE_NOTIF_RESPONSE, reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton('Aggiungi una risposta', callback_data=ADD),
                  InlineKeyboardButton('Rimuovi una risposta', callback_data=REMOVE)],
                 [InlineKeyboardButton('Mostra tutte le risposte', callback_data=SHOW)]]
            )
        )
        # Tell ConversationHandler that we're in state `FIRST` now
        return FIRST

    def open_gate(self, update, context):
        print_log(datetime.datetime.now())
        print_log("\tReceived open request... Opening gate")
        if self.lastopen + TIME_AVOID_OPEN > time.time():
            self.open_dev.on
            time.sleep(0.2)
            self.open_dev.off
            print_log("\tSent out signal. Is it open?")
            update.message.reply_text(self.selectOpen())
        else:
            print_log("\tDid not close yet.. aborting")
            update.message.reply_text(self.selectOpen())

    def ask_where_add(self, update, context):
        self.clean_query_remove_markup(update.callback_query)
        print_log(datetime.datetime.now())
        print_log("\tReceived request to add response")
        context.user_data['action'] = ADD

        self.updater.bot.send_message(
            update.effective_chat.id, ADD_NOTIFICATION_RESPONSE, reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton('Suona il cancello', callback_data=ADD_RING),
                  InlineKeyboardButton('Dare il tiro', callback_data=ADD_OPEN)]]
            ))
        return SECOND

    def ask_where_remove(self, update, context):
        self.clean_query_remove_markup(update.callback_query)
        print_log(datetime.datetime.now())
        print_log("\tReceived request to remove response")
        context.user_data['action'] = REMOVE

        self.updater.bot.send_message(
            update.effective_chat.id, ADD_NOTIFICATION_RESPONSE, reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton('Suona il cancello', callback_data=DELETE_RING),
                  InlineKeyboardButton('Dare il tiro', callback_data=DELETE_OPEN)]]
            ))
        return SECOND

    def ask_where_show(self, update, context):
        print_log("\tReceived request to show responses. For what?")
        self.clean_query_remove_markup(update.callback_query)
        context.user_data['page'] = 0
        context.user_data['action'] = SHOW
        self.updater.bot.send_message(
            update.effective_chat.id, SHOW_NOTIFICATION_RESPONSE, reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton('Suona il cancello', callback_data=SHOW_RING),
                  InlineKeyboardButton('Dare il tiro', callback_data=SHOW_OPEN)]]
            ))
        return SECOND

    def add_to_conf(self, update, context):
        print_log(datetime.datetime.now())
        print_log("\tAdding new chat..")
        added = self.addChat(update.effective_chat.id,
                             update.effective_chat.title)
        print_log("\tDone!")
        if self.alwaysupdate and added:
            self.update_file(PATHS.CONF_FILE, self.conf)
        if added:
            update.message.reply_text(
                "added your chat. it'll have to be verified by a moderator before you're clear!")
        else:
            update.message.reply_text(
                "I already added your chat")

    def remove_from_conf(self, update, context):
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

    def reload_settings(self, update, context):
        print_log(datetime.datetime.now())
        print_log("Received reload request")
        settings = [self.conf, self.responses]
        for i, e in enumerate([PATHS.CONF_FILE, PATHS.RESPONSE_FILE]):
            with open(e) as f:
                print_log(datetime.datetime.now())
                print_log("\tReloading conf file...")
                settings[i] = json.load(f)
                print_log("\tReloaded!")
                update.message.reply_text("reloaded configuration file!")

    def relax(self):
        self.updater.start_polling()  # not sure if necessary
        self.updater.idle()

    def process_response(self, update, context):
        self.clean_query_remove_markup(update.callback_query)
        query = update.callback_query
        print_log(datetime.datetime.now())
        print_log("Received response...")
        if query.data == OPEN:
            print_log("Request approved! Opening..")
            self.open_gate(update, context)

            # ok, the next part is a bit of a hack: because i used send_message
            # to alert every chat, i don't have an update or a context, but only
            # the list of messages i sent. at the same time, i want to function
            # that opens the gate to be a callback functions (have update and
            # context as parameters), so that it can be called directly with a
            # command. so, i switch out the message in the original update,
            # because i know that's the only part open_gate will use
            for i in self.pending_alerts:
                update.message = i
                self.open_gate(update, context)
        else:
            print_log("\tRequest ignored")

    def send_to_enabled(self, message=None):
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
                   (key, value) in self.conf.items() if value[1] == 1}
        if message == None:
            message = self.selectRing()
        if self.lastring + TIME_AVOID_RING < time.time():
            print_log("\tSENDINGNOTIFICATION!!")
            for chat in enabled.keys():
                # send message and save to pending_alerts
                self.pending_alerts.append(self.updater.bot.send_message(
                    chat, message, reply_markup=InlineKeyboardMarkup(
                        self.reply_to_ring)))
            self.lastring = time.time()
        else:
            print_log("\tToo little time since last notification")

    def ping_all(self, update, context):
        self.send_to_enabled(message='PING!')
        update.message.reply_text("did you get pinged?")

    def addfromnewmember(self, update, context):
        if self.updater.bot.id in [member.id for member in update.message.new_chat_members]:
            print_log("Just got added to a chat")
            self.add_to_conf(update, context)
            print_log("Done!")

    def exitleftchat(self, update, context):
        if update.message.left_chat_member.id == self.updater.bot.id:
            print_log("Received removal update")

            self.remove_from_conf(update, context)

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
            self.update_file(PATHS.CONF_FILE, self.conf)

        return removed

    def selectOpen(self):
        choice = random.choice(self.responses[OPEN])
        if choice != None:
            return OPEN_PREFIX + random.choice(self.responses[OPEN])
        else:
            return OPEN_PREFIX + OPEN_NOTIFICATION_FALLBACK

    def selectRing(self):
        choice = random.choice(self.responses[RING])
        if choice != None:
            return RING_PREFIX + random.choice(self.responses[RING])
        else:
            return RING_PREFIX + RING_NOTIFICATION_FALLBACK

    def ask_new_notif(self, update, context):
        print_log(datetime.datetime.now())
        self.clean_query_remove_markup(update.callback_query)
        # ADD_RING o ADD_OPEN
        context.user_data['action'] = update.callback_query.data
        print_log(
            f"\tReceived request to add response to {update.callback_query.data}")

        self.updater.bot.send_message(update.effective_chat.id,
                                      "Ok! Answer to this message with your new notification. Be aware, I'll prepend a "
                                      "prefix just to make sure it's easy to understand quickly what you're saying")
        return THIRD

    def ask_remove_notif(self, update, context):
        self.clean_query_remove_markup(update.callback_query)
        print_log("\tReceived request to remove response.")

        self.updater.bot.send_message(update.effective_chat.id,
                                      "Ok! Which type?", reply_markup=InlineKeyboardMarkup(
                                          [[InlineKeyboardButton('Ring Notification', callback_data=DELETE_OPEN),
                                            InlineKeyboardButton('Open Notification', callback_data=DELETE_RING)],
                                              [InlineKeyboardButton('I changed my mind', callback_data=GO_BACK)]]))
        # TODO: fuck
        return FOURTH

    def show_list(self, update, context):
        self.clean_query_remove_markup(update.callback_query)
        if update.callback_query.data == SHOW_OPEN:
            context.user_data['location'] = OPEN
        else:
            context.user_data['location'] = RING

        self.index_responses(context)
        self.pick_remove_notif(update, context)

    def add_notif(self, update, context):
        if update.message.reply_to_message.from_user.id == self.updater.bot.id:
            requested_action = context.user_data['action']
            self.responses[action_to_names[requested_action]].append(
                update.message.text)
            update.message.reply_text(f"Your notification has been added!")
            print_log(datetime.datetime.now())
            print_log(
                f"\tAdded new response to {requested_action}, updating file...")
            self.update_file(PATHS.RESPONSE_FILE, self.responses)
            return ConversationHandler.END
        else:
            print_log("FUCKFUCK")
            return THIRD

    def remove_notif(self, update, context):
        self.clean_query_remove_markup(update.callback_query)
        print_log(
            f"\tReceived request to remove response from {update.callback_query}. Presenting choices...")
        if update.callback_query.data == DELETE_OPEN:
            context.user_data['location'] = OPEN
        else:
            context.user_data['location'] = RING
        self.updater.bot.send_message(update.effective_chat.id, "Please answer to the next message the"
                                      "number of the entry you would like to delete")

        self.index_responses(context)
        self.pick_remove_notif(update, context)

    def pick_remove_notif(self, update, context):
        print_log("\tAsking user to select option...")
        # TODO: switch to KeyboardMarkup at leaaast
        if update.callback_query.data == NEXT:
            # only change page if there are more entries to show
            if context.user_data[PAGE] * PAGE_SIZE < len(context.user_data[INDEX_TO_RESPONSE]):
                context.user_data[PAGE] += 1

        elif update.callback_query.data == PREV:
            # only change page if there are entries before this page
            if context.user_data[PAGE] > 0:
                context.user_data[PAGE] -= 1

        selected_type = context.user_data[LOCATION]

        start_list = context.user_data[PAGE] * PAGE_SIZE
        # TODO: might want to use the context dict we just created
        selected_entries = self.responses[selected_type][start_list: start_list + PAGE_SIZE]
        entries_list = [
            f"{context.user_data[RESPONSE_TO_INDEX][i]}. {i}" for i in selected_entries]

        message_text = '\n'.join(entries_list)
        print_log(f"OPTIONS SENT: \n{message_text}")
        self.updater.bot.send_message(update.effective_chat.id,
                                      message_text, reply_markup=InlineKeyboardMarkup(
                                          [[InlineKeyboardButton(NEXT, callback_data=NEXT),
                                            InlineKeyboardButton(PREV, callback_data=PREV)],
                                           [InlineKeyboardButton(QUIT, callback_data=QUIT)]]))
        return FIFTH

    def remove_selected_notif(self, update, context):
        self.clean_query_remove_markup(update.callback_query)
        if context.user_data[ACTION] != REMOVE:
            # we got in here "by mistake": we misinterpreted
            # a random response for the user's pick on what
            # to remove, but he was just reading the responses.
            # So, we ignore it
            return FIFTH
        if update.message.reply_to_message.from_user.id == self.updater.bot.id:
            print_log(
                f"\tReceived request to remove entry {update.message.text}")
            if str.isdigit(update.message.text):
                removed_response = context.user_data[INDEX_TO_RESPONSE].pop(
                    int(update.message.text))
                # i'm curious what they'll try
                with open(PATHS.DEL_FILE, 'a+') as f:
                    f.write(f"{str(removed_response)}\n")

                self.responses[LOCATION].remove(removed_response)
                print_log(f"\tRemoved {removed_response}")
                self.update_file(PATHS.RESPONSE_FILE, self.responses)

                update.message.reply_text(
                    "I removed what you asked. Hope it wasn't offensive!\nBye!")

            return ConversationHandler.END

    def update_file(self, file, obj):
        with open(file, 'w') as f:
            print_log("\t\tSaving to file..")
            # consider sort_keys=True
            json.dump(obj, f, indent=4)
            print_log("\t\tSaved!")

    def clean_query_remove_markup(self, query):
        query.answer()
        query.edit_message_text(
            text="Selected option: {}".format(query.data))

    def abort_conversation(self, update, context):
        print_log(datetime.datetime.now())
        print_log("Aborted conversation")
        update.message.reply_text("Sorry about the misunderstanding.")
        return ConversationHandler.END

    def index_responses(self, context):
        # i considered the inclusion of a bidirectional dict.
        # then i decided it wasn't worth the effort
        loc = context.user_data[LOCATION]
        response_dict = self.responses[loc]
        context.user_data[INDEX_TO_RESPONSE] = {
            i: response_dict[i] for i in range(0, len(response_dict))}
        context.user_data[RESPONSE_TO_INDEX] = {
            v: k for k, v in context.user_data[INDEX_TO_RESPONSE].items()
        }


class stupid():
    def __init__(self):
        self.when_pressed = None


if __name__ == '__main__':
    # handler = BotHandler(LED(PIN_OPEN), Button(PIN_RING))
    handler = BotHandler(stupid(), stupid())
    handler.relax()
