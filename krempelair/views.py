#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import smbus


from flask import request, render_template, current_app, url_for, make_response
from flask.views import View

from lib.bus import digitalOut




def air_status(inp):
    """"""
    #digitalOut.setValue(0x20,0,value)
    status = {"msg":"running","code":digitalOut.setValue(0x20,0,inp)}
    r = make_response(json.dumps(status, indent=4),200)
    r.headers["Content-Type"] = "application/json; charset=utf-8"
    return r
