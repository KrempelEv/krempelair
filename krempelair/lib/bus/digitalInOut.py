#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
    krempelair.lib.bus.digitalInOut

    Simple Wraper Libary

    :copyright: (c) 2017 by @graphics80 on github.com.
    :license:  AGPL-3.0, see LICENSE for more details.
"""


import smbus
import logging as log


class digiInOut():

    def __init__(self):
        self._bus = smbus.SMBus(1)
        self._o1 = 0

    #Funktion Setze Bit in Variable / Function Set Bit in byte
    def __set_bit(self, bit, value):
        log.debug(value | (1<<bit))    #Ausgabe in der Python Shell
        return value | (1<<bit)

    #Funktion ruecksetzte Bit in Variable / Function reset Bit in byte
    def __clear_bit(self, bit, value):
        log.debug(value & ~(1<<bit))    #Ausgabe in der Python Shell
        return value & ~(1<<bit)        #Funktion Setze Bit in Variable / Function Set Bit in byte

    def getValue(self, address):
        return (255 - self._bus.read_byte(address))

    def setValue(self, address, pin, state):
        self._o1 = self.getValue(address)
        if state == 1:
    	       self._o1 = self.__set_bit(pin, self._o1)
        else:
    	       self._o1 = self.__clear_bit(pin, self._o1)

        self._bus.write_byte(address,255-self._o1)
