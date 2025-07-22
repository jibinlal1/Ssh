from __future__ import annotations
from aiofiles.os import makedirs
from asyncio import gather
from mega import MegaApi, MegaListener, MegaRequest, MegaTransfer, MegaError
from secrets import token_urlsafe
from threading import Event

from bot import config_dict, task_dict, task_dict_lock, non_queued_dl, queue_dict_lock, LOGGER
from bot.helper.ext_utils.bot_utils import sync_to_async, is_premium_user
from bot.helper.ext_utils.files_utils import check_storage_threshold
from bot.helper.ext_utils.links_utils import get_mega_link_type
from bot.helper.ext_utils.status_utils import get_readable_file_size
from bot.helper.ext_utils.task_manager import stop_duplicate_check
from bot.helper.mirror_utils.status_utils.mega_download_status import MegaDownloadStatus
from bot.helper.mirror_utils.status_utils.queue_status import QueueStatus
from bot.helper.telegram_helper.message_utils import sendMessage, sendStatusMessage
from bot.helper.listeners import tasks_listener as task


class MegaAppListener(MegaListener):
    _NO_EVENT_ON = (MegaRequest.TYPE_LOGIN, MegaRequest.TYPE_FETCH_NODES)
    NO_ERROR = 'no error'

    def __init__(self, continue_event: Event, listener: task.TaskListener):
        self.continue_event = continue_event
        self.node = None
        self.public_node = None
        self.listener = listener
        self.is_cancelled = False
        self.error = None
        self.completed = False
        self.isFile = False
        self._bytes_transferred = 0
        self._speed = 0
        self._retry = 0
        self._name = ''
        super().__init__()

    @property
    def speed(self):
        return self._speed

    @property
    def downloaded_bytes(self):
        return self._bytes_transferred

    def onRequestFinish(self, api: MegaApi, request: MegaRequest, error):
        if self.is_cancelled:
            return
        if str(error).lower() != 'no error':
            self.error = error.copy()
            LOGGER.error('Mega onRequestFinishError: %s', self.error)
            self.continue_event.set()
            return
        request_type = request.getType()
        if request_type == MegaRequest.TYPE_LOGIN:
            api.fetchNodes()
        elif request_type == MegaRequest.TYPE_GET_PUBLIC_NODE:
            self.public_node = request.getPublicMegaNode()
            self._name = self.public_node.getName()
        elif request_type == MegaRequest.TYPE_FETCH_NODES:
            LOGGER.info('Fetching Root Node.')
            self.node = api.getRootNode()
            self._name = self.node.getName()
            LOGGER.info('Node Name: %s', self._name)
        if request_type not in self._NO_EVENT_ON or self.node and 'cloud drive' not in self._name.lower():
            self.continue_event.set()

    def onRequestTemporaryError(self, api: MegaApi, request: MegaRequest, error: MegaError):
        err_msg = error.toString()
        LOGGER.error('Mega Request error in %s', err_msg)
        if 'retrying' in err_msg.lower() and self._retry < 5:
            self._retry += 1
        else:
            if not self.is_cancelled:
                self.is_cancelled = True
            self.error = f'RequestTempError: {err_msg}'
            self.continue_event.set()

    def onTransferUpdate(self, api: MegaApi, transfer: MegaTransfer):
        if self.is_cancelled:
            api.cancelTransfer(transfer, None)
            self.continue_event.set()
            return
        self._speed = transfer.getSpeed()
        self._bytes_transferred = transfer.getTransferredBytes()

    def onTransferFinish(self, api: MegaApi, transfer: MegaTransfer, error):
        try:
            if self.is_cancelled:
                self.continue_event.set()
            elif transfer.isFinished() and (transfer.isFolderTransfer() or self.isFile):
                self.completed = True
                self.continue_event.set()
        except Exception as e:
            LOGGER.error(e)

    def onTransferTemporaryError(self, api: MegaApi, transfer: MegaRequest, error: MegaError):
        filen = transfer.getFileName()
        state = transfer.getState()
        errStr = error.toString()
        LOGGER.error('Mega download error in file %s %s: %s', transfer, filen, error)
        if state in [1, 4] and 'over quota' not in errStr.lower():
            # Sometimes MEGA (offical client) can't stream a node either and raises a temp failed error.
            # Don't break the transfer queue if transfer's in queued (1) or retrying (4) state [causes seg fault]
            return

        self.error = f'TransferTempError: {errStr} ({filen}'
        if not self.is_cancelled:
            self.is_cancelled = True
            self.continue_event.set()

    async def cancel_task(self):
        self.is_cancelled = True
        await self.listener.onDownloadError('Download Canceled by user!')


class AsyncExecutor:
    def __init__(self):
        self.continue_event = Event()

    def do(self, function, args):
        self.continue_event.clear()
        function(*args)
        self.continue_event.wait()


async def add_mega_download(listener: task.TaskListener, path: str):
    from_queue = False
    MEGA_EMAIL, MEGA_PASSWORD = config_dict['MEGA_EMAIL'], config_dict['MEGA_PASSWORD']

    executor = AsyncExecutor()
    folder_api = None
    api = MegaApi(None, None, None, config_dict['AUTHOR_NAME'])

    mega_listener = MegaAppListener(executor.continue_event, listener)
    api.addListener(mega_listener)

    if MEGA_EMAIL and MEGA_PASSWORD:
        await sync_to_async(executor.do, api.login, (MEGA_EMAIL, MEGA_PASSWORD))

    if get_mega_link_type(listener.link) == 'file':
        await sync_to_async(executor.do, api.getPublicNode, (listener.link,))
        node = mega_listener.public_node
        mega_listener.isFile = True
    else:
        folder_api = MegaApi(None, None, None, 'MLTB')
        folder_api.addListener(mega_listener)
        await sync_to_async(executor.do, folder_api.loginToFolder, (listener.link,))
        node = await sync_to_async(folder_api.authorizeNode, mega_listener.node)
    if mega_listener.error:
        if not mega_listener.is_cancelled:
            await sendMessage(str(mega_listener.error), listener.message)
        await sync_to_async(executor.do, api.logout, ())
        if folder_api:
            await sync_to_async(executor.do, folder_api.logout, ())
        return

    listener.name = listener.name or node.getName()
    megadl, zuzdl, leechdl, storage = config_dict['MEGA_LIMIT'], config_dict['ZIP_UNZIP_LIMIT'], config_dict['LEECH_LIMIT'], config_dict['STORAGE_THRESHOLD']
    file, name = await stop_duplicate_check(listener, mega_listener.isFile)
    if file:
        listener.name = name
        LOGGER.info('File/folder already in Drive!')
        await gather(listener.onDownloadError('File/folder already in Drive!', file), sync_to_async(executor.do, api.logout, ()))
        if folder_api:
            await sync_to_async(executor.do, folder_api.logout, ())
        return
    gid = token_urlsafe(8)
    size = api.getSize(node)
    msgerr = None
    megadl, zuzdl, leechdl, storage = config_dict['MEGA_LIMIT'], config_dict['ZIP_UNZIP_LIMIT'], config_dict['LEECH_LIMIT'], config_dict['STORAGE_THRESHOLD']
    if config_dict['PREMIUM_MODE'] and not is_premium_user(listener.user_id):
        mdl = zuzdl = leechdl = config_dict['NONPREMIUM_LIMIT']
        if mdl < megadl:
            megadl = mdl
    if megadl and size >= megadl * 1024**3:
        msgerr = f'Mega limit is {megadl}GB'
    if not msgerr:
        if zuzdl and any([listener.compress, listener.extract]) and size >= zuzdl * 1024**3:
            msgerr = f'Zip/Unzip limit is {zuzdl}GB'
        elif leechdl and listener.isLeech and size >= leechdl * 1024**3:
            msgerr = f'Leech limit is {leechdl}GB'
    if msgerr:
        LOGGER.info('File/folder size over the limit size!')
        await listener.onDownloadError(f'{msgerr}. File/folder size is {get_readable_file_size(size)}.')
        if folder_api:
            await sync_to_async(executor.do, folder_api.logout, ())

    async with task_dict_lock:
        task_dict[listener.mid] = MegaDownloadStatus(listener, mega_listener, size, gid)
    async with queue_dict_lock:
        non_queued_dl.add(listener.mid)

    if from_queue:
        LOGGER.info('Start Queued Download from Mega: %s', listener.name)
    else:
        await listener.onDownloadStart()
        if listener.multi <= 1:
            await sendStatusMessage(listener.message)
        LOGGER.info('Download from Mega: %s', listener.name)

    await makedirs(path, exist_ok=True)
    await sync_to_async(executor.do, api.startDownload, (node, path, listener.name, None, False, None))
    await sync_to_async(executor.do, api.logout, ())
    if folder_api:
        await sync_to_async(executor.do, folder_api.logout, ())

    if mega_listener.completed:
        await listener.onDownloadComplete()
    elif (error := mega_listener.error) and mega_listener.is_cancelled:
        await listener.onDownloadError(error)