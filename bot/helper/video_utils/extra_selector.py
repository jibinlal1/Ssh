from __future__ import annotations
from ast import literal_eval
from asyncio import Event, wait_for, wrap_future, gather
from functools import partial
from pyrogram.filters import regex, user
from pyrogram.handlers import CallbackQueryHandler
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from time import time

from bot import VID_MODE
from bot.helper.ext_utils.bot_utils import new_thread
from bot.helper.ext_utils.status_utils import get_readable_file_size, get_readable_time
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.message_utils import sendMessage, editMessage, deleteMessage
from bot.helper.video_utils import executor as exc


class ExtraSelect:
    def __init__(self, executor: exc.VidEcxecutor):
        self._listener = executor.listener
        self._time = time()
        self._reply = None
        self.executor = executor
        self.event = Event()
        self.is_cancel = False
        self.extension: list[str] = [None, None, 'mkv']
        self.status = ''
        self.swap_selection = {'selected_stream': None, 'remaps': {}}

    @new_thread
    async def _event_handler(self):
        pfunc = partial(cb_extra, obj=self)
        handler = self._listener.client.add_handler(CallbackQueryHandler(pfunc, filters=regex('^extra') & user(self._listener.user_id)), group=-1)
        try:
            await wait_for(self.event.wait(), timeout=180)
        except:
            self.event.set()
        finally:
            self._listener.client.remove_handler(*handler)

    async def update_message(self, text: str, buttons):
        if not self._reply:
            self._reply = await sendMessage(text, self._listener.message, buttons)
        else:
            await editMessage(text, self._reply, buttons)

    def _streams_select(self, streams: dict=None):
        buttons = ButtonMaker()
        if not self.executor.data:
            self.executor.data.setdefault('stream', {})
            self.executor.data['sdata'] = []
            for stream in streams:
                indexmap, codec_name, codec_type, lang = stream.get('index'), stream.get('codec_name'), stream.get('codec_type'), stream.get('tags', {}).get('language')
                if not lang:
                    lang = str(indexmap)
                if codec_type not in ['video', 'audio', 'subtitle']:
                    continue
                if codec_type == 'audio':
                    self.executor.data['is_audio'] = True
                elif codec_type == 'subtitle':
                    self.executor.data['is_sub'] = True
                self.executor.data['stream'][indexmap] = {'info': f'{codec_type.title()} ~ {lang.upper()}',
                                                          'name': codec_name,
                                                          'map': indexmap,
                                                          'type': codec_type,
                                                          'lang': lang}
        mode, ddict = self.executor.mode, self.executor.data
        for key, value in ddict['stream'].items():
            if mode == 'extract':
                buttons.button_data(value['info'], f'extra {mode} {key}')
                audext, subext, vidext = self.extension
                text = (f'<b>STREAM EXTRACT SETTINGS ~ {self._listener.tag}</b>\n'
                        f'<code>{self.executor.name}</code>\n'
                        f"<b></b>File Size: <b>{get_readable_file_size(self.executor.size)}</b>\n"
                        f'<b></b>Video Format: <b>{vidext.upper()}</b>\n'
                        f'<b></b>Audio Format: <b>{audext.upper()}</b>\n'
                        f'<b></b>Subtitle Format: <b>{subext.upper()}</b>\n'
                        f"<b></b>Alternative Mode: <b>{'✓ Enable' if ddict.get('alt_mode') else 'Disable'}</b>\n\n"
                        'Select avalilable stream below to unpack!')
            elif mode == 'swap_stream':
                pass # The new swap_stream_select method handles this
            else:
                if value['type'] != 'video':
                    buttons.button_data(value['info'], f'extra {mode} {key}')
                text = (f'<b>STREAM REMOVE SETTINGS ~ {self._listener.tag}</b>\n'
                        f'<code>{self.executor.name}</code>\n'
                        f'File Size: <b>{get_readable_file_size(self.executor.size)}</b>\n')
                if sdata := ddict.get('sdata'):
                    text += '\nStream will removed:\n'
                    for i, sindex in enumerate(sdata, start=1):
                        text += f"{i}. {ddict['stream'][sindex]['info']}\n".replace('✓ ', '')
                text += '\nSelect avalilable stream below!'
        if mode == 'extract':
            buttons.button_data('✓ ALT Mode' if ddict.get('alt_mode') else 'ALT Mode', f"extra {mode} alt {ddict.get('alt_mode', False)}", 'footer')
        if ddict.get('is_sub'):
            buttons.button_data('All Subs', f'extra {mode} subtitle')
        if ddict.get('is_audio'):
            buttons.button_data('All Audio', f'extra {mode} audio')
        buttons.button_data('Cancel', 'extra cancel', 'footer')
        if mode == 'extract':
            for ext in self.extension:
                buttons.button_data(ext.upper(), f'extra {mode} extension {ext}', 'header')
            buttons.button_data('Extract All', f'extra {mode} video audio subtitle')
        else:
            buttons.button_data('Reset', f'extra {mode} reset', 'header')
            buttons.button_data('Reverse', f'extra {mode} reverse', 'header')
            buttons.button_data('Continue', f'extra {mode} continue', 'footer')
        text += f'\n\n<i>Time Out: {get_readable_time(180 - (time()-self._time))}</i>'
        return text, buttons.build_menu(2)

    def set_default_audio_stream(self, streams: list[dict]):
        """Set the first audio stream index as default audio stream in executor data."""
        first_audio_index = next((s['index'] for s in streams if s['codec_type'] == 'audio'), None)
        self.executor.data['default_audio'] = first_audio_index

    async def compress_select(self, streams: dict):
        self.executor.data = {}
        self.set_default_audio_stream(streams)  # Set default audio here
        buttons = ButtonMaker()
        for stream in streams:
            indexmap, codec_type, lang = stream.get('index'), stream.get('codec_type'), stream.get('tags', {}).get('language')
            if not lang:
                lang = str(indexmap)
            if codec_type == 'video' and indexmap == 0:
                self.executor.data['video'] = indexmap
            if codec_type == 'video' and 'video' not in self.executor.data:
                self.executor.data['video'] = indexmap
            if codec_type == 'audio':
                buttons.button_data(f'Audio ~ {lang.upper()}', f'extra compress {indexmap}')
        buttons.button_data('Continue', 'extra compress 0')
        buttons.button_data('Cancel', 'extra cancel')
        await self.update_message(f'{self._listener.tag}, Select available audio or press <b>Continue (no audio)</b>.\n<code>{self.executor.name}</code>', buttons.build_menu(2))

    async def swap_stream_select(self, streams: dict):
        self.set_default_audio_stream(streams)  # Set default audio here as well
        self.executor.data = {'streams': streams, 'remaps': self.swap_selection['remaps'], 'selected_stream': self.swap_selection['selected_stream']}
        buttons = ButtonMaker()
        
        text = (f"<b>STREAM REORDER SETTINGS ~ {self._listener.tag}</b>\n"
                f"<code>{self.executor.name}</code>\n"
                f"File Size: <b>{get_readable_file_size(self.executor.size)}</b>\n\n")

        all_streams = [s for s in streams if s['codec_type'] == 'audio']
        
        reordered_streams = self.executor.data.get('remaps', {})
        
        text += "<b>Current Stream Order:</b>\n"
        for s in all_streams:
            lang = s.get('tags', {}).get('language', f'#{s.get("index")}')
            new_pos = reordered_streams.get(s['index'], s['index'])
            text += f"{s['codec_type'].title()} Stream {s['index']} ({lang.upper()}) -> New Position: {new_pos}\n"

        text += "\n<b>Select an audio stream to reorder:</b>\n"

        for s in all_streams:
            lang = s.get('tags', {}).get('language', f'#{s.get("index")}')
            button_text = f"✓ {s['codec_type'].title()} ({s['index']}) ({lang.upper()})" if s['index'] in reordered_streams else f"{s['codec_type'].title()} ({s['index']}) ({lang.upper()})"
            buttons.button_data(button_text, f"extra swap_stream_select {s['index']}")
        
        buttons.button_data('Continue', 'extra swap_continue', 'footer')
        buttons.button_data('Cancel', 'extra cancel', 'footer')
        
        await self.update_message(text, buttons.build_menu(2))

    # ... (rest of the class unchanged, omitted here for brevity) ...


# The cb_extra callback remains as before, with the fix applied in the 'swap_position' case, omitted here for brevity.

