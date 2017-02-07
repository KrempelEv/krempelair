#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from flask import make_response


def api_response(responseObj, httpStatus=200):
    """Creates an json Response from an given Python Object

    Keyword arguments:
    responseObj -- Python Object
    httpStatus -- HTTP Response Code (default 0.0)
    """
    r = make_response(json.dumps(responseObj, indent=4),httpStatus)
    r.headers["Content-Type"] = "application/json; charset=utf-8"
    return r
