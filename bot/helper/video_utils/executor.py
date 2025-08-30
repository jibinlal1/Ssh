from __future__ import annotations
from aiofiles import open as aiopen
from aiofiles.os import path as aiopath, makedirs, listdir
from aioshutil import move
from ast import literal_eval
from asyncio import create_subprocess_exec, sleep, gather, Event
from asyncio.subprocess import PIPE
from natsort import natsorted
from os import path as ospath, walk
from time import time

from bot import config_dict, task_dict, task_dict_lock, queue_dict_lock, non_queued_dl, LOGGER, VID_MODE, FFMPEG_NAME
from bot.helper.ext_utils.bot_utils import sync_to_async, cmd_exec, new_task
from bot.helper.ext_utils.files_utils import get_path_size, clean_target
from bot.helper.ext_utils.links_utils import get_url_name
from bot.helper.ext_utils.media_utils import get_document_type, get_media_info, FFProgress
from bot.helper.ext_utils.task_manager import check_running_tasks
from bot.helper.listeners import tasks_listener as task
from bot.helper.mirror_utils.status_utils.ffmpeg_status import FFMpegStatus
from bot.helper.mirror_utils.status_utils.queue_status import QueueStatus
from bot.helper.telegram_helper.message_utils import sendStatusMessage, update_status_message
from bot.helper.video_utils.extra_selector import ExtraSelect


async def get_metavideo(video_file):
    stdout, stderr, rcode = await cmd_exec(['ffprobe', '-hide_banner', '-print_format', 'json', '-show_format', '-show_streams', video_file])
    if rcode != 0:
        LOGGER.error(stderr)
        return {}, {}
    metadata = literal_eval(stdout)
    return metadata.get('streams', {}), metadata.get('format', {})


class VidEcxecutor(FFProgress):
    def __init__(self, listener: task.TaskListener, path: str, gid: str, metadata=False):
        self.data = None
        self.event = Event()
        self.listener = listener
        self.path = path
        self.name = ''
        self.outfile = ''
        self.size = 0
        self._metadata = metadata
        self._up_path = path
        self._gid = gid
        self._start_time = time()
        self._files = []
        self._qual = {'1080p': '1920', '720p': '1280', '540p': '960', '480p': '854', '360p': '640'}
        super().__init__()
        self.is_cancel = False

    async def _queue(self, update=False):
        if self._metadata:
            add_to_queue, event = await check_running_tasks(self.listener.mid)
            if add_to_queue:
                LOGGER.info('Added to Queue/Download: %s', self.name)
                async with task_dict_lock:
                    task_dict[self.listener.mid] = QueueStatus(self.listener, self.size, self._gid, 'dl')
                await self.listener.onDownloadStart()
                if update:
                    await sendStatusMessage(self.listener.message)
                await event.wait()
                async with task_dict_lock:
                    if self.listener.mid not in task_dict:
                        self.is_cancel = True
                        return
            async with queue_dict_lock:
                non_queued_dl.add(self.listener.mid)

    async def execute(self):
        self._is_dir = await aiopath.isdir(self.path)
        self.mode, self.name, kwargs = self.listener.vidMode
        if not self._metadata and self.mode in config_dict['DISABLE_MULTI_VIDTOOLS']:
            if path := await self._get_video():
                self.path = path
            else:
                return self._up_path
        if self._metadata:
            if not self.name:
                self.name = get_url_name(self.path)
            if not self.name.upper().endswith(('MP4', 'MKV')):
                self.name += '.mkv'
            try:
                self.size = int(self._metadata[1]['size'])
            except Exception as e:
                LOGGER.error(e)
                await self.listener.onDownloadError('Invalid data, check the link!')
                return

        try:
            match self.mode:
                case 'vid_vid':
                    return await self._merge_vids()
                case 'vid_aud':
                    return await self._merge_auds()
                case 'vid_sub':
                    return await self._merge_subs(**kwargs)
                case 'trim':
                    return await self._vid_trimmer(**kwargs)
                case 'watermark':
                    return await self._vid_marker(**kwargs)
                case 'compress':
                    return await self._vid_compress(**kwargs)
                case 'subsync':
                    return await self._subsync(**kwargs)
                case 'rmstream':
                    return await self._rm_stream()
                case 'swap_stream':
                    return await self._swap_streams()
                case 'extract':
                    return await self._vid_extract()
                case _:
                    return await self._vid_convert()
        except Exception as e:
            LOGGER.error(e, exc_info=True)
        return self._up_path

    @new_task
    async def _start_handler(self, *args):
        await sleep(0.5)
        await ExtraSelect(self).get_buttons(*args)

    async def _send_status(self, status='wait'):
        async with task_dict_lock:
            task_dict[self.listener.mid] = FFMpegStatus(self.listener, self, self._gid, status)
        if self._metadata and status == 'wait':
            await sendStatusMessage(self.listener.message)

    async def _get_files(self):
        file_list = []
        if self._metadata:
            file_list.append(self.path)
        elif await aiopath.isfile(self.path):
            if (await get_document_type(self.path))[0]:
                file_list.append(self.path)
        else:
            for dirpath, _, files in await sync_to_async(walk, self.path):
                for file in natsorted(files):
                    file = ospath.join(dirpath, file)
                    if (await get_document_type(file))[0]:
                        file_list.append(file)
        return file_list

    async def _get_video(self):
        if not self._is_dir and (await get_document_type(self.path))[0]:
            return self.path
        for dirpath, _, files in await sync_to_async(walk, self.path):
            for file in natsorted(files):
                file = ospath.join(dirpath, file)
                if (await get_document_type(file))[0]:
                    return file

    async def _final_path(self, outfile=''):
        if self._metadata:
            self._up_path = outfile or self.outfile
        else:
            scan_dir = self._up_path if self._is_dir else ospath.split(self._up_path)[0]
            for dirpath, _, files in await sync_to_async(walk, scan_dir):
                for file in files:
                    if file.endswith(tuple(self.listener.extensionFilter)):
                        await clean_target(ospath.join(dirpath, file))

            all_files = []
            for dirpath, _, files in await sync_to_async(walk, scan_dir):
                all_files.extend((dirpath, file) for file in files)
            if len(all_files) == 1:
                self._up_path = ospath.join(*all_files[0])

        return self._up_path

    async def _name_base_dir(self, path, info: str=None, multi: bool=False):
        base_dir, file_name = ospath.split(path)
        if not self.name or multi:
            if info:
                if await aiopath.isfile(path):
                    file_name = file_name.rsplit('.', 1)[0]
                file_name += f'_{info}.mkv'
            self.name = file_name
        if not self.name.upper().endswith(('MP4', 'MKV')):
            self.name += '.mkv'
        return base_dir if await aiopath.isfile(path) else path

    async def _run_cmd(self, cmd, status='prog'):
        await self._send_status(status)
        self.listener.suproc = await create_subprocess_exec(*cmd, stderr=PIPE)
        _, code = await gather(self.progress(status), self.listener.suproc.wait())
        if code == 0:
            if not self.listener.seed:
                await gather(*[clean_target(file) for file in self._files])
            self._files.clear()
            return True
        if self.listener.suproc == 'cancelled' or code == -9:
            self.is_cancel = True
        else:
            LOGGER.error('%s. Failed to %s: %s', (await self.listener.suproc.stderr.read()).decode().strip(), VID_MODE[self.mode], self.outfile)
            self._files.clear()

    async def _vid_extract(self):
        # Your unchanged code here...
        pass

    # Other unchanged methods ...

    async def _vid_convert(self):
        file_list = await self._get_files()
        multi = len(file_list) > 1
        if not file_list:
            return self._up_path

        if self._metadata:
            base_dir = self.listener.dir
            await makedirs(base_dir, exist_ok=True)
            streams = self._metadata[0]
        else:
            main_video = file_list[0]
            base_dir, (streams, _), self.size = await gather(
                self._name_base_dir(main_video, 'Convert', multi),
                get_metavideo(main_video),
                get_path_size(main_video)
            )
        self._start_handler(streams)
        await gather(self._send_status(), self.event.wait())
        await self._queue()
        if self.is_cancel:
            return
        if not self.data:
            return self._up_path

        resolution = self.data.get('resolution', '720p')
        scale_width = self._qual.get(resolution, '1280')

        self.outfile = self._up_path

        for file in file_list:
            self.path = file
            if not self._metadata:
                _, self.size = await gather(self._name_base_dir(self.path, f'Convert-{self.data}', multi), get_path_size(self.path))
            self.outfile = ospath.join(base_dir, self.name)
            self._files.append(self.path)
            cmd = [
                FFMPEG_NAME, '-hide_banner', '-ignore_unknown', '-y', '-i', self.path,
                '-map', '0:v:0',
                '-vf', f'scale={scale_width}:-2',
                '-map', '0:a:?', '-map', '0:s:?',
                '-c:a', 'copy', '-c:s', 'copy', self.outfile
            ]
            await self._run_cmd(cmd)
            if self.is_cancel:
                return

        return await self._final_path()

    # All other methods exactly as in your original code
