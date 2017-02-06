#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import smbus


from flask import request, render_template, current_app, url_for, make_response
from flask.views import View

from lib.bus.digitalOut import digiOut


def sys_status():
    pins = digiOut()
    stateMsg = {0: "aus",
                5: "zuluft-stufe:1 / fortluft-stufe:1",
                7: "zuluft-stufe:2 / fortluft-stufe:1",
                15: "zuluft-stufe:2 / fortluft-stufe:2"}
    status = pins.getValue(0x21)
    return {"msg":stateMsg[status],"code": status, "bin": "{0:#b}".format(status)}


def air_get_status():
    """"""
    status = sys_status()
    r = make_response(json.dumps(status, indent=4),200)
    r.headers["Content-Type"] = "application/json; charset=utf-8"
    return r


def air_set_status(pin,state):
    """"""
    #digitalOut.setValue(0x20,0,value)
    pins = digiOut()
    status = sys_status()
    pins.setValue(0x20,pin,(state | status["code"]))
    status = sys_status()
    r = make_response(json.dumps(status, indent=4),200)
    r.headers["Content-Type"] = "application/json; charset=utf-8"
    return r
