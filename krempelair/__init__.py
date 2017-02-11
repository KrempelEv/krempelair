#!/usr/bin/env python3
# -*- coding: utf-8 -*-



import jinja2
import flask

import views


class Krempelair(flask.Flask):
    jinja_options = {
        'extensions': ['jinja2.ext.autoescape'],
        'undefined': jinja2.StrictUndefined
    }

    def __init__(self):
        """(See `make_app` for parameter descriptions.)"""
        flask.Flask.__init__(self, __name__)

        self.setup_routes()

    def create_jinja_environment(self):
        """Called by Flask.__init__"""
        env = super(Krempelair, self).create_jinja_environment()
        for func in [
            'force_unicode',
            'timesince',
            'shorten_sha1',
            'shorten_message',
            'extract_author_name',
            'formattimestamp',
        ]:
            env.filters[func] = getattr(utils, func)


        return env

    def setup_routes(self):
        for endpoint, rule in [
            ('air_get_status_betrieb',   '/'),
            ('air_get_status_stoerung',   '/stoerung'),
            ('air_set_status',   '/<int:pin>/<int:state>'),
            ('air_set_level', '/lueftung/stufe/<int:level>'),
            ('air_set_timer', '/lueftung/timer/<int:time>'),
            ('air_set_temp', '/lueftung/temperatur/<int:temp>'),
            ('air_set_raucherraum_on', '/raucherraum/on'),
            ('air_set_raucherraum_off', '/raucherraum/off'),

        ]:
            self.add_url_rule(rule, view_func=getattr(views, endpoint))


if __name__ == "__main__":
    app = Krempelair()
    app.run(host="0.0.0.0", debug=True)
