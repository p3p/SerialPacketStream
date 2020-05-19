from enum import IntEnum

from SerialPacketStream import Service, ServicePacket, RawDataPacket, FramePacket
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


class FileService(Service):
    def __init__(self):
        super().__init__()
        self.register_packet(QueryPacket)
        self.register_packet(ActionResponsePacket)
        self.register_packet(FileInfoPacket)

    def query_remote(self):
        self.send_packet(QueryPacket(version_major = 0, version_minor = 1, version_patch = 0,
            compression_support = True, compression_window = 8, compression_lookahead = 4))
        response = self.wait_packet(QueryPacket)
        logger.info("Remote FileService Version: {}.{}.{}".format(response.version_major, response.version_minor, response.version_patch))

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
            logger.info("file closed successfully")
        else:
            logger.error('close barfed')

    def ls(self):
        self.send_packet(ServicePacket(packet_id = PacketCode.LIST))
        listing = []
        response = self.wait_packet(FileInfoPacket)
        listing.append(response)
        while True: # todo: timeout
            response = self.wait_packet(FileInfoPacket)
            if response.meta == FileInfoPacket.Meta.EOL:
                break
            listing.append(response)
        return listing

    def write(self, buffer, progress = None):
        if progress is not None:
            next(progress)
        byte_count = 0

        for x in (buffer[i:i + self.max_block_size()] for i in range(0, len(buffer), self.max_block_size())):
            # make sure the last packet needed for this buffer is sent as a DATA packet not DATA_NACK
            packet_type = FramePacket.Type.DATA_NACK if len(x) == self.max_block_size() and len(self.tx_queue) < 64 else FramePacket.Type.DATA
            self.send_packet(RawDataPacket(packet_id = PacketCode.WRITE, data = x), packet_type = packet_type, block = True)

            byte_count += len(x)
            if progress is not None and packet_type == FramePacket.Type.DATA:
                #only update progress after a packet was confirmed delivered (only DATA types can be blocked until acked)
                progress.send(byte_count)
