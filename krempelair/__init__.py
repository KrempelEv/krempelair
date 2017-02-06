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
            ('air_status',   '/<int:inp>'),

        ]:
            self.add_url_rule(rule, view_func=getattr(views, endpoint))

    def should_use_ctags(self, git_repo, git_commit):
        if self.ctags_policy == 'none':
            return False
        elif self.ctags_policy == 'ALL':
            return True
        elif self.ctags_policy == 'tags-and-branches':
            return git_commit.id in git_repo.get_tag_and_branch_shas()
        else:
            raise ValueError("Unknown ctags policy %r" % self.ctags_policy)

if __name__ == "__main__":
    app = Krempelair()
    app.run(host="0.0.0.0", debug=True)
