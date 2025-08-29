from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

class ButtonMaker:
    def __init__(self):
        self._button = []
        self._header_button = []
        self._footer_button = []

    def build_button(self, key, link, GDrive=False):
        if GDrive and key == "Don't Change":
            self._button.append(InlineKeyboardButton(text=str(key), callback_data=link.encode("UTF-8")))
        else:
            self._button.append(InlineKeyboardButton(text=str(key), url=link))

    def get_buttons(self, edit_buttons=None):
        if edit_buttons:
            return InlineKeyboardMarkup(edit_buttons)
        return InlineKeyboardMarkup([self._button])

    def sbutton(self, key, data):
        self._button.append(InlineKeyboardButton(text=str(key), callback_data=data.encode("UTF-8")))

    def kbutton(self, key, data, callback=True):
        if callback:
            self._button.append(InlineKeyboardButton(text=key, callback_data=data.encode("UTF-8")))
        else:
            self._button.append(InlineKeyboardButton(text=key, url=data))

    def dbutton(self, key, data, source_url):
        self._button.append(InlineKeyboardButton(text=key, callback_data=data.encode("UTF-8")))
        self.build_button("Source", source_url)

    def add_header(self, key, data, callback=True):
        if callback:
            self._header_button.append(InlineKeyboardButton(text=key, callback_data=data.encode("UTF-8")))
        else:
            self._header_button.append(InlineKeyboardButton(text=key, url=data))

    def add_footer(self, key, data, callback=True):
        if callback:
            self._footer_button.append(InlineKeyboardButton(text=key, callback_data=data.encode("UTF-8")))
        else:
            self._footer_button.append(InlineKeyboardButton(text=key, url=data))

    @property
    def markup(self):
        buttons = [self._button[i:i+3] for i in range(0, len(self._button), 3)]
        if self._header_button:
            buttons.insert(0, self._header_button)
        if self._footer_button:
            buttons.append(self._footer_button)
        return InlineKeyboardMarkup(buttons)
