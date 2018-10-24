#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import smbus
import logging as log


class analogInOut():

    def __init__(self):
        self._bus = smbus.SMBus(1)

    def getValueOld(self, address, chanel):
        var = self._bus.read_i2c_block_data(address,chanel,11)    #Werte von Board in 11 stelliges Array schreiben
        val = var[2]*256+var[3]                                   #Berechnung der korrekten Zahlenwerte aus dem Array
        log.debug("Analogwert von Adresse "+str(address)+ " mit Kanal " +str(chanel) +" mit Wert "+ str(val))    #Ausgabe in der Python Shell
        return val
    
    
    def getValue(self, address, chanel):
        var = self._bus.read_i2c_block_data(address,0x00,11)    #Werte von Board in 11 stelliges Array schreiben
        if chanel == 0:
            val = var[2]*256+var[1]
        if chanel == 1:
            val = var[4]*256+var[3]                                   #Berechnung der korrekten Zahlenwerte aus d$
        if chanel == 2:
            val = var[6]*256+var[5]
        if chanel == 3:
            val = var[8]*256+var[7]
        if chanel == 4:
            val = var[10]*256+var[9]
        return val


    def setValue(self, address, chanel, value):
        a=int(value)
        HBy = int(a/256)
        LBy = int(a-HBy*256)
        field=[LBy,HBy]
        self._bus.write_i2c_block_data(address,chanel,field)
