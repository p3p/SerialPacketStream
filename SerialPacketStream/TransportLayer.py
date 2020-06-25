import time
from collections import deque
from threading import Thread
import struct

import logging
logger = logging.getLogger('default')

import SerialPacketStream.FramePacket as FramePacket
import SerialPacketStream.Codec as Codec
import SerialPacketStream.Checksum as Checksum

class ServicePacket(Codec.Serializable):
    __fullqualname__ = '{}.{}'.format(__module__, __qualname__)
    frame_packet = None
    packet_id = None

    def __init__(self, *args, **options):
        self.packet_id = options.get('packet_id') if 'packet_id' in options else type(self).packet_id
        super().__init__(*args, **options)

    def status(self):
        if self.frame_packet == None:
            return FramePacket.Status.PENDING
        return self.frame_packet.status

class RawDataPacket(ServicePacket):
    data : Codec.bytearray_t

class ServicePacketListener(object):
    def __init__(self, service, packet_cls):
        self.service = service
        self.packet_cls = packet_cls
    def __enter__(self):
        return self.service.start_listening(self.packet_cls)
    def __exit__(self, type, value, traceback):
        self.service.finish_listening(self.packet_cls)
        return False

class Service(object):
    __fullqualname__ = '{}.{}'.format(__module__, __qualname__)
    def __init__(self):
        type(self).__fullqualname__ = "{}.{}".format(type(self).__module__, type(self).__qualname__)
        self.rx_queue = deque()
        self.tx_queue = deque()
        self.listeners = {}
        self.packets = {}
        self._transport_layer = None

    def register_packet(self, packet_cls, packet_id = None):
        packet_id = packet_cls.packet_id if packet_id == None else packet_id
        if not issubclass(packet_cls, ServicePacket):
            raise TypeError("Expected subclass: {}".format(ServicePacket))
        if not 0 <= packet_id <= 255:
            raise ValueError("packet_id not in range (0..255)")
        logger.debug("{} registered packet id: {}".format(type(self).__fullqualname__, packet_id))
        self.packets[packet_id] = packet_cls

    def send_packet(self, packet, packet_type = FramePacket.Type.DATA, block = False):
        if not isinstance(packet, ServicePacket):
            raise TypeError("Expected: {}".format(ServicePacket))

        self.tx_queue.append((packet_type, packet))
        # todo timeout
        while block and packet_type == FramePacket.Type.DATA and packet.status() != FramePacket.Status.COMPLETE:
            time.sleep(0.0000001) # this just releases the thread timeslice

        return packet

    def start_listening(self, packet_cls):
        deq = deque()
        self.listeners[packet_cls] = deq
        return deq

    def finish_listening(self, packet_cls):
        del self.listeners[packet_cls]

    def dispatch(self, packet):
        if type(packet) in self.listeners:
            self.listeners[type(packet)].append(packet)
        else:
            self.rx_queue.append(packet)

    def wait_packet(self, packet_cls):
        if not issubclass(packet_cls, ServicePacket):
            raise TypeError("Expected subclass: {}".format(ServicePacket))

        while True: # todo: timeout
            while len(self.rx_queue):
                p = self.rx_queue.popleft()
                if type(p) is packet_cls:
                    return p
            time.sleep(0.0001) # allow time for packets to arraive

        return None

    def max_block_size(self):
        return self._transport_layer.sync_max_block_size


class SyncPacket(ServicePacket):
    packet_id = 5

    version_major : Codec.uint16_t
    version_minor : Codec.uint16_t
    version_patch : Codec.uint16_t

    serial_buffer_size : Codec.uint16_t
    payload_buffer_size : Codec.uint16_t


class ClosePacket(ServicePacket):
    packet_id = 7


class TransportLayerControl(Service):
    def __init__(self):
        super().__init__()
        self.register_packet(SyncPacket)
        self.register_packet(ClosePacket)

    def synchronise(self):
        # Packets cant be sent by Services until synchronised so work around this
        # by going directly through the transport layer
        logger.info("Switching Marlin to Binary Protocol...")
        self._transport_layer.stream_write(b"\nM28B1\n")
        logger.info("Atempting binary stream synchronisation...")
        packet = SyncPacket(*self._transport_layer.VERSION, 512, 512)
        packet.frame_packet = self._transport_layer.send_packet(FramePacket.Type.DATA_FAF, 0, packet.packet_id, bytes(packet))

    def disconnect(self):
        self.send_packet(ClosePacket(), block = True)

    def reset_mcu(self):
        logger.warn("Resetting the remote device will drop all currently buffered packets")
        self.send_packet(ServicePacket(packet_id = 8), block = False)
        time.sleep(1)
        #self._transport_layer.reconnect()

    def update(self):
        if len(self.rx_queue):
            packet = self.rx_queue.popleft()
            if isinstance(packet, SyncPacket):
                self._transport_layer.sync_max_block_size = min(packet.payload_buffer_size, self._transport_layer.default_max_block_size)
                logger.info("Serial TransportLayer Synchronised (Version: {}.{}.{}, {}B serial buffer, {}B payload buffer) ".format(packet.version_major, packet.version_minor, packet.version_patch, packet.serial_buffer_size, packet.payload_buffer_size))
                self._transport_layer.synchronised = True
                if packet._frame_packet.header.packet_type == FramePacket.Type.DATA_FAF:
                    logger.info("Remote Sync request accepted")
                    self.send_packet(SyncPacket(512, *self._transport_layer.VERSION))


class TransportLayer(object):
    VERSION = [0,2,0]
    class ReceiveStreamState(object):
        def __init__(self):
            self.reset_connection()

        def reset_connection(self):
            self.sync = 0
            self.retries = 0
            self.reset_packet()

        def reset_packet(self):
            self.state = None
            self.data = bytearray()
            self.packet = None
            self.checksum = 0

    class TransmitStreamState(object):
        def __init__(self):
            self.reset_connection()

        def reset_connection(self):
            self.sync = None
            self.sync_last = None
            self.queue = deque()

        def sync_increment(self):
            self.sync = self.sync_next()
            return self.sync

        def sync_next(self):
            return 0 if self.sync == None else (self.sync + 1) & 0xFF

        def sync_acked(self, value):
            self.sync_last = value

        def sync_to_idx(self, sync):
            return (sync - (self.sync_last + 1)) & 0xFF

    def __init__(self, connection, max_block_size):
        self.synchronised = False
        self.active = True
        self.connection = connection

        self.services = {}

        self.default_max_block_size = max_block_size
        self.sync_max_block_size = 0

        self.rx_queue = deque()
        self.tx_queue = deque()

        self.tx_stream = TransportLayer.TransmitStreamState()

        self.in_log = open('serial_in.log', 'wb')
        self.out_log = open('serial_out.log', 'wb')

        self.rx_stream = TransportLayer.ReceiveStreamState()
        self.max_retries = 0 # infinite

        self.control = TransportLayerControl()
        self.attach(0, self.control)

        self.worker_thread = Thread(target=TransportLayer.process_connection, args=(self,))
        self.worker_thread.start()


    #def __del__(self):
        #self.out_log.close()
        #self.in_log.close()
        #logger.debug("TransportLayer object destroyed")

    def attach(self, channel, service):
        if not isinstance(service, Service):
            raise TypeError("Expected: {}".format(Service))
        if channel in self.services:
            raise ValueError("{} already attached to channel {}".format(self.services[channel], channel))

        logger.info("{} listening on channel {}".format(type(service).__fullqualname__, channel))
        self.services[channel] = service
        service._transport_layer = self

    def reconnect(self):
        self.synchronised = False
        self.connection.close()
        self.tx_stream.reset_connection()
        self.rx_stream.reset_connection()

        time.sleep(1)
        logger.warn("Attempting reconection to {}".format(self.connection))
        for _ in range(5):
            try:
                self.connection.close()
                time.sleep(0.1)
                self.connection.open()
                self.control.synchronise()
                return
            except OSError as e:
                logger.error(e)
                time.sleep(2)
        raise RuntimeError("Unable to reconnect to Serial Port")

    def process_connection(self):
        logger.debug("TransportLayer process thread started")
        while self.active:
            self.control.update()
            try:
                self.process_receive()
                self.process_transmit()
            except OSError as e:
                logger.error('{}{}'.format(type(e), e))
                self.reconnect()
            if self.rx_stream.packet == None and len(self.tx_queue) == 0:
                time.sleep(0.0000001) # thread timeslice release
        logger.debug("TransportLayer process thread finished")

    def process_transmit(self):
        if self.synchronised:
            for channel in self.services:
                if len(self.services[channel].tx_queue):
                    packet_type, packet = self.services[channel].tx_queue.popleft()
                    packet.frame_packet = self.send_packet(packet_type, channel, packet.packet_id, bytes(packet))
             #       logger.debug("Queueing:\t{} for [channel: {}] {}".format(packet, channel, type(self.services[channel]).__fullqualname__))

        if len(self.tx_queue) and len(self.tx_stream.queue) < 256:
            packet = self.tx_queue.popleft()

            if isinstance(packet, FramePacket.Data):
                if packet.header.packet_type == FramePacket.Type.DATA_FAF:
                    packet.status = FramePacket.Status.COMPLETE
                else:
                    packet.status = FramePacket.Status.INTRANSIT
                    if len(self.tx_stream.queue) == 255:
                        packet.header.packet_type = FramePacket.Type.DATA
                    packet.header.sync = self.tx_stream.sync_increment()
                    self.tx_stream.queue.append(packet)

            self.stream_write(bytes(packet))
            #logger.debug("Transmitting:\t{}".format(packet))

    def process_receive(self):
        def state_PACKET_RESET():
            self.rx_stream.reset_packet()
            self.rx_stream.state = state_PACKET_WAIT
            state_PACKET_WAIT()

        def state_PACKET_WAIT():
            if not self.connection.in_waiting > 0:
                return
            # look for the packet frame start
            self.stream_read(self.rx_stream.data, 1)
            if len(self.rx_stream.data) == 2:
                token = struct.unpack('<H', self.rx_stream.data)[0]
                if token & 0xFCFF == FramePacket.Data.Header.HEADER_TOKEN:
                    # pull the 2 bit packet type from the tokens 2nd byte
                    packet_type = (token >> 8) & 0x03
                    self.rx_stream.state = state_PACKET_RESPONSE if packet_type == FramePacket.Type.RESPONSE else state_PACKET_HEADER
                else:
                    # noise on the bus
                    del self.rx_stream.data[0]

        def state_PACKET_RESPONSE():
            self.stream_read(self.rx_stream.data, FramePacket.Response.SIZE - len(self.rx_stream.data))
            if len(self.rx_stream.data) != FramePacket.Response.SIZE:
                return
            packet = FramePacket.Response.from_bytes(self.rx_stream.data)
            if packet.checksum == Checksum.crc8(0, self.rx_stream.data[:-1]):
                self.process_response(packet)
                self.rx_stream.state = state_PACKET_RESET

        def state_PACKET_HEADER():
            self.stream_read(self.rx_stream.data, FramePacket.Data.Header.SIZE - len(self.rx_stream.data))
            if len(self.rx_stream.data) != FramePacket.Data.Header.SIZE:
                return

            self.rx_stream.packet = FramePacket.Data.from_bytearray(self.rx_stream.data)
            header = self.rx_stream.packet.header

            if header.checksum == Checksum.crc8(0, self.rx_stream.data[:-1]):
                if self.rx_stream.sync == header.sync or header.packet_type == FramePacket.Type.DATA_FAF:
                    if header.payload_size:
                        self.rx_stream.data = bytearray()
                        self.rx_stream.state = state_PACKET_DATA
                    else:
                        self.dispatch_packet(self.rx_stream.packet)
                        self.rx_stream.state = state_PACKET_RESET
                elif self.rx_stream.retries > 0:
                    self.rx_stream.state = state_PACKET_RESET  # drop everything during retry
                elif header.sync == (self.rx_stream.sync - 1) & 0xFF:
                    # appears to be resending the last pack we already acked, lost response?, resend
                    self.send_response(FramePacket.Response.Type.ACK, (self.rx_stream.sync - 1) & 0xFF)
                    self.rx_stream.state = state_PACKET_RESET
                else:
                    self.rx_stream.state = state_PACKET_RESEND

            # At this point we know the header is corrupt and not trusted
            # but to speed up stream recovery the packet type is assumed
            # to be correct

            elif header.packet_type == FramePacket.Type.DATA_FAF:
                self.rx_stream.state = state_PACKET_RESET # corrupt FaF packets are droped
            elif self.rx_stream.retries > 0:
                self.rx_stream.state = state_PACKET_RESET # drop everything during retry
            else:
                self.rx_stream.state = state_PACKET_RESEND

        def state_PACKET_DATA():
            start_idx = len(self.rx_stream.packet.data)
            self.stream_read(self.rx_stream.packet.data, self.rx_stream.packet.header.payload_size - len(self.rx_stream.packet.data))
            self.rx_stream.checksum = Checksum.crc16(self.rx_stream.checksum, self.rx_stream.packet.data[start_idx:])
            if len(self.rx_stream.packet.data) != self.rx_stream.packet.header.payload_size:
                return
            self.rx_stream.state = state_PACKET_FOOTER

        def state_PACKET_FOOTER():
            self.stream_read(self.rx_stream.data, FramePacket.Data.Footer.SIZE - len(self.rx_stream.data))
            if len(self.rx_stream.data) != FramePacket.Data.Footer.SIZE:
                return
            self.rx_stream.packet.footer = FramePacket.Data.Footer.from_bytes(self.rx_stream.data)
            if self.rx_stream.checksum == self.rx_stream.packet.footer.checksum:
                self.dispatch_packet(self.rx_stream.packet)
                self.rx_stream.state = state_PACKET_RESET
            else:
                self.rx_stream.state = state_PACKET_RESEND

        def state_PACKET_RESEND():
            if self.rx_stream.retries < self.max_retries or self.max_retries == 0:
                self.max_retries += 1
                self.send_response(FramePacket.Response.Type.NACK, self.rx_stream.sync)
                self.rx_stream.state = state_PACKET_RESET
            self.rx_stream.state = state_PACKET_ERROR

        def state_PACKET_ERROR():
            logger.error("data stream error")
            self.rx_stream.reset_connection()

        def state_PACKET_TIMEOUT():
            logger.warn("packet timeout")
            self.rx_stream.state = state_PACKET_RESEND

        if self.rx_stream.state != None:
            self.rx_stream.state()
        else: state_PACKET_RESET()

    def process_response(self, packet):
        def valid_response():
            for x in self.tx_stream.queue:
                if x.header.sync == packet.sync_id:
                    return True
            return False

        if valid_response() == False:
            # fatal stream desync exception ?
            logger.error("received invalid response")
            return

        # if we got a valid response then every packet that was transmitted before this one
        # can be acknoledged

        # logger.debug("Response:\t{}".format(packet))

        while len(self.tx_stream.queue) > 0 and self.tx_stream.queue[0].header.sync != packet.sync_id:
            p = self.tx_stream.queue.popleft()
            p.status = FramePacket.Status.COMPLETE
            p.response = FramePacket.Response.Type.ACK

        if packet.response == FramePacket.Response.Type.ACK:
            p = self.tx_stream.queue.popleft()
            p.status = FramePacket.Status.COMPLETE
            p.response = packet.response
            self.tx_stream.sync_last = packet.sync_id
        elif packet.response == FramePacket.Response.Type.REJECT:
            # A rejected packet will never be excepted by remote
            # just drop it
            p = self.tx_stream.queue.popleft()
            p.status = FramePacket.Status.FAILED
            p.response = packet.response
            self.tx_stream.sync_last = packet.sync_id
        #elif packet.response == FramePacket.Response.Type.NYET:
        # todo: NYET packets should requeue all currently queued packets for that channel at the back of the queue
        #    channel_packets = [x for x in self.tx_stream.queue if x.header.channel == packet.header.channel]
        else:
            while len(self.tx_stream.queue):
                p = self.tx_stream.queue.pop()
                p.status = FramePacket.Status.RETRY
                p.response = packet.response
                self.tx_queue.appendleft(p)

    def dispatch_packet(self, packet):
        if packet.header.channel in self.services and packet.header.packet_id in self.services[packet.header.channel].packets:
            packet_class = self.services[packet.header.channel].packets[packet.header.packet_id]
            service_packet = packet_class.from_bytes(packet.data)
            service_packet._frame_packet = packet
            self.services[packet.header.channel].dispatch(service_packet)
            self.send_response(FramePacket.Response.Type.ACK, self.rx_stream.sync)
            #service_class = type(self.services[packet.header.channel])
            #logger.debug("Dispatching:\t{0} to [channel: {2}] {1}".format(service_packet, service_class.__fullqualname__, packet.header.channel))
        else:
            logger.debug("Rejected:\t{}".format(packet))
            self.send_response(FramePacket.Response.Type.REJECT, self.rx_stream.sync)

        if packet.header.packet_type != FramePacket.Type.DATA_FAF:
            self.rx_stream.sync = (self.rx_stream.sync + 1) & 0xFF

    def stream_read(self, buffer, size):
        recv = self.connection.read(size)
        self.in_log.write(recv)
        self.in_log.flush()
        buffer.extend(recv)
        return len(recv)

    def stream_write(self, buffer):
        nbytes = self.connection.write(buffer)
        self.out_log.write(buffer)
        self.out_log.flush()
        return nbytes

    def send_packet(self, packet_type, channel, packet_id, payload):
        packet = FramePacket.Data.create(packet_type, channel, packet_id, payload)
        self.tx_queue.append(packet)
        return packet

    def send_response(self, response_id, packet_sync):
        self.tx_queue.append(FramePacket.Response(response=response_id, sync_id=packet_sync))

    def connect(self):
        self.control.synchronise()
        time.sleep(0.1)
        while not self.synchronised:
            self.control.synchronise()
            time.sleep(1.0)
        return self.synchronised

    def disconnect(self):
        self.control.disconnect()
        self.synchronised = False

    def shutdown(self):
        self.active = False
        self.worker_thread.join()