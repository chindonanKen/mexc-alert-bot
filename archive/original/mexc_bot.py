#!/usr/bin/env python3
import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Dict, List
import pytz
import requests
import telebot

BOT_TOKEN = "REDACTED_OLD_TOKEN__ROTATE_IN_BOTFATHER_IF_EVER_USED"  # Old token was exposed in early versions - rotate if necessary
TIMEZONE = pytz.timezone('Asia/Singapore')
ALERTS_FILE = "price_alerts.json"
MEXC_API = "https://api.mexc.com/api/v3/ticker/price"
TOLERANCE_PERCENT = 0.0005   # 0.2% tolerance (change this if you want)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN)
alerts: Dict[int, List[Dict]] = {}

def load_alerts():
    global alerts
    if os.path.exists(ALERTS_FILE):
        with open(ALERTS_FILE) as f:
            alerts = {int(k): v for k, v in json.load(f).items()}

def save_alerts():
    with open(ALERTS_FILE, "w") as f:
        json.dump(alerts, f, indent=2)

def add_alert(user_id, symbol, price):
    if user_id not in alerts:
        alerts[user_id] = []
    alert_id = len(alerts[user_id])
    alerts[user_id].append({"id": alert_id, "symbol": symbol.upper(), "price": price, "enabled": True})
    save_alerts()
    return alert_id

def remove_alert(user_id, alert_id):
    if user_id in alerts:
        alerts[user_id] = [a for a in alerts[user_id] if a["id"] != alert_id]
        save_alerts()
        return True
    return False

def toggle_alert(user_id, alert_id):
    if user_id in alerts:
        for a in alerts[user_id]:
            if a["id"] == alert_id:
                a["enabled"] = not a["enabled"]
                save_alerts()
                return a["enabled"]
    return False

def get_price(symbol):
    try:
        r = requests.get(f"{MEXC_API}?symbol={symbol}", timeout=5)
        if r.status_code == 200:
            return float(r.json()["price"])
    except:
        pass
    return None

def price_monitor():
    while True:
        for user_id in list(alerts.keys()):
            for a in list(alerts.get(user_id, [])):
                if not a["enabled"]:
                    continue
                price = get_price(a["symbol"])
                if price is None:
                    continue
                # Percentage-based check (much better for all price ranges)
                if abs(price - a["price"]) / a["price"] <= TOLERANCE_PERCENT:
                    msg = f"ALERT TRIGGERED!\n\n{a['symbol']} hit ${a['price']}\nCurrent: ${price:.8f}"
                    try:
                        bot.send_message(user_id, msg)
                        logger.info(f"Alert triggered: {a['symbol']} @ ${a['price']}")
                    except:
                        pass
                    remove_alert(user_id, a["id"])
        time.sleep(2)

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "MEXC Bot Ready\n\n/addalert SYMBOL PRICE\n/listalerts\n/togglealert ID\n/removealert ID\n/status")

@bot.message_handler(commands=['addalert'])
def addalert(message):
    user_id = message.from_user.id
    args = message.text.split()[1:]
    if len(args) != 2:
        bot.reply_to(message, "Usage: /addalert SYMBOL PRICE")
        return
    symbol = args[0].upper()
    try:
        price = float(args[1])
    except:
        bot.reply_to(message, "Price must be number")
        return
    alert_id = add_alert(user_id, symbol, price)
    bot.reply_to(message, f"Alert #{alert_id} created for {symbol} at ${price}")

@bot.message_handler(commands=['listalerts'])
def listalerts(message):
    user_id = message.from_user.id
    user_alerts = alerts.get(user_id, [])
    if not user_alerts:
        bot.reply_to(message, "No alerts")
        return
    text = "Your Alerts:\n"
    for a in user_alerts:
        status = "ON" if a["enabled"] else "OFF"
        text += f"#{a['id']} {a['symbol']} @ ${a['price']} [{status}]\n"
    bot.reply_to(message, text)

@bot.message_handler(commands=['togglealert'])
def togglealert(message):
    user_id = message.from_user.id
    args = message.text.split()[1:]
    if not args:
        bot.reply_to(message, "Usage: /togglealert ID")
        return
    aid = int(args[0])
    new_state = toggle_alert(user_id, aid)
    bot.reply_to(message, f"Alert #{aid} {'ENABLED' if new_state else 'DISABLED'}")

@bot.message_handler(commands=['removealert'])
def removealert(message):
    user_id = message.from_user.id
    args = message.text.split()[1:]
    if not args:
        bot.reply_to(message, "Usage: /removealert ID")
        return
    aid = int(args[0])
    if remove_alert(user_id, aid):
        bot.reply_to(message, f"Removed #{aid}")
    else:
        bot.reply_to(message, "Not found")

@bot.message_handler(commands=['status'])
def status(message):
    user_id = message.from_user.id
    count = len(alerts.get(user_id, []))
    bot.reply_to(message, f"Alerts: {count}\nBot running")

if __name__ == "__main__":
    load_alerts()
    threading.Thread(target=price_monitor, daemon=True).start()
    logger.info("Starting bot...")
    bot.polling()
