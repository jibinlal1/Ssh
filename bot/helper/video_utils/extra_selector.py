from __future__ import annotations
from asyncio import Event, wait_for, wrap_future
from functools import partial
from pyrogram.filters import regex, user
from pyrogram.handlers import CallbackQueryHandler
from pyrogram.types import CallbackQuery
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
        
        if not self.executor.data:
            self.executor.data = {}
        
        # Set default conversion options
        self.executor.data.setdefault('preset', 'medium')
        self.executor.data.setdefault('crf', 23)
        self.executor.data.setdefault('audio_channels', 2)
        self.executor.data.setdefault('audio_codec', 'aac')
        self.executor.data.setdefault('bitrate', '160k')
        self.executor.data.setdefault('video_codec', 'libx264')

    @new_thread
    async def _event_handler(self):
        pfunc = partial(cb_extra, obj=self)
        handler = self._listener.client.add_handler(
            CallbackQueryHandler(pfunc, filters=regex('^extra') & user(self._listener.user_id)), group=-1)
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

    async def convert_select(self, streams: dict):
        buttons = ButtonMaker()
        hvid = '1080p'
        resolution = {'1080p': 'Convert 1080p', '720p': 'Convert 720p',
                      '540p': 'Convert 540p', '480p': 'Convert 480p', '360p': 'Convert 360p'}
        
        for stream in streams:
            if stream.get('codec_type') == 'video':
                vid_height = f"{stream.get('height', 0)}p"
                if vid_height in resolution:
                    hvid = vid_height
                break
        
        keys = list(resolution)
        for key in keys[keys.index(hvid):]:
            buttons.button_data(resolution[key], f'extra convert {key}')
        
        buttons.button_data('Cancel', 'extra cancel', 'footer')
        await self.update_message(f'{self._listener.tag}, Select a resolution to convert.\n<code>{self.executor.name}</code>', buttons.build_menu(2))

    async def show_conversion_options(self, resolution: str):
        self.executor.data['resolution'] = resolution
        buttons = ButtonMaker()
        buttons.button_data(f"Video Codec: {self.executor.data['video_codec']}", 'extra set_video_codec')
        buttons.button_data(f"Preset: {self.executor.data['preset']}", 'extra set_preset')
        buttons.button_data(f"CRF: {self.executor.data['crf']}", 'extra set_crf')
        buttons.button_data(f"Audio Codec: {self.executor.data['audio_codec']}", 'extra set_audio_codec')
        buttons.button_data(f"Audio Channels: {self.executor.data['audio_channels']}", 'extra set_audio_channels')
        buttons.button_data(f"Bitrate: {self.executor.data['bitrate']}", 'extra set_bitrate')
        buttons.button_data('Reset', 'extra reset_conversion', 'header')
        buttons.button_data('Continue', 'extra start_conversion', 'footer')
        buttons.button_data('Back', 'extra back_to_res_select', 'footer')
        buttons.button_data('Cancel', 'extra cancel', 'footer')
        text = (f"<b>Conversion Settings for {resolution}</b>\n"
                f"Adjust parameters or Continue with defaults.\n"
                f"<code>{self.executor.name}</code>")
        await self.update_message(text, buttons.build_menu(2))

    async def _select_option(self, title, current_value, options, callback_prefix):
        buttons = ButtonMaker()
        for val in options:
            prefix = "✓ " if str(val) == str(current_value) else ""
            buttons.button_data(f"{prefix}{val}", f"extra {callback_prefix} {val}")
        buttons.button_data('Back', 'extra back_to_conversion_options', 'footer')
        buttons.button_data('Cancel', 'extra cancel', 'footer')
        await self.update_message(f"<b>Select {title}</b>", buttons.build_menu(3))

    async def get_buttons(self, *args):
        future = self._event_handler()
        
        # Dynamically call the correct select method based on the mode
        select_method = getattr(self, f'{self.executor.mode}_select', None)
        if callable(select_method):
            await select_method(*args)
        
        await wrap_future(future)
        
        if self._reply:
            await deleteMessage(self._reply)
        
        if self.is_cancel:
            self.executor.data = None
            self.executor.is_cancel = True
            await self._listener.onUploadError(f'{VID_MODE.get(self.executor.mode, "Process")} stopped by user!')
        
        self.event.set()

async def cb_extra(_, query: CallbackQuery, obj: ExtraSelect):
    data = query.data.split()
    cmd = data[1]
    await query.answer()

    if cmd == 'cancel':
        obj.is_cancel = True
        obj.event.set()
        return

    # Conversion Menu Logic
    if cmd == 'convert':
        resolution = data[2]
        await obj.show_conversion_options(resolution)
    elif cmd == 'back_to_res_select':
        streams = obj.executor._metadata[0] if obj.executor._metadata else []
        await obj.convert_select(streams)
    elif cmd == 'set_video_codec':
        await obj._select_option("Video Codec", obj.executor.data.get('video_codec'), ['libx264', 'libx265'], "video_codec_value")
    elif cmd == 'video_codec_value':
        obj.executor.data['video_codec'] = data[2]
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'set_preset':
        options = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow']
        await obj._select_option("Preset", obj.executor.data.get('preset'), options, "preset_value")
    elif cmd == 'preset_value':
        obj.executor.data['preset'] = data[2]
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'set_crf':
        await obj._select_option("CRF", obj.executor.data.get('crf'), [18, 20, 22, 23, 24, 26, 28], "crf_value")
    elif cmd == 'crf_value':
        obj.executor.data['crf'] = int(data[2])
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'set_audio_codec':
        await obj._select_option("Audio Codec", obj.executor.data.get('audio_codec'), ['aac', 'ac3', 'copy'], "audio_codec_value")
    elif cmd == 'audio_codec_value':
        obj.executor.data['audio_codec'] = data[2]
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'set_audio_channels':
        await obj._select_option("Audio Channels", obj.executor.data.get('audio_channels'), [1, 2, 6], "audio_channels_value")
    elif cmd == 'audio_channels_value':
        obj.executor.data['audio_channels'] = int(data[2])
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'set_bitrate':
        await obj._select_option("Bitrate", obj.executor.data.get('bitrate'), ['128k', '160k', '192k', '256k', '320k'], "bitrate_value")
    elif cmd == 'bitrate_value':
        obj.executor.data['bitrate'] = data[2]
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'back_to_conversion_options':
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'reset_conversion':
        obj.executor.data.update({'preset': 'medium', 'crf': 23, 'audio_channels': 2, 'audio_codec': 'aac', 'bitrate': '160k', 'video_codec': 'libx264'})
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'start_conversion':
        obj.event.set()
