import struct
from collections import namedtuple

import SerialPacketStream.Checksum as Checksum

class basic_type(object):
    pass

class basic_array(object):
    def __init__(self, datatype, length, default=None):
        self.datatype = datatype
        self.length = length
        length = length if isinstance(length, int) else 0
        self.default = [datatype()]*length if default is None else [default]*length

class codec_type(object):
    size = -1
    datatype = None

    @classmethod
    def encode(cls, value, buffer = None):
        raise(NotImplementedError())

    @classmethod
    def decode(cls, buffer):
        raise(NotImplementedError())


class uint8_t(basic_type):
    token = 'B'
    datatype = int
    size = 1
class int8_t(basic_type):
    token = 'b'
    datatype = int
    size = 1
class uint16_t(basic_type):
    token = 'H'
    datatype = int
    size = 2
class int16_t(basic_type):
    token = 'h'
    datatype = int
    size = 2
class uint32_t(basic_type):
    token = 'I'
    datatype = int
    size = 4
class int32_t(basic_type):
    token = 'i'
    datatype = int
    size = 4
class uint64_t(basic_type):
    token = 'Q'
    datatype = int
    size = 8
class int64_t(basic_type):
    token = 'q'
    datatype = int
    size = 8
class float_t(basic_type):
    token = 'f'
    datatype = float
    size = 4
class double_t(basic_type):
    token = 'd'
    datatype = float
    size = 8

class cstring(codec_type):
    datatype = str

    @classmethod
    def encode(cls, value, buffer):
        return bytes(value, 'utf-8') + b'\0'

    @classmethod
    def decode(cls, buffer):
        length = buffer.index_of(0)
        start = buffer.offset
        if not length < 0:
            buffer.offset = length + 1
            return bytes(buffer.memory[start:length]).decode('utf-8')
        else:
            #raise(RuntimeError("cstring decode failed"))
            buffer.offset = len(buffer.memory)
            return bytes(buffer.memory[start:]).decode('utf-8')

class bytearray_t(codec_type):
    datatype = bytearray

    @classmethod
    def encode(cls, value, buffer):
        return bytes(value)

    @classmethod
    def decode(cls, buffer):
        return bytearray(buffer.memory)

class crc8_t(codec_type):
    datatype = int
    fmt = struct.Struct('<B')

    @classmethod
    def encode(cls, value, buffer):
        return cls.fmt.pack(Checksum.crc8(0, buffer))

    @classmethod
    def decode(cls, buffer):
        value = cls.fmt.unpack_from(buffer.memory, buffer.offset)[0]
        buffer.offset += cls.fmt.size
        return value

class crc16_t(codec_type):
    datatype = int
    fmt = struct.Struct('<H')

    @classmethod
    def encode(cls, value, buffer):
        return cls.fmt.pack(Checksum.crc16(0, buffer))

    @classmethod
    def decode(cls, buffer):
        value = cls.fmt.unpack_from(buffer.memory, buffer.offset)[0]
        buffer.offset += cls.fmt.size
        return value

fmt_block = namedtuple('FormatBlock', 'datatype, size, length, constant')

class OffsetBuffer(object):
    def __init__(self, buffer, offset = 0):
        self.offset = offset
        #self.memory = memoryview(buffer)
        self.memory = buffer

    def reset(self):
        self.offset = 0

    def remainder(self):
        return bytes(self.memory[self.offset:])

    def index_of(self, value):
        index = self.offset
        while index < len(self.memory):
            if self.memory[index] is value:
                return index
            index += 1
        return -1

def build_struct_format(cls):
    cls.fmt_list = cls.build_fmt()
    return cls

class Serializable(object):
    __fullqualname__ = '{}.{}'.format(__module__, __qualname__)
    fmt_list = None
    def __init__(self, *args, **options):
        type(self).__annotations__ = getattr(type(self), '__annotations__', {})
        for i,(k,v) in enumerate(type(self).__annotations__.items()):
            v_type = v if isinstance(v, type) else type(v)
            default_val = getattr(type(self), k, None)
            if issubclass(v_type, basic_type):
                setattr(self, k, args[i] if i < len(args) else options.get(k) if k in options else default_val if default_val is not None else v.datatype())
            elif v_type is basic_array:
                length = v.length if isinstance(v.length, int) else getattr(self, v.length)
                datatype_type = v.datatype if isinstance(v.datatype, type) else type(v.datatype)
                default_type = datatype_type.datatype if issubclass(datatype_type, (basic_type, codec_type)) else datatype_type
                setattr(self, k, args[i] if i < len(args) else options.get(k) if k in options else default_val if default_val is not None else [default_type()]*length)
            elif issubclass(v_type, Serializable):
                setattr(self, k, args[i] if i < len(args) else options.get(k) if k in options else default_val if default_val is not None else v())
            elif issubclass(v_type, codec_type):
                setattr(self, k, args[i] if i < len(args) else options.get(k) if k in options else default_val if default_val is not None else v.datatype())
            else:
                raise(ValueError("__annotations__ values muse be of type basic_type, array_type, codec_type or Serializable not {}".format(v)))

    @classmethod
    def build_fmt(cls):
        def value_to_tokens(key, value):
            v_type = value if isinstance(value, type) else type(value)
            if issubclass(v_type, basic_type):
                return (value.token)
            elif v_type is basic_array:
                constant = True if isinstance(value.length, int) else False
                token = value.datatype.token if issubclass(v_type, basic_type) else ''
                return fmt_block(value, struct.calcsize(token), value.length, constant)
            elif issubclass(v_type, Serializable) or issubclass(v_type, codec_type):
                return (cls.__annotations__.get(key))

        fmt_blocks = []
        fmt_string = ''
        for k, v in cls.__annotations__.items():
            value = value_to_tokens(k, v)
            if isinstance(value, str):
                fmt_string += value
            else:
                if fmt_string:
                    fmt_blocks.append(fmt_block(struct.Struct('<' + fmt_string), struct.calcsize('<' + fmt_string), 1, True))
                    fmt_string = ''
                fmt_blocks.append(value)

        if fmt_string:
            fmt_blocks.append(fmt_block(struct.Struct('<' + fmt_string), struct.calcsize('<' + fmt_string), 1, True))
        return fmt_blocks

    def update_auto_variables(self):
        for k,v in type(self).__annotations__.items():
            if isinstance(v, basic_array) and isinstance(v.length, str):
                setattr(self, v.length, len(getattr(self, k)))

    def __bytes__(self):
        # todo: refactor to use compressed format list
        buffer = bytearray()
        self.update_auto_variables()
        def pack_value(datatype, value):
            if isinstance(value, Serializable):
                return bytes(value)
            elif isinstance(datatype, basic_array):
                buf = bytearray()
                for v in value:
                    buf.extend(pack_value(datatype.datatype, v))
                return buf
            elif isinstance(datatype, type) and issubclass(datatype, codec_type):
                return datatype.encode(value, buffer=buffer)
            else:
                # todo: make less stupidly inefficient using the fmt list
                return struct.pack('<' + datatype.token, value)

        for k,v in self.__annotations__.items():
            buffer.extend(pack_value(v, getattr(self, k)))

        return bytes(buffer)

    @classmethod
    def from_offsetbuffer(cls, buffer):
        #todo: format list can be compressed for all constant groups, posibly recursivly
        cls.fmt_list = cls.build_fmt() if cls.fmt_list == None else cls.fmt_list
        args = []

        def unpack_value(value):
            if isinstance(value, type) and issubclass(value, Serializable):
                return (value.from_offsetbuffer(buffer),)
            elif isinstance(value, fmt_block) and isinstance(value.datatype, struct.Struct):
                ret = value.datatype.unpack_from(buffer.memory, buffer.offset)
                buffer.offset += value.size
                return ret
            elif isinstance(value, fmt_block) and isinstance(value.datatype, basic_array):
                length = value.length
                if isinstance(length, str):
                    length = args[list(cls.__annotations__.keys()).index(length)]
                return ([unpack_value(value.datatype.datatype)[0] for _ in range(length)],)
            elif isinstance(value, type) and issubclass(value, codec_type):
                return (value.decode(buffer),)
            elif isinstance(value, type) and issubclass(value, basic_type):
                ret = struct.unpack_from('<' + value.token, buffer.memory, buffer.offset)
                buffer.offset += value.size
                return ret
            else:
                raise(RuntimeError("unpack_value", value))

        for l in cls.fmt_list:
            args.extend(unpack_value(l))

        return cls(*args)

    def make_tuple(self):
        self.update_auto_variables()
        name = type(self).__name__
        field_names = ','.join([k for k, v in type(self).__annotations__.items()])
        fields = []
        for k,v in type(self).__annotations__.items():
            if isinstance(v, type) and issubclass(v, Serializable):
                fields.append(getattr(self, k).make_tuple())
            else:
                fields.append(getattr(self, k))
        return namedtuple(name, field_names)._make(fields)

    @classmethod
    def from_bytes(cls, buffer):
        return cls.from_offsetbuffer(OffsetBuffer(buffer))

    def __repr__(self):
        return str(self.make_tuple())

    # todo: typecheck the values being asigned
    def __setattr__(self, name, value):
        # if name in self.__annotations__:
        #     feildtype = self.__annotations__.get(name)
        #     feildtype = feildtype if isinstance(feildtype, type) else type(feildtype)
        #     print('setting {} : {} = {}'.format(name, feildtype, value))
        #     super().__setattr__(name, value)
        # else:
        super().__setattr__(name, value)

