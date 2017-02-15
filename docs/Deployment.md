# Deployment

To deploy Krempelair use The following Guide

## Preperation

First of all Install the following Software Packages:
 * nginx-extras
 * uwsgi
 * uwsgi-python
 * python-flask
 * git

you can do that using the following command:

```bash
# apt-get install -y nginx-extras uwsgi uwsgi-python python-flask git
```
the next step is, creating the krempel folder and cloning the git 
[krempelair repo](https://github.com/bittracker/krempelair)

```bash
$ mkdir -p /opt/krempel && cd /opt/krempel
$ git clone https://github.com/bittracker/krempelair.git
```

## NGINX Configuration
add the following contents to your nginx configuration:

```conf
server {
	listen 80 default_server;
	listen [::]:80 default_server;


	root /opt/krempel/krempelair/web-ui;

	index index.html index.htm index.nginx-debian.html;

	server_name _;

	location / {
		try_files $uri $uri/ =404;
	}


 	location ~ ^/api {
		include uwsgi_params;
                uwsgi_pass 127.0.0.1:9090;
		uwsgi_param SCRIPT_NAME /api;
        }

}

```

## uWSGI Config 
now create the following file under `/etc/uwsgi/apps-available/krempelair.ini`

```ini
[uwsgi]
plugin=python
procname=krempelair
auto-procname=krempelair
force-cwd=/opt/krempel/krempelair
chdir=/opt/krempel/krempelair
wsgi-file=uwsgi.py
socket=127.0.0.1:9090
master=true
processes=2 
threads=2
mount = /api=uwsgi.py
manage-script-name=true
```

## This is the end my Friend
restarting all affectet services and try if you can access the web-ui

```
# systemctrl restart {nginx,uwsgi}
```

## Hints

if an command example starting with `#` the command is running with root permissions (sudo)
