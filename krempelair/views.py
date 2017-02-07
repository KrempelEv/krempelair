#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import smbus


from flask import request, render_template, current_app, url_for, make_response
from flask.views import View

from lib.bus.digitalOut import digiOut


def sys_status_betrieb():
    """"""
    pins = digiOut()
    stateMsg = {"ZUL St1": "0",
                "FOL St1": "0",
                "ZUL St2": "0",
                "FOL St2": "0",
                "LE PU": "0",
                "5": "0",
                "6": "0",
                "7": "0"}
    status = pins.getValue(0x21)
    if status&1 != 0:
        stateMsg["ZUL St1"] = "1"
    if status&2 != 0:
        stateMsg["FOL St1"] = "1"
    if status&4 != 0:
        stateMsg["ZUL St2"] = "1"
    if status&8 != 0:
        stateMsg["FOL St2"] = "1"
    if status&16 == 0:  # LE PU ist invertiert
        stateMsg["LE PU"] = "1"
    if status&32 != 0:
        stateMsg["5"] = "1"
    if status&64 != 0:
        stateMsg["6"] = "1"
    if status&128 != 0:
        stateMsg["7"] = "1"
    return stateMsg

def sys_status_stoerungen():
    """"""
    pins = digiOut()
    stateMsg = {"AL0": "0",
                "AL1": "0",
                "AL2": "0",
                "AL3": "0",
                "AL4": "0",
                "AL5": "0",
                "AL6": "0",
                "AL7": "0"}
    status = pins.getValue(0x22)
    if status&1 != 0:
        stateMsg["AL0"] = "1"
    if status&2 != 0:
        stateMsg["AL1"] = "1"
    if status&4 != 0:
        stateMsg["AL2"] = "1"
    if status&8 != 0:
        stateMsg["AL3"] = "1"
    if status&16 != 0:
        stateMsg["AL4"] = "1"
    if status&32 != 0:
        stateMsg["AL5"] = "1"
    if status&64 != 0:
        stateMsg["AL6"] = "1"
    if status&128 != 0:
        stateMsg["AL7"] = "1"
    return stateMsg

def air_get_status_stoerung():
    """"""
    status = sys_status_stoerung()
    r = make_response(json.dumps(status, indent=4),200)
    r.headers["Content-Type"] = "application/json; charset=utf-8"
    return r


def air_get_status_betrieb():
    """"""
    status = sys_status_betrieb()
    r = make_response(json.dumps(status, indent=4),200)
    r.headers["Content-Type"] = "application/json; charset=utf-8"
    return r


def air_set_status(pin,state):
    """"""
    pins = digiOut()
    status = sys_status(0x20)
    pins.setValue(0x20, pin, state)
    status = sys_status(0x20)
    r = make_response(json.dumps(status, indent=4),303)
    r.headers["Location"] = "/"
    r.headers["Content-Type"] = "application/json; charset=utf-8"
    return r

def air_off():
    """"""
    air_set_status(0,0)
    return air_set_status(1,0)
