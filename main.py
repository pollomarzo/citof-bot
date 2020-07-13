from citofbot import BotHandler
from gpiozero import Button
from gpiozero import LED

PIN_OPEN = 1
PIN_RING = 2
# will call this pin even though the second one is gpio

if __name__ == '__main__':
    handler = BotHandler(LED(PIN_OPEN), Button(PIN_RING))
    handler.relax()
