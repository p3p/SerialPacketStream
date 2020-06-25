import serial
import serial.tools.list_ports

import argparse
import os
import time
import logging
import math
from collections import deque
from statistics import median
from operator import attrgetter

import time
import filecmp

import SerialPacketStream
import SerialPacketStream.FileService

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Send files over a serial port to Marlin')
    parser.add_argument("-p", "--port", default="/dev/ttyACM0", help="serial port to use")
    parser.add_argument("-b", "--baud", default="115200", help="baud rate of serial connection")
    parser.add_argument("-d", "--blocksize", default="512", help="defaults to autodetect")
    parser.add_argument("--log-level", default='DEBUG', choices=['DEBUG', 'INFO', 'WARN', 'ERROR', 'CRITICAL'], help="Log Level")
    args = parser.parse_args()

    logger = logging.getLogger('default')
    console_log = logging.StreamHandler()
    formatter = logging.Formatter('[%(threadName)-10s] %(msecs)03d: %(levelname)-5s - %(message)s')
    console_log.setFormatter(formatter)
    console_log.setLevel(logging.INFO)

    #file_log = logging.FileHandler('default.log')
    #file_log.setFormatter(formatter)
    #file_log.setLevel(logging.DEBUG)

    logger.addHandler(console_log)
    #logger.addHandler(file_log)

    logger.setLevel(getattr(logging, args.log_level, None))

    logger.debug("Logger Started")

    logger.info("pySerial Version: {}".format(serial.VERSION))
    logger.info("Available ports:")
    for x in serial.tools.list_ports.comports():
        logger.info("\t{}".format(x))
    logger.info("Connecting to: {}".format(args.port))

    serial_connection = serial.serial_for_url(args.port, baudrate = args.baud, write_timeout = 0, timeout = 0)

    transport_layer = SerialPacketStream.TransportLayer(serial_connection, int(args.blocksize))
    file_service = SerialPacketStream.FileService()
    transport_layer.connect()

    transport_layer.attach(1, file_service)
    file_service.query_remote()

    def progress_callback(filesize):
        results = deque()
        last_time = time.perf_counter()
        last_bytes = 0
        while True:
            byte_count = yield
            delta_time = time.perf_counter() - last_time
            delta_bytes = byte_count - last_bytes
            KiBs = (delta_bytes / delta_time) / 1024
            results.append(KiBs)
            if len(results) > 31:
                results.popleft()
            progress = (byte_count / filesize) * 100.0
            if math.isclose(progress, 100.0):
                print("{:.0f}% @ {:.0f}KiB/s     ".format(progress, median(results)), end='\n')
            else:
                print("{:.0f}% @ {:.0f}KiB/s     ".format(progress, median(results)), end='\r')
            last_time = time.perf_counter()
            last_bytes = byte_count

    def ls(path, abs_dir = '', dirs = [], recursive = False):
        if path != '':
            file_service.cd(path)

        abs_dir = "{}{}{}".format(abs_dir, '/' if abs_dir != '/' and abs_dir != '' else '' , path)
        dirs.append(abs_dir)

        for x in sorted(file_service.ls(), key=attrgetter('meta', 'filename')):
            print("{}\t{}{}{}".format(x.size if x.meta != x.Meta.FOLDER else '*', abs_dir, '/' if abs_dir != '/' else '' ,x.filename))
            if x.meta == x.Meta.FOLDER and recursive:
                ls(x.filename, abs_dir, dirs, recursive=True)

        dirs.pop()

        if len(dirs) > 0:
            abs_dir = dirs[-1]
            if abs_dir != '':
                file_service.cd(abs_dir)


    file_service.mount()
    #file_service.put("testbig.g", "ZZ.g", progress=progress_callback(os.path.getsize("testbig.g")))
    #file_service.get("ggjfty.g", "test2.g", progress=progress_callback(os.path.getsize("testbig.g")))
    #logger.info("files identical?: {}".format(filecmp.cmp("testbig.g", "test2.g", shallow=False)))
    ls('/', recursive=True)

    #file_service.cd("/TRASH-~1")
    # print("Current dir: ", file_service.pwd())

    file_service.unmount()

    time.sleep(1)

    #transport_layer.control.reset_mcu()
    #file_service.query_remote()

    transport_layer.disconnect()
    transport_layer.shutdown()

    logger.debug("Main Exit")
