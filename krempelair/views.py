#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import smbus


from flask import request, render_template, current_app, url_for, make_response
from flask.views import View

from lib.jsonApi import api_response
from lib.bus.digitalInOut import digiInOut
from lib.bus.analogInOut import analogInOut


def sys_status_betrieb():
    """"""
    pins = digiInOut()
    stateMsg = {"ZUL_St1": False,
                "ZUL_St2": False,
                "FOL_St1": False,
                "FOL_St2": False,
                "LE_PU": False,
                "5": False,
                "6": False,
                "7": False}
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
        stateMsg["5"] = True
    if status&64 != 0:
        stateMsg["6"] = True
    if status&128 != 0:
        stateMsg["7"] = True
    return stateMsg

def sys_status_stoerung():
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

def air_get_status_stoerung():
    """"""
    status = sys_status_stoerung()
    return api_response(status)


def air_get_status_betrieb():
    """"""
    status = sys_status_betrieb()
    return api_response(status)

def air_get_temperaturen():
    """"""
    temperaturen = {"ZUL" : 0
                    "ABL" : 0
                    "FOL" : 0
                    "AUL" : 0}
    analogIn = analogInOut()
    wertZUL = analogIn.getValue(0x08,0x00)
    tempZUL = wertZUL / 1024 * 50
    wertABL = analogIn.getValue(0x08,0x01)
    tempABL = wertZUL / 1024 * 50
    wertFOL = analogIn.getValue(0x08,0x02)
    tempFOL = wertZUL / 1024 * 50
    wertAUL = analogIn.getValue(0x08,0x03)
    tempAUL = wertZUL / 1024 * 50
    temperaturen["ZUL"] = tempZUL
    temperaturen["ABL"] = tempABL
    temperaturen["FOL"] = tempFOL
    temperaturen["AUL"] = tempAUL
    return api_response(temperaturen)

def air_set_status(pin,state):
    """"""
    pins = digiInOut()
    pins.setValue(0x20, pin, state)
    status = sys_status_betrieb()
    r =api_response(status,304)
    r.headers["Location"] = "/"
    return r


def air_set_level(level):
    """"""
    if(level == 0):
        air_set_status(0,0)
        air_set_status(1,0)
    if(level == 1):
        air_set_status(0,1)
        air_set_status(1,0)
    if(level == 2):
        air_set_status(0,1)
        air_set_status(1,1)
    status = sys_status_betrieb()
    r =api_response(status,200)
    return r


def air_set_timer(time):
    """"""
    print time
    status = sys_status_betrieb()
    r =api_response(status,200)
    return r


def air_set_temp(temp):
    """"""
    print temp
    status = sys_status_betrieb()
    r =api_response(status,200)
    return r


def air_set_raucherraum_on():
    """"""
    return air_set_status(2,1)


def air_set_raucherraum_off():
    """"""
    return air_set_status(2,0)
