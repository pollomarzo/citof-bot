from citofbot import BotHandler
from gpiozero import Button

if __name__ == '__main__':
    handler = BotHandler()
    button = Button(2)
    button.when_pressed = handler.send_to_enabled
    handler.relax()
