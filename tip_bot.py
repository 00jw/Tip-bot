import json
import traceback

from random import randint

from pymongo import MongoClient
from telegram import Bot
from web3 import Web3, HTTPProvider

import argparse

parser = argparse.ArgumentParser()
parser.add_argument("-c", "--config", type=str, required=True, help="Config file")
parser.add_argument("-t", "--toothless", help="Does not send transactions", action="store_true")
parser.add_argument("-v", "--verbose", help="A more verbose output", action="store_true")
args = parser.parse_args()

with open(args.config) as conf_file:
    conf = json.load(conf_file)
    connectionString = conf['mongo']['connectionString']
    http_provider = conf['web3']['provider']
    bot_token = conf['telegram_bot']['bot_token']
    dictionary = conf['dictionary']
    donate_address = conf['donate_address']



class TipBot:
    def __init__(self, w3):

        print("Running toothless. Transactions will not be sent")

        self.w3 = w3
        print("Web3 Connected: %s " % self.w3.isConnected())

        # Telegram bot initialization
        self.bot = Bot(bot_token)

        # Tip bot Initialization
        client = MongoClient(connectionString)
        db = client.get_default_database()
        self.col_users = db['Users']

        # get chat updates
        self.new_message = self.wait_new_message()
        self.message = self.new_message.message \
            if self.new_message.message is not None \
            else self.new_message.callback_query.message
        self.text, _is_document = self.get_action(self.new_message)
        self.message_text = str(self.text).lower()
        print(self.text)

        # init user data
        try:
            self.first_name = self.new_message.effective_user.first_name
            self.username = self.new_message.effective_user.username
            self.user_id = self.new_message.effective_user.id
        except Exception as exc:
            print(exc)

        self.chat_id = self.message.chat.id
        self.tomo_address = self.get_user_data()

        split = self.message_text.split(' ')
        if len(split) > 1:
            args = split[1:]
        else:
            args = None

        self.check_username_on_change()
        self.action_processing(split[0], args)



    """
        Get group username
    """
    def get_group_username(self):
        try:
            return str(self.message.chat.username)
        except:
            return str(self.message.chat.id)


    """
            Get User username
    """
    def get_user_username(self):
        try:
            return str(self.message.from_user.username)
        except:
            if args.verbose:
                print("Could not find username for:")
                print(self.message)
            return None


    def wait_new_message(self):
        while True:
            updates = self.bot.get_updates()
            if len(updates) > 0:
                break
        update = updates[0]
        self.bot.get_updates(offset=update["update_id"] + 1)
        return update


    """
        Get user action | msg or callback
    """
    @staticmethod
    def get_action(message):
        _is_document = False

        if message['message'] is not None:
            menu_option = message['message']['text']
            _is_document = message['message']['document'] is not None
        elif message["callback_query"] != 0:
            menu_option = message["callback_query"]["data"]

        return str(menu_option), _is_document



    """
        Handle user actions
    """
    def action_processing(self, cmd, args):
        if "/start" == cmd:
            _is_user_exists = self.col_users.find_one({"_id": self.user_id}) is not None
            if not _is_user_exists:
                public_key, pr_key = self.create_user_wallet()
                self.col_users.insert({
                    "_id": self.user_id,
                    "UserName": self.username,
                    "TomoAddress": public_key,
                    "TomoPrivateKey": pr_key,
                    "Balance": 0
                })
                self.bot.send_message(
                    self.user_id,
                    dictionary['welcome'] % public_key,
                    parse_mode='HTML'
                )



        elif "/tip" == cmd or "/send" == cmd:
            if args is not None and len(args) >= 1:
                if self.message.reply_to_message is not None:
                    if args.verbose:
                        print("running tip_in_the_chat() with args:")
                        print(*args)
                    self.tip_in_the_chat(*args)
                else:
                    if args.verbose:
                        print("running tip_user() with args:")
                        print(*args)
                    self.tip_user(*args)
            else:
                self.bot.send_message(self.user_id,
                                      dictionary['incorrect_parameters'],
                                      parse_mode='HTML')

        elif "/balance" == cmd:
            balance = self.check_balance()
            self.bot.send_message(
                self.user_id,
                dictionary['balance'] % balance,
                parse_mode='HTML'
            )

        elif "/withdraw" == cmd:
            try:
                if args is not None and len(args) == 2:
                    self.withdraw_coins(*args)
                else:
                    self.bot.send_message(
                        self.user_id,
                        dictionary['incorrect_withdraw'],
                        parse_mode='HTML'
                    )
            except Exception as exc:
                print(exc)

        elif "/deposit" == cmd:
            self.bot.send_message(
                self.user_id,
                dictionary['deposit'] % self.tomo_address,
                parse_mode='HTML'
            )

        elif "/donate" == cmd:
            if args is not None or len(args) == 1:
                self.donate(*args)

            else:
                self.bot.send_message(
                    self.user_id,
                    dictionary['donate'],
                    parse_mode='HTML'
                )

        elif "/help" == cmd:
            self.bot.send_message(
                self.user_id,
                dictionary['help'],
                parse_mode='HTML'
            )

        elif "/backup" == cmd:
            _private_key = self.col_users.find_one({"_id": self.user_id})['TomoPrivateKey']
            self.bot.send_message(
                self.user_id,
                dictionary['backup'] % _private_key,
                parse_mode='HTML'
            )


    """
        Check username on change in the bot
    """
    def check_username_on_change(self):
        _is_username_in_db = self.col_users.find_one({"UserName": self.username}) is not None if self.username is not None else True
        if not _is_username_in_db:
            self.col_users.update(
                {
                    "_id": self.user_id
                },
                {
                    "$set":
                        {
                            "UserName": self.username
                        }
                }
            )

    """
        Create new wallet for new bot member
    """
    def create_user_wallet(self):
        acct = self.w3.eth.account.create('%s %s %s' % (self.user_id, self.first_name, randint(10000, 1000000)))
        print(acct.address, acct.privateKey.hex())
        return acct.address, acct.privateKey.hex()

    """
        Check user balance
    """
    def check_balance(self):
        balance = self.w3.fromWei(self.w3.eth.getBalance(self.tomo_address), 'ether')
        return balance


    """
        Get user data
    """
    def get_user_data(self):
        try:
            _user = self.col_users.find_one({"_id": self.user_id})
            return _user['TomoAddress']
        except Exception as exc:
            print(exc)
            return None, None


    """
        Withdraw coins to address with params:
        address
        amount
    """
    def withdraw_coins(self, address, amount):
        try:
            try:
                amount = float(amount)
            except Exception as exc:
                self.bot.send_message(self.user_id,
                                      dictionary['incorrect_amount'],
                                      parse_mode='HTML')
                print(exc)

            balance = self.check_balance()
            if balance > amount:
                to_address = self.w3.toChecksumAddress(address)
                gas = 40000
                gas_price = self.w3.eth.gasPrice
                txn = \
                    {
                        'from': self.tomo_address,
                        'gas': gas,
                        'to': to_address,
                        'value': self.w3.toWei(amount, 'ether') - (gas*gas_price),
                        'gasPrice': gas_price,
                        'nonce': self.w3.eth.getTransactionCount(self.tomo_address),
                    }

                _private_key = self.col_users.find_one({"_id": self.user_id})['TomoPrivateKey']
                signed_txn = self.w3.eth.account.signTransaction(txn,
                                                                 private_key=_private_key)
                if args.toothless:
                    tx = signex_txn.hash.hex()
                else:
                    tx = self.w3.eth.sendRawTransaction(signed_txn.rawTransaction)
                    tx = self.w3.toHex(tx)

                self.bot.send_message(self.user_id,
                                      dictionary['withdrawal_result'] % (amount, address, tx),
                                      parse_mode='HTML')
            else:
                self.bot.send_message(self.user_id,
                                      dictionary['incorrect_balance'] % balance,
                                      parse_mode='HTML')
        except Exception as exc:
            print(exc)


    """
        Donate to address
    """
    def donate(self, amount):
        try:
            try:
                amount = float(amount)
            except Exception as exc:
                self.bot.send_message(self.user_id,
                                      dictionary['incorrect_amount'],
                                      parse_mode='HTML')
                print(exc)

            balance = self.check_balance()
            if balance > amount:
                to_address = self.w3.toChecksumAddress(donate_address)
                gas = 40000
                gas_price = self.w3.eth.gasPrice
                txn = \
                    {
                        'from': self.tomo_address,
                        'gas': gas,
                        'to': to_address,
                        'value': self.w3.toWei(amount, 'ether') - (gas*gas_price),
                        'gasPrice': gas_price,
                        'nonce': self.w3.eth.getTransactionCount(self.tomo_address),
                    }

                _private_key = self.col_users.find_one({"_id": self.user_id})['TomoPrivateKey']
                signed_txn = self.w3.eth.account.signTransaction(txn,
                                                                 private_key=_private_key)
                if args.toothless:
                    tx = signed_txn.hash.hex()
                else:
                    tx = self.w3.eth.sendRawTransaction(signed_txn.rawTransaction)
                    tx = self.w3.toHex(tx)

                self.bot.send_message(self.user_id,
                                      dictionary['donate_result'] % (balance, tx),
                                      parse_mode='HTML')
            else:
                self.bot.send_message(self.user_id,
                                      dictionary['incorrect_balance'] % balance,
                                      parse_mode='HTML')
        except Exception as exc:
            print(exc)


    """
        Tip user with params:
        username
        amount
    """
    def tip_user(self, username, amount, coin='Tomo'):
        try:
            try:
                amount = float(amount)
            except Exception as exc:
                self.bot.send_message(self.user_id,
                                      dictionary['incorrect_amount'],
                                      parse_mode='HTML')
                print(exc)

            username = username.replace('@', '')

            _user = self.col_users.find_one({"UserName": username})
            _is_username_exists = _user is not None
            if not _is_username_exists:
                self.bot.send_message(self.user_id,
                                      dictionary['username_error'],
                                      parse_mode='HTML')
                return

            self.send_tip(_user['_id'], _user['TomoAddress'], amount, coin)

        except Exception as exc:
            print(exc)


    """
        Send a tip to user in the chat
    """
    def tip_in_the_chat(self, amount, coin='Tomo'):
        try:
            try:
                amount = float(amount)
            except Exception as exc:
                self.bot.send_message(self.user_id,
                                      dictionary['incorrect_amount'],
                                      parse_mode='HTML')
                print(exc)

            _user = self.col_users.find_one({"_id": self.message.reply_to_message.from_user.id})

            self.send_tip(self.message.reply_to_message.from_user.id, _user['TomoAddress'], amount, coin)

        except Exception as exc:
            print(exc)


    """
        Send tip to user with params
        user_id - user identificator
        addrees - user address
        amount - amount of a tip
        
    """
    def send_tip(self, user_id, address, amount, coin):
        try:
            balance = self.check_balance()
            if balance > amount:
                gas = 40000
                gas_price = self.w3.eth.gasPrice
                txn = \
                    {
                        'from': self.tomo_address,
                        'gas': gas,
                        'to': address,
                        'value': self.w3.toWei(amount, 'ether') - (gas*gas_price),
                        'gasPrice': gas_price,
                        'nonce': self.w3.eth.getTransactionCount(self.tomo_address),
                    }

                _private_key = self.col_users.find_one({"_id": self.user_id})[
                    'TomoPrivateKey']
                signed_txn = self.w3.eth.account.signTransaction(txn,
                                                                 private_key=_private_key)
                if args.toothless:
                    tx = signed_txn.hash.hex()
                else:
                    tx = self.w3.eth.sendRawTransaction(signed_txn.rawTransaction)
                    tx = self.w3.toHex(tx)

                self.bot.send_message(user_id,
                                      dictionary['tip_recieved'] % (amount, coin, tx),
                                      parse_mode='HTML')
                self.bot.send_message(self.user_id,
                                      dictionary['tip_sent'] % (amount, coin, tx),
                                      parse_mode='HTML')
            else:
                self.bot.send_message(self.user_id,
                                      dictionary['incorrect_balance'] % (coin, balance),
                                      parse_mode='HTML')
        except Exception as exc:
            print(exc)


def main():
    w3 = Web3(HTTPProvider(http_provider))
    while True:
        try:
            TipBot(w3=w3)
        except Exception as e:
            if "Timed out" not in str(e):
                traceback.print_exc()
                print(e)


if __name__ == '__main__':
    main()
