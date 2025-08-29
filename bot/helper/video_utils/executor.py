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

        if self.mode == 'convert':
            self.data = kwargs
            await self._vid_convert()
            return self._up_path
            
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
            if hasattr(self.listener, 'extensionFilter'):
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
            if not getattr(self.listener, 'seed', False):
                await gather(*[clean_target(file) for file in self._files])
            self._files.clear()
            return True
        if self.listener.suproc == 'cancelled' or code == -9:
            self.is_cancel = True
        else:
            stderr = (await self.listener.suproc.stderr.read()).decode().strip()
            LOGGER.error('%s. Failed to %s: %s', stderr, VID_MODE.get(self.mode, 'process'), self.outfile)
            self._files.clear()
        return False

    async def _vid_convert(self):
        extra_selector = ExtraSelect(self)
        await extra_selector.get_buttons()

        if extra_selector.is_cancel:
            self.is_cancel = True
            return

        file_list = await self._get_files()
        if not file_list:
            return

        await self._queue()
        if self.is_cancel:
            return

        quality = self.data['quality']
        
        base_dir = self.listener.dir
        await makedirs(base_dir, exist_ok=True)
        
        self.path = file_list[0]
        self.size = await get_path_size(self.path)
        
        base_name = ospath.basename(self.path).rsplit('.', 1)[0]
        self.name = f"{base_name}_{quality}.mkv"
        self.outfile = ospath.join(base_dir, self.name)
        
        self._files.append(self.path)
        
        cmd = [FFMPEG_NAME, '-hide_banner', '-ignore_unknown', '-y', '-i', self.path,
               '-vf', f'scale={self._qual[quality]}:-2',
               '-c:v', self.listener.vcodec,
               '-preset', self.listener.preset,
               '-crf', self.listener.crf,
               '-b:v', self.listener.vbitrate,
               '-map', '0:v:0',
               '-map', '0:a:?',
               '-map', '0:s:?',
               '-c:a', 'copy',
               '-c:s', 'copy',
               self.outfile]
               
        if await self._run_cmd(cmd):
            self._up_path = self.outfile

    async def _vid_extract(self):
        if file_list := await self._get_files():
            if self._metadata:
                base_dir = ospath.join(self.listener.dir, self.name.split('.', 1)[0])
                await makedirs(base_dir, exist_ok=True)
                streams = self._metadata[0]
            else:
                main_video = file_list[0]
                base_dir, (streams, _), self.size = await gather(self._name_base_dir(main_video, 'Extract', len(file_list) > 1),
                                                                 get_metavideo(main_video), get_path_size(main_video))
            self._start_handler(streams)
            await gather(self._send_status(), self.event.wait())
        else:
            return self._up_path

        await self._queue()
        if self.is_cancel:
            return
        if not self.data:
            return self._up_path

        if await aiopath.isfile(self._up_path) or self._metadata:
            base_name = self.name if self._metadata else ospath.basename(self.path)
            self._up_path = ospath.join(base_dir, f'{base_name.rsplit(".", 1)[0]} (EXTRACT)')
            await makedirs(self._up_path, exist_ok=True)
            base_dir = self._up_path

        task_files = []
        for file in file_list:
            self.path = file
            if not self._metadata:
                self.size = await get_path_size(self.path)
            base_name = self.name if self._metadata else ospath.basename(self.path)
            base_name = base_name.rsplit('.', 1)[0]
            extension = dict(zip(['audio', 'subtitle', 'video'], self.data['extension']))

            def _build_command(stream_data):
                cmd = [FFMPEG_NAME, '-hide_banner', '-ignore_unknown', '-i', self.path, '-map', f'0:{stream_data["map"]}']
                if self.data.get('alt_mode'):
                    if stream_data['type'] == 'audio':
                        cmd.extend(('-b:a', '156k'))
                    elif stream_data['type'] == 'video':
                        cmd.extend(('-c', 'copy'))
                else:
                    cmd.extend(('-c', 'copy'))
                cmd.extend((self.outfile, '-y'))
                return cmd

            keys = self.data['key']
            if isinstance(keys, int):
                stream_data = self.data['stream'][keys]
                self.name = f'{base_name}_{stream_data["lang"].upper()}.{extension[stream_data["type"]]}'
                self.outfile = ospath.join(base_dir, self.name)
                cmd = _build_command(stream_data)
                if await self._run_cmd(cmd):
                    task_files.append(file)
                else:
                    await move(file, self._up_path)
                if self.is_cancel:
                    return
            else:
                ext_all = []
                for stream_data in self.data['stream'].values():
                    for key in keys:
                        if key == stream_data['type']:
                            self.name = f'{base_name}_{stream_data["lang"].upper()}.{extension[key]}'
                            self.outfile = ospath.join(base_dir, self.name)
                            cmd = _build_command(stream_data)
                            if await self._run_cmd(cmd):
                                ext_all.append(file)
                            if self.is_cancel:
                                return
                if any(ext_all):
                    task_files.append(file)
                else:
                    await move(file, self._up_path)

        await gather(*[clean_target(file) for file in task_files])
        return await self._final_path(self._up_path)

    async def _rm_stream(self):
        # ... (rest of your existing methods)
        pass

    async def _vid_trimmer(self, start_time, end_time):
        # ...
        pass

    async def _subsync(self, type: str='sync_manual'):
        # ...
        pass

    async def _vid_compress(self, quality=None):
        # ...
        pass

    async def _vid_marker(self, **kwargs):
        # ...
        pass

    async def _merge_vids(self):
        # ...
        pass

    async def _merge_auds(self):
        # ...
        pass

    async def _merge_subs(self, **kwargs):
        # ...
        pass

    async def _swap_streams(self):
        # ...
        pass
