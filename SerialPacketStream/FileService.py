from enum import IntEnum
from collections import deque
import time

from SerialPacketStream import Service, ServicePacket, ServicePacketListener, RawDataPacket, FramePacket
import SerialPacketStream.Codec as Codec

import logging
logger = logging.getLogger('default')

PacketCode = IntEnum('ActionCode',
    ['QUERY',
     'ACTION',
     'ACTION_RESPONSE',
     'OPEN',
     'CLOSE',
     'WRITE',
     'ABORT',
     'REQUEST',
     'LIST',
     'CD',
     'PWD',
     'FILE',
     'MOUNT',
     'UNMOUNT'], start = 0)


class QueryPacket(ServicePacket):
    packet_id = PacketCode.QUERY

    version_major : Codec.uint16_t
    version_minor : Codec.uint16_t
    version_patch : Codec.uint16_t
    compression_support : Codec.uint8_t
    compression_lookahead : Codec.uint8_t
    compression_window : Codec.uint8_t


class ActionResponsePacket(ServicePacket):
    Code = IntEnum( 'Code',
        ['SUCCESS',
         'BUSY',
         'FAIL',
         'IOERROR',
         'INVALID'], start = 0)
    packet_id = PacketCode.ACTION_RESPONSE

    code : Codec.uint8_t


class FileOpenPacket(ServicePacket):
    packet_id = PacketCode.OPEN

    dummy : Codec.uint8_t
    compression : Codec.uint8_t
    filename : Codec.cstring


class FileInfoPacket(ServicePacket):
    Meta = IntEnum( 'Meta',
        ['FOLDER',
         'FILE',
         'EOL'], start = 0)
    packet_id = PacketCode.FILE

    index : Codec.uint8_t
    meta : Codec.uint8_t
    size : Codec.uint32_t
    filename : Codec.cstring

class FileActionPacket(ServicePacket):
    Action = IntEnum ('Action',
        [], start = 0)
    action : Codec.uint8_t
    filename : Codec.cstring

class FileDataPacket(RawDataPacket):
    packet_id = PacketCode.WRITE


class FileService(Service):
    def __init__(self):
        super().__init__()
        self.register_packet(QueryPacket)
        self.register_packet(ActionResponsePacket)
        self.register_packet(FileInfoPacket)
        self.register_packet(FileDataPacket)

    def query_remote(self):
        self.send_packet(QueryPacket(version_major = 0, version_minor = 1, version_patch = 0,
            compression_support = True, compression_window = 8, compression_lookahead = 4))
        response = self.wait_packet(QueryPacket)
        logger.info("Remote FileService Version: {}.{}.{}".format(response.version_major, response.version_minor, response.version_patch))

    def mount(self):
        self.send_packet(ServicePacket(packet_id = PacketCode.MOUNT))
        response = self.wait_packet(ActionResponsePacket)
        if response.code == ActionResponsePacket.Code.SUCCESS:
            return True
        else:
            logger.warn("FileService.mount return error code {}".format(response.code))
            return False

    def unmount(self):
        self.send_packet(ServicePacket(packet_id = PacketCode.UNMOUNT))
        response = self.wait_packet(ActionResponsePacket)
        if response.code == ActionResponsePacket.Code.SUCCESS:
            return True
        else:
            logger.warn("FileService.unmount return error code {}".format(response.code))
            return False

    def open(self, filename, compression = False, dummy = False):
        self.send_packet(FileOpenPacket(filename=filename, compression=compression, dummy=dummy))
        response = self.wait_packet(ActionResponsePacket)
        if response.code == ActionResponsePacket.Code.SUCCESS:
            logger.info("File \'{}\' opened successfuly".format(filename))
            return True
        else:
            logger.warn("FileService.open \'{}\' returned error code: {}".format(filename, response.code))
            return False

    def close(self):
        self.send_packet(ServicePacket(packet_id = PacketCode.CLOSE))
        response = self.wait_packet(ActionResponsePacket)
        if response.code == ActionResponsePacket.Code.SUCCESS:
            return True
        else:
            logger.warn("FileService.close return error code {}".format(response.code))
            return False

    def abort(self):
        self.send_packet(ServicePacket(packet_id = PacketCode.ABORT))
        response = self.wait_packet(ActionResponsePacket)
        if response.code == ActionResponsePacket.Code.SUCCESS:
            return True
        else:
            logger.warn("FileService.abort return error code {}".format(response.code))
            return False

    def write(self, buffer, progress = None):
        if progress is not None:
            next(progress)
        byte_count = 0

        for x in (buffer[i:i + self.max_block_size()] for i in range(0, len(buffer), self.max_block_size())):
            # make sure the last packet needed for this buffer is sent as a DATA packet not DATA_NACK
            packet_type = FramePacket.Type.DATA_NACK if len(x) == self.max_block_size() and len(self.tx_queue) < 64 else FramePacket.Type.DATA
            self.send_packet(RawDataPacket(packet_id = PacketCode.WRITE, data = x), packet_type = packet_type, block = True) # todo: timeout and error

            byte_count += len(x)
            if progress is not None and packet_type == FramePacket.Type.DATA:
                #only update progress after a packet was confirmed delivered (only DATA types can be blocked until acked)
                progress.send(byte_count)

        return byte_count

    def ls(self):
        listing = []
        with ServicePacketListener(self, FileInfoPacket) as packet_queue:
            self.send_packet(ServicePacket(packet_id = PacketCode.LIST))
            while(True): #todo timeout
                if len(packet_queue):
                    packet = packet_queue.popleft()
                    if packet.meta != FileInfoPacket.Meta.EOL:
                        listing.append(packet)
                    else:
                        break
                time.sleep(0.000001)

        return listing

    def cd(self, filename):
        self.send_packet(FileActionPacket(packet_id = PacketCode.CD, filename = filename))
        response = self.wait_packet(ActionResponsePacket) # todo: timeout
        if response.code == ActionResponsePacket.Code.SUCCESS:
            return True
        logger.warn("FileService.cd({}) return error code {}".format(filename, response.code))
        return False

    def pwd(self):
        self.send_packet(ServicePacket(packet_id = PacketCode.PWD))
        response = self.wait_packet(FileInfoPacket) # todo: timeout
        return response.filename

    def put(self, src, dst=None, compression=False, dummy=False, progress=None):
        if dst is None:
            dst = src

        self.open(dst, compression=compression, dummy=dummy)
        with open(src, "rb") as f:  #todo: lazy loading using generator
            self.write(f.read(), progress=progress)
        self.close()

    # implement read api, this uses temporary request api
    # todo: loads of error checking and timeouts
    def get(self, src, dst=None, compression=False, dummy=False, progress=None):
        if progress is not None:
            next(progress)

        if dst is None:
            dst = src

        bytes_read = 0

        self.send_packet(FileOpenPacket(packet_id = PacketCode.REQUEST, filename=src, compression=compression, dummy=dummy))
        response = self.wait_packet(ActionResponsePacket)
        if response.code == ActionResponsePacket.Code.SUCCESS:
            with open(dst, 'wb') as f:
                packet = self.wait_packet(FileDataPacket)
                while len(packet.data) == 64: #todo: 64 is the clients max packet payload size, needs added to transport layer query? ..
                    f.write(packet.data)
                    bytes_read += len(packet.data)
                    if progress is not None:
                        progress.send(bytes_read)
                    packet = self.wait_packet(FileDataPacket)
                bytes_read += len(packet.data)
                if progress is not None:
                    progress.send(bytes_read)
                f.write(packet.data)
        else:
            logger.warn("Request return error code {}".format(response.code))
