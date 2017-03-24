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
            #Allgemeine Funktionen
            #('air_login',   '/login/<user>/<key>'),
            
            # Getter der Temperaturen und des Betriebsstatus
            ('air_get_status_betrieb',   '/'),
            ('air_get_status_stoerung',   '/stoerung'),
            ('air_get_temperaturen', '/lueftung/temperatur'),
            
            # Setters für Funktionen
            ('air_set_level', '/lueftung/stufe/<int:level>'),
            ('air_set_timer', '/lueftung/timer/<int:time>'),
            ('air_set_tempSoll', '/lueftung/temperatur/sollTemp/<int:temp>'),
            ('air_set_tempNAK', '/lueftung/temperatur/sollTempNAK/<int:temp>'),
            ('air_set_NAK', '/lueftung/NAK/<int:NAK>'),
            ('air_set_tempZulMax', '/lueftung/temperatur/tempZulMax/<int:temp>'),
            ('air_set_tempZulMin', '/lueftung/temperatur/tempZulMin/<int:temp>'),
        ]:
            self.add_url_rule(rule, view_func=getattr(views, endpoint))


if __name__ == "__main__":
    app = Krempelair()
    app.run(host="0.0.0.0", debug=True)
else:
    application = Krempelair()
