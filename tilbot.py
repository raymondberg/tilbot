import logging
import os
import re
import time
import traceback
import yaml

from client import slack_client

PLUGIN_CONTAINER_FOLDER = os.path.dirname(os.path.realpath(__file__))
CONFIG_FILEPATH = os.path.join(PLUGIN_CONTAINER_FOLDER, 'tilbot_config.yaml')
STATE_FILEPATH = os.path.join(PLUGIN_CONTAINER_FOLDER, 'tilbot_state.yaml')

logging.info('Starting to log')

CONFIG = yaml.load(open(CONFIG_FILEPATH))
ALLOW_TRUSTED_COMMANDS = CONFIG['allow_trusted_commands']
BOT_HOME_CHANNEL_ID = CONFIG['bot_home_channel_id']
BOT_NAME = CONFIG['bot_name']
BOT_USER_ID = CONFIG['bot_user_id']
CRON_PING_FREQUENCY_SECONDS = CONFIG['cron_ping_frequency_seconds']
TRUSTED_USER_ID = CONFIG['trusted_user_id']

outputs = []
crontable = []

crontable.append([CRON_PING_FREQUENCY_SECONDS, 'dump_til_tag'])

def say(channel, message):
    outputs.append([channel, message])

def dump_til_tag(channel=None, state=None):
    if channel is None:
        channel = BOT_HOME_CHANNEL_ID
    if state is None:
        state = TilState.load()

    next_untilled_user_id = state.next_untilled_user_id()
    if next_untilled_user_id:
        message = 'Hey, {}, you\'re up! Share something you learned by saying `@{} til your new thing`'
        say(channel, message.format(Message.at_user(next_untilled_user_id), BOT_NAME))

class TilState:
    @classmethod
    def reset(cls):
        fresh_state = cls()
        fresh_state.save()
        return fresh_state

    @classmethod
    def load(cls):
        try:
            with open(STATE_FILEPATH) as file_obj:
                return cls(yaml.load(file_obj))
        except FileNotFoundError:
            return cls.reset()

    def __init__(self, user_entries=None):
        if user_entries == None:
            user_entries = {}
        self.user_entries = user_entries

    def add_user(self, user_id):
        self.user_entries[user_id] = { 'til': None, 'skip': False}

    def rm_user(self, user_id):
        del self.user_entries[user_id]

    def add_til(self, user_id, til):
        if user_id not in self.user_entries:
            self.add_user(user_id)

        self.user_entries[user_id]['til'] = til

    def skip_user(self, user_id):
        if user_id in self.user_entries:
            self.user_entries[user_id]['skip'] = True
            return True
        return False

    def get_til(self, user_id):
        if user_id in self.user_entries:
            return self.user_entries[user_id]['til']

    def save(self):
        with open(STATE_FILEPATH, 'w') as file_obj:
            yaml.dump(self.user_entries, file_obj)

    def get_untilled_user_ids(self):
        return [ user_id for user_id in self.user_entries
            if self.user_entries[user_id]['til'] is None and
                not self.user_entries[user_id]['skip']]

    def next_untilled_user_id(self):
        untilled_user_ids = sorted(self.get_untilled_user_ids())
        if len(untilled_user_ids) > 0:
            return untilled_user_ids[0]

class Message:
    REQUIRED_FIELDS = ['channel', 'text', 'user']
    @classmethod
    def from_dict(cls, data):
        for key in cls.REQUIRED_FIELDS:
            if key not in data:
                return None
        message = cls(data['channel'], data['text'], data['user'])
        message.raw = data
        return message

    @classmethod
    def extract_user_id(cls, raw_user):
        try:
            return re.match(r'.*<@([A-Z0-9]+)>.*', raw_user).groups(0)[0]
        except AttributeError:
            return None

    @classmethod
    def at_user(cls, user_id):
        return '<@{}>'.format(user_id)

    def __init__(self, channel, text, user_id):
        self.channel = channel
        self.text = text
        self.user_id = user_id
        self.words = text.split(' ')
        self.extract_command_word()
        self.state = TilState.load()

    def extract_command_word(self):
        if len(self.words) > 1 and ( self.words[0] == BOT_NAME or
          Message.extract_user_id(self.words[0]) == BOT_USER_ID):
            self.command = self.words[1].lower()
            self.payload = ' '.join(self.words[2:])
        else:
            self.command = None
            self.payload = self.text

    def process(self):
        normal_process_functions = {
            'til': self.process_til,
            'help': self.process_help,
            'remind': self.process_remind,
            'ping': self.process_ping,
            'mine': self.process_mine,
        }
        if self.command in normal_process_functions:
            logging.info('Interpreted normal command: {}'.format(self.raw))
            return normal_process_functions[self.command]()

        if self.user_id == TRUSTED_USER_ID:
            privileged_process_functions = {
                'reset': self.process_reset,
                'adduser': self.process_add_user,
                'addusers': self.process_add_users,
                'rmuser': self.process_rm_user,
                'skip': self.process_skip,
            }
            if self.command in privileged_process_functions:
                logging.info('Interpreted privileged command: {}'.format(self.raw))
                return privileged_process_functions[self.command]()
        else:
            logging.warning('unknown command: {}'.format(self))

    def process_til(self):
        next_untilled_user_id = self.state.next_untilled_user_id()
        self.state.add_til(self.user_id, self.payload)
        self.state.save()
        say(self.channel, 'okay, {}, thanks for sharing that til!'.format(Message.at_user(self.user_id)))

        if next_untilled_user_id == self.user_id:
            self.process_ping()

    def process_help(self):
        say(self.channel, '''It's not hard!
Just say `@{} til [the thing you learned]`! I can also do some other things for you:```
help: print this
remind: remind everyone who hasn't til'd yet
ping: ping the person who is next in my til list
mine: remind you what you told me was your til```'''.format(BOT_NAME))

    def process_remind(self):
        untilled_user_ids = self.state.get_untilled_user_ids()
        if len(untilled_user_ids)  > 0:
            username_string = ', '.join([self.at_user(user_id) for user_id in untilled_user_ids])
            reminder = 'These are the users who have yet to TIL: {}'.format(username_string)
            say(self.channel, reminder)
        else:
            say(self.channel, 'Looks like everybody is up to date!')

    def process_mine(self):
        til = self.state.get_til(self.user_id)
        if til:
            say(self.channel, 'You said ```{}```'.format(til))
        else:
            say(self.channel, 'I don\'t have one for you yet!')

    def process_reset(self):
        self.state = TilState.reset()
        say(self.channel, 'okay, I reset')

    def process_ping(self):
        dump_til_tag(self.channel, self.state)

    def process_skip(self):
        user_id = Message.extract_user_id(self.words[2])
        if user_id:
            self.state.skip_user(user_id)
            self.state.save()
            say(self.channel, 'Okay, I skipped {}'.format(Message.at_user(user_id)))
        else:
            say(self.channel, 'Sorry, that name isn\'t familiar to me.')

    def process_add_user(self):
        user_id = Message.extract_user_id(self.words[2])
        if user_id:
            self.state.add_user(user_id)
            self.state.save()
            say(self.channel, 'Okay, I added {}'.format(Message.at_user(user_id)))
        else:
            say(self.channel, 'Sorry, that name isn\'t familiar to me.')

    def process_add_users(self):
        user_ids = [Message.extract_user_id(user) for user in self.words[2:]]
        if len(user_ids) > 0:
            say(self.channel, "This may take a bit; be patient!")
        for user_id in user_ids:
            if user_id is None:
                say(self.channel, 'Sorry, that name isn\'t familiar to me.')
            else:
                self.state.add_user(user_id)
                self.state.save()
                say(self.channel, 'Okay, I added {}'.format(Message.at_user(user_id)))
            time.sleep(1)

    def process_rm_user(self):
        user_id = Message.extract_user_id(self.words[2])
        if user_id:
            self.state.rm_user(user_id)
            self.state.save()
            say(self.channel, 'Okay, I removed {}'.format(Message.at_user(user_id)))
        else:
            say(self.channel, 'Sorry, that name isn\'t familiar to me.')

    def __str__(self):
        return '<Message {}>'.format(self.raw)

def process_message(data):
    message = Message.from_dict(data)
    if message:
        try:
            message.process()
        except Exception as e:
            logging.error('Something exploded! {}'.format(e))
            logging.error(format(traceback.format_exc()))
    else:
        logging.info("Something wasn't interpretable as a command: {}".format(data))
