#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import smbus


from flask import request, render_template, current_app, url_for, make_response
from flask.views import View

from lib.bus.digitalOut import digiOut


def sys_status(address):
    pins = digiOut()
    try:
        stateMsg = {0: "aus",
                    5: "zuluft-stufe:1 / fortluft-stufe:1",
                    7: "zuluft-stufe:2 / fortluft-stufe:1",
                    15: "zuluft-stufe:2 / fortluft-stufe:2"}
        status = pins.getValue(address)
        return {"msg":stateMsg[status],"code": status, "bin": "{0:#b}".format(status)}
    except:
        return {"msg":"unknown","code": status, "bin": "{0:#b}".format(status)}


def air_get_status():
    """"""
    status = sys_status(0x21)
    r = make_response(json.dumps(status, indent=4),200)
    r.headers["Content-Type"] = "application/json; charset=utf-8"
    return r


def air_set_status(pin,state):
    """"""
    #digitalOut.setValue(0x20,0,value)

    # p: 2
    # state 1
    #b0ß000001
    pins = digiOut()
    status = sys_status(0x22)
    pins.setValue(0x22, pin, state)
    status = sys_status(0x22)
    r = make_response(json.dumps(status, indent=4),200)
    r.headers["Content-Type"] = "application/json; charset=utf-8"
    return r
