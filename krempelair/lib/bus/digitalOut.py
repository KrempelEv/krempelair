#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
    krempelair.lib.bus.digitalOut

    Simple Wraper Libary

    :copyright: (c) 2017 by @graphics80 on github.com.
    :license:  AGPL-3.0, see LICENSE for more details.
"""


import smbus
import logging as log


class digiOut():

    def __init__(self):
        self._bus = smbus.SMBus(1)
        self._o1 = 0

    #Funktion Setze Bit in Variable / Function Set Bit in byte
    def __set_bit(self, value, bit):
        log.debug(value | (1<<bit))    #Ausgabe in der Python Shell
        return value | (1<<bit)

    #Funktion ruecksetzte Bit in Variable / Function reset Bit in byte
    def __clear_bit(self, value, bit):
        log.debug(value & ~(1<<bit))    #Ausgabe in der Python Shell
        return value & ~(1<<bit)#Funktion Setze Bit in Variable / Function Set Bit in byte

    def getValue(self, address):
        return (255 - self._bus.read_byte_data(address,255))

    def setValue(self, address, pin, state):
        if state == 1:
    	       self._o1 = self.__set_bit(o1,pin)
        else:
    	       self._o1 = self.__clear_bit(o1,pin)

        self._bus.write_byte(address,255-self._o1)
