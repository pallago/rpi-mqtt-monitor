#!/usr/bin/python
#This python script is only suitable for UPS Shield X1200, X1201 and X1202

import struct
import smbus
import time


class X1202:
    """
    Class to read voltage and capcity (state of charge SOC)
    """
    _I2C_ADDRESS = 0x36
    
    def __init__(self):
        try:
            self.bus = smbus.SMBus(1)
        except IOError as e:
            errno, strerror = e.args
            print("I/O error({0}): {1}".format(errno,strerror))


    def readVoltage(self):

         #read = self.bus.read_word_data(self._I2C_ADDRESS, 2)
         #swapped = struct.unpack("<H", struct.pack(">H", read))[0]
         #voltage = swapped * 1.25 /1000/16
         voltage = 1
         return voltage


    def readSOC(self):

         #read = self.bus.read_word_data(self.I2CADDRESS, 4)
         #swapped = struct.unpack("<H", struct.pack(">H", read))[0]
         #soc = swapped/256
         soc = 100
         return soc

    def close(self):
        """Closes the i2c connection"""
        self.bus.close()

    def __enter__(self):
        """used to enable python's with statement support"""
        return self

    def __exit__(self, type, value, traceback):
        """with support"""
        self.close()


if __name__ == "__main__":
    try:
        with X1202() as dev1:
            
            while True:
                 print ("Voltage:%5.2fV" % dev1.readVoltage())
                 print ("Battery:%5i%%" % dev1.readSOC())
                 print ("")
                 time.sleep(2)

    except IOError as e:
        print(type(e), e)
        print("Error creating connection to i2c.  This must be run as root")
