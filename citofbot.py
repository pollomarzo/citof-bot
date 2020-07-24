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
from telegram.error import (TelegramError, Unauthorized, BadRequest, 
                            TimedOut, ChatMigrated, NetworkError)
# import logging
# logging.basicConfig(level=logging.DEBUG,
#                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

PIN_OPEN = 4
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

WELCOME_TO_CHANGE = 'Hi! So you want to change something about the responses?'
CHANGE_NOTIF_RESPONSE = 'Che cosa vuoi cambiare?'
ADD_NOTIFICATION_RESPONSE = 'Per quale notifica vuoi aggiungere una frase?'
REMOVE_NOTIFICATION_RESPONSE = 'Per che evento vuoi eliminare una notifica?'
SHOW_NOTIFICATION_RESPONSE = 'Per che evento vuoi vedere le notifiche?'
RING_PREFIX = '[cancello]'
RING_NOTIFICATION_FALLBACK = "SOMEONE'S AT THE DOOR! IS IT THE COPS? GO CHECK!"
OPEN_PREFIX = '[apro]'
OPEN_NOTIFICATION_FALLBACK = "WHO LET THE DOGS IN! WHO! WHO! whowho!"
WARN_ADD_ANSWER = ("Hey! I'll only say this once. I'll be waiting for a reply, "
                   "not any normal message. I want to make sure I don't catch the wrong message. "
                   "If you've changed your mind, just tap 'quit'!")
# bad name, time to avoid re-ringing due to multiple signals
TIME_AVOID_RING = 10
TIME_AVOID_OPEN = 10
OPEN_TIME_SLEEP = 0.3


# responses for callback
RING = 'ring_notifications'
OPEN = 'open_notifications'
IGNORE = 'ignore'

CONFIRM = 'confirm'
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
ZERO = '0'
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
LAST_KNOWN_STATE = 'lastKnownState'
WARN_RESPONSE = 'warnResponse'


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
                RING: [],
                OPEN: []
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
            ConversationHandler(
                entry_points=[CommandHandler(
                    'change_responses', self.enter_change_convo)],
                states={
                    ZERO: [
                        CallbackQueryHandler(
                            self.change_response, pattern='^' + CONFIRM + '$')],
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
                            self.show_list, pattern='^' + f"{SHOW_OPEN}|{SHOW_RING}" + '$')],
                    THIRD: [
                        MessageHandler(Filters.reply, self.add_notif),
                        MessageHandler(Filters.all, self.warn_about_answer)
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
                        MessageHandler(
                            Filters.text, self.remove_selected_notif)
                    ]
                },
                fallbacks=[CallbackQueryHandler(
                    self.abort_conversation),  # pattern='^' + QUIT + '$') omitted, as
                    # any other uncollected query should be collected too
                    CommandHandler(
                    'abort', self.abort_conversation),
                    MessageHandler(Filters.update, self.unclear_input)],
                per_message=False
            )
        )
        self.updater.dispatcher.add_handler(
            MessageHandler(Filters.status_update.new_chat_members, self.addfromnewmember))
        self.updater.dispatcher.add_handler(
            MessageHandler(Filters.status_update.left_chat_member, self.exitleftchat))
        self.updater.dispatcher.add_handler(
            CommandHandler('open_gate', self.open_gate))
        self.updater.dispatcher.add_handler(
            CallbackQueryHandler(self.process_response)
        )
        self.updater.dispatcher.add_error_handler(self.process_error)

        # set notification on signal received
        self.ring_dev.when_pressed = self.send_to_enabled

        self.alwaysupdate = alwaysupdate

    
    def process_error(self, update, context):
        print_log(datetime.datetime.now())
        print_log(f"error raised!: {context.error}")
        try:
            raise context.error
        except Unauthorized:
            # remove update.message.chat_id from conversation list
            self.remove_from_conf(update,context)
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
            print_log("\thandling chat migration,,")
            old_chat_id = update.effective_chat.id
            new_chat_id = e.new_chat_id
            old_chat_name, _ = self.conf[old_chat_id]
            self.removeChat(old_chat_id)
            self.conf[new_chat_id] = (old_chat_name, 1)
            self.update_file(PATHS.CONF_FILE, self.conf)
            # the chat_id of a group has changed, use e.new_chat_id instead
        except TelegramError:
            # handle all other telegram related errors
            pass
    
    ###################### I/O HANDLERS #############################################################

    def open_gate(self, update, context):
        print_log(datetime.datetime.now())
        print_log("\tReceived open request... Opening gate")
        if self.lastopen + TIME_AVOID_OPEN < time.time():
            print_log("opening gate...")
            self.open_dev.on()
            time.sleep(OPEN_TIME_SLEEP)
            self.open_dev.off()
            print_log("\tSent out signal. Is it open?")
            update.message.reply_text(self.selectOpen())
        else:
            print_log("\tDid not close yet.. aborting")
            update.message.reply_text(self.selectOpen())
    
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
                final_message = self.updater.bot.send_message(
                    chat, message, reply_markup=InlineKeyboardMarkup(
                        self.reply_to_ring))
                self.pending_alerts.append(final_message)
            self.lastring = time.time()
        else:
            print_log("\tToo little time since last notification")

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

    ###################### OTHER DIRECT COMMANDS HANDLERS ###########################################

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
        if update.message is not None:
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

    def ping_all(self, update, context):
        self.send_to_enabled(message='PING!')
        update.message.reply_text("did you get pinged?")

    def addfromnewmember(self, update, context):
        print("in addfromnewmember")
        if self.updater.bot.id in [member.id for member in update.message.new_chat_members]:
            print_log("Just got added to a chat")
            self.add_to_conf(update, context)
            print_log("Done!")
    
    def exitleftchat(self, update, context):
        print("in exitleftchat")
        if update.message.left_chat_member.id == self.updater.bot.id:
            print_log("Received removal update")

            self.remove_from_conf(update, context)

    ####################### CONVERSATION HANDLERS ###################################################

    def enter_change_convo(self, update, context):
        print_log("\tEntering change response dialog...")
        self.updater.bot.send_message(
            update.effective_chat.id, WELCOME_TO_CHANGE, reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton('Yes, I do', callback_data=CONFIRM),
                  InlineKeyboardButton("Nope, I don't", callback_data=QUIT)]]
            )
        )
        return ZERO
    
    @save_state_factory(ZERO)
    def abort_conversation(self, update, context):
        self.clean_query_remove_markup(update.callback_query)
        print_log(datetime.datetime.now())
        print_log("Aborted conversation")
        self.updater.bot.send_message(
            update.effective_chat.id, "Sorry about the misunderstanding.")
        return ConversationHandler.END
        
    def unclear_input(self, update, context):
        print_log(
            f"\tReceived unclear input {update.message.text}, going back to last know state {context.user_data[LAST_KNOWN_STATE]}")
        return context.user_data[LAST_KNOWN_STATE]

    @save_state_factory(ZERO)
    def change_response(self, update, context):
        self.clean_query_remove_markup(update.callback_query)
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

    @save_state_factory(FIRST)
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

    @save_state_factory(FIRST)
    def ask_where_remove(self, update, context):
        self.clean_query_remove_markup(update.callback_query)
        print_log("\tReceived request to remove response.")
        context.user_data['action'] = REMOVE

        self.updater.bot.send_message(update.effective_chat.id,
                                      REMOVE_NOTIFICATION_RESPONSE, reply_markup=InlineKeyboardMarkup(
                                          [[InlineKeyboardButton('Ring Notification', callback_data=DELETE_RING),
                                            InlineKeyboardButton('Open Notification', callback_data=DELETE_OPEN)],
                                              [InlineKeyboardButton('I changed my mind', callback_data=GO_BACK)]]))
        return FOURTH

    @save_state_factory(FIRST)
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
    
    @save_state_factory(SECOND)
    def ask_new_notif(self, update, context):
        print_log(datetime.datetime.now())
        self.clean_query_remove_markup(update.callback_query)
        # ADD_RING o ADD_OPEN
        context.user_data[ACTION] = update.callback_query.data
        print_log(
            f"\tReceived request to add response to {update.callback_query.data}")
        context.user_data[WARN_RESPONSE] = 0
        self.updater.bot.send_message(update.effective_chat.id,
                                      "Ok! *Answer* to this message with your new notification. Be aware, I'll prepend a "
                                      "prefix just to make sure it's easy to understand quickly what you're saying",
                                      reply_markup=InlineKeyboardMarkup(
                                          [[InlineKeyboardButton(
                                              'Quit', callback_data=QUIT)]])
                                      )
        return THIRD

    @save_state_factory(SECOND)
    def show_list(self, update, context):
        # self.clean_query_remove_markup(update.callback_query)
        # omitted as next handler will clear it
        if update.callback_query.data == SHOW_OPEN:
            context.user_data[LOCATION] = OPEN
        else:
            context.user_data[LOCATION] = RING

        self.index_responses(context)
        return self.pick_remove_notif(update, context)

    @save_state_factory(THIRD)
    def add_notif(self, update, context):
        if update.message.reply_to_message.from_user.id == self.updater.bot.id:
            requested_action = context.user_data[ACTION]
            if requested_action not in self.responses[action_to_names[requested_action]]:
                self.responses[action_to_names[requested_action]].append(
                    update.message.text)
            update.message.reply_text(f"Your notification has been added!")
            print_log(datetime.datetime.now())
            print_log(
                f"\tAdded new response to {requested_action}, updating file...")
            self.update_file(PATHS.RESPONSE_FILE, self.responses)
            return ConversationHandler.END
        else:
            print_log("\tCaught a response to someone else, will be waiting")
            return THIRD

    @save_state_factory(THIRD)
    def warn_about_answer(self, update, context):
        if context.user_data[WARN_RESPONSE] == 0:
            context.user_data[WARN_RESPONSE] = 1
            print_log(
                "\tCaught some random message, warning about reply necessity")
            self.updater.bot.send_message(
                update.effective_chat.id, WARN_ADD_ANSWER, reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(
                        'Quit', callback_data=QUIT)]])
            )
        else:
            print_log("\tCaught more random messages, ignoring")
        return THIRD

    @save_state_factory(FOURTH)
    def remove_notif(self, update, context):
        # self.clean_query_remove_markup(update.callback_query)
        # omitted as next handler will clear it
        print_log(
            f"\tReceived request to remove response from {update.callback_query.data}. Presenting choices...")
        context.user_data[PAGE] = 0
        if update.callback_query.data == DELETE_OPEN:
            context.user_data[LOCATION] = OPEN
        else:
            context.user_data[LOCATION] = RING
        self.updater.bot.send_message(update.effective_chat.id, "Please answer to the next message the"
                                      "number of the entry you would like to delete")

        self.index_responses(context)
        return self.pick_remove_notif(update, context)

    @save_state_factory(FIFTH)
    def pick_remove_notif(self, update, context):
        self.clean_query_remove_markup(update.callback_query)
        print_log("\tAsking user to select option...")
        if update.callback_query.data == QUIT:
            print_log("\tAborted due to user input.")
            return ConversationHandler.END
        # TODO: switch to KeyboardMarkup at leaaast
        elif update.callback_query.data == NEXT:
            # only change page if there are more entries to show
            # the page is +1 for maths reasons
            if (context.user_data[PAGE] + 1) * PAGE_SIZE < len(context.user_data[INDEX_TO_RESPONSE]):
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
        if entries_list != []:
            message_text = '\n'.join(entries_list)
            keyboard_layout = InlineKeyboardMarkup(
                [[InlineKeyboardButton(NEXT, callback_data=NEXT),
                  InlineKeyboardButton(PREV, callback_data=PREV)],
                 [InlineKeyboardButton(QUIT, callback_data=QUIT)]])
            next_state = FIFTH
        else:
            message_text = 'Sorry, there are no options saved. Maybe add some first!'
            keyboard_layout = InlineKeyboardMarkup(
                [[InlineKeyboardButton("Sure, let's add one", callback_data=ADD),
                  InlineKeyboardButton("No thank you.", callback_data=QUIT)]])
            next_state = FIRST
        print_log(f"OPTIONS SENT: \n{message_text}")
        self.updater.bot.send_message(update.effective_chat.id,
                                      message_text, reply_markup=keyboard_layout)
        return next_state

    @save_state_factory(FIFTH)
    def remove_selected_notif(self, update, context):
        self.clean_query_remove_markup(update.callback_query)
        print_log("in remove_selected_notif")
        answered_message = update.message.reply_to_message
        if context.user_data[ACTION] != REMOVE:
            # we got in here "by mistake": we misinterpreted
            # a random response for the user's pick on what
            # to remove, but he was just reading the responses.
            # So, we ignore it
            return FIFTH
        if answered_message.from_user.id == self.updater.bot.id:
            # delete markup from previous question
            self.updater.bot.edit_message_text(f"Asked to remove entry {update.message.text}",
                                               chat_id=update.effective_chat.id,
                                               message_id=answered_message.message_id)
            print_log(
                f"\tReceived request to remove entry {update.message.text}")
            if str.isdigit(update.message.text):
                requested_number = int(update.message.text)
                if requested_number < 0 or requested_number not in context.user_data[INDEX_TO_RESPONSE].keys():
                    self.updater.bot.send_message(update.effective_chat.id,
                                                  "Right, very funny. How about a real number next time?\njerk")
                    return self.change_response(update, context)
                removed_response = context.user_data[INDEX_TO_RESPONSE].pop(
                    requested_number)
                # i'm curious what they'll try
                with open(PATHS.DEL_FILE, 'a+') as f:
                    f.write(f"{str(removed_response)}\n")

                self.responses[context.user_data[LOCATION]].remove(
                    removed_response)
                print_log(f"\tRemoved {removed_response}")
                self.update_file(PATHS.RESPONSE_FILE, self.responses)

                update.message.reply_text(
                    "I removed what you asked. Hope it wasn't offensive!\nBye!")
            else:
                print_log("\tCouldn't understand answer")
                self.unclear_input(update, context)

            return ConversationHandler.END

    ####################### UTILS #############################################

    def relax(self):
        self.updater.start_polling()  # not sure if necessary
        self.updater.idle()

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
            self.conf.pop(chat_id)
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

    
    def update_file(self, file, obj):
        with open(file, 'w') as f:
            print_log("\t\tSaving to file..")
            # consider sort_keys=True
            json.dump(obj, f, indent=4)
            print_log("\t\tSaved!")

    def clean_query_remove_markup(self, query):
        if (query != None):
            query.answer()
            query.edit_message_text(
                text="Selected option: {}".format(query.data))

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

    def on(self):
        print("I'm getting turned on...")


if __name__ == '__main__':
    # handler = BotHandler(LED(PIN_OPEN), Button(PIN_RING))
    handler = BotHandler(stupid(), stupid())
    handler.send_to_enabled("FIRST TEST NOTIFICATION")
    handler.relax()
