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

bus = smbus.SMBus(1)

o1 = 0

#Funktion Setze Bit in Variable / Function Set Bit in byte
def __set_bit(value, bit):
    log.debug(value | (1<<bit))    #Ausgabe in der Python Shell
    return value | (1<<bit)

#Funktion ruecksetzte Bit in Variable / Function reset Bit in byte
def __clear_bit(value, bit):
    log.debug(value & ~(1<<bit))    #Ausgabe in der Python Shell
    return value & ~(1<<bit)#Funktion Setze Bit in Variable / Function Set Bit in byte

def getValue(address, pin):
    global o1
    return bus.read_byte_data(address,(1<<pin))


def setValue(address, pin, state):
    global o1
    if state == 1:
	o1 = __set_bit(o1,pin)
    else:
	o1 = __clear_bit(o1,pin)
    bus.write_byte(address,255-o1)
