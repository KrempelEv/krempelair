#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import smbus


from flask import request, render_template, current_app, url_for, make_response
from flask.views import View

from lib.bus.digitalOut import digiOut




def air_get_status():
    """"""
    #digitalOut.setValue(0x20,0,value)
    stateMsg = {"0": "aus",
                "5": "zuluft-stufe:1 / fortluft-stufe:1",
                "7": "zuluft-stufe:2 / fortluft-stufe:1",
                "15": "zuluft-stufe:2 / fortluft-stufe:2"}
    pins = digiOut()
    status = str(int(pins.getValue(0x21)))
    status = {"msg":stateMsg[status],"code":status}
    r = make_response(json.dumps(status, indent=4),200)
    r.headers["Content-Type"] = "application/json; charset=utf-8"
    return r


def air_set_status(pin,state):
    """"""
    #digitalOut.setValue(0x20,0,value)
    pins = digiOut()
    status = {"msg":"running","code":pins.setValue(0x20,pin,state)}
    r = make_response(json.dumps(status, indent=4),200)
    r.headers["Content-Type"] = "application/json; charset=utf-8"
    return r
