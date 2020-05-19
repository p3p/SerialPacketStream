from enum import IntEnum
import struct

import SerialPacketStream.Codec as Codec
import SerialPacketStream.Checksum as Checksum

Status = IntEnum( 'Status',
    ['NONE',
     'RECEIVING',
     'TRANSMITING',
     'COMPLETE',
     'VALID',
     'PENDING',
     'BUFFERED',
     'INTRANSIT',
     'RETRY',
     'FAILED'], start = 0)

Type = IntEnum('Type', ['RESPONSE', 'DATA', 'DATA_NACK', 'DATA_FAF'], start = 0)

class frame_token_t(Codec.codec_type):
    datatype = int
    fmt = struct.Struct('<H')

    @classmethod
    def encode(cls, value, buffer):
        return cls.fmt.pack(0xACB5 | int(value) << 8)

    @classmethod
    def decode(cls, buffer):
        value = cls.fmt.unpack_from(buffer.memory, buffer.offset)
        buffer.offset += cls.fmt.size
        return (value[0] >> 8) & 0x03

class Data(object):

    class Header(Codec.Serializable):
        SIZE  = 8
        HEADER_TOKEN = 0xACB5
        packet_type : frame_token_t
        sync : Codec.uint8_t
        channel : Codec.uint8_t
        packet_id : Codec.uint8_t
        payload_size : Codec.uint16_t
        checksum : Codec.crc8_t

    class Footer(Codec.Serializable):
        SIZE = 2
        checksum : Codec.uint16_t

    def __bytes__(self):
        data = bytearray()
        data += bytes(self.header)
        data += self.data
        self.footer = Data.Footer(Checksum.crc16(0, self.data))
        data += bytes(self.footer)
        return bytes(data)

    @classmethod
    def from_bytearray(cls, data):
        packet = cls()
        packet.header = Data.Header.from_bytes(data[0:8])

        if len(data) > Data.Header.SIZE:
            packet.data = data[8:-2]
            packet.footer = Data.Footer.from_bytes(data[-2:])

        return packet

    @classmethod
    def create(cls, packet_type, channel, packet_id, payload):
        packet = cls()
        packet.header = Data.Header(packet_type, 0, channel, packet_id, len(payload))
        packet.data = payload
        return packet

    def __init__(self):
        self.header = None
        self.data = bytearray()
        self.footer = None
        self.status = Status.NONE
        self.response = None

    def __str__(self):
        payload_string = ", {}, {}".format([hex(x) if i < 9 else "..." for i, x in enumerate(self.data) if i < 10], self.footer) if self.header.payload_size else ""
        return "BasePacket(Status: {}, {}{})".format(Status(self.status)._name_, self.header, payload_string)


class Response(Codec.Serializable):
    SIZE = 5
    Type = IntEnum('Type', ['ACK', 'NACK', 'NYET', 'REJECT'], start = 0)

    packet_type : frame_token_t
    response : Codec.uint8_t
    sync_id : Codec.uint8_t
    checksum : Codec.crc8_t
