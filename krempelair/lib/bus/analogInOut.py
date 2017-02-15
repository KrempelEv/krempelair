#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import smbus
import logging as log


class analogInOut():

    def __init__(self):
        self._bus = smbus.SMBus(1)

    def getValue(self, address, chanel):
        var = self._bus.read_i2c_block_data(address,chanel,11)    #Werte von Board in 11 stelliges Array schreiben
        val = var[2]*256+var[1]                                   #Berechnung der korrekten Zahlenwerte aus dem Array
        log.debug("Analogwert von Adresse "+str(address)+ " mit Kanal " +str(chanel) +" mit Wert "+ str(val))    #Ausgabe in der Python Shell
        return val
