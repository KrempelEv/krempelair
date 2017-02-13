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


def sys_status_betrieb():
    """"""
    pins = digiInOut()
    stateMsg = {"ZUL_St1": false,
                "ZUL_St2": false,
                "FOL_St1": false,
                "FOL_St2": false,
                "LE_PU": false,
                "5": false,
                "6": false,
                "7": false}
    status = pins.getValue(0x21)
    if status&1 != 0:
        stateMsg["ZUL_St1"] = true
    if status&2 != 0:
        stateMsg["ZUL_St2"] = true
    if status&4 != 0:
        stateMsg["FOL_St1"] = true
    if status&8 != 0:
        stateMsg["FOL_St2"] = true
    if status&16 != 0:
        stateMsg["LE_PU"] = true
    if status&32 != 0:
        stateMsg["5"] = true
    if status&64 != 0:
        stateMsg["6"] = true
    if status&128 != 0:
        stateMsg["7"] = true
    return stateMsg

def sys_status_stoerung():
    """"""
    pins = digiInOut()
    stateMsg = {"Quit": false,
                "Sammelalarm": false,
                "Frost": false,
                "Stroem_ZUL": false,
                "Stroem_FOL": false,
                "AL5": false,
                "AL6": false,
                "AL7": false}
    status = pins.getValue(0x22)
    if status&1 != 0:
        stateMsg["Quit"] = true
    if status&2 != 0:
        stateMsg["Sammelalarm"] = true
    if status&4 != 0:
        stateMsg["Frost"] = true
    if status&8 != 0:
        stateMsg["Stroem_ZUL"] = true
    if status&16 != 0:
        stateMsg["Stroem_FOL"] = true
    if status&32 != 0:
        stateMsg["AL5"] = true
    if status&64 != 0:
        stateMsg["AL6"] = true
    if status&128 != 0:
        stateMsg["AL7"] = true
    return stateMsg

def air_get_status_stoerung():
    """"""
    status = sys_status_stoerung()
    return api_response(status)


def air_get_status_betrieb():
    """"""
    status = sys_status_betrieb()

    return api_response(status)


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
    r =api_response(status,304)
    r.headers["Location"] = "/"
    return r


def air_set_timer(time):
    """"""
    print time
    status = sys_status_betrieb()
    r =api_response(status,304)
    r.headers["Location"] = "/"
    return r


def air_set_temp(temp):
    """"""
    print temp
    status = sys_status_betrieb()
    r =api_response(status,304)
    r.headers["Location"] = "/"
    return r


def air_set_raucherraum_on():
    """"""
    return air_set_status(2,1)


def air_set_raucherraum_off():
    """"""
    return air_set_status(2,0)
