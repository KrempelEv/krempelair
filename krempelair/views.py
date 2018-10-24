#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import smbus
import pickle
import sqlite3


from flask import request, render_template, current_app, url_for, make_response
from flask.views import View

from lib.jsonApi import api_response
from lib.bus.digitalInOut import digiInOut
from lib.bus.analogInOut import analogInOut


def _sys_status_betrieb():
    """"""
    pins = digiInOut()
    stateMsg = {"ZUL_St1": False,
                "ZUL_St2": False,
                "FOL_St1": False,
                "FOL_St2": False,
                "LE_PU": False,
                "Raucherraum": False,
                "6": False,
                "7": False,
                "NAK" : False}
    status = pins.getValue(0x21)
    if status&1 != 0:
        stateMsg["ZUL_St1"] = True
    if status&2 != 0:
        stateMsg["ZUL_St2"] = True
    if status&4 != 0:
        stateMsg["FOL_St1"] = True
    if status&8 != 0:
        stateMsg["FOL_St2"] = True
    if status&16 != 0:
        stateMsg["LE_PU"] = True
    if status&32 != 0:
        stateMsg["Raucherraum"] = True
    if status&64 != 0:
        stateMsg["6"] = True
    if status&128 != 0:
        stateMsg["7"] = True
    NAK = _sys_get_NAK()
    stateMsg["NAK"] = NAK
    return stateMsg

def _sys_status_stoerung():
    """"""
    pins = digiInOut()
    stateMsg = {"Quit": False,
                "Sammelalarm": False,
                "Frost": False,
                "Stroem_ZUL": False,
                "Stroem_FOL": False,
                "AL5": False,
                "AL6": False,
                "AL7": False}
    status = pins.getValue(0x22)
    if status&1 != 0:
        stateMsg["Quit"] = True
    if status&2 != 0:
        stateMsg["Sammelalarm"] = True
    if status&4 != 0:
        stateMsg["Frost"] = True
    if status&8 != 0:
        stateMsg["Stroem_ZUL"] = True
    if status&16 != 0:
        stateMsg["Stroem_FOL"] = True
    if status&32 != 0:
        stateMsg["AL5"] = True
    if status&64 != 0:
        stateMsg["AL6"] = True
    if status&128 != 0:
        stateMsg["AL7"] = True
    return stateMsg

def _sys_get_temperaturen():
    """"""
    temperaturen = {"ZUL" : 0,
                    "ABL" : 0,
                    "FOL" : 0,
                    "AUL" : 0,
                    "RaumVorne" : 0,
                    "TempWitt" : 0,
                    "TempSoll" : 0,
                    "TempSollNAK": 0}
    # Werte holen
    analogIn = analogInOut()
    wertZUL = analogIn.getValue(0x08,1)
    tempZUL = round((float(wertZUL)/1024)*50,1)
    wertABL = analogIn.getValue(0x08,2)
    tempABL = round((float(wertABL)/1024)*50,1)
    wertFOL = analogIn.getValue(0x08,3)
    tempFOL = round((float(wertFOL)/1024)*50,1)
    wertAUL = analogIn.getValue(0x08,4)
    tempAUL = round(-30.0 + (float(wertAUL)/1024)*100.0,1)
    wertRaumVorne = analogIn.getValue(0x09,1)
    tempRaumVorne = round((float(wertRaumVorne)/1024)*50.0,1)
    wertTempWitt = analogIn.getValue(0x09,2)
    tempWitt = round(-30.0 + (float(wertTempWitt)/1024)*100.0,1)
    tempSoll = _sys_get_tempSoll()
    tempSollNAK = _sys_get_tempNAK()
    # Zuweisen in JSON
    temperaturen["ZUL"] = tempZUL
    temperaturen["ABL"] = tempABL
    temperaturen["FOL"] = tempFOL
    temperaturen["AUL"] = tempAUL
    temperaturen["RaumVorne"] = tempRaumVorne
    temperaturen["TempWitt"] = tempWitt
    temperaturen["TempSoll"] = tempSoll
    temperaturen["TempSollNAK"] = tempSollNAK
    return temperaturen

def _sys_set_status(pin,state):
    """"""
    pins = digiInOut()
    pins.setValue(0x20, pin, state)
    return {"pin":pin,"state":state}

def _sys_get_tempSoll():
    """"""
    conn = sqlite3.connect('/opt/krempel/share/data.db')
    c = conn.cursor()
    c.execute("SELECT value FROM sollwerte WHERE key LIKE '%tempSoll';")
    value = c.fetchone()[0]
    conn.commit()
    conn.close()
    return(value)


def _sys_get_NAK():
    """"""
    conn = sqlite3.connect('/opt/krempel/share/data.db')
    c = conn.cursor()
    #c.execute("SELECT value FROM nak WHERE key LIKE '%nak';")
    #NAKvalue = int(c.fetchone()[0])
    conn.commit()
    conn.close()
    NAKvalue = 1
    if(NAKvalue == 1):
        return True
    else:
        return False

def _sys_get_tempNAK():
    """"""
    conn = sqlite3.connect('/opt/krempel/share/data.db')
    c = conn.cursor()
    c.execute("SELECT value FROM sollwerte WHERE key LIKE '%tempSollNAK';")
    value = c.fetchone()[0]
    conn.commit()
    conn.close()
    return(value)

# API Functions
def air_get_status_stoerung():
    """"""
    status = _sys_status_stoerung()
    return api_response(status)

def air_get_status_betrieb():
    """"""
    status = _sys_status_betrieb()
    return api_response(status)

def air_get_temperaturen():
    """"""
    temperaturen = _sys_get_temperaturen()
    return api_response(temperaturen)

def air_set_wrg(level):
    """"""
    if(level>1023):
        level = 1023
    if(level<0):
        level = 0
    analogPins = analogInOut()
    analogPins.setValue(0x58,0x00,level)
    return api_response(level)

def air_set_le(level):
    """"""
    if(level>1023):
        level = 1023
    if(level<0):
        level = 0
    analogPins = analogInOut()
    analogPins.setValue(0x58,0x01,level)
    return api_response(level)

    

def air_set_level(level):
    """"""
    # Lueftung Aus
    if(level == 0):
        _sys_set_status(0,0)
        _sys_set_status(1,0)
    # Lueftung Stufe 1
    if(level == 1):
        _sys_set_status(0,1)
        _sys_set_status(1,0)
    # Lueftung Stufe 2
    if(level == 2):
        _sys_set_status(0,1)
        _sys_set_status(1,1)
    # Raucher Ein
    if(level == 10):
        _sys_set_status(2,0)
    # Raucher Aus
    if(level == 11):
        _sys_set_status(2,1)
    
    status = _sys_status_betrieb()
    return api_response(status,200)

def air_set_timer(time):
    """"""
    print time
    status = _sys_status_betrieb()
    return api_response(status,200)

def air_set_tempSoll(temp):
    """"""
    conn = sqlite3.connect('/opt/krempel/share/data.db')
    c = conn.cursor()
    c.execute("UPDATE sollwerte SET value = ? WHERE key='tempSoll';",(temp))
    conn.commit()
    conn.close()
    return api_response(temp,200)

def air_set_tempNAK(temp):
    """"""
    conn = sqlite3.connect('/opt/krempel/share/data.db')
    c = conn.cursor()
    c.execute("UPDATE sollwerte SET value = ? WHERE key='tempSollNAK';",(temp))
    conn.commit()
    conn.close()
    return api_response(temp,200)

def air_set_NAK(NAK):
    """"""
    conn = sqlite3.connect('/opt/krempel/share/data.db')
    c = conn.cursor()
    c.execute('INSERT INTO nak VALUES (?,?)',['nak',NAK])
    conn.commit()
    conn.close()
    return api_response(NAK,200)
